from __future__ import annotations

import asyncio
import json
import logging
import unittest
from io import StringIO

from unittest.mock import patch

from types import SimpleNamespace

from fastapi import HTTPException
from psycopg_pool import PoolTimeout

from mari_server import settings
from mari_server.operations import routes as operation_routes
from mari_server.operations import telemetry as observability
from starlette.requests import Request
from starlette.responses import JSONResponse


def metrics_request(authorization: str | None = None) -> Request:
    headers = [(b"authorization", authorization.encode())] if authorization else []
    return Request({
        "type": "http", "method": "GET", "path": "/metrics", "query_string": b"",
        "scheme": "http", "server": ("test", 80), "client": ("test", 1), "root_path": "",
        "headers": headers,
    })


def readyz_request(ready: bool = True) -> Request:
    request = Request({
        "type": "http", "method": "GET", "path": "/readyz", "query_string": b"",
        "scheme": "http", "server": ("test", 80), "client": ("test", 1), "root_path": "",
        "headers": [], "app": SimpleNamespace(state=SimpleNamespace(ready=ready)),
    })
    return request


class ReadinessProbeTests(unittest.TestCase):
    def test_a_saturated_pool_answers_503_instead_of_hanging(self) -> None:
        # The chart's probe allows 3 s; the pool's default wait is 30 s. The
        # probe borrows with a short timeout, and the resulting PoolTimeout
        # has to come back as a distinct 503, never as a hang or a 500.
        with patch.object(operation_routes.system, "ready", side_effect=PoolTimeout("pool full")):
            with self.assertRaises(HTTPException) as caught:
                operation_routes.readyz(readyz_request())
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.detail, "Database connection pool is saturated.")

    def test_probe_borrows_with_a_short_timeout(self) -> None:
        from mari_server.persistence.postgres import connection, system
        from unittest.mock import MagicMock
        lease = MagicMock()
        lease.__enter__.return_value.execute.return_value.fetchone.return_value = {"ok": 1}
        process_pool = MagicMock()
        process_pool.connection.return_value = lease
        with patch.object(connection, "pool", return_value=process_pool):
            system.ready()
        process_pool.connection.assert_called_once_with(timeout=system.READY_POOL_TIMEOUT_SECONDS)
        self.assertLessEqual(system.READY_POOL_TIMEOUT_SECONDS, 3.0)

    def test_other_database_failures_keep_their_own_503(self) -> None:
        with patch.object(operation_routes.system, "ready", side_effect=RuntimeError("down")):
            with self.assertRaises(HTTPException) as caught:
                operation_routes.readyz(readyz_request())
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.detail, "Database is unavailable.")


