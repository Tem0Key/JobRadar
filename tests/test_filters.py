import pytest

from jobradar.config import GeographyRules, SearchProfile
from jobradar.filters import assess_geography, evaluate
from jobradar.models import Job


@pytest.fixture
def job():
    return Job(
        source="greenhouse",
        source_job_id="123",
        source_board="greenhouse:global:example",
        company="Example Inc",
        title="Senior Network Security Engineer",
        description="Manage Palo Alto firewall and VPN infrastructure.",
        remote_type="remote",
        location="Remote — Costa Rica",
        language="en",
        seniority="senior",
        url="https://example.com/jobs/123",
    )


def profile(**changes):
    return SearchProfile(name="Network Security", **changes)


def test_keyword_groups_and_alternatives_are_explainable(job):
    result = evaluate(
        job,
        profile(
            keywords=["SOC", "firewall"],
            title_keywords=["Network Security"],
            description_keywords=["NGFW", "Palo Alto"],
        ),
    )
    assert result.matched
    assert result.matched_keywords == ["firewall", "Network Security", "Palo Alto"]
    assert result.eligibility == "allowed"
    assert all("matched" in reason for reason in result.reasons)
    assert not evaluate(job, profile(keywords=["firewall"], title_keywords=["SOC"])).matched


@pytest.mark.parametrize(
    ("keyword", "title", "description", "expected"),
    [
        ("SOC", "Social Media Engineer", "Social infrastructure", False),
        ("SOC", "SOC Engineer", "", True),
        ("ИБ", "Разработчик", "Спасибо за интерес к вакансии", False),
        ("ИБ", "Инженер ИБ", "", True),
        ("VPN", "VPNs engineer", "", False),
        ("VPN", "Engineer", "VPN, firewall", True),
        ("network security", "NETWORK  SECURITY Engineer", "", True),
    ],
)
def test_keyword_boundaries_and_casefold(job, keyword, title, description, expected):
    job = job.model_copy(update={"title": title, "description": description})
    assert evaluate(job, profile(keywords=[keyword])).matched is expected


def test_exclusion_keyword_wins_over_positive_keyword(job):
    result = evaluate(job, profile(keywords=["firewall"], exclude_keywords=["VPN"]))
    assert not result.matched
    assert "Excluded keywords: VPN" in result.reasons


def test_exclusion_keywords_also_use_word_boundaries(job):
    job.description = "We operate social platforms and firewalls."
    assert evaluate(job, profile(exclude_keywords=["SOC"])).matched


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"companies": ["example inc"]}, True),
        ({"companies": ["Example"]}, False),
        ({"locations": ["costa rica"]}, True),
        ({"locations": ["Canada"]}, False),
        ({"remote_types": ["remote"]}, True),
        ({"remote_types": ["onsite"]}, False),
        ({"seniorities": ["SENIOR", "lead"]}, True),
        ({"seniorities": ["junior"]}, False),
        ({"languages": ["EN"]}, True),
        ({"languages": ["ru"]}, False),
        ({"companies": ["example inc"], "languages": ["ru"]}, False),
    ],
)
def test_metadata_filters(job, filters, expected):
    assert evaluate(job, profile(**filters)).matched is expected


@pytest.mark.parametrize("include_unknown", [True, False])
def test_missing_field_respects_include_unknown(job, include_unknown):
    job.seniority = None
    result = evaluate(job, profile(seniorities=["senior"], include_unknown=include_unknown))
    assert result.matched is include_unknown
    assert result.eligibility == "needs_review"
    assert any("seniorities" in reason for reason in result.reasons)


def test_empty_description_with_description_filter_requires_review(job):
    job.description = ""
    result = evaluate(job, profile(description_keywords=["firewall"]))
    assert result.matched
    assert result.eligibility == "needs_review"
    assert not evaluate(
        job, profile(description_keywords=["firewall"], include_unknown=False)
    ).matched


@pytest.mark.parametrize("query", ["FIREWALL", "example inc", "COSTA RICA", "Security"])
def test_free_text_search_uses_all_searchable_fields(job, query):
    assert evaluate(job, query=query).matched


def test_free_text_search_does_not_require_word_boundaries(job):
    assert evaluate(job, query="fire").matched
    assert not evaluate(job, query="astronaut").matched


def test_no_profile_inventory_does_not_suppress_foreign_jobs(job):
    job.remote_type = "onsite"
    job.location = "Toronto, Canada"
    result = evaluate(job, None, "firewall")
    assert result.matched
    assert result.eligibility == "excluded"
    assert not evaluate(job, profile()).matched


def test_remote_by_itself_is_not_worldwide(job):
    job.location = "Remote"
    result = evaluate(job, profile())
    assert result.matched
    assert result.eligibility == "needs_review"
    assert not evaluate(job, profile(include_unknown=False)).matched


@pytest.mark.parametrize("location", ["Remote (US)", "Remote — EMEA", "Remote, Canada"])
def test_specific_foreign_remote_scope_is_never_inferred_worldwide(job, location):
    job.location = location
    job.description = "Work from anywhere. Manage firewalls."
    result = evaluate(job, profile())
    assert result.eligibility == "needs_review"


