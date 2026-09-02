from unittest import TestCase, mock

from mari_server.automations import runtime


class EmbeddingReindexStartupTests(TestCase):
    @mock.patch("mari_server.sources.sync.start_reindex")
    @mock.patch("mari_server.persistence.postgres.document_index.needs_reindex", return_value=True)
    @mock.patch("mari_server.automations.runtime.ensure_fact_scan_flow")
    @mock.patch("mari_server.automations.runtime.ensure_digest_flow")
    @mock.patch("mari_server.automations.runtime.workflow_store.quarantine_orphan_sync_workflows", return_value=[])
    @mock.patch("mari_server.automations.runtime.workflow_store.connector_sources", return_value=[])
    @mock.patch("mari_server.automations.runtime.workflow_store.active_projects")
    def test_stale_embedding_profiles_start_a_guarded_refresh(
        self, active_projects, _sources, _quarantine, _digest, _facts, needs_reindex, start_reindex,
    ):
        active_projects.return_value = [{"id": 7, "slug": "acme", "name": "Acme"}]

        runtime.seed_scheduled_flows()

        needs_reindex.assert_called_once_with()
        start_reindex.assert_called_once_with()

    @mock.patch("mari_server.sources.sync.start_reindex")
    @mock.patch("mari_server.persistence.postgres.document_index.needs_reindex", return_value=False)
    @mock.patch("mari_server.automations.runtime.ensure_fact_scan_flow")
    @mock.patch("mari_server.automations.runtime.ensure_digest_flow")
    @mock.patch("mari_server.automations.runtime.workflow_store.quarantine_orphan_sync_workflows", return_value=[])
    @mock.patch("mari_server.automations.runtime.workflow_store.connector_sources", return_value=[])
    @mock.patch("mari_server.automations.runtime.workflow_store.active_projects")
    def test_current_embedding_profiles_do_not_start_a_refresh(
        self, active_projects, _sources, _quarantine, _digest, _facts, _needs_reindex, start_reindex,
    ):
        active_projects.return_value = [{"id": 7, "slug": "acme", "name": "Acme"}]

        runtime.seed_scheduled_flows()

        start_reindex.assert_not_called()
