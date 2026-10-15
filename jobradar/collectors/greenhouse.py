"""Public Greenhouse Job Board API adapter (all published jobs per board)."""

from typing import Any

from jobradar.collectors.base import CollectionError, Collector, CollectorResult
from jobradar.collectors.normalization import (
    optional_string,
    parse_timestamp,
    plain_text,
    remote_type,
    required_string,
    restrictions,
    salary_fields,
    seniority,
    source_id,
)
from jobradar.config import SourceConfig
from jobradar.models import Job


def _metadata(item: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    values = item.get("metadata")
    if isinstance(values, list):
        for entry in values:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                result[entry["name"].strip().casefold()] = entry.get("value")
    return result


def _metadata_text(metadata: dict[str, Any], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = metadata.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            strings = [entry for entry in value if isinstance(entry, str) and entry.strip()]
            if strings:
                return "; ".join(strings)
    return None


class GreenhouseCollector(Collector):
    @staticmethod
    def normalize(item: dict[str, Any], source: SourceConfig) -> Job:
        title = required_string(item.get("title"), "title")
        if not isinstance(item.get("content"), str):
            raise ValueError("missing or invalid content (content=true is required)")
        description = plain_text(item["content"])
        raw_location = item.get("location")
        location = (
            optional_string(raw_location.get("name")) if isinstance(raw_location, dict) else None
        )
        metadata = _metadata(item)
        workplace = _metadata_text(
            metadata, ("workplace type", "workplace_type", "remote type", "remote status", "remote")
        )
        if workplace is None and metadata.get("remote") is True:
            workplace = "remote"
        remote = remote_type(location, workplace)
        return Job(
            source="greenhouse",
            source_job_id=source_id(item.get("id")),
            source_board=source.board_key,
            company=source.name,
            title=title,
            description=description,
            location=location,
            remote_type=remote,
            remote_restrictions=restrictions(location, description, remote),
            employment_type=_metadata_text(metadata, ("employment type", "employment_type")),
            seniority=seniority(title),
            language=optional_string(item.get("language"))
            or _metadata_text(metadata, ("description language", "language")),
            url=required_string(item.get("absolute_url"), "absolute_url"),
            published_at=parse_timestamp(
                item.get("first_published", item.get("first_published_at"))
            ),
            source_updated_at=parse_timestamp(item.get("updated_at")),
            raw_data=item,
            **salary_fields(item.get("salary_range")),
        )

    def collect(self, source: SourceConfig) -> CollectorResult:
        result = CollectorResult()
        url = f"https://boards-api.greenhouse.io/v1/boards/{source.identifier}/jobs"
        try:
            payload = self.http.get_json(url, params={"content": "true"})
        except CollectionError as exc:
            return CollectorResult(complete=False, errors=[str(exc)])
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            return CollectorResult(complete=False, errors=["Invalid Greenhouse jobs envelope"])
        entries = payload["jobs"]
        meta = payload.get("meta")
        total = meta.get("total") if isinstance(meta, dict) else None
        if (
            not isinstance(total, int)
            or isinstance(total, bool)
            or total < 0
            or total != len(entries)
        ):
            result.complete = False
            result.errors.append(
                "Greenhouse meta.total is missing or does not match the jobs array"
            )
        seen: set[str] = set()
        for index, item in enumerate(entries):
            try:
                if not isinstance(item, dict):
                    raise ValueError("job is not an object")
                job = self.normalize(item, source)
                if job.source_job_id in seen:
                    raise ValueError(f"duplicate job id {job.source_job_id}")
                seen.add(job.source_job_id)
                result.jobs.append(job)
            except (ValueError, TypeError) as exc:
                result.complete = False
                result.errors.append(f"Greenhouse item {index}: {exc}")
        return result
