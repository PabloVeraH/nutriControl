# Bitácora del proyecto

Registro de fases, verificaciones y puntos de control. No se pasa a la
siguiente fase sin cerrar el punto de control humano correspondiente.

## Estado

| Fase | Estado | Rama |
| --- | --- | --- |
| 0 — Esqueleto en el PC (mock) | Implementada y verificada; checkpoint de Telegram pendiente | `fase-0` |
| 1 — Raspberry + sondas de temperatura | En curso; software y documentación listos, validación física pendiente | `fase-1` |
| 2 — Flotador y relés | En curso; drivers, CLI y verificación listos, validación física pendiente | `fase-2` |
| 3 — Flujómetro | Pendiente | — |
| 4 — Aire (HTU21D) | Pendiente | — |
| 5 — pH y TDS (ADS1115) | Pendiente | — |
| 6 — Bomba y dosificación | Pendiente | — |
| 7 — Operación | Pendiente | — |

## Fase 0 — Esqueleto en el PC

**Fecha:** 2026-10-22

### Implementado

- Estructura del repositorio y configuración de Docker Compose en tres capas
  (`compose.yaml`, `compose.mock.yaml`, `compose.hardware.yaml`) más el
  override opcional de alertas (`compose.alerts.yaml`).
- Paquete `hidro`:
  - Carga y validación de `config/hidro.yaml` con pydantic, incluida la
    validación cruzada de GPIO, sensores y reglas.
  - Drivers reales y simuladores para DS18B20, HTU21D, ADS1115, pH, TDS,
    flujómetro, flotador, relé y bomba peristáltica.
  - Registro de drivers con selección mock/hardware y ADC compartido.
  - Cálculos de VPD, punto de rocío y compensaciones de pH/EC.
  - Monitoreo (reglas 7.1), dosificación (7.2) y límites de seguridad (7.3).
  - Alertas con deduplicación, escalado de severidad y recuperación; envío por
    Telegram.
  - Escritura en InfluxDB con buffer, puntos de latido y de dosificación.
  - Ciclo principal con manejo de señales, apagado seguro y healthcheck.
  - CLI: `config-check`, `read`, `switch`, `pump`, `alert-test`,
    `safety-reset`, `version`, `run`.
- Configuración de ejemplo con valores conservadores: dosificación desactivada
  y en `dry_run`, dosis pequeñas y límites por hora/día.
- Grafana con provisioning de datasource, dashboard y alerta de "sin datos".
- `README.md`, `.env.example`, `deploy.sh`, `.gitignore`, `.gitattributes`.
- Material de referencia local en `docs/`, excluido del repositorio.

### Verificación

| Prueba | Resultado |
| --- | --- |
| `pytest` (193 pruebas) | 193 pasan, cobertura 86,85 % |
| `ruff check src tests` | Sin hallazgos |
| `mypy src` | Sin errores |
| `docker compose ... up -d --build` | 3 contenedores levantan; núcleo `healthy` |
| Mediciones en InfluxDB | `air_temp`, `air_humidity`, `air_dew_point`, `air_vpd`, `water_temp_1`, `water_temp_2`, `ph`, `ec`, `ec_ppm`, `flow`, `tank_level_low`, `heartbeat` |
| Latido | Punto `heartbeat` con `mode=mock` y ciclo creciente cada 30 s |
| Dashboard Grafana | `uid=hidro` aprovisionado y visible en el puerto 3300 |
| Datasource Grafana | `OK`, 3 buckets detectados |
| Alertas Grafana | Contact point, política y regla `hidro-sin-datos` aprovisionados (verificados con credenciales de prueba; los archivos generados no se versionan) |

Durante la verificación se corrigieron: el montaje anidado de provisioning de
Grafana (ahora se montan los subdirectorios), el tipado del `chatid` de
Telegram (se genera el YAML con comillas) y el arranque de Grafana sin token
(las alertas son un override opcional).

### Publicación

