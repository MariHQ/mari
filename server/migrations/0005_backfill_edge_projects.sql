-- Link extraction added edges after the baseline project backfill but did not
-- populate their project_id. Scoped lineage deliberately ignores NULL, so the
-- graph appeared empty even though extraction reported created edges.
--
-- Only adopt an edge when both endpoints agree on a non-NULL project. Any
-- ambiguous legacy row remains fail-closed for an operator to inspect.
UPDATE edges e
   SET project_id = f.project_id
  FROM documents f, documents t
 WHERE e.from_doc = f.id
   AND e.to_doc = t.id
   AND f.project_id IS NOT NULL
   AND f.project_id = t.project_id
   AND e.project_id IS NULL;
