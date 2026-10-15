import csv
import io
import json

import httpx
from sqlalchemy.exc import SQLAlchemyError

from jobradar.collectors import RetryingHTTPClient
from jobradar.config import HTTPSettings, SourceConfig, SourcesConfig
from jobradar.database import Repository
from jobradar.models import Job
from jobradar.services.collection import collect_jobs
from jobradar.services.export import render_export, write_export


def test_failed_board_does_not_stop_other_board():
    def handler(request):
        if "greenhouse" in request.url.host:
            return httpx.Response(503)
        if int(request.url.params.get("skip", 0)):
            return httpx.Response(200, json=[])
        return httpx.Response(
            200,
            json=[
                {
                    "id": "1",
                    "text": "Firewall Engineer",
                    "categories": {},
                    "hostedUrl": "https://jobs.lever.co/example/1",
                    "descriptionPlain": "Full text",
                }
            ],
        )

    config = SourcesConfig(
        sources=[
            SourceConfig(name="Broken", source="greenhouse", identifier="broken"),
            SourceConfig(name="Healthy", source="lever", identifier="example"),
        ]
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with RetryingHTTPClient(HTTPSettings(attempts=1, min_interval=0), client=client) as http:
            with Repository("sqlite://") as repository:
                summaries = collect_jobs(config, repository, http=http)
                assert not summaries[0]["complete"]
                assert summaries[1]["complete"]
                assert repository.list_jobs()[0].title == "Firewall Engineer"
                assert len(repository.stats()["recent_runs"]) == 2


def test_source_filter_skips_other_adapters():
    with Repository("sqlite://") as repository:
        config = SourcesConfig(
            sources=[
                SourceConfig(name="Skipped", source="greenhouse", identifier="skip"),
            ]
        )
        with RetryingHTTPClient(HTTPSettings(min_interval=0)) as http:
            assert collect_jobs(config, repository, source="lever", http=http) == []


def test_unexpected_adapter_error_is_audited_and_next_board_runs(monkeypatch):
    def broken_adapter(*args):
        raise RuntimeError("unexpected adapter failure")

    monkeypatch.setattr("jobradar.services.collection.GreenhouseCollector.collect", broken_adapter)
    config = SourcesConfig(
        sources=[
            SourceConfig(name="Broken", source="greenhouse", identifier="broken"),
            SourceConfig(name="Healthy", source="lever", identifier="healthy"),
        ]
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
    ) as client:
        with RetryingHTTPClient(HTTPSettings(attempts=1, min_interval=0), client=client) as http:
            with Repository("sqlite://") as repository:
                summaries = collect_jobs(config, repository, http=http)
                assert not summaries[0]["complete"]
                assert "unexpected adapter failure" in summaries[0]["errors"][0]
                assert summaries[1]["complete"]
                assert len(repository.stats()["recent_runs"]) == 2


def test_storage_failure_does_not_expose_bound_data_or_stop_next_board(monkeypatch):
    config = SourcesConfig(
        sources=[
            SourceConfig(name="First", source="lever", identifier="first"),
            SourceConfig(name="Next", source="lever", identifier="next"),
        ]
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
    ) as client:
        with RetryingHTTPClient(HTTPSettings(attempts=1, min_interval=0), client=client) as http:
            with Repository("sqlite://") as repository:
                save = repository.save_snapshot

                def storage(board, *args, **kwargs):
                    if board.identifier == "first":
                        raise SQLAlchemyError("bound source text should not appear in status")
                    return save(board, *args, **kwargs)

                monkeypatch.setattr(repository, "save_snapshot", storage)
                progress = []
                results = collect_jobs(config, repository, http=http, on_board=progress.append)
                assert not results[0]["complete"]
                assert results[1]["complete"]
                assert "bound source text" not in json.dumps(results)
                assert len(progress) == 2


def test_export_preserves_full_text_unicode_and_raw(tmp_path):
    job = Job(
        source="greenhouse",
        source_job_id="1",
        source_board="greenhouse:global:test",
        company="Тест",
        title="=dangerous",
        description="Firewall\nПолный текст," * 1000,
        url="https://example.com/1",
        raw_data={"html": "<p>raw</p>"},
    )
    result = json.loads(render_export([job], "json"))[0]
    assert result["description"] == job.description
    assert result["raw_data"] == job.raw_data
    rows = list(csv.DictReader(io.StringIO(render_export([job], "csv"))))
    assert rows[0]["title"] == "'=dangerous"
    assert rows[0]["description"] == job.description
    path = tmp_path / "nested" / "jobs.json"
    write_export(path, render_export([job], "json"))
    assert json.loads(path.read_text(encoding="utf-8"))[0] == result
    assert list(path.parent.iterdir()) == [path]


def test_empty_export_has_valid_header_and_json():
    assert json.loads(render_export([], "json")) == []
    assert "source_job_id" in render_export([], "csv").splitlines()[0]
