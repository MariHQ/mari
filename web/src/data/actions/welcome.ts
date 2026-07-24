/* Onboarding: the writes that take a workspace from zero documents to one.
 *
 * Every step here does real work against the same endpoints the Sources page
 * uses, so "connect" during onboarding and "connect" from the console cannot
 * drift apart. The transports are shared from `./sources`.
 *
 * The one thing this file does differently: `harvestGlossary` returns the
 * fresh candidate list rather than only writing. The wizard reads its data
 * from a cached query that is not re-run mid-step, so a scan that only wrote
 * to the server would look, on screen, like a scan that found nothing.
 */

import type { WelcomeActions } from "@mari-design/components/pages/WelcomePage";
import type { Candidate } from "@mari-design/components/features/WelcomeGlossaryStep";
import { gqlResult } from "../../lib/api";
import { mutate } from "./index";
import { postJson, uploadDocuments } from "./sources";

type CandidateRow = {
  id: number; term: string; definition: string; evidence: string; evidenceDocId: number;
};

/** Re-read the candidates the harvest just wrote. */
async function readCandidates(): Promise<Candidate[]> {
  const r = await gqlResult<{ glossaryCandidates: CandidateRow[] }>(
    `{ glossaryCandidates { id term definition evidence evidenceDocId } }`);
  if (!r.ok) throw new Error(r.error);
  return (r.data.glossaryCandidates ?? []).map<Candidate>((c) => ({
    id: c.id,
    term: c.term,
    definition: c.definition,
    // "" for a term someone typed by hand, which was never mined from a doc.
    evidence: c.evidence ?? "",
    evidenceDocId: c.evidenceDocId || undefined,
  }));
}

export function welcomeActions({ navigate }: { navigate: (href: string) => void }): WelcomeActions {
  return {
    navigate,

    connectGithubRepo: ({ repo, paths }) =>
      mutate(`mutation($repo: String!, $paths: String) { connectGithubRepo(repo: $repo, paths: $paths) }`,
        { repo: repo.trim(), paths: paths.trim() || null }),

    connectSource: async ({ provider, config }) => {
      const r = await postJson<{ error?: string; sourceId?: number }>("/connectors/connect", { provider, config });
      // A refusal arrives as a 200 with {error}: validate ran and nothing was
      // created. Re-thrown so the step shows the provider's own words.
      if (r.error) throw new Error(r.error);
    },

    uploadFiles: uploadDocuments,

    chooseGuide: (id) =>
      mutate(`mutation($key: String!) { setDefaultStylePack(key: $key) }`, { key: id }),

    harvestGlossary: async () => {
      await mutate(`mutation { harvestGlossary }`);
      return readCandidates();
    },

    addGlossaryTerms: async (terms) => {
      /* A harvested row already exists as `candidate = true`, so accepting it
         is a promotion, not an insert. A term with no stored row (nothing in
         this app produces one today, but the type allows it) is upserted. */
      for (const t of terms) {
        if (t.id != null) {
          await mutate(`mutation($id: Int!) { promoteGlossaryCandidate(id: $id, accept: true) }`, { id: t.id });
        } else {
          await mutate(
            `mutation($term: String!, $definition: String!, $evidence: String!, $docId: Int) {
               upsertGlossary(term: $term, definition: $definition, evidence: $evidence, evidenceDocId: $docId)
             }`,
            { term: t.term, definition: t.definition, evidence: t.evidence, docId: t.evidenceDocId ?? null },
          );
        }
      }
    },

    // Onboarding is finished by leaving it: there is no "onboarding complete"
    // flag on the server, and inventing a mutation to set one would be a
    // handler with nothing behind it.
    finish: () => { window.location.href = "/"; },
  };
}
