-- Immutable temporal assertions and their reconstructible multi-vector inputs.
-- Existing facts/candidates remain the compatibility projection while the
-- workflow migrates to these canonical records.

ALTER TABLE facts
  ADD COLUMN canonical_key text,
  ADD COLUMN criticality text NOT NULL DEFAULT 'normal'
    CHECK (criticality IN ('low', 'normal', 'high', 'critical')),
  ADD COLUMN current_assertion_id bigint;

UPDATE facts SET canonical_key = 'fact:' || id WHERE canonical_key IS NULL;
CREATE UNIQUE INDEX facts_project_canonical_key_idx
  ON facts (project_id, canonical_key) WHERE canonical_key IS NOT NULL;

ALTER TABLE fact_extraction_candidates
  ADD COLUMN structured_claim jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN extraction_recipe text NOT NULL DEFAULT 'facts-extract-v3';

CREATE TABLE fact_assertions (
  id                  bigserial PRIMARY KEY,
  project_id          integer NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  fact_id             integer REFERENCES facts(id) ON DELETE CASCADE,
  candidate_id        bigint REFERENCES fact_extraction_candidates(id) ON DELETE CASCADE,
  run_id              integer REFERENCES workflow_runs(id) ON DELETE SET NULL,
  source_document_id  integer REFERENCES documents(id) ON DELETE SET NULL,
  claim               text NOT NULL,
  structured_claim    jsonb NOT NULL DEFAULT '{}'::jsonb,
  adjudication        jsonb NOT NULL DEFAULT '{}'::jsonb,
  extraction_schema   text NOT NULL DEFAULT 'fact-assertion-v1',
  valid_from          timestamptz,
  valid_to            timestamptz,
  recorded_from       timestamptz NOT NULL DEFAULT now(),
  recorded_to         timestamptz,
  status              text NOT NULL DEFAULT 'proposed'
    CHECK (status IN ('proposed', 'pending_review', 'active', 'superseded',
                      'invalidated', 'rejected')),
  confidence          real NOT NULL DEFAULT 0 CHECK (confidence >= 0 AND confidence <= 1),
  confidence_reason   text NOT NULL DEFAULT '',
  extraction_model    text NOT NULL DEFAULT '',
  extraction_recipe   text NOT NULL DEFAULT '',
  actor               text NOT NULL DEFAULT '',
  content_hash        text NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  CHECK (fact_id IS NOT NULL OR candidate_id IS NOT NULL),
  CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from),
  CHECK (recorded_to IS NULL OR recorded_to > recorded_from)
);

CREATE UNIQUE INDEX fact_assertions_candidate_idx
  ON fact_assertions (project_id, candidate_id) WHERE candidate_id IS NOT NULL;
CREATE INDEX fact_assertions_fact_time_idx
  ON fact_assertions (project_id, fact_id, recorded_from DESC);
CREATE INDEX fact_assertions_active_idx
  ON fact_assertions (project_id, status, valid_from, valid_to);

ALTER TABLE facts
  ADD CONSTRAINT facts_current_assertion_fk
  FOREIGN KEY (current_assertion_id) REFERENCES fact_assertions(id) ON DELETE SET NULL;

INSERT INTO fact_assertions (
  project_id, fact_id, source_document_id, claim, valid_from, valid_to,
  recorded_from, recorded_to, status, confidence, confidence_reason,
  actor, content_hash
)
SELECT project_id, id, document_id, claim,
       CASE WHEN invalidated_at IS NOT NULL
            THEN least(COALESCE(valid_from, invalidated_at - interval '1 microsecond'),
                       invalidated_at - interval '1 microsecond')
            ELSE valid_from END,
       invalidated_at,
       CASE WHEN invalidated_at IS NOT NULL
            THEN least(COALESCE(valid_from, invalidated_at - interval '1 microsecond'),
                       invalidated_at - interval '1 microsecond')
            ELSE COALESCE(valid_from, now()) END,
       invalidated_at,
       CASE WHEN status IN ('Retired', 'Invalidated') THEN 'invalidated' ELSE 'active' END,
       CASE WHEN status = 'Verified' THEN 1 ELSE 0.5 END,
       'Backfilled from the fact ledger', owner_name,
       md5(claim)
  FROM facts;

UPDATE facts f
   SET current_assertion_id = a.id
  FROM fact_assertions a
 WHERE a.fact_id = f.id AND a.project_id = f.project_id;

