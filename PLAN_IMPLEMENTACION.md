# Plan de implementación: sistema hidropónico NFT automatizado ("hidro")

> **Especificación del proyecto.** Este archivo es la referencia completa: decisiones ya tomadas (con sus razones), el hardware real, el cableado, las fórmulas, las reglas de seguridad y un plan por fases con puntos de control. No reabrir decisiones de la sección 2 sin una razón técnica concreta, y si se hace, consultarlo antes con Pablo.

---

## 0. Cómo trabajar con este plan

1. **Avanza por fases (sección 9), en orden.** No empieces una fase sin cerrar la anterior.
2. **Cada fase termina en un punto de control humano.** El usuario debe hacer algo físico (conectar, medir, observar). Detente, dale instrucciones exactas y espera su resultado antes de seguir. Nunca asumas que el hardware funciona.
3. **Mantener un registro en `PROGRESS.md`** en la raíz: fase actual, qué se hizo, qué quedó pendiente, resultados de los puntos de control y decisiones nuevas. Actualizarlo al terminar cada tarea. Antes de retomar el trabajo, leer `PROGRESS.md` primero.
4. **Idioma:** documentación, mensajes de log de cara al usuario, alertas y comentarios explicativos en **español**. Identificadores de código (clases, funciones, variables) en **inglés**.
5. **Pregunta antes de decidir** cuando algo no esté cubierto aquí y la decisión sea difícil de revertir o afecte la seguridad eléctrica o de las plantas. Para lo demás, elige lo razonable y anótalo en `PROGRESS.md`.
6. **Las dudas de la sección 11 ya están resueltas** (respuestas al final del documento). Las que sigan abiertas (plantas, estanque, compras) se cierran antes de la fase que las necesite.

---

## 1. Contexto

- **Usuario:** Pablo, ingeniero de software senior con foco backend (más de 10 años, Python, PHP, Java). Domina el software; está aprendiendo la parte de electrónica. Explica lo eléctrico paso a paso, sin asumir conocimiento previo.
- **Ubicación:** Chiguayante, Concepción, Chile. Red eléctrica de **220 V / 50 Hz**. Zona horaria `America/Santiago`.
- **Objetivo:** cultivar lechugas en un sistema **NFT** (Nutrient Film Technique): una lámina delgada de solución nutritiva corre de forma continua por canales inclinados (2° a 3°) y vuelve a un estanque. El sistema de software monitorea el agua y el aire, alerta ante fallas y, cuando haya bombas suficientes, dosifica nutrientes y regulador de pH.
- **Referencia de origen:** el proyecto "Automated Hydroponic System Build" de Kyle Gabriel (ver Fuentes). Usa el software Mycodo y sensores Atlas Scientific. Nosotros replicamos la idea con hardware más barato y software propio.

### Hechos agronómicos que el sistema debe respetar

- En NFT las raíces no tienen sustrato que retenga agua: **si el flujo se detiene, en días calurosos se dañan en menos de una hora.** La alarma de flujo es la más importante del sistema.
- **La bomba principal corre 24/7.** No se programa con temporizador.
- Rangos objetivo por defecto para lechuga (todos configurables):
  - pH: 5,5 a 6,2 (normal); fuera de 5,0 a 7,0 es "peligro".
  - EC: 1000 a 1300 µS/cm.
  - Temperatura del agua: ideal 18 a 22 °C; alerta sobre 24 °C.
  - Temperatura del aire: alerta bajo 5 °C o sobre 35 °C.
  - VPD (informativo): en torno a 0,85 kPa (±0,35).
- Los nutrientes suelen ser ácidos: **al dosificar nutrientes baja el pH.** Por eso se corrige primero la EC y después el pH (sección 7).

---

## 2. Decisiones ya tomadas (no reabrir sin razón)

| Decisión | Razón |
|---|---|
| **Software propio, no Mycodo** | Mycodo (~119.000 líneas) es pesado (nginx en 80/443, Flask, InfluxDB, servicios systemd), su soporte Docker es experimental y no documenta el acceso a GPIO/I2C, y su lógica de control vive en fragmentos editados en una UI web, fuera de git. Sus drivers de sensores son envoltorios delgados (100 a 200 líneas) sobre librerías estándar. Además, al revisarlos se encontraron problemas concretos (ver abajo). |
| **Python** (3.12 o superior) | El cuello de botella no es la CPU (pocas lecturas cada segundos o minutos). El ecosistema de sensores y la experiencia del usuario están en Python. En Rust, `rppal` está en modo mantenimiento (última versión diciembre de 2024). La capa de drivers queda detrás de interfaces para permitir migrar el núcleo en el futuro si se quisiera. |
| **Docker / docker compose** | La Raspberry Pi 4 (64 bits) del usuario ya corre otros proyectos. Todo debe quedar aislado. |
| **Sin contenedores privilegiados** | Se pasan solo los dispositivos necesarios (sección 5). |
| **No usar los puertos 80 ni 443** | Pueden estar en uso por otros proyectos del usuario. Solo Grafana se publica, en un puerto configurable (por defecto 3300). |
| **Desarrollo en el PC del usuario, despliegue a la Pi** | Todo el código debe poder correr y probarse en el PC con drivers simulados (`HIDRO_HARDWARE=mock`). La Pi solo recibe el código y construye/levanta los contenedores. |
| **Alertas por Telegram** | Bot de Telegram; token y chat id en `.env`. |
| **La bomba principal NO depende del software** | Va enchufada directo a la corriente. El software solo la vigila con el flujómetro y alerta. Si el contenedor se cae o la Pi se cuelga, las plantas siguen con agua. |
| **Drivers como plugins** | Interfaz común; cada sensor o salida se declara en YAML. Se agregan drivers a medida que el usuario compre hardware. |
| **No copiar código de Mycodo** | Mycodo es GPL-3.0: copiarlo obliga a licenciar este proyecto igual si se distribuye. Se puede leer como referencia; las ideas y fórmulas sí se pueden usar. |
| **No usar `pigpio` ni `RPi.GPIO`** | `pigpio` necesita un demonio aparte y no funciona en Raspberry Pi 5; `RPi.GPIO` es legado. Usar `gpiozero` con backend `lgpio` (alternativa aceptable: bindings oficiales de libgpiod v2, paquete `gpiod`). |

