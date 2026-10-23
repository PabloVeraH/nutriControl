from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from conftest import NOW

from hidro.config import DosingLimits
from hidro.control.safety import SafetyGuard


def make_guard(tmp_path: Path, **limits: object) -> SafetyGuard:
    values = {"max_doses_per_hour": 2, "max_ml_per_day": {"pump_ph_down": 10.0}}
    values.update(limits)
    return SafetyGuard(tmp_path / "safety.json", DosingLimits.model_validate(values))


def test_hourly_limit(tmp_path: Path) -> None:
    guard = make_guard(tmp_path)
    assert guard.block_reason("pump_ph_down", 1.0, NOW) is None
    guard.record("pump_ph_down", 1.0, NOW)
    guard.record("pump_ph_down", 1.0, NOW)
    assert guard.block_reason("pump_ph_down", 1.0, NOW) is not None
    # fuera de la ventana de una hora vuelve a permitir
    assert guard.block_reason("pump_ph_down", 1.0, NOW + timedelta(hours=2)) is None


def test_daily_ml_limit(tmp_path: Path) -> None:
    guard = make_guard(tmp_path, max_doses_per_hour=10)
    guard.record("pump_ph_down", 8.0, NOW)
    assert guard.block_reason("pump_ph_down", 1.0, NOW) is None
    assert guard.block_reason("pump_ph_down", 3.0, NOW) is not None


def test_manual_block_and_reset(tmp_path: Path) -> None:
    guard = make_guard(tmp_path)
    guard.block("pump_ph_down", "prueba")
    assert guard.is_blocked("pump_ph_down")
    assert guard.block_reason("pump_ph_down", 1.0, NOW) == "prueba"

    reloaded = make_guard(tmp_path)
    assert reloaded.is_blocked("pump_ph_down")

    reloaded.reset("pump_ph_down")
    assert not reloaded.is_blocked("pump_ph_down")
    assert reloaded.block_reason("pump_ph_down", 1.0, NOW) is None


def test_reset_one_pump_keeps_the_others(tmp_path: Path) -> None:
    guard = make_guard(tmp_path, max_doses_per_hour=1, max_ml_per_day={})
    guard.record("pump_ph_down", 1.0, NOW)
    guard.record("pump_nutrient_a", 1.0, NOW)
    guard.block("pump_ph_down", "ph down")
    guard.block("pump_nutrient_a", "nutrient")

    guard.reset("pump_ph_down")

    assert not guard.is_blocked("pump_ph_down")
    assert guard.block_reason("pump_ph_down", 1.0, NOW) is None
    assert guard.is_blocked("pump_nutrient_a")
    assert guard.block_reason("pump_nutrient_a", 1.0, NOW) == "nutrient"


def test_reset_all_and_prune(tmp_path: Path) -> None:
    guard = make_guard(tmp_path)
    guard.record("pump_ph_down", 1.0, NOW - timedelta(days=3))
    guard.record("pump_ph_down", 1.0, NOW)
    guard.block("pump_ph_down", "x")
    guard.reset()
    assert guard.blocked == {}
    assert guard.block_reason("pump_ph_down", 1.0, NOW) is None


def test_corrupt_state_file(tmp_path: Path) -> None:
    path = tmp_path / "safety.json"
    path.write_text("{no es json", encoding="utf-8")
    guard = SafetyGuard(path, DosingLimits())
    assert guard.blocked == {}


def test_reset_from_another_process_is_seen_by_the_running_guard(tmp_path: Path) -> None:
    core = make_guard(tmp_path)
    core.block("pump_ph_down", "límite")
    make_guard(tmp_path).reset("pump_ph_down")  # `hidro safety reset` en otro proceso

    assert not core.is_blocked("pump_ph_down")
    core.record("pump_ph_up", 1.0, NOW)  # guardar no debe resucitar el bloqueo
    assert not make_guard(tmp_path).is_blocked("pump_ph_down")


def test_corrupt_state_file_does_not_unblock(tmp_path: Path) -> None:
    guard = make_guard(tmp_path)
    guard.block("pump_ph_down", "límite")
    (tmp_path / "safety.json").write_text("{roto", encoding="utf-8")
    assert guard.is_blocked("pump_ph_down")


def test_dry_run_and_real_doses_are_counted_apart(tmp_path: Path) -> None:
    guard = make_guard(tmp_path, max_doses_per_hour=1)
    guard.record("pump_ph_down", 1.0, NOW, dry_run=True)
    assert guard.block_reason("pump_ph_down", 1.0, NOW) is None
    assert guard.block_reason("pump_ph_down", 1.0, NOW, dry_run=True) is not None
