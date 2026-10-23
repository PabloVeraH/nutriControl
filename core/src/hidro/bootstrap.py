"""Construcción del grafo de objetos de la aplicación."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from hidro.alerts.manager import AlertManager
from hidro.alerts.telegram import TelegramNotifier
from hidro.calibration import CalibrationStore
from hidro.config import AppConfig
from hidro.control.dosing import DosingController
from hidro.control.monitor import Monitor
from hidro.control.safety import SafetyGuard
from hidro.drivers.base import LatestReadings, Sensor, Switch
from hidro.drivers.registry import build_all
from hidro.drivers.runtime import RuntimeContext
from hidro.logging_setup import get_logger
from hidro.settings import Settings
from hidro.storage.influx import InfluxStorage

logger = get_logger("bootstrap")


@dataclass
class App:
    settings: Settings
    config: AppConfig
    calibration: CalibrationStore
    latest: LatestReadings
    runtime: RuntimeContext
    outputs: dict[str, Switch]
    sensors: list[Sensor]
    notifier: TelegramNotifier
    alerts: AlertManager
    storage: InfluxStorage
    safety: SafetyGuard
    dosing: DosingController
    monitor: Monitor

    def close(self) -> None:
        for output in self.outputs.values():
            try:
                output.close()
            except Exception:
                logger.warning("error al cerrar la salida %s", getattr(output, "id", "?"), exc_info=True)
        for sensor in self.sensors:
            try:
                sensor.close()
            except Exception:
                logger.warning("error al cerrar el sensor %s", getattr(sensor, "id", "?"), exc_info=True)
        self.runtime.close()
        self.storage.close()
        self.notifier.close()
        self.latest.close()


def build_app(settings: Settings, config: AppConfig) -> App:
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    latest = LatestReadings()
    calibration = CalibrationStore(settings.data_dir / "calibration.yaml")
    runtime = RuntimeContext(config.mode, settings.data_dir, calibration, latest)

    outputs, sensors = build_all(config, runtime)

    notifier = TelegramNotifier(
        settings.telegram.token,
        settings.telegram.chat_id,
        enabled=settings.telegram.enabled and config.alerts.telegram.enabled,
    )
    alerts = AlertManager(
        notifier,
        repeat_critical=timedelta(minutes=config.alerts.telegram.repeat_critical_every_min),
        silence_path=settings.data_dir / "silence.json",
    )
    storage = InfluxStorage(settings.influx)
    safety = SafetyGuard(settings.data_dir / "safety.json", config.dosing.limits)
    dosing = DosingController(config, outputs, latest, safety)
    monitor = Monitor(config, latest)

    logger.info(
        "aplicación construida: modo=%s, sensores=%s, salidas=%s, dosificación=%s",
        config.mode,
        len(sensors),
        len(outputs),
        "activada" if config.dosing.enabled else "desactivada",
    )

    return App(
        settings=settings,
        config=config,
        calibration=calibration,
        latest=latest,
        runtime=runtime,
        outputs=outputs,
        sensors=sensors,
        notifier=notifier,
        alerts=alerts,
        storage=storage,
        safety=safety,
        dosing=dosing,
        monitor=monitor,
    )
