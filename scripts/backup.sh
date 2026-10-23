#!/usr/bin/env bash
# Respalda /data (volumen hidro_data) y el bucket de InfluxDB.
#
# Uso: bash scripts/backup.sh [directorio-destino]
# Por defecto crea backups/<fecha>. No detiene hidro-core.
set -euo pipefail

# Git Bash convierte rutas de contenedor (/tmp, /data) a rutas de Windows.
export MSYS_NO_PATHCONV=1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "Falta .env; no se pueden leer los secretos de InfluxDB." >&2
  exit 1
fi

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
: "${INFLUX_BUCKET:?definir INFLUX_BUCKET en .env}"

if ! docker info >/dev/null 2>&1; then
  echo "docker no está disponible para este usuario (¿grupo docker?)." >&2
  exit 1
fi

STAMP="$(date +%Y%m%dT%H%M%S)"
DEST="${1:-$ROOT/backups/$STAMP}"
mkdir -p "$DEST"
DEST="$(cd "$DEST" && pwd)"
# Git Bash entrega rutas /c/... que Docker Desktop no entiende como bind.
HOST_DEST="$DEST"
if command -v cygpath >/dev/null 2>&1; then
  HOST_DEST="$(cygpath -m "$DEST")"
fi

if [[ -z "$(docker compose -f compose.yaml ps -q influxdb 2>/dev/null || true)" ]]; then
  echo "InfluxDB no está en marcha. Levanta el stack antes de respaldar." >&2
  exit 1
fi

DATA_VOLUME="${HIDRO_DATA_VOLUME:-}"
CORE_ID="$(docker compose -f compose.yaml ps -q hidro-core 2>/dev/null || true)"
if [[ -z "$DATA_VOLUME" && -n "$CORE_ID" ]]; then
  DATA_VOLUME="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' "$CORE_ID")"
fi
DATA_VOLUME="${DATA_VOLUME:-hidro_hidro_data}"
if ! docker volume inspect "$DATA_VOLUME" >/dev/null 2>&1; then
  echo "No se encontró el volumen de datos '$DATA_VOLUME'." >&2
  exit 1
fi

echo "Respaldando $DATA_VOLUME en $DEST/hidro-data.tgz"
docker run --rm \
  -v "$DATA_VOLUME:/data:ro" \
  -v "$HOST_DEST:/backup" \
  alpine:3.20 \
  sh -c "tar czf /backup/hidro-data.tgz -C /data . && chown $(id -u):$(id -g) /backup/hidro-data.tgz"

INFLUX_ID="$(docker compose -f compose.yaml ps -q influxdb)"
echo "Respaldando el bucket $INFLUX_BUCKET desde InfluxDB"
docker compose -f compose.yaml exec -T influxdb \
  influx backup /tmp/hidro-backup \
  --bucket "$INFLUX_BUCKET" \
  --org "$INFLUX_ORG" \
  --token "$INFLUX_TOKEN"
docker cp "$INFLUX_ID:/tmp/hidro-backup" "$HOST_DEST/influx"
docker compose -f compose.yaml exec -T influxdb rm -rf /tmp/hidro-backup
docker run --rm -v "$HOST_DEST:/backup" alpine:3.20 \
  chown -R "$(id -u):$(id -g)" /backup/influx

cp config/hidro.yaml "$DEST/hidro.yaml"
{
  echo "fecha=$(date -Iseconds)"
  echo "rama=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo desconocida)"
  echo "commit=$(git rev-parse --short HEAD 2>/dev/null || echo desconocido)"
  echo "influx_org=$INFLUX_ORG"
  echo "influx_bucket=$INFLUX_BUCKET"
  echo "volumen_datos=$DATA_VOLUME"
} > "$DEST/manifest.txt"

echo "Respaldo listo: $DEST"
du -sh "$DEST" 2>/dev/null || true
