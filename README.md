# nutriControl

Sistema de control y monitoreo para un cultivo hidropónico NFT. El núcleo
(`hidro-core`) lee sensores, publica en InfluxDB, evalúa reglas de alarma y
dosificación, y avisa por Telegram. Grafana muestra el estado del cultivo.

Estado actual: **Fase 0** verificada en simulación. Las fases 1, 2, 3 y 4
tienen el software y la verificación listos, pendientes del hardware y del
acceso a la Pi.
El detalle de lo hecho, lo verificado y los puntos de control está en
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
  `GRAFANA_ADMIN_PASSWORD`. El dashboard "nutriControl — sistema NFT" y el datasource
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
| `hidro flow [--seconds N]` | Mide el caudal durante N segundos |
| `hidro calibrate flow [--liters N]` | Calibra los pulsos por litro del flujómetro |
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
bajo demanda; en el PC se usa el modo mock. Las dependencias de desarrollo
incluyen `gpiozero` (sin `lgpio`): `tests/test_gpio_hardware.py` ejercita el
camino de hardware con su `MockFactory`, así un uso incorrecto de la API falla
en el PC y no recién en la Pi.

## Despliegue en la Raspberry Pi

La Pi comparte equipo con otros servicios. El stack está limitado a 256 + 768 +
384 MB y Grafana usa el puerto 3300. La imagen se construye en la propia Pi
(arm64), así que el primer build puede tardar varios minutos.

### Preparación del host (una vez)

Sobre Raspberry Pi OS de 64 bits, con acceso SSH:

```bash
sudo apt update && sudo apt full-upgrade -y
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"   # cerrar y abrir sesión
sudo raspi-config nonint do_i2c 0
sudo raspi-config nonint do_onewire 0
sudo reboot
```

Después del reinicio, comprueba que Docker funciona sin `sudo` y que los buses
están activos:

```bash
docker run --rm hello-world
getent group gpio i2c
ls -l /dev/i2c-1 /dev/gpiochip0
ls /sys/bus/w1/devices/
```

### Diagnóstico del host

Clona el repositorio en la Pi y ejecuta el diagnóstico (no modifica nada):

```bash
cd ~/nutriControl
bash scripts/pi_check.sh
```

Revisa el modelo, la RAM, el disco, Docker, los grupos, los GIDs declarados en
`.env` frente a los reales, `/dev/i2c-1`, `/dev/gpiochip0`, los overlays de I2C
y 1-Wire, las sondas DS18B20 con su temperatura, `i2cdetect` y el puerto 3300.
El script termina con código distinto de cero si algo crítico falla.

### `.env` en la Pi

El `.env` no se versiona. La primera vez cópialo desde el PC:

```bash
scp .env usuario@host:~/nutriControl/.env
```

En la Pi completa `GPIO_GID` e `I2C_GID` con los valores reales de
`getent group gpio i2c`, además de los secretos de InfluxDB, Grafana y
Telegram. En el PC, `DEPLOY_HOST` y `DEPLOY_PATH` indican el destino SSH.
Para que `deploy.sh` no pida contraseña:

```bash
ssh-copy-id usuario@host
```

### Despliegue

```bash
./deploy.sh
```

El script exige un árbol limpio, empuja la rama actual, entra por SSH, clona el
repositorio si falta, hace `git pull`, genera las alertas de Grafana si hay
Telegram y levanta:

```bash
docker compose -f compose.yaml -f compose.hardware.yaml -f compose.alerts.yaml up -d --build
```

Si faltan `TELEGRAM_BOT_TOKEN` o `TELEGRAM_CHAT_ID`, avisa y despliega sin las
alertas de Grafana. Para exigirlas siempre usa `DEPLOY_REQUIRE_ALERTS=1`.
Otras variables: `DEPLOY_SKIP_PUSH=1` (no empujar) y `DEPLOY_ALLOW_DIRTY=1`
(permitir cambios sin commitear).

La Pi necesita credenciales de git para el repositorio privado (clave SSH o
token). No las guardes en el repositorio.

### Fase 1: sondas DS18B20

El driver real ya está implementado y probado; esta fase valida el cableado y
la calibración. Con las sondas conectadas según el plano:

