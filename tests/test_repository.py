from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from jobradar.config import SourceConfig
from jobradar.database import Repository
from jobradar.database.models import CollectionRun
from jobradar.models import Job


@pytest.fixture
def source():
    return SourceConfig(name="Example", source="greenhouse", identifier="example")


@pytest.fixture
def repository(tmp_path):
    with Repository(f"sqlite:///{tmp_path / 'nested' / 'jobs.db'}") as repository:
        yield repository


def vacancy(source, identifier="1", **changes):
    values = {
        "source": source.source,
        "source_board": source.board_key,
        "source_job_id": identifier,
        "company": source.name,
        "title": "Network Security Engineer",
        "description": "Full description.\n" * 3000,
        "location": "Costa Rica",
        "url": f"https://example.org/jobs/{identifier}",
        "raw_data": {"id": identifier, "content": {"html": "<p>Полный текст</p>"}},
    }
    values.update(changes)
    return Job(**values)


def test_snapshot_roundtrip_upsert_and_reactivation(repository, source, monkeypatch):
    first = datetime(2026, 1, 1, tzinfo=UTC)
    later = first + timedelta(days=1)
    monkeypatch.setattr("jobradar.database.repository.utc_now", lambda: first)
    original = vacancy(
        source,
        published_at=datetime(2025, 12, 1, tzinfo=timezone(timedelta(hours=3))),
        salary_min=100000,
        salary_max=150000,
        salary_currency="USD",
    )
    initial = repository.save_snapshot(source, [original], complete=True)
    assert initial["inserted"] == 1
    persisted = repository.list_jobs()[0]
    assert persisted.description == original.description
    assert persisted.raw_data == original.raw_data
    assert persisted.first_seen_at == first
    assert persisted.published_at == datetime(2025, 11, 30, 21, tzinfo=UTC)
    assert persisted.salary_min == 100000
    assert persisted.last_seen_at.tzinfo == UTC

    repository.save_snapshot(
        source, [], complete=True, mark_inactive=True, allow_empty_deactivation=True
    )
    assert not repository.list_jobs()
    monkeypatch.setattr("jobradar.database.repository.utc_now", lambda: later)
    changed = vacancy(
        source, title="Senior Network Security Engineer", description="Updated full text"
    )
    updated = repository.save_snapshot(source, [changed], complete=True)
    assert updated["inserted"] == 0
    assert updated["updated"] == 1
    saved = repository.list_jobs()[0]
    assert saved.id == original.id
    assert saved.id != changed.id
    assert saved.first_seen_at == first
    assert saved.last_seen_at == later
    assert saved.status == "active"
    assert saved.title == changed.title
    assert saved.description == changed.description
    assert len(repository.list_jobs(status=None)) == 1


@pytest.mark.parametrize(
    ("complete", "errors", "allow_empty", "expected"),
    [
        (False, [], True, 0),
        (True, ["Request timeout"], True, 0),
        (True, [], False, 0),
        (True, [], True, 1),
    ],
)
def test_empty_snapshot_deactivation_guards(
    repository, source, complete, errors, allow_empty, expected
):
    repository.save_snapshot(source, [vacancy(source)], complete=True)
    result = repository.save_snapshot(
        source,
        [],
        complete=complete,
        errors=errors,
        mark_inactive=True,
        allow_empty_deactivation=allow_empty,
    )
    assert result["inactivated"] == expected
    assert len(repository.list_jobs()) == 1 - expected
    assert repository.stats()["recent_runs"][0]["errors"] == errors


def test_no_inactivation_by_default_and_partial_refresh(repository, source):
    repository.save_snapshot(source, [vacancy(source), vacancy(source, "2")], complete=True)
    repository.save_snapshot(source, [vacancy(source)], complete=True)
    assert len(repository.list_jobs()) == 2
    result = repository.save_snapshot(
        source, [vacancy(source, title="Changed")], complete=False, mark_inactive=True
    )
    assert result["updated"] == 1
    assert len(repository.list_jobs()) == 2
    assert "Changed" in {job.title for job in repository.list_jobs()}


def test_complete_snapshot_only_deactivates_own_board(repository, source):
    another = source.model_copy(update={"identifier": "another", "name": "Another Company"})
    repository.save_snapshot(source, [vacancy(source), vacancy(source, "2")], complete=True)
    repository.save_snapshot(another, [vacancy(another, "3")], complete=True)
    result = repository.save_snapshot(source, [vacancy(source)], complete=True, mark_inactive=True)
    assert result["inactivated"] == 1
    active = repository.list_jobs()
    assert {job.source_job_id for job in active} == {"1", "3"}
    assert len(repository.list_jobs(company="another company")) == 1
    assert len(repository.list_jobs(source="lever")) == 0
    assert repository.list_jobs(status="inactive")[0].source_job_id == "2"


