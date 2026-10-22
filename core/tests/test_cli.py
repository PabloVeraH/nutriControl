from __future__ import annotations

import signal
from argparse import Namespace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import FakePump, FakeSwitch, base_config_dict, make_config

from hidro import cli
from hidro.calibration import CalibrationStore
from hidro.config import AppConfig
from hidro.drivers.base import DriverError


class FakeApp:
    def __init__(self, outputs: dict[str, object]) -> None:
        self.outputs = outputs
        self.closed = False

    def close(self) -> None:
        self.closed = True


def install_app(monkeypatch: pytest.MonkeyPatch, app: FakeApp) -> None:
    monkeypatch.setattr(
        cli,
        "_settings",
        lambda verbose=True: SimpleNamespace(
            config_path=Path("config/hidro.yaml"),
            mode_override="mock",
        ),
    )
    monkeypatch.setattr(cli, "load_config", lambda *_args: object())
    monkeypatch.setattr("hidro.bootstrap.build_app", lambda *_args: app)


def switch_args(*, output_id: str = "relay", state: str = "on", seconds: float = 1.0) -> Namespace:
    return Namespace(id=output_id, state=state, seconds=seconds)


def test_switch_off_turns_output_off(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    relay = FakeSwitch("relay")
    relay.on()
    app = FakeApp({"relay": relay})
    install_app(monkeypatch, app)

    assert cli.cmd_switch(switch_args(state="off")) == 0

    assert not relay.is_on
    assert app.closed
    assert "apagado" in capsys.readouterr().out


def test_switch_on_sleeps_and_turns_off(monkeypatch: pytest.MonkeyPatch) -> None:
    relay = FakeSwitch("relay")
    app = FakeApp({"relay": relay})
    install_app(monkeypatch, app)
    slept: list[float] = []
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: slept.append(seconds))

    assert cli.cmd_switch(switch_args(seconds=0.25)) == 0

    assert relay.events == ["on", "off"]
    assert slept == [0.25]
    assert app.closed


def test_switch_turns_off_after_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    relay = FakeSwitch("relay")
    app = FakeApp({"relay": relay})
    install_app(monkeypatch, app)

    def interrupted(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.time, "sleep", interrupted)

    assert cli.cmd_switch(switch_args()) == 0

    assert not relay.is_on
    assert app.closed


def test_switch_rejects_pumps(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pump = FakePump("pump")
    app = FakeApp({"pump": pump})
    install_app(monkeypatch, app)

    assert cli.cmd_switch(switch_args(output_id="pump")) == 2

    assert not pump.is_on
    assert app.closed
    assert "peristáltica" in capsys.readouterr().err


def test_switch_reports_unknown_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    app = FakeApp({})
    install_app(monkeypatch, app)

    assert cli.cmd_switch(switch_args(output_id="no_existe")) == 2

    assert app.closed
    assert "no existe la salida" in capsys.readouterr().err



def test_switch_turns_off_on_sigterm(monkeypatch: pytest.MonkeyPatch) -> None:
    relay = FakeSwitch("relay")
    app = FakeApp({"relay": relay})
    install_app(monkeypatch, app)
    previous = signal.getsignal(signal.SIGTERM)

    def terminated(_seconds: float) -> None:
        # docker stop envía SIGTERM; el manejador debe convertirlo en interrupción.
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)

    monkeypatch.setattr(cli.time, "sleep", terminated)

    assert cli.cmd_switch(switch_args(seconds=60)) == 0

    assert relay.events == ["on", "off"]
    assert signal.getsignal(signal.SIGTERM) is previous


@pytest.mark.parametrize("seconds", [0.0, -1.0])
def test_switch_rejects_non_positive_seconds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], seconds: float
) -> None:
    relay = FakeSwitch("relay")
    app = FakeApp({"relay": relay})
    install_app(monkeypatch, app)

    assert cli.cmd_switch(switch_args(seconds=seconds)) == 2

    assert relay.events == []
    assert "--seconds" in capsys.readouterr().err


class FakeFlowDevice:
    def __init__(self) -> None:
        self.when_activated = None
        self.closed = False

    def pulse(self, count: int = 1) -> None:
        for _ in range(count):
            assert self.when_activated is not None
            self.when_activated()

    def close(self) -> None:
        self.closed = True


