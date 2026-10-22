"""Simulador de flotador."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime

from hidro.config import MockFloatParams
from hidro.drivers.base import invalid_measurement
from hidro.drivers.mock.common import is_invalid
from hidro.model import Measurement


class MockFloatSwitchSensor:
    def __init__(
        self,
        sensor_id: str,
        params: MockFloatParams,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.id = sensor_id
        self.params = params
        self._clock = clock
        self._started = clock()

    def read(self) -> list[Measurement]:
        now = datetime.now(UTC)
        if is_invalid(self.params.scenario):
            return [invalid_measurement(f"{self.id}_low", "bool", self.id, now)]
        low = self.params.low
        if self.params.low_after_s is not None:
            low = low or (self._clock() - self._started) >= self.params.low_after_s
        return [Measurement(f"{self.id}_low", low, "bool", now, "ok", self.id)]

    def close(self) -> None:
        return None
