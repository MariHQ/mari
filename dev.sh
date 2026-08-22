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

# The db container's published port, discovered rather than assumed. Compose
# will happily report 5432 while something else on the host already holds it,
# in which case connecting there reaches the wrong Postgres and the API dies on
# `role "mari" does not exist`. Probe every published port and take the one that
# actually answers as our role.
db_port() {
  local candidates p
  candidates=$(docker compose port db 5432 2>/dev/null | awk -F: '{print $NF}')
  candidates="$candidates $(docker port "$(docker compose ps -q db 2>/dev/null)" 5432 2>/dev/null | awk -F: '{print $NF}')"
  for p in $(printf '%s\n' $candidates | sort -un); do
    if PGPASSWORD=mari psql -h 127.0.0.1 -p "$p" -U mari -d mari_cloud -tAc 'select 1' >/dev/null 2>&1; then
      printf '%s' "$p"; return 0
    fi
  done
  return 1
}

DB_URL="postgresql://mari:mari@localhost:5432/mari_cloud"

# .env is read by docker compose, and until now by nothing else, so anything
# configured there was invisible to this script: Publish, for one, would build
# locally and report no bucket however carefully MARI_S3_BUCKET was set. Load it
# here too, without overriding a variable already exported in this shell.
if [ -f .env ]; then
  while IFS= read -r line; do
    case "$line" in ''|'#'*) continue;; *=*) ;; *) continue;; esac
    key=${line%%=*}
    [ -n "${!key-}" ] || export "$line"
  done < .env
fi

echo "==> Postgres (docker)"
docker compose up -d db >/dev/null
until docker compose exec -T db pg_isready -U mari -d mari_cloud >/dev/null 2>&1; do sleep 1; done

DB_PORT=$(db_port) || {
  echo "!! the db container is up but no published port answers as role 'mari'." >&2
  echo "   Published: $(docker port "$(docker compose ps -q db)" 5432 2>/dev/null | tr '\n' ' ')" >&2
  echo "   Something else is probably holding 5432. Free it, or publish another port." >&2
  exit 1
}
DB_URL="postgresql://mari:mari@localhost:${DB_PORT}/mari_cloud"
[ "$DB_PORT" = "5432" ] || echo "==> Postgres on :$DB_PORT (5432 is taken by something else)"

# The baseline schema only belongs on an empty database. Once the migration
# ledger exists the API applies server/migrations/ itself at startup, and
# re-running init.sql would trip on constraints later migrations replaced.
if docker compose exec -T db psql -U mari -d mari_cloud -tAc "select to_regclass('schema_migrations')" 2>/dev/null | grep -q schema_migrations; then
  echo "==> Schema: migration ledger present, the API applies migrations at startup"
else
  echo "==> Schema (baseline)"
  docker compose exec -T db psql -U mari -d mari_cloud -v ON_ERROR_STOP=1 -q -f - < server/init.sql
fi

if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "==> Ollama detected on the host"
else
  echo "==> Ollama not reachable at :11434 — LLM features degrade, everything else works"
fi

# Both children die with this script.
trap 'kill 0' EXIT INT TERM

echo "==> API  (uvicorn --reload)"
( cd server && MARI_DB="$DB_URL" ./.venv/bin/python -m uvicorn mari_server.app:app --reload --port 8000 ) &

echo "==> Web  (vite)"
( cd web && npm run dev ) &

echo
echo "    Web  http://localhost:5173"
echo "    API  http://localhost:8000"
echo "    Ctrl-C stops both."
wait
