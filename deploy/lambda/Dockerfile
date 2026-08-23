ARG LAMBDA_ADAPTER_IMAGE=public.ecr.aws/awsguru/aws-lambda-adapter:1.0.1

FROM node:20-bookworm-slim AS web-build
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
# The console is a thin app over the @mari-design/components library, which
# lives in the vendor/mari-design submodule. web/lib-path.mjs looks for it one
# level above the app directory, so it has to land at /src/vendor/...
COPY vendor/mari-design/components/ /src/vendor/mari-design/components/
COPY web/ ./
RUN npm run build

FROM ${LAMBDA_ADAPTER_IMAGE} AS lambda-adapter

FROM debian:bookworm-slim AS prctl-shim
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
COPY deploy/lambda/prctl-shim.c /tmp/prctl-shim.c
RUN gcc -shared -fPIC -O2 -o /lambda-prctl-shim.so /tmp/prctl-shim.c -ldl

FROM pgvector/pgvector:pg16-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# The server imports the mari_components packages (connectors, retrieval,
# knowledge, agents, destinations, trajectories, workflows); they live outside
# server/ and are not in requirements.txt, so install them from source first.
COPY mari-components/ /app/mari-components/
RUN pip3 install --break-system-packages --no-cache-dir /app/mari-components/packages/*
COPY server/requirements.txt /app/server/requirements.txt
RUN pip3 install --break-system-packages --no-cache-dir -r /app/server/requirements.txt

COPY --from=lambda-adapter /lambda-adapter /opt/extensions/lambda-adapter
COPY --from=prctl-shim /lambda-prctl-shim.so /usr/local/lib/lambda-prctl-shim.so
COPY server/ /app/server/
COPY --from=web-build /src/web/dist/ /app/web/
COPY deploy/lambda/mari_cloud.dump /app/mari_cloud.dump
COPY deploy/lambda/start.sh /app/start.sh

RUN chmod 755 /app/start.sh \
    && mkdir -p /app/server/builds \
    && chown -R postgres:postgres /app

ENV AWS_LWA_PORT=8080 \
    AWS_LWA_READINESS_CHECK_PATH=/healthz \
    AWS_LWA_ASYNC_INIT=true \
    AWS_LWA_ENABLE_COMPRESSION=true \
    AWS_LWA_ENABLE_GRACEFUL_SHUTDOWN=true \
    MARI_DB=postgresql://postgres@127.0.0.1:5432/mari_cloud \
    MARI_STATIC_DIR=/app/web \
    MARI_PGDATA=/tmp/mari/pgdata \
    PYTHONUNBUFFERED=1

USER postgres
WORKDIR /app/server
EXPOSE 8080
ENTRYPOINT ["/app/start.sh"]
