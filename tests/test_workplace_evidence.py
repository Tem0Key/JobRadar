from jobradar.config import GeographyRules
from jobradar.filters import assess_geography
from jobradar.models import Job
from jobradar.models.locations import workplace_locations


def job(**values):
    return Job(
        source="greenhouse",
        source_job_id="1",
        source_board="greenhouse:global:test",
        company="Test",
        title="Network Security Engineer",
        url="https://example.com/1",
        **values,
    )


def test_foreign_offices_resolve_generic_hybrid_scope():
    vacancy = job(
        location="Hybrid",
        remote_type="hybrid",
        raw_data={
            "offices": [{"name": "Singapore", "location": "Singapore"}],
        },
    )
    eligibility, reasons = assess_geography(vacancy, GeographyRules())
    assert eligibility == "excluded"
    assert "Singapore" in reasons[0]
    assert vacancy.location == "Hybrid"  # original source location stays unchanged


def test_costa_rica_office_is_an_allowed_alternative():
    vacancy = job(
        location="Hybrid",
        remote_type="hybrid",
        raw_data={
            "metadata": [{"name": "Job Posting Location", "value": ["Austin, US", "Costa Rica"]}],
        },
    )
    assert assess_geography(vacancy, GeographyRules())[0] == "allowed"


def test_office_does_not_limit_worldwide_remote_hiring():
    vacancy = job(
        location="Remote worldwide",
        remote_type="remote",
        raw_data={
            "offices": [{"location": "United States"}],
        },
    )
    assert assess_geography(vacancy, GeographyRules())[0] == "allowed"


def test_posting_metadata_precedes_office_default_and_handles_invalid_values():
    raw = {
        "offices": [{"location": "Singapore"}],
        "metadata": [{"name": "Job Posting Location", "value": [None, "Costa Rica", "Costa Rica"]}],
    }
    assert workplace_locations(raw) == ["Costa Rica"]
    assert workplace_locations({"offices": [{"name": "Hybrid"}, None]}) == []
