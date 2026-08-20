#!/usr/bin/env sh
set -eu

compose="docker compose -f docker-compose.yml -f deploy/integration/docker-compose.yml"
restore_db="mari_restore_ci"
archive="$(mktemp "${TMPDIR:-/tmp}/mari-restore-drill.XXXXXX.dump")"

cleanup() {
  $compose exec -T db dropdb -U mari --if-exists "$restore_db" >/dev/null 2>&1 || true
  $compose run --rm --no-deps --entrypoint /bin/sh minio-init -c '
    mc alias set local http://minio:9000 mari-ci mari-ci-secret >/dev/null &&
    mc rb --force local/mari-restore-ci >/dev/null 2>&1 || true
  ' >/dev/null 2>&1 || true
  rm -f "$archive"
}
trap cleanup EXIT INT TERM

# Capture a deterministic application-level signature rather than trusting a
# successful pg_restore exit code. The restored database must contain both the
# migration ledger and representative tenant records.
signature_sql="SELECT concat_ws('|',
  (SELECT count(*) FROM schema_migrations),
  (SELECT count(*) FROM projects),
  (SELECT count(*) FROM project_members),
  (SELECT count(*) FROM sources),
  (SELECT count(*) FROM documents),
  (SELECT count(*) FROM facts),
  (SELECT count(*) FROM tasks),
  (SELECT count(*) FROM events),
  (SELECT count(*) FROM sessions),
  (SELECT count(*) FROM webhook_events),
  (SELECT count(*) FROM iceberg_tables));"

source_signature="$($compose exec -T db \
  psql -U mari -d mari_cloud -Atqc "$signature_sql" | tr -d '\r')"
$compose exec -T db pg_dump -U mari -d mari_cloud \
  --format=custom --no-owner --no-privileges > "$archive"
test -s "$archive"

$compose exec -T db dropdb -U mari --if-exists "$restore_db"
$compose exec -T db createdb -U mari "$restore_db"
$compose exec -T db pg_restore -U mari -d "$restore_db" \
  --no-owner --no-privileges < "$archive"

restored_signature="$($compose exec -T db \
  psql -U mari -d "$restore_db" -Atqc "$signature_sql" | tr -d '\r')"
if [ "$source_signature" != "$restored_signature" ]; then
  echo "Postgres restore signature mismatch: $source_signature != $restored_signature" >&2
  exit 1
fi

# A restored database must accept the current migrator as a no-op. This catches
# missing/corrupt migration ledgers and makes rollback rehearsals realistic.
$compose exec -T \
  -e MARI_DB="postgresql://mari:mari@db:5432/$restore_db" \
  api python -c 'import schema_migrations; assert schema_migrations.migrate() == []'

# The integration object store has versioning enabled. Mirror all generated
# vector generations into a separate restore bucket and compare object counts;
# a missing manifest or generation file makes this check fail.
$compose run --rm --no-deps --entrypoint /bin/sh minio-init -c '
set -eu
mc alias set local http://minio:9000 mari-ci mari-ci-secret >/dev/null
# Idempotent and fails closed if the backing store cannot provide versioning.
mc version enable local/mari-ci >/dev/null
mc mb --ignore-existing local/mari-restore-ci >/dev/null
mc mirror --overwrite local/mari-ci/vectors local/mari-restore-ci/vectors >/dev/null
manifest=$(mc find local/mari-restore-ci/vectors --name current.json)
test -n "$manifest"
differences=$(mc diff local/mari-ci/vectors local/mari-restore-ci/vectors)
test -z "$differences"
'

echo "Restore drill passed (Postgres signature $source_signature; object snapshots verified)."
