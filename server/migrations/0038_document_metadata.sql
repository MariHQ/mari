-- Preserve connector metadata needed for structured retrieval without mixing
-- it into document bodies or semantic embeddings.
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb;
