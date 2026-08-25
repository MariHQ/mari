#!/usr/bin/env bash
set -Eeuo pipefail

context="$(kubectl config current-context)"
case "$context" in
  docker-desktop|kind-*) ;;
  *)
    echo "Refusing to replace the mari namespace on context '$context'." >&2
    echo "Use Docker Desktop/kind, or set MARI_K8S_SMOKE_ALLOW_CONTEXT=1 deliberately." >&2
    [[ "${MARI_K8S_SMOKE_ALLOW_CONTEXT:-}" == "1" ]] || exit 2
    ;;
esac

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
api_repository="${MARI_K8S_API_REPOSITORY:-mari-cloud-api}"
api_tag="${MARI_K8S_API_TAG:-k8s-smoke}"
web_repository="${MARI_K8S_WEB_REPOSITORY:-mari-cloud-web}"
web_tag="${MARI_K8S_WEB_TAG:-k8s-smoke}"
api_image="$api_repository:$api_tag"
web_image="$web_repository:$web_tag"

diagnostics() {
  kubectl -n mari get all || true
  kubectl -n mari describe pods || true
  kubectl -n mari logs -l app.kubernetes.io/component=api --all-containers --tail=200 || true
  kubectl -n mari logs -l app.kubernetes.io/component=web --tail=100 || true
}
trap diagnostics ERR

docker build -f "$root/server/Dockerfile" -t "$api_image" "$root"
docker build -f "$root/web/Dockerfile" -t "$web_image" "$root"

if [[ "$context" == kind-* ]]; then
  cluster="${context#kind-}"
  kind load docker-image --name "$cluster" "$api_image" "$web_image"
fi

kubectl delete namespace mari --ignore-not-found --wait --timeout=180s
kubectl create namespace mari

kubectl -n mari create secret generic mari-secrets \
  --from-literal='POSTGRES_PASSWORD=mari-smoke-only' \
  --from-literal='MARI_DB=postgresql://mari:mari-smoke-only@postgres:5432/mari_cloud'

helm upgrade --install mari "$root/deploy/helm/mari" \
  --namespace mari \
  --set secrets.existingSecret=mari-secrets \
  --set-string api.image.repository="$api_repository" \
  --set-string api.image.tag="$api_tag" \
  --set-string web.image.repository="$web_repository" \
  --set-string web.image.tag="$web_tag" \
  --set ingress.enabled=false \
  --set-string config.MARI_AUTH_BYPASS=true \
  --set-string config.MARI_AUTH_BYPASS_DEV_MODE=true \
  --set-string config.MARI_VECTOR_FLUSH_SECONDS=0 \
  --wait --timeout 10m

test "$(kubectl -n mari exec deployment/mari-api -c api -- id -u)" = "10001"
kubectl -n mari exec deployment/mari-web -- \
  wget -qO- http://127.0.0.1:8080/livez | grep -q '"ok":true'
kubectl -n mari exec deployment/mari-web -- \
  wget -qO- http://127.0.0.1:8080/readyz | grep -q '"database":"ok"'
kubectl -n mari exec deployment/mari-web -- \
  wget -qO- http://127.0.0.1:8080/ | grep -qi '<!doctype html>'

db_pod="$(kubectl -n mari get pod -l app.kubernetes.io/component=postgres -o jsonpath='{.items[0].metadata.name}')"
migration_count="$(kubectl -n mari exec "$db_pod" -- \
  psql -U mari -d mari_cloud -Atc 'select count(*) from schema_migrations')"
test "$migration_count" -gt 0

test "$(kubectl -n mari get deployment mari-web -o jsonpath='{.spec.replicas}')" = "1"
kubectl -n mari get deployments,statefulsets,pods,services,pvc,pdb
echo "Kubernetes smoke passed on $context with $migration_count migrations."
