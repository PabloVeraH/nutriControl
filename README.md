# nutriControl

Sistema de control y monitoreo para un cultivo hidropónico NFT. El núcleo
(`hidro-core`) lee sensores, publica en InfluxDB, evalúa reglas de alarma y
dosificación, y avisa por Telegram. Grafana muestra el estado del cultivo.

Estado actual: **Fase 0** verificada en simulación. Las fases 1 a 7 tienen el
software y las pruebas listos. Las fases 1 a 6 esperan la validación física y la
fase 6 la fuente de 12 V, la calibración real de la bomba y una semana de
operación en `dry_run` antes de habilitar dosis reales. La Fase 7 agrega el
resumen diario, los comandos de Telegram, los respaldos y la operación
documentada; su punto de control sigue pendiente hasta detener `hidro-core` y
recibir la alerta real de Grafana.
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

La alerta "hidro-core sin datos" de Grafana se dispara tras unos 10 minutos sin
latidos en InfluxDB (consulta de 5 min que debe seguir vacía otros 5 min, para
no avisar por un error pasajero) y también envía a Telegram. Los archivos generados
quedan en `data/grafana/alerting/`, que no se versiona.

El núcleo también envía un **resumen diario** a la hora configurada en
`alerts.daily_summary_at` (por defecto 09:00, hora de `TZ`). Resume lecturas
actuales, mínimo/promedio/máximo de las últimas 24 h, dosis aplicadas, alertas
activas, bloqueos y estado de dosificación. Si InfluxDB no responde, el resumen
sale igualmente sin la sección histórica. Si el envío falla, se reintenta en el
siguiente ciclo; el estado se guarda en `/data` para no duplicarlo tras un
reinicio.

### Comandos por Telegram

Solo se aceptan mensajes del chat indicado en `TELEGRAM_CHAT_ID`; los mensajes
de cualquier otro chat se ignoran y quedan en el log local.

| Comando | Efecto |
| --- | --- |
| `/estado` | Lecturas actuales, alertas activas, silencio, bombas bloqueadas, calibración de pH y salidas encendidas |
| `/silenciar [minutos]` | Silencia avisos no críticos durante N minutos (60 por defecto, máximo 1440) |
| `/ayuda` | Muestra la ayuda |

El silencio se guarda en `/data/silence.json` y sobrevive a un reinicio. Las
alertas **críticas** (por ejemplo, flujo detenido) siguen llegando aunque el
resto esté silenciado. Tampoco afecta a las alertas que envía Grafana, como
"hidro-core sin datos": esas salen directamente de Grafana. El offset de lectura
se guarda en
`/data/telegram_offset.json`, así que un reinicio no repite comandos ya
procesados. Los comandos enviados mientras el núcleo estaba detenido se
responden al volver; un `/silenciar` con más de 10 minutos de retraso se ignora
(con aviso) para no tapar las alertas de ese momento.

## Comandos del núcleo

Dentro del contenedor (o con el paquete instalado en el host):

| Comando | Descripción |
| --- | --- |
| `hidro config-check` | Valida `config/hidro.yaml` |
| `hidro read [--json]` | Lee todos los sensores una vez |
| `hidro flow [--seconds N]` | Mide el caudal durante N segundos |
| `hidro calibrate flow [--liters N]` | Calibra los pulsos por litro del flujómetro |
| `hidro calibrate ph --point 6.86\|4.00 [--temperature T]` | Calibra el pH en dos puntos |
| `hidro calibrate ec --standard 1413 [--temperature T]` | Calibra el factor k del TDS/EC |
| `hidro calibrate pump [--id ID] [--seconds 60] [--ml N]` | Calibra el caudal de una bomba peristáltica |
| `hidro interference` | Mide si el TDS encendido altera el pH |
| `hidro switch <id> on\|off [--seconds N]` | Acciona un relé (por defecto 5 s) |
| `hidro pump <id> --ml N [--yes]` | Dosifica manualmente con una bomba calibrada |
| `hidro alert-test` | Envía un mensaje de prueba a Telegram |
| `hidro safety reset [pump]` | Reinicia los bloqueos de seguridad de una bomba o de todas |
| `hidro version` | Muestra la versión |
| `hidro run` | Ciclo principal (lo usa el contenedor) |

Ejemplos:

