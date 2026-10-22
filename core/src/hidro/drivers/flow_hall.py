"""Driver de flujómetro de efecto Hall (YF-S401)."""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hidro.drivers.base import DriverError, invalid_measurement
from hidro.logging_setup import get_logger
from hidro.model import Measurement

logger = get_logger("drivers.flow")


class FlowCounter:
    """Cuenta pulsos y calcula caudal y volumen, con acceso protegido."""

    def __init__(self, pulses_per_liter: float, total_l: float = 0.0) -> None:
        if pulses_per_liter <= 0:
            raise ValueError("pulses_per_liter debe ser positivo")
        self.pulses_per_liter = pulses_per_liter
        self.total_l = total_l
        self._pulses = 0
        self._last_pulses = 0
        self._last_time: float | None = None
        self._last_rate = 0.0
        self._lock = threading.Lock()

    def add_pulses(self, count: int = 1) -> None:
        with self._lock:
            self._pulses += count

    @property
    def pulses(self) -> int:
        with self._lock:
            return self._pulses

    def sample(self, now: float) -> tuple[float, float]:
        """Devuelve (L/min, litros acumulados) desde la muestra anterior."""
        with self._lock:
            delta_pulses = self._pulses - self._last_pulses
            self._last_pulses = self._pulses
            elapsed = None if self._last_time is None else max(0.0, now - self._last_time)
            self._last_time = now

            if elapsed is not None and elapsed > 0:
                self._last_rate = delta_pulses / self.pulses_per_liter / (elapsed / 60.0)
            self.total_l += delta_pulses / self.pulses_per_liter
            return self._last_rate, self.total_l


def calculate_pulses_per_liter(pulses: int, liters: float) -> float:
    """Calcula la calibración a partir de los pulsos contados y el volumen vertido."""
    if liters <= 0:
        raise ValueError("los litros deben ser positivos")
    if pulses <= 0:
        raise ValueError("no se registraron pulsos; revisa el cableado y el flujo")
    return pulses / liters


class FlowHallSensor:
    """Sensor de flujo con contador persistente en /data."""

    def __init__(
        self,
        sensor_id: str,
        device: Any,
        counter: FlowCounter,
        total_path: Path,
        *,
        save_every_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.id = sensor_id
        self.device = device
        self.counter = counter
        self.total_path = total_path
        self.save_every_s = save_every_s
        self._clock = clock
        self._last_save = clock()
        try:
            # DigitalInputDevice no tiene when_pressed (es de Button) y gpiozero
            # rechaza atributos nuevos: el evento correcto es when_activated.
            device.when_activated = self._on_pulse
        except Exception as exc:  # pragma: no cover - depende de gpiozero
            raise DriverError(f"no se pudo registrar el flujo {sensor_id}: {exc}") from exc

    def _on_pulse(self) -> None:
        self.counter.add_pulses(1)

    def read(self) -> list[Measurement]:
        now_mono = self._clock()
        rate, _total = self.counter.sample(now_mono)
        if now_mono - self._last_save >= self.save_every_s:
            self.save_total()
            self._last_save = now_mono

        if rate < 0:
            logger.error("flujo %s: caudal negativo (%.3f L/min)", self.id, rate)
            return [invalid_measurement(self.id, "L/min", self.id)]

        return [
            Measurement(
                name=self.id,
                value=round(rate, 3),
                unit="L/min",
                timestamp=datetime.now(UTC),
                quality="ok",
                source=self.id,
            )
        ]

    def save_total(self) -> None:
        self.total_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.total_path.with_suffix(".tmp")
        payload = {"total_l": round(self.counter.total_l, 4)}
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, self.total_path)

    def close(self) -> None:
        self.save_total()
        try:
            self.device.close()
        except Exception:  # pragma: no cover - cierre defensivo
            logger.warning("no se pudo cerrar el flujómetro %s", self.id, exc_info=True)
