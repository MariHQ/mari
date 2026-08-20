# Python architecture

Mari's server code is migrating from flat modules to four explicit layers under
`server/mari_server`. The layers describe dependency direction, not deployment
units. They run in one process today.

## Dependency direction

```text
domain <- application <- infrastructure <- api
              ^                 |
              +-----------------+
```

- `domain`: pure business vocabulary and invariants. Standard library only. It
  does not know SQL, HTTP, models, configuration, or background workers.
- `application`: use cases and ports. It coordinates domain behavior through
  callables/protocols supplied by the host. It may use `mari-components`, but it
  does not import Mari's database, model gateway, authentication, or web stack.
- `infrastructure`: production implementations of application ports: SQL,
  object storage, provider APIs, model gateways, and telemetry. It does not
  expose HTTP routes.
- `api`: transport-only adapters. It validates HTTP/GraphQL input, resolves
  request identity, invokes an application use case through infrastructure
  adapters, and serializes output. Business behavior and SQL do not belong here.

Dependencies point inward. Domain and application modules never import outward
layers. Cross-layer calls use explicit immutable values and injected ports.

## Shared components versus product integration

Reusable algorithms, connector protocols, retrieval, workflow extraction, and
agent loops live in `mari-components`. They accept caller-owned storage, model,
authorization, and observability functions. `mari-cloud` owns project identity,
persistence schemas, FastAPI/GraphQL, deployment, and concrete wiring.

The server may adapt a `mari-components` primitive in infrastructure. It must
not copy reusable logic back into a route or database module.

## First migrated vertical: agent chat

- `domain/navigation.py`: valid product routes.
- `application/agent.py`: streaming turn orchestration and ports.
- `infrastructure/agent_tools.py`: project-scoped read tools over injected data
  access functions.
- `infrastructure/agent_runtime.py`: concrete Postgres, model, retrieval, usage,
  and trajectory adapters.
- `api/agent.py`: request/session invocation and SSE serialization.

## Connector ingestion

- `application/connector_ingestion.py`: consumes native connector pages lazily
  and applies each replay-safe synchronization plan through one transaction
  port. It neither buffers a corpus nor knows Postgres.
- `infrastructure/connector_provider.py`: adapts Mari's HTTP client and stored
  cursor envelope to `mari-components` connector definitions.
- `infrastructure/connector_runtime.py`: project-scoped document/chunk writes,
  checkpoints, embeddings, link extraction, and workflow triggers.

Each page's knowledge mutations, manifest, and provider checkpoint commit in
one transaction. Full-snapshot absence deletion is planned only by
`mari-components` after a provider declares the terminal page complete.

## Unified Review

- `domain/review.py`: immutable queue records and policy results.
- `application/review.py`: filtering, cursors, deterministic policy evaluation,
  separation-of-duties checks, replay orchestration, and explicit persistence
  ports.
- `infrastructure/review_repository.py`: the cross-domain Postgres projection,
  stored decisions, native-record approval updates, and audit adapter.

GraphQL resolvers translate these values at the boundary. They do not own the
policy or reach through a flat Review service.

## MCP destinations

- `domain/mcp.py`: destination spec, capability vocabulary, and validation.
- `application/mcp.py`: create/update/delete/test lifecycle through explicit
  token, persistence, diagnostics, and audit ports.
- `infrastructure/mcp_repository.py`: hashed-token Postgres records and
  capability-specific health counts.

The Strawberry mutation only resolves the authorized project and translates
the application result. Duplicate creation fails rather than rotating a token
or mutating an existing destination implicitly.

## Migration rules

1. Migrate one complete vertical workflow at a time, with its tests.
2. Define the domain types and application ports before moving implementation.
3. Preserve behavior at the boundary; then remove the legacy implementation.
4. Do not create generic repositories, service locators, or an application
   container. Inject the few functions each use case actually needs.
5. Do not add flat compatibility facades for migrated modules. Migrate all
   internal callers and let stale imports fail immediately.
6. `test_architecture.py` is a required CI gate. Extend it when a new boundary is
   introduced.

Next verticals are publishing and automation execution. The
large GraphQL modules should become transport declarations that call those use
cases, not be split mechanically by line count.
