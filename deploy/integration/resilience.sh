#!/usr/bin/env sh
set -eu

compose="docker compose -f docker-compose.yml -f deploy/integration/docker-compose.yml"

wait_status() {
  expected="$1"
  attempts="$2"
  count=0
  while [ "$count" -lt "$attempts" ]; do
    actual="$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/readyz || true)"
    if [ "$actual" = "$expected" ]; then
      return 0
    fi
    count=$((count + 1))
    sleep 1
  done
  echo "readyz did not reach HTTP $expected (last response: $actual)" >&2
  return 1
}

# Normal-load SLO at the public nginx boundary.
$compose exec -T \
  -e MARI_LOAD_DURATION_SECONDS="${MARI_LOAD_DURATION_SECONDS:-15}" \
  -e MARI_LOAD_CONCURRENCY="${MARI_LOAD_CONCURRENCY:-8}" \
  api python -m tests.integration_load

# Process restart: knowledge, sessions, and webhook control state remain in
# Postgres, and the public endpoint recovers.
$compose restart api
$compose up -d --wait --wait-timeout 180 api web
wait_status 200 30
$compose exec -T -e MARI_LOAD_DURATION_SECONDS=3 -e MARI_LOAD_CONCURRENCY=2 \
  api python -m tests.integration_load

# Ollama is an optional inference dependency. Knowledge reads and readiness
# remain available while it is down; model-backed actions report their own
# dependency failure instead of taking the product offline.
$compose stop ollama
wait_status 200 10
$compose exec -T -e MARI_LOAD_DURATION_SECONDS=3 -e MARI_LOAD_CONCURRENCY=2 \
  api python -m tests.integration_load
$compose start ollama

# PostgreSQL is required. Readiness must fail during the outage and recover
# after the same database comes back without recreating application state.
$compose stop db
wait_status 503 20
$compose start db
$compose up -d --wait --wait-timeout 180 db api web
wait_status 200 30
$compose exec -T -e MARI_LOAD_DURATION_SECONDS=3 -e MARI_LOAD_CONCURRENCY=2 \
  api python -m tests.integration_load
