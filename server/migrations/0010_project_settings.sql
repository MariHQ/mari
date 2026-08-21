ALTER TABLE settings
  ADD COLUMN IF NOT EXISTS project_id int REFERENCES projects(id);

DO $$
DECLARE only_project int;
BEGIN
  IF (SELECT count(*) FROM projects) = 1 THEN
    SELECT id INTO only_project FROM projects LIMIT 1;
    UPDATE settings SET project_id = only_project WHERE project_id IS NULL;
  END IF;
END $$;

ALTER TABLE settings DROP CONSTRAINT IF EXISTS settings_pkey;
CREATE UNIQUE INDEX IF NOT EXISTS settings_project_key_uidx
  ON settings (project_id, key);
CREATE INDEX IF NOT EXISTS settings_project_idx ON settings (project_id);
