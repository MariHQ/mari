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
- `agentchat.py`: temporary compatibility exports only; new code must not depend
  on it.

## Migration rules

1. Migrate one complete vertical workflow at a time, with its tests.
2. Define the domain types and application ports before moving implementation.
3. Preserve behavior at the boundary; then remove the legacy implementation.
4. Do not create generic repositories, service locators, or an application
   container. Inject the few functions each use case actually needs.
5. A compatibility module may re-export new symbols but may not contain logic.
6. `test_architecture.py` is a required CI gate. Extend it when a new boundary is
   introduced.

Next verticals are connector ingestion, knowledge review, and publishing. The
large GraphQL modules should become transport declarations that call those use
cases, not be split mechanically by line count.
