"""Comandos entrantes de Telegram con lista blanca de chats."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path

from hidro.alerts.manager import AlertManager
from hidro.alerts.telegram import TelegramNotifier
from hidro.logging_setup import get_logger

logger = get_logger("alerts.commands")

DEFAULT_SILENCE_MIN = 60.0
MAX_SILENCE_MIN = 1440.0
# Un /silenciar enviado con el núcleo detenido llega al reiniciar: pasado este
# tiempo ya no refleja la intención del usuario y podría tapar alertas nuevas.
MAX_SILENCE_COMMAND_AGE = timedelta(minutes=10)

HELP_TEXT = (
    "Comandos disponibles:\n"
    "/estado — lecturas, alertas y bloqueos actuales.\n"
    "/silenciar [minutos] — silencia avisos no críticos del núcleo (60 min "
    "por defecto, máximo 1440). Las alertas críticas y las de Grafana siguen "
    "llegando.\n"
    "/ayuda — muestra esta ayuda."
)


class TelegramCommandHandler:
    """Autoriza, interpreta y responde comandos; no envía mensajes."""

    def __init__(
        self,
        *,
        alerts: AlertManager,
        status_provider: Callable[[datetime], str],
        allowed_chat_ids: Iterable[str | int],
        timezone: tzinfo,
    ) -> None:
        self.alerts = alerts
        self.status_provider = status_provider
        self.allowed_chat_ids = {str(chat_id) for chat_id in allowed_chat_ids}
        self.timezone = timezone

    def handle(
        self,
        chat_id: object,
        text: str,
        now: datetime,
        sent_at: datetime | None = None,
    ) -> str | None:
        if str(chat_id) not in self.allowed_chat_ids:
            logger.warning("comando de Telegram ignorado: chat no autorizado")
            return None

        parts = text.strip().split()
        if not parts:
            return None
        command = parts[0].split("@", 1)[0].lower()
        if not command.startswith("/"):
            return None

        if command in {"/estado", "/status"}:
            try:
                return self.status_provider(now)
            except Exception:
                logger.exception("no se pudo construir el estado para Telegram")
                return "⚠️ No se pudo leer el estado del núcleo; revisa los logs."

        if command == "/silenciar":
            if sent_at is not None and now - sent_at > MAX_SILENCE_COMMAND_AGE:
                minutes = (now - sent_at).total_seconds() / 60
                return (
                    f"⏱️ Ignoré «{text.strip()}»: llegó con {minutes:.0f} min de retraso "
                    "(el núcleo estaba detenido). Repítelo si aún quieres silenciar."
                )
            return self._silence(parts[1:], now)
        return HELP_TEXT

    def _silence(self, args: list[str], now: datetime) -> str:
        raw = args[0].replace(",", ".") if args else str(DEFAULT_SILENCE_MIN)
        try:
            minutes = float(raw)
        except ValueError:
            minutes = math.nan
        if not math.isfinite(minutes) or not 0 < minutes <= MAX_SILENCE_MIN:
            return (
                "Uso: /silenciar [minutos], con un valor mayor que 0 y "
                f"máximo {MAX_SILENCE_MIN:.0f}."
            )

        self.alerts.silence_for(minutes, now)
        until = (now + timedelta(minutes=minutes)).astimezone(self.timezone)
        return (
            f"🔕 Alertas no críticas silenciadas durante {minutes:g} min "
            f"(hasta {until:%Y-%m-%d %H:%M %Z}). Las críticas siguen activas."
        )


class TelegramCommandBot:
    """Consulta getUpdates y confirma el offset en /data."""

    def __init__(
        self,
        notifier: TelegramNotifier,
        handler: TelegramCommandHandler,
        *,
        offset_path: Path,
    ) -> None:
        self.notifier = notifier
        self.handler = handler
        self.offset_path = Path(offset_path)
        self._offset = self._load_offset()

    def poll(self, now: datetime) -> int:
        try:
            updates = self.notifier.get_updates(self._offset)
        except Exception:
            logger.exception("falló la consulta de comandos de Telegram")
            return 0
        if not updates:
            return 0

        handled = 0
        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                self._offset = update_id + 1
            message = update.get("message")
            if not isinstance(message, dict):
                continue
            text = message.get("text")
            chat = message.get("chat")
            if not isinstance(text, str) or not isinstance(chat, dict):
                continue
            chat_id = chat.get("id")
            if chat_id is None:
                continue
            sent_at = None
            if isinstance(message.get("date"), int):
                sent_at = datetime.fromtimestamp(message["date"], UTC)
            reply = self.handler.handle(chat_id, text, now, sent_at)
            if reply:
                self.notifier.send(reply)
            handled += 1

        self._save_offset()
        return handled

    def _load_offset(self) -> int | None:
        if not self.offset_path.exists():
            return None
        try:
            raw = json.loads(self.offset_path.read_text(encoding="utf-8"))
            value = raw.get("offset")
            return int(value) if value is not None else None
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            logger.warning("no se pudo leer el offset de Telegram: %s", exc)
            return None

    def _save_offset(self) -> None:
        if self._offset is None:
            return
        try:
            self.offset_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.offset_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"offset": self._offset}, indent=2), encoding="utf-8")
            os.replace(tmp, self.offset_path)
        except OSError as exc:
            logger.warning("no se pudo guardar el offset de Telegram: %s", exc)
