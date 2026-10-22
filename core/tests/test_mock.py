from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
from conftest import FakeClock, FakeSwitch

from hidro.config import (
    MockDS18B20Params,
    MockFloatParams,
    MockFlowParams,
    MockHTU21DParams,
    MockPHParams,
    MockTDSParams,
)
from hidro.drivers.mock.common import gaussian, is_invalid
from hidro.drivers.mock.ds18b20 import MockDS18B20Sensor
from hidro.drivers.mock.float_switch import MockFloatSwitchSensor
from hidro.drivers.mock.flow_hall import MockFlowHallSensor
from hidro.drivers.mock.htu21d import MockHTU21DSensor
from hidro.drivers.mock.peristaltic import build_mock_pump
from hidro.drivers.mock.ph_analog import MockPHAnalogSensor
from hidro.drivers.mock.relay import MockRelay
from hidro.drivers.mock.tds_analog import MockTDSAnalogSensor


def test_common_helpers() -> None:
    assert is_invalid("invalid")
    assert is_invalid("disconnected")
    assert not is_invalid("normal")
    rng = random.Random(1)
    assert gaussian(rng, 0) == 0.0
    assert gaussian(rng, 1) != 0.0


def test_mock_ds18b20_drift_and_invalid() -> None:
    clock = FakeClock()
    sensor = MockDS18B20Sensor(
        "water_temp_1",
        MockDS18B20Params(base_c=20.0, noise_c=0.0, drift_c_per_h=2.0),
        clock=clock,
        rng=random.Random(0),
    )
    assert sensor.read()[0].value == 20.0
    clock.advance(3600)
    assert sensor.read()[0].value == pytest.approx(22.0)
    sensor.close()

    broken = MockDS18B20Sensor("water_temp_1", MockDS18B20Params(scenario="invalid"))
    assert broken.read()[0].quality == "invalid"


def test_mock_htu21d_reports_vpd_and_dew_point() -> None:
    sensor = MockHTU21DSensor(
        "air",
        MockHTU21DParams(temp_c=22.0, humidity=55.0, noise=0.0),
        rng=random.Random(0),
    )
    readings = {item.name: item for item in sensor.read()}
    assert readings["air_temp"].value == 22.0
    assert readings["air_humidity"].value == 55.0
    assert readings["air_vpd"].value > 0
    assert 10 < readings["air_dew_point"].value < 15
    sensor.close()

    broken = MockHTU21DSensor("air", MockHTU21DParams(scenario="disconnected"))
    assert [item.quality for item in broken.read()] == ["invalid", "invalid"]


def test_mock_float_switch_sequences() -> None:
    clock = FakeClock()
    sensor = MockFloatSwitchSensor(
        "tank", MockFloatParams(low=False, low_after_s=10), clock=clock
    )
    assert sensor.read()[0].value is False
    clock.advance(10)
    assert sensor.read()[0].value is True
    sensor.close()

    low = MockFloatSwitchSensor("tank", MockFloatParams(low=True))
    assert low.read()[0].value is True
    broken = MockFloatSwitchSensor("tank", MockFloatParams(scenario="invalid"))
    assert broken.read()[0].quality == "invalid"


def test_mock_ph_drift_clamp_and_invalid() -> None:
    clock = FakeClock()
    sensor = MockPHAnalogSensor(
        "ph",
        MockPHParams(ph=5.9, noise=0.0, drift_per_h=1.0),
        clock=clock,
        rng=random.Random(0),
    )
    assert sensor.read()[0].value == pytest.approx(5.9)
    clock.advance(3600)
    assert sensor.read()[0].value == pytest.approx(6.9)
    sensor.close()

    high = MockPHAnalogSensor(
        "ph",
        MockPHParams(ph=13.9, noise=0.0, drift_per_h=1.0),
        clock=clock,
    )
    clock.advance(3600)
    assert high.read()[0].value == 14.0

    broken = MockPHAnalogSensor("ph", MockPHParams(scenario="invalid"))
    assert broken.read()[0].quality == "invalid"


def test_mock_tds_with_power_switch() -> None:
    switch = FakeSwitch()
    sensor = MockTDSAnalogSensor(
        "ec",
        MockTDSParams(ec=1150.0, noise=0.0, drift_per_h=0.0),
        power_switch=switch,
        settle_s=2.0,
        time_scale=0.0,
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    readings = {item.name: item for item in sensor.read()}
    assert switch.events == ["on", "off"]
    assert readings["ec"].value == 1150.0
    assert readings["ec_ppm"].value == 575.0
    sensor.close()
    assert switch.events[-1] == "off"

    broken = MockTDSAnalogSensor("ec", MockTDSParams(scenario="invalid"))
    assert [item.quality for item in broken.read()] == ["invalid", "invalid"]


def test_mock_flow_rate_total_and_stop(tmp_path: Path) -> None:
    clock = FakeClock()
    total_path = tmp_path / "flow_total_flow.json"
    sensor = MockFlowHallSensor(
        "flow",
        MockFlowParams(l_min=1.4, noise_l_min=0.0, stop_after_s=120),
        pulses_per_liter=600.0,
        total_path=total_path,
        clock=clock,
        rng=random.Random(0),
    )
    assert clock.now == 1000.0

    clock.advance(60)
    reading = sensor.read()[0]
    assert reading.value == pytest.approx(1.4)
    saved = json.loads(total_path.read_text(encoding="utf-8"))
    assert saved["total_l"] == pytest.approx(1.4)

    clock.advance(60)
    stopped = sensor.read()[0]
    assert stopped.value == 0.0
    assert sensor.stop_after_s == 120
    sensor.close()


def test_mock_flow_constructor_override(tmp_path: Path) -> None:
    sensor = MockFlowHallSensor(
        "flow",
        MockFlowParams(l_min=1.4, stop_after_s=120),
        pulses_per_liter=600.0,
        total_path=tmp_path / "flow.json",
        stop_after_s=30,
    )
    assert sensor.stop_after_s == 30


def test_mock_flow_loads_and_tolerates_corrupt_total(tmp_path: Path) -> None:
    path = tmp_path / "flow.json"
    path.write_text(json.dumps({"total_l": 3.5}), encoding="utf-8")
    sensor = MockFlowHallSensor(
        "flow", MockFlowParams(), pulses_per_liter=600.0, total_path=path
    )
    assert sensor.counter.total_l == pytest.approx(3.5)

    path.write_text("no json", encoding="utf-8")
    sensor = MockFlowHallSensor(
        "flow", MockFlowParams(), pulses_per_liter=600.0, total_path=path
    )
    assert sensor.counter.total_l == 0.0

    broken = MockFlowHallSensor(
        "flow", MockFlowParams(scenario="invalid"), pulses_per_liter=600.0, total_path=path
    )
    assert broken.read()[0].quality == "invalid"


def test_mock_relay_state_and_logging() -> None:
    relay = MockRelay("tds_power")
    assert relay.is_on is False
    relay.on()
    assert relay.is_on is True
    relay.on()
    relay.off()
    assert relay.is_on is False
    relay.close()
    assert relay.is_on is False


def test_build_mock_pump_uses_short_time_scale() -> None:
    pump = build_mock_pump(
        "pump_ph_down",
        ml_per_min=10.0,
        max_on_s=30.0,
        time_scale=0.0,
        sleep=lambda _s: None,
    )
    seconds = pump.dispense_ml(1.0)
    assert seconds == pytest.approx(6.0)
    assert pump.is_on is False
    pump.close()