### Problemas encontrados en Mycodo (para no repetirlos)

- **DS18B20:** el bucle de reintentos lee dos veces aunque la primera funcione y nunca registra la falla (`if i == n` con `range(n)` jamás se cumple). Lo bueno que sí hace: descarta la lectura de 85 °C (valor de encendido, indica falla de comunicación) y los valores fuera de -55 a 125 °C.
- **Flujómetro:** depende de `pigpio`. Además no cuenta el primer pulso.
- **pH analógico:** la corrección de temperatura escala el voltaje absoluto por la razón de temperaturas absolutas. En módulos como el PH-4502C, donde pH 7 corresponde a unos 2,5 V (desplazado), eso distorsiona. Lo correcto es escalar la **pendiente** alrededor del punto isopotencial (pH 7). Ver sección 6.
- **EC/TDS analógico:** usa una recta de dos puntos; la placa del usuario (clon de DFRobot Gravity TDS) usa una curva cúbica oficial. Ver sección 6.

---

## 3. Hardware del usuario

### 3.1 Lo que ya tiene

| Pieza | Qué es | Interfaz | Notas |
|---|---|---|---|
| Raspberry Pi 4 (64 bits, Raspberry Pi OS) | Controlador | — | Compartida con otros proyectos |
| Cables Dupont macho-hembra, 6 mini protoboards | Conexiones sin soldar | — | Cada columna de 5 agujeros está unida por dentro |
| 2 sondas DS18B20 sumergibles | Temperatura del agua | 1-Wire | Cables: rojo = VCC, negro = GND, amarillo = datos |
| Placa adaptadora "Pluggable Terminal" | Bornes DAT/VCC/GND | 1-Wire | Trae la resistencia pull-up de 4,7 kΩ (marcada 472). Ambas sondas van en los mismos bornes |
| Sensor HTU21D (placa roja) | Temperatura y humedad del aire | I2C, dirección **0x40** | Los pines vienen **sin soldar** |
| Sonda de pH + módulo PH-4502C | pH | Analógica (pin `Po`) | Se alimenta con 5 V; su salida puede pasar de 3,3 V. Pines: `To`, `Do`, `Po`, `G`, `G`, `V+`. Solo se usan `V+`, `G` y `Po` |
| Medidor TDS Keyestudio V1.0 | Conductividad / sólidos disueltos | Analógica (pin `A`) | Clon de DFRobot Gravity TDS (SEN0244). Alimentación 3,3 a 5,5 V; salida 0 a 2,3 V |
| Flujómetro YF-S401 | Flujo de agua | Pulsos digitales (5 V) | Rango 0,3 a 6 L/min. Aproximadamente F = 98 × Q (Hz, Q en L/min) → **~5880 pulsos/L** (calibrar) |
| Flotador (interruptor magnético) | Nivel bajo del estanque | Digital | Dos cables, sin polaridad |
| Bomba peristáltica Grothen G328 | Dosificación | 12 V DC vía relé | Tubo 3 × 5 mm. Caudal: medirlo (no inventar) |
| Relé mecánico HW-279, 2 canales | Conmuta cargas DC o AC | GPIO | 10 A. Bornes de control DC+, DC−, IN1, IN2. **Puente en posición "High"** (activa con nivel alto) |
| Relé de estado sólido, 2 canales ("High Level Trigger") | Conmuta cargas **solo AC** | GPIO | Máximo 2 A. **No sirve para la bomba de 12 V DC** |
| 3 adaptadores USB a serial (CP2102, CH340, PL2303) | Puerto serie | USB | Reservados para un futuro sensor de CO2 MH-Z19B |
| Módulo LoRa SX1278 + antena | Radio | SPI | **Fuera del alcance** de este proyecto |

### 3.2 Lo que falta comprar (preguntar al usuario el estado)

- **ADS1115** (conversor analógico a digital, I2C, 4 canales): imprescindible para pH y TDS.
- Kit de resistencias (al menos 10 kΩ y 20 kΩ): para el flotador y los divisores de voltaje.
- Cautín y estaño: para el HTU21D.
- Fuente de 12 V / 2 A: para la bomba peristáltica.
- Soluciones de calibración: pH 4,00 y 6,86 (o 7,00), EC 1413 µS/cm, agua destilada.
- Parte hidropónica: bomba sumergible, canales de PVC de grado alimenticio, estanque con tapa opaca, nutrientes A y B, regulador de pH.
- Más adelante: 3 bombas peristálticas más (para pH down, nutriente A y nutriente B por separado), caja estanca.

### 3.3 Sensores del proyecto de referencia que NO tiene (drivers futuros)

Diseñar la capa de drivers para que estos se agreguen sin tocar el núcleo:

