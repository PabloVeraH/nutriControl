from __future__ import annotations

from typing import Any

from hidro.alerts.telegram import TelegramNotifier


class FakeResponse:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def raise_for_status(self) -> None:
        if self.fail:
            raise RuntimeError("telegram caído")


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


def test_notifier_respects_enabled_flag() -> None:
    client = FakeClient()
    notifier = TelegramNotifier("token", "chat", enabled=False, client=client)
    assert notifier.send("hola") is False
    assert client.calls == []