```bash
docker compose -f compose.yaml -f compose.mock.yaml exec hidro-core hidro read
docker compose -f compose.yaml -f compose.mock.yaml exec hidro-core hidro switch tds_power on
```

`hidro pump` pide confirmación salvo que se use `--yes` y no pasa por las
reglas 7.2 ni por los límites 7.3: úsalo solo para pruebas pequeñas después de
calibrar. La dosificación automática queda habilitada pero en `dry_run: true`
durante la Fase 6; las bombas no se mueven hasta cambiar ese valor con la
aprobación explícita del punto de control.

## Configuración

Todo lo agronómico vive en `config/hidro.yaml`:

- `read_interval_s`: período de lectura y publicación.
- `sensors`: sondas DS18B20, HTU21D, flotador, flujómetro, pH y EC, con su
  calibración y parámetros de simulación (`mock`).
- `outputs`: relé del TDS y bombas peristálticas. Cada bomba puede declarar
  `role: ph_down`, `ph_up`, `nutrient_a` o `nutrient_b`; si se omite, se usan
  los ids convencionales (`pump_ph_down`, etc.). Las reglas toman el primer
  sensor `ph_analog` y el primero `tds_analog`, sin depender de que se llamen
  `ph` y `ec`.
- `ranges`, `monitoring`: umbrales de alarma y tiempos sostenidos.
- `dosing`: dosis, intervalos, límites y modo `dry_run`.
- `alerts`: Telegram, repetición de críticas y hora del resumen diario
  (`daily_summary_at: "HH:MM"`).

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
`heartbeat.json`, contadores de flujo, el estado de seguridad, el silencio de
alertas, el offset de Telegram y el estado del resumen diario. InfluxDB y
Grafana usan sus propios volúmenes.

### Respaldos y restauración

El respaldo incluye el volumen `/data` y un respaldo **completo** de InfluxDB
(bucket `hidro`, organización, tokens y buckets internos): la restauración usa
`influx restore --full`, que reemplaza la instancia entera. No detiene `hidro-core`; InfluxDB toma un respaldo en
caliente.

```bash
bash scripts/backup.sh
```

Crea `backups/<fecha>/` con `hidro-data.tgz`, el directorio `influx/`, una copia
de `config/hidro.yaml` y un `manifest.txt`. La carpeta `backups/` no se
versiona. El `.env` no se incluye: guárdalo por separado y nunca en git.

Para restaurar (reemplaza `/data` y todo InfluxDB, incluidos sus tokens, y
detiene el núcleo y Grafana durante el proceso; el `INFLUX_TOKEN` del `.env`
debe ser el del respaldo):

```bash
bash scripts/restore.sh backups/20261022T090000
```

Pide confirmación. En la Pi usa `compose.hardware.yaml` por defecto; en el PC:

```bash
RESTORE_COMPOSE=compose.mock.yaml bash scripts/restore.sh backups/...
```

