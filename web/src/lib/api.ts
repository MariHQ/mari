// Thin GraphQL client. useQuery is the canonical read hook: loading state on
// first fetch, then stale-while-revalidate from an in-memory cache so route
// revisits render real data instantly (no canned-data flicker, ever).

import { useCallback, useEffect, useState } from "react";

type GqlResult<T> = { ok: true; data: T } | { ok: false; error: string };

/* ── session recovery ─────────────────────────────────────────────────────
 *
 * A 401 from /graphql means the cookie this browser is presenting is not a
 * session the server accepts — which is a session problem, not a "service
 * unavailable" one, and rendering it as `HTTP 401` inside the read-error card
 * is the console telling a visitor to retry something no retry will fix.
 *
 * The demo deployment produces this for a reason no page can see: each Lambda
 * execution environment runs its own Postgres restored from a dump, so a
 * session minted by one instance is unknown to the next one that answers.
 * Before the hardening pass the accepted cookie was a static string, so every
 * instance honoured it and this never showed.
 *
 * api.ts must not import the auth context (auth.tsx imports this module), so
 * the auth layer registers the recovery and this only knows "ask once, then
 * retry once". Bounded on purpose: a server that keeps rejecting us must end
 * up showing its own 401, not looping. */
type SessionRecovery = () => Promise<boolean>;

let recoverSessionFn: SessionRecovery | null = null;
let recoveryInFlight: Promise<boolean> | null = null;
let recoveriesUsed = 0;

/** How many times one page load may try to re-establish a session before a
 *  401 is simply reported. Three covers a couple of unlucky instance hops; it
 *  cannot become a retry loop. */
const MAX_RECOVERIES = 3;

/** Installed by AuthProvider. Passing null (unmount) disables recovery. */
export function setSessionRecovery(fn: SessionRecovery | null) {
  recoverSessionFn = fn;
  if (!fn) recoveryInFlight = null;
}

/** Resolves true when a usable session now exists. Concurrent callers share
 *  one attempt, so twenty queries failing at once cause one recovery. */
function recoverSession(): Promise<boolean> {
  if (!recoverSessionFn || recoveriesUsed >= MAX_RECOVERIES) return Promise.resolve(false);
  if (!recoveryInFlight) {
    recoveriesUsed += 1;
    const attempt = recoverSessionFn().catch(() => false);
    recoveryInFlight = attempt;
    void attempt.then(() => { if (recoveryInFlight === attempt) recoveryInFlight = null; });
  }
  return recoveryInFlight;
}

/** Like gql(), but keeps the real failure: network error, HTTP status, or the
 *  first GraphQL error message. Use it wherever the UI must say *why*. */
export async function gqlResult<T = any>(query: string, variables?: Record<string, unknown>): Promise<GqlResult<T>> {
  try {
    const res = await fetch("/graphql", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, variables }),
    });
    // Rejected session: re-establish it once and ask again. If that is not
    // possible the 401 falls through and is reported like any other failure.
    if (res.status === 401 && await recoverSession()) {
      return gqlResult<T>(query, variables);
    }
    if (!res.ok) return { ok: false, error: `HTTP ${res.status}` };
    const json = await res.json();
    if (json.errors?.length) {
      return { ok: false, error: String(json.errors[0]?.message ?? "GraphQL error") };
    }
    return { ok: true, data: json.data as T };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "Network error" };
  }
}

/** Convenience wrapper: null on any failure. Prefer gqlResult() when the
 *  caller needs to distinguish or report the failure. */
export async function gql<T = any>(query: string, variables?: Record<string, unknown>): Promise<T | null> {
  const r = await gqlResult<T>(query, variables);
  return r.ok ? r.data : null;
}

type QueryState<T> = { data: T | null; loading: boolean; error: boolean; errorText?: string };
export type QueryResult<T> = QueryState<T> & { refetch: () => void };

// Session-lived cache of mapped results, keyed by query+variables. Holding
// mapped values (not raw payloads) keeps revisits O(1) and referentially
// stable enough for memo'd children.
const queryCache = new Map<string, unknown>();

/** Canonical read hook. First visit: { loading: true } until the API answers
 *  (render a Spinner/EmptyState — never canned data). Revisits: cached real
 *  data immediately, revalidated in the background. `refetch()` after a
 *  mutation re-runs the query and updates the cache in place. */
export function useQuery<T>(
  /** null asks nothing. Hooks cannot be skipped, but a *request* can be: a
   *  caller with no reason to query (signed out, no id yet) passing null gets
   *  the idle state and no network call. It used to have to invent a harmless
   *  query instead, which still hit /graphql and still needed a session. */
  query: string | null,
  opts?: { variables?: Record<string, unknown>; map?: (data: any) => T },
): QueryResult<T> {
  const key = query ? query + (opts?.variables ? "::" + JSON.stringify(opts.variables) : "") : "";
  const cached = key ? (queryCache.get(key) as T | undefined) : undefined;
  const [state, setState] = useState<QueryState<T>>(
    !query
      ? { data: null, loading: false, error: false }
      : cached !== undefined
      ? { data: cached, loading: false, error: false }
      : { data: null, loading: true, error: false },
  );
  const [nonce, setNonce] = useState(0);
  const refetch = useCallback(() => setNonce((n) => n + 1), []);

  // Key changed (new query/variables): swap to that key's cache entry — or a
  // fresh loading state — synchronously during render, before the refetch.
  const [prevKey, setPrevKey] = useState(key);
  if (prevKey !== key) {
    setPrevKey(key);
    const hit = key ? (queryCache.get(key) as T | undefined) : undefined;
    setState(hit !== undefined
      ? { data: hit, loading: false, error: false }
      : { data: null, loading: !!query, error: false });
  }

  useEffect(() => {
    if (!query) return;
    let alive = true;
    gqlResult(query, opts?.variables).then((r) => {
      if (!alive) return;
      if (!r.ok) {
        // Failed revalidations keep showing the cached data they had, but the
        // error flag must be honest so pages can say the data may be stale.
        setState((s) => ({ ...s, loading: false, error: true, errorText: r.error }));
        return;
      }
      const mapped = opts?.map ? opts.map(r.data) : (r.data as T);
      queryCache.set(key, mapped);
      setState({ data: mapped, loading: false, error: false });
    });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, nonce]);

  return { ...state, refetch };
}

/** Drop every cached query result (e.g. after logout). */
export function clearQueryCache() {
  queryCache.clear();
}

// (The old /chat page-stream helper lived here; the Mari agent dock's SSE
// client is src/components/chat/stream.ts.)