- Rama `fase-0`, commit `a31c9a2` (`feat(fase-0): esqueleto del sistema NFT en
  modo simulado`), publicado en `origin/fase-0`.
- `main` todavía no existe: se creará al cerrar el checkpoint.

### Punto de control humano (pendiente)

Estado al 2026-10-22: `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` siguen vacíos;
no se ha confirmado la recepción de una alerta real. El resto de la Fase 0 está
verificado.

1. Crear el bot con @BotFather y obtener `TELEGRAM_BOT_TOKEN`.
2. Obtener `TELEGRAM_CHAT_ID`.
3. Completar ambas variables en `.env`.
4. Ejecutar `python scripts/render_grafana_alerting.py`.
5. Levantar `docker compose -f compose.yaml -f compose.mock.yaml -f compose.alerts.yaml up -d`.
6. Confirmar que llega el mensaje de
   `docker compose ... exec hidro-core hidro alert-test`.
7. Revisar el dashboard en <http://localhost:3300>.

### Decisiones

- InfluxDB 2.7 con límites explícitos de memoria y retención de 365 días por la
  Pi de 4 GB compartida.
- Se implementan los drivers reales desde la Fase 0 y se validan con buses y
  archivos falsos; las fases de hardware quedan como validación y calibración.
- El `gpio` del YAML es numeración BCM (GPIO22 = pin físico 15).
- Las alertas de Grafana se generan desde `.env` para no versionar secretos y
  evitar que Grafana interprete el chat id como número.
- `docs/` no se versiona.

### Pendiente para cerrar la Fase 0

- Confirmar el checkpoint de Telegram (token, chat id y mensaje de prueba).

## Fase 1 — Raspberry + sondas de temperatura

**Estado:** en curso. Código, documentación y diagnóstico listos; la validación
física requiere acceso SSH a la Pi y las sondas conectadas.
**Rama:** `fase-1` · **Fecha:** 2026-10-22

### Hecho

- Driver real `ds18b20`: lee `w1_slave`, valida CRC, aplica offset y descarta
  lecturas de 85 °C. Probado con un árbol sysfs falso para lectura válida,
  offset, CRC inválida, 85 °C, trama incompleta, dispositivo ausente y serial
  vacío.
- `compose.hardware.yaml` fuerza `HIDRO_HARDWARE=hardware`, expone
  `/dev/i2c-1` y `/dev/gpiochip0`, monta `/sys` en solo lectura y aplica
  `GPIO_GID`/`I2C_GID`.
- README con la preparación del host, el `.env` de la Pi, el despliegue y la
  secuencia de las sondas.
- `scripts/pi_check.sh`: diagnóstico sin efectos secundarios (modelo, RAM,
  disco, Docker, grupos, GIDs declarados frente a reales, dispositivos,
  overlays de I2C/1-Wire, sondas con temperatura, `i2cdetect`, puerto 3300).
- `deploy.sh` permite desplegar sin alertas mientras Telegram no esté
  configurado; `DEPLOY_REQUIRE_ALERTS=1` las exige.

### Bloqueos

- El acceso SSH no interactivo a la Pi responde
  `Permission denied (publickey,password)`; falta instalar la clave pública del
  PC (`ssh-copy-id`).
- `GPIO_GID` e `I2C_GID` del `.env` son provisionales (997/998); hay que
  reemplazarlos por los valores reales de la Pi.
- No hay sondas DS18B20 conectadas; faltan los seriales `28-…`.
- Por lo anterior no se ejecutó el primer despliegue ni `pi_check.sh` en la Pi.

### Tareas

- [x] Driver real y pruebas.
- [x] Preparación del host documentada.
- [x] Diagnóstico automatizado.
- [x] Despliegue sin alertas permitido.
- [ ] Instalar la clave SSH del PC en la Pi.
- [ ] Ejecutar `bash scripts/pi_check.sh` y corregir fallas.
- [ ] Copiar `.env` a la Pi y ajustar los GIDs.
- [ ] Primer despliegue con `./deploy.sh`.
- [ ] Conectar las sondas, anotar los seriales y actualizar `config/hidro.yaml`.
- [ ] Verificar la temperatura desde el contenedor y en Grafana.

