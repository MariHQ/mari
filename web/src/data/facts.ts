/* Facts ledger adapter. */

import { useState } from "react";
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

/** Status tabs are the page's vocabulary; the API supplies only the rows. */
const TABS: { id: string; label: string; match: (f: Fact) => boolean }[] = [
  { id: "all", label: "All", match: () => true },
  { id: "verified", label: "Verified", match: (f) => f.status === "Verified" },
  { id: "needs-evidence", label: "Needs evidence", match: (f) => f.status === "Needs evidence" },
  { id: "draft", label: "Draft", match: (f) => f.status === "Draft" },
];

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

/** Pure: rows + the contradiction pairs + the selected tab → everything the
 *  page renders. */
export function buildFacts(facts: Fact[], banner: FactsData["banner"], filter: string): FactsData {
  const filters: FactFilter[] = TABS.map((t) => ({
    id: t.id, label: t.label, count: facts.filter(t.match).length,
  }));

  return {
    filters,
    filter,
    facts: facts.filter(TABS.find((t) => t.id === filter)?.match ?? (() => true)),
    banner,
    // The verification-audit card re-lists facts by staleness. It is a view
    // of the same rows, so it only appears once the user asks for it.
    audit: null,
    taskAudit: null,
    impact: null,
    extras: null,
  };
}

export function useFacts(): PageData<FactsData> {
  const [filter] = useState("all");
  const q = useQuery<Res>(QUERY, { map: (d: Res) => d });
  return {
    data: buildFacts(q.data ? mapFacts(q.data) : [], q.data ? mapBanner(q.data) : null, filter),
    loading: q.loading,
    error: q.error ? (q.errorText ?? "The fact ledger is temporarily unavailable.") : null,
  };
}
