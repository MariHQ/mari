// The published knowledge chat: one destination, one anonymous reader, no
// console around it. The transcript is the library's <ChatMessage>, the same
// component the signed-in agent dock renders, so an answer looks the same
// wherever it is read and there is one place to fix how a citation behaves.
//
// It renders the PUBLIC source variant. The `href` the server returns is a
// console route behind auth, so offering it here would hand a visitor a link
// to a login page; only a document's own `source_url` is worth showing, and a
// document without one gets no link rather than a dead one.
import { FormEvent, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { Button, ChatMessage, TypingIndicator } from "@mari-design/components";
import type { ChatMessageData, ChatSourceData } from "@mari-design/components";

type Config = { name: string; title: string; welcome: string; project: string };
// `candidates` is everything the server retrieved (the meta event); `sources`
// is what the finished answer cites (the sources event), or null until then.
type Turn = { question: string; answer: string; candidates: ChatSourceData[]; sources: ChatSourceData[] | null };

/** The [n] markers in the answer so far, read the way the library links them:
    outside fenced code and inline code, and never a real link `[3](…)`. */
function citedNumbers(text: string): Set<number> {
  const cited = new Set<number>();
  let fence = "";
  const prose: string[] = [];
  for (const line of text.split("\n")) {
    const open = /^[ \t]{0,3}(`{3,}|~{3,})/.exec(line);
    if (fence) { if (open && line.trim().startsWith(fence)) fence = ""; }
    else if (open) fence = open[1].slice(0, 3);
    else prose.push(line);
  }
  prose.join("\n").split(/(`[^`]*`)/).forEach((part, index) => {
    if (index % 2) return;
    for (const match of part.matchAll(/\[(\d{1,3})\](?!\()/g)) cited.add(Number(match[1]));
  });
  return cited;
}

/** The rail under an answer. Once the server has settled it, that list; while
    the answer is still arriving, the candidates the text has cited so far. A
    "could not find" answer therefore never shows the pages it did not use. */
function sourcesFor(turn: Turn): ChatSourceData[] {
  if (turn.sources) return turn.sources;
  const cited = citedNumbers(turn.answer);
  return turn.candidates.filter((source) => cited.has(source.n));
}

export function KnowledgeChatDestination() {
  const { project = "", slug = "" } = useParams();
  const [config, setConfig] = useState<Config | null>(null);
  const [error, setError] = useState("");
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  // A public session reference is "<id>.<token>", echoed back as given.
  const session = useRef<number | string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void fetch(`/knowledge-chat-api/${encodeURIComponent(project)}/${encodeURIComponent(slug)}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(response.status === 404 ? "This knowledge chat is not deployed." : "Knowledge chat is unavailable.");
        setConfig(await response.json() as Config);
      }).catch((cause) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Knowledge chat is unavailable."); });
    return () => controller.abort();
  }, [project, slug]);

  async function ask(event: FormEvent) {
    event.preventDefault();
    const message = question.trim();
    if (!message || busy) return;
    setBusy(true); setError(""); setQuestion("");
    const turn: Turn = { question: message, answer: "", candidates: [], sources: null };
    setTurns((rows) => [...rows, turn]);
    try {
      const response = await fetch(`/knowledge-chat-api/${encodeURIComponent(project)}/${encodeURIComponent(slug)}/chat`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, session_id: session.current }),
      });
      if (!response.ok || !response.body) throw new Error("The assistant could not answer right now.");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      const apply = (block: string) => {
        const lines = block.split(/\r?\n/); let eventName = "message";
        const data: string[] = [];
        for (const line of lines) {
          if (line.startsWith("event:")) eventName = line.slice(6).trim();
          if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
        }
        if (!data.length || eventName === "done") return;
        const payload = JSON.parse(data.join("\n")) as { token?: string; session_id?: number | string; sources?: ChatSourceData[] };
        if (payload.session_id) session.current = payload.session_id;
        setTurns((rows) => rows.map((row, index) => {
          if (index !== rows.length - 1) return row;
          // The sources event is the server's final word on the rail; an
          // older server that never sends one leaves the citation filter above
          // in charge.
          if (eventName === "sources") return { ...row, sources: payload.sources ?? [] };
          return { ...row, answer: row.answer + (payload.token ?? ""), candidates: payload.sources ?? row.candidates };
        }));
      };
      while (true) {
        const { done, value } = await reader.read(); buffer += decoder.decode(value, { stream: !done });
        const blocks = buffer.split(/\r?\n\r?\n/); buffer = blocks.pop() ?? "";
        blocks.forEach(apply);
        if (done) { if (buffer.trim()) apply(buffer); break; }
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The assistant could not answer right now.");
    } finally { setBusy(false); }
  }

  if (error && !config) return <main id="main-content" tabIndex={-1} className="mx-auto max-w-2xl p-8"><h1 className="text-2xl font-semibold">Knowledge chat</h1><p role="alert" className="mt-4">{error}</p></main>;
  if (!config) return <main id="main-content" tabIndex={-1} className="mx-auto max-w-2xl p-8" aria-busy="true">Loading knowledge chat…</main>;
  return <main id="main-content" tabIndex={-1} className="mx-auto flex min-h-screen max-w-3xl flex-col px-5 py-10 sm:px-8">
    <header className="border-b border-ink/15 pb-6"><p className="font-term text-xs uppercase tracking-wider text-moss">{config.name}</p><h1 className="mt-2 text-3xl font-semibold text-ink">{config.title}</h1><p className="mt-2 text-ink/70">{config.welcome}</p></header>
    <section aria-live="polite" aria-label="Conversation" className="flex flex-1 flex-col gap-6 py-6">
      {turns.map((turn, index) => {
        const pending = busy && index === turns.length - 1;
        const question: ChatMessageData = { id: `q${index}`, role: "user", content: turn.question };
        const answer: ChatMessageData = {
          id: `a${index}`, role: "assistant", content: turn.answer,
          sources: sourcesFor(turn), streaming: pending && turn.answer !== "",
        };
        return (
          <article key={index} className="flex flex-col gap-1">
            <ChatMessage message={question} />
            {/* Nothing has come back yet: say so once, rather than drawing an
                empty answer box the reader has to interpret. */}
            {pending && !turn.answer
              ? <TypingIndicator label="Reading the knowledge base…" />
              : <ChatMessage message={answer} sourceVariant="public" />}
          </article>
        );
      })}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    </section>
    <form onSubmit={ask} className="sticky bottom-0 flex gap-2 border-t border-ink/15 bg-paper py-4"><label className="sr-only" htmlFor="knowledge-question">Ask a question</label><input id="knowledge-question" value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="Ask a question" className="min-w-0 flex-1 rounded-lg border border-ink/25 bg-white px-4 py-3" />{/* The library Button defaults to type="button", which would make this a
        button in a form that submits nothing. */}
    <Button type="submit" variant="primary" disabled={busy || !question.trim()}>{busy ? "Answering…" : "Ask"}</Button></form>
  </main>;
}
