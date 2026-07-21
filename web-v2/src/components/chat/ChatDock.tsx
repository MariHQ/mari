// ChatDock — the Claude-Code-style Mari agent, docked as a persistent
// right-side pane. It survives route changes (mounted once in App), streams
// tokens, renders tool-call rows (⏺ running → ✓/✗ summary, expandable), and
// obeys `navigate` events from the server so the agent can drive the SPA
// while the dock stays open. Visual language: Mari's notebook materials
// (tokens from styles.css) with Claude Code's structure and density.

import { FormEvent, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import * as Ic from "../icons";
import { Button, Chip, IconRing, Menu, MenuItem, MenuLabel, Spinner } from "../ui";
import { gql } from "../../lib/api";
import { agentChatStream } from "./stream";
import "./chat.css";

// ————— dock open/close store (topbar toggle + dock share it) —————

let dockOpen = localStorage.getItem("mari.dock") === "1";
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

export function toggleDock(force?: boolean) {
  dockOpen = force ?? !dockOpen;
  localStorage.setItem("mari.dock", dockOpen ? "1" : "0");
  emit();
}

function useDockOpen() {
  return useSyncExternalStore(
    (cb) => { listeners.add(cb); return () => listeners.delete(cb); },
    () => dockOpen,
  );
}

/** Topbar toggle — the chat-bubble affordance that summons the agent. */
export function ChatDockToggle() {
  const open = useDockOpen();
  return (
    <button
      className="iconbtn"
      onClick={() => toggleDock()}
      aria-label={open ? "Close Mari agent" : "Open Mari agent"}
      aria-pressed={open}
      title="Mari agent"
    >
      <Ic.Chat size={17} />
    </button>
  );
}

// ————— message model —————

type ToolItem = {
  kind: "tool";
  name: string;
  args: Record<string, unknown>;
  summary: string;
  ok: boolean | null; // null = still running
};
type Item =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string }
  | { kind: "warning"; text: string }
  | ToolItem;

type SessionRow = { id: number; title: string; messages: { role: string; content: string; sources: unknown }[] };

const SESSIONS_QUERY = "{ chatSessions { id title messages { id role content sources } } }";
const OFFLINE_MSG = "I can't reach the Mari API right now — start the server and try again.";

const SUGGESTIONS = [
  "Tag the auth RFC needs-review and take me to it",
  "What sources are connected? Sync the stale one",
  "Find docs about session timeout",
];

/** Rebuild dock items from a persisted session (tool trace rides in sources). */
function itemsFromSession(s: SessionRow): Item[] {
  const items: Item[] = [];
  for (const m of s.messages) {
    if (m.role === "user") {
      items.push({ kind: "user", text: m.content });
      continue;
    }
    let trace: any = m.sources;
    if (typeof trace === "string") { try { trace = JSON.parse(trace); } catch { trace = []; } }
    if (Array.isArray(trace)) {
      for (const ev of trace) {
        if (ev && ev.kind === "tool") {
          items.push({ kind: "tool", name: String(ev.name), args: ev.args ?? {}, summary: String(ev.summary ?? ""), ok: !!ev.ok });
        }
      }
    }
    items.push({ kind: "assistant", text: m.content });
  }
  return items;
}

function ToolRow({ item }: { item: ToolItem }) {
  const [expanded, setExpanded] = useState(false);
  const argsShort = Object.entries(item.args)
    .map(([k, v]) => `${k}: ${typeof v === "string" ? JSON.stringify(v.length > 42 ? v.slice(0, 42) + "…" : v) : String(v)}`)
    .join(", ");
  return (
    <div className={`dock__tool${item.ok === false ? " dock__tool--err" : ""}`}>
      <button
        className="dock__toolrow"
        onClick={() => setExpanded((e) => !e)}
        aria-expanded={expanded}
      >
        <span className="dock__toolstatus" aria-hidden="true">
          {item.ok === null ? <Spinner size="sm" label={`Running ${item.name}`} /> : item.ok ? <Ic.Check size={13} /> : <Ic.Close size={13} />}
        </span>
        <span className="dock__toolname mono">{item.name}</span>
        <span className="dock__toolargs mono">({argsShort})</span>
        <span className="dock__toolchev"><Ic.Chev size={12} /></span>
      </button>
      <div className="dock__toolsum">{item.ok === null ? "Running…" : item.summary}</div>
      {expanded && (
        <pre className="dock__tooldetail mono">{JSON.stringify({ args: item.args, result: item.summary, ok: item.ok }, null, 2)}</pre>
      )}
    </div>
  );
}