| Componente de referencia | Driver futuro | Nota |
|---|---|---|
| Atlas Scientific EZO (RTD, pH, EC, FLO, PMP) | Un driver genérico `atlas_ezo` | Todos usan el mismo protocolo I2C de comandos de texto (p. ej. enviar `R`, esperar, leer; primer byte = código de estado). Se calibran por dentro |
| MH-Z19B (CO2) | `mhz19b` por UART | Usar uno de los adaptadores USB a serial |
| Sensor de corriente AC (transformador + ADS1115) | `ac_current` | Reutiliza el ADS1115 |
| LCD 20×4 I2C | `lcd_hd44780_i2c` | Librería RPLCD |
| Cámara de la Pi | `picamera2` | Timelapse |

---

## 4. Cableado y mapa de pines

> **Reglas eléctricas absolutas:**
> - Ningún pin GPIO puede recibir más de **3,3 V**. Toda señal de 5 V pasa por un divisor de voltaje.
> - Desenchufar la Pi antes de mover cualquier cable.
> - El lado de **220 V** nunca lo instruyes paso a paso. Indica que debe ir en caja cerrada y que lo arme o revise alguien con experiencia (o usar enchufes inteligentes Tasmota/Kasa como alternativa).
> - Todos los aparatos comparten la misma tierra (GND).

### 4.1 Mapa de pines

> Los números `gpio:` de la configuración YAML son **BCM** (el número de GPIO, no el pin físico). Ejemplo: `gpio: 22` es GPIO22 y corresponde al pin físico 15.

| Pin físico | Nombre | Se conecta a |
|---|---|---|
| 1 | 3,3 V | Regleta de 3,3 V: DS18B20 (VCC de la placa), HTU21D, ADS1115, TDS |
| 2 | 5 V | Regleta de 5 V: módulo de pH, flujómetro, relés |
| 3 | GPIO2 (SDA) | HTU21D (`DA`) y ADS1115 (`SDA`) |
| 5 | GPIO3 (SCL) | HTU21D (`CL`) y ADS1115 (`SCL`) |
| 6 | GND | Regleta de tierra |
| 7 | GPIO4 | Datos 1-Wire (DAT de la placa adaptadora) |
| 11 | GPIO17 | Flotador |
| 13 | GPIO27 | Señal del flujómetro (después del divisor) |
| 15 | GPIO22 | Relé mecánico, canal 1: bomba peristáltica |
| 16 | GPIO23 | Relé mecánico, canal 2: alimentación del TDS (ver 4.6) |
| 18 | GPIO24 | Relé de estado sólido, canal 1: reservado (equipo AC) |
| 22 | GPIO25 | Relé de estado sólido, canal 2: reservado (luz de cultivo AC) |

### 4.2 DS18B20 (1-Wire)

- Ambos amarillos → `DAT`, ambos rojos → `VCC`, ambos negros → `GND` de la placa adaptadora.
- Placa: `DAT` → pin 7, `VCC` → pin 1, `GND` → pin 6.
- Host: activar 1-Wire (`raspi-config` → Interface Options → 1-Wire, o `dtoverlay=w1-gpio,gpiopin=4` en `/boot/firmware/config.txt`).
- Prueba en el host: `ls /sys/bus/w1/devices/` (dos carpetas `28-…`); `cat /sys/bus/w1/devices/28-*/temperature` (milésimas de °C).

### 4.3 Flotador

- Un cable → 3,3 V. El otro → una columna libre de la protoboard → pin 11 (GPIO17).
- Resistencia de 10 kΩ de esa columna a GND (pull-down).
- Prueba en el host: `pinctrl get 17` → `lo` o `hi`; cambia al subir el flotador con el dedo.
- Registrar cuál valor corresponde a "estanque bajo" (depende de la orientación del anillo).

### 4.4 Flujómetro YF-S401 (divisor 10 kΩ + 20 kΩ)

- Rojo → 5 V; negro → GND.
- Amarillo → columna A; **10 kΩ** entre A y columna B; B → pin 13 (GPIO27); **20 kΩ** entre B y GND.
- 5 V × 20 / (10 + 20) ≈ 3,33 V en el pin.

### 4.5 HTU21D (I2C)

- Soldar la tira de 4 pines primero.
- `+` → 3,3 V; `-` → GND; `DA` → pin 3; `CL` → pin 5.
- Host: activar I2C. Prueba: `sudo apt install -y i2c-tools && i2cdetect -y 1` → debe aparecer `40`.

### 4.6 ADS1115 + pH + TDS

- ADS1115: `VDD` → 3,3 V; `GND` → GND; `SDA` → pin 3; `SCL` → pin 5; `ADDR` → GND (dirección **0x48**).
- Módulo de pH: `V+` → 5 V; `G` → GND; `Po` → **divisor propio** (otro par de 10 kΩ + 20 kΩ, igual al del flujómetro) → `A0`. No se usan `To` ni `Do`. Sonda enroscada al conector BNC.
- TDS: sonda enchufada a su placa; `+` → 3,3 V **a través del canal 2 del relé mecánico** (GPIO23); `-` → GND; `A` → `A1` directo (su salida no pasa de 2,3 V).
- Ganancia del ADS1115: **±4,096 V** (LSB = 125 µV).
- `i2cdetect -y 1` debe mostrar `40` y `48`.
- **Interferencia pH/TDS:** si ambas sondas están en el mismo recipiente y el TDS está energizado, la lectura de pH se altera. Secuencia de medición: TDS apagado → leer pH → encender TDS → esperar (configurable, ~2 s) → leer TDS → apagar TDS. Si el usuario confirma que no hay interferencia, se puede simplificar, pero el diseño por defecto es este.
- **La sonda de pH nunca debe secarse.** Recordarlo en la documentación.

### 4.7 Relés

