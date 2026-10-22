"""Simulador de flujómetro."""

from __future__ import annotations

import json
import os
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from hidro.config import MockFlowParams
from hidro.drivers.base import invalid_measurement
from hidro.drivers.flow_hall import FlowCounter
from hidro.drivers.mock.common import gaussian, is_invalid
from hidro.model import Measurement


class MockFlowHallSensor:
    def __init__(
        self,
        sensor_id: str,
        params: MockFlowParams,
        *,
        pulses_per_liter: float,
        total_path: Path,
        stop_after_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self.id = sensor_id
        self.params = params
        self.total_path = total_path
        self.stop_after_s = stop_after_s if stop_after_s is not None else params.stop_after_s
        self._clock = clock
        self._rng = rng or random.Random()
        self._started = clock()
        self._last = self._started
        self._last_save = self._started
        total = self._load_total()
        self.counter = FlowCounter(pulses_per_liter, total_l=total)
        self.counter.sample(self._started)

    def _load_total(self) -> float:
        if not self.total_path.exists():
            return 0.0
        try:
            return float(json.loads(self.total_path.read_text(encoding="utf-8")).get("total_l", 0.0))
        except (OSError, ValueError):
            return 0.0

    def _save_total(self) -> None:
        self.total_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.total_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"total_l": round(self.counter.total_l, 4)}), encoding="utf-8")
        os.replace(tmp, self.total_path)

    def read(self) -> list[Measurement]:
        now = datetime.now(UTC)
        if is_invalid(self.params.scenario):
            return [invalid_measurement(self.id, "L/min", self.id, now)]

        mono = self._clock()
        elapsed = max(0.0, mono - self._last)
        self._last = mono
        stopped = self.stop_after_s is not None and (mono - self._started) >= self.stop_after_s
        rate = 0.0 if stopped else max(0.0, self.params.l_min + gaussian(self._rng, self.params.noise_l_min))
        pulses = int(rate / 60.0 * elapsed * self.counter.pulses_per_liter)
        if pulses:
            self.counter.add_pulses(pulses)

        measured, _total = self.counter.sample(mono)
        if mono - self._last_save >= 60:
            self._save_total()
            self._last_save = mono
        return [Measurement(self.id, round(measured, 3), "L/min", now, "ok", self.id)]

    def close(self) -> None:
        self._save_total()
