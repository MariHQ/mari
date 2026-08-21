"""Small dependency-free black-box load probe for the assembled stack."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import http.cookiejar
import json
import math
import os
import threading
import time
import urllib.error
import urllib.request


BASE = os.environ.get("MARI_LOAD_BASE_URL", "http://web:8080").rstrip("/")
DURATION = max(1.0, float(os.environ.get("MARI_LOAD_DURATION_SECONDS", "15")))
CONCURRENCY = max(1, int(os.environ.get("MARI_LOAD_CONCURRENCY", "8")))
P95_BUDGET_MS = float(os.environ.get("MARI_LOAD_P95_MS", "2000"))
ERROR_BUDGET = float(os.environ.get("MARI_LOAD_ERROR_RATE", "0.01"))
QUERY = "{ sourcePulse { id provider name docsCount health } reviewItems(first: 25) { totalCount pageInfo { hasNextPage } } }"


def _request(opener, path: str, *, body: dict | None = None, headers: dict | None = None):
    payload = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        BASE + path,
        data=payload,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST" if body is not None else "GET",
    )
    started = time.perf_counter()
    with opener.open(request, timeout=10) as response:
        value = json.loads(response.read())
    return value, (time.perf_counter() - started) * 1000


def _worker(deadline: float, latencies: list[float], errors: list[str], lock: threading.Lock):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        _request(opener, "/auth/bypass", body={})
        identity, _ = _request(opener, "/auth/me")
        project = identity.get("activeProject") or {}
        project_key = project.get("slug") or project.get("id")
        if not project_key:
            raise RuntimeError("auth/me returned no active project")
    except Exception as exc:  # noqa: BLE001
        with lock:
            errors.append(f"authentication: {type(exc).__name__}: {exc}")
        return

    headers = {"X-Mari-Project": str(project_key)}
    while time.monotonic() < deadline:
        try:
            result, elapsed = _request(opener, "/graphql", body={"query": QUERY}, headers=headers)
            if result.get("errors"):
                raise RuntimeError(str(result["errors"][0].get("message") or "GraphQL error"))
            if "sourcePulse" not in (result.get("data") or {}):
                raise RuntimeError("sourcePulse missing from response")
            with lock:
                latencies.append(elapsed)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(f"{type(exc).__name__}: {exc}")


def main() -> None:
    latencies: list[float] = []
    errors: list[str] = []
    lock = threading.Lock()
    deadline = time.monotonic() + DURATION
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = [pool.submit(_worker, deadline, latencies, errors, lock) for _ in range(CONCURRENCY)]
        for future in futures:
            future.result()

    attempts = len(latencies) + len(errors)
    if attempts < CONCURRENCY * 2:
        raise SystemExit(f"load probe made too few requests: {attempts}")
    ordered = sorted(latencies)
    p95 = ordered[min(len(ordered) - 1, max(0, math.ceil(len(ordered) * 0.95) - 1))] if ordered else math.inf
    error_rate = len(errors) / attempts
    print(json.dumps({
        "requests": attempts,
        "successes": len(latencies),
        "errors": len(errors),
        "error_rate": round(error_rate, 5),
        "p95_ms": round(p95, 2),
        "budget_p95_ms": P95_BUDGET_MS,
    }, sort_keys=True))
    if errors:
        print("first_errors=" + json.dumps(errors[:5]))
    if error_rate > ERROR_BUDGET:
        raise SystemExit(f"error rate {error_rate:.3%} exceeds {ERROR_BUDGET:.3%}")
    if p95 > P95_BUDGET_MS:
        raise SystemExit(f"p95 {p95:.1f}ms exceeds {P95_BUDGET_MS:.1f}ms")


if __name__ == "__main__":
    main()
