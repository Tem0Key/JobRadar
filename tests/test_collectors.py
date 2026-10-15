import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest

from jobradar.collectors import (
    CollectionError,
    GreenhouseCollector,
    LeverCollector,
    RetryingHTTPClient,
)
from jobradar.config import HTTPSettings, SourceConfig

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def greenhouse_payload():
    return json.loads((FIXTURES / "greenhouse_jobs.json").read_text(encoding="utf-8"))


@pytest.fixture
def lever_payload():
    return json.loads((FIXTURES / "lever_postings.json").read_text(encoding="utf-8"))


def source(platform="greenhouse", region="global"):
    return SourceConfig(name="Example", source=platform, identifier="example", region=region)


def http(handler, **settings):
    return RetryingHTTPClient(
        HTTPSettings(min_interval=0, backoff_base=0, **settings),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
        random=lambda: 0,
    )


def test_greenhouse_parses_full_text_and_keeps_unknown_fields(greenhouse_payload):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=greenhouse_payload)

    result = GreenhouseCollector(http(handler)).collect(source())
    assert result.complete and not result.errors
    job = result.jobs[0]
    assert job.source_job_id == "123456"
    assert job.description == (
        "Network & Security\nConfigure Palo Alto firewalls.\nVPN and routing\n"
        "NDR investigations\nFinal paragraph."
    )
    assert job.raw_data == greenhouse_payload["jobs"][0]
    assert job.source_updated_at == datetime(2026, 10, 1, 10, 30, tzinfo=UTC)
    assert job.published_at is None
    assert job.salary_min is None and job.salary_currency is None
    assert job.language is None
    assert job.remote_type == "remote"
    assert job.remote_restrictions == "Remote - Costa Rica"
    assert job.seniority == "senior"
    assert job.employment_type == "Full-time"
    assert requests[0].url.params["content"] == "true"


def test_greenhouse_uses_explicit_publication_only(greenhouse_payload):
    greenhouse_payload["jobs"][0]["first_published"] = "2026-08-03T12:00:00-06:00"
    result = GreenhouseCollector(
        http(lambda _: httpx.Response(200, json=greenhouse_payload))
    ).collect(source())
    assert result.jobs[0].published_at == datetime(2026, 8, 3, 18, tzinfo=UTC)


@pytest.mark.parametrize("payload", [{}, [], {"jobs": {}}, {"jobs": None}])
def test_greenhouse_invalid_envelope_is_incomplete(payload):
    result = GreenhouseCollector(http(lambda _: httpx.Response(200, json=payload))).collect(
        source()
    )
    assert not result.complete and result.errors and not result.jobs


def test_greenhouse_malformed_item_retains_valid_jobs(greenhouse_payload):
    greenhouse_payload["jobs"].extend([None, {"id": "broken"}])
    greenhouse_payload["meta"]["total"] = 3
    result = GreenhouseCollector(
        http(lambda _: httpx.Response(200, json=greenhouse_payload))
    ).collect(source())
    assert not result.complete
    assert len(result.jobs) == 1
    assert len(result.errors) == 2


def test_greenhouse_inconsistent_total_is_incomplete(greenhouse_payload):
    greenhouse_payload["meta"]["total"] = 20
    result = GreenhouseCollector(
        http(lambda _: httpx.Response(200, json=greenhouse_payload))
    ).collect(source())
    assert not result.complete and len(result.jobs) == 1


def test_greenhouse_duplicate_ids_are_incomplete(greenhouse_payload):
    greenhouse_payload["jobs"].append(deepcopy(greenhouse_payload["jobs"][0]))
    greenhouse_payload["meta"]["total"] = 2
    result = GreenhouseCollector(
        http(lambda _: httpx.Response(200, json=greenhouse_payload))
    ).collect(source())
    assert not result.complete and len(result.jobs) == 1


def test_lever_parses_all_sections_salary_and_ignores_created_at(lever_payload):
    def handler(request):
        return httpx.Response(200, json=lever_payload if request.url.params["skip"] == "0" else [])

    result = LeverCollector(http(handler)).collect(source("lever"))
    assert result.complete and not result.errors
    job = result.jobs[0]
    for part in (
        "Maintain the network.",
        "<T>",
        "Responsibilities",
        "Routing",
        "Switching and VPN",
        "Must be based in Costa Rica.",
        "Salary details.",
    ):
        assert part in job.description
    assert "HTML fallback" not in job.description
    assert job.salary_min == 50000 and job.salary_max == 80000
    assert job.salary_currency == "USD" and job.salary_interval == "per-year"
    assert job.published_at is None and job.source_updated_at is None
    assert job.remote_restrictions == "Must be based in Costa Rica."
    assert job.raw_data == lever_payload[0]


def test_lever_paginates_short_pages_until_empty_and_supports_eu(lever_payload):
    requests = []

    def handler(request):
        requests.append(request)
        skip = int(request.url.params["skip"])
        if skip == 3:
            return httpx.Response(200, json=[])
        postings = []
        for index in range(2 if skip == 0 else 1):
            posting = deepcopy(lever_payload[0])
            posting["id"] = str(skip + index)
            postings.append(posting)
        return httpx.Response(200, json=postings)

    result = LeverCollector(http(handler, page_size=100)).collect(source("lever", "eu"))
    assert result.complete and len(result.jobs) == 3
    assert [request.url.params["skip"] for request in requests] == ["0", "2", "3"]
    assert all(request.url.host == "api.eu.lever.co" for request in requests)
    assert all(request.url.params["mode"] == "json" for request in requests)


