# Workflow assurance matrix

This file separates executable assurance from architecture work. A green mock
browser test proves the UI-to-transport contract, not that a third-party tenant
accepted credentials. Run the deterministic server, component, and Playwright
suites with `make test`; run the real local-model probe with
`make test-live-ollama` while Ollama is available.

| Burndown area | Browser + server assurance | Status / remaining production check |
| --- | --- | --- |
| Demo → OSS migration | Every console route loads under a shared landmark/accessibility/overflow contract on desktop and mobile; Firefox and WebKit smoke every route; OSS messaging and Ollama settings are asserted | **External check:** compare the deployed demo artifact, environment, migrations, and routing with this revision |
| Confluence polling | Browser credential wizard validates, connects, and schedules a poll; server covers auth failures, body conversion, and cursor filtering | **Live check ready:** sandbox tenant credentials. The original list said “confluent”; tests assume **Confluence**, not Confluent Kafka |
| Slack polling | Browser validates and connects; server covers day-safe incremental polling and bot filtering | **Live check ready:** sandbox bot token and channels |
| Google Docs/Drive polling | Browser validates and connects; server covers pagination, Docs export, text download, and cursor escaping | **Live check ready:** OAuth token/folder plus refresh-token lifecycle; bearer tokens are short-lived |
| GitHub polling | Browser validates/connects and exercises incremental/full polls; server covers pagination caps and scoped token reset | **Live check ready:** sandbox token and repository |
| Slack bot | Browser saves masked secrets and invokes `auth.test`; server covers signing, mention/DM answering, citations, and posting | **Live check ready:** Slack app install and an actual Events API delivery |
| GitHub webhook/bot | Browser setup saves a generated secret and renders observed delivery; server covers rotating HMAC secrets | **External check:** install a webhook on a sandbox repo and send a real delivery to a publicly reachable deployment |
| MCP | Browser creates, reveals the one-time token, configures, and health-checks a server; server covers bearer/slug auth and JSON-RPC initialize/ping/tools | **Live check ready:** deployed URL and an independent MCP client |
| Workflows / queue | Browser runs, dry-runs, pauses, creates, and follows a flow into its editor; server covers trigger filters, bounded execution, retry, checkpoint/approval semantics | **Not production-scale:** queue is process-local; no durable cross-instance lease/recovery yet |
| LLM insights / facts | Browser scans, verifies, captures, and renders write errors; server covers Ollama contracts, width, JSON extraction, provenance, contradictions, and cited bot answers | **External check:** fixed quality/eval corpus and acceptance thresholds |
| MUVERA + polarquant | Unit tests cover asymmetric MUVERA FDE generation, deterministic PolarQuant packing, approximate scan, exact MaxSim reranking, and filesystem snapshots; browser search reaches the fused retrieval path | **Implemented:** run a fixed production-representative recall/latency benchmark before choosing final projection and rerank limits |
| Lineage | Browser renders server-grounded nodes, opens detail, and proves a 2,000-node response is reduced to a ranked 35-node viewport with an omission count on desktop and mobile | **Partial:** tuning settings requested in the burndown do not exist yet |
| Agent trajectories | Browser covers progressive disclosure, taxonomy filters, URL pagination, errors, and a bounded 5,000-row archive; Ollama-backed harvesting is exercised through the server | **Implemented:** production evaluation still needs a privacy and taxonomy review against real Rippling traces |
| High-volume UI | Tasks (1,500), answers (1,200), facts (1,000), members (700), trajectories (5,000), and lineage (2,000 nodes) assert bounded DOM pages and no viewport overflow | Complete for these highest-growth surfaces; add equivalent fixtures whenever a new unbounded collection ships |
| Automated fact approval | Manual verification, contradiction review, and workflow approval are tested | **Not implemented:** there is no confidence/policy auto-approval engine |
| Rippling user management | Browser invite and GitHub-team provisioning are tested; current SSO state is rendered | **Partial:** Rippling IdP contract is unknown and SCIM endpoint is explicitly unavailable |
| Deployment / scheduled tasks | Browser saves the S3 publish target and deploys a site | **Partial:** no selected Rippling platform contract, k8s manifests, or production scheduler design |
| LLM gateway | Direct Ollama execution is tested | **Not implemented:** no gateway adapter/configuration exists |
| Glean-like search publish target | Knowledge search is browser-tested and URL-addressable | **Not implemented:** search is not available as a Publish target/API contract |
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

- The current `ThreadPoolExecutor` provides backpressure only inside one API
  process. For a large deployment, use a durable Postgres-backed queue first
  (transactional enqueue, `FOR UPDATE SKIP LOCKED`, leases, attempts, idempotency
  keys), then allow a managed queue adapter if Rippling's deployment platform
  already standardizes on one.
- Iceberg storage primitives now provide a typed mutation journal and snapshot
  time travel. The remaining migration step is to materialize request-time SQL
  in embedded DuckDB, import the current Postgres corpus, and then remove the
  Postgres runtime and deployment dependency.
- Derived MUVERA/PolarQuant embeddings are deliberately outside the canonical
  store and flush atomically to filesystem or S3. Continue measuring recall@k,
  nDCG, latency, storage amplification, and rerank cost against fixed corpora;
  every result retains document/chunk identity for lineage.
- Lineage tuning should be workspace settings with validated bounds (edge-type
  weights, similarity threshold, recency decay, collapse threshold, max nodes),
  plus a reset-to-default action and saved views.
- Automated fact approval should begin with policy rules and confidence bands:
  auto-approve only claims backed by multiple current authoritative sources;
  queue conflicts, single-source claims, and high-impact policy claims.
- User-management, Kubernetes/scheduled-job design, and LLM gateway support need
  Rippling's identity provider, deployment platform, and gateway contracts before
  implementation. Search-as-a-publish-target likewise needs an API contract,
  auth/scoping model, and freshness SLO.
