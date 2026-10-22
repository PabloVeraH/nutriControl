"""Driver HTU21D (temperatura y humedad, I2C)."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from hidro.calc import clamp, dew_point_c, vpd_kpa
from hidro.drivers.base import DriverError, invalid_measurement
from hidro.logging_setup import get_logger
from hidro.model import Measurement

logger = get_logger("drivers.htu21d")

CMD_RESET = 0xFE
CMD_TEMP = 0xF3
CMD_HUMIDITY = 0xF5


def _write_message(address: int, data: list[int]) -> Any:
    from smbus2 import i2c_msg

    return i2c_msg.write(address, data)


def _read_message(address: int, length: int) -> Any:
    from smbus2 import i2c_msg

    return i2c_msg.read(address, length)


def crc8(data: Sequence[int]) -> int:
    """CRC-8 del HTU21D: polinomio 0x31, valor inicial 0x00."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def temperature_from_raw(raw: int) -> float:
    return -46.85 + 175.72 * (raw & 0xFFFC) / 65536.0


def humidity_from_raw(raw: int) -> float:
    return clamp(-6.0 + 125.0 * (raw & 0xFFFC) / 65536.0, 0.0, 100.0)


class HTU21DSensor:
    """Lee temperatura y humedad del aire, validando el CRC de cada trama."""

    def __init__(
        self,
        sensor_id: str,
        bus: Any,
        *,
        address: int = 0x40,
        reset: bool = True,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.id = sensor_id
        self.bus = bus
        self.address = address
        self._sleep = sleep
        if reset:
            try:
                self._send(CMD_RESET)
                self._sleep(0.015)
            except OSError as exc:
                logger.warning("no se pudo reiniciar el HTU21D %s: %s", self.id, exc)

    def _send(self, command: int) -> None:
        message = _write_message(self.address, [command])
        self.bus.i2c_rdwr(message)

    def _read_raw(self, command: int, delay_s: float) -> int:
        try:
            self._send(command)
            self._sleep(delay_s)
            message = _read_message(self.address, 3)
            self.bus.i2c_rdwr(message)
            data = list(message)
        except OSError as exc:
            raise DriverError(f"error I2C con HTU21D: {exc}") from exc

        if len(data) != 3:
            raise DriverError(f"trama incompleta del HTU21D ({len(data)} bytes)")
        if crc8(data[:2]) != data[2]:
            raise DriverError("CRC inválido en la trama del HTU21D")
        return (data[0] << 8) | data[1]

    def read(self) -> list[Measurement]:
        now = datetime.now(UTC)
        try:
            temp = temperature_from_raw(self._read_raw(CMD_TEMP, 0.060))
            humidity = humidity_from_raw(self._read_raw(CMD_HUMIDITY, 0.020))
        except DriverError as exc:
            logger.error("HTU21D %s sin lectura: %s", self.id, exc)
            return [
                invalid_measurement(f"{self.id}_temp", "°C", self.id, now),
                invalid_measurement(f"{self.id}_humidity", "%", self.id, now),
            ]

        return [
            Measurement(f"{self.id}_temp", round(temp, 2), "°C", now, "ok", self.id),
            Measurement(f"{self.id}_humidity", round(humidity, 2), "%", now, "ok", self.id),
            Measurement(f"{self.id}_vpd", round(vpd_kpa(temp, humidity), 3), "kPa", now, "ok", self.id),
            Measurement(
                f"{self.id}_dew_point", round(dew_point_c(temp, humidity), 2), "°C", now, "ok", self.id
            ),
        ]

    def close(self) -> None:
        return None
