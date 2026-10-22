from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import FakeClock

from hidro.drivers.flow_hall import (
    FlowCounter,
    FlowHallSensor,
    calculate_pulses_per_liter,
)


class FakeDevice:
    def __init__(self) -> None:
        self.when_pressed = None
        self.closed = False

    def pulse(self, count: int = 1) -> None:
        for _ in range(count):
            assert self.when_pressed is not None
            self.when_pressed()

    def close(self) -> None:
        self.closed = True


def test_counter_rate_and_total() -> None:
    counter = FlowCounter(600)
    counter.sample(0.0)
    counter.add_pulses(600)
    rate, total = counter.sample(60.0)
    assert rate == pytest.approx(1.0)
    assert total == pytest.approx(1.0)
    counter.add_pulses(300)
    rate, total = counter.sample(90.0)
    assert rate == pytest.approx(1.0)
    assert total == pytest.approx(1.5)


def test_counter_first_sample_has_no_rate() -> None:
    counter = FlowCounter(600)
    counter.add_pulses(10)
    rate, total = counter.sample(1.0)
    assert rate == 0.0
    assert total == pytest.approx(10 / 600)


def test_counter_rejects_zero_ppl() -> None:
    with pytest.raises(ValueError):
        FlowCounter(0)


def test_calculate_pulses_per_liter() -> None:
    assert calculate_pulses_per_liter(5880, 1.0) == pytest.approx(5880.0)
    assert calculate_pulses_per_liter(2940, 0.5) == pytest.approx(5880.0)


def test_calculate_pulses_per_liter_rejects_bad_values() -> None:
    with pytest.raises(ValueError):
        calculate_pulses_per_liter(0, 1.0)
    with pytest.raises(ValueError):
        calculate_pulses_per_liter(100, 0)


def test_sensor_counts_all_pulses(tmp_path: Path) -> None:
    clock = FakeClock()
    device = FakeDevice()
    total_path = tmp_path / "flow.json"
    sensor = FlowHallSensor(
        "flow",
        device,
        FlowCounter(600),
        total_path,
        save_every_s=0,
        clock=clock,
    )
    sensor.read()
    device.pulse(600)
    clock.advance(60)
    reading = sensor.read()[0]
    assert reading.value == pytest.approx(1.0)
    assert reading.quality == "ok"
    saved = json.loads(total_path.read_text(encoding="utf-8"))
    assert saved["total_l"] == pytest.approx(1.0)


def test_sensor_persists_and_reloads_total(tmp_path: Path) -> None:
    total_path = tmp_path / "flow.json"
    total_path.write_text(json.dumps({"total_l": 12.5}), encoding="utf-8")
    counter = FlowCounter(600, total_l=12.5)
    sensor = FlowHallSensor("flow", FakeDevice(), counter, total_path, save_every_s=0)
    sensor.close()
    saved = json.loads(total_path.read_text(encoding="utf-8"))
    assert saved["total_l"] == pytest.approx(12.5)


def test_sensor_close_closes_device(tmp_path: Path) -> None:
    device = FakeDevice()
    sensor = FlowHallSensor(
        "flow", device, FlowCounter(600), tmp_path / "flow.json", save_every_s=3600
    )
    sensor.close()
    assert device.closed
