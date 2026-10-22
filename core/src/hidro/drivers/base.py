"""Interfaces comunes de drivers y registro de últimas lecturas."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from hidro.model import Measurement


class DriverError(RuntimeError):
    """Falla de un driver; el ciclo la registra y continúa."""


class SensorNotReady(DriverError):
    """El sensor todavía no puede entregar una lectura válida."""


class PumpNotCalibrated(DriverError):
    """La bomba no tiene caudal calibrado."""


class DoseTooLong(DriverError):
    """La dosis supera el tiempo máximo de encendido."""


@runtime_checkable
class Sensor(Protocol):
    id: str

    def read(self) -> list[Measurement]: ...

    def close(self) -> None: ...


@runtime_checkable
class Switch(Protocol):
    id: str

    def on(self) -> None: ...

    def off(self) -> None: ...

    @property
    def is_on(self) -> bool: ...

    def close(self) -> None: ...


class Pump(Switch, Protocol):
    def dispense_ml(self, ml: float) -> float: ...


class LatestReadings:
    """Guarda la última lectura válida de cada magnitud."""

    def __init__(self) -> None:
        self._values: dict[str, Measurement] = {}
        self._invalid: dict[str, Measurement] = {}
        self._lock = threading.Lock()

    def update(self, measurements: list[Measurement]) -> None:
        with self._lock:
            for measurement in measurements:
                if measurement.valid:
                    self._values[measurement.name] = measurement
                    self._invalid.pop(measurement.name, None)
                else:
                    self._invalid[measurement.name] = measurement

    def get(self, name: str) -> Measurement | None:
        with self._lock:
            return self._values.get(name)

    def invalid(self, name: str) -> Measurement | None:
        with self._lock:
            return self._invalid.get(name)

    def snapshot(self) -> dict[str, Measurement]:
        with self._lock:
            return dict(self._values)

    def age_s(self, name: str, now: datetime) -> float | None:
        measurement = self.get(name)
        if measurement is None:
            return None
        return (now - measurement.timestamp).total_seconds()

    def value(self, name: str) -> float | bool | None:
        measurement = self.get(name)
        return measurement.value if measurement is not None else None

    def close(self) -> None:
        with self._lock:
            self._values.clear()
            self._invalid.clear()


def invalid_measurement(
    name: str,
    unit: str,
    source: str = "",
    timestamp: datetime | None = None,
) -> Measurement:
    return Measurement(
        name=name,
        value=0.0,
        unit=unit,
        timestamp=timestamp or datetime.now(UTC),
        quality="invalid",
        source=source,
    )
