from __future__ import annotations

from conftest import FakeClock

from hidro.drivers.float_switch import FloatSwitchSensor


def test_initial_state_is_published_immediately() -> None:
    clock = FakeClock()
    sensor = FloatSwitchSensor("tank_level", lambda: 0, low_when=0, debounce_s=10, clock=clock)
    reading = sensor.read()[0]
    assert reading.name == "tank_level_low"
    assert reading.value is True


def test_debounce_requires_stable_state() -> None:
    clock = FakeClock()
    raw = {"value": 0}
    sensor = FloatSwitchSensor(
        "tank_level", lambda: raw["value"], low_when=0, debounce_s=10, clock=clock
    )
    assert sensor.read()[0].value is True

    raw["value"] = 1
    clock.advance(10)
    assert sensor.read()[0].value is True
    clock.advance(10)
    assert sensor.read()[0].value is False


def test_bounce_back_resets_timer() -> None:
    clock = FakeClock()
    raw = {"value": 0}
    sensor = FloatSwitchSensor(
        "tank_level", lambda: raw["value"], low_when=0, debounce_s=10, clock=clock
    )
    raw["value"] = 1
    clock.advance(8)
    assert sensor.read()[0].value is True
    raw["value"] = 0
    clock.advance(1)
    assert sensor.read()[0].value is True
