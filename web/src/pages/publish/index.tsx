// Publish — the doc-site product. A sites index (every site, every release),
// a per-site editor (SiteEditor), and the MCP servers tab (McpServersTab).
// The editor is deep-linkable via ?site=<id>.
// Reference: output/imagegen/mari-cloud-doc-site-builder.png

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import * as Ic from "../../components/icons";
import { TagChip } from "../../components/TagChip";
import { Button, Card, CountChip, EmptyState, Input, PageHeader, SectionLabel, Spinner, StatusChip, Tabs } from "../../components/ui";
import { gql, useQuery } from "../../lib/api";
import {
  Release, SITES_Q, Site, SOURCE_TAGS, buildSiteEx,
  fmtDeployed, relDot, releasesQ, sitePath, useBuilt,
} from "./data";
import { McpServersTab } from "./McpServersTab";
import { SiteEditor } from "./SiteEditor";
import "../publish.css";

// ————— sites index —————

function SiteCard({ site, onEdit }: { site: Site; onEdit: () => void }) {
  const [buildNonce, setBuildNonce] = useState(0);
  const [busy, setBusy] = useState<"build" | "deploy" | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const built = useBuilt(site.id, buildNonce);
  const releasesQuery = useQuery<Release[]>(releasesQ(site.id), { map: (d: any) => d.releases ?? [] });
  const releases = releasesQuery.data ?? [];
  const live = releases.find((r) => r.status === "live") ?? releases[0] ?? null;

  // buildSiteEx without a generator reuses the site's persisted choice
  // (theme.generator) — the picker itself lives in the site editor.
  const buildAndOpen = async () => {
    if (busy) return;
    setBusy("build"); setNote(null);
    const res = await buildSiteEx(site.id);
    setBusy(null);
    if (res?.ok && res.url) {
      setBuildNonce((n) => n + 1);
      setNote(`Built with ${res.generator} — ${res.pages ?? "?"} pages in ${res.seconds ?? "?"}s.`);
      window.open(res.url, "_blank", "noopener");
    } else if (res) {
      setNote(`Build failed (${res.generator}): ${res.error ?? "unknown error"}`);
    } else {
      setNote("Build failed — is the API running?");
    }
  };

  const deploy = async () => {
    if (busy) return;
    setBusy("deploy"); setNote(null);
    const d = await gql<{ deploySite: string }>(`mutation { deploySite(id: ${site.id}) }`);
    setBusy(null);
    setNote(d?.deploySite ? `Deployed ${d.deploySite}` : "Deploy failed — is the API running?");
    releasesQuery.refetch();
    setBuildNonce((n) => n + 1);
  };

  return (
    <Card
      className="pub-site"
      title={site.name}
      actions={site.status === "live" ? <StatusChip status="running" label="Live" /> : <StatusChip status="draft" />}
    >
      <div className="row" style={{ gap: 8 }}>
        <Ic.Globe size={13} style={{ color: "var(--ink-faint)" } as any} />
        <span className="mono">{site.domain}</span>
      </div>

      <div className="row pub-site__stats">
        <span><b className="pub-num">{site.docs}</b> <span className="card__hint">docs</span></span>
        <span><b className={`pub-num${site.warnings ? " pub-num--warn" : ""}`}>{site.warnings}</b> <span className="card__hint">warnings</span></span>
        <span className="pub-site__live">
          <b className="pub-num">{live?.version ?? "—"}</b>
          <span className="card__hint">{live ? fmtDeployed(live.deployed) : releasesQuery.loading ? "loading…" : "never deployed"}</span>
        </span>
      </div>

      <div className="pub-divider pub-site__rels">
        {releasesQuery.loading && (
          <div style={{ display: "grid", placeItems: "center", minHeight: 48 }}>
            <Spinner size="sm" label="Loading releases" />
          </div>
        )}
        {releases.slice(0, 3).map((r) => (
          <div key={r.id} className="pub-site__rel">
            <span className="pub-dot" style={{ background: relDot(r.status) }} />
            <b>{r.version}</b>
            <span className="card__hint pub-ellip" style={{ flex: 1 }} title={r.notes}>{r.notes || fmtDeployed(r.deployed)}</span>
          </div>
        ))}
        {!releasesQuery.loading && !releases.length && (
          <div className="card__hint" style={{ padding: "8px 0" }}>
            {releasesQuery.error ? "API offline — releases unavailable." : "No releases yet — deploy to create the first one."}
          </div>
        )}
      </div>

      {note && <div className={`card__hint${/failed/i.test(note) ? " pub-err" : ""}`} style={{ marginTop: 6 }} role={/failed/i.test(note) ? "alert" : undefined}>{note}</div>}
      <div className="row" style={{ gap: 8, marginTop: 10 }}>
        {built ? (
          <a className="btn btn--compact" href={sitePath(site.id)} target="_blank" rel="noreferrer">
            Open site <Ic.External size={12} />
          </a>
        ) : (
          <Button compact disabled={busy === "build" || built === null} onClick={buildAndOpen}>
            {busy === "build" ? "Building…" : <>Build &amp; open <Ic.External size={12} /></>}
          </Button>
        )}
        <Button compact onClick={onEdit}><Ic.Pencil size={12} /> Edit</Button>
        <span className="card__spacer" />
        <Button variant="primary" compact disabled={busy === "deploy"} onClick={deploy}>
          <Ic.Send size={12} /> {busy === "deploy" ? "Deploying…" : "Deploy"}
        </Button>
      </div>
    </Card>
  );
}

