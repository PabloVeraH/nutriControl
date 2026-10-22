"""Utilidades compartidas por los tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from hidro.alerts.telegram import TelegramNotifier
from hidro.config import AppConfig
from hidro.model import Measurement, Quality

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeSleeper:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock

    def __call__(self, seconds: float) -> None:
        self.clock.advance(seconds)


class RecordingNotifier(TelegramNotifier):
    def __init__(self, configured: bool = True) -> None:
        super().__init__(
            "token" if configured else "",
            "chat" if configured else "",
            enabled=configured,
        )
        self.messages: list[str] = []

    def send(self, text: str) -> bool:
        self.messages.append(text)
        return True


class FakeSwitch:
    def __init__(self, switch_id: str = "relay") -> None:
        self.id = switch_id
        self._on = False
        self.events: list[str] = []

    def on(self) -> None:
        self._on = True
        self.events.append("on")

    def off(self) -> None:
        self._on = False
        self.events.append("off")

    @property
    def is_on(self) -> bool:
        return self._on

    def close(self) -> None:
        self.off()


class FakePump(FakeSwitch):
    def __init__(
        self,
        pump_id: str,
        *,
        ml_per_min: float | None = 10.0,
        max_on_s: float = 30.0,
    ) -> None:
        super().__init__(pump_id)
        self.ml_per_min = ml_per_min
        self.max_on_s = max_on_s
        self.dispensed: list[float] = []

    def dispense_ml(self, ml: float) -> float:
        self.dispensed.append(ml)
        seconds = ml / (self.ml_per_min or 1.0) * 60.0
        return seconds


def measurement(
    name: str,
    value: float | bool,
    unit: str = "",
    *,
    at: datetime = NOW,
    quality: Quality = "ok",
    source: str = "",
) -> Measurement:
    return Measurement(
        name=name,
        value=value,
        unit=unit,
        timestamp=at,
        quality=quality,
        source=source or name,
    )


def base_config_dict() -> dict[str, Any]:
    return {
        "mode": "mock",
        "read_interval_s": 30,
        "sensors": [
            {"id": "water_temp_1", "type": "ds18b20", "serial": "28-test"},
            {
                "id": "flow",
                "type": "flow_hall",
                "gpio": 27,
            },
            {
                "id": "ph",
                "type": "ph_analog",
                "adc": {"channel": 0},
                "temperature_from": "water_temp_1",
            },
            {
                "id": "ec",
                "type": "tds_analog",
                "adc": {"channel": 1},
                "temperature_from": "water_temp_1",
                "power_switch": "tds_power",
            },
        ],
        "outputs": [
            {"id": "tds_power", "type": "relay", "gpio": 23},
            {
                "id": "pump_ph_down",
                "type": "peristaltic",
                "gpio": 22,
                "max_on_s": 30,
            },
        ],
        "dosing": {
            "enabled": True,
            "dry_run": True,
            "check_interval_s": 300,
            "mix_wait_s": 600,
            "ph_down_ml": 1.0,
            "ph_up_ml": 1.0,
            "nutrient_ml": 3.0,
            "limits": {
                "max_doses_per_hour": 3,
                "max_ml_per_day": {"pump_ph_down": 20},
            },
        },
    }


def make_config(**overrides: Any) -> AppConfig:
    raw = base_config_dict()
    raw.update(overrides)
    return AppConfig.model_validate(raw)


@pytest.fixture
def config() -> AppConfig:
    return make_config()


@pytest.fixture
def notifier() -> RecordingNotifier:
    return RecordingNotifier()
