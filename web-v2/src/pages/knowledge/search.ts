// Knowledge search model — result shape and tab/filter matchers.

import { SourceKey } from "../../data/sources";

export type Result = {
  id: number; source: SourceKey; srcLabel: string; title: string; snippet: string;
  who: string; whoInitials: string; date: string; tags: string[]; kind: string;
  chunkLabel?: string; messageCount?: number; participantCount?: number; channel?: string;
};

export const TAB_NAMES = ["All", "Documents", "Conversations", "Pages", "PRs"] as const;
export type TabName = (typeof TAB_NAMES)[number];

export const tabMatch = (r: Result, t: TabName) =>
  t === "All" ? true :
  t === "Documents" ? r.kind === "page" :
  t === "Conversations" ? r.kind === "thread" :
  t === "Pages" ? r.source === "docs" || r.source === "notion" :
  r.kind === "pr";

export const SRC_LABEL_TO_KEY: Record<string, SourceKey> = {
  GitHub: "github", Slack: "slack", Docs: "docs", Notion: "notion", Drive: "gdocs", Granola: "granola",
};
export const SRC_KEY_TO_LABEL: Record<string, string> = {
  github: "GitHub", slack: "Slack", docs: "Docs", notion: "Notion", gdocs: "Drive", granola: "Granola",
};
export const BASE_SOURCES = ["GitHub", "Slack", "Docs", "Notion"];
export const TYPE_ROWS = ["Document", "Page", "Conversation", "Task"];
export const BASE_OWNERS = ["Maya Chen", "Alex Rivera", "Auth Team"];
export const FRESH_ROWS = ["Any time", "Past 24 hours", "Past 7 days", "Past 30 days"];
export const STATUS_ROWS = ["Canonical", "Draft", "Stale", "Deprecated", "Internal", "Customer-facing", "Needs review"];
export const STATUS_TAG: Record<string, string> = {
  Canonical: "canonical", Draft: "draft", Stale: "stale", Deprecated: "deprecated",
  Internal: "internal", "Customer-facing": "customer-facing", "Needs review": "needs-review",
};

export const SORT_OPTIONS = ["Best match", "Newest", "Title"] as const;
export type SortName = (typeof SORT_OPTIONS)[number];

// Seed dates are May 2024; the demo anchors "today" at May 13, 2024.
const ANCHOR = new Date("2024-05-13").getTime();
export const freshMatch = (r: Result, fresh: string) => {
  if (fresh === "Any time") return true;
  const t = new Date(r.date).getTime();
  if (isNaN(t)) return true;
  const days = fresh === "Past 24 hours" ? 1 : fresh === "Past 7 days" ? 7 : 30;
  return ANCHOR - t <= days * 86400000;
};

export const typeMatch = (r: Result, label: string) =>
  label === "Document" ? r.kind === "page" :
  label === "Page" ? r.source === "docs" || r.source === "notion" :
  label === "Conversation" ? r.kind === "thread" :
  r.kind === "pr";
