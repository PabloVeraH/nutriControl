"""Simulador de pH."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from datetime import UTC, datetime

from hidro.config import MockPHParams
from hidro.drivers.base import invalid_measurement
from hidro.drivers.mock.common import gaussian, is_invalid
from hidro.model import Measurement


class MockPHAnalogSensor:
    def __init__(
        self,
        sensor_id: str,
        params: MockPHParams,
        *,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self.id = sensor_id
        self.params = params
        self._clock = clock
        self._rng = rng or random.Random()
        self._started = clock()

    def read(self) -> list[Measurement]:
        now = datetime.now(UTC)
        if is_invalid(self.params.scenario):
            return [invalid_measurement(self.id, "pH", self.id, now)]
        hours = (self._clock() - self._started) / 3600.0
        value = self.params.ph + self.params.drift_per_h * hours + gaussian(self._rng, self.params.noise)
        value = max(0.0, min(14.0, value))
        return [Measurement(self.id, round(value, 3), "pH", now, "ok", self.id)]

    def close(self) -> None:
        return None
