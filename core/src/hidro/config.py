"""Carga y validación de la configuración (config/hidro.yaml)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

GAINS = (4.096, 2.048, 1.024, 0.512, 0.256)
ID_PATTERN = r"^[a-z][a-z0-9_]*$"


class ConfigError(ValueError):
    """La configuración no es válida."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- mock


class MockSensorParams(StrictModel):
    scenario: Literal["normal", "invalid", "disconnected"] = "normal"


class MockDS18B20Params(MockSensorParams):
    base_c: float = 20.0
    noise_c: float = 0.05
    drift_c_per_h: float = 0.0


class MockHTU21DParams(MockSensorParams):
    temp_c: float = 22.0
    humidity: float = 55.0
    noise: float = 0.2


class MockFloatParams(MockSensorParams):
    low: bool = False
    low_after_s: float | None = None


class MockFlowParams(MockSensorParams):
    l_min: float = 1.4
    noise_l_min: float = 0.03
    stop_after_s: float | None = None


class MockPHParams(MockSensorParams):
    ph: float = 5.9
    noise: float = 0.02
    drift_per_h: float = 0.0


class MockTDSParams(MockSensorParams):
    ec: float = 1150.0
    noise: float = 8.0
    drift_per_h: float = 0.0


class MockOutputParams(StrictModel):
    time_scale: float = Field(default=0.05, gt=0)


# --------------------------------------------------------------------------- sensores


class SensorBase(StrictModel):
    id: str = Field(pattern=ID_PATTERN)
    type: str


class DS18B20Config(SensorBase):
    type: Literal["ds18b20"]
    serial: str = ""
    offset_c: float = 0.0
    base_path: str = "/sys/bus/w1/devices"
    mock: MockDS18B20Params = Field(default_factory=MockDS18B20Params)


class HTU21DConfig(SensorBase):
    type: Literal["htu21d"]
    i2c_bus: int = Field(default=1, ge=0)
    address: int = Field(default=0x40, ge=0x03, le=0x77)
    mock: MockHTU21DParams = Field(default_factory=MockHTU21DParams)


class FloatSwitchConfig(SensorBase):
    type: Literal["float_switch"]
    gpio: int = Field(ge=0, le=27)
    low_when: int = 0
    debounce_s: float = Field(default=10.0, ge=0)
    pull: Literal["up", "down", "none"] = "down"
    mock: MockFloatParams = Field(default_factory=MockFloatParams)

    @field_validator("low_when")
    @classmethod
    def _low_when_binary(cls, value: int) -> int:
        if value not in (0, 1):
            raise ValueError("low_when debe ser 0 o 1")
        return value


class FlowHallConfig(SensorBase):
    type: Literal["flow_hall"]
    gpio: int = Field(ge=0, le=27)
    pulses_per_liter: float = Field(default=5880.0, gt=0)
    mock: MockFlowParams = Field(default_factory=MockFlowParams)


class ADCConfig(StrictModel):
    address: int = Field(default=0x48, ge=0x48, le=0x4B)
    channel: int = Field(ge=0, le=3)
    gain_v: float = 4.096
    samples: int = Field(default=10, ge=1, le=50)

    @field_validator("gain_v")
    @classmethod
    def _known_gain(cls, value: float) -> float:
        if value not in GAINS:
            raise ValueError(f"ganancia no soportada: {value} (usar {GAINS})")
        return value


class PHAnalogConfig(SensorBase):
    type: Literal["ph_analog"]
    adc: ADCConfig
    temperature_from: str | None = None
    mock: MockPHParams = Field(default_factory=MockPHParams)


class TDSAnalogConfig(SensorBase):
    type: Literal["tds_analog"]
    adc: ADCConfig
    temperature_from: str | None = None
    power_switch: str | None = None
    settle_s: float = Field(default=2.0, ge=0)
    tds_factor: float = Field(default=0.5, gt=0)
    mock: MockTDSParams = Field(default_factory=MockTDSParams)


SensorConfig = Annotated[
    DS18B20Config | HTU21DConfig | FloatSwitchConfig | FlowHallConfig | PHAnalogConfig
    | TDSAnalogConfig,
    Field(discriminator="type"),
]


# --------------------------------------------------------------------------- salidas


class OutputBase(StrictModel):
    id: str = Field(pattern=ID_PATTERN)
    type: str


class RelayConfig(OutputBase):
    type: Literal["relay"]
    gpio: int = Field(ge=0, le=27)
    active_high: bool = True


PumpRole = Literal["ph_down", "ph_up", "nutrient_a", "nutrient_b"]

# Id convencional de cada rol: las reglas lo usan si ninguna bomba declara el rol.
PUMP_ROLE_IDS: dict[PumpRole, str] = {
    "ph_down": "pump_ph_down",
    "ph_up": "pump_ph_up",
    "nutrient_a": "pump_nutrient_a",
    "nutrient_b": "pump_nutrient_b",
}


