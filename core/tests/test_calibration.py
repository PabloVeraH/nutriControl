from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hidro.calibration import CalibrationStore, ECCalibration, PHCalibration


def make_ph(**overrides: float) -> PHCalibration:
    values = {
        "v1": 2.0,
        "ph1": 6.86,
        "v2": 2.4,
        "ph2": 4.0,
        "t_cal": 25.0,
    }
    values.update(overrides)
    return PHCalibration(
        **values,  # type: ignore[arg-type]
        calibrated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_slope_and_v7() -> None:
    calibration = make_ph()
    assert calibration.slope == pytest.approx((2.0 - 2.4) / (6.86 - 4.0))
    assert calibration.v7 == pytest.approx(2.0 + calibration.slope * (7 - 6.86))
    assert calibration.ph_from_voltage(2.0, 25.0) == pytest.approx(6.86, abs=1e-6)
    assert calibration.ph_from_voltage(2.4, 25.0) == pytest.approx(4.0, abs=1e-6)


def test_temperature_compensation_scales_slope() -> None:
    calibration = make_ph()
    hot = calibration.slope_at(35.0)
    cold = calibration.slope_at(15.0)
    assert abs(hot) > abs(cold)
    assert hot == pytest.approx(calibration.slope * (308.15 / 298.15))
    # sin compensación se usa la pendiente calibrada
    assert calibration.ph_from_voltage(2.0, None) == pytest.approx(6.86)


def test_slope_validation() -> None:
    assert make_ph().slope_is_reasonable()
    assert not make_ph(v1=1.0, v2=2.9).slope_is_reasonable()


def test_calibration_age() -> None:
    calibration = make_ph()
    assert not calibration.is_old(calibration.calibrated_at + timedelta(days=10))
    assert calibration.is_old(calibration.calibrated_at + timedelta(days=31))
    assert calibration.age_days(calibration.calibrated_at + timedelta(days=2)) == pytest.approx(2.0)


def test_store_roundtrip(tmp_path: Path) -> None:
    store = CalibrationStore(tmp_path / "calibration.yaml")
    assert store.ph() is None
    assert store.ec() is None
    assert store.flow() is None
    assert store.pump_ml_per_min("pump_ph_down") is None

    store.save_ph(
        v1=2.0,
        ph1=6.86,
        v2=2.4,
        ph2=4.0,
        t_cal=21.5,
        calibrated_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    ph = store.ph()
    assert ph is not None
    assert ph.t_cal == 21.5
    assert ph.calibrated_at == datetime(2026, 2, 1, tzinfo=UTC)

    store.save_ec(k=1.5, standard_us_cm=1413, voltage=1.2, t_c=24.0)
    ec = store.ec()
    assert ec is not None
    assert ec.k == 1.5
    assert ec.standard_us_cm == 1413

    store.save_flow(pulses_per_liter=5900)
    assert store.flow().pulses_per_liter == 5900  # type: ignore[union-attr]

    store.save_pump("pump_ph_down", 12.4)
    assert store.pump_ml_per_min("pump_ph_down") == 12.4


def test_store_rejects_bad_slope(tmp_path: Path) -> None:
    store = CalibrationStore(tmp_path / "calibration.yaml")
    with pytest.raises(ValueError):
        store.save_ph(v1=1.0, ph1=6.86, v2=1.0, ph2=4.0, t_cal=25.0)


def test_pending_point(tmp_path: Path) -> None:
    store = CalibrationStore(tmp_path / "calibration.yaml")
    store.set_ph_pending(6.86, 2.01, 22.0)
    pending = store.ph_pending()
    assert pending is not None
    assert pending.voltage == 2.01
    store.clear_ph_pending()
    assert store.ph_pending() is None


def test_corrupt_file_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "calibration.yaml"
    path.write_text("ph: [no, es, un, mapa]", encoding="utf-8")
    store = CalibrationStore(path)
    assert store.ph() is None


def test_store_rejects_negative_values(tmp_path: Path) -> None:
    store = CalibrationStore(tmp_path / "calibration.yaml")
    with pytest.raises(ValueError):
        store.save_ec(k=0, standard_us_cm=1413, voltage=1.0, t_c=25.0)
    with pytest.raises(ValueError):
        store.save_flow(pulses_per_liter=0)
    for value in (0, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="finito y positivo"):
            store.save_pump("pump", value)


def test_ec_k_bounds_and_reasonableness(tmp_path: Path) -> None:
    store = CalibrationStore(tmp_path / "calibration.yaml")
    for k in (0.01, 20.0, float("nan")):
        with pytest.raises(ValueError, match="factor k fuera de rango"):
            store.save_ec(k=k, standard_us_cm=1413, voltage=1.0, t_c=25.0)

    with pytest.raises(ValueError, match="patrón"):
        store.save_ec(k=1.0, standard_us_cm=0.0, voltage=1.0, t_c=25.0)

    now = datetime.now(UTC)
    assert ECCalibration(1.0, 1413, 1.0, 25.0, now).k_is_reasonable()
    assert not ECCalibration(0.05, 1413, 1.0, 25.0, now).k_is_reasonable()
    assert not ECCalibration(float("inf"), 1413, 1.0, 25.0, now).k_is_reasonable()


def test_yaml_syntax_error_does_not_crash_reads(tmp_path: Path) -> None:
    path = tmp_path / "calibration.yaml"
    path.write_text("flow: {pulses_per_liter: 5000", encoding="utf-8")
    store = CalibrationStore(path)
    assert store.flow() is None
    assert store.ph() is None


def test_save_refuses_to_overwrite_an_invalid_file(tmp_path: Path) -> None:
    path = tmp_path / "calibration.yaml"
    original = "ph: [no, es, un, mapa]\nec: {k: 1.1, calibrated_at: 2026-01-01T00:00:00Z}\n"
    path.write_text(original, encoding="utf-8")
    store = CalibrationStore(path)

    with pytest.raises(ValueError, match="no es válido"):
        store.save_flow(pulses_per_liter=5000)

    assert path.read_text(encoding="utf-8") == original
