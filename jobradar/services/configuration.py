"""Validated, atomic configuration editing for local management surfaces."""

from pathlib import Path
from threading import RLock

import yaml

from jobradar.config import (
    ProfilesConfig,
    Settings,
    SourcesConfig,
    load_profiles,
    load_sources,
)
from jobradar.services.export import write_export


class ConfigurationStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = RLock()
        # Examples are starter templates. Keep user edits in a personal sibling.
        for field in ("sources_file", "profiles_file"):
            path = getattr(settings, field)
            personal = self._personal_path(path)
            if personal != path and personal.is_file():
                setattr(settings, field, personal)

    @staticmethod
    def _personal_path(path: Path) -> Path:
        if path.name in {"sources.example.yaml", "profiles.example.yaml"}:
            return path.with_name(path.name.replace(".example.yaml", ".yaml"))
        return path

    def load_sources(self) -> SourcesConfig:
        with self._lock:
            return load_sources(self.settings.sources_file)

    def load_profiles(self) -> ProfilesConfig:
        with self._lock:
            return load_profiles(self.settings.profiles_file)

    def save_sources(self, value: SourcesConfig) -> SourcesConfig:
        validated = SourcesConfig.model_validate(value.model_dump())
        with self._lock:
            path = self._personal_path(self.settings.sources_file)
            self._write(path, validated.model_dump(mode="json"))
            self.settings.sources_file = path
        return validated

    def save_profiles(self, value: ProfilesConfig) -> ProfilesConfig:
        validated = ProfilesConfig.model_validate(value.model_dump())
        with self._lock:
            path = self._personal_path(self.settings.profiles_file)
            self._write(path, validated.model_dump(mode="json"))
            self.settings.profiles_file = path
        return validated

    @staticmethod
    def _write(path: Path, value: dict) -> None:
        content = "# Saved by JobRadar web management.\n" + yaml.safe_dump(
            value, allow_unicode=True, sort_keys=False, width=100
        )
        write_export(path, content)
