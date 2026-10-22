"""Configuración de entorno (secretos, rutas, zona horaria)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import find_dotenv, load_dotenv


@dataclass(frozen=True, slots=True)
class InfluxSettings:
    url: str
    token: str
    org: str
    bucket: str

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.token)


@dataclass(frozen=True, slots=True)
class TelegramSettings:
    token: str
    chat_id: str
    enabled: bool

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)


@dataclass(frozen=True, slots=True)
class Settings:
    config_path: Path
    data_dir: Path
    mode_override: str | None
    timezone: ZoneInfo
    log_level: str
    log_format: str
    influx: InfluxSettings
    telegram: TelegramSettings

    @classmethod
    def from_env(cls) -> Settings:
        dotenv_path = find_dotenv(usecwd=True)
        if dotenv_path:
            load_dotenv(dotenv_path, override=False)

        mode = os.getenv("HIDRO_HARDWARE", "").strip().lower()
        mode_override = mode if mode in {"hardware", "mock"} else None

        tz_name = os.getenv("TZ", "America/Santiago")
        try:
            timezone = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            timezone = ZoneInfo("UTC")

        telegram_enabled = os.getenv("TELEGRAM_ENABLED", "true").strip().lower() not in {
            "0",
            "false",
            "no",
        }

        config_path = _default_config_path()
        data_dir = Path(os.getenv("HIDRO_DATA_DIR", "")) if os.getenv("HIDRO_DATA_DIR") else _default_data_dir(config_path)

        return cls(
            config_path=config_path,
            data_dir=data_dir,
            mode_override=mode_override,
            timezone=timezone,
            log_level=os.getenv("HIDRO_LOG_LEVEL", "INFO"),
            log_format=os.getenv("HIDRO_LOG_FORMAT", "json"),
            influx=InfluxSettings(
                url=os.getenv("INFLUX_URL", "http://localhost:8086"),
                token=os.getenv("INFLUX_TOKEN", ""),
                org=os.getenv("INFLUX_ORG", "hidro"),
                bucket=os.getenv("INFLUX_BUCKET", "hidro"),
            ),
            telegram=TelegramSettings(
                token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
                chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
                enabled=telegram_enabled,
            ),
        )


def _default_config_path() -> Path:
    from_env = os.getenv("HIDRO_CONFIG")
    if from_env:
        return Path(from_env)
    current = Path.cwd()
    for base in (current, *current.parents):
        candidate = base / "config" / "hidro.yaml"
        if candidate.exists():
            return candidate
    return Path("config/hidro.yaml")


def _default_data_dir(config_path: Path) -> Path:
    if config_path.parent.name == "config":
        return config_path.parent.parent / "data"
    return Path("data")
