"""Construcción de drivers a partir de la configuración."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from hidro.config import (
    AppConfig,
    DS18B20Config,
    FloatSwitchConfig,
    FlowHallConfig,
    HTU21DConfig,
    PeristalticConfig,
    PHAnalogConfig,
    RelayConfig,
    TDSAnalogConfig,
)
from hidro.drivers.ads1115 import ADS1115
from hidro.drivers.base import DriverError, Sensor, Switch
from hidro.drivers.ds18b20 import DS18B20Sensor
from hidro.drivers.float_switch import FloatSwitchSensor
from hidro.drivers.flow_hall import FlowCounter, FlowHallSensor
from hidro.drivers.htu21d import HTU21DSensor
from hidro.drivers.mock.ds18b20 import MockDS18B20Sensor
from hidro.drivers.mock.float_switch import MockFloatSwitchSensor
from hidro.drivers.mock.flow_hall import MockFlowHallSensor
from hidro.drivers.mock.htu21d import MockHTU21DSensor
from hidro.drivers.mock.peristaltic import build_mock_pump
from hidro.drivers.mock.ph_analog import MockPHAnalogSensor
from hidro.drivers.mock.relay import MockRelay
from hidro.drivers.mock.tds_analog import MockTDSAnalogSensor
from hidro.drivers.peristaltic import PeristalticPump
from hidro.drivers.ph_analog import PHAnalogSensor
from hidro.drivers.relay import RelayOutput
from hidro.drivers.runtime import RuntimeContext
from hidro.drivers.tds_analog import TDSAnalogSensor


def flow_total_path(data_dir: Path, sensor_id: str) -> Path:
    return data_dir / f"flow_total_{sensor_id}.json"


def load_flow_total(path: Path) -> float:
    if not path.exists():
        return 0.0
    try:
        return float(json.loads(path.read_text(encoding="utf-8")).get("total_l", 0.0))
    except (OSError, ValueError):
        return 0.0


def build_outputs(config: AppConfig, ctx: RuntimeContext) -> dict[str, Switch]:
    outputs: dict[str, Switch] = {}
    for cfg in config.outputs:
        if isinstance(cfg, RelayConfig):
            if ctx.mode == "mock":
                device: Switch = MockRelay(cfg.id)
            else:
                device = RelayOutput(
                    cfg.id,
                    ctx.gpio_output(cfg.gpio, active_high=cfg.active_high, initial=False),
                )
            outputs[cfg.id] = device
        elif isinstance(cfg, PeristalticConfig):
            ml_per_min = cfg.ml_per_min
            if ml_per_min is None:
                ml_per_min = ctx.calibration.pump_ml_per_min(cfg.id)
            if ctx.mode == "mock":
                pump: Switch = build_mock_pump(
                    cfg.id,
                    ml_per_min=ml_per_min,
                    max_on_s=cfg.max_on_s,
                    time_scale=cfg.mock.time_scale,
                )
            else:
                relay = RelayOutput(
                    f"{cfg.id}_relay",
                    ctx.gpio_output(cfg.gpio, active_high=True, initial=False),
                )
                pump = PeristalticPump(
                    cfg.id,
                    relay,
                    ml_per_min=ml_per_min,
                    max_on_s=cfg.max_on_s,
                )
            outputs[cfg.id] = pump
    ctx.outputs = outputs
    return outputs


def build_sensors(config: AppConfig, ctx: RuntimeContext) -> list[Sensor]:
    sensors: list[Sensor] = []
    adcs: dict[int, ADS1115] = {}
    mock_flow_stop = _mock_flow_stop_override()

    for cfg in config.sensors:
        if isinstance(cfg, DS18B20Config):
            if ctx.mode == "mock":
                sensors.append(MockDS18B20Sensor(cfg.id, cfg.mock))
            else:
                sensors.append(
                    DS18B20Sensor(
                        cfg.id,
                        cfg.serial,
                        offset_c=cfg.offset_c,
                        base_path=cfg.base_path,
                    )
                )
        elif isinstance(cfg, HTU21DConfig):
            if ctx.mode == "mock":
                sensors.append(MockHTU21DSensor(cfg.id, cfg.mock))
            else:
                sensors.append(
                    HTU21DSensor(
                        cfg.id,
                        ctx.smbus(cfg.i2c_bus),
                        address=cfg.address,
                    )
                )
        elif isinstance(cfg, FloatSwitchConfig):
            if ctx.mode == "mock":
                sensors.append(MockFloatSwitchSensor(cfg.id, cfg.mock))
            else:
                device = ctx.gpio_input(
                    cfg.gpio,
                    pull_up=cfg.pull == "up",
                )

                def read_level(dev: Any = device) -> int:
                    return int(dev.value)

                sensors.append(
                    FloatSwitchSensor(
                        cfg.id,
                        read_level,
                        low_when=cfg.low_when,
                        debounce_s=cfg.debounce_s,
                    )
                )
        elif isinstance(cfg, FlowHallConfig):
            ppl = cfg.pulses_per_liter
            calibration = ctx.calibration.flow()
            if calibration is not None:
                ppl = calibration.pulses_per_liter
            total_path = flow_total_path(ctx.data_dir, cfg.id)
            if ctx.mode == "mock":
                sensors.append(
                    MockFlowHallSensor(
                        cfg.id,
                        cfg.mock,
                        pulses_per_liter=ppl,
                        total_path=total_path,
                        stop_after_s=mock_flow_stop,
                    )
                )
            else:
                counter = FlowCounter(ppl, total_l=load_flow_total(total_path))
                sensors.append(
                    FlowHallSensor(
                        cfg.id,
                        ctx.gpio_input(cfg.gpio, pull_up=False, bounce_time=None),
                        counter,
                        total_path,
                    )
                )
        elif isinstance(cfg, PHAnalogConfig):
            if ctx.mode == "mock":
                sensors.append(MockPHAnalogSensor(cfg.id, cfg.mock))
            else:
                adc = _get_adc(adcs, ctx, cfg.adc.address)
                sensors.append(
                    PHAnalogSensor(
                        cfg.id,
                        adc,
                        ctx.calibration,
                        ctx.latest,
                        channel=cfg.adc.channel,
                        gain_v=cfg.adc.gain_v,
                        samples=cfg.adc.samples,
                        temperature_from=cfg.temperature_from,
                    )
                )
        elif isinstance(cfg, TDSAnalogConfig):
            power_switch = ctx.outputs.get(cfg.power_switch) if cfg.power_switch else None
            if cfg.power_switch and power_switch is None:
                raise DriverError(f"{cfg.id}: no existe la salida {cfg.power_switch}")
            if ctx.mode == "mock":
                sensors.append(
                    MockTDSAnalogSensor(
                        cfg.id,
                        cfg.mock,
                        power_switch=power_switch,
                        settle_s=cfg.settle_s,
                        tds_factor=cfg.tds_factor,
                    )
                )
            else:
                adc = _get_adc(adcs, ctx, cfg.adc.address)
                sensors.append(
                    TDSAnalogSensor(
                        cfg.id,
                        adc,
                        ctx.calibration,
                        ctx.latest,
                        channel=cfg.adc.channel,
                        gain_v=cfg.adc.gain_v,
                        samples=cfg.adc.samples,
                        temperature_from=cfg.temperature_from,
                        power_switch=power_switch,
                        settle_s=cfg.settle_s,
                        tds_factor=cfg.tds_factor,
                    )
                )
    return sensors


def _get_adc(cache: dict[int, ADS1115], ctx: RuntimeContext, address: int) -> ADS1115:
    if address not in cache:
        cache[address] = ADS1115(ctx.smbus(1), address=address)
    return cache[address]


def _mock_flow_stop_override() -> float | None:
    raw = os.getenv("HIDRO_MOCK_FLOW_STOP_AFTER_S", "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def build_all(config: AppConfig, ctx: RuntimeContext) -> tuple[dict[str, Switch], list[Sensor]]:
    outputs = build_outputs(config, ctx)
    sensors = build_sensors(config, ctx)
    return outputs, sensors


def describe_driver(driver: Any) -> str:
    return f"{driver.__class__.__name__}({getattr(driver, 'id', '?')})"
