from __future__ import annotations

from typing import Any

from hidro.drivers.relay import RelayOutput


class FakeDevice:
    def __init__(self) -> None:
        self.value = 0
        self.closed = False

    def on(self) -> None:
        self.value = 1

    def off(self) -> None:
        self.value = 0

    def close(self) -> None:
        self.closed = True


class BrokenCloseDevice(FakeDevice):
    def close(self) -> None:
        raise RuntimeError("boom")


def test_relay_delegates_to_device() -> None:
    device = FakeDevice()
    relay = RelayOutput("tds_power", device)
    assert relay.is_on is False
    relay.on()
    assert relay.is_on is True
    relay.off()
    assert relay.is_on is False

    relay.close()
    assert device.closed is True


def test_relay_close_propagates_device_errors() -> None:
    relay = RelayOutput("tds_power", BrokenCloseDevice())
    try:
        relay.close()
    except RuntimeError as exc:
        assert "boom" in str(exc)
    else:
        raise AssertionError("se esperaba el error de cierre")


def test_relay_accepts_any_truthy_value() -> None:
    class WeirdDevice(FakeDevice):
        def __init__(self) -> None:
            super().__init__()
            self.value: Any = "yes"

    relay = RelayOutput("x", WeirdDevice())
    assert relay.is_on is True
