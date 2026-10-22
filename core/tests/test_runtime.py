from __future__ import annotations

from pathlib import Path

import pytest

from hidro.calibration import CalibrationStore
from hidro.drivers.base import DriverError, LatestReadings
from hidro.drivers.runtime import RuntimeContext


def make_context(tmp_path: Path, mode: str = "mock") -> RuntimeContext:
    return RuntimeContext(
        mode,
        tmp_path,
        CalibrationStore(tmp_path / "calibration.yaml"),
        LatestReadings(),
    )


def test_missing_i2c_device_is_reported_on_hardware(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, "hardware")
    with pytest.raises(DriverError, match="no existe"):
        ctx.smbus(1)


def test_missing_gpiochip_is_reported_on_hardware(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, "hardware")
    with pytest.raises(DriverError, match="gpiochip"):
        ctx.gpio_output(17)
    with pytest.raises(DriverError, match="gpiochip"):
        ctx.gpio_input(17, pull_up=True)


def test_close_releases_devices_and_buses(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)

    class Device:
        def __init__(self, fail: bool = False) -> None:
            self.fail = fail
            self.closed = False

        def close(self) -> None:
            self.closed = True
            if self.fail:
                raise RuntimeError("boom")

    good = Device()
    also_good = Device()
    bad = Device(fail=True)
    good_bus = Device()
    bad_bus = Device(fail=True)
    ctx._devices.extend([good, also_good, bad])
    ctx._i2c.update({1: good_bus, 2: bad_bus})

    ctx.close()
    assert good.closed is True
    assert also_good.closed is True
    assert bad.closed is True
    assert good_bus.closed is True
    assert bad_bus.closed is True
    assert ctx._devices == []
    assert ctx._i2c == {}


def test_context_starts_with_no_shared_resources(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    assert ctx.outputs == {}
    assert ctx.data_dir == tmp_path
    ctx.close()
