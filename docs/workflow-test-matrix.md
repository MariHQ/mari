# Workflow assurance matrix

This file separates executable assurance from architecture work. A green mock
browser test proves the UI-to-transport contract, not that a third-party tenant
accepted credentials. Run the deterministic server, component, and Playwright
suites with `make test`; run the real local-model probe with
`make test-live-ollama` while Ollama is available.

The production-like gate is `make test-integration`. Unlike the normal browser
suite, it uses no mocked GraphQL or REST handlers: nginx, FastAPI, Postgres,
Iceberg with a PostgreSQL catalog, MinIO, and Ollama all run as real services. Its
GitHub Actions workflow is `.github/workflows/integration-stack.yml`.

| Burndown area | Browser + server assurance | Status / remaining production check |
| --- | --- | --- |
| Demo → OSS migration | Every console route loads under a shared landmark/accessibility/overflow contract on desktop and mobile; Firefox and WebKit smoke every route; OSS messaging and Ollama settings are asserted | **External check:** compare the deployed demo artifact, environment, migrations, and routing with this revision |
| Confluence polling | Browser credential wizard validates, connects, and schedules a poll; server covers ordered pagination checkpoints, restrictions, cap safety, retry, deletion safety, and cursor filtering | **Credential canary:** `make test-live-connectors` waits for a completed sandbox sync with persisted documents/chunks. The original list said “confluent”; this is **Confluence**, not Kafka |
| Slack polling | Browser validates and connects; server covers day-safe incremental polling, edit lookback, replies, cap safety, ACL metadata, and deleted-day reconciliation | **Credential canary:** `make test-live-connectors` requires a completed sandbox sync |
| Google Docs/Drive polling | Browser validates and connects; server covers Docs export, full pagination, Changes API tombstones, native cursors, ACL metadata, and token refresh | **Credential canary:** `make test-live-connectors` requires a completed sandbox sync |
| GitHub polling | Browser validates/connects and exercises incremental/full polls; server covers files/issues/commits, truncation fallback, deletion, retry, and non-destructive full resync | **Credential canary:** `make test-live-connectors` requires a completed sandbox sync |
| Slack bot | Browser performs project-scoped setup; a fake Slack HTTP service proves `auth.test`, signed mention/DM routing, ACL filtering, deduplication, and real `chat.postMessage` | **Credential canary:** live suite sends a signed event and verifies the actual Slack reply/status update |
| GitHub webhook/bot | Browser setup saves a generated secret and renders observed delivery; server covers rotating HMAC secrets | **External check:** install a webhook on a sandbox repo and send a real delivery to a publicly reachable deployment |
| MCP | Browser creates, reveals the one-time token, configures, and health-checks a server; server covers bearer/slug auth and JSON-RPC initialize/ping/tools | **Live check ready:** deployed URL and an independent MCP client |
| Workflows / queue | Server covers trigger filters, bounded execution, retry, checkpoint/approval semantics, per-user run dismissals, and resuming in-flight runs across workflow upgrades; the pipeline editor is gone, so runs start from scheduled syncs, promoted workflows, or the Scheduled tasks page (pause/resume, cadence, run now, remove), which the browser suite covers | **Not production-scale:** queue is process-local; no durable cross-instance lease/recovery yet |
| LLM insights / facts | Browser scans, configures scan budgets/schedule, reviews staged candidates, verifies, captures, and renders write errors; server covers Ollama contracts, JSON extraction, provenance, contradictions, cited bot answers, the fact-intelligence schema, staged candidate review, per-stage LLM budgets, adjudication modes, embedding-space impact clustering, and the versioned chunk/fact embedding stores with startup refresh of stale profiles | **External check:** fixed quality/eval corpus and acceptance thresholds, especially for AI-adjudicated review |
| MUVERA + polarquant | Unit tests cover asymmetric MUVERA FDE generation, deterministic PolarQuant packing, approximate scan, exact MaxSim reranking, and filesystem snapshots; browser search reaches the fused retrieval path | **Implemented:** run a fixed production-representative recall/latency benchmark before choosing final projection and rerank limits |
| Lineage | Browser renders server-grounded nodes, opens detail, and proves a 2,000-node response is reduced to a ranked 35-node viewport with an omission count on desktop and mobile | **Partial:** tuning settings requested in the burndown do not exist yet |
| Agent trajectories | Browser covers progressive disclosure, taxonomy filters, URL pagination, errors, and a bounded 5,000-row archive; Ollama-backed harvesting is exercised through the server | **Implemented:** production evaluation still needs a privacy and taxonomy review against real Rippling traces |
| High-volume UI | Tasks (1,500), answers (1,200), facts (1,000), members (700), trajectories (5,000), and lineage (2,000 nodes) assert bounded DOM pages and no viewport overflow | Complete for these highest-growth surfaces; add equivalent fixtures whenever a new unbounded collection ships |
| Automated approval | Unified Review projects facts, decisions, answers, findings, changes, tasks, and waiting workflows; deterministic policy tests cover thresholds, trusted evidence, separation of duties, dry-run, audit reason, and replay | **Implemented conservatively:** absent/ambiguous signals remain manual by design |
| Enterprise identity | Browser member workflows plus server tests cover generic OIDC PKCE/JWK verification, immutable identity linking, group-to-project roles, and SCIM Users/Groups deprovisioning | **Integration check:** configure Rippling's issuer/client/group mapping and run its sandbox conformance flow |
| Deployment / scheduled tasks | Production images, health probes, graceful lifecycle, K8s manifests/PDB/HPA, restore drill, restart/failure injection, and 1k-request load gate run in `make test-integration` | **Deployment choice remains operator-owned:** the bundled scheduler intentionally keeps the API at one replica unless replaced by an external scheduler |
| LLM gateway | Ollama remains the default; OpenAI-compatible gateway generation/embedding/streaming, routing headers, masking, retry, health, telemetry, settings UI, and failure behavior are tested | **Integration check:** supply the enterprise gateway URL/token/model routing contract |
| Search/chat publish targets | Destinations provides deployable interactive Knowledge chat; production CI creates it, deploys it, asks a real Ollama-backed question, verifies a source citation, and follows the evidence link. Scoped `/api/search` and MCP cover machine consumers | **Implemented:** external hosting/domain policy is deployment-specific |
| Claude-plugin messaging | Browser asserts standard MCP instructions and absence of Claude-plugin promotion on primary surfaces | Complete for current primary browser messaging; keep any optional integration outside the main path |