- Relé mecánico HW-279: puente en **High**. `DC+` → 5 V, `DC-` → GND, `IN1` → pin 15, `IN2` → pin 16.
- Bomba peristáltica: positivo de la fuente de 12 V → `COM` del canal 1; `NO` → positivo de la bomba; negativo de la bomba → negativo de la fuente. Los 12 V nunca tocan la Pi.
- Relé de estado sólido: `DC+` → 5 V, `DC-` → GND, `CH1` → pin 18, `CH2` → pin 22. Solo cargas AC de hasta 2 A.
- **Verificar en el punto de control:** que 3,3 V del GPIO activan cada módulo (los de 5 V suelen hacerlo con optoacoplador, pero no está garantizado), y qué hace cada relé cuando el contenedor se detiene (sección 8).
- Prueba en el host sin la app: `pinctrl set 22 op dh` (enciende) / `pinctrl set 22 op dl` (apaga).

---

## 5. Arquitectura

### 5.1 Servicios (docker compose)

| Servicio | Imagen | Rol | Puertos |
|---|---|---|---|
| `hidro-core` | Construida desde `core/` (Python 3.12+ slim, arm64 y amd64) | Drivers, ciclo de lectura, reglas, dosificación, alertas Telegram, CLI de calibración | Ninguno publicado |
| `influxdb` | `influxdb:2` | Series de tiempo | Ninguno publicado (solo red interna) |
| `grafana` | `grafana/grafana-oss` | Paneles y alertas de "sin datos" | `${GRAFANA_PORT:-3300}:3000` |

- `restart: unless-stopped` en todos.
- Volúmenes nombrados para InfluxDB, Grafana y `hidro-core` (`/data`: calibración, estado de contadores, heartbeat).
- `TZ=America/Santiago`.
- Grafana con **provisioning** versionado: datasource de InfluxDB, un dashboard y una regla de alerta "sin datos de hidro-core por más de 10 minutos" con contact point de Telegram. Esto cubre el caso en que `hidro-core` muera (no puede avisar de su propia muerte).
- Retención en InfluxDB: 365 días (configurable). Escrituras cada 30 a 60 s para no desgastar la microSD.
- La Pi tiene 4 GB de RAM y comparte equipo con Plex, Pi-hole y otros contenedores. InfluxDB 2 entra, pero con límites de memoria explícitos (`mem_limit`) y sin publicar puertos. Si en la fase 1 el consumo ahoga al resto, proponer una alternativa más liviana antes de cambiar.

### 5.2 Acceso a hardware desde `hidro-core` (sin `privileged`)

```yaml
devices:
  - /dev/i2c-1:/dev/i2c-1
  - /dev/gpiochip0:/dev/gpiochip0
group_add:
  - "${GPIO_GID}"   # GID numérico del grupo gpio del host (getent group gpio)
  - "${I2C_GID}"    # GID numérico del grupo i2c del host (getent group i2c)
```

- 1-Wire: Docker monta `/sys` en solo lectura en contenedores no privilegiados; `/sys/bus/w1/devices` debería ser visible. **Verificarlo en la fase 1.** Solo se lee.
- Los `devices` y `group_add` viven en `compose.hardware.yaml`, no en `compose.yaml`. Motivo: Docker Compose no permite quitar `devices` desde un override de forma portable, así que en el PC se usa `compose.yaml + compose.mock.yaml` y en la Pi `compose.yaml + compose.hardware.yaml`. En modo `mock` no se pasan dispositivos.
- Al iniciar, el contenedor debe fallar con un mensaje claro si en modo `hardware` falta un dispositivo declarado en la configuración.
- El proceso corre como usuario no root dentro del contenedor.

### 5.3 Estructura del repositorio

```
.
├── PLAN_IMPLEMENTACION.md      # este archivo
├── PROGRESS.md                 # bitácora del proyecto
├── README.md                   # cómo levantar, desplegar, calibrar
├── .env.example                # nunca commitear .env
├── compose.yaml
├── compose.mock.yaml           # override para el PC (sin dispositivos)
├── compose.hardware.yaml       # dispositivos y grupos para la Pi
├── deploy.sh                   # PC → Pi (ver 5.5)
├── config/
│   ├── hidro.yaml              # hardware declarado, rangos, reglas
│   └── grafana/provisioning/...
└── core/
    ├── Dockerfile
    ├── pyproject.toml          # uv, ruff, pytest, mypy
    ├── src/hidro/
    │   ├── main.py             # arranque, ciclo, señales
    │   ├── config.py           # carga y validación (pydantic)
    │   ├── model.py            # Measurement, unidades
    │   ├── drivers/
    │   │   ├── base.py         # interfaces Sensor, Switch, Pump
    │   │   ├── registry.py     # type → clase
    │   │   ├── ds18b20.py
    │   │   ├── htu21d.py
    │   │   ├── ads1115.py
    │   │   ├── ph_analog.py
    │   │   ├── tds_analog.py
    │   │   ├── flow_hall.py
    │   │   ├── float_switch.py
    │   │   ├── relay.py
    │   │   ├── peristaltic.py
    │   │   └── mock/           # simuladores de cada uno
    │   ├── calc.py             # VPD, punto de rocío, compensaciones
    │   ├── calibration.py      # lectura/escritura de /data/calibration.yaml
    │   ├── control/
    │   │   ├── monitor.py      # reglas de la sección 7.1
    │   │   ├── dosing.py       # reglas de la sección 7.2
    │   │   └── safety.py       # límites y bloqueos
    │   ├── alerts/
    │   │   ├── manager.py      # deduplicación, repetición y recuperación
    │   │   └── telegram.py
    │   ├── storage/influx.py
    │   ├── healthcheck.py      # healthcheck de Docker (heartbeat)
    │   └── cli.py              # `hidro calibrate`, `hidro read`, `hidro pump`
    └── tests/
```

