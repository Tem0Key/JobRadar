"""Explainable search rules and deliberately conservative location assessment."""

import re
from typing import Literal

from pydantic import BaseModel, Field

from jobradar.config import GeographyRules, SearchProfile
from jobradar.models import Job
from jobradar.models.locations import workplace_locations

Eligibility = Literal["allowed", "excluded", "needs_review"]
_REQUIRED_SCOPE = (
    r"\b(?:must|need\s+to|have\s+to)\s+(?:be\s+)?"
    r"(?:(?:based|located)\s+in|(?:reside|live)\s+(?:in|within)|"
    r"(?:legally\s+)?(?:authorized|authorised|eligible)\s+to\s+work\s+(?:in|within))"
    r"\s+(?:the\s+)?"
    r"|\bremote\s+(?:work\s+)?(?:is\s+)?(?:only\s+)?(?:within|from|in)\s+(?:the\s+)?"
)
_INCIDENTAL_CONTEXT = (
    r"\b(?:benefits?|travel|customers?|clients?|offices?|headquarters|"
    r"headquartered|conferences?)\b"
)


class MatchResult(BaseModel):
    matched: bool
    matched_keywords: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    eligibility: Eligibility = "needs_review"
    eligibility_reasons: list[str] = Field(default_factory=list)


def _contains(text: str, term: str) -> bool:
    """Match Unicode words/phrases without finding SOC in social or ИБ in спасибо."""
    term = term.strip().casefold()
    if not term:
        return False
    expression = r"\s+".join(re.escape(part) for part in term.split())
    if term[0].isalnum() or term[0] == "_":
        expression = r"(?<!\w)" + expression
    if term[-1].isalnum() or term[-1] == "_":
        expression += r"(?!\w)"
    return re.search(expression, text.casefold()) is not None


def _terms_in(text: str, terms: list[str]) -> list[str]:
    return [term for term in terms if _contains(text, term)]


def matches_title(title: str, profile: SearchProfile) -> bool:
    """Cheap role gate for list queries; detail evaluation still explains every rule."""
    return not _terms_in(title, profile.exclude_title_keywords) and (
        not profile.title_keywords or bool(_terms_in(title, profile.title_keywords))
    )


def _clauses(text: str) -> list[str]:
    # Keep abbreviations such as U.S. intact while separating complete sentences.
    return re.split(r"(?<=[.!?;])\s+|\n+", text.casefold())


def _compact_scope(text: str, rules: GeographyRules) -> bool:
    """Recognize metadata-like scopes, not verbatim description sentences."""
    value = text.casefold()
    terms = [
        *rules.allowed_location_terms,
        *rules.worldwide_terms,
        *rules.allowed_remote_region_terms,
        *rules.blocked_location_terms,
        "remote",
        "remotely",
        "only",
        "from",
        "in",
        "within",
        "the",
        "worldwide",
        "удаленно",
        "удалённо",
        "только",
    ]
    for term in sorted(terms, key=len, reverse=True):
        if term.strip():
            expression = r"(?<!\w)" + re.escape(term.casefold()) + r"(?!\w)"
            value = re.sub(expression, " ", value)
    return not bool(re.search(r"\w", value))


def _mandatory_scope_mentions(text: str, terms: list[str]) -> list[str]:
    found = []
    for clause in _clauses(text):
        if re.search(_INCIDENTAL_CONTEXT, clause):
            continue
        for term in terms:
            if not term.strip():
                continue
            literal = re.escape(term.casefold())
            patterns = [
                f"(?:{_REQUIRED_SCOPE}){literal}(?!\\w)",
                r"(?<!\w)" + literal + r"\s+(?:residents?|citizens?)\s+only\b",
                r"\b(?:remote|role|position|job|applicants?|candidates?)\b[^.!?\n]{0,60}"
                + r"(?<!\w)"
                + literal
                + r"\s+only\b",
                r"\b(?:must|need\s+to|have\s+to)\s+(?:be\s+)?(?:a\s+)?"
                + literal
                + r"\s+(?:residents?|citizens?)\b",
                r"\b(?:must|need\s+to|have\s+to)\s+(?:have|hold)\s+(?:a\s+)?"
                + literal
                + r"\s+(?:citizenship|work\s+authorization)\b",
            ]
            if any(re.search(pattern, clause) for pattern in patterns):
                found.append(term)
    return list(dict.fromkeys(found))


