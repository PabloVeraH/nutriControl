# Bitácora del proyecto

Registro de fases, verificaciones y puntos de control. No se pasa a la
siguiente fase sin cerrar el punto de control humano correspondiente.

## Estado

| Fase | Estado | Rama |
| --- | --- | --- |
| 0 — Esqueleto en el PC (mock) | Implementada y verificada; checkpoint de Telegram pendiente | `fase-0` |
| 1 — Raspberry + sondas de temperatura | En curso; software y documentación listos, validación física pendiente | `fase-1` |
| 2 — Flotador y relés | En curso; drivers, CLI y verificación listos, validación física pendiente | `fase-2` |
| 3 — Flujómetro | En curso; driver y calibración listos, validación física pendiente | `fase-3` |
| 4 — Aire (HTU21D) | En curso; driver, cálculos y verificación listos, validación física pendiente | `fase-4` |
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
  terminar o interrumpir; pruebas de encendido, apagado, interrupción, bomba
  rechazada y salida inexistente.
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

**Estado:** en curso. Driver, calibración y documentación listos; falta el
cableado y la validación física.
**Rama:** `fase-3` · **Fecha:** 2026-10-22

### Hecho

- `flow_hall`: contador de pulsos protegido, caudal en L/min, total acumulado
  persistente en `/data` y cierre ordenado.
- La calibración guardada en `/data/calibration.yaml` reemplaza el
  `pulses_per_liter` del YAML al construir el driver.
- `calculate_pulses_per_liter`: valida litros positivos y pulsos mayores que
  cero.
- CLI `hidro calibrate flow [--liters N] [--id ID]`: guía la prueba con un
  recipiente graduado, cuenta los pulsos reales y guarda la calibración. Exige
  modo hardware y no guarda nada si no hubo pulsos.
- CLI `hidro flow [--seconds N]`: mide el caudal en una ventana corta con el
  núcleo detenido, para comprobar el cableado antes de la calibración.
- El flujómetro usa `bounce_time=None`; con el filtro de 20 ms del flotador se
  perderían pulsos a caudales altos. El parámetro quedó configurable en
  `RuntimeContext.gpio_input`.
- La alarma crítica de flujo ya estaba implementada y probada: se activa tras
  `alarm_after_s` por debajo de `min_l_min`, bloquea la dosificación, se repite
  cada 15 min y se recupera al normalizar.
- README con el divisor de tensión, la calibración y la prueba de alarma.
- Pruebas nuevas del cálculo de pulsos, del CLI de calibración y del
  `bounce_time` del flujómetro.

### Bloqueos

- Persisten el acceso SSH sin clave y la falta de hardware de las fases 1, 2 y 3.
- No hay flujómetro ni resistencias de 10 kΩ y 20 kΩ conectados.
- Telegram sigue sin configurar, así que no se puede validar la alerta real.

### Tareas

- [x] Driver, persistencia y pruebas.
- [x] CLI `hidro calibrate flow`.
- [x] CLI `hidro flow` para verificar el caudal.
- [x] Corrección del antirrebote para pulsos.
- [x] Documentación.
- [ ] Cablear el YF-S401 con el divisor y verificar que la señal no supere 3,3 V.
- [ ] Ver caudal con agua o soplando.
- [ ] Calibrar con 1 L y registrar los pulsos/L obtenidos.
- [ ] Cortar el flujo y recibir la alerta crítica en Telegram.
- [ ] Confirmar que la dosificación queda bloqueada durante la alarma.

### Punto de control humano

1. El GPIO27 recibe una señal de 0–3,3 V, nunca 5 V.
2. `hidro flow` muestra un caudal coherente al abrir el flujo.
3. La calibración con 1 L guarda un valor de pulsos/L y se conserva tras
   reiniciar el contenedor.
4. Al cortar el flujo, llega la alerta crítica en Telegram tras el tiempo
   configurado.
5. La dosificación permanece bloqueada mientras la alarma está activa.

### Publicación

- Rama `fase-3`, commits `db6fb28` y `bab40a5`, publicada en `origin/fase-3`.

## Revisión de las fases 0–2

**Fecha:** 2026-10-22 · **Rama:** `revision-fases-0-2` (desde `fase-3`)

Revisión del código, las pruebas y la documentación de las fases 0 a 2. Cada
corrección tiene una prueba que falla con el código anterior.

