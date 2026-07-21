// Publish — MCP servers tab: expose the curated knowledge base to Claude and
// other agents. Create (capability-scoped), configure, test, connect, delete.
// One-time bearer tokens surface through the shared <TokenReveal>.

import { useState } from "react";
import * as Ic from "../../components/icons";
import { TokenReveal } from "../../components/TokenReveal";
import {
  Button, Card, Chip, CountChip, EmptyState, Input, PageHeader, SectionLabel, Select, Spinner,
} from "../../components/ui";
import { gql, useQuery } from "../../lib/api";
import {
  FreshToken, MCP_CAPS, MCP_Q, MCP_UNITS, McpServer, McpTest,
  capTools, capsOf, connectSnippet, useCopy,
} from "./data";

function McpCapPicker({ value, onToggle }: { value: string[]; onToggle: (k: string) => void }) {
  return (
    <div className="mcp-caps">
      {MCP_CAPS.map((c) => (
        <label key={c.key} className={`mcp-cap${value.includes(c.key) ? " on" : ""}`}>
          <input type="checkbox" checked={value.includes(c.key)} onChange={() => onToggle(c.key)} />
          <span style={{ minWidth: 0 }}>
            <b>{c.key}</b>
            <span className="card__hint"> · {c.tools} tool{c.tools === 1 ? "" : "s"}</span>
            <span className="mcp-cap__desc">{c.desc}</span>
          </span>
        </label>
      ))}
    </div>
  );
}

function NewMcpServerPanel({ onClose, onCreated }: {
  onClose: () => void;
  onCreated: (fresh: FreshToken) => void;
}) {
  const [name, setName] = useState("");
  const [scope, setScope] = useState("project");
  const [caps, setCaps] = useState<string[]>(["search", "facts"]);
  const [creating, setCreating] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const toggle = (k: string) =>
    setCaps((v) => (v.includes(k) ? v.filter((x) => x !== k) : [...v, k]));

  const create = async () => {
    if (!name.trim() || !caps.length || creating) return;
    setCreating(true); setErr(null);
    const d = await gql<{ createMcpServer: string }>(
      `mutation($n: String!, $s: String!, $c: JSON!) { createMcpServer(name: $n, scope: $s, capabilities: $c) }`,
      { n: name.trim(), s: scope, c: caps }
    );
    if (!d?.createMcpServer) {
      setCreating(false);
      setErr("Couldn't create the server — is the API running?");
      return;
    }
    // url is generated server-side — fetch the list once to find it
    const list = await gql<{ mcpServers: McpServer[] }>(`{ mcpServers { id name url } }`);
    const made = list?.mcpServers.find((m) => m.name === name.trim());
    setCreating(false);
    onCreated({ name: name.trim(), url: made?.url ?? "", token: d.createMcpServer });
  };

  return (
    <Card className="pub-new" title="New MCP server" style={{ marginBottom: 12 }}>
      <div className="mcp-newgrid">
        <span>
          <SectionLabel className="pub-sec pub-sec--tight">Name</SectionLabel>
          <Input
            autoFocus value={name} placeholder="mari-support-kb"
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && create()}
          />
        </span>
        <span>
          <SectionLabel className="pub-sec pub-sec--tight">Scope</SectionLabel>
          <Select value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="project">project</option>
            <option value="org">org</option>
          </Select>
        </span>
      </div>
      <SectionLabel className="pub-sec">Capabilities — {capTools(caps)} tools selected</SectionLabel>
      <McpCapPicker value={caps} onToggle={toggle} />
      {err && <div className="card__hint pub-err">{err}</div>}
      <div className="row" style={{ gap: 8, marginTop: 12 }}>
        <Button compact onClick={onClose}>Cancel</Button>
        <span className="card__spacer" />
        <Button variant="primary" compact disabled={creating || !name.trim() || !caps.length} onClick={create}>
          <Ic.Plus size={12} /> {creating ? "Creating…" : "Create server"}
        </Button>
      </div>
    </Card>
  );
}

