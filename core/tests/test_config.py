from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from conftest import base_config_dict, make_config
from pydantic import ValidationError

from hidro.config import (
    AppConfig,
    ConfigError,
    format_validation_error,
    load_config,
    measurement_names_for,
)


def write_config(path: Path, raw: dict) -> Path:
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def test_load_config_and_mode_override(tmp_path: Path) -> None:
    raw = base_config_dict()
    raw["mode"] = "hardware"
    path = write_config(tmp_path / "hidro.yaml", raw)

    config = load_config(path)
    assert config.mode == "hardware"
    assert config.read_interval_s == 30
    assert config.sensor("ph") is not None

    override = load_config(path, "mock")
    assert override.mode == "mock"


def test_missing_file_is_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="no existe"):
        load_config(tmp_path / "no-existe.yaml")


def test_invalid_yaml_is_config_error(tmp_path: Path) -> None:
    path = tmp_path / "hidro.yaml"
    path.write_text("sensores: [", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML inválido"):
        load_config(path)


def test_yaml_root_must_be_mapping(tmp_path: Path) -> None:
    path = tmp_path / "hidro.yaml"
    path.write_text("- uno\n- dos\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapa YAML"):
        load_config(path)


def test_validation_error_lists_paths(tmp_path: Path) -> None:
    raw = base_config_dict()
    raw["read_interval_s"] = 1
    path = write_config(tmp_path / "hidro.yaml", raw)
    with pytest.raises(ConfigError) as excinfo:
        load_config(path)
    assert "read_interval_s" in str(excinfo.value)


def test_format_validation_error() -> None:
    raw = base_config_dict()
    raw["read_interval_s"] = 1
    with pytest.raises(Exception) as excinfo:
        AppConfig.model_validate(raw)
    message = format_validation_error(excinfo.value)  # type: ignore[arg-type]
    assert message.startswith("configuración inválida")
    assert "read_interval_s" in message


def test_duplicate_ids_are_rejected() -> None:
    raw = base_config_dict()
    raw["outputs"][0]["id"] = "flow"
    with pytest.raises(Exception, match="ids duplicados"):
        AppConfig.model_validate(raw)


def test_gpio_conflicts_are_rejected() -> None:
    raw = base_config_dict()
    raw["outputs"][1]["gpio"] = 27
    with pytest.raises(Exception, match="GPIO 27"):
        AppConfig.model_validate(raw)


def test_temperature_source_must_exist() -> None:
    raw = base_config_dict()
    raw["sensors"][2]["temperature_from"] = "no_existe"
    with pytest.raises(Exception, match="sensor inexistente"):
        AppConfig.model_validate(raw)


def test_temperature_source_must_be_ds18b20() -> None:
    raw = base_config_dict()
    raw["sensors"][2]["temperature_from"] = "flow"
    with pytest.raises(Exception, match="debe ser un ds18b20"):
        AppConfig.model_validate(raw)


def test_temperature_source_must_be_read_first() -> None:
    raw = base_config_dict()
    ph = raw["sensors"].pop(2)
    raw["sensors"].insert(0, ph)
    with pytest.raises(Exception, match="leerse antes"):
        AppConfig.model_validate(raw)


def test_tds_power_switch_must_exist() -> None:
    raw = base_config_dict()
    raw["sensors"][3]["power_switch"] = "no_existe"
    with pytest.raises(Exception, match="salida inexistente"):
        AppConfig.model_validate(raw)


def test_tds_power_switch_must_be_a_relay() -> None:
    raw = base_config_dict()
    raw["sensors"][3]["power_switch"] = "pump_ph_down"
    with pytest.raises(Exception, match="debe ser un relé"):
        AppConfig.model_validate(raw)


def test_ph_must_be_declared_before_tds() -> None:
    raw = base_config_dict()
    raw["sensors"][2], raw["sensors"][3] = raw["sensors"][3], raw["sensors"][2]
    with pytest.raises(Exception, match="pH debe declararse antes"):
        AppConfig.model_validate(raw)


def test_daily_limit_requires_peristaltic_pump() -> None:
    raw = base_config_dict()
    raw["dosing"]["limits"]["max_ml_per_day"] = {"tds_power": 10}
    with pytest.raises(Exception, match="no es una bomba peristáltica"):
        AppConfig.model_validate(raw)


def test_daily_limit_must_be_positive() -> None:
    raw = base_config_dict()
    raw["dosing"]["limits"]["max_ml_per_day"] = {"pump_ph_down": 0}
    with pytest.raises(Exception, match="finito y positivo"):
        AppConfig.model_validate(raw)


def test_duplicate_pump_roles_are_rejected() -> None:
    raw = base_config_dict()
    raw["outputs"][1]["role"] = "ph_down"
    raw["outputs"].append(
        {
            "id": "pump_ph_down_2",
            "type": "peristaltic",
            "role": "ph_down",
            "gpio": 5,
        }
    )
    with pytest.raises(Exception, match="roles de bomba duplicados"):
        AppConfig.model_validate(raw)


def test_conventional_pump_id_cannot_take_another_role() -> None:
    raw = base_config_dict()
    raw["outputs"][1]["id"] = "pump_ph_up"
    raw["outputs"][1]["role"] = "ph_down"
    raw["dosing"]["limits"]["max_ml_per_day"] = {}
    with pytest.raises(Exception, match="cambia el id o el rol"):
        AppConfig.model_validate(raw)


def test_validator_rejects_invalid_low_when() -> None:
    raw = base_config_dict()
    raw["sensors"].append(
        {"id": "tank", "type": "float_switch", "gpio": 5, "low_when": 2}
    )
    with pytest.raises(Exception, match="low_when"):
        AppConfig.model_validate(raw)


def test_validator_rejects_unknown_adc_gain() -> None:
    raw = base_config_dict()
    raw["sensors"][2]["adc"]["gain_v"] = 1.5
    with pytest.raises(Exception, match="ganancia no soportada"):
        AppConfig.model_validate(raw)


def test_extra_fields_are_rejected() -> None:
    raw = base_config_dict()
    raw["campo_extra"] = True
    with pytest.raises(Exception, match="Extra inputs"):
        AppConfig.model_validate(raw)


def test_helpers_and_measurement_names() -> None:
    raw = base_config_dict()
    raw["sensors"].insert(1, {"id": "air", "type": "htu21d"})
    raw["sensors"].insert(2, {"id": "tank", "type": "float_switch", "gpio": 5})
    config = AppConfig.model_validate(raw)

    assert config.sensor("ph") is not None
    assert config.sensor("no_existe") is None
    assert config.output("tds_power") is not None
    assert config.output("no_existe") is None
    assert [sensor.id for sensor in config.sensors_of("ds18b20")] == ["water_temp_1"]

    expected = [
        "water_temp_1",
        "air_temp",
        "air_humidity",
        "tank_low",
        "flow",
        "ph",
        "ec",
        "ec_ppm",
    ]
    assert config.measurement_names() == expected

    for sensor in config.sensors:
        assert measurement_names_for(sensor)


def test_measurement_names_for_unknown_type() -> None:
    class Raro:
        pass

    assert measurement_names_for(Raro()) == []  # type: ignore[arg-type]


def test_models_are_frozen() -> None:
    config = make_config()
    with pytest.raises(ValidationError):
        config.mode = "hardware"  # type: ignore[misc]
