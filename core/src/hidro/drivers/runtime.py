"""Recursos compartidos por los drivers (I2C, GPIO, calibraciones)."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from hidro.calibration import CalibrationStore
from hidro.drivers.base import DriverError, LatestReadings, Switch
from hidro.logging_setup import get_logger

logger = get_logger("runtime")


class RuntimeContext:
    """Crea y reutiliza buses I2C y dispositivos GPIO."""

    def __init__(
        self,
        mode: str,
        data_dir: Path,
        calibration: CalibrationStore,
        latest: LatestReadings,
    ) -> None:
        self.mode = mode
        self.data_dir = data_dir
        self.calibration = calibration
        self.latest = latest
        self.outputs: dict[str, Switch] = {}
        # Se comparte con el ciclo principal: SIGTERM corta una dosis en curso.
        self.stop_event = threading.Event()
        self._i2c: dict[int, Any] = {}
        self._devices: list[Any] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ I2C

    def smbus(self, bus: int) -> Any:
        with self._lock:
            if bus in self._i2c:
                return self._i2c[bus]
            if self.mode == "hardware":
                device = Path(f"/dev/i2c-{bus}")
                if not device.exists():
                    raise DriverError(
                        f"no existe {device}; activar I2C en la Raspberry y revisar "
                        "que el contenedor tenga el dispositivo montado"
                    )
            try:
                from smbus2 import SMBus
            except ImportError as exc:  # pragma: no cover - depende del hardware
                raise DriverError(
                    "falta smbus2; instalar las dependencias de hardware"
                ) from exc
            handle = SMBus(bus)
            self._i2c[bus] = handle
            return handle

    # ------------------------------------------------------------------ GPIO

    def gpio_output(self, gpio: int, *, active_high: bool = True, initial: bool = False) -> Any:
        self._require_gpiochip()
        try:
            from gpiozero import OutputDevice
        except ImportError as exc:  # pragma: no cover - depende del hardware
            raise DriverError("falta gpiozero; instalar las dependencias de hardware") from exc
        try:
            device = OutputDevice(gpio, active_high=active_high, initial_value=initial)
        except Exception as exc:
            raise _gpio_error(gpio, exc) from exc
        self._devices.append(device)
        return device

    def gpio_input(
        self, gpio: int, *, pull_up: bool | None, bounce_time: float | None = 0.02
    ) -> Any:
        """`pull_up=None` deja la entrada flotante (pull-up/down solo externo)."""
        self._require_gpiochip()
        try:
            from gpiozero import DigitalInputDevice
        except ImportError as exc:  # pragma: no cover - depende del hardware
            raise DriverError("falta gpiozero; instalar las dependencias de hardware") from exc
        # gpiozero exige active_state cuando no hay pull interno.
        active_state = True if pull_up is None else None
        try:
            device = DigitalInputDevice(
                gpio, pull_up=pull_up, active_state=active_state, bounce_time=bounce_time
            )
        except Exception as exc:
            raise _gpio_error(gpio, exc) from exc
        self._devices.append(device)
        return device

    def _require_gpiochip(self) -> None:
        if self.mode != "hardware":
            return
        if not Path("/dev/gpiochip0").exists():
            raise DriverError(
                "no existe /dev/gpiochip0; revisar el mapeo de dispositivos del contenedor"
            )

    # ------------------------------------------------------------------ cierre

    def close(self) -> None:
        for device in reversed(self._devices):
            try:
                device.close()
            except Exception:  # pragma: no cover - cierre defensivo
                logger.warning("no se pudo cerrar un dispositivo GPIO", exc_info=True)
        self._devices.clear()
        for bus, handle in self._i2c.items():
            try:
                handle.close()
            except Exception:  # pragma: no cover - cierre defensivo
                logger.warning("no se pudo cerrar I2C-%s", bus, exc_info=True)
        self._i2c.clear()


def _gpio_error(gpio: int, exc: Exception) -> DriverError:
    return DriverError(
        f"no se pudo abrir GPIO{gpio}: {exc}. Si hidro-core está corriendo, "
        "detenerlo antes (docker compose ... stop hidro-core)"
    )
