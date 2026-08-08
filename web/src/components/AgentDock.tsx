// The Mari agent, docked. The visual surface is the library's ChatDock —
// this file is the app's wiring: the floating launcher, the transcript
// state machine over the /agent/chat SSE stream, and the router hookup so
// `navigate` events from the server move the SPA while the dock stays open.
//
// Mounted once inside Routed() (it needs the router), rendered only for a
// signed-in user. The transcript lives for the life of the mount; the
// server persists sessions and threads multi-turn context via session_id.
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Sparkles, X } from "lucide-react";
import { ChatDock } from "@mari-design/components";
import type { ChatMessageData, ToolCallData } from "@mari-design/components/chat/types";
import { useAuth } from "../lib/auth";
import { agentChatStream } from "../lib/agentStream";

const OFFLINE_MSG = "I can't reach the Mari API right now — start the server and try again.";

const SUGGESTIONS = [
  "What sources are connected?",
  "Find docs about connecting a source",
  "Take me to the repository audit",
];

export function AgentDock() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(() => localStorage.getItem("mari.dock") === "1");
  const [messages, setMessages] = useState<ChatMessageData[]>([]);
  const [streaming, setStreaming] = useState(false);
  const sessionRef = useRef<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const idRef = useRef(0);

  if (!user) return null;

  const toggle = (next: boolean) => {
    setOpen(next);
    localStorage.setItem("mari.dock", next ? "1" : "0");
  };

  /** Mutate the streaming assistant message in place (append text, attach a
   *  tool row, resolve one) — always the last entry while a turn runs. */
  const patchLast = (fn: (m: ChatMessageData) => ChatMessageData) =>
    setMessages((ms) => ms.map((m, i) => (i === ms.length - 1 ? fn(m) : m)));

  const send = async (text: string) => {
    if (streaming || !text.trim()) return;
    const uid = () => `m${++idRef.current}`;
    setMessages((ms) => [
      ...ms,
      { id: uid(), role: "user", content: text },
      { id: uid(), role: "assistant", content: "", tools: [], streaming: true },
    ]);
    setStreaming(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    const ok = await agentChatStream(text, sessionRef.current, {
      onMeta: (sid) => { sessionRef.current = sid; },
      onToolStart: ({ name, args }) =>
        patchLast((m) => ({ ...m, tools: [...(m.tools ?? []), { name, args, ok: null } as ToolCallData] })),
      onToolResult: ({ name, summary, ok: toolOk }) =>
        patchLast((m) => {
          const tools = [...(m.tools ?? [])];
          // Resolve the most recent still-running call of this tool.
          for (let i = tools.length - 1; i >= 0; i--) {
            if (tools[i].name === name && tools[i].ok == null) {
              tools[i] = { ...tools[i], summary, ok: toolOk };
              break;
            }
          }
          return { ...m, tools };
        }),
      onNavigate: (path) => navigate(path),
      onWarning: (message) =>
        setMessages((ms) => {
          const last = ms[ms.length - 1];
          const warn: ChatMessageData = { id: `m${++idRef.current}`, role: "warning", content: message };
          // Keep the streaming assistant bubble last so tokens keep landing in it.
          return [...ms.slice(0, -1), warn, last];
        }),
      onToken: (token) => patchLast((m) => ({ ...m, content: m.content + token })),
    }, ctrl.signal);

    patchLast((m) => ({
      ...m,
      streaming: false,
      content: ok ? m.content : OFFLINE_MSG,
    }));
    setStreaming(false);
    abortRef.current = null;
  };

  if (!open) {
    return (
      <button
        onClick={() => toggle(true)}
        aria-label="Open the Mari agent"
        title="Ask Mari"
        className="fixed bottom-5 right-5 z-40 flex h-12 w-12 items-center justify-center rounded-full bg-ink text-paper shadow-lg hover:opacity-90"
      >
        <Sparkles size={20} />
      </button>
    );
  }

  return (
    <div className="fixed bottom-5 right-5 z-40 flex h-[min(600px,calc(100dvh-2.5rem))] w-[min(400px,calc(100vw-2.5rem))]">
      <ChatDock
        className="flex-1 shadow-xl"
        title="Mari agent"
        messages={messages}
        isStreaming={streaming}
        onSend={send}
        onStop={() => abortRef.current?.abort()}
        suggestions={messages.length === 0 ? SUGGESTIONS : undefined}
        hint="The agent can search, read and edit documents, tag, sync, and navigate. Every action lands in the audit trail."
        placeholder="Ask Mari…"
        headerActions={
          <button
            onClick={() => toggle(false)}
            aria-label="Close the Mari agent"
            className="rounded p-1 text-ink/60 hover:bg-ink/5 hover:text-ink"
          >
            <X size={16} />
          </button>
        }
      />
    </div>
  );
}
