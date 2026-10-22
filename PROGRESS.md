# Bitácora del proyecto

Registro de fases, verificaciones y puntos de control. No se pasa a la
siguiente fase sin cerrar el punto de control humano correspondiente.

## Estado

| Fase | Estado | Rama |
| --- | --- | --- |
| 0 — Esqueleto en el PC (mock) | Implementada; falta confirmación de Telegram | `fase-0` |
| 1 — Raspberry + sondas de temperatura | Pendiente (sin hardware) | — |
| 2 — Flotador y relés | Pendiente | — |
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
| Mediaciones en InfluxDB | `air_temp`, `air_humidity`, `air_dew_point`, `air_vpd`, `water_temp_1`, `water_temp_2`, `ph`, `ec`, `ec_ppm`, `flow`, `tank_level_low`, `heartbeat` |
| Latido | Punto `heartbeat` con `mode=mock` y ciclo creciente cada 30 s |
| Dashboard Grafana | `uid=hidro` aprovisionado y visible en el puerto 3300 |
| Datasource Grafana | `OK`, 3 buckets detectados |
| Alertas Grafana | Contact point, política y regla `hidro-sin-datos` aprovisionados (verificados con credenciales de prueba; los archivos generados no se versionan) |

Durante la verificación se corrigieron: el montaje anidado de provisioning de
Grafana (ahora se montan los subdirectorios), el tipado del `chatid` de
Telegram (se genera el YAML con comillas) y el arranque de Grafana sin token
(las alertas son un override opcional).

### Punto de control humano (pendiente)

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

### Pendiente antes de la Fase 1

- Confirmar la preparación de la Pi: Docker, grupos `gpio`/`i2c`, I2C y 1-Wire
  activados, GIDs en `.env`.
- Obtener los seriales `28-…` de las dos sondas DS18B20.
- Copiar `.env` a la Pi y verificar credenciales de git para el repositorio
  privado.

## Fase 1 — Raspberry + sondas de temperatura

Pendiente de hardware.

## Fase 2 — Flotador y relés

Pendiente de hardware.

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
