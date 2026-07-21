import type { ChipTone } from "../../components/ui";

export type DecisionStatus = "proposed" | "ratified" | "superseded";

export type Decision = {
  id: number;
  statement: string;
  context: string;
  status: DecisionStatus;
  sourceLabel: string;
  owners: string[];
  decidedOn: string;
  supersededBy: number | null;
  supersededByStatement: string;
  impactSummary: string;
  impactCount: number;
};

type ImpactDoc = { title: string; source: string; severity: string; reason: string };
export type ImpactState = {
  loading: boolean;
  open: boolean;
  summary?: string;
  docs?: ImpactDoc[];
  creatingTasks?: boolean;
  tasksCreated?: number;
};

export const DECISIONS_QUERY = `{
  decisions {
    id statement context status sourceLabel owners decidedOn
    supersededBy supersededByStatement impactSummary impactCount
  }
}`;

export const FILTERS = ["All", "Proposed", "Ratified", "Superseded"] as const;
export type Filter = (typeof FILTERS)[number];

export const STAMP_LABEL: Record<DecisionStatus, string> = {
  proposed: "Proposed",
  ratified: "Ratified",
  superseded: "Superseded",
};

export const SEVERITY: Record<string, { tone: ChipTone; label: string }> = {
  "update-required": { tone: "red", label: "Update required" },
  review: { tone: "gold", label: "Review" },
  minor: { tone: "blue", label: "Minor" },
};

export function sourceKind(label: string): "slack" | "granola" | "github" | "docs" {
  const l = label.toLowerCase();
  if (l.startsWith("slack")) return "slack";
  if (l.startsWith("granola")) return "granola";
  if (l.startsWith("github")) return "github";
  return "docs";
}
