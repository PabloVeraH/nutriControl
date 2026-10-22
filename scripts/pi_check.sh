#!/usr/bin/env bash
# Diagnóstico del host para las fases de hardware (Raspberry Pi).
# No modifica nada: solo lee el sistema, los grupos y los dispositivos.
#
# Uso: bash scripts/pi_check.sh
set -u

FAILS=0
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

section() { printf '\n== %s ==\n' "$1"; }
pass() { printf '  [ok]    %s\n' "$1"; }
warn() { printf '  [aviso] %s\n' "$1"; }
fail() {
  printf '  [falla] %s\n' "$1"
  FAILS=$((FAILS + 1))
}

section "Sistema"
if [[ -r /proc/device-tree/model ]]; then
  pass "modelo: $(tr -d '\0' < /proc/device-tree/model)"
else
  warn "no se pudo leer el modelo de la placa"
fi
pass "arquitectura: $(uname -m)"
pass "kernel: $(uname -r)"
pass "fecha: $(date '+%Y-%m-%d %H:%M:%S %Z')"

MEM_MB="$(awk '/^MemTotal:/ {printf "%d", $2 / 1024}' /proc/meminfo)"
pass "RAM: ${MEM_MB} MB"

DISK_MB="$(df -Pm / | awk 'NR == 2 {print $4}')"
if [[ -n "$DISK_MB" && "$DISK_MB" -lt 4096 ]]; then
  warn "quedan ${DISK_MB} MB libres en /; se recomiendan al menos 4 GB"
else
  pass "espacio libre en /: ${DISK_MB} MB"
fi

section "Docker"
if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    pass "docker $(docker version --format '{{.Server.Version}}' 2>/dev/null)"
  else
    fail "docker instalado pero sin acceso (¿usuario en el grupo docker y sesión reiniciada?)"
  fi
  if docker compose version >/dev/null 2>&1; then
    pass "docker compose $(docker compose version --short 2>/dev/null)"
  else
    fail "falta el plugin docker compose"
  fi
else
  fail "docker no está instalado"
fi

section "Grupos"
for group in docker gpio i2c; do
  if [[ " $(id -nG) " == *" $group "* ]]; then
    pass "usuario en el grupo $group"
  else
    warn "usuario fuera del grupo $group (¿cerraste sesión tras usermod?)"
  fi
  line="$(getent group "$group" 2>/dev/null || true)"
  if [[ -n "$line" ]]; then
    pass "grupo $group: $line"
  else
    warn "no existe el grupo $group en este sistema"
  fi
done

ENV_FILE="$ROOT/.env"
if [[ -f "$ENV_FILE" ]]; then
  for pair in "GPIO_GID:gpio" "I2C_GID:i2c"; do
    key="${pair%%:*}"
    group="${pair##*:}"
    declared="$(awk -F= -v k="$key" '$1 == k {gsub("\r", "", $2); print $2}' "$ENV_FILE" | tail -n 1)"
    actual="$(getent group "$group" 2>/dev/null | cut -d: -f3 || true)"
    if [[ -z "$declared" ]]; then
      warn "$key no está definido en .env"
    elif [[ "$declared" == "$actual" ]]; then
      pass "$key=$declared coincide con el grupo $group"
    else
      fail "$key=$declared pero el grupo $group tiene GID ${actual:-?}"
    fi
  done
else
  warn "no hay .env en $ROOT; no se pueden comparar los GIDs"
fi

section "Dispositivos"
for device in /dev/i2c-1 /dev/gpiochip0; do
  if [[ -e "$device" ]]; then
    pass "$device presente ($(stat -c '%A %U:%G' "$device" 2>/dev/null))"
  else
    fail "$device no existe (¿I2C activado y placa compatible?)"
  fi
done

CONFIG_TXT=/boot/firmware/config.txt
[[ -f "$CONFIG_TXT" ]] || CONFIG_TXT=/boot/config.txt
if [[ -f "$CONFIG_TXT" ]]; then
  if grep -Eq '^[[:space:]]*dtparam=i2c_arm=on' "$CONFIG_TXT"; then
    pass "I2C activado en $CONFIG_TXT"
  else
    fail "I2C no está activado en $CONFIG_TXT (raspi-config nonint do_i2c 0)"
  fi
  if grep -Eq '^[[:space:]]*dtoverlay=w1-gpio' "$CONFIG_TXT"; then
    pass "1-Wire activado en $CONFIG_TXT"
  else
    fail "1-Wire no está activado en $CONFIG_TXT (raspi-config nonint do_onewire 0)"
  fi
else
  fail "no se encontró config.txt de la Raspberry"
fi

section "Sondas DS18B20"
W1_DIR=/sys/bus/w1/devices
mapfile -t PROBES < <(find "$W1_DIR" -maxdepth 1 -type l -name '28-*' 2>/dev/null | sort)
if [[ ${#PROBES[@]} -eq 0 ]]; then
  fail "no hay sondas DS18B20 en $W1_DIR (revisar cableado y dtoverlay)"
else
  for probe in "${PROBES[@]}"; do
    serial="$(basename "$probe")"
    if ! grep -q 'YES' "$probe/w1_slave" 2>/dev/null; then
      fail "$serial: CRC inválida o lectura incompleta"
      continue
    fi
    raw="$(awk -F't=' '/t=/ {print $2; exit}' "$probe/w1_slave")"
    temp="$(awk -v v="$raw" 'BEGIN {printf "%.3f", v / 1000}')"
    if awk -v t="$temp" 'BEGIN {exit !(t >= 84.9)}'; then
      fail "$serial: ${temp} °C (sospechoso; sonda desconectada o cableado)"
    else
      pass "$serial: ${temp} °C"
    fi
  done
fi

section "I2C"
if command -v i2cdetect >/dev/null 2>&1; then
  if i2cdetect -y 1 >/dev/null 2>&1; then
    pass "i2cdetect -y 1:"
    i2cdetect -y 1 2>/dev/null | sed 's/^/          /'
  else
    warn "i2cdetect falló (¿I2C activado?)"
  fi
else
  warn "i2cdetect no instalado (sudo apt install i2c-tools)"
fi

section "Puerto de Grafana"
if command -v ss >/dev/null 2>&1; then
  if ss -ltnH 2>/dev/null | awk '{print $4}' | grep -qE '[:.]3300$'; then
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'hidro-grafana-1'; then
      pass "3300 en uso por hidro-grafana"
    else
      fail "3300 ocupado por otro proceso"
    fi
  else
    pass "3300 libre"
  fi
else
  warn "ss no disponible; no se pudo revisar el puerto 3300"
fi

section "Contenedores"
if docker info >/dev/null 2>&1; then
  docker ps --format '  {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || true
else
  warn "no se pudo listar contenedores"
fi

section "Resumen"
if [[ $FAILS -eq 0 ]]; then
  printf '  Sin fallas. El host está listo para desplegar.\n'
else
  printf '  %d comprobaciones fallaron. Corrígelas antes de la puesta en marcha.\n' "$FAILS"
fi
exit $((FAILS > 0))
