from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from conftest import base_config_dict

from hidro.healthcheck import EXIT_OK, EXIT_UNHEALTHY, main
from hidro.heartbeat import write_heartbeat
from hidro.settings import InfluxSettings, Settings, TelegramSettings


def settings_for(tmp_path: Path, config_path: Path | None = None) -> Settings:
    return Settings(
        config_path=config_path or tmp_path / "config" / "hidro.yaml",
        data_dir=tmp_path / "data",
        mode_override="mock",
        timezone=ZoneInfo("UTC"),
        log_level="INFO",
        log_format="json",
        influx=InfluxSettings(url="", token="", org="hidro", bucket="hidro"),
        telegram=TelegramSettings("", "", enabled=False),
    )


def prepare(tmp_path: Path, monkeypatch) -> Settings:
    config_path = tmp_path / "config" / "hidro.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        yaml.safe_dump(base_config_dict(), sort_keys=False), encoding="utf-8"
    )
    settings = settings_for(tmp_path, config_path)
    monkeypatch.setattr(
        "hidro.healthcheck.Settings.from_env", classmethod(lambda _cls: settings)
    )
    return settings


def test_missing_heartbeat_is_unhealthy(tmp_path: Path, monkeypatch, capsys) -> None:
    prepare(tmp_path, monkeypatch)
    assert main() == EXIT_UNHEALTHY
    assert "sin heartbeat" in capsys.readouterr().err


def test_recent_heartbeat_is_healthy(tmp_path: Path, monkeypatch) -> None:
    settings = prepare(tmp_path, monkeypatch)
    write_heartbeat(settings.data_dir, datetime.now(UTC), 3)
    assert main() == EXIT_OK


def test_stale_heartbeat_is_unhealthy(tmp_path: Path, monkeypatch, capsys) -> None:
    settings = prepare(tmp_path, monkeypatch)
    write_heartbeat(settings.data_dir, datetime.now(UTC) - timedelta(seconds=120), 3)
    assert main() == EXIT_UNHEALTHY
    assert "atrasado" in capsys.readouterr().err


def test_invalid_config_is_unhealthy(tmp_path: Path, monkeypatch, capsys) -> None:
    settings = settings_for(tmp_path, tmp_path / "no-existe.yaml")
    monkeypatch.setattr(
        "hidro.healthcheck.Settings.from_env", classmethod(lambda _cls: settings)
    )
    assert main() == EXIT_UNHEALTHY
    assert "configuración inválida" in capsys.readouterr().err
