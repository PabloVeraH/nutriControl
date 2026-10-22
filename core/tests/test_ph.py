from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hidro.calibration import CalibrationStore
from hidro.drivers.base import LatestReadings
from hidro.drivers.ph_analog import (
    PHAnalogSensor,
    calibrate_two_points,
    ph_from_voltage,
    voltage_at_ph7,
    voltage_slope,
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


def test_two_point_helpers() -> None:
    slope = voltage_slope(2.0, 6.86, 2.4, 4.0)
    v7 = voltage_at_ph7(2.0, 6.86, slope)
    assert ph_from_voltage(v7, v7, slope) == pytest.approx(7.0)
    assert ph_from_voltage(2.0, v7, slope) == pytest.approx(6.86)
    result = calibrate_two_points((2.0, 6.86), (2.4, 4.0), 22.0)
    assert result["slope"] == pytest.approx(slope)
    assert result["v7"] == pytest.approx(v7)
    assert result["temperature_c"] == 22.0
    with pytest.raises(ValueError):
        voltage_slope(1.0, 7.0, 1.0, 7.0)
    with pytest.raises(ValueError):
        ph_from_voltage(1.0, 1.0, 0.0)


def _store_with_ph(tmp_path: Path) -> CalibrationStore:
    store = CalibrationStore(tmp_path / "calibration.yaml")
    store.save_ph(v1=2.0, ph1=6.86, v2=2.4, ph2=4.0, t_cal=25.0)
    return store


def _latest_with_temp(temp: float, at: datetime) -> LatestReadings:
    latest = LatestReadings()
    latest.update([Measurement("water_temp_1", temp, "°C", at, "ok", "water_temp_1")])
    return latest


def test_ph_sensor_reads_temperature_compensated(tmp_path: Path) -> None:
    store = _store_with_ph(tmp_path)
    latest = _latest_with_temp(25.0, datetime.now(UTC))
    adc = FakeADC(2.2)
    sensor = PHAnalogSensor(
        "ph", adc, store, latest, channel=0, temperature_from="water_temp_1"
    )
    reading = sensor.read()[0]
    assert reading.name == "ph"
    assert reading.quality == "ok"
    assert reading.value == pytest.approx(7.0 + (2.2 - 1.98042) / -0.13986, abs=0.01)
    assert adc.calls == [(0, 4.096, 10)]


def test_ph_sensor_without_temperature_is_suspect(tmp_path: Path) -> None:
    store = _store_with_ph(tmp_path)
    sensor = PHAnalogSensor(
        "ph", FakeADC(2.0), store, LatestReadings(), temperature_from="water_temp_1"
    )
    reading = sensor.read()[0]
    assert reading.quality == "suspect"
    assert reading.value == pytest.approx(6.86)


def test_ph_sensor_stale_temperature_is_suspect(tmp_path: Path) -> None:
    store = _store_with_ph(tmp_path)
    old = datetime.now(UTC) - timedelta(minutes=10)
    sensor = PHAnalogSensor(
        "ph",
        FakeADC(2.0),
        store,
        _latest_with_temp(25.0, old),
        temperature_from="water_temp_1",
    )
    assert sensor.read()[0].quality == "suspect"


def test_ph_sensor_uncalibrated_is_invalid(tmp_path: Path) -> None:
    sensor = PHAnalogSensor(
        "ph",
        FakeADC(2.0),
        CalibrationStore(tmp_path / "calibration.yaml"),
        LatestReadings(),
    )
    reading = sensor.read()[0]
    assert reading.quality == "invalid"


def test_ph_sensor_adc_error_is_invalid(tmp_path: Path) -> None:
    store = _store_with_ph(tmp_path)
    sensor = PHAnalogSensor("ph", FakeADC(2.0, fail=True), store, LatestReadings())
    assert sensor.read()[0].quality == "invalid"


def test_ph_sensor_out_of_range_is_invalid(tmp_path: Path) -> None:
    store = _store_with_ph(tmp_path)
    sensor = PHAnalogSensor("ph", FakeADC(4.0), store, LatestReadings())
    assert sensor.read()[0].quality == "invalid"


def test_old_calibration_is_suspect(tmp_path: Path) -> None:
    store = CalibrationStore(tmp_path / "calibration.yaml")
    store.save_ph(
        v1=2.0,
        ph1=6.86,
        v2=2.4,
        ph2=4.0,
        t_cal=25.0,
        calibrated_at=datetime.now(UTC) - timedelta(days=45),
    )
    latest = _latest_with_temp(25.0, datetime.now(UTC))
    sensor = PHAnalogSensor("ph", FakeADC(2.0), store, latest)
    assert sensor.read()[0].quality == "suspect"
