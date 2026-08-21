/** Destinations adapter: knowledge chat, MCP, and interactive bots. */

import { useEffect, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import type {
  KnowledgeChatDestination, McpCreated, McpDraft, PublishData, PublishSection,
} from "@mari-design/components/pages/PublishPage";
import type { McpServer } from "@mari-design/components/features/PublishMcpServers";
import { useQuery } from "../lib/api";
import type { PageData } from "./types";
import { EMPTY_BOTS, mapBots, type BotsStatusResponse } from "./bots";

const QUERY = `{
  mcpServers { id name url scope status tools config }
  knowledgeChatDestinations { id name slug title welcome status url tools }
  botsStatus
}`;

type Res = BotsStatusResponse & {
  mcpServers: {
    id: number; name: string; url: string; scope: string; status: string;
    tools: number; config: { capabilities?: string[] } | null;
  }[];
  knowledgeChatDestinations: KnowledgeChatDestination[];
};

const MCP_STATUS = new Set<McpServer["status"]>(["connected", "idle"]);
const MCP_SCOPE = new Set<McpServer["scope"]>(["workspace", "product", "team", "org", "public", "project"]);

export function mapServers(res: Res): McpServer[] {
  return (res.mcpServers ?? [])
    .filter((server) => MCP_STATUS.has(server.status as McpServer["status"]) && MCP_SCOPE.has(server.scope as McpServer["scope"]))
    .map((server) => ({
      id: server.id, name: server.name, url: server.url,
      scope: server.scope as McpServer["scope"], status: server.status as McpServer["status"],
      capabilities: server.config?.capabilities ?? [],
    }));
}

const NO_DRAFT: McpDraft = { name: "", scope: "workspace", capabilities: [], toolCount: 0 };
const NO_CREATED: McpCreated = { name: "", scopeLabel: "", toolCount: 0, token: "", snippet: "" };

export const EMPTY: PublishData = {
  view: "chat", servers: [], serverCount: 0, draft: NO_DRAFT, created: NO_CREATED,
  chats: [], selectedChatId: null, slack: EMPTY_BOTS.slack, github: EMPTY_BOTS.github,
};

export function buildPublish(res: Res | null, section: PublishSection = "chat",
                             selectedChatId: number | null = null): PublishData {
  const view: PublishData["view"] = section === "mcp" ? "mcp-list" : section;
  if (!res) return { ...EMPTY, view, selectedChatId };
  const servers = mapServers(res);
  const bots = mapBots(res);
  return {
    view, servers, serverCount: servers.length, draft: NO_DRAFT, created: NO_CREATED,
    chats: (res.knowledgeChatDestinations ?? []).map((row) => ({
      ...row, status: row.status === "live" ? "live" : "draft",
    })),
    selectedChatId, slack: bots.slack, github: bots.github,
  };
}

export function usePublish(): PageData<PublishData> {
  const [params] = useSearchParams();
  const tab = params.get("tab");
  const section: PublishSection = tab === "mcp" || tab === "bots" ? tab : "chat";
  const chat = Number(params.get("chat"));
  const selectedChatId = Number.isInteger(chat) && chat > 0 ? chat : null;
  const query = useQuery<Res>(QUERY, {
    cacheKey: `destination:${section}:${selectedChatId ?? "list"}`,
    map: (data: Res) => data,
  });
  const { refetch } = query;
  useEffect(() => {
    const refresh = () => refetch();
    window.addEventListener("mari:publish-refresh", refresh);
    return () => window.removeEventListener("mari:publish-refresh", refresh);
  }, [refetch]);
  const data = useMemo(
    () => buildPublish(query.data, section, selectedChatId),
    [query.data, section, selectedChatId],
  );
  return {
    data, loading: query.loading,
    error: query.error ? (query.errorText ?? "Destinations are temporarily unavailable.") : null,
  };
}
