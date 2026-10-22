from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

from conftest import make_config

from hidro.bootstrap import build_app
from hidro.settings import InfluxSettings, Settings, TelegramSettings


def settings_for(tmp_path: Path, *, telegram: bool = False) -> Settings:
    return Settings(
        config_path=tmp_path / "config" / "hidro.yaml",
        data_dir=tmp_path / "data",
        mode_override="mock",
        timezone=ZoneInfo("UTC"),
        log_level="INFO",
        log_format="json",
        influx=InfluxSettings(url="", token="", org="hidro", bucket="hidro"),
        telegram=TelegramSettings(
            token="token" if telegram else "",
            chat_id="chat" if telegram else "",
            enabled=True,
        ),
    )


def test_build_app_wires_mock_components(tmp_path: Path) -> None:
    config = make_config()
    settings = settings_for(tmp_path)
    app = build_app(settings, config)

    assert settings.data_dir.is_dir()
    assert app.config is config
    assert app.outputs.keys() == {"tds_power", "pump_ph_down"}
    assert [sensor.id for sensor in app.sensors] == ["water_temp_1", "flow", "ph", "ec"]
    assert app.storage.enabled is False
    assert app.notifier.configured is False
    assert app.dosing.config is config
    assert app.monitor.config is config
    assert app.safety.path == settings.data_dir / "safety.json"
    assert app.calibration.path == settings.data_dir / "calibration.yaml"

    app.close()


def test_build_app_enables_telegram_when_configured(tmp_path: Path) -> None:
    app = build_app(settings_for(tmp_path, telegram=True), make_config())
    assert app.notifier.configured is True
    assert app.notifier.enabled is True
    app.close()


def test_app_close_continues_after_errors(tmp_path: Path) -> None:
    app = build_app(settings_for(tmp_path), make_config())

    class Broken:
        id = "broken"

        def close(self) -> None:
            raise RuntimeError("boom")

        def off(self) -> None:
            raise RuntimeError("boom")

    app.outputs["tds_power"] = Broken()  # type: ignore[assignment]
    app.sensors.append(Broken())  # type: ignore[arg-type]
    app.close()

    assert app.latest.snapshot() == {}
