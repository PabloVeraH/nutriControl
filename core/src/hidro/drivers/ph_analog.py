"""Driver de pH analógico (módulo PH-4502C a través de ADS1115)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from hidro.calibration import CalibrationStore
from hidro.drivers.base import DriverError, LatestReadings, invalid_measurement
from hidro.logging_setup import get_logger
from hidro.model import Measurement, Quality

logger = get_logger("drivers.ph")


class PHAnalogSensor:
    """Convierte el voltaje del módulo de pH en pH compensado por temperatura."""

    def __init__(
        self,
        sensor_id: str,
        adc: Any,
        calibration: CalibrationStore,
        latest: LatestReadings,
        *,
        channel: int = 0,
        gain_v: float = 4.096,
        samples: int = 10,
        temperature_from: str | None = None,
        temperature_max_age_s: float = 120.0,
    ) -> None:
        self.id = sensor_id
        self.adc = adc
        self.calibration = calibration
        self.latest = latest
        self.channel = channel
        self.gain_v = gain_v
        self.samples = samples
        self.temperature_from = temperature_from
        self.temperature_max_age_s = temperature_max_age_s

    def _water_temperature(self) -> float | None:
        if not self.temperature_from:
            return None
        measurement = self.latest.get(self.temperature_from)
        if measurement is None or not measurement.valid:
            return None
        age = (datetime.now(UTC) - measurement.timestamp).total_seconds()
        if age > self.temperature_max_age_s:
            return None
        return float(measurement.numeric)

    def read(self) -> list[Measurement]:
        now = datetime.now(UTC)
        calibration = self.calibration.ph()
        if calibration is None:
            logger.warning("pH %s sin calibrar: ejecutar 'hidro calibrate ph'", self.id)
            return [invalid_measurement(self.id, "pH", self.id, now)]

        try:
            voltage = self.adc.read_median_voltage(self.channel, self.gain_v, self.samples)
        except DriverError as exc:
            logger.error("pH %s: %s", self.id, exc)
            return [invalid_measurement(self.id, "pH", self.id, now)]

        temperature = self._water_temperature()
        quality: Quality = "ok"
        if temperature is None:
            quality = "suspect"
        if calibration.is_old(now):
            quality = "suspect"
            logger.warning(
                "calibración de pH con %.1f días; recalibrar", calibration.age_days(now)
            )
        if not calibration.slope_is_reasonable():
            quality = "suspect"
            logger.warning(
                "pendiente de pH anómala (%.3f V/pH); revisar calibración", calibration.slope
            )

        value = calibration.ph_from_voltage(voltage, temperature)
        if not 0.0 <= value <= 14.0:
            logger.error("pH %s fuera de rango: %.2f (voltaje %.4f V)", self.id, value, voltage)
            return [invalid_measurement(self.id, "pH", self.id, now)]

        return [
            Measurement(
                name=self.id,
                value=round(value, 3),
                unit="pH",
                timestamp=now,
                quality=quality,
                source=self.id,
            )
        ]

    def close(self) -> None:
        return None
