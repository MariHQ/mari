-- Facts had no record of when they entered the ledger. The only date the
-- console could show was verified_at, blank until a person verifies, so a
-- freshly extracted fact rendered dateless. Backfill from the earliest
-- assertion where one exists; verified_at, then valid_from, are the least
-- wrong stand-ins for rows that predate assertions.
ALTER TABLE facts ADD COLUMN created_at timestamptz;

UPDATE facts SET created_at = COALESCE(
  (SELECT min(a.created_at) FROM fact_assertions a WHERE a.fact_id = facts.id),
  verified_at, valid_from, now());

ALTER TABLE facts ALTER COLUMN created_at SET NOT NULL;
ALTER TABLE facts ALTER COLUMN created_at SET DEFAULT now();
