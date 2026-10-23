from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from conftest import base_config_dict

from hidro.calibration import CalibrationStore
from hidro.config import AppConfig
from hidro.drivers.base import LatestReadings
from hidro.drivers.mock.ds18b20 import MockDS18B20Sensor
from hidro.drivers.mock.float_switch import MockFloatSwitchSensor
from hidro.drivers.mock.flow_hall import MockFlowHallSensor
from hidro.drivers.mock.htu21d import MockHTU21DSensor
from hidro.drivers.mock.ph_analog import MockPHAnalogSensor
from hidro.drivers.mock.relay import MockRelay
from hidro.drivers.mock.tds_analog import MockTDSAnalogSensor
from hidro.drivers.registry import (
    build_all,
    build_outputs,
    build_sensors,
    describe_driver,
    flow_total_path,
    load_flow_total,
)
from hidro.drivers.runtime import RuntimeContext


def full_config(**overrides) -> AppConfig:
    raw = copy.deepcopy(base_config_dict())
    raw["sensors"].insert(1, {"id": "air", "type": "htu21d"})
    raw["sensors"].insert(2, {"id": "tank", "type": "float_switch", "gpio": 5})
    raw["outputs"][1]["ml_per_min"] = 10.0
    raw["outputs"].extend(
        [
            {"id": "pump_nutrient_a", "type": "peristaltic", "gpio": 16, "ml_per_min": 12},
            {"id": "pump_nutrient_b", "type": "peristaltic", "gpio": 26, "ml_per_min": 12},
        ]
    )
    raw.update(overrides)
    return AppConfig.model_validate(raw)


def context(tmp_path: Path, mode: str = "mock") -> RuntimeContext:
    return RuntimeContext(
        mode,
        tmp_path,
        CalibrationStore(tmp_path / "calibration.yaml"),
        LatestReadings(),
    )


def test_build_all_mock_drivers(tmp_path: Path) -> None:
    config = full_config()
    ctx = context(tmp_path)
    outputs, sensors = build_all(config, ctx)

    assert set(outputs) == {
        "tds_power",
        "pump_ph_down",
        "pump_nutrient_a",
        "pump_nutrient_b",
    }
    assert isinstance(outputs["tds_power"], MockRelay)
    assert outputs["pump_ph_down"].ml_per_min == 10.0
    assert ctx.outputs is outputs

    by_id = {sensor.id: sensor for sensor in sensors}
    assert isinstance(by_id["water_temp_1"], MockDS18B20Sensor)
    assert isinstance(by_id["air"], MockHTU21DSensor)
    assert isinstance(by_id["tank"], MockFloatSwitchSensor)
    assert isinstance(by_id["flow"], MockFlowHallSensor)
    assert isinstance(by_id["ph"], MockPHAnalogSensor)
    assert isinstance(by_id["ec"], MockTDSAnalogSensor)
    assert by_id["ec"].power_switch is outputs["tds_power"]


def test_tds_mock_uses_declared_power_switch(tmp_path: Path) -> None:
    config = full_config()
    ctx = context(tmp_path)
    outputs, sensors = build_all(config, ctx)
    ec = next(sensor for sensor in sensors if sensor.id == "ec")
    readings = ec.read()
    assert len(readings) == 2
    relay = outputs["tds_power"]
    assert isinstance(relay, MockRelay)
    assert relay.is_on is False


def test_pump_calibration_is_used_when_config_has_no_value(tmp_path: Path) -> None:
    raw = copy.deepcopy(base_config_dict())
    raw["outputs"][1]["ml_per_min"] = None
    config = AppConfig.model_validate(raw)
    ctx = context(tmp_path)
    ctx.calibration.save_pump("pump_ph_down", 8.5)

    outputs = build_outputs(config, ctx)
    assert outputs["pump_ph_down"].ml_per_min == pytest.approx(8.5)


def test_pump_calibration_overrides_config_value(tmp_path: Path) -> None:
    raw = copy.deepcopy(base_config_dict())
    raw["outputs"][1]["ml_per_min"] = 12.0
    config = AppConfig.model_validate(raw)
    ctx = context(tmp_path)
    ctx.calibration.save_pump("pump_ph_down", 8.5)

    outputs = build_outputs(config, ctx)
    assert outputs["pump_ph_down"].ml_per_min == pytest.approx(8.5)


def test_flow_calibration_overrides_config(tmp_path: Path) -> None:
    config = full_config()
    ctx = context(tmp_path)
    ctx.calibration.save_flow(pulses_per_liter=5000)
    _, sensors = build_all(config, ctx)
    flow = next(sensor for sensor in sensors if sensor.id == "flow")
    assert flow.counter.pulses_per_liter == 5000


def test_hardware_flow_disables_debounce(tmp_path: Path) -> None:
    class FakeFlowDevice:
        when_activated = None

        def close(self) -> None:
            pass

    class FakeContext:
        mode = "hardware"
        data_dir = tmp_path

        def __init__(self) -> None:
            self.calibration = CalibrationStore(tmp_path / "calibration.yaml")
            self.calls: list[tuple[int, bool, float | None]] = []

        def gpio_input(
            self, gpio: int, *, pull_up: bool, bounce_time: float | None = 0.02
        ) -> FakeFlowDevice:
            self.calls.append((gpio, pull_up, bounce_time))
            return FakeFlowDevice()

    config = AppConfig.model_validate(
        {
            "mode": "hardware",
            "sensors": [{"id": "flow", "type": "flow_hall", "gpio": 27}],
            "outputs": [],
        }
    )
    ctx = FakeContext()

    sensors = build_sensors(config, ctx)

    assert [sensor.id for sensor in sensors] == ["flow"]
    assert ctx.calls == [(27, False, None)]


def test_flow_total_is_loaded_from_data_dir(tmp_path: Path) -> None:
    config = full_config()
    path = flow_total_path(tmp_path, "flow")
    path.write_text(json.dumps({"total_l": 12.25}), encoding="utf-8")

    ctx = context(tmp_path)
    _, sensors = build_all(config, ctx)
    flow = next(sensor for sensor in sensors if sensor.id == "flow")
    assert flow.counter.total_l == pytest.approx(12.25)


def test_mock_flow_stop_environment_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HIDRO_MOCK_FLOW_STOP_AFTER_S", "15")
    _, sensors = build_all(full_config(), context(tmp_path))
    flow = next(sensor for sensor in sensors if sensor.id == "flow")
    assert flow.stop_after_s == 15

    monkeypatch.setenv("HIDRO_MOCK_FLOW_STOP_AFTER_S", "no-numero")
    _, sensors = build_all(full_config(), context(tmp_path))
    flow = next(sensor for sensor in sensors if sensor.id == "flow")
    assert flow.stop_after_s is None


def test_load_flow_total(tmp_path: Path) -> None:
    path = tmp_path / "total.json"
    assert load_flow_total(path) == 0.0

    path.write_text(json.dumps({"total_l": 2.5}), encoding="utf-8")
    assert load_flow_total(path) == 2.5

    path.write_text("no json", encoding="utf-8")
    assert load_flow_total(path) == 0.0


def test_describe_driver() -> None:
    relay = MockRelay("tds_power")
    assert describe_driver(relay) == "MockRelay(tds_power)"
