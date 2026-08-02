#!/usr/bin/env bash
# path: scripts/dev.sh
#
# Start the backend and the Vite dev server together and stop both cleanly on Ctrl-C.
# Equivalent to `make dev`, for people who would rather not have make installed.

set -Eeuo pipefail
cd "$(dirname "$0")/.."

BACKEND_PORT="${BACKEND_PORT:-8000}"

if [[ ! -d backend/.venv ]]; then
  echo "Creating backend/.venv"
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install --quiet --upgrade pip
  backend/.venv/bin/pip install --quiet -r backend/requirements.txt
fi

if [[ ! -d frontend/node_modules ]]; then
  echo "Installing client dependencies"
  (cd frontend && npm install --no-audit --no-fund)
fi

if [[ ! -f assets/maps/alley.nav.json ]]; then
  echo "Generating map and nav graph"
  (cd backend && ../backend/.venv/bin/python -m app.scripts.gen_map)
  (cd backend && ../backend/.venv/bin/python -m app.scripts.gen_nav alley)
fi

# Kill the whole process group on exit so a stray uvicorn doesn't hold the port.
trap 'kill 0' EXIT INT TERM

echo "backend  → http://localhost:${BACKEND_PORT}"
echo "frontend → http://localhost:5173"

(cd backend && .venv/bin/python -m uvicorn app.main:app \
  --host 0.0.0.0 --port "${BACKEND_PORT}" --reload) &

(cd frontend && npm run dev) &

wait
