"""Latido del proceso para el healthcheck de Docker y Grafana."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path


def heartbeat_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / "heartbeat.json"


def write_heartbeat(data_dir: str | Path, now: datetime, cycle: int) -> None:
    path = heartbeat_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"at": now.isoformat(), "cycle": cycle}), encoding="utf-8"
    )
    os.replace(tmp, path)


def read_heartbeat(data_dir: str | Path) -> tuple[datetime, int] | None:
    path = heartbeat_path(data_dir)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return datetime.fromisoformat(raw["at"]), int(raw.get("cycle", 0))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def heartbeat_age_s(data_dir: str | Path, now: datetime | None = None) -> float | None:
    data = read_heartbeat(data_dir)
    if data is None:
        return None
    reference = now or datetime.now(UTC)
    return (reference - data[0]).total_seconds()
