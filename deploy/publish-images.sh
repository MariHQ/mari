#!/usr/bin/env bash
# Build and push the customer container images to Amazon ECR Public.
#
#   ./deploy/publish-images.sh v0.1.1
#
# The GitHub workflow (.github/workflows/container-release.yml) publishes the
# same images to ghcr.io/marihq with GITHUB_TOKEN, but it cannot reach ECR
# Public, which is the registry the Helm chart actually pulls from. v0.1.0
# got there by an ad hoc push nobody wrote down. This script is that push,
# written down.
#
# Run it from any directory. It builds from the repo root because both
# Dockerfiles expect the root as build context, and the web image compiles
# against the vendor/mari-design submodule, so the submodule must be checked
# out before building.
set -euo pipefail
cd "$(dirname "$0")/.."

REGISTRY="public.ecr.aws/k1b8z8i5"
PLATFORMS="linux/amd64,linux/arm64"

VERSION="${1:-}"
if [ -z "$VERSION" ]; then
  echo "usage: ./deploy/publish-images.sh vX.Y.Z (for example v0.1.1)" >&2
  exit 1
fi
case "$VERSION" in
  v*) ;;
  *)
    echo "version must include the leading v, got '$VERSION'" >&2
    exit 1
    ;;
esac

command -v docker >/dev/null 2>&1 || {
  echo "docker is not on PATH. Install Docker Desktop or the docker CLI first." >&2
  exit 1
}
command -v aws >/dev/null 2>&1 || {
  echo "aws is not on PATH. Install the AWS CLI first." >&2
  exit 1
}

# The web Dockerfile copies vendor/mari-design/components into the build, so
# an unpopulated submodule fails halfway through a long multi-arch build.
# Catch it here instead.
if [ ! -e vendor/mari-design/components ]; then
  echo "vendor/mari-design is not checked out. Run: git submodule update --init --recursive" >&2
  exit 1
fi

echo "==> Preflight"
# ECR Public authenticates through us-east-1 regardless of where anything
# else lives. An exported AWS_PROFILE is honored as is, and this also fails
# loudly when the session has expired.
aws ecr-public get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin public.ecr.aws

for component in api web; do
  case "$component" in
    api) dockerfile="server/Dockerfile" ;;
    web) dockerfile="web/Dockerfile" ;;
  esac
  image="$REGISTRY/mari-$component"
  echo "==> Build + push $image:$VERSION ($PLATFORMS)"
  # provenance off to match the GitHub workflow, so both registries carry
  # plain multi-arch manifest lists rather than attestation-wrapped ones.
  docker buildx build \
    --platform "$PLATFORMS" \
    --provenance=false \
    -f "$dockerfile" \
    -t "$image:$VERSION" \
    -t "$image:latest" \
    --push \
    .
done

echo "==> Published mari-api and mari-web at $VERSION and latest"
echo "    The chart pins images by digest. Copy the new digests into"
echo "    deploy/helm/mari/values.yaml before packaging the release."
