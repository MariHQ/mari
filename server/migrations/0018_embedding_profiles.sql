-- Derived vectors are reusable only within the exact provider/model/vector
-- space that created them. Existing rows deliberately get an empty profile so
-- the next sync re-embeds them once with the configured HTTP provider.
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding_profile text NOT NULL DEFAULT '';

UPDATE settings
SET value = jsonb_set(
      jsonb_set(
        jsonb_set(value, '{provider}', '"openai"'::jsonb, true),
        '{model}', '"text-embedding-3-small"'::jsonb, true),
      '{options}', '["openai:text-embedding-3-small","ollama:nomic-embed-text"]'::jsonb, true)
WHERE key = 'embedding'
  AND COALESCE(value->>'provider', '') IN ('sentence-transformers', 'local');