### 5.4 Interfaces de drivers

```python
@dataclass(frozen=True)
class Measurement:
    name: str          # p. ej. "water_temp_1", "ph", "ec"
    value: float
    unit: str          # "°C", "%", "pH", "µS/cm", "ppm", "L/min", "L", "kPa", "bool"
    timestamp: datetime
    quality: Literal["ok", "suspect", "invalid"] = "ok"

class Sensor(Protocol):
    def read(self) -> list[Measurement]: ...
    def close(self) -> None: ...

class Switch(Protocol):          # relés y salidas on/off
    def on(self) -> None: ...
    def off(self) -> None: ...
    @property
    def is_on(self) -> bool: ...

class Pump(Protocol):            # dosificación por volumen
    def dispense_ml(self, ml: float) -> None: ...   # bloqueante, con corte forzado
```

- Cada driver se registra por `type` y recibe su bloque de configuración del YAML.
- Los drivers nunca deciden reglas de negocio; solo leen o actúan.
- Toda lectura inválida devuelve `quality="invalid"` en vez de lanzar excepción al ciclo principal; el ciclo registra, no se cae.
- Cada driver real tiene su simulador en `drivers/mock/` con comportamiento configurable (valor base, ruido, escenarios de falla como flujo en cero o sensor desconectado) para los tests y el modo `mock`.

### 5.5 Flujo de desarrollo y despliegue

- En el PC: `docker compose -f compose.yaml -f compose.mock.yaml up --build` debe levantar todo con datos simulados y Grafana mostrando gráficos.
- Tests: `pytest` sin hardware. Cobertura obligatoria para fórmulas, calibración, reglas de dosificación y límites de seguridad.
- Despliegue recomendado: repositorio git `PabloVeraH/nutriControl` (privado). En la Pi: `git pull && docker compose -f compose.yaml -f compose.hardware.yaml up -d --build`. `deploy.sh` lo automatiza por SSH. El host, el usuario SSH y la ruta remota viven **solo en `.env`** (`DEPLOY_HOST`, `DEPLOY_PATH`), nunca en el repositorio.
- La imagen se construye en la Pi (arm64). Si el build en la Pi resulta lento, proponer `docker buildx` multi-arquitectura desde el PC.

### 5.6 Configuración (ejemplo de `config/hidro.yaml`)

```yaml
mode: hardware            # hardware | mock (sobrescribible con HIDRO_HARDWARE)
read_interval_s: 30

sensors:
  - id: water_temp_1
    type: ds18b20
    serial: "28-xxxxxxxxxxxx"   # se obtiene en la fase 1
  - id: water_temp_2
    type: ds18b20
    serial: "28-yyyyyyyyyyyy"
  - id: air
    type: htu21d
    i2c_bus: 1
    address: 0x40
  - id: tank_level
    type: float_switch
    gpio: 17
    low_when: 0                 # se confirma en la fase 2
  - id: flow
    type: flow_hall
    gpio: 27
    pulses_per_liter: 5880      # se calibra en la fase 3
  - id: ph
    type: ph_analog
    adc: { address: 0x48, channel: 0, gain_v: 4.096 }
    temperature_from: water_temp_1
  - id: ec
    type: tds_analog
    adc: { address: 0x48, channel: 1, gain_v: 4.096 }
    temperature_from: water_temp_1
    power_switch: tds_power
    settle_s: 2

outputs:
  - id: tds_power
    type: relay
    gpio: 23
  - id: pump_ph_down
    type: peristaltic
    gpio: 22
    ml_per_min: null            # se calibra en la fase 6; null = bloqueada
    max_on_s: 30

ranges:
  ph:        { low: 5.5, high: 6.2, danger_low: 5.0, danger_high: 7.0 }
  ec:        { low: 1000, high: 1300 }          # µS/cm
  water_temp: { high_alert: 24 }
  air_temp:  { low_alert: 5, high_alert: 35 }
  flow:      { min_l_min: 0.3, alarm_after_s: 60 }

dosing:
  enabled: false            # se habilita explícitamente en la fase 6
  dry_run: true
  check_interval_s: 300
  mix_wait_s: 600           # espera tras cada dosis antes de volver a medir
  ph_down_ml: 1.0
  nutrient_ml: 3.0
  limits:
    max_doses_per_hour: 3
    max_ml_per_day: { pump_ph_down: 20 }

alerts:
  telegram:
    repeat_critical_every_min: 15
    daily_summary_at: "09:00"
```

---

## 6. Drivers y fórmulas

### 6.1 DS18B20

- Leer `/sys/bus/w1/devices/<serial>/w1_slave` y validar que la primera línea termine en `YES` (CRC correcto); la temperatura viene como `t=xxxxx` en milésimas de °C. (Alternativa: archivo `temperature`, sin CRC.)
- Descartar exactamente 85,000 °C (valor de encendido) y valores fuera de -55 a 125 °C → `invalid`.
- Hasta 2 reintentos con espera de 1 s; **registrar** cada falla (no repetir el error de Mycodo).
- Una conversión toma ~750 ms: no leer más seguido que eso.
- Aplicar `offset` opcional de configuración.

### 6.2 HTU21D (implementar con `smbus2`)

