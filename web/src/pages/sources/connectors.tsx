// Shared connector metadata for the Sources page and wizard.
// The catalog itself is server-truth: GET /connectors/catalog (registry-driven,
// CONNECTORS-CONTRACT.md). This module holds the fetch helpers, types, and
// provider marks — bespoke icons where we have them, a generic plug otherwise.

import * as Ic from "../../components/icons";
import { SourceIcon } from "../../components/shared";

export const PROVIDER_NAME: Record<string, string> = {
  github: "GitHub", slack: "Slack", gdocs: "Google Drive", gdrive: "Google Drive",
  notion: "Notion", granola: "Granola", upload: "Upload", website: "Website",
  confluence: "Confluence", jira: "Jira", linear: "Linear", zendesk: "Zendesk",
  asana: "Asana", trello: "Trello", airtable: "Airtable", dropbox: "Dropbox",
};

/** Default GitHub paths filter (glob) — matches the server contract. */
export const DEFAULT_PATHS = "**/*.md";

/* ————————————————— catalog (server truth) ————————————————— */

type CatalogField = {
  key: string;
  label: string;
  secret: boolean;
  placeholder: string;
  help: string;
};

export type CatalogEntry = {
  key: string;
  name: string;
  blurb: string;
  fields: CatalogField[];
  docsUrl: string;
  builtin: boolean;
  connected: boolean;
  sourceId: number | null;
};

/** REST base — dev talks straight to uvicorn (CORS allows :5173); prod is same-origin. */
export const CONNECT_BASE = import.meta.env.DEV ? "http://localhost:8000" : "";

/** Providers shown on the main wizard step; the rest sit behind "Show all". */
export const TOP_COUNT = 8;

export async function fetchCatalog(): Promise<CatalogEntry[] | null> {
  try {
    const res = await fetch(`${CONNECT_BASE}/connectors/catalog`);
    if (!res.ok) return null;
    return (await res.json()) as CatalogEntry[];
  } catch {
    return null;
  }
}

export async function validateConnector(
  provider: string,
  config: Record<string, string>,
): Promise<{ ok: boolean; error: string } | null> {
  try {
    const res = await fetch(`${CONNECT_BASE}/connectors/validate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, config }),
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function connectConnector(
  provider: string,
  config: Record<string, string>,
): Promise<{ sourceId?: number; error?: string } | null> {
  try {
    const res = await fetch(`${CONNECT_BASE}/connectors/connect`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, config }),
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

/* ————————————————— provider marks ————————————————— */

const BESPOKE = ["github", "slack", "gdocs", "notion", "granola", "docs"];

/** Generic plug mark for providers without a bespoke icon. */
function PlugMark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M9 7V3M15 7V3" />
      <path d="M6.5 7h11v4a5.5 5.5 0 0 1-11 0V7Z" />
      <path d="M12 16.5V19a2.5 2.5 0 0 1-2.5 2.5" />
    </svg>
  );
}

export function ProviderMark({ provider, size = 22 }: { provider: string; size?: number }) {
  const key = provider === "gdrive" ? "gdocs" : provider;
  if (BESPOKE.includes(key)) return <SourceIcon source={key as any} size={size} />;
  if (key === "upload") return <Ic.Send size={size} style={{ transform: "rotate(-45deg)" } as any} />;
  if (key === "website") return <Ic.Globe size={size} />;
  return <PlugMark size={size} />;
}
