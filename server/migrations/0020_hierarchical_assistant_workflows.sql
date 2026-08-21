ALTER TABLE assistant_workflows ADD COLUMN phases jsonb NOT NULL DEFAULT '[]';
ALTER TABLE assistant_workflows ADD COLUMN match_index jsonb NOT NULL DEFAULT '{}';
ALTER TABLE assistant_workflows ADD COLUMN embedding_profile text NOT NULL DEFAULT '';
ALTER TABLE assistant_workflows ADD COLUMN match_threshold real NOT NULL DEFAULT 0.55;

UPDATE assistant_workflows aw
   SET phases = t.phases,
       steps = COALESCE((
         SELECT jsonb_agg(jsonb_build_object(
           'ordinal', ts.ordinal, 'tool', ts.tool, 'family', ts.action_family,
           'arguments', COALESCE(ts.edited_args, ts.args),
           'disposition', ts.disposition, 'summary', ts.summary
         ) ORDER BY ts.ordinal)
           FROM trajectory_steps ts
          WHERE ts.trajectory_id = aw.trajectory_id
            AND ts.project_id = aw.project_id
            AND ts.disposition <> 'excluded'
       ), '[]'::jsonb)
  FROM trajectories t
 WHERE t.id = aw.trajectory_id AND t.project_id = aw.project_id;
