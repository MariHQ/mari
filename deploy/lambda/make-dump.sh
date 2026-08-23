#!/usr/bin/env bash
# Build the Postgres snapshot that ships inside the Lambda image.
#
#   ./deploy/lambda/make-dump.sh
#
# The image carries a pg_dump archive; start.sh restores it into /tmp the first
# time a cold container boots, so a fresh Lambda comes up with the workspace
# already populated instead of an empty schema.
#
# The snapshot is taken from the LOCAL development database — the one `dev.sh`
# runs — because that is where the corpus actually is: ingested documents,
# their embeddings, the flows, the digest. The dump is deliberately gitignored
# (18MB of binary that changes on every ingest), which is exactly why this
# script exists: the sanitising below must not be a thing somebody remembered
# to do by hand once.
#
# WHAT IS REMOVED, AND WHY
#
# The resulting image is deployed to a public address, so anything in it is
# public. Three things therefore do not travel:
#
#   sessions      A session token is a bearer credential — whoever holds one
#                 IS that user. Shipping live ones hands out accounts.
#   magic_links   Same, with a shorter fuse.
#   password_hash Protects a real person's account, and people reuse
#                 passwords. The demo is entered through the sign-in bypass
#                 (MARI_AUTH_BYPASS), so the account keeps its identity and
#                 loses only the credential.
#
# Provider API keys are cleared too. They are normally empty in development,
# and "normally" is not a good enough reason to publish one.
#
# Everything else — documents, chunks, embeddings, answers, facts, flows,
# tags, the digest — is the point of the snapshot and is kept as-is.
set -euo pipefail
cd "$(dirname "$0")/../.."

OUT=deploy/lambda/mari_cloud.dump
SRC=mari_cloud
WORK=mari_pub   # scratch copy; the scrub must never touch the real database

psql_() { docker compose exec -T db psql -U mari -X -q "$@"; }

echo "==> snapshot $SRC"
docker compose exec -T db pg_dump -U mari -d "$SRC" -Fc --no-owner --no-privileges > /tmp/mari-src.dump

echo "==> restore into scratch database $WORK"
psql_ -d postgres -c "DROP DATABASE IF EXISTS $WORK;" -c "CREATE DATABASE $WORK;"
docker compose exec -T db pg_restore -U mari -d "$WORK" --no-owner --no-privileges < /tmp/mari-src.dump

echo "==> remove credential material"
psql_ -d "$WORK" -v ON_ERROR_STOP=1 <<'SQL'
TRUNCATE sessions;
-- magic_links is created lazily (auth.py, on the first mint), so a workspace
-- that never sent one has no table. DROP IF EXISTS covers both cases and is
-- the stronger scrub for a public image; the server recreates it on demand.
DROP TABLE IF EXISTS magic_links;
UPDATE users SET password_hash = '';
UPDATE settings
   SET value = jsonb_set(value, '{keys}', '{"openai": "", "anthropic": ""}'::jsonb)
 WHERE key = 'llm' AND value ? 'keys';
-- Per-account preferences are somebody's settings, not default data.
DELETE FROM settings WHERE key LIKE 'user_prefs:%';
-- Connector credentials live in sources.config since the connector move;
-- strip every secret-bearing key and leave the rest of the config (cursor,
-- checkpoint, repo, site) so the sources still describe themselves.
UPDATE sources
   SET config = config - 'api_token' - 'token' - 'bot_token' - 'user_token'
                       - 'webhook_secret' - 'signing_secret' - 'access_token'
                       - 'refresh_token' - 'client_secret' - 'password'
 WHERE config IS NOT NULL;
-- Bot settings carry signing material; keep the row, drop the secrets.
UPDATE settings
   SET value = value - 'webhook_secret' - 'bot_token' - 'signing_secret' - 'app_token'
 WHERE key IN ('github_bot', 'slack_bot') AND jsonb_typeof(value) = 'object';
-- API keys are bearer credentials for the REST surface; none should travel.
TRUNCATE api_keys;
SQL

echo "==> verify the scrub actually happened"
psql_ -d "$WORK" -v ON_ERROR_STOP=1 <<'SQL'
DO $$
DECLARE bad int;
BEGIN
  SELECT (SELECT count(*) FROM sessions)
       -- the scrub DROPs magic_links; any surviving table is a failure
       + (SELECT count(*) FROM pg_tables
           WHERE schemaname = 'public' AND tablename = 'magic_links')
       + (SELECT count(*) FROM users WHERE password_hash <> '')
       + (SELECT count(*) FROM sources
           WHERE config ?| array['api_token','token','bot_token','user_token','webhook_secret',
                                 'signing_secret','access_token','refresh_token','client_secret','password'])
       + (SELECT count(*) FROM settings
           WHERE key IN ('github_bot','slack_bot')
             AND value ?| array['webhook_secret','bot_token','signing_secret','app_token'])
       + (SELECT count(*) FROM api_keys)
    INTO bad;
  IF bad <> 0 THEN
    RAISE EXCEPTION 'Refusing to ship: % row(s) of credential material remain', bad;
  END IF;
END $$;
SQL

echo "==> write $OUT"
docker compose exec -T db pg_dump -U mari -d "$WORK" -Fc --no-owner --no-privileges > "$OUT"
psql_ -d postgres -c "DROP DATABASE $WORK;"
rm -f /tmp/mari-src.dump

echo
ls -lh "$OUT"
echo "Now build and push the image, then point the stack at the new tag (see deploy/lambda/README.md)."
