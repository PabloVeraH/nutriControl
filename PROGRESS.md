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
| 5 — pH y TDS (ADS1115) | En curso; drivers, CLI y verificación listos, validación física pendiente | `fase-5` |
| 6 — Bomba y dosificación | En curso; software, calibración y pruebas listos, validación física y `dry_run` pendientes | `fase-6` |
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

**Estado:** en curso. Drivers, CLI, pruebas y documentación listos; faltan el
hardware y las soluciones de calibración.
**Rama:** `fase-5` (desde `main`) · **Fecha:** 2026-10-22

### Hecho

- Drivers `ads1115`, `ph_analog` y `tds_analog` con lecturas single-shot,
  media recortada, compensación de temperatura y secuencia de energizado del
  TDS. El pH se declara antes que el EC en la configuración para medirlo con el
  TDS apagado.
- `CalibrationStore` persiste pH en dos puntos (`V1, pH1, V2, pH2, T_cal`) y el
  factor `k` del EC con patrón, voltaje y temperatura. El punto de pH pendiente
  permite completar la calibración en dos ejecuciones.
- CLI `hidro calibrate ph --point 6.86|4.00`: guía el enjuague, pide la
  temperatura (o acepta `--temperature`), espera que tres lecturas consecutivas
  queden dentro de 10 mV (máximo 30 s), pide confirmación y valida la pendiente
  entre `0,02` y `0,60 V/pH`. Si la pendiente es anómala, conserva el punto
  pendiente y no guarda.
- CLI `hidro calibrate ec --standard 1413`: reutiliza el relé `tds_power`, espera
  `settle_s`, estabiliza la lectura y apaga el relé en un `finally`; valida
  `k` entre `0,1` y `10` y pide confirmación antes de guardar.
- `TDSAnalogSensor.powered()` concentra el encendido, asentamiento y apagado;
  un `k` anómalo en el archivo de calibración marca el EC como `suspect`.
- Ambas calibraciones exigen modo hardware, avisan que hay que detener
  `hidro-core` y prohíben guardar sobre un `calibration.yaml` inválido.
- Pruebas nuevas de estabilidad del ADC, parámetros inválidos, contexto
  `powered`, límites de `k` y flujos CLI de pH/EC, incluido el apagado del relé
  ante errores. `pytest`: 273 pruebas, cobertura 90,57 %; `ruff` y `mypy` sin
  hallazgos.
- README con el cableado, el divisor del pH, `i2cdetect`, la calibración guiada
  y la prueba de interferencia pH/TDS.

### Bloqueos

- Persisten el acceso SSH sin clave y la falta de hardware de las fases 1 a 4.
- No hay ADS1115, sondas de pH/TDS, resistencias de 10 kΩ/20 kΩ, soluciones de
  calibración ni agua destilada conectadas.
- Telegram sigue sin configurar; no se pueden validar las alertas reales.
- Sin acceso a la Pi no se puede ejecutar `i2cdetect` ni la calibración física.

### Tareas

- [x] Drivers y pruebas de ADC, pH y TDS.
- [x] Persistencia y validación de calibraciones.
- [x] CLI de calibración de pH y EC.
- [x] Secuencia de energizado del TDS y apagado seguro.
- [x] Documentación y verificación local.
- [ ] Cablear el ADS1115, los divisores y las sondas.
- [ ] Confirmar `40` y `48` en `i2cdetect -y 1`.
- [ ] Calibrar pH 6,86 y 4,00 y registrar la pendiente y `V7`.
- [ ] Calibrar EC con 1413 µS/cm y registrar `k`.
- [ ] Verificar con una tercera solución o un medidor de lápiz.
- [ ] Probar la interferencia pH/TDS y registrar el resultado.
- [ ] Reiniciar el núcleo y confirmar pH/EC con `quality=ok`.

### Punto de control humano

1. `i2cdetect -y 1` muestra `40` (HTU21D) y `48` (ADS1115).
2. La calibración de pH guarda una pendiente y `V7` coherentes; una pendiente
   fuera de rango es rechazada.
3. La calibración de EC guarda un `k` coherente y el relé `tds_power` queda
   apagado al terminar.
4. La verificación en una tercera solución o con un medidor de lápiz es
   razonable.
5. La prueba de interferencia pH/TDS no muestra una variación apreciable; si la
   muestra, se mantiene la secuencia por defecto y se documenta.
6. `hidro read` muestra pH y EC con `quality=ok` y valores coherentes.

Los comandos concretos están en el README, sección "Fase 5: pH y TDS
(ADS1115)".

### Publicación

- Rama `fase-5`, commits `30e9d85`, `4de997c` y `0feac8e`, publicada en
  `origin/fase-5`. La calibración física y el punto de control siguen
  pendientes de hardware y soluciones.

### Revisión

**Rama:** `revision-fase-5` (desde `fase-5`). Cada corrección tiene una prueba
que falla con el código anterior.

