from __future__ import annotations

import math

import pytest

from hidro.calc import clamp, dew_point_c, saturation_vapor_pressure_kpa, trimmed_mean, vpd_kpa


def test_saturation_vapor_pressure() -> None:
    assert saturation_vapor_pressure_kpa(25.0) == pytest.approx(3.169, abs=0.01)


def test_vpd() -> None:
    assert vpd_kpa(25.0, 60.0) == pytest.approx(1.267, abs=0.02)
    assert vpd_kpa(20.0, 100.0) == pytest.approx(0.0, abs=1e-9)


def test_vpd_clamps_humidity() -> None:
    assert vpd_kpa(20.0, 150.0) == pytest.approx(0.0, abs=1e-9)
    saturated = saturation_vapor_pressure_kpa(20.0)
    assert vpd_kpa(20.0, -10.0) == pytest.approx(saturated)


def test_dew_point() -> None:
    assert dew_point_c(25.0, 60.0) == pytest.approx(16.7, abs=0.4)
    assert dew_point_c(20.0, 50.0) == pytest.approx(9.3, abs=0.4)


def test_dew_point_never_nan_with_tiny_humidity() -> None:
    assert not math.isnan(dew_point_c(20.0, 0.0))


def test_clamp() -> None:
    assert clamp(5, 0, 10) == 5
    assert clamp(-1, 0, 10) == 0
    assert clamp(11, 0, 10) == 10


def test_trimmed_mean_discards_extremes() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 100.0]
    assert trimmed_mean(values) == pytest.approx(3.0)
    assert trimmed_mean([4.0, 6.0]) == pytest.approx(5.0)
    with pytest.raises(ValueError):
        trimmed_mean([])
