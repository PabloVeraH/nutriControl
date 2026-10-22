from __future__ import annotations

from pathlib import Path

import pytest

from hidro.drivers.base import DriverError
from hidro.drivers.ds18b20 import DS18B20Sensor

VALID = (
    "72 01 4b 46 7f ff 0c 10 41 : crc=41 YES\n"
    "72 01 4b 46 7f ff 0c 10 41 t=23125\n"
)


def sensor_dir(tmp_path: Path, serial: str = "28-test", content: str = VALID) -> Path:
    base = tmp_path / "devices"
    directory = base / serial
    directory.mkdir(parents=True)
    (directory / "w1_slave").write_text(content, encoding="ascii")
    return base


def test_valid_reading(tmp_path: Path) -> None:
    base = sensor_dir(tmp_path)
    sensor = DS18B20Sensor("water_temp_1", "28-test", base_path=base, sleep=lambda _s: None)
    reading = sensor.read()[0]
    assert reading.name == "water_temp_1"
    assert reading.value == pytest.approx(23.125)
    assert reading.quality == "ok"


def test_offset_applied(tmp_path: Path) -> None:
    base = sensor_dir(tmp_path)
    sensor = DS18B20Sensor(
        "water_temp_1", "28-test", offset_c=-0.5, base_path=base, sleep=lambda _s: None
    )
    assert sensor.read()[0].value == pytest.approx(22.625)


@pytest.mark.parametrize(
    "content",
    [
        "72 01 4b 46 7f ff 0c 10 41 : crc=00 NO\n72 01 4b 46 7f ff 0c 10 41 t=23125\n",
        "72 01 4b 46 7f ff 0c 10 41 : crc=41 YES\n72 01 4b 46 7f ff 0c 10 41 t=85000\n",
        "72 01 4b 46 7f ff 0c 10 41 : crc=41 YES\n72 01 4b 46 7f ff 0c 10 41 t=-60000\n",
        "72 01 4b 46 7f ff 0c 10 41 : crc=41 YES\nsin temperatura\n",
        "",
    ],
)
def test_invalid_readings(tmp_path: Path, content: str) -> None:
    base = sensor_dir(tmp_path, content=content)
    sensor = DS18B20Sensor(
        "water_temp_1", "28-test", base_path=base, retries=1, sleep=lambda _s: None
    )
    reading = sensor.read()[0]
    assert reading.quality == "invalid"


def test_missing_device(tmp_path: Path) -> None:
    sensor = DS18B20Sensor(
        "water_temp_1", "28-inexistente", base_path=tmp_path, retries=0, sleep=lambda _s: None
    )
    assert sensor.read()[0].quality == "invalid"


def test_empty_serial_rejected() -> None:
    with pytest.raises(DriverError):
        DS18B20Sensor("water_temp_1", "")
