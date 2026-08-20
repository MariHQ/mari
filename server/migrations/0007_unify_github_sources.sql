-- Existing GitHub sources predate the connector catalog.  Move them onto the
-- generic worker and deliberately reset their incompatible legacy cursor so
-- the first shared-connector run produces one authoritative snapshot.
UPDATE sources
SET kind = 'connector',
    config = (COALESCE(config, '{}'::jsonb) - 'shas') || jsonb_build_object(
      'provider_key', 'github',
      'cursor', '',
      'item_hashes', '{}'::jsonb,
      'full_snapshot_pending', true,
      'full_snapshot_seen_paths', '[]'::jsonb
    ),
    health = CASE WHEN status = 'active' THEN 'Syncing' ELSE health END
WHERE kind = 'github';
