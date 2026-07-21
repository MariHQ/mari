import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import * as Ic from "../components/icons";
import { Pill, SourceIcon } from "../components/shared";
import {
  Avatar,
  Button,
  Card,
  EmptyState,
  Menu,
  MenuRadioGroup,
  MenuRadioItem,
  PageHeader,
  Spinner,
  Tabs,
  fmtDate,
} from "../components/ui";
import { SourceKey } from "../data/sources";
import { gql, useQuery } from "../lib/api";
import { Inspector, InspectorDoc } from "./knowledge/Inspector";
import {
  BASE_OWNERS,
  BASE_SOURCES,
  FRESH_ROWS,
  Result,
  SORT_OPTIONS,
  SRC_KEY_TO_LABEL,
  SRC_LABEL_TO_KEY,
  STATUS_ROWS,
  STATUS_TAG,
  SortName,
  TAB_NAMES,
  TabName,
  TYPE_ROWS,
  freshMatch,
  tabMatch,
  typeMatch,
} from "./knowledge/search";

const SEARCH_QUERY = `query($q: String!) { search(query: $q) { id source title snippet kind author authorInitials date tags } }`;

const mapResults = (d: any): Result[] =>
  (d.search ?? []).map((r: any) => ({
    id: r.id, source: r.source, srcLabel: r.source === "slack" ? "SLACK THREAD" : String(r.source).toUpperCase(),
    title: r.source === "slack" ? `${r.channel ?? "#eng-auth"} · ${r.title}` : r.title,
    snippet: r.source === "slack" ? `Conversation summary: ${r.snippet} Related replies, decisions, and linked context are ranked together.` : r.snippet,
    kind: r.kind,
    who: r.source === "slack" ? "Auth Team" : r.author,
    whoInitials: r.source === "slack" ? "AT" : r.authorInitials,
    date: r.date, tags: r.source === "slack" ? [] : (r.tags ?? []),
    ...(r.source === "slack" ? { chunkLabel: "Thread chunk", messageCount: r.messageCount ?? 18, participantCount: r.participantCount ?? 6, channel: r.channel ?? "#eng-auth" } : {}),
  }));

function FooterStat({ value, label }: { value: string | number; label: string }) {
  return (
    <span>
      <b style={{ font: "600 17px var(--display)", color: "var(--ink)" }}>{value}</b> {label}
    </span>
  );
}

