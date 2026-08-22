ALTER TABLE assistant_workflows
  ADD COLUMN cache_policy text NOT NULL DEFAULT 'none'
    CHECK (cache_policy IN ('none', 'reviewed_answer')),
  ADD COLUMN cached_answer text NOT NULL DEFAULT '',
  ADD COLUMN cached_sources jsonb NOT NULL DEFAULT '[]',
  ADD COLUMN cache_dependencies jsonb NOT NULL DEFAULT '[]',
  ADD COLUMN cache_refreshed_at timestamptz;

CREATE INDEX assistant_workflows_cache_idx
  ON assistant_workflows(project_id, cache_policy, status, updated_at DESC);
