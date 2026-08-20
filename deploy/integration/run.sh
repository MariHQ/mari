#!/usr/bin/env sh
set -eu

cleanup() {
  docker compose -f docker-compose.yml -f deploy/integration/docker-compose.yml down -v --remove-orphans
}
trap cleanup EXIT INT TERM

docker compose -f docker-compose.yml -f deploy/integration/docker-compose.yml up -d --build --wait --wait-timeout 900
docker compose -f docker-compose.yml -f deploy/integration/docker-compose.yml exec -T \
  -e MARI_INTEGRATION_STACK=1 api python -m unittest tests.test_integration_stack -v
./deploy/integration/resilience.sh
./deploy/integration/restore-drill.sh
(
  cd web
  MARI_E2E_EXTERNAL_SERVER=1 \
  MARI_E2E_BASE_URL=http://127.0.0.1:8080 \
  MARI_E2E_INTEGRATION=1 \
    npx playwright test --project=integration-chromium --reporter=line
)