| Hallazgo | Gravedad | Corrección |
| --- | --- | --- |
| Con el núcleo detenido el GPIO de `tds_power` queda flotando: el TDS podía quedar energizado durante la calibración de pH y alterarla | Alta | `calibrate ph` toma `tds_power` y lo mantiene apagado |
| Nada impedía calibrar con el núcleo corriendo (comparte el ADS1115 y conmuta el relé cada ciclo) | Alta | Al tomar `tds_power`, el GPIO ocupado aborta con un mensaje claro |
| La estabilidad (3 muestras en 1,5 s, 10 mV ≈ 0,08 pH, máximo 30 s) aceptaba una sonda todavía en deriva | Alta | 2 mV durante 10 s (pH, máximo 3 min) y 5 mV (EC, máximo 1 min); ajustable con `--tolerance-mv` |
| No existía forma de hacer la prueba de interferencia pH/TDS del plan: el núcleo y `hidro read` siempre leen el pH con el TDS apagado | Media | Nuevo `hidro interference` |
| Una pendiente positiva (soluciones o `--point` intercambiados) se aceptaba | Media | Pide confirmación |
| Un punto pendiente de días atrás se combinaba con el nuevo | Media | Con más de 1 h ofrece usar la nueva medición como primer punto |
| La temperatura de calibración se pedía a mano, aunque el plan la toma del DS18B20 | Baja | Se propone la lectura de `temperature_from` |
| Una lectura `suspect` (calibración vencida o anómala, EC sin calibrar, sin temperatura) solo quedaba en el log; la dosificación se bloqueaba sin aviso | Media | Aviso por Telegram (`quality-<id>`) |
| El CLI pedía reiniciar el núcleo tras calibrar pH/EC, pero el driver lee la calibración en cada ciclo | Baja | Mensaje corregido |
| README de las fases 1 y 5: `exec hidro-core hidro read` falla en la Pi porque el núcleo tiene los GPIO | Media | Se detiene el núcleo y se usa `run --rm` |
| `ph_analog` duplicaba la matemática de `PHCalibration` (`calibrate_two_points` y otras) y tenía código muerto | Baja | Eliminado; el CLI usa `PHCalibration` |

Sin cambios:

- Los valores nominales de los buffers varían con la temperatura (6,86 es 6,90
  a 15 °C). El error es de unas 0,04 pH, menor que la precisión del PH-4502C.
- Las tolerancias de estabilidad son estimaciones sin hardware; se ajustan en
  la primera calibración real con `--tolerance-mv`.

## Fase 6 — Bomba y dosificación

**Estado:** en curso. Driver, calibración, reglas, límites, pruebas y
documentación listos; faltan la fuente de 12 V, la calibración real y la semana
en `dry_run` antes de habilitar dosis reales.
**Rama:** `fase-6` (desde `main`) · **Fecha:** 2026-10-22

### Hecho

- Driver `PeristalticPump` con dosis por volumen, `max_on_s`, evento de parada
  compartido con SIGTERM y apagado del relé en `finally`. El registro aplica la
  calibración guardada y, si existe, manda sobre `ml_per_min` del YAML.
- `CalibrationStore.save_pump()` valida un caudal finito y positivo y lo
  persiste en `/data/calibration.yaml`.
- CLI `hidro calibrate pump [--id ID] [--seconds 60] [--ml N] [--yes]`: exige
  modo hardware, avisa que hay que detener el núcleo, pide confirmación, enciende
  la bomba como máximo 120 s, la apaga siempre, pide el volumen medido, calcula
  `ml/min`, avisa si se aleja de la referencia y guarda.
- Reglas 7.2: pH peligroso primero, EC bajo con nutrientes A/B, EC alto con
  aviso de dilución y pH fuera de rango. Se evalúan en `dry_run`, se registran
  en log/Influx y se avisan por Telegram sin mover bombas.
- Reglas 7.3: límite por hora y por día con bloqueo persistente de la bomba
  afectada. Al alcanzar un límite pide revisión humana y `hidro safety reset`;
  un fallo de dosificación también bloquea. Reiniciar una bomba no borra los
  límites ni los registros de las otras.
- Sensores y bombas configurables: las reglas toman el primer `ph_analog` y
  `tds_analog`, y cada bomba puede declarar `role` (`ph_down`, `ph_up`,
  `nutrient_a`, `nutrient_b`). Los ids convencionales siguen funcionando como
  respaldo.
- CLI `hidro safety reset [pump]` y alias compatible `hidro safety-reset`.
- Validación de `--ml` finito y positivo en `hidro pump`; configuración rechaza
  roles de bomba duplicados y límites diarios no positivos.
- Configuración de ejemplo con `dosing.enabled: true`, `dry_run: true`,
  `role: ph_down` y límites de 3 dosis/h y 20 ml/día.
