-- ingest_checkpoints was never project-scoped, but pause_source and the
-- checkpoint feed already filter it by project_id: pausing a source died
-- with `column "project_id" does not exist`. sync_events HAS the column,
-- and its busiest writer never filled it, so project-filtered readers saw
-- four rows out of nine hundred.
ALTER TABLE ingest_checkpoints ADD COLUMN IF NOT EXISTS project_id integer;

-- Backfill through the provider's source row. A checkpoint whose provider no
-- longer has a source stays NULL: transient run records, never guessed at.
UPDATE ingest_checkpoints ic SET project_id = s.project_id
  FROM sources s WHERE ic.project_id IS NULL AND s.provider = ic.provider;
CREATE INDEX IF NOT EXISTS ingest_checkpoints_project_idx
  ON ingest_checkpoints (project_id);

UPDATE sync_events e SET project_id = s.project_id
  FROM sources s WHERE e.project_id IS NULL AND s.provider = e.provider;
