"""Local web management: saved jobs, validated edits and isolated background work."""

import csv
import io
import json
import time
from threading import Event

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from jobradar.config import ProfilesConfig, SearchProfile, Settings, SourceConfig, SourcesConfig
from jobradar.database import Repository
from jobradar.models import Job
from jobradar.web.app import create_app

WRITE_HEADERS = {"X-JobRadar-Request": "1"}


@pytest.fixture
def web_settings(tmp_path):
    greenhouse = SourceConfig(name="Acme Ру", source="greenhouse", identifier="example")
    lever = SourceConfig(name="Other", source="lever", identifier="other")
    sources = SourcesConfig(sources=[greenhouse, lever])
    profiles = ProfilesConfig(
        profiles=[SearchProfile(name="Network Security", keywords=["firewall", "сетевой"])]
    )
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'web.db').as_posix()}",
        sources_file=tmp_path / "sources.yaml",
        profiles_file=tmp_path / "profiles.yaml",
        _env_file=None,
    )
    settings.sources_file.write_text(
        yaml.safe_dump(sources.model_dump(mode="json"), allow_unicode=True), encoding="utf-8"
    )
    settings.profiles_file.write_text(
        yaml.safe_dump(profiles.model_dump(mode="json"), allow_unicode=True), encoding="utf-8"
    )

    def vacancy(board, identifier, title, **values):
        return Job(
            id=identifier,
            source=board.source,
            source_board=board.board_key,
            source_job_id=identifier,
            company=board.name,
            title=title,
            url=f"https://example.org/jobs/{identifier}",
            **values,
        )

    with Repository(settings.database_url) as repository:
        repository.save_snapshot(
            greenhouse,
            [vacancy(greenhouse, "old", "Old role")],
            complete=True,
        )
        repository.save_snapshot(
            greenhouse,
            [
                vacancy(
                    greenhouse,
                    "firewall",
                    "Firewall Engineer",
                    description="Full <script>alert(1)</script> text\nSecurity " * 200,
                    location="Remote worldwide",
                    remote_type="remote",
                    seniority="senior",
                    language="en",
                    raw_data={"body": "Полный текст"},
                ),
                vacancy(
                    greenhouse,
                    "network",
                    "Сетевой инженер",
                    description="Маршрутизация и VPN",
                    location="San José, Costa Rica",
                    remote_type="onsite",
                    seniority="mid",
                    language="ru",
                ),
            ],
            complete=True,
            mark_inactive=True,
        )
        repository.save_snapshot(
            lever,
            [vacancy(lever, "software", "Software Engineer", location="New York")],
            complete=True,
        )
    return settings


@pytest.fixture
def client(web_settings):
    with TestClient(create_app(web_settings)) as client:
        yield client


def test_bootstrap_and_stats(client):
    response = client.get("/api/bootstrap")
    assert response.status_code == 200
    body = response.json()
    assert body["stats"]["total"] == 4
    assert body["stats"]["by_status"] == {"active": 3, "inactive": 1}
    assert len(body["sources"]["sources"]) == 2
    assert body["profiles"]["profiles"][0]["name"] == "Network Security"
    assert body["collection"]["status"] == "idle"
    assert client.get("/api/stats").json() == body["stats"]


def test_job_pagination_and_full_detail(client):
    body = client.get("/api/jobs", params={"page_size": 2}).json()
    assert body["total"] == 3
    assert body["pages"] == 2
    assert len(body["items"]) == 2
    assert all("description" not in job and "raw_data" not in job for job in body["items"])
    assert all("match" in job for job in body["items"])
    assert len(client.get("/api/jobs?page=2&page_size=2").json()["items"]) == 1
    assert client.get("/api/jobs?page=99").json()["items"] == []
    response = client.get("/api/jobs/firewall", params={"profile": "Network Security"})
    assert response.status_code == 200
    detail = response.json()
    assert len(detail["description"]) > 5000
    assert detail["raw_data"] == {"body": "Полный текст"}
    assert detail["match"]["matched"]
    assert detail["match"]["eligibility"] == "allowed"
    assert client.get("/api/jobs/missing").status_code == 404
    # Stored inactive and nonmatching records are still available by ID.
    assert client.get("/api/jobs/old").json()["status"] == "inactive"
    assert not client.get("/api/jobs/software?query=firewall").json()["match"]["matched"]


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"query": "FIREWALL"}, {"firewall"}),
        ({"profile": "Network Security"}, {"firewall", "network"}),
        ({"source": "greenhouse"}, {"firewall", "network"}),
        ({"company": "acme ру"}, {"firewall", "network"}),
        ({"remote_type": "remote"}, {"firewall"}),
        ({"eligibility": "needs_review"}, {"software"}),
        ({"location": "COSTA RICA"}, {"network"}),
        ({"seniority": "senior"}, {"firewall"}),
        ({"language": "ru"}, {"network"}),
        ({"status": "inactive"}, {"old"}),
        ({"status": "all"}, {"old", "firewall", "network", "software"}),
        ({"query": "absent"}, set()),
        ({"query": "firewall", "language": "ru"}, set()),
    ],
)
def test_web_filters(client, filters, expected):
    response = client.get("/api/jobs", params=filters)
    assert response.status_code == 200
    assert {job["id"] for job in response.json()["items"]} == expected


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page_size": 0},
        {"page_size": 101},
        {"source": "unknown"},
        {"remote_type": "worldwide"},
        {"eligibility": "maybe"},
        {"status": "bad"},
        {"profile": "missing"},
    ],
)
def test_invalid_job_filters(client, params):
    assert client.get("/api/jobs", params=params).status_code == 422


