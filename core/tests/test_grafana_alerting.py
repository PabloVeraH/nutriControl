from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_no_data_rule_fires_after_ten_minutes() -> None:
    raw = yaml.safe_load(
        (ROOT / "config" / "grafana" / "alerting" / "rules.yaml").read_text(
            encoding="utf-8"
        )
    )
    rule = raw["groups"][0]["rules"][0]

    assert rule["uid"] == "hidro-sin-datos"
    assert rule["noDataState"] == "Alerting"
    assert rule["execErrState"] == "Alerting"
    # 5 min de consulta + 5 min de "for": ~10 min sin latidos, sin disparar
    # por un error pasajero de una sola evaluación.
    assert rule["for"] == "5m"
    assert all(item["relativeTimeRange"]["from"] == 300 for item in rule["data"])
    assert 'range(start: -5m)' in rule["data"][0]["model"]["query"]


def test_renderer_quotes_telegram_chat_id(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "render_grafana_alerting", ROOT / "scripts" / "render_grafana_alerting.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=123:ABC\nTELEGRAM_CHAT_ID=987654321\n",
        encoding="utf-8",
    )
    target = tmp_path / "alerting"
    module.render(ROOT, env_file, target)

    contact = (target / "contact-points.yaml").read_text(encoding="utf-8")
    assert 'bottoken: "123:ABC"' in contact
    assert 'chatid: "987654321"' in contact
    assert (target / "policies.yaml").exists()
    assert (target / "rules.yaml").exists()
