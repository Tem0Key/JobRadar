"""Published workplace evidence independent of search preferences or database schema."""

from typing import Any


def workplace_locations(raw_data: dict[str, Any]) -> list[str]:
    """Read job-level posting locations; company-wide prose is never location evidence."""
    values: list[str] = []
    metadata = raw_data.get("metadata")
    for field in metadata if isinstance(metadata, list) else []:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name", "")).strip().casefold()
        if name not in {"job posting location", "job posting locations"}:
            continue
        value = field.get("value")
        values.extend(value if isinstance(value, list) else [value])
    values = [value.strip() for value in values if isinstance(value, str) and value.strip()]
    if not values:
        offices = raw_data.get("offices")
        for office in offices if isinstance(offices, list) else []:
            if isinstance(office, dict):
                values.append(office.get("location") or office.get("name"))
    generic = {"remote", "hybrid", "onsite", "on-site", "distributed", "global", "virtual"}
    return list(
        dict.fromkeys(
            value.strip()
            for value in values
            if isinstance(value, str) and value.strip() and value.strip().casefold() not in generic
        )
    )
