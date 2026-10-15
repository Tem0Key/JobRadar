from pathlib import Path

import pytest
from pydantic import ValidationError

from jobradar.config import (
    ProfilesConfig,
    Settings,
    SourceConfig,
    SourcesConfig,
    load_profiles,
    load_sources,
    read_yaml,
)
from jobradar.models import Job


def test_example_configs():
    sources = load_sources(Path("config/sources.example.yaml")).sources
    assert {source.identifier for source in sources if source.enabled} == {
        "cloudflare",
        "torq",
        "canarytechnologies",
        "atmosera",
    }
    assert load_profiles(Path("config/profiles.example.yaml")).get("network security").keywords


def test_default_profile_must_exist():
    with pytest.raises(ValidationError, match="default_profile"):
        ProfilesConfig(default_profile="missing", profiles=[])
    config = ProfilesConfig.model_validate(
        {
            "default_profile": "NETWORK",
            "profiles": [{"name": "Network"}],
        }
    )
    assert config.get(config.default_profile).name == "Network"


def test_invalid_config_rejects_typo_and_duplicate_boards():
    source = SourceConfig(name="Example", source="lever", identifier="example")
    with pytest.raises(ValidationError, match="duplicate"):
        SourcesConfig(sources=[source, source])
    with pytest.raises(ValidationError):
        SourceConfig(name="Example", source="lever", identifier="../foo")
    with pytest.raises(ValidationError):
        SourceConfig(name="Example", source="greenhouse", identifier="example", region="eu")
    with pytest.raises(ValidationError):
        SourcesConfig.model_validate({"sources": [], "typo": True})


def test_duplicate_profiles_and_missing_profile():
    with pytest.raises(ValidationError, match="unique"):
        ProfilesConfig.model_validate({"profiles": [{"name": "SOC"}, {"name": "soc"}]})
    with pytest.raises(ValueError, match="Unknown profile"):
        ProfilesConfig().get("missing")


def test_yaml_empty_is_configuration_error(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML mapping"):
        read_yaml(path)


def test_settings_environment(monkeypatch):
    monkeypatch.setenv("JOBRADAR_DATABASE_URL", "sqlite:///custom.db")
    assert Settings(_env_file=None).database_url == "sqlite:///custom.db"


def test_naive_job_dates_are_rejected():
    with pytest.raises(ValidationError, match="timezone"):
        Job(
            source="lever",
            source_job_id="1",
            source_board="lever:global:example",
            company="Example",
            title="Engineer",
            url="https://example.com/jobs/1",
            published_at="2026-10-01T00:00:00",
        )
