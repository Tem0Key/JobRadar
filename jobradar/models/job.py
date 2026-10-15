from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

RemoteType = Literal["remote", "hybrid", "onsite", "unknown"]
JobStatus = Literal["active", "inactive", "unknown"]


def utc_now() -> datetime:
    return datetime.now(UTC)


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    source: str
    source_job_id: str
    source_board: str
    company: str
    title: str
    description: str = ""
    location: str | None = None
    remote_type: RemoteType = "unknown"
    remote_restrictions: str | None = None
    employment_type: str | None = None
    seniority: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_interval: str | None = None
    language: str | None = None
    url: str
    published_at: datetime | None = None
    source_updated_at: datetime | None = None
    first_seen_at: datetime = Field(default_factory=utc_now)
    last_seen_at: datetime = Field(default_factory=utc_now)
    status: JobStatus = "active"
    raw_data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source", "source_job_id", "source_board", "company", "title", "url")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("first_seen_at", "last_seen_at", "published_at", "source_updated_at")
    @classmethod
    def timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamps must include a timezone")
        return value.astimezone(UTC) if value else None
