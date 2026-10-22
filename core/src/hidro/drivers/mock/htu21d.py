"""Simulador de HTU21D."""

from __future__ import annotations

import random
from datetime import UTC, datetime

from hidro.calc import dew_point_c, vpd_kpa
from hidro.config import MockHTU21DParams
from hidro.drivers.htu21d import invalid_air_measurements
from hidro.drivers.mock.common import gaussian, is_invalid
from hidro.model import Measurement


class MockHTU21DSensor:
    def __init__(self, sensor_id: str, params: MockHTU21DParams, *, rng: random.Random | None = None) -> None:
        self.id = sensor_id
        self.params = params
        self._rng = rng or random.Random()

    def read(self) -> list[Measurement]:
        now = datetime.now(UTC)
        if is_invalid(self.params.scenario):
            return invalid_air_measurements(self.id, now)
        temp = self.params.temp_c + gaussian(self._rng, self.params.noise)
        humidity = max(0.0, min(100.0, self.params.humidity + gaussian(self._rng, self.params.noise)))
        return [
            Measurement(f"{self.id}_temp", round(temp, 2), "°C", now, "ok", self.id),
            Measurement(f"{self.id}_humidity", round(humidity, 2), "%", now, "ok", self.id),
            Measurement(f"{self.id}_vpd", round(vpd_kpa(temp, humidity), 3), "kPa", now, "ok", self.id),
            Measurement(
                f"{self.id}_dew_point",
                round(dew_point_c(temp, humidity), 2),
                "°C",
                now,
                "ok",
                self.id,
            ),
        ]

    def close(self) -> None:
        return None
