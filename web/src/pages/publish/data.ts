// Publish — shared types, queries and helpers for the doc-site product
// (sites index, per-site editor, MCP servers tab).

import { useEffect, useState } from "react";
import { fmtDateTime } from "../../components/ui";
import { gql } from "../../lib/api";

// ————— types —————

export type Site = {
  id: number;
  name: string;
  domain: string;
  status: string;
  theme: any;
  sources: string[] | null;
  nav: any[] | null;
  gates: any[] | null;
  docs: number;
  warnings: number;
};

export type Release = {
  id: number;
  version: string;
  status: string;
  deployed: string;
  docs: number;
  notes: string;
};

export type McpServer = {
  id: number;
  name: string;
  url: string;
  scope: string;
  status: string;
  tools: number;
  config: any;
  token: string;
};

export type FreshToken = { name: string; url: string; token: string };

export type McpTest = { ok: boolean; latency_ms: number; checks: Record<string, number> } | "error" | null;

// ————— queries —————

export const SITES_Q = `{ sites { id name domain status theme sources nav gates docs warnings } }`;
export const releasesQ = (siteId: number) =>
  `{ releases(siteId: ${siteId}) { id version status deployed docs notes } }`;
export const MCP_Q = `{ mcpServers { id name url scope status tools config token } }`;

// ————— theme + source constants —————
// Hexes here are site-theme DATA (the built site's palette), not app chrome.

export const THEMES = [
  { key: "mari", name: "Mari Editorial", accent: "#b04e2c", bg: "#fcf9f1" },
  { key: "minimal", name: "Minimal", accent: "#2d2a22", bg: "#ffffff" },
  { key: "material", name: "Material", accent: "#35549d", bg: "#f8f9fc" },
  { key: "starlight", name: "Starlight", accent: "#43663c", bg: "#fbfaf6" },
];

export const ACCENTS = ["#b04e2c", "#35549d", "#43663c", "#6a5a9c", "#8a8171"];

export const SOURCE_TAGS = ["customer-facing", "canonical", "internal-only", "verified"];

// ————— site generators (buildSiteEx) —————
// Persisted per-site in the theme jsonb under key `generator`.

export const GENERATORS = [
  { key: "mari", label: "Mari handcrafted" },
  { key: "docusaurus", label: "Docusaurus" },
] as const;

export type BuildResult = {
  ok: boolean; generator: string;
  url?: string; pages?: number; seconds?: number; error?: string;
};

/** buildSiteEx — generator omitted (null) reuses the site's saved choice.
 *  Returns null only when the API is unreachable; build failures come back
 *  as { ok: false, error }. */
export async function buildSiteEx(id: number, generator?: string): Promise<BuildResult | null> {
  const d = await gql<{ buildSiteEx: BuildResult }>(
    `mutation($id: Int!, $g: String) { buildSiteEx(id: $id, generator: $g) }`,
    { id, g: generator ?? null },
  );
  return d?.buildSiteEx ?? null;
}

// ————— MCP capability catalogue —————

export const MCP_CAPS = [
  { key: "search", tools: 3, desc: "hybrid search over documents" },
  { key: "facts", tools: 4, desc: "verified facts + verify" },
  { key: "glossary", tools: 2, desc: "term definitions" },
  { key: "chat", tools: 1, desc: "ask Mari" },
  { key: "lineage", tools: 2, desc: "graph edges" },
  { key: "answers", tools: 2, desc: "approved answers, served verbatim" },
] as const;

export const MCP_UNITS: Record<string, string> = {
  search: "documents", facts: "facts", glossary: "terms",
  chat: "sessions", lineage: "edges", answers: "answers",
};

// ————— helpers —————

export const cap = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
export const radiusToLetter = (r: number) => (r <= 5 ? "S" : r <= 10 ? "M" : "L");
export const radiusToNumber = (l: string) => (l === "S" ? 4 : l === "M" ? 8 : 14);
export const sitePath = (id: number) => `/sites/site_${id}/`;
export const relDot = (status: string) =>
  status === "live" ? "var(--green)" : status === "rolled_back" ? "var(--gold)" : "#c9bda0";

/** Release timestamps arrive preformatted from the API ("Jul 13, 9:24 AM");
 *  anything machine-parseable goes through the canonical fmtDateTime. */
export const fmtDeployed = (s: string) =>
  Number.isNaN(Date.parse(s)) ? s : fmtDateTime(s);

export function normalizeGates(gates: any[] | null): { name: string; ok: boolean; note: string }[] | null {
  if (!Array.isArray(gates)) return null;
  if (!gates.length) return [];
  return gates.map((g: any) => ({
    name: g.gate ?? g.name,
    ok: (g.status ?? (g.ok ? "pass" : "warn")) === "pass",
    note: g.detail ?? (g.status === "pass" || g.ok ? "Passed" : cap(String(g.status ?? "warn"))),
  }));
}

export const capsOf = (s: McpServer): string[] =>
  Array.isArray(s.config?.capabilities) ? s.config.capabilities : [];

export const capTools = (keys: string[]) =>
  MCP_CAPS.filter((c) => keys.includes(c.key)).reduce((n, c) => n + c.tools, 0);

export const connectSnippet = (name: string, url: string, token?: string) =>
  `claude mcp add ${name} --transport http ${url} \\\n  --header "Authorization: Bearer ${token || "YOUR_TOKEN"}"`;

/** Has this site ever been built? (probes the static preview path) */
export function useBuilt(siteId: number, nonce: number): boolean | null {
  const [built, setBuilt] = useState<boolean | null>(null);
  useEffect(() => {
    let alive = true;
    fetch(sitePath(siteId), { method: "HEAD" })
      .then((r) => { if (alive) setBuilt(r.ok); })
      .catch(() => { if (alive) setBuilt(false); });
    return () => { alive = false; };
  }, [siteId, nonce]);
  return built;
}

export function useCopy(): [string | null, (key: string, text: string) => void] {
  const [copied, setCopied] = useState<string | null>(null);
  const copy = (key: string, text: string) => {
    navigator.clipboard.writeText(text).then(
      () => { setCopied(key); setTimeout(() => setCopied(null), 1600); },
      () => { /* clipboard unavailable */ }
    );
  };
  return [copied, copy];
}
