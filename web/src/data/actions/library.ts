/* Library actions — the workspace's editorial system.
 *
 * Four of the five tabs have a backing store and a mutation each. The Rules
 * tab's registry is compiled into LibraryRulesPanel as live RegExps and the
 * checker runs in the browser — but which pack the project writes to, whether
 * the grammar pass is on, and which rules it has turned down are the project's
 * settings, not the registry's, and those DO persist. */

import type { LibraryActions } from "@mari-design/components/pages/LibraryPage";
import { mutate } from "./index";

const UPSERT_TAG = `mutation($tag: String!, $label: String!, $kind: String!, $searchWeight: Float!, $behaviors: String!) {
  upsertTagDef(tag: $tag, label: $label, kind: $kind, searchWeight: $searchWeight, behaviors: $behaviors)
}`;
const DELETE_TAG = `mutation($tag: String!) { deleteTagDef(tag: $tag) }`;
const SET_TAG_WEIGHT = `mutation($tag: String!, $weight: Float!) { setTagWeight(tag: $tag, weight: $weight) }`;
const UPSERT_GLOSSARY = `mutation($id: Int, $term: String!, $definition: String!, $evidence: String!) {
  upsertGlossary(id: $id, term: $term, definition: $definition, evidence: $evidence)
}`;
const DELETE_GLOSSARY = `mutation($id: Int!) { deleteGlossary(id: $id) }`;
const SET_DEFAULT_PACK = `mutation($key: String!) { setDefaultStylePack(key: $key) }`;
const SET_VOICE = `mutation($voice: String!, $terms: String!, $banned: String!, $inclusive: Boolean!, $jargon: Boolean!, $sentenceCase: Boolean!) {
  setVoiceLayer(voice: $voice, terms: $terms, banned: $banned, inclusive: $inclusive, jargon: $jargon, sentenceCase: $sentenceCase)
}`;
const UPSERT_TEMPLATE = `mutation($key: String!, $name: String!, $category: String!, $description: String!, $sections: [String!], $icon: String!) {
  upsertDocumentTemplate(key: $key, name: $name, category: $category, description: $description, sections: $sections, icon: $icon)
}`;
const DELETE_TEMPLATE = `mutation($key: String!) { deleteDocumentTemplate(key: $key) }`;
const UPDATE_SETTING = `mutation($key: String!, $value: JSON!) { updateSetting(key: $key, value: $value) }`;

/* The panel colours a tag from the library's five-tone scale; the store keeps
   a `kind` from the ingest side of the product. `src/data/library.ts` maps
   kind → tone on the way in, so this is that map run backwards. It is lossy
   (several kinds share a tone), which is why an EDIT of a shipped tag keeps
   the tone it already had rather than being re-derived from a colour. */
const KIND_FOR_TONE: Record<string, string> = {
  ok: "canonical",
  info: "verified",
  attention: "factcheck",
  blocked: "blocked",
  neutral: "neutral",
};

export function libraryActions(): LibraryActions {
  return {
    /* ── tags ─────────────────────────────────────────────────────────────*/
    saveTag: async ({ id, name, tone, weight, behaviors }) => {
      await mutate(UPSERT_TAG, {
        tag: id,
        label: name,
        kind: KIND_FOR_TONE[tone] ?? "neutral",
        searchWeight: weight,
        // Stored as one "Boosts search; wins conflicts" string, which is how
        // the adapter splits it back apart.
        behaviors: behaviors.join("; "),
      });
      // `description` and `evidence` have no column in tag_definitions, so the
      // composer's description is not sent: there is nowhere for it to land.
    },
    deleteTag: async (id) => {
      await mutate(DELETE_TAG, { tag: id });
    },
    setTagWeight: async (id, weight) => {
      await mutate(SET_TAG_WEIGHT, { tag: id, weight });
    },

    /* ── glossary ─────────────────────────────────────────────────────────*/
    saveTerm: async ({ id, term, definition }) => {
      // Ids arrive as strings because that is what the panel's rows carry;
      // the store keys on an integer. A row this panel invented locally has no
      // numeric id, and upserting with none creates the term, which is right.
      const numeric = id === null ? null : Number(id);
      await mutate(UPSERT_GLOSSARY, {
        id: numeric !== null && Number.isFinite(numeric) ? numeric : null,
        term,
        definition,
        // The panel has no evidence field, so nothing is claimed about where
        // the definition came from. Empty is honest; a placeholder would not be.
        evidence: "",
      });
    },
    deleteTerm: async (id) => {
      const numeric = Number(id);
      if (!Number.isFinite(numeric)) throw new Error("That term has no id on the server yet.");
      await mutate(DELETE_GLOSSARY, { id: numeric });
    },

    /* ── style guides ─────────────────────────────────────────────────────*/
    setDefaultPack: async (key) => {
      await mutate(SET_DEFAULT_PACK, { key });
    },
    saveVoice: async (layer) => {
      await mutate(SET_VOICE, {
        voice: layer.voice, terms: layer.terms, banned: layer.banned,
        inclusive: layer.inclusive, jargon: layer.jargon, sentenceCase: layer.sentenceCase,
      });
    },
    // No create/delete for a pack: "Custom guide" is a placeholder with no
    // name, description or tone to send, so wiring it would mean inventing a
    // pack rather than saving one.

    /* ── templates ────────────────────────────────────────────────────────*/
    saveTemplate: async ({ id, name, category, description, sections, icon }) => {
      await mutate(UPSERT_TEMPLATE, { key: id, name, category, description, sections, icon });
    },
    deleteTemplate: async (id) => {
      await mutate(DELETE_TEMPLATE, { key: id });
    },

    /* ── rules ────────────────────────────────────────────────────────────*/
    /* One settings row, `rule_config`, replaced whole — it holds nothing this
       panel does not edit, so there is nothing to carry across. `statuses` is
       only the rules the workspace moved off Active, which is why an untouched
       registry stores an empty object rather than a copy of every default. */
    saveRuleConfig: async ({ pack, grammar, statuses }) => {
      await mutate(UPDATE_SETTING, { key: "rule_config", value: { pack, grammar, statuses } });
    },
  };
}