class FakeRuntime:
    def __init__(self, device: FakeFlowDevice) -> None:
        self.device = device
        self.closed = False
        self.gpio: tuple[int, bool, float | None] | None = None

    def gpio_input(
        self, gpio: int, *, pull_up: bool, bounce_time: float | None = 0.02
    ) -> FakeFlowDevice:
        self.gpio = (gpio, pull_up, bounce_time)
        return self.device

    def close(self) -> None:
        self.closed = True


class FakeStore:
    def __init__(self) -> None:
        self.saved: float | None = None

    def flow(self) -> None:
        return None

    def save_flow(self, *, pulses_per_liter: float) -> None:
        self.saved = pulses_per_liter


def test_calibrate_flow_saves_pulses_per_liter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = make_config(mode="hardware")
    settings = SimpleNamespace(
        config_path=tmp_path / "hidro.yaml",
        data_dir=tmp_path,
        mode_override=None,
    )
    store = FakeStore()
    device = FakeFlowDevice()
    runtime = FakeRuntime(device)
    prompts: list[str] = []

    monkeypatch.setattr(cli, "_settings", lambda verbose=True: settings)
    monkeypatch.setattr(cli, "load_config", lambda *_args: config)
    monkeypatch.setattr(cli, "CalibrationStore", lambda _path: store)
    monkeypatch.setattr("hidro.drivers.runtime.RuntimeContext", lambda *_args: runtime)

    def fake_input(prompt: str = "") -> str:
        prompts.append(prompt)
        if len(prompts) == 2:
            device.pulse(5880)
        return ""

    monkeypatch.setattr("builtins.input", fake_input)

    assert cli.cmd_calibrate_flow(Namespace(id=None, liters=1.0)) == 0

    assert store.saved == pytest.approx(5880.0)
    assert runtime.gpio == (27, False, None)
    assert runtime.closed
    assert len(prompts) == 2


def test_calibrate_flow_rejects_no_pulses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = make_config(mode="hardware")
    settings = SimpleNamespace(
        config_path=tmp_path / "hidro.yaml",
        data_dir=tmp_path,
        mode_override=None,
    )
    store = FakeStore()
    runtime = FakeRuntime(FakeFlowDevice())

    monkeypatch.setattr(cli, "_settings", lambda verbose=True: settings)
    monkeypatch.setattr(cli, "load_config", lambda *_args: config)
    monkeypatch.setattr(cli, "CalibrationStore", lambda _path: store)
    monkeypatch.setattr("hidro.drivers.runtime.RuntimeContext", lambda *_args: runtime)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "")

    assert cli.cmd_calibrate_flow(Namespace(id=None, liters=1.0)) == 2

    assert store.saved is None
    assert runtime.closed
    assert "no se registraron pulsos" in capsys.readouterr().err


def test_calibrate_flow_requires_hardware_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = SimpleNamespace(
        config_path=tmp_path / "hidro.yaml",
        data_dir=tmp_path,
        mode_override=None,
    )
    monkeypatch.setattr(cli, "_settings", lambda verbose=True: settings)
    monkeypatch.setattr(cli, "load_config", lambda *_args: make_config())

    assert cli.cmd_calibrate_flow(Namespace(id=None, liters=1.0)) == 2

    assert "modo hardware" in capsys.readouterr().err


def test_parser_accepts_calibrate_flow() -> None:
    args = cli.build_parser().parse_args(["calibrate", "flow", "--liters", "0.5"])

    assert args.func is cli.cmd_calibrate_flow
    assert args.sensor == "flow"
    assert args.liters == 0.5


def test_flow_measures_rate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = make_config(mode="hardware")
    settings = SimpleNamespace(
        config_path=tmp_path / "hidro.yaml",
        data_dir=tmp_path,
        mode_override=None,
    )
    device = FakeFlowDevice()
    runtime = FakeRuntime(device)
    now = [1000.0]
    monkeypatch.setattr(cli, "_settings", lambda verbose=True: settings)
    monkeypatch.setattr(cli, "load_config", lambda *_args: config)
    monkeypatch.setattr("hidro.drivers.runtime.RuntimeContext", lambda *_args: runtime)
    monkeypatch.setattr(cli.time, "monotonic", lambda: now[0])
    monkeypatch.setattr("builtins.input", lambda _prompt="": "")

    def fake_sleep(seconds: float) -> None:
        now[0] += seconds
        device.pulse(98)

    monkeypatch.setattr(cli.time, "sleep", fake_sleep)

    assert cli.cmd_flow(Namespace(id=None, seconds=1.0)) == 0

    out = capsys.readouterr().out
    assert "caudal: 1.000 L/min" in out
    assert runtime.closed


