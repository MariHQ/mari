# One Fly Machine: PostgreSQL/pgvector, API, and the compiled web console.
# The database and model cache live on the mounted /data volume.
FROM node:22-bookworm-slim AS web-build
WORKDIR /build
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY vendor/mari-design/components/ /vendor/mari-design/components/
COPY web/ ./
RUN npm run build

FROM pgvector/pgvector:pg16-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       awscli ca-certificates curl git gosu python3 python3-pip tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/server
COPY vendor/mari-components/ /app/vendor/mari-components/
RUN python3 -m pip install --break-system-packages --no-cache-dir /app/vendor/mari-components
COPY server/requirements.txt ./requirements.txt
RUN python3 -m pip install --break-system-packages --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cpu \
      --extra-index-url https://pypi.org/simple 'torch==2.13.0+cpu' \
    && python3 -m pip install --break-system-packages --no-cache-dir -r requirements.txt
COPY server/ /app/server/
COPY --from=web-build /build/dist/ /app/web/
COPY deploy/fly/start.sh /app/start.sh
RUN chmod 0755 /app/start.sh \
    && useradd --system --home-dir /app --shell /usr/sbin/nologin mari \
    && mkdir -p /data /app/server/builds \
    && chown -R mari:mari /app/server/builds /app/web

ENV PGDATA=/data/postgres \
    HOME=/data/mari \
    XDG_CACHE_HOME=/data/cache \
    HF_HOME=/data/models/huggingface \
    MARI_DB=postgresql://mari@127.0.0.1:5432/mari_cloud \
    MARI_STATIC_DIR=/app/web \
    MARI_SENTENCE_TRANSFORMERS_CACHE=/data/models/sentence-transformers

EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/app/start.sh"]
