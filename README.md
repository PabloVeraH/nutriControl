# hidro

Sistema de control y monitoreo para un cultivo hidropónico NFT. El núcleo
(`hidro-core`) lee sensores, publica en InfluxDB, evalúa reglas de alarma y
dosificación, y avisa por Telegram. Grafana muestra el estado del cultivo.

Estado actual: **Fase 0** (simulación completa en el PC, sin hardware). El
detalle de lo hecho, lo verificado y los puntos de control está en
[`PROGRESS.md`](PROGRESS.md). El diseño completo está en
[`PLAN_IMPLEMENTACION.md`](PLAN_IMPLEMENTACION.md).

## Arquitectura

| Servicio | Imagen | Puerto | Memoria | Función |
| --- | --- | --- | --- | --- |
| `hidro-core` | `core/Dockerfile` | — | 256 MB | Sensores, control, alertas, Influx |
| `influxdb` | `influxdb:2.7` | interno 8086 | 768 MB | Series de tiempo (retención 365 días) |
| `grafana` | `grafana-oss:11.3.0` | `${GRAFANA_PORT}` (3300) | 384 MB | Dashboard y alerta de "sin datos" |

Solo Grafana publica un puerto. InfluxDB y el núcleo quedan en la red interna
de Docker Compose.

## Inicio rápido en el PC (modo simulado)

Requisitos: Docker Desktop con Compose v2 y, para desarrollo, Python 3.12.

```bash
cp .env.example .env
```

Genera secretos reales para `.env` (no dejes los valores de ejemplo):

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Usa valores distintos para `INFLUX_TOKEN`, `INFLUX_ADMIN_PASSWORD` y
`GRAFANA_ADMIN_PASSWORD`.

```bash
docker compose -f compose.yaml -f compose.mock.yaml up -d --build
```

- Grafana: <http://localhost:3300> con `GRAFANA_ADMIN_USER` y
  `GRAFANA_ADMIN_PASSWORD`. El dashboard "hidro — sistema NFT" y el datasource
  se aprovisionan solos.
- Logs del núcleo: `docker compose -f compose.yaml -f compose.mock.yaml logs -f hidro-core`
- Estado: `docker compose -f compose.yaml -f compose.mock.yaml ps`
- Lectura puntual de sensores simulados:
  `docker compose -f compose.yaml -f compose.mock.yaml exec hidro-core hidro read`

El modo mock no toca GPIO, I2C ni 1-Wire. Los sensores simulados publican cada
30 s; el dashboard se actualiza solo.

## Telegram y alertas de Grafana

