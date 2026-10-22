from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from conftest import NOW, FakePump, make_config, measurement

from hidro.control.dosing import (
    NUTRIENT_A,
    NUTRIENT_B,
    PH_DOWN,
    PH_UP,
    DoseAction,
    DosingController,
    DosingFlags,
    execute_action,
)
from hidro.control.safety import SafetyGuard
from hidro.drivers.base import LatestReadings, PumpNotCalibrated


def populated(
    *,
    ph: float = 5.9,
    ec: float = 1150.0,
    at=NOW,
    ph_quality: str = "ok",
    ec_quality: str = "ok",
) -> LatestReadings:
    latest = LatestReadings()
    latest.update(
        [
            measurement("ph", ph, "pH", at=at, quality=ph_quality),  # type: ignore[arg-type]
            measurement("ec", ec, "µS/cm", at=at, quality=ec_quality),  # type: ignore[arg-type]
        ]
    )
    return latest


def build(
    tmp_path: Path,
    *,
    outputs: dict | None = None,
    latest: LatestReadings | None = None,
    limits: dict | None = None,
    **dosing_overrides,
) -> tuple[DosingController, SafetyGuard]:
    dosing = {
        "enabled": True,
        "dry_run": True,
        "check_interval_s": 300,
        "mix_wait_s": 600,
        "ph_down_ml": 1.0,
        "ph_up_ml": 1.0,
        "nutrient_ml": 3.0,
        "limits": limits or {"max_doses_per_hour": 3},
    }
    dosing.update(dosing_overrides)
    config = make_config(dosing=dosing)
    safety = SafetyGuard(tmp_path / "safety.json", config.dosing.limits)
    if outputs is None:
        outputs = {"pump_ph_down": FakePump(PH_DOWN)}
    controller = DosingController(config, outputs, latest or populated(), safety)
    return controller, safety


def test_normal_readings_do_nothing(tmp_path: Path) -> None:
    controller, _ = build(tmp_path)
    decision = controller.plan(NOW, DosingFlags())
    assert decision.actions == []
    assert decision.blocked == []
    assert decision.notices == []


def test_disabled_dosing_is_blocked(tmp_path: Path) -> None:
    controller, _ = build(tmp_path, enabled=False)
    decision = controller.plan(NOW, DosingFlags())
    assert decision.blocked == ["dosificación deshabilitada"]
    assert decision.actions == []


def test_missing_sensor_readings_are_reported_and_recovered(tmp_path: Path) -> None:
    controller, _ = build(tmp_path, latest=LatestReadings())
    decision = controller.plan(NOW, DosingFlags())
    assert "sin lecturas recientes de pH/EC" in decision.blocked
    assert [notice.key for notice in decision.notices] == ["dosing-sensors"]

    controller.latest = populated()
    decision = controller.plan(NOW, DosingFlags())
    assert decision.blocked == []
    assert decision.notices[0].key == "dosing-sensors"
    assert decision.notices[0].active is False


def test_stale_or_invalid_readings_are_rejected(tmp_path: Path) -> None:
    stale = populated(at=NOW - timedelta(seconds=100))
    controller, _ = build(tmp_path, latest=stale)
    stale_notices = controller.plan(NOW, DosingFlags()).notices
    assert [(notice.key, notice.active) for notice in stale_notices] == [
        ("dosing-sensors", True)
    ]

    invalid = populated(ph_quality="invalid")
    controller, _ = build(tmp_path, latest=invalid)
    invalid_notices = controller.plan(NOW, DosingFlags()).notices
    assert [(notice.key, notice.active) for notice in invalid_notices] == [
        ("dosing-sensors", True)
    ]


def test_flow_or_tank_alarm_blocks_dosing(tmp_path: Path) -> None:
    controller, _ = build(tmp_path)
    flow = controller.plan(NOW, DosingFlags(flow_alarm=True))
    assert "flujo en alarma" in flow.blocked
    tank = controller.plan(NOW, DosingFlags(tank_low=True))
    assert "estanque bajo" in tank.blocked


