CREATE TABLE IF NOT EXISTS gdrive_watch_channels (
  id                  bigserial PRIMARY KEY,
  project_id          int NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  source_id           int NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  channel_id          text NOT NULL UNIQUE,
  token_hash          text NOT NULL,
  resource_id         text NOT NULL DEFAULT '',
  page_token          text NOT NULL,
  expiration          timestamptz,
  last_message_number bigint NOT NULL DEFAULT 0,
  status              text NOT NULL DEFAULT 'creating'
                        CHECK (status IN ('creating', 'active', 'retiring',
                                          'needs_full_resync', 'error')),
  last_error          text NOT NULL DEFAULT '',
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS gdrive_watch_source_idx
  ON gdrive_watch_channels(project_id, source_id, status, expiration);
