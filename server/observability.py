"""Small, dependency-free production observability primitives for Mari.

The module deliberately avoids importing the database or application modules so
it can be used from request, connector, workflow, and model code without adding
cycles. Metrics are process-local and Prometheus-compatible; a scraper is the
durable store.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
import uuid
from contextvars import ContextVar
from collections import defaultdict
from dataclasses import dataclass, field

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


_HEADER_VALUE = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")
_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
_request_id: ContextVar[str] = ContextVar("mari_request_id", default="")
_correlation_id: ContextVar[str] = ContextVar("mari_correlation_id", default="")


def safe_request_id(value: str | None) -> str:
    """Accept a trace-friendly caller id, otherwise mint an opaque UUID."""
    return value if value and _HEADER_VALUE.fullmatch(value) else str(uuid.uuid4())


def request_context() -> tuple[str, str]:
    """Current request/correlation ids, or fresh ids for background work."""
    request_id = _request_id.get() or safe_request_id(None)
    return request_id, _correlation_id.get() or request_id


class JsonFormatter(logging.Formatter):
    """One JSON object per line, suitable for container log collectors."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "correlation_id", "method", "path", "status", "duration_ms"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers[:] = [handler]
        logger.propagate = False


def _labels(values: tuple[tuple[str, str], ...]) -> str:
    if not values:
        return ""
    escaped = [f'{k}="{v.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'
               for k, v in values]
    return "{" + ",".join(escaped) + "}"


@dataclass
class Metrics:
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = field(
        default_factory=lambda: defaultdict(float))
    _gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = field(default_factory=dict)
    _histograms: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = field(default_factory=dict)

    @staticmethod
    def labels(**labels: object) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((str(k), str(v)) for k, v in labels.items()))

    def inc(self, name: str, amount: float = 1.0, **labels: object) -> None:
        with self._lock:
            self._counters[(name, self.labels(**labels))] += amount

    def gauge(self, name: str, value: float, **labels: object) -> None:
        if not math.isfinite(value):
            return
        with self._lock:
            self._gauges[(name, self.labels(**labels))] = value

    def observe(self, name: str, value: float, **labels: object) -> None:
        if not math.isfinite(value) or value < 0:
            return
        key = (name, self.labels(**labels))
        with self._lock:
            state = self._histograms.setdefault(key, [0.0] * (len(_BUCKETS) + 2))
            for index, bound in enumerate(_BUCKETS):
                if value <= bound:
                    state[index] += 1
            state[len(_BUCKETS)] += 1  # +Inf / count
            state[len(_BUCKETS) + 1] += value

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            counters = sorted(self._counters.items())
            gauges = sorted(self._gauges.items())
            histograms = sorted(self._histograms.items())
        seen: set[str] = set()
        for (name, labels), value in counters:
            if name not in seen:
                lines.extend((f"# HELP {name} Mari application counter.", f"# TYPE {name} counter"))
                seen.add(name)
            lines.append(f"{name}{_labels(labels)} {value:g}")
        for (name, labels), value in gauges:
            if name not in seen:
                lines.extend((f"# HELP {name} Mari application gauge.", f"# TYPE {name} gauge"))
                seen.add(name)
            lines.append(f"{name}{_labels(labels)} {value:g}")
        for (name, labels), state in histograms:
            if name not in seen:
                lines.extend((f"# HELP {name} Mari application histogram.", f"# TYPE {name} histogram"))
                seen.add(name)
            for index, bound in enumerate(_BUCKETS):
                bucket_labels = tuple(sorted((*labels, ("le", str(bound)))))
                lines.append(f"{name}_bucket{_labels(bucket_labels)} {state[index]:g}")
            inf_labels = tuple(sorted((*labels, ("le", "+Inf"))))
            lines.append(f"{name}_bucket{_labels(inf_labels)} {state[len(_BUCKETS)]:g}")
            lines.append(f"{name}_count{_labels(labels)} {state[len(_BUCKETS)]:g}")
            lines.append(f"{name}_sum{_labels(labels)} {state[len(_BUCKETS) + 1]:g}")
        return "\n".join(lines) + "\n"


METRICS = Metrics()


def record_llm(operation: str, provider: str, success: bool, duration_seconds: float) -> None:
    labels = {"operation": operation, "provider": provider or "unknown", "result": "ok" if success else "error"}
    METRICS.inc("mari_llm_requests_total", **labels)
    METRICS.observe("mari_llm_request_duration_seconds", duration_seconds,
                    operation=operation, provider=provider or "unknown")


def record_llm_usage(provider: str, model: str, usage: dict[str, object] | None,
                     cost: float | None = None) -> None:
    """Account only numeric provider totals; never prompts or response text."""
    usage = usage or {}
    prompt = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    completion = usage.get("completion_tokens", usage.get("output_tokens", 0))
    total = usage.get("total_tokens", 0)
    try:
        prompt_n, completion_n = max(0, int(prompt or 0)), max(0, int(completion or 0))
        total_n = max(0, int(total or prompt_n + completion_n))
    except (TypeError, ValueError):
        return
    labels = {"provider": provider or "unknown", "model": model or "unknown"}
    if prompt_n:
        METRICS.inc("mari_llm_tokens_total", prompt_n, token_type="prompt", **labels)
    if completion_n:
        METRICS.inc("mari_llm_tokens_total", completion_n, token_type="completion", **labels)
    if total_n and not (prompt_n or completion_n):
        METRICS.inc("mari_llm_tokens_total", total_n, token_type="total", **labels)
    try:
        if cost is not None and float(cost) >= 0:
            METRICS.inc("mari_llm_cost_usd_total", float(cost), **labels)
    except (TypeError, ValueError):
        pass


def observe_connector_lag(provider: str, lag_seconds: float) -> None:
    METRICS.gauge("mari_connector_sync_lag_seconds", max(0.0, lag_seconds), provider=provider or "unknown")


class RequestTelemetryMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        request_id = safe_request_id(request.headers.get("X-Request-ID"))
        correlation_id = safe_request_id(request.headers.get("X-Correlation-ID") or request_id)
        request.state.request_id = request_id
        request.state.correlation_id = correlation_id
        request_token = _request_id.set(request_id)
        correlation_token = _correlation_id.set(correlation_id)
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            duration = time.perf_counter() - started
            route = request.scope.get("route")
            path = getattr(route, "path", request.url.path)
            METRICS.inc("mari_http_requests_total", method=request.method, route=path, status=status)
            METRICS.observe("mari_http_request_duration_seconds", duration,
                            method=request.method, route=path)
            logging.getLogger("mari.http").info(
                "request completed",
                extra={"request_id": request_id, "correlation_id": correlation_id,
                       "method": request.method, "path": path, "status": status,
                       "duration_ms": round(duration * 1000, 2)},
            )
            if "response" in locals():
                response.headers["X-Request-ID"] = request_id
                response.headers["X-Correlation-ID"] = correlation_id
            _request_id.reset(request_token)
            _correlation_id.reset(correlation_token)
