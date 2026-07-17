-- Mari Cloud — complete database schema (idempotent).
--
-- Single source of truth for the schema: safe to run any number of times,
-- via `psql -f init.sql` and at server startup (db.ensure_schema()).
-- Applied once by the docker-compose db-init service on a fresh database.
--
-- No demo/seed content: the workspace starts empty and is populated by the
-- onboarding flow and real connectors. The only rows created here are product
-- configuration defaults (tag taxonomy, settings, default graph perspectives).
--
-- Sections below were previously split across init.sql..init9.sql (schema
-- versions v1–v9); merged pre-release. Order matters — later sections ALTER
-- tables created by earlier ones — so keep them in sequence when editing.
-- ═══════════════════════════════════════════════════════════════════════
-- ▼ core knowledge tables
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS sources (
  id          serial PRIMARY KEY,
  provider    text NOT NULL UNIQUE,          -- github|slack|gdocs|notion|granola|docs
  display_name text NOT NULL,
  status      text NOT NULL DEFAULT 'active',
  stat_num    text NOT NULL DEFAULT '0',
  stat_unit   text NOT NULL DEFAULT 'items',
  bars        int[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS documents (
  id          serial PRIMARY KEY,
  source      text NOT NULL,
  external_id text NOT NULL,
  title       text NOT NULL,
  snippet     text NOT NULL DEFAULT '',
  body        text NOT NULL DEFAULT '',
  author      text NOT NULL DEFAULT '',
  author_initials text NOT NULL DEFAULT '',
  kind        text NOT NULL DEFAULT 'page',
  updated_src date,
  graph_x     real, graph_y real, graph_day int, graph_icon text, graph_meta text,
  UNIQUE (source, external_id)
);

CREATE TABLE IF NOT EXISTS tags (
  document_id int NOT NULL REFERENCES documents(id),
  tag         text NOT NULL,                 -- canonical|verified|stale|needs-review|…
  PRIMARY KEY (document_id, tag)
);

CREATE TABLE IF NOT EXISTS edges (
  id       serial PRIMARY KEY,
  from_doc int NOT NULL REFERENCES documents(id),
  to_doc   int NOT NULL REFERENCES documents(id),
  rel      text NOT NULL,                    -- content|impact|reference|co_referenced|edited_by…
  day      int NOT NULL DEFAULT 0,
  curve    real NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS facts (
  id       serial PRIMARY KEY,
  claim    text NOT NULL UNIQUE,
  source   text NOT NULL,
  owner_name text NOT NULL,
  owner_tint int NOT NULL DEFAULT 1,
  status   text NOT NULL DEFAULT 'Needs review',
  verified text NOT NULL DEFAULT '—'
);

CREATE TABLE IF NOT EXISTS tasks (
  id       serial PRIMARY KEY,
  title    text NOT NULL UNIQUE,
  assignee text NOT NULL,
  assignee_initials text NOT NULL,
  assignee_tint int NOT NULL DEFAULT 1,
  kind     text NOT NULL,                    -- stale|factcheck|approval
  kind_label text NOT NULL,
  done     boolean NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS digest_topics (
  id       serial PRIMARY KEY,
  title    text NOT NULL UNIQUE,
  summary  text NOT NULL,
  wheres   jsonb NOT NULL DEFAULT '[]',      -- [{source,label}]
  impact   jsonb NOT NULL DEFAULT '[]'       -- [{name,tone}]
);

CREATE TABLE IF NOT EXISTS ask_answers (
  id       serial PRIMARY KEY,
  question text NOT NULL UNIQUE,
  answer   text NOT NULL,
  sources  jsonb NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS events (
  id          serial PRIMARY KEY,
  actor       text NOT NULL,
  verb        text NOT NULL,
  target      text NOT NULL,
  occurred_at timestamptz NOT NULL DEFAULT now()
);

-- ═══════════════════════════════════════════════════════════════════════
-- ▼ search, people, config, ingestion, workflows, publishing, chat
-- ═══════════════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ————— search columns —————
ALTER TABLE documents ADD COLUMN IF NOT EXISTS embedding vector(768);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS search_vec tsvector
  GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || snippet || ' ' || body)) STORED;
CREATE INDEX IF NOT EXISTS documents_search_idx ON documents USING gin (search_vec);
CREATE INDEX IF NOT EXISTS documents_trgm_idx ON documents USING gin (title gin_trgm_ops);

ALTER TABLE edges ADD COLUMN IF NOT EXISTS meta jsonb NOT NULL DEFAULT '{}';

-- ————— people & access —————
CREATE TABLE IF NOT EXISTS users (
  id        serial PRIMARY KEY,
  name      text NOT NULL UNIQUE,
  initials  text NOT NULL,
  tint      int  NOT NULL DEFAULT 1,
  email     text NOT NULL DEFAULT '',
  role      text NOT NULL DEFAULT 'user',        -- admin|manager|user
  provider  text NOT NULL DEFAULT 'manual',      -- manual|github
  status    text NOT NULL DEFAULT 'active',
  joined    date NOT NULL DEFAULT '2024-04-01'
);

CREATE TABLE IF NOT EXISTS api_keys (
  id         serial PRIMARY KEY,
  name       text NOT NULL UNIQUE,
  prefix     text NOT NULL,
  scopes     text NOT NULL DEFAULT 'read',
  created_at date NOT NULL DEFAULT '2024-05-01',
  last_used  text NOT NULL DEFAULT 'never',
  revoked    boolean NOT NULL DEFAULT false
);

-- ————— knowledge config —————
CREATE TABLE IF NOT EXISTS tag_definitions (
  tag           text PRIMARY KEY,
  label         text NOT NULL,
  kind          text NOT NULL DEFAULT 'neutral',  -- pill style key
  search_weight real NOT NULL DEFAULT 1.0,
  is_default    boolean NOT NULL DEFAULT true,
  behaviors     text NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS glossary (
  id         serial PRIMARY KEY,
  term       text NOT NULL UNIQUE,
  definition text NOT NULL,
  owner_name text NOT NULL DEFAULT '',
  updated    date NOT NULL DEFAULT '2024-05-01'
);

CREATE TABLE IF NOT EXISTS settings (
  key   text PRIMARY KEY,
  value jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS mcp_servers (
  id      serial PRIMARY KEY,
  name    text NOT NULL UNIQUE,
  url     text NOT NULL,
  scope   text NOT NULL DEFAULT 'project',
  status  text NOT NULL DEFAULT 'connected',
  tools   int NOT NULL DEFAULT 0
);

-- ————— ingestion —————
CREATE TABLE IF NOT EXISTS ingest_checkpoints (
  id        serial PRIMARY KEY,
  provider  text NOT NULL,
  item      text NOT NULL,
  stage     text NOT NULL DEFAULT 'fetched',  -- fetched|parsed|chunked|embedded|indexed|edges_built
  progress  int NOT NULL DEFAULT 0,
  total     int NOT NULL DEFAULT 5,
  cursor_id text NOT NULL DEFAULT '',
  duration  text NOT NULL DEFAULT '',
  status    text NOT NULL DEFAULT 'running',  -- running|done|paused
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (provider, item)
);

CREATE TABLE IF NOT EXISTS sync_events (
  id       serial PRIMARY KEY,
  provider text NOT NULL,
  event    text NOT NULL,
  detail   text NOT NULL DEFAULT '',
  at_label text NOT NULL DEFAULT ''
);

ALTER TABLE sources ADD COLUMN IF NOT EXISTS config jsonb NOT NULL DEFAULT '{}';
ALTER TABLE sources ADD COLUMN IF NOT EXISTS docs_count int NOT NULL DEFAULT 0;
ALTER TABLE sources ADD COLUMN IF NOT EXISTS health text NOT NULL DEFAULT 'Healthy';

-- ————— review workflow on documents —————
CREATE TABLE IF NOT EXISTS findings (
  id          serial PRIMARY KEY,
  document_id int NOT NULL REFERENCES documents(id),
  kind        text NOT NULL,                 -- prose|fact|freshness
  severity    text NOT NULL DEFAULT 'warn',  -- warn|error
  text        text NOT NULL,
  note        text NOT NULL DEFAULT '',
  UNIQUE (document_id, text)
);

CREATE TABLE IF NOT EXISTS changes (
  id          serial PRIMARY KEY,
  document_id int NOT NULL REFERENCES documents(id),
  original    text NOT NULL,
  replacement text NOT NULL,
  reason      text NOT NULL DEFAULT '',
  status      text NOT NULL DEFAULT 'pending',  -- pending|accepted|rejected
  UNIQUE (document_id, original)
);

-- ————— workflows —————
CREATE TABLE IF NOT EXISTS workflows (
  id          serial PRIMARY KEY,
  name        text NOT NULL UNIQUE,
  description text NOT NULL DEFAULT '',
  color       text NOT NULL DEFAULT '#5c7a4c',
  pinned      boolean NOT NULL DEFAULT false,
  status      text NOT NULL DEFAULT 'active',
  nodes       jsonb NOT NULL DEFAULT '[]',
  config      jsonb NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS workflow_runs (
  id          serial PRIMARY KEY,
  workflow_id int NOT NULL REFERENCES workflows(id),
  number      int NOT NULL,
  status      text NOT NULL DEFAULT 'running',  -- running|passed|failed
  started_label text NOT NULL DEFAULT '',
  duration    text NOT NULL DEFAULT '',
  progress    int NOT NULL DEFAULT 0,
  stats       jsonb NOT NULL DEFAULT '{}',
  rows_data   jsonb NOT NULL DEFAULT '[]',
  UNIQUE (workflow_id, number)
);

-- ————— publishing —————
CREATE TABLE IF NOT EXISTS sites (
  id      serial PRIMARY KEY,
  name    text NOT NULL UNIQUE,
  domain  text NOT NULL,
  status  text NOT NULL DEFAULT 'draft',
  theme   jsonb NOT NULL DEFAULT '{}',
  sources jsonb NOT NULL DEFAULT '[]',
  nav     jsonb NOT NULL DEFAULT '[]',
  gates   jsonb NOT NULL DEFAULT '[]',
  docs    int NOT NULL DEFAULT 0,
  warnings int NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS releases (
  id        serial PRIMARY KEY,
  site_id   int NOT NULL REFERENCES sites(id),
  version   text NOT NULL,
  status    text NOT NULL DEFAULT 'live',    -- live|previous|rolled_back
  deployed  text NOT NULL DEFAULT '',
  docs      int NOT NULL DEFAULT 0,
  notes     text NOT NULL DEFAULT '',
  UNIQUE (site_id, version)
);

-- ————— notifications & watches —————
CREATE TABLE IF NOT EXISTS notifications (
  id         serial PRIMARY KEY,
  user_name  text NOT NULL,
  kind       text NOT NULL DEFAULT 'info',   -- info|factcheck|stale|approval|mention
  text       text NOT NULL,
  detail     text NOT NULL DEFAULT '',
  at_label   text NOT NULL DEFAULT '',
  read       boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_name, text)
);

CREATE TABLE IF NOT EXISTS watches (
  user_name   text NOT NULL,
  document_id int NOT NULL REFERENCES documents(id),
  PRIMARY KEY (user_name, document_id)
);

-- ————— chat —————
CREATE TABLE IF NOT EXISTS chat_sessions (
  id         serial PRIMARY KEY,
  title      text NOT NULL DEFAULT 'New conversation',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id         serial PRIMARY KEY,
  session_id int NOT NULL REFERENCES chat_sessions(id),
  role       text NOT NULL,                  -- user|assistant
  content    text NOT NULL,
  sources    jsonb NOT NULL DEFAULT '[]',
  created_at timestamptz NOT NULL DEFAULT now()
);

-- ————— product configuration defaults —————
-- The tag taxonomy (search weights the ranking engine reads) and workspace
-- settings. Defaults only; onboarding and admin screens overwrite them.

INSERT INTO tag_definitions (tag, label, kind, search_weight, is_default, behaviors) VALUES
  ('canonical',    'Canonical',    'canonical', 2.0, true,  'Boosts search; wins conflicts'),
  ('verified',     'Verified',     'verified',  1.5, true,  'Facts extracted are trusted'),
  ('stale',        'Stale',        'stale',     0.5, true,  'Demoted in search; drift alert'),
  ('needs-review', 'Needs review', 'factcheck', 0.8, true,  'Surfaces in review queue'),
  ('customer-facing', 'Customer facing', 'approval', 1.2, true, 'Publishable to doc sites'),
  ('internal-only',   'Internal only',   'neutral',  1.0, true, 'Excluded from doc sites'),
  ('deprecated',      'Deprecated',      'stale',    0.3, false, 'Hidden from Ask Mari answers')
ON CONFLICT (tag) DO NOTHING;

INSERT INTO settings (key, value) VALUES
  ('workspace',       '{"name":"","slug":"","plan":"","timezone":"America/Los_Angeles","language":"English (US)"}'),
  ('embedding',       '{"provider":"ollama","model":"nomic-embed-text","dims":768,"options":["openai:text-embedding-3-small","ollama:nomic-embed-text","local:bge-small-en","local:all-MiniLM-L6-v2"],"default":"openai:text-embedding-3-small"}'),
  ('llm',             '{"provider":"ollama","model":"gemma3:4b","options":["anthropic:claude-sonnet-5","openai:gpt-5.2","ollama:gemma3:4b"],"keys":{"anthropic":"","openai":""}}'),
  ('chunking',        '{"default":{"strategy":"heading","max_tokens":512,"overlap":64},"slack":{"strategy":"thread","max_tokens":768,"overlap":0}}'),
  ('digest_schedule', '{"cron":"0 9 * * MON","enabled":true,"last_run":""}')
ON CONFLICT (key) DO NOTHING;

-- ═══════════════════════════════════════════════════════════════════════
-- ▼ approved answers, decisions
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS approved_answers (
  id         serial PRIMARY KEY,
  question   text NOT NULL UNIQUE,
  answer     text NOT NULL,
  status     text NOT NULL DEFAULT 'draft',      -- draft|approved|retired
  owner_name text NOT NULL DEFAULT '',
  channels   text[] NOT NULL DEFAULT '{}',       -- slack-bot|support-widget|docs-site
  sources    jsonb NOT NULL DEFAULT '[]',        -- [{source,title}]
  served     int NOT NULL DEFAULT 0,
  spark      int[] NOT NULL DEFAULT '{}',
  updated    date NOT NULL DEFAULT '2024-05-01',
  embedding  vector(768)
);

CREATE TABLE IF NOT EXISTS decisions (
  id            serial PRIMARY KEY,
  statement     text NOT NULL UNIQUE,
  context       text NOT NULL DEFAULT '',
  status        text NOT NULL DEFAULT 'proposed', -- proposed|ratified|superseded
  source_label  text NOT NULL DEFAULT '',
  owners        text[] NOT NULL DEFAULT '{}',
  decided_on    date,
  superseded_by int REFERENCES decisions(id),
  impact_summary text NOT NULL DEFAULT '',
  impact_count  int NOT NULL DEFAULT 0
);

ALTER TABLE glossary ADD COLUMN IF NOT EXISTS candidate boolean NOT NULL DEFAULT false;
ALTER TABLE glossary ADD COLUMN IF NOT EXISTS variants text NOT NULL DEFAULT '';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS readability text NOT NULL DEFAULT '';

-- ═══════════════════════════════════════════════════════════════════════
-- ▼ lineage graph time columns + default perspectives
-- ═══════════════════════════════════════════════════════════════════════

ALTER TABLE documents ADD COLUMN IF NOT EXISTS created_src date;

UPDATE documents SET created_src = CASE
    WHEN graph_day IS NOT NULL THEN updated_src - LEAST(graph_day, 3)
    ELSE updated_src END
WHERE created_src IS NULL;

ALTER TABLE edges ADD COLUMN IF NOT EXISTS created_at date;

-- Derived edges without a day inherit the later endpoint's date.
UPDATE edges e SET created_at = CASE
    WHEN e.day > 0 THEN DATE '2024-04-29' + e.day
    ELSE GREATEST(f.updated_src, t.updated_src) END
FROM documents f, documents t
WHERE e.created_at IS NULL AND f.id = e.from_doc AND t.id = e.to_doc;

CREATE TABLE IF NOT EXISTS graph_views (
  id         serial PRIMARY KEY,
  name       text NOT NULL UNIQUE,
  state      jsonb NOT NULL DEFAULT '{}',
  created_by text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Default perspectives (§4.1) — generic lenses, not tied to any content.
-- states use the frontend URL-param names.
INSERT INTO graph_views (name, state, created_by) VALUES
  ('Stale docs', '{"lens":"stale","scope":"everything"}', 'Mari'),
  ('Untranslated customer-facing', '{"chip":"untranslated","scope":"everything"}', 'Mari'),
  ('Recent decisions', '{"rels":["derived","translates"],"scope":"everything"}', 'Mari')
ON CONFLICT (name) DO NOTHING;

-- ═══════════════════════════════════════════════════════════════════════
-- ▼ real ingestion: chunk embeddings + per-source provenance
-- ═══════════════════════════════════════════════════════════════════════

-- sources: kind distinguishes real connectors ('github') from unset rows;
-- last_sync_at is the authoritative sync timestamp (config.last_sync_at mirrors it).
ALTER TABLE sources ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT '';
ALTER TABLE sources ADD COLUMN IF NOT EXISTS last_sync_at timestamptz;

-- documents: link back to the owning source + file-level provenance.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash text NOT NULL DEFAULT '';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_path text NOT NULL DEFAULT '';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_id int;
CREATE INDEX IF NOT EXISTS documents_source_id_idx ON documents (source_id);

-- chunks: unit of embedding. content_hash (sha256) gates re-embedding.
CREATE TABLE IF NOT EXISTS chunks (
  id           serial PRIMARY KEY,
  document_id  int NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  idx          int NOT NULL,
  content      text NOT NULL,
  content_hash text NOT NULL,
  embedding    vector(768),
  UNIQUE (document_id, idx)
);
CREATE INDEX IF NOT EXISTS chunks_document_id_idx ON chunks (document_id);

-- ═══════════════════════════════════════════════════════════════════════
-- ▼ workflow triggers
-- ═══════════════════════════════════════════════════════════════════════

-- Trigger shape (jsonb): {"on": "document_changed" | "document_added" | "",
--   "source_id": int|null, "tag": str|null, "path_glob": str|null}
-- Empty "on" = manual-only (default, backward compatible).
ALTER TABLE workflows ADD COLUMN IF NOT EXISTS trigger jsonb NOT NULL DEFAULT '{}';

-- Provenance for auto-started runs, e.g. 'Triggered by: docs/setup.md updated'.
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS triggered_by text NOT NULL DEFAULT '';

-- ═══════════════════════════════════════════════════════════════════════
-- ▼ edge uniqueness for link extraction
-- ═══════════════════════════════════════════════════════════════════════

-- Dedupe any pre-existing duplicate edges, then enforce uniqueness on
-- (from_doc, to_doc, rel) so links.py can INSERT ... ON CONFLICT DO NOTHING.
DELETE FROM edges a USING edges b
  WHERE a.id > b.id AND a.from_doc = b.from_doc AND a.to_doc = b.to_doc AND a.rel = b.rel;

CREATE UNIQUE INDEX IF NOT EXISTS edges_src_dst_rel_uidx ON edges (from_doc, to_doc, rel);

-- ═══════════════════════════════════════════════════════════════════════
-- ▼ usage telemetry
-- ═══════════════════════════════════════════════════════════════════════

-- Every insightStats number derives from rows here or in real tables — never
-- a constant.
CREATE TABLE IF NOT EXISTS usage_log (
  id     serial PRIMARY KEY,
  kind   text NOT NULL,               -- search|chat_answer|site_view
  detail text NOT NULL DEFAULT '',
  at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS usage_log_kind_at_idx ON usage_log (kind, at);

-- ═══════════════════════════════════════════════════════════════════════
-- ▼ scheduled flow triggers
-- ═══════════════════════════════════════════════════════════════════════

-- workflow_runs.started_at: a real timestamp so the flow scheduler can derive
-- "last started" per workflow (started_label is a display string with no year).
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS started_at timestamptz NOT NULL DEFAULT now();
