-- Mari — complete database schema (idempotent).
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
  tools   int NOT NULL DEFAULT 0,
  -- createMcpServer has always written these two; the table never had them,
  -- so creating an MCP server failed with "column config does not exist" on
  -- every install. Nothing called the mutation until the console was wired,
  -- which is why it went unnoticed.
  config  jsonb NOT NULL DEFAULT '{}'::jsonb,
  token   text
);
-- Existing databases predate the two columns above.
ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS config jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS token  text;

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

-- Real start timestamp. started_label is a pre-formatted display string with
-- no year ('Jul 20, 02:57 PM'), which the console cannot format or sort on.
-- Every writer relies on the DEFAULT, so no INSERT had to change.
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS started_at timestamptz NOT NULL DEFAULT now();

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

-- ═══════════════════════════════════════════════════════════════════════
-- ▼ workspace identity, member provisioning, task deadlines, event detail
-- ═══════════════════════════════════════════════════════════════════════

-- events.detail — the label/value rows the access log shows when a row is
-- expanded. A jsonb ARRAY of {"label","value"}, not an object: jsonb does not
-- preserve object key order, and the panel renders these in the order the
-- writer chose. Rows written before this column existed keep '[]' and expand
-- to nothing, which is the truth about them.
ALTER TABLE events ADD COLUMN IF NOT EXISTS detail jsonb NOT NULL DEFAULT '[]';

-- findings.created_at / changes.created_at — when the detector wrote the row.
-- Insights counts "drift caught" and "docs fixed" over a window the reader
-- picks, and neither table recorded a time, so the whole dashboard could only
-- ever be all-time. Rows that predate this column take the migration's own
-- clock: that is the earliest moment this database can honestly vouch for
-- them, and it is why a range narrower than the deployment shows only rows
-- written since.
ALTER TABLE findings ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE changes  ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS findings_created_at_idx ON findings (created_at);
CREATE INDEX IF NOT EXISTS changes_created_at_idx ON changes (created_at);

-- tasks.due_date — a real deadline, NULL when the task has none. Most tasks
-- have none: nothing in the product assigns an SLA, so the console renders
-- them without a due date rather than with an invented one. Overdue is
-- derived (due_date < current_date AND NOT done), never stored.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS due_date date;
CREATE INDEX IF NOT EXISTS tasks_due_date_idx ON tasks (due_date) WHERE NOT done;

-- facts.verified_at — the verification date as a DATE. facts.verified is a
-- pre-formatted display string ('Jul 08, 2026', or '—' for never), which the
-- console can neither re-format nor sort on; verified_at is what the API
-- serves. Backfilled below from any parseable legacy value; the text column
-- stays for older readers but nothing new writes it.
ALTER TABLE facts ADD COLUMN IF NOT EXISTS verified_at date;
UPDATE facts SET verified_at = to_date(verified, 'Mon DD, YYYY')
  WHERE verified_at IS NULL AND verified ~ '^[A-Z][a-z]{2} [0-9]{2}, [0-9]{4}$';

-- provisioning — how people get into this workspace. Manual invites are the
-- only mechanism the server implements; github_team is empty until an admin
-- configures one (setGithubTeam), and SCIM has no endpoint at all, so its
-- flag is false and stays false until one exists.
INSERT INTO settings (key, value) VALUES
  ('provisioning', '{"manual_invites":true,"github_team":"","scim_enabled":false}')
ON CONFLICT (key) DO NOTHING;

-- ═══════════════════════════════════════════════════════════════════════
-- ▼ editorial system: style guides + rule registry, document templates,
--   glossary provenance; doc-site theme presets + generator feature switches
-- ═══════════════════════════════════════════════════════════════════════

-- ————— style guides and the deterministic rule registry —————
-- A style guide is a pack of prose rules a workspace can adopt as its default;
-- the voice layer (settings.voice, below) stacks on top of whichever one it
-- picks. Both tables are seeded with the packs Mari ships and are writable:
-- upsertStyleGuide / upsertStyleRule add a team's own.

