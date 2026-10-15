"""Small, conservative rules; missing facts remain missing."""

import html
import math
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

from jobradar.models import RemoteType


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.suppressed = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in {"script", "style"}:
            self.suppressed += 1
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.suppressed = max(0, self.suppressed - 1)
        if tag in {"p", "div", "li", "h1", "h2", "h3", "h4", "tr", "section"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.suppressed:
            self.parts.append(data)


def plain_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    # Greenhouse commonly returns entity-escaped HTML rather than literal tags.
    parser = _TextExtractor()
    parser.feed(html.unescape(value))
    parser.close()
    lines = [
        re.sub(r"[\t \xa0]+", " ", line).strip() for line in "".join(parser.parts).splitlines()
    ]
    return "\n".join(line for line in lines if line)


def optional_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def required_string(value: Any, field: str) -> str:
    result = optional_string(value)
    if result is None:
        raise ValueError(f"missing or invalid {field}")
    return result


def source_id(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).strip():
        raise ValueError("missing or invalid id")
    return str(value)


def parse_timestamp(value: Any) -> datetime | None:
    """Only accept timestamps carrying an explicit timezone (or Unix milliseconds)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            return None
        try:
            return datetime.fromtimestamp(value / 1000, UTC)
        except (ValueError, OverflowError, OSError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(UTC) if parsed.tzinfo else None
        except ValueError:
            return None
    return None


def number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def salary_fields(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    minimum = number(data.get("min", data.get("minValue")))
    maximum = number(data.get("max", data.get("maxValue")))
    if minimum is not None and maximum is not None and maximum < minimum:
        return {}
    if minimum is None and maximum is None:
        return {}
    return {
        "salary_min": minimum,
        "salary_max": maximum,
        "salary_currency": optional_string(data.get("currency")),
        "salary_interval": optional_string(data.get("interval", data.get("unit"))),
    }


def seniority(title: str) -> str | None:
    rules = {
        "intern": r"\b(?:intern|internship|стаж[её]р)\b",
        "junior": r"\b(?:junior|jr\.?|младший)\b",
        "senior": r"\b(?:senior|sr\.?|старший)\b",
        "staff": r"\bstaff\b",
        "principal": r"\bprincipal\b",
        "lead": r"\b(?:lead|ведущий)\b",
        "manager": r"\bmanager\b",
        "director": r"\b(?:director|head of)\b",
    }
    matches = [name for name, pattern in rules.items() if re.search(pattern, title, re.I)]
    return matches[0] if len(matches) == 1 else None


def remote_type(location: str | None, workplace: str | None = None) -> RemoteType:
    def classify(value: str) -> RemoteType:
        types: set[RemoteType] = set()
        if re.search(r"\b(?:hybrid|гибрид\w*)\b", value, re.I):
            return "hybrid"
        if re.search(r"\b(?:remote|удал[её]н\w*)\b", value, re.I):
            types.add("remote")
        if re.search(r"\b(?:on[- ]?site|in[- ]office|office[- ]based|офис)\b", value, re.I):
            types.add("onsite")
        return types.pop() if len(types) == 1 else "unknown"

    # Explicit source metadata is stronger evidence than a location hint.
    explicit = classify(workplace or "")
    return explicit if explicit != "unknown" else classify(location or "")


def restrictions(location: str | None, description: str, remote: RemoteType) -> str | None:
    if remote != "remote":
        return None
    evidence: list[str] = []
    if location and location.casefold().strip() not in {"remote", "удалённо", "удаленно"}:
        evidence.append(location)
    # Preserve explicit eligibility evidence verbatim, without inferring countries.
    pattern = (
        r"(?:must|need to|have to)\s+(?:be\s+)?(?:based|located|reside)|"
        r"(?:remote|work remotely)\s+(?:only\s+)?(?:within|from|in)\s+|"
        r"(?:eligible|authorized|authorised) to work|"
        r"(?:remote|applicants|candidates)\s+(?:in\s+)?[^.\n]{0,50}\bonly\b|"
        r"worldwide|work from anywhere"
    )
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", description):
        if re.search(pattern, sentence, re.I) and sentence.strip() not in evidence:
            evidence.append(sentence.strip())
    return "\n".join(evidence) or None
