// Agent-chat SSE client (POST /agent/chat) — the agentic sibling of
// lib/api.ts chatStream. Named events: meta, tool_start, tool_result,
// navigate, warning, token, done. Supports AbortSignal for the Stop button.

type AgentToolStart = { name: string; args: Record<string, unknown> };
type AgentToolResult = { name: string; summary: string; ok: boolean };

export type AgentStreamHandlers = {
  onMeta?: (sessionId: number) => void;
  onToolStart?: (ev: AgentToolStart) => void;
  onToolResult?: (ev: AgentToolResult) => void;
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
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message }),
      signal,
    });
    if (!res.ok || !res.body) return false;
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const frames = buf.split("\n\n");
      buf = frames.pop() ?? "";
      for (const frame of frames) {
        const event = frame.match(/^event: (\S+)/m)?.[1] ?? "token";
        const dataLine = frame.split("\n").find((l) => l.startsWith("data: "));
        if (!dataLine) continue;
        let data: any;
        try {
          data = JSON.parse(dataLine.slice(6));
        } catch {
          continue;
        }
        switch (event) {
          case "meta": handlers.onMeta?.(data.session_id); break;
          case "tool_start": handlers.onToolStart?.({ name: data.name, args: data.args ?? {} }); break;
          case "tool_result": handlers.onToolResult?.({ name: data.name, summary: data.summary ?? "", ok: !!data.ok }); break;
          case "navigate": handlers.onNavigate?.(String(data.path ?? "")); break;
          case "warning": handlers.onWarning?.(String(data.message ?? "")); break;
          case "token": if (data.token) handlers.onToken?.(data.token); break;
          case "done": handlers.onDone?.(data.session_id); break;
        }
      }
    }
    return true;
  } catch (e) {
    // A user-initiated Stop is a successful outcome, not an offline API.
    return e instanceof DOMException && e.name === "AbortError";
  }
}
