"""CLI de operación y calibración."""

from __future__ import annotations

import argparse
import json
import math
import signal
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from hidro import __version__
from hidro.calibration import (
    MAX_ABS_SLOPE,
    MAX_EC_K,
    MIN_ABS_SLOPE,
    MIN_EC_K,
    CalibrationStore,
    PHCalibration,
)
from hidro.config import (
    ADCConfig,
    AppConfig,
    ConfigError,
    DS18B20Config,
    FlowHallConfig,
    PeristalticConfig,
    PHAnalogConfig,
    RelayConfig,
    TDSAnalogConfig,
    load_config,
)
from hidro.control.safety import SafetyGuard
from hidro.drivers.ads1115 import ADS1115, read_stable_voltage
from hidro.drivers.base import (
    DoseTooLong,
    DriverError,
    LatestReadings,
    PumpNotCalibrated,
)
from hidro.drivers.relay import RelayOutput
from hidro.drivers.tds_analog import calibration_k, gravity_polynomial
from hidro.logging_setup import setup_logging
from hidro.settings import Settings


def _settings(verbose: bool = False) -> Settings:
    settings = Settings.from_env()
    setup_logging(settings.log_level if verbose else "WARNING", "text")
    return settings


def _require_hardware(config: AppConfig, action: str) -> bool:
    if config.mode != "hardware":
        print(
            f"{action} requiere el modo hardware "
            "(HIDRO_HARDWARE=hardware)",
            file=sys.stderr,
        )
        return False
    return True


def _ask_temperature(default_c: float, supplied: float | None) -> float:
    if supplied is None:
        raw = input(f"Temperatura de la solución [{default_c:.1f} °C]: ").strip()
        value = default_c if not raw else float(raw.replace(",", "."))
    else:
        value = supplied
    if not math.isfinite(value) or not -5.0 <= value <= 60.0:
        raise ValueError("temperatura fuera del rango -5 a 60 °C")
    return value


def _confirm(prompt: str) -> bool:
    answer = input(f"{prompt} [s/N] ").strip().lower()
    return answer in {"s", "si", "sí", "y", "yes"}


# Una sonda de pH deriva lentamente durante uno o dos minutos: la lectura se da
# por estable solo si se mantiene dentro de la tolerancia durante 10 s. 2 mV son
# unos 0,02 pH con la pendiente esperada tras el divisor (~0,12 V/pH).
PH_TOLERANCE_MV = 2.0
EC_TOLERANCE_MV = 5.0
STABLE_WINDOW_S = 10.0
SAMPLE_INTERVAL_S = 0.5
PH_STABLE_TIMEOUT_S = 180.0
EC_STABLE_TIMEOUT_S = 60.0
PENDING_MAX_AGE = timedelta(hours=1)
NOMINAL_PH_SLOPE_V = 0.12
INTERFERENCE_PH = 0.05


def _stable_voltage(
    adc: Any, adc_config: ADCConfig, *, tolerance_mv: float, timeout_s: float
) -> float:
    print(
        f"Leyendo ADS1115 en 0x{adc_config.address:02X}, canal {adc_config.channel}, "
        f"hasta que varíe menos de {tolerance_mv:g} mV durante {STABLE_WINDOW_S:g} s..."
    )

    def show(value: float) -> None:
        print(f"  {value:.4f} V")

    return read_stable_voltage(
        lambda: adc.read_median_voltage(
            adc_config.channel, adc_config.gain_v, adc_config.samples
        ),
        tolerance_v=tolerance_mv / 1000.0,
        window=int(STABLE_WINDOW_S / SAMPLE_INTERVAL_S),
        timeout_s=timeout_s,
        interval_s=SAMPLE_INTERVAL_S,
        on_sample=show,
    )


def _probe_temperature(config: AppConfig, sensor_id: str | None) -> float | None:
    """Temperatura del DS18B20 de `temperature_from`, para proponerla por defecto."""
    from hidro.drivers.ds18b20 import DS18B20Sensor

    sensor = config.sensor(sensor_id) if sensor_id else None
    if not isinstance(sensor, DS18B20Config):
        return None
    try:
        probe = DS18B20Sensor(
            sensor.id, sensor.serial, offset_c=sensor.offset_c, base_path=sensor.base_path,
            retries=0,
        )
    except DriverError:
        return None
    reading = probe.read()[0]
    if reading.quality != "ok":
        return None
    print(f"{sensor.id} marca {reading.numeric:.1f} °C (ajústalo si no está en la solución)")
    return float(reading.numeric)


