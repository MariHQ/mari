// DocReview static data and shared types.

import { fmtDate } from "../../components/ui";

/* ————— shared types ————— */

export type BlockType = "h1" | "h2" | "h3" | "p" | "li" | "code";
export type Block = {
  id: number;
  type: BlockType;
  html: string;
  lang?: string;
  /** Heading was written without a space after the hashes ("##1. Overview").
   *  Preserved so save round-trips the body byte-for-byte. */
  tight?: boolean;
};

export type Finding = { id: number; kind: string; severity: string; text: string; note: string };

export type Change = {
  id: number;
  original: string;
  proposed: string;
  rule: string;
  state: "pending" | "accepted" | "rejected";
};

export type Rev = { id: number; actor: string; verb: string; at: string };

export type DocMeta = {
  id: number; title: string; author: string; authorInitials: string;
  date: string; tags: string[]; watched: boolean; source: string;
};

/* ————— static data ————— */

// Visual skill grid → API refinement skill keys.
export const SKILLS = [
  { name: "Tighten", sub: "Remove fluff", api: "tighten" },
  { name: "Deslop", sub: "Reduce wordiness", api: "plain" },
  { name: "Clarify", sub: "Improve clarity", api: "plain" },
  { name: "Sharpen", sub: "Stronger verbs", api: "active" },
  { name: "Understate", sub: "Reduce hype", api: "inclusive" },
  { name: "Polish", sub: "Improve tone", api: "headings" },
  { name: "Critique", sub: "Overall feedback", api: "terminology" },
];

const daysFromNow = (days: number) => fmtDate(new Date(Date.now() + days * 86400000));

export const initialsOf = (name: string) =>
  name.split(/\s+/).filter(Boolean).map((w) => w[0]).join("").slice(0, 2).toUpperCase() || "?";

export const tintOf = (name: string) => {
  let h = 0;
  for (const ch of name) h = (h + ch.charCodeAt(0)) % 4;
  return h + 1;
};

export const TASK_OWNERS = [
  { init: "AK", name: "Aki Kim", tint: 2 },
  { init: "LS", name: "Lena Shah", tint: 1 },
  { init: "MC", name: "Maya Chen", tint: 1 },
];
export const TASK_DUES = [daysFromNow(3), daysFromNow(7), daysFromNow(14)];
export const TASK_PRIS: [string, string][] = [
  ["High", "var(--red)"],
  ["Medium", "var(--gold)"],
  ["Low", "var(--green)"],
];

export const BLOCK_TAG: Record<BlockType, string> = { h1: "h1", h2: "h2", h3: "h3", p: "p", li: "div", code: "pre" };
export const BLOCK_LABEL: Record<BlockType, string> = { h1: "H1", h2: "H2", h3: "H3", p: "¶", li: "•", code: "‹›" };
