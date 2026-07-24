/* Decisions ledger adapter. */

import { useState } from "react";
import type { DecisionsData, LedgerFilterTab } from "@mari-design/components/pages/DecisionsPage";
import type { Decision } from "@mari-design/components/features/DecisionCardFeature";
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

/** The ledger tabs are a fixed vocabulary; only the counts come from the API. */
const TABS: { id: string; label: string; match: (d: Decision) => boolean }[] = [
  { id: "all", label: "All", match: () => true },
  { id: "awaiting", label: "Awaiting sign-off", match: (d) => d.status === "proposed" },
  { id: "ratified", label: "Ratified", match: (d) => d.status === "ratified" },
  { id: "superseded", label: "Superseded", match: (d) => d.status === "superseded" },
];

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

/** Pure: rows + the selected tab → everything the page renders. */
export function buildDecisions(decisions: Decision[], filter: string): DecisionsData {
  const filters: LedgerFilterTab[] = TABS.map((t) => ({
    id: t.id, label: t.label, count: decisions.filter(t.match).length,
  }));

  return {
    decisions: decisions.filter(TABS.find((t) => t.id === filter)?.match ?? (() => true)),
    filter,
    filters,
    // The rail lists what still needs a signature — derived from the same
    // rows the ledger shows, so the two can never disagree.
    awaiting: decisions.filter((d) => d.status === "proposed").map((d) => d.statement),
    howItWorks: HOW_IT_WORKS,
    // Capture composer and the ratify card are opened by the user.
    composer: null,
    ratify: null,
    extras: null,
  };
}

export function useDecisions(): PageData<DecisionsData> {
  const [filter] = useState("all");
  const q = useQuery<Decision[]>(QUERY, { map: mapDecisions });
  return {
    data: buildDecisions(q.data ?? [], filter),
    loading: q.loading,
    error: q.error ? (q.errorText ?? "The decision ledger is temporarily unavailable.") : null,
  };
}