export default function ChatDock() {
  const open = useDockOpen();
  const navigate = useNavigate();
  const [items, setItems] = useState<Item[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const sessionIdRef = useRef<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const streamRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = streamRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [items, open]);

  async function send(text: string) {
    const msg = text.trim();
    if (!msg || streaming) return;
    setInput("");
    setStreaming(true);
    setItems((it) => [...it, { kind: "user", text: msg }]);

    const push = (item: Item) => setItems((it) => [...it, item]);
    const patchLastTool = (fn: (t: ToolItem) => ToolItem) =>
      setItems((it) => {
        const i = it.map((x) => x.kind).lastIndexOf("tool");
        return i === -1 ? it : it.map((x, j) => (j === i && x.kind === "tool" ? fn(x) : x));
      });

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const ok = await agentChatStream(msg, sessionIdRef.current, {
      onMeta: (sid) => { sessionIdRef.current = sid; setSessionId(sid); },
      onToolStart: (ev) => push({ kind: "tool", name: ev.name, args: ev.args, summary: "", ok: null }),
      onToolResult: (ev) => patchLastTool((t) => ({ ...t, summary: ev.summary, ok: ev.ok })),
      onNavigate: (path) => navigate(path),
      onWarning: (m) => push({ kind: "warning", text: m }),
      onToken: (tok) =>
        setItems((it) => {
          const last = it[it.length - 1];
          if (last?.kind === "assistant") {
            return it.map((x, j) => (j === it.length - 1 ? { ...x, text: (x as any).text + tok } : x));
          }
          return [...it, { kind: "assistant", text: tok }];
        }),
    }, ctrl.signal);
    if (!ok) push({ kind: "assistant", text: OFFLINE_MSG });
    // A stopped run may leave a spinner behind — resolve it visually.
    patchLastTool((t) => (t.ok === null ? { ...t, ok: false, summary: "stopped" } : t));
    abortRef.current = null;
    setStreaming(false);
  }

  function stop() {
    abortRef.current?.abort();
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void send(input);
  }

  function newConversation() {
    if (streaming) return;
    sessionIdRef.current = null;
    setSessionId(null);
    setItems([]);
  }

  async function loadSessions() {
    const d = await gql<{ chatSessions: SessionRow[] }>(SESSIONS_QUERY);
    setSessions(d?.chatSessions ?? []);
  }

  function resumeSession(s: SessionRow) {
    if (streaming) return;
    sessionIdRef.current = s.id;
    setSessionId(s.id);
    setItems(itemsFromSession(s));
  }

  // Closed: a floating launcher in the bottom-right corner, present on every
  // page, so the agent is always one click away.
  if (!open) {
    return createPortal(
      <button className="dock-launcher" aria-label="Open Mari agent" title="Mari agent — it can act on Mari for you" onClick={() => toggleDock(true)}>
        <Ic.Sparkle size={20} />
      </button>,
      document.body,
    );
  }

  return createPortal(
    <aside className="chat-dock" aria-label="Mari agent">
      <header className="dock__head">
        <IconRing size={30}><Ic.Sparkle size={14} /></IconRing>
        <span className="dock__title">
          Mari agent
          {sessionId !== null && <span className="dock__session mono">#{sessionId}</span>}
        </span>
        <Menu
          minWidth={260}
          trigger={(
            <Button icon compact aria-label="Past conversations" onClick={() => void loadSessions()}>
              <Ic.Calendar size={14} />
            </Button>
          )}
        >
          {sessions.length === 0 ? (
            <MenuLabel>No past conversations.</MenuLabel>
          ) : (
            sessions.map((s) => (
              <MenuItem
                key={s.id}
                icon={<Ic.Chat size={14} />}
                disabled={streaming}
                end={<small>{s.id === sessionId ? "current" : `${s.messages.length} msgs`}</small>}
                onSelect={() => resumeSession(s)}
              >
                {s.title}
              </MenuItem>
            ))
          )}
        </Menu>
        <Button icon compact aria-label="New conversation" disabled={streaming} onClick={newConversation}>
          <Ic.Plus size={14} />
        </Button>
        <Button icon compact aria-label="Close Mari agent" onClick={() => toggleDock(false)}>
          <Ic.Close size={14} />
        </Button>
      </header>

      <div className="dock__stream" ref={streamRef}>
        {items.length === 0 ? (
          <div className="dock__empty">
            <p>I can operate Mari for you — search, edit and tag docs, approve answers, sync sources, run flows, and steer this screen.</p>
            <div className="dock__suggestions">
              {SUGGESTIONS.map((s) => (
                <Chip key={s} onClick={() => void send(s)}>{s}</Chip>
              ))}
            </div>
          </div>
        ) : (
          items.map((item, i) => {
            switch (item.kind) {
              case "user":
                return <div key={i} className="dock__user"><span className="dock__prompt mono">❯</span>{item.text}</div>;
              case "assistant":
                return <div key={i} className="dock__assistant">{item.text}</div>;
              case "warning":
                return <div key={i} className="dock__warning">{item.text}</div>;
              case "tool":
                return <ToolRow key={i} item={item} />;
            }
          })
        )}
        {streaming && items[items.length - 1]?.kind !== "assistant" && (
          <div className="dock__thinking"><Spinner size="sm" label="Mari is working" /> working…</div>
        )}
      </div>

      <form className="dock__composer" onSubmit={onSubmit}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask Mari to do something…"
          aria-label="Ask the Mari agent"
        />
        {streaming ? (
          <Button compact variant="danger" onClick={stop}>Stop</Button>
        ) : (
          <Button compact type="submit" disabled={!input.trim()} aria-label="Send">
            <Ic.Send size={14} />
          </Button>
        )}
      </form>
      <div className="dock__hint">Tools run with your own permissions · gemma3 local</div>
    </aside>,
    document.body,
  );
}
