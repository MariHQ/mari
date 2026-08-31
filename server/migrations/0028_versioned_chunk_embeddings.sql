-- Chunk vectors are canonical derived data in Postgres. A model rotation must
-- add a profile, not overwrite the previous vector, so every tiered retrieval
-- artifact can be reconstructed without calling an embedding provider.
CREATE TABLE chunk_embeddings (
  project_id        integer NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  chunk_id          integer NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  document_id       integer NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  embedding_profile text NOT NULL,
  provider          text NOT NULL DEFAULT '',
  model             text NOT NULL DEFAULT '',
  purpose           text NOT NULL DEFAULT 'document',
  representation    text NOT NULL DEFAULT 'dense-chunk-set-v1',
  distance_metric   text NOT NULL DEFAULT 'cosine-maxsim',
  normalized        boolean NOT NULL DEFAULT false,
  dimensions        integer NOT NULL DEFAULT 768 CHECK (dimensions > 0),
  content_hash      text NOT NULL,
  embedding         vector(768) NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (chunk_id, embedding_profile, purpose)
);

CREATE INDEX chunk_embeddings_profile_document_idx
  ON chunk_embeddings (project_id, embedding_profile, document_id, chunk_id);

-- Preserve vectors produced before the versioned store existed. Provider and
-- model remain blank because older profiles were not guaranteed to be
-- losslessly parseable (Ollama model names may themselves contain colons).
INSERT INTO chunk_embeddings (
  project_id, chunk_id, document_id, embedding_profile, purpose,
  dimensions, content_hash, embedding
)
SELECT c.project_id, c.id, c.document_id, c.embedding_profile, 'document',
       768, c.content_hash, c.embedding
  FROM chunks c
 WHERE c.project_id IS NOT NULL
   AND c.embedding IS NOT NULL
   AND c.embedding_profile <> ''
ON CONFLICT (chunk_id, embedding_profile, purpose) DO NOTHING;