def _scope_only_terms(text: str, terms: list[str]) -> list[str]:
    """'Only' attached to a structured foreign scope overrides alternative locations."""
    found = []
    for term in terms:
        if not term.strip():
            continue
        literal = re.escape(term.casefold())
        patterns = [
            r"(?<!\w)" + literal + r"\s+only\b",
            r"\bonly\s+(?:(?:in|within|from)\s+)?(?:the\s+)?" + literal + r"(?!\w)",
            r"\bтолько\s+(?:в\s+)?" + literal + r"(?!\w)",
        ]
        if any(re.search(pattern, text.casefold()) for pattern in patterns):
            found.append(term)
        elif re.search(r"\b(?:only|только)\b", term.casefold()) and _contains(text, term):
            found.append(term)
    return found


def _explicit_alternatives(text: str, allowed: list[str], blocked: list[str]) -> bool:
    if re.search(_INCIDENTAL_CONTEXT, text.casefold()):
        return False
    parts = re.split(r"[;,|/]|\b(?:or|and|или)\b", text.casefold())
    return (
        len(parts) > 1
        and any(_terms_in(part, allowed) for part in parts)
        and any(_terms_in(part, blocked) for part in parts)
    )


def _location_is_specific(text: str | None, rules: GeographyRules) -> bool:
    if not text:
        return False
    value = text.casefold()
    generic = [
        "remote",
        "remotely",
        "hybrid",
        "onsite",
        "on-site",
        "on site",
        "unknown",
        "multiple locations",
        "various locations",
        "location",
        "workplace",
        "anywhere",
        "удаленно",
        "удалённо",
        "гибрид",
        "неизвестно",
        *rules.worldwide_terms,
    ]
    for term in sorted(generic, key=len, reverse=True):
        value = re.sub(re.escape(term.casefold()), " ", value)
    return bool(re.search(r"\w", value))


def _description_workplace_terms(text: str, terms: list[str]) -> list[str]:
    """Require employment wording in the same short clause as a permitted location.

    Description text is not structured geography. This intentionally misses some valid
    phrasings rather than treating worldwide customers or company offices as eligibility.
    """
    found: list[str] = []
    clauses = _clauses(text)
    for term in terms:
        normalized = term.strip().casefold()
        if not normalized:
            continue
        for clause in clauses:
            if not _contains(clause, normalized):
                continue
            if re.search(r"\b(?:not|cannot|except|excluding|excludes)\b", clause):
                continue
            if re.search(_INCIDENTAL_CONTEXT, clause):
                continue
            literal = r"\s+".join(re.escape(part) for part in normalized.split())
            prefixes = [
                r"\b(?:work|working)\s+(?:remotely\s+)?(?:from|in|across)\s+",
                r"\b(?:hire|hiring|recruiting)\s+(?:people\s+)?(?:in|from|across)\s+",
                r"\bremote\s+(?:(?:role|position|job|work|is|available|open|based)\s+)*"
                r"(?:(?:from|in|across|throughout|within|anywhere|to)\s+)*",
                r"\b(?:candidates?|applicants?|you)\s+"
                r"(?:(?:can|may|must|be|are|based|located|reside|residing|living|in)\s+){1,8}",
                r"\b(?:role|position|job)\s+(?:is\s+)?(?:open|available)\s+"
                r"(?:to\s+)?(?:candidates?\s+)?(?:in\s+|from\s+)?",
                r"\b(?:работа|работать)\s+(?:удаленно\s+|удалённо\s+)?(?:из|в)\s+",
                f"(?:{_REQUIRED_SCOPE})",
            ]
            if any(re.search(prefix + literal + r"(?!\w)", clause) for prefix in prefixes):
                found.append(term)
                break
            if normalized in {"work from anywhere", "globally remote"}:
                found.append(term)
                break
    return found


