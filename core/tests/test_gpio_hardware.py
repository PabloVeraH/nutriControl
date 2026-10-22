"""Camino de hardware con gpiozero real (MockFactory), sin Raspberry.

Los fakes a mano aceptan cualquier atributo; gpiozero no. Estas pruebas
detectan usos de la API que solo fallarían en la Pi (p. ej. `when_pressed`
en un DigitalInputDevice).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

gpiozero = pytest.importorskip("gpiozero")
from gpiozero.pins.mock import MockFactory  # noqa: E402

from hidro.calibration import CalibrationStore  # noqa: E402
from hidro.config import AppConfig  # noqa: E402
from hidro.drivers.base import DriverError, LatestReadings  # noqa: E402
from hidro.drivers.registry import build_outputs, build_sensors  # noqa: E402
from hidro.drivers.runtime import RuntimeContext  # noqa: E402


@pytest.fixture
def factory(monkeypatch: pytest.MonkeyPatch) -> Iterator[MockFactory]:
    mock = MockFactory()
    gpiozero.Device.pin_factory = mock
    # En el PC no existe /dev/gpiochip0; MockFactory reemplaza al chip.
    monkeypatch.setattr(RuntimeContext, "_require_gpiochip", lambda self: None)
    yield mock
    mock.reset()
    gpiozero.Device.pin_factory = None


def hardware_context(tmp_path: Path) -> RuntimeContext:
    return RuntimeContext(
        "hardware",
        tmp_path,
        CalibrationStore(tmp_path / "calibration.yaml"),
        LatestReadings(),
    )


def config_with(sensors: list[dict], outputs: list[dict] | None = None) -> AppConfig:
    return AppConfig.model_validate(
        {"mode": "hardware", "sensors": sensors, "outputs": outputs or []}
    )


def test_flow_counts_real_gpiozero_pulses(factory: MockFactory, tmp_path: Path) -> None:
    ctx = hardware_context(tmp_path)
    config = config_with(
        [{"id": "flow", "type": "flow_hall", "gpio": 27, "pulses_per_liter": 100}]
    )
    (flow,) = build_sensors(config, ctx)

    pin = factory.pin(27)
    for _ in range(50):
        pin.drive_high()
        pin.drive_low()

    assert flow.counter.pulses == 50
    ctx.close()


@pytest.mark.parametrize(
    ("pull", "expected_pull"),
    [("down", "down"), ("up", "up"), ("none", "floating")],
)
def test_float_pull_is_applied(
    factory: MockFactory, tmp_path: Path, pull: str, expected_pull: str
) -> None:
    ctx = hardware_context(tmp_path)
    config = config_with([{"id": "tank", "type": "float_switch", "gpio": 17, "pull": pull}])
    build_sensors(config, ctx)
    assert factory.pin(17).pull == expected_pull
    ctx.close()


@pytest.mark.parametrize("pull", ["down", "up", "none"])
@pytest.mark.parametrize("low_when", [0, 1])
def test_float_low_when_uses_physical_level(
    factory: MockFactory, tmp_path: Path, pull: str, low_when: int
) -> None:
    ctx = hardware_context(tmp_path)
    pin = factory.pin(17)
    config = config_with(
        [
            {
                "id": "tank",
                "type": "float_switch",
                "gpio": 17,
                "pull": pull,
                "low_when": low_when,
                "debounce_s": 0,
            }
        ]
    )
    (tank,) = build_sensors(config, ctx)

    pin.drive_low()
    assert tank.read()[0].value is (low_when == 0)
    pin.drive_high()
    assert tank.read()[0].value is (low_when == 1)
    ctx.close()


def test_relay_drives_the_pin_and_starts_off(factory: MockFactory, tmp_path: Path) -> None:
    ctx = hardware_context(tmp_path)
    outputs = build_outputs(
        config_with(
            [{"id": "t", "type": "ds18b20", "serial": "28-x"}],
            [{"id": "tds_power", "type": "relay", "gpio": 23}],
        ),
        ctx,
    )
    relay = outputs["tds_power"]
    pin = factory.pin(23)
    assert pin.state is False

    relay.on()
    assert pin.state is True
    assert relay.is_on is True

    relay.close()
    assert pin.state is False


def test_pump_shares_the_stop_event(factory: MockFactory, tmp_path: Path) -> None:
    ctx = hardware_context(tmp_path)
    outputs = build_outputs(
        config_with(
            [{"id": "t", "type": "ds18b20", "serial": "28-x"}],
            [{"id": "pump_ph_down", "type": "peristaltic", "gpio": 22, "ml_per_min": 60}],
        ),
        ctx,
    )
    ctx.stop_event.set()

    outputs["pump_ph_down"].dispense_ml(10)  # 10 s, pero el evento corta enseguida

    assert factory.pin(22).state is False
    ctx.close()


def test_busy_gpio_is_reported_with_a_hint(factory: MockFactory, tmp_path: Path) -> None:
    ctx = hardware_context(tmp_path)
    ctx.gpio_output(22)
    with pytest.raises(DriverError, match="detenerlo antes"):
        ctx.gpio_output(22)
    with pytest.raises(DriverError, match="GPIO22"):
        ctx.gpio_input(22, pull_up=False)
    ctx.close()
