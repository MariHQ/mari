/* Library adapter — the workspace's editorial system.
 *
 * Four of the five tabs have a backing store: tags (`tag_definitions`),
 * glossary (`glossary`), style guides (`style_guides` + `style_rules`) and
 * templates (`document_templates`). The rules tab is the exception — its
 * registry holds live RegExps, so it lives inside `LibraryRulesPanel` and the
 * panel exports the count rather than asking us for a number only it knows. */

import type { LibraryData, LibraryTab } from "@mari-design/components/pages/LibraryPage";
import type { TagDef } from "@mari-design/components/features/LibraryTagsPanel";
import type { Term } from "@mari-design/components/features/LibraryGlossaryPanel";
import type { Guide, VoiceLayer } from "@mari-design/components/features/LibraryGuidesPanel";
import type { Template, TemplateIcon } from "@mari-design/components/features/LibraryTemplatesPanel";
import { RULE_COUNT, type CheckerDoc } from "@mari-design/components/features/LibraryRulesPanel";
import { useMemo } from "react";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";

/* ── query ──────────────────────────────────────────────────────────────── */

const QUERY = `{
  tagDefs { tag label kind searchWeight isDefault behaviors usage }
  glossary { id term definition owner updated }
  graphStats { docs }
  workspace { name }
  styleGuides { key name description tone builtin rules preview }
  defaultStylePack
  voiceLayer { voice terms banned inclusive jargon sentenceCase }
  documentTemplates { key name category description sections icon standard }
  search(query: "", k: 3) { id title source body }
}`;

type Res = {
  tagDefs: {
    tag: string; label: string; kind: string; searchWeight: number;
    isDefault: boolean; behaviors: string; usage: number;
  }[];
  glossary: { id: number; term: string; definition: string; owner: string; updated: string }[];
  graphStats: { docs: number } | null;
  workspace: { name: string } | null;
  styleGuides: {
    key: string; name: string; description: string; tone: string;
    builtin: boolean; rules: number; preview: string[];
  }[];
  defaultStylePack: string;
  voiceLayer: {
    voice: string; terms: string; banned: string;
    inclusive: boolean; jargon: boolean; sentenceCase: boolean;
  } | null;
  documentTemplates: {
    key: string; name: string; category: string; description: string;
    sections: string[]; icon: string; standard: boolean;
  }[];
  search: { id: number; title: string; source: string; body: string }[];
};

/* ── mapping helpers ────────────────────────────────────────────────────── */

/* tag_definitions.kind is a pill-style key from the ingest side of the product
   ("canonical", "factcheck", "approval", …). The panel colors a tag from the
   library's five-tone scale. Anything unmapped is neutral — a tone, not a
   claim about what the tag means. */
const TONE: Record<string, TagDef["tone"]> = {
  canonical: "ok",
  verified: "info",
  approval: "info",
  factcheck: "attention",
  stale: "attention",
  blocked: "blocked",
  neutral: "neutral",
};

export function mapTags(res: Res): TagDef[] {
  return (res.tagDefs ?? []).map<TagDef>((t) => ({
    id: t.tag,
    name: t.label,
    tone: TONE[t.kind] ?? "neutral",
    weight: t.searchWeight,
    usage: t.usage,
    // Stored as one "Boosts search; wins conflicts" string; the row draws chips.
    behaviors: t.behaviors ? t.behaviors.split(";").map((b) => b.trim()).filter(Boolean) : [],
    standard: t.isDefault,
    // `description` and `evidence` have no column in tag_definitions (see the
    // report). Empty renders the row without them rather than restating the
    // label as if it were a definition.
    description: "",
    evidence: "",
  }));
}

export function mapTerms(res: Res): Term[] {
  return (res.glossary ?? []).map<Term>((g) => ({
    id: String(g.id), term: g.term, definition: g.definition, owner: g.owner, updated: g.updated,
  }));
}

/* style_guides.tone is a colour, not a claim — the same five-tone scale the
   IconRing draws. A tone from a newer writer falls back to the neutral one
   rather than tinting a pack a meaning nobody gave it. */
const GUIDE_TONES = new Set<Guide["tone"]>(["ink", "ok", "attention", "blocked", "info"]);

/** Style packs, with the rule count and preview the server read straight off
 *  `style_rules` — so a pack can never advertise a rule it does not contain. */