def assess_geography(job: Job, rules: GeographyRules) -> tuple[Eligibility, list[str]]:
    """Assess geographic evidence independently of keyword relevance."""
    # Some boards put only "Hybrid" in location, while job-level offices carry the city.
    # Office locations constrain onsite/hybrid work; they do not constrain remote hiring.
    places = workplace_locations(job.raw_data)
    if places and job.remote_type in {"onsite", "hybrid"}:
        if not _location_is_specific(job.location, rules):
            job = job.model_copy(update={"location": "; ".join(places)})
    full_text = "\n".join(
        [job.title, job.description, job.location or "", job.remote_restrictions or ""]
    )
    hard = _terms_in(full_text, rules.hard_exclusion_phrases)
    if hard:
        return "excluded", [f"Explicit geographic restriction: {', '.join(hard)}"]

    # Locations come from configuration; these grammatical exclusions apply to any target.
    for term in [*rules.allowed_location_terms, *rules.allowed_remote_region_terms]:
        if not term.strip():
            continue
        literal = r"\s+".join(re.escape(part) for part in term.casefold().split())
        denial = (
            r"\b(?:except(?:\s+for)?|excluding|excludes?|not\s+(?:available|open|eligible)"
            r"\s+(?:in|to)|(?:cannot|can't)\s+(?:work|be\s+based)\s+(?:from|in)|"
            r"(?:do\s+not|don't)\s+hire\s+(?:in|from))\s+"
        )
        if re.search(denial + literal + r"(?!\w)", full_text.casefold()):
            return "excluded", [f"Configured target or region explicitly excluded: {term}"]

    target_terms = rules.allowed_location_terms
    remote_terms = [*target_terms, *rules.worldwide_terms, *rules.allowed_remote_region_terms]
    mandatory_blocked = _mandatory_scope_mentions(full_text, rules.blocked_location_terms)
    if mandatory_blocked:
        return "excluded", [f"Mandatory foreign workplace scope: {', '.join(mandatory_blocked)}"]

    restriction_lines = (job.remote_restrictions or "").splitlines()
    compact_restrictions = [line for line in restriction_lines if _compact_scope(line, rules)]
    descriptive_restrictions = [
        line for line in restriction_lines if line not in compact_restrictions
    ]
    description = "\n".join([job.description, *descriptive_restrictions])
    scopes = [job.location or "", *compact_restrictions]
    all_scope_allowed = any(_terms_in(scope, remote_terms) for scope in scopes)
    if job.remote_type == "remote":
        all_scope_allowed = all_scope_allowed or bool(
            _description_workplace_terms(description, remote_terms)
        )
    mixed_scope_review = False
    for scope in scopes:
        blocked = _terms_in(scope, rules.blocked_location_terms)
        if not blocked:
            continue
        foreign_only = _scope_only_terms(scope, rules.blocked_location_terms)
        if foreign_only:
            return "excluded", [
                f"Mandatory foreign-only workplace scope: {', '.join(foreign_only)}"
            ]
        eligible_terms = target_terms if job.remote_type in {"onsite", "hybrid"} else remote_terms
        if _terms_in(scope, eligible_terms):
            if not _explicit_alternatives(scope, eligible_terms, rules.blocked_location_terms):
                mixed_scope_review = True
            continue
        if all_scope_allowed or (
            job.remote_type == "remote" and re.search(_INCIDENTAL_CONTEXT, scope.casefold())
        ):
            mixed_scope_review = True
            continue
        return "excluded", [f"Blocked workplace location: {', '.join(blocked)}"]
    if mixed_scope_review:
        return "needs_review", ["Conflicting geographic scopes or incidental office location"]

    # An unrecognized mandatory country must not be overruled by a worldwide statement.
    for clause in _clauses(description):
        if re.search(_INCIDENTAL_CONTEXT, clause):
            continue
        for match in re.finditer(_REQUIRED_SCOPE, clause):
            scope = clause[match.end() :].strip()
            if not any(
                re.match(re.escape(term.casefold()) + r"(?!\w)", scope)
                for term in remote_terms
                if term.strip()
            ):
                return "needs_review", ["Mandatory workplace scope is not confirmed for the target"]
    for line in descriptive_restrictions:
        if re.search(_INCIDENTAL_CONTEXT, line.casefold()):
            continue
        if not _description_workplace_terms(line, remote_terms):
            return "needs_review", ["Unrecognized remote restriction requires manual review"]

    target_in_location = _terms_in(job.location or "", target_terms)
    target_in_restrictions = _terms_in("\n".join(compact_restrictions), target_terms)

    if job.remote_type in {"onsite", "hybrid"}:
        if target_in_location:
            return "allowed", [f"Workplace is in {rules.target_location}"]
        if _location_is_specific(job.location, rules):
            return "excluded", [
                f"{job.remote_type} workplace is outside the configured target: {job.location}"
            ]
        if _description_workplace_terms(description, target_terms):
            return "allowed", [f"Description explicitly permits work in {rules.target_location}"]
        return "needs_review", [f"{job.remote_type} workplace has no confirmed target location"]

    if job.remote_type == "remote":
        location_allowed = _terms_in(job.location or "", remote_terms)
        restriction_scope = "\n".join(compact_restrictions)
        restrictions_allowed = _terms_in(restriction_scope, remote_terms)
        for field, matches in [
            (job.location, location_allowed),
            (restriction_scope, restrictions_allowed),
        ]:
            if _location_is_specific(field, rules) and not matches:
                return "needs_review", [
                    "Remote workplace contains a scope not confirmed for the configured target"
                ]
        if location_allowed or restrictions_allowed:
            evidence = list(dict.fromkeys(location_allowed + restrictions_allowed))
            return "allowed", [f"Explicit remote workplace scope: {', '.join(evidence)}"]
        description_allowed = _description_workplace_terms(description, remote_terms)
        if description_allowed:
            return "allowed", [
                f"Description explicitly permits remote work: {', '.join(description_allowed)}"
            ]
        return "needs_review", ["Remote work is stated, but geographic eligibility is unconfirmed"]

    if target_in_location or target_in_restrictions:
        return "allowed", [f"Location is in {rules.target_location}; workplace type is unknown"]
    return "needs_review", ["Workplace type and target geographic eligibility are unconfirmed"]