function NewSiteCard({ onCreated }: { onCreated: (id: number) => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [domain, setDomain] = useState("");
  const [tags, setTags] = useState<string[]>(["customer-facing", "canonical"]);
  const [creating, setCreating] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const toggle = (t: string) =>
    setTags((v) => (v.includes(t) ? v.filter((x) => x !== t) : [...v, t]));

  const create = async () => {
    if (!name.trim() || !domain.trim() || creating) return;
    setCreating(true); setErr(null);
    const d = await gql<{ createSite: number }>(
      `mutation($n: String!, $d: String!, $s: JSON!) { createSite(name: $n, domain: $d, sources: $s) }`,
      { n: name.trim(), d: domain.trim(), s: tags }
    );
    setCreating(false);
    if (d?.createSite != null) onCreated(d.createSite);
    else setErr("Couldn't create the site — is the API running?");
  };

  if (!open) {
    return (
      <button type="button" className="card pub-site pub-new pub-new__invite" onClick={() => setOpen(true)}>
        <span>
          <span className="pub-new__title"><Ic.Plus size={15} /> New site</span>
          <span className="card__hint pub-new__hint">Pick sources, get a docs website</span>
        </span>
      </button>
    );
  }

  return (
    <Card className="pub-site pub-new" title="New site">
      <SectionLabel className="pub-sec">Name</SectionLabel>
      <Input autoFocus value={name} placeholder="Partner API docs" onChange={(e) => setName(e.target.value)} />
      <SectionLabel className="pub-sec">Domain</SectionLabel>
      <Input value={domain} placeholder="docs.example.com" onChange={(e) => setDomain(e.target.value)} />
      <SectionLabel className="pub-sec">Sources — docs tagged with…</SectionLabel>
      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        {SOURCE_TAGS.map((t) => (
          <TagChip key={t} tag={t} selected={tags.includes(t)} onClick={() => toggle(t)} />
        ))}
      </div>
      {err && <div className="card__hint pub-err">{err}</div>}
      <div className="row" style={{ gap: 8, marginTop: 12 }}>
        <Button compact onClick={() => setOpen(false)}>Cancel</Button>
        <span className="card__spacer" />
        <Button variant="primary" compact disabled={creating || !name.trim() || !domain.trim()} onClick={create}>
          {creating ? "Creating…" : "Create site"}
        </Button>
      </div>
    </Card>
  );
}

function SitesIndex({ sites, onEdit, refetch }: { sites: Site[]; onEdit: (id: number) => void; refetch: () => void }) {
  return (
    <>
      <PageHeader
        title="Doc sites"
        description="Sites build from the knowledge base and deploy to an S3 bucket you map a domain to."
        actions={<CountChip value={sites.length} />}
      />
      <div className="pub-grid">
        {sites.map((s) => <SiteCard key={s.id} site={s} onEdit={() => onEdit(s.id)} />)}
        <NewSiteCard onCreated={(id) => { refetch(); onEdit(id); }} />
      </div>
    </>
  );
}

// ————— page —————

type PageTab = "sites" | "mcp";

export default function PublishPage() {
  const [pageTab, setPageTab] = useState<PageTab>("sites");
  const [params, setParams] = useSearchParams();
  const siteParam = params.get("site");
  const editingId = siteParam != null ? Number(siteParam) : null;

  const sitesQuery = useQuery<Site[]>(SITES_Q, { map: (d: any) => d.sites ?? [] });
  const sites = sitesQuery.data ?? [];
  const refetchSites = sitesQuery.refetch;
  const editing = editingId != null ? sites.find((s) => s.id === editingId) ?? null : null;

  // Returning from the editor (backLink clears ?site) — refresh the list.
  const prevEditing = useRef(editingId);
  useEffect(() => {
    if (prevEditing.current != null && editingId == null) refetchSites();
    prevEditing.current = editingId;
  }, [editingId, refetchSites]);

  return (
    <>
      <div className="pub-tabs">
        <Tabs<PageTab>
          value={pageTab}
          onChange={setPageTab}
          ariaLabel="Publish sections"
          options={[
            { id: "sites", label: "Doc sites" },
            { id: "mcp", label: "MCP servers" },
          ]}
        />
      </div>

      {pageTab === "mcp" ? (
        <McpServersTab />
      ) : editingId != null ? (
        editing ? (
          <SiteEditor key={editing.id} site={editing} />
        ) : (
          <div>
            <Button variant="link" style={{ marginBottom: 8 }} onClick={() => setParams({})}>← All sites</Button>
            <div className="card__hint">Loading site…</div>
          </div>
        )
      ) : sitesQuery.loading ? (
        <div className="card" style={{ display: "grid", placeItems: "center", minHeight: 160 }}>
          <Spinner size="sm" label="Loading sites" />
        </div>
      ) : sitesQuery.error && !sitesQuery.data ? (
        <div className="card">
          <EmptyState icon={<Ic.Globe size={20} />}>API offline — doc sites unavailable.</EmptyState>
        </div>
      ) : (
        <SitesIndex
          sites={sites}
          onEdit={(id) => setParams({ site: String(id) })}
          refetch={refetchSites}
        />
      )}
    </>
  );
}