- Dirección 0x40. Reset suave `0xFE` al iniciar (esperar 15 ms).
- Temperatura sin retención de bus: comando `0xF3`, esperar ≥ 50 ms, leer 3 bytes (MSB, LSB, CRC).
- Humedad sin retención: comando `0xF5`, esperar ≥ 16 ms, leer 3 bytes.
- Verificar CRC-8: polinomio 0x31 (x⁸ + x⁵ + x⁴ + 1), valor inicial 0x00.
- Limpiar los 2 bits de estado: `S = raw & 0xFFFC`.
- `T = -46.85 + 175.72 * S / 65536` (°C); `RH = -6 + 125 * S / 65536` (%), acotar a 0–100.
- Derivados en `calc.py`:
  - Presión de vapor de saturación: `SVP = 0.61078 * exp(17.27 * T / (T + 237.3))` (kPa).
  - `VPD = SVP * (1 - RH / 100)` (kPa).
  - Punto de rocío (Magnus): `γ = ln(RH/100) + 17.62*T/(243.12+T)`; `Td = 243.12*γ / (17.62 - γ)`.
- Referencia de verificación: librería `adafruit-circuitpython-htu21d` (no es necesario depender de ella).

### 6.3 ADS1115 (implementar con `smbus2`)

- Dirección 0x48, modo single-shot, PGA ±4,096 V, 128 SPS.
- Registro de configuración `0x01`, registro de conversión `0x00`. Escribir la configuración con el bit OS para iniciar la conversión, esperar (~9 ms a 128 SPS o hacer polling del bit OS) y leer el valor de 16 bits con signo.
- `V = raw * 4.096 / 32768`.
- Para pH y TDS: tomar N muestras (p. ej. 10), descartar extremos, promediar (mediana o media recortada).
- Referencia de verificación: `adafruit-circuitpython-ads1x15`.

### 6.4 pH analógico (PH-4502C a través del divisor)

- El ADS1115 mide el voltaje **después del divisor**. No hace falta deshacer el divisor: la calibración se hace en el mismo circuito y lo absorbe.
- **Calibración en dos puntos** a temperatura `T_cal` (de la sonda DS18B20 en el momento de calibrar): buffers pH₁ = 6,86 (o 7,00) y pH₂ = 4,00, con voltajes V₁ y V₂ estabilizados.
  - Pendiente (V por unidad de pH): `s = (V1 - V2) / (pH1 - pH2)`
  - Voltaje a pH 7: `V7 = V1 + s * (7 - pH1)`
- **Medición con compensación de temperatura (Nernst sobre la pendiente):**
  - `s_T = s * (T + 273.15) / (T_cal + 273.15)`
  - `pH = 7 + (V - V7) / s_T`
- Si no hay temperatura disponible, usar `s` sin compensar y marcar `quality="suspect"`.
- Validar: |s| razonable (después del divisor, del orden de 0,1 a 0,15 V/pH; a definir con la primera calibración real) y advertir si la calibración es anómala o tiene más de 30 días.
- Guardar en `/data/calibration.yaml`: `V1, V2, pH1, pH2, T_cal, fecha`.

### 6.5 TDS / EC analógico (clon de DFRobot Gravity TDS)

Fórmula oficial de DFRobot (repositorio GravityTDS):

- `ec_raw = (133.42*V**3 - 255.86*V**2 + 857.39*V) * k`  (µS/cm, V en voltios a la salida de la placa)
- `ec_25 = ec_raw / (1 + 0.02 * (T - 25))`  (compensación de temperatura)
- `tds_ppm = ec_25 * 0.5`
- **Calibración de k** con solución de 1413 µS/cm a temperatura T:
  `k = 1413 * (1 + 0.02*(T - 25)) / (133.42*V**3 - 255.86*V**2 + 857.39*V)`
- Por defecto `k = 1.0` (marcar `suspect` hasta calibrar).
- Publicar ambas: `ec` (µS/cm, compensada a 25 °C) y `tds` (ppm). Las reglas usan `ec`.
- Respetar la secuencia de energizado de la sección 4.6.

### 6.6 Flujómetro (efecto Hall)

- `gpiozero.DigitalInputDevice` (backend lgpio) con callback en flanco de subida, o eventos de libgpiod v2. Contar **todos** los pulsos (incluido el primero).
- Contador protegido con lock. Cada ciclo: `L/min = pulsos_del_periodo / pulsos_por_litro / minutos_transcurridos`.
- Persistir el total acumulado en `/data` periódicamente para sobrevivir reinicios.
- Calibración: hacer pasar exactamente 1 L y ajustar `pulses_per_liter` con regla de tres (comando CLI).
- Antirrebote en software solo si el usuario observa pulsos espurios.

### 6.7 Flotador

- Entrada digital con el pull-down externo de 10 kΩ (configurar la entrada sin pull interno, o pull-down, consistente con el hardware).
- Publicar `tank_low` (bool) según `low_when`.
- Filtrar oleaje: el estado cambia solo si se mantiene N segundos (configurable, p. ej. 10 s).

### 6.8 Relés y bomba peristáltica

- Relé: `gpiozero.OutputDevice(active_high=True, initial_value=False)`.
- Bomba peristáltica: `dispense_ml(ml)` → `seconds = ml / ml_per_min * 60`; encender, esperar, apagar **siempre** en un `try/finally`. Rechazar si `seconds > max_on_s` o si `ml_per_min` es `null` (no calibrada).
- Calibración: CLI que enciende 60 s; el usuario mide los ml en una probeta; se guarda `ml_per_min`.

---

## 7. Reglas de control y dosificación

### 7.1 Monitoreo (siempre activo)

| Condición | Severidad | Acción |
|---|---|---|
| Flujo < `min_l_min` por más de `alarm_after_s` | **CRÍTICA** | Telegram inmediato; repetir cada 15 min hasta normalizar; bloquear dosificación |
| Estanque bajo (flotador) | Alta | Telegram; bloquear dosificación |
| pH fuera de rango de peligro | Alta | Telegram (si la dosificación está habilitada, corregir según 7.2) |
| pH fuera del rango normal sostenido > 30 min | Media | Telegram |
| EC sobre el máximo | Media | Telegram: "agregar agua para diluir" |
| EC bajo el mínimo sostenido > 30 min | Media | Telegram (o dosificar si hay bombas A/B) |
| Agua > 24 °C | Media | Telegram |
| Aire fuera de 5–35 °C | Media | Telegram |
| Sensor sin lectura válida > 5 min | Media | Telegram indicando cuál |
| Recuperación de cualquier condición | Info | Telegram "normalizado" |