class ObservabilityTests(unittest.TestCase):
    def test_request_middleware_propagates_ids_and_records_http_metrics(self) -> None:
        metrics = observability.Metrics()
        original = observability.METRICS
        scope = {
            "type": "http", "method": "GET", "path": "/items/7",
            "query_string": b"secret=never-a-label", "scheme": "http",
            "server": ("test", 80), "client": ("test", 1), "root_path": "",
            "headers": [(b"x-request-id", b"request-7"), (b"x-correlation-id", b"trace-2")],
        }
        request = Request(scope)

        class Route:
            path = "/items/{item_id}"

        async def call_next(req):
            req.scope["route"] = Route()
            return JSONResponse({"id": 7})

        try:
            observability.METRICS = metrics
            middleware = observability.RequestTelemetryMiddleware(lambda *_: None)
            response = asyncio.run(middleware.dispatch(request, call_next))
        finally:
            observability.METRICS = original
        self.assertEqual(response.headers["X-Request-ID"], "request-7")
        self.assertEqual(response.headers["X-Correlation-ID"], "trace-2")
        rendered = metrics.render()
        self.assertIn('mari_http_requests_total{method="GET",route="/items/{item_id}",status="200"} 1', rendered)
        self.assertNotIn("secret", rendered)

    def test_request_ids_reject_header_injection_and_preserve_valid_ids(self) -> None:
        self.assertEqual(observability.safe_request_id("trace-123/example"), "trace-123/example")
        minted = observability.safe_request_id("bad\nheader")
        self.assertNotIn("\n", minted)
        self.assertEqual(len(minted), 36)

    def test_unmatched_paths_share_one_bounded_metric_label(self) -> None:
        metrics = observability.Metrics()
        original = observability.METRICS

        async def exercise(path: str) -> None:
            request = Request({
                "type": "http", "method": "GET", "path": path,
                "query_string": b"", "scheme": "http", "server": ("test", 80),
                "client": ("test", 1), "root_path": "", "headers": [],
            })
            middleware = observability.RequestTelemetryMiddleware(lambda *_: None)
            await middleware.dispatch(request, lambda _: asyncio.sleep(0, result=JSONResponse({}, status_code=404)))

        try:
            observability.METRICS = metrics
            asyncio.run(exercise("/random/one"))
            asyncio.run(exercise("/random/two"))
        finally:
            observability.METRICS = original
        rendered = metrics.render()
        self.assertIn('route="<unmatched>",status="404"} 2', rendered)
        self.assertNotIn("random", rendered)

    def test_prometheus_output_has_counter_gauge_and_cumulative_histogram(self) -> None:
        metrics = observability.Metrics()
        metrics.inc("mari_test_total", route="/x", status=200)
        metrics.gauge("mari_test_lag_seconds", 12.5, provider="confluence")
        metrics.observe("mari_test_duration_seconds", 0.02, route="/x")
        rendered = metrics.render()
        self.assertIn("# TYPE mari_test_total counter", rendered)
        self.assertIn('mari_test_total{route="/x",status="200"} 1', rendered)
        self.assertIn('mari_test_lag_seconds{provider="confluence"} 12.5', rendered)
        self.assertIn('mari_test_duration_seconds_bucket{le="0.025",route="/x"} 1', rendered)
        self.assertIn('mari_test_duration_seconds_count{route="/x"} 1', rendered)

    def test_json_formatter_emits_machine_readable_request_context(self) -> None:
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(observability.JsonFormatter())
        logger = logging.getLogger("mari.test.observability")
        logger.handlers[:] = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)
        logger.info("done", extra={"request_id": "r-1", "status": 204, "duration_ms": 3.5})
        row = json.loads(stream.getvalue())
        self.assertEqual(row["message"], "done")
        self.assertEqual(row["request_id"], "r-1")
        self.assertEqual(row["status"], 204)

    def scrape(self, token: str, authorization: str | None) -> str:
        metrics = observability.Metrics()
        metrics.inc("mari_test_scrape_total")
        with patch.dict(settings.CONFIG["server"], {"metrics_token": token}), \
             patch.object(observability, "METRICS", metrics), \
             patch.object(operation_routes.system, "connector_lag", return_value=[]):
            return operation_routes.metrics(metrics_request(authorization))

    def test_metrics_stay_open_until_a_token_is_configured(self) -> None:
        self.assertIn("mari_test_scrape_total 1", self.scrape("", None))

    def test_metrics_require_the_configured_bearer_token(self) -> None:
        for header in (None, "Bearer wrong", "Basic scrape-secret", "scrape-secret"):
            with self.assertRaises(HTTPException) as caught:
                self.scrape("scrape-secret", header)
            self.assertEqual(caught.exception.status_code, 401)
            self.assertEqual(caught.exception.headers["WWW-Authenticate"], "Bearer")
        self.assertIn("mari_test_scrape_total 1", self.scrape("scrape-secret", "Bearer scrape-secret"))

    def test_llm_hook_records_result_without_prompt_or_credentials(self) -> None:
        metrics = observability.Metrics()
        original = observability.METRICS
        try:
            observability.METRICS = metrics
            observability.record_llm("generate", "ollama", False, 0.25)
        finally:
            observability.METRICS = original
        rendered = metrics.render()
        self.assertIn('mari_llm_requests_total{operation="generate",provider="ollama",result="error"} 1', rendered)
        self.assertNotIn("prompt", rendered)


if __name__ == "__main__":
    unittest.main()