class PeristalticConfig(OutputBase):
    type: Literal["peristaltic"]
    gpio: int = Field(ge=0, le=27)
    # El rol permite que las reglas encuentren la bomba aunque su id cambie.
    # Si se omite, se usa el id convencional (pump_ph_down, etc.).
    role: PumpRole | None = None
    ml_per_min: float | None = Field(default=None, gt=0)
    max_on_s: float = Field(default=30.0, gt=0)
    mock: MockOutputParams = Field(default_factory=MockOutputParams)


OutputConfig = Annotated[RelayConfig | PeristalticConfig, Field(discriminator="type")]


# --------------------------------------------------------------------------- rangos


class PHRange(StrictModel):
    low: float = 5.5
    high: float = 6.2
    danger_low: float = 5.0
    danger_high: float = 7.0


class ECRange(StrictModel):
    low: float = 1000.0
    high: float = 1300.0


class WaterTempRange(StrictModel):
    high_alert: float = 24.0


class AirTempRange(StrictModel):
    low_alert: float = 5.0
    high_alert: float = 35.0


class FlowRange(StrictModel):
    min_l_min: float = 0.3
    alarm_after_s: float = 60.0


class Ranges(StrictModel):
    ph: PHRange = Field(default_factory=PHRange)
    ec: ECRange = Field(default_factory=ECRange)
    water_temp: WaterTempRange = Field(default_factory=WaterTempRange)
    air_temp: AirTempRange = Field(default_factory=AirTempRange)
    flow: FlowRange = Field(default_factory=FlowRange)


class MonitoringConfig(StrictModel):
    stale_after_s: float = Field(default=300.0, gt=0)
    ph_sustained_s: float = Field(default=1800.0, ge=0)
    ec_low_sustained_s: float = Field(default=1800.0, ge=0)


# --------------------------------------------------------------------------- dosificación


class DosingLimits(StrictModel):
    max_doses_per_hour: int = Field(default=3, ge=0)
    max_ml_per_day: dict[str, float] = Field(default_factory=dict)


class DosingConfig(StrictModel):
    enabled: bool = False
    dry_run: bool = True
    check_interval_s: float = Field(default=300.0, gt=0)
    mix_wait_s: float = Field(default=600.0, ge=0)
    ph_down_ml: float = Field(default=1.0, gt=0)
    ph_up_ml: float = Field(default=1.0, gt=0)
    nutrient_ml: float = Field(default=3.0, gt=0)
    limits: DosingLimits = Field(default_factory=DosingLimits)


class TelegramConfig(StrictModel):
    enabled: bool = True
    repeat_critical_every_min: float = Field(default=15.0, gt=0)


class AlertsConfig(StrictModel):
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    daily_summary_at: str = "09:00"

    @field_validator("daily_summary_at")
    @classmethod
    def _valid_summary_time(cls, value: str) -> str:
        raw = value.strip()
        parts = raw.split(":")
        if len(parts) != 2:
            raise ValueError("daily_summary_at debe tener el formato HH:MM")
        try:
            hour, minute = int(parts[0]), int(parts[1])
        except ValueError as exc:
            raise ValueError("daily_summary_at debe tener el formato HH:MM") from exc
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("daily_summary_at debe ser una hora entre 00:00 y 23:59")
        return f"{hour:02d}:{minute:02d}"


# --------------------------------------------------------------------------- raíz


