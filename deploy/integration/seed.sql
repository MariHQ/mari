\set ON_ERROR_STOP on

INSERT INTO users (name, initials, tint, email, role, provider, status)
VALUES ('CI Admin', 'CI', 1, 'ci-admin@example.test', 'admin', 'manual', 'active')
ON CONFLICT (name) DO UPDATE SET status = 'active', role = 'admin';

INSERT INTO project_members (project_id, user_id, role, status)
SELECT p.id, u.id, 'owner', 'active'
FROM projects p, users u
WHERE p.slug = 'default' AND u.name = 'CI Admin'
ON CONFLICT (project_id, user_id) DO UPDATE SET role = 'owner', status = 'active';

INSERT INTO settings (key, value) VALUES
  ('setup_complete', 'true'::jsonb),
  ('workspace', '{"name":"Integration Workspace","slug":"integration","plan":"enterprise","timezone":"UTC","language":"English (US)"}'::jsonb),
  ('embedding', '{"provider":"ollama","model":"nomic-embed-text","dims":768,"options":["ollama:nomic-embed-text"]}'::jsonb),
  ('llm', '{"provider":"ollama","model":"qwen2.5:0.5b","options":["ollama:qwen2.5:0.5b"],"keys":{}}'::jsonb)
ON CONFLICT (key) DO UPDATE SET value = excluded.value;

INSERT INTO sources (provider, display_name, status, stat_num, stat_unit, docs_count, health, kind, project_id)
SELECT 'integration', 'Production-like CI', 'active', '1', 'documents', 1, 'Healthy', 'connector', p.id
FROM projects p WHERE p.slug = 'default'
ON CONFLICT (project_id, provider) DO UPDATE SET display_name = excluded.display_name, status = 'active';

INSERT INTO documents
  (source, external_id, title, snippet, body, author, author_initials, kind,
   updated_src, created_src, content_hash, source_path, source_id, project_id,
   acl_visibility, acl_principals)
SELECT 'integration', 'retention-runbook', 'Retention runbook',
       'Customer data is retained for 30 days.',
       '# Retention\nCustomer data is retained for 30 days after deletion.',
       'CI Admin', 'CI', 'page', current_date, current_date,
       'integration-retention-v1', 'retention.md', s.id, p.id, 'project', '[]'::jsonb
FROM projects p JOIN sources s ON s.project_id = p.id AND s.provider = 'integration'
WHERE p.slug = 'default'
ON CONFLICT (project_id, source, external_id) DO UPDATE
SET title = excluded.title, snippet = excluded.snippet, body = excluded.body,
    content_hash = excluded.content_hash, source_id = excluded.source_id;

INSERT INTO tags (document_id, tag, project_id)
SELECT d.id, 'canonical', d.project_id FROM documents d
WHERE d.source = 'integration' AND d.external_id = 'retention-runbook'
ON CONFLICT (document_id, tag) DO UPDATE SET project_id = excluded.project_id;

INSERT INTO facts (claim, source, owner_name, owner_tint, status, verified, verified_at, document_id, project_id)
SELECT 'Customer data is retained for 30 days.', 'Retention runbook', 'CI Admin', 1,
       'Verified', current_date::text, current_date, d.id, d.project_id
FROM documents d WHERE d.source = 'integration' AND d.external_id = 'retention-runbook'
ON CONFLICT (project_id, claim) DO UPDATE
SET status = 'Verified', verified_at = current_date, document_id = excluded.document_id;

INSERT INTO tasks
  (title, assignee, assignee_initials, assignee_tint, kind, kind_label, done,
   subject_type, subject_id, subject_title, subject_href, project_id)
SELECT 'Verify integration retention policy', 'CI Admin', 'CI', 1, 'factcheck', 'Fact check', false,
       'document', d.id::text, d.title, '/knowledge/doc?id=' || d.id, d.project_id
FROM documents d WHERE d.source = 'integration' AND d.external_id = 'retention-runbook'
ON CONFLICT (project_id, title) DO UPDATE SET done = false;
