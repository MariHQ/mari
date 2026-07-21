// Thin GraphQL client. useQuery is the canonical read hook: loading state on
// first fetch, then stale-while-revalidate from an in-memory cache so route
// revisits render real data instantly (no canned-data flicker, ever).

import { useCallback, useEffect, useState } from "react";

type GqlResult<T> = { ok: true; data: T } | { ok: false; error: string };

/** Like gql(), but keeps the real failure: network error, HTTP status, or the
 *  first GraphQL error message. Use it wherever the UI must say *why*. */
export async function gqlResult<T = any>(query: string, variables?: Record<string, unknown>): Promise<GqlResult<T>> {
  try {
    const res = await fetch("/graphql", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, variables }),
    });
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
  query: string,
  opts?: { variables?: Record<string, unknown>; map?: (data: any) => T },
): QueryResult<T> {
  const key = query + (opts?.variables ? "::" + JSON.stringify(opts.variables) : "");
  const cached = queryCache.get(key) as T | undefined;
  const [state, setState] = useState<QueryState<T>>(
    cached !== undefined
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
    const hit = queryCache.get(key) as T | undefined;
    setState(hit !== undefined
      ? { data: hit, loading: false, error: false }
      : { data: null, loading: true, error: false });
  }

  useEffect(() => {
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
