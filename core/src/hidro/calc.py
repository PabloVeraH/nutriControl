"""Fórmulas agronómicas y utilidades numéricas."""

from __future__ import annotations

import math


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def saturation_vapor_pressure_kpa(temp_c: float) -> float:
    """Presión de vapor de saturación (Tetens, kPa)."""
    return 0.61078 * math.exp(17.27 * temp_c / (temp_c + 237.3))


def vpd_kpa(temp_c: float, relative_humidity: float) -> float:
    """Déficit de presión de vapor en kPa."""
    rh = clamp(relative_humidity, 0.0, 100.0)
    return saturation_vapor_pressure_kpa(temp_c) * (1.0 - rh / 100.0)


def dew_point_c(temp_c: float, relative_humidity: float) -> float:
    """Punto de rocío con la aproximación de Magnus."""
    rh = clamp(relative_humidity, 0.1, 100.0)
    gamma = math.log(rh / 100.0) + 17.62 * temp_c / (243.12 + temp_c)
    return 243.12 * gamma / (17.62 - gamma)


def trimmed_mean(values: list[float], trim: int = 1) -> float:
    """Media recortada: descarta `trim` valores por cada extremo."""
    if not values:
        raise ValueError("se necesita al menos un valor")
    ordered = sorted(values)
    if len(ordered) <= trim * 2:
        return sum(ordered) / len(ordered)
    kept = ordered[trim : len(ordered) - trim]
    return sum(kept) / len(kept)
