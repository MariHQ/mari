#!/usr/bin/env bash
set -euo pipefail

PGDATA="${MARI_PGDATA:-/tmp/mari/pgdata}"
PGSOCKET=/tmp/mari-pg
FIRST_START=0
export LD_PRELOAD=/usr/local/lib/lambda-prctl-shim.so

mkdir -p "$PGDATA" "$PGSOCKET"
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

# Demo model wiring: the dump ships settings.llm with the self-hosted default
# (provider ollama), and this image carries no ollama, so without intervention
# the dock answers only through the deterministic fallback. When the stack
# names a model, pin the settings row to it. Idempotent, runs every boot, and
# an empty MARI_LLM_DEFAULT leaves the row exactly as the dump restored it.
if [[ -n "${MARI_LLM_DEFAULT:-}" ]]; then
  LLM_PROVIDER="${MARI_LLM_DEFAULT%%:*}"
  LLM_MODEL="${MARI_LLM_DEFAULT#*:}"
  psql -h 127.0.0.1 -U postgres -d mari_cloud -q \
    -v prov="$LLM_PROVIDER" -v model="$LLM_MODEL" -v key="${MARI_LLM_KEY:-}" <<'SQL'
INSERT INTO settings (key, value)
VALUES ('llm', jsonb_build_object(
  'provider', :'prov'::text,
  'model',    :'model'::text,
  'keys',     jsonb_build_object(:'prov'::text, :'key'::text)))
ON CONFLICT (key) DO UPDATE
  SET value = settings.value || EXCLUDED.value;
SQL
fi

# Founders-only sign-in: MARI_SEED_ADMINS is a JSON array of
# {name, email, password}. Every listed account is upserted as an admin with
# that password, and every OTHER account's credentials are blanked, so the
# only people who can sign in are the ones the stack names. Runs every boot:
# the database is restored from the dump per execution environment, and this
# is what makes the accounts exist on all of them. Empty means the dump's own
# accounts stand untouched.
if [[ -n "${MARI_SEED_ADMINS:-}" ]]; then
  python3 - <<'PY'
import hashlib, json, os, secrets
import psycopg

def scrypt_hash(password):  # same format server/auth.py _hash writes
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + ":" + digest.hex()

admins = json.loads(os.environ["MARI_SEED_ADMINS"])
emails = [a["email"].lower() for a in admins]
with psycopg.connect(os.environ["MARI_DB"]) as conn:
    for a in admins:
        name, email, pw = a["name"], a["email"], a["password"]
        initials = "".join(w[0].upper() for w in name.split()[:2]) or "AD"
        row = conn.execute("SELECT id FROM users WHERE lower(email) = lower(%s)", (email,)).fetchone()
        if row:
            conn.execute("UPDATE users SET role = 'admin', provider = 'manual', password_hash = %s WHERE id = %s",
                         (scrypt_hash(pw), row[0]))
            continue
        # users.name is unique; a dump row already using this display name
        # must not block the account (or be taken over by it).
        if conn.execute("SELECT 1 FROM users WHERE name = %s", (name,)).fetchone():
            name = name + " (owner)"
        conn.execute("""INSERT INTO users (name, initials, tint, email, role, provider, password_hash)
                        VALUES (%s, %s, 1, %s, 'admin', 'manual', %s)""",
                     (name, initials, email, scrypt_hash(pw)))
    conn.execute("UPDATE users SET password_hash = '', github_id = '', google_id = '' WHERE lower(email) != ALL(%s)",
                 (emails,))
print(f"seeded {len(admins)} admin account(s); all other credentials blanked", flush=True)
PY
fi

shutdown() {
  if [[ -n "${API_PID:-}" ]]; then
    kill -TERM "$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
  fi
  pg_ctl -D "$PGDATA" -m fast -w stop 2>/dev/null || true
}
trap shutdown TERM INT EXIT

uvicorn mari_server.api.app:app --host 0.0.0.0 --port 8080 &
API_PID=$!
wait "$API_PID"
