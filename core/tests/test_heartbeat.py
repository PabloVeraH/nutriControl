from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hidro.heartbeat import (
    heartbeat_age_s,
    heartbeat_path,
    read_heartbeat,
    write_heartbeat,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_write_and_read_heartbeat(tmp_path: Path) -> None:
    assert heartbeat_path(tmp_path) == tmp_path / "heartbeat.json"
    write_heartbeat(tmp_path, NOW, 7)

    assert read_heartbeat(tmp_path) == (NOW, 7)
    payload = json.loads((tmp_path / "heartbeat.json").read_text(encoding="utf-8"))
    assert payload == {"at": NOW.isoformat(), "cycle": 7}
    assert not (tmp_path / "heartbeat.tmp").exists()


def test_missing_heartbeat(tmp_path: Path) -> None:
    assert read_heartbeat(tmp_path) is None
    assert heartbeat_age_s(tmp_path, NOW) is None


def test_corrupt_heartbeat_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "heartbeat.json").write_text("{no es json", encoding="utf-8")
    assert read_heartbeat(tmp_path) is None

    (tmp_path / "heartbeat.json").write_text("[1, 2]", encoding="utf-8")
    assert read_heartbeat(tmp_path) is None

    (tmp_path / "heartbeat.json").write_text('{"cycle": 3}', encoding="utf-8")
    assert read_heartbeat(tmp_path) is None


def test_cycle_defaults_to_zero(tmp_path: Path) -> None:
    (tmp_path / "heartbeat.json").write_text(
        json.dumps({"at": NOW.isoformat()}), encoding="utf-8"
    )
    assert read_heartbeat(tmp_path) == (NOW, 0)


def test_heartbeat_age(tmp_path: Path) -> None:
    write_heartbeat(tmp_path, NOW - timedelta(seconds=45), 1)
    assert heartbeat_age_s(tmp_path, NOW) == 45.0
