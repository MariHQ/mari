-- Fact extraction is content-addressed at passage granularity. A chunk is read
-- once for a given hash; changing it creates one new unit of work while
-- unchanged passages remain complete.
ALTER TABLE documents ADD COLUMN facts_scanned_hash text;

CREATE TABLE fact_chunk_scans (
  project_id   integer NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  chunk_id     integer NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  content_hash text NOT NULL,
  scanned_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, chunk_id, content_hash)
);

CREATE INDEX fact_chunk_scans_chunk_idx
  ON fact_chunk_scans (project_id, chunk_id, scanned_at DESC);

-- A deployment must not suddenly re-mine every passage that the previous
-- document checkpoint considered complete. Documents changed after their last
-- scan are deliberately excluded and will enter the incremental queue.
INSERT INTO fact_chunk_scans (project_id, chunk_id, content_hash, scanned_at)
SELECT c.project_id, c.id, c.content_hash, d.facts_scanned_at
  FROM chunks c
  JOIN documents d ON d.project_id = c.project_id AND d.id = c.document_id
 WHERE d.facts_scanned_at IS NOT NULL
   AND (d.updated_src IS NULL OR d.facts_scanned_at >= d.updated_src)
ON CONFLICT DO NOTHING;

UPDATE documents
   SET facts_scanned_hash = content_hash
 WHERE facts_scanned_at IS NOT NULL
   AND (updated_src IS NULL OR facts_scanned_at >= updated_src);

-- Normalized keys catch harmless punctuation/case/spacing variants before a
-- candidate reaches review. They are intentionally indexed, not globally
-- unique: historical duplicates must be merged with their provenance intact.
ALTER TABLE facts ADD COLUMN normalized_key text NOT NULL DEFAULT '';
ALTER TABLE facts ADD COLUMN merged_into_fact_id integer REFERENCES facts(id) ON DELETE SET NULL;
ALTER TABLE fact_extraction_candidates ADD COLUMN normalized_key text NOT NULL DEFAULT '';
ALTER TABLE fact_extraction_candidates ADD COLUMN source_chunk_id integer REFERENCES chunks(id) ON DELETE SET NULL;
ALTER TABLE fact_extraction_candidates ADD COLUMN source_content_hash text NOT NULL DEFAULT '';
ALTER TABLE fact_assertions ADD COLUMN normalized_key text NOT NULL DEFAULT '';
ALTER TABLE fact_assertions ADD COLUMN source_chunk_id integer REFERENCES chunks(id) ON DELETE SET NULL;
ALTER TABLE fact_assertions ADD COLUMN source_content_hash text NOT NULL DEFAULT '';

UPDATE facts
   SET normalized_key = btrim(regexp_replace(lower(trim(claim)), '[^[:alnum:]]+', ' ', 'g'));
UPDATE fact_extraction_candidates
   SET normalized_key = btrim(regexp_replace(lower(trim(claim)), '[^[:alnum:]]+', ' ', 'g'));
UPDATE fact_assertions
   SET normalized_key = btrim(regexp_replace(lower(trim(claim)), '[^[:alnum:]]+', ' ', 'g'));

CREATE INDEX facts_project_normalized_key_idx
  ON facts (project_id, normalized_key) WHERE normalized_key <> '';
CREATE INDEX fact_candidates_run_normalized_key_idx
  ON fact_extraction_candidates (run_id, normalized_key) WHERE normalized_key <> '';
CREATE INDEX fact_assertions_project_normalized_key_idx
  ON fact_assertions (project_id, normalized_key) WHERE normalized_key <> '';
CREATE INDEX facts_project_unmerged_idx
  ON facts (project_id, id) WHERE merged_into_fact_id IS NULL;
