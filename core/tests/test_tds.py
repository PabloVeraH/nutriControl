from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hidro.calibration import CalibrationStore
from hidro.drivers.base import LatestReadings
from hidro.drivers.tds_analog import (
    TDSAnalogSensor,
    calibration_k,
    ec_to_reference,
    gravity_polynomial,
)
from hidro.model import Measurement


class FakeADC:
    def __init__(self, voltage: float, fail: bool = False) -> None:
        self.voltage = voltage
        self.fail = fail
        self.calls: list[tuple[int, float, int]] = []

    def read_median_voltage(self, channel: int, gain_v: float, samples: int) -> float:
        self.calls.append((channel, gain_v, samples))
        if self.fail:
            from hidro.drivers.base import DriverError

            raise DriverError("i2c roto")
        return self.voltage


class FakeSwitch:
    def __init__(self) -> None:
        self.id = "tds_power"
        self.events: list[str] = []

    def on(self) -> None:
        self.events.append("on")

    def off(self) -> None:
        self.events.append("off")

    @property
    def is_on(self) -> bool:
        return bool(self.events and self.events[-1] == "on")

    def close(self) -> None:
        pass


def _store(tmp_path: Path, k: float | None = 1.0) -> CalibrationStore:
    store = CalibrationStore(tmp_path / "calibration.yaml")
    if k is not None:
        store.save_ec(k=k, standard_us_cm=1413, voltage=1.0, t_c=25.0)
    return store


def _latest(temp: float | None = 25.0, age_s: float = 0.0) -> LatestReadings:
    latest = LatestReadings()
    if temp is not None:
        latest.update(
            [
                Measurement(
                    "water_temp_1",
                    temp,
                    "°C",
                    datetime.now(UTC) - timedelta(seconds=age_s),
                    "ok",
                    "water_temp_1",
                )
            ]
        )
    return latest


def test_polynomial_reference_values() -> None:
    assert gravity_polynomial(0.0) == 0.0
    assert gravity_polynomial(1.0) == pytest.approx(734.95, abs=0.01)
    assert gravity_polynomial(2.0) == pytest.approx(1758.70, abs=0.01)


def test_temperature_compensation() -> None:
    assert ec_to_reference(1000, 25.0) == pytest.approx(1000.0)
    assert ec_to_reference(1000, 20.0) == pytest.approx(1000 / 0.9)
    assert ec_to_reference(1000, 30.0) == pytest.approx(1000 / 1.1)


def test_calibration_k_roundtrip() -> None:
    voltage = 1.0
    k = calibration_k(voltage, 25.0, 1413.0)
    assert gravity_polynomial(voltage) * k == pytest.approx(1413.0)
    with pytest.raises(ValueError):
        calibration_k(0.0, 25.0, 1413.0)


def test_tds_reads_and_powers_switch(tmp_path: Path) -> None:
    switch = FakeSwitch()
    sensor = TDSAnalogSensor(
        "ec",
        FakeADC(1.0),
        _store(tmp_path),
        _latest(25.0),
        channel=1,
        temperature_from="water_temp_1",
        power_switch=switch,
        sleep=lambda _s: None,
    )
    readings = sensor.read()
    assert switch.events == ["on", "off"]
    assert readings[0].name == "ec"
    assert readings[0].value == pytest.approx(734.95, abs=0.1)
    assert readings[0].quality == "ok"
    assert readings[1].name == "ec_ppm"
    assert readings[1].value == pytest.approx(367.5, abs=0.1)


def test_tds_temperature_compensation_applied(tmp_path: Path) -> None:
    sensor = TDSAnalogSensor(
        "ec",
        FakeADC(1.0),
        _store(tmp_path),
        _latest(30.0),
        temperature_from="water_temp_1",
        power_switch=None,
        sleep=lambda _s: None,
    )
    reading = sensor.read()[0]
    assert reading.value == pytest.approx(734.95 / 1.1, abs=0.1)


def test_tds_without_temperature_is_suspect(tmp_path: Path) -> None:
    sensor = TDSAnalogSensor("ec", FakeADC(1.0), _store(tmp_path), LatestReadings())
    assert sensor.read()[0].quality == "suspect"


def test_tds_uncalibrated_is_suspect(tmp_path: Path) -> None:
    sensor = TDSAnalogSensor(
        "ec", FakeADC(1.0), _store(tmp_path, k=None), _latest(25.0)
    )
    reading = sensor.read()[0]
    assert reading.quality == "suspect"
    assert reading.value == pytest.approx(734.95, abs=0.1)


def test_tds_adc_error_is_invalid(tmp_path: Path) -> None:
    switch = FakeSwitch()
    sensor = TDSAnalogSensor(
        "ec",
        FakeADC(1.0, fail=True),
        _store(tmp_path),
        _latest(),
        power_switch=switch,
        sleep=lambda _s: None,
    )
    readings = sensor.read()
    assert switch.events == ["on", "off"]
    assert all(reading.quality == "invalid" for reading in readings)


def test_tds_out_of_range_is_invalid(tmp_path: Path) -> None:
    sensor = TDSAnalogSensor("ec", FakeADC(3.9), _store(tmp_path), _latest())
    assert sensor.read()[0].quality == "invalid"


def test_tds_closes_power_switch(tmp_path: Path) -> None:
    switch = FakeSwitch()
    sensor = TDSAnalogSensor(
        "ec", FakeADC(1.0), _store(tmp_path), _latest(), power_switch=switch
    )
    sensor.close()
    assert switch.events[-1] == "off"
