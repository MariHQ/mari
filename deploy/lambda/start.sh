#!/usr/bin/env bash
set -euo pipefail

PGDATA="${MARI_PGDATA:-/tmp/mari/pgdata}"
PGSOCKET=/tmp/mari-pg
FIRST_START=0
export LD_PRELOAD=/usr/local/lib/lambda-prctl-shim.so

mkdir -p "$PGDATA" "$PGSOCKET" "${MARI_BUILDS_DIR:-/tmp/mari/builds}"
chmod 700 "$PGDATA"

if [[ ! -s "$PGDATA/PG_VERSION" ]]; then
  FIRST_START=1
  initdb -D "$PGDATA" --username=postgres --encoding=UTF8 --locale=C --auth-local=trust --auth-host=trust
fi

pg_ctl -D "$PGDATA" \
  -o "-h 127.0.0.1 -k $PGSOCKET -p 5432 -c listen_addresses=127.0.0.1" \
  -w start

if [[ "$FIRST_START" == "1" ]]; then
  createdb -h 127.0.0.1 -U postgres mari_cloud
  pg_restore -h 127.0.0.1 -U postgres -d mari_cloud --no-owner --no-privileges /app/mari_cloud.dump
fi

shutdown() {
  if [[ -n "${API_PID:-}" ]]; then
    kill -TERM "$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
  fi
  pg_ctl -D "$PGDATA" -m fast -w stop 2>/dev/null || true
}
trap shutdown TERM INT EXIT

uvicorn app:app --host 0.0.0.0 --port 8080 &
API_PID=$!
wait "$API_PID"
