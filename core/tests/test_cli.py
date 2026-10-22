from __future__ import annotations

import signal
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import FakePump, FakeSwitch, make_config

from hidro import cli


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