def test_parser_accepts_flow() -> None:
    args = cli.build_parser().parse_args(["flow", "--seconds", "2"])

    assert args.func is cli.cmd_flow
    assert args.seconds == 2.0


@pytest.mark.parametrize(("answer", "expected_code"), [("", 1), ("s", 0)])
def test_calibrate_flow_confirms_outlier(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    answer: str,
    expected_code: int,
) -> None:
    settings = SimpleNamespace(config_path=tmp_path / "hidro.yaml", data_dir=tmp_path, mode_override=None)
    store = FakeStore()
    device = FakeFlowDevice()
    prompts: list[str] = []

    monkeypatch.setattr(cli, "_settings", lambda verbose=True: settings)
    monkeypatch.setattr(cli, "load_config", lambda *_args: make_config(mode="hardware"))
    monkeypatch.setattr(cli, "CalibrationStore", lambda _path: store)
    monkeypatch.setattr("hidro.drivers.runtime.RuntimeContext", lambda *_args: FakeRuntime(device))

    def fake_input(prompt: str = "") -> str:
        prompts.append(prompt)
        if len(prompts) == 2:
            device.pulse(100)  # 100 pulsos/L frente a 5880 de referencia
        return answer if len(prompts) == 3 else ""

    monkeypatch.setattr("builtins.input", fake_input)

    assert cli.cmd_calibrate_flow(Namespace(id=None, liters=1.0)) == expected_code

    assert "se aleja mucho" in capsys.readouterr().err
    assert store.saved == (100.0 if expected_code == 0 else None)


class FakeGpioOutput:
    def __init__(self) -> None:
        self.value = False
        self.events: list[str] = []
        self.closed = False

    def on(self) -> None:
        self.value = True
        self.events.append("on")

    def off(self) -> None:
        self.value = False
        self.events.append("off")

    def close(self) -> None:
        self.closed = True


class FakeCalibrationRuntime:
    def __init__(self) -> None:
        self.bus = object()
        self.device = FakeGpioOutput()
        self.gpio_calls: list[tuple[int, bool, bool]] = []
        self.closed = False

    def smbus(self, _bus: int) -> object:
        return self.bus

    def gpio_output(
        self, gpio: int, *, active_high: bool = True, initial: bool = False
    ) -> FakeGpioOutput:
        self.gpio_calls.append((gpio, active_high, initial))
        return self.device

    def close(self) -> None:
        self.closed = True


def install_pump_calibration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    config: AppConfig,
    runtime: FakeCalibrationRuntime,
) -> None:
    settings = SimpleNamespace(
        config_path=tmp_path / "hidro.yaml", data_dir=tmp_path, mode_override=None
    )
    monkeypatch.setattr(cli, "_settings", lambda verbose=True: settings)
    monkeypatch.setattr(cli, "load_config", lambda *_args: config)
    monkeypatch.setattr("hidro.drivers.runtime.RuntimeContext", lambda *_args: runtime)


