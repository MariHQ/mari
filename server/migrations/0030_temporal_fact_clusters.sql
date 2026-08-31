-- Canonical fact/candidate vectors. As with chunk_embeddings, a model rotation
-- appends a profile instead of overwriting the vectors needed to reconstruct
-- an older impact decision.
CREATE TABLE fact_embeddings (
  id                bigserial PRIMARY KEY,
  project_id        integer NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  subject_type      text NOT NULL CHECK (subject_type IN ('fact', 'candidate')),
  subject_id        bigint NOT NULL,
  embedding_profile text NOT NULL,
  provider          text NOT NULL DEFAULT '',
  model             text NOT NULL DEFAULT '',
  purpose           text NOT NULL DEFAULT 'fact',
  representation    text NOT NULL DEFAULT 'dense-fact-claim-v1',
  distance_metric   text NOT NULL DEFAULT 'cosine',
  normalized        boolean NOT NULL DEFAULT false,
  dimensions        integer NOT NULL DEFAULT 768 CHECK (dimensions > 0),
  content_hash      text NOT NULL,
  embedding         vector(768) NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (project_id, subject_type, subject_id, embedding_profile, purpose)
);

CREATE INDEX fact_embeddings_profile_subject_idx
  ON fact_embeddings (project_id, embedding_profile, subject_type, subject_id);

-- A link is a reproducible observation made under one embedding profile. The
-- target label and temporal metadata are snapshotted so an invalidation report
-- still explains what was considered after documents and claims evolve.
CREATE TABLE fact_semantic_links (
  id                bigserial PRIMARY KEY,
  project_id        integer NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  subject_type      text NOT NULL CHECK (subject_type IN ('fact', 'candidate')),
  subject_id        bigint NOT NULL,
  target_type       text NOT NULL CHECK (target_type IN ('fact', 'document')),
  target_id         bigint NOT NULL,
  embedding_profile text NOT NULL,
  similarity        real NOT NULL CHECK (similarity >= -1 AND similarity <= 1),
  relation          text NOT NULL CHECK (relation IN ('source', 'related', 'supports', 'contradicts')),
  target_label      text NOT NULL DEFAULT '',
  target_updated_at timestamptz,
  target_content_hash text NOT NULL DEFAULT '',
  observed_at       timestamptz NOT NULL DEFAULT now(),
  active            boolean NOT NULL DEFAULT true,
  UNIQUE (project_id, subject_type, subject_id, target_type, target_id, embedding_profile)
);

CREATE INDEX fact_semantic_links_subject_idx
  ON fact_semantic_links (project_id, subject_type, subject_id, active, similarity DESC);
CREATE INDEX fact_semantic_links_target_fact_idx
  ON fact_semantic_links (project_id, target_id, active)
  WHERE target_type = 'fact';

ALTER TABLE fact_extraction_candidates
  ADD COLUMN impact_score integer NOT NULL DEFAULT 0,
  ADD COLUMN high_impact boolean NOT NULL DEFAULT false;

ALTER TABLE facts
  ADD COLUMN valid_from timestamptz,
  ADD COLUMN invalidated_at timestamptz,
  ADD COLUMN invalidation_reason text NOT NULL DEFAULT '';

UPDATE facts f
   SET valid_from = COALESCE(f.verified_at::timestamptz, d.updated_src, now())
  FROM documents d
 WHERE f.document_id = d.id AND f.valid_from IS NULL;
UPDATE facts SET valid_from = COALESCE(valid_from, verified_at::timestamptz, now())
 WHERE valid_from IS NULL;
