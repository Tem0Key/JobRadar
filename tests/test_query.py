from concurrent.futures import ThreadPoolExecutor

import pytest
import yaml
from sqlalchemy.orm import Session

from jobradar.config import Settings, SourceConfig
from jobradar.database import Repository
from jobradar.database.models import JobRecord
from jobradar.models import Job
from jobradar.services.query import VacancyQuery


@pytest.fixture
def settings(tmp_path):
    profiles = tmp_path / "profiles.yaml"
    profiles.write_text(
        yaml.safe_dump({"profiles": [{"name": "Security", "keywords": ["firewall"]}]}),
        encoding="utf-8",
    )
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}",
        profiles_file=profiles,
    )
    with Repository(settings.database_url) as repository:
        for source, company, identifier, values in [
            (
                "greenhouse",
                "Компания Тест",
                "1",
                {
                    "title": "Firewall Engineer",
                    "description": "NGFW and network security",
                    "location": "San José, Costa Rica",
                    "remote_type": "remote",
                    "seniority": "Senior",
                    "language": "en",
                },
            ),
            (
                "greenhouse",
                "Other",
                "2",
                {
                    "title": "Firewall Administrator",
                    "location": "United States",
                    "remote_type": "onsite",
                },
            ),
            (
                "lever",
                "Routers",
                "3",
                {"title": "Network Engineer", "description": "Routing", "remote_type": "remote"},
            ),
        ]:
            board = SourceConfig(name=company, source=source, identifier=f"test{identifier}")
            repository.save_snapshot(
                board,
                [
                    Job(
                        id=identifier,
                        source=source,
                        source_board=board.board_key,
                        source_job_id=identifier,
                        company=company,
                        url=f"https://example.com/jobs/{identifier}",
                        **values,
                    )
                ],
                complete=True,
            )
    return settings


def change_job(settings, id, **values):
    with Repository(settings.database_url) as repository, Session(repository.engine) as session:
        record = session.get(JobRecord, id)
        for name, value in values.items():
            setattr(record, name, value)
        session.commit()


def ids(result):
    return {job.id for job in result[0]}


def test_inventory_preserves_geographically_excluded_jobs(settings):
    jobs, matches = VacancyQuery(settings).select()
    assert len(jobs) == 3
    assert matches["2"].matched
    assert matches["2"].eligibility == "excluded"
    assert matches["1"].eligibility == "allowed"
    assert matches["3"].eligibility == "needs_review"


def test_profile_relevance_and_geography_and_free_text(settings):
    query = VacancyQuery(settings)
    assert ids(query.select(profile="Security")) == {"1"}
    assert ids(query.select(query="Firewall")) == {"1", "2"}
    assert ids(query.select(query="routing")) == {"3"}
    assert ids(query.select(query="КОМПАНИЯ")) == {"1"}
    assert ids(query.select(query="Costa Rica")) == {"1"}
    assert ids(query.select(query="routing", profile="Security")) == set()
    assert ids(query.select(query="  firewall  ", profile="security")) == {"1"}


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"source": "lever"}, {"3"}),
        ({"company": " КОМПАНИЯ  ТЕСТ "}, {"1"}),
        ({"remote_type": "onsite"}, {"2"}),
        ({"remote_type": "remote"}, {"1", "3"}),
        ({"seniority": " senior "}, {"1"}),
        ({"language": "EN"}, {"1"}),
        ({"location": "COSTA"}, {"1"}),
        ({"eligibility": "allowed"}, {"1"}),
        ({"eligibility": "excluded"}, {"2"}),
        ({"eligibility": "needs_review"}, {"3"}),
        ({"company": "no match"}, set()),
        ({"remote_type": "unknown"}, set()),
        ({"query": "nonexistent"}, set()),
    ],
)
def test_filters(settings, filters, expected):
    assert ids(VacancyQuery(settings).select(**filters)) == expected


def test_status_all_and_detail_include_inactive(settings):
    change_job(settings, "1", status="inactive")
    query = VacancyQuery(settings)
    assert ids(query.select()) == {"2", "3"}
    assert ids(query.select(status="inactive")) == {"1"}
    assert ids(query.select(status="all")) == {"1", "2", "3"}
    assert ids(query.select(status=None)) == {"1", "2", "3"}
    assert query.detail("1")[0].status == "inactive"


@pytest.mark.parametrize(
    "filters",
    [
        {"source": "bad"},
        {"status": "bad"},
        {"remote_type": "bad"},
        {"eligibility": "bad"},
        {"profile": "nonexistent"},
    ],
)
def test_invalid_filters_raise(settings, filters):
    with pytest.raises(ValueError):
        VacancyQuery(settings).select(**filters)


