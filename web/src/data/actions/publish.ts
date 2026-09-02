/** Actions for knowledge chat, MCP, and bot destinations. */

import type { PublishActions } from "@mari-design/components/pages/PublishPage";
import { gql } from "../../lib/api";
import { mutate, type ActionContext } from "./index";
import { botActions } from "./bots";

const SERVERS = `{ mcpServers { id name url } }`;
const CREATE_SERVER = `mutation($name: String!, $scope: String!, $capabilities: JSON!) {
  createMcpServer(name: $name, scope: $scope, capabilities: $capabilities)
}`;
const UPDATE_SERVER = `mutation($id: Int!, $scope: String, $capabilities: JSON!) {
  updateMcpServer(id: $id, scope: $scope, capabilities: $capabilities)
}`;
const DELETE_SERVER = `mutation($id: Int!) { deleteMcpServer(id: $id) }`;
const TEST_SERVER = `mutation($id: Int!) { testMcpServer(id: $id) }`;
const CREATE_CHAT = `mutation($name: String!, $slug: String!, $title: String!, $welcome: String!, $tools: [String!]!) {
  createKnowledgeChatDestination(name: $name, slug: $slug, title: $title, welcome: $welcome, tools: $tools)
}`;
const UPDATE_CHAT = `mutation($id: Int!, $name: String!, $title: String!, $welcome: String!, $tools: [String!]!) {
  updateKnowledgeChatDestination(id: $id, name: $name, title: $title, welcome: $welcome, tools: $tools)
}`;
const DEPLOY_CHAT = `mutation($id: Int!) { deployKnowledgeChatDestination(id: $id) }`;

export function publishActions({ navigate }: ActionContext): PublishActions {
  return {
    ...botActions(),
    openSection: (section) => navigate(section === "chat" ? "/publish" : `/publish?tab=${section}`),
    openKnowledgeChat: (id) => navigate(`/publish?tab=chat&chat=${id}`),
    createKnowledgeChat: async (args) => {
      const data = await mutate(CREATE_CHAT, args);
      const id = data?.createKnowledgeChatDestination;
      if (typeof id !== "number" || id <= 0) throw new Error("The knowledge chat could not be created.");
      // A destination is immediately readable after the mutation. Explicitly
      // refresh the adapter as well as changing the selection so a create from
      // a cached/list route cannot open an editor backed by the pre-create
      // response (the visible symptom is an empty or half-drawn chat card).
      window.dispatchEvent(new Event("mari:publish-refresh"));
      navigate(`/publish?tab=chat&chat=${id}`);
    },
    updateKnowledgeChat: async (id, args) => {
      await mutate(UPDATE_CHAT, { id, ...args });
      window.dispatchEvent(new Event("mari:publish-refresh"));
    },
    deployKnowledgeChat: async (id) => {
      await mutate(DEPLOY_CHAT, { id });
      window.dispatchEvent(new Event("mari:publish-refresh"));
    },
    createServer: async ({ name, scope, capabilities }) => {
      const data = await mutate(CREATE_SERVER, { name, scope, capabilities });
      const token = data?.createMcpServer;
      if (typeof token !== "string" || !token) throw new Error("The server returned no bearer token.");
      const response = await gql<{ mcpServers: { id: number; name: string; url: string }[] }>(SERVERS);
      const row = (response?.mcpServers ?? []).find((server) => server.name === name);
      if (!row) throw new Error("The server was created but is not in the list yet.");
      return { id: row.id, url: row.url, token };
    },
    updateServer: async (id, args) => { await mutate(UPDATE_SERVER, { id, ...args }); },
    deleteServer: async (id) => { await mutate(DELETE_SERVER, { id }); },
    testServer: async (id) => {
      const data = await mutate(TEST_SERVER, { id });
      return (data?.testMcpServer ?? {}) as { ok?: boolean; latency_ms?: number; checks?: Record<string, number> };
    },
  };
}
