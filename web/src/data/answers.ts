/* Approved-answers adapter.
 *
 * Two root fields: the curated answers themselves, and the questions people
 * asked that no approved answer covers (`answerCoverageGaps`, added for this
 * page — it reads the search log and the assistant's own transcript, so an
 * empty coverage rail means nobody has asked anything uncovered, not that the
 * feature is unwired). */

import type { AnswerStat, AnswersData, HarvestSource } from "@mari-design/components/pages/AnswersPage";
import type { Answer } from "@mari-design/components/features/AnswerCard";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

/* ── query ──────────────────────────────────────────────────────────────── */

const QUERY = `{
  approvedAnswers { id question answer status owner channels sources served spark updated }
  answerCoverageGaps(limit: 8)
  answerHarvestSources
}`;

type Res = {
  approvedAnswers: {
    id: number; question: string; answer: string; status: string; owner: string;
    channels: string[]; sources: { source: string; title: string }[] | null;
    served: number; spark: number[]; updated: string;
  }[];
  answerCoverageGaps: string[];
  answerHarvestSources: { slack: number; docs: number; chat: number } | null;
};

/* ── mapping helpers ────────────────────────────────────────────────────── */

const STATUS = new Set<Answer["status"]>(["approved", "draft", "retired"]);

/* The card draws a serving toggle per channel and has a label for exactly
   three. A channel a newer bot registered has no toggle, so it is dropped
   rather than rendered as an unnamed switch. */
const CHANNELS = new Set<Answer["channels"][number]>(["slack-bot", "support-widget", "docs-site"]);

export function mapAnswers(res: Res): Answer[] {
  return (res.approvedAnswers ?? [])
    // status is the card's whole frame — badge, actions and tone. A row whose
    // status this build does not know would render as an untitled card.
    .filter((a) => STATUS.has(a.status as Answer["status"]))
    .map<Answer>((a) => ({
      id: a.id,
      question: a.question,
      answer: a.answer,
      status: a.status as Answer["status"],
      owner: a.owner,
      channels: (a.channels ?? []).filter((c): c is Answer["channels"][number] =>
        CHANNELS.has(c as Answer["channels"][number])),
      sources: a.sources ?? [],
      served: a.served,
      // [] is a real answer: an answer nobody has been served has no curve, and
      // the card then draws no sparkline rather than a flat invented one.
      spark: a.spark ?? [],
      updated: a.updated,
    }));
}

/* The headline strip. The counts are the answers we already have; the labels,
   tones and captions are the page's vocabulary for them, not the API's. */
const TILES: { label: string; sub: string; tone: AnswerStat["tone"]; of: (a: Answer[]) => number }[] = [
  { label: "Approved", sub: "serving verbatim", tone: "ok", of: (a) => a.filter((x) => x.status === "approved").length },
  { label: "Drafts", sub: "awaiting review", tone: "attention", of: (a) => a.filter((x) => x.status === "draft").length },
  { label: "Served", sub: "all time", tone: "info", of: (a) => a.reduce((n, x) => n + (x.served ?? 0), 0) },
];

/* What the scan can actually read, in the order the wizard offers it. The keys
   are the three `scanAnswerCandidates` accepts; the labels and descriptions are
   the console's words for them, and the COUNT decides whether a source is
   offered at all — a workspace with no Slack was previously invited to harvest
   Slack and got nothing back. Every offered source starts selected: there is
   no reason to open the wizard with a source you have switched off. */
const HARVEST: { key: HarvestSource["key"]; field: "slack" | "docs" | "chat"; label: string; desc: string }[] = [
  { key: "slack", field: "slack", label: "Slack threads", desc: "Questions asked and answered in channels Mari indexes." },
  { key: "docs", field: "docs", label: "Documents", desc: "The indexed corpus, mined for questions it already answers." },
  { key: "history", field: "chat", label: "Chat history", desc: "What people have actually asked the assistant." },
];

export function mapHarvestSources(res: Res): HarvestSource[] {
  const counts = res.answerHarvestSources;
  if (!counts) return [];
  return HARVEST
    .filter((h) => (counts[h.field] ?? 0) > 0)
    .map<HarvestSource>((h) => ({ key: h.key, label: h.label, desc: h.desc, on: true }));
}

/** Pure: the answers + coverage gaps → everything the page renders.
    `filter` is only which tab opens selected: the page's own tab strip filters
    the list, so handing it a pre-filtered one would leave the other tabs with
    nothing to show. */
export function buildAnswers(
  answers: Answer[], coverage: string[], filter: AnswersData["filter"],
  harvestSources: HarvestSource[] = [],
): AnswersData {
  return {
    // Empty means there is nothing to scan, and no "Harvest questions" button
    // is drawn — which is the truth about a workspace with no corpus yet.
    harvestSources,
    stats: TILES.map<AnswerStat>((t) => ({
      value: t.of(answers).toLocaleString("en-US"), label: t.label, tone: t.tone, sub: t.sub,
    })),
    filter,
    answers,
    coverage,
    // The harvest wizard and the coverage pane are routes this app does not
    // have yet; the list is what `/answers` is.
    pane: { kind: "answers" },
  };
}

export const EMPTY: AnswersData = buildAnswers([], [], "all");

/* ── adapter ────────────────────────────────────────────────────────────── */

export function useAnswers(): PageData<AnswersData> {
  const q = useQuery<{ answers: Answer[]; coverage: string[]; harvest: HarvestSource[] }>(QUERY, {
    map: (d: Res) => ({
      answers: mapAnswers(d), coverage: d.answerCoverageGaps ?? [], harvest: mapHarvestSources(d),
    }),
  });
  return {
    data: buildAnswers(q.data?.answers ?? [], q.data?.coverage ?? [], "all", q.data?.harvest ?? []),
    loading: q.loading,
    error: q.error ? (q.errorText ?? "Answers are temporarily unavailable.") : null,
  };
}