INSERT INTO fact_assertions (
  project_id, candidate_id, run_id, source_document_id, claim, structured_claim,
  extraction_recipe, status, confidence, confidence_reason, actor, content_hash, recorded_from
)
SELECT project_id, id, run_id, document_id, claim, structured_claim,
       extraction_recipe,
       CASE review_status WHEN 'accepted' THEN 'pending_review'
                          WHEN 'rejected' THEN 'rejected' ELSE 'proposed' END,
       greatest(0, least(1, confidence)), review_reason, reviewer,
       md5(claim), created_at
  FROM fact_extraction_candidates;

CREATE TABLE fact_representation_components (
  id                     bigserial PRIMARY KEY,
  project_id             integer NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  assertion_id           bigint NOT NULL REFERENCES fact_assertions(id) ON DELETE CASCADE,
  embedding_profile      text NOT NULL,
  representation_profile text NOT NULL DEFAULT 'fact-components-v1',
  ordinal                integer NOT NULL CHECK (ordinal >= 0),
  component_role         text NOT NULL
    CHECK (component_role IN ('claim', 'atomic_claim', 'subject', 'relation',
                              'object', 'scope', 'time', 'condition', 'evidence_span')),
  rendered_text          text NOT NULL,
  content_hash           text NOT NULL,
  provider               text NOT NULL DEFAULT '',
  model                  text NOT NULL DEFAULT '',
  dimensions             integer NOT NULL CHECK (dimensions > 0),
  purpose                text NOT NULL DEFAULT 'fact',
  embedding              vector NOT NULL,
  created_at             timestamptz NOT NULL DEFAULT now(),
  UNIQUE (project_id, assertion_id, embedding_profile, representation_profile, ordinal)
);

CREATE INDEX fact_representation_components_assertion_idx
  ON fact_representation_components
  (project_id, embedding_profile, representation_profile, assertion_id, ordinal);

-- Preserve the deployed dense claim vector as component zero. New profiles may
-- have any dimension and append rows rather than replacing this history.
INSERT INTO fact_representation_components (
  project_id, assertion_id, embedding_profile, ordinal, component_role,
  rendered_text, content_hash, provider, model, dimensions, purpose, embedding
)
SELECT e.project_id, a.id, e.embedding_profile, 0, 'claim', a.claim,
       e.content_hash, e.provider, e.model, e.dimensions, e.purpose, e.embedding::vector
  FROM fact_embeddings e
  JOIN fact_assertions a ON a.project_id = e.project_id
   AND ((e.subject_type = 'fact' AND a.fact_id = e.subject_id)
     OR (e.subject_type = 'candidate' AND a.candidate_id = e.subject_id))
ON CONFLICT DO NOTHING;

CREATE TABLE evidence_spans (
  id                 bigserial PRIMARY KEY,
  project_id         integer NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  document_id        integer NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_id           integer REFERENCES chunks(id) ON DELETE SET NULL,
  start_offset       integer NOT NULL DEFAULT 0,
  end_offset         integer NOT NULL,
  quote              text NOT NULL,
  content_hash       text NOT NULL,
  acl                 jsonb NOT NULL DEFAULT '{}'::jsonb,
  source_authority   text NOT NULL DEFAULT 'unrated',
  published_at       timestamptz,
  effective_from     timestamptz,
  effective_to       timestamptz,
  revised_at         timestamptz,
  ingested_at        timestamptz NOT NULL DEFAULT now(),
  observed_at        timestamptz NOT NULL DEFAULT now(),
  created_at         timestamptz NOT NULL DEFAULT now(),
  CHECK (end_offset > start_offset),
  UNIQUE (project_id, document_id, content_hash, start_offset, end_offset)
);

CREATE INDEX evidence_spans_document_idx
  ON evidence_spans (project_id, document_id, revised_at DESC, id);

