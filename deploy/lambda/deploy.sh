#!/usr/bin/env bash
# Build, push and release cloud.mari.guru, or roll it back.
#
#   ./deploy/lambda/deploy.sh                          # tag defaults to YYYYMMDD-<short sha>
#   ./deploy/lambda/deploy.sh 20260725-x               # explicit tag for a new build; refused when the
#                                                      # tag already exists in ECR (MARI_ALLOW_TAG_OVERWRITE=1
#                                                      # replaces that image on purpose)
#   ./deploy/lambda/deploy.sh --rollback <image|tag>   # no build, no push: point the stack at an image
#                                                      # that is already in ECR, then verify
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
# A rollback is the same stack update and nothing else. Until 2026-09-02 the
# failure hint said to re-run this script with the previous tag, which
# REBUILT the current (broken) tree and pushed it under that tag. ECR tags are
# mutable, so the one known-good image was overwritten by the thing being
# rolled back from. `--rollback` never writes to the registry and refuses a
# target that is not already there; the same check stops an explicit tag from
# replacing an existing image by accident.
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

usage() {
  cat <<USAGE
usage: $0                          build, push and release (tag YYYYMMDD-<short sha>)
       $0 <tag>                    build, push and release under an explicit new tag
       $0 --rollback <image|tag>   stack update only, to an image already in ECR
USAGE
}

MODE=release
TARGET=""
case "${1:-}" in
  --rollback) MODE=rollback; TARGET="${2:-}" ;;
  -h|--help) usage; exit 0 ;;
  -*) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  *) TARGET="${1:-}" ;;
esac

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

REGISTRY="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
REPO="$REGISTRY/mari-cloud"
ECR_REPO_NAME="${REPO##*/}"

# Is this image in ECR? $1 = repository name, $2 = imageTag=... or
# imageDigest=... Returns 0 when present, 1 when absent, 2 when the question
# could not be answered (expired session, no permission); callers stop on 2
# rather than treating "could not check" as "not there".
mari_ecr_image_state() {
  local err
  if err=$(aws ecr describe-images --repository-name "$1" --region "$REGION" \
             --image-ids "$2" --output text 2>&1 >/dev/null); then
    return 0
  fi
  case "$err" in
    *ImageNotFoundException*) return 1 ;;
    *) echo "$err" >&2; return 2 ;;
  esac
}

# The image the stack points at right now ("" when there is no stack yet).
mari_stack_image() {
  aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
    --query "Stacks[0].Parameters[?ParameterKey=='ImageUri'].ParameterValue" --output text 2>/dev/null || true
}

mari_rollback_hint() {
  if [ -n "${1:-}" ]; then
    echo "    Roll back with (stack update only, nothing is built or pushed):" >&2
    echo "        $0 --rollback $1" >&2
  fi
}

# $1 = the image the stack now points at, $2 = the image to suggest rolling
# back to on failure (may be empty). Returns 1 when the site is not healthy.
mari_verify_release() {
  local image="$1" fallback="${2:-}" code="" auth_code i
  echo "==> Verify"
  for i in 1 2 3 4 5; do
    code=$(curl -sS -o /tmp/mari-healthz -w "%{http_code}" --max-time 120 "https://$DOMAIN/healthz" || true)
    echo "    /healthz -> $code"
    [ "$code" = "200" ] && { cat /tmp/mari-healthz; echo; break; }
    sleep 8
  done
  if [ "$code" != "200" ]; then
    echo "    /healthz never returned 200. The stack now points at $image and it is not healthy." >&2
    mari_rollback_hint "$fallback"
    return 1
  fi
  # /auth/me proves the API router and the database answer, not just the
  # process: with no cookie it returns 200 with a null user (it reads the
  # settings table to report needs_setup), so 200 is the only healthy answer.
  auth_code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 60 "https://$DOMAIN/auth/me" || true)
  echo "    /auth/me -> $auth_code"
  if [ "$auth_code" != "200" ]; then
    echo "    /auth/me did not answer like a running API. The stack now points at $image." >&2
    mari_rollback_hint "$fallback"
    return 1
  fi
}

