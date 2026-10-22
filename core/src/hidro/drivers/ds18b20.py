"""Driver DS18B20 por 1-Wire (sysfs)."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from hidro.drivers.base import DriverError, invalid_measurement
from hidro.logging_setup import get_logger
from hidro.model import Measurement

logger = get_logger("drivers.ds18b20")

_TEMPERATURE_RE = re.compile(r"t=(-?\d+)")


class DS18B20Sensor:
    """Lee una sonda DS18B20 desde /sys/bus/w1/devices."""

    def __init__(
        self,
        sensor_id: str,
        serial: str,
        *,
        offset_c: float = 0.0,
        base_path: str | Path = "/sys/bus/w1/devices",
        retries: int = 2,
        retry_delay_s: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not serial:
            raise DriverError(
                f"DS18B20 '{sensor_id}' sin serial; se obtiene en la fase 1 con "
                "ls /sys/bus/w1/devices/"
            )
        self.id = sensor_id
        self.serial = serial
        self.offset_c = offset_c
        self.base_path = Path(base_path)
        self.retries = retries
        self.retry_delay_s = retry_delay_s
        self._sleep = sleep

    @property
    def device_path(self) -> Path:
        return self.base_path / self.serial

    def read(self) -> list[Measurement]:
        reason = "sin intentos"
        for attempt in range(self.retries + 1):
            value, reason = self._read_once()
            if value is not None:
                return [
                    Measurement(
                        name=self.id,
                        value=round(value + self.offset_c, 3),
                        unit="°C",
                        timestamp=datetime.now(UTC),
                        quality="ok",
                        source=self.id,
                    )
                ]
            logger.warning(
                "lectura DS18B20 %s falló (intento %s/%s): %s",
                self.id,
                attempt + 1,
                self.retries + 1,
                reason,
            )
            if attempt < self.retries:
                self._sleep(self.retry_delay_s)

        logger.error("DS18B20 %s sin lectura válida: %s", self.id, reason)
        return [invalid_measurement(self.id, "°C", self.id)]

    def _read_once(self) -> tuple[float | None, str]:
        try:
            raw = (self.device_path / "w1_slave").read_text(encoding="ascii")
        except OSError as exc:
            return None, f"no se pudo leer {self.device_path}: {exc}"

        lines = raw.splitlines()
        if not lines or not lines[0].strip().endswith("YES"):
            return None, "CRC inválido"

        match = _TEMPERATURE_RE.search(raw)
        if match is None:
            return None, "respuesta sin temperatura"

        value = int(match.group(1)) / 1000.0
        if abs(value - 85.0) < 0.01:
            return None, "valor de encendido 85 °C (falla de comunicación)"
        if not -55.0 <= value <= 125.0:
            return None, f"temperatura fuera de rango ({value} °C)"
        return value, ""

    def close(self) -> None:
        return None
