"""Driver de flotador magnético con antirrebote."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime

from hidro.model import Measurement


class FloatSwitchSensor:
    """Publica `{id}_low` tras mantener el estado durante `debounce_s`."""

    def __init__(
        self,
        sensor_id: str,
        read_raw: Callable[[], int | bool],
        *,
        low_when: int = 0,
        debounce_s: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.id = sensor_id
        self._read_raw = read_raw
        self.low_when = low_when
        self.debounce_s = debounce_s
        self._clock = clock
        self._stable = self._candidate_low()
        self._pending = self._stable
        self._pending_since = clock()

    def _candidate_low(self) -> bool:
        return int(self._read_raw()) == self.low_when

    def read(self) -> list[Measurement]:
        now = self._clock()
        candidate = self._candidate_low()
        if candidate != self._pending:
            self._pending = candidate
            self._pending_since = now
        if candidate != self._stable and now - self._pending_since >= self.debounce_s:
            self._stable = candidate

        return [
            Measurement(
                name=f"{self.id}_low",
                value=self._stable,
                unit="bool",
                timestamp=datetime.now(UTC),
                quality="ok",
                source=self.id,
            )
        ]

    def close(self) -> None:
        return None
