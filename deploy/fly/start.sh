#!/bin/sh
set -eu

PGDATA=${PGDATA:-/data/postgres}
BACKUP_DIR=/data/backups
api_pid=""
backup_pid=""
postgres_pid=""
mkdir -p "$PGDATA" "$BACKUP_DIR" /data/mari /data/cache \
  /data/models/huggingface /data/models/sentence-transformers
chown -R postgres:postgres "$PGDATA"
chown -R postgres:postgres "$BACKUP_DIR"
chown -R mari:mari /data/mari /data/cache /data/models /app/server/builds

if [ ! -s "$PGDATA/PG_VERSION" ]; then
  gosu postgres initdb -D "$PGDATA" --auth-local=trust --auth-host=trust --username=mari
fi

gosu postgres postgres -D "$PGDATA" -c listen_addresses=127.0.0.1 -p 5432 &
postgres_pid=$!

cleanup() {
  for pid in "$api_pid" "$backup_pid" "$postgres_pid"; do
    [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null || true
  done
  for pid in "$api_pid" "$backup_pid" "$postgres_pid"; do
    [ -n "$pid" ] && wait "$pid" 2>/dev/null || true
  done
}
trap cleanup INT TERM EXIT

until gosu postgres pg_isready -h 127.0.0.1 -p 5432 -U mari -d postgres >/dev/null 2>&1; do
  kill -0 "$postgres_pid" 2>/dev/null || exit 1
  sleep 1
done

if ! gosu postgres psql -h 127.0.0.1 -U mari -d postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname='mari_cloud'" | grep -q 1; then
  gosu postgres createdb -h 127.0.0.1 -U mari mari_cloud
fi

gosu mari python3 -m schema_migrations

backup_loop() {
  while :; do
    sleep "${MARI_BACKUP_INTERVAL_SECONDS:-86400}"
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    file="$BACKUP_DIR/mari-$stamp.dump"
    if gosu postgres pg_dump -h 127.0.0.1 -U mari -Fc mari_cloud -f "$file"; then
      if [ -n "${MARI_S3_BUCKET:-}" ]; then
        if [ -n "${AWS_REGION:-}" ]; then
          aws s3 cp "$file" "s3://${MARI_S3_BUCKET}/backups/postgres/$(basename "$file")" \
            --region "$AWS_REGION" || true
        else
          aws s3 cp "$file" "s3://${MARI_S3_BUCKET}/backups/postgres/$(basename "$file")" || true
        fi
      fi
      find "$BACKUP_DIR" -type f -name 'mari-*.dump' -mtime +3 -delete
    fi
  done
}
backup_loop &
backup_pid=$!

gosu mari uvicorn mari_server.api.app:app --host 0.0.0.0 --port 8080 &
api_pid=$!
wait "$api_pid"
