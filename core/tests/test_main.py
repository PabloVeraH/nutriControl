from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

from conftest import NOW, FakeClock, FakePump, RecordingNotifier, base_config_dict, make_config

from hidro.bootstrap import build_app
from hidro.control.dosing import DosingFlags
from hidro.drivers.base import LatestReadings
from hidro.heartbeat import read_heartbeat
from hidro.main import Controller
from hidro.model import Measurement
from hidro.settings import InfluxSettings, Settings, TelegramSettings


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        config_path=tmp_path / "config" / "hidro.yaml",
        data_dir=tmp_path / "data",
        mode_override="mock",
        timezone=ZoneInfo("UTC"),
        log_level="INFO",
        log_format="json",
        influx=InfluxSettings(url="", token="", org="hidro", bucket="hidro"),
        telegram=TelegramSettings("", "", enabled=False),
    )


def test_controller_runs_one_cycle_and_writes_heartbeat(tmp_path: Path) -> None:
    app = build_app(settings_for(tmp_path), make_config())
    controller = Controller(app, clock=FakeClock())
    controller._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    def stop_on_sleep(_seconds: float) -> None:
        observed["ph"] = app.latest.get("ph")
        controller.stop()

    observed: dict[str, object] = {}
    controller._sleep = stop_on_sleep
    controller.run_forever()

    assert read_heartbeat(app.settings.data_dir) is not None
    assert observed["ph"] is not None


def test_read_sensors_skips_failures(tmp_path: Path) -> None:
    class Broken:
        id = "broken"

        def read(self) -> list[Measurement]:
            raise RuntimeError("boom")

        def close(self) -> None:
            pass

    app = build_app(settings_for(tmp_path), make_config())
    app.sensors.append(Broken())  # type: ignore[arg-type]
    readings = Controller(app)._read_sensors()
    assert readings
    assert all(item.name != "broken" for item in readings)
    app.close()


def test_dose_executes_dry_run_and_remembers_time(tmp_path: Path) -> None:
    raw = base_config_dict()
    raw["outputs"][1]["ml_per_min"] = 10.0
    config = make_config(
        sensors=raw["sensors"],
        outputs=raw["outputs"],
        dosing=raw["dosing"],
    )
    app = build_app(settings_for(tmp_path), config)
    app.latest.update(
        [
            Measurement("ph", 4.8, "pH", NOW, "ok", "ph"),
            Measurement("ec", 1150.0, "µS/cm", NOW, "ok", "ec"),
        ]
    )
    app.notifier = RecordingNotifier()
    controller = Controller(app)

    controller._dose(NOW, DosingFlags())

    assert app.dosing.last_dose_at == NOW
    assert app.notifier.messages
    assert "SIMULACIÓN" in app.notifier.messages[0]
    app.close()


def test_dose_reports_pump_errors(tmp_path: Path) -> None:
    class ExplodingPump(FakePump):
        def dispense_ml(self, ml: float) -> float:
            raise RuntimeError("bomba rota")

    raw = base_config_dict()
    dosing = dict(raw["dosing"])
    dosing["dry_run"] = False
    config = make_config(
        sensors=raw["sensors"], outputs=raw["outputs"], dosing=dosing
    )
    app = build_app(settings_for(tmp_path), config)
    app.latest.update(
        [
            Measurement("ph", 4.8, "pH", NOW, "ok", "ph"),
            Measurement("ec", 1150.0, "µS/cm", NOW, "ok", "ec"),
        ]
    )
    app.notifier = RecordingNotifier()
    app.outputs["pump_ph_down"] = ExplodingPump("pump_ph_down")

    Controller(app)._dose(NOW, DosingFlags())

    assert any("ERROR AL DOSIFICAR" in message for message in app.notifier.messages)
    app.close()


def test_read_sensors_updates_latest(tmp_path: Path) -> None:
    app = build_app(settings_for(tmp_path), make_config())
    readings = Controller(app)._read_sensors()
    assert readings
    assert app.latest.snapshot()
    app.close()


def test_latest_starts_empty() -> None:
    assert LatestReadings().snapshot() == {}
