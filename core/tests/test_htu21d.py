from __future__ import annotations

import pytest

from hidro.calc import dew_point_c, vpd_kpa
from hidro.drivers import htu21d
from hidro.drivers.htu21d import (
    HTU21DSensor,
    crc8,
    humidity_from_raw,
    temperature_from_raw,
)


class FakeBus:
    def __init__(self) -> None:
        self.messages: list[object] = []

    def i2c_rdwr(self, message: object) -> None:
        self.messages.append(message)


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
    frames = iter([frame(0x6666), frame(0x5F5C)])

    monkeypatch.setattr(htu21d, "_write_message", lambda _address, _data: object())
    monkeypatch.setattr(htu21d, "_read_message", lambda _address, _length: next(frames))

    sensor = HTU21DSensor("air", bus, reset=False, sleep=lambda _s: None)
    readings = {item.name: item for item in sensor.read()}

    temp = temperature_from_raw(0x6666)
    humidity = humidity_from_raw(0x5F5C)
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

    assert {item.name for item in readings} == {"air_temp", "air_humidity"}
    assert all(item.quality == "invalid" for item in readings)


def test_bus_error_returns_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingBus:
        def i2c_rdwr(self, _message: object) -> None:
            raise OSError("bus caído")

    monkeypatch.setattr(htu21d, "_write_message", lambda _address, _data: object())
    sensor = HTU21DSensor("air", FailingBus(), reset=False, sleep=lambda _s: None)
    readings = sensor.read()

    assert {item.name for item in readings} == {"air_temp", "air_humidity"}
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
    frames = iter([frame(0x6666, bad_crc=True)])

    monkeypatch.setattr(htu21d, "_write_message", lambda _address, _data: object())
    monkeypatch.setattr(htu21d, "_read_message", lambda _address, _length: next(frames))

    sensor = HTU21DSensor("air", bus, reset=False, sleep=lambda _s: None)
    readings = sensor.read()
    assert all(item.quality == "invalid" for item in readings)
    assert {item.name for item in readings} == {"air_temp", "air_humidity"}