Para automatizar una restauración ya revisada, `RESTORE_YES=1`. Si el volumen
tiene otro nombre, defínelo con `HIDRO_DATA_VOLUME`. Después de restaurar,
revisa `docker compose ps`, los logs y una lectura de sensores antes de confiar
en el sistema.

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
docker compose -f compose.yaml -f compose.hardware.yaml stop hidro-core
docker compose -f compose.yaml -f compose.hardware.yaml run --rm --no-deps hidro-core read
docker compose -f compose.yaml -f compose.hardware.yaml up -d hidro-core
```

`hidro read` abre todos los GPIO declarados; con el núcleo corriendo están
ocupados, por eso se detiene antes (con `exec` fallaría).

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

Suelda primero la tira de 4 pines del módulo; un contacto sin soldar da tramas
con CRC inválido intermitentes. Conexiones del módulo: `+` a **3,3 V** (pin 1,
nunca 5 V), `-` a GND, `DA` (SDA) a GPIO2 (pin 3) y `CL` (SCL) a GPIO3 (pin 5).
No hacen falta resistencias de pull-up: la Pi ya trae 1,8 kΩ en GPIO2 y GPIO3,
y el módulo suele traer las suyas. La dirección es `0x40`.

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

También se puede hacer a mano. Hay que detener el núcleo porque `hidro read`
abre todos los GPIO y el bus, y un segundo proceso no puede tomarlos:

```bash
docker compose -f compose.yaml -f compose.hardware.yaml stop hidro-core
docker compose -f compose.yaml -f compose.hardware.yaml run --rm --no-deps hidro-core read
docker compose -f compose.yaml -f compose.hardware.yaml up -d hidro-core
```

La lectura debe incluir `air_temp`, `air_humidity`, `air_vpd` (kPa) y
`air_dew_point` (°C). La temperatura y la humedad deben ser coherentes con el
ambiente; al soplar suavemente sobre el sensor, la humedad debe subir. La humedad
se compensa por temperatura según la hoja de datos (−0,15 %HR/°C respecto de
25 °C, entre 0 y 80 °C); el VPD se calcula con Tetens y el punto de rocío con
Magnus. El driver descarta las tramas con CRC inválido, las que no corresponden
a la medición pedida (bit de estado) y las temperaturas fuera de −40 a 125 °C:
una trama de ceros pasa el CRC pero daría −46,85 °C. En esos casos las cuatro
magnitudes salen `invalid`; revisa el cableado y la longitud de los cables
I2C. El monitor avisa de que el sensor no tiene lectura.

### Fase 5: pH y TDS (ADS1115)

Cablea el ADS1115 a **3,3 V** (`VDD`), GND, SDA en GPIO2 (pin 3) y SCL en
GPIO3 (pin 5), con `ADDR` a GND para que quede en `0x48`. El módulo de pH
PH-4502C se alimenta con 5 V; su salida `Po` **no puede entrar directo a la
Pi**: pasa por un divisor de 10 kΩ y 20 kΩ y llega a `A0`. La sonda TDS se
alimenta a 3,3 V a través del relé `tds_power` (GPIO23) y su salida `A` va
directo a `A1`, porque no supera 2,3 V. La sonda de pH nunca debe secarse:
después de usarla, vuelve a dejarla en agua o en solución de almacenamiento.

Con el bus activo, `i2cdetect -y 1` debe mostrar `40` (HTU21D) y `48`
(ADS1115). Si no aparece `48`, revisa alimentación, GND, SDA/SCL y que `ADDR`
esté a GND.

**Detén el núcleo antes de calibrar.** El ADS1115 y el relé no se comparten con
el ciclo principal:

```bash
docker compose -f compose.yaml -f compose.hardware.yaml stop hidro-core
```

Prepara dos recipientes limpios con solución de pH 6,86 y 4,00 (o 7,00 y 4,00),
agua destilada para enjuagar y un termómetro para la solución. La calibración
de pH es guiada y se guarda en dos pasos; el primer punto queda pendiente en
`/data/calibration.yaml`:

```bash
docker compose -f compose.yaml -f compose.hardware.yaml run --rm --no-deps \
  hidro-core calibrate ph --point 6.86
docker compose -f compose.yaml -f compose.hardware.yaml run --rm --no-deps \
  hidro-core calibrate ph --point 4.00
```

Cada comando pregunta la temperatura de la solución (o usa `--temperature T`);
propone la del DS18B20 de `temperature_from` si está disponible, así que conviene
meter esa sonda en la solución. Pide enjuagar y colocar la sonda y espera a que
el voltaje varíe menos de 2 mV (unos 0,02 pH) durante 10 s, con un máximo de
3 min: una sonda de pH deriva lentamente uno o dos minutos y una lectura
apresurada calibra a mitad de la deriva. Si el ruido no deja estabilizar, afloja
la exigencia con `--tolerance-mv 4`. Mientras mide, el comando mantiene apagado
`tds_power`: con el núcleo detenido ese GPIO queda flotando y el TDS energizado
alteraría el pH. Si el núcleo sigue corriendo, el comando falla porque el GPIO
está ocupado.

Muestra cada lectura y pide confirmación antes de guardar. Si el segundo punto
produce una pendiente fuera de `0,02–0,60 V/pH`, no guarda nada y conserva el
primer punto para reintentar. Si la pendiente es positiva (el PH-4502C entrega
más voltaje cuanto más ácida es la solución), probablemente se intercambiaron
las soluciones o el `--point`, y pide confirmación. Si el primer punto tiene más
de 1 h, ofrece usar la nueva medición como primer punto. Al guardar, el punto
pendiente desaparece y se muestra la pendiente, `V7` y `T_cal`.

Para el TDS/EC usa la solución patrón de 1413 µS/cm a la misma temperatura:

```bash
docker compose -f compose.yaml -f compose.hardware.yaml run --rm --no-deps \
  hidro-core calibrate ec --standard 1413
