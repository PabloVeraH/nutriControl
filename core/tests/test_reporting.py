from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from conftest import NOW, RecordingNotifier, make_config, measurement

from hidro.alerts.manager import Severity
from hidro.bootstrap import build_app
from hidro.reporting import (
    DailySummary,
    build_daily_summary,
    build_status_text,
    parse_daily_time,
)
from hidro.settings import InfluxSettings, Settings, TelegramSettings
from hidro.storage.influx import DailyStats, DoseStats, VariableStats


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        config_path=tmp_path / "config" / "hidro.yaml",
        data_dir=tmp_path / "data",
        mode_override="mock",
        timezone=ZoneInfo("UTC"),
        log_level="INFO",
        log_format="json",
        influx=InfluxSettings(url="", token="", org="hidro", bucket="hidro"),
        telegram=TelegramSettings("", "", enabled=False),
    )


def test_parse_daily_time() -> None:
    assert parse_daily_time("09:05").hour == 9
    assert parse_daily_time("09:05").minute == 5


def test_status_text_includes_readings_alerts_silence_and_blocks(tmp_path: Path) -> None:
    app = build_app(settings_for(tmp_path), make_config())
    app.latest.update(
        [
            measurement("water_temp_1", 20.4, "°C"),
            measurement("ph", 5.9, "pH"),
            measurement("tank", False, ""),
        ]
    )
    app.latest.update([measurement("ph", 0.0, "pH", quality="invalid")])
    app.alerts.report(
        "flow",
        active=True,
        severity=Severity.CRITICAL,
        message="flujo cero",
        now=NOW,
    )
    app.alerts.silence_for(30, NOW)
    app.safety.block("pump_ph_down", "revisar calibración")
    app.outputs["tds_power"].on()

    text = build_status_text(
        config=app.config,
        latest=app.latest,
        alerts=app.alerts,
        safety=app.safety,
        dosing=app.dosing,
        outputs=app.outputs,
        calibration=app.calibration,
        now=NOW,
        timezone=ZoneInfo("UTC"),
        started_at=NOW - timedelta(hours=2, minutes=5),
    )

    assert "📋 ESTADO" in text
    assert "modo mock" in text
    assert "En marcha desde hace 2 h 5 min" in text
    assert "dry_run" in text
    assert "Calibración de pH: sin calibrar" in text
    assert "flow: flujo cero" in text
    assert "silenciadas hasta" in text
    assert "pump_ph_down: revisar calibración" in text
    assert "tds_power" in text
    assert "water_temp_1: 20.4 °C" in text
    assert "ph: 5.90 pH — lectura actual invalid" in text
    assert "flow: sin lectura" in text
    app.close()


def test_daily_summary_has_date_heading(tmp_path: Path) -> None:
    app = build_app(settings_for(tmp_path), make_config())
    text = build_daily_summary(
        config=app.config,
        latest=app.latest,
        alerts=app.alerts,
        safety=app.safety,
        dosing=app.dosing,
        outputs=app.outputs,
        calibration=app.calibration,
        now=NOW,
        timezone=ZoneInfo("UTC"),
        started_at=NOW,
    )
    assert "📊 RESUMEN DIARIO — 2026-01-01" in text
    assert "historial no disponible" in text
    app.close()


def test_daily_summary_includes_24h_stats_and_doses(tmp_path: Path) -> None:
    app = build_app(settings_for(tmp_path), make_config())
    app.latest.update(
        [
            measurement("water_temp_1", 20.4, "°C"),
            measurement("ph", 5.9, "pH"),
        ]
    )
    daily = DailyStats(
        variables={
            "ph": VariableStats("ph", 10, 5.8, 6.0, 5.9),
            "water_temp_1": VariableStats("water_temp_1", 10, 19.8, 21.0, 20.4),
        },
        doses=(DoseStats("pump_ph_down", True, 2, 2.0),),
    )
    text = build_daily_summary(
        config=app.config,
        latest=app.latest,
        alerts=app.alerts,
        safety=app.safety,
        dosing=app.dosing,
        outputs=app.outputs,
        calibration=app.calibration,
        now=NOW,
        timezone=ZoneInfo("UTC"),
        started_at=NOW,
        daily=daily,
    )
    assert "Últimas 24 h:" in text
    assert "ph: mín 5.80 · prom 5.90 · máx 6.00 pH (n=10)" in text
    assert (
        "water_temp_1: mín 19.8 · prom 20.4 · máx 21.0 °C (n=10)" in text
    )
    assert "pump_ph_down: 2 dosis, 2 ml (simulada)" in text
    app.close()


