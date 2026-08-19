from __future__ import annotations

import asyncio
import json
import logging
import unittest
from io import StringIO

import observability
from starlette.requests import Request
from starlette.responses import JSONResponse


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