def _hold_tds_off(config: AppConfig, runtime: Any) -> dict[str, RelayOutput]:
    """Toma y apaga la alimentación de los TDS.

    Con el núcleo detenido el GPIO queda flotando y un TDS energizado altera el
    pH. Si hidro-core sigue corriendo, el GPIO está ocupado y esto falla con un
    DriverError: evita calibrar mientras el ciclo usa el mismo ADC y relé.
    """
    switches: dict[str, RelayOutput] = {}
    for sensor in config.sensors:
        if not isinstance(sensor, TDSAnalogConfig) or not sensor.power_switch:
            continue
        output = config.output(sensor.power_switch)
        if isinstance(output, RelayConfig) and output.id not in switches:
            switches[output.id] = RelayOutput(
                output.id,
                runtime.gpio_output(output.gpio, active_high=output.active_high, initial=False),
            )
    return switches


def _release(switches: dict[str, RelayOutput]) -> None:
    for switch in switches.values():
        switch.off()


def _print_core_stop_hint() -> None:
    print(
        "Detén hidro-core antes de calibrar; el ADS1115 y el relé no se "
        "pueden compartir con el ciclo principal:"
    )
    print("  docker compose -f compose.yaml -f compose.hardware.yaml stop hidro-core")


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


def cmd_calibrate_flow(args: argparse.Namespace) -> int:
    from hidro.drivers.base import LatestReadings
    from hidro.drivers.flow_hall import (
        FlowCounter,
        FlowHallSensor,
        calculate_pulses_per_liter,
    )
    from hidro.drivers.registry import flow_total_path
    from hidro.drivers.runtime import RuntimeContext

    settings = _settings(verbose=True)
    config = load_config(settings.config_path, settings.mode_override)
    if config.mode != "hardware":
        print(
            "la calibración de flujo requiere el modo hardware "
            "(HIDRO_HARDWARE=hardware)",
            file=sys.stderr,
        )
        return 2
    if args.liters <= 0:
        print("--liters debe ser positivo", file=sys.stderr)
        return 2

    flows = [sensor for sensor in config.sensors if isinstance(sensor, FlowHallConfig)]
    if args.id is not None:
        flows = [sensor for sensor in flows if sensor.id == args.id]
    if not flows:
        print("no se encontró el flujómetro en la configuración", file=sys.stderr)
        return 2
    flow = flows[0]

    print(f"Calibración de flujo: {flow.id} (GPIO{flow.gpio})")
    print(f"Prepara un recipiente graduado y {args.liters:g} L de agua.")
    print("El flujo debe estar detenido antes de empezar.")
    try:
        input("Pulsa Enter para comenzar a contar pulsos... ")
    except (EOFError, KeyboardInterrupt):
        print("\ncalibración cancelada", file=sys.stderr)
        return 130

    store = CalibrationStore(settings.data_dir / "calibration.yaml")
    runtime = RuntimeContext(config.mode, settings.data_dir, store, LatestReadings())
    try:
        counter = FlowCounter(flow.pulses_per_liter)
        FlowHallSensor(
            flow.id,
            runtime.gpio_input(flow.gpio, pull_up=False, bounce_time=None),
            counter,
            flow_total_path(settings.data_dir, flow.id),
        )
        input(f"Vierte exactamente {args.liters:g} L y pulsa Enter al terminar... ")
        pulses_per_liter = calculate_pulses_per_liter(counter.pulses, args.liters)
        previous = store.flow()
        reference = previous.pulses_per_liter if previous else flow.pulses_per_liter
        if not 0.5 <= pulses_per_liter / reference <= 2.0:
            # Ruido en la señal o un volumen mal medido dan valores absurdos que
            # luego dispararían (o esconderían) la alarma de flujo.
            print(
                f"aviso: {pulses_per_liter:.0f} pulsos/L se aleja mucho de la "
                f"referencia ({reference:.0f} pulsos/L)",
                file=sys.stderr,
            )
            answer = input("¿Guardar de todos modos? [s/N] ").strip().lower()
            if answer not in {"s", "si", "sí", "y", "yes"}:
                print("calibración descartada")
                return 1
        store.save_flow(pulses_per_liter=pulses_per_liter)
        print(f"calibración guardada: {pulses_per_liter:.0f} pulsos/L")
        if previous is not None:
            print(f"  valor anterior: {previous.pulses_per_liter:.0f} pulsos/L")
        print("Reinicia hidro-core para aplicar la nueva calibración.")
        return 0
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (EOFError, KeyboardInterrupt):
        print("\ncalibración cancelada", file=sys.stderr)
        return 130
    finally:
        runtime.close()


