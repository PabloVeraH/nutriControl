"""Simulador de relé."""

from __future__ import annotations

from hidro.drivers.base import Switch
from hidro.logging_setup import get_logger

logger = get_logger("drivers.mock.relay")


class MockRelay(Switch):
    def __init__(self, output_id: str) -> None:
        self.id = output_id
        self._on = False

    def on(self) -> None:
        if not self._on:
            logger.info("relé %s ON (simulado)", self.id)
        self._on = True

    def off(self) -> None:
        if self._on:
            logger.info("relé %s OFF (simulado)", self.id)
        self._on = False

    @property
    def is_on(self) -> bool:
        return self._on

    def close(self) -> None:
        self.off()
