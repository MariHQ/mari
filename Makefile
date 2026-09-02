.PHONY: test test-server test-components test-web test-browser test-contracts test-agent-evals test-live-ollama test-live-deepseek test-live-connectors test-integration test-reliability test-restore test-fly-image test-k8s

test: test-server test-components test-web test-browser

test-server:
	PYTHONPATH=server server/.venv/bin/python -m unittest discover -s server/tests -v

# The component packages carry their own suites; nothing else discovers them.
test-components:
	cd mari-components && ../server/.venv/bin/python -m unittest discover -s tests -v
	for package in mari-components/packages/*; do \
	  if [ -d "$$package/tests" ]; then \
	    (cd "$$package" && ../../../server/.venv/bin/python -m unittest discover -s tests -v) || exit 1; \
	  fi; \
	done

test-web:
	npm --prefix web run check

test-browser:
	npm --prefix web run e2e

test-contracts:
	PYTHONPATH=server server/.venv/bin/python -m mari_server.scripts.export_graphql_schema

test-agent-evals:
	PYTHONPATH=server server/.venv/bin/python -m unittest server.tests.test_agent_evals -v

test-live-ollama:
	MARI_TEST_LIVE_OLLAMA=1 PYTHONPATH=server server/.venv/bin/python -m unittest server.tests.test_llm_ollama.LiveOllamaTests -v

test-live-deepseek:
	MARI_TEST_LIVE_DEEPSEEK=1 PYTHONPATH=server server/.venv/bin/python -m unittest server.tests.test_llm_gateway.LiveDeepSeekGatewayTests -v

test-live-connectors:
	./deploy/live-canary.sh

test-integration:
	./deploy/integration/run.sh

test-reliability:
	./deploy/integration/resilience.sh

test-restore:
	./deploy/integration/restore-drill.sh

test-fly-image:
	./deploy/fly/smoke.sh

test-k8s:
	./deploy/k8s/smoke.sh
