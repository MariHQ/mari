-- Signed provider deliveries are acknowledged only after this durable insert.
-- Leases make in-flight work recoverable after a process crash; coalesce_key
-- serializes updates for the same remote aggregate without dropping events.
CREATE TABLE IF NOT EXISTS event_inbox (
  id             bigserial PRIMARY KEY,
  provider       text NOT NULL,
  project_id     int NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  delivery_id    text NOT NULL,
  coalesce_key   text NOT NULL DEFAULT '',
  payload        jsonb NOT NULL,
  status         text NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'processing', 'completed', 'dead')),
  attempts       int NOT NULL DEFAULT 0,
  available_at   timestamptz NOT NULL DEFAULT now(),
  lease_until    timestamptz,
  completed_at   timestamptz,
  last_error     text NOT NULL DEFAULT '',
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (provider, project_id, delivery_id)
);
CREATE INDEX IF NOT EXISTS event_inbox_ready_idx
  ON event_inbox(status, available_at, lease_until, id);
CREATE INDEX IF NOT EXISTS event_inbox_coalesce_idx
  ON event_inbox(provider, project_id, coalesce_key, status, lease_until)
  WHERE coalesce_key <> '';

CREATE TABLE IF NOT EXISTS slack_bot_threads (
  installation_id int NOT NULL REFERENCES bot_installations(id) ON DELETE CASCADE,
  project_id      int NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  channel_id      text NOT NULL,
  thread_ts       text NOT NULL,
  bot_message_ts  text NOT NULL DEFAULT '',
  joined_at       timestamptz NOT NULL DEFAULT now(),
  last_event_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (installation_id, channel_id, thread_ts)
);
CREATE INDEX IF NOT EXISTS slack_bot_threads_project_idx
  ON slack_bot_threads(project_id, last_event_at DESC);
