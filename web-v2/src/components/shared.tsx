import * as Ic from "./icons";
import { SourceKey } from "../data/sources";
import { DocsMark, DriveMark, GitHubMark, GranolaMark, NotionMark, SlackMark } from "./icons";
import { TagChip } from "./TagChip";

export function SourceIcon({ source, size = 22 }: { source: SourceKey; size?: number }) {
  switch (source) {
    case "github": return <GitHubMark size={size} />;
    case "slack": return <SlackMark size={size} />;
    case "gdocs": return <DriveMark size={size} />;
    case "notion": return <NotionMark size={size} />;
    case "granola": return <GranolaMark size={size} />;
    case "docs": return <DocsMark size={size} />;
  }
}

const PILL_LABELS: Record<string, string> = {
  canonical: "Canonical",
  verified: "Verified",
  stale: "Stale",
  factcheck: "Fact check",
  approval: "Approval",
  "needs-review": "Needs review",
};

export function Pill({ kind, text }: { kind: string; text?: string }) {
  if (["canonical", "draft", "stale", "deprecated", "internal", "customer-facing", "needs-review", "verified"].includes(kind)) {
    return <TagChip tag={kind} label={text} />;
  }
  const cls = kind === "needs-review" ? "stale" : kind;
  const icon =
    kind === "canonical" ? <Ic.CheckCircle size={13} /> :
    kind === "verified" ? <Ic.ShieldCheck size={13} /> :
    kind === "stale" || kind === "needs-review" ? <Ic.Bell size={12} /> :
    kind === "factcheck" ? <Ic.Check size={12} /> :
    <Ic.Check size={12} />;
  return (
    <span className={`pill pill--${cls}`}>
      {icon}
      {text ?? PILL_LABELS[kind] ?? kind}
    </span>
  );
}
