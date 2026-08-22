-- Human-reviewed assistant behavior is not an executable automation. Keep it
-- beside the trajectory that grounded it and expose it to conversational
-- runtimes as guidance.
CREATE TABLE assistant_workflows (
  id            serial PRIMARY KEY,
  project_id    int NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  trajectory_id int NOT NULL REFERENCES trajectories(id) ON DELETE CASCADE,
  name          text NOT NULL,
  description   text NOT NULL DEFAULT '',
  status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused')),
  steps         jsonb NOT NULL DEFAULT '[]',
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (project_id, trajectory_id),
  UNIQUE (project_id, name)
);

CREATE INDEX assistant_workflows_active_idx
  ON assistant_workflows(project_id, status, updated_at DESC);

-- Move drafts created by the former implementation out of Automations. Those
-- nodes were never executable there; preserving their ids keeps existing
-- trajectory links stable.
INSERT INTO assistant_workflows
  (id, project_id, trajectory_id, name, description, status, steps, created_at, updated_at)
SELECT w.id, w.project_id, t.id, w.name, w.description, 'active',
       COALESCE((
         SELECT jsonb_agg(node->'config' ORDER BY ordinal)
           FROM jsonb_array_elements(w.nodes) WITH ORDINALITY AS n(node, ordinal)
          WHERE node->>'kind' = 'observed_tool'
       ), '[]'::jsonb),
       now(), now()
  FROM trajectories t
  JOIN workflows w ON w.id = t.promoted_workflow_id AND w.project_id = t.project_id
 WHERE t.promoted_workflow_id IS NOT NULL
ON CONFLICT DO NOTHING;

DELETE FROM workflow_runs r
 USING trajectories t
 WHERE r.workflow_id = t.promoted_workflow_id
   AND t.promoted_workflow_id IS NOT NULL;

DELETE FROM workflows w
 USING trajectories t
 WHERE w.id = t.promoted_workflow_id
   AND w.project_id = t.project_id
   AND t.promoted_workflow_id IS NOT NULL;

SELECT setval('assistant_workflows_id_seq',
              GREATEST(1, COALESCE((SELECT max(id) FROM assistant_workflows), 0)), true);