MAX_PUMP_CALIBRATION_S = 120.0


def cmd_calibrate_pump(args: argparse.Namespace) -> int:
    from hidro.drivers.runtime import RuntimeContext

    settings = _settings(verbose=True)
    config = load_config(settings.config_path, settings.mode_override)
    if not _require_hardware(config, "la calibración de bomba"):
        return 2

    seconds = float(args.seconds)
    if not math.isfinite(seconds) or not 0.0 < seconds <= MAX_PUMP_CALIBRATION_S:
        print(
            f"--seconds debe ser mayor que 0 y como máximo {MAX_PUMP_CALIBRATION_S:g} s",
            file=sys.stderr,
        )
        return 2

    measured_ml: float | None = None
    if args.ml is not None:
        measured_ml = float(args.ml)
        if not math.isfinite(measured_ml) or measured_ml <= 0:
            print("--ml debe ser finito y positivo", file=sys.stderr)
            return 2

    pumps = [output for output in config.outputs if isinstance(output, PeristalticConfig)]
    if args.id is not None:
        pumps = [pump for pump in pumps if pump.id == args.id]
    elif len(pumps) > 1:
        ids = ", ".join(pump.id for pump in pumps)
        print(f"hay varias bombas ({ids}); indica una con --id", file=sys.stderr)
        return 2
    if not pumps:
        print("no se encontró la bomba en la configuración", file=sys.stderr)
        return 2
    pump = pumps[0]

    print(f"Calibración de bomba: {pump.id} (GPIO{pump.gpio})")
    print("Deja la entrada de la manguera en agua limpia y la salida en un recipiente graduado.")
    print("La bomba debe estar cebada y sin aire antes de empezar.")
    print(f"Se encenderá durante {seconds:g} s; apágala de inmediato si no circula agua.")
    _print_core_stop_hint()
    if not args.yes:
        try:
            if not _confirm(f"¿Encender {pump.id} durante {seconds:g} s?"):
                print("calibración cancelada")
                return 1
        except (EOFError, KeyboardInterrupt):
            print("\ncalibración cancelada", file=sys.stderr)
            return 130

    store = CalibrationStore(settings.data_dir / "calibration.yaml")
    runtime = RuntimeContext(config.mode, settings.data_dir, store, LatestReadings())
    try:
        relay = RelayOutput(
            f"{pump.id}_relay",
            runtime.gpio_output(pump.gpio, active_high=True, initial=False),
        )
        try:
            relay.on()
            print(f"{pump.id}: encendida; esperando {seconds:g} s (Ctrl+C para cortar)...")
            time.sleep(seconds)
        finally:
            relay.off()
        print(f"{pump.id}: apagada")

        # La bomba ya giró: un error de tipeo no debe obligar a repetir la medición.
        while measured_ml is None:
            raw = input("Volumen medido en el recipiente (ml): ").strip().replace(",", ".")
            try:
                value = float(raw)
            except ValueError:
                value = math.nan
            if math.isfinite(value) and value > 0:
                measured_ml = value
            else:
                print("escribe un volumen positivo en ml, por ejemplo 12,5", file=sys.stderr)

        ml_per_min = measured_ml / seconds * 60.0
        if not math.isfinite(ml_per_min) or ml_per_min <= 0:
            raise ValueError("la calibración calculada no es válida")

        previous = store.pump_ml_per_min(pump.id)
        reference = previous if previous is not None else pump.ml_per_min
        if reference is not None and not 0.5 <= ml_per_min / reference <= 2.0:
            print(
                f"aviso: {ml_per_min:.1f} ml/min se aleja mucho de la referencia "
                f"({reference:.1f} ml/min)",
                file=sys.stderr,
            )
            if not args.yes and not _confirm("¿Guardar de todos modos?"):
                print("calibración descartada")
                return 1
        elif not args.yes and not _confirm(f"¿Guardar {ml_per_min:.1f} ml/min?"):
            print("calibración descartada")
            return 1

        store.save_pump(pump.id, ml_per_min)
        print(f"calibración guardada: {pump.id} = {ml_per_min:.2f} ml/min")
        if previous is not None:
            print(f"  valor anterior: {previous:.2f} ml/min")
        print("Reinicia hidro-core para aplicar la nueva calibración.")
        return 0
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (EOFError, KeyboardInterrupt):
        print("\ncalibración cancelada", file=sys.stderr)
        return 130
    finally:
        runtime.close()


