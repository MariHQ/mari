# Kubernetes deployment runbook

The Helm chart in `deploy/helm/mari` is the Kubernetes deployment path. It starts
PostgreSQL/pgvector, one API, exactly one web pod, and persistent volumes for
the database and application data. Never commit a populated Secret; the exact
required keys and install commands are in the chart README. Helm is the single
deployment source; generated manifests must not be edited or committed.

## Publishing releases

A release tag must be `vMAJOR.MINOR.PATCH`, and its version without the leading
`v` must match both `version` and `appVersion` in the chart's `Chart.yaml`.
Pushing the tag runs `.github/workflows/helm-release.yml`, which lints, renders,
and packages the chart, pushes it to
`oci://ghcr.io/marihq/charts/mari`, and attaches the `.tgz` and SHA-256 checksum
to the tag's GitHub Release. A metadata mismatch fails before anything is
published; both default image tags must match as well. Validate the chart
locally with `make test-helm` before tagging. The `charts/mari`, `mari-api`, and
`mari-web` packages must be public so customers can install them anonymously.
Verify anonymous pulls after publication; the chart attached to the public
GitHub Release remains a fallback.

## Publishing images

Each release tag automatically publishes `mari-api` and `mari-web` as
multi-architecture images to `ghcr.io/marihq` using the built-in
`GITHUB_TOKEN`. The chart uses the matching immutable version tag. The manual
workflow remains available for rebuilding a release version when needed; its
version must match the chart metadata.

## Preflight

1. Create the two database keys documented in the chart README. The bundled
   PostgreSQL/pgvector StatefulSet stores its database on a persistent volume.
2. Size and back up both persistent volumes. All Iceberg, vector, audit, and
   cache files stay below `/data` on the API volume; no object store or cloud
   credentials are required.
3. Configure source and model providers in Mari after installation. Their
   credentials are application data, not Kubernetes deployment secrets.
4. Confirm that the API and web tags match the chart release and resolve from
   GHCR without authentication.
5. Set the customer-owned ingress hostname, application URL, and CORS origin.

## Rollout

Install the chart and wait for `/readyz`, then run the smoke suite. The API uses
the `Recreate` strategy because its filesystem volume is ReadWriteOnce. Keep the
web replica count at exactly one for v0.1.1.

For rollback, deploy the previous immutable image digest. Schema changes must
remain backward-compatible for at least one release. A rollback is complete only
after readiness is green and connector lag resumes falling.

Note for 0.2.0 and later: these releases include migrations 0034 and later. The
`schema-migrations` init container refuses to start a release against a
database that holds migrations it does not ship, so once 0.2.0 has run its
migrations an older API image will not come up. 0.2.0 is not downgradable
past 0.1.3 without restoring a database backup taken before the upgrade.
Take that backup before you roll out.

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
