from __future__ import annotations

import pytest

from hidro.drivers.ads1115 import ADS1115
from hidro.drivers.base import DriverError


class FakeBus:
    def __init__(self, raw: int) -> None:
        self.raw = raw
        self.writes: list[tuple[int, int, list[int]]] = []
        self.reads: list[tuple[int, int, int]] = []

    def write_i2c_block_data(self, address: int, register: int, data: list[int]) -> None:
        self.writes.append((address, register, data))

    def read_i2c_block_data(self, address: int, register: int, length: int) -> list[int]:
        self.reads.append((address, register, length))
        return [(self.raw >> 8) & 0xFF, self.raw & 0xFF]


def test_voltage_from_raw() -> None:
    assert ADS1115.voltage_from_raw(32767, 4.096) == pytest.approx(4.095875)
    assert ADS1115.voltage_from_raw(-32768, 4.096) == pytest.approx(-4.096)
    assert ADS1115.voltage_from_raw(0, 2.048) == 0.0


def test_config_word_channel_and_gain() -> None:
    bus = FakeBus(0)
    adc = ADS1115(bus, sleep=lambda _s: None)
    adc.read_raw(channel=1, gain_v=2.048)
    address, register, data = bus.writes[0]
    assert address == 0x48
    assert register == 0x01
    word = (data[0] << 8) | data[1]
    assert word == 0xD583
    assert bus.reads == [(0x48, 0x00, 2)]


def test_negative_raw_is_signed() -> None:
    adc = ADS1115(FakeBus(0x8000), sleep=lambda _s: None)
    assert adc.read_raw(0, 4.096) == -32768
    assert adc.read_voltage(0, 4.096) == pytest.approx(-4.096)


def test_invalid_channel_or_gain() -> None:
    adc = ADS1115(FakeBus(0), sleep=lambda _s: None)
    with pytest.raises(DriverError):
        adc.read_raw(4)
    with pytest.raises(DriverError):
        adc.read_raw(0, 1.5)


def test_median_voltage_is_trimmed() -> None:
    class SequenceBus(FakeBus):
        def __init__(self) -> None:
            super().__init__(0)
            self.values = [1000, 2000, 3000, 4000, 30000]
            self.index = 0

        def read_i2c_block_data(self, address: int, register: int, length: int) -> list[int]:
            raw = self.values[self.index]
            self.index += 1
            return [(raw >> 8) & 0xFF, raw & 0xFF]

    adc = ADS1115(SequenceBus(), sleep=lambda _s: None)
    voltage = adc.read_median_voltage(0, 4.096, samples=5, trim=1)
    assert voltage == pytest.approx(3000 * 4.096 / 32768)


def test_incomplete_read_raises() -> None:
    class ShortBus(FakeBus):
        def read_i2c_block_data(self, address: int, register: int, length: int) -> list[int]:
            return [0]

    adc = ADS1115(ShortBus(0), sleep=lambda _s: None)
    with pytest.raises(DriverError):
        adc.read_raw(0)