- README con el circuito de 12 V, el diodo de recirculación, la calibración, el
  uso de `dry_run`, el reset de seguridad y el punto de control humano.
- Pruebas nuevas de calibración de bomba, apagado ante interrupción, roles e ids
  personalizados, persistencia de límites, reset por bomba y bloqueo tras un
  fallo. `pytest`: 303 pruebas, cobertura 90,69 %; `ruff` y `mypy` sin
  hallazgos.

### Bloqueos

- No hay fuente de 12 V, bomba peristáltica ni cableado de potencia terminado.
- Persisten el acceso SSH sin clave y la falta de hardware de las fases 1 a 5.
- Telegram sigue sin configurar; no se pueden validar los avisos reales.
- No se ha medido el caudal real ni se ha completado la semana en `dry_run`.
- No hay aprobación explícita para poner `dry_run: false`.

### Tareas

- [x] Driver de bomba peristáltica y apagado seguro.
- [x] Calibración persistente de `ml/min` y CLI guiada.
- [x] Reglas de dosificación 7.2 en `dry_run`.
- [x] Límites 7.3 persistentes y reset manual.
- [x] Ids de sensores y roles de bombas configurables.
- [x] Pruebas, validación y documentación.
- [ ] Montar fuente de 12 V, fusible, diodo y relé; verificar que no llegan
      12 V a la Pi.
- [ ] Cebar la bomba y calibrar 60 s con agua; repetir y comparar.
- [ ] Revisar una semana de decisiones en `dry_run` por Telegram y el log.
- [ ] Verificar apagado con SIGTERM, Ctrl+C y corte del núcleo.
- [ ] Probar un límite y el reset seguro.
- [ ] Obtener aprobación explícita y hacer la primera dosis real pequeña.

### Punto de control humano

1. La bomba responde desde GPIO22 y el relé queda apagado al terminar o si se
   interrumpe el comando.
2. La calibración de 60 s produce un `ml/min` coherente y estable.
3. La semana en `dry_run` muestra decisiones razonables y avisos claros, sin
   activar la bomba.
4. Los límites bloquean la bomba y solo `hidro safety reset` la habilita de
   nuevo.
5. El usuario aprueba explícitamente el cambio a `dry_run: false`.

Los comandos concretos están en el README, sección "Fase 6: bomba peristáltica
y dosificación".

### Revisión

Rama `revision-fase-6` (desde `fase-6`). Hallazgos corregidos:

- **Crítico — corrección de pH invertida** (heredado de la Fase 0): con pH
  bajo se programaba `pump_ph_down` (más ácido) y con pH alto `pump_ph_up`; los
  tests lo fijaban. Ahora pH bajo → `ph_up` con `ph_up_ml` y pH alto →
  `ph_down` con `ph_down_ml`. Con la única bomba `ph_down`, un pH bajo solo
  avisa. Si `ph_danger` llega sin lectura fuera de rango, no se dosifica.
- **`hidro safety reset` no surtía efecto con el núcleo vivo:** el núcleo
  guardaba los bloqueos en memoria y los reescribía en el siguiente guardado.
  `SafetyGuard` relee `safety.json` en cada operación; un archivo dañado ya no
  borra los bloqueos. README: el reset se hace con `exec`.
- **`dry_run` generaba bloqueos reales:** como el pH simulado no cambia, el
  límite de 3 dosis/h se alcanzaba en ~30 min y dejaba la bomba bloqueada para
  cuando se pasara a dosis reales. Ahora en `dry_run` se avisa "se bloquearía"
  sin bloquear, y dosis simuladas y reales se cuentan por separado.
- **Nutrientes desbalanceados:** si B estaba bloqueada o sin calibrar, A se
  dosificaba sola. Ahora van juntas o ninguna.
- **Rol contradictorio:** `pump_ph_up` con `role: ph_down` hacía que la regla
  de subir pH usara el ácido por el id de respaldo; la configuración lo rechaza.
- **Driver:** `dispense_ml(nan)` dejaba la bomba encendida en un bucle sin fin
  (`nan <= 0` es falso); ahora exige volumen finito. Una parada a mitad de dosis
  devuelve el tiempo real encendida en vez del pedido.
- **`calibrate pump`:** un error de tipeo en el volumen abortaba después de
  bombear; ahora vuelve a preguntar.
- Pruebas nuevas verificadas contra el código original (fallan o se cuelgan,
  caso NaN). `pytest`: 319 pruebas, cobertura 90,85 %; `ruff` y `mypy` sin
  hallazgos.

### Publicación

- Rama `fase-6`, commit `fae9c3c` (`feat(fase-6): calibrar bombas y persistir
  bloqueos de dosificación`), publicada en `origin/fase-6`.
- La calibración física, la semana en `dry_run` y el punto de control siguen
  pendientes de hardware y de la aprobación explícita del usuario.

## Fase 7 — Operación

Pendiente.
