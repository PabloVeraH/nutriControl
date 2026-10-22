"""Utilidades comunes de los simuladores."""

from __future__ import annotations

import random


def is_invalid(scenario: str) -> bool:
    return scenario in {"invalid", "disconnected"}


def gaussian(rng: random.Random, sigma: float) -> float:
    return rng.gauss(0.0, sigma) if sigma > 0 else 0.0
