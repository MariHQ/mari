# Mari API — FastAPI + psycopg + pgvector client.
# Build context is the REPO ROOT (see docker-compose.yml), not server/.
#
# The base image is pinned by tag only. To pin by digest, resolve it with
#   docker buildx imagetools inspect python:3.12-slim --format '{{.Manifest.Digest}}'
# and write FROM python:3.12-slim@sha256:<digest>.
FROM python:3.12-slim

# git is required at runtime: the repo audit clones the connected GitHub
# repo and shells out to `git log` against it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/server

# Install the reusable product-knowledge packages from this repository before
# the application dependencies so local, CI, and container builds use the same
# source tree.
COPY mari-components/ /app/mari-components/
RUN pip install --no-cache-dir /app/mari-components/packages/*

# Dependencies first for layer caching. Every runtime dependency is declared
# in requirements.txt so local and container installs resolve the same set.
COPY server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ /app/server/

# app.py mkdirs builds/ at startup, but create it here too so the image
# works read-only-ish out of the box.
RUN addgroup --system --gid 10001 mari \
    && adduser --system --uid 10001 --ingroup mari --home /app mari \
    && mkdir -p /app/server/builds /app/data \
    && chown -R mari:mari /app

# Keep the runtime identity numeric so Kubernetes can enforce runAsNonRoot
# without having to resolve an image-local user name.
USER 10001:10001

EXPOSE 8000
CMD ["uvicorn", "mari_server.app:app", "--host", "0.0.0.0", "--port", "8000"]
