from __future__ import annotations

import copy
from datetime import timedelta

from conftest import NOW, base_config_dict, make_config, measurement

from hidro.control.monitor import Monitor
from hidro.drivers.base import LatestReadings


def full_config():
    raw = copy.deepcopy(base_config_dict())
    raw["sensors"].insert(1, {"id": "air", "type": "htu21d"})
    raw["sensors"].insert(2, {"id": "tank", "type": "float_switch", "gpio": 5})
    return make_config(
        sensors=raw["sensors"],
        outputs=raw["outputs"],
    )


def populated(
    *,
    water_temp: float = 20.0,
    air_temp: float = 22.0,
    humidity: float = 55.0,
    tank_low: bool = False,
    flow: float = 1.4,
    ph: float = 5.9,
    ec: float = 1150.0,
    at=NOW,
) -> LatestReadings:
    latest = LatestReadings()
    latest.update(
        [
            measurement("water_temp_1", water_temp, "°C", at=at),
            measurement("air_temp", air_temp, "°C", at=at),
            measurement("air_humidity", humidity, "%", at=at),
            measurement("tank_low", tank_low, "bool", at=at),
            measurement("flow", flow, "L/min", at=at),
            measurement("ph", ph, "pH", at=at),
            measurement("ec", ec, "µS/cm", at=at),
            measurement("ec_ppm", ec * 0.5, "ppm", at=at),
        ]
    )
    return latest


def by_key(result) -> dict:
    return {signal.key: signal for signal in result.signals}


def test_normal_state_has_no_alert() -> None:
    result = Monitor(full_config(), populated()).evaluate(NOW)
    assert result.conditions.flow_alarm is False
    assert result.conditions.tank_low is False
    assert result.conditions.ph_danger is False
    assert [signal.key for signal in result.signals if signal.active] == []
    assert len(result.signals) == 14


def test_missing_readings_mark_every_sensor_stale() -> None:
    result = Monitor(full_config(), LatestReadings()).evaluate(NOW)
    signals = by_key(result)
    for sensor_id in ("water_temp_1", "air", "tank", "flow", "ph", "ec"):
        assert signals[f"sensor-{sensor_id}"].active is True
    assert signals["flow"].active is False
    assert signals["tank-low"].active is False
    assert signals["ph-danger"].active is False
    assert signals["ec-low"].active is False


def test_water_temperature_high() -> None:
    result = Monitor(full_config(), populated(water_temp=26.0)).evaluate(NOW)
    signal = by_key(result)["water-temp-water_temp_1"]
    assert signal.active is True
    assert "26.0" in signal.message


def test_air_temperature_outside_range() -> None:
    monitor = Monitor(full_config(), populated(air_temp=40.0))
    assert by_key(monitor.evaluate(NOW))["air-temp-air"].active is True
    monitor.latest = populated(air_temp=0.0)
    assert by_key(monitor.evaluate(NOW))["air-temp-air"].active is True
    monitor.latest = populated(air_temp=22.0)
    assert by_key(monitor.evaluate(NOW))["air-temp-air"].active is False


def test_flow_alarm_must_be_sustained() -> None:
    monitor = Monitor(full_config(), populated(flow=0.1))
    first = monitor.evaluate(NOW)
    assert first.conditions.flow_alarm is False
    assert by_key(first)["flow"].active is False

    monitor.latest = populated(flow=0.1, at=NOW + timedelta(seconds=60))
    later = monitor.evaluate(NOW + timedelta(seconds=61))
    assert later.conditions.flow_alarm is True
    assert by_key(later)["flow"].active is True
    assert "FLUJO DE AGUA" in by_key(later)["flow"].message

    monitor.latest = populated(flow=1.4, at=NOW + timedelta(seconds=62))
    recovered = monitor.evaluate(NOW + timedelta(seconds=62))
    assert recovered.conditions.flow_alarm is False
    assert by_key(recovered)["flow"].active is False


def test_tank_low_is_immediate() -> None:
    result = Monitor(full_config(), populated(tank_low=True)).evaluate(NOW)
    assert result.conditions.tank_low is True
    assert by_key(result)["tank-low"].active is True


def test_ph_danger_is_immediate() -> None:
    result = Monitor(full_config(), populated(ph=4.9)).evaluate(NOW)
    assert result.conditions.ph_danger is True
    assert by_key(result)["ph-danger"].active is True
    assert by_key(result)["ph-range"].active is False
    assert "PELIGRO" in by_key(result)["ph-danger"].message


def test_ph_range_alarm_is_sustained_and_resets() -> None:
    monitor = Monitor(full_config(), populated(ph=5.4))
    first = monitor.evaluate(NOW)
    assert by_key(first)["ph-range"].active is False
    assert first.conditions.ph_danger is False

    monitor.latest = populated(ph=5.4, at=NOW + timedelta(seconds=1799))
    later = monitor.evaluate(NOW + timedelta(seconds=1800))
    assert by_key(later)["ph-range"].active is True
    assert "fuera del rango" in by_key(later)["ph-range"].message

    monitor.latest = populated(ph=5.9, at=NOW + timedelta(seconds=1801))
    recovered = monitor.evaluate(NOW + timedelta(seconds=1801))
    assert by_key(recovered)["ph-range"].active is False


def test_ec_low_is_sustained_and_high_is_immediate() -> None:
    monitor = Monitor(full_config(), populated(ec=800))
    first = monitor.evaluate(NOW)
    assert by_key(first)["ec-low"].active is False
    assert by_key(first)["ec-high"].active is False

    monitor.latest = populated(ec=800, at=NOW + timedelta(seconds=1799))
    later = monitor.evaluate(NOW + timedelta(seconds=1800))
    assert by_key(later)["ec-low"].active is True

    monitor.latest = populated(ec=1400, at=NOW + timedelta(seconds=1801))
    high = monitor.evaluate(NOW + timedelta(seconds=1801))
    assert by_key(high)["ec-low"].active is False
    assert by_key(high)["ec-high"].active is True


def test_missing_optional_sensors_are_not_alarms() -> None:
    raw = copy.deepcopy(base_config_dict())
    raw["sensors"] = [
        sensor
        for sensor in raw["sensors"]
        if sensor["type"] in {"ds18b20", "ph_analog", "tds_analog"}
    ]
    config = make_config(sensors=raw["sensors"], outputs=raw["outputs"])
    result = Monitor(config, populated()).evaluate(NOW)
    signals = by_key(result)
    assert signals["flow"].active is False
    assert signals["tank-low"].active is False
    assert result.conditions == result.conditions.__class__()


def test_stale_sensor_flag_clears_when_reading_arrives() -> None:
    monitor = Monitor(full_config(), LatestReadings())
    stale = by_key(monitor.evaluate(NOW))["sensor-ph"]
    assert stale.active is True

    monitor.latest = populated()
    fresh = by_key(monitor.evaluate(NOW))["sensor-ph"]
    assert fresh.active is False
