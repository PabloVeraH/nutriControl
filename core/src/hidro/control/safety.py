"""Límites de seguridad de dosificación, persistentes entre reinicios."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from hidro.config import DosingLimits
from hidro.logging_setup import get_logger

logger = get_logger("control.safety")


@dataclass(frozen=True)
class DoseRecord:
    pump_id: str
    ml: float
    at: datetime
    dry_run: bool = False


class SafetyGuard:
    """Corta la dosificación al alcanzar los límites y exige reinicio manual."""

    def __init__(self, path: Path, limits: DosingLimits) -> None:
        self.path = Path(path)
        self.limits = limits
        self._records: list[DoseRecord] = []
        self._blocked: dict[str, str] = {}
        self._load()

    # ------------------------------------------------------------------ consulta

    def block_reason(self, pump_id: str, ml: float, now: datetime) -> str | None:
        if pump_id in self._blocked:
            return self._blocked[pump_id]

        hourly = sum(
            1
            for record in self._records
            if record.pump_id == pump_id and now - record.at <= timedelta(hours=1)
        )
        if hourly >= self.limits.max_doses_per_hour:
            return (
                f"{self.limits.max_doses_per_hour} dosis por hora alcanzadas en {pump_id}"
            )

        daily_limit = self.limits.max_ml_per_day.get(pump_id)
        if daily_limit is not None:
            daily_ml = sum(
                record.ml
                for record in self._records
                if record.pump_id == pump_id and now - record.at <= timedelta(days=1)
            )
            if daily_ml + ml > daily_limit:
                return (
                    f"límite diario de {daily_limit:g} ml alcanzado en {pump_id} "
                    f"({daily_ml:g} ml en 24 h)"
                )
        return None

    def is_blocked(self, pump_id: str) -> bool:
        return pump_id in self._blocked

    @property
    def blocked(self) -> dict[str, str]:
        return dict(self._blocked)

    # ------------------------------------------------------------------ cambios

    def record(self, pump_id: str, ml: float, now: datetime, *, dry_run: bool = False) -> None:
        self._records.append(DoseRecord(pump_id=pump_id, ml=ml, at=now, dry_run=dry_run))
        self._prune(now)
        self._save()

    def block(self, pump_id: str, reason: str) -> None:
        if pump_id not in self._blocked:
            logger.error("bomba %s bloqueada: %s", pump_id, reason)
        self._blocked[pump_id] = reason
        self._save()

    def reset(self, pump_id: str | None = None) -> None:
        if pump_id is None:
            self._blocked.clear()
        else:
            self._blocked.pop(pump_id, None)
        self._records.clear()
        self._save()
        logger.info("seguridad reiniciada para %s", pump_id or "todas las bombas")

    # ------------------------------------------------------------------ persistencia

    def _prune(self, now: datetime) -> None:
        cutoff = now - timedelta(days=2)
        self._records = [record for record in self._records if record.at >= cutoff]

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._blocked = {str(k): str(v) for k, v in raw.get("blocked", {}).items()}
            self._records = [
                DoseRecord(
                    pump_id=item["pump"],
                    ml=float(item["ml"]),
                    at=datetime.fromisoformat(item["at"]),
                    dry_run=bool(item.get("dry_run", False)),
                )
                for item in raw.get("doses", [])
            ]
        except (OSError, ValueError, KeyError) as exc:
            logger.error("no se pudo leer el estado de seguridad %s: %s", self.path, exc)
            self._blocked = {}
            self._records = []

    def _save(self) -> None:
        payload = {
            "blocked": self._blocked,
            "doses": [
                {
                    "pump": record.pump_id,
                    "ml": record.ml,
                    "at": record.at.isoformat(),
                    "dry_run": record.dry_run,
                }
                for record in self._records
            ],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
