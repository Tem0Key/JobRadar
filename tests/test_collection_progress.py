import httpx

from jobradar.collectors import RetryingHTTPClient
from jobradar.config import HTTPSettings, SourceConfig, SourcesConfig
from jobradar.database import Repository
from jobradar.services.collection import collect_jobs


def sources():
    return SourcesConfig(
        sources=[
            SourceConfig(name="One", source="greenhouse", identifier="one"),
            SourceConfig(name="Two", source="greenhouse", identifier="two"),
        ]
    )


def test_progress_callback_receives_each_persisted_board():
    notifications = []
    with Repository("sqlite://") as repository:

        def progress(summary):
            # Summary must be sent only after the snapshot is saved.
            assert len(repository.stats()["recent_runs"]) == len(notifications) + 1
            notifications.append(summary)
            # Observers cannot mutate the collector's final results.
            summary["complete"] = False
            summary["errors"].append("observer change")

        with httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"jobs": [], "meta": {"total": 0}})
            )
        ) as client:
            with RetryingHTTPClient(HTTPSettings(min_interval=0), client=client) as http:
                summaries = collect_jobs(sources(), repository, http=http, on_board=progress)
    assert len(notifications) == 2
    assert all(summary["complete"] for summary in summaries)
    assert not any(summary["errors"] for summary in summaries)


def test_progress_failure_does_not_mark_completed_board_failed_or_stop_collection(caplog):
    observed = []

    def broken_progress(summary):
        observed.append(summary["board"])
        raise RuntimeError("progress view unavailable")

    with Repository("sqlite://") as repository:
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"jobs": [], "meta": {"total": 0}})
            )
        ) as client:
            with RetryingHTTPClient(HTTPSettings(min_interval=0), client=client) as http:
                summaries = collect_jobs(sources(), repository, http=http, on_board=broken_progress)
        assert len(repository.stats()["recent_runs"]) == 2
    assert len(observed) == 2
    assert all(summary["complete"] and not summary["errors"] for summary in summaries)
    assert "progress view unavailable" in caplog.text


def test_progress_includes_failed_board_summary():
    observed = []
    with Repository("sqlite://") as repository:
        with httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(503))
        ) as client:
            with RetryingHTTPClient(
                HTTPSettings(attempts=1, min_interval=0), client=client
            ) as http:
                summaries = collect_jobs(sources(), repository, http=http, on_board=observed.append)
    assert observed == summaries
    assert len(observed) == 2
    assert all(not summary["complete"] and summary["errors"] for summary in observed)
