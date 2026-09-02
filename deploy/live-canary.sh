#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${MARI_LIVE_ENV_FILE:-$root/.env.live}"
if [[ ! -f "$env_file" ]]; then
  echo "Missing $env_file. Copy deploy/live.env.example to .env.live and fill sandbox credentials." >&2
  exit 2
fi

set -a
# This file is local, gitignored, and controlled by the operator.
source "$env_file"
set +a

: "${MARI_E2E_BASE_URL:?Set MARI_E2E_BASE_URL in $env_file}"

# With mutations on, the live suite creates sources, installs a Slack bot,
# posts and deletes Slack messages and runs a fact scan. Never against
# production. This is a hard stop with no override: point the suite at a
# sandbox or run it read-only.
# Order matters: userinfo comes off before the port, or a URL like
# https://user:pw@cloud.mari.guru/ parses to host "user" and the guard
# below never fires. Lowercase so CLOUD.mari.guru is still production.
host="${MARI_E2E_BASE_URL#*://}"
host="${host%%/*}"
host="${host##*@}"
host="${host%%:*}"
host="$(printf '%s' "$host" | tr '[:upper:]' '[:lower:]')"
case "$host" in
  cloud.mari.guru|*.cloud.mari.guru)
    if [[ "${MARI_E2E_MUTATIONS:-0}" == "1" ]]; then
      echo "Refusing to run: MARI_E2E_MUTATIONS=1 against $host (production)." >&2
      echo "Set MARI_E2E_MUTATIONS=0 for read-only checks, or point MARI_E2E_BASE_URL at a sandbox." >&2
      exit 1
    fi
    echo "Read-only live suite against $host (mutations off)."
    ;;
esac

export MARI_E2E_LIVE=1
export MARI_E2E_EXTERNAL_SERVER=1

cd "$root/web"
npx playwright test --project=live-chromium --reporter=line
