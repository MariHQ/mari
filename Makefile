.PHONY: test test-server test-web test-browser test-contracts test-agent-evals test-live-ollama test-live-connectors test-integration test-reliability test-restore

test: test-server test-web test-browser

test-server:
	PYTHONPATH=server server/.venv/bin/python -m unittest discover -s server/tests -v

test-web:
	npm --prefix web run check

test-browser:
	npm --prefix web run e2e

test-contracts:
	PYTHONPATH=server server/.venv/bin/python -m export_graphql_schema

test-agent-evals:
	PYTHONPATH=server server/.venv/bin/python -m unittest server.tests.test_agent_evals -v

test-live-ollama:
	MARI_TEST_LIVE_OLLAMA=1 PYTHONPATH=server server/.venv/bin/python -m unittest server.tests.test_llm_ollama.LiveOllamaTests -v

test-live-connectors:
	./deploy/live-canary.sh

test-integration:
	./deploy/integration/run.sh

test-reliability:
	./deploy/integration/resilience.sh

test-restore:
	./deploy/integration/restore-drill.sh
