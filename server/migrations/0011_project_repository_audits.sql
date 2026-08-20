CREATE TABLE IF NOT EXISTS audit_runs (
    id serial PRIMARY KEY,
    project_id bigint REFERENCES projects(id) ON DELETE CASCADE,
    provider text NOT NULL DEFAULT 'github',
    repo text NOT NULL DEFAULT '',
    findings int NOT NULL DEFAULT 0,
    fixed int NOT NULL DEFAULT 0,
    ran_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_findings (
    id serial PRIMARY KEY,
    project_id bigint REFERENCES projects(id) ON DELETE CASCADE,
    run_id int NOT NULL REFERENCES audit_runs(id) ON DELETE CASCADE,
    kind text NOT NULL,
    title text NOT NULL,
    detail text NOT NULL DEFAULT '',
    fix_action text NOT NULL DEFAULT '',
    fix_payload jsonb NOT NULL DEFAULT '{}',
    status text NOT NULL DEFAULT 'open',
    UNIQUE (run_id, kind, title)
);

CREATE TABLE IF NOT EXISTS audit_author_map (
    id serial PRIMARY KEY,
    project_id bigint REFERENCES projects(id) ON DELETE CASCADE,
    email text NOT NULL,
    git_name text NOT NULL DEFAULT '',
    member_name text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'suggested',
    decided_by text NOT NULL DEFAULT '',
    decided_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE audit_runs ADD COLUMN IF NOT EXISTS project_id bigint REFERENCES projects(id) ON DELETE CASCADE;
ALTER TABLE audit_findings ADD COLUMN IF NOT EXISTS project_id bigint REFERENCES projects(id) ON DELETE CASCADE;
ALTER TABLE audit_author_map ADD COLUMN IF NOT EXISTS project_id bigint REFERENCES projects(id) ON DELETE CASCADE;

UPDATE audit_runs SET project_id = (SELECT min(id) FROM projects) WHERE project_id IS NULL;
UPDATE audit_findings f SET project_id = r.project_id FROM audit_runs r
 WHERE f.run_id = r.id AND f.project_id IS NULL;
UPDATE audit_author_map SET project_id = (SELECT min(id) FROM projects) WHERE project_id IS NULL;

ALTER TABLE audit_runs ALTER COLUMN project_id SET NOT NULL;
ALTER TABLE audit_findings ALTER COLUMN project_id SET NOT NULL;
ALTER TABLE audit_author_map ALTER COLUMN project_id SET NOT NULL;

ALTER TABLE audit_author_map DROP CONSTRAINT IF EXISTS audit_author_map_email_key;
CREATE UNIQUE INDEX IF NOT EXISTS audit_author_map_project_email_uniq
 ON audit_author_map(project_id, lower(email));
CREATE INDEX IF NOT EXISTS audit_runs_project_idx ON audit_runs(project_id, id DESC);
CREATE INDEX IF NOT EXISTS audit_findings_project_run_idx ON audit_findings(project_id, run_id);
