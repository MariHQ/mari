-- Global settings (currently per-user preferences) deliberately have no
-- project_id. PostgreSQL treats NULLs as distinct in the composite project
-- index, so give those rows their own conflict target.
CREATE UNIQUE INDEX IF NOT EXISTS settings_global_key_uidx
  ON settings (key) WHERE project_id IS NULL;
