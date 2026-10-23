#!/usr/bin/env bash
# Restaura un respaldo creado por scripts/backup.sh.
#
# Uso: bash scripts/restore.sh backups/AAAAMMDDTHHMMSS
# En la Pi usa compose.hardware.yaml; para el PC: RESTORE_COMPOSE=compose.mock.yaml
# La restauración reemplaza el volumen /data y el bucket completo de InfluxDB.
set -euo pipefail

# Git Bash convierte rutas de contenedor (/tmp, /data) a rutas de Windows.
export MSYS_NO_PATHCONV=1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ $# -ne 1 ]]; then
  echo "Uso: bash scripts/restore.sh <directorio-del-respaldo>" >&2
  exit 2
fi
if [[ ! -f .env ]]; then
  echo "Falta .env; no se pueden leer los secretos de InfluxDB." >&2
  exit 1
fi
if [[ ! -d "$1" ]]; then
  echo "No existe el directorio de respaldo: $1" >&2
  exit 1
fi
BACKUP_DIR="$(cd "$1" && pwd)"
# Git Bash entrega rutas /c/... que Docker Desktop no entiende como bind.
HOST_BACKUP_DIR="$BACKUP_DIR"
if command -v cygpath >/dev/null 2>&1; then
  HOST_BACKUP_DIR="$(cygpath -m "$BACKUP_DIR")"
fi

for item in hidro-data.tgz influx; do
  if [[ ! -e "$BACKUP_DIR/$item" ]]; then
    echo "Falta $item en $BACKUP_DIR." >&2
    exit 1
  fi
done

# Carga .env sin depender del shell y tolerando finales de línea CRLF.
while IFS='=' read -r key value || [[ -n "$key" ]]; do
  key="${key%$'\r'}"
  value="${value%$'\r'}"
  [[ -z "$key" || "$key" == \#* ]] && continue
  [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
  export "$key=$value"
done < .env

: "${INFLUX_TOKEN:?definir INFLUX_TOKEN en .env}"
: "${INFLUX_ORG:?definir INFLUX_ORG en .env}"

if ! docker info >/dev/null 2>&1; then
  echo "docker no está disponible para este usuario (¿grupo docker?)." >&2
  exit 1
fi

OVERRIDE="${RESTORE_COMPOSE:-compose.hardware.yaml}"
if [[ ! -f "$OVERRIDE" ]]; then
  echo "No existe el compose indicado: $OVERRIDE" >&2
  exit 1
fi
COMPOSE_FILES=(-f compose.yaml -f "$OVERRIDE")
if [[ -f data/grafana/alerting/contact-points.yaml ]]; then
  COMPOSE_FILES+=(-f compose.alerts.yaml)
fi

if [[ "${RESTORE_YES:-0}" != "1" ]]; then
  echo "Se reemplazarán /data y el bucket '$INFLUX_BUCKET' de InfluxDB."
  read -r -p "¿Continuar? [s/N] " answer
  case "$answer" in
    s|S|si|sí|y|yes) ;;
    *) echo "restauración cancelada"; exit 1 ;;
  esac
fi

DATA_VOLUME="${HIDRO_DATA_VOLUME:-}"
CORE_ID="$(docker compose "${COMPOSE_FILES[@]}" ps -q hidro-core 2>/dev/null || true)"
if [[ -z "$DATA_VOLUME" && -n "$CORE_ID" ]]; then
  DATA_VOLUME="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' "$CORE_ID")"
fi
DATA_VOLUME="${DATA_VOLUME:-hidro_hidro_data}"
if ! docker volume inspect "$DATA_VOLUME" >/dev/null 2>&1; then
  echo "No se encontró el volumen de datos '$DATA_VOLUME'." >&2
  exit 1
fi

echo "Deteniendo hidro-core y Grafana"
docker compose "${COMPOSE_FILES[@]}" stop hidro-core grafana >/dev/null 2>&1 || true

echo "Restaurando $DATA_VOLUME"
docker run --rm \
  -v "$DATA_VOLUME:/data" \
  -v "$HOST_BACKUP_DIR:/backup:ro" \
  alpine:3.20 \
  sh -c 'cd /data && rm -rf ./* ./.[!.]* ./..?* 2>/dev/null || true; tar xzf /backup/hidro-data.tgz -C /data'

echo "Levantando InfluxDB para restaurar el bucket"
docker compose "${COMPOSE_FILES[@]}" up -d influxdb
for _ in $(seq 1 30); do
  if docker compose "${COMPOSE_FILES[@]}" exec -T influxdb influx ping >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

INFLUX_ID="$(docker compose "${COMPOSE_FILES[@]}" ps -q influxdb)"
if [[ -z "$INFLUX_ID" ]]; then
  echo "InfluxDB no arrancó; no se puede restaurar." >&2
  exit 1
fi
docker compose "${COMPOSE_FILES[@]}" exec -T influxdb rm -rf /tmp/hidro-restore
docker cp "$HOST_BACKUP_DIR/influx" "$INFLUX_ID:/tmp/hidro-restore"
# Con --full no se puede limitar a una org o un bucket; el respaldo ya trae
# el snapshot completo de este InfluxDB dedicado.
docker compose "${COMPOSE_FILES[@]}" exec -T influxdb \
  influx restore /tmp/hidro-restore \
  --full \
  --token "$INFLUX_TOKEN"
docker compose "${COMPOSE_FILES[@]}" exec -T influxdb rm -rf /tmp/hidro-restore

echo "Levantando el stack"
docker compose "${COMPOSE_FILES[@]}" up -d
docker compose "${COMPOSE_FILES[@]}" ps
echo "Restauración terminada. Revisa los logs y el dashboard antes de confiar en el sistema."
