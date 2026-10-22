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

# Bit 1 del LSB indica el tipo de medición (hoja de datos): 0 = temperatura, 1 = humedad.
STATUS_HUMIDITY = 0x02
# Rango de operación del HTU21D; fuera de él la trama es basura (p. ej. todo ceros,
# que pasa el CRC y daría -46,85 °C).
TEMP_MIN_C = -40.0
TEMP_MAX_C = 125.0
# Coeficiente de compensación de humedad por temperatura (hoja de datos, 0 a 80 °C).
RH_TEMP_COEFF = -0.15


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


def compensate_humidity(relative_humidity: float, temp_c: float) -> float:
    """RH compensada = RH + (25 - T) * -0.15, válida entre 0 y 80 °C."""
    if not 0.0 <= temp_c <= 80.0:
        return relative_humidity
    return clamp(relative_humidity + (25.0 - temp_c) * RH_TEMP_COEFF, 0.0, 100.0)


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

    def _read_raw(self, command: int, delay_s: float, *, humidity: bool) -> int:
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
        if bool(data[1] & STATUS_HUMIDITY) != humidity:
            raise DriverError("la trama del HTU21D no corresponde a la medición pedida")
        return (data[0] << 8) | data[1]

    def read(self) -> list[Measurement]:
        now = datetime.now(UTC)
        try:
            temp = temperature_from_raw(self._read_raw(CMD_TEMP, 0.060, humidity=False))
            if not TEMP_MIN_C <= temp <= TEMP_MAX_C:
                raise DriverError(f"temperatura fuera del rango del sensor ({temp:.2f} °C)")
            humidity = compensate_humidity(
                humidity_from_raw(self._read_raw(CMD_HUMIDITY, 0.020, humidity=True)), temp
            )
        except DriverError as exc:
            logger.error("HTU21D %s sin lectura: %s", self.id, exc)
            return invalid_air_measurements(self.id, now)

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


def invalid_air_measurements(sensor_id: str, now: datetime) -> list[Measurement]:
    """Las cuatro magnitudes también en el fallo: así `hidro read` muestra cuál falló."""
    return [
        invalid_measurement(f"{sensor_id}_temp", "°C", sensor_id, now),
        invalid_measurement(f"{sensor_id}_humidity", "%", sensor_id, now),
        invalid_measurement(f"{sensor_id}_vpd", "kPa", sensor_id, now),
        invalid_measurement(f"{sensor_id}_dew_point", "°C", sensor_id, now),
    ]
