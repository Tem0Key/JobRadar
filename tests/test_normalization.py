from datetime import UTC, datetime

import pytest

from jobradar.collectors.normalization import (
    parse_timestamp,
    plain_text,
    remote_type,
    restrictions,
    salary_fields,
    seniority,
)


@pytest.mark.parametrize(
    ("location", "workplace", "expected"),
    [
        ("Remote", None, "remote"),
        ("Remote - US", None, "remote"),
        ("San José, Costa Rica", None, "unknown"),
        (None, None, "unknown"),
        ("London", "hybrid", "hybrid"),
        ("Remote", "on-site", "onsite"),
        ("Remote / Onsite", None, "unknown"),
        ("Hybrid - Remote and office", None, "hybrid"),
        ("Удалённо", None, "remote"),
    ],
)
def test_remote_metadata_is_conservative(location, workplace, expected):
    assert remote_type(location, workplace) == expected


def test_remote_does_not_mean_worldwide():
    assert restrictions("Remote", "We use remote access tools.", "remote") is None
    assert restrictions("Remote - US", "", "remote") == "Remote - US"
    assert restrictions(None, "Must be based in Canada. Other text.", "remote") == (
        "Must be based in Canada."
    )
    assert restrictions("Remote", "We hire worldwide.", "remote") == "We hire worldwide."


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Senior Firewall Engineer", "senior"),
        ("Junior Network Engineer", "junior"),
        ("Ведущий сетевой инженер", "lead"),
        ("Network Engineer", None),
        ("Senior / Staff Network Engineer", None),
        ("Security Principal Investigator", "principal"),
    ],
)
def test_seniority_only_from_explicit_title_markers(title, expected):
    assert seniority(title) == expected


@pytest.mark.parametrize("value", [None, "2026-01-01", "invalid", True, float("inf")])
def test_unreliable_timestamps_remain_null(value):
    assert parse_timestamp(value) is None


def test_timestamp_conversion_to_utc():
    assert parse_timestamp("2026-01-01T12:00:00-06:00") == datetime(2026, 1, 1, 18, tzinfo=UTC)


@pytest.mark.parametrize(
    "data",
    [None, {}, {"min": "100000"}, {"min": True}, {"min": -1}, {"min": 10, "max": 5}],
)
def test_invalid_or_unpublished_salary_not_guessed(data):
    assert salary_fields(data) == {}


def test_html_text_keeps_every_section_and_decodes_entities():
    text = plain_text("&lt;p&gt;A &amp;amp; B&lt;/p&gt;&lt;ul&gt;&lt;li&gt;C&lt;/li&gt;&lt;/ul&gt;")
    assert text == "A & B\nC"
