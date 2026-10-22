from __future__ import annotations

from datetime import timedelta

from conftest import NOW, RecordingNotifier

from hidro.alerts.manager import AlertManager, Severity


def test_critical_repeats_only_after_interval() -> None:
    notifier = RecordingNotifier()
    manager = AlertManager(notifier, repeat_critical=timedelta(minutes=15))

    manager.report(
        "flow",
        active=True,
        severity=Severity.CRITICAL,
        message="flujo detenido",
        now=NOW,
    )
    assert len(notifier.messages) == 1
    assert "CRÍTICO" in notifier.messages[0]

    manager.report(
        "flow",
        active=True,
        severity=Severity.CRITICAL,
        message="flujo detenido",
        now=NOW + timedelta(minutes=5),
    )
    assert len(notifier.messages) == 1

    manager.report(
        "flow",
        active=True,
        severity=Severity.CRITICAL,
        message="flujo detenido",
        now=NOW + timedelta(minutes=16),
    )
    assert len(notifier.messages) == 2


def test_non_critical_is_deduplicated_and_recovered() -> None:
    notifier = RecordingNotifier()
    manager = AlertManager(notifier)

    manager.report(
        "water-temp", active=True, severity=Severity.MEDIUM, message="agua caliente", now=NOW
    )
    manager.report(
        "water-temp",
        active=True,
        severity=Severity.MEDIUM,
        message="agua caliente",
        now=NOW + timedelta(minutes=1),
    )
    assert len(notifier.messages) == 1

    manager.report(
        "water-temp",
        active=False,
        severity=Severity.MEDIUM,
        message="agua caliente",
        now=NOW + timedelta(minutes=2),
        resolved_message="agua normal",
    )
    assert len(notifier.messages) == 2
    assert "NORMALIZADO" in notifier.messages[1]
    assert not manager.active_keys


def test_severity_upgrade_notifies_again() -> None:
    notifier = RecordingNotifier()
    manager = AlertManager(notifier)
    manager.report("ph", active=True, severity=Severity.MEDIUM, message="pH fuera", now=NOW)
    manager.report("ph", active=True, severity=Severity.HIGH, message="pH peligro", now=NOW)
    assert len(notifier.messages) == 2
    assert "ALERTA" in notifier.messages[1]


def test_recovery_without_previous_alert_is_silent() -> None:
    notifier = RecordingNotifier()
    manager = AlertManager(notifier)
    manager.report(
        "x", active=False, severity=Severity.MEDIUM, message="", now=NOW, resolved_message="ok"
    )
    assert notifier.messages == []


def test_silence_suppresses_non_critical_but_not_critical() -> None:
    notifier = RecordingNotifier()
    manager = AlertManager(notifier)
    manager.silence_for(30, NOW)
    assert manager.is_silenced(NOW + timedelta(minutes=5))

    manager.report("ec", active=True, severity=Severity.MEDIUM, message="EC alta", now=NOW)
    assert notifier.messages == []

    manager.report(
        "flow", active=True, severity=Severity.CRITICAL, message="flujo cero", now=NOW
    )
    assert len(notifier.messages) == 1

    manager.report(
        "ec",
        active=False,
        severity=Severity.MEDIUM,
        message="EC alta",
        now=NOW + timedelta(minutes=6),
        resolved_message="EC normal",
    )
    assert len(notifier.messages) == 1


def test_silence_expires() -> None:
    notifier = RecordingNotifier()
    manager = AlertManager(notifier)
    manager.silence_for(10, NOW)
    assert not manager.is_silenced(NOW + timedelta(minutes=11))