### Punto de control humano

1. `bash scripts/pi_check.sh` termina sin fallas.
2. Los tres servicios quedan `healthy` tras el despliegue.
3. `hidro read` muestra las dos temperaturas de agua con `quality=ok`.
4. Al calentar una sonda con la mano, la temperatura sube en Grafana.
5. El usuario confirma los seriales usados en `config/hidro.yaml`.

Los comandos concretos están en el README, sección
"Despliegue en la Raspberry Pi".

### Publicación

- Rama `fase-1`, commits `09459e9`, `457e522` y `44b4bc8`, publicada en
  `origin/fase-1`.

## Fase 2 — Flotador y relés (sin carga)

**Estado:** en curso. Drivers, CLI y verificación listos; falta el cableado y
la prueba física.
**Rama:** `fase-2` · **Fecha:** 2026-10-22

### Hecho

- `float_switch`: antirrebote configurable y detección de nivel bajo según
  `low_when`; pruebas de estado inicial, debounce y rebote.
- `relay`: salida on/off con `active_high`, sin estado residual al cerrar;
  pruebas de delegación y propagación de errores.
- CLI `hidro switch <id> on|off [--seconds N]`, con apagado garantizado al
  terminar o interrumpir.
- Apagado ordenado con `SIGTERM`/`SIGINT`: todas las salidas se apagan antes de
  cerrar. Se agregaron pruebas de `_shutdown` (todas las salidas apagadas y
  tolerancia a fallos de una salida).
- `scripts/phase2_check.sh`: guía el flotador, el relé `tds_power` y las pruebas
  de `SIGTERM`/`SIGKILL`, deteniendo y restaurando `hidro-core`.
- README con el cableado, `low_when`, exclusividad de GPIO, prueba del canal 1
  con `pinctrl` y mitigaciones de robustez.
- El CLI rechaza las bombas peristálticas en `switch`; el canal 1 se valida con
  `pinctrl` o en la Fase 6.

### Bloqueos

- Persisten el acceso SSH sin clave y la falta de hardware de las fases 1 y 2.
- No se ejecutó `scripts/phase2_check.sh` ni la prueba física de stop/kill.

### Tareas

- [x] Drivers y pruebas.
- [x] CLI `hidro switch`.
- [x] Apagado ordenado y pruebas.
- [x] Verificación asistida.
- [ ] Cablear flotador y módulo de relés sin carga.
- [ ] Confirmar `low_when` con el flotador.
- [ ] Confirmar el clic de `tds_power` y del canal 1.
- [ ] Ejecutar stop/kill y registrar el estado del relé.
- [ ] Definir la mitigación si el relé queda activo tras `SIGKILL`.

### Punto de control humano

1. `tank_level_low` cambia al mover el flotador y `low_when` queda correcto.
2. El módulo de relés hace clic con `hidro switch` y con `pinctrl`.
3. Tras `docker compose stop`, el relé queda apagado.
4. Tras `docker compose kill`, el usuario describe el estado del relé.
5. Si el relé queda activo, se acuerda la mitigación antes de conectar cargas.

### Publicación

- Rama `fase-2`, commits `4e0d333` y posteriores, publicada en `origin/fase-2`.

## Fase 3 — Flujómetro

Pendiente de hardware (resistencias de 10 kΩ y 20 kΩ).

## Fase 4 — Aire (HTU21D)

Pendiente de hardware.

## Fase 5 — pH y TDS (ADS1115)

Pendiente de hardware y soluciones de calibración.

## Fase 6 — Bomba y dosificación

Pendiente de hardware (fuente de 12 V).

## Fase 7 — Operación

Pendiente.
