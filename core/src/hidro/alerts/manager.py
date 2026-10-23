"""Gestión de alertas: deduplicación, repetición y recuperación."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from hidro.alerts.telegram import TelegramNotifier
from hidro.logging_setup import get_logger

logger = get_logger("alerts.manager")


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    INFO = "info"


_ORDER = {
    Severity.INFO: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}

_PREFIX = {
    Severity.CRITICAL: "🔴 CRÍTICO",
    Severity.HIGH: "🟠 ALERTA",
    Severity.MEDIUM: "🟡 AVISO",
    Severity.INFO: "🔵 INFO",
}


@dataclass
class AlertState:
    key: str
    severity: Severity
    message: str
    notified: bool = False
    last_sent: datetime | None = None


class AlertManager:
    """Una sola notificación por condición, salvo las críticas que se repiten."""

    def __init__(
        self,
        notifier: TelegramNotifier,
        *,
        repeat_critical: timedelta = timedelta(minutes=15),
        silence_path: Path | None = None,
    ) -> None:
        self.notifier = notifier
        self.repeat_critical = repeat_critical
        self._states: dict[str, AlertState] = {}
        self._silence_path = Path(silence_path) if silence_path is not None else None
        self._silenced_until = self._load_silence()

    # ------------------------------------------------------------------ API

    def report(
        self,
        key: str,
        *,
        active: bool,
        severity: Severity,
        message: str,
        now: datetime,
        resolved_message: str | None = None,
    ) -> None:
        if active:
            self._raise(key, severity, message, now)
        else:
            self._clear(key, now, resolved_message)

    def notify(self, text: str) -> bool:
        return self.notifier.send(text)

    def silence_for(self, minutes: float, now: datetime) -> None:
        self._silenced_until = now + timedelta(minutes=minutes)
        self._save_silence()
        logger.info("alertas silenciadas por %.0f minutos", minutes)

    def is_silenced(self, now: datetime) -> bool:
        if self._silenced_until is None:
            return False
        if now < self._silenced_until:
            return True
        self._silenced_until = None
        self._save_silence()
        return False

    @property
    def silenced_until(self) -> datetime | None:
        return self._silenced_until

    @property
    def active_keys(self) -> set[str]:
        return set(self._states)

    @property
    def active_alerts(self) -> dict[str, str]:
        return {key: state.message for key, state in self._states.items()}

    # ------------------------------------------------------------------ interno

    def _raise(self, key: str, severity: Severity, message: str, now: datetime) -> None:
        state = self._states.get(key)
        if state is None:
            state = AlertState(key=key, severity=severity, message=message)
            self._states[key] = state
            self._maybe_send(state, now)
            return

        upgraded = _ORDER[severity] > _ORDER[state.severity]
        state.message = message
        state.severity = severity
        if upgraded:
            state.notified = False
            self._maybe_send(state, now)
            return
        if not state.notified:
            # El envío anterior falló (red caída) o estaba silenciado: reintentar
            # en cada ciclo; si no, la alarma de flujo podría no llegar nunca.
            self._maybe_send(state, now)
            return
        if severity is Severity.CRITICAL:
            last = state.last_sent or now
            if now - last >= self.repeat_critical:
                state.notified = False
                self._maybe_send(state, now)

    def _clear(self, key: str, now: datetime, resolved_message: str | None) -> None:
        state = self._states.pop(key, None)
        if state is None or not state.notified:
            return
        if state.severity is not Severity.CRITICAL and self.is_silenced(now):
            logger.info("condición normalizada durante silencio: %s", state.message)
            return
        text = resolved_message or state.message
        self.notifier.send(f"✅ NORMALIZADO\n{text}")

    def _maybe_send(self, state: AlertState, now: datetime) -> None:
        if self.is_silenced(now) and state.severity is not Severity.CRITICAL:
            logger.info("alerta silenciada: %s", state.message)
            return
        prefix = _PREFIX[state.severity]
        sent = self.notifier.send(f"{prefix}\n{state.message}")
        state.notified = sent or not self.notifier.enabled
        state.last_sent = now if state.notified else None

    def _load_silence(self) -> datetime | None:
        if self._silence_path is None or not self._silence_path.exists():
            return None
        try:
            raw = json.loads(self._silence_path.read_text(encoding="utf-8"))
            value = raw.get("silenced_until")
            if not value:
                return None
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            logger.warning("no se pudo leer el silencio de alertas: %s", exc)
            return None

    def _save_silence(self) -> None:
        if self._silence_path is None:
            return
        try:
            self._silence_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._silence_path.with_suffix(".tmp")
            payload = {
                "silenced_until": (
                    self._silenced_until.isoformat()
                    if self._silenced_until is not None
                    else None
                )
            }
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp, self._silence_path)
        except OSError as exc:
            # No poder persistir el silencio no debe impedir silenciar en vivo.
            logger.warning("no se pudo guardar el silencio de alertas: %s", exc)


def max_severity(current: Severity | None, new: Severity) -> Severity:
    if current is None:
        return new
    return new if _ORDER[new] > _ORDER[current] else current
