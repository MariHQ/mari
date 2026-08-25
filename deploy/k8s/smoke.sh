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
api_image="${MARI_K8S_API_IMAGE:-mari-cloud-api:k8s-smoke}"
web_image="${MARI_K8S_WEB_IMAGE:-mari-cloud-web:k8s-smoke}"

diagnostics() {
  kubectl -n mari get all || true
  kubectl -n mari describe pods || true
  kubectl -n mari logs -l app.kubernetes.io/name=mari-api --all-containers --tail=200 || true
  kubectl -n mari logs -l app.kubernetes.io/name=mari-web --tail=100 || true
}
trap diagnostics ERR

docker build -f "$root/server/Dockerfile" -t "$api_image" "$root"
docker build -f "$root/web/Dockerfile" -t "$web_image" "$root"

if [[ "$context" == kind-* ]]; then
  cluster="${context#kind-}"
  kind load docker-image --name "$cluster" "$api_image" "$web_image"
fi

kubectl apply -f "$root/deploy/k8s/namespace.yaml"
kubectl -n mari create secret generic mari-secrets \
  --from-literal='MARI_DB=postgresql://mari:mari@mari-postgres:5432/mari_cloud' \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n mari create deployment mari-postgres \
  --image=pgvector/pgvector:pg16 --dry-run=client -o yaml | kubectl apply -f -
kubectl -n mari set env deployment/mari-postgres \
  POSTGRES_USER=mari POSTGRES_PASSWORD=mari POSTGRES_DB=mari_cloud
kubectl -n mari create service clusterip mari-postgres \
  --tcp=5432:5432 --dry-run=client -o yaml | kubectl apply -f -
kubectl -n mari rollout status deployment/mari-postgres --timeout=180s

kubectl apply -k "$root/deploy/k8s"
kubectl -n mari patch configmap mari-config --type merge -p \
  '{"data":{"MARI_ICEBERG_WAREHOUSE":"/app/data/iceberg","MARI_VECTOR_URI":"/app/data/vectors","MARI_VECTOR_CACHE":"/app/data/vector-cache","MARI_VECTOR_FLUSH_SECONDS":"0","MARI_OLLAMA_HOST":"http://127.0.0.1:11434","MARI_AUTH_BYPASS":"true","MARI_AUTH_BYPASS_DEV_MODE":"true"}}'
kubectl -n mari set image deployment/mari-api \
  schema-migrations="$api_image" api="$api_image"
kubectl -n mari set image deployment/mari-web web="$web_image"
# ConfigMap values are read only at pod creation and do not have a generated
# hash in the conservative production base, so force the smoke pod to refresh.
kubectl -n mari rollout restart deployment/mari-api
kubectl -n mari rollout status deployment/mari-api --timeout=300s
kubectl -n mari rollout status deployment/mari-web --timeout=300s

test "$(kubectl -n mari exec deployment/mari-api -c api -- id -u)" = "10001"
kubectl -n mari exec deployment/mari-web -- \
  wget -qO- http://127.0.0.1:8080/livez | grep -q '"ok":true'
kubectl -n mari exec deployment/mari-web -- \
  wget -qO- http://127.0.0.1:8080/readyz | grep -q '"database":"ok"'
kubectl -n mari exec deployment/mari-web -- \
  wget -qO- http://127.0.0.1:8080/ | grep -qi '<!doctype html>'

db_pod="$(kubectl -n mari get pod -l app=mari-postgres -o jsonpath='{.items[0].metadata.name}')"
migration_count="$(kubectl -n mari exec "$db_pod" -- \
  psql -U mari -d mari_cloud -Atc 'select count(*) from schema_migrations')"
test "$migration_count" -gt 0

kubectl -n mari get deployments,pods,services,hpa,pdb
echo "Kubernetes smoke passed on $context with $migration_count migrations."
