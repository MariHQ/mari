#!/usr/bin/env bash
# Build, push and release cloud.mari.guru.
#
#   ./deploy/lambda/deploy.sh              # tag defaults to YYYYMMDD-<short sha>
#   ./deploy/lambda/deploy.sh 20260725-x   # explicit tag (also how you roll back)
#
# The whole deployment is one CloudFormation stack, `mari-cloud-prod`: the
# Lambda, the HTTP API, the ACM certificate, the custom domain and the Route53
# record. The image tag is a stack PARAMETER.
#
# That last point is the whole reason this script exists. Releasing with
# `aws lambda update-function-code` works — the site picks the new image up
# immediately — and it silently desynchronises the function from the stack that
# owns it, because the `ImageUri` parameter still names the old tag. The next
# legitimate `aws cloudformation deploy`, by anyone, for any reason, then
# reverts production to whatever image the parameter still points at. Always
# release by updating the stack.
#
# (There is also a dead `mari-cloud` stack in some accounts, left in
# ROLLBACK_COMPLETE by the first create attempt in July 2026, which failed
# because the template then set ReservedConcurrentExecutions and the account's
# total concurrency limit is 10 — any reservation drops unreserved below the
# minimum of 10. The property is gone; the shell held nothing and was deleted.)
set -euo pipefail
cd "$(dirname "$0")/../.."

STACK="${MARI_STACK:-mari-cloud-prod}"
REGION="${MARI_REGION:-us-east-1}"
DOMAIN="${MARI_DOMAIN:-cloud.mari.guru}"

echo "==> Preflight"
# This repo is public, so the account id and hosted zone are resolved at run
# time rather than committed. Both can be overridden by environment variable
# when deploying somewhere else.
#
# Resolving the account also serves as the SSO check the preflight used to do
# on its own: it fails loudly when the session has expired.
ACCOUNT="${MARI_AWS_ACCOUNT:-$(aws sts get-caller-identity --query Account --output text)}"
case "$ACCOUNT" in
  [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) ;;
  *) echo "    could not resolve the AWS account id (got '$ACCOUNT'). Is the SSO session live?" >&2; exit 1 ;;
esac

# The hosted zone for the parent of $DOMAIN, so cloud.mari.guru looks up
# mari.guru. Set MARI_HOSTED_ZONE_ID when the zone is not the immediate parent.
ZONE="${MARI_HOSTED_ZONE_ID:-$(aws route53 list-hosted-zones-by-name --dns-name "${DOMAIN#*.}" \
  --query 'HostedZones[0].Id' --output text 2>/dev/null | sed 's|^/hostedzone/||')}"
case "$ZONE" in
  Z*) ;;
  *) echo "    could not resolve a hosted zone for ${DOMAIN#*.}. Set MARI_HOSTED_ZONE_ID." >&2; exit 1 ;;
esac

REPO="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/mari-cloud"

# Dirty-tree gate and the check suite are shared with deploy/publish-images.sh.
# shellcheck source=../preflight.sh
source deploy/preflight.sh
mari_require_clean_tree || exit 1

# Tag: YYYYMMDD-<short sha>, so the tag names the commit that was built and
# never depends on registry state. (It used to be the ECR image count plus
# one, so deleting any image made the next tag collide with an existing one.)
# A tree shipped with MARI_ALLOW_DIRTY=1 gets a -dirty suffix: the sha alone
# would claim a commit that does not contain what was built.
if [ -n "${1:-}" ]; then
  TAG="$1"
else
  TAG="$(date -u +%Y%m%d)-$(git rev-parse --short=8 HEAD)"
  if [ -n "$(git status --porcelain)" ]; then
    TAG="$TAG-dirty"
  fi
fi
IMAGE="$REPO:$TAG"

# The tag we are about to replace, so a failed verify can say how to roll back.
PREVIOUS_IMAGE="$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Parameters[?ParameterKey=='ImageUri'].ParameterValue" --output text 2>/dev/null || true)"

mari_run_checks

echo "==> Build + push $IMAGE"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
# --provenance/--sbom off and oci-mediatypes=false: buildx otherwise emits an
# OCI manifest LIST with attestations, and Lambda rejects it with "image
# manifest, config or layer media type ... is not supported". Lambda wants a
# single-platform Docker v2 manifest.
docker buildx build \
  --platform linux/arm64 \
  --provenance=false --sbom=false \
  -f deploy/lambda/Dockerfile \
  --output "type=image,name=$IMAGE,push=true,oci-mediatypes=false" \
  .

echo "==> Release (stack update, NOT update-function-code)"
# Demo model wiring: pass LlmDefault/LlmKey only when set in the environment.
# cloudformation deploy keeps a parameter's previous stack value when it is
# omitted from --parameter-overrides, so a release without these env vars
# leaves the running model configuration untouched.
EXTRA_PARAMS=()
[ -n "${MARI_LLM_DEFAULT:-}" ] && EXTRA_PARAMS+=("LlmDefault=$MARI_LLM_DEFAULT")
[ -n "${MARI_LLM_KEY:-}" ] && EXTRA_PARAMS+=("LlmKey=$MARI_LLM_KEY")
[ -n "${MARI_SESSION_SECRET:-}" ] && EXTRA_PARAMS+=("SessionSecret=$MARI_SESSION_SECRET")
[ -n "${MARI_SEED_ADMINS:-}" ] && EXTRA_PARAMS+=("SeedAdmins=$MARI_SEED_ADMINS")
aws cloudformation deploy \
  --template-file deploy/lambda/template.yaml \
  --stack-name "$STACK" \
  --parameter-overrides "ImageUri=$IMAGE" "HostedZoneId=$ZONE" "DomainName=$DOMAIN" \
    ${EXTRA_PARAMS[@]+"${EXTRA_PARAMS[@]}"} \
  --capabilities CAPABILITY_IAM \
  --region "$REGION"

echo "==> Verify"
code=""
for i in 1 2 3 4 5; do
  code=$(curl -sS -o /tmp/mari-healthz -w "%{http_code}" --max-time 120 "https://$DOMAIN/healthz" || true)
  echo "    /healthz -> $code"
  [ "$code" = "200" ] && { cat /tmp/mari-healthz; echo; break; }
  sleep 8
done
if [ "$code" != "200" ]; then
  echo "    /healthz never returned 200. The stack now points at $IMAGE and it is not healthy." >&2
  if [ -n "$PREVIOUS_IMAGE" ]; then
    echo "    Roll back with: MARI_SKIP_TESTS=1 $0 ${PREVIOUS_IMAGE##*:}" >&2
  fi
  exit 1
fi
# /auth/me proves the API router and the database answer, not just the
# process: 401 (no cookie) is the healthy answer, anything else is not.
auth_code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 60 "https://$DOMAIN/auth/me" || true)
echo "    /auth/me -> $auth_code"
if [ "$auth_code" != "401" ] && [ "$auth_code" != "200" ]; then
  echo "    /auth/me did not answer like a running API. Roll back as above." >&2
  exit 1
fi

echo "==> Released $IMAGE"
echo "    Stack parameter and running function now agree; drift is what this avoids."
