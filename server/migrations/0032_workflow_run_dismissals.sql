CREATE TABLE IF NOT EXISTS workflow_run_dismissals (
  project_id int NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  run_id int NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
  user_id int NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  dismissed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, run_id, user_id)
);

CREATE INDEX IF NOT EXISTS workflow_run_dismissals_user_idx
  ON workflow_run_dismissals (project_id, user_id, dismissed_at DESC);
