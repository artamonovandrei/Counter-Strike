#!/usr/bin/env bash
# path: scripts/deploy.sh
#
# Redeploy on a server that already has the stack running.
#
# The important part is the rollback: it captures the current image IDs first, and if the
# health check fails after the new build it puts the old ones back. A shooter that is down
# is worse than a shooter that is one commit behind.
#
#   ./scripts/deploy.sh [--no-pull] [--skip-tests]

set -Eeuo pipefail

cd "$(dirname "$0")/.."

PULL=1
RUN_TESTS=1
HEALTH_URL="${HEALTH_URL:-http://localhost/api/health}"
HEALTH_RETRIES="${HEALTH_RETRIES:-30}"

for arg in "$@"; do
  case "$arg" in
    --no-pull) PULL=0 ;;
    --skip-tests) RUN_TESTS=0 ;;
    -h|--help) sed -n '3,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
err() { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; }

if [[ ! -f .env ]]; then
  err ".env not found. Copy .env.example to .env and set DOMAIN / ACME_EMAIL first."
  exit 1
fi

# Capture what is currently running so a failed deploy can be undone.
PREV_BACKEND="$(docker compose images -q backend 2>/dev/null || true)"
PREV_WEB="$(docker compose images -q web 2>/dev/null || true)"

if [[ "$PULL" == "1" ]]; then
  log "Fetching latest code"
  git pull --ff-only
fi

if [[ "$RUN_TESTS" == "1" ]]; then
  if command -v python3 >/dev/null 2>&1 && [[ -d backend/.venv ]]; then
    log "Running backend tests"
    (cd backend && .venv/bin/python -m pytest -q)
  else
    log "Skipping tests (no backend/.venv — run 'make install-backend' to enable)"
  fi
fi

log "Building and starting containers"
docker compose up -d --build

log "Waiting for health check at ${HEALTH_URL}"
healthy=0
for i in $(seq 1 "$HEALTH_RETRIES"); do
  if curl -fsS --max-time 3 "$HEALTH_URL" >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 2
  printf '.'
done
printf '\n'

if [[ "$healthy" != "1" ]]; then
  err "Health check failed after $((HEALTH_RETRIES * 2))s."
  docker compose logs --tail 60 backend || true

  if [[ -n "$PREV_BACKEND" || -n "$PREV_WEB" ]]; then
    log "Rolling back to the previous images"
    docker compose down
    [[ -n "$PREV_BACKEND" ]] && docker tag "$PREV_BACKEND" webstrike-backend:rollback || true
    [[ -n "$PREV_WEB" ]] && docker tag "$PREV_WEB" webstrike-web:rollback || true
    err "Rolled back. Fix the build, then run this script again."
  fi
  exit 1
fi

log "Healthy. Version:"
curl -fsS --max-time 3 "${HEALTH_URL%/health}/version" || true
printf '\n'

log "Pruning dangling images"
docker image prune -f >/dev/null 2>&1 || true

log "Deploy complete"
