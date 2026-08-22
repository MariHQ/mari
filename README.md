<div align="center">

# 🌿 Mari

**The product knowledge cloud — connect everything your team knows, keep it true, and put it to work.**

*Search it. Trust it. Trace it. Publish it. Ask it.*

[Quick start](#-quick-start) · [Features](#-features) · [Connectors](#-connectors) · [Architecture](#-architecture) · [Development](#-development)

</div>

---

## What is Mari?

Mari is a self-hosted **product knowledge platform**. It continuously ingests the places your team's knowledge actually lives — GitHub repos, Slack threads, wikis, tickets, docs — into one searchable, verifiable, traceable knowledge base, then puts an **agent** on top of it that can answer questions *and act*: identify drift, create review work, trigger syncs, run workflows, and walk you around the product. Connector documents remain read-only records of their source systems.

Where a search tool stops at "here are ten links," Mari keeps going:

- **Which of these claims is still true?** Facts have owners, verification status, and freshness.
- **Where did this answer come from?** Every answer cites the document, commit, or thread it came from.
- **What changed, and what did it break?** A living lineage graph ties documents, commits, PRs, and decisions together with real extracted links — structural (`#123` references, markdown links) and semantic (embedding similarity).
- **Now publish it.** Turn curated knowledge into a deployed docs site (native generator or Docusaurus) with one flow.

Everything runs on your infrastructure: Postgres for transactional state, Iceberg for durable knowledge snapshots, rebuildable MUVERA/PolarQuant retrieval artifacts on filesystem or S3, local LLMs via Ollama (with graceful degradation when they're offline), and a React front end with a hand-drawn "editorial notebook" design system.

---

## ✨ Features

### 🔌 Ingestion that stays fresh
- **14 real connectors** (see below) feeding one pipeline: fetch → chunk → content-hash → embed. Unchanged content is never re-embedded — a no-op resync of a 200-doc repo takes seconds.
- **Resumable, diff-based sync.** Cursors survive restarts; losing one is safe — sync falls back to a full tree diff by content hash and re-embeds nothing that hasn't actually changed.
- **Live updates**: GitHub webhooks trigger instant re-syncs; scheduled syncs cover the rest, on a per-source cadence you set from the Sources page rather than in env vars.
- GitHub ingestion goes beyond files: **commit messages, PR descriptions, issues, and comments** become searchable knowledge documents.

### 🔎 Hybrid search & cited answers
- Hybrid retrieval: literal keyword candidates fused with MUVERA approximate retrieval, PolarQuant packing, and exact MaxSim reranking, with project-scoped rebuildable artifacts.
- Chat answers stream with **numbered citations** back to their sources.
- Honest telemetry: usage counters ("searches", "answers served") count real events, from the day counting started.

### 🤖 The Mari agent
- An **agent dock** on every page (floating launcher, bottom right): compact stream, visible tool calls with expandable results, streaming tokens.
- It can search and read source documents, manage governed knowledge, sync sources, and **navigate the app** while the conversation stays open.
- Safety rails: source content is read-only, navigation is whitelist-validated, write tools are capability-gated, and every action lands in the audit trail.

### 🕸 Lineage you can actually read
- A single-pane Cytoscape graph of your whole knowledge ecosystem, with a time axis and as-of scrubbing.
- **Roll-up macro nodes**: hundreds of commits/PRs collapse into per-repo groups ("229 commits · MariHQ/web") that expand on click, ranked by connectivity. Aggregated edges show link volume ("references ×52").
- Link extraction is real: `#123` cross-references (PR ↔ issue ↔ commit), resolved markdown links between pages, and capped embedding-similarity edges.

### ⚙️ Scheduled syncs and promoted workflows
Source syncs and the weekly digest run on a schedule you control from the Sources page, and a reviewed agent trajectory can be promoted into a workflow that runs the same way. Steps execute server-side (fetch → refine → fact-check → tag → approve → notify) and every run carries its provenance: *"Triggered by: docs/auth.md updated"*, *"Scheduled · every 10 min"*. There is no pipeline editor: workflows are promoted from observed work, not drawn by hand.

### ✅ Facts, decisions & answers
- **Facts**: verifiable claims with owners, sources, and verification lifecycle.
- **Decisions**: a ratified ledger (proposed → ratified → superseded) with impact analysis.
- **Approved answers**: canonical Q&A served to chat/search, with an **LLM harvest wizard** that mines new questions from your sources.
- **Glossary**: shared definitions in the Library, seedable by an LLM that reads your actual documents (grounded — terms must appear in the text).

### 🧑‍💻 Codebase intelligence
- **Repo audit**: clones your connected repos (token never persisted to disk) and scans for documentation drift — coverage gaps, unmapped commit authors, stale localization — with one-click fixes that ingest the missing docs. An unmapped commit author becomes a suggestion or a mapping onto an existing member — the audit never creates an account, since a commit address is evidence that someone committed, not that they belong in your workspace.

### 🚀 Destinations
- Deploy a project-scoped interactive **Knowledge chat** destination with streamed answers and evidence links.
- Expose the same corpus through MCP, the scoped Search API, Slack, and GitHub bots.
- Building your own bot or agent on top of Mari? Use the MCP server or the `mari-components` packages directly; `/api/search` is a convenience endpoint, not the integration surface.

### 💬 Bots (self-serve)
- **Slack bot**: copy a generated app manifest, paste your bot token, verify — then @mention Mari in any channel and it answers from your knowledge base.
- **GitHub webhook**: guided setup with generated secret and delivery verification. A secret is **required** — with none configured (`MARI_GITHUB_WEBHOOK_SECRET`, or one generated in Settings → Bots) every delivery is rejected with a 401 that says so, because an unverified webhook is an open endpoint for driving syncs.

### 🎨 Bring your own branding
- The entire UI is driven by design tokens; workspace branding (accent palette, logo, display/body fonts) re-skins every component with **zero page changes**.
- A living design system, exhibited at **Settings → Design & brand** — one Card, one Button, one Chip family across the whole product.

### 🔐 Auth & workspace
- Email/password (scrypt), GitHub & Google OAuth, first-run setup token, session cookies.
- **Invite-only by default.** An account is all the GraphQL surface asks for, so only people an admin invited can register — open sign-up is a deliberate switch (`MARI_AUTH_REGISTRATION`).
- **Sessions expire** (14 days by default, `auth.session_days`; a demo-bypass session gets 12 hours). Expired rows are deleted, not just ignored.
- Three tiers — admin, manager, user — enforced on every mutation, and the audit log records the person who actually made the request.
- Members, roles, API keys (one-time reveal), full audit log.

---

## 🔌 Connectors

Every connector listed is real — genuine API client, honest credential validation, incremental sync through the shared embedding pipeline. No "coming soon" tiles.

| | | | |
|---|---|---|---|
| **GitHub** (files, commits, PRs, issues, comments) | **Slack** (channel history) | **Website** (same-origin crawler, sitemap-aware) | **File upload** (markdown/text) |
| **Notion** | **Google Drive** | **Confluence** | **Jira** |
| **Linear** | **Zendesk** (help center) | **Asana** | **Trello** |
| **Airtable** | **Dropbox** | | |

Connecting is self-serve: pick a provider, fill its credential fields, **Test connection** (vendor errors surfaced verbatim), connect — then watch the live sync progress. Every connected source automatically gets a scheduled sync flow.

---

## 🚀 Quick start

### Docker (recommended)

```sh
# --recurse-submodules is required: the console's component library is a submodule.
git clone --recurse-submodules <this repo> && cd mari
cp .env.example .env          # every value optional — defaults just work
docker compose up --build
```

Open **http://localhost:8080**. On a fresh installation, the setup page creates
the first owner and workspace directly; Postgres serializes the claim so only
one first-owner request can succeed. The onboarding wizard then takes over —
connect a source, pick a style guide, and seed your glossary.

After that the workspace is **invite-only**: admins invite members from Settings → Members, and only an invited address can register.

**Local model configuration:** generation can use Ollama or an OpenAI-compatible gateway; derived embeddings can use Sentence Transformers locally. Provider selection is explicit and unavailable providers surface an error instead of silently changing behavior.

### Testing

```sh
npm --prefix web install
(cd web && npx playwright install chromium)
make test                    # server, web contract/smoke, and Playwright
make test-live-ollama        # real local Ollama generation + embedding
make test-integration        # production images + Postgres + Iceberg + MinIO + Ollama
```

`make test-integration` is the assembled-system gate used in CI. It starts the
same API and nginx images shipped by the deployment, applies the schema to a
fresh pgvector/Postgres database, seeds one scoped project, persists Iceberg
state and durable session/webhook control records in Postgres, mirrors a real
MUVERA/PolarQuant generation into MinIO, and calls real Ollama embedding and
generation models. Playwright then signs in through the explicit development
bypass and verifies health/security headers, real search results, durable
Review writes, project identity, audit data, and a created/deployed interactive
knowledge chat that answers from indexed evidence. The stack is isolated
and deleted after the run; no third-party credentials are used.

Credential-gated sandbox connector and bot checks are documented in
[`docs/workflow-test-matrix.md`](docs/workflow-test-matrix.md). They are never
run implicitly because they create sources and workflow runs.

### Configuration

Everything is env-driven (`.env.example` documents the full list; env overrides `mari.toml` — see `server/mari_server/settings.py`):

| Variable | Purpose |
|---|---|
| `MARI_DB` | Point at managed Postgres (pgvector required) |
| `MARI_GITHUB_TOKEN` | Token for GitHub ingestion |
| `MARI_GITHUB_CLIENT_ID` / `_SECRET` | GitHub OAuth sign-in |
| `MARI_GOOGLE_CLIENT_ID` / `_SECRET` | Google OAuth sign-in |
| `MARI_GITHUB_WEBHOOK_SECRET` | Webhook HMAC verification |
| `MARI_OLLAMA_HOST` | ollama endpoint |
| `MARI_LLM_GATEWAY_URL` / `_TOKEN` | OpenAI-compatible enterprise gateway endpoint and Secret-backed credential |
| `MARI_LLM_GATEWAY_HEADERS` / `_METADATA` | Optional JSON routing headers and request metadata for the gateway |
| `MARI_LLM_GATEWAY_MODEL_HEADER` | Optional header name that receives the selected model ID |
| `MARI_LLM_GATEWAY_RETRIES` | Bounded retry count for gateway 429/5xx/timeouts (0–5, default 2) |
| `MARI_VECTOR_URI` | Rebuildable MUVERA/PolarQuant vector snapshots; local path or `s3://bucket/prefix` |
| `MARI_VECTOR_FLUSH_SECONDS` | Debounce interval for flushing changed derived vectors (default 30 seconds) |
| `MARI_ICEBERG_WAREHOUSE` | Iceberg warehouse location; local filesystem by default, S3 in production |
| `MARI_ICEBERG_CATALOG` | Named PyIceberg REST/Glue catalog for shared multi-writer deployments |
| `MARI_S3_BUCKET` | Object storage for backups and derived artifacts |
| `MARI_AUTH_BYPASS` | One-click demo login, off unless you set it to `true`. It signs anyone who can reach the port in as the workspace admin, with no credential — turn it on only for throwaway demo instances. The server logs a warning at startup while it is on |
| `MARI_AUTH_REGISTRATION` | Open sign-up (default off — the workspace is invite-only). Invited people can always register whether or not this is set |
| `MARI_CRAWL_ALLOW_LOOPBACK` | Allow the website connector to crawl localhost (dev only) |

### Desktop app

Mari is also available as a self-contained Electron app. The installer
includes the existing React console, FastAPI service, and a private local
PostgreSQL + pgvector database. Launching the app starts the whole local stack;
closing it stops the services, while the workspace data stays on the device.
Docker, Python, and a separately deployed Mari server are not required.

The browser/server deployment remains available independently through Docker.
Both distributions run the same UI and API; the desktop build simply supervises
its own local copies. Ollama remains optional for local model features.

To run or package the desktop client from source:

```sh
cd desktop
npm install
npm run prepare:resources
python -m pip install -r ../server/requirements.txt pyinstaller
npm run build:api
(cd ../web && npm run build)
npm start       # development
npm run dist    # self-contained installer for the current platform
```

---

## 🏗 Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Web (Vite + React + TS)                                        │
│  design-system components · agent dock · Cytoscape lineage      │
└───────────────┬─────────────────────────────────────────────────┘
                │  GraphQL (/graphql) · SSE (/chat, /agent/chat)
                │  REST (/connectors, /bots, /onboard, /webhooks)
┌───────────────┴─────────────────────────────────────────────────┐
│  API (FastAPI + Strawberry GraphQL)                             │
│                                                                 │
│  api/ → application/ → domain ports                            │
│                 ↓                                               │
│  infrastructure/ (Postgres · Iceberg · providers · models)     │
│  connectors → canonical document versions → query projection   │
│  bots/webhooks → durable inbox → connector reconciliation      │
└───────┬────────────────────────────────────┬────────────────────┘
        │                                    │
┌───────┴───────────────┐  ┌──────────────┐  ┌──────────────────┐
│ Postgres control/read │  │ Iceberg docs │  │ derived vectors  │
│ state + projections   │  │ + versions   │  │ filesystem / S3  │
└───────────────────────┘  └──────────────┘  └──────────────────┘
```

Design principles:

- **Explicit storage ownership** — Iceberg is canonical for document content and immutable versions; Postgres owns transactional control state and query projections; vector snapshots are derived and rebuildable.
- **One ingestion pipeline** — every connector feeds the same version → projection → chunk → embed path, so incremental sync semantics are identical everywhere.
- **Honest by construction** — no canned data in the UI, no fake integrations, metrics count real events, and failures surface verbatim.
- **Explicit model behavior** — configured providers are used as configured; model failures are observable and never masquerade as another implementation.

---

## 🛠 Development

```sh
# 1. Postgres (local, pgvector available)
createdb mari_cloud
for f in server/init*.sql; do psql mari_cloud -f "$f"; done   # idempotent

# 2. API — http://localhost:8000 (/graphql, /healthz)
cd server
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app:app --reload --port 8000

# 3. Web — http://localhost:5173 (proxies API routes to :8000)
git submodule update --init   # once: pulls the @mari-design/components library
cd web
npm install
npm run dev
```

Useful commands in `web/`:

```sh
npm run typecheck # tsc over src/
npm run smoke     # server-render the adapted pages from mock API responses
npm run check     # typecheck + smoke
npm run build     # typecheck + production build
```

### The console is a thin app over a shared component library

`web/` holds no page code. Every screen — layout, states, copy — comes from
**[mari-design](https://github.com/MariHQ/mari-design)**, pinned here as the
`vendor/mari-design` submodule and imported as `@mari-design/components`:

```sh
git clone --recurse-submodules …     # or: git submodule update --init
```

The library's pages are pure presenters: each takes
`{ data, loading, error, mobile }` and renders exactly what it is handed,
with no demo content of its own. So the app is three things:

| | |
|---|---|
| `src/App.tsx` | routes over the library's `PAGES` registry |
| `src/lib/` | GraphQL client (`api.ts`) and session context (`auth.tsx`) |
| `src/data/<page>.ts` | **the actual work** — one GraphQL query plus a mapper onto that page's exported `XxxData` type |

Adding a screen means adding it to the library, then writing its adapter.
Read `src/data/overview.ts` first; it is the worked reference.

Two rules follow from the pages being pure presenters:

- **Visual changes belong in mari-design, not here.** There is nothing in
  `web/` to restyle.
- **Never invent data in a mapper.** If a page needs a field the API has no
  source for, add the field to the backend returning a real — possibly
  empty — result. A mapper that fabricates a value makes "the query failed"
  indistinguishable from "there is nothing", and ships a number no one can
  trace.

Want to contribute? Read [CONTRIBUTING.md](CONTRIBUTING.md) — commits must be signed off (`git commit -s`), which is how you agree to the [CLA](CLA.md).

Deeper docs: [DESIGN.md](DESIGN.md) (product design), [LINEAGE-DESIGN.md](LINEAGE-DESIGN.md), and the frozen integration contracts (`*-CONTRACT.md`).

---

## 📦 Deploying

- **docker compose** (above) — Postgres + API + nginx-served web.
- **Managed Postgres** — set `MARI_DB`, run `server/init*.sql` once, drop the bundled `db` service.
- **AWS Lambda** — the API container can also serve the compiled web app (`MARI_STATIC_DIR`); see `deploy/lambda/`. `cloud.mari.guru` runs from the single `mari-cloud-prod` CloudFormation stack (Lambda + HTTP API + ACM certificate + custom domain + DNS), where the image tag is a stack **parameter**. Release with `./deploy/lambda/deploy.sh`, which builds, pushes and then updates the *stack*. Do not release with `aws lambda update-function-code`: the site picks the image up, but the stack parameter still names the old tag, so the next stack update reverts production.

---

## 📄 License

Mari is licensed under the [Apache License 2.0](LICENSE.md). You can use, modify, and
redistribute it, including commercially, as long as you keep the copyright and license notices and
state what you changed. It carries an explicit patent grant.

Copyright © 2026 Eric Disque and Daniel Henneberger. See [NOTICE](NOTICE).

Contributions are covered by a lightweight [Contributor License Agreement](CLA.md): you keep your
copyright, and your sign-off licenses the contribution to the project. Every commit needs a
`Signed-off-by` line, which `git commit -s` adds for you. Details in [CONTRIBUTING.md](CONTRIBUTING.md).

The `vendor/mari-design` submodule is a separate repository under its own terms.

---

<div align="center">

Built with FastAPI · Strawberry GraphQL · Postgres + pgvector · React · Cytoscape.js · Radix UI · ollama

</div>