```

El comando enciende `tds_power`, espera `settle_s` (2 s), estabiliza la lectura
(5 mV durante 10 s, máximo 1 min; ajustable con `--tolerance-mv`) y apaga el
relé incluso si falla la medición. Calcula `k` y lo rechaza si queda
fuera de `0,1–10`, para no guardar una calibración absurda; pide confirmación y
lo escribe en `/data/calibration.yaml`. Si tienes un medidor de lápiz o una
tercera solución, verifica la lectura antes de dar la calibración por buena.

El núcleo lee la calibración en cada ciclo: no hace falta reiniciarlo para
aplicarla. Antes de levantarlo, revisa que pH y EC tengan `quality=ok` y valores
coherentes:

```bash
docker compose -f compose.yaml -f compose.hardware.yaml run --rm --no-deps hidro-core read
docker compose -f compose.yaml -f compose.hardware.yaml up -d hidro-core
```

Prueba de interferencia, con las dos sondas en el mismo recipiente y el núcleo
detenido:

```bash
docker compose -f compose.yaml -f compose.hardware.yaml run --rm --no-deps \
  hidro-core interference
```

Mide el pH estable con el TDS apagado, enciende `tds_power`, espera `settle_s`,
vuelve a medir y lo apaga siempre. Informa la diferencia en mV y en pH (con la
calibración, o con una pendiente nominal de 0,12 V/pH si aún no hay). Con
0,05 pH o más hay interferencia: se mantiene la secuencia por defecto (el núcleo
lee el pH con el TDS apagado). Anota el resultado en `PROGRESS.md`.

La calibración de pH se considera vencida a los 30 días. Una lectura de pH o EC
`suspect` (calibración vencida, anómala o ausente, o sin temperatura del agua)
genera un aviso por Telegram y bloquea la dosificación hasta corregirla.

### Fase 6: bomba peristáltica y dosificación

**No conectes la bomba todavía si no tienes la fuente de 12 V y el circuito de
potencia armado.** El relé de la Pi solo conmuta los contactos; no alimenta la
bomba. Usa una fuente de 12 V para la bomba, un fusible y el canal 1 del módulo
de relés (GPIO22, jumper en **High**):

```text
+12 V -- fusible -- COM
NO --------------- bomba +
bomba - ---------- 0 V de la fuente de 12 V
```

Pon un diodo de recirculación entre los terminales de la bomba (cátodo al
positivo) si el módulo o la bomba no lo traen. **Nunca lleves los 12 V a la
Pi.** La entrada `IN` del relé sigue en GPIO22 y el módulo se alimenta con 5 V
y GND de la Pi, como en la Fase 2. La carga va siempre entre `COM` y `NO`:
`NC` queda cerrado con el relé en reposo.

Antes de energizar, deja la entrada de la manguera en agua limpia y la salida
en un recipiente graduado. **La bomba peristáltica no debe girar en seco.**

#### Calibrar el caudal

Detén el núcleo: el GPIO22 queda tomado por `hidro-core` y dos procesos no
pueden abrirlo. La calibración enciende la bomba 60 s por defecto y la apaga
siempre en un `finally`, incluso con Ctrl+C:

```bash
docker compose -f compose.yaml -f compose.hardware.yaml stop hidro-core
docker compose -f compose.yaml -f compose.hardware.yaml run --rm --no-deps \
  hidro-core calibrate pump --id pump_ph_down