def test_mix_wait_blocks_next_dose(tmp_path: Path) -> None:
    controller, _ = build(tmp_path)
    controller.last_dose_at = NOW - timedelta(seconds=100)
    decision = controller.plan(NOW, DosingFlags())
    assert decision.blocked
    assert "esperando mezcla" in decision.blocked[0]

    controller.last_dose_at = NOW - timedelta(seconds=700)
    assert controller.plan(NOW, DosingFlags()).blocked == []


def test_ph_danger_schedules_correction(tmp_path: Path) -> None:
    low_outputs = {PH_DOWN: FakePump(PH_DOWN), PH_UP: FakePump(PH_UP)}
    controller, _ = build(tmp_path, outputs=low_outputs, latest=populated(ph=4.8))
    decision = controller.plan(NOW, DosingFlags())
    assert [action.pump_id for action in decision.actions] == [PH_DOWN]
    assert decision.actions[0].seconds == pytest.approx(6.0)
    assert "pH 4.80" in decision.actions[0].reason

    controller.latest = populated(ph=7.2)
    decision = controller.plan(NOW, DosingFlags())
    assert [action.pump_id for action in decision.actions] == [PH_UP]


def test_ph_outside_range_schedules_correction(tmp_path: Path) -> None:
    outputs = {PH_DOWN: FakePump(PH_DOWN), PH_UP: FakePump(PH_UP)}
    controller, _ = build(tmp_path, outputs=outputs, latest=populated(ph=5.4))
    assert [a.pump_id for a in controller.plan(NOW, DosingFlags()).actions] == [PH_DOWN]

    controller.latest = populated(ph=6.3)
    assert [a.pump_id for a in controller.plan(NOW, DosingFlags()).actions] == [PH_UP]

    controller.latest = populated(ph=6.1)
    assert controller.plan(NOW, DosingFlags()).actions == []


def test_ec_danger_schedules_both_nutrients(tmp_path: Path) -> None:
    outputs = {
        NUTRIENT_A: FakePump(NUTRIENT_A),
        NUTRIENT_B: FakePump(NUTRIENT_B),
    }
    controller, _ = build(tmp_path, outputs=outputs, latest=populated(ec=800))
    decision = controller.plan(NOW, DosingFlags())
    assert [action.pump_id for action in decision.actions] == [NUTRIENT_A, NUTRIENT_B]
    assert all(action.ml == 3.0 for action in decision.actions)


def test_missing_nutrient_pump_requires_manual_dose(tmp_path: Path) -> None:
    controller, _ = build(
        tmp_path,
        outputs={NUTRIENT_A: FakePump(NUTRIENT_A)},
        latest=populated(ec=800),
    )
    decision = controller.plan(NOW, DosingFlags())
    assert decision.actions == []
    assert [notice.key for notice in decision.notices] == ["dosing-nutrients"]
    assert "manualmente" in decision.notices[0].message


def test_ec_too_high_requires_dilution(tmp_path: Path) -> None:
    controller, _ = build(tmp_path, latest=populated(ec=1400))
    decision = controller.plan(NOW, DosingFlags())
    assert decision.actions == []
    assert decision.blocked == ["EC sobre el máximo: diluir con agua"]


def test_missing_pump_is_reported_then_cleared(tmp_path: Path) -> None:
    controller, _ = build(tmp_path, outputs={}, latest=populated(ph=5.4))
    decision = controller.plan(NOW, DosingFlags())
    assert decision.actions == []
    assert [notice.key for notice in decision.notices] == ["dosing-missing-pump_ph_down"]
    assert "falta la bomba" in decision.notices[0].message

    controller.outputs[PH_DOWN] = FakePump(PH_DOWN)
    decision = controller.plan(NOW, DosingFlags())
    inactive = [notice for notice in decision.notices if notice.key == "dosing-missing-pump_ph_down"]
    assert inactive and inactive[0].active is False


