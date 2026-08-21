import { FormEvent, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";

type Config = { name: string; title: string; welcome: string; project: string };
type Source = { n: number; source: string; title: string; meta: string; href?: string };
type Turn = { question: string; answer: string; sources: Source[] };

export function KnowledgeChatDestination() {
  const { project = "", slug = "" } = useParams();
  const [config, setConfig] = useState<Config | null>(null);
  const [error, setError] = useState("");
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const session = useRef<number | null>(null);

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
    const turn: Turn = { question: message, answer: "", sources: [] };
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
        const payload = JSON.parse(data.join("\n")) as { token?: string; session_id?: number; sources?: Source[] };
        if (payload.session_id) session.current = payload.session_id;
        setTurns((rows) => rows.map((row, index) => index === rows.length - 1 ? {
          ...row, answer: row.answer + (payload.token ?? ""), sources: payload.sources ?? row.sources,
        } : row));
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
      {turns.map((turn, index) => <article key={index} className="flex flex-col gap-3"><p className="ml-auto max-w-[85%] rounded-xl bg-moss px-4 py-3 text-white">{turn.question}</p><div className="max-w-[90%] rounded-xl border border-ink/15 bg-white px-4 py-3 text-ink">{turn.answer || (busy && index === turns.length - 1 ? "Thinking…" : "")}</div>
        {turn.sources.length > 0 && <ol aria-label="Sources" className="flex flex-col gap-1 text-sm text-ink/70">{turn.sources.map((source) => <li key={`${source.n}-${source.title}`}>{source.href ? <a className="font-semibold text-moss underline" href={source.href}>[{source.n}] {source.title}</a> : <strong>[{source.n}] {source.title}</strong>}{source.meta ? ` — ${source.meta}` : ""}</li>)}</ol>}
      </article>)}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    </section>
    <form onSubmit={ask} className="sticky bottom-0 flex gap-2 border-t border-ink/15 bg-paper py-4"><label className="sr-only" htmlFor="knowledge-question">Ask a question</label><input id="knowledge-question" value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="Ask a question" className="min-w-0 flex-1 rounded-lg border border-ink/25 bg-white px-4 py-3" /><button disabled={busy || !question.trim()} className="rounded-lg bg-moss px-5 py-3 font-medium text-white disabled:opacity-50">{busy ? "Answering…" : "Ask"}</button></form>
  </main>;
}
