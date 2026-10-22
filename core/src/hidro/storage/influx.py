"""Escritura de series de tiempo en InfluxDB 2."""

from __future__ import annotations

import contextlib
from collections import deque
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

from hidro.logging_setup import get_logger
from hidro.model import Measurement
from hidro.settings import InfluxSettings

logger = get_logger("storage.influx")

MAX_BUFFERED_POINTS = 5000


class InfluxStorage:
    def __init__(self, settings: InfluxSettings) -> None:
        self.settings = settings
        self.enabled = settings.enabled
        self._buffer: deque[Point] = deque(maxlen=MAX_BUFFERED_POINTS)
        self._client: InfluxDBClient | None = None
        self._write_api: Any = None
        self._warned = False
        if not self.enabled:
            logger.warning("InfluxDB deshabilitado: falta INFLUX_TOKEN o INFLUX_URL")

    def _ensure_client(self) -> bool:
        if not self.enabled:
            return False
        if self._write_api is not None:
            return True
        try:
            self._client = InfluxDBClient(
                url=self.settings.url,
                token=self.settings.token,
                org=self.settings.org,
                timeout=10_000,
            )
            self._write_api = self._client.write_api(write_options=SYNCHRONOUS)
            return True
        except Exception as exc:
            if not self._warned:
                logger.error("no se pudo crear el cliente de InfluxDB: %s", exc)
                self._warned = True
            return False

    def write_measurements(self, measurements: Iterable[Measurement]) -> None:
        points = []
        for measurement in measurements:
            if not measurement.valid:
                continue
            point = (
                Point(measurement.name)
                .tag("quality", measurement.quality)
                .tag("source", measurement.source or measurement.name)
                .field("value", measurement.value)
                .time(measurement.timestamp)
            )
            points.append(point)
        self._write(points)

    def write_heartbeat(self, now: datetime, cycle: int, mode: str) -> None:
        point = (
            Point("heartbeat")
            .tag("mode", mode)
            .field("value", 1)
            .field("cycle", cycle)
            .time(now)
        )
        self._write([point])

    def write_dose(
        self,
        pump_id: str,
        ml: float,
        seconds: float,
        *,
        dry_run: bool,
        reason: str,
        now: datetime,
    ) -> None:
        point = (
            Point("dose")
            .tag("pump", pump_id)
            .tag("dry_run", "true" if dry_run else "false")
            .field("ml", float(ml))
            .field("seconds", float(seconds))
            .field("reason", reason)
            .time(now)
        )
        self._write([point])

    def _write(self, points: list[Point]) -> None:
        if not self.enabled or (not points and not self._buffer):
            return
        if not self._ensure_client():
            self._buffer.extend(points)
            return

        pending = list(self._buffer) + points
        self._buffer.clear()
        try:
            assert self._write_api is not None
            self._write_api.write(
                bucket=self.settings.bucket,
                org=self.settings.org,
                record=pending,
            )
        except Exception as exc:
            logger.error("falló la escritura en InfluxDB (%s puntos): %s", len(pending), exc)
            self._buffer.extend(pending)
            self._warned = True

    def close(self) -> None:
        if self._buffer and self.enabled:
            self._write([])
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close()
