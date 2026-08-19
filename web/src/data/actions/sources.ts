/* Sources & connectors: the writes that fill an empty workspace.
 *
 * Three transports meet here, and which one a handler uses is decided by what
 * the server actually offers, never by tidiness:
 *
 *   • /connectors/validate + /connectors/connect  (REST) — the connector
 *     framework. `connect` answers 200 with `{error}` on a refusal rather than
 *     a status code, because a bad token is a normal outcome, so the honest
 *     failure is in the body and has to be re-thrown.
 *   • connectGithubRepo / syncSource / resyncSource / disconnectSource
 *     (GraphQL) — GitHub predates the connector framework and has its own
 *     repo-picker path.
 *   • /onboard/upload (REST, multipart) — files cannot travel through GraphQL.
 *
 * Every handler throws the server's own words. "Bad credentials",
 * "Repository acme/docs not found", "Slack is already connected": each names
 * the one thing the user has to change, and a house apology in their place
 * would leave a console stuck at zero documents with no way forward.
 */

import type { SourcesActions } from "@mari-design/components/pages/SourcesPage";
import type { Source } from "@mari-design/components/features/SourcesConnectorCard";
import { clearQueryCache, gqlResult } from "../../lib/api";
import { mutate } from "./index";

/* ── REST helpers (shared with the welcome/onboarding actions) ───────────── */

/** POST JSON and surface the real failure: FastAPI's `detail`, else the
 *  status. Never a generic message when the server sent a specific one. */
export async function postJson<T = any>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
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
  const res = await fetch("/onboard/upload", { method: "POST", body: form });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(typeof (json as any)?.detail === "string" ? (json as any).detail : `Upload failed with HTTP ${res.status}.`);
  }
  clearQueryCache();
  const rejected = ((json as UploadResult).files ?? []).filter((f) => f.error);
  if (rejected.length) {
    throw new Error(rejected.map((f) => `${f.name}: ${f.error}`).join(" · "));
  }
}

/* ── connect ────────────────────────────────────────────────────────────── */

/** GitHub is not a connector-framework provider: it has a server-side token
 *  and is chosen by repository. Both its connect and its test go elsewhere. */
const GITHUB = "github";

export async function connectAny(provider: string, config: Record<string, string>): Promise<void> {
  if (provider === GITHUB) {
    const repo = (config.repo ?? "").trim();
    if (!repo) throw new Error("Name the repository to connect, as owner/name.");
    await mutate(
      `mutation($repo: String!, $paths: String, $token: String) {
        connectGithubRepo(repo: $repo, paths: $paths, token: $token)
      }`,
      {
        repo,
        paths: (config.paths ?? "").trim() || null,
        token: (config.token ?? "").trim() || null,
      },
    );
    clearQueryCache();
    return;
  }
  // 200 with {error} is this endpoint's refusal: validate ran, nothing was
  // created, and the reason is in the body.
  const r = await postJson<{ error?: string; sourceId?: number }>("/connectors/connect", { provider, config });
  if (r.error) throw new Error(r.error);
  clearQueryCache();
}

export async function testAny(provider: string, config: Record<string, string>): Promise<{ ok: boolean; error?: string }> {
  if (provider === GITHUB) {
    return postJson<{ ok: boolean; error?: string }>("/connectors/validate", { provider, config });
  }
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
    connectSource: ({ provider, config }) => connectAny(provider, config),
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

    // Destructive; the page puts it behind a ConfirmButton. The server pauses
    // the source and its running checkpoint rather than deleting documents,
    // which is what "disconnect" has always meant here.
    disconnect: (s) => mutate(`mutation($p: String!) { disconnectSource(provider: $p) }`, { p: s.provider }),

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
       schedule — the same one the Flows editor shows. So this writes the flow,
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
