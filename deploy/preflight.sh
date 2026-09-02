#!/usr/bin/env bash
# Shared release preflight, sourced by deploy/lambda/deploy.sh and
# deploy/publish-images.sh so the two release paths run the same gate.
#
#   source deploy/preflight.sh      # after cd to the repo root
#   mari_require_clean_tree || exit 1
#   mari_run_checks
#
# Environment:
#   MARI_ALLOW_DIRTY=1   ship uncommitted code anyway (prints a warning)
#   MARI_SKIP_TESTS=1    skip the server unit suite, for re-running a release
#                        whose tests already passed in this session

# Shipping uncommitted code makes an image tag unreproducible: the tag names
# a commit that does not contain what was built. Refuse unless the operator
# says so explicitly.
mari_require_clean_tree() {
  if [ -z "$(git status --porcelain)" ]; then
    return 0
  fi
  if [ "${MARI_ALLOW_DIRTY:-}" = "1" ]; then
    echo "    WARNING: working tree is dirty (MARI_ALLOW_DIRTY=1); shipping uncommitted code"
    return 0
  fi
  echo "    working tree is dirty. Commit or stash first, or set MARI_ALLOW_DIRTY=1 to ship anyway." >&2
  git status --short >&2
  return 1
}

# The same gate for every image build: web typecheck + server-render smoke,
# a bytecode compile of the Python tree, and the server unit suite. There is
# no CI on this repo, so a release is the last place these run.
mari_run_checks() {
  echo "==> Checks (typecheck + server-render smoke + server unit tests)"
  ( cd web && npm run check )
  python3 -m compileall -q server/mari_server mari-components/packages
  if [ "${MARI_SKIP_TESTS:-}" = "1" ]; then
    echo "    skipping server unit tests (MARI_SKIP_TESTS=1)"
  else
    make test-server
  fi
}
