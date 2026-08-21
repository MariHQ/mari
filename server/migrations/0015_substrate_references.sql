CREATE TABLE IF NOT EXISTS substrate_documents (
  id bigserial PRIMARY KEY,
  project_id bigint NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  substrate text NOT NULL,
  external_id text NOT NULL,
  title text NOT NULL,
  excerpt text NOT NULL DEFAULT '',
  source text NOT NULL DEFAULT '',
  url text NOT NULL DEFAULT '',
  updated_at timestamptz,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  readability text NOT NULL DEFAULT '',
  graph_x real,
  graph_y real,
  facts_scanned_at timestamptz,
  decisions_scanned_at timestamptz,
  observed_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (project_id, substrate, external_id)
);

CREATE INDEX IF NOT EXISTS substrate_documents_project_observed_idx
  ON substrate_documents(project_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS substrate_document_tags (
  project_id bigint NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  document_id bigint NOT NULL REFERENCES substrate_documents(id) ON DELETE CASCADE,
  tag text NOT NULL,
  PRIMARY KEY (project_id, document_id, tag)
);

CREATE TABLE IF NOT EXISTS substrate_document_watches (
  project_id bigint NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  document_id bigint NOT NULL REFERENCES substrate_documents(id) ON DELETE CASCADE,
  user_name text NOT NULL,
  PRIMARY KEY (project_id, document_id, user_name)
);

CREATE TABLE IF NOT EXISTS substrate_findings (
  id bigserial PRIMARY KEY,
  project_id bigint NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  document_id bigint NOT NULL REFERENCES substrate_documents(id) ON DELETE CASCADE,
  kind text NOT NULL DEFAULT 'fact-check',
  severity text NOT NULL DEFAULT 'warning',
  text text NOT NULL,
  note text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE facts ADD COLUMN IF NOT EXISTS substrate_document_id bigint
  REFERENCES substrate_documents(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS facts_substrate_document_idx
  ON facts(project_id, substrate_document_id) WHERE substrate_document_id IS NOT NULL;

ALTER TABLE glossary ADD COLUMN IF NOT EXISTS substrate_document_id bigint
  REFERENCES substrate_documents(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS substrate_sources (
  id bigserial PRIMARY KEY,
  project_id bigint NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  substrate text NOT NULL,
  external_id text NOT NULL,
  name text NOT NULL,
  kind text NOT NULL,
  status text NOT NULL,
  credential_id text NOT NULL DEFAULT '',
  document_count integer,
  last_run_at timestamptz,
  error text NOT NULL DEFAULT '',
  configuration jsonb NOT NULL DEFAULT '{}'::jsonb,
  observed_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (project_id, substrate, external_id)
);

CREATE INDEX IF NOT EXISTS substrate_sources_project_idx
  ON substrate_sources(project_id, id);
