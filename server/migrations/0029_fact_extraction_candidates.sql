-- Fact extraction is a staged workflow. Candidates remain attached to the
-- workflow run until they have been reviewed and explicitly published; the
-- fact ledger therefore never doubles as a model-output scratchpad.
CREATE TABLE fact_extraction_candidates (
  id              bigserial PRIMARY KEY,
  project_id      integer NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  run_id          integer NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
  document_id     integer REFERENCES documents(id) ON DELETE SET NULL,
  claim           text NOT NULL,
  source_label    text NOT NULL DEFAULT '',
  evidence        text NOT NULL DEFAULT '',
  confidence      real NOT NULL DEFAULT 0,
  review_status   text NOT NULL DEFAULT 'pending'
                  CHECK (review_status IN ('pending', 'accepted', 'rejected')),
  review_kind     text NOT NULL DEFAULT '',
  review_reason   text NOT NULL DEFAULT '',
  reviewer        text NOT NULL DEFAULT '',
  reviewed_at     timestamptz,
  published_fact_id integer REFERENCES facts(id) ON DELETE SET NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (run_id, claim)
);

CREATE INDEX fact_extraction_candidates_run_status_idx
  ON fact_extraction_candidates (project_id, run_id, review_status, id);
