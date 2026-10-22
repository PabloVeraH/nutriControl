"""Simulador de TDS/EC."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from datetime import UTC, datetime

from hidro.config import MockTDSParams
from hidro.drivers.base import Switch, invalid_measurement
from hidro.drivers.mock.common import gaussian, is_invalid
from hidro.model import Measurement


class MockTDSAnalogSensor:
    def __init__(
        self,
        sensor_id: str,
        params: MockTDSParams,
        *,
        power_switch: Switch | None = None,
        settle_s: float = 2.0,
        time_scale: float = 0.05,
        tds_factor: float = 0.5,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self.id = sensor_id
        self.params = params
        self.power_switch = power_switch
        self.settle_s = settle_s
        self.time_scale = time_scale
        self.tds_factor = tds_factor
        self._clock = clock
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._started = clock()

    def read(self) -> list[Measurement]:
        now = datetime.now(UTC)
        if is_invalid(self.params.scenario):
            return [
                invalid_measurement(self.id, "µS/cm", self.id, now),
                invalid_measurement(f"{self.id}_ppm", "ppm", self.id, now),
            ]

        if self.power_switch is not None:
            self.power_switch.on()
        try:
            self._sleep(self.settle_s * self.time_scale)
        finally:
            if self.power_switch is not None:
                self.power_switch.off()

        hours = (self._clock() - self._started) / 3600.0
        ec = max(0.0, self.params.ec + self.params.drift_per_h * hours + gaussian(self._rng, self.params.noise))
        return [
            Measurement(self.id, round(ec, 1), "µS/cm", now, "ok", self.id),
            Measurement(f"{self.id}_ppm", round(ec * self.tds_factor, 1), "ppm", now, "ok", self.id),
        ]

    def close(self) -> None:
        if self.power_switch is not None:
            self.power_switch.off()