def cmd_calibrate_ph(args: argparse.Namespace) -> int:
    from hidro.drivers.runtime import RuntimeContext

    settings = _settings(verbose=True)
    config = load_config(settings.config_path, settings.mode_override)
    if not _require_hardware(config, "la calibración de pH"):
        return 2

    point = float(args.point)
    if not math.isfinite(point) or not 0.0 <= point <= 14.0:
        print("--point debe estar entre 0 y 14 pH", file=sys.stderr)
        return 2

    sensors = [sensor for sensor in config.sensors if isinstance(sensor, PHAnalogConfig)]
    sensor_id = getattr(args, "id", None)
    if sensor_id is not None:
        sensors = [sensor for sensor in sensors if sensor.id == sensor_id]
    if not sensors:
        print("no se encontró el sensor de pH en la configuración", file=sys.stderr)
        return 2
    ph = sensors[0]

    store = CalibrationStore(settings.data_dir / "calibration.yaml")
    pending = store.ph_pending()
    replace_pending = False
    if pending is not None:
        if abs(point - pending.ph) < 0.01:
            print(
                f"Ya hay un punto pendiente de pH {pending.ph:.2f} "
                f"({pending.voltage:.4f} V)."
            )
            if not _confirm("¿Reemplazarlo con una nueva medición?"):
                print("punto pendiente conservado")
                return 1
            replace_pending = True
        elif abs(point - pending.ph) < 1.0:
            print(
                "error: los dos puntos deben diferir al menos 1 pH "
                f"(pendiente: {pending.ph:.2f})",
                file=sys.stderr,
            )
            return 2
        age = datetime.now(UTC) - pending.measured_at
        if not replace_pending and age > PENDING_MAX_AGE:
            print(
                f"aviso: el punto pendiente de pH {pending.ph:.2f} tiene "
                f"{age.total_seconds() / 3600:.1f} h; la sonda pudo derivar desde entonces."
            )
            if not _confirm("¿Combinarlo igual con esta medición?"):
                print("Esta medición quedará como nuevo primer punto.")
                replace_pending = True

    print(f"Calibración de pH: {ph.id}")
    _print_core_stop_hint()
    print("Enjuaga la sonda con agua destilada y sécala suavemente.")
    print(
        "La sonda de pH no debe secarse; después de calibrar, vuelve a "
        "dejarla en agua o en la solución de almacenamiento."
    )
    print(f"Coloca la sonda en la solución de pH {point:.2f}.")
    try:
        supplied = getattr(args, "temperature", None)
        probe = _probe_temperature(config, ph.temperature_from) if supplied is None else None
        default_temp = probe if probe is not None else (
            pending.t_c if pending is not None else 25.0
        )
        temperature = _ask_temperature(default_temp, supplied)
        print(f"Temperatura de calibración: {temperature:.1f} °C")
        input("Pulsa Enter cuando la sonda esté en la solución... ")

        runtime = RuntimeContext(config.mode, settings.data_dir, store, LatestReadings())
        held: dict[str, RelayOutput] = {}
        try:
            held = _hold_tds_off(config, runtime)
            adc = ADS1115(runtime.smbus(1), address=ph.adc.address)
            voltage = _stable_voltage(
                adc,
                ph.adc,
                tolerance_mv=getattr(args, "tolerance_mv", None) or PH_TOLERANCE_MV,
                timeout_s=PH_STABLE_TIMEOUT_S,
            )
            if not _confirm(
                f"¿Guardar el punto pH {point:.2f} con {voltage:.4f} V?"
            ):
                print("punto descartado")
                return 1

            if pending is None or replace_pending:
                store.set_ph_pending(point, voltage, temperature)
                print(f"Punto pH {point:.2f} guardado como pendiente.")
                print(
                    "Enjuaga la sonda y repite el comando con el otro buffer "
                    "(por ejemplo, --point 4.00)."
                )
                return 0

            t_cal = (pending.t_c + temperature) / 2.0
            if abs(pending.t_c - temperature) > 2.0:
                print(
                    "aviso: la temperatura cambió entre los dos puntos; "
                    f"se usa T_cal={t_cal:.1f} °C",
                    file=sys.stderr,
                )
            candidate = PHCalibration(
                v1=pending.voltage,
                ph1=pending.ph,
                v2=voltage,
                ph2=point,
                t_cal=t_cal,
                calibrated_at=datetime.now(UTC),
            )
            slope = candidate.slope
            if not candidate.slope_is_reasonable():
                print(
                    f"error: pendiente {slope:.4f} V/pH fuera del rango "
                    f"{MIN_ABS_SLOPE:g}–{MAX_ABS_SLOPE:g}; no se guardó. "
                    "Revisa la sonda y repite el punto.",
                    file=sys.stderr,
                )
                return 2
            if slope > 0:
                # El PH-4502C entrega más voltaje cuanto más ácida es la solución.
                print(
                    "aviso: la pendiente es positiva; con el PH-4502C debería ser "
                    "negativa. ¿Se intercambiaron las soluciones o el --point?",
                    file=sys.stderr,
                )
                if not _confirm("¿Guardar de todos modos?"):
                    print("calibración descartada; el punto pendiente se conserva")
                    return 1
            if not _confirm(
                f"¿Guardar la calibración pH con pendiente {slope:.4f} V/pH?"
            ):
                print("calibración descartada; el punto pendiente se conserva")
                return 1
            calibration = store.save_ph(
                v1=pending.voltage,
                ph1=pending.ph,
                v2=voltage,
                ph2=point,
                t_cal=t_cal,
            )
            print(
                f"Calibración de pH guardada: pendiente "
                f"{calibration.slope:.4f} V/pH, V7={calibration.v7:.4f} V, "
                f"T_cal={calibration.t_cal:.1f} °C"
            )
            print("hidro-core la usa desde su próxima lectura; vuelve a levantarlo.")
            return 0
        finally:
            _release(held)
            runtime.close()
    except (EOFError, KeyboardInterrupt):
        print("\ncalibración cancelada", file=sys.stderr)
        return 130
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def cmd_calibrate_ec(args: argparse.Namespace) -> int:
    from hidro.drivers.runtime import RuntimeContext
    from hidro.drivers.tds_analog import TDSAnalogSensor

    settings = _settings(verbose=True)
    config = load_config(settings.config_path, settings.mode_override)
    if not _require_hardware(config, "la calibración de EC"):
        return 2

    standard = float(args.standard)
    if not math.isfinite(standard) or standard <= 0:
        print("--standard debe ser positivo (µS/cm)", file=sys.stderr)
        return 2

    sensors = [sensor for sensor in config.sensors if isinstance(sensor, TDSAnalogConfig)]
    sensor_id = getattr(args, "id", None)
    if sensor_id is not None:
        sensors = [sensor for sensor in sensors if sensor.id == sensor_id]
    if not sensors:
        print("no se encontró el sensor de EC/TDS en la configuración", file=sys.stderr)
        return 2
    tds = sensors[0]

    store = CalibrationStore(settings.data_dir / "calibration.yaml")
    previous = store.ec()

    print(f"Calibración de EC: {tds.id}")
    _print_core_stop_hint()
    print("Enjuaga la sonda con agua destilada y colócala en la solución patrón.")
    print(f"Solución patrón: {standard:g} µS/cm.")
    try:
        supplied = getattr(args, "temperature", None)
        probe = _probe_temperature(config, tds.temperature_from) if supplied is None else None
        default_temp = probe if probe is not None else (
            previous.t_c if previous and previous.t_c is not None else 25.0
        )
        temperature = _ask_temperature(default_temp, supplied)
        print(f"Temperatura de calibración: {temperature:.1f} °C")
        input("Pulsa Enter cuando la sonda esté en la solución... ")

        runtime = RuntimeContext(config.mode, settings.data_dir, store, LatestReadings())
        sensor = None
        held: dict[str, RelayOutput] = {}
        try:
            held = _hold_tds_off(config, runtime)
            switch = held.get(tds.power_switch) if tds.power_switch else None
            if switch is None:
                print(
                    "aviso: el TDS no tiene power_switch configurado; "
                    "la sonda permanece alimentada",
                    file=sys.stderr,
                )

            adc = ADS1115(runtime.smbus(1), address=tds.adc.address)
            sensor = TDSAnalogSensor(
                tds.id,
                adc,
                store,
                LatestReadings(),
                channel=tds.adc.channel,
                gain_v=tds.adc.gain_v,
                samples=tds.adc.samples,
                power_switch=switch,
                settle_s=tds.settle_s,
                tds_factor=tds.tds_factor,
                sleep=time.sleep,
            )
            with sensor.powered():
                voltage = _stable_voltage(
                    adc,
                    tds.adc,
                    tolerance_mv=getattr(args, "tolerance_mv", None) or EC_TOLERANCE_MV,
                    timeout_s=EC_STABLE_TIMEOUT_S,
                )

            raw_ec = gravity_polynomial(voltage)
            k = calibration_k(voltage, temperature, standard)
            print(f"Voltaje estabilizado: {voltage:.4f} V")
            print(f"EC de la curva sin calibrar: {raw_ec:.1f} µS/cm")
            print(f"Factor k calculado: {k:.4f}")
            if not MIN_EC_K <= k <= MAX_EC_K:
                print(
                    f"error: k={k:.4f} fuera del rango {MIN_EC_K:g}–{MAX_EC_K:g}; "
                    "revisa la sonda, la solución y el cableado",
                    file=sys.stderr,
                )
                return 2
            if previous is not None:
                print(f"  k anterior: {previous.k:.4f}")
            if not _confirm(f"¿Guardar la calibración EC con k={k:.4f}?"):
                print("calibración descartada")
                return 1
            calibration = store.save_ec(
                k=k,
                standard_us_cm=standard,
                voltage=voltage,
                t_c=temperature,
            )
            print(
                f"Calibración de EC guardada: k={calibration.k:.4f}, "
                f"patrón={standard:g} µS/cm, T={temperature:.1f} °C"
            )
            print("hidro-core la usa desde su próxima lectura; vuelve a levantarlo.")
            return 0
        finally:
            if sensor is not None:
                sensor.close()
            _release(held)
            runtime.close()
    except (EOFError, KeyboardInterrupt):
        print("\ncalibración cancelada", file=sys.stderr)
        return 130
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def cmd_interference(args: argparse.Namespace) -> int:
    """Mide el pH con el TDS apagado y encendido (sección 4.6 del plan)."""
    from hidro.drivers.runtime import RuntimeContext

    settings = _settings(verbose=True)
    config = load_config(settings.config_path, settings.mode_override)
    if not _require_hardware(config, "la prueba de interferencia"):
        return 2
    ph = next((s for s in config.sensors if isinstance(s, PHAnalogConfig)), None)
    tds = next(
        (s for s in config.sensors if isinstance(s, TDSAnalogConfig) and s.power_switch), None
    )
    if ph is None or tds is None or tds.power_switch is None:
        print("se necesitan un sensor de pH y un TDS con power_switch", file=sys.stderr)
        return 2

    store = CalibrationStore(settings.data_dir / "calibration.yaml")
    calibration = store.ph()
    slope = abs(calibration.slope) if calibration else NOMINAL_PH_SLOPE_V
    tolerance_mv = getattr(args, "tolerance_mv", None) or PH_TOLERANCE_MV

    print("Prueba de interferencia pH/TDS")
    _print_core_stop_hint()
    print("Pon las dos sondas en el mismo recipiente con agua o solución nutritiva.")
    runtime = RuntimeContext(config.mode, settings.data_dir, store, LatestReadings())
    held: dict[str, RelayOutput] = {}
    try:
        input("Pulsa Enter cuando estén sumergidas... ")
        held = _hold_tds_off(config, runtime)
        adc = ADS1115(runtime.smbus(1), address=ph.adc.address)

        print("pH con el TDS apagado:")
        off_v = _stable_voltage(adc, ph.adc, tolerance_mv=tolerance_mv, timeout_s=PH_STABLE_TIMEOUT_S)
        switch = held[tds.power_switch]
        switch.on()
        try:
            time.sleep(tds.settle_s)
            print("pH con el TDS encendido:")
            on_v = _stable_voltage(
                adc, ph.adc, tolerance_mv=tolerance_mv, timeout_s=PH_STABLE_TIMEOUT_S
            )
        finally:
            switch.off()

        delta_v = on_v - off_v
        delta_ph = abs(delta_v) / slope
        basis = "calibración" if calibration else f"pendiente nominal {NOMINAL_PH_SLOPE_V:g} V/pH"
        print(f"Diferencia: {delta_v * 1000:+.1f} mV ≈ {delta_ph:.2f} pH ({basis})")
        if delta_ph >= INTERFERENCE_PH:
            print(
                "Hay interferencia: mantén la secuencia por defecto (TDS apagado al leer pH) "
                "y anota el resultado en PROGRESS.md."
            )
        else:
            print(
                f"No se aprecia interferencia (menos de {INTERFERENCE_PH:g} pH). "
                "Anota el resultado en PROGRESS.md."
            )
        return 0
    except (EOFError, KeyboardInterrupt):
        print("\nprueba cancelada", file=sys.stderr)
        return 130
    finally:
        _release(held)
        runtime.close()

