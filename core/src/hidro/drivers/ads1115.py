"""Driver ADS1115 (ADC I2C de 16 bits)."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from hidro.calc import trimmed_mean
from hidro.drivers.base import DriverError
from hidro.logging_setup import get_logger

logger = get_logger("drivers.ads1115")

REG_CONVERSION = 0x00
REG_CONFIG = 0x01

OS_SINGLE = 0x8000
MODE_SINGLE = 0x0100
DR_128_SPS = 0x0080
COMP_DISABLED = 0x0003

_PGA_BITS = {
    4.096: 0x0200,
    2.048: 0x0400,
    1.024: 0x0600,
    0.512: 0x0800,
    0.256: 0x0A00,
}


class ADS1115:
    """Lecturas single-shot en modo single-ended."""

    def __init__(
        self,
        bus: Any,
        *,
        address: int = 0x48,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.bus = bus
        self.address = address
        self._sleep = sleep

    @staticmethod
    def voltage_from_raw(raw: int, gain_v: float) -> float:
        return raw * gain_v / 32768.0

    def _config_word(self, channel: int, gain_v: float) -> int:
        if channel not in (0, 1, 2, 3):
            raise DriverError(f"canal ADS1115 inválido: {channel}")
        if gain_v not in _PGA_BITS:
            raise DriverError(f"ganancia ADS1115 no soportada: {gain_v}")
        mux = 0x4000 | (channel << 12)
        return OS_SINGLE | mux | _PGA_BITS[gain_v] | MODE_SINGLE | DR_128_SPS | COMP_DISABLED

    def read_raw(self, channel: int, gain_v: float = 4.096) -> int:
        config = self._config_word(channel, gain_v)
        payload = [(config >> 8) & 0xFF, config & 0xFF]
        try:
            self.bus.write_i2c_block_data(self.address, REG_CONFIG, payload)
            self._sleep(0.012)
            data = self.bus.read_i2c_block_data(self.address, REG_CONVERSION, 2)
        except OSError as exc:
            raise DriverError(f"error I2C con ADS1115: {exc}") from exc
        if len(data) != 2:
            raise DriverError(f"lectura incompleta del ADS1115 ({len(data)} bytes)")
        raw = (data[0] << 8) | data[1]
        if raw & 0x8000:
            raw -= 0x10000
        return raw

    def read_voltage(self, channel: int, gain_v: float = 4.096) -> float:
        return self.voltage_from_raw(self.read_raw(channel, gain_v), gain_v)

    def read_median_voltage(
        self,
        channel: int,
        gain_v: float = 4.096,
        samples: int = 10,
        trim: int = 1,
    ) -> float:
        values = [self.read_voltage(channel, gain_v) for _ in range(max(1, samples))]
        return trimmed_mean(values, trim=trim)

    def close(self) -> None:
        return None
