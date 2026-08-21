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
export MARI_E2E_LIVE=1
export MARI_E2E_EXTERNAL_SERVER=1

cd "$root/web"
npx playwright test --project=live-chromium --reporter=line