```

El comando pide confirmación antes de encender, muestra el tiempo, apaga la
bomba y pregunta cuántos mililitros se juntaron. Calcula
`ml/min = ml × 60 / segundos`, avisa si el resultado se aleja mucho de la
referencia anterior y guarda el valor en `/data/calibration.yaml`. Para una
prueba más corta usa `--seconds 30`; para automatizar una repetición ya medida,
`--ml N --yes`. No se aceptan tiempos fuera de `0–120 s` ni volúmenes no
finitos o no positivos.

La calibración guardada manda sobre `ml_per_min` del YAML. Reinicia el núcleo
para que el registro de drivers la cargue:

```bash
docker compose -f compose.yaml -f compose.hardware.yaml up -d hidro-core
```

Si la bomba no tiene `role: ph_down` en `config/hidro.yaml`, las reglas usan el
id convencional `pump_ph_down`; también puedes declarar el rol explícitamente
y cambiar el id sin tocar el código. Un id convencional no puede declarar otro
rol (`pump_ph_up` con `role: ph_down` se rechaza). Los sensores de pH y EC se
toman por tipo, así que sus ids pueden ser otros.

Dirección de la corrección: **pH alto se baja con `ph_down` (ácido)** y pH bajo
se sube con `ph_up` (base). Con una sola bomba `ph_down`, un pH bajo genera un
aviso para corregir a mano y nunca agrega más ácido. Los nutrientes A y B se
dosifican juntos: si una de las dos no puede (falta, sin calibrar, bloqueada),
no se dosifica ninguna.

#### Semana en dry_run y límites

`config/hidro.yaml` deja `dosing.enabled: true` y `dosing.dry_run: true` para
la Fase 6. En este modo el núcleo evalúa las reglas 7.2 cada
`check_interval_s`, escribe en el log y envía a Telegram lo que *habría* hecho,
pero no activa ninguna bomba. Revisa durante una semana:

```bash
docker compose -f compose.yaml -f compose.hardware.yaml logs -f hidro-core
```

Los avisos de simulación deben decir `SIMULACIÓN` y las dosis, motivos y
segundos deben ser coherentes. Si falta una bomba, no está calibrada o la dosis
supera `max_on_s`, el aviso lo indica y no se simula una acción.

Los límites 7.3 son por bomba y persistentes entre reinicios: por defecto 3
dosis por hora y 20 ml/día para `pump_ph_down`. Con dosis reales, al alcanzar
un límite, o si una dosis falla, la bomba queda bloqueada y hay que revisar el
cultivo antes de reanudar. El reset no usa GPIO, así que se ejecuta con el
núcleo en marcha y este lo ve en su siguiente evaluación:

```bash
docker compose -f compose.yaml -f compose.hardware.yaml exec hidro-core hidro safety reset pump_ph_down   # solo esa bomba
docker compose -f compose.yaml -f compose.hardware.yaml exec hidro-core hidro safety reset                # todas las bombas
```

En `dry_run` el pH no cambia y la misma regla se repite, así que el límite se
alcanza enseguida: se avisa `SIMULACIÓN: ... se bloquearía` pero **no** se deja
un bloqueo real, y las dosis simuladas no cuentan para los límites de las dosis
reales. Un bloqueo real (por ejemplo, de un fallo) sí se respeta en `dry_run`.

Reiniciar una bomba no borra los límites de las demás. El nombre anterior
`hidro safety-reset` sigue funcionando. No reinicies el bloqueo para “seguir
probando”: primero corrige la causa.

#### Punto de control humano

La fase no se cierra hasta completar y registrar:

1. Fuente de 12 V, fusible, diodo y bomba conectados sin llevar 12 V a la Pi.
2. Bomba cebada, sin aire, y calibración de 60 s guardada; una segunda medición
   queda dentro de una variación razonable.
3. Semana en `dry_run` con decisiones y avisos revisados en Telegram.
4. Prueba de parada: `docker compose stop hidro-core` y Ctrl+C en una dosis
   dejan la bomba apagada.
5. Aviso `se bloquearía` visto en `dry_run`; con las primeras dosis reales,
   bloqueo por límite y `hidro safety reset` verificados.
6. Aprobación explícita del usuario antes de poner `dry_run: false` y hacer la
   primera dosis real, empezando por un volumen pequeño.

`hidro pump <id> --ml N` es una dosificación manual que no pasa por las reglas
ni por los límites; úsala solo con la bomba calibrada y con confirmación.

## Operación

### Rutina diaria

1. Lee el resumen de las 09:00 o envía `/estado` al bot.
2. Revisa pH, EC, temperatura del agua, flujo y nivel del estanque en Grafana.
3. Si hay una alerta, corrige la causa antes de resetear un bloqueo.
4. Confirma que el estanque tiene agua suficiente y que la bomba principal
   sigue funcionando; el software no la controla.
5. Revisa que `hidro-core` esté `healthy`:

   ```bash
   docker compose -f compose.yaml -f compose.hardware.yaml ps
   ```

### Rutina semanal y mensual

- Semanal: revisa logs, espacio en disco, última dosis y que el respaldo más
  reciente se pueda leer. Haz un respaldo después de cada cambio de
  configuración o calibración:

  ```bash
  bash scripts/backup.sh
  ```

- Mensual: recalibra el pH y revisa el estado físico de las sondas. Si el EC
  deriva o cambias la sonda, calibra también el EC con la solución patrón.
- Guarda los respaldos fuera de la Pi. Un respaldo en el mismo disco no protege
  de una falla del disco.

### Cuidado de la sonda de pH

- La sonda **no debe secarse**. En operación queda sumergida en la solución;
  fuera de ella, guárdala con el capuchón lleno de solución de almacenamiento
  (KCl 3 M) o, a falta de ella, buffer pH 4.
- Enjuágala con agua destilada entre soluciones, pero **no la guardes en agua
  destilada**: pierde electrolito y responde más lento.
- No la frotes ni la limpies con alcohol; usa agua destilada y un paño suave.
- La calibración vence a los 30 días. Una lectura `suspect` genera un aviso y
  bloquea la dosificación hasta recalibrar o corregir la temperatura.

### Recalibración mensual del pH

Detén el núcleo, porque la calibración comparte el ADS1115 y el relé del TDS:

```bash
docker compose -f compose.yaml -f compose.hardware.yaml stop hidro-core
docker compose -f compose.yaml -f compose.hardware.yaml run --rm --no-deps \
  hidro-core calibrate ph --point 6.86
