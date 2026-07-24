/* Approved-answers writes.
 *
 * Not wired: the harvest wizard. `scanAnswerCandidates` exists, but the page
 * renders one static step of the wizard out of `data.pane` with no accept /
 * skip state to import from, so there is nothing for a handler to submit yet.
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

export function answersActions(): AnswersActions {
  return {
    save: async ({ id, question, answer }) => { await mutate(SAVE, { id, question, answer }); },
    // No id: the same mutation, inserting rather than updating. It lands as a
    // draft, which is why the composer says so instead of claiming an approval.
    create: async ({ question, answer }) => { await mutate(SAVE, { question, answer }); },
    setStatus: async ({ id, status }) => { await mutate(SET_STATUS, { id, status }); },
    setChannels: async ({ id, channels }) => { await mutate(SET_CHANNELS, { id, channels }); },
  };
}