| Hallazgo | Gravedad | Corrección |
| --- | --- | --- |
| `flow_hall` asignaba `when_pressed`, que no existe en `DigitalInputDevice`; gpiozero lo rechaza y el núcleo no arrancaba en modo hardware (también `read`, `switch` y `phase2_check.sh`) | Crítica | Usa `when_activated` |
| Las pruebas usaban fakes que aceptan cualquier atributo, por eso no se detectó | Técnica | `gpiozero` en `dev`; `test_gpio_hardware.py` usa `MockFactory` |
| Con `pull: up` el flotador leía `value`, que gpiozero invierte: `low_when` quedaba al revés | Alta | Se lee el nivel físico (`pin.state`) |
| `pull: none` activaba el pull-down interno | Media | Entrada flotante (`pull_up=None`, `active_state=True`) |
| Si el primer envío a Telegram fallaba, la alerta no se reintentaba nunca (incluida la crítica de flujo) | Alta | Se reintenta en cada ciclo hasta entregarla |
| Los errores de httpx incluyen la URL con el token del bot y se escribían en el log | Alta (seguridad) | El token se reemplaza por `<token>` |
| La dosificación aceptaba lecturas `suspect` (EC sin calibrar, pH sin temperatura), contra el plan 7.2 | Alta | Solo dosifica con calidad `ok` |
| Sin lectura de flujo o flotador no había alarma y la dosificación no se bloqueaba | Alta | Bloquea si falta una lectura reciente de flujo o flotador |
| `hidro switch` como PID 1 de `docker compose run` ignoraba SIGTERM: `docker stop` terminaba en SIGKILL con el relé encendido | Media | SIGTERM se trata como Ctrl+C y el `finally` apaga el relé |
| `hidro switch --seconds 0` o negativo terminaba con traceback | Baja | Se rechaza con código 2 |
| GPIO ocupado (núcleo corriendo) daba un traceback de gpiozero; `hidro run` no capturaba errores de hardware al arrancar | Media | `DriverError` con la sugerencia de detener `hidro-core`; `run` sale con código 3 |
| SIGTERM no cortaba una dosis en curso (hasta `max_on_s` = 30 s, igual que `stop_grace_period`) | Media | Las bombas comparten el evento de parada del ciclo |
| README y `phase2_check.sh` sugerían la carga en `NC` como mitigación, que la deja energizada con el relé en reposo | Alta (eléctrica) | Pull-down de 10 kΩ en `IN` y carga siempre en `COM`–`NO` |
| Código muerto: `AlertManager(clock=)`, `tds_analog.build_from_config`, `FlowHallSensor.load_total` | Baja | Eliminado |

Pendiente, sin cambios en esta revisión:

- La dosificación busca las mediciones por nombre fijo (`ph`, `ec`) y las bombas
  por id fijo (`pump_ph_down`…); otro id en el YAML las deja sin efecto. Se
  revisa en la Fase 6.
- Una excepción fuera de los drivers (monitor, alertas) termina el ciclo; el
  `finally` apaga las salidas y Docker reinicia el contenedor.

## Revisión de la fase 3

**Fecha:** 2026-10-22 · **Rama:** `revision-fase-3` (desde `main`)

| Hallazgo | Gravedad | Corrección |
| --- | --- | --- |
| Si fallaba el guardado del total en `/data`, la excepción anulaba la lectura de flujo y la alarma crítica no podía activarse | Alta | El error se registra y el caudal se publica igual |
| Un `calibration.yaml` inválido se ignoraba en silencio y la siguiente calibración lo sobrescribía, borrando pH, EC y bombas | Alta | Guardar sobre un archivo inválido es un error; al leer se avisa en el log |
| Un error de sintaxis YAML en `calibration.yaml` impedía arrancar (bucle de reinicios) | Media | Se ignora con aviso, igual que los demás errores |
| La primera lectura del flujómetro siempre daba 0 L/min (sin ventana previa): `hidro read` y un punto falso tras cada reinicio | Media | La ventana empieza al construir el sensor; con menos de 1 s la lectura es `invalid` y no consume pulsos |
| `hidro calibrate flow` guardaba cualquier valor, aunque fuera absurdo por ruido o un volumen mal medido | Media | Pide confirmación fuera de 0,5×–2× de la referencia |

Sin cambios: un flujómetro sin lectura genera el aviso medio de "sensor sin
lectura" (tabla 7.1). Con el cable cortado no llegan pulsos y se lee 0 L/min,
así que la alarma crítica sí se activa; desde la revisión anterior, además, la
falta de lectura bloquea la dosificación.

## Fase 4 — Aire (HTU21D)

**Estado:** en curso. Driver, cálculos, pruebas y verificación listos; falta el
sensor conectado y la comprobación física.
**Rama:** `fase-4` (desde `main`) · **Fecha:** 2026-10-22