@pytest.mark.parametrize(
    "description",
    [
        "We serve customers worldwide and offer a remote role.",
        "We have offices in Costa Rica and the US. This role is remote.",
        "Remote workers receive worldwide travel benefits.",
        "Our worldwide clients need remote infrastructure support.",
        "This role is not remote worldwide.",
        "We cannot work from anywhere.",
    ],
)
def test_unrelated_or_negated_geography_is_not_permission(job, description):
    job.location = "Remote"
    job.description = description
    assert evaluate(job, profile()).eligibility == "needs_review"


@pytest.mark.parametrize(
    "description",
    [
        "This role is remote worldwide.",
        "You can work from Costa Rica.",
        "This position is open to candidates in Costa Rica.",
        "We hire in Costa Rica.",
        "Work from anywhere.",
    ],
)
def test_explicit_description_workplace_permission(job, description):
    job.location = "Remote"
    job.description = description
    assert evaluate(job, profile()).eligibility == "allowed"


def test_regions_are_configured_and_require_workplace_evidence(job):
    rules = GeographyRules(allowed_remote_region_terms=["LATAM"])
    job.location = "Remote — LATAM"
    assert evaluate(job, profile(geography=rules)).eligibility == "allowed"
    assert evaluate(job, profile()).eligibility == "needs_review"
    job.location = "Remote"
    job.description = "We serve LATAM customers."
    assert evaluate(job, profile(geography=rules)).eligibility == "needs_review"
    job.description = "We hire in LATAM."
    assert evaluate(job, profile(geography=rules)).eligibility == "allowed"


def test_blocked_location_terms_do_not_scan_general_company_description(job):
    rules = GeographyRules(blocked_location_terms=["United States", "US"])
    job.description = "Our customers include the United States and Europe."
    assert evaluate(job, profile(geography=rules)).eligibility == "allowed"
    job.location = "Remote (US)"
    result = evaluate(job, profile(geography=rules))
    assert not result.matched
    assert result.eligibility == "excluded"


def test_hard_exclusion_in_description_overrides_worldwide_location(job):
    rules = GeographyRules(hard_exclusion_phrases=["US residents only"])
    job.location = "Remote — Worldwide"
    job.description = "Firewall engineer. US residents only."
    result = evaluate(job, profile(geography=rules))
    assert not result.matched
    assert result.eligibility == "excluded"
    assert "US residents only" in result.eligibility_reasons[0]


@pytest.mark.parametrize(
    "description",
    [
        "Remote worldwide except Costa Rica.",
        "Work from anywhere, excluding Costa Rica.",
        "This job is not available in Costa Rica.",
        "We do not hire in Costa Rica.",
    ],
)
def test_explicit_exclusion_of_configured_target_wins(job, description):
    job.location = "Remote — Worldwide"
    job.description = description
    assert evaluate(job, profile()).eligibility == "excluded"


def test_explicit_exclusion_of_permitted_region_wins(job):
    job.location = "Remote — Worldwide"
    job.description = "Remote worldwide except LATAM."
    rules = GeographyRules(allowed_remote_region_terms=["LATAM"])
    assert evaluate(job, profile(geography=rules)).eligibility == "excluded"


@pytest.mark.parametrize("remote_type", ["onsite", "hybrid"])
def test_onsite_and_hybrid_require_target_location(job, remote_type):
    job.remote_type = remote_type
    assert evaluate(job, profile()).eligibility == "allowed"
    job.location = "Berlin, Germany"
    assert evaluate(job, profile()).eligibility == "excluded"
    job.location = None
    assert evaluate(job, profile()).eligibility == "needs_review"


def test_structured_restrictions_override_ambiguous_remote_text(job):
    job.location = "Remote"
    job.remote_restrictions = "Costa Rica"
    assert evaluate(job, profile()).eligibility == "allowed"
    job.remote_restrictions = "US only"
    rules = GeographyRules(blocked_location_terms=["US only"])
    assert evaluate(job, profile(geography=rules)).eligibility == "excluded"


def test_result_serializes_explanations(job):
    result = evaluate(job, profile(keywords=["firewall"]))
    assert result.model_dump()["matched_keywords"] == ["firewall"]
    eligibility, reasons = assess_geography(job, GeographyRules())
    assert eligibility == "allowed"
    assert reasons


@pytest.mark.parametrize(
    "location", ["Remote (US)", "Remote United States", "Remote — U.S.", "Remote Europe"]
)
def test_configured_foreign_remote_aliases_are_excluded(job, location):
    rules = GeographyRules(blocked_location_terms=["US", "U.S.", "United States", "Europe"])
    job.location = location
    job.description = "We offer worldwide opportunities."
    result = evaluate(job, profile(geography=rules))
    assert not result.matched
    assert result.eligibility == "excluded"


