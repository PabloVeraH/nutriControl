from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from conftest import NOW, RecordingNotifier

from hidro.alerts.commands import TelegramCommandBot, TelegramCommandHandler
from hidro.alerts.manager import AlertManager, Severity


def handler(notifier: RecordingNotifier | None = None) -> tuple[TelegramCommandHandler, AlertManager]:
    manager = AlertManager(notifier or RecordingNotifier())
    return (
        TelegramCommandHandler(
            alerts=manager,
            status_provider=lambda _now: "ESTADO ACTUAL",
            allowed_chat_ids=[42],
            timezone=ZoneInfo("UTC"),
        ),
        manager,
    )


def test_only_allowlisted_chat_is_authorized() -> None:
    commands, _manager = handler()
    assert commands.handle(42, "/estado", NOW) == "ESTADO ACTUAL"
    assert commands.handle("42", "/estado", NOW) == "ESTADO ACTUAL"
    assert commands.handle(99, "/estado", NOW) is None
    assert commands.handle(99, "/silenciar 10", NOW) is None


def test_estado_accepts_bot_suffix_and_ignores_plain_text() -> None:
    commands, _manager = handler()
    assert commands.handle(42, "/estado@nutricontrol_bot", NOW) == "ESTADO ACTUAL"
    assert commands.handle(42, "hola", NOW) is None


def test_silenciar_sets_deadline_and_mentions_critical_alerts() -> None:
    commands, manager = handler()
    reply = commands.handle(42, "/silenciar 30", NOW)
    assert reply is not None
    assert "30 min" in reply
    assert "críticas siguen activas" in reply
    assert manager.silenced_until == NOW + timedelta(minutes=30)
    assert manager.is_silenced(NOW + timedelta(minutes=29))
    assert not manager.is_silenced(NOW + timedelta(minutes=31))


def test_silenciar_defaults_to_one_hour() -> None:
    commands, manager = handler()
    reply = commands.handle(42, "/silenciar", NOW)
    assert reply is not None
    assert "60 min" in reply
    assert manager.silenced_until == NOW + timedelta(hours=1)


def test_silenciar_rejects_invalid_values() -> None:
    commands, manager = handler()
    for value in ("0", "-5", "1441", "abc", "nan"):
        reply = commands.handle(42, f"/silenciar {value}", NOW)
        assert reply is not None
        assert "Uso: /silenciar" in reply
        assert manager.silenced_until is None


def test_unknown_command_returns_help() -> None:
    commands, _manager = handler()
    reply = commands.handle(42, "/borrar", NOW)
    assert reply is not None
    assert "/estado" in reply
    assert "/silenciar" in reply


def test_status_provider_error_does_not_crash_the_bot() -> None:
    commands = TelegramCommandHandler(
        alerts=AlertManager(RecordingNotifier()),
        status_provider=lambda _now: (_ for _ in ()).throw(RuntimeError("boom")),
        allowed_chat_ids=[42],
        timezone=ZoneInfo("UTC"),
    )
    reply = commands.handle(42, "/estado", NOW)
    assert reply is not None
    assert "No se pudo leer el estado" in reply


class UpdateNotifier(RecordingNotifier):
    def __init__(self, updates: list[dict]) -> None:
        super().__init__()
        self.updates = updates

    def get_updates(self, offset: int | None = None) -> list[dict]:
        self.requested_offsets.append(offset)
        return self.updates


def update(update_id: int, chat_id: int, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {"chat": {"id": chat_id}, "text": text},
    }


def test_poller_replies_only_to_authorized_chat_and_persists_offset(tmp_path: Path) -> None:
    commands, _manager = handler()
    offset_path = tmp_path / "telegram_offset.json"
    notifier = UpdateNotifier(
        [
            update(10, 42, "/estado"),
            update(11, 99, "/estado"),
            {"update_id": 12, "edited_message": {}},
        ]
    )
    bot = TelegramCommandBot(notifier, commands, offset_path=offset_path)

    assert bot.poll(NOW) == 2
    assert notifier.messages == ["ESTADO ACTUAL"]
    assert notifier.requested_offsets == [None]
    assert offset_path.exists()

    second_notifier = UpdateNotifier([])
    second_bot = TelegramCommandBot(second_notifier, commands, offset_path=offset_path)
    assert second_bot.poll(NOW) == 0
    assert second_notifier.requested_offsets == [13]


def test_status_critical_alerts_survive_silence() -> None:
    notifier = RecordingNotifier()
    commands, manager = handler(notifier)
    commands.handle(42, "/silenciar 30", NOW)
    manager.report(
        "flow",
        active=True,
        severity=Severity.CRITICAL,
        message="flujo cero",
        now=NOW,
    )
    assert any("CRÍTICO" in message for message in notifier.messages)