### Hecho

- Driver real `htu21d`: reset, lectura de temperatura y humedad por I2C,
  validación CRC-8 y descarte de tramas incompletas o con error de bus.
- Conversiones de fábrica y publicación de `air_temp`, `air_humidity`,
  `air_vpd` y `air_dew_point`.
- `calc.py`: VPD con Tetens y punto de rocío con Magnus, con humedad acotada
  para evitar valores no físicos o `NaN`.
- Simulador `MockHTU21DSensor` con los mismos cuatro valores y escenario de
  desconexión; registro automático en modo mock/hardware.
- Pruebas del CRC, las conversiones, los valores derivados, las tramas cortas,
  los errores de bus y el fallo de reset.
- `scripts/phase4_check.sh`: detiene el núcleo, comprueba `0x40` con
  `i2cdetect`, valida los cuatro valores y verifica que la humedad sube al
  soplar; restaura el estado del núcleo al terminar.
- README con el cableado a 3,3 V, la dirección `0x40`, la lectura manual y la
  comprobación asistida.

### Bloqueos

- Persisten el acceso SSH sin clave y la falta de hardware de las fases 1, 2 y 3.
- No hay HTU21D con pines soldados ni un módulo conectado al bus I2C.
- Sin acceso a la Pi no se puede ejecutar `i2cdetect` ni la comprobación física.

### Tareas

- [x] Driver HTU21D con CRC.
- [x] VPD y punto de rocío.
- [x] Simulador y registro del driver.
- [x] Pruebas unitarias y de errores.
- [x] Script de verificación y documentación.
- [ ] Soldar los pines y cablear a 3,3 V, GND, SDA y SCL.
- [ ] Confirmar `40` en `i2cdetect -y 1`.
- [ ] Ver temperatura y humedad coherentes con el ambiente.
- [ ] Soplar sobre el sensor y confirmar que la humedad sube.
- [ ] Verificar que VPD y punto de rocío son coherentes.

### Punto de control humano

1. `i2cdetect -y 1` muestra el HTU21D en `0x40`.
2. `hidro read` publica temperatura y humedad coherentes con el ambiente.
3. Al soplar sobre el sensor, la humedad sube.
4. `air_vpd` está en kPa, no es negativo, y `air_dew_point` no supera la
   temperatura del aire.

### Publicación

- Rama `fase-4`, commits `62e6e51`, `cbee075` y `85c9913`, publicada en
  `origin/fase-4`.

### Revisión

**Rama:** `revision-fase-4` (desde `fase-4`). Cada corrección tiene una prueba
que falla con el código anterior.

| Hallazgo | Gravedad | Corrección |
| --- | --- | --- |
| Una trama de ceros `[0, 0, 0]` pasa el CRC y daba −46,85 °C: alerta falsa de aire frío con el bus trabado o SDA a masa | Alta | Se descartan temperaturas fuera del rango del sensor (−40 a 125 °C) |
| No se comprobaba el bit de estado que indica si la trama es de temperatura o de humedad | Media | Una trama del tipo equivocado es `invalid` |
| Las tramas de prueba (`0x6666`, `0x5F5C`) tenían el bit de estado al revés | Técnica | Tramas reales y vectores de CRC de la hoja de datos |
| Faltaba la compensación de humedad por temperatura de la hoja de datos (hasta ±1,5 %HR entre 15 y 35 °C) | Media | −0,15 %HR/°C respecto de 25 °C, entre 0 y 80 °C |
| En el fallo solo salían `temp` y `humidity` como `invalid`; `phase4_check.sh` reportaba además "no se publicó" VPD y rocío | Baja | Las cuatro magnitudes salen `invalid` (driver y simulador) |
| Prueba del aliento: se soplaba antes de arrancar el contenedor de lectura (varios segundos) y la humedad bajaba antes de medir | Media | Se sopla al pulsar Enter y hasta que aparece la lectura |
| Si `hidro read` fallaba, el script no mostraba el error | Baja | Muestra la salida completa |
| README: pedía añadir pull-ups (la Pi ya trae 1,8 kΩ en GPIO2/3), no nombraba los pines `+ - DA CL` del módulo y atribuía la parada del núcleo al bus | Baja | Corregido |

## Fase 5 — pH y TDS (ADS1115)

Pendiente de hardware y soluciones de calibración.

## Fase 6 — Bomba y dosificación

Pendiente de hardware (fuente de 12 V).

## Fase 7 — Operación

Pendiente.
