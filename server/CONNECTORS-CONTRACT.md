# Connector host contract

Provider behavior belongs exclusively to `mari-components`. Mari Cloud does
not define connector modules, provider metadata, cursor semantics, or a legacy
poll result shape.

Add and test connectors in:

- `vendor/mari-components/src/mari_components/connectors/`
- `vendor/mari-components/tests/test_*connectors.py`

The public contract is `ConnectorDefinition.validate()` and
`ConnectorDefinition.poll()`, yielding native `PollPage` values. The shared
`plan_sync()` function owns replay, cursor, checkpoint, tombstone, ACL-change,
and incomplete-snapshot semantics. See `vendor/mari-components/README.md` and
`SCOPE.md` for authoring and invariants.

Mari Cloud owns only host concerns:

- `mari_server/infrastructure/connector_provider.py` injects the SSRF-guarded
  HTTP transport;
- `mari_server/infrastructure/connector_runtime.py` loads source state, applies
  `plan_sync()` page by page, and commits documents plus checkpoints;
- `connectors_api.py` presents the component catalog and stores credentials;
- webhook modules durably enqueue provider hints before acknowledging them.

There must never be a `server/connectors/` provider package or a second
connector registry. `tests/test_architecture.py` enforces that boundary.
