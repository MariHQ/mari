/* Decisions ledger adapter. */

import type { DecisionsData, LedgerFilterTab } from "@mari-design/components/pages/DecisionsPage";
import type { Decision } from "@mari-design/components/features/DecisionCardFeature";
import { useEffect } from "react";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

const QUERY = `{
  decisions {
    id statement context status sourceLabel owners decidedOn
    supersededBy supersededByStatement impactSummary impactCount
  }
}`;

type Res = {
  decisions: {
    id: number; statement: string; context: string; status: string; sourceLabel: string;
    owners: string[]; decidedOn: string; supersededBy: number | null;
    supersededByStatement: string; impactSummary: string; impactCount: number;
  }[];
};

const STATUSES: Decision["status"][] = ["proposed", "ratified", "ignored", "superseded"];

/** The ledger tabs are a fixed vocabulary; only the counts come from the API.
    `status` is the page's own contract for what a tab shows — it does the
    filtering, so the ledger below the strip and the strip's counts come from
    one list. */
const TABS: LedgerFilterTab[] = [
  { id: "all", label: "All", count: 0 },
  { id: "awaiting", label: "Awaiting sign-off", count: 0, status: "proposed" },
  { id: "ratified", label: "Ratified", count: 0, status: "ratified" },
  { id: "superseded", label: "Superseded", count: 0, status: "superseded" },
];

/** The ledger files a decision set aside as either word; a tab for one shows
    the other too, exactly as the page's own filter resolves them. */
const seen = (s: Decision["status"]) => (s === "superseded" ? "ignored" : s);

const HOW_IT_WORKS =
  "Mari captures decisions where they are made, then tracks what they affect. " +
  "A proposal stays in the ledger until someone signs off; once ratified it is " +
  "linked from every document its impact touches.";

export function mapDecisions(res: Res): Decision[] {
  return (res.decisions ?? []).map<Decision>((d) => ({
    id: d.id,
    statement: d.statement,
    context: d.context,
    // An unrecognized status would silently render as a proposal, so anything
    // outside the ledger's four states is treated as still proposed.
    status: (STATUSES.includes(d.status as Decision["status"]) ? d.status : "proposed") as Decision["status"],
    source: d.sourceLabel,
    // sourceLabel is prose ("Slack · #eng-identity"); the leading word is the
    // provider whose mark the card draws.
    provider: (d.sourceLabel.split(/[\s·]/)[0] ?? "").toLowerCase(),
    owners: d.owners ?? [],
    decidedOn: d.decidedOn || undefined,
    ignoredFor: d.supersededByStatement || undefined,
    impact: {
      // The panel is closed until the user opens it; the persisted readout is
      // what the ledger shows in the meantime.
      open: false, loading: false, docs: null, tasksCreated: false,
      count: d.impactCount, summary: d.impactSummary,
    },
  }));
}

/** Pure: rows + the tab the ledger opens on → everything the page renders. */
export function buildDecisions(decisions: Decision[], filter: string): DecisionsData {
  const filters: LedgerFilterTab[] = TABS.map((t) => ({
    ...t,
    count: t.status ? decisions.filter((d) => seen(d.status) === seen(t.status!)).length : decisions.length,
  }));

  return {
    decisions,
    filter,
    filters,
    // Deprecated and unread: the rail now derives what awaits a signature from
    // `decisions` itself, records and ids included, instead of matching these
    // statements back to rows by string. It is still built only because
    // `DecisionsData.awaiting` is still a REQUIRED field of the library's type;
    // it goes the moment that field does. Derived from the same rows either
    // way, so the two cannot disagree in the meantime.
    awaiting: decisions.filter((d) => d.status === "proposed").map((d) => d.statement),
    howItWorks: HOW_IT_WORKS,
    // Capture composer and the ratify card are opened by the user.
    composer: null,
    ratify: null,
    extras: null,
  };
}

/* A decision scan lands new proposals minutes after the page asked for them,
 * and a write from `actions/decisions.ts` cannot reach this hook. So the write
 * side says "the ledger moved" and an open Decisions page re-reads — the same
 * arrangement the fact ledger uses, for the same reason. */
const listeners = new Set<() => void>();

/** Announce that the stored ledger changed. Called by the decisions actions. */
export function decisionsChanged(): void {
  for (const l of [...listeners]) l();
}

export function useDecisions(): PageData<DecisionsData> {
  const q = useQuery<Decision[]>(QUERY, { map: mapDecisions });
  const refetch = q.refetch;
  useEffect(() => {
    listeners.add(refetch);
    return () => { listeners.delete(refetch); };
  }, [refetch]);
  return {
    // "all" is where the ledger opens; the page's tab strip takes it from there.
    data: buildDecisions(q.data ?? [], "all"),
    loading: q.loading,
    error: q.error ? (q.errorText ?? "The decision ledger is temporarily unavailable.") : null,
  };
}