function McpServerCard({ server, freshToken, onChanged }: {
  server: McpServer;
  freshToken?: string;
  onChanged: () => void;
}) {
  const caps = capsOf(server);
  const [open, setOpen] = useState<"configure" | "connect" | null>(null);
  const [copied, copy] = useCopy();

  // configure draft
  const [draftCaps, setDraftCaps] = useState<string[]>([]);
  const [draftScope, setDraftScope] = useState(server.scope);
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState<string | null>(null);

  // test + delete
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<McpTest>(null);
  const [confirmDel, setConfirmDel] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const openConfigure = () => {
    if (open === "configure") { setOpen(null); return; }
    setDraftCaps(capsOf(server));
    setDraftScope(server.scope);
    setSaveErr(null);
    setOpen("configure");
  };

  const toggleDraft = (k: string) =>
    setDraftCaps((v) => (v.includes(k) ? v.filter((x) => x !== k) : [...v, k]));

  const save = async () => {
    if (saving) return;
    setSaving(true); setSaveErr(null);
    const d = await gql<{ updateMcpServer: boolean }>(
      `mutation($s: String, $c: JSON) { updateMcpServer(id: ${server.id}, scope: $s, capabilities: $c) }`,
      { s: draftScope, c: draftCaps }
    );
    setSaving(false);
    if (d?.updateMcpServer) { setOpen(null); onChanged(); }
    else setSaveErr("Save failed — is the API running?");
  };

  const runTest = async () => {
    if (testing) return;
    setTesting(true); setTest(null);
    const d = await gql<{ testMcpServer: any }>(`mutation { testMcpServer(id: ${server.id}) }`);
    setTesting(false);
    setTest(d?.testMcpServer ?? "error");
  };

  const del = async () => {
    if (deleting) return;
    setDeleting(true);
    const d = await gql<{ deleteMcpServer: boolean }>(`mutation { deleteMcpServer(id: ${server.id}) }`);
    setDeleting(false); setConfirmDel(false);
    if (d?.deleteMcpServer) onChanged();
  };

  const snippet = connectSnippet(server.name, server.url, freshToken);
  const testOk = test !== null && test !== "error" && test.ok;

  return (
    <Card>
      <div className="row" style={{ gap: 10 }}>
        <span className="pub-dot" style={{ background: server.status === "connected" ? "var(--green)" : "#939da7" }} />
        <b className="mcp-name">{server.name}</b>
        <Chip caps>{server.scope}</Chip>
        <span className="card__hint" style={{ flex: "none" }}>{server.tools} tools</span>
        <span className="pub-ellip mono mcp-url">{server.url}</span>
        <Button compact className="pub-opt-sm" style={{ flex: "none" }} onClick={() => copy("url", server.url)}>
          {copied === "url" ? <><Ic.Check size={12} /> Copied</> : "Copy"}
        </Button>
      </div>

      <div className="row" style={{ gap: 6, marginTop: 10, flexWrap: "wrap" }}>
        {caps.length
          ? caps.map((c) => <Chip key={c}>{c}</Chip>)
          : <span className="card__hint" style={{ fontStyle: "italic" }}>capabilities not set</span>}
        <span className="row" style={{ gap: 6, marginLeft: "auto" }}>
          <Button compact className="pub-opt-sm" onClick={openConfigure}>
            <Ic.Gear size={12} /> Configure
          </Button>
          <Button compact className="pub-opt-sm" disabled={testing} onClick={runTest}>
            {testing ? <Spinner size="sm" /> : <Ic.Play size={12} />} {testing ? "Testing…" : "Test"}
          </Button>
          <Button compact className="pub-opt-sm" onClick={() => setOpen((o) => (o === "connect" ? null : "connect"))}>
            <Ic.External size={12} /> How to connect
          </Button>
          {confirmDel ? (
            <>
              <Button variant="danger" compact className="pub-opt-sm" disabled={deleting} onClick={del}>
                {deleting ? "Deleting…" : "Really delete?"}
              </Button>
              <Button compact className="pub-opt-sm" disabled={deleting} onClick={() => setConfirmDel(false)}>
                Keep
              </Button>
            </>
          ) : (
            <Button compact className="pub-opt-sm" onClick={() => setConfirmDel(true)}>
              <Ic.Trash size={12} /> Delete
            </Button>
          )}
        </span>
      </div>

      {test !== null && (
        <div className="mcp-inline">
          {test === "error" ? (
            <div className="mcp-check mcp-check--fail"><Ic.Close size={13} /> Test failed — is the API running?</div>
          ) : (
            <>
              <div className={`mcp-check mcp-check--head ${testOk ? "mcp-check--ok" : "mcp-check--fail"}`}>
                {testOk ? <Ic.CheckCircle size={13} /> : <Ic.Close size={13} />} {testOk ? "Connected" : "Not responding"} · {test.latency_ms}ms
              </div>
              {Object.entries(test.checks ?? {}).map(([k, n]) => (
                <div key={k} className="mcp-check mcp-check--detail">
                  <span className="mono mcp-check__key">{k}</span>
                  <span className="mcp-check--ok"><Ic.Check size={11} /> {n} {MCP_UNITS[k] ?? "items"}</span>
                </div>
              ))}
            </>
          )}
        </div>
      )}

      {open === "configure" && (
        <div className="mcp-inline">
          <div className="row" style={{ gap: 10 }}>
            <SectionLabel className="pub-sec pub-sec--flat">Capabilities — {capTools(draftCaps)} tools</SectionLabel>
            <Select style={{ width: 130, flex: "none" }} value={draftScope} onChange={(e) => setDraftScope(e.target.value)}>
              <option value="project">project</option>
              <option value="org">org</option>
            </Select>
          </div>
          {!caps.length && (
            <div className="card__hint pub-warn" style={{ marginTop: 6 }}>
              Legacy server — capabilities were never set. Pick what it should expose and save.
            </div>
          )}
          <McpCapPicker value={draftCaps} onToggle={toggleDraft} />
          {saveErr && <div className="card__hint pub-err">{saveErr}</div>}
          <div className="row" style={{ gap: 8, marginTop: 10 }}>
            <Button compact onClick={() => setOpen(null)}>Cancel</Button>
            <span className="card__spacer" />
            <Button variant="primary" compact disabled={saving || !draftCaps.length} onClick={save}>
              {saving ? "Saving…" : "Save"}
            </Button>
          </div>
        </div>
      )}

      {open === "connect" && (
        <div className="mcp-inline">
          <SectionLabel className="pub-sec pub-sec--tight">Connect from claude-code</SectionLabel>
          <div className="mcp-snippet">
            <pre>{snippet}</pre>
            <Button compact className="pub-opt-sm" style={{ flex: "none" }} onClick={() => copy("snip", snippet)}>
              {copied === "snip" ? <><Ic.Check size={12} /> Copied</> : <><Ic.Clipboard size={12} /> Copy</>}
            </Button>
          </div>
          {!freshToken && (
            <div className="card__hint" style={{ marginTop: 5 }}>
              Replace YOUR_TOKEN with the bearer token from when this server was created — tokens are shown once.
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

export function McpServersTab() {
  const [creating, setCreating] = useState(false);
  const [fresh, setFresh] = useState<FreshToken | null>(null);

  const serversQ = useQuery<McpServer[]>(MCP_Q, { map: (d: any) => d.mcpServers ?? [] });
  const servers = serversQ.data ?? [];
  const { refetch } = serversQ;

  return (
    <>
      <PageHeader
        title="MCP servers"
        description="Expose your curated knowledge to Claude and other agents — per-project, capability-scoped."
        actions={(
          <>
            <CountChip value={servers.length} />
            <Button variant="primary" onClick={() => setCreating(true)}>
              <Ic.Plus size={14} /> New server
            </Button>
          </>
        )}
      />

      {fresh && (
        <div style={{ marginBottom: 12 }}>
          <TokenReveal
            token={fresh.token}
            title={`${fresh.name} is live — here's your bearer token`}
            onDismiss={() => setFresh(null)}
          />
        </div>
      )}
      {creating && (
        <NewMcpServerPanel
          onClose={() => setCreating(false)}
          onCreated={(f) => { setCreating(false); setFresh(f); refetch(); }}
        />
      )}

      <div className="mcp-list">
        {serversQ.loading && (
          <div style={{ display: "grid", placeItems: "center", minHeight: 120 }}>
            <Spinner size="sm" label="Loading MCP servers" />
          </div>
        )}
        {serversQ.error && !serversQ.data && (
          <EmptyState icon={<Ic.Key size={20} />}>API offline — MCP servers unavailable.</EmptyState>
        )}
        {servers.map((m) => (
          <McpServerCard
            key={m.id} server={m}
            freshToken={fresh && fresh.name === m.name ? fresh.token : undefined}
            onChanged={refetch}
          />
        ))}
        {serversQ.data && !servers.length && (
          <EmptyState icon={<Ic.Key size={20} />} title="No MCP servers yet">
            Create one to expose this knowledge base to agents.
          </EmptyState>
        )}
      </div>
    </>
  );
}