CREATE TABLE IF NOT EXISTS style_guides (
  key         text PRIMARY KEY,
  name        text NOT NULL,
  description text NOT NULL DEFAULT '',
  tone        text NOT NULL DEFAULT 'ink',      -- ink|ok|attention|blocked|info (a color, not a claim)
  builtin     boolean NOT NULL DEFAULT false,   -- shipped with the product vs. written by this team
  sort        int NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS style_rules (
  id          text PRIMARY KEY,                 -- stable dotted id, e.g. 'clarity.passive'
  guide_key   text NOT NULL REFERENCES style_guides(key) ON DELETE CASCADE,
  family      text NOT NULL DEFAULT '',
  severity    text NOT NULL DEFAULT 'advisory', -- error|warn|advisory
  description text NOT NULL DEFAULT '',
  pack        text NOT NULL DEFAULT '',         -- per-rule code cited in findings, e.g. 'slop-01'
  suggestion  text NOT NULL DEFAULT '',
  sort        int NOT NULL DEFAULT 100
);
CREATE INDEX IF NOT EXISTS style_rules_guide_idx ON style_rules (guide_key);

-- The four packs Mari ships, and the rules in them. This is the registry the
-- Library's rules tab counts and the guides tab previews: ids, families,
-- severities and pack codes are the same ones the checker cites, so the count
-- on the tab strip and the rules a document is checked against are one list.
-- Rule detection itself (the pattern) stays with the checker — storing a second
-- copy of every regex here would be a second implementation free to drift.
INSERT INTO style_guides (key, name, description, tone, builtin, sort) VALUES
  ('ai-slop',   'AI slop',            'Strips the tells of machine-written prose: hedges, filler intensifiers, marketing adjectives.', 'attention', true, 10),
  ('clarity',   'Clarity',            'Plain sentences with a named actor. No passive voice hiding who acts, no sentence too long to scan.', 'info', true, 20),
  ('house',     'House style',        'Sentence-case headings, serial commas, and the product wording this workspace has standardised on.', 'ink', true, 30),
  ('inclusive', 'Inclusive language', 'Neutral forms of address and technical terms that carry no loaded history.', 'ok', true, 40)
ON CONFLICT (key) DO NOTHING;

INSERT INTO style_rules (id, guide_key, family, severity, description, pack, suggestion, sort) VALUES
  ('ai-slop.hedging',      'ai-slop',   'AI slop',     'warn',     'Hedging filler like “it’s worth noting” adds no information.',        'slop-01', 'Delete the hedge and state the point.',        10),
  ('ai-slop.delve',        'ai-slop',   'AI slop',     'warn',     '“Delve into” is an AI tell; prefer a plain verb.',                     'slop-04', 'Use “look at” or “examine”.',                  20),
  ('ai-slop.seamless',     'ai-slop',   'AI slop',     'advisory', 'Marketing adjective with no measurable meaning.',                      'slop-09', 'Describe what actually happens.',              30),
  ('ai-slop.leverage',     'ai-slop',   'AI slop',     'advisory', '“Leverage” as a verb; prefer “use”.',                                  'slop-12', 'Use “use”.',                                   40),
  ('clarity.passive',      'clarity',   'Clarity',     'advisory', 'Passive construction hides the actor.',                                'clarity-02', 'Name who does the action.',                 10),
  ('clarity.long-sentence','clarity',   'Clarity',     'warn',     'Sentences over 24 words are hard to scan.',                            'clarity-05', 'Split into two sentences.',                 20),
  ('clarity.very',         'clarity',   'Clarity',     'advisory', 'Intensifiers like “very” weaken the claim.',                           'clarity-08', 'Pick a stronger word.',                     30),
  ('clarity.in-order-to',  'clarity',   'Clarity',     'advisory', '“In order to” is wordy.',                                              'clarity-11', 'Use “to”.',                                 40),
  ('style.sentence-case',  'house',     'Style guide', 'warn',     'Headings should be sentence case, not Title Case.',                    'style-03', 'Lowercase all but the first word.',            10),
  ('style.oxford',         'house',     'Style guide', 'advisory', 'Missing serial comma before “and”.',                                   'style-06', 'Add the serial comma.',                        20),
  ('style.login',          'house',     'Style guide', 'warn',     'Prefer “sign in” (verb) over “login”.',                                'style-09', 'Use “sign in”.',                               30),
  ('inclusive.guys',       'inclusive', 'Inclusive',   'error',    '“You guys” excludes; use a neutral address.',                          'incl-01', 'Use “everyone” or “you all”.',                 10),
  ('inclusive.whitelist',  'inclusive', 'Inclusive',   'error',    '“Whitelist/blacklist” carry bias; prefer allow/deny.',                 'incl-03', 'Use “allowlist” / “denylist”.',                20),
  ('inclusive.sanity',     'inclusive', 'Inclusive',   'warn',     '“Sanity check” is ableist; prefer a neutral term.',                    'incl-06', 'Use “quick check” or “confidence check”.',     30)
ON CONFLICT (id) DO NOTHING;

-- settings.style_guide.default_pack — which pack this workspace adopted.
-- Empty on a fresh install: nobody has chosen yet, and the guides tab shows
-- four packs with none active rather than a preference invented for them.
-- settings.voice — the workspace's own voice layer, stacked on the pack. All
-- blank/off until someone writes one down; that is what an unwritten voice is.
INSERT INTO settings (key, value) VALUES
  ('style_guide', '{"default_pack":""}'),
  ('voice',       '{"voice":"","terms":"","banned":"","inclusive":false,"jargon":false,"sentence_case":false}')
ON CONFLICT (key) DO NOTHING;

-- ————— document templates —————
-- Scaffolds a new document can start from: a name, a category and the section
-- headings the draft opens with. Shipped set below; upsertDocumentTemplate adds
-- the team's own (standard = false).
CREATE TABLE IF NOT EXISTS document_templates (
  key         text PRIMARY KEY,
  name        text NOT NULL,
  category    text NOT NULL DEFAULT '',
  description text NOT NULL DEFAULT '',
  sections    jsonb NOT NULL DEFAULT '[]',      -- ordered array of heading strings
  icon        text NOT NULL DEFAULT 'file-text',-- clipboard|git-fork|shield-check|file-text|sprout|book-open|megaphone
  standard    boolean NOT NULL DEFAULT false,
  sort        int NOT NULL DEFAULT 100
);

INSERT INTO document_templates (key, name, category, description, sections, icon, standard, sort) VALUES
  ('runbook', 'Runbook', 'Operations', 'Step-by-step recovery procedure for one failure mode, with a rollback.',
   '["Overview","When to use this","Prerequisites","Steps","Verification","Rollback","Escalation"]', 'clipboard', true, 10),
  ('adr', 'Architecture decision record', 'Decisions', 'One decision, the context that forced it, and what it costs.',
   '["Status","Context","Decision","Consequences","Alternatives considered"]', 'git-fork', true, 20),
  ('postmortem', 'Postmortem', 'Incidents', 'Blameless write-up of an incident: what broke, for whom, and what changes.',
   '["Summary","Impact","Timeline","Root cause","Resolution","Action items","What we learned"]', 'shield-check', true, 30),
  ('rfc', 'RFC', 'Design', 'A proposal circulated for comment before the work starts.',
   '["Summary","Motivation","Proposal","Alternatives","Open questions","Rollout"]', 'file-text', true, 40),
  ('onboarding', 'Onboarding guide', 'Guides', 'What a new teammate needs in their first week, in the order they need it.',
   '["Welcome","Access and accounts","Environment setup","Your first task","Who to ask"]', 'sprout', true, 50),
  ('how-to', 'How-to guide', 'Guides', 'One reader goal, achieved in numbered steps that can be verified.',
   '["Goal","Before you start","Steps","Verify it worked","Troubleshooting"]', 'book-open', true, 60),
  ('release-notes', 'Release notes', 'Announcements', 'What shipped, what changed for the reader, and what breaks.',
   '["Highlights","Changes","Fixes","Breaking changes","Upgrade notes"]', 'megaphone', true, 70)
ON CONFLICT (key) DO NOTHING;

-- ————— glossary provenance —————
-- Where a harvested term was actually found. The glossary already recorded the
-- spelling variants it saw; evidence records the document it saw them in, so a
-- candidate can be judged against its source instead of on the model's word.
-- '' / NULL for terms someone typed in by hand — those have no source document.
ALTER TABLE glossary ADD COLUMN IF NOT EXISTS evidence text NOT NULL DEFAULT '';
ALTER TABLE glossary ADD COLUMN IF NOT EXISTS evidence_doc_id int REFERENCES documents(id) ON DELETE SET NULL;

-- ————— doc-site theme presets —————
-- The presets the site generator can actually render (sitebuilder.THEME_PRESETS
-- reads this table, falling back to its own copy when the row is missing). The
-- accent is each preset's default link/heading colour, used when a site has not
-- overridden it — so the swatch the Publish page shows is the colour the build
-- would ship.
CREATE TABLE IF NOT EXISTS site_theme_presets (
  key          text PRIMARY KEY,   -- also the value stored in sites.theme->>'theme'
  name         text NOT NULL,
  accent       text NOT NULL,
  bg           text NOT NULL,
  card         text NOT NULL,
  ink          text NOT NULL,
  line         text NOT NULL,
  display_font text NOT NULL,
  serif_font   text NOT NULL,
  sort         int NOT NULL DEFAULT 100,
  -- A preset's own dark-mode colours. NULL (the default for every preset
  -- above) means "no opinion" — dark mode falls back to sharing Starlight's
  -- colours, same as before this existed. A preset only needs these if its
  -- dark mode is a specific, deliberate palette rather than generic
  -- "whatever dark mode this generator has" — Mari Blueprint is the first:
  -- its own theme (mari-cli/theme/mari.css) ships a named "navy" dark mode,
  -- not Starlight's near-black.
  dark_bg      text,
  dark_card    text,
  dark_ink     text,
  dark_line    text
);

