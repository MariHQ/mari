ALTER TABLE trajectories
  ADD COLUMN selected_workflow_id int REFERENCES assistant_workflows(id) ON DELETE SET NULL,
  ADD COLUMN selected_workflow_score real,
  ADD COLUMN selected_workflow_exact boolean NOT NULL DEFAULT false,
  ADD COLUMN execution_mode text NOT NULL DEFAULT 'unknown',
  ADD COLUMN observed_cluster_id int REFERENCES assistant_workflows(id) ON DELETE SET NULL;

UPDATE trajectories
   SET observed_cluster_id = matched_workflow_id,
       selected_workflow_id = matched_workflow_id,
       execution_mode = 'legacy'
 WHERE matched_workflow_id IS NOT NULL;

CREATE INDEX trajectories_observed_cluster_idx
  ON trajectories(project_id, observed_cluster_id, started_at DESC);

CREATE INDEX trajectories_selected_workflow_idx
  ON trajectories(project_id, selected_workflow_id, started_at DESC);
