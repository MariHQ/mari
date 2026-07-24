#!/usr/bin/env bash
# Local development stack.
#
#   ./dev.sh
#
# Postgres runs in Docker (pgvector is the one dependency that is genuinely
# awkward to install natively). The API and the web app run on the host, which
# is the point: from inside a container "localhost" is the container, so a
# containerised API cannot reach an Ollama running on your machine. Native API
# + host Ollama at http://localhost:11434 needs no configuration at all.
#
#   API   http://localhost:8000
#   Web   http://localhost:5173   <- open this one
#
# `docker compose up` remains the one-command production-shaped path; it just
# expects Ollama at host.docker.internal instead.
set -euo pipefail
cd "$(dirname "$0")"

DB_URL="postgresql://mari:mari@localhost:5432/mari_cloud"

echo "==> Postgres (docker)"
docker compose up -d db >/dev/null
until docker compose exec -T db pg_isready -U mari -d mari_cloud >/dev/null 2>&1; do sleep 1; done

echo "==> Schema (idempotent)"
docker compose exec -T db psql -U mari -d mari_cloud -v ON_ERROR_STOP=1 -q -f - < server/init.sql

if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "==> Ollama detected on the host"
else
  echo "==> Ollama not reachable at :11434 — LLM features degrade, everything else works"
fi

# Both children die with this script.
trap 'kill 0' EXIT INT TERM

echo "==> API  (uvicorn --reload)"
( cd server && MARI_DB="$DB_URL" ./.venv/bin/python -m uvicorn app:app --reload --port 8000 ) &

echo "==> Web  (vite)"
( cd web && npm run dev ) &

echo
echo "    Web  http://localhost:5173"
echo "    API  http://localhost:8000"
echo "    Ctrl-C stops both."
wait