ALTER TABLE site_theme_presets ADD COLUMN IF NOT EXISTS dark_bg text;
ALTER TABLE site_theme_presets ADD COLUMN IF NOT EXISTS dark_card text;
ALTER TABLE site_theme_presets ADD COLUMN IF NOT EXISTS dark_ink text;
ALTER TABLE site_theme_presets ADD COLUMN IF NOT EXISTS dark_line text;

INSERT INTO site_theme_presets (key, name, accent, bg, card, ink, line, display_font, serif_font, sort,
                                 dark_bg, dark_card, dark_ink, dark_line) VALUES
  ('Mari Editorial', 'Mari Editorial', '#b04e2c', '#f6f0e3', '#fcf9f1', '#2d2a22', '#e2d8c2',
   '''Playfair Display'', Georgia, serif', '''Lora'', Georgia, serif', 10, NULL, NULL, NULL, NULL),
  ('Minimal', 'Minimal', '#1f6feb', '#ffffff', '#fafafa', '#1a1a1a', '#e5e5e5',
   '''Source Sans 3'', system-ui, sans-serif', '''Source Sans 3'', system-ui, sans-serif', 20, NULL, NULL, NULL, NULL),
  ('Material', 'Material', '#1a73e8', '#f5f5f6', '#ffffff', '#202124', '#dadce0',
   '''Source Sans 3'', Roboto, sans-serif', '''Source Sans 3'', Roboto, sans-serif', 30, NULL, NULL, NULL, NULL),
  ('Starlight', 'Starlight', '#7c9cff', '#17181c', '#1f2127', '#e7e9ee', '#33363f',
   '''Source Sans 3'', system-ui, sans-serif', '''Source Sans 3'', system-ui, sans-serif', 40, NULL, NULL, NULL, NULL),
  -- mari.guru/docs's OWN brand — not the marketing homepage's warm-sand look,
  -- but the "Brutalist Blueprint" skin the docs specifically ship today
  -- (mari-cli/theme/mari.css, served live as mari.guru/docs/theme/*.css):
  -- white paper, ink-blue #10263B, biscay-blue links, sharp corners, hairline
  -- borders. Pair with theme.radius = 0 for the sharp-corners part — presets
  -- don't carry their own radius, that's a per-site setting. Its dark mode
  -- (`html.navy` in the source theme) is a navy blue, not black.
  ('Mari Blueprint', 'Mari Blueprint', '#1e6fa8', '#ffffff', '#f7f8fa', '#10263b', '#d4d5d8',
   '''Inter'', ui-sans-serif, system-ui, sans-serif', '''Inter'', ui-sans-serif, system-ui, sans-serif', 5,
   '#0e2032', '#0a1926', '#eaf0f5', '#2d4356')
ON CONFLICT (key) DO NOTHING;

-- Backfills the row above on a database where it was already seeded before
-- dark_bg/dark_card/dark_ink/dark_line existed (ON CONFLICT DO NOTHING above
-- would otherwise leave those columns null forever on such a database).
UPDATE site_theme_presets SET dark_bg = '#0e2032', dark_card = '#0a1926',
  dark_ink = '#eaf0f5', dark_line = '#2d4356'
WHERE key = 'Mari Blueprint' AND dark_bg IS NULL;

-- ————— site generator feature switches —————
-- One row per switch the generator actually honours; sites.features holds the
-- per-site overrides ({"search": false}). A switch is only listed here once
-- sitebuilder reads it — the Publish page must not offer a toggle that changes
-- nothing about the built site.
CREATE TABLE IF NOT EXISTS site_feature_defs (
  key        text PRIMARY KEY,
  label      text NOT NULL,
  hint       text NOT NULL DEFAULT '',
  default_on boolean NOT NULL DEFAULT true,
  sort       int NOT NULL DEFAULT 100
);

ALTER TABLE sites ADD COLUMN IF NOT EXISTS features jsonb NOT NULL DEFAULT '{}';

-- Editions: sibling doc sets a reader can switch between, rendered as a toggle
-- above the sidebar search. Deliberately NOT part of theme. theme is written by
-- updateSiteTheme (user JSON) and aiCustomizeSite (LLM output), and an edition
-- carries a URL, so putting it there would let either of those paths write the
-- destination of a link on every page of a published site.
ALTER TABLE sites ADD COLUMN IF NOT EXISTS editions jsonb NOT NULL DEFAULT '[]';

INSERT INTO site_feature_defs (key, label, hint, default_on, sort) VALUES
  ('sidebar',    'Sidebar navigation',  'Lists every published page down the left of each page.',              true, 10),
  ('search',     'In-page search',      'Ships a title index with the site so readers can filter pages in the browser.', true, 20),
  ('customizer', 'Live customizer',     'Shows the Customize button that writes theme changes back to Mari.',  true, 30),
  ('provenance', 'Provenance footer',   'Footer line naming the site and the domain each page was published from.', true, 40),
  ('source_path','Source paths',        'Prints the path of the document each page was built from.',           false, 50)
ON CONFLICT (key) DO NOTHING;

-- ═══════════════════════════════════════════════════════════════════════
-- ▼ per-document claim provenance
-- ═══════════════════════════════════════════════════════════════════════

-- facts.document_id — which document a claim was mined from, as a key.
-- facts.source is a free-text label the extractor writes ('Mari scan · <title>')
-- and a label is not a key: a title can be edited, duplicated or shared between
-- documents, so matching on it would attribute one document's claims to
-- another. Doc Review's claims list stayed empty for exactly this reason.
-- NULL means "nobody recorded where this came from" — every row written before
-- this column existed, and every fact typed in by hand without a citation. It
-- is deliberately NOT backfilled from `source`: a guessed provenance reads the
-- same as a recorded one and there is no way to tell them apart afterwards.
ALTER TABLE facts ADD COLUMN IF NOT EXISTS document_id int REFERENCES documents(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS facts_document_id_idx ON facts (document_id) WHERE document_id IS NOT NULL;


-- ═══════════════════════════════════════════════════════════════════════
-- ▼ sessions expire (AUTH-3)
-- ═══════════════════════════════════════════════════════════════════════

-- Cookie sessions. Created here so a fresh database has the table with its
-- expiry column from the start; server/auth.py:ensure_schema() runs the same
-- migration for databases that already had the two-column version.
--
-- `expires_at` is the fix: created_at was recorded and never consulted, so the
-- cookie's max_age was the only lifetime and it is a client-side hint. A
-- stolen token stayed valid forever. current_user() now joins on
-- `expires_at > now()`, logout deletes the row, and changing a password
-- deletes every other row for that user — three ways to end a session that all
-- agree, instead of one that only asked the browser nicely.
CREATE TABLE IF NOT EXISTS sessions (
  token      text PRIMARY KEY,
  user_id    int NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL DEFAULT now() + interval '14 days'
);

-- Pre-existing installs: add the column, give rows an expiry derived from when
-- they were actually created (never a fresh 14 days — that would extend every
-- live session as a side effect of deploying the fix), then enforce it.
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS expires_at timestamptz;
UPDATE sessions SET expires_at = created_at + interval '14 days' WHERE expires_at IS NULL;
ALTER TABLE sessions ALTER COLUMN expires_at SET NOT NULL;
ALTER TABLE sessions ALTER COLUMN expires_at SET DEFAULT now() + interval '14 days';
CREATE INDEX IF NOT EXISTS sessions_expires_idx ON sessions (expires_at);
DELETE FROM sessions WHERE expires_at <= now();

-- The static bypass cookie ("mari-bypass") was accepted as a session value and
-- resolved to the workspace admin. It is no longer accepted anywhere; this
-- clears any row a previous version may have written under it.
DELETE FROM sessions WHERE token = 'mari-bypass';


-- ═══════════════════════════════════════════════════════════════════════
-- ▼ invitations (AUTH-2)
-- ═══════════════════════════════════════════════════════════════════════

-- Who invited this member, or '' for accounts nobody invited. POST
-- /auth/register lets someone claim a credential-less row by proving they hold
-- the address, so "credential-less" cannot be the whole test: repoaudit's
-- invite_member fix creates member rows from addresses it finds in a
-- repository (CODEOWNERS, commit authors), and those are not invitations. Only
-- a row an admin created through inviteMember carries a name here, and only
-- such a row can be claimed.
--
-- Rows that predate this column keep '' — an install upgrading mid-invite has
-- to re-issue it. Failing closed is the right side to be wrong on: the other
-- direction hands an account to whoever appears in a git log.
ALTER TABLE users ADD COLUMN IF NOT EXISTS invited_by text NOT NULL DEFAULT '';

-- `ask_answers` is gone: nothing ever wrote it, and the resolver that read
-- it could only raise on an empty table. The live question-and-answer surface
-- is `approved_answers`. Drop the vestigial table where it still exists.
DROP TABLE IF EXISTS ask_answers;


-- ═══════════════════════════════════════════════════════════════════════
-- ▼ foreign keys that ADD COLUMN IF NOT EXISTS silently skipped (MIG-1)
-- ═══════════════════════════════════════════════════════════════════════

-- `ALTER TABLE … ADD COLUMN IF NOT EXISTS … REFERENCES …` adds the constraint
-- ONLY when it adds the column. On a database that already had the column —
-- every install that ran an earlier version of this file — the whole statement
-- is skipped, column and constraint alike, and the two diverge permanently:
-- the column exists, the foreign key does not, and nothing ever notices because
-- re-running init.sql keeps skipping it.
--
-- The shape below is the fix and the pattern to copy: add the column WITHOUT
-- the reference, then add the constraint separately, guarded on its own
-- existence rather than the column's. Both halves are then independently
-- idempotent, so a database missing either one converges.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'facts_document_id_fkey') THEN
    ALTER TABLE facts ADD CONSTRAINT facts_document_id_fkey
      FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE SET NULL;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'glossary_evidence_doc_id_fkey') THEN
    ALTER TABLE glossary ADD CONSTRAINT glossary_evidence_doc_id_fkey
      FOREIGN KEY (evidence_doc_id) REFERENCES documents(id) ON DELETE SET NULL;
  END IF;
END $$;


-- ═══════════════════════════════════════════════════════════════════════
-- ▼ scanner rotation (FACT-2)
-- ═══════════════════════════════════════════════════════════════════════

-- When the fact scan picked documents with `ORDER BY updated_src DESC LIMIT 8`,
-- the two newest documents consumed the whole per-scan budget every time, so a
-- document that was never the newest was never mined — no matter how many times
-- someone ran the scan. These columns record when each scanner last READ a
-- document, and the scanners order by them (nulls first), so every document is
-- reached in turn instead of the same two forever.
--
-- Separate columns per scanner on purpose: one shared "scanned_at" would let
-- the fact scan starve the decision scan and vice versa. NULL means "no scanner
-- has read this document yet", which is the honest state of every row that
-- predates the column and every document ingested since the last scan.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS facts_scanned_at timestamptz;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS decisions_scanned_at timestamptz;
CREATE INDEX IF NOT EXISTS documents_facts_scanned_idx
  ON documents (facts_scanned_at NULLS FIRST, updated_src DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS documents_decisions_scanned_idx
  ON documents (decisions_scanned_at NULLS FIRST, updated_src DESC NULLS LAST);


-- ═══════════════════════════════════════════════════════════════════════
-- ▼ the indexes the query plans were missing
-- ═══════════════════════════════════════════════════════════════════════

-- ————— SRCH-5: approximate-nearest-neighbour indexes on both vectors —————
--
-- Neither `documents.embedding` nor `chunks.embedding` had an index, so hybrid
-- search computed a cosine distance for every row in both tables on every
-- keystroke and then threw almost all of them away.
--
-- HNSW, not IVFFlat. IVFFlat partitions the vectors it can see at build time
-- into `lists` centroids; the right `lists` is a function of the row count, and
-- an IVFFlat index built on an empty table — which is exactly what a fresh
-- install has, since init.sql runs before a single document is ingested — has
-- no useful centroids at all and must be REINDEXed once the corpus grows.
-- init.sql is the only migration surface here and it is run at startup, so an
-- index that needs a manual rebuild at an unknown future row count is an index
-- that will silently stay wrong. HNSW builds a navigable graph incrementally as
-- rows arrive, needs no row-count tuning, and gives good recall from the first
-- row to the millionth. The costs are real and accepted: slower inserts and a
-- larger index than IVFFlat. Ingestion here is a background job, not a request.
--
-- vector_cosine_ops matches the `<=>` operator the search SQL uses. An index
-- built with a different opclass is simply never used — silently.
CREATE INDEX IF NOT EXISTS documents_embedding_hnsw_idx
  ON documents USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_idx
  ON chunks USING hnsw (embedding vector_cosine_ops);

-- ————— STATS-4: the access log —————
-- `events` had nothing but its primary key, so every audit-log page sorted the
-- whole table and `auditLogTotal` counted it. This is the exact sort the
-- resolver asks for (occurred_at DESC, id DESC), so it can be read straight off
-- the index instead of sorted.
CREATE INDEX IF NOT EXISTS events_occurred_at_idx ON events (occurred_at DESC, id DESC);

-- The access log's search is `actor ILIKE %q% OR verb ILIKE %q% OR target
-- ILIKE %q%` — three unanchored patterns, which only a trigram index can serve.
-- pg_trgm is created above and `documents_trgm_idx` already depends on it, so
-- it is a hard dependency of this schema either way; the guard is here so a
-- database where the extension could not be installed still gets the rest of
-- this file rather than failing on statement one of the last section.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
    CREATE INDEX IF NOT EXISTS events_actor_trgm_idx  ON events USING gin (actor  gin_trgm_ops);
    CREATE INDEX IF NOT EXISTS events_verb_trgm_idx   ON events USING gin (verb   gin_trgm_ops);
    CREATE INDEX IF NOT EXISTS events_target_trgm_idx ON events USING gin (target gin_trgm_ops);
  ELSE
    RAISE NOTICE 'pg_trgm is not installed: the access log search will scan events sequentially';
  END IF;
END $$;

-- ————— STATS-5: inbound edges —————
-- `edges_src_dst_rel_uidx` leads with from_doc, so it serves `WHERE from_doc =
-- %s` and does nothing at all for `WHERE to_doc = %s` — the inbound half of
-- relatedDocuments, the inbound count in `lineage`, and every "what cites this"
-- query was a sequential scan of the whole edge table.
CREATE INDEX IF NOT EXISTS edges_to_doc_idx ON edges (to_doc);

-- ————— STATS-8: documents by source —————
-- `answerHarvestSources` counts documents by source three times per page load,
-- and the lineage lanes group by it.
CREATE INDEX IF NOT EXISTS documents_source_idx ON documents (source);

-- ————— supporting indexes for the joins those pages sit on —————
-- `tags` is joined by tag (not just by document) in the search boost subquery
-- and in graph_stats; its primary key leads with document_id and cannot serve
-- either. `facts.status` is counted on every Overview load.
CREATE INDEX IF NOT EXISTS tags_tag_idx ON tags (tag);
CREATE INDEX IF NOT EXISTS facts_status_idx ON facts (status);
CREATE INDEX IF NOT EXISTS workflow_runs_workflow_number_idx ON workflow_runs (workflow_id, number DESC);
