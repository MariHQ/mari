#!/bin/sh
set -eu

image=${MARI_FLY_IMAGE:-mari-fly-smoke}
container=${MARI_FLY_CONTAINER:-mari-fly-smoke}
volume=${MARI_FLY_VOLUME:-mari-fly-smoke-data}

cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
cleanup

docker build -f deploy/fly/Dockerfile -t "$image" .
docker volume create "$volume" >/dev/null
docker run -d --name "$container" --memory=2g -p 18080:8080 -v "$volume":/data \
  -e MARI_AUTH_BYPASS=true -e MARI_AUTH_BYPASS_DEV_MODE=true \
  "$image" >/dev/null

i=0
until curl -fsS http://127.0.0.1:18080/readyz >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 180 ]; then
    docker logs "$container"
    exit 1
  fi
  sleep 1
done

curl -fsS http://127.0.0.1:18080/ >/dev/null
curl -fsS http://127.0.0.1:18080/livez >/dev/null
docker exec "$container" gosu postgres psql -h 127.0.0.1 -U mari -d mari_cloud \
  -tAc "SELECT extname FROM pg_extension WHERE extname='vector'" | grep -q vector
docker exec \
  -e MARI_EMBEDDING_PROVIDER=sentence-transformers \
  -e MARI_EMBEDDING_MODEL=sentence-transformers/all-mpnet-base-v2 \
  "$container" gosu mari python3 -c \
  "import llm; vector=llm.embed('Mari product knowledge'); assert vector is not None, llm.last_error(); assert len(vector) == 768"

docker restart "$container" >/dev/null
i=0
until curl -fsS http://127.0.0.1:18080/readyz >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -lt 120 ] || exit 1
  sleep 1
done
echo "Fly image smoke OK"
