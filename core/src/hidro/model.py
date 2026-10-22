"""Modelo de datos compartido por drivers, reglas y almacenamiento."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Quality = Literal["ok", "suspect", "invalid"]


@dataclass(frozen=True, slots=True)
class Measurement:
    """Una lectura con su unidad, instante y calidad."""

    name: str
    value: float | bool
    unit: str
    timestamp: datetime
    quality: Quality = "ok"
    source: str = ""

    @property
    def valid(self) -> bool:
        return self.quality != "invalid"

    @property
    def numeric(self) -> float:
        if isinstance(self.value, bool):
            return 1.0 if self.value else 0.0
        return self.value

    def is_stale(self, now: datetime, max_age_s: float) -> bool:
        return (now - self.timestamp).total_seconds() > max_age_s
