"""Reglas de monitoreo y alertas (sección 7.1 del plan)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hidro.alerts.manager import Severity
from hidro.config import (
    AppConfig,
    DS18B20Config,
    FloatSwitchConfig,
    FlowHallConfig,
    HTU21DConfig,
    PHAnalogConfig,
    TDSAnalogConfig,
    measurement_names_for,
)
from hidro.control.dosing import DosingFlags
from hidro.drivers.base import LatestReadings
from hidro.logging_setup import get_logger
from hidro.model import Measurement

logger = get_logger("control.monitor")


@dataclass(frozen=True)
class MonitorSignal:
    key: str
    active: bool
    severity: Severity
    message: str
    resolved_message: str = ""


@dataclass(frozen=True)
class MonitorResult:
    signals: list[MonitorSignal]
    conditions: DosingFlags


class Monitor:
    def __init__(self, config: AppConfig, latest: LatestReadings) -> None:
        self.config = config
        self.latest = latest
        self._since: dict[str, datetime] = {}

    def evaluate(self, now: datetime) -> MonitorResult:
        signals: list[MonitorSignal] = []
        signals.extend(self._sensor_signals(now))
        signals.extend(self._water_signals(now))
        signals.extend(self._air_signals(now))

        flow_signal, flow_alarm = self._flow_signal(now)
        signals.append(flow_signal)

        tank_signal, tank_low = self._tank_signal(now)
        signals.append(tank_signal)

        ph_signals, ph_danger = self._ph_signals(now)
        signals.extend(ph_signals)

        ec_signals, _ec_low, _ec_high = self._ec_signals(now)
        signals.extend(ec_signals)

        conditions = DosingFlags(
            flow_alarm=flow_alarm,
            tank_low=tank_low,
            ph_danger=ph_danger,
        )
        return MonitorResult(signals=signals, conditions=conditions)

    # ------------------------------------------------------------------ sensores

    def _sensor_signals(self, now: datetime) -> list[MonitorSignal]:
        signals = []
        for sensor in self.config.sensors:
            key = f"sensor-{sensor.id}"
            names = measurement_names_for(sensor)
            fresh = any(self._fresh(name, now, self.config.monitoring.stale_after_s) for name in names)
            signals.append(
                MonitorSignal(
                    key=key,
                    active=not fresh,
                    severity=Severity.MEDIUM,
                    message=(
                        f"Sin lectura válida de '{sensor.id}' desde hace más de "
                        f"{self.config.monitoring.stale_after_s / 60:.0f} min."
                    ),
                    resolved_message=f"Lectura de '{sensor.id}' normalizada.",
                )
            )
        return signals

    def _water_signals(self, now: datetime) -> list[MonitorSignal]:
        signals = []
        limit = self.config.ranges.water_temp.high_alert
        for sensor in self.config.sensors:
            if not isinstance(sensor, DS18B20Config):
                continue
            measurement = self._fresh(sensor.id, now, self.config.monitoring.stale_after_s)
            active = measurement is not None and float(measurement.numeric) > limit
            value = float(measurement.numeric) if measurement is not None else 0.0
            signals.append(
                MonitorSignal(
                    key=f"water-temp-{sensor.id}",
                    active=active,
                    severity=Severity.MEDIUM,
                    message=(
                        f"Temperatura del agua '{sensor.id}' alta: {value:.1f} °C "
                        f"(límite {limit:g} °C)."
                    ),
                    resolved_message=f"Temperatura del agua '{sensor.id}' normalizada.",
                )
            )
        return signals

    def _air_signals(self, now: datetime) -> list[MonitorSignal]:
        signals = []
        low = self.config.ranges.air_temp.low_alert
        high = self.config.ranges.air_temp.high_alert
        for sensor in self.config.sensors:
            if not isinstance(sensor, HTU21DConfig):
                continue
            measurement = self._fresh(
                f"{sensor.id}_temp", now, self.config.monitoring.stale_after_s
            )
            active = measurement is not None and not low <= float(measurement.numeric) <= high
            value = float(measurement.numeric) if measurement is not None else 0.0
            signals.append(
                MonitorSignal(
                    key=f"air-temp-{sensor.id}",
                    active=active,
                    severity=Severity.MEDIUM,
                    message=(
                        f"Temperatura del aire '{sensor.id}' fuera de rango: "
                        f"{value:.1f} °C (objetivo {low:g}–{high:g} °C)."
                    ),
                    resolved_message=f"Temperatura del aire '{sensor.id}' normalizada.",
                )
            )
        return signals

    # ------------------------------------------------------------------ flujo

    def _flow_signal(self, now: datetime) -> tuple[MonitorSignal, bool]:
        config = next(
            (sensor for sensor in self.config.sensors if isinstance(sensor, FlowHallConfig)), None
        )
        if config is None:
            return (
                MonitorSignal("flow", False, Severity.CRITICAL, "", ""),
                False,
            )

        measurement = self._fresh(config.id, now, self.config.monitoring.stale_after_s)
        minimum = self.config.ranges.flow.min_l_min
        low = measurement is not None and float(measurement.numeric) < minimum
        active = self._sustained("flow", low, now, self.config.ranges.flow.alarm_after_s)
        value = float(measurement.numeric) if measurement is not None else 0.0
        return (
            MonitorSignal(
                key="flow",
                active=active,
                severity=Severity.CRITICAL,
                message=(
                    f"FLUJO DE AGUA DETENIDO O INSUFICIENTE: {value:.2f} L/min "
                    f"(mínimo {minimum:g} L/min). Revisar la bomba de inmediato."
                ),
                resolved_message=f"Flujo de agua normalizado ({value:.2f} L/min).",
            ),
            active,
        )

    # ------------------------------------------------------------------ estanque

    def _tank_signal(self, now: datetime) -> tuple[MonitorSignal, bool]:
        config = next(
            (sensor for sensor in self.config.sensors if isinstance(sensor, FloatSwitchConfig)),
            None,
        )
        if config is None:
            return MonitorSignal("tank-low", False, Severity.HIGH, "", ""), False

        measurement = self._fresh(
            f"{config.id}_low", now, self.config.monitoring.stale_after_s
        )
        active = bool(measurement.value) if measurement is not None else False
        return (
            MonitorSignal(
                key="tank-low",
                active=active,
                severity=Severity.HIGH,
                message=(
                    "ESTANQUE BAJO: el flotador indica nivel insuficiente. "
                    "Rellenar antes de que la bomba tome aire."
                ),
                resolved_message="Nivel de estanque normalizado.",
            ),
            active,
        )

    # ------------------------------------------------------------------ pH

    def _ph_signals(self, now: datetime) -> tuple[list[MonitorSignal], bool]:
        config = next(
            (sensor for sensor in self.config.sensors if isinstance(sensor, PHAnalogConfig)), None
        )
        ranges = self.config.ranges.ph
        if config is None:
            return [], False

        measurement = self._fresh(config.id, now, self.config.monitoring.stale_after_s)
        if measurement is None:
            return [
                MonitorSignal("ph-danger", False, Severity.HIGH, "", ""),
                MonitorSignal("ph-range", False, Severity.MEDIUM, "", ""),
            ], False

        value = float(measurement.numeric)
        danger = value < ranges.danger_low or value > ranges.danger_high
        outside = value < ranges.low or value > ranges.high

        range_active = False if danger else self._sustained(
            "ph-range", outside, now, self.config.monitoring.ph_sustained_s
        )

        return (
            [
                MonitorSignal(
                    key="ph-danger",
                    active=danger,
                    severity=Severity.HIGH,
                    message=(
                        f"pH EN ZONA DE PELIGRO: {value:.2f} "
                        f"(seguro {ranges.danger_low:g}–{ranges.danger_high:g})."
                    ),
                    resolved_message=f"pH fuera de peligro ({value:.2f}).",
                ),
                MonitorSignal(
                    key="ph-range",
                    active=range_active,
                    severity=Severity.MEDIUM,
                    message=(
                        f"pH fuera del rango objetivo: {value:.2f} "
                        f"(objetivo {ranges.low:g}–{ranges.high:g}) durante más de "
                        f"{self.config.monitoring.ph_sustained_s / 60:.0f} min."
                    ),
                    resolved_message=f"pH dentro del rango objetivo ({value:.2f}).",
                ),
            ],
            danger,
        )

    # ------------------------------------------------------------------ EC

    def _ec_signals(self, now: datetime) -> tuple[list[MonitorSignal], bool, bool]:
        config = next(
            (sensor for sensor in self.config.sensors if isinstance(sensor, TDSAnalogConfig)), None
        )
        ranges = self.config.ranges.ec
        if config is None:
            return [
                MonitorSignal("ec-low", False, Severity.MEDIUM, "", ""),
                MonitorSignal("ec-high", False, Severity.MEDIUM, "", ""),
            ], False, False

        measurement = self._fresh(config.id, now, self.config.monitoring.stale_after_s)
        if measurement is None:
            return [
                MonitorSignal("ec-low", False, Severity.MEDIUM, "", ""),
                MonitorSignal("ec-high", False, Severity.MEDIUM, "", ""),
            ], False, False

        value = float(measurement.numeric)
        low = value < ranges.low
        high = value > ranges.high
        low_active = False if high else self._sustained(
            "ec-low", low, now, self.config.monitoring.ec_low_sustained_s
        )

        return (
            [
                MonitorSignal(
                    key="ec-low",
                    active=low_active,
                    severity=Severity.MEDIUM,
                    message=(
                        f"EC bajo el mínimo: {value:.0f} µS/cm "
                        f"(mínimo {ranges.low:g}) durante más de "
                        f"{self.config.monitoring.ec_low_sustained_s / 60:.0f} min."
                    ),
                    resolved_message=f"EC dentro del rango ({value:.0f} µS/cm).",
                ),
                MonitorSignal(
                    key="ec-high",
                    active=high,
                    severity=Severity.MEDIUM,
                    message=(
                        f"EC sobre el máximo: {value:.0f} µS/cm "
                        f"(máximo {ranges.high:g}). Agregar agua para diluir."
                    ),
                    resolved_message=f"EC dentro del rango ({value:.0f} µS/cm).",
                ),
            ],
            low,
            high,
        )

    # ------------------------------------------------------------------ utilidades

    def _fresh(self, name: str, now: datetime, max_age_s: float) -> Measurement | None:
        measurement = self.latest.get(name)
        if measurement is None or not measurement.valid:
            return None
        if (now - measurement.timestamp).total_seconds() > max_age_s:
            return None
        return measurement

    def _sustained(self, key: str, active: bool, now: datetime, hold_s: float) -> bool:
        if not active:
            self._since.pop(key, None)
            return False
        since = self._since.setdefault(key, now)
        return (now - since).total_seconds() >= hold_s