export function mapGuides(res: Res): Guide[] {
  return (res.styleGuides ?? []).map<Guide>((g) => ({
    id: g.key,
    name: g.name,
    description: g.description,
    rules: g.rules,
    tone: GUIDE_TONES.has(g.tone as Guide["tone"]) ? (g.tone as Guide["tone"]) : "ink",
    preview: g.preview ?? [],
  }));
}

/* The gallery draws seven glyphs. A template stored with an icon this build
   has no glyph for renders as a plain document rather than as nothing. */
const TEMPLATE_ICONS = new Set<TemplateIcon>([
  "clipboard", "git-fork", "shield-check", "file-text", "sprout", "book-open", "megaphone",
]);

export function mapTemplates(res: Res): Template[] {
  return (res.documentTemplates ?? []).map<Template>((t) => ({
    id: t.key,
    name: t.name,
    category: t.category,
    description: t.description,
    sections: t.sections ?? [],
    standard: t.standard,
    icon: TEMPLATE_ICONS.has(t.icon as TemplateIcon) ? (t.icon as TemplateIcon) : "file-text",
  }));
}

/** Documents the rules checker can be pointed at: real corpus text, not a
 *  sample. `search` with an empty query returns most-recently-updated. */
export function mapCheckerDocs(res: Res): CheckerDoc[] {
  return (res.search ?? [])
    .filter((d) => d.body)
    .map<CheckerDoc>((d) => ({
      id: String(d.id), label: d.title, source: d.source, text: d.body,
    }));
}

/** A workspace that has not written a voice layer down: blank fields, every
 *  switch off. `voiceLayer` returns exactly this shape for such a workspace,
 *  so this is only the fallback for a response that has not arrived. */
const NO_VOICE: VoiceLayer = {
  voice: "", terms: "", banned: "", inclusive: false, jargon: false, sentenceCase: false,
};

/* ── mapper ─────────────────────────────────────────────────────────────── */

export const EMPTY: LibraryData = {
  tab: "tags",
  tags: [], totalDocs: 0, checkerDocs: [], workspace: "",
  terms: [], guides: [], defaultPack: "", voice: NO_VOICE, templates: [],
  // Deprecated and no longer read: the tab strip counts the very collections
  // it is about to render, so a badge can no longer say 40 over a panel
  // drawing 12. Still built only because `LibraryData.counts` is still a
  // REQUIRED field of the library's type; it goes when that field does.
  counts: { tags: 0, rules: RULE_COUNT, glossary: 0, guides: 0, templates: 0 },
};

/** Pure: the whole response → everything the Library renders. */
export function buildLibrary(res: Res | null, tab: LibraryTab): LibraryData {
  if (!res) return { ...EMPTY, tab };
  const tags = mapTags(res);
  const terms = mapTerms(res);
  const guides = mapGuides(res);
  const templates = mapTemplates(res);
  return {
    tab,
    tags,
    totalDocs: res.graphStats?.docs ?? 0,
    checkerDocs: mapCheckerDocs(res),
    workspace: res.workspace?.name ?? "",
    terms,
    guides,
    // The pack key this workspace adopted. "" when nobody has chosen one, and
    // the panel then opens with no pack selected.
    defaultPack: res.defaultStylePack ?? "",
    voice: res.voiceLayer ?? NO_VOICE,
    templates,
    // Deprecated and unread — see EMPTY. Derived from the same collections the
    // page counts for itself, so while the field survives it cannot disagree.
    counts: {
      tags: tags.length,
      // The rule registry holds live RegExps, so it stays inside
      // LibraryRulesPanel, and the panel exports its own size. `styleRules` is
      // the server's separate per-pack registry — what the guides tab counts —
      // so badging it here would name a number the rules tab does not show.
      rules: RULE_COUNT,
      glossary: terms.length,
      guides: guides.length,
      templates: templates.length,
    },
  };
}

/* ── adapter ────────────────────────────────────────────────────────────── */

export function useLibrary(): PageData<LibraryData> {
  const q = useQuery<Res>(QUERY, { map: (d: Res) => d });
  /* Built once per RESPONSE, not once per render. The tags, glossary and
     template panels each seed an editable list from these arrays but never
     resync, so a per-render identity does not clobber them today — this is the
     five mappers, not a live bug. It also stops the panels' identity churning
     under any resync they gain later. */
  const data = useMemo(() => buildLibrary(q.data, "tags"), [q.data]);
  return {
    data,
    loading: q.loading,
    error: q.error ? (q.errorText ?? "The library is temporarily unavailable.") : null,
  };
}
