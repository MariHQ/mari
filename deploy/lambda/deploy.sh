#!/usr/bin/env bash
# Build, push and release cloud.mari.guru.
#
#   ./deploy/lambda/deploy.sh              # tag defaults to YYYYMMDD-lambda-N
#   ./deploy/lambda/deploy.sh 20260725-x   # explicit tag
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

STACK=mari-cloud-prod
REGION=us-east-1
ACCOUNT=386318010728
REPO="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/mari-cloud"
DOMAIN=cloud.mari.guru
ZONE=Z078478638DMYR1QGRLRH

TAG="${1:-$(date -u +%Y%m%d)-lambda-$(( $(aws ecr list-images --repository-name mari-cloud --region "$REGION" \
  --query 'length(imageIds)' --output text 2>/dev/null || echo 0) + 1 ))}"
IMAGE="$REPO:$TAG"

echo "==> Preflight"
aws sts get-caller-identity >/dev/null   # fails loudly if SSO has expired
[ -z "$(git status --porcelain)" ] || echo "    WARNING: working tree is dirty; you are shipping uncommitted code"

echo "==> Checks (typecheck + server-render smoke)"
( cd web && npm run check )
python3 -m py_compile server/*.py server/connectors/*.py

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
aws cloudformation deploy \
  --template-file deploy/lambda/template.yaml \
  --stack-name "$STACK" \
  --parameter-overrides "ImageUri=$IMAGE" "HostedZoneId=$ZONE" "DomainName=$DOMAIN" \
  --capabilities CAPABILITY_IAM \
  --region "$REGION"

echo "==> Verify"
for i in 1 2 3 4 5; do
  code=$(curl -sS -o /tmp/mari-healthz -w "%{http_code}" --max-time 120 "https://$DOMAIN/healthz" || true)
  echo "    /healthz -> $code"
  [ "$code" = "200" ] && { cat /tmp/mari-healthz; echo; break; }
  sleep 8
done

echo "==> Released $IMAGE"
echo "    Stack parameter and running function now agree; drift is what this avoids."
