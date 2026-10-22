#!/usr/bin/env bash
# Despliegue PC -> Raspberry Pi por SSH.
#
# Requisitos en la Pi: docker, docker compose, git, python3 y acceso al
# repositorio. El host, la ruta y los secretos se leen de .env (no versionado).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "Falta .env. Copia .env.example y completa los valores." >&2
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

: "${DEPLOY_HOST:?definir DEPLOY_HOST en .env}"
: "${DEPLOY_PATH:?definir DEPLOY_PATH en .env}"

# Las alertas de Grafana necesitan el bot de Telegram. En las primeras fases de
# hardware se puede desplegar sin ellas; en producción conviene exigirlas.
ALERTS=1
if [[ -z "${TELEGRAM_BOT_TOKEN:-}" || -z "${TELEGRAM_CHAT_ID:-}" ]]; then
  if [[ "${DEPLOY_REQUIRE_ALERTS:-0}" == "1" ]]; then
    echo "Faltan TELEGRAM_BOT_TOKEN y/o TELEGRAM_CHAT_ID y DEPLOY_REQUIRE_ALERTS=1." >&2
    exit 1
  fi
  echo "Aviso: sin Telegram; se despliega sin las alertas de Grafana." >&2
  ALERTS=0
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
REMOTE_URL="$(git remote get-url origin)"

if [[ "${DEPLOY_ALLOW_DIRTY:-0}" != "1" && -n "$(git status --porcelain)" ]]; then
  echo "Hay cambios sin commitear. Commitéalos o usa DEPLOY_ALLOW_DIRTY=1." >&2
  exit 1
fi

if [[ "${DEPLOY_SKIP_PUSH:-0}" != "1" ]]; then
  git push -u origin "$BRANCH"
fi

echo "Desplegando $BRANCH en $DEPLOY_HOST:$DEPLOY_PATH"

ssh "$DEPLOY_HOST" \
  "DEPLOY_PATH='$DEPLOY_PATH' BRANCH='$BRANCH' REMOTE_URL='$REMOTE_URL' ALERTS='$ALERTS' bash -s" <<'REMOTE_SCRIPT'
set -euo pipefail

DEPLOY_PATH="${DEPLOY_PATH/#\~/$HOME}"

if ! docker info >/dev/null 2>&1; then
  echo "docker no está disponible para este usuario (¿grupo docker?)." >&2
  exit 1
fi

mkdir -p "$DEPLOY_PATH"
if [[ ! -d "$DEPLOY_PATH/.git" ]]; then
  git clone "$REMOTE_URL" "$DEPLOY_PATH"
fi

cd "$DEPLOY_PATH"
git fetch origin
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

if [[ ! -f .env ]]; then
  echo "Falta .env en $DEPLOY_PATH. Cópialo desde el PC antes de desplegar." >&2
  exit 1
fi

COMPOSE_FILES=(-f compose.yaml -f compose.hardware.yaml)
if [[ "${ALERTS:-1}" == "1" ]]; then
  python3 scripts/render_grafana_alerting.py
  COMPOSE_FILES+=(-f compose.alerts.yaml)
fi

docker compose "${COMPOSE_FILES[@]}" up -d --build
docker compose "${COMPOSE_FILES[@]}" ps
REMOTE_SCRIPT