def evaluate(
    job: Job, profile: SearchProfile | None = None, query: str | None = None
) -> MatchResult:
    """Combine groups with AND and alternatives with OR; flag uncertainty explicitly.

    Without a profile the inventory/free search never rejects on geography. A profile
    rejects explicit exclusions and retains uncertain jobs unless include_unknown=False.
    """
    rules = profile.geography if profile else GeographyRules()
    eligibility, eligibility_reasons = assess_geography(job, rules)
    result = MatchResult(
        matched=profile is None or eligibility != "excluded",
        eligibility=eligibility,
        eligibility_reasons=eligibility_reasons,
    )
    text = f"{job.title}\n{job.description}"
    if query and query.strip():
        query = query.strip()
        searchable = "\n".join(
            [
                text,
                job.location or "",
                job.company,
                *workplace_locations(job.raw_data),
            ]
        )
        if query.casefold() in searchable.casefold():
            result.matched_keywords.append(query)
            result.reasons.append(f"Query matched: {query}")
        else:
            result.matched = False
            result.reasons.append(f"Query did not match: {query}")

    if profile:
        excluded_titles = _terms_in(job.title, profile.exclude_title_keywords)
        if excluded_titles:
            result.matched = False
            result.reasons.append(f"Excluded title keywords: {', '.join(excluded_titles)}")
        excluded = _terms_in(text, profile.exclude_keywords)
        if excluded:
            result.matched = False
            result.reasons.append(f"Excluded keywords: {', '.join(excluded)}")

        def unknown(group: str) -> None:
            result.reasons.append(f"Missing value for filter: {group}")
            if not profile.include_unknown:
                result.matched = False
            if result.eligibility != "excluded":
                result.eligibility = "needs_review"
            result.eligibility_reasons.append(f"Filter field is unknown: {group}")

        for group, value, terms in [
            ("keywords", text, profile.keywords),
            ("title_keywords", job.title, profile.title_keywords),
            ("description_keywords", job.description, profile.description_keywords),
        ]:
            if not terms:
                continue
            if not value.strip():
                unknown(group)
                continue
            matches = _terms_in(value, terms)
            if matches:
                result.matched_keywords.extend(matches)
                result.reasons.append(f"{group} matched: {', '.join(matches)}")
            else:
                result.matched = False
                result.reasons.append(f"No match for filter: {group}")

        for group, value, values, substring in [
            ("companies", job.company, profile.companies, False),
            (
                "locations",
                "; ".join([job.location or "", *workplace_locations(job.raw_data)]),
                profile.locations,
                True,
            ),
            ("remote_types", job.remote_type, profile.remote_types, False),
            ("seniorities", job.seniority, profile.seniorities, False),
            ("languages", job.language, profile.languages, False),
        ]:
            if not values:
                continue
            if not value or value.casefold() == "unknown":
                unknown(group)
                continue
            normalized = value.casefold()
            matches = [
                option
                for option in values
                if (
                    option.casefold() in normalized
                    if substring
                    else option.casefold() == normalized
                )
            ]
            if matches:
                result.reasons.append(f"{group} matched: {', '.join(matches)}")
            else:
                result.matched = False
                result.reasons.append(f"No match for filter: {group}")
        if result.eligibility == "needs_review" and not profile.include_unknown:
            result.matched = False
            result.reasons.append("Unconfirmed eligibility excluded by include_unknown=false")

    result.matched_keywords = list(dict.fromkeys(result.matched_keywords))
    return result
