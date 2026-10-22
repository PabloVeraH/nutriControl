"""Calibraciones persistentes en /data/calibration.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hidro.logging_setup import get_logger

logger = get_logger("calibration")

PH_CALIBRATION_MAX_AGE = timedelta(days=30)
MIN_ABS_SLOPE = 0.02
MAX_ABS_SLOPE = 0.60


@dataclass(frozen=True, slots=True)
class PHCalibration:
    v1: float
    ph1: float
    v2: float
    ph2: float
    t_cal: float
    calibrated_at: datetime

    @property
    def slope(self) -> float:
        return (self.v1 - self.v2) / (self.ph1 - self.ph2)

    @property
    def v7(self) -> float:
        return self.v1 + self.slope * (7.0 - self.ph1)

    def slope_at(self, temp_c: float) -> float:
        """Compensación de Nernst: la pendiente escala con la temperatura absoluta."""
        return self.slope * (temp_c + 273.15) / (self.t_cal + 273.15)

    def ph_from_voltage(self, voltage: float, temp_c: float | None) -> float:
        slope = self.slope_at(temp_c) if temp_c is not None else self.slope
        return 7.0 + (voltage - self.v7) / slope

    def slope_is_reasonable(self) -> bool:
        return MIN_ABS_SLOPE <= abs(self.slope) <= MAX_ABS_SLOPE

    def is_old(self, now: datetime | None = None) -> bool:
        reference = now or datetime.now(UTC)
        return reference - self.calibrated_at > PH_CALIBRATION_MAX_AGE

    def age_days(self, now: datetime | None = None) -> float:
        reference = now or datetime.now(UTC)
        return (reference - self.calibrated_at).total_seconds() / 86400.0


@dataclass(frozen=True, slots=True)
class ECCalibration:
    k: float
    standard_us_cm: float | None
    voltage: float | None
    t_c: float | None
    calibrated_at: datetime


@dataclass(frozen=True, slots=True)
class FlowCalibration:
    pulses_per_liter: float
    calibrated_at: datetime


@dataclass(frozen=True, slots=True)
class PumpCalibration:
    ml_per_min: float
    calibrated_at: datetime


@dataclass(frozen=True, slots=True)
class PHPendingPoint:
    ph: float
    voltage: float
    t_c: float
    measured_at: datetime


class _PHEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    v1: float
    ph1: float
    v2: float
    ph2: float
    t_cal: float
    calibrated_at: datetime


class _ECEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    k: float
    standard_us_cm: float | None = None
    voltage: float | None = None
    t_c: float | None = None
    calibrated_at: datetime


class _FlowEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    pulses_per_liter: float
    calibrated_at: datetime


class _PumpEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ml_per_min: float
    calibrated_at: datetime


class _PendingEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ph: float
    voltage: float
    t_c: float
    measured_at: datetime


class _CalibrationFile(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ph: _PHEntry | None = None
    ec: _ECEntry | None = None
    flow: _FlowEntry | None = None
    pumps: dict[str, _PumpEntry] = Field(default_factory=dict)
    ph_pending: _PendingEntry | None = None


class CalibrationStore:
    """Lee y escribe calibraciones de forma atómica."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    # ------------------------------------------------------------------ lectura

    def _load(self, *, strict: bool = False) -> _CalibrationFile:
        """Con `strict` un archivo inválido es un error: guardar sobre él borraría
        las demás calibraciones. Al leer se usan los valores por defecto."""
        if not self.path.exists():
            return _CalibrationFile()
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
            return _CalibrationFile.model_validate(raw)
        except (OSError, yaml.YAMLError, ValidationError) as exc:
            message = f"{self.path} no es válido; corrígelo o respáldalo y bórralo ({exc})"
            if strict:
                raise ValueError(message) from exc
            logger.warning("%s; se ignoran las calibraciones guardadas", message)
            return _CalibrationFile()

    def ph(self) -> PHCalibration | None:
        entry = self._load().ph
        if entry is None:
            return None
        return PHCalibration(
            v1=entry.v1,
            ph1=entry.ph1,
            v2=entry.v2,
            ph2=entry.ph2,
            t_cal=entry.t_cal,
            calibrated_at=entry.calibrated_at,
        )

    def ec(self) -> ECCalibration | None:
        entry = self._load().ec
        if entry is None:
            return None
        return ECCalibration(
            k=entry.k,
            standard_us_cm=entry.standard_us_cm,
            voltage=entry.voltage,
            t_c=entry.t_c,
            calibrated_at=entry.calibrated_at,
        )

    def flow(self) -> FlowCalibration | None:
        entry = self._load().flow
        if entry is None:
            return None
        return FlowCalibration(
            pulses_per_liter=entry.pulses_per_liter,
            calibrated_at=entry.calibrated_at,
        )

    def pump_ml_per_min(self, pump_id: str) -> float | None:
        entry = self._load().pumps.get(pump_id)
        return entry.ml_per_min if entry else None

    def ph_pending(self) -> PHPendingPoint | None:
        entry = self._load().ph_pending
        if entry is None:
            return None
        return PHPendingPoint(
            ph=entry.ph,
            voltage=entry.voltage,
            t_c=entry.t_c,
            measured_at=entry.measured_at,
        )

    # ------------------------------------------------------------------ escritura

    def save_ph(
        self,
        *,
        v1: float,
        ph1: float,
        v2: float,
        ph2: float,
        t_cal: float,
        calibrated_at: datetime | None = None,
    ) -> PHCalibration:
        calibration = PHCalibration(
            v1=v1,
            ph1=ph1,
            v2=v2,
            ph2=ph2,
            t_cal=t_cal,
            calibrated_at=calibrated_at or datetime.now(UTC),
        )
        if not calibration.slope_is_reasonable():
            raise ValueError(
                f"pendiente fuera de rango: {calibration.slope:.3f} V/pH "
                f"(esperado entre {MIN_ABS_SLOPE} y {MAX_ABS_SLOPE})"
            )
        data = self._load(strict=True)
        data.ph = _PHEntry(
            v1=v1,
            ph1=ph1,
            v2=v2,
            ph2=ph2,
            t_cal=t_cal,
            calibrated_at=calibration.calibrated_at,
        )
        data.ph_pending = None
        self._save(data)
        return calibration

    def save_ec(
        self,
        *,
        k: float,
        standard_us_cm: float,
        voltage: float,
        t_c: float,
        calibrated_at: datetime | None = None,
    ) -> ECCalibration:
        if k <= 0:
            raise ValueError("el factor k debe ser positivo")
        entry = ECCalibration(
            k=k,
            standard_us_cm=standard_us_cm,
            voltage=voltage,
            t_c=t_c,
            calibrated_at=calibrated_at or datetime.now(UTC),
        )
        data = self._load(strict=True)
        data.ec = _ECEntry(
            k=entry.k,
            standard_us_cm=entry.standard_us_cm,
            voltage=entry.voltage,
            t_c=entry.t_c,
            calibrated_at=entry.calibrated_at,
        )
        self._save(data)
        return entry

    def save_flow(
        self, *, pulses_per_liter: float, calibrated_at: datetime | None = None
    ) -> FlowCalibration:
        if pulses_per_liter <= 0:
            raise ValueError("pulsos por litro debe ser positivo")
        entry = FlowCalibration(
            pulses_per_liter=pulses_per_liter,
            calibrated_at=calibrated_at or datetime.now(UTC),
        )
        data = self._load(strict=True)
        data.flow = _FlowEntry(
            pulses_per_liter=entry.pulses_per_liter,
            calibrated_at=entry.calibrated_at,
        )
        self._save(data)
        return entry

    def save_pump(self, pump_id: str, ml_per_min: float) -> PumpCalibration:
        if ml_per_min <= 0:
            raise ValueError("ml/min debe ser positivo")
        entry = PumpCalibration(ml_per_min=ml_per_min, calibrated_at=datetime.now(UTC))
        data = self._load(strict=True)
        data.pumps[pump_id] = _PumpEntry(
            ml_per_min=entry.ml_per_min,
            calibrated_at=entry.calibrated_at,
        )
        self._save(data)
        return entry

    def set_ph_pending(self, ph: float, voltage: float, t_c: float) -> PHPendingPoint:
        entry = _PendingEntry(
            ph=ph,
            voltage=voltage,
            t_c=t_c,
            measured_at=datetime.now(UTC),
        )
        data = self._load(strict=True)
        data.ph_pending = entry
        self._save(data)
        return PHPendingPoint(
            ph=entry.ph,
            voltage=entry.voltage,
            t_c=entry.t_c,
            measured_at=entry.measured_at,
        )

    def clear_ph_pending(self) -> None:
        data = self._load(strict=True)
        data.ph_pending = None
        self._save(data)

    # ------------------------------------------------------------------ utilidades

    def _save(self, data: _CalibrationFile) -> None:
        payload: dict[str, Any] = {}
        if data.ph is not None:
            payload["ph"] = data.ph.model_dump(mode="json")
        if data.ec is not None:
            payload["ec"] = data.ec.model_dump(mode="json")
        if data.flow is not None:
            payload["flow"] = data.flow.model_dump(mode="json")
        if data.pumps:
            payload["pumps"] = {
                pump_id: entry.model_dump(mode="json") for pump_id, entry in data.pumps.items()
            }
        if data.ph_pending is not None:
            payload["ph_pending"] = data.ph_pending.model_dump(mode="json")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
        os.replace(tmp, self.path)
