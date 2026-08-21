-- Connector activity is project-owned just like its source. Persistence
-- already requires this column; make a fresh database match that contract.
ALTER TABLE sync_events
  ADD COLUMN IF NOT EXISTS project_id integer REFERENCES projects(id);

CREATE INDEX IF NOT EXISTS sync_events_project_idx
  ON sync_events (project_id, id DESC);
