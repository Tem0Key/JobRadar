"""Transactional storage, collection history, and conservative duplicate hints."""

import re
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Self

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from jobradar.config import SourceConfig
from jobradar.database.models import Base, CollectionRun, JobRecord
from jobradar.models import Job
from jobradar.models.job import utc_now


def _normalized(value: str | None) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value or "").casefold()).strip()


class Repository:
    def __init__(self, database_url: str) -> None:
        url = make_url(database_url)
        options: dict[str, Any] = {}
        if url.get_backend_name() == "sqlite":
            if url.database and url.database != ":memory:":
                Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
            options["connect_args"] = {"timeout": 30}
        self.engine = create_engine(database_url, **options)
        if url.get_backend_name() == "sqlite":

            @event.listens_for(self.engine, "connect")
            def sqlite_settings(connection: Any, _: Any) -> None:
                cursor = connection.cursor()
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.close()

        Base.metadata.create_all(self.engine)

    def close(self) -> None:
        self.engine.dispose()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def save_snapshot(
        self,
        source: SourceConfig,
        jobs: list[Job],
        *,
        complete: bool,
        mark_inactive: bool = False,
        allow_empty_deactivation: bool = False,
        errors: list[str] | None = None,
        started_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Save all usable jobs and history atomically; deactivate only a proven full board.

        A failed/partial collection can still refresh jobs it actually returned. An ID
        belonging to another board is never overwritten. A database write failure
        rolls back the whole snapshot before a separate failure history entry is saved.
        """
        now = utc_now()
        started = started_at or now
        run_errors = list(errors or [])
        summary: dict[str, Any] = {
            "source": source.source,
            "company": source.name,
            "board": source.board_key,
            "complete": complete and not run_errors,
            "fetched": len(jobs),
            "inserted": 0,
            "updated": 0,
            "inactivated": 0,
            "errors": run_errors,
        }
        unique_jobs: dict[str, Job] = {}
        for job in jobs:
            if job.source != source.source or job.source_board != source.board_key:
                run_errors.append(f"Job {job.source_job_id}: source or board does not match config")
                continue
            if job.source_job_id in unique_jobs:
                run_errors.append(f"Repeated source job ID in snapshot: {job.source_job_id}")
            unique_jobs[job.source_job_id] = job
        summary["complete"] = bool(summary["complete"] and not run_errors)

        try:
            with Session(self.engine) as session, session.begin():
                identifiers = list(unique_jobs)
                existing: dict[str, JobRecord] = {}
                # Keep IN clauses below SQLite's lowest commonly supported variable limit.
                for offset in range(0, len(identifiers), 500):
                    statement = select(JobRecord).where(
                        JobRecord.source == source.source,
                        JobRecord.source_job_id.in_(identifiers[offset : offset + 500]),
                    )
                    existing.update(
                        (record.source_job_id, record) for record in session.scalars(statement)
                    )
                seen: set[str] = set()
                for job in unique_jobs.values():
                    record = existing.get(job.source_job_id)
                    if record is not None and record.source_board != source.board_key:
                        run_errors.append(
                            f"Job ID {job.source_job_id} already belongs to board "
                            f"{record.source_board}; skipped"
                        )
                        summary["complete"] = False
                        continue
                    values = job.model_dump(
                        exclude={"id", "first_seen_at", "last_seen_at", "status"}
                    )
                    if record is None:
                        record = JobRecord(
                            **values,
                            id=job.id,
                            first_seen_at=now,
                            last_seen_at=now,
                            status="active",
                        )
                        session.add(record)
                        summary["inserted"] += 1
                    else:
                        for field, value in values.items():
                            setattr(record, field, value)
                        record.last_seen_at = now
                        record.status = "active"
                        summary["updated"] += 1
                    seen.add(job.source_job_id)

                if (
                    mark_inactive
                    and summary["complete"]
                    and not run_errors
                    and (seen or allow_empty_deactivation)
                ):
                    board_records = session.scalars(
                        select(JobRecord).where(
                            JobRecord.source == source.source,
                            JobRecord.source_board == source.board_key,
                            JobRecord.status != "inactive",
                        )
                    )
                    for record in board_records:
                        if record.source_job_id not in seen:
                            record.status = "inactive"
                            summary["inactivated"] += 1

                session.add(CollectionRun(**summary, started_at=started, finished_at=now))
        except SQLAlchemyError as error:
            # Do not include SQL parameters, which can contain full descriptions/raw data.
            run_errors.append(f"Database write failed: {type(error).__name__}")
            summary.update(complete=False, inserted=0, updated=0, inactivated=0)
            with Session(self.engine) as session, session.begin():
                session.add(CollectionRun(**summary, started_at=started, finished_at=utc_now()))
        return summary

    def list_jobs(
        self,
        source: str | None = None,
        status: str | None = "active",
        company: str | None = None,
    ) -> list[Job]:
        statement = select(JobRecord)
        if source is not None:
            statement = statement.where(JobRecord.source == source)
        if status is not None:
            statement = statement.where(JobRecord.status == status)
        statement = statement.order_by(JobRecord.last_seen_at.desc(), JobRecord.id.asc())
        with Session(self.engine) as session:
            return [
                Job.model_validate({field: getattr(record, field) for field in Job.model_fields})
                for record in session.scalars(statement)
                # SQLite lower() folds ASCII only. Python handles Cyrillic company names
                # consistently with the PostgreSQL path and profile rule matching.
                if company is None or _normalized(record.company) == _normalized(company)
            ]

    def stats(self) -> dict[str, Any]:
        with Session(self.engine) as session:
            total = session.scalar(select(func.count()).select_from(JobRecord)) or 0
            by_source = dict(
                session.execute(
                    select(JobRecord.source, func.count()).group_by(JobRecord.source)
                ).all()
            )
            by_status = dict(
                session.execute(
                    select(JobRecord.status, func.count()).group_by(JobRecord.status)
                ).all()
            )
            recent_runs = [
                {
                    column.name: getattr(run, column.name)
                    for column in CollectionRun.__table__.columns
                    if column.name != "id"
                }
                for run in session.scalars(
                    select(CollectionRun)
                    .order_by(CollectionRun.finished_at.desc(), CollectionRun.id.desc())
                    .limit(20)
                )
            ]
        return {
            "total": total,
            "by_source": by_source,
            "by_status": by_status,
            "recent_runs": recent_runs,
        }

    def possible_duplicates(self) -> list[dict[str, Any]]:
        """Suggest exact normalized company/title/location matches across platforms.

        Empty locations are excluded: absent location is insufficient evidence for
        even this conservative hint. A hint never modifies or deletes a vacancy.
        """
        groups: dict[tuple[str, str, str], list[Job]] = defaultdict(list)
        for job in self.list_jobs():
            key = (_normalized(job.company), _normalized(job.title), _normalized(job.location))
            if all(key):
                groups[key].append(job)
        return [
            {
                "job_ids": sorted(job.id for job in matches),
                "company": matches[0].company,
                "title": matches[0].title,
                "location": matches[0].location,
                "reason": "Same normalized company, title and location across different sources",
            }
            for _, matches in sorted(groups.items())
            if len({job.source for job in matches}) > 1
        ]