CREATE TABLE fact_evidence_groups (
  id                 bigserial PRIMARY KEY,
  project_id         integer NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  assertion_id       bigint NOT NULL REFERENCES fact_assertions(id) ON DELETE CASCADE,
  verdict            text NOT NULL
    CHECK (verdict IN ('supports', 'contradicts', 'qualifies', 'insufficient')),
  sufficient         boolean NOT NULL DEFAULT false,
  confidence         real NOT NULL DEFAULT 0 CHECK (confidence >= 0 AND confidence <= 1),
  rationale          text NOT NULL DEFAULT '',
  decision_kind      text NOT NULL DEFAULT 'embedding'
    CHECK (decision_kind IN ('embedding', 'llm', 'human')),
  decision_model     text NOT NULL DEFAULT '',
  decision_recipe    text NOT NULL DEFAULT '',
  context_hash       text NOT NULL DEFAULT '',
  retrieval_profile  text NOT NULL DEFAULT '',
  active             boolean NOT NULL DEFAULT true,
  reviewer           text NOT NULL DEFAULT '',
  reviewed_at        timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX fact_evidence_groups_assertion_idx
  ON fact_evidence_groups (project_id, assertion_id, active, decision_kind, id);

CREATE TABLE fact_evidence_group_members (
  group_id           bigint NOT NULL REFERENCES fact_evidence_groups(id) ON DELETE CASCADE,
  evidence_span_id   bigint NOT NULL REFERENCES evidence_spans(id) ON DELETE CASCADE,
  role               text NOT NULL
    CHECK (role IN ('support', 'contradiction', 'qualification', 'context')),
  ordinal            integer NOT NULL DEFAULT 0,
  similarity         real,
  PRIMARY KEY (group_id, evidence_span_id)
);

CREATE TABLE fact_relations (
  id                 bigserial PRIMARY KEY,
  project_id         integer NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  source_assertion_id bigint NOT NULL REFERENCES fact_assertions(id) ON DELETE CASCADE,
  target_assertion_id bigint NOT NULL REFERENCES fact_assertions(id) ON DELETE CASCADE,
  relation           text NOT NULL
    CHECK (relation IN ('supports', 'contradicts', 'supersedes', 'qualifies',
                        'duplicate', 'related', 'insufficient')),
  approximate_score  real,
  exact_score        real,
  evidence_group_id  bigint REFERENCES fact_evidence_groups(id) ON DELETE SET NULL,
  decision_kind      text NOT NULL DEFAULT 'embedding'
    CHECK (decision_kind IN ('embedding', 'llm', 'human')),
  decision_model     text NOT NULL DEFAULT '',
  decision_recipe    text NOT NULL DEFAULT '',
  confidence         real NOT NULL DEFAULT 0 CHECK (confidence >= 0 AND confidence <= 1),
  rationale          text NOT NULL DEFAULT '',
  retrieval_profile  text NOT NULL DEFAULT '',
  index_generation   text NOT NULL DEFAULT '',
  observed_at        timestamptz NOT NULL DEFAULT now(),
  active             boolean NOT NULL DEFAULT true,
  CHECK (source_assertion_id <> target_assertion_id),
  UNIQUE (project_id, source_assertion_id, target_assertion_id, relation, retrieval_profile)
);

CREATE INDEX fact_relations_source_idx
  ON fact_relations (project_id, source_assertion_id, active, exact_score DESC NULLS LAST);
CREATE INDEX fact_relations_target_idx
  ON fact_relations (project_id, target_assertion_id, active);

CREATE TABLE fact_clusters (
  id                 bigserial PRIMARY KEY,
  project_id         integer NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  stable_key         text NOT NULL,
  label              text NOT NULL DEFAULT '',
  summary            text NOT NULL DEFAULT '',
  embedding_profile  text NOT NULL,
  retrieval_profile  text NOT NULL DEFAULT '',
  generation         text NOT NULL,
  lifecycle          text NOT NULL DEFAULT 'created'
    CHECK (lifecycle IN ('created', 'continued', 'split', 'merged', 'retired')),
  previous_cluster_ids bigint[] NOT NULL DEFAULT '{}',
  label_kind         text NOT NULL DEFAULT 'none'
    CHECK (label_kind IN ('none', 'llm', 'human')),
  label_model        text NOT NULL DEFAULT '',
  created_at         timestamptz NOT NULL DEFAULT now(),
  UNIQUE (project_id, stable_key, generation)
);

CREATE TABLE fact_cluster_memberships (
  cluster_id         bigint NOT NULL REFERENCES fact_clusters(id) ON DELETE CASCADE,
  assertion_id       bigint NOT NULL REFERENCES fact_assertions(id) ON DELETE CASCADE,
  membership_score   real NOT NULL,
  explanation        text NOT NULL DEFAULT '',
  PRIMARY KEY (cluster_id, assertion_id)
);

CREATE TABLE fact_dependencies (
  id                 bigserial PRIMARY KEY,
  project_id         integer NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  assertion_id       bigint NOT NULL REFERENCES fact_assertions(id) ON DELETE CASCADE,
  downstream_type    text NOT NULL,
  downstream_id      text NOT NULL,
  downstream_label   text NOT NULL DEFAULT '',
  dependency_type    text NOT NULL
    CHECK (dependency_type IN ('derived_from', 'cited_by', 'used_by_decision',
                               'used_by_answer', 'used_by_workflow',
                               'required_by_playbook')),
  provenance         jsonb NOT NULL DEFAULT '{}'::jsonb,
  parent_dependency_id bigint REFERENCES fact_dependencies(id) ON DELETE CASCADE,
  active_from        timestamptz NOT NULL DEFAULT now(),
  active_to          timestamptz,
  created_by         text NOT NULL DEFAULT '',
  UNIQUE (project_id, assertion_id, downstream_type, downstream_id, dependency_type)
);

CREATE INDEX fact_dependencies_reverse_idx
  ON fact_dependencies (project_id, assertion_id, active_to, dependency_type);

CREATE TABLE fact_invalidation_events (
  id                 bigserial PRIMARY KEY,
  project_id         integer NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  assertion_id       bigint NOT NULL REFERENCES fact_assertions(id) ON DELETE CASCADE,
  replacement_assertion_id bigint REFERENCES fact_assertions(id) ON DELETE SET NULL,
  reason             text NOT NULL,
  actor              text NOT NULL,
  effective_at       timestamptz NOT NULL,
  created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE fact_impact_items (
  id                 bigserial PRIMARY KEY,
  event_id           bigint NOT NULL REFERENCES fact_invalidation_events(id) ON DELETE CASCADE,
  impact_kind        text NOT NULL CHECK (impact_kind IN ('direct', 'transitive', 'possible')),
  target_type        text NOT NULL,
  target_id          text NOT NULL,
  target_label       text NOT NULL DEFAULT '',
  severity           integer NOT NULL DEFAULT 0,
  owner              text NOT NULL DEFAULT '',
  disposition        text NOT NULL DEFAULT 'pending'
    CHECK (disposition IN ('pending', 'confirmed', 'dismissed', 'revalidated')),
  revalidation_run_id integer REFERENCES workflow_runs(id) ON DELETE SET NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  UNIQUE (event_id, impact_kind, target_type, target_id)
);

CREATE TABLE fact_llm_invocations (
  id                 bigserial PRIMARY KEY,
  project_id         integer NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  run_id             integer REFERENCES workflow_runs(id) ON DELETE SET NULL,
  stage              text NOT NULL,
  purpose            text NOT NULL,
  provider           text NOT NULL DEFAULT '',
  model              text NOT NULL DEFAULT '',
  recipe             text NOT NULL DEFAULT '',
  max_calls          integer NOT NULL CHECK (max_calls >= 0),
  max_input_tokens   integer NOT NULL CHECK (max_input_tokens >= 0),
  max_output_tokens  integer NOT NULL CHECK (max_output_tokens >= 0),
  calls_used         integer NOT NULL DEFAULT 0 CHECK (calls_used >= 0),
  input_tokens       integer NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
  output_tokens      integer NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
  cost_usd           numeric(14,6) NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
  status             text NOT NULL DEFAULT 'configured'
    CHECK (status IN ('configured', 'running', 'completed', 'skipped', 'exhausted', 'failed')),
  visible_config     jsonb NOT NULL DEFAULT '{}'::jsonb,
  started_at         timestamptz,
  completed_at       timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now(),
  UNIQUE (project_id, run_id, stage, purpose)
);

CREATE TABLE vector_index_generations (
  id                 bigserial PRIMARY KEY,
  project_id         integer NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  object_type        text NOT NULL,
  retrieval_backend  text NOT NULL CHECK (retrieval_backend IN ('postgres', 'muvera')),
  retrieval_profile  text NOT NULL,
  generation         text NOT NULL,
  canonical_digest   text NOT NULL,
  row_count          integer NOT NULL DEFAULT 0,
  vector_count       integer NOT NULL DEFAULT 0,
  artifact_uri       text NOT NULL DEFAULT '',
  checksums           jsonb NOT NULL DEFAULT '{}'::jsonb,
  benchmark           jsonb NOT NULL DEFAULT '{}'::jsonb,
  status              text NOT NULL DEFAULT 'building'
    CHECK (status IN ('building', 'ready', 'promoted', 'failed', 'retired')),
  started_at          timestamptz NOT NULL DEFAULT now(),
  completed_at        timestamptz,
  UNIQUE (project_id, object_type, retrieval_profile, generation)
);
