from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from conftest import NOW, FakePump, base_config_dict, make_config, measurement

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
    flow: float | None = 1.4,
) -> LatestReadings:
    latest = LatestReadings()
    latest.update(
        [
            measurement("ph", ph, "pH", at=at, quality=ph_quality),  # type: ignore[arg-type]
            measurement("ec", ec, "µS/cm", at=at, quality=ec_quality),  # type: ignore[arg-type]
        ]
    )
    if flow is not None:
        latest.update([measurement("flow", flow, "L/min", at=at)])
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


def test_unknown_flow_blocks_dosing(tmp_path: Path) -> None:
    controller, _ = build(tmp_path, latest=populated(ph=4.8, flow=None))
    decision = controller.plan(NOW, DosingFlags())
    assert "sin lectura reciente de flujo (flow)" in decision.blocked
    assert decision.actions == []

    controller.latest = populated(ph=4.8)
    controller.latest.update([measurement("flow", 1.4, at=NOW - timedelta(seconds=100))])
    assert "sin lectura reciente de flujo (flow)" in controller.plan(NOW, DosingFlags()).blocked


def test_unknown_tank_level_blocks_dosing(tmp_path: Path) -> None:
    controller, _ = build(tmp_path, latest=populated(ph=4.8))
    sensors = [*controller.config.model_dump()["sensors"]]
    sensors.insert(1, {"id": "tank", "type": "float_switch", "gpio": 17})
    controller.config = make_config(sensors=sensors, dosing=controller.config.model_dump()["dosing"])
    assert "sin lectura reciente del flotador (tank)" in controller.plan(NOW, DosingFlags()).blocked

    controller.latest.update([measurement("tank_low", False, "bool")])
    assert controller.plan(NOW, DosingFlags()).blocked == []


def test_suspect_readings_block_dosing(tmp_path: Path) -> None:
    for latest in (populated(ph=4.8, ec_quality="suspect"), populated(ph=4.8, ph_quality="suspect")):
        controller, _ = build(tmp_path, latest=latest)
        decision = controller.plan(NOW, DosingFlags())
        assert "sin lecturas recientes de pH/EC" in decision.blocked
        assert decision.actions == []


def test_mix_wait_blocks_next_dose(tmp_path: Path) -> None:
    controller, _ = build(tmp_path)
    controller.last_dose_at = NOW - timedelta(seconds=100)
    decision = controller.plan(NOW, DosingFlags())
    assert decision.blocked
    assert "esperando mezcla" in decision.blocked[0]

    controller.last_dose_at = NOW - timedelta(seconds=700)
    assert controller.plan(NOW, DosingFlags()).blocked == []


def test_ph_danger_schedules_correction(tmp_path: Path) -> None:
    outputs = {PH_DOWN: FakePump(PH_DOWN), PH_UP: FakePump(PH_UP)}
    controller, _ = build(tmp_path, outputs=outputs, latest=populated(ph=4.8))
    decision = controller.plan(NOW, DosingFlags())
    # pH muy ácido: se sube con base, nunca con más ácido.
    assert [action.pump_id for action in decision.actions] == [PH_UP]
    assert decision.actions[0].seconds == pytest.approx(6.0)
    assert "pH 4.80: pH bajo" in decision.actions[0].reason

    controller.latest = populated(ph=7.2)
    decision = controller.plan(NOW, DosingFlags())
    assert [action.pump_id for action in decision.actions] == [PH_DOWN]
    assert "pH alto" in decision.actions[0].reason


def test_ph_outside_range_schedules_correction(tmp_path: Path) -> None:
    outputs = {PH_DOWN: FakePump(PH_DOWN), PH_UP: FakePump(PH_UP)}
    controller, _ = build(tmp_path, outputs=outputs, latest=populated(ph=5.4))
    assert [a.pump_id for a in controller.plan(NOW, DosingFlags()).actions] == [PH_UP]

    controller.latest = populated(ph=6.3)
    assert [a.pump_id for a in controller.plan(NOW, DosingFlags()).actions] == [PH_DOWN]

    controller.latest = populated(ph=6.1)
    assert controller.plan(NOW, DosingFlags()).actions == []


def test_ph_doses_use_their_own_volume(tmp_path: Path) -> None:
    outputs = {PH_DOWN: FakePump(PH_DOWN), PH_UP: FakePump(PH_UP)}
    controller, _ = build(
        tmp_path, outputs=outputs, latest=populated(ph=5.4), ph_up_ml=2.0, ph_down_ml=0.5
    )
    assert [a.ml for a in controller.plan(NOW, DosingFlags()).actions] == [2.0]
    controller.latest = populated(ph=6.5)
    assert [a.ml for a in controller.plan(NOW, DosingFlags()).actions] == [0.5]


def test_single_ph_down_pump_never_acidifies_low_ph(tmp_path: Path) -> None:
    # Configuración inicial del plan: solo pH down. Con pH bajo se avisa.
    controller, _ = build(tmp_path, latest=populated(ph=5.4))
    decision = controller.plan(NOW, DosingFlags())
    assert decision.actions == []
    assert [notice.key for notice in decision.notices] == ["dosing-missing-pump_ph_up"]


def test_danger_flag_without_out_of_range_reading_does_nothing(tmp_path: Path) -> None:
    outputs = {PH_DOWN: FakePump(PH_DOWN), PH_UP: FakePump(PH_UP)}
    controller, _ = build(tmp_path, outputs=outputs)
    assert controller.plan(NOW, DosingFlags(ph_danger=True)).actions == []


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
    controller, _ = build(tmp_path, outputs={}, latest=populated(ph=6.5))
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
        latest=populated(ph=6.5),
    )
    decision = controller.plan(NOW, DosingFlags())
    assert decision.actions == []
    assert "no está calibrada" in decision.notices[0].message


