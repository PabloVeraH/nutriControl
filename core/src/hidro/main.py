"""Arranque, ciclo principal y manejo de señales."""

from __future__ import annotations

import signal
import time
from collections.abc import Callable
from datetime import UTC, datetime

from hidro.alerts.commands import TelegramCommandBot, TelegramCommandHandler
from hidro.bootstrap import App
from hidro.config import ConfigError, load_config
from hidro.control.dosing import DosingFlags, execute_action
from hidro.drivers.base import DriverError
from hidro.heartbeat import write_heartbeat
from hidro.logging_setup import get_logger, setup_logging
from hidro.model import Measurement
from hidro.reporting import DailySummary, build_daily_summary, build_status_text
from hidro.settings import Settings

logger = get_logger("main")


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Controller:
    def __init__(
        self,
        app: App,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        wall_clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.app = app
        self._clock = clock
        self._sleep = sleep
        self._wall_clock = wall_clock
        self._started_at = self._wall_clock()
        # El mismo evento llega a las bombas: SIGTERM corta una dosis en curso
        # en vez de esperar hasta max_on_s (stop_grace_period es de 30 s).
        self._stop = app.runtime.stop_event
        self._commands = TelegramCommandBot(
            app.notifier,
            TelegramCommandHandler(
                alerts=app.alerts,
                status_provider=self._status_text,
                allowed_chat_ids=[app.notifier.chat_id],
                timezone=app.settings.timezone,
            ),
            offset_path=app.settings.data_dir / "telegram_offset.json",
        )
        self._summary = DailySummary(
            app.notifier,
            at=app.config.alerts.daily_summary_at,
            timezone=app.settings.timezone,
            state_path=app.settings.data_dir / "daily_summary.json",
        )

    # ------------------------------------------------------------------ ciclo

    def run_forever(self) -> None:
        self._install_signal_handlers()
        self._startup()
        config = self.app.config
        cycle = 0
        next_dose = 0.0

        try:
            while not self._stop.is_set():
                started = self._clock()
                now = self._wall_clock()
                measurements = self._read_sensors()
                result = self.app.monitor.evaluate(now)

                for signal_item in result.signals:
                    self.app.alerts.report(
                        signal_item.key,
                        active=signal_item.active,
                        severity=signal_item.severity,
                        message=signal_item.message,
                        now=now,
                        resolved_message=signal_item.resolved_message,
                    )

                if self._clock() >= next_dose:
                    self._dose(now, result.conditions)
                    next_dose = self._clock() + config.dosing.check_interval_s

                self.app.storage.write_measurements(measurements)
                self.app.storage.write_heartbeat(now, cycle, config.mode)
                write_heartbeat(self.app.settings.data_dir, now, cycle)
                self._commands.poll(now)
                if self._summary.is_due(now):
                    self._summary.maybe_send(now, self._daily_summary_text(now))
                cycle += 1

                elapsed = self._clock() - started
                remaining = config.read_interval_s - elapsed
                if remaining > 0:
                    self._interruptible_sleep(remaining)
                else:
                    logger.warning(
                        "el ciclo tardó %.1f s (intervalo configurado %.0f s)",
                        elapsed,
                        config.read_interval_s,
                    )
                if cycle % 20 == 0:
                    blocked = self.app.safety.blocked
                    if blocked:
                        logger.warning("bombas bloqueadas: %s", blocked)
        finally:
            self._shutdown()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------ etapas

    def _startup(self) -> None:
        logger.info("iniciando hidro-core en modo %s", self.app.config.mode)
        for output in self.app.outputs.values():
            try:
                output.off()
            except Exception:
                logger.error("no se pudo apagar la salida %s", output.id, exc_info=True)
        logger.info("todas las salidas quedaron apagadas")

    def _read_sensors(self) -> list[Measurement]:
        measurements: list[Measurement] = []
        for sensor in self.app.sensors:
            try:
                readings = sensor.read()
            except Exception:
                logger.exception("fallo inesperado del sensor %s", sensor.id)
                continue
            self.app.latest.update(readings)
            measurements.extend(readings)
        return measurements

    def _dose(self, now: datetime, flags: DosingFlags) -> None:
        decision = self.app.dosing.plan(now, flags)
        for notice in decision.notices:
            self.app.alerts.report(
                notice.key,
                active=notice.active,
                severity=notice.severity,
                message=notice.message,
                now=now,
            )

        if decision.blocked:
            logger.debug("dosificación bloqueada: %s", "; ".join(decision.blocked))
            return

        for action in decision.actions:
            output = self.app.outputs.get(action.pump_id)
            if output is None:
                continue
            try:
                text = execute_action(
                    action,
                    output,
                    dry_run=self.app.config.dosing.dry_run,
                    safety=self.app.safety,
                    now=now,
                )
            except Exception as exc:
                logger.exception("falló la dosificación con %s", action.pump_id)
                self.app.safety.block(action.pump_id, f"fallo al dosificar: {exc}")
                self.app.notifier.send(
                    f"⚠️ ERROR AL DOSIFICAR\n{action.pump_id}: {exc}. "
                    "La bomba queda bloqueada; corregir y ejecutar "
                    "'hidro safety reset'."
                )
                continue

            logger.info(text)
            self.app.notifier.send(text)
            self.app.storage.write_dose(
                action.pump_id,
                action.ml,
                action.seconds,
                dry_run=self.app.config.dosing.dry_run,
                reason=action.reason,
                now=now,
            )

        if decision.actions:
            self.app.dosing.last_dose_at = now

    def _status_text(self, now: datetime) -> str:
        return build_status_text(
            config=self.app.config,
            latest=self.app.latest,
            alerts=self.app.alerts,
            safety=self.app.safety,
            dosing=self.app.dosing,
            outputs=self.app.outputs,
            calibration=self.app.calibration,
            now=now,
            timezone=self.app.settings.timezone,
            started_at=self._started_at,
        )

    def _daily_summary_text(self, now: datetime) -> str:
        return build_daily_summary(
            config=self.app.config,
            latest=self.app.latest,
            alerts=self.app.alerts,
            safety=self.app.safety,
            dosing=self.app.dosing,
            outputs=self.app.outputs,
            calibration=self.app.calibration,
            now=now,
            timezone=self.app.settings.timezone,
            started_at=self._started_at,
            daily=self.app.storage.daily_stats(),
        )

    def _shutdown(self) -> None:
        logger.info("deteniendo hidro-core")
        for output in self.app.outputs.values():
            try:
                output.off()
            except Exception:
                logger.error("no se pudo apagar la salida %s", output.id, exc_info=True)
        self.app.close()
        logger.info("hidro-core detenido")

    # ------------------------------------------------------------------ utilidades

    def _interruptible_sleep(self, seconds: float) -> None:
        deadline = self._clock() + seconds
        while not self._stop.is_set():
            remaining = deadline - self._clock()
            if remaining <= 0:
                return
            self._sleep(min(0.25, remaining))

    def _install_signal_handlers(self) -> None:
        def handler(signum: int, _frame: object) -> None:
            logger.info("señal %s recibida; cerrando", signum)
            self._stop.set()

        try:
            signal.signal(signal.SIGTERM, handler)
            signal.signal(signal.SIGINT, handler)
        except ValueError:  # pragma: no cover - solo ocurre fuera del hilo principal
            logger.warning("no se pudieron instalar los manejadores de señales")


def run() -> int:
    settings = Settings.from_env()
    setup_logging(settings.log_level, settings.log_format)
    try:
        config = load_config(settings.config_path, settings.mode_override)
    except ConfigError as exc:
        logger.error("%s", exc)
        return 2

    from hidro.bootstrap import build_app

    try:
        app = build_app(settings, config)
    except DriverError as exc:
        logger.error("error de hardware al iniciar: %s", exc)
        return 3
    Controller(app).run_forever()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
