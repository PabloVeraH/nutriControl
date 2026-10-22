"""Reglas de dosificación (sección 7.2 del plan)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from hidro.alerts.manager import Severity
from hidro.config import AppConfig, FloatSwitchConfig, FlowHallConfig
from hidro.control.safety import SafetyGuard
from hidro.drivers.base import DoseTooLong, LatestReadings, PumpNotCalibrated, Switch
from hidro.logging_setup import get_logger

logger = get_logger("control.dosing")

PH_DOWN = "pump_ph_down"
PH_UP = "pump_ph_up"
NUTRIENT_A = "pump_nutrient_a"
NUTRIENT_B = "pump_nutrient_b"


@dataclass(frozen=True)
class DoseAction:
    pump_id: str
    ml: float
    reason: str
    seconds: float


@dataclass(frozen=True)
class DosingNotice:
    key: str
    active: bool
    severity: Severity
    message: str


@dataclass
class DosingDecision:
    actions: list[DoseAction] = field(default_factory=list)
    notices: list[DosingNotice] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DosingFlags:
    flow_alarm: bool = False
    tank_low: bool = False
    ph_danger: bool = False


class DosingController:
    def __init__(
        self,
        config: AppConfig,
        outputs: dict[str, Switch],
        latest: LatestReadings,
        safety: SafetyGuard,
    ) -> None:
        self.config = config
        self.dosing = config.dosing
        self.outputs = outputs
        self.latest = latest
        self.safety = safety
        self.last_dose_at: datetime | None = None
        self._notice_keys: set[str] = set()

    # ------------------------------------------------------------------ plan

    def plan(self, now: datetime, flags: DosingFlags) -> DosingDecision:
        decision = DosingDecision()
        notices: dict[str, DosingNotice] = {}

        def notice(key: str, severity: Severity, message: str) -> None:
            notices[key] = DosingNotice(key, True, severity, message)

        if not self.dosing.enabled:
            decision.blocked.append("dosificación deshabilitada")
            self._finish(decision, notices)
            return decision

        max_age = self.config.read_interval_s * 2.5
        if flags.flow_alarm:
            decision.blocked.append("flujo en alarma")
        if flags.tank_low:
            decision.blocked.append("estanque bajo")
        # Sin lectura no hay alarma, pero tampoco certeza: no se dosifica a ciegas.
        for sensor in self.config.sensors:
            if isinstance(sensor, FlowHallConfig) and self._fresh_value(sensor.id, now, max_age) is None:
                decision.blocked.append(f"sin lectura reciente de flujo ({sensor.id})")
            if isinstance(sensor, FloatSwitchConfig) and (
                self._fresh_value(f"{sensor.id}_low", now, max_age) is None
            ):
                decision.blocked.append(f"sin lectura reciente del flotador ({sensor.id})")

        ph = self._fresh_value("ph", now, max_age)
        ec = self._fresh_value("ec", now, max_age)
        if ph is None or ec is None:
            decision.blocked.append("sin lecturas recientes de pH/EC")
            notice(
                "dosing-sensors",
                Severity.MEDIUM,
                "No se dosifica: falta una lectura reciente y confiable de pH o EC "
                "(sin calibrar o sin temperatura cuenta como no confiable).",
            )
            self._finish(decision, notices)
            return decision

        if self.last_dose_at is not None:
            waited = (now - self.last_dose_at).total_seconds()
            if waited < self.dosing.mix_wait_s:
                decision.blocked.append(
                    f"esperando mezcla ({waited:.0f}/{self.dosing.mix_wait_s:.0f} s)"
                )

        if decision.blocked:
            self._finish(decision, notices)
            return decision

        if flags.ph_danger or ph < self.config.ranges.ph.danger_low or ph > self.config.ranges.ph.danger_high:
            if ph < self.config.ranges.ph.danger_low:
                self._add_ph_action(decision, notices, PH_DOWN, self.dosing.ph_down_ml, ph, now, "pH bajo")
            else:
                self._add_ph_action(decision, notices, PH_UP, self.dosing.ph_up_ml, ph, now, "pH alto")
        elif ec < self.config.ranges.ec.low:
            self._add_nutrient_actions(decision, notices, ec, now)
        elif ec > self.config.ranges.ec.high:
            decision.blocked.append("EC sobre el máximo: diluir con agua")
        elif ph < self.config.ranges.ph.low:
            self._add_ph_action(decision, notices, PH_DOWN, self.dosing.ph_down_ml, ph, now, "pH bajo")
        elif ph > self.config.ranges.ph.high:
            self._add_ph_action(decision, notices, PH_UP, self.dosing.ph_up_ml, ph, now, "pH alto")

        self._finish(decision, notices)
        return decision

    # ------------------------------------------------------------------ acciones

    def _add_ph_action(
        self,
        decision: DosingDecision,
        notices: dict[str, DosingNotice],
        pump_id: str,
        ml: float,
        ph: float,
        now: datetime,
        reason: str,
    ) -> None:
        detail = f"pH {ph:.2f}: {reason}"
        self._schedule(decision, notices, pump_id, ml, detail, now, Severity.HIGH, f"pH {ph:.2f}")

    def _add_nutrient_actions(
        self,
        decision: DosingDecision,
        notices: dict[str, DosingNotice],
        ec: float,
        now: datetime,
    ) -> None:
        pumps = [NUTRIENT_A, NUTRIENT_B]
        available = [pump_id for pump_id in pumps if pump_id in self.outputs]
        if len(available) < 2:
            notices["dosing-nutrients"] = DosingNotice(
                "dosing-nutrients",
                True,
                Severity.MEDIUM,
                "EC bajo el mínimo, pero faltan bombas de nutrientes A/B: "
                "agregar nutriente manualmente.",
            )
            return
        detail = f"EC {ec:.0f} µS/cm bajo el mínimo"
        for pump_id in available:
            self._schedule(
                decision,
                notices,
                pump_id,
                self.dosing.nutrient_ml,
                detail,
                now,
                Severity.MEDIUM,
                detail,
            )

    def _schedule(
        self,
        decision: DosingDecision,
        notices: dict[str, DosingNotice],
        pump_id: str,
        ml: float,
        reason: str,
        now: datetime,
        severity: Severity,
        context: str,
    ) -> None:
        pump = self.outputs.get(pump_id)
        missing_key = f"dosing-missing-{pump_id}"
        if pump is None or not hasattr(pump, "dispense_ml"):
            notices[missing_key] = DosingNotice(
                missing_key,
                True,
                severity,
                f"{context}: falta la bomba '{pump_id}'. Corregir manualmente.",
            )
            return

        ml_per_min = getattr(pump, "ml_per_min", None)
        max_on_s = getattr(pump, "max_on_s", 30.0)
        if ml_per_min is None or ml_per_min <= 0:
            key = f"dosing-uncalibrated-{pump_id}"
            notices[key] = DosingNotice(
                key,
                True,
                Severity.MEDIUM,
                f"{context}: la bomba '{pump_id}' no está calibrada; no se dosifica.",
            )
            return

        seconds = ml / ml_per_min * 60.0
        if seconds > max_on_s:
            key = f"dosing-too-long-{pump_id}"
            notices[key] = DosingNotice(
                key,
                True,
                Severity.HIGH,
                f"{context}: la dosis de {ml:g} ml requiere {seconds:.0f} s en "
                f"'{pump_id}' (máximo {max_on_s:.0f} s). Revisar calibración.",
            )
            return

        safety_reason = self.safety.block_reason(pump_id, ml, now)
        if safety_reason is not None:
            key = f"safety-{pump_id}"
            notices[key] = DosingNotice(
                key,
                True,
                Severity.HIGH,
                f"Bomba '{pump_id}' bloqueada por seguridad: {safety_reason}. "
                "Revisar y ejecutar 'hidro safety-reset'.",
            )
            return

        decision.actions.append(
            DoseAction(pump_id=pump_id, ml=ml, reason=reason, seconds=seconds)
        )

    # ------------------------------------------------------------------ utilidades

    def _fresh_value(self, name: str, now: datetime, max_age_s: float) -> float | None:
        measurement = self.latest.get(name)
        # Plan 7.2: también bloquea "suspect" (EC sin calibrar, pH sin temperatura).
        if measurement is None or measurement.quality != "ok":
            return None
        if (now - measurement.timestamp).total_seconds() > max_age_s:
            return None
        return float(measurement.numeric)

    def _finish(
        self, decision: DosingDecision, notices: dict[str, DosingNotice]
    ) -> None:
        current = set(notices)
        for key in self._notice_keys - current:
            decision.notices.append(DosingNotice(key, False, Severity.MEDIUM, ""))
        decision.notices.extend(notices.values())
        self._notice_keys = current


def execute_action(
    action: DoseAction,
    output: Switch,
    *,
    dry_run: bool,
    safety: SafetyGuard,
    now: datetime,
) -> str:
    """Ejecuta una dosis (o la simula) y devuelve el texto para Telegram."""
    pump = output
    if dry_run:
        safety.record(action.pump_id, action.ml, now, dry_run=True)
        return (
            f"🧪 SIMULACIÓN: {action.reason}. "
            f"Dosificaría {action.ml:g} ml con {action.pump_id} ({action.seconds:.0f} s)."
        )

    try:
        seconds = pump.dispense_ml(action.ml)  # type: ignore[attr-defined]
    except (PumpNotCalibrated, DoseTooLong) as exc:
        raise RuntimeError(str(exc)) from exc
    safety.record(action.pump_id, action.ml, now)
    return (
        f"💧 Dosificado {action.ml:g} ml con {action.pump_id} "
        f"({seconds:.0f} s). Motivo: {action.reason}."
    )
