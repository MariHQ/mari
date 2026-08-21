// Agent-chat SSE client (POST /agent/chat). Named events: meta, tool_start,
// tool_result, navigate, warning, token, done. Supports AbortSignal for the
// Stop button. Ported from the pre-library console (39a55e1), where it lived
// as components/chat/stream.ts — the contract on the server side
// (server/agentchat.py) is unchanged.

import { projectHeaders } from "./api";

type AgentToolStart = { name: string; args: Record<string, unknown> };
type AgentToolResult = { name: string; summary: string; ok: boolean };
type AgentAuthRequest = { name: string; provider: string; kind: string; scopes: string[]; setupUrl: string };
type AgentWorkflow = { id: number; name: string; workflowScore: number; phaseIndex: number; stepIndex: number };

export type AgentStreamHandlers = {
  onMeta?: (sessionId: number) => void;
  onWorkflowSelected?: (workflow: AgentWorkflow) => void;
  onToolStart?: (ev: AgentToolStart) => void;
  onToolProposal?: (ev: AgentToolStart) => void;
  onToolResult?: (ev: AgentToolResult) => void;
  onAuthRequired?: (ev: AgentAuthRequest) => void;
  onNavigate?: (path: string) => void;
  onWarning?: (message: string) => void;
  onToken?: (token: string) => void;
  onDone?: (sessionId: number) => void;
};

/** Stream one agent turn. Resolves true when the stream completed (or was
 *  aborted by the caller), false when the API was unreachable. */
export async function agentChatStream(
  message: string,
  sessionId: number | null,
  handlers: AgentStreamHandlers,
  signal?: AbortSignal,
): Promise<boolean> {
  try {
    const res = await fetch("/agent/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...projectHeaders() },
      body: JSON.stringify({ session_id: sessionId, message }),
      signal,
    });
    if (!res.ok || !res.body) return false;
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    const dispatch = (frame: string) => {
      const event = frame.match(/^event:\s*(\S+)/m)?.[1] ?? "token";
      const dataText = frame.split(/\r?\n/)
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).replace(/^ /, ""))
        .join("\n");
      if (!dataText) return;
      let data: any;
      try { data = JSON.parse(dataText); } catch { return; }
      switch (event) {
        case "meta": handlers.onMeta?.(data.session_id); break;
        case "workflow_selected": handlers.onWorkflowSelected?.({ id: Number(data.id), name: String(data.name ?? ""), workflowScore: Number(data.workflow_score), phaseIndex: Number(data.phase_index), stepIndex: Number(data.step_index) }); break;
        case "tool_proposal": handlers.onToolProposal?.({ name: data.name, args: data.args ?? {} }); break;
        case "tool_start": handlers.onToolStart?.({ name: data.name, args: data.args ?? {} }); break;
        case "tool_result": handlers.onToolResult?.({ name: data.name, summary: data.summary ?? "", ok: !!data.ok }); break;
        case "auth_required": handlers.onAuthRequired?.({ name: data.name, provider: data.provider ?? "", kind: data.kind ?? "", scopes: data.scopes ?? [], setupUrl: data.setup_url ?? "" }); break;
        case "navigate": handlers.onNavigate?.(String(data.path ?? "")); break;
        case "warning": handlers.onWarning?.(String(data.message ?? "")); break;
        case "token": if (data.token) handlers.onToken?.(data.token); break;
        case "done": handlers.onDone?.(data.session_id); break;
      }
    };
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const frames = buf.split(/\r?\n\r?\n/);
      buf = frames.pop() ?? "";
      frames.forEach(dispatch);
    }
    buf += decoder.decode();
    if (buf.trim()) dispatch(buf);
    return true;
  } catch (e) {
    // A user-initiated Stop is a successful outcome, not an offline API.
    return e instanceof DOMException && e.name === "AbortError";
  }
}
