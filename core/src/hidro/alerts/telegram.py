"""Envío de mensajes por Telegram."""

from __future__ import annotations

import contextlib
from typing import Any

import httpx

from hidro.logging_setup import get_logger

logger = get_logger("alerts.telegram")


class TelegramNotifier:
    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        enabled: bool = True,
        timeout_s: float = 10.0,
        client: Any | None = None,
    ) -> None:
        self.token = token
        self.chat_id = chat_id
        self.enabled = enabled and bool(token) and bool(chat_id)
        self._timeout = timeout_s
        self._client = client
        if enabled and not self.enabled:
            logger.warning(
                "Telegram deshabilitado: faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID"
            )

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        if not self.enabled:
            logger.info("Telegram (no configurado): %s", text.replace("\n", " | "))
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        try:
            if self._client is not None:
                response = self._client.post(url, json=payload, timeout=self._timeout)
            else:
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.post(url, json=payload)
            response.raise_for_status()
            return True
        except Exception as exc:
            logger.error("no se pudo enviar el mensaje de Telegram: %s", exc)
            return False

    def close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close()
