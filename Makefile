.PHONY: test test-server test-web test-browser test-live-ollama test-integration

test: test-server test-web test-browser

test-server:
	PYTHONPATH=server server/.venv/bin/python -m unittest discover -s server/tests -v

test-web:
	npm --prefix web run check

test-browser:
	npm --prefix web run e2e

test-live-ollama:
	MARI_TEST_LIVE_OLLAMA=1 PYTHONPATH=server server/.venv/bin/python -m unittest server.tests.test_llm_ollama.LiveOllamaTests -v

test-integration:
	./deploy/integration/run.sh

test-reliability:
	./deploy/integration/resilience.sh
