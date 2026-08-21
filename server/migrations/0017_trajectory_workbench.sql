-- Human-governed trajectory workbench: tune observed tools/evidence and
-- explicitly promote a successful trace into a draft workflow.
ALTER TABLE trajectories ADD COLUMN IF NOT EXISTS promoted_workflow_id int;
ALTER TABLE trajectory_steps ADD COLUMN IF NOT EXISTS disposition text NOT NULL DEFAULT 'included';
ALTER TABLE trajectory_steps ADD COLUMN IF NOT EXISTS edited_args jsonb;

CREATE TABLE IF NOT EXISTS trajectory_evidence (
  id              serial PRIMARY KEY,
  project_id      int REFERENCES projects(id) ON DELETE CASCADE,
  trajectory_id   int NOT NULL REFERENCES trajectories(id) ON DELETE CASCADE,
  document_id     int NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  title           text NOT NULL DEFAULT '',
  reason          text NOT NULL DEFAULT '',
  rank            int NOT NULL DEFAULT 0,
  relevance       text NOT NULL DEFAULT 'observed',
  note            text NOT NULL DEFAULT '',
  UNIQUE (trajectory_id, document_id)
);

CREATE INDEX IF NOT EXISTS trajectory_evidence_project_idx
  ON trajectory_evidence(project_id, trajectory_id, rank, id);

ALTER TABLE knowledge_chat_destinations ADD COLUMN IF NOT EXISTS tools jsonb
  NOT NULL DEFAULT '["search","facts","answers"]'::jsonb;