def test_daily_summary_sends_once_per_day(tmp_path: Path) -> None:
    notifier = RecordingNotifier()
    summary = DailySummary(
        notifier,
        at="09:00",
        timezone=ZoneInfo("UTC"),
        state_path=tmp_path / "daily_summary.json",
    )

    assert not summary.maybe_send(datetime(2026, 1, 1, 8, 59, tzinfo=UTC), "resumen")
    assert summary.maybe_send(datetime(2026, 1, 1, 9, 0, tzinfo=UTC), "resumen")
    assert notifier.messages == ["resumen"]
    assert not summary.maybe_send(datetime(2026, 1, 1, 12, 0, tzinfo=UTC), "otra vez")
    assert summary.maybe_send(datetime(2026, 1, 2, 9, 1, tzinfo=UTC), "resumen 2")
    assert notifier.messages == ["resumen", "resumen 2"]


def test_daily_summary_state_survives_restart(tmp_path: Path) -> None:
    state_path = tmp_path / "daily_summary.json"
    notifier = RecordingNotifier()
    first = DailySummary(
        notifier,
        at="09:00",
        timezone=ZoneInfo("UTC"),
        state_path=state_path,
    )
    assert first.maybe_send(datetime(2026, 1, 1, 9, 0, tzinfo=UTC), "resumen")

    second = DailySummary(
        RecordingNotifier(),
        at="09:00",
        timezone=ZoneInfo("UTC"),
        state_path=state_path,
    )
    assert not second.maybe_send(datetime(2026, 1, 1, 10, 0, tzinfo=UTC), "duplicado")


class FlakyNotifier(RecordingNotifier):
    def __init__(self) -> None:
        super().__init__()
        self.fail = True

    def send(self, text: str) -> bool:
        if self.fail:
            self.fail = False
            return False
        return super().send(text)


def test_daily_summary_retries_after_send_failure(tmp_path: Path) -> None:
    notifier = FlakyNotifier()
    summary = DailySummary(
        notifier,
        at="09:00",
        timezone=ZoneInfo("UTC"),
        state_path=tmp_path / "daily_summary.json",
    )
    now = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)

    assert not summary.maybe_send(now, "resumen")
    # Espera antes de reintentar: no repetir la consulta de 24 h en cada ciclo.
    assert not summary.is_due(now + timedelta(minutes=1))
    assert summary.maybe_send(now + timedelta(minutes=5), "resumen")
    assert notifier.messages == ["resumen"]


def test_daily_summary_without_telegram_is_logged_once(
    tmp_path: Path, caplog
) -> None:
    notifier = RecordingNotifier(configured=False)
    summary = DailySummary(
        notifier,
        at="09:00",
        timezone=ZoneInfo("UTC"),
        state_path=tmp_path / "daily_summary.json",
    )
    with caplog.at_level("INFO"):
        assert summary.maybe_send(datetime(2026, 1, 1, 9, 0, tzinfo=UTC), "resumen")
    assert "sin Telegram habilitado/configurado" in caplog.text
    assert notifier.messages == []


def test_timestamp_and_alias_timezone_is_rendered_locally(tmp_path: Path) -> None:
    app = build_app(settings_for(tmp_path), make_config())
    text = build_status_text(
        config=app.config,
        latest=app.latest,
        alerts=app.alerts,
        safety=app.safety,
        dosing=app.dosing,
        outputs=app.outputs,
        calibration=app.calibration,
        now=NOW,
        timezone=ZoneInfo("America/Santiago"),
        started_at=NOW,
    )
    assert "2026-01-01 09:00 -03" in text
    app.close()