@pytest.mark.parametrize(
    "description",
    [
        "Candidates must reside in US.",
        "You must live in the United States.",
        "Applicants need to be located in U.S.",
        "You have to be based in the United States.",
        "You must be authorised to work in the United States.",
        "You must be legally authorized to work in US.",
        "Remote from Europe.",
        "US residents only.",
        "Applicants must be a US citizen.",
        "Candidates must hold United States citizenship.",
        "This role is US only.",
    ],
)
def test_configured_mandatory_foreign_scope_overrides_worldwide(job, description):
    rules = GeographyRules(blocked_location_terms=["US", "U.S.", "United States", "Europe"])
    job.location = "Remote Worldwide"
    job.description = description
    result = evaluate(job, profile(geography=rules))
    assert result.eligibility == "excluded"
    assert not result.matched


@pytest.mark.parametrize(
    "description", ["Must reside in Australia.", "Remote worldwide, but must live in Australia."]
)
def test_unconfigured_mandatory_country_cannot_be_overridden_by_worldwide(job, description):
    job.location = "Remote Worldwide"
    job.description = description
    assert evaluate(job, profile()).eligibility == "needs_review"


@pytest.mark.parametrize(
    "description",
    [
        "We serve worldwide customers in US.",
        "Our US offices help customers worldwide.",
        "Our customers must reside in the United States.",
        "Worldwide travel benefits for all employees.",
    ],
)
def test_incidental_description_excerpts_are_not_structured_permissions(job, description):
    rules = GeographyRules(blocked_location_terms=["US", "United States"])
    job.location = "Remote"
    job.description = description
    job.remote_restrictions = description  # Collector stores explicit evidence verbatim.
    assert evaluate(job, profile(geography=rules)).eligibility == "needs_review"


def test_worldwide_remote_with_incidental_us_office_is_ambiguous(job):
    rules = GeographyRules(blocked_location_terms=["US", "United States"])
    job.location = "Remote Worldwide (US office)"
    job.description = "Work from anywhere."
    assert evaluate(job, profile(geography=rules)).eligibility == "needs_review"


def test_unknown_compact_restriction_prevents_false_worldwide_permission(job):
    job.location = "Remote Worldwide"
    job.remote_restrictions = "Europe only"
    assert evaluate(job, profile()).eligibility == "needs_review"


def test_compact_configured_restriction_can_confirm_workplace(job):
    job.location = "Remote"
    job.remote_restrictions = "Remote — Costa Rica"
    assert evaluate(job, profile()).eligibility == "allowed"


def test_foreign_data_scope_is_not_automatically_a_workplace_restriction(job):
    rules = GeographyRules(blocked_location_terms=["US"])
    job.location = "Remote Worldwide"
    job.description = "Work from anywhere. Our training dataset is US only."
    assert evaluate(job, profile(geography=rules)).eligibility == "allowed"


@pytest.mark.parametrize("remote_type", ["remote", "hybrid", "onsite"])
@pytest.mark.parametrize(
    "location", ["Costa Rica; United States", "Costa Rica or United States", "Costa Rica / US"]
)
def test_foreign_alternative_does_not_discard_explicit_target_workplace(job, remote_type, location):
    rules = GeographyRules(blocked_location_terms=["US", "United States", "Canada"])
    job.remote_type = remote_type
    job.location = location
    result = evaluate(job, profile(geography=rules))
    assert result.matched
    assert result.eligibility == "allowed"


def test_permitted_remote_region_and_foreign_country_are_alternatives(job):
    rules = GeographyRules(
        allowed_remote_region_terms=["LATAM"], blocked_location_terms=["US", "Canada"]
    )
    job.location = "Remote LATAM; Canada"
    assert evaluate(job, profile(geography=rules)).eligibility == "allowed"


@pytest.mark.parametrize(
    "location", ["Costa Rica; United States only", "Costa Rica; only US", "Costa Rica; US only"]
)
def test_foreign_only_scope_overrides_target_mention(job, location):
    rules = GeographyRules(blocked_location_terms=["US", "United States"])
    job.location = location
    assert evaluate(job, profile(geography=rules)).eligibility == "excluded"


def test_foreign_citizenship_overrides_alternative_target_location(job):
    rules = GeographyRules(blocked_location_terms=["US", "United States"])
    job.location = "Costa Rica; United States"
    job.description = "Candidates must be US citizens."
    assert evaluate(job, profile(geography=rules)).eligibility == "excluded"


def test_conflicting_separate_geography_fields_require_review(job):
    rules = GeographyRules(blocked_location_terms=["US", "United States"])
    job.location = "Remote United States"
    job.remote_restrictions = "Costa Rica"
    assert evaluate(job, profile(geography=rules)).eligibility == "needs_review"


def test_explicit_worldwide_description_conflicting_with_foreign_metadata_requires_review(job):
    rules = GeographyRules(blocked_location_terms=["US", "United States"])
    job.location = "Remote United States"
    job.description = "This role is remote worldwide."
    assert evaluate(job, profile(geography=rules)).eligibility == "needs_review"