1. Crea un bot con [@BotFather](https://t.me/BotFather) y guarda el token.
2. Envía un mensaje al bot y obtén el chat id (`getUpdates` o un bot como
   `@userinfobot`).
3. Completa en `.env`:

   ```dotenv
   TELEGRAM_BOT_TOKEN=123456:ABC...
   TELEGRAM_CHAT_ID=123456789
   ```

4. Genera la configuración de alertas de Grafana (el chat id debe quedar entre
   comillas en el YAML; Grafana interpreta los números como números y rechaza
   el contact point):

   ```bash
   python scripts/render_grafana_alerting.py
   ```

5. Levanta el stack con el override de alertas:

   ```bash
   docker compose -f compose.yaml -f compose.mock.yaml -f compose.alerts.yaml up -d
   ```

6. Prueba el aviso desde el núcleo:

   ```bash
   docker compose -f compose.yaml -f compose.mock.yaml exec hidro-core hidro alert-test
   ```

La alerta "hidro-core sin datos" de Grafana se dispara cuando no hay latidos en
InfluxDB durante 10 minutos y también envía a Telegram. Los archivos generados
quedan en `data/grafana/alerting/`, que no se versiona.

## Comandos del núcleo

Dentro del contenedor (o con el paquete instalado en el host):

| Comando | Descripción |
| --- | --- |
| `hidro config-check` | Valida `config/hidro.yaml` |
| `hidro read [--json]` | Lee todos los sensores una vez |
| `hidro switch <id> on\|off [--seconds N]` | Acciona un relé (por defecto 5 s) |
| `hidro pump <id> --ml N [--yes]` | Dosifica con una bomba peristáltica |
| `hidro alert-test` | Envía un mensaje de prueba a Telegram |
| `hidro safety-reset [pump]` | Reinicia los bloqueos de seguridad |
| `hidro version` | Muestra la versión |
| `hidro run` | Ciclo principal (lo usa el contenedor) |

Ejemplos:

```bash
docker compose -f compose.yaml -f compose.mock.yaml exec hidro-core hidro read
docker compose -f compose.yaml -f compose.mock.yaml exec hidro-core hidro switch tds_power on
```

`hidro pump` pide confirmación salvo que se use `--yes`. La dosificación está
desactivada (`dosing.enabled: false`) y en `dry_run: true` hasta la Fase 6.

## Configuración

Todo lo agronómico vive en `config/hidro.yaml`:

- `read_interval_s`: período de lectura y publicación.
- `sensors`: sondas DS18B20, HTU21D, flotador, flujómetro, pH y EC, con su
  calibración y parámetros de simulación (`mock`).
- `outputs`: relé del TDS y bomba peristáltica.
- `ranges`, `monitoring`: umbrales de alarma y tiempos sostenidos.
- `dosing`: dosis, intervalos, límites y modo `dry_run`.
- `alerts`: Telegram, repetición de críticas y resumen diario.

Variables de entorno principales (`.env`, nunca versionado):

| Variable | Uso |
| --- | --- |
| `TZ` | Zona horaria (por defecto `America/Santiago`) |
| `GRAFANA_PORT` | Puerto publicado de Grafana (3300) |
| `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` | Acceso a Grafana |
| `INFLUX_ORG` / `INFLUX_BUCKET` | Organización y bucket; el bucket debe ser `hidro` |
| `INFLUX_TOKEN` / `INFLUX_ADMIN_PASSWORD` | Secretos de InfluxDB |
| `INFLUX_RETENTION` | Retención (8760h = 365 días) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Avisos |
| `DEPLOY_HOST` / `DEPLOY_PATH` | Destino SSH para `deploy.sh` |
| `GPIO_GID` / `I2C_GID` | Grupos del host en la Pi |

Los overrides fijan el modo: `compose.mock.yaml` fuerza `mock` y
`compose.hardware.yaml` fuerza `hardware`. No hace falta definir
`HIDRO_HARDWARE` a mano.

### Datos persistentes

El volumen `hidro_data` (montado en `/data`) guarda `calibration.yaml`,
`heartbeat.json`, contadores de flujo y el estado de seguridad. InfluxDB y
Grafana usan sus propios volúmenes. Para respaldar:

```bash
docker run --rm -v hidro_hidro_data:/data -v "$PWD/backup:/backup" alpine \
  tar czf /backup/hidro_data.tgz -C /data .
```

## Desarrollo y pruebas

```bash
cd core
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check src tests
mypy src
```

Las pruebas cubren fórmulas, calibración, reglas de dosificación y límites de
seguridad. `smbus2` no funciona en Windows: los drivers reales solo se importan
bajo demanda; en el PC se usa el modo mock.

## Despliegue en la Raspberry Pi

La Pi comparte equipo con otros servicios. El stack está limitado a 256 + 768 +
384 MB y Grafana usa el puerto 3300.

Preparación del host (una vez):

```bash
sudo apt update && sudo apt full-upgrade -y
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"   # cerrar y abrir sesión
sudo raspi-config nonint do_i2c 0
sudo raspi-config nonint do_onewire 0
sudo reboot
```

Después del reinicio, verifica y anota los GIDs:

```bash
getent group gpio i2c
ls -l /dev/i2c-1 /dev/gpiochip0
ls /sys/bus/w1/devices/
```

Completa en el `.env` de la Pi `GPIO_GID`, `I2C_GID`, `DEPLOY_HOST` y
`DEPLOY_PATH`. La primera vez hay que copiar el `.env` a la Pi (no está en git):

```bash
scp .env usuario@host:~/nutriControl/.env
```

Despliegue:

```bash
./deploy.sh
```

El script exige un árbol limpio, empuja la rama actual, entra por SSH, clona el
repositorio si falta, hace `git pull`, genera las alertas de Grafana y levanta:

```bash
docker compose -f compose.yaml -f compose.hardware.yaml -f compose.alerts.yaml up -d --build
```

Requiere `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` porque el stack de la Pi
siempre aprovisiona las alertas. Variables útiles: `DEPLOY_SKIP_PUSH=1` (no
empujar) y `DEPLOY_ALLOW_DIRTY=1` (permitir cambios sin commitear).

La Pi necesita credenciales de git para el repositorio privado (clave SSH o
token). No las guardes en el repositorio.

## Operación

- Reinicio automático: `restart: unless-stopped` en los tres servicios.
- Salud del núcleo: healthcheck por latido; `docker compose ps` muestra
  `healthy` o `unhealthy`.
- Grafana avisa por Telegram si el núcleo deja de publicar durante 10 minutos.
- Retención de InfluxDB: 365 días (por la RAM y el disco compartidos de la Pi).
- Actualización: `git pull` (o `./deploy.sh` desde el PC) y `up -d --build`.

## Seguridad

- `.env` está ignorado por git. No commitear tokens ni contraseñas.
- El núcleo no corre `privileged`: recibe solo `/dev/i2c-1`,
  `/dev/gpiochip0`, `/sys` en solo lectura y los GIDs necesarios.
- Solo Grafana publica un puerto; InfluxDB no se expone.
- Rota los secretos si alguna vez se filtran y revisa `git status` antes de
  cada commit.

## Problemas frecuentes

| Síntoma | Causa / solución |
| --- | --- |
| `No module named 'fcntl'` en Windows | `smbus2` no existe en Windows; usa modo mock |
| Grafana reinicia y no arranca | Faltan las alertas generadas o el token; ejecuta `scripts/render_grafana_alerting.py` |
| Grafana dice "sin datos" | El núcleo no publica: revisa `docker compose logs hidro-core` y el latido |
| La dosificación no actúa | `dosing.enabled`/`dry_run`, bloqueo de seguridad o falta calibrar `ml_per_min` |
| Bomba bloqueada tras un fallo | Corrige la causa y ejecuta `hidro safety-reset` |
| `Permission denied` en `/dev` | GIDs incorrectos en `.env` o falta `group_add` (usa `compose.hardware.yaml`) |
| DS18B20 marca 85 °C | Sonda desconectada, mal cableada o serial equivocado |
| El pH o el EC no cuadran | Calibrar en la Fase 5; el TDS se mide con el pH apagado |

## Estructura

```
compose.yaml              # servicios base
compose.mock.yaml         # PC: modo simulado
compose.hardware.yaml     # Pi: dispositivos y grupos
compose.alerts.yaml       # Grafana: alertas Telegram (requiere render)
config/hidro.yaml         # hardware declarado, rangos y reglas
config/grafana/           # provisioning de datasource, dashboard y alertas
core/src/hidro/           # paquete: drivers, control, alertas, storage, CLI
core/tests/               # pruebas sin hardware
scripts/                  # utilidades de despliegue
deploy.sh                 # PC → Pi por SSH
```
