// Live sync state for one GitHub source — polls `syncStatus(sourceId)` from
// the frozen contract (GITHUB-SYNC-CONTRACT.md) every ~1.5s while a sync is
// running, then stops. `refresh()` re-fetches immediately (call it right after
// firing syncSource/resyncSource so polling resumes for the new run).

import { useCallback, useEffect, useState } from "react";
import { gql } from "../../lib/api";

type SyncStatus = {
  state: string;         // idle | running | error
  phase: string;         // listing | fetching | chunking | embedding | indexing | done | ""
  done: number;
  total: number;
  lastSyncAt: string;    // ISO 8601 or ""
  lastError: string;     // "" when healthy
  cursor: string;        // head commit sha (short) or ""
  docCount: number;
  chunkCount: number;
  embeddedCount: number;
};

const SYNC_STATUS_QUERY = `query($id: Int!) {
  syncStatus(sourceId: $id) {
    state phase done total lastSyncAt lastError cursor docCount chunkCount embeddedCount
  }
}`;

export const PHASES = ["listing", "fetching", "chunking", "embedding", "indexing"] as const;

export const PHASE_LABEL: Record<string, string> = {
  listing: "Listing", fetching: "Fetching", chunking: "Chunking",
  embedding: "Embedding", indexing: "Indexing", done: "Done",
};

export type SyncStatusResult = {
  /** null until the first response; stays null if the server can't answer. */
  status: SyncStatus | null;
  /** true when the last poll failed (server down / query not implemented yet). */
  unreachable: boolean;
  /** Re-fetch now and resume polling if a sync is running. */
  refresh: () => void;
};

export function useSyncStatus(sourceId: number | null): SyncStatusResult {
  const [status, setStatus] = useState<SyncStatus | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (sourceId == null) return;
    let alive = true;
    let timer: number | undefined;
    const poll = async () => {
      const data = await gql<{ syncStatus: SyncStatus }>(SYNC_STATUS_QUERY, { id: sourceId });
      if (!alive) return;
      if (data?.syncStatus) {
        setStatus(data.syncStatus);
        setUnreachable(false);
        // Poll only while a sync is actually running.
        if (data.syncStatus.state === "running") timer = window.setTimeout(poll, 1500);
      } else {
        setUnreachable(true);
      }
    };
    void poll();
    return () => { alive = false; if (timer !== undefined) window.clearTimeout(timer); };
  }, [sourceId, tick]);

  return { status, unreachable, refresh };
}
