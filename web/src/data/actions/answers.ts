/* Approved-answers writes, including the harvest wizard.
 *
 * The wizard is a real state machine now, so `scanAnswerCandidates` finally
 * has somewhere to deliver to: the scan answers with what the LLM mined, the
 * review step decides what survives, and the import saves the survivors as
 * drafts through the same mutation the composer uses.
 */

import type { AnswersActions } from "@mari-design/components/pages/AnswersPage";
import { mutate } from "../actions";

const SAVE = `mutation UpsertAnswer($id: Int, $question: String!, $answer: String!) {
  upsertAnswer(id: $id, question: $question, answer: $answer)
}`;

const SET_STATUS = `mutation SetAnswerStatus($id: Int!, $status: String!) {
  setAnswerStatus(id: $id, status: $status)
}`;

const SET_CHANNELS = `mutation SetAnswerChannels($id: Int!, $channels: [String!]!) {
  setAnswerChannels(id: $id, channels: $channels)
}`;

const SCAN = `mutation ScanAnswerCandidates($sources: [String!]!) {
  scanAnswerCandidates(sources: $sources) { question draftAnswer sourceLabel confidence }
}`;

/** The wizard's source keys, in the server's vocabulary. "history" is the
    console's name for what the API calls the chat log. */
const SOURCE_KEY: Record<string, string> = { slack: "slack", docs: "docs", history: "chat" };

/** The API grades a candidate high/medium/low; the wizard shows a percentage
    and auto-accepts at 75. These are the midpoints of those three bands, not a
    measurement — the model does not produce one, and inventing more precision
    than exists would be the lie the number is there to avoid. */
const CONFIDENCE: Record<string, number> = { high: 90, medium: 60, low: 30 };

export function answersActions(): AnswersActions {
  return {
    save: async ({ id, question, answer }) => { await mutate(SAVE, { id, question, answer }); },
    // No id: the same mutation, inserting rather than updating. It lands as a
    // draft, which is why the composer says so instead of claiming an approval.
    create: async ({ question, answer }) => { await mutate(SAVE, { question, answer }); },
    setStatus: async ({ id, status }) => { await mutate(SET_STATUS, { id, status }); },
    setChannels: async ({ id, channels }) => { await mutate(SET_CHANNELS, { id, channels }); },

    harvest: async (sources) => {
      const data = await mutate(SCAN, { sources: sources.map((s) => SOURCE_KEY[s] ?? s) });
      const rows: { question: string; draftAnswer: string; sourceLabel: string; confidence: string }[] =
        data?.scanAnswerCandidates ?? [];
      return rows.map((r) => ({
        question: r.question,
        draft: r.draftAnswer,
        source: r.sourceLabel,
        confidence: CONFIDENCE[r.confidence] ?? 60,
      }));
    },

    /* Saved one at a time rather than in a batch: there is no bulk mutation,
       and a partial failure has to leave the answers that DID save in place.
       The wizard reports the whole thing as failed if any of them throws,
       which is honest — some were saved and the list will show them. */
    importAnswers: async (drafts) => {
      for (const d of drafts) await mutate(SAVE, { question: d.question, answer: d.answer });
    },
  };
}
