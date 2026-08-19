# Production operations and SLOs

## Service objectives

Measure these objectives over a rolling 28-day window, excluding announced
maintenance:

- API availability: 99.9% of non-probe HTTP requests return a status below 500.
- Interactive latency: 95% of non-streaming API requests complete within 750 ms.
- Search freshness: 99% of healthy connector sources are no more than twice
  their configured poll interval behind.
- Workflow completion: 99% of started, non-approval workflow runs reach a
  terminal state within 15 minutes.
- LLM dependency: track separately from API availability. Ollama/provider
  failures are expected to degrade to deterministic behavior and must not make
  the API unavailable.

Page when the fast-burn rate consumes 2% of the 28-day error budget in one hour,
or the slow-burn rate consumes 10% in six hours. Page immediately when readiness
is continuously failing for five minutes or all connector freshness gauges stop
advancing.

## Signals

The API exposes unauthenticated, non-secret operational endpoints:

- `GET /livez`: process liveness only; never checks downstream services.
- `GET /readyz`: startup completion and database reachability.
- `GET /healthz`: compatibility alias for readiness.
- `GET /metrics`: Prometheus text exposition for HTTP request count/latency,
  LLM request result/latency, connector sync lag, and metrics dependency errors.

Every response carries `X-Request-ID` and `X-Correlation-ID`. A valid inbound
value is propagated; otherwise Mari generates a UUID. Container logs are JSON
and include those IDs, route template, status, and duration. Query strings,
request bodies, prompts, credentials, and connector configuration are never
logged or labeled.

## Dashboard and alert minimums

Graph request rate, 5xx ratio, p50/p95/p99 latency, readiness, connector lag by
provider, and LLM error/latency by provider and operation. Do not label metrics
with document, source, user, request, or model input values; those are unbounded
and can contain private data.

The API deployment intentionally remains one replica while scheduled work is
owned by an in-process scheduler. Scaling it horizontally would duplicate
scheduled executions. The web tier can autoscale independently. Before raising
the API replica count, introduce a single scheduler leader or external scheduler
and verify exactly-once claim behavior under a two-pod failure test.
