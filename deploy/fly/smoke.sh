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
# The image ships no embedding model (79cfb76 moved embeddings to HTTP), so
# stand up a stub endpoint inside the container and prove both HTTP transports
# the image can use: the ollama shape (/api/embeddings) and the OpenAI shape
# (/v1/embeddings) that the gateway and openai providers speak.
docker exec -d "$container" python3 -c "$(cat <<'STUB'
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
VEC = [0.01] * 768
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length') or 0))
        if self.path == '/api/embeddings':
            body = {'embedding': VEC}
        elif self.path == '/v1/embeddings':
            body = {'data': [{'index': 0, 'embedding': VEC}], 'usage': {}}
        else:
            self.send_response(404); self.end_headers(); return
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
HTTPServer(('127.0.0.1', 11434), H).serve_forever()
STUB
)"
i=0
until docker exec "$container" python3 -c \
  "import socket; socket.create_connection(('127.0.0.1', 11434), timeout=1).close()" >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -lt 30 ] || { echo "embedding stub never came up" >&2; exit 1; }
  sleep 1
done
embed_check="from mari_server.providers import models; vector=models.embed('Mari product knowledge'); assert vector is not None, models.last_error(); assert len(vector) == 768"
docker exec \
  -e MARI_EMBEDDING_PROVIDER=ollama \
  -e MARI_EMBEDDING_MODEL=nomic-embed-text \
  "$container" gosu mari python3 -c "$embed_check"
docker exec \
  -e MARI_EMBEDDING_PROVIDER=gateway \
  -e MARI_EMBEDDING_MODEL=text-embedding-3-small \
  -e MARI_LLM_GATEWAY_URL=http://127.0.0.1:11434/v1 \
  -e MARI_LLM_GATEWAY_COMPATIBILITY=openai \
  "$container" gosu mari python3 -c "$embed_check"

docker restart "$container" >/dev/null
i=0
until curl -fsS http://127.0.0.1:18080/readyz >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -lt 120 ] || exit 1
  sleep 1
done
echo "Fly image smoke OK"
