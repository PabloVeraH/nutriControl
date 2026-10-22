"""Simulador de bomba peristáltica."""

from __future__ import annotations

import time
from collections.abc import Callable

from hidro.drivers.mock.relay import MockRelay
from hidro.drivers.peristaltic import PeristalticPump


def build_mock_pump(
    pump_id: str,
    *,
    ml_per_min: float | None,
    max_on_s: float,
    time_scale: float = 0.05,
    sleep: Callable[[float], None] = time.sleep,
) -> PeristalticPump:
    return PeristalticPump(
        pump_id,
        MockRelay(f"{pump_id}_relay"),
        ml_per_min=ml_per_min,
        max_on_s=max_on_s,
        sleep=sleep,
        time_scale=time_scale,
    )
