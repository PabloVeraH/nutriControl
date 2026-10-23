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

        response = self._post("sendMessage", {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        })
        return response is not None

    def get_updates(self, offset: int | None = None) -> list[dict[str, Any]]:
        """Lee los mensajes pendientes sin bloquear el ciclo principal.

        El bot solo procesa mensajes; las actualizaciones se confirman al
        guardar el offset, por lo que un reinicio no repite comandos ya vistos.
        """
        if not self.enabled:
            return []

        payload: dict[str, Any] = {
            "timeout": 0,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        response = self._post("getUpdates", payload)
        if response is None:
            return []
        try:
            body = response.json()
        except Exception as exc:
            logger.error("respuesta inválida de Telegram: %s", self._redact(str(exc)))
            return []
        if not isinstance(body, dict) or not body.get("ok"):
            logger.error("Telegram rechazó getUpdates")
            return []
        result = body.get("result", [])
        if not isinstance(result, list):
            logger.error("Telegram devolvió una lista de actualizaciones inválida")
            return []
        return [item for item in result if isinstance(item, dict)]

    def _post(self, method: str, payload: dict[str, Any]) -> Any | None:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        try:
            if self._client is not None:
                response = self._client.post(url, json=payload, timeout=self._timeout)
            else:
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.post(url, json=payload)
            response.raise_for_status()
            return response
        except Exception as exc:
            # Los errores de httpx incluyen la URL, que contiene el token del bot.
            logger.error(
                "no se pudo comunicar con Telegram (%s): %s",
                method,
                self._redact(str(exc)),
            )
            return None

    def _redact(self, text: str) -> str:
        return text.replace(self.token, "<token>")

    def close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close()