- Deduplicar: no repetir la misma alerta salvo las críticas (cada 15 min).
- Resumen diario a la hora configurada: min/máx/promedio de cada variable y dosis aplicadas.
- Comandos opcionales del bot (fase 7): `/estado`, `/silenciar <minutos>`.

### 7.2 Dosificación (basada en la lógica del proyecto de referencia)

Cada `check_interval_s`, con las últimas lecturas **válidas y recientes**:

1. **Bloqueos (no dosificar si cualquiera se cumple):** flujo en alarma; estanque bajo; pH o EC `invalid`, `suspect` o con más de 2 ciclos de antigüedad; dentro de `mix_wait_s` desde la última dosis; límites de `safety.py` alcanzados; `dosing.enabled == false`.
2. **pH en peligro** (< `danger_low` o > `danger_high`): corregir pH primero (ácido o base, `ph_down_ml`), alertar.
3. Si no: **EC bajo** el mínimo → dosificar nutriente A y B en partes iguales (`nutrient_ml` cada una).
4. Si no: **EC sobre** el máximo → no dosificar; alertar "diluir con agua".
5. Si no: **pH fuera del rango normal** → corregir pH.

- Razón del orden: los nutrientes bajan el pH; ajustar pH antes de nutrientes obliga a reajustar.
- **Configuración inicial con una sola bomba:** asignarla a **pH down** (en hidroponía el pH tiende a subir a medida que las plantas crecen). Las reglas que requieran bombas inexistentes generan alerta en vez de dosis.
- `dry_run: true` registra en log y en Telegram lo que habría hecho, sin activar bombas. Pasar a `false` solo con confirmación explícita del usuario (fase 6).

### 7.3 Límites de seguridad (`safety.py`)

- Máximo de dosis por hora y de ml por día, por bomba.
- Si se alcanza un límite: bloquear esa bomba, alertar y **exigir reinicio manual** (CLI `hidro safety reset`).
- Corte forzado de cada bomba en `max_on_s`.

---

## 8. Robustez

- **Al arrancar:** apagar todas las salidas antes de cualquier otra cosa.
- **Al recibir SIGTERM/SIGINT:** apagar todas las salidas, cerrar drivers, salir limpio. `stop_grace_period` suficiente en compose.
- **Prueba obligatoria (fase 2 y 6):** con un relé encendido, detener el contenedor (`docker compose stop hidro-core`) y luego matarlo (`docker kill`) y verificar qué hace el relé en cada caso. Si al liberar el GPIO el relé queda encendido o en estado indeterminado, documentarlo y proponer mitigación (resistencia pull-down en la entrada del módulo, etc.) antes de conectar cargas.
- Heartbeat: `hidro-core` escribe la hora de su último ciclo en `/data/heartbeat`; `healthcheck` de Docker falla si tiene más de 3 ciclos de antigüedad.
- Logs estructurados (JSON) a stdout; rotación con la configuración de logging de Docker (`max-size`, `max-file`).
- Una excepción en un driver nunca detiene el ciclo: se registra, se marca la medición como `invalid` y se continúa.
- Backups: script para exportar `/data` (calibraciones) y el bucket de InfluxDB; documentar cómo restaurar.

---

## 9. Fases

Cada fase: objetivo, tareas, criterio de aceptación y **punto de control humano**. No pasar a la siguiente sin cerrar el punto de control y registrarlo en `PROGRESS.md`.

### Fase 0 — Esqueleto en el PC (sin hardware)

- Tareas: estructura del repositorio; `compose.yaml` + `compose.mock.yaml`; `hidro-core` con interfaces, registro de drivers, config con validación, drivers mock para todos los sensores y salidas de la sección 3.1; escritura a InfluxDB; Grafana con provisioning (datasource + dashboard); alertas Telegram funcionando con datos simulados (incluido un escenario de flujo en cero); tests de fórmulas (6.2 a 6.5), reglas (7.2) y límites (7.3); `README.md`, `.env.example`, `deploy.sh`.
- Aceptación: `docker compose -f compose.yaml -f compose.mock.yaml up --build` muestra gráficos en Grafana; `pytest` pasa; llega una alerta de prueba a Telegram.
- Punto de control: el usuario crea el bot con @BotFather, pone token y chat id en `.env`, levanta el stack en su PC y confirma que ve gráficos y recibe alertas.

### Fase 1 — Raspberry lista + sondas de temperatura

- Tareas: documentar la preparación del host (Docker, grupos `gpio` e `i2c`, I2C y 1-Wire activados, GIDs en `.env`); primer despliegue; driver real `ds18b20`; verificar acceso a `/sys/bus/w1` desde el contenedor; verificar `/dev/gpiochip0` y `/dev/i2c-1`.
- Punto de control: el usuario cablea las sondas (4.2), corre las pruebas de host, entrega los dos seriales `28-…`; con una sonda en la mano la temperatura sube en Grafana.

### Fase 2 — Flotador y relés (sin carga)

- Tareas: drivers `float_switch` y `relay`; CLI `hidro switch <id> on|off`; prueba de robustez de la sección 8.
- Punto de control: el usuario cablea flotador (4.3) y control de relés (4.7, sin cargas); confirma `low_when`; confirma que los relés hacen clic con el CLI; informa el resultado de la prueba de detener/matar el contenedor.