def cmd_flow(args: argparse.Namespace) -> int:
    from hidro.drivers.base import LatestReadings
    from hidro.drivers.flow_hall import FlowCounter, FlowHallSensor
    from hidro.drivers.registry import flow_total_path
    from hidro.drivers.runtime import RuntimeContext

    settings = _settings(verbose=True)
    config = load_config(settings.config_path, settings.mode_override)
    if config.mode != "hardware":
        print(
            "la medición de flujo requiere el modo hardware "
            "(HIDRO_HARDWARE=hardware)",
            file=sys.stderr,
        )
        return 2
    if args.seconds <= 0:
        print("--seconds debe ser positivo", file=sys.stderr)
        return 2

    flows = [sensor for sensor in config.sensors if isinstance(sensor, FlowHallConfig)]
    if args.id is not None:
        flows = [sensor for sensor in flows if sensor.id == args.id]
    if not flows:
        print("no se encontró el flujómetro en la configuración", file=sys.stderr)
        return 2
    flow = flows[0]

    print(f"Flujo: {flow.id} (GPIO{flow.gpio})")
    try:
        input("Abre el flujo y pulsa Enter para medir... ")
    except (EOFError, KeyboardInterrupt):
        print("\nmedición cancelada", file=sys.stderr)
        return 130

    store = CalibrationStore(settings.data_dir / "calibration.yaml")
    calibration = store.flow()
    pulses_per_liter = (
        calibration.pulses_per_liter if calibration is not None else flow.pulses_per_liter
    )
    runtime = RuntimeContext(config.mode, settings.data_dir, store, LatestReadings())
    try:
        counter = FlowCounter(pulses_per_liter)
        FlowHallSensor(
            flow.id,
            runtime.gpio_input(flow.gpio, pull_up=False, bounce_time=None),
            counter,
            flow_total_path(settings.data_dir, flow.id),
            save_every_s=3600,
        )
        counter.sample(time.monotonic())
        print(f"Midiendo durante {args.seconds:g} s...")
        time.sleep(args.seconds)
        rate, total = counter.sample(time.monotonic())
        print(f"caudal: {rate:.3f} L/min")
        print(f"volumen contado: {total:.3f} L")
        if counter.pulses == 0:
            print("no se registraron pulsos; revisa el divisor, la masa y el GPIO")
        return 0
    finally:
        runtime.close()


