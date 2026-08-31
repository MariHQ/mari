from pathlib import Path
import unittest


MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "0031_fact_intelligence.sql"
).read_text(encoding="utf-8")


class FactIntelligenceSchemaTests(unittest.TestCase):
    def test_assertions_are_bitemporal_and_immutable_by_shape(self):
        self.assertIn("CREATE TABLE fact_assertions", MIGRATION)
        self.assertIn("valid_from", MIGRATION)
        self.assertIn("valid_to", MIGRATION)
        self.assertIn("recorded_from", MIGRATION)
        self.assertIn("recorded_to", MIGRATION)
        self.assertIn("current_assertion_id", MIGRATION)

    def test_component_vectors_are_canonical_and_dimension_rotatable(self):
        section = MIGRATION.split("CREATE TABLE fact_representation_components", 1)[1]
        section = section.split("CREATE INDEX", 1)[0]
        self.assertIn("component_role", section)
        self.assertIn("rendered_text", section)
        self.assertIn("embedding_profile", section)
        self.assertIn("embedding              vector NOT NULL", section)
        self.assertNotIn("vector(768)", section)

    def test_evidence_relations_clusters_dependencies_and_budget_are_durable(self):
        for table in (
            "evidence_spans",
            "fact_evidence_groups",
            "fact_relations",
            "fact_clusters",
            "fact_dependencies",
            "fact_invalidation_events",
            "fact_impact_items",
            "fact_llm_invocations",
            "vector_index_generations",
        ):
            self.assertIn(f"CREATE TABLE {table}", MIGRATION)

    def test_llm_budget_has_configured_and_consumed_limits(self):
        section = MIGRATION.split("CREATE TABLE fact_llm_invocations", 1)[1]
        self.assertIn("max_calls", section)
        self.assertIn("max_input_tokens", section)
        self.assertIn("max_output_tokens", section)
        self.assertIn("calls_used", section)
        self.assertIn("input_tokens", section)
        self.assertIn("output_tokens", section)
        self.assertIn("visible_config", section)

    def test_fact_key_remains_rollout_compatible(self):
        self.assertNotIn("ALTER COLUMN canonical_key SET NOT NULL", MIGRATION)
        self.assertIn("WHERE canonical_key IS NOT NULL", MIGRATION)


if __name__ == "__main__":
    unittest.main()
