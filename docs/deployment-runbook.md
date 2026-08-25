# Kubernetes deployment runbook

The Helm chart in `deploy/helm/mari` is the v0.1.0 deployment path. It starts
PostgreSQL/pgvector, one API, exactly one web pod, and persistent volumes for
the database and application data. Never commit a populated Secret; the exact
required keys and install commands are in the chart README. Helm is the single
deployment source; generated manifests must not be edited or committed.

## Preflight

1. Create the two database keys documented in the chart README. The bundled
   PostgreSQL/pgvector StatefulSet stores its database on a persistent volume.
2. Size and back up both persistent volumes. All Iceberg, vector, audit, and
   cache files stay below `/data` on the API volume; no object store or cloud
   credentials are required.
3. Configure source and model providers in Mari after installation. Their
   credentials are application data, not Kubernetes deployment secrets.
4. Confirm that the immutable API and web digests in the chart match the v0.1.0
   release published to GHCR.
5. Set the customer-owned ingress hostname, application URL, and CORS origin.

## Rollout

Install the chart and wait for `/readyz`, then run the smoke suite. The API uses
the `Recreate` strategy because its filesystem volume is ReadWriteOnce. Keep the
web replica count at exactly one for v0.1.0.

Every pull request also builds both production images and installs the Helm
chart in a disposable kind cluster, runs the real migration init container, waits for the workloads,
and probes `/livez`, `/readyz`, and the SPA through the web Service. To run the
same destructive smoke locally against Docker Desktop Kubernetes, use
`make test-k8s`; it replaces objects in the local `mari` namespace and leaves
them running for inspection.

### Live connector canaries

The `Live connector canaries` workflow validates Confluence, Slack, Google
Drive, and GitHub against a dedicated sandbox every Tuesday without storing
credentials or creating sources. Configure the protected GitHub environment
`connector-canary` with the `MARI_E2E_*` secrets named in the workflow. The
job intentionally fails when any priority credential is absent or expired, so
a green schedule means all four vendor authentication contracts were actually
exercised. A manual dispatch can opt into mutation-enabled sandbox workflows;
scheduled runs are read-only validation.

Live Playwright recording is disabled because forms contain real secrets.

For rollback, deploy the previous immutable image digest. Schema changes must
remain backward-compatible for at least one release. A rollback is complete only
after readiness is green and connector lag resumes falling.

## Backup and restore

- Back up the PostgreSQL and API persistent volumes on the same schedule.
- Test restoring both volumes into an isolated namespace.
- Published site artifacts can be regenerated, but retain release manifests.
- Derived embeddings/vector snapshots are disposable; restore source documents
  and rebuild them rather than treating them as records of truth.

Quarterly, restore into an isolated namespace, run schema initialization, compare
document/source/workflow/audit counts, rebuild retrieval snapshots, and execute
the browser smoke suite. Record recovery point and recovery time.

Every pull request performs the same core recovery proof in the production-like
Compose stack: `make test-restore` restores a `pg_dump` into an isolated database,
compares representative tenant and control-state counts, reruns the migration ledger,
and restores the versioned application artifacts. This is a release gate, not a substitute
for the managed provider's point-in-time recovery exercise.

## Incident triage

Use the response request/correlation ID to join client reports to JSON logs. If
`/livez` passes and `/readyz` fails, inspect database reachability/pool exhaustion.
If both pass but LLM errors rise, preserve API service and investigate the model
dependency separately. If connector lag rises, inspect sync checkpoints and the
scheduler before forcing a full resync.