def test_json_and_csv_export_all_filtered_results(client):
    for format in ("json", "csv"):
        response = client.get(
            "/api/export", params={"format": format, "profile": "Network Security"}
        )
        assert response.status_code == 200
        assert response.headers["content-disposition"] == f'attachment; filename="jobs.{format}"'
        if format == "json":
            records = response.json()
            assert records[0]["description"] is not None
        else:
            assert response.content.startswith(b"\xef\xbb\xbf")
            records = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
        assert len(records) == 2
        assert {job["id"] for job in records} == {"firewall", "network"}
        assert any(job["title"] == "Сетевой инженер" for job in records)
        assert all("match" in job for job in records)
    assert client.get("/api/export?format=xml").status_code == 422
    assert client.get("/api/export?query=absent&format=json").json() == []


def test_sources_and_profiles_validated_persisted_and_used_immediately(client, web_settings):
    source_config = client.get("/api/sources").json()
    source_config["sources"][0]["enabled"] = False
    result = client.put("/api/sources", json=source_config, headers=WRITE_HEADERS)
    assert result.status_code == 200
    assert result.json() == source_config
    assert not yaml.safe_load(web_settings.sources_file.read_text(encoding="utf-8"))["sources"][0][
        "enabled"
    ]
    profiles = client.get("/api/profiles").json()
    profiles["profiles"][0]["keywords"] = ["software"]
    assert client.put("/api/profiles", json=profiles, headers=WRITE_HEADERS).status_code == 200
    selected = client.get("/api/jobs?profile=Network%20Security").json()
    assert {job["id"] for job in selected["items"]} == {"software"}
    invalid = json.loads(json.dumps(source_config))
    invalid["sources"][0]["identifier"] = "../escape"
    assert client.put("/api/sources", json=invalid, headers=WRITE_HEADERS).status_code == 422
    assert client.get("/api/sources").json() == source_config
    profiles["profiles"].append(profiles["profiles"][0])
    assert client.put("/api/profiles", json=profiles, headers=WRITE_HEADERS).status_code == 422


@pytest.mark.parametrize("endpoint", ["/api/sources", "/api/profiles"])
def test_configuration_write_failure_is_clean_and_preserves_old_config(
    client, monkeypatch, endpoint
):
    config = client.get(endpoint).json()

    def fail_write(*args):
        raise OSError("Sensitive OS details")

    monkeypatch.setattr(client.app.state.configuration, "_write", fail_write)
    response = client.put(endpoint, json=config, headers=WRITE_HEADERS)
    assert response.status_code == 503
    assert "Sensitive" not in response.text
    assert client.get(endpoint).json() == config


def test_yaml_and_database_failures_are_clean(client, web_settings):
    web_settings.sources_file.write_text("sources: [broken:", encoding="utf-8")
    assert client.get("/api/sources").status_code == 422
    web_settings.database_url = "sqlite:///does-not-exist/../../:invalid:/db"
    # Use a temporary path already permitted by the fixture instead of creating odd paths.
    corrupt = web_settings.profiles_file.parent / "corrupt.db"
    corrupt.write_text("Not a database", encoding="utf-8")
    web_settings.database_url = f"sqlite:///{corrupt.as_posix()}"
    response = client.get("/api/stats")
    assert response.status_code == 503
    assert "Not a database" not in response.text


def test_missing_config_returns_503(client, web_settings):
    web_settings.sources_file = web_settings.sources_file.with_name("missing.yaml")
    assert client.get("/api/sources").status_code == 503
    assert client.post("/api/collect", json={}, headers=WRITE_HEADERS).status_code == 503
    assert not client.get("/api/collection").json()["running"]


def test_local_security_headers_and_mutation_checks(client):
    response = client.get("/api/jobs")
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert "'unsafe-inline'" not in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/jobs", headers={"Host": "attacker.example"}).status_code == 400
    config = client.get("/api/profiles").json()
    assert client.put("/api/profiles", json=config).status_code == 403
    for origin in (
        "https://attacker.example",
        "null",
        "http://testserver.evil",
        "http://testserver/",
    ):
        response = client.put(
            "/api/profiles", json=config, headers={**WRITE_HEADERS, "Origin": origin}
        )
        assert response.status_code == 403
    response = client.put(
        "/api/profiles", json=config, headers={**WRITE_HEADERS, "Origin": "http://testserver"}
    )
    assert response.status_code == 200
    preflight = client.options(
        "/api/collect",
        headers={
            "Origin": "https://attacker.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-JobRadar-Request",
        },
    )
    assert "access-control-allow-origin" not in preflight.headers


