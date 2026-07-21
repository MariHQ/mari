// Connector catalog for the Welcome wizard — driven by GET /connectors/catalog
// (CONNECTORS-CONTRACT.md). Every provider listed here is real and connectable:
// github has its dedicated repo-picker flow, upload posts to /onboard/upload,
// and everything else goes through the generic credential form
// (POST /connectors/validate → /connectors/connect → syncStatus polling).
// While the catalog endpoint isn't reachable, LOCAL_CATALOG mirrors the same
// 14 providers with the same field-spec shape; connect attempts still talk to
// the server and surface its honest errors.

import { useEffect, useState } from "react";
import * as Ic from "../../components/icons";
import { DriveMark, GitHubMark, NotionMark, SlackMark } from "../../components/icons";
import { IconRing } from "../../components/ui";

type FieldSpec = {
  key: string;
  label: string;
  secret: boolean;
  placeholder?: string;
  help?: string;
};

export type CatalogProvider = {
  key: string;
  name: string;
  blurb: string;
  fields: FieldSpec[];
  docsUrl?: string;
  connected: boolean;
  sourceId: number | null;
};

/** A connection made during this wizard session — real sourceIds only. */
export type SessionConnection = {
  provider: string;
  name: string;
  sourceId: number;
  /** e.g. the repo name, file count — shown in the finish summary. */
  detail: string;
  /** true when the source syncs via syncStatus (github + framework connectors). */
  polls: boolean;
};

/** The 8 most-likely tiles shown before "Show all" (contract order). */
export const TOP_PROVIDERS = ["github", "slack", "upload", "website", "notion", "gdrive", "confluence", "jira"];

const token = (label = "API token", placeholder = ""): FieldSpec => ({
  key: "token", label, secret: true, placeholder,
});
const siteUrl = (placeholder: string): FieldSpec => ({
  key: "site_url", label: "Site URL", secret: false, placeholder,
});
const email = (): FieldSpec => ({
  key: "email", label: "Account email", secret: false, placeholder: "you@team.com",
});

/** Same providers + field shapes as the server catalog (contract §Providers). */
const LOCAL_CATALOG: CatalogProvider[] = ([
  { key: "github", name: "GitHub", blurb: "Markdown docs from your repositories.", fields: [] },
  {
    key: "slack", name: "Slack", blurb: "Channel history from selected public channels.",
    fields: [
      { key: "bot_token", label: "Bot token", secret: true, placeholder: "xoxb-…", help: "OAuth & Permissions → Bot User OAuth Token" },
      { key: "channels", label: "Channels", secret: false, placeholder: "general, engineering", help: "Comma-separated public channel names" },
    ],
    docsUrl: "https://api.slack.com/apps",
  },
  { key: "upload", name: "Upload files", blurb: "Markdown and text files from your device.", fields: [] },
  {
    key: "website", name: "Web page", blurb: "Crawl a docs site — same-origin pages from a start URL.",
    fields: [{ key: "start_url", label: "Start URL", secret: false, placeholder: "https://docs.example.com" }],
  },
  {
    key: "notion", name: "Notion", blurb: "Pages and databases your team writes in.",
    fields: [token("Internal integration token", "ntn_…")],
    docsUrl: "https://developers.notion.com/docs/create-a-notion-integration",
  },
  {
    key: "gdrive", name: "Google Drive", blurb: "Docs from shared folders via a service account.",
    fields: [{ key: "service_account_json", label: "Service account JSON", secret: true, placeholder: '{"type": "service_account", …}' }],
  },
  {
    key: "confluence", name: "Confluence", blurb: "Spaces and pages from your wiki.",
    fields: [siteUrl("https://team.atlassian.net/wiki"), email(), token("API token")],
  },
  {
    key: "jira", name: "Jira", blurb: "Issues and their discussions.",
    fields: [siteUrl("https://team.atlassian.net"), email(), token("API token")],
  },
  {
    key: "linear", name: "Linear", blurb: "Issues from your teams.",
    fields: [token("Personal API key", "lin_api_…")],
  },
  {
    key: "zendesk", name: "Zendesk", blurb: "Tickets and help-center articles.",
    fields: [{ key: "subdomain", label: "Subdomain", secret: false, placeholder: "yourteam" }, email(), token("API token")],
  },
  {
    key: "asana", name: "Asana", blurb: "Projects and task descriptions.",
    fields: [token("Personal access token")],
  },
  {
    key: "trello", name: "Trello", blurb: "Boards and card descriptions.",
    fields: [{ key: "api_key", label: "API key", secret: false }, token("Token")],
  },
  {
    key: "airtable", name: "Airtable", blurb: "Records from a base.",
    fields: [token("Personal access token", "pat…"), { key: "base_id", label: "Base ID", secret: false, placeholder: "app…" }],
  },
  {
    key: "dropbox", name: "Dropbox", blurb: "Documents from your team folders.",
    fields: [token("Access token")],
  },
] as Omit<CatalogProvider, "connected" | "sourceId">[]).map((p) => ({ ...p, connected: false, sourceId: null }));

export type CatalogState = {
  providers: CatalogProvider[];
  /** true = the list came from GET /connectors/catalog; false = local fallback. */
  live: boolean;
  loading: boolean;
  refetch: () => void;
};

/** Fetch the real catalog; fall back to LOCAL_CATALOG (same shape) when the
 *  endpoint isn't reachable — connect/validate calls still go to the server. */
export function useCatalog(): CatalogState {
  const [state, setState] = useState<{ providers: CatalogProvider[]; live: boolean; loading: boolean }>({
    providers: LOCAL_CATALOG, live: false, loading: true,
  });
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await fetch("/connectors/catalog");
        const isJson = (r.headers.get("content-type") ?? "").includes("json");
        if (!r.ok || !isJson) throw new Error();
        const list = (await r.json()) as CatalogProvider[];
        if (!Array.isArray(list) || list.length === 0) throw new Error();
        if (alive) setState({ providers: list, live: true, loading: false });
      } catch {
        if (alive) setState({ providers: LOCAL_CATALOG, live: false, loading: false });
      }
    })();
    return () => { alive = false; };
  }, [nonce]);
  return { ...state, refetch: () => setNonce((n) => n + 1) };
}

/** Brand mark where one exists; neutral ring + initial otherwise. */
export function ProviderGlyph({ provider, name, size = 26 }: { provider: string; name: string; size?: number }) {
  switch (provider) {
    case "github": return <GitHubMark size={size} />;
    case "slack": return <SlackMark size={size} />;
    case "gdrive": return <DriveMark size={size} />;
    case "notion": return <NotionMark size={size} />;
    case "upload": return <Ic.Send size={size} />;
    case "website": return <Ic.Globe size={size} />;
    default:
      return <IconRing size={size}><span className="wc-glyph-initial">{name[0]}</span></IconRing>;
  }
}