class AppConfig(StrictModel):
    mode: Literal["hardware", "mock"] = "hardware"
    read_interval_s: float = Field(default=30.0, ge=5)
    sensors: list[SensorConfig] = Field(min_length=1)
    outputs: list[OutputConfig] = Field(default_factory=list)
    ranges: Ranges = Field(default_factory=Ranges)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    dosing: DosingConfig = Field(default_factory=DosingConfig)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)

    @model_validator(mode="after")
    def _validate_relationships(self) -> AppConfig:
        sensor_ids = [sensor.id for sensor in self.sensors]
        output_ids = [output.id for output in self.outputs]
        duplicates = {item for item in sensor_ids + output_ids if (sensor_ids + output_ids).count(item) > 1}
        if duplicates:
            raise ValueError(f"ids duplicados: {sorted(duplicates)}")

        gpio_owners: dict[int, str] = {}
        for sensor in self.sensors:
            if isinstance(sensor, (FloatSwitchConfig, FlowHallConfig)):
                self._claim_gpio(gpio_owners, sensor.gpio, sensor.id)
        for output_cfg in self.outputs:
            self._claim_gpio(gpio_owners, output_cfg.gpio, output_cfg.id)

        for sensor in self.sensors:
            if isinstance(sensor, (PHAnalogConfig, TDSAnalogConfig)) and sensor.temperature_from:
                target = self.sensor(sensor.temperature_from)
                if target is None:
                    raise ValueError(
                        f"{sensor.id}: temperature_from apunta a un sensor inexistente "
                        f"({sensor.temperature_from})"
                    )
                if not isinstance(target, DS18B20Config):
                    raise ValueError(f"{sensor.id}: temperature_from debe ser un ds18b20")
                if sensor_ids.index(target.id) > sensor_ids.index(sensor.id):
                    raise ValueError(
                        f"{sensor.id}: el sensor de temperatura debe leerse antes que {sensor.id}"
                    )

            if isinstance(sensor, TDSAnalogConfig) and sensor.power_switch:
                switch = self.output(sensor.power_switch)
                if switch is None:
                    raise ValueError(
                        f"{sensor.id}: power_switch apunta a una salida inexistente "
                        f"({sensor.power_switch})"
                    )
                if not isinstance(switch, RelayConfig):
                    raise ValueError(f"{sensor.id}: power_switch debe ser un relé")

        ph_positions = [i for i, s in enumerate(self.sensors) if isinstance(s, PHAnalogConfig)]
        tds_positions = [i for i, s in enumerate(self.sensors) if isinstance(s, TDSAnalogConfig)]
        if ph_positions and tds_positions and max(ph_positions) > min(tds_positions):
            raise ValueError(
                "el sensor de pH debe declararse antes que el de TDS "
                "(el TDS se enciende después de medir pH)"
            )

        roles = [
            output.role
            for output in self.outputs
            if isinstance(output, PeristalticConfig) and output.role is not None
        ]
        duplicate_roles = {role for role in roles if roles.count(role) > 1}
        if duplicate_roles:
            raise ValueError(f"roles de bomba duplicados: {sorted(duplicate_roles)}")
        # pump_ph_up con role: ph_down haría que la regla de "subir pH" use el ácido.
        for pump in self.outputs:
            if not isinstance(pump, PeristalticConfig) or pump.role is None:
                continue
            for role, conventional_id in PUMP_ROLE_IDS.items():
                if pump.id == conventional_id and pump.role != role:
                    raise ValueError(
                        f"{pump.id}: su id es el convencional de '{role}' pero declara "
                        f"role: {pump.role}; cambia el id o el rol"
                    )

        for pump_id, limit in self.dosing.limits.max_ml_per_day.items():
            output = self.output(pump_id)
            if not isinstance(output, PeristalticConfig):
                raise ValueError(f"max_ml_per_day: {pump_id} no es una bomba peristáltica")
            if not math.isfinite(limit) or limit <= 0:
                raise ValueError(
                    f"max_ml_per_day: el límite de {pump_id} debe ser finito y positivo"
                )

        return self

    @staticmethod
    def _claim_gpio(owners: dict[int, str], gpio: int, owner: str) -> None:
        if gpio in owners:
            raise ValueError(f"GPIO {gpio} usado por {owners[gpio]} y {owner}")
        owners[gpio] = owner

    def sensor(self, sensor_id: str) -> SensorConfig | None:
        for sensor in self.sensors:
            if sensor.id == sensor_id:
                return sensor
        return None

    def output(self, output_id: str) -> OutputConfig | None:
        for output in self.outputs:
            if output.id == output_id:
                return output
        return None

    def sensors_of(self, sensor_type: str) -> list[SensorConfig]:
        return [sensor for sensor in self.sensors if sensor.type == sensor_type]

    def measurement_names(self) -> list[str]:
        names: list[str] = []
        for sensor in self.sensors:
            names.extend(measurement_names_for(sensor))
        return names


def measurement_names_for(sensor: SensorConfig) -> list[str]:
    if isinstance(sensor, DS18B20Config):
        return [sensor.id]
    if isinstance(sensor, HTU21DConfig):
        return [f"{sensor.id}_temp", f"{sensor.id}_humidity"]
    if isinstance(sensor, FloatSwitchConfig):
        return [f"{sensor.id}_low"]
    if isinstance(sensor, FlowHallConfig):
        return [sensor.id]
    if isinstance(sensor, PHAnalogConfig):
        return [sensor.id]
    if isinstance(sensor, TDSAnalogConfig):
        return [sensor.id, f"{sensor.id}_ppm"]
    return []


def load_config(path: str | Path, mode_override: str | None = None) -> AppConfig:
    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise ConfigError(f"no existe el archivo de configuración: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML inválido en {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path}: la raíz debe ser un mapa YAML")
    if mode_override:
        raw["mode"] = mode_override
    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(format_validation_error(exc)) from exc


def format_validation_error(exc: ValidationError) -> str:
    lines = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        lines.append(f"  - {location}: {error['msg']}")
    return "configuración inválida:\n" + "\n".join(lines)
