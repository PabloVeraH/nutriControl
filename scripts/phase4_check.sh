#!/usr/bin/env bash
# Verificación asistida de la fase 4: sensor de aire HTU21D.
#
# Se ejecuta en la Raspberry Pi, desde la raíz del repositorio y con el stack
# ya desplegado. Detiene hidro-core porque `hidro read` abre todos los GPIO y
# el bus I2C (un segundo proceso no puede tomarlos) y lo vuelve a dejar como
# estaba al terminar.
#
# Uso: bash scripts/phase4_check.sh [id_del_sensor]
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE=(docker compose -f compose.yaml -f compose.hardware.yaml)
AIR_ID="${1:-air}"
PROBLEMS=0
WAS_RUNNING=0
CORE_STOPPED=0

section() { printf '\n== %s ==\n' "$1"; }
problem() {
  printf '  [problema] %s\n' "$1"
  PROBLEMS=$((PROBLEMS + 1))
}
ask_yes() {
  local answer=""
  read -r -p "$1 [s/N] " answer
  [[ "$answer" =~ ^[sSyY]$ ]]
}
run_cli() { "${COMPOSE[@]}" run --rm --no-deps hidro-core "$@"; }
core_up() { "${COMPOSE[@]}" up -d hidro-core >/dev/null 2>&1 || true; }
core_stop() { "${COMPOSE[@]}" stop hidro-core >/dev/null 2>&1; }
value_of() { printf '%s\n' "$1" | awk -v n="$2" '$1 == n {print $2; exit}'; }
quality_of() { printf '%s\n' "$1" | awk -v n="$2" '$1 == n {print $4; exit}'; }
is_number() { [[ "$1" =~ ^-?[0-9]+([.][0-9]+)?$ ]]; }

restore_core() {
  [[ "$CORE_STOPPED" == "1" ]] || return 0
  CORE_STOPPED=0
  if [[ "$WAS_RUNNING" == "1" ]]; then
    core_up
  else
    core_stop >/dev/null 2>&1 || true
  fi
}
trap restore_core EXIT

section "Comprobaciones previas"
if [[ ! -f .env ]]; then
  echo "Falta .env en $ROOT." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "docker no está disponible para este usuario." >&2
  exit 1
fi
if ! "${COMPOSE[@]}" config -q; then
  echo "La configuración de Compose no es válida." >&2
  exit 1
fi
if ! run_cli config-check; then
  echo "config/hidro.yaml no es válido." >&2
  exit 1
fi

if "${COMPOSE[@]}" ps --status running --services 2>/dev/null | grep -qx hidro-core; then
  WAS_RUNNING=1
fi

cat <<'EOF'

Esta prueba lee el HTU21D y comprueba que la humedad sube al soplar.
No mojes el sensor: la humedad ambiente es suficiente.
EOF
if ! ask_yes "¿El HTU21D está cableado a 3,3 V y el bus I2C está libre?"; then
  echo "Prueba cancelada." >&2
  exit 1
fi

section "1/3 Presencia en el bus I2C"
if ! core_stop; then
  problem "no se pudo detener hidro-core"
  exit 1
fi
CORE_STOPPED=1
if command -v i2cdetect >/dev/null 2>&1; then
  i2c_out="$(i2cdetect -y 1 2>&1)"
  printf '%s\n' "$i2c_out" | sed 's/^/  /'
  if printf '%s\n' "$i2c_out" | grep -Eq '(^|[[:space:]])40([[:space:]]|$)'; then
    echo "  [ok] el HTU21D responde en 0x40."
  else
    problem "no aparece 0x40; revisa 3,3 V, GND, SDA/SCL y las soldaduras"
  fi
else
  echo "  [aviso] i2cdetect no está instalado; se omite esta comprobación."
  echo "          Instálalo con: sudo apt install i2c-tools"
fi

