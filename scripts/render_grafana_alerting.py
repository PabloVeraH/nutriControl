#!/usr/bin/env python3
"""Genera la configuración de alertas de Grafana desde .env.

Grafana interpreta los valores numéricos de los contact points como números,
por lo que el chat id de Telegram debe quedar entre comillas en el YAML final.
Este script renderiza la plantilla con los valores reales y deja el resultado
en un directorio ignorado por git (data/grafana/alerting).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REQUIRED = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
ALERTING_FILES = ("policies.yaml", "rules.yaml")


def read_env(path: Path) -> dict[str, str]:
    """Lee un archivo KEY=VALUE simple, sin interpretar el shell."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def render(repo: Path, env_file: Path, target: Path) -> None:
    env = read_env(env_file)
    missing = [name for name in REQUIRED if not env.get(name)]
    if missing:
        raise SystemExit(
            "faltan variables en {}: {}".format(env_file, ", ".join(missing))
        )

    source = repo / "config" / "grafana" / "alerting"
    template = (source / "contact-points.yaml.template").read_text(encoding="utf-8")
    content = template.replace(
        "@TELEGRAM_BOT_TOKEN@", json.dumps(env["TELEGRAM_BOT_TOKEN"])
    ).replace("@TELEGRAM_CHAT_ID@", json.dumps(env["TELEGRAM_CHAT_ID"]))

    target.mkdir(parents=True, exist_ok=True)
    for name in ALERTING_FILES:
        shutil.copyfile(source / name, target / name)
    (target / "contact-points.yaml").write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="archivo .env a leer (por defecto el de la raíz del repo)",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help="directorio de salida (por defecto data/grafana/alerting)",
    )
    args = parser.parse_args(argv)

    repo = Path(__file__).resolve().parents[1]
    env_file = args.env_file or repo / ".env"
    target = args.target or repo / "data" / "grafana" / "alerting"
    render(repo, env_file, target)
    print("alertas de Grafana generadas en {}".format(target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
