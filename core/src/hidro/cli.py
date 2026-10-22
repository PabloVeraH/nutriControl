"""CLI de operación y calibración."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime

from hidro import __version__
from hidro.calibration import CalibrationStore
from hidro.config import ConfigError, load_config
from hidro.control.safety import SafetyGuard
from hidro.drivers.base import DoseTooLong, DriverError, PumpNotCalibrated
from hidro.logging_setup import setup_logging
from hidro.settings import Settings


def _settings(verbose: bool = False) -> Settings:
    settings = Settings.from_env()
    setup_logging(settings.log_level if verbose else "WARNING", "text")
    return settings


def cmd_config_check(_args: argparse.Namespace) -> int:
    settings = _settings(verbose=True)
    config = load_config(settings.config_path, settings.mode_override)
    print(f"configuración válida: {settings.config_path}")
    print(f"  modo: {config.mode}")
    print(f"  intervalo de lectura: {config.read_interval_s:g} s")
    print(f"  sensores: {', '.join(sensor.id for sensor in config.sensors)}")
    print(f"  salidas: {', '.join(output.id for output in config.outputs) or '(ninguna)'}")
    print(
        "  dosificación: "
        + ("activada" if config.dosing.enabled else "desactivada")
        + (" (dry-run)" if config.dosing.dry_run else "")
    )
    for pump_id, limit in config.dosing.limits.max_ml_per_day.items():
        print(f"  límite diario {pump_id}: {limit:g} ml")
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    from hidro.bootstrap import build_app

    settings = _settings(verbose=args.verbose)
    config = load_config(settings.config_path, settings.mode_override)
    app = build_app(settings, config)
    try:
        rows = []
        for sensor in app.sensors:
            try:
                readings = sensor.read()
            except Exception as exc:
                print(f"  {sensor.id}: ERROR {exc}", file=sys.stderr)
                continue
            app.latest.update(readings)
            rows.extend(readings)
        if args.json:
            print(
                json.dumps(
                    [
                        {
                            "name": item.name,
                            "value": item.value,
                            "unit": item.unit,
                            "quality": item.quality,
                            "timestamp": item.timestamp.isoformat(),
                        }
                        for item in rows
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        print(f"{'medición':24} {'valor':>12} {'unidad':8} calidad")
        for item in rows:
            value = "sí" if item.value is True else "no" if item.value is False else f"{item.value:.3f}"
            print(f"{item.name:24} {value:>12} {item.unit:8} {item.quality}")
        return 0
    finally:
        app.close()


def cmd_switch(args: argparse.Namespace) -> int:
    from hidro.bootstrap import build_app

    settings = _settings(verbose=True)
    config = load_config(settings.config_path, settings.mode_override)
    app = build_app(settings, config)
    try:
        output = app.outputs.get(args.id)
        if output is None:
            print(f"no existe la salida '{args.id}'", file=sys.stderr)
            return 2
        if hasattr(output, "dispense_ml"):
            print(
                f"'{args.id}' es una bomba peristáltica; usar 'hidro pump {args.id} --ml N'",
                file=sys.stderr,
            )
            return 2
        if args.state == "off":
            output.off()
            print(f"{args.id}: apagado")
            return 0
        output.on()
        print(f"{args.id}: encendido por {args.seconds:g} s (Ctrl+C para cortar)")
        try:
            time.sleep(args.seconds)
        except KeyboardInterrupt:
            pass
        finally:
            output.off()
            print(f"{args.id}: apagado")
        return 0
    finally:
        app.close()


def cmd_pump(args: argparse.Namespace) -> int:
    from hidro.bootstrap import build_app

    settings = _settings(verbose=True)
    config = load_config(settings.config_path, settings.mode_override)
    app = build_app(settings, config)
    try:
        output = app.outputs.get(args.id)
        if output is None or not hasattr(output, "dispense_ml"):
            print(f"'{args.id}' no es una bomba peristáltica", file=sys.stderr)
            return 2
        ml_per_min = getattr(output, "ml_per_min", None)
        if ml_per_min is None:
            print(f"la bomba '{args.id}' no está calibrada", file=sys.stderr)
            return 2
        seconds = args.ml / ml_per_min * 60.0
        if not args.yes:
            answer = input(
                f"Dosificar {args.ml:g} ml con {args.id} ({seconds:.0f} s)? [s/N] "
            ).strip().lower()
            if answer not in {"s", "si", "sí", "y", "yes"}:
                print("cancelado")
                return 1
        actual = output.dispense_ml(args.ml)
        print(f"{args.id}: {args.ml:g} ml en {actual:.1f} s")
        return 0
    except (PumpNotCalibrated, DoseTooLong) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        app.close()


def cmd_alert_test(_args: argparse.Namespace) -> int:
    from hidro.alerts.telegram import TelegramNotifier

    settings = _settings(verbose=True)
    notifier = TelegramNotifier(settings.telegram.token, settings.telegram.chat_id)
    if not notifier.configured:
        print(
            "Telegram no configurado: completar TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en .env",
            file=sys.stderr,
        )
        return 2
    local = datetime.now(UTC).astimezone(settings.timezone)
    text = (
        "✅ PRUEBA DE ALERTAS\n"
        f"hidro v{__version__} — {local:%Y-%m-%d %H:%M:%S %Z}\n"
        "Si ves este mensaje, las notificaciones funcionan."
    )
    ok = notifier.send(text)
    print("mensaje enviado" if ok else "no se pudo enviar el mensaje", file=sys.stderr)
    return 0 if ok else 1


def cmd_safety_reset(args: argparse.Namespace) -> int:
    settings = _settings(verbose=True)
    config = load_config(settings.config_path, settings.mode_override)
    store = CalibrationStore(settings.data_dir / "calibration.yaml")
    del store  # se conserva la calibración; solo se reinicia la seguridad
    guard = SafetyGuard(settings.data_dir / "safety.json", config.dosing.limits)
    guard.reset(args.pump)
    print(f"seguridad reiniciada: {args.pump or 'todas las bombas'}")
    return 0


def cmd_version(_args: argparse.Namespace) -> int:
    print(f"hidro {__version__}")
    return 0


def cmd_run(_args: argparse.Namespace) -> int:
    from hidro.main import run

    return run()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hidro",
        description="Control y monitoreo de un sistema hidropónico NFT",
    )
    parser.add_argument("--version", action="version", version=f"hidro {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="ejecutar el ciclo principal")
    run_parser.set_defaults(func=cmd_run)

    read_parser = subparsers.add_parser("read", help="leer todos los sensores una vez")
    read_parser.add_argument("--json", action="store_true", help="salida JSON")
    read_parser.add_argument("-v", "--verbose", action="store_true")
    read_parser.set_defaults(func=cmd_read)

    check_parser = subparsers.add_parser("config-check", help="validar la configuración")
    check_parser.set_defaults(func=cmd_config_check)

    switch_parser = subparsers.add_parser("switch", help="accionar un relé")
    switch_parser.add_argument("id")
    switch_parser.add_argument("state", choices=["on", "off"])
    switch_parser.add_argument(
        "--seconds", type=float, default=5.0, help="tiempo encendido antes de apagar"
    )
    switch_parser.set_defaults(func=cmd_switch)

    pump_parser = subparsers.add_parser("pump", help="dosificar con una bomba peristáltica")
    pump_parser.add_argument("id")
    pump_parser.add_argument("--ml", type=float, required=True)
    pump_parser.add_argument("--yes", action="store_true", help="no pedir confirmación")
    pump_parser.set_defaults(func=cmd_pump)

    alert_parser = subparsers.add_parser("alert-test", help="enviar un mensaje de prueba")
    alert_parser.set_defaults(func=cmd_alert_test)

    safety_parser = subparsers.add_parser(
        "safety-reset", help="reiniciar bloqueos de seguridad"
    )
    safety_parser.add_argument("pump", nargs="?", default=None)
    safety_parser.set_defaults(func=cmd_safety_reset)

    version_parser = subparsers.add_parser("version", help="mostrar la versión")
    version_parser.set_defaults(func=cmd_version)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(f"error de configuración:\n{exc}", file=sys.stderr)
        return 2
    except DriverError as exc:
        print(f"error de hardware: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("interrumpido", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
