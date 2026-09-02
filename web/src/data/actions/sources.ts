/* Sources & connectors: the writes that fill an empty workspace.
 *
 * Two transports meet here, and which one a handler uses is decided by what
 * the server actually offers, never by tidiness:
 *
 *   • /connectors/validate + /connectors/connect  (REST) — the connector
 *     framework. `connect` answers 200 with `{error}` on a refusal rather than
 *     a status code, because a bad token is a normal outcome, so the honest
 *     failure is in the body and has to be re-thrown.
 *   • syncSource / resyncSource / disconnectSource (GraphQL) — lifecycle
 *     operations for a source that already exists.
 *   • /onboard/upload (REST, multipart) — files cannot travel through GraphQL.
 *
 * Every handler throws the server's own words. "Bad credentials",
 * "Repository acme/docs not found", "Slack is already connected": each names
 * the one thing the user has to change, and a house apology in their place
 * would leave a console stuck at zero documents with no way forward.
 */

import type { SourcesActions } from "@mari-design/components/pages/SourcesPage";
import { DuplicateSourceError } from "@mari-design/components/features/SourcesConnectorWizard";
import type { Source } from "@mari-design/components/features/SourcesConnectorCard";
import { gqlResult, invalidateQueries, projectHeaders } from "../../lib/api";
import { mutate } from "./index";

/* ── REST helpers (shared with the welcome/onboarding actions) ───────────── */

/** POST JSON and surface the real failure: FastAPI's `detail`, else the
 *  status. Never a generic message when the server sent a specific one. */
export async function postJson<T = any>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...projectHeaders() },
    body: JSON.stringify(body),
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(typeof (json as any)?.detail === "string" ? (json as any).detail : `The server answered HTTP ${res.status}.`);
  }
  return json as T;
}

type UploadResult = {
  ok: boolean; sourceId: number;
  files: { name: string; docId: number | null; chunks: number; embedded: number; error?: string }[];
};

/** POST files to the real ingestion pipeline. Per-file rejections come back
 *  inside a 200, so they are collected into one thrown message: a user who
 *  dropped in a .pdf has to be told which file was skipped and why. */
export async function uploadDocuments(files: File[]): Promise<void> {
  if (files.length === 0) return;
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  const res = await fetch("/onboard/upload", { method: "POST", headers: projectHeaders(), body: form });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(typeof (json as any)?.detail === "string" ? (json as any).detail : `Upload failed with HTTP ${res.status}.`);
  }
  invalidateQueries();
  const rejected = ((json as UploadResult).files ?? []).filter((f) => f.error);
  if (rejected.length) {
    throw new Error(rejected.map((f) => `${f.name}: ${f.error}`).join(" · "));
  }
}

/* ── connect ────────────────────────────────────────────────────────────── */

export async function connectAny(provider: string, config: Record<string, string>, name?: string): Promise<string | void> {
  // 200 with {error} is this endpoint's refusal: validate ran, nothing was
  // created, and the reason is in the body. `name` is the optional display
  // name typed in the wizard, sent only when the user gave one.
  const r = await postJson<{ error?: string; sourceId?: number; existing?: { sourceId: number; name: string } }>(
    "/connectors/connect", { provider, config, ...(name ? { name } : {}) });
  if (r.error) {
    /* A duplicate-active refusal also names the live source it collided with.
       The prose still travels as the thrown message, exactly like every other
       refusal; the structured `existing` rides on DuplicateSourceError (the
       library duck-types its shape), which is what lets the wizard offer
       "Edit the existing source" instead of a dead end. Ids become the
       opaque strings the library carries. */
    if (r.existing?.sourceId != null) {
      throw new DuplicateSourceError(r.error, { sourceId: String(r.existing.sourceId), name: r.existing.name ?? "" });
    }
    throw new Error(r.error);
  }
  invalidateQueries();
  // The id is what lets the wizard follow the first sync it just started.
  return r.sourceId != null ? String(r.sourceId) : undefined;
}

export async function testAny(provider: string, config: Record<string, string>): Promise<{ ok: boolean; error?: string }> {
  const r = await postJson<{ ok: boolean; error?: string }>("/connectors/validate", { provider, config });
  return { ok: r.ok, error: r.error };
}

/* ── factory ────────────────────────────────────────────────────────────── */

/** The numeric source id the sync mutations take. The card carries it as a
 *  string because ids are opaque to the library. */
const idOf = (s: Source): number => {
  const n = Number(s.id);
  if (!Number.isFinite(n)) throw new Error(`This source has no server id, so it cannot be synced from here.`);
  return n;
};

