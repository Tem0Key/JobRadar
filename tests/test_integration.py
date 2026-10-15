"""Explicit opt-in only: JOBRADAR_INTEGRATION=1 python -m pytest -m integration."""

import os

import pytest

from jobradar.collectors import GreenhouseCollector, LeverCollector, RetryingHTTPClient
from jobradar.config import HTTPSettings, SourceConfig

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("JOBRADAR_INTEGRATION") != "1", reason="real API opt-in disabled"),
]


@pytest.mark.parametrize(
    "source,identifier,collector",
    [
        ("greenhouse", "cloudflare", GreenhouseCollector),
        ("greenhouse", "torq", GreenhouseCollector),
        ("lever", "lever", LeverCollector),
        ("lever", "canarytechnologies", LeverCollector),
        ("lever", "atmosera", LeverCollector),
        ("lever", "leverdemo", LeverCollector),
    ],
)
def test_public_board(source, identifier, collector):
    board = SourceConfig(name=identifier, source=source, identifier=identifier)
    with RetryingHTTPClient(HTTPSettings(timeout=30)) as http:
        result = collector(http).collect(board)
    assert result.complete, result.errors
    assert not result.errors
    # Empty boards are valid; no dependence on employers having current openings.
    for job in result.jobs:
        assert job.source_job_id and job.url
        assert job.raw_data