def test_uncalibrated_pump_is_reported(tmp_path: Path) -> None:
    controller, _ = build(
        tmp_path,
        outputs={PH_DOWN: FakePump(PH_DOWN, ml_per_min=None)},
        latest=populated(ph=5.4),
    )
    decision = controller.plan(NOW, DosingFlags())
    assert decision.actions == []
    assert "no está calibrada" in decision.notices[0].message


def test_too_long_dose_is_reported(tmp_path: Path) -> None:
    controller, _ = build(
        tmp_path,
        outputs={PH_DOWN: FakePump(PH_DOWN, ml_per_min=0.1, max_on_s=30)},
        latest=populated(ph=5.4),
    )
    decision = controller.plan(NOW, DosingFlags())
    assert decision.actions == []
    assert "máximo 30 s" in decision.notices[0].message


def test_safety_block_is_reported(tmp_path: Path) -> None:
    controller, safety = build(tmp_path, latest=populated(ph=5.4))
    safety.block(PH_DOWN, "falla previa")
    decision = controller.plan(NOW, DosingFlags())
    assert decision.actions == []
    assert "bloqueada por seguridad" in decision.notices[0].message
    assert "safety-reset" in decision.notices[0].message


def test_hourly_limit_blocks_dose(tmp_path: Path) -> None:
    controller, safety = build(
        tmp_path,
        latest=populated(ph=5.4),
        limits={"max_doses_per_hour": 1},
    )
    safety.record(PH_DOWN, 1.0, NOW - timedelta(minutes=5))
    decision = controller.plan(NOW, DosingFlags())
    assert decision.actions == []
    assert "1 dosis por hora" in decision.notices[0].message


def test_daily_limit_blocks_dose(tmp_path: Path) -> None:
    controller, safety = build(
        tmp_path,
        latest=populated(ph=5.4),
        limits={"max_doses_per_hour": 3, "max_ml_per_day": {PH_DOWN: 1.5}},
    )
    safety.record(PH_DOWN, 1.0, NOW - timedelta(hours=2))
    decision = controller.plan(NOW, DosingFlags())
    assert decision.actions == []
    assert "límite diario" in decision.notices[0].message


class ExplodingPump(FakePump):
    def dispense_ml(self, ml: float) -> float:
        raise PumpNotCalibrated("sin calibración")


def test_execute_action_dry_run_does_not_move_pump(tmp_path: Path) -> None:
    pump = FakePump(PH_DOWN)
    safety = SafetyGuard(tmp_path / "safety.json", make_config().dosing.limits)
    action = DoseAction(PH_DOWN, 1.0, "pH bajo", 6.0)
    text = execute_action(action, pump, dry_run=True, safety=safety, now=NOW)
    assert "SIMULACIÓN" in text
    assert pump.dispensed == []
    assert safety.blocked == {}
    assert (tmp_path / "safety.json").exists()


def test_execute_action_real_dispenses_and_records(tmp_path: Path) -> None:
    pump = FakePump(PH_DOWN)
    safety = SafetyGuard(tmp_path / "safety.json", make_config().dosing.limits)
    action = DoseAction(PH_DOWN, 1.0, "pH bajo", 6.0)
    text = execute_action(action, pump, dry_run=False, safety=safety, now=NOW)
    assert pump.dispensed == [1.0]
    assert "Dosificado" in text
    assert "pH bajo" in text


def test_execute_action_wraps_pump_errors(tmp_path: Path) -> None:
    safety = SafetyGuard(tmp_path / "safety.json", make_config().dosing.limits)
    action = DoseAction(PH_DOWN, 1.0, "pH bajo", 6.0)
    with pytest.raises(RuntimeError, match="sin calibración"):
        execute_action(action, ExplodingPump(PH_DOWN), dry_run=False, safety=safety, now=NOW)
