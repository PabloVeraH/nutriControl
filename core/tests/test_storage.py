from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

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


class FakeQueryAPI:
    def __init__(self, results: list[list]) -> None:
        self.results = results
        self.queries: list[tuple[str, str]] = []
        self.fail = False

    def query(self, query: str, *, org: str) -> list:
        if self.fail:
            raise RuntimeError("consulta caída")
        self.queries.append((query, org))
        return self.results.pop(0)


class FakeQueryClient(FakeClient):
    def __init__(self, write_api: FakeWriteAPI, query_api: FakeQueryAPI) -> None:
        super().__init__(write_api)
        self.query_api_instance = query_api

    def query_api(self) -> FakeQueryAPI:
        return self.query_api_instance


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


def test_daily_stats_parses_variables_and_doses(monkeypatch) -> None:
    api = FakeWriteAPI()
    query_api = FakeQueryAPI(
        [
            [
                SimpleNamespace(
                    records=[
                        {
                            "_measurement": "ph",
                            "count": 10.0,
                            "sum": 59.0,
                            "min": 5.8,
                            "max": 6.0,
                        },
                        {
                            "_measurement": "water_temp_1",
                            "count": 0.0,
                            "sum": 0.0,
                            "min": 0.0,
                            "max": 0.0,
                        },
                    ]
                )
            ],
            [
                SimpleNamespace(
                    records=[
                        {
                            "pump": "pump_ph_down",
                            "dry_run": "true",
                            "count": 2.0,
                            "ml": 2.0,
                        }
                    ]
                )
            ],
        ]
    )
    install_client(monkeypatch, FakeQueryClient(api, query_api))
    storage = InfluxStorage(settings())

    stats = storage.daily_stats()

    assert stats is not None
    assert stats.variables["ph"].mean == 5.9
    assert stats.variables["ph"].minimum == 5.8
    assert stats.variables["ph"].maximum == 6.0
    assert "water_temp_1" not in stats.variables
    assert stats.doses[0].pump_id == "pump_ph_down"
    assert stats.doses[0].dry_run is True
    assert stats.doses[0].count == 2
    assert stats.doses[0].ml == 2.0
    assert len(query_api.queries) == 2
    assert 'range(start: -24h)' in query_api.queries[0][0]
    assert 'r._measurement != "heartbeat"' in query_api.queries[0][0]
    assert "r._measurement !~ /_low$/" in query_api.queries[0][0]
    assert "float(v: r._value)" in query_api.queries[0][0]
    assert 'r._measurement == "dose"' in query_api.queries[1][0]


def test_daily_stats_returns_none_when_disabled_or_query_fails(
    monkeypatch, caplog
) -> None:
    disabled = InfluxStorage(settings(token=""))
    assert disabled.daily_stats() is None

    query_api = FakeQueryAPI([])
    query_api.fail = True
    install_client(monkeypatch, FakeQueryClient(FakeWriteAPI(), query_api))
    storage = InfluxStorage(settings())
    with caplog.at_level("WARNING"):
        assert storage.daily_stats() is None
    assert "no se pudieron leer los agregados diarios" in caplog.text


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
