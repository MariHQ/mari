# Mari API — FastAPI + psycopg + pgvector client.
# Build context is the REPO ROOT (see docker-compose.yml), not server/.
FROM python:3.12-slim

# git is required at runtime: the repo audit clones the connected GitHub
# repo and shells out to `git log` against it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/server

# Dependencies first for layer caching. psycopg[binary] ships a prebuilt
# libpq, and markdown is imported by the site builder; neither is in
# requirements.txt (the local dev flow installs them separately).
COPY server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt "psycopg[binary]>=3.2" markdown

COPY server/ /app/server/

# app.py mkdirs builds/ at startup, but create it here too so the image
# works read-only-ish out of the box.
RUN mkdir -p /app/server/builds

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
