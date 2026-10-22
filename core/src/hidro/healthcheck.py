"""Healthcheck de Docker basado en el latido del ciclo."""

from __future__ import annotations

import sys

from hidro.config import ConfigError, load_config
from hidro.heartbeat import heartbeat_age_s
from hidro.settings import Settings

EXIT_OK = 0
EXIT_UNHEALTHY = 1


def main() -> int:
    settings = Settings.from_env()
    try:
        config = load_config(settings.config_path, settings.mode_override)
    except ConfigError as exc:
        print(f"configuración inválida: {exc}", file=sys.stderr)
        return EXIT_UNHEALTHY

    max_age = max(3 * config.read_interval_s, 30.0)
    age = heartbeat_age_s(settings.data_dir)
    if age is None:
        print("sin heartbeat de hidro-core", file=sys.stderr)
        return EXIT_UNHEALTHY
    if age > max_age:
        print(f"heartbeat atrasado: {age:.0f} s (máximo {max_age:.0f} s)", file=sys.stderr)
        return EXIT_UNHEALTHY
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