def cmd_switch(args: argparse.Namespace) -> int:
    from hidro.bootstrap import build_app

    if args.state == "on" and args.seconds <= 0:
        print("--seconds debe ser positivo", file=sys.stderr)
        return 2

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
        # Como PID 1 de `docker compose run`, sin manejador el proceso ignora
        # SIGTERM y Docker termina con SIGKILL: el relé quedaría sin apagar.
        previous = signal.signal(signal.SIGTERM, signal.default_int_handler)
        try:
            output.on()
            print(f"{args.id}: encendido por {args.seconds:g} s (Ctrl+C para cortar)")
            time.sleep(args.seconds)
        except KeyboardInterrupt:
            pass
        finally:
            output.off()
            signal.signal(signal.SIGTERM, previous)
            print(f"{args.id}: apagado")
        return 0
    finally:
        app.close()


def cmd_pump(args: argparse.Namespace) -> int:
    from hidro.bootstrap import build_app

    if not math.isfinite(args.ml) or args.ml <= 0:
        print("--ml debe ser finito y positivo", file=sys.stderr)
        return 2

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
    except (PumpNotCalibrated, DoseTooLong, ValueError) as exc:
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

    flow_parser = subparsers.add_parser("flow", help="medir el caudal durante N segundos")
    flow_parser.add_argument("--seconds", type=float, default=5.0)
    flow_parser.add_argument("--id", default=None, help="id del flujómetro")
    flow_parser.set_defaults(func=cmd_flow)

    calibrate_parser = subparsers.add_parser("calibrate", help="calibrar sensores")
    calibrate_sub = calibrate_parser.add_subparsers(dest="sensor", required=True)
    flow_parser = calibrate_sub.add_parser(
        "flow", help="calibrar los pulsos por litro del flujómetro"
    )
    flow_parser.add_argument("--id", default=None, help="id del flujómetro")
    flow_parser.add_argument(
        "--liters", type=float, default=1.0, help="volumen vertido para calibrar"
    )
    flow_parser.set_defaults(func=cmd_calibrate_flow)

    pump_cal_parser = calibrate_sub.add_parser(
        "pump", help="calibrar el caudal de una bomba peristáltica"
    )
    pump_cal_parser.add_argument("--id", default=None, help="id de la bomba")
    pump_cal_parser.add_argument(
        "--seconds",
        type=float,
        default=60.0,
        help="tiempo encendida para medir el volumen (60 s por defecto)",
    )
    pump_cal_parser.add_argument(
        "--ml", type=float, default=None, help="volumen medido; si se omite, se pregunta"
    )
    pump_cal_parser.add_argument("--yes", action="store_true", help="no pedir confirmación")
    pump_cal_parser.set_defaults(func=cmd_calibrate_pump)

    ph_parser = calibrate_sub.add_parser("ph", help="calibrar pH en dos puntos")
    ph_parser.add_argument("--point", type=float, required=True, help="pH del buffer")
    ph_parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="temperatura de la solución en °C (si se omite, se pregunta)",
    )
    ph_parser.add_argument(
        "--tolerance-mv",
        type=float,
        default=None,
        help=f"estabilidad exigida en mV (por defecto {PH_TOLERANCE_MV:g})",
    )
    ph_parser.add_argument("--id", default=None, help="id del sensor de pH")
    ph_parser.set_defaults(func=cmd_calibrate_ph)

    ec_parser = calibrate_sub.add_parser("ec", help="calibrar EC con una solución patrón")
    ec_parser.add_argument(
        "--standard", type=float, required=True, help="conductividad del patrón en µS/cm"
    )
    ec_parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="temperatura de la solución en °C (si se omite, se pregunta)",
    )
    ec_parser.add_argument(
        "--tolerance-mv",
        type=float,
        default=None,
        help=f"estabilidad exigida en mV (por defecto {EC_TOLERANCE_MV:g})",
    )
    ec_parser.add_argument("--id", default=None, help="id del sensor de EC/TDS")
    ec_parser.set_defaults(func=cmd_calibrate_ec)

    interference_parser = subparsers.add_parser(
        "interference", help="medir si el TDS encendido altera el pH"
    )
    interference_parser.add_argument(
        "--tolerance-mv", type=float, default=None, help="estabilidad exigida en mV"
    )
    interference_parser.set_defaults(func=cmd_interference)

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
        "safety", help="gestionar los bloqueos de seguridad"
    )
    safety_sub = safety_parser.add_subparsers(dest="safety_action", required=True)
    safety_reset_parser = safety_sub.add_parser(
        "reset", help="reiniciar bloqueos de seguridad"
    )
    safety_reset_parser.add_argument("pump", nargs="?", default=None)
    safety_reset_parser.set_defaults(func=cmd_safety_reset)

    # Se conserva el nombre anterior para no romper los comandos ya documentados.
    legacy_safety_parser = subparsers.add_parser(
        "safety-reset", help="reiniciar bloqueos de seguridad (nombre anterior)"
    )
    legacy_safety_parser.add_argument("pump", nargs="?", default=None)
    legacy_safety_parser.set_defaults(func=cmd_safety_reset)

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