def test_lever_repeated_page_stops_as_incomplete(lever_payload):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=lever_payload)

    result = LeverCollector(http(handler)).collect(source("lever"))
    assert not result.complete and len(result.jobs) == 1
    assert len(requests) == 2
    assert any("no progress" in error for error in result.errors)


def test_lever_page_limit_does_not_claim_complete(lever_payload):
    result = LeverCollector(
        http(lambda _: httpx.Response(200, json=lever_payload), max_pages=1)
    ).collect(source("lever"))
    assert not result.complete and len(result.jobs) == 1
    assert "maximum page" in result.errors[-1]


def test_lever_keeps_jobs_when_later_page_fails(lever_payload):
    def handler(request):
        if request.url.params["skip"] == "0":
            return httpx.Response(200, json=lever_payload)
        raise httpx.ConnectError("connection failed", request=request)

    result = LeverCollector(http(handler, attempts=2)).collect(source("lever"))
    assert not result.complete and len(result.jobs) == 1 and result.errors


def test_lever_malformed_item_retains_valid_jobs(lever_payload):
    def handler(request):
        data = lever_payload + [{"id": "broken"}] if request.url.params["skip"] == "0" else []
        return httpx.Response(200, json=data)

    result = LeverCollector(http(handler)).collect(source("lever"))
    assert not result.complete and len(result.jobs) == 1


def test_lever_invalid_envelope_is_incomplete():
    result = LeverCollector(http(lambda _: httpx.Response(200, json={"error": "bad"}))).collect(
        source("lever")
    )
    assert not result.complete and not result.jobs


@pytest.mark.parametrize("platform", ["greenhouse", "lever"])
def test_empty_api_result_is_complete(platform):
    payload = {"jobs": [], "meta": {"total": 0}} if platform == "greenhouse" else []
    collector = GreenhouseCollector if platform == "greenhouse" else LeverCollector
    result = collector(http(lambda _: httpx.Response(200, json=payload))).collect(source(platform))
    assert result.complete and not result.jobs and not result.errors


@pytest.mark.parametrize("platform", ["greenhouse", "lever"])
def test_timeout_is_bounded_and_reported(platform):
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout("timed out", request=request)

    collector = GreenhouseCollector if platform == "greenhouse" else LeverCollector
    result = collector(http(handler, attempts=3)).collect(source(platform))
    assert not result.complete and not result.jobs
    assert len(requests) == 3
    assert "ReadTimeout" in result.errors[0]


@pytest.mark.parametrize("status", [429, 500, 502, 503, 599])
def test_transient_status_retried(status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status if len(requests) == 1 else 200, json={"ok": True})

    assert http(handler).get_json("https://example.test/jobs") == {"ok": True}
    assert len(requests) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_permanent_status_not_retried(status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status)

    with pytest.raises(CollectionError, match=f"HTTP {status}"):
        http(handler).get_json("https://example.test/jobs")
    assert len(requests) == 1


def test_malformed_json_is_not_retried():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text="<html>not json</html>")

    with pytest.raises(CollectionError, match="Invalid JSON"):
        http(handler).get_json("https://example.test/jobs")
    assert len(requests) == 1


@pytest.mark.parametrize(
    "retry_after",
    ["999999", format_datetime(datetime.now(UTC) + timedelta(days=1))],
)
@pytest.mark.parametrize("attempts", [1, 3])
def test_long_retry_after_defers_without_another_request(retry_after, attempts):
    sleeps = []
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429, headers={"Retry-After": retry_after})
        return httpx.Response(200, json=[])

    wrapper = RetryingHTTPClient(
        HTTPSettings(min_interval=0, backoff_base=1, max_backoff=3, attempts=attempts),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleeps.append,
        random=lambda: 1,
    )
    with pytest.raises(CollectionError, match="board deferred until the next scheduled run"):
        wrapper.get_json("https://example.test/jobs")
    assert len(requests) == 1
    assert sleeps == []


def test_retry_after_within_budget_is_honored():
    sleeps = []
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json=[])

    wrapper = RetryingHTTPClient(
        HTTPSettings(min_interval=0, backoff_base=1, max_backoff=3),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleeps.append,
        random=lambda: 1,
    )
    assert wrapper.get_json("https://example.test/jobs") == []
    assert len(requests) == 2
    assert sleeps == [2]


def test_rate_gate_covers_retries_and_different_boards():
    now = [0.0]
    starts = []

    def sleep(seconds):
        now[0] += seconds

    def handler(request):
        starts.append(now[0])
        return httpx.Response(503 if len(starts) == 1 else 200, json=[])

    wrapper = RetryingHTTPClient(
        HTTPSettings(min_interval=2, backoff_base=0.5),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleep,
        monotonic=lambda: now[0],
        random=lambda: 0,
    )
    wrapper.get_json("https://example.test/first")
    wrapper.get_json("https://example.test/second")
    assert starts == [0, 2, 4]


def test_exponential_backoff_and_jitter_are_bounded():
    sleeps = []
    wrapper = RetryingHTTPClient(
        HTTPSettings(min_interval=0, attempts=4, backoff_base=2, max_backoff=6),
        client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503))),
        sleep=sleeps.append,
        random=lambda: 1,
    )
    with pytest.raises(CollectionError, match="after 4 attempts"):
        wrapper.get_json("https://example.test/jobs")
    assert sleeps == [2.5, 5, 6]
