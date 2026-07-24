/* Approved-answers adapter.
 *
 * Two root fields: the curated answers themselves, and the questions people
 * asked that no approved answer covers (`answerCoverageGaps`, added for this
 * page — it reads the search log and the assistant's own transcript, so an
 * empty coverage rail means nobody has asked anything uncovered, not that the
 * feature is unwired). */

import type { AnswerStat, AnswersData } from "@mari-design/components/pages/AnswersPage";
import type { Answer } from "@mari-design/components/features/AnswerCard";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

/* ── query ──────────────────────────────────────────────────────────────── */

const QUERY = `{
  approvedAnswers { id question answer status owner channels sources served spark updated }
  answerCoverageGaps(limit: 8)
}`;

type Res = {
  approvedAnswers: {
    id: number; question: string; answer: string; status: string; owner: string;
    channels: string[]; sources: { source: string; title: string }[] | null;
    served: number; spark: number[]; updated: string;
  }[];
  answerCoverageGaps: string[];
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

/** Pure: the answers + coverage gaps → everything the page renders. */
export function buildAnswers(answers: Answer[], coverage: string[], filter: AnswersData["filter"]): AnswersData {
  return {
    stats: TILES.map<AnswerStat>((t) => ({
      value: t.of(answers).toLocaleString("en-US"), label: t.label, tone: t.tone, sub: t.sub,
    })),
    filter,
    answers: filter === "all" ? answers : answers.filter((a) => a.status === SINGULAR[filter]),
    coverage,
    // The harvest wizard and the coverage pane are routes this app does not
    // have yet; the list is what `/answers` is.
    pane: { kind: "answers" },
  };
}

/** Tab id → the status it filters to. The tabs are plural, the column is not. */
const SINGULAR: Record<Exclude<AnswersData["filter"], "all">, Answer["status"]> = {
  approved: "approved", drafts: "draft", retired: "retired",
};

export const EMPTY: AnswersData = buildAnswers([], [], "all");

/* ── adapter ────────────────────────────────────────────────────────────── */

export function useAnswers(): PageData<AnswersData> {
  const q = useQuery<{ answers: Answer[]; coverage: string[] }>(QUERY, {
    map: (d: Res) => ({ answers: mapAnswers(d), coverage: d.answerCoverageGaps ?? [] }),
  });
  return {
    data: buildAnswers(q.data?.answers ?? [], q.data?.coverage ?? [], "all"),
    loading: q.loading,
    error: q.error ? (q.errorText ?? "Answers are temporarily unavailable.") : null,
  };
}
