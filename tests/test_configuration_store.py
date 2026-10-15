from pathlib import Path

import pytest
import yaml

from jobradar.config import ProfilesConfig, Settings, SourceConfig, SourcesConfig
from jobradar.services.configuration import ConfigurationStore


def make_store(tmp_path):
    sources = tmp_path / "sources.example.yaml"
    profiles = tmp_path / "profiles.example.yaml"
    sources.write_text("sources: []\n", encoding="utf-8")
    profiles.write_text("profiles: []\n", encoding="utf-8")
    settings = Settings(_env_file=None, sources_file=sources, profiles_file=profiles)
    return ConfigurationStore(settings)


def test_web_config_saves_personal_without_overwriting_examples(tmp_path):
    store = make_store(tmp_path)
    value = SourcesConfig(
        sources=[SourceConfig(name="Тест", source="lever", identifier="test", region="eu")]
    )
    store.save_sources(value)
    assert store.settings.sources_file == tmp_path / "sources.yaml"
    assert (tmp_path / "sources.example.yaml").read_text(encoding="utf-8") == "sources: []\n"
    assert store.load_sources() == value
    reloaded = make_store(tmp_path)
    assert reloaded.load_sources() == value


def test_profile_edits_are_persistent(tmp_path):
    store = make_store(tmp_path)
    profile = ProfilesConfig.model_validate(
        {"profiles": [{"name": "Мой SOC", "keywords": ["SOC"]}]}
    )
    store.save_profiles(profile)
    assert store.load_profiles() == profile
    assert (
        yaml.safe_load((tmp_path / "profiles.yaml").read_text(encoding="utf-8"))["profiles"][0][
            "name"
        ]
        == "Мой SOC"
    )


def test_failed_atomic_save_keeps_old_settings_and_file(tmp_path, monkeypatch):
    store = make_store(tmp_path)

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr("jobradar.services.configuration.write_export", fail)
    with pytest.raises(OSError):
        store.save_sources(SourcesConfig())
    assert store.settings.sources_file.name == "sources.example.yaml"
    assert not (tmp_path / "sources.yaml").exists()


def test_default_settings_pick_personal_configs_on_restart(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "config"
    config.mkdir()
    assert Settings(_env_file=None).sources_file == Path("config/sources.example.yaml")
    (config / "sources.yaml").write_text("sources: []\n", encoding="utf-8")
    (config / "profiles.yaml").write_text("profiles: []\n", encoding="utf-8")
    settings = Settings(_env_file=None)
    assert settings.sources_file == Path("config/sources.yaml")
    assert settings.profiles_file == Path("config/profiles.yaml")