docker compose -f compose.yaml -f compose.hardware.yaml run --rm --no-deps \
  hidro-core calibrate ph --point 4.00
docker compose -f compose.yaml -f compose.hardware.yaml up -d hidro-core
```

Enjuaga con agua destilada entre soluciones, espera a que la lectura se
estabilice y usa la temperatura real de la solución. Al terminar, vuelve a
dejar la sonda húmeda. Comprueba el resultado con `/estado` o `hidro read` y
anota la fecha y la pendiente en `PROGRESS.md`.

### Actualización y salud

- Reinicio automático: `restart: unless-stopped` en los tres servicios.
- Salud del núcleo: healthcheck por latido; `docker compose ps` muestra
  `healthy` o `unhealthy`.
- Grafana avisa por Telegram si el núcleo deja de publicar durante unos 10
  minutos.
- Retención de InfluxDB: 365 días (por la RAM y el disco compartidos de la Pi).
- Actualización: `./deploy.sh` desde el PC o `git pull` y `up -d --build` en la
  Pi, revisando después `/estado` y el dashboard.

### Punto de control de la Fase 7

La fase no se cierra hasta completar y registrar:

1. Recibir el resumen diario a la hora configurada.
2. Probar `/estado` y `/silenciar 10` desde el chat autorizado; confirmar que
   las alertas críticas no quedan silenciadas.
3. Detener `hidro-core` a propósito y recibir la alerta "hidro-core sin datos"
   de Grafana en Telegram.
4. Hacer un respaldo y una restauración de prueba, y confirmar que el sistema
   vuelve a operar.
5. Revisar esta guía y confirmar que se puede operar sin ayuda.

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
| El bot no responde a `/estado` | Revisa `TELEGRAM_CHAT_ID`, el token, los logs y que el núcleo esté corriendo |
| No llega el resumen diario | Comprueba `alerts.daily_summary_at`, `TZ` y el estado en `/data/daily_summary.json` |
| Falla `scripts/backup.sh` | InfluxDB debe estar en marcha y `INFLUX_TOKEN` debe ser el token de operador (respaldo completo) |
| La dosificación no actúa | `dosing.enabled`/`dry_run`, bloqueo de seguridad o falta calibrar `ml_per_min` |
| Bomba bloqueada tras un fallo o un límite | Corrige la causa, revisa el cultivo y ejecuta `hidro safety reset [pump]` |
| `Permission denied` en `/dev` | GIDs incorrectos en `.env` o falta `group_add` (usa `compose.hardware.yaml`) |
| DS18B20 marca 85 °C | Sonda desconectada, mal cableada o serial equivocado |
| El pH o el EC no cuadran | Calibrar en la Fase 5; el TDS se enciende después de medir el pH |

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
scripts/                  # respaldos, render de alertas y verificaciones por fase
deploy.sh                 # PC → Pi por SSH
backups/                  # respaldos locales, ignorados por git
```