# ————— rollback: stack update only —————
if [ "$MODE" = "rollback" ]; then
  CURRENT_IMAGE="$(mari_stack_image)"
  if [ -z "$TARGET" ]; then
    echo "    --rollback needs the image to roll back to: a bare tag or a full ECR image URI." >&2
    echo "    The stack currently points at: ${CURRENT_IMAGE:-(no stack found)}" >&2
    echo "    Most recent images in $REPO:" >&2
    aws ecr describe-images --repository-name "$ECR_REPO_NAME" --region "$REGION" \
      --query 'reverse(sort_by(imageDetails,&imagePushedAt))[:10].[imagePushedAt,imageTags[0]]' \
      --output text >&2 || true
    exit 2
  fi
  case "$TARGET" in
    */*) IMAGE="$TARGET" ;;
    *)   IMAGE="$REPO:$TARGET" ;;
  esac
  if [ "${IMAGE%%/*}" != "$REGISTRY" ]; then
    echo "    $IMAGE is not in this account's registry ($REGISTRY); the Lambda could not pull it." >&2
    exit 2
  fi
  ref="${IMAGE##*/}"   # mari-cloud:<tag> or mari-cloud@sha256:<digest>
  case "$ref" in
    *@sha256:*) repo_name="${ref%%@*}"; image_id="imageDigest=${ref#*@}" ;;
    *:*)        repo_name="${ref%%:*}"; image_id="imageTag=${ref#*:}" ;;
    *) echo "    $IMAGE names no tag or digest." >&2; exit 2 ;;
  esac
  state=0; mari_ecr_image_state "$repo_name" "$image_id" || state=$?
  case "$state" in
    0) ;;
    1) echo "    $IMAGE is not in ECR. A rollback only re-points the stack at an image that already exists; it never builds one." >&2; exit 1 ;;
    *) echo "    could not check ECR for $IMAGE (see above). Is the SSO session live?" >&2; exit 1 ;;
  esac
  if [ "$IMAGE" = "$CURRENT_IMAGE" ]; then
    echo "    the stack already points at $IMAGE; updating anyway so the function agrees with it."
  fi
  # The template comes from this tree, exactly as a release's does. Say so
  # when it carries uncommitted edits, since those would ship with the rollback.
  if [ -n "$(git status --porcelain -- deploy/lambda/template.yaml)" ]; then
    echo "    WARNING: deploy/lambda/template.yaml has uncommitted changes; this rollback applies them."
  fi

  echo "==> Roll back $STACK: ${CURRENT_IMAGE:-?} -> $IMAGE (stack update only; nothing is built or pushed)"
  # Only ImageUri is overridden: cloudformation deploy keeps every other
  # parameter's previous stack value when it is omitted here.
  aws cloudformation deploy \
    --template-file deploy/lambda/template.yaml \
    --stack-name "$STACK" \
    --parameter-overrides "ImageUri=$IMAGE" \
    --capabilities CAPABILITY_IAM \
    --region "$REGION"

  if ! mari_verify_release "$IMAGE" ""; then
    echo "    Pick an older image and try again: $0 --rollback <tag>   (run $0 --rollback with no tag to list them)" >&2
    exit 1
  fi
  echo "==> Rolled back to $IMAGE"
  echo "    Stack parameter and running function now agree; drift is what this avoids."
  exit 0
fi

# ————— release: build, push, stack update —————
# The hosted zone for the parent of $DOMAIN, so cloud.mari.guru looks up
# mari.guru. Set MARI_HOSTED_ZONE_ID when the zone is not the immediate parent.
ZONE="${MARI_HOSTED_ZONE_ID:-$(aws route53 list-hosted-zones-by-name --dns-name "${DOMAIN#*.}" \
  --query 'HostedZones[0].Id' --output text 2>/dev/null | sed 's|^/hostedzone/||')}"
case "$ZONE" in
  Z*) ;;
  *) echo "    could not resolve a hosted zone for ${DOMAIN#*.}. Set MARI_HOSTED_ZONE_ID." >&2; exit 1 ;;
esac

# Dirty-tree gate and the check suite are shared with deploy/publish-images.sh.
# shellcheck source=../preflight.sh
source deploy/preflight.sh
mari_require_clean_tree || exit 1

# Tag: YYYYMMDD-<short sha>, so the tag names the commit that was built and
# never depends on registry state. (It used to be the ECR image count plus
# one, so deleting any image made the next tag collide with an existing one.)
# A tree shipped with MARI_ALLOW_DIRTY=1 gets a -dirty suffix: the sha alone
# would claim a commit that does not contain what was built.
if [ -n "$TARGET" ]; then
  TAG="$TARGET"
else
  TAG="$(date -u +%Y%m%d)-$(git rev-parse --short=8 HEAD)"
  if [ -n "$(git status --porcelain)" ]; then
    TAG="$TAG-dirty"
  fi
fi
IMAGE="$REPO:$TAG"

# An explicit tag names a NEW build. ECR tags are mutable, so building under a
# tag that already exists replaces that image, and nothing keeps a copy of
# the old one. Someone typing an old tag here almost always means "run that
# image again", which is --rollback.
if [ -n "$TARGET" ]; then
  state=0; mari_ecr_image_state "$ECR_REPO_NAME" "imageTag=$TAG" || state=$?
  case "$state" in
    0)
      if [ "${MARI_ALLOW_TAG_OVERWRITE:-}" != "1" ]; then
        echo "    $IMAGE already exists in ECR. Building now would replace that image with the current tree." >&2
        echo "    To run the existing image:   $0 --rollback $IMAGE" >&2
        echo "    To build under a new tag:    $0            (or $0 <new-tag>)" >&2
        echo "    To replace it on purpose:    MARI_ALLOW_TAG_OVERWRITE=1 $0 $TAG" >&2
        exit 1
      fi
      echo "    WARNING: replacing the existing image $IMAGE (MARI_ALLOW_TAG_OVERWRITE=1)"
      ;;
    1) ;;
    *) echo "    could not check ECR for tag $TAG (see above). Is the SSO session live?" >&2; exit 1 ;;
  esac
fi

# The image we are about to replace, so a failed verify can say how to roll back.
PREVIOUS_IMAGE="$(mari_stack_image)"
if [ "$PREVIOUS_IMAGE" = "$IMAGE" ]; then
  PREVIOUS_IMAGE=""   # re-releasing the running tag: there is nothing older to point at
fi

mari_run_checks

echo "==> Build + push $IMAGE"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"
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

mari_verify_release "$IMAGE" "$PREVIOUS_IMAGE" || exit 1

echo "==> Released $IMAGE"
echo "    Stack parameter and running function now agree; drift is what this avoids."
