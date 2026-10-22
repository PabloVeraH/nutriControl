"""Simulador de DS18B20."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from datetime import UTC, datetime

from hidro.config import MockDS18B20Params
from hidro.drivers.base import invalid_measurement
from hidro.drivers.mock.common import gaussian, is_invalid
from hidro.model import Measurement


class MockDS18B20Sensor:
    def __init__(
        self,
        sensor_id: str,
        params: MockDS18B20Params,
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
        now_dt = datetime.now(UTC)
        if is_invalid(self.params.scenario):
            return [invalid_measurement(self.id, "°C", self.id, now_dt)]
        hours = (self._clock() - self._started) / 3600.0
        value = (
            self.params.base_c
            + self.params.drift_c_per_h * hours
            + gaussian(self._rng, self.params.noise_c)
        )
        return [Measurement(self.id, round(value, 3), "°C", now_dt, "ok", self.id)]

    def close(self) -> None:
        return None