section "2/3 Lectura de temperatura, humedad, VPD y punto de rocío"
first="$(run_cli read 2>&1)"
air_lines="$(printf '%s\n' "$first" | grep -E "^(  )?${AIR_ID}_(temp|humidity|vpd|dew_point)")"
if [[ -n "$air_lines" ]]; then
  printf '%s\n' "$air_lines" | sed 's/^/  /'
else
  # Sin filas del sensor: mostrar la salida completa (suele traer el error).
  printf '%s\n' "$first" | sed 's/^/  /'
fi
for name in "${AIR_ID}_temp" "${AIR_ID}_humidity" "${AIR_ID}_vpd" "${AIR_ID}_dew_point"; do
  value="$(value_of "$first" "$name")"
  quality="$(quality_of "$first" "$name")"
  if [[ -z "$value" ]]; then
    problem "no se publicó $name"
  elif [[ "$quality" != "ok" ]]; then
    problem "$name tiene calidad '$quality' (CRC, trama incompleta o bus)"
  elif ! is_number "$value"; then
    problem "$name no es numérico: $value"
  else
    echo "  [ok] $name = $value"
  fi
done

temp="$(value_of "$first" "${AIR_ID}_temp")"
humidity="$(value_of "$first" "${AIR_ID}_humidity")"
vpd="$(value_of "$first" "${AIR_ID}_vpd")"
dew="$(value_of "$first" "${AIR_ID}_dew_point")"
if is_number "$temp" && awk -v t="$temp" 'BEGIN {exit !(t >= -10 && t <= 60)}'; then
  echo "  [ok] temperatura dentro de un rango ambiente razonable."
else
  problem "temperatura fuera de -10 a 60 °C; revisa el sensor"
fi
if is_number "$humidity" && awk -v h="$humidity" 'BEGIN {exit !(h >= 0 && h <= 100)}'; then
  echo "  [ok] humedad dentro de 0–100 %."
else
  problem "humedad fuera de 0–100 %"
fi
if is_number "$vpd" && awk -v v="$vpd" 'BEGIN {exit !(v >= 0)}'; then
  echo "  [ok] VPD no negativo."
else
  problem "VPD ausente o negativo"
fi
if is_number "$temp" && is_number "$dew" \
  && awk -v t="$temp" -v d="$dew" 'BEGIN {exit !(d <= t + 0.5)}'; then
  echo "  [ok] punto de rocío coherente con la temperatura."
else
  problem "punto de rocío mayor que la temperatura"
fi

section "3/3 Respuesta al aliento"
if ! is_number "$humidity"; then
  problem "sin humedad inicial; no se puede comparar"
else
  # La lectura arranca un contenedor nuevo y tarda unos segundos: si se sopla
  # antes, la humedad ya bajó cuando llega la lectura.
  echo "Al pulsar Enter empieza a soplar suavemente sobre el sensor, sin tocarlo"
  echo "ni mojarlo, y sigue soplando hasta que aparezca la lectura."
  read -r -p "Pulsa Enter y sopla... " _
  second="$(run_cli read 2>&1)"
  humidity_after="$(value_of "$second" "${AIR_ID}_humidity")"
  if ! is_number "$humidity_after"; then
    problem "la segunda lectura no tiene humedad válida"
  else
    printf '  antes: %s %%   después: %s %%\n' "$humidity" "$humidity_after"
    if awk -v a="$humidity" -v b="$humidity_after" 'BEGIN {exit !(b >= a + 2)}'; then
      echo "  [ok] la humedad subió al menos 2 puntos."
    elif awk -v a="$humidity" 'BEGIN {exit !(a >= 95)}'; then
      echo "  [aviso] la humedad ya era muy alta; prueba en un ambiente más seco."
    else
      problem "la humedad no subió; acerca el aliento o revisa la ventilación del sensor"
    fi
  fi
fi

restore_core

section "Resumen"
if [[ "$PROBLEMS" -eq 0 ]]; then
  echo "Sin problemas detectados. Registra el resultado en PROGRESS.md."
else
  printf '%d comprobaciones presentaron problemas.\n' "$PROBLEMS"
fi
exit $((PROBLEMS > 0))
