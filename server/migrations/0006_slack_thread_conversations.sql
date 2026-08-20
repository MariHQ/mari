-- Slack bot tokens cannot call conversations.replies for public channels.
-- Persist Mari's own conversation turns so threaded follow-ups remain durable,
-- replayable, and independent of a user OAuth token.
ALTER TABLE slack_bot_threads
  ADD COLUMN IF NOT EXISTS conversation jsonb NOT NULL DEFAULT '[]'::jsonb;
