#!/usr/bin/env bash
# Verificación asistida de la fase 2: flotador y relés, sin cargas.
#
# Se ejecuta en la Raspberry Pi, desde la raíz del repositorio y con el stack
# ya desplegado. Detiene hidro-core para liberar los GPIO y lo vuelve a dejar
# como estaba al terminar.
#
# Uso: bash scripts/phase2_check.sh [id_de_salida]
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE=(docker compose -f compose.yaml -f compose.hardware.yaml)
OUTPUT_ID="${1:-tds_power}"
PROBLEMS=0
WAS_RUNNING=0

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

wait_healthy() {
  local id status _i
  for _i in $(seq 1 40); do
    id="$("${COMPOSE[@]}" ps -q hidro-core 2>/dev/null | head -n 1)"
    if [[ -n "$id" ]]; then
      status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id" 2>/dev/null)"
      [[ "$status" == "healthy" ]] && return 0
      [[ "$status" == "running" ]] && sleep 1 && continue
    fi
    sleep 1
  done
  return 1
}

restore_core() {
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

Esta prueba acciona el flotador y los relés. No conectes cargas todavía
(bomba, 12 V, 220 V). Usa solo la protoboard y observa el clic o el LED del
módulo de relés.
EOF
if ! ask_yes "¿Confirmas que los relés están sin carga?"; then
  echo "Prueba cancelada." >&2
  exit 1
fi

section "1/3 Flotador (GPIO17)"
if ! core_stop; then
  problem "no se pudo detener hidro-core; los GPIO siguen ocupados"
  exit 1
fi
echo "hidro-core detenido para liberar el GPIO del flotador."

before="$(run_cli read 2>&1)"
if ! printf '%s\n' "$before" | grep -q 'tank_level_low'; then
  problem "no se pudo leer tank_level_low"
  printf '%s\n' "$before" | sed 's/^/  /'
else
  printf '%s\n' "$before" | grep 'tank_level_low' | sed 's/^/  /'
  echo
  echo "Mueve el flotador a la posición contraria (subirlo o bajarlo)."
  read -r -p "Pulsa Enter cuando ya lo moviste... " _
  after="$(run_cli read 2>&1)"
  printf '%s\n' "$after" | grep 'tank_level_low' | sed 's/^/  /'
  before_low="$(printf '%s\n' "$before" | awk '/tank_level_low/ {print $2}')"
  after_low="$(printf '%s\n' "$after" | awk '/tank_level_low/ {print $2}')"
  if [[ "$before_low" == "$after_low" ]]; then
    problem "el valor no cambió; revisa el cableado y ajusta low_when en config/hidro.yaml"
  else
    echo "  [ok] tank_level_low cambió de $before_low a $after_low."
    echo "  Anota cuál posición física corresponde a 'estanque bajo' y deja low_when acorde."
  fi
fi

section "2/3 Relé $OUTPUT_ID (GPIO23)"
echo "Se probará el canal 2 (alimentación del TDS), sin carga."
echo "El canal 1 (bomba, GPIO22) se comprueba con pinctrl o en la fase 6."
if ! run_cli switch "$OUTPUT_ID" off; then
  problem "no se pudo apagar $OUTPUT_ID"
else
  echo "Pulsa Enter y observa el módulo durante 3 segundos."
  read -r -p "Enter para encender... " _
  if ! run_cli switch "$OUTPUT_ID" on --seconds 3; then
    problem "no se pudo encender $OUTPUT_ID"
  elif ask_yes "¿El relé hizo clic o encendió su LED?"; then
    echo "  [ok] el GPIO activa el módulo de relés."
  else
    problem "el relé no respondió; revisa el puente High, la alimentación de 5 V y el GPIO"
  fi
fi

section "3/3 Robustez al detener el contenedor"
echo "SIGTERM: hidro-core debe apagar todas las salidas antes de salir."
echo "El relé $OUTPUT_ID se energiza unos 2 s en cada ciclo (cada 30 s)."
echo "Detén el contenedor justo cuando escuches el clic para probar con el relé activo."
core_up
if ! wait_healthy; then
  problem "hidro-core no llegó a healthy"
else
  read -r -p "Cuando escuches el clic, pulsa Enter para ejecutar 'docker compose stop'... " _
  "${COMPOSE[@]}" stop hidro-core >/dev/null 2>&1 || true
  sleep 2
  "${COMPOSE[@]}" logs --tail 6 hidro-core 2>&1 | sed 's/^/  /'
  if ask_yes "¿Estaba encendido al detener y quedó apagado?"; then
    echo "  [ok] apagado limpio con SIGTERM."
  else
    problem "el relé no se apagó con SIGTERM o la prueba se hizo con el relé ya apagado"
  fi
fi

echo
echo "SIGKILL: el proceso no alcanza a ejecutar el apagado ordenado."
echo "El relé $OUTPUT_ID se energiza unos 2 s en cada ciclo (cada 30 s)."
core_up
if ! wait_healthy; then
  problem "hidro-core no llegó a healthy para la prueba de SIGKILL"
else
  echo "Espera a escuchar el clic del relé y pulsa Enter de inmediato."
  read -r -p "Cuando escuches el clic, pulsa Enter para ejecutar 'docker compose kill'... " _
  "${COMPOSE[@]}" kill hidro-core >/dev/null 2>&1 || true
  if ask_yes "¿Estaba encendido al matar y quedó apagado o en un estado seguro?"; then
    echo "  [ok] el módulo no queda activo al liberar el GPIO."
  else
    problem "el relé queda activo o indeterminado tras SIGKILL"
    cat <<'EOF'
    Mitigación antes de conectar cargas:
      - agregar una resistencia pull-down en la entrada IN del módulo, o
      - usar el contacto NC para que la carga quede desconectada sin señal.
    Documenta el resultado en PROGRESS.md.
EOF
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