def test_board_collision_does_not_overwrite_or_deactivate(repository, source):
    another = source.model_copy(update={"identifier": "another"})
    repository.save_snapshot(source, [vacancy(source)], complete=True)
    repository.save_snapshot(another, [vacancy(another, "2")], complete=True)
    collision = vacancy(another, title="Should not overwrite")
    result = repository.save_snapshot(another, [collision], complete=True, mark_inactive=True)
    assert result["complete"] is False
    assert result["errors"]
    assert len(repository.list_jobs()) == 2
    assert all(job.title != collision.title for job in repository.list_jobs())


def test_invalid_board_and_repeated_ids_disable_deactivation(repository, source):
    another = source.model_copy(update={"identifier": "another"})
    repository.save_snapshot(source, [vacancy(source), vacancy(source, "2")], complete=True)
    invalid = repository.save_snapshot(
        source, [vacancy(another)], complete=True, mark_inactive=True
    )
    assert invalid["complete"] is False
    repeated = repository.save_snapshot(
        source, [vacancy(source), vacancy(source)], complete=True, mark_inactive=True
    )
    assert repeated["complete"] is False
    assert repeated["updated"] == 1
    assert len(repository.list_jobs()) == 2


def test_write_failure_rolls_back_snapshot_and_records_failure(repository, source):
    repository.save_snapshot(source, [vacancy(source)], complete=True)
    broken = vacancy(source, "3", raw_data={"not_json_serializable": object()})
    result = repository.save_snapshot(
        source,
        [vacancy(source, title="Must roll back"), broken],
        complete=True,
        mark_inactive=True,
    )
    assert result["complete"] is False
    assert result["inserted"] == result["updated"] == result["inactivated"] == 0
    assert result["errors"] == ["Database write failed: StatementError"]
    saved = repository.list_jobs()
    assert len(saved) == 1
    assert saved[0].title == "Network Security Engineer"
    stats = repository.stats()
    assert stats["recent_runs"][0]["complete"] is False
    assert stats["total"] == 1
    with Session(repository.engine) as session:
        assert len(session.scalars(select(CollectionRun)).all()) == 2


def test_stats_and_conservative_cross_source_duplicates(repository, source):
    lever = SourceConfig(name="  EXAMPLE  ", source="lever", identifier="example")
    repository.save_snapshot(source, [vacancy(source)], complete=True)
    repository.save_snapshot(
        lever,
        [vacancy(lever, "l1", title="Network   Security Engineer", location="costa rica")],
        complete=True,
    )
    duplicates = repository.possible_duplicates()
    assert len(duplicates) == 1
    assert len(duplicates[0]["job_ids"]) == 2
    assert "different sources" in duplicates[0]["reason"]
    stats = repository.stats()
    assert stats["total"] == 2
    assert stats["by_source"] == {"greenhouse": 1, "lever": 1}
    assert stats["by_status"] == {"active": 2}
    assert len(stats["recent_runs"]) == 2
    assert stats["recent_runs"][0]["finished_at"].tzinfo == UTC
    # Hints never merge or delete jobs.
    assert len(repository.list_jobs()) == 2


def test_missing_location_is_insufficient_duplicate_evidence(repository, source):
    lever = SourceConfig(name="Example", source="lever", identifier="example")
    repository.save_snapshot(source, [vacancy(source, location=None)], complete=True)
    repository.save_snapshot(lever, [vacancy(lever, location=None)], complete=True)
    assert repository.possible_duplicates() == []


def test_data_survives_repository_reopen(tmp_path, source):
    database_url = f"sqlite:///{tmp_path / 'jobs.db'}"
    with Repository(database_url) as repository:
        repository.save_snapshot(source, [vacancy(source)], complete=True)
    with Repository(database_url) as reopened:
        assert len(reopened.list_jobs()) == 1
        assert reopened.stats()["total"] == 1


def test_company_filter_folds_unicode_consistently(repository, source):
    company = source.model_copy(update={"name": "Тест Инфраструктура"})
    repository.save_snapshot(company, [vacancy(company)], complete=True)
    assert len(repository.list_jobs(company="Тест Инфраструктура")) == 1
    assert len(repository.list_jobs(company="тест инфраструктура")) == 1
    assert not repository.list_jobs(company="Другая компания")


def test_in_memory_database(source):
    with Repository("sqlite://") as repository:
        repository.save_snapshot(source, [vacancy(source)], complete=True)
        assert len(repository.list_jobs()) == 1
