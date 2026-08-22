-- Trajectories became Workflows, and Approved answers folded into it as a tab.
--
-- Two consequences for the schema. An observed workflow can now be turned down
-- without being deleted, so it carries a disposition of its own (the one on
-- trajectory_steps grades a tool call, not the run). And an answer can be
-- promoted straight out of an observed workflow, so an approved answer records
-- where it came from, which answer it replaces, and when someone should look
-- at it again.
--
-- Both columns are nullable and defaulted: a workspace mid-rollout keeps
-- serving every answer it already had.

ALTER TABLE trajectories ADD COLUMN IF NOT EXISTS disposition text NOT NULL DEFAULT 'observed';

ALTER TABLE approved_answers ADD COLUMN IF NOT EXISTS trajectory_id int
  REFERENCES trajectories(id) ON DELETE SET NULL;
ALTER TABLE approved_answers ADD COLUMN IF NOT EXISTS supersedes int
  REFERENCES approved_answers(id) ON DELETE SET NULL;
ALTER TABLE approved_answers ADD COLUMN IF NOT EXISTS recheck_after timestamptz;

-- The Observed tab filters on disposition and status before it orders, and the
-- answers tab looks an answer's origin up by trajectory.
CREATE INDEX IF NOT EXISTS trajectories_project_disposition_idx
  ON trajectories (project_id, disposition, started_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS answers_project_trajectory_idx
  ON approved_answers (project_id, trajectory_id);
