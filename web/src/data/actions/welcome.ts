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
import { clearQueryCache, gqlResult } from "../../lib/api";
import { mutate } from "./index";
import { connectAny, testAny, uploadDocuments } from "./sources";

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

/* Where each provider documents the credentials its step asks for. The URL is
   the connector catalog's own `docsUrl` — the same one the Sources wizard
   shows — so "Where do I get these?" opens the provider's page rather than a
   link this app invented.

   Loaded once when the actions object is built, not on the click: opening a
   tab from an async continuation is what popup blockers exist to stop, and the
   wizard is several steps away from the first credential form by then. A
   provider the catalog gives no URL for opens nothing. */
const docsUrls = new Map<string, string>();

function loadDocsUrls(): void {
  void gqlResult<{ connectorCatalog: { key: string; docsUrl?: string }[] }>(`{ connectorCatalog }`)
    .then((r) => {
      if (!r.ok) return;
      for (const p of r.data?.connectorCatalog ?? []) {
        if (p.docsUrl) docsUrls.set(p.key, p.docsUrl);
      }
    });
}

export function welcomeActions({ navigate }: { navigate: (href: string) => void }): WelcomeActions {
  loadDocsUrls();
  return {
    navigate,

    // A new tab, not a route: the destination is the provider's own site, and
    // it must not replace a half-finished onboarding step.
    openDocs: (provider: string) => {
      const url = docsUrls.get(provider);
      if (url) window.open(url, "_blank", "noopener,noreferrer");
    },

    // Welcome does not follow the sync, so the id connectAny answers with is
    // deliberately dropped here (the Sources wizard is the flow that polls).
    connectGithubRepo: async ({ repo, paths }) => {
      await connectAny("github", { repo: repo.trim(), paths: paths.trim() });
    },

    testConnection: ({ provider, config }) => testAny(provider, config),

    connectSource: async ({ provider, config }) => {
      await connectAny(provider, config);
    },

    uploadFiles: uploadDocuments,

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
    // handler with nothing behind it. Leave the way the rest of the app moves
    // (router push, not a full reload — the reference demo lands straight on
    // the Overview), and drop the read cache first so the Overview counts
    // what onboarding just connected instead of what it saw before.
    finish: () => { clearQueryCache(); navigate("/"); },
  };
}