### Fase 3 — Flujómetro

- Requiere: resistencias de 10 kΩ y 20 kΩ.
- Tareas: driver `flow_hall`; CLI de calibración de pulsos por litro; alarma crítica real.
- Punto de control: el usuario cablea (4.4), sopla o hace pasar agua y ve flujo; calibra con 1 L; corta el flujo y recibe la alerta crítica en Telegram.

### Fase 4 — Aire (HTU21D)

- Requiere: pines soldados.
- Tareas: driver `htu21d` con CRC; VPD y punto de rocío.
- Punto de control: `i2cdetect` muestra `40`; temperatura y humedad coherentes con el ambiente; al soplar sube la humedad.

### Fase 5 — pH y TDS (ADS1115)

- Requiere: ADS1115, resistencias, soluciones de calibración.
- Tareas: drivers `ads1115`, `ph_analog`, `tds_analog`; secuencia de energizado del TDS; CLI `hidro calibrate ph --point 6.86`, `--point 4.00`, `hidro calibrate ec --standard 1413`; validación de calibraciones.
- Punto de control: `i2cdetect` muestra `40` y `48`; el usuario calibra guiado por el CLI paso a paso (enjuagar con agua destilada entre soluciones, esperar estabilidad); verifica en una tercera solución o con un medidor de lápiz si tiene; prueba de interferencia pH/TDS.

### Fase 6 — Bomba peristáltica y dosificación

- Requiere: fuente de 12 V.
- Tareas: driver `peristaltic`; CLI de calibración de ml/min; reglas de 7.2 en `dry_run`; límites 7.3.
- Punto de control: el usuario cablea el circuito de 12 V (4.7) y calibra con agua; una semana (o lo que acuerden) en `dry_run` revisando las decisiones en Telegram; solo con su confirmación explícita, `dry_run: false`.

### Fase 7 — Operación

- Tareas: alerta de "sin datos" en Grafana hacia Telegram; resumen diario; comandos `/estado` y `/silenciar`; backups; documentación final (operación diaria, recalibración mensual del pH, cuidado de la sonda).
- Punto de control: el usuario detiene `hidro-core` a propósito y recibe la alerta de Grafana; revisa el README y confirma que puede operar sin ayuda.

### Futuro (fuera del alcance actual)

Drivers de la sección 3.3; bombas para nutrientes A y B; control de luz de cultivo por horario (6:00 a 20:00 en el proyecto de referencia) vía relé de estado sólido; timelapse con cámara.

---

## 10. Errores típicos a prevenir

- Conectar una señal de 5 V directo a un GPIO (pH `Po`, flujómetro).
- Usar el relé de estado sólido para la bomba de 12 V DC (no corta corriente continua).
- Olvidar el puente "High" del relé mecánico.
- No activar I2C o 1-Wire en el host.
- Dejar secar la sonda de pH.
- Medir pH con el TDS energizado en el mismo recipiente.
- Dosificar sin flujo o con estanque bajo.
- Tomar como válida la lectura de 85 °C del DS18B20.
- GIDs de `gpio`/`i2c` distintos entre el host y lo declarado en `.env`.
- Publicar puertos innecesarios o usar 80/443.
- Commitear `.env` con el token de Telegram.

---

## 11. Decisiones de arranque (resueltas)

1. **PC:** Windows con Docker Desktop instalado (Docker 29, Compose v5) y Python 3.12.
2. **Raspberry Pi 4:** 4 GB de RAM. Comparte equipo con Plex, Pi-hole, nginx y otros contenedores; de ahí los límites de memoria y la retención de 365 días.
3. **Repositorio:** GitHub `PabloVeraH/nutriControl`, privado. Flujo: una rama por fase, integrada a `main` al cerrar el punto de control.
4. **Pi:** usuario e IP de SSH definidos en `.env` (`DEPLOY_HOST`). **Dato privado: no se versiona.**
5. **Puertos ocupados en la Pi:** 22, 53, 80, 111, 139, 443, 445, 631, 1901, 32400, 5050, 5900, 8000, 8080, 8443, 9091, 9443, 51413, 51821. **3300 está libre** y es el que usa Grafana.
6. **Piezas de la sección 3.2:** ninguna comprada todavía. La fase 0 se completa en el PC; las fases de hardware arrancan cuando lleguen los componentes.
7. **Plantas y estanque:** sin definir. Se usan dosis conservadoras y se ajustan en la fase 6.

---

## 12. Fuentes

- Proyecto de referencia: [Automated Hydroponic System Build — Kyle Gabriel](https://kylegabriel.com/projects/2020/06/automated-hydroponic-system-build.html)
- [Mycodo en GitHub](https://github.com/kizniche/Mycodo) (GPL-3.0; solo como referencia) y su [README de Docker](https://github.com/kizniche/Mycodo/blob/master/docker/README.md)
- [DFRobot GravityTDS](https://github.com/DFRobot/GravityTDS) (fórmula del TDS) y [wiki del sensor SEN0244](https://wiki.dfrobot.com/Gravity__Analog_TDS_Sensor___Meter_For_Arduino_SKU__SEN0244)
- [rppal en docs.rs](https://docs.rs/crate/rppal/0.22.1) (estado del ecosistema Rust)
- [pigpiod no funciona en Raspberry Pi 5](https://discourse.nodered.org/t/migrating-to-pi-5-trouble-with-pigpiod/88762)
- Hojas de datos a consultar: HTU21D (TE Connectivity), ADS1115 (Texas Instruments), DS18B20 (Analog Devices/Maxim), YF-S401.
