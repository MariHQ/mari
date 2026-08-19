# Kubernetes deployment runbook

The manifests in `deploy/k8s` are a conservative baseline. Replace image names,
hostnames, storage endpoints, and secret values through your normal deployment
system; never commit a populated Secret.

## Preflight

1. Provide a managed PostgreSQL/pgvector URL in `mari-secrets`. Verify backups,
   point-in-time recovery, TLS, connection limits, and a restore rehearsal.
2. Configure the Iceberg catalog/warehouse and S3 credentials through workload
   identity. Derived vector snapshots use `MARI_VECTOR_URI`; they can be rebuilt.
3. Confirm the required LLM gateway or Ollama endpoint and its network policy.
   Enterprise gateways use `MARI_LLM_GATEWAY_URL` and the Secret-backed
   `MARI_LLM_GATEWAY_TOKEN`; optional JSON headers/metadata support tenant and
   policy routing. Select provider `gateway` in the model settings, then run the
   prompt-free `testLlmGateway` health mutation before enabling traffic.
4. Build immutable API and web image digests and replace the example tags.
5. Set the ingress hostname, CORS origin, session secret, and OAuth callbacks.

## Rollout

Apply namespace/config/secret references, then API, web, services, disruption
budgets, autoscaling, and ingress. Wait for `/readyz`, run the smoke suite, and
verify `/metrics` is scraped. Use a rolling deployment with `maxUnavailable: 0`.
Do not raise API replicas above one until scheduler leadership exists (see the
SLO document).

For rollback, deploy the previous immutable image digest. Schema changes must
remain backward-compatible for at least one release. A rollback is complete only
after readiness is green and connector lag resumes falling.

## Backup and restore

- Back up the managed transactional database with daily snapshots and PITR.
- Protect the Iceberg catalog and warehouse with bucket versioning, encryption,
  and lifecycle rules. Back up catalog metadata on the same recovery schedule.
- Published site artifacts can be regenerated, but retain release manifests.
- Derived embeddings/vector snapshots are disposable; restore source documents
  and rebuild them rather than treating them as records of truth.

Quarterly, restore into an isolated namespace, run schema initialization, compare
document/source/workflow/audit counts, rebuild retrieval snapshots, and execute
the browser smoke suite. Record recovery point and recovery time.

## Incident triage

Use the response request/correlation ID to join client reports to JSON logs. If
`/livez` passes and `/readyz` fails, inspect database reachability/pool exhaustion.
If both pass but LLM errors rise, preserve API service and investigate the model
dependency separately. If connector lag rises, inspect sync checkpoints and the
scheduler before forcing a full resync.
