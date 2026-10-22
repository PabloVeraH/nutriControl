from __future__ import annotations

import json
import logging

from hidro.logging_setup import JsonFormatter, get_logger, setup_logging


def test_json_formatter_includes_context_and_exception() -> None:
    try:
        raise RuntimeError("falla")
    except RuntimeError:
        import sys

        exc_info = sys.exc_info()

    record = logging.LogRecord(
        "hidro.test", logging.ERROR, __file__, 10, "mensaje %s", ("ok",), exc_info
    )
    record.ctx_cycle = 4
    record.ctx_mode = "mock"

    payload = json.loads(JsonFormatter().format(record))
    assert payload["level"] == "error"
    assert payload["logger"] == "hidro.test"
    assert payload["msg"] == "mensaje ok"
    assert payload["cycle"] == 4
    assert payload["mode"] == "mock"
    assert "RuntimeError: falla" in payload["exc"]


def test_setup_logging_text_and_json(capsys) -> None:
    setup_logging("DEBUG", "text")
    logging.getLogger("hidro.prueba").warning("hola texto")
    assert "hola texto" in capsys.readouterr().out

    setup_logging("INFO", "json")
    logging.getLogger("hidro.prueba").info("hola json", extra={"ctx_ciclo": 2})
    output = capsys.readouterr().out.strip()
    payload = json.loads(output)
    assert payload["msg"] == "hola json"
    assert payload["ciclo"] == 2


def test_get_logger_namespace() -> None:
    assert get_logger("main").name == "hidro.main"
