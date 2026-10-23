from __future__ import annotations

from typing import Any

import httpx
import pytest

from hidro.alerts.telegram import TelegramNotifier


class FakeResponse:
    def __init__(
        self, fail: bool = False, body: dict[str, Any] | None = None
    ) -> None:
        self.fail = fail
        self.body = body or {}

    def raise_for_status(self) -> None:
        if self.fail:
            raise RuntimeError("telegram caído")

    def json(self) -> dict[str, Any]:
        return self.body


class FakeClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, dict[str, Any], float]] = []
        self.closed = False

    def post(self, url: str, *, json: dict[str, Any], timeout: float) -> FakeResponse:
        self.calls.append((url, json, timeout))
        return FakeResponse(self.fail)

    def close(self) -> None:
        self.closed = True


def test_disabled_notifier_does_not_send() -> None:
    notifier = TelegramNotifier("", "", enabled=True)
    assert notifier.configured is False
    assert notifier.send("hola") is False


def test_send_success_and_payload() -> None:
    client = FakeClient()
    notifier = TelegramNotifier("token", "chat", client=client)
    assert notifier.configured is True
    assert notifier.send("hola") is True
    assert len(client.calls) == 1
    url, payload, timeout = client.calls[0]
    assert url == "https://api.telegram.org/bottoken/sendMessage"
    assert payload == {
        "chat_id": "chat",
        "text": "hola",
        "disable_web_page_preview": True,
    }
    assert timeout == 10.0
    notifier.close()
    assert client.closed is True


def test_send_failure_returns_false() -> None:
    notifier = TelegramNotifier("token", "chat", client=FakeClient(fail=True))
    assert notifier.send("hola") is False


def test_get_updates_returns_messages_and_sends_offset() -> None:
    class UpdateClient(FakeClient):
        response_body: dict[str, Any]

        def post(
            self, url: str, *, json: dict[str, Any], timeout: float
        ) -> FakeResponse:
            self.calls.append((url, json, timeout))
            return FakeResponse(body=self.response_body)

    update_client = UpdateClient()
    update_client.response_body = {
        "ok": True,
        "result": [
            {"update_id": 5, "message": {"text": "/estado"}},
            {"update_id": 6, "edited_message": {}},
        ],
    }
    notifier = TelegramNotifier("token", "chat", client=update_client)

    updates = notifier.get_updates(offset=4)

    assert len(updates) == 2
    url, payload, _timeout = update_client.calls[0]
    assert url.endswith("/getUpdates")
    assert payload["offset"] == 4
    assert payload["timeout"] == 0
    assert payload["allowed_updates"] == ["message"]


def test_get_updates_handles_errors_without_leaking_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = "123456:SECRETO"

    class RejectingUpdateClient(FakeClient):
        def post(
            self, url: str, *, json: dict[str, Any], timeout: float
        ) -> FakeResponse:
            request = httpx.Request("POST", url)
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError("401 " + url, request=request, response=response)

    notifier = TelegramNotifier(token, "chat", client=RejectingUpdateClient())
    with caplog.at_level("ERROR"):
        assert notifier.get_updates() == []
    assert "SECRETO" not in caplog.text
    assert "<token>" in caplog.text


def test_disabled_notifier_does_not_poll() -> None:
    notifier = TelegramNotifier("", "", enabled=True)
    assert notifier.get_updates() == []


def test_notifier_respects_enabled_flag() -> None:
    client = FakeClient()
    notifier = TelegramNotifier("token", "chat", enabled=False, client=client)
    assert notifier.send("hola") is False
    assert client.calls == []


def test_send_failure_does_not_log_the_token(caplog: pytest.LogCaptureFixture) -> None:
    token = "123456:SECRETO"

    class RejectingClient(FakeClient):
        def post(self, url: str, *, json: dict[str, Any], timeout: float) -> Any:
            request = httpx.Request("POST", url)
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("404 para " + url, request=request, response=response)

    notifier = TelegramNotifier(token, "chat", client=RejectingClient())
    with caplog.at_level("ERROR"):
        assert notifier.send("hola") is False
    assert "SECRETO" not in caplog.text
    assert "<token>" in caplog.text
