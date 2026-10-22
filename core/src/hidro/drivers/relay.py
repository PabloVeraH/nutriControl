"""Salida de relé."""

from __future__ import annotations

from typing import Any

from hidro.drivers.base import Switch


class RelayOutput(Switch):
    def __init__(self, output_id: str, device: Any) -> None:
        self.id = output_id
        self._device = device

    def on(self) -> None:
        self._device.on()

    def off(self) -> None:
        self._device.off()

    @property
    def is_on(self) -> bool:
        return bool(self._device.value)

    def close(self) -> None:
        try:
            self.off()
        finally:
            self._device.close()
