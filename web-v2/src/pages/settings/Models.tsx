// Settings → Models (embedding, LLM provider + keys, chunking).

import { useState } from "react";
import * as Ic from "../../components/icons";
import { Button, Card, Field, Input, PageHeader, Select, Table } from "../../components/ui";
import { SavedNote, saveSetting, useSave, useSettings } from "./shared";

/** "openai:text-embedding-3-small" → "OpenAI — text-embedding-3-small" */
function optionLabel(opt: string): string {
  const i = opt.indexOf(":");
  const provider = i === -1 ? opt : opt.slice(0, i);
  const model = i === -1 ? "" : opt.slice(i + 1);
  const pretty: Record<string, string> = { openai: "OpenAI", anthropic: "Anthropic", ollama: "Ollama", local: "Local" };
  return model ? `${pretty[provider] ?? provider} — ${model}` : provider;
}
const optProvider = (opt: string) => (opt.includes(":") ? opt.slice(0, opt.indexOf(":")) : opt);
const optModel = (opt: string) => (opt.includes(":") ? opt.slice(opt.indexOf(":") + 1) : opt);

type Health = { ok: boolean; service?: string; documents?: number; embedded?: number } | { error: string };

const STRATEGIES = ["heading", "thread", "fixed"];

export default function ModelsPage() {
  const [nonce, setNonce] = useState(0);
  const refetch = () => setNonce((n) => n + 1);
  const settings = useSettings(nonce);

  // ——— embedding ———
  const emb = settings.embedding ?? {};
  const embOptions: string[] = emb.options ?? [];
  const current = `${emb.provider}:${emb.model}`;
  const [embSel, setEmbSel] = useState(current);
  const [embDirty, setEmbDirty] = useState(false);
  // sync server → draft during render (previous-value pattern, no effect)
  const [prevEmbCurrent, setPrevEmbCurrent] = useState(current);
  if (current !== prevEmbCurrent) {
    setPrevEmbCurrent(current);
    if (!embDirty) setEmbSel(current);
  }
  const embSave = useSave();
  const saveEmb = () =>
    embSave.run(async () => {
      await saveSetting("embedding", { ...emb, provider: optProvider(embSel), model: optModel(embSel) });
      setEmbDirty(false);
      refetch();
    });

  // ——— llm provider + keys ———
  const llm = settings.llm ?? {};
  const llmOptions: string[] = llm.options ?? [];
  const llmCurrent = `${llm.provider}:${llm.model}`;
  const [llmSel, setLlmSel] = useState(llmCurrent);
  const [openaiKey, setOpenaiKey] = useState<string>(llm.keys?.openai ?? "");
  const [anthropicKey, setAnthropicKey] = useState<string>(llm.keys?.anthropic ?? "");
  const [llmDirty, setLlmDirty] = useState(false);
  const llmKey = JSON.stringify(llm);
  const [prevLlmKey, setPrevLlmKey] = useState(llmKey);
  if (llmKey !== prevLlmKey) {
    setPrevLlmKey(llmKey);
    if (!llmDirty) {
      setLlmSel(llmCurrent);
      setOpenaiKey(llm.keys?.openai ?? "");
      setAnthropicKey(llm.keys?.anthropic ?? "");
    }
  }
  const [showOpenai, setShowOpenai] = useState(false);
  const [showAnthropic, setShowAnthropic] = useState(false);
  const llmSave = useSave();
  const saveLlm = () =>
    llmSave.run(async () => {
      await saveSetting("llm", {
        ...llm,
        provider: optProvider(llmSel),
        model: optModel(llmSel),
        keys: { ...(llm.keys ?? {}), openai: openaiKey, anthropic: anthropicKey },
      });
      setLlmDirty(false);
      refetch();
    });

  // ——— test connection ———
  const [testing, setTesting] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const testConnection = async () => {
    setTesting(true);
    setHealth(null);
    try {
      const res = await fetch("/healthz");
      const isJson = (res.headers.get("content-type") ?? "").includes("application/json");
      setHealth(res.ok && isJson ? await res.json() : { error: res.ok ? "API not reachable through the dev proxy" : `HTTP ${res.status}` });
    } catch {
      setHealth({ error: "Could not reach the API" });
    } finally {
      setTesting(false);
    }
  };

  // ——— chunking ———
  const chunking = settings.chunking ?? {};
  const [chunkDraft, setChunkDraft] = useState<Record<string, { strategy: string; max_tokens: number; overlap: number }>>(chunking);
  const [chunkDirty, setChunkDirty] = useState(false);
  const chunkKey = JSON.stringify(chunking);
  const [prevChunkKey, setPrevChunkKey] = useState(chunkKey);
  if (chunkKey !== prevChunkKey) {
    setPrevChunkKey(chunkKey);
    if (!chunkDirty) setChunkDraft(chunking);
  }
  const setChunk = (src: string, field: "strategy" | "max_tokens" | "overlap", value: string) => {
    setChunkDirty(true);
    setChunkDraft((d) => ({
      ...d,
      [src]: { ...d[src], [field]: field === "strategy" ? value : Number(value) || 0 },
    }));
  };
  const chunkSave = useSave();
  const saveChunking = () =>
    chunkSave.run(async () => {
      await saveSetting("chunking", { ...chunking, ...chunkDraft });
      setChunkDirty(false);
      refetch();
    });

  const keyField = (
    label: string, value: string, onChange: (v: string) => void, shown: boolean, toggle: () => void, placeholder: string,
  ) => (
    <Field label={label}>
      <span className="row" style={{ gap: 8 }}>
        <Input
          className="mono"
          type={shown ? "text" : "password"}
          placeholder={placeholder}
          value={value}
          onChange={(e) => { setLlmDirty(true); onChange(e.target.value); }}
        />
        <Button icon onClick={toggle} aria-label={shown ? "Hide key" : "Reveal key"} title={shown ? "Hide key" : "Reveal key"} aria-pressed={shown}>
          <Ic.Eye size={15} />
        </Button>
      </span>
    </Field>
  );

  return (
    <>
      <PageHeader
        eyebrow="Settings"
        title="Models"
        description="Which models embed, search, and answer for this workspace"
      />

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        {/* embedding */}
        <Card className="setcard" icon={<Ic.Layers size={18} />} title="Embedding model">
          <Field
            label="Model"
            hint={`Used for knowledge search and similarity. Current dimensions: ${emb.dims}`}
          >
            <Select value={embSel} onChange={(e) => { setEmbDirty(true); setEmbSel(e.target.value); }}>
              {(embOptions.includes(embSel) ? embOptions : [embSel, ...embOptions]).map((o) => (
                <option key={o} value={o}>
                  {optionLabel(o)}{o === emb.default ? " (default)" : ""}
                </option>
              ))}
            </Select>
          </Field>
          <div className="row" style={{ gap: 10, marginTop: 14 }}>
            <Button variant="primary" onClick={saveEmb} disabled={embSave.saving || !embDirty}>
              {embSave.saving ? "Saving…" : "Save"}
            </Button>
            {embSave.saved && <SavedNote />}
          </div>
        </Card>

        {/* llm provider */}
        <Card className="setcard" icon={<Ic.Sparkle size={18} />} title="LLM provider">
          <Field label="Model" hint="Answers Ask Mari, refinements, and digests.">
            <Select value={llmSel} onChange={(e) => { setLlmDirty(true); setLlmSel(e.target.value); }}>
              {(llmOptions.includes(llmSel) ? llmOptions : [llmSel, ...llmOptions]).map((o) => (
                <option key={o} value={o}>{optionLabel(o)}</option>
              ))}
            </Select>
          </Field>
        </Card>
      </div>

      {/* llm keys + test connection */}
      <Card className="setcard" icon={<Ic.Key size={18} />} title="LLM provider keys">
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
          {keyField("OpenAI API key", openaiKey, setOpenaiKey, showOpenai, () => setShowOpenai((v) => !v), "sk-…")}
          {keyField("Anthropic API key", anthropicKey, setAnthropicKey, showAnthropic, () => setShowAnthropic((v) => !v), "sk-ant-…")}
        </div>
        <div className="row" style={{ gap: 12, marginTop: 16, flexWrap: "wrap" }}>
          <Button variant="primary" onClick={saveLlm} disabled={llmSave.saving || !llmDirty}>
            {llmSave.saving ? "Saving…" : "Save changes"}
          </Button>
          {llmSave.saved && <SavedNote />}
          <Button onClick={testConnection} disabled={testing}>
            <Ic.Refresh size={14} /> {testing ? "Testing…" : "Test connection"}
          </Button>
          {health && ("error" in health ? (
            <span className="row" style={{ gap: 6, color: "var(--red)" }}>
              <Ic.Bell size={14} /> {health.error}
            </span>
          ) : (
            <span className="row" style={{ gap: 10 }}>
              <span className="row" style={{ gap: 5, color: health.ok ? "var(--green-deep)" : "var(--red)" }}>
                <Ic.CheckCircle size={14} /> {health.ok ? "Connected" : "Unhealthy"}
              </span>
              <span className="card__hint">{health.service}</span>
              <span className="card__hint">{health.documents} documents · {health.embedded} embedded</span>
            </span>
          ))}
        </div>
      </Card>

      {/* chunking */}
      <Card
        className="setcard"
        variant="flush"
        icon={<Ic.Doc size={18} />}
        title="Chunking"
        hint="How documents are split before embedding, per source."
      >
        <Table columns={["Source", "Strategy", { label: "Max tokens", width: 130 }, { label: "Overlap", width: 110 }]}>
          {Object.keys(chunkDraft).map((src) => (
            <tr key={src}>
              <td><b style={{ fontWeight: 600, textTransform: "capitalize" }}>{src}</b></td>
              <td>
                <Select
                  style={{ width: 150 }}
                  aria-label={`Strategy for ${src}`}
                  value={chunkDraft[src]?.strategy ?? "heading"}
                  onChange={(e) => setChunk(src, "strategy", e.target.value)}
                >
                  {(STRATEGIES.includes(chunkDraft[src]?.strategy) ? STRATEGIES : [chunkDraft[src]?.strategy, ...STRATEGIES].filter(Boolean)).map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </Select>
              </td>
              <td>
                <Input
                  className="mono" type="number" min={0} style={{ width: 96 }}
                  aria-label={`Max tokens for ${src}`}
                  value={chunkDraft[src]?.max_tokens ?? 0}
                  onChange={(e) => setChunk(src, "max_tokens", e.target.value)}
                />
              </td>
              <td>
                <Input
                  className="mono" type="number" min={0} style={{ width: 84 }}
                  aria-label={`Overlap for ${src}`}
                  value={chunkDraft[src]?.overlap ?? 0}
                  onChange={(e) => setChunk(src, "overlap", e.target.value)}
                />
              </td>
            </tr>
          ))}
        </Table>
        <div className="row" style={{ gap: 10, padding: "12px 18px 15px" }}>
          <Button variant="primary" onClick={saveChunking} disabled={chunkSave.saving || !chunkDirty}>
            {chunkSave.saving ? "Saving…" : "Save"}
          </Button>
          {chunkSave.saved && <SavedNote />}
        </div>
      </Card>
    </>
  );
}