export default function Knowledge() {
  const [sp] = useSearchParams();
  const urlQ = sp.get("q");
  const [q, setQ] = useState(urlQ ?? "authentication rollout");
  const [prevUrlQ, setPrevUrlQ] = useState(urlQ);
  if (urlQ !== prevUrlQ) {
    setPrevUrlQ(urlQ);
    if (urlQ !== null) setQ(urlQ);
  }

  // live search against the GraphQL API (debounced into the query variables)
  const [debouncedQ, setDebouncedQ] = useState(q);
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 250);
    return () => clearTimeout(t);
  }, [q]);
  const searchQ = useQuery<Result[]>(SEARCH_QUERY, { variables: { q: debouncedQ }, map: mapResults });
  const results = useMemo(() => searchQ.data ?? [], [searchQ.data]);

  /* ————— filters ————— */
  const [srcSel, setSrcSel] = useState<Set<string>>(new Set());
  const [typeSel, setTypeSel] = useState<Set<string>>(new Set());
  const [ownerSel, setOwnerSel] = useState<Set<string>>(new Set());
  const [statusSel, setStatusSel] = useState<Set<string>>(new Set());
  const [fresh, setFresh] = useState("Any time");
  const [moreOpen, setMoreOpen] = useState<Record<string, boolean>>({});

  const toggleIn = (setter: React.Dispatch<React.SetStateAction<Set<string>>>) => (label: string) =>
    setter((s) => {
      const n = new Set(s);
      if (n.has(label)) n.delete(label);
      else n.add(label);
      return n;
    });

  const extraSources = useMemo(
    () => Array.from(new Set(results.map((r) => SRC_KEY_TO_LABEL[r.source] ?? r.source))).filter((l) => !BASE_SOURCES.includes(l)).slice(0, 2),
    [results],
  );
  const extraOwners = useMemo(
    () => Array.from(new Set(results.map((r) => r.who))).filter((w) => !BASE_OWNERS.includes(w)).slice(0, 2),
    [results],
  );
  const knownOwners = useMemo(() => [...BASE_OWNERS, ...extraOwners], [extraOwners]);
  const extraStatuses = useMemo(
    () => Array.from(new Set(results.flatMap((r) => r.tags))).filter((t) => !Object.values(STATUS_TAG).includes(t)).slice(0, 2),
    [results],
  );

  const ownerMatch = (r: Result, label: string) =>
    label === "Other people" ? !knownOwners.includes(r.who) : r.who === label;

  const baseFiltered = useMemo(() => {
    const matchesChecks = (r: Result) =>
      (srcSel.size === 0 || Array.from(srcSel).some((l) => r.source === (SRC_LABEL_TO_KEY[l] ?? l))) &&
      (typeSel.size === 0 || Array.from(typeSel).some((l) => typeMatch(r, l))) &&
      (ownerSel.size === 0 || Array.from(ownerSel).some((l) => l === "Other people" ? !knownOwners.includes(r.who) : r.who === l)) &&
      (statusSel.size === 0 || Array.from(statusSel).some((l) => r.tags.includes(STATUS_TAG[l] ?? l))) &&
      freshMatch(r, fresh);
    return results.filter(matchesChecks);
  }, [results, srcSel, typeSel, ownerSel, statusSel, fresh, knownOwners]);

  /* ————— tabs + sort ————— */
  const [tab, setTab] = useState<TabName>("All");
  const filtered = useMemo(() => baseFiltered.filter((r) => tabMatch(r, tab)), [baseFiltered, tab]);

  const [sort, setSort] = useState<SortName>("Best match");
  const sorted = useMemo(() => {
    const a = [...filtered];
    if (sort === "Newest") a.sort((x, y) => new Date(y.date).getTime() - new Date(x.date).getTime());
    else if (sort === "Title") a.sort((x, y) => x.title.localeCompare(y.title));
    return a;
  }, [filtered, sort]);

  /* ————— selection + bookmarks ————— */
  const [selId, setSelId] = useState<number | null>(null);
  const sel = sorted.find((r) => r.id === selId) ?? sorted[0];
  const [saved, setSaved] = useState<Set<number>>(new Set());
  const toggleSaved = (id: number) =>
    setSaved((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  /* ————— inspector: fetch the selected document + its real revision history ————— */
  const selDocId = sel?.id;
  const [fetched, setFetched] = useState<{ id: number; doc: any; revs: { id: number; actor: string; verb: string; at: string }[] | null } | null>(null);
  const [tagOverrides, setTagOverrides] = useState<Record<number, string[]>>({});
  useEffect(() => {
    if (selDocId == null) return;
    let alive = true;
    gql(`{
      document(id: ${selDocId}) { id title snippet author date tags source kind watched }
      revisions(documentId: ${selDocId}) { id actor verb at }
    }`).then((d: any) => {
      if (!alive) return;
      setFetched({ id: selDocId, doc: d?.document ?? null, revs: d?.revisions ?? null });
    });
    return () => { alive = false; };
  }, [selDocId]);
  const selDoc = sel && fetched?.id === sel.id ? fetched.doc : null;
  const selRevs = sel && fetched?.id === sel.id ? fetched.revs : null;

  const insp: InspectorDoc | null = sel
    ? {
        id: selDoc?.id ?? sel.id,
        title: selDoc?.title ?? sel.title,
        owner: selDoc?.author ?? sel.who,
        updated: selDoc?.date ?? sel.date,
        sourceKey: (selDoc?.source ?? sel.source) as SourceKey,
        source: SRC_KEY_TO_LABEL[selDoc?.source ?? sel.source] ?? (selDoc?.source ?? sel.source),
        kind: selDoc?.kind ?? sel.kind,
        summary: selDoc?.snippet ?? sel.snippet,
        tags: (tagOverrides[sel.id] ?? selDoc?.tags ?? sel.tags) as string[],
        watched: !!selDoc?.watched,
      }
    : null;

  const factsQ = useQuery<{ claim: string; verified: string; status: string }[]>(
    `{ facts { id claim status verified } }`,
    { map: (d) => d.facts ?? [] },
  );
  const factsData = factsQ.data ?? [];
  const verifiedFacts = factsData.filter((f) => f.status === "Verified").slice(0, 6);
  const factRows = verifiedFacts.map((f) => ({ text: f.claim, sub: `Verified ${f.verified}` }));

  const related = sorted.filter((r) => r.id !== sel?.id).slice(0, 4);
  const relatedRows = related.map((r) => ({ source: r.source, text: r.title, sub: `${r.srcLabel} · ${fmtDate(r.date)}` }));

  // real revision history for the selected doc
  const timeline = (selRevs ?? []).map((r) => ({ date: r.at, what: `${r.verb} by ${r.actor}`, sub: "" }));

  /* ————— footer strip: live corpus stats ————— */
  const pulseQ = useQuery<{ docsCount: number }[]>(`{ sourcePulse { docsCount } }`, { map: (d) => d.sourcePulse ?? [] });
  const freshQ = useQuery<{ fresh: number; aging: number; stale: number }[]>(`{ freshness { fresh aging stale } }`, { map: (d) => d.freshness ?? [] });
  const pulseRows = pulseQ.data;
  const freshRows = freshQ.data;
  const verifiedCount = factsData.filter((f) => f.status === "Verified").length;
  const freshTotal = (freshRows ?? []).reduce((n, r) => n + r.fresh + r.aging + r.stale, 0);
  const footer = {
    documents: pulseRows ? pulseRows.reduce((n, s) => n + s.docsCount, 0).toLocaleString("en-US") : "—",
    facts: factsQ.data ? verifiedCount.toLocaleString("en-US") : "—",
    fresh: freshRows && freshTotal > 0
      ? `${Math.round(((freshRows ?? []).reduce((n, r) => n + r.fresh, 0) / freshTotal) * 100)}%`
      : "—",
    sources: pulseRows ? pulseRows.length : "—",
  };

  /* ————— filter rail groups ————— */
  type Row = { label: string; icon?: SourceKey; n: number; active: boolean; onToggle: () => void };
  const srcRow = (label: string): Row => ({
    label, icon: SRC_LABEL_TO_KEY[label],
    n: results.filter((r) => r.source === SRC_LABEL_TO_KEY[label]).length,
    active: srcSel.has(label), onToggle: () => toggleIn(setSrcSel)(label),
  });
  const groups: { name: string; rows: Row[]; extra: Row[] }[] = [
    { name: "Source", rows: BASE_SOURCES.map(srcRow), extra: extraSources.map(srcRow) },
    {
      name: "Content type",
      rows: TYPE_ROWS.map((label) => ({
        label, n: results.filter((r) => typeMatch(r, label)).length,
        active: typeSel.has(label), onToggle: () => toggleIn(setTypeSel)(label),
      })),
      extra: [],
    },
    {
      name: "Owner",
      rows: [...BASE_OWNERS, "Other people"].map((label) => ({
        label, n: results.filter((r) => ownerMatch(r, label)).length,
        active: ownerSel.has(label), onToggle: () => toggleIn(setOwnerSel)(label),
      })),
      extra: extraOwners.map((label) => ({
        label, n: results.filter((r) => r.who === label).length,
        active: ownerSel.has(label), onToggle: () => toggleIn(setOwnerSel)(label),
      })),
    },
    {
      name: "Freshness",
      rows: FRESH_ROWS.map((label) => ({
        label, n: results.filter((r) => freshMatch(r, label)).length,
        active: fresh === label, onToggle: () => setFresh(label),
      })),
      extra: [],
    },
    {
      name: "Status",
      rows: STATUS_ROWS.map((label) => ({
        label, n: results.filter((r) => r.tags.includes(STATUS_TAG[label])).length,
        active: statusSel.has(label), onToggle: () => toggleIn(setStatusSel)(label),
      })),
      extra: extraStatuses.map((label) => ({
        label, n: results.filter((r) => r.tags.includes(label)).length,
        active: statusSel.has(label), onToggle: () => toggleIn(setStatusSel)(label),
      })),
    },
  ];

  return (
    <>
      <PageHeader eyebrow="Knowledge" title="Search the knowledge base" />
      <div className="kb">
        {/* filters */}
        <div className="filters">
          {groups.map((g) => {
            const rows = moreOpen[g.name] ? [...g.rows, ...g.extra] : g.rows;
            return (
              <div key={g.name}>
                <h4>{g.name}</h4>
                {rows.map((r) => (
                  <label
                    className="frow"
                    key={r.label}
                    onClick={r.icon ? r.onToggle : undefined}
                    style={{ cursor: "pointer", ...(r.active ? { color: "var(--ink)", fontWeight: 600 } : {}) }}
                  >
                    {r.icon ? (
                      <SourceIcon source={r.icon} size={17} />
                    ) : (
                      <input type="checkbox" checked={r.active} onChange={r.onToggle} style={{ accentColor: "#2c6e49" }} />
                    )}
                    {r.label}
                    <span className="n">{r.n}</span>
                  </label>
                ))}
                {g.extra.length > 0 && (
                  <button className="more" onClick={() => setMoreOpen((m) => ({ ...m, [g.name]: !m[g.name] }))}>
                    {moreOpen[g.name] ? "Show fewer" : "Show more"}{" "}
                    <Ic.Chev size={13} style={moreOpen[g.name] ? { transform: "rotate(180deg)" } : undefined} />
                  </button>
                )}
              </div>
            );
          })}
        </div>

        {/* results */}
        <div>
          <div className="searchbox">
            <Ic.Search size={18} />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search the knowledge base" />
            {q && <button onClick={() => setQ("")} style={{ color: "var(--ink-faint)" }}>✕</button>}
          </div>
          <div className="search-chunk-note"><Ic.Chat size={14} /><span><b>Conversation-aware Slack search</b> · related messages are indexed and ranked as one thread-sized decision chunk, not thousands of atomic artifacts.</span></div>

          <div className="row" style={{ alignItems: "flex-start" }}>
            <Tabs
              variant="underline"
              ariaLabel="Result type"
              value={tab}
              onChange={setTab}
              options={TAB_NAMES.map((t) => ({
                id: t,
                label: t,
                ...(t === "All" ? {} : { count: baseFiltered.filter((r) => tabMatch(r, t)).length }),
              }))}
            />
            <span className="card__spacer" />
            <Menu
              align="end"
              trigger={(
                <Button compact aria-label="Sort results">
                  Sort: {sort} <Ic.Chev size={12} />
                </Button>
              )}
            >
              <MenuRadioGroup value={sort} onValueChange={(v) => setSort(v as SortName)}>
                {SORT_OPTIONS.map((o) => (
                  <MenuRadioItem key={o} value={o}>{o}</MenuRadioItem>
                ))}
              </MenuRadioGroup>
            </Menu>
          </div>

          <div className="card__hint" style={{ marginBottom: 10 }}>
            {searchQ.loading ? "Searching…" : `${sorted.length} results`}
          </div>

          {searchQ.loading && (
            <Card variant="plain">
              <div style={{ display: "grid", placeItems: "center", minHeight: 120 }}>
                <Spinner size="sm" label="Searching" />
              </div>
            </Card>
          )}
          {searchQ.error && !searchQ.data && (
            <Card variant="plain">
              <EmptyState>API offline — search is unavailable.</EmptyState>
            </Card>
          )}

          {sorted.map((r, i) => (
            <Card
              variant="flush"
              className={`result${sel?.id === r.id ? " selected" : ""}`}
              style={{ display: "block" }}
              key={r.id}
              onClick={() => setSelId(r.id)}
            >
              <div className="row" style={{ alignItems: "flex-start", gap: 14 }}>
                <div className="result__srcicon">
                  <SourceIcon source={r.source} size={26} />
                  <span className="result__srclabel">{r.srcLabel}</span>
                </div>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div className="result__heading"><Link to={`/knowledge/doc?id=${r.id}`}><h4 className="result__title">{r.title}</h4></Link>{r.chunkLabel && <span className="conversation-badge">{r.chunkLabel}</span>}</div>
                  <div className="result__snippet">{r.snippet}</div>
                  <div className="result__meta">
                    <Avatar name={r.who} initials={r.whoInitials} size="sm" tint={(((i % 4) + 1) as 1 | 2 | 3 | 4)} />
                    {r.source === "slack" ? <><span>{r.messageCount} messages</span><span>{r.participantCount} participants</span><span>{fmtDate(r.date)}</span></> : <>{r.who} · {fmtDate(r.date)}{r.tags.map((t) => <Pill key={t} kind={t} />)}</>}
                  </div>
                </div>
                <Button
                  icon
                  aria-label={saved.has(r.id) ? "Remove bookmark" : "Bookmark result"}
                  style={{ alignSelf: "flex-start" }}
                  onClick={(e) => {
                    e.stopPropagation();
                    toggleSaved(r.id);
                  }}
                >
                  <Ic.Bookmark size={16} style={saved.has(r.id) ? { fill: "#1c3f60", color: "#1c3f60" } : undefined} />
                </Button>
              </div>
            </Card>
          ))}
          {searchQ.data && sorted.length === 0 && (
            <Card variant="plain">
              <EmptyState>No results match the current filters. Clear a filter or try a different search.</EmptyState>
            </Card>
          )}

          <Card variant="plain">
            <div className="row" style={{ justifyContent: "space-between", font: "13px var(--sans)", color: "var(--ink-soft)" }}>
              <FooterStat value={footer.documents} label="documents" />
              <FooterStat value={footer.facts} label="verified facts" />
              <FooterStat value={footer.fresh} label="fresh" />
              <span className="wf__running"><span className="dot" /> Live ingestion · {footer.sources} sources</span>
            </div>
          </Card>
        </div>

        {/* inspector */}
        {insp && sel ? (
          <Inspector
            insp={insp}
            sel={sel}
            factRows={factRows}
            relatedRows={relatedRows}
            timeline={timeline}
            onTagsChange={(id, tags) => setTagOverrides((items) => ({ ...items, [id]: tags }))}
          />
        ) : (
          <Card variant="flush" className="inspector">
            <div style={{ display: "grid", placeItems: "center", minHeight: 160 }}>
              {searchQ.loading ? <Spinner size="sm" /> : <EmptyState>Select a result to inspect it.</EmptyState>}
            </div>
          </Card>
        )}
      </div>
    </>
  );
}
