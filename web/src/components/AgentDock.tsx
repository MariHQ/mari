// The Mari agent, docked. The visual surface is the library's ChatDock —
// this file is the app's wiring: the floating launcher, the transcript
// state machine over the /agent/chat SSE stream, and the router hookup so
// `navigate` events from the server move the SPA while the dock stays open.
//
// Two pieces. AgentDockProvider is mounted once inside Routed() (it needs the
// router) and owns the transcript, so it lives for the life of the session;
// the server persists sessions and threads multi-turn context via session_id.
// AgentDock is the surface, handed to the frame through ShellChrome.aside so
// the open dock is a flex sibling of the page rather than a card floating over
// half of it. Every page mounts its own frame, so the surface remounts on
// navigation; that is why the state lives above it.
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { X } from "lucide-react";
import { ChatDock, DockLauncher, DockRail } from "@mari-design/components";
import type { ChatMessageData, ToolCallData } from "@mari-design/components/chat/types";
import { useAuth } from "../lib/auth";
import { agentChatStream } from "../lib/agentStream";

const OFFLINE_MSG = "I can't reach the Mari API right now. Start the server and try again.";

const SUGGESTIONS = [
  "What sources are connected?",
  "Help me set up MCP",
  "Which review tasks are open?",
];

type DockState = {
  open: boolean;
  toggle: (next: boolean) => void;
  messages: ChatMessageData[];
  streaming: boolean;
  send: (text: string) => Promise<void>;
  stop: () => void;
};

const DockContext = createContext<DockState | null>(null);

export function AgentDockProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(() => localStorage.getItem("mari.dock") === "1");
  const [messages, setMessages] = useState<ChatMessageData[]>([]);
  const [streaming, setStreaming] = useState(false);
  const sessionRef = useRef<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const inFlightRef = useRef(false);
  const idRef = useRef(0);

  useEffect(() => () => {
    inFlightRef.current = false;
    abortRef.current?.abort();
  }, []);
  useEffect(() => {
    if (user) return;
    inFlightRef.current = false;
    abortRef.current?.abort();
    abortRef.current = null;
  }, [user]);

  const toggle = (next: boolean) => {
    setOpen(next);
    localStorage.setItem("mari.dock", next ? "1" : "0");
  };

  /** Mutate the streaming assistant message in place (append text, attach a
   *  tool row, resolve one) — always the last entry while a turn runs. */
  const patchLast = (fn: (m: ChatMessageData) => ChatMessageData) =>
    setMessages((ms) => ms.map((m, i) => (i === ms.length - 1 ? fn(m) : m)));

  const send = async (text: string) => {
    if (inFlightRef.current || !text.trim()) return;
    inFlightRef.current = true;
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
      /* The retriever has already run by the time `meta` lands, so the cited
         documents are attached to the assistant turn before the first token.
         The reply's `[3]` is a link into this list, so it has to be here while
         the text streams, not bolted on at the end. */
      onMeta: ({ sessionId, sources }) => {
        sessionRef.current = sessionId;
        if (sources.length) patchLast((m) => ({ ...m, sources }));
      },
      onWorkflowSelected: ({ id, name, workflowScore, phaseIndex, stepIndex, cacheHit }) =>
        patchLast((m) => ({ ...m, tools: [...(m.tools ?? []), {
          name: `Workflow · ${name}`, args: { workflowId: id }, ok: true,
          state: "complete", summary: `${cacheHit ? "Served reviewed cache · " : ""}Matched phase ${phaseIndex + 1}, step ${stepIndex + 1} · ${workflowScore.toFixed(2)}`,
        } as ToolCallData] })),
      onToolProposal: ({ name, args }) =>
        patchLast((m) => ({ ...m, tools: [...(m.tools ?? []), { name, args, ok: null, state: "proposed" }] })),
      onToolStart: ({ name, args }) =>
        patchLast((m) => {
          const tools = [...(m.tools ?? [])];
          let proposed = -1;
          for (let i = tools.length - 1; i >= 0; i--) {
            if (tools[i].name === name && tools[i].state === "proposed") { proposed = i; break; }
          }
          if (proposed >= 0) tools[proposed] = { ...tools[proposed], state: "running" };
          else tools.push({ name, args, ok: null, state: "running" });
          return { ...m, tools };
        }),
      onToolResult: ({ name, summary, ok: toolOk }) =>
        patchLast((m) => {
          const tools = [...(m.tools ?? [])];
          // Resolve the most recent still-running call of this tool.
          for (let i = tools.length - 1; i >= 0; i--) {
            if (tools[i].name === name && tools[i].ok == null) {
              tools[i] = { ...tools[i], summary, ok: toolOk, state: "complete" };
              break;
            }
          }
          return { ...m, tools };
        }),
      onAuthRequired: ({ name, provider, kind, scopes, setupUrl }) =>
        patchLast((m) => {
          const tools = [...(m.tools ?? [])];
          for (let i = tools.length - 1; i >= 0; i--) {
            if (tools[i].name === name && tools[i].ok == null) {
              tools[i] = { ...tools[i], ok: false, state: "auth_required", summary: `Authorization required: ${provider}`, auth: { provider, kind, scopes, setupUrl } };
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
      content: ok ? m.content : (m.content ? `${m.content}\n\n${OFFLINE_MSG}` : OFFLINE_MSG),
    }));
    inFlightRef.current = false;
    setStreaming(false);
    abortRef.current = null;
  };

  return (
    <DockContext.Provider value={{ open, toggle, messages, streaming, send, stop: () => abortRef.current?.abort() }}>
      {children}
    </DockContext.Provider>
  );
}

/** The launcher when closed; when open, the library's DockRail, which the
 *  frame lays out beside the page so the content narrows to make room (and
 *  which floats below `lg`, where the mobile frame takes over). Renders
 *  nothing without a session or outside the provider. */
export function AgentDock() {
  const dock = useContext(DockContext);
  const { user } = useAuth();
  if (!dock || !user) return null;
  const { open, toggle, messages, streaming, send, stop } = dock;

  if (!open) return <DockLauncher onClick={() => toggle(true)} label="Open the Mari agent" title="Ask Mari" />;

  return (
    <DockRail>
      <ChatDock
        className="min-h-0 flex-1 max-lg:shadow-xl"
        title="Mari agent"
        messages={messages}
        isStreaming={streaming}
        onSend={send}
        onStop={stop}
        suggestions={messages.length === 0 ? SUGGESTIONS : undefined}
        hint="The agent can search and read knowledge, explain product workflows, and take you to the right screen. It reads; it does not change anything on its own."
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
    </DockRail>
  );
}
