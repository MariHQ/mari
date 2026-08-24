-- The dock's search results now carry the record's trust metadata
-- (verification status, owner, age). Two rules teach every chat surface to
-- use it: prefer what the team verified, and say so when the ground is
-- soft. Mirrors the 0024 seeding; ON CONFLICT keeps workspace edits.
INSERT INTO style_rules (id, guide_key, family, severity, description, pack, suggestion, sort) VALUES
  ('chat.trust', 'chat', 'Chat', 'warn',
   'Prefer canonical and verified sources over unreviewed ones when they disagree, and say which you used.',
   'chat-11', 'Lead with the verified source and name its status.', 110),
  ('chat.fresh', 'chat', 'Chat', 'warn',
   'When a cited source is marked stale or needs review, or its age undercuts the claim, say so in one clause instead of presenting it as settled.',
   'chat-12', 'Add "per a stale doc from March" style caveats.', 120)
ON CONFLICT (id) DO NOTHING;