def test_too_long_dose_is_reported(tmp_path: Path) -> None:
    controller, _ = build(
        tmp_path,
        outputs={PH_DOWN: FakePump(PH_DOWN, ml_per_min=0.1, max_on_s=30)},
        latest=populated(ph=6.5),
    )
    decision = controller.plan(NOW, DosingFlags())
    assert decision.actions == []
    assert "máximo 30 s" in decision.notices[0].message


def test_safety_block_is_reported(tmp_path: Path) -> None:
    controller, safety = build(tmp_path, latest=populated(ph=6.5))
    safety.block(PH_DOWN, "falla previa")
    decision = controller.plan(NOW, DosingFlags())
    assert decision.actions == []
    assert "bloqueada por seguridad" in decision.notices[0].message
    assert "safety reset" in decision.notices[0].message


def test_custom_sensor_ids_and_pump_roles(tmp_path: Path) -> None:
    raw = base_config_dict()
    raw["sensors"][2]["id"] = "ph_probe"
    raw["sensors"][3]["id"] = "ec_probe"
    raw["outputs"][1]["id"] = "dosificador"
    raw["outputs"][1]["role"] = "ph_down"
    raw["dosing"]["limits"]["max_ml_per_day"] = {"dosificador": 20}
    config = make_config(
        sensors=raw["sensors"], outputs=raw["outputs"], dosing=raw["dosing"]
    )
    latest = LatestReadings()
    latest.update(
        [
            measurement("ph_probe", 6.5, "pH"),
            measurement("ec_probe", 1150.0, "µS/cm"),
            measurement("flow", 1.4, "L/min"),
        ]
    )
    safety = SafetyGuard(tmp_path / "safety.json", config.dosing.limits)
    controller = DosingController(
        config, {"dosificador": FakePump("dosificador")}, latest, safety
    )

    decision = controller.plan(NOW, DosingFlags())

    assert [action.pump_id for action in decision.actions] == ["dosificador"]


def test_hourly_limit_blocks_dose_until_manual_reset(tmp_path: Path) -> None:
    controller, safety = build(
        tmp_path,
        dry_run=False,
        latest=populated(ph=6.5),
        limits={"max_doses_per_hour": 1},
    )
    safety.record(PH_DOWN, 1.0, NOW - timedelta(minutes=5))
    decision = controller.plan(NOW, DosingFlags())
    assert decision.actions == []
    assert "1 dosis por hora" in decision.notices[0].message
    assert safety.is_blocked(PH_DOWN)
    assert safety.block_reason(PH_DOWN, 1.0, NOW + timedelta(hours=2)) is not None


def test_daily_limit_blocks_dose_until_manual_reset(tmp_path: Path) -> None:
    controller, safety = build(
        tmp_path,
        dry_run=False,
        latest=populated(ph=6.5),
        limits={"max_doses_per_hour": 3, "max_ml_per_day": {PH_DOWN: 1.5}},
    )
    safety.record(PH_DOWN, 1.0, NOW - timedelta(hours=2))
    decision = controller.plan(NOW, DosingFlags())
    assert decision.actions == []
    assert "límite diario" in decision.notices[0].message
    assert safety.is_blocked(PH_DOWN)


def test_dry_run_limit_is_simulated_without_real_block(tmp_path: Path) -> None:
    controller, safety = build(
        tmp_path, latest=populated(ph=6.5), limits={"max_doses_per_hour": 1}
    )
    safety.record(PH_DOWN, 1.0, NOW - timedelta(minutes=5), dry_run=True)
    decision = controller.plan(NOW, DosingFlags())
    assert decision.actions == []
    assert "SIMULACIÓN" in decision.notices[0].message
    assert "se bloquearía" in decision.notices[0].message
    assert not safety.is_blocked(PH_DOWN)


def test_dry_run_doses_do_not_count_for_real_limits(tmp_path: Path) -> None:
    controller, safety = build(
        tmp_path,
        dry_run=False,
        latest=populated(ph=6.5),
        limits={"max_doses_per_hour": 1},
    )
    safety.record(PH_DOWN, 1.0, NOW - timedelta(minutes=5), dry_run=True)
    assert [a.pump_id for a in controller.plan(NOW, DosingFlags()).actions] == [PH_DOWN]


def test_real_block_is_reported_in_dry_run(tmp_path: Path) -> None:
    controller, safety = build(tmp_path, latest=populated(ph=6.5))
    safety.block(PH_DOWN, "fallo al dosificar")
    message = controller.plan(NOW, DosingFlags()).notices[0].message
    assert "bloqueada por seguridad" in message
    assert "SIMULACIÓN" not in message


def test_nutrients_are_skipped_together_when_one_is_blocked(tmp_path: Path) -> None:
    outputs = {NUTRIENT_A: FakePump(NUTRIENT_A), NUTRIENT_B: FakePump(NUTRIENT_B)}
    controller, safety = build(tmp_path, outputs=outputs, latest=populated(ec=800))
    safety.block(NUTRIENT_B, "falla previa")
    decision = controller.plan(NOW, DosingFlags())
    # Solo A desequilibraría la solución: no se dosifica ninguna.
    assert decision.actions == []
    assert [notice.key for notice in decision.notices] == [f"safety-{NUTRIENT_B}"]


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
