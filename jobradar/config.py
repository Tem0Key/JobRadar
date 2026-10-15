from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from jobradar.models import RemoteType


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SourceConfig(StrictModel):
    name: str = Field(min_length=1)
    source: Literal["greenhouse", "lever"]
    identifier: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    region: Literal["global", "eu"] = "global"
    enabled: bool = True

    @model_validator(mode="after")
    def valid_region(self) -> "SourceConfig":
        if self.source == "greenhouse" and self.region != "global":
            raise ValueError("region is only supported for Lever")
        return self

    @property
    def board_key(self) -> str:
        return f"{self.source}:{self.region}:{self.identifier}"


class HTTPSettings(StrictModel):
    timeout: float = Field(default=20, gt=0, le=300)
    attempts: int = Field(default=3, ge=1, le=8)
    min_interval: float = Field(default=1, ge=0, le=60)
    backoff_base: float = Field(default=1, ge=0, le=30)
    max_backoff: float = Field(default=60, gt=0, le=300)
    page_size: int = Field(default=100, ge=1, le=1000)
    max_pages: int = Field(default=100, ge=1, le=10000)


class SourcesConfig(StrictModel):
    http: HTTPSettings = Field(default_factory=HTTPSettings)
    mark_inactive_on_complete: bool = False
    allow_empty_deactivation: bool = False
    sources: list[SourceConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_boards(self) -> "SourcesConfig":
        keys = [source.board_key for source in self.sources]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate source board configuration")
        return self


class GeographyRules(StrictModel):
    target_location: str = "Costa Rica"
    allowed_location_terms: list[str] = Field(default_factory=lambda: ["Costa Rica", "Коста-Рика"])
    worldwide_terms: list[str] = Field(default_factory=lambda: ["worldwide", "work from anywhere"])
    allowed_remote_region_terms: list[str] = Field(default_factory=list)
    blocked_location_terms: list[str] = Field(default_factory=list)
    hard_exclusion_phrases: list[str] = Field(default_factory=list)


class SearchProfile(StrictModel):
    name: str = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)
    title_keywords: list[str] = Field(default_factory=list)
    description_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    exclude_title_keywords: list[str] = Field(default_factory=list)
    companies: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    remote_types: list[RemoteType] = Field(default_factory=list)
    seniorities: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    include_unknown: bool = True
    geography: GeographyRules = Field(default_factory=GeographyRules)


class ProfilesConfig(StrictModel):
    default_profile: str | None = None
    profiles: list[SearchProfile] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_names(self) -> "ProfilesConfig":
        names = [profile.name.casefold() for profile in self.profiles]
        if len(names) != len(set(names)):
            raise ValueError("profile names must be unique")
        if self.default_profile is not None and self.default_profile.casefold() not in names:
            raise ValueError("default_profile must name a configured profile")
        if self.default_profile is not None:
            self.default_profile = next(
                profile.name
                for profile in self.profiles
                if profile.name.casefold() == self.default_profile.casefold()
            )
        return self

    def get(self, name: str) -> SearchProfile:
        for profile in self.profiles:
            if profile.name.casefold() == name.casefold():
                return profile
        raise ValueError(f"Unknown profile: {name}")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JOBRADAR_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    database_url: str = "sqlite:///data/jobradar.db"
    sources_file: Path = Field(default_factory=lambda: _default_config("sources"))
    profiles_file: Path = Field(default_factory=lambda: _default_config("profiles"))


def _default_config(name: str) -> Path:
    personal = Path("config") / f"{name}.yaml"
    return personal if personal.is_file() else Path("config") / f"{name}.example.yaml"


def read_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a YAML mapping in {path}")
    return data


def load_sources(path: Path) -> SourcesConfig:
    return SourcesConfig.model_validate(read_yaml(path))


def load_profiles(path: Path) -> ProfilesConfig:
    return ProfilesConfig.model_validate(read_yaml(path))
