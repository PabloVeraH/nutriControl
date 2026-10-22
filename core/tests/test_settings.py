from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import hidro.settings as settings_module
from hidro.settings import Settings, _default_config_path, _default_data_dir


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch) -> None:
    monkeypatch.setattr(settings_module, "find_dotenv", lambda **_kwargs: "")


@pytest.fixture
def clean_env(monkeypatch) -> None:
    for name in (
        "HIDRO_CONFIG",
        "HIDRO_DATA_DIR",
        "HIDRO_HARDWARE",
        "HIDRO_LOG_LEVEL",
        "HIDRO_LOG_FORMAT",
        "INFLUX_URL",
        "INFLUX_TOKEN",
        "INFLUX_ORG",
        "INFLUX_BUCKET",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "TELEGRAM_ENABLED",
        "TZ",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults_and_config_discovery(tmp_path: Path, monkeypatch, clean_env) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "hidro.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    settings = Settings.from_env()
    assert settings.config_path == config_path
    assert settings.data_dir == tmp_path / "data"
    assert settings.timezone == ZoneInfo("America/Santiago")
    assert settings.mode_override is None
    assert settings.log_level == "INFO"
    assert settings.log_format == "json"
    assert settings.influx.url == "http://localhost:8086"
    assert settings.influx.token == ""
    assert settings.influx.org == "hidro"
    assert settings.influx.bucket == "hidro"
    assert settings.influx.enabled is False
    assert settings.telegram.enabled is True
    assert settings.telegram.configured is False


def test_environment_overrides(tmp_path: Path, monkeypatch, clean_env) -> None:
    config_path = tmp_path / "custom.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    data_dir = tmp_path / "datos"
    monkeypatch.setenv("HIDRO_CONFIG", str(config_path))
    monkeypatch.setenv("HIDRO_DATA_DIR", str(data_dir))
    monkeypatch.setenv("HIDRO_HARDWARE", " MOCK ")
    monkeypatch.setenv("TZ", "No/Existe")
    monkeypatch.setenv("HIDRO_LOG_LEVEL", "debug")
    monkeypatch.setenv("HIDRO_LOG_FORMAT", "text")
    monkeypatch.setenv("INFLUX_URL", "http://influx:8086")
    monkeypatch.setenv("INFLUX_TOKEN", "token")
    monkeypatch.setenv("INFLUX_ORG", "org")
    monkeypatch.setenv("INFLUX_BUCKET", "bucket")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    monkeypatch.setenv("TELEGRAM_ENABLED", "no")

    settings = Settings.from_env()
    assert settings.config_path == config_path
    assert settings.data_dir == data_dir
    assert settings.mode_override == "mock"
    assert settings.timezone == ZoneInfo("UTC")
    assert settings.log_level == "debug"
    assert settings.log_format == "text"
    assert settings.influx.enabled is True
    assert settings.influx.url == "http://influx:8086"
    assert settings.influx.token == "token"
    assert settings.influx.org == "org"
    assert settings.influx.bucket == "bucket"
    assert settings.telegram.enabled is False
    assert settings.telegram.configured is True


def test_unknown_mode_is_ignored(tmp_path: Path, monkeypatch, clean_env) -> None:
    config_path = tmp_path / "hidro.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("HIDRO_CONFIG", str(config_path))
    monkeypatch.setenv("HIDRO_HARDWARE", "otro")
    assert Settings.from_env().mode_override is None


def test_default_paths_helpers(tmp_path: Path, monkeypatch, clean_env) -> None:
    monkeypatch.chdir(tmp_path)
    assert _default_config_path() == Path("config/hidro.yaml")

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "hidro.yaml").write_text("{}\n", encoding="utf-8")
    assert _default_config_path() == config_dir / "hidro.yaml"
    assert _default_data_dir(config_dir / "hidro.yaml") == tmp_path / "data"
    assert _default_data_dir(tmp_path / "otro.yaml") == Path("data")

    monkeypatch.setenv("HIDRO_CONFIG", "config/otro.yaml")
    assert _default_config_path() == Path("config/otro.yaml")
