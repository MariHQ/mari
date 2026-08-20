"""Named read models used by the product agent tool registry."""

from dataclasses import dataclass

from mari_server.persistence.postgres.database import q, q1


@dataclass(frozen=True, slots=True)
class AgentToolStore:
    project_id: int

    def document(self, document_id: int):
        return q1(
            """SELECT id, title, body, snippet, source, author, updated_src
                 FROM documents WHERE project_id = %s AND id = %s""",
            (self.project_id, document_id),
        )

    def document_tags(self, document_id: int):
        return q(
            "SELECT tag FROM tags WHERE project_id = %s AND document_id = %s ORDER BY tag",
            (self.project_id, document_id),
        )

    def sources(self):
        return q(
            """SELECT id, display_name, provider, kind, status, health, docs_count
                 FROM sources WHERE project_id = %s ORDER BY id""",
            (self.project_id,),
        )

    def workflows(self):
        return q(
            """SELECT id, name, status, description FROM workflows
                 WHERE project_id = %s ORDER BY id""",
            (self.project_id,),
        )

    def workflow(self, workflow_id: int):
        return q1(
            """SELECT id, name, description, status, nodes, trigger
                 FROM workflows WHERE project_id = %s AND id = %s""",
            (self.project_id, workflow_id),
        )

    def workflow_runs(self, workflow_id: int):
        return q(
            """SELECT id, number, status, progress, stats, rows_data, triggered_by
                 FROM workflow_runs WHERE project_id = %s AND workflow_id = %s
                ORDER BY id DESC LIMIT 10""",
            (self.project_id, workflow_id),
        )

    def trajectories(self):
        return q(
            """SELECT id, prompt, status, layer2, category, macro_intent,
                      step_count, failure_count, rework_count, started_at
                 FROM trajectories WHERE project_id = %s
                ORDER BY started_at DESC, id DESC LIMIT 50""",
            (self.project_id,),
        )

    def trajectory(self, trajectory_id: int):
        return q1(
            """SELECT id, prompt, status, layer1, layer2, category, macro_intent,
                      phases, step_count, failure_count, rework_count
                 FROM trajectories WHERE project_id = %s AND id = %s""",
            (self.project_id, trajectory_id),
        )

    def trajectory_steps(self, trajectory_id: int):
        return q(
            """SELECT ordinal, tool, action_family, summary, ok
                 FROM trajectory_steps WHERE project_id = %s AND trajectory_id = %s
                ORDER BY ordinal""",
            (self.project_id, trajectory_id),
        )

    def answers(self):
        return q(
            """SELECT id, question, status, served FROM approved_answers
                 WHERE project_id = %s ORDER BY id""",
            (self.project_id,),
        )
