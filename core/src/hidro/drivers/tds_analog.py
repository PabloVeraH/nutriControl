"""Driver TDS/EC analógico (clon de DFRobot Gravity TDS)."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from hidro.calibration import CalibrationStore
from hidro.config import TDSAnalogConfig
from hidro.drivers.base import DriverError, LatestReadings, Switch, invalid_measurement
from hidro.logging_setup import get_logger
from hidro.model import Measurement, Quality

logger = get_logger("drivers.tds")


def gravity_polynomial(voltage: float) -> float:
    """Curva oficial de DFRobot GravityTDS (µS/cm, sin calibrar)."""
    return 133.42 * voltage**3 - 255.86 * voltage**2 + 857.39 * voltage


def ec_to_reference(ec: float, temperature_c: float, coefficient: float = 0.02) -> float:
    """Compensa la conductividad a 25 °C."""
    return ec / (1.0 + coefficient * (temperature_c - 25.0))


def calibration_k(voltage: float, temperature_c: float, standard_us_cm: float) -> float:
    """Factor k para una solución patrón."""
    raw = gravity_polynomial(voltage)
    target = standard_us_cm * (1.0 + 0.02 * (temperature_c - 25.0))
    if raw <= 0:
        raise ValueError("el voltaje medido no permite calcular k")
    return target / raw


class TDSAnalogSensor:
    """Mide EC y TDS encendiendo la sonda solo durante la lectura."""

    def __init__(
        self,
        sensor_id: str,
        adc: Any,
        calibration: CalibrationStore,
        latest: LatestReadings,
        *,
        channel: int = 1,
        gain_v: float = 4.096,
        samples: int = 10,
        temperature_from: str | None = None,
        power_switch: Switch | None = None,
        settle_s: float = 2.0,
        tds_factor: float = 0.5,
        temperature_max_age_s: float = 120.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.id = sensor_id
        self.adc = adc
        self.calibration = calibration
        self.latest = latest
        self.channel = channel
        self.gain_v = gain_v
        self.samples = samples
        self.temperature_from = temperature_from
        self.power_switch = power_switch
        self.settle_s = settle_s
        self.tds_factor = tds_factor
        self.temperature_max_age_s = temperature_max_age_s
        self._sleep = sleep

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

    def _sample_voltage(self) -> float:
        if self.power_switch is None:
            return self.adc.read_median_voltage(self.channel, self.gain_v, self.samples)
        self.power_switch.on()
        try:
            self._sleep(self.settle_s)
            return self.adc.read_median_voltage(self.channel, self.gain_v, self.samples)
        finally:
            self.power_switch.off()

    def read(self) -> list[Measurement]:
        now = datetime.now(UTC)
        try:
            voltage = self._sample_voltage()
        except DriverError as exc:
            logger.error("TDS %s: %s", self.id, exc)
            return [
                invalid_measurement(self.id, "µS/cm", self.id, now),
                invalid_measurement(f"{self.id}_ppm", "ppm", self.id, now),
            ]

        if not -0.05 <= voltage <= 3.3:
            logger.error("TDS %s: voltaje fuera de rango (%.4f V)", self.id, voltage)
            return [
                invalid_measurement(self.id, "µS/cm", self.id, now),
                invalid_measurement(f"{self.id}_ppm", "ppm", self.id, now),
            ]

        temperature = self._water_temperature()
        reference_temp = temperature if temperature is not None else 25.0
        calibration = self.calibration.ec()
        k = calibration.k if calibration else 1.0
        quality: Quality = "ok"
        if calibration is None:
            quality = "suspect"
            logger.warning("EC %s sin calibrar: se usa k=1.0", self.id)
        if temperature is None:
            quality = "suspect"

        ec_raw = gravity_polynomial(voltage) * k
        ec_25 = ec_to_reference(ec_raw, reference_temp)
        ppm = ec_25 * self.tds_factor

        return [
            Measurement(self.id, round(ec_25, 1), "µS/cm", now, quality, self.id),
            Measurement(f"{self.id}_ppm", round(ppm, 1), "ppm", now, quality, self.id),
        ]

    def close(self) -> None:
        if self.power_switch is not None:
            try:
                self.power_switch.off()
            except Exception:
                logger.warning("no se pudo apagar %s", self.id, exc_info=True)


def build_from_config(
    config: TDSAnalogConfig,
    *,
    adc: Any,
    calibration: CalibrationStore,
    latest: LatestReadings,
    power_switch: Switch | None,
) -> TDSAnalogSensor:
    return TDSAnalogSensor(
        config.id,
        adc,
        calibration,
        latest,
        channel=config.adc.channel,
        gain_v=config.adc.gain_v,
        samples=config.adc.samples,
        temperature_from=config.temperature_from,
        power_switch=power_switch,
        settle_s=config.settle_s,
        tds_factor=config.tds_factor,
    )