export function sourcesActions(): SourcesActions {
  return {
    testConnection: ({ provider, config }) => testAny(provider, config),
    connectSource: ({ provider, config, name }) => connectAny(provider, config, name),

    /* One reading of a running sync, from the in-memory ingest registry. The
       card and the connect dialog poll this to completion — the server's
       "listing" was previously the last thing the page ever learned. */
    syncProgress: async (sourceId: string) => {
      const r = await gqlResult<{ syncStatus: { state: string; phase: string; done: number; total: number; lastError: string } }>(
        `query($id: Int!) { syncStatus(sourceId: $id) { state phase done total lastError } }`, { id: Number(sourceId) });
      if (!r.ok) throw new Error(r.error);
      const st = r.data?.syncStatus;
      if (!st) return { state: "done" as const };
      if (st.state === "running") {
        return { state: "running" as const, phase: st.phase || "listing", done: st.done, total: st.total };
      }
      if (st.state === "error") {
        // lastError is the live registry message, falling back server-side to
        // the stored config value. A crash before anything was stored has no
        // words anywhere, so only then does a generic line stand in.
        invalidateQueries();
        return { state: "failed" as const, error: st.lastError || "The sync failed without reporting a reason." };
      }
      invalidateQueries();
      return { state: "done" as const, done: st.done, total: st.total };
    },
    uploadFiles: uploadDocuments,

    // Long-running by design: the mutation returns once the server has
    // accepted the run, and the page's sync-status polling owns it from there.
    // `false` is not a failure of the request, it is `ingest.start_sync`
    // declining because this source already has a run in flight, and saying
    // so beats a button that reports success and changes nothing.
    syncNow: async (s) => {
      const d = await mutate(`mutation($id: Int!) { syncSource(sourceId: $id) }`, { id: idOf(s) });
      if (d?.syncSource === false) throw new Error("A sync is already running for this source.");
    },
    fullResync: async (s) => {
      const d = await mutate(`mutation($id: Int!) { resyncSource(sourceId: $id) }`, { id: idOf(s) });
      if (d?.resyncSource === false) throw new Error("A sync is already running for this source, so it cannot be rebuilt yet.");
    },

    /* Corrects stored connector settings in place. `updateSourceConfig` MERGES
       the given keys into the config jsonb, which is why the dialog sends only
       the fields the user changed: an untouched secret stays stored. The
       server refuses the identity keys (`repo`, `provider_key`) with its own
       words, and those reach the dialog verbatim. The corrected settings only
       matter after a rebuild, so the full resync is started here too. Unlike
       `fullResync`, a `false` answer is tolerated: the save already succeeded,
       and false means a run is in flight on this source anyway. */
    updateConfig: async (s, config) => {
      await mutate(
        `mutation($provider: String!, $config: JSON!) { updateSourceConfig(provider: $provider, config: $config) }`,
        { provider: s.provider, config });
      await mutate(`mutation($id: Int!) { resyncSource(sourceId: $id) }`, { id: idOf(s) });
    },

    // Destructive; the page puts it behind a ConfirmButton. The server pauses
    // the source and its running checkpoint rather than deleting documents,
    // which is what "disconnect" has always meant here.
    // `false` is the server declining: pauseSource resolves the id among
    // connector rows only, so a legacy row (an orphan the retired
    // connectSource mutation left) answers false and nothing was paused.
    // Throwing keeps the card from drawing it paused.
    disconnect: async (s) => {
      const d = await mutate(`mutation($id: Int!) { pauseSource(sourceId: $id) }`, { id: idOf(s) });
      if (d?.pauseSource === false) throw new Error("This source has no connector behind it, so it cannot be paused. Remove it and connect again.");
    },

    /* The real delete disconnect never was: the server drops the source row,
       its documents and everything hanging off them, its checkpoints, and its
       scheduled sync flow, in one transaction. Connector and legacy rows
       (an orphan the retired connectSource mutation left) both go; refusals
       are the server's own words — the upload row, "A sync for this source
       is still running." — and they reach the confirm dialog verbatim.
       `false` is the row already being gone, which is what removing wanted. */
    removeSource: async (s, deleteDocuments) => {
      await mutate(`mutation($id: Int!, $deleteDocuments: Boolean!) {
        removeSource(sourceId: $id, deleteDocuments: $deleteDocuments)
      }`, { id: idOf(s), deleteDocuments });
      invalidateQueries();
    },

    /* A first sync that failed left a real `sources` row behind — the connect
       succeeded, the ingest did not — so retrying is starting that row's sync
       again. The failed panel carries only the provider key, which is what
       resolves the row. */
    retryFirstSync: async (provider: string) => {
      const r = await gqlResult<{ sourcePulse: { id: number; provider: string }[] }>(
        `{ sourcePulse { id provider } }`);
      if (!r.ok) throw new Error(r.error);
      const row = (r.data?.sourcePulse ?? []).find((s) => s.provider === provider);
      if (!row) throw new Error(`${provider} is not connected to this workspace, so there is no sync to retry.`);
      const d = await mutate(`mutation($id: Int!) { syncSource(sourceId: $id) }`, { id: row.id });
      if (d?.syncSource === false) throw new Error("A sync is already running for this source.");
    },

    /* The cadence is not a column on the source: every connected source gets a
       schedule-triggered "Sync <name>" flow, and that flow's trigger IS the
       schedule the sync runtime reads. So this writes the flow,
       and `sourcePulse` reads it back, which is why the two can never disagree.
       `null` means manual only, which is a trigger with no event. */
    setSyncSchedule: async (s, everyMinutes) => {
      const r = await gqlResult<{ sourcePulse: { id: number; syncFlowId: number | null }[] }>(
        `{ sourcePulse { id syncFlowId } }`);
      if (!r.ok) throw new Error(r.error);
      const flowId = (r.data?.sourcePulse ?? []).find((x) => x.id === idOf(s))?.syncFlowId;
      if (flowId == null) throw new Error("This source has no sync flow, so there is no schedule to set.");
      const trigger = everyMinutes === null
        ? JSON.stringify({ on: "" })
        : JSON.stringify({ on: "schedule", every_minutes: everyMinutes });
      const d = await mutate(
        `mutation($id: Int!, $trigger: String!) { setWorkflowTrigger(workflowId: $id, trigger: $trigger) }`,
        { id: flowId, trigger });
      if (d?.setWorkflowTrigger === false) throw new Error("That sync flow is no longer in this workspace.");
    },
  };
}
