"""Estado operativo, resumen diario y mensajes para Telegram."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, tzinfo
from pathlib import Path

from hidro.alerts.manager import AlertManager
from hidro.alerts.telegram import TelegramNotifier
from hidro.calibration import CalibrationStore
from hidro.config import AppConfig
from hidro.control.dosing import DosingController
from hidro.control.safety import SafetyGuard
from hidro.drivers.base import LatestReadings, Switch
from hidro.logging_setup import get_logger
from hidro.model import Measurement
from hidro.storage.influx import DailyStats, VariableStats

logger = get_logger("reporting")


def parse_daily_time(value: str) -> time:
    """Convierte HH:MM en ``time``; la configuración ya valida el formato."""
    hour, minute = value.split(":")
    return time(hour=int(hour), minute=int(minute))


class DailySummary:
    """Envía el resumen una vez al día, sobreviviendo a reinicios."""

    def __init__(
        self,
        notifier: TelegramNotifier,
        *,
        at: str,
        timezone: tzinfo,
        state_path: Path,
    ) -> None:
        self.notifier = notifier
        self.at = parse_daily_time(at)
        self.timezone = timezone
        self.state_path = Path(state_path)
        self._last_sent: date | None = self._load()

    def is_due(self, now: datetime) -> bool:
        local = now.astimezone(self.timezone)
        if self._last_sent is not None and local.date() <= self._last_sent:
            return False
        return (local.hour, local.minute) >= (self.at.hour, self.at.minute)

    def maybe_send(self, now: datetime, text: str) -> bool:
        if not self.is_due(now):
            return False
        local = now.astimezone(self.timezone)

        if not self.notifier.enabled:
            logger.info("resumen diario sin Telegram habilitado/configurado:\n%s", text)
        elif not self.notifier.send(text):
            logger.error("no se pudo enviar el resumen diario; se reintenta")
            return False

        self._last_sent = local.date()
        self._save()
        logger.info("resumen diario enviado para %s", local.date())
        return True

    def _load(self) -> date | None:
        if not self.state_path.exists():
            return None
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            value = raw.get("last_sent")
            return date.fromisoformat(value) if value else None
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            logger.warning("no se pudo leer el estado del resumen diario: %s", exc)
            return None

    def _save(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            payload = {"last_sent": self._last_sent.isoformat() if self._last_sent else None}
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp, self.state_path)
        except OSError as exc:
            # El resumen ya se envió; fallar al guardar solo puede duplicarlo.
            logger.warning("no se pudo guardar el estado del resumen diario: %s", exc)


def build_status_text(
    *,
    config: AppConfig,
    latest: LatestReadings,
    alerts: AlertManager,
    safety: SafetyGuard,
    dosing: DosingController,
    outputs: Mapping[str, Switch],
    calibration: CalibrationStore | None,
    now: datetime,
    timezone: tzinfo,
    started_at: datetime,
    heading: str = "📋 ESTADO",
) -> str:
    """Mensaje compacto con lo necesario para decidir sin abrir Grafana."""
    local = now.astimezone(timezone)
    lines = [
        heading,
        f"{local:%Y-%m-%d %H:%M %Z} · modo {config.mode}",
        f"En marcha desde hace {_format_uptime(now - started_at)}.",
        "",
        "Dosificación: " + _dosing_label(dosing),
    ]

    if dosing.last_dose_at is not None:
        lines.append(f"Última dosis: {_local_time(dosing.last_dose_at, timezone)}")
    else:
        lines.append("Última dosis: ninguna desde el arranque")

    ph_calibration = calibration.ph() if calibration is not None else None
    if ph_calibration is None:
        lines.append("Calibración de pH: sin calibrar")
    else:
        lines.append(
            "Calibración de pH: "
            f"{ph_calibration.age_days(now):.0f} días"
            f"{' (VENCIDA)' if ph_calibration.is_old(now) else ''}"
        )

    lines.append("")
    active = alerts.active_alerts
    if active:
        lines.append("Alertas activas:")
        lines.extend(f"  - {key}: {message}" for key, message in sorted(active.items()))
    else:
        lines.append("Alertas activas: ninguna")

    if alerts.is_silenced(now) and alerts.silenced_until is not None:
        lines.append(
            "Alertas no críticas silenciadas hasta "
            f"{_local_time(alerts.silenced_until, timezone)}."
        )
    else:
        lines.append("Alertas: sin silencio temporal.")

    blocked = safety.blocked
    if blocked:
        lines.append("Bombas bloqueadas:")
        lines.extend(f"  - {pump}: {reason}" for pump, reason in sorted(blocked.items()))
    else:
        lines.append("Bombas bloqueadas: ninguna")

    on_outputs = sorted(output_id for output_id, output in outputs.items() if output.is_on)
    lines.append(
        "Salidas encendidas: " + (", ".join(on_outputs) if on_outputs else "ninguna")
    )

    lines.append("")
    lines.append("Lecturas:")
    snapshot = latest.snapshot()
    for name in config.measurement_names():
        measurement = snapshot.get(name)
        invalid = latest.invalid(name)
        text = _format_measurement(measurement)
        if invalid is not None:
            if measurement is None:
                text = f"sin lectura (lectura actual {invalid.quality})"
            else:
                text = f"{text} — lectura actual {invalid.quality}"
        lines.append(f"  {name}: {text}")

    return "\n".join(lines)


def build_daily_summary(
    *,
    config: AppConfig,
    latest: LatestReadings,
    alerts: AlertManager,
    safety: SafetyGuard,
    dosing: DosingController,
    outputs: Mapping[str, Switch],
    calibration: CalibrationStore | None,
    now: datetime,
    timezone: tzinfo,
    started_at: datetime,
    daily: DailyStats | None = None,
) -> str:
    local = now.astimezone(timezone)
    lines = [
        build_status_text(
            config=config,
            latest=latest,
            alerts=alerts,
            safety=safety,
            dosing=dosing,
            outputs=outputs,
            calibration=calibration,
            now=now,
            timezone=timezone,
            started_at=started_at,
            heading=f"📊 RESUMEN DIARIO — {local:%Y-%m-%d}",
        ),
        "",
    ]
    if daily is None:
        lines.append("Últimas 24 h: historial no disponible (InfluxDB).")
        return "\n".join(lines)

    lines.append("Últimas 24 h:")
    snapshot = latest.snapshot()
    if not daily.variables:
        lines.append("  sin lecturas numéricas")
    for name in config.measurement_names():
        stats = daily.variables.get(name)
        if stats is None:
            continue
        unit = snapshot[name].unit if name in snapshot else ""
        lines.append(_format_stats(stats, unit))

    if daily.doses:
        lines.append("Dosis aplicadas (24 h):")
        for dose in daily.doses:
            kind = "simulada" if dose.dry_run else "real"
            lines.append(
                f"  - {dose.pump_id}: {dose.count} dosis, {dose.ml:g} ml ({kind})"
            )
    else:
        lines.append("Dosis aplicadas (24 h): ninguna")
    return "\n".join(lines)


def _dosing_label(dosing: DosingController) -> str:
    if not dosing.dosing.enabled:
        return "desactivada"
    if dosing.dosing.dry_run:
        return "activada en dry_run (no mueve bombas)"
    return "activada"


def _format_measurement(measurement: Measurement | None) -> str:
    if measurement is None:
        return "sin lectura"
    if isinstance(measurement.value, bool):
        value = "sí" if measurement.value else "no"
    else:
        number = float(measurement.value)
        if not math.isfinite(number):
            return "sin lectura"
        value = _format_number(number, measurement.unit)
    suffix = f" {measurement.unit}" if measurement.unit else ""
    quality = "" if measurement.quality == "ok" else f" ({measurement.quality})"
    return f"{value}{suffix}{quality}"


def _format_number(number: float, unit: str) -> str:
    if unit == "pH":
        return f"{number:.2f}"
    if unit == "µS/cm":
        return f"{number:.0f}"
    if unit == "L/min":
        return f"{number:.2f}"
    if unit in {"°C", "%"}:
        return f"{number:.1f}"
    return f"{number:g}"


def _format_stats(stats: VariableStats, unit: str) -> str:
    suffix = f" {unit}" if unit else ""
    return (
        f"  {stats.name}: mín {_format_number(stats.minimum, unit)} · "
        f"prom {_format_number(stats.mean, unit)} · "
        f"máx {_format_number(stats.maximum, unit)}{suffix} "
        f"(n={stats.count})"
    )


def _format_uptime(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    days, remainder = divmod(max(total, 0), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        return f"{days} d {hours} h"
    if hours:
        return f"{hours} h {minutes} min"
    return f"{minutes} min"


def _local_time(value: datetime, timezone: tzinfo) -> str:
    return f"{value.astimezone(timezone):%Y-%m-%d %H:%M %Z}"
