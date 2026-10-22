"""Driver de pH analógico (módulo PH-4502C a través de ADS1115)."""

from __future__ import annotations

import time
from collections.abc import Callable
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
        self._sleep = time.sleep

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


def voltage_slope(v1: float, ph1: float, v2: float, ph2: float) -> float:
    """Pendiente en V/pH para dos puntos de calibración."""
    if abs(ph1 - ph2) < 1e-9:
        raise ValueError("los puntos de calibración deben tener pH distinto")
    return (v1 - v2) / (ph1 - ph2)


def voltage_at_ph7(v1: float, ph1: float, slope: float) -> float:
    return v1 + slope * (7.0 - ph1)


def ph_from_voltage(voltage: float, v7: float, slope: float) -> float:
    if abs(slope) < 1e-9:
        raise ValueError("pendiente de calibración nula")
    return 7.0 + (voltage - v7) / slope


def calibrate_two_points(
    point_a: tuple[float, float],
    point_b: tuple[float, float],
    temperature_c: float,
) -> dict[str, float]:
    """Calcula pendiente, V7 y el voltaje esperado en pH 7."""
    v1, ph1 = point_a
    v2, ph2 = point_b
    slope = voltage_slope(v1, ph1, v2, ph2)
    return {
        "slope": slope,
        "v7": voltage_at_ph7(v1, ph1, slope),
        "temperature_c": temperature_c,
    }


def mean_sample(samples: list[float]) -> float:
    if not samples:
        raise ValueError("sin muestras")
    return sum(samples) / len(samples)


def sleep_s(seconds: float) -> Callable[[], None]:
    return lambda: time.sleep(seconds)
