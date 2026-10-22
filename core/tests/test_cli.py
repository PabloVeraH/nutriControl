from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import FakePump, FakeSwitch

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
