ALTER TABLE documents
  ALTER COLUMN updated_src TYPE timestamptz
    USING updated_src::timestamp AT TIME ZONE 'UTC',
  ALTER COLUMN created_src TYPE timestamptz
    USING created_src::timestamp AT TIME ZONE 'UTC';

ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS observed_at timestamptz NOT NULL DEFAULT now();

-- Existing GitHub projections contain import dates rather than provider
-- timestamps. Force one authoritative replay so source timestamps are restored
-- from GitHub and persisted through the corrected projection path.
UPDATE sources
   SET config = (coalesce(config, '{}'::jsonb)
                 - 'cursor' - 'checkpoint' - 'item_hashes' - 'full_snapshot_seen_paths')
                || '{"full_snapshot_pending":true}'::jsonb
 WHERE kind = 'connector'
   AND split_part(provider, ':', 1) = 'github';