## Credential-gated browser checks

Live checks are deliberately excluded from the default suite. They mutate a
sandbox workspace and must be enabled explicitly; live traces and videos are
disabled to avoid recording secrets.

```sh
MARI_E2E_LIVE=1 \
MARI_E2E_EXTERNAL_SERVER=1 \
MARI_E2E_BASE_URL=https://sandbox.example.com \
MARI_E2E_MUTATIONS=1 \
npm --prefix web run e2e:live
```

The live spec reads these only from the environment:

- Login: `MARI_E2E_EMAIL`, `MARI_E2E_PASSWORD` (not needed with auth bypass).
- Confluence: `MARI_E2E_CONFLUENCE_SITE_URL`, `MARI_E2E_CONFLUENCE_EMAIL`, `MARI_E2E_CONFLUENCE_API_TOKEN`.
- Slack polling/bot: `MARI_E2E_SLACK_BOT_TOKEN`, `MARI_E2E_SLACK_CHANNELS`, `MARI_E2E_SLACK_SIGNING_SECRET`.
- Google Drive: `MARI_E2E_GDRIVE_ACCESS_TOKEN`, optionally `MARI_E2E_GDRIVE_FOLDER_ID`.
- GitHub: `MARI_E2E_GITHUB_TOKEN`, `MARI_E2E_GITHUB_REPO`.

## Architecture status not disguised as tests

- Scheduled workflow execution is intentionally process-local and bounded; the
  supplied K8s deployment keeps one scheduler-bearing API replica. Connector
  checkpoints, workflow state, sessions, webhook deduplication, and review/audit
  records are durable in Postgres. Move execution to an external scheduler only
  when the selected deployment platform requires horizontal workers.
- Iceberg storage primitives provide a typed mutation journal and snapshot time
  travel, with catalog and application transactions sharing the managed
  PostgreSQL recovery boundary. Derived vector artifacts remain rebuildable.
- Derived MUVERA/PolarQuant embeddings are deliberately outside the canonical
  store and flush atomically to filesystem or S3. Continue measuring recall@k,
  nDCG, latency, storage amplification, and rerank cost against fixed corpora;
  every result retains document/chunk identity for lineage.
- Lineage exposes bounded graph modes and tuning controls; production defaults
  should still be calibrated against the target corpus and user research.
- Automated approval intentionally auto-applies only deterministic, policy-safe
  outcomes backed by sufficient trusted evidence; conflicts and low-confidence
  items stay in Unified Review.
- OIDC/SCIM, Kubernetes, the OpenAI-compatible gateway, scoped Search API, MCP,
  and interactive Knowledge chat are implemented. Their final environment
  values and vendor sandbox certification require the customer's real contracts.
