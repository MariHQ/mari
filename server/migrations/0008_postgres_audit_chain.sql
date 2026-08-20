CREATE TABLE IF NOT EXISTS audit_events (
  event_id text PRIMARY KEY,
  occurred_at timestamptz NOT NULL,
  project_id bigint NOT NULL,
  actor_type text NOT NULL,
  actor_id text NOT NULL,
  actor_name text NOT NULL,
  action text NOT NULL,
  resource_type text NOT NULL,
  resource_id text NOT NULL,
  outcome text NOT NULL,
  reason text NOT NULL,
  request_id text NOT NULL,
  correlation_id text NOT NULL,
  detail_json jsonb NOT NULL,
  previous_hash text NOT NULL,
  event_hash text NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS audit_events_project_order_idx
  ON audit_events (project_id, occurred_at DESC, event_id DESC);
