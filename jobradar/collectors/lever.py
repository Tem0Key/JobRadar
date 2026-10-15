"""Public Lever Postings API adapter with bounded skip/limit pagination."""

from typing import Any

from jobradar.collectors.base import CollectionError, Collector, CollectorResult
from jobradar.collectors.normalization import (
    optional_string,
    parse_timestamp,
    plain_text,
    remote_type,
    required_string,
    restrictions,
    salary_fields,
    seniority,
    source_id,
)
from jobradar.config import SourceConfig
from jobradar.models import Job


def _text_variant(item: dict[str, Any], key: str) -> str:
    value = item.get(f"{key}Plain")
    # Plain variants are already text; feeding them through an HTML parser could
    # drop literal comparison symbols such as <T> in technical job descriptions.
    if isinstance(value, str) and value.strip():
        return value.strip()
    return plain_text(item.get(key))


def _description(item: dict[str, Any]) -> str:
    if not isinstance(item.get("description"), str) and not isinstance(
        item.get("descriptionPlain"), str
    ):
        raise ValueError("missing or invalid description")
    sections = [_text_variant(item, "description")]
    lists = item.get("lists")
    if lists is not None and not isinstance(lists, list):
        raise ValueError("invalid description lists")
    for entry in lists or []:
        if not isinstance(entry, dict):
            raise ValueError("invalid description list entry")
        if entry.get("content") is not None and not isinstance(entry["content"], str):
            raise ValueError("invalid description list content")
        heading = plain_text(entry.get("text"))
        content = _text_variant(entry, "content")
        sections.append("\n".join(part for part in (heading, content) if part))
    sections.extend((_text_variant(item, "additional"), _text_variant(item, "salaryDescription")))
    return "\n\n".join(section for section in sections if section)


class LeverCollector(Collector):
    @staticmethod
    def normalize(item: dict[str, Any], source: SourceConfig) -> Job:
        title = required_string(item.get("text"), "text")
        categories = item.get("categories")
        if not isinstance(categories, dict):
            categories = {}
        location = optional_string(categories.get("location"))
        # Preserve all source locations where the public API supplies them.
        all_locations = categories.get("allLocations")
        if isinstance(all_locations, list):
            locations = [value for value in all_locations if isinstance(value, str) and value]
            if location and location not in locations:
                locations.insert(0, location)
            location = "; ".join(dict.fromkeys(locations)) or location
        description = _description(item)
        remote = remote_type(location, optional_string(item.get("workplaceType")))
        return Job(
            source="lever",
            source_job_id=source_id(item.get("id")),
            source_board=source.board_key,
            company=source.name,
            title=title,
            description=description,
            location=location,
            remote_type=remote,
            remote_restrictions=restrictions(location, description, remote),
            employment_type=optional_string(categories.get("commitment")),
            seniority=seniority(title),
            language=optional_string(item.get("language")),
            url=required_string(item.get("hostedUrl"), "hostedUrl"),
            # createdAt is posting creation, not a trustworthy publication date.
            published_at=parse_timestamp(item.get("publishedAt")),
            source_updated_at=parse_timestamp(item.get("updatedAt")),
            raw_data=item,
            **salary_fields(item.get("salaryRange")),
        )

    def collect(self, source: SourceConfig) -> CollectorResult:
        result = CollectorResult()
        host = "api.eu.lever.co" if source.region == "eu" else "api.lever.co"
        url = f"https://{host}/v0/postings/{source.identifier}"
        seen: set[str] = set()
        page_size = self.http.settings.page_size
        offset = 0
        for page in range(self.http.settings.max_pages):
            try:
                entries = self.http.get_json(
                    url, params={"mode": "json", "skip": offset, "limit": page_size}
                )
            except CollectionError as exc:
                result.complete = False
                result.errors.append(str(exc))
                return result
            if not isinstance(entries, list):
                result.complete = False
                result.errors.append("Invalid Lever postings envelope (expected an array)")
                return result
            if not entries:
                return result
            offset += len(entries)
            new_ids = 0
            for index, item in enumerate(entries):
                try:
                    if not isinstance(item, dict):
                        raise ValueError("job is not an object")
                    job = self.normalize(item, source)
                    if job.source_job_id in seen:
                        raise ValueError(f"duplicate job id {job.source_job_id}")
                    seen.add(job.source_job_id)
                    new_ids += 1
                    result.jobs.append(job)
                except (ValueError, TypeError) as exc:
                    result.complete = False
                    result.errors.append(f"Lever page {page + 1}, item {index}: {exc}")
            if new_ids == 0:
                result.complete = False
                result.errors.append("Lever pagination made no progress; stopping")
                return result
        result.complete = False
        result.errors.append("Lever maximum page limit reached before an empty page")
        return result
