from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from conftest import (
    NOW,
    FakeClock,
    FakePump,
    FakeSwitch,
    RecordingNotifier,
    base_config_dict,
    make_config,
)

from hidro import main
from hidro.bootstrap import build_app
from hidro.control.dosing import DosingFlags
from hidro.drivers.base import DriverError, LatestReadings
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


def test_controller_polls_commands_and_sends_daily_summary(tmp_path: Path) -> None:
    notifier = RecordingNotifier()
    app = build_app(
        settings_for(tmp_path),
        make_config(alerts={"daily_summary_at": "09:00"}),
    )
    app.notifier = notifier
    app.alerts.notifier = notifier
    wall_time = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    controller = Controller(app, clock=FakeClock(), wall_clock=lambda: wall_time)
    controller._install_signal_handlers = lambda: None  # type: ignore[method-assign]
    controller._sleep = lambda _seconds: controller.stop()

    controller.run_forever()

    assert notifier.requested_offsets == [None]
    assert any("RESUMEN DIARIO" in message for message in notifier.messages)


def test_daily_summary_failure_does_not_stop_the_cycle(tmp_path: Path) -> None:
    app = build_app(
        settings_for(tmp_path),
        make_config(alerts={"daily_summary_at": "09:00"}),
    )
    wall_time = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    controller = Controller(app, clock=FakeClock(), wall_clock=lambda: wall_time)
    controller._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    def explode(_now: datetime) -> str:
        raise RuntimeError("influx raro")

    controller._daily_summary_text = explode  # type: ignore[method-assign]
    controller._sleep = lambda _seconds: controller.stop()

    controller.run_forever()

    assert read_heartbeat(app.settings.data_dir) is not None


def test_shutdown_turns_off_all_outputs(tmp_path: Path) -> None:
    app = build_app(settings_for(tmp_path), make_config())
    for output in app.outputs.values():
        output.on()
    assert all(output.is_on for output in app.outputs.values())

    Controller(app)._shutdown()

    assert all(not output.is_on for output in app.outputs.values())


def test_shutdown_continues_when_an_output_fails(tmp_path: Path) -> None:
    class FailingSwitch(FakeSwitch):
        def off(self) -> None:
            raise RuntimeError("falla al apagar")

    app = build_app(settings_for(tmp_path), make_config())
    app.outputs["roto"] = FailingSwitch("roto")

    Controller(app)._shutdown()

    assert all(not output.is_on for output in app.outputs.values() if output.id != "roto")


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
            Measurement("ph", 7.2, "pH", NOW, "ok", "ph"),
            Measurement("ec", 1150.0, "µS/cm", NOW, "ok", "ec"),
            Measurement("flow", 1.4, "L/min", NOW, "ok", "flow"),
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
            Measurement("ph", 7.2, "pH", NOW, "ok", "ph"),
            Measurement("ec", 1150.0, "µS/cm", NOW, "ok", "ec"),
            Measurement("flow", 1.4, "L/min", NOW, "ok", "flow"),
        ]
    )
    app.notifier = RecordingNotifier()
    app.outputs["pump_ph_down"] = ExplodingPump("pump_ph_down")

    Controller(app)._dose(NOW, DosingFlags())

    assert any("ERROR AL DOSIFICAR" in message for message in app.notifier.messages)
    assert app.safety.is_blocked("pump_ph_down")
    assert "safety reset" in app.notifier.messages[0]
    app.close()


def test_read_sensors_updates_latest(tmp_path: Path) -> None:
    app = build_app(settings_for(tmp_path), make_config())
    readings = Controller(app)._read_sensors()
    assert readings
    assert app.latest.snapshot()
    app.close()


def test_latest_starts_empty() -> None:
    assert LatestReadings().snapshot() == {}


def test_stop_reaches_the_pumps(tmp_path: Path) -> None:
    app = build_app(settings_for(tmp_path), make_config())
    controller = Controller(app)

    controller.stop()

    assert app.runtime.stop_event.is_set()
    app.close()


def test_run_reports_hardware_errors_at_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken_build(*_args: object) -> None:
        raise DriverError("no existe /dev/gpiochip0")

    monkeypatch.setattr(main.Settings, "from_env", classmethod(lambda cls: settings_for(tmp_path)))
    monkeypatch.setattr(main, "setup_logging", lambda *_args: None)
    monkeypatch.setattr(main, "load_config", lambda *_args: make_config())
    monkeypatch.setattr("hidro.bootstrap.build_app", broken_build)

    with caplog.at_level("ERROR"):
        assert main.run() == 3
    assert "gpiochip0" in caplog.text