```bash
ls /sys/bus/w1/devices/                       # seriales 28-...
cat /sys/bus/w1/devices/28-xxxxxxxxxxxx/w1_slave
```

Copia los dos seriales en `config/hidro.yaml` (`water_temp_1` y
`water_temp_2`). Después verifica que el contenedor ve el bus y las sondas:

```bash
docker compose -f compose.yaml -f compose.hardware.yaml exec hidro-core ls /sys/bus/w1/devices
docker compose -f compose.yaml -f compose.hardware.yaml exec hidro-core hidro read
```

Hasta que se conecten los sensores de las fases siguientes, sus lecturas
aparecerán como `invalid`; es lo esperado. Si Telegram está activo, llegará un
aviso único por cada sensor ausente ("sin lectura válida"); no se repite salvo
que la condición sea crítica. La prueba de aceptación es tomar una
sonda en la mano y ver subir su temperatura en Grafana.

Si una sonda marca 85 °C o CRC inválida, revisa el cableado, la resistencia de
4,7 kΩ y que el serial del YAML coincida con el del bus.

### Fase 2: flotador y relés

Cablea el flotador (sección 4.3 del plan: GPIO17 con pull-down de 10 kΩ) y el
módulo de relés (4.7: puente en **High**, `DC+` a 5 V, canales en GPIO22 y
GPIO23). **No conectes cargas todavía.**

El valor de `low_when` depende de la orientación física del flotador:

- `low_when: 0` si entrega `0` cuando el estanque está bajo.
- `low_when: 1` si la lógica está invertida.

`low_when` se compara con el nivel físico del pin (el mismo que muestra
`pinctrl get 17`), sea cual sea `pull`. `pull: down` coincide con la
resistencia externa de 10 kΩ a GND; `pull: none` deja solo la externa.

Para comprobarlo hay que detener el núcleo: los GPIO quedan tomados por
`hidro-core` y un segundo proceso no puede abrirlos.

```bash
docker compose -f compose.yaml -f compose.hardware.yaml stop hidro-core
docker compose -f compose.yaml -f compose.hardware.yaml run --rm --no-deps hidro-core read
```

Mueve el flotador y repite la lectura. Ajusta `low_when` en `config/hidro.yaml`
hasta que `tank_level_low` valga `sí` solo con el nivel bajo.

Los relés se prueban sin carga:

```bash
docker compose -f compose.yaml -f compose.hardware.yaml run --rm --no-deps \
  hidro-core switch tds_power on --seconds 3
docker compose -f compose.yaml -f compose.hardware.yaml run --rm --no-deps \
  hidro-core switch tds_power off
```

El canal 1 (bomba, GPIO22) se puede comprobar en el host con
`pinctrl set 22 op dh`, esperar y `pinctrl set 22 op dl`; con la aplicación se
prueba en la Fase 6. `hidro switch` rechaza las bombas peristálticas a
propósito: para dosificar se usa `hidro pump` con la bomba calibrada.

La verificación asistida hace todo lo anterior y además prueba el apagado:

```bash
bash scripts/phase2_check.sh
```

Pide confirmar que no hay cargas, detiene y restaura `hidro-core`, comprueba el
flotador, acciona `tds_power` y guía las pruebas de robustez:

- **SIGTERM:** `docker compose stop hidro-core` con `tds_power` energizado debe
  apagar todas las salidas.
- **SIGKILL:** `docker compose kill hidro-core` mientras `tds_power` está
  energizado. Al liberar el GPIO, la Pi normalmente lo deja como entrada sin
  excitación (esta prueba lo confirma): si el relé queda activo o indeterminado, hay que añadir una resistencia
  pull-down (10 kΩ de `IN` a GND) en la entrada del módulo antes de conectar
  cargas. La carga va siempre en `COM`–`NO`: **nunca en `NC`**, que queda
  cerrado justamente cuando el relé está en reposo. El resultado se registra en
  `PROGRESS.md`.

### Fase 3: flujómetro

Cablea el YF-S401 según la sección 4.4: rojo a 5 V, negro a GND, amarillo al
divisor de 10 kΩ y 20 kΩ, y el punto medio a GPIO27. El divisor es obligatorio:
el GPIO no admite 5 V. Verifica con un multímetro que la señal no supere 3,3 V.