def test_detail_returns_explanation_for_unmatched_and_missing_job(settings):
    query = VacancyQuery(settings)
    job, result = query.detail("2", profile="Security", query="routing")
    assert job.id == "2"
    assert not result.matched
    assert result.eligibility == "excluded"
    assert "Query did not match: routing" in result.reasons
    assert query.detail("missing") is None
    with pytest.raises(ValueError):
        query.detail("1", profile="missing")


def observe_evaluations(monkeypatch):
    from jobradar.services import query as query_module

    original = query_module.evaluate
    evaluated = []

    def observe(job, profile=None, query=None):
        evaluated.append(job.id)
        return original(job, profile, query)

    monkeypatch.setattr(query_module, "evaluate", observe)
    return evaluated


def test_cheap_filters_run_before_evaluation(settings, monkeypatch):
    evaluated = observe_evaluations(monkeypatch)
    query = VacancyQuery(settings)
    assert ids(query.select(query="Firewall", remote_type="remote")) == {"1"}
    assert evaluated == ["1"]
    query.select(source="lever", location="Costa Rica")
    assert evaluated == ["1"]


def test_office_change_invalidates_geo_cache_without_date_change(settings):
    change_job(
        settings,
        "1",
        location="Hybrid",
        remote_type="hybrid",
        raw_data={
            "offices": [{"location": "Singapore"}],
        },
    )
    query = VacancyQuery(settings)
    assert "1" not in ids(query.select(profile="Security"))
    assert "1" in ids(query.select(location="Singapore"))
    assert "1" in ids(query.select(query="Singapore"))
    change_job(settings, "1", raw_data={"offices": [{"location": "Costa Rica"}]})
    assert "1" in ids(query.select(profile="Security"))


def test_match_cache_reused_but_content_change_invalidates(settings, monkeypatch):
    evaluated = observe_evaluations(monkeypatch)
    query = VacancyQuery(settings)
    query.select(profile="Security")
    assert len(evaluated) == 3
    query.select(profile="Security")
    assert len(evaluated) == 3
    query.detail("1", profile="Security")
    assert len(evaluated) == 3
    # Simulate an upstream edit with identical update/last-seen dates.
    change_job(settings, "1", description="Firewall and security", title="Network Engineer")
    assert ids(query.select(profile="Security")) == {"1"}
    assert len(evaluated) == 4
    change_job(settings, "1", description="Routing")
    assert ids(query.select(profile="Security")) == set()
    assert len(evaluated) == 5
    change_job(settings, "1", raw_data={"only": "irrelevant transport metadata"})
    query.select(profile="Security")
    assert len(evaluated) == 5


def test_profile_contents_and_mutated_path_invalidate_cache(settings, tmp_path, monkeypatch):
    evaluated = observe_evaluations(monkeypatch)
    query = VacancyQuery(settings)
    assert ids(query.select(profile="Security")) == {"1"}
    settings.profiles_file.write_text(
        yaml.safe_dump({"profiles": [{"name": "Security", "keywords": ["routing"]}]}),
        encoding="utf-8",
    )
    assert ids(query.select(profile="Security")) == {"3"}
    assert len(evaluated) == 6
    replacement = tmp_path / "new-profiles.yaml"
    replacement.write_text(
        yaml.safe_dump({"profiles": [{"name": "Security", "keywords": ["NGFW"]}]}),
        encoding="utf-8",
    )
    settings.profiles_file = replacement
    assert ids(query.select(profile="Security")) == {"1"}
    assert len(evaluated) == 9


def test_cache_bounded_and_results_independent(settings, monkeypatch):
    evaluated = observe_evaluations(monkeypatch)
    query = VacancyQuery(settings, cache_size=2)
    jobs, _ = query.select()
    assert len(query._cache) == 2
    evicted = jobs[0].id
    query.detail(evicted)
    assert len(evaluated) == 4  # Least recently used result was evicted.
    _, result = query.detail(evicted)
    result.reasons.append("Caller mutation")
    _, again = query.detail(evicted)
    assert "Caller mutation" not in again.reasons
    with pytest.raises(ValueError, match="cache_size"):
        VacancyQuery(settings, cache_size=0)


def test_query_cache_supports_concurrent_web_requests(settings):
    query = VacancyQuery(settings, cache_size=2)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: ids(query.select(profile="Security")), range(12)))
    assert results == [{"1"}] * 12
    assert len(query._cache) <= 2