def test_calibrate_pump_saves_ml_per_min(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime = FakeCalibrationRuntime()
    install_pump_calibration(
        monkeypatch, tmp_path, config=make_config(mode="hardware"), runtime=runtime
    )
    slept: list[float] = []
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: slept.append(seconds))
    answers = iter(["s", "12", "s"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    code = cli.cmd_calibrate_pump(
        Namespace(id=None, seconds=60.0, ml=None, yes=False)
    )

    assert code == 0
    assert slept == [60.0]
    assert runtime.gpio_calls == [(22, True, False)]
    assert runtime.device.events == ["on", "off"]
    assert runtime.closed
    store = CalibrationStore(tmp_path / "calibration.yaml")
    assert store.pump_ml_per_min("pump_ph_down") == pytest.approx(12.0)
    assert "calibración guardada" in capsys.readouterr().out


def test_calibrate_pump_accepts_measured_volume(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = FakeCalibrationRuntime()
    install_pump_calibration(
        monkeypatch, tmp_path, config=make_config(mode="hardware"), runtime=runtime
    )
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    code = cli.cmd_calibrate_pump(
        Namespace(id="pump_ph_down", seconds=30.0, ml=6.0, yes=True)
    )

    assert code == 0
    assert runtime.device.events == ["on", "off"]
    store = CalibrationStore(tmp_path / "calibration.yaml")
    assert store.pump_ml_per_min("pump_ph_down") == pytest.approx(12.0)


def test_calibrate_pump_can_be_cancelled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime = FakeCalibrationRuntime()
    install_pump_calibration(
        monkeypatch, tmp_path, config=make_config(mode="hardware"), runtime=runtime
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")

    code = cli.cmd_calibrate_pump(
        Namespace(id=None, seconds=60.0, ml=None, yes=False)
    )

    assert code == 1
    assert runtime.device.events == []
    assert not runtime.closed
    assert "cancelada" in capsys.readouterr().out


def test_calibrate_pump_turns_off_after_interrupt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = FakeCalibrationRuntime()
    install_pump_calibration(
        monkeypatch, tmp_path, config=make_config(mode="hardware"), runtime=runtime
    )

    def interrupted(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.time, "sleep", interrupted)

    code = cli.cmd_calibrate_pump(
        Namespace(id=None, seconds=60.0, ml=12.0, yes=True)
    )

    assert code == 130
    assert runtime.device.events == ["on", "off"]
    assert runtime.closed


@pytest.mark.parametrize(("seconds", "ml"), [(0.0, 10.0), (121.0, 10.0), (60.0, 0.0)])
def test_calibrate_pump_rejects_invalid_measurements(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    seconds: float,
    ml: float,
) -> None:
    runtime = FakeCalibrationRuntime()
    install_pump_calibration(
        monkeypatch, tmp_path, config=make_config(mode="hardware"), runtime=runtime
    )

    code = cli.cmd_calibrate_pump(
        Namespace(id=None, seconds=seconds, ml=ml, yes=True)
    )

    assert code == 2
    assert runtime.device.events == []
    assert "debe ser" in capsys.readouterr().err


def test_calibrate_pump_confirms_outlier(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = base_config_dict()
    raw["outputs"][1]["ml_per_min"] = 10.0
    config = make_config(mode="hardware", outputs=raw["outputs"], dosing=raw["dosing"])
    runtime = FakeCalibrationRuntime()
    install_pump_calibration(monkeypatch, tmp_path, config=config, runtime=runtime)
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)
    answers = iter(["s", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    code = cli.cmd_calibrate_pump(
        Namespace(id=None, seconds=60.0, ml=1.0, yes=False)
    )

    assert code == 1
    assert runtime.device.events == ["on", "off"]
    assert "se aleja mucho" in capsys.readouterr().err
    store = CalibrationStore(tmp_path / "calibration.yaml")
    assert store.pump_ml_per_min("pump_ph_down") is None


def test_calibrate_pump_requires_id_with_multiple_pumps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = base_config_dict()
    raw["outputs"].append(
        {"id": "pump_nutrient_a", "type": "peristaltic", "gpio": 5}
    )
    config = make_config(mode="hardware", outputs=raw["outputs"])
    runtime = FakeCalibrationRuntime()
    install_pump_calibration(monkeypatch, tmp_path, config=config, runtime=runtime)

    code = cli.cmd_calibrate_pump(
        Namespace(id=None, seconds=60.0, ml=12.0, yes=True)
    )

    assert code == 2
    assert runtime.device.events == []
    assert "--id" in capsys.readouterr().err


def test_calibrate_pump_requires_hardware(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime = FakeCalibrationRuntime()
    install_pump_calibration(
        monkeypatch, tmp_path, config=make_config(), runtime=runtime
    )

    code = cli.cmd_calibrate_pump(
        Namespace(id=None, seconds=60.0, ml=12.0, yes=True)
    )

    assert code == 2
    assert runtime.device.events == []
    assert "modo hardware" in capsys.readouterr().err


def test_parser_accepts_calibrate_pump() -> None:
    args = cli.build_parser().parse_args(
        ["calibrate", "pump", "--seconds", "30", "--ml", "10"]
    )

    assert args.func is cli.cmd_calibrate_pump
    assert args.sensor == "pump"
    assert args.seconds == 30.0
    assert args.ml == 10.0


def test_parser_accepts_safety_reset() -> None:
    args = cli.build_parser().parse_args(["safety", "reset", "pump_ph_down"])

    assert args.func is cli.cmd_safety_reset
    assert args.safety_action == "reset"
    assert args.pump == "pump_ph_down"


def test_parser_keeps_legacy_safety_reset() -> None:
    args = cli.build_parser().parse_args(["safety-reset"])

    assert args.func is cli.cmd_safety_reset
    assert args.pump is None


def test_pump_rejects_invalid_volume(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.cmd_pump(Namespace(id="pump", ml=0.0, yes=True)) == 2
    assert "--ml" in capsys.readouterr().err


def install_calibration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    config: object,
    runtime: FakeCalibrationRuntime,
    voltage: float,
) -> None:
    settings = SimpleNamespace(config_path=tmp_path / "hidro.yaml", data_dir=tmp_path, mode_override=None)
    monkeypatch.setattr(cli, "_settings", lambda verbose=True: settings)
    monkeypatch.setattr(cli, "load_config", lambda *_args: config)
    monkeypatch.setattr("hidro.drivers.runtime.RuntimeContext", lambda *_args: runtime)
    monkeypatch.setattr(
        cli, "read_stable_voltage", lambda _reader, **_kwargs: voltage
    )


def confirming_input(prompts: list[str]) -> str:
    if "Guardar" in prompts[-1] or "Reemplazar" in prompts[-1]:
        return "s"
    return ""


def confirm_prompts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    prompts: list[str] = []

    def fake_input(prompt: str = "") -> str:
        prompts.append(prompt)
        return confirming_input(prompts)

    monkeypatch.setattr("builtins.input", fake_input)
    return prompts


def test_calibrate_ph_first_point_saves_pending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime = FakeCalibrationRuntime()
    install_calibration(
        monkeypatch,
        tmp_path,
        config=make_config(mode="hardware"),
        runtime=runtime,
        voltage=2.0,
    )
    confirm_prompts(monkeypatch)

    code = cli.cmd_calibrate_ph(
        Namespace(id=None, point=6.86, temperature=25.0)
    )

    store = CalibrationStore(tmp_path / "calibration.yaml")
    pending = store.ph_pending()
    assert code == 0
    assert pending is not None
    assert pending.ph == pytest.approx(6.86)
    assert pending.voltage == pytest.approx(2.0)
    assert pending.t_c == pytest.approx(25.0)
    assert runtime.closed
    assert "guardado como pendiente" in capsys.readouterr().out


def test_calibrate_ph_second_point_saves_calibration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = CalibrationStore(tmp_path / "calibration.yaml")
    store.set_ph_pending(6.86, 2.0, 25.0)
    runtime = FakeCalibrationRuntime()
    install_calibration(
        monkeypatch,
        tmp_path,
        config=make_config(mode="hardware"),
        runtime=runtime,
        voltage=2.4,
    )
    confirm_prompts(monkeypatch)

    code = cli.cmd_calibrate_ph(
        Namespace(id="ph", point=4.0, temperature=25.0)
    )

    calibration = store.ph()
    assert code == 0
    assert calibration is not None
    assert calibration.slope == pytest.approx((2.0 - 2.4) / (6.86 - 4.0))
    assert calibration.t_cal == pytest.approx(25.0)
    assert store.ph_pending() is None
    assert runtime.closed


def test_calibrate_ph_rejects_bad_slope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = CalibrationStore(tmp_path / "calibration.yaml")
    store.set_ph_pending(6.86, 2.0, 25.0)
    runtime = FakeCalibrationRuntime()
    install_calibration(
        monkeypatch,
        tmp_path,
        config=make_config(mode="hardware"),
        runtime=runtime,
        voltage=2.01,
    )
    confirm_prompts(monkeypatch)

    code = cli.cmd_calibrate_ph(
        Namespace(id=None, point=4.0, temperature=25.0)
    )

    assert code == 2
    assert store.ph() is None
    assert store.ph_pending() is not None
    assert runtime.closed
    assert "pendiente" in capsys.readouterr().err


def test_calibrate_ph_can_replace_pending_point(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = CalibrationStore(tmp_path / "calibration.yaml")
    store.set_ph_pending(6.86, 2.0, 25.0)
    runtime = FakeCalibrationRuntime()
    install_calibration(
        monkeypatch,
        tmp_path,
        config=make_config(mode="hardware"),
        runtime=runtime,
        voltage=2.05,
    )
    confirm_prompts(monkeypatch)

    code = cli.cmd_calibrate_ph(
        Namespace(id=None, point=6.86, temperature=25.0)
    )

    pending = store.ph_pending()
    assert code == 0
    assert pending is not None
    assert pending.voltage == pytest.approx(2.05)


def test_calibrate_ph_requires_hardware(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = SimpleNamespace(config_path=tmp_path / "hidro.yaml", data_dir=tmp_path, mode_override=None)
    monkeypatch.setattr(cli, "_settings", lambda verbose=True: settings)
    monkeypatch.setattr(cli, "load_config", lambda *_args: make_config())

    assert cli.cmd_calibrate_ph(Namespace(id=None, point=6.86, temperature=25.0)) == 2
    assert "modo hardware" in capsys.readouterr().err


@pytest.mark.parametrize("point", [-1.0, 15.0, float("nan")])
def test_calibrate_ph_rejects_bad_point(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    point: float,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = SimpleNamespace(config_path=tmp_path / "hidro.yaml", data_dir=tmp_path, mode_override=None)
    monkeypatch.setattr(cli, "_settings", lambda verbose=True: settings)
    monkeypatch.setattr(cli, "load_config", lambda *_args: make_config(mode="hardware"))

    assert cli.cmd_calibrate_ph(Namespace(id=None, point=point, temperature=25.0)) == 2
    assert "--point" in capsys.readouterr().err


def test_parser_accepts_calibrate_ph() -> None:
    args = cli.build_parser().parse_args(["calibrate", "ph", "--point", "6.86"])

    assert args.func is cli.cmd_calibrate_ph
    assert args.sensor == "ph"
    assert args.point == 6.86


def test_calibrate_ec_saves_factor_k(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime = FakeCalibrationRuntime()
    install_calibration(
        monkeypatch,
        tmp_path,
        config=make_config(mode="hardware"),
        runtime=runtime,
        voltage=1.0,
    )
    confirm_prompts(monkeypatch)
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    code = cli.cmd_calibrate_ec(
        Namespace(id="ec", standard=1413.0, temperature=25.0)
    )

    calibration = CalibrationStore(tmp_path / "calibration.yaml").ec()
    assert code == 0
    assert calibration is not None
    assert calibration.k == pytest.approx(1413.0 / 734.95, rel=1e-4)
    assert runtime.gpio_calls == [(23, True, False)]
    assert runtime.device.events[0] == "on"
    assert runtime.device.events[-1] == "off"
    assert runtime.closed
    assert "Calibración de EC guardada" in capsys.readouterr().out


def test_calibrate_ec_rejects_out_of_range_k(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime = FakeCalibrationRuntime()
    install_calibration(
        monkeypatch,
        tmp_path,
        config=make_config(mode="hardware"),
        runtime=runtime,
        voltage=0.05,
    )
    confirm_prompts(monkeypatch)
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    code = cli.cmd_calibrate_ec(
        Namespace(id=None, standard=1413.0, temperature=25.0)
    )

    assert code == 2
    assert CalibrationStore(tmp_path / "calibration.yaml").ec() is None
    assert runtime.device.events[-1] == "off"
    assert runtime.closed
    assert "fuera del rango" in capsys.readouterr().err


def test_calibrate_ec_turns_off_relay_on_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = FakeCalibrationRuntime()
    install_calibration(
        monkeypatch,
        tmp_path,
        config=make_config(mode="hardware"),
        runtime=runtime,
        voltage=1.0,
    )
    confirm_prompts(monkeypatch)
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    def fail(_reader: object, **_kwargs: object) -> float:
        raise DriverError("i2c roto")

    monkeypatch.setattr(cli, "read_stable_voltage", fail)

    with pytest.raises(DriverError, match="i2c roto"):
        cli.cmd_calibrate_ec(
            Namespace(id=None, standard=1413.0, temperature=25.0)
        )

    assert runtime.device.events[0] == "on"
    assert runtime.device.events[-1] == "off"
    assert runtime.closed


@pytest.mark.parametrize("standard", [0.0, -1.0, float("nan")])
def test_calibrate_ec_rejects_bad_standard(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    standard: float,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = SimpleNamespace(config_path=tmp_path / "hidro.yaml", data_dir=tmp_path, mode_override=None)
    monkeypatch.setattr(cli, "_settings", lambda verbose=True: settings)
    monkeypatch.setattr(cli, "load_config", lambda *_args: make_config(mode="hardware"))

    assert cli.cmd_calibrate_ec(
        Namespace(id=None, standard=standard, temperature=25.0)
    ) == 2
    assert "--standard" in capsys.readouterr().err


def test_parser_accepts_calibrate_ec() -> None:
    args = cli.build_parser().parse_args(
        ["calibrate", "ec", "--standard", "1413"]
    )

    assert args.func is cli.cmd_calibrate_ec
    assert args.sensor == "ec"
    assert args.standard == 1413.0


def capture_stability(monkeypatch: pytest.MonkeyPatch, voltages: list[float]) -> list[dict]:
    """Reemplaza la lectura estable: devuelve `voltages` en orden y guarda los parámetros."""
    calls: list[dict] = []
    pending = iter(voltages)

    def fake(_reader: object, **kwargs: object) -> float:
        calls.append(kwargs)
        return next(pending)

    monkeypatch.setattr(cli, "read_stable_voltage", fake)
    return calls


def test_calibrate_ph_holds_tds_power_off(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runtime = FakeCalibrationRuntime()
    install_calibration(
        monkeypatch, tmp_path, config=make_config(mode="hardware"), runtime=runtime, voltage=2.0
    )
    confirm_prompts(monkeypatch)

    assert cli.cmd_calibrate_ph(Namespace(id=None, point=6.86, temperature=25.0)) == 0

    # tds_power (GPIO23) se toma apagado durante la lectura y queda apagado.
    assert runtime.gpio_calls == [(23, True, False)]
    assert runtime.device.value is False
    assert "on" not in runtime.device.events


def test_calibrate_ph_uses_strict_stability_and_knob(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_calibration(
        monkeypatch,
        tmp_path,
        config=make_config(mode="hardware"),
        runtime=FakeCalibrationRuntime(),
        voltage=2.0,
    )
    calls = capture_stability(monkeypatch, [2.0, 2.0])
    confirm_prompts(monkeypatch)

    cli.cmd_calibrate_ph(Namespace(id=None, point=6.86, temperature=25.0))
    cli.cmd_calibrate_ph(Namespace(id=None, point=6.86, temperature=25.0, tolerance_mv=5.0))

    assert calls[0]["tolerance_v"] == pytest.approx(0.002)
    assert calls[0]["window"] * calls[0]["interval_s"] == pytest.approx(10.0)
    assert calls[1]["tolerance_v"] == pytest.approx(0.005)


def test_calibrate_ec_uses_its_own_tolerance(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    install_calibration(
        monkeypatch,
        tmp_path,
        config=make_config(mode="hardware"),
        runtime=FakeCalibrationRuntime(),
        voltage=1.0,
    )
    calls = capture_stability(monkeypatch, [1.0])
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
    confirm_prompts(monkeypatch)

    cli.cmd_calibrate_ec(Namespace(id=None, standard=1413.0, temperature=25.0))

    assert calls[0]["tolerance_v"] == pytest.approx(0.005)


def test_calibrate_ph_old_pending_point_can_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = CalibrationStore(tmp_path / "calibration.yaml")
    old = store.set_ph_pending(6.86, 2.0, 25.0)
    later = old.measured_at + timedelta(hours=3)
    monkeypatch.setattr(cli, "datetime", SimpleNamespace(now=lambda tz=None: later))
    install_calibration(
        monkeypatch,
        tmp_path,
        config=make_config(mode="hardware"),
        runtime=FakeCalibrationRuntime(),
        voltage=2.4,
    )
    confirm_prompts(monkeypatch)  # "¿Combinarlo igual...?" -> no

    assert cli.cmd_calibrate_ph(Namespace(id=None, point=4.0, temperature=25.0)) == 0

    pending = store.ph_pending()
    assert store.ph() is None
    assert pending is not None
    assert pending.ph == pytest.approx(4.0)
    assert "3.0 h" in capsys.readouterr().out


def test_calibrate_ph_positive_slope_requires_confirmation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = CalibrationStore(tmp_path / "calibration.yaml")
    store.set_ph_pending(6.86, 2.4, 25.0)  # voltajes intercambiados: pendiente positiva
    install_calibration(
        monkeypatch,
        tmp_path,
        config=make_config(mode="hardware"),
        runtime=FakeCalibrationRuntime(),
        voltage=2.0,
    )

    def decline_override(prompt: str = "") -> str:
        return "" if "de todos modos" in prompt else "s"

    monkeypatch.setattr("builtins.input", decline_override)

    assert cli.cmd_calibrate_ph(Namespace(id=None, point=4.0, temperature=25.0)) == 1

    assert store.ph() is None
    assert store.ph_pending() is not None
    assert "pendiente es positiva" in capsys.readouterr().err


def test_calibrate_ph_defaults_to_probe_temperature(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_calibration(
        monkeypatch,
        tmp_path,
        config=make_config(mode="hardware"),
        runtime=FakeCalibrationRuntime(),
        voltage=2.0,
    )
    monkeypatch.setattr(cli, "_probe_temperature", lambda _config, _sensor_id: 18.3)
    confirm_prompts(monkeypatch)  # Enter acepta la temperatura propuesta

    assert cli.cmd_calibrate_ph(Namespace(id=None, point=6.86, temperature=None)) == 0

    pending = CalibrationStore(tmp_path / "calibration.yaml").ph_pending()
    assert pending is not None
    assert pending.t_c == pytest.approx(18.3)


def test_probe_temperature_reads_the_ds18b20(tmp_path: Path) -> None:
    device = tmp_path / "28-abc"
    device.mkdir()
    (device / "w1_slave").write_text(
        "72 01 4b 46 7f ff 0e 10 57 : crc=57 YES\n72 01 4b 46 7f ff 0e 10 57 t=18312\n",
        encoding="utf-8",
    )
    config = AppConfig.model_validate(
        {
            "mode": "hardware",
            "sensors": [
                {
                    "id": "water_temp_1",
                    "type": "ds18b20",
                    "serial": "28-abc",
                    "base_path": str(tmp_path),
                }
            ],
        }
    )
    assert cli._probe_temperature(config, "water_temp_1") == pytest.approx(18.312)
    assert cli._probe_temperature(config, None) is None
    (device / "w1_slave").unlink()
    assert cli._probe_temperature(config, "water_temp_1") is None


@pytest.mark.parametrize(
    ("on_voltage", "verdict"), [(2.020, "Hay interferencia"), (2.001, "No se aprecia")]
)
def test_interference_compares_ph_with_tds_off_and_on(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    on_voltage: float,
    verdict: str,
) -> None:
    runtime = FakeCalibrationRuntime()
    install_calibration(
        monkeypatch, tmp_path, config=make_config(mode="hardware"), runtime=runtime, voltage=0.0
    )
    capture_stability(monkeypatch, [2.0, on_voltage])
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "")

    assert cli.cmd_interference(Namespace(tolerance_mv=None)) == 0

    events = runtime.device.events
    assert events.count("on") == 1
    assert "off" in events[events.index("on") :]
    assert runtime.device.value is False
    assert verdict in capsys.readouterr().out


def test_interference_turns_tds_off_when_reading_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = FakeCalibrationRuntime()
    install_calibration(
        monkeypatch, tmp_path, config=make_config(mode="hardware"), runtime=runtime, voltage=0.0
    )
    readings = iter([2.0])

    def fail_second(_reader: object, **_kwargs: object) -> float:
        value = next(readings, None)
        if value is None:
            raise DriverError("el voltaje no se estabilizó")
        return value

    monkeypatch.setattr(cli, "read_stable_voltage", fail_second)
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "")

    with pytest.raises(DriverError):
        cli.cmd_interference(Namespace(tolerance_mv=None))

    assert runtime.device.value is False
    assert runtime.closed


def test_parser_accepts_interference() -> None:
    args = cli.build_parser().parse_args(["interference", "--tolerance-mv", "3"])
    assert args.func is cli.cmd_interference
    assert args.tolerance_mv == 3.0
