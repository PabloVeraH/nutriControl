from __future__ import annotations

from datetime import UTC, datetime

import hidro.storage.influx as influx_module
from hidro.model import Measurement
from hidro.settings import InfluxSettings
from hidro.storage.influx import InfluxStorage

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def settings(token: str = "token") -> InfluxSettings:
    return InfluxSettings(
        url="http://influx:8086",
        token=token,
        org="hidro",
        bucket="hidro",
    )


class FakeWriteAPI:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, list]] = []
        self.fail = False

    def write(self, *, bucket: str, org: str, record: list) -> None:
        if self.fail:
            raise RuntimeError("influx caído")
        self.calls.append((bucket, org, list(record)))


class FakeClient:
    def __init__(self, write_api: FakeWriteAPI) -> None:
        self.write_api_instance = write_api
        self.closed = False

    def write_api(self, *, write_options=None):
        return self.write_api_instance

    def close(self) -> None:
        self.closed = True


def install_client(monkeypatch, client: FakeClient) -> None:
    monkeypatch.setattr(influx_module, "InfluxDBClient", lambda **_kwargs: client)


def measurement(
    name: str = "ph",
    value: float = 5.9,
    *,
    quality: str = "ok",
) -> Measurement:
    return Measurement(name, value, "pH", NOW, quality, name)  # type: ignore[arg-type]


def test_disabled_storage_does_nothing() -> None:
    storage = InfluxStorage(settings(token=""))
    assert storage.enabled is False
    storage.write_measurements([measurement()])
    storage.write_heartbeat(NOW, 1, "mock")
    storage.write_dose("pump", 1.0, 6.0, dry_run=True, reason="x", now=NOW)
    storage.close()


def test_write_measurements_skips_invalid_and_builds_points(monkeypatch) -> None:
    api = FakeWriteAPI()
    install_client(monkeypatch, FakeClient(api))
    storage = InfluxStorage(settings())

    storage.write_measurements([measurement(), measurement("ec", quality="invalid")])
    assert len(api.calls) == 1
    bucket, org, points = api.calls[0]
    assert bucket == "hidro"
    assert org == "hidro"
    assert len(points) == 1
    line = points[0].to_line_protocol()
    assert line.startswith("ph,quality=ok,source=ph")
    assert "value=5.9" in line


def test_write_heartbeat_and_dose(monkeypatch) -> None:
    api = FakeWriteAPI()
    install_client(monkeypatch, FakeClient(api))
    storage = InfluxStorage(settings())

    storage.write_heartbeat(NOW, 7, "mock")
    storage.write_dose(
        "pump_ph_down", 1.5, 9.0, dry_run=False, reason="pH bajo", now=NOW
    )

    heartbeat = api.calls[0][2][0].to_line_protocol()
    dose = api.calls[1][2][0].to_line_protocol()
    assert heartbeat.startswith("heartbeat,mode=mock")
    assert "value=1i" in heartbeat
    assert "cycle=7i" in heartbeat
    assert dose.startswith("dose,dry_run=false,pump=pump_ph_down")
    assert "ml=1.5" in dose
    assert "seconds=9" in dose
    assert "reason=\"pH bajo\"" in dose


def test_client_creation_failure_buffers(monkeypatch) -> None:
    storage = InfluxStorage(settings())

    def broken(**_kwargs):
        raise RuntimeError("sin conexión")

    monkeypatch.setattr(influx_module, "InfluxDBClient", broken)
    storage.write_measurements([measurement()])
    assert len(storage._buffer) == 1

    api = FakeWriteAPI()
    install_client(monkeypatch, FakeClient(api))
    storage.write_measurements([measurement("ec", 1150.0)])
    assert len(api.calls[0][2]) == 2
    assert len(storage._buffer) == 0


def test_write_failure_keeps_points(monkeypatch) -> None:
    api = FakeWriteAPI()
    api.fail = True
    install_client(monkeypatch, FakeClient(api))
    storage = InfluxStorage(settings())

    storage.write_measurements([measurement()])
    assert len(storage._buffer) == 1

    api.fail = False
    storage.write_heartbeat(NOW, 2, "mock")
    assert len(api.calls[0][2]) == 2
    assert list(storage._buffer) == []


def test_close_flushes_buffer_and_closes_client(monkeypatch) -> None:
    api = FakeWriteAPI()
    api.fail = True
    client = FakeClient(api)
    install_client(monkeypatch, client)
    storage = InfluxStorage(settings())
    storage.write_measurements([measurement()])

    api.fail = False
    storage.close()
    assert len(api.calls) == 1
    assert client.closed is True


def test_close_tolerates_client_errors(monkeypatch) -> None:
    class ExplodingClient(FakeClient):
        def close(self) -> None:
            raise RuntimeError("boom")

    api = FakeWriteAPI()
    client = ExplodingClient(api)
    install_client(monkeypatch, client)
    storage = InfluxStorage(settings())
    storage.write_measurements([measurement()])
    storage.close()