Con el flujo detenido, calibra con un recipiente graduado. El CLI cuenta los
pulsos reales y guarda el resultado en `/data/calibration.yaml`; el valor de
`pulses_per_liter` del YAML queda como referencia:

```bash
docker compose -f compose.yaml -f compose.hardware.yaml stop hidro-core
docker compose -f compose.yaml -f compose.hardware.yaml run --rm --no-deps \
  hidro-core calibrate flow --liters 1
```

El comando pide confirmación para empezar y para terminar. Vierte exactamente
1 L (o el valor de `--liters`) y pulsa Enter. Si no cuenta pulsos, revisa el
divisor, la masa común y que el GPIO sea 27. Si el resultado se aleja a menos
de la mitad o a más del doble de la referencia (la calibración anterior o
`pulses_per_liter`), el comando pide confirmación antes de guardarlo: suele
indicar ruido en la señal o un volumen mal medido.

Si `/data/calibration.yaml` está dañado, el núcleo lo ignora con un aviso y los
comandos de calibración se niegan a sobrescribirlo, para no perder las demás
calibraciones.

Antes de reiniciar, verifica el caudal. Abre el flujo y pulsa Enter:

```bash
docker compose -f compose.yaml -f compose.hardware.yaml run --rm --no-deps \
  hidro-core flow --seconds 5
docker compose -f compose.yaml -f compose.hardware.yaml up -d hidro-core
```

El comando mide durante 5 s y muestra los L/min y el volumen contado. Con el
flujo abierto debe superar `min_l_min` (0,3 L/min). La alarma crítica se activa
tras `alarm_after_s` (60 s) sin caudal suficiente; requiere Telegram configurado,
se repite cada 15 min y bloquea la dosificación.

El flujómetro usa `bounce_time=None` para no perder pulsos a caudales altos; el
filtro de 20 ms se reserva para el flotador.

El caudal se calcula sobre la ventana entre dos lecturas. `hidro read` lee una
sola vez, justo al arrancar, así que el flujo puede aparecer como `invalid`
(ventana menor a 1 s); para ver el caudal se usa `hidro flow`.

### Fase 4: aire (HTU21D)

El HTU21D debe ir soldado a una placa o módulo con pines; no uses cables sueltos
sobre el chip. Alimenta a **3,3 V** (no 5 V), conecta GND, SDA a GPIO2 (pin 3) y
SCL a GPIO3 (pin 5). La mayoría de los módulos incluye las resistencias de
pull-up; si usas el chip suelto, añádelas. La dirección es `0x40`.

Con la Pi encendida y el bus I2C activo:

```bash
i2cdetect -y 1
```

Debe aparecer `40`. Si no aparece, revisa alimentación, masa, soldaduras y que
SDA/SCL no estén intercambiados.

La comprobación asistida detiene `hidro-core`, lee el sensor, valida los rangos
y comprueba que la humedad sube al soplar; al terminar deja el núcleo como estaba:

```bash
bash scripts/phase4_check.sh air
```

También se puede hacer a mano. Con el núcleo detenido para no interferir con el
bus:

```bash
docker compose -f compose.yaml -f compose.hardware.yaml stop hidro-core
docker compose -f compose.yaml -f compose.hardware.yaml run --rm --no-deps hidro-core read
docker compose -f compose.yaml -f compose.hardware.yaml up -d hidro-core
```

La lectura debe incluir `air_temp`, `air_humidity`, `air_vpd` (kPa) y
`air_dew_point` (°C). La temperatura y la humedad deben ser coherentes con el
ambiente; al soplar suavemente sobre el sensor, la humedad debe subir. El VPD se
calcula con Tetens y el punto de rocío con Magnus. Si aparecen lecturas
`invalid` con `CRC inválido`, revisa el cableado y la longitud de los cables
I2C; el driver descarta la trama y el monitor avisa de que el sensor no tiene
lectura.

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
scripts/                  # render de alertas, diagnóstico y verificaciones por fase
deploy.sh                 # PC → Pi por SSH
```
