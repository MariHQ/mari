/* Facts ledger adapter. */

import { useEffect, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import type { FactsData, FactFilter } from "@mari-design/components/pages/FactsPage";
import type { Fact } from "@mari-design/components/features/FactsVerificationAudit";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

const QUERY = `{
  facts { id claim source owner status verified }
  factContradictions { factId claim otherFactId otherClaim reason detail }
}`;

type Res = {
  facts: { id: number; claim: string; source: string; owner: string; status: string; verified: string }[];
  factContradictions: {
    factId: number; claim: string; otherFactId: number;
    otherClaim: string; reason: string; detail: string;
  }[];
};

/* The status tabs used to be a hardcoded list ("Needs evidence", "Draft") that
 * the ledger never uses: every claim the API actually returns is "Verified" or
 * "Needs review", so two tabs read 0 forever and five rows sat under no tab at
 * all. The vocabulary belongs to the data, so the tabs are derived from the
 * statuses present — "All" first, then one tab per status, most rows first. */
export function tabsFor(facts: Fact[]): FactFilter[] {
  const counts = new Map<string, number>();
  for (const f of facts) counts.set(f.status, (counts.get(f.status) ?? 0) + 1);
  return [
    { id: "all", label: "All", count: facts.length },
    ...[...counts]
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .map(([status, count]) => ({ id: status.toLowerCase().replace(/\s+/g, "-"), label: status, count, status })),
  ];
}

export function mapFacts(res: Res): Fact[] {
  return (res.facts ?? []).map<Fact>((f) => ({
    id: f.id, claim: f.claim, source: f.source, owner: f.owner, status: f.status,
    // The row computes an age off this, so an unverified fact has to be null
    // rather than "" — otherwise it reads as verified at the epoch.
    verified: f.verified || null,
  }));
}

/** Every pair of stored claims that disagree, as the banner above the table.
 *  Both sides of every pair are real `facts` rows — the detector pairs claims,
 *  it never writes one — so the banner quotes the ledger back at itself rather
 *  than describing it. A consistent ledger yields null and no banner. */
export function mapBanner(res: Res): FactsData["banner"] {
  const pairs = res.factContradictions ?? [];
  if (!pairs.length) return null;
  const [first] = pairs;
  const body =
    `“${first.claim}” disagrees with “${first.otherClaim}” (${first.reason}: ${first.detail}).` +
    (pairs.length > 1 ? ` ${pairs.length - 1} more pair${pairs.length === 2 ? "" : "s"} disagree.` : "");
  return {
    title: pairs.length === 1 ? "Two claims contradict each other" : `${pairs.length} pairs of claims contradict each other`,
    body,
  };
}

/** Pure: rows + the contradiction pairs → everything the page renders. The
 *  page owns which tab is selected (it filters the rows it was given), so the
 *  ledger hands over every row and the tab it opens on. */
export function buildFacts(facts: Fact[], banner: FactsData["banner"], filter = "all"): FactsData {
  const filters = tabsFor(facts);
  return {
    filters,
    // Deep links land on a real tab or fall back to All: the home page's
    // "Facts to review" tile counts only needs-review claims, so it links
    // straight to that filter instead of a page-wide list the number never
    // described.
    filter: filters.some((f) => f.id === filter) ? filter : "all",
    facts,
    banner,
    // The verification-audit card re-lists facts by staleness. It is a view
    // of the same rows, so it only appears once the user asks for it.
    audit: null,
    taskAudit: null,
    impact: null,
    extras: null,
  };
}

/* A fact scan lands new claims minutes after the page asked for them, and a
 * write from `actions/facts.ts` cannot reach this hook. So the write side says
 * "the ledger moved" and an open Facts page re-reads. Without this the run
 * finishes, reports "4 new claims", and the table underneath still shows the
 * old rows until the next visit. */
const listeners = new Set<() => void>();

/** Announce that the stored ledger changed. Called by the facts actions. */
export function factsChanged(): void {
  for (const l of [...listeners]) l();
}

export function useFacts(): PageData<FactsData> {
  const q = useQuery<Res>(QUERY, { map: (d: Res) => d });
  const refetch = q.refetch;
  useEffect(() => {
    listeners.add(refetch);
    return () => { listeners.delete(refetch); };
  }, [refetch]);
  /* Built once per RESPONSE, not once per render. The facts list clears its
     verified / retired / failed overlays whenever `facts` changes identity —
     that is how a fresh read supersedes an optimistic one — so remapping every
     render would erase the "verified" tick the moment after it was clicked. */
  const [params] = useSearchParams();
  const tab = params.get("tab") ?? "all";
  const data = useMemo(
    () => buildFacts(q.data ? mapFacts(q.data) : [], q.data ? mapBanner(q.data) : null, tab),
    [q.data, tab],
  );
  return {
    data,
    loading: q.loading,
    error: q.error ? (q.errorText ?? "The fact ledger is temporarily unavailable.") : null,
  };
}
