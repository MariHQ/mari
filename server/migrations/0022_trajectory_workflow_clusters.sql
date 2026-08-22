ALTER TABLE trajectories
  ADD COLUMN matched_workflow_id int REFERENCES assistant_workflows(id) ON DELETE SET NULL;

UPDATE trajectories
   SET matched_workflow_id = promoted_workflow_id
 WHERE promoted_workflow_id IS NOT NULL;

CREATE INDEX trajectories_matched_workflow_idx
  ON trajectories(project_id, matched_workflow_id, started_at DESC);