def wait_for_collection(client):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = client.get("/api/collection").json()
        if not result["running"]:
            return result
        time.sleep(0.01)
    pytest.fail("Background collection did not complete")


def test_collection_is_background_reports_progress_and_rejects_conflicting_edits(
    client, monkeypatch
):
    entered = Event()
    release = Event()
    captured = []
    summary = {"company": "Acme Ру", "complete": True, "fetched": 4, "errors": []}

    def blocked_collect(config, repository, *, source, on_board):
        captured.append((config, repository, source))
        on_board(summary)
        entered.set()
        assert release.wait(5)
        return [summary]

    monkeypatch.setattr("jobradar.web.runner.collect_jobs", blocked_collect)
    try:
        response = client.post("/api/collect", json={"source": "greenhouse"}, headers=WRITE_HEADERS)
        assert response.status_code == 202
        assert response.json()["running"]
        assert entered.wait(2)
        progress = client.get("/api/collection").json()
        assert progress["source"] == "greenhouse"
        assert progress["total_boards"] == 1
        assert progress["completed_boards"] == 1
        assert progress["results"] == [summary]
        assert client.get("/api/jobs").status_code == 200
        assert client.post("/api/collect", json={}, headers=WRITE_HEADERS).status_code == 409
        for endpoint in ("/api/sources", "/api/profiles"):
            config = client.get(endpoint).json()
            assert client.put(endpoint, json=config, headers=WRITE_HEADERS).status_code == 409
    finally:
        release.set()
    final = wait_for_collection(client)
    assert final["status"] == "completed"
    assert final["finished_at"] and final["started_at"]
    assert captured[0][2] == "greenhouse"
    # Snapshots returned to callers are copies, so clients cannot mutate worker state.
    snapshot = client.app.state.collection.snapshot()
    snapshot["results"][0]["company"] = "Changed"
    assert client.get("/api/collection").json()["results"][0]["company"] == "Acme Ру"


@pytest.mark.parametrize(
    ("summaries", "status"),
    [
        ([], "completed"),
        ([{"complete": False, "fetched": 0, "errors": ["429"]}], "failed"),
        ([{"complete": False, "fetched": 2, "errors": ["partial"]}], "partial"),
        (
            [
                {"complete": True, "fetched": 0, "errors": []},
                {"complete": False, "fetched": 0, "errors": ["timeout"]},
            ],
            "partial",
        ),
    ],
)
def test_background_collection_outcomes(client, monkeypatch, summaries, status):
    monkeypatch.setattr("jobradar.web.runner.collect_jobs", lambda *args, **kwargs: summaries)
    assert client.post("/api/collect", json={}, headers=WRITE_HEADERS).status_code == 202
    result = wait_for_collection(client)
    assert result["status"] == status
    assert result["completed_boards"] == len(summaries)
    assert len(result["errors"]) == sum(len(row["errors"]) for row in summaries)
    # Once a run finishes another run can be submitted.
    assert client.post("/api/collect", json={}, headers=WRITE_HEADERS).status_code == 202
    wait_for_collection(client)


def test_background_failure_hides_database_parameters_and_allows_retry(client, monkeypatch, caplog):
    def failed_collect(*args, **kwargs):
        raise SQLAlchemyError("private job parameters")

    monkeypatch.setattr("jobradar.web.runner.collect_jobs", failed_collect)
    assert client.post("/api/collect", json={}, headers=WRITE_HEADERS).status_code == 202
    result = wait_for_collection(client)
    assert result["status"] == "failed"
    assert not result["running"]
    assert "SQLAlchemyError" in result["errors"][0]
    assert "private job" not in str(result)
    assert "private job" not in caplog.text


def test_invalid_collection_body(client):
    for body in ({"source": "indeed"}, {"surprise": True}, []):
        assert client.post("/api/collect", json=body, headers=WRITE_HEADERS).status_code == 422


def test_static_dashboard(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "JobRadar" in response.text


def test_profile_role_exclusions_and_default_roundtrip(client):
    config = client.get("/api/profiles").json()
    config["default_profile"] = "Network Security"
    config["profiles"][0]["exclude_title_keywords"] = ["software", "sales"]
    response = client.put("/api/profiles", json=config, headers=WRITE_HEADERS)
    assert response.status_code == 200
    saved = client.get("/api/bootstrap").json()["profiles"]
    assert saved["default_profile"] == "Network Security"
    assert saved["profiles"][0]["exclude_title_keywords"] == ["software", "sales"]
