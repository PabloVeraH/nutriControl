from __future__ import annotations

import pytest

from hidro.calc import dew_point_c, vpd_kpa
from hidro.drivers import htu21d
from hidro.drivers.htu21d import (
    HTU21DSensor,
    compensate_humidity,
    crc8,
    humidity_from_raw,
    temperature_from_raw,
)


class FakeBus:
    def __init__(self) -> None:
        self.messages: list[object] = []

    def i2c_rdwr(self, message: object) -> None:
        self.messages.append(message)


# Tramas reales: bit 1 del LSB en 0 para temperatura (23,35 °C) y en 1 para humedad.
TEMP_RAW = 0x6664
HUMIDITY_RAW = 0x5F5E
AIR_NAMES = {"air_temp", "air_humidity", "air_vpd", "air_dew_point"}


def frame(raw: int, bad_crc: bool = False) -> list[int]:
    msb = (raw >> 8) & 0xFF
    lsb = raw & 0xFF
    checksum = crc8([msb, lsb])
    if bad_crc:
        checksum ^= 0xFF
    return [msb, lsb, checksum]


def test_crc8_known_value() -> None:
    assert crc8([0x01]) == 0x31
    assert crc8([0x00]) == 0x00
    assert crc8(frame(0x6666)[:2]) == frame(0x6666)[2]


def test_conversions() -> None:
    assert temperature_from_raw(0) == pytest.approx(-46.85)
    assert humidity_from_raw(0) == 0.0
    assert humidity_from_raw(0xFFFF) == 100.0
    assert humidity_from_raw(0x5F5C) == pytest.approx(-6 + 125 * 0x5F5C / 65536)


def test_read_publishes_derived_values(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = FakeBus()
    frames = iter([frame(TEMP_RAW), frame(HUMIDITY_RAW)])

    monkeypatch.setattr(htu21d, "_write_message", lambda _address, _data: object())
    monkeypatch.setattr(htu21d, "_read_message", lambda _address, _length: next(frames))

    sensor = HTU21DSensor("air", bus, reset=False, sleep=lambda _s: None)
    readings = {item.name: item for item in sensor.read()}

    temp = temperature_from_raw(TEMP_RAW)
    humidity = compensate_humidity(humidity_from_raw(HUMIDITY_RAW), temp)
    assert readings["air_temp"].value == pytest.approx(round(temp, 2))
    assert readings["air_humidity"].value == pytest.approx(round(humidity, 2))
    assert readings["air_vpd"].unit == "kPa"
    assert readings["air_vpd"].value == pytest.approx(round(vpd_kpa(temp, humidity), 3))
    assert readings["air_dew_point"].unit == "°C"
    assert readings["air_dew_point"].value == pytest.approx(round(dew_point_c(temp, humidity), 2))
    assert all(item.quality == "ok" for item in readings.values())


def test_short_frame_returns_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = FakeBus()

    monkeypatch.setattr(htu21d, "_write_message", lambda _address, _data: object())
    monkeypatch.setattr(htu21d, "_read_message", lambda _address, _length: [0x00, 0x01])

    sensor = HTU21DSensor("air", bus, reset=False, sleep=lambda _s: None)
    readings = sensor.read()

    assert {item.name for item in readings} == AIR_NAMES
    assert all(item.quality == "invalid" for item in readings)


def test_bus_error_returns_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingBus:
        def i2c_rdwr(self, _message: object) -> None:
            raise OSError("bus caído")

    monkeypatch.setattr(htu21d, "_write_message", lambda _address, _data: object())
    sensor = HTU21DSensor("air", FailingBus(), reset=False, sleep=lambda _s: None)
    readings = sensor.read()

    assert {item.name for item in readings} == AIR_NAMES
    assert all(item.quality == "invalid" for item in readings)


def test_reset_failure_does_not_prevent_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ResetFailingBus:
        def i2c_rdwr(self, _message: object) -> None:
            raise OSError("sin dispositivo")

    monkeypatch.setattr(htu21d, "_write_message", lambda _address, _data: object())
    sensor = HTU21DSensor("air", ResetFailingBus(), sleep=lambda _s: None)

    assert sensor.id == "air"


def test_bad_crc_returns_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = FakeBus()
    frames = iter([frame(TEMP_RAW, bad_crc=True)])

    monkeypatch.setattr(htu21d, "_write_message", lambda _address, _data: object())
    monkeypatch.setattr(htu21d, "_read_message", lambda _address, _length: next(frames))

    sensor = HTU21DSensor("air", bus, reset=False, sleep=lambda _s: None)
    readings = sensor.read()
    assert all(item.quality == "invalid" for item in readings)
    assert {item.name for item in readings} == AIR_NAMES


def test_crc8_datasheet_vectors() -> None:
    # Ejemplos de la hoja de datos de TE Connectivity.
    assert crc8([0x68, 0x3A]) == 0x7C
    assert crc8([0x4E, 0x85]) == 0x6B
    assert crc8([0xDC]) == 0x79


def read_with_frames(monkeypatch: pytest.MonkeyPatch, *frames: list[int]) -> dict:
    pending = iter(frames)
    monkeypatch.setattr(htu21d, "_write_message", lambda _address, _data: object())
    monkeypatch.setattr(htu21d, "_read_message", lambda _address, _length: next(pending))
    sensor = HTU21DSensor("air", FakeBus(), reset=False, sleep=lambda _s: None)
    return {item.name: item for item in sensor.read()}


def test_all_zero_frame_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    # [0, 0, 0] pasa el CRC, pero -46,85 °C está fuera del rango del sensor.
    readings = read_with_frames(monkeypatch, [0, 0, 0], frame(HUMIDITY_RAW))
    assert set(readings) == AIR_NAMES
    assert all(item.quality == "invalid" for item in readings.values())


def test_swapped_frames_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    readings = read_with_frames(monkeypatch, frame(HUMIDITY_RAW), frame(TEMP_RAW))
    assert all(item.quality == "invalid" for item in readings.values())


def test_humidity_temperature_compensation() -> None:
    assert compensate_humidity(50.0, 25.0) == pytest.approx(50.0)
    assert compensate_humidity(50.0, 15.0) == pytest.approx(48.5)
    assert compensate_humidity(50.0, 35.0) == pytest.approx(51.5)
    assert compensate_humidity(50.0, -5.0) == pytest.approx(50.0)  # fuera de 0 a 80 °C
    assert compensate_humidity(0.5, 5.0) == 0.0
