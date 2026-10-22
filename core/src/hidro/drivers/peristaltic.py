"""Bomba peristáltica con dosificación por volumen."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable

from hidro.drivers.base import DoseTooLong, PumpNotCalibrated, Switch


class PeristalticPump:
    """Enciende el relé el tiempo necesario para dosificar un volumen."""

    def __init__(
        self,
        pump_id: str,
        switch: Switch,
        *,
        ml_per_min: float | None,
        max_on_s: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        stop_event: threading.Event | None = None,
        time_scale: float = 1.0,
    ) -> None:
        self.id = pump_id
        self._switch = switch
        self.ml_per_min = ml_per_min
        self.max_on_s = max_on_s
        self._clock = clock
        self._sleep = sleep
        self._stop_event = stop_event
        self._time_scale = time_scale

    def dispense_ml(self, ml: float) -> float:
        # NaN pasaría `ml <= 0` y dejaría la bomba encendida sin fin.
        if not math.isfinite(ml) or ml <= 0:
            raise ValueError("el volumen debe ser finito y positivo")
        if self.ml_per_min is None or self.ml_per_min <= 0:
            raise PumpNotCalibrated(f"la bomba {self.id} no está calibrada")
        seconds = ml / self.ml_per_min * 60.0
        if seconds > self.max_on_s:
            raise DoseTooLong(
                f"la dosis de {ml} ml en {self.id} requiere {seconds:.1f} s "
                f"(máximo {self.max_on_s:.0f} s)"
            )

        started = self._clock()
        scaled = seconds * self._time_scale
        try:
            self._switch.on()
            while True:
                if self._stop_event is not None and self._stop_event.is_set():
                    break
                remaining = scaled - (self._clock() - started)
                if remaining <= 0:
                    break
                self._sleep(min(0.1, remaining))
        finally:
            self._switch.off()
        elapsed = self._clock() - started

        # Una parada a mitad de dosis devuelve el tiempo realmente encendida.
        if self._time_scale > 0:
            return min(seconds, elapsed / self._time_scale)
        return seconds

    # Interfaz Switch: solo se usa para apagado de emergencia, nunca para
    # dejar la bomba encendida de forma indefinida.
    def on(self) -> None:
        self._switch.on()

    def off(self) -> None:
        self._switch.off()

    @property
    def is_on(self) -> bool:
        return self._switch.is_on

    def close(self) -> None:
        try:
            self.off()
        finally:
            self._switch.close()
