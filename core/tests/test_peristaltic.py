from __future__ import annotations

import threading

import pytest
from conftest import FakeClock, FakeSleeper, FakeSwitch

from hidro.drivers.base import DoseTooLong, PumpNotCalibrated
from hidro.drivers.peristaltic import PeristalticPump


def make_pump(
    *,
    ml_per_min: float | None = 10.0,
    max_on_s: float = 30.0,
    stop_event: threading.Event | None = None,
) -> tuple[PeristalticPump, FakeSwitch, FakeClock]:
    clock = FakeClock()
    switch = FakeSwitch()
    pump = PeristalticPump(
        "pump",
        switch,
        ml_per_min=ml_per_min,
        max_on_s=max_on_s,
        clock=clock,
        sleep=FakeSleeper(clock),
        stop_event=stop_event,
    )
    return pump, switch, clock


def test_dispense_uses_calibration() -> None:
    pump, switch, clock = make_pump()
    seconds = pump.dispense_ml(5.0)
    assert seconds == pytest.approx(30.0)
    assert switch.events == ["on", "off"]
    assert clock.now == pytest.approx(1030.0)


def test_dispense_always_turns_off_on_error() -> None:
    pump, _switch, _clock = make_pump()

    class ExplodingSwitch(FakeSwitch):
        def on(self) -> None:
            super().on()
            raise RuntimeError("boom")

    pump._switch = ExplodingSwitch()
    with pytest.raises(RuntimeError):
        pump.dispense_ml(1.0)
    assert pump._switch.events[-1] == "off"


def test_rejects_uncalibrated() -> None:
    pump, switch, _clock = make_pump(ml_per_min=None)
    with pytest.raises(PumpNotCalibrated):
        pump.dispense_ml(1.0)
    assert switch.events == []


def test_rejects_too_long() -> None:
    pump, switch, _clock = make_pump(max_on_s=10)
    with pytest.raises(DoseTooLong):
        pump.dispense_ml(5.0)
    assert switch.events == []


def test_rejects_non_positive_volume() -> None:
    pump, _switch, _clock = make_pump()
    with pytest.raises(ValueError):
        pump.dispense_ml(0)


def test_stop_event_interrupts_dispense() -> None:
    stop = threading.Event()
    clock = FakeClock()
    switch = FakeSwitch()

    def sleeping(_seconds: float) -> None:
        stop.set()
        clock.advance(0.1)

    pump = PeristalticPump(
        "pump",
        switch,
        ml_per_min=1.0,
        max_on_s=600,
        clock=clock,
        sleep=sleeping,
        stop_event=stop,
    )
    pump.dispense_ml(1.0)
    assert switch.events == ["on", "off"]
    assert clock.now < 1010.0
