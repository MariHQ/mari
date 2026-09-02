-- Two bookkeeping columns the runtime needs and had nowhere to keep.
--
-- workflow_runs.heartbeat_at: a run that died between persists (a DB blip in
-- _persist, a killed worker) stayed 'running' until the next restart, and the
-- scheduler treated it as live forever. The runner now touches this on every
-- persist; a 'running' row whose heartbeat is older than the runtime's stale
-- threshold has no process behind it and is failed. Defaults to now() so rows
-- inserted by the previous release during a rolling deploy read as fresh.
--
-- sources.last_full_sync_at: when this source last completed an authoritative
-- full reconcile. Incremental syncs only delete on explicit tombstones, which
-- most connectors never emit, so a page deleted at the provider stayed indexed
-- until someone clicked resync. The scheduled sync step reads this to decide
-- when a full pass is due and writes it after one succeeds. NULL means never,
-- which makes the first scheduled sync after this deploy a full one.
ALTER TABLE workflow_runs ADD COLUMN heartbeat_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE sources ADD COLUMN last_full_sync_at timestamptz;
