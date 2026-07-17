// Document page: a real editor over document(id).body markdown — contentEditable
// blocks, working toolbar, save via updateDocument, live outline, findings
// underlined in the prose, review-before-apply that actually rewrites the body,
// live revision history, and the fact-check panel.

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import * as Ic from "../../components/icons";
import { TagChip } from "../../components/TagChip";
import { TagPicker } from "../../components/TagPicker";
import { Button, Chip, EmptyState, PageHeader, Spinner, fmtDateTime, useToast } from "../../components/ui";
import { gql, useQuery } from "../../lib/api";
import { ChangeQueue, type ChangeTab } from "./ChangeQueue";
import {
  SKILLS, type Block, type BlockType, type Change, type DocMeta, type Finding, type Rev,
} from "./data";
import { Editor, type Annot } from "./Editor";
import { FindingsPanel, type FactTab } from "./FindingsPanel";
import {
  blockText, cleanText, decorateBlock, escapeHtml, hasOwnNumbering, mdInline, nid,
  parseMarkdown, serialize, stripMarks,
} from "./markdown";
import { OutlinePanel, RevisionsPanel, type OutlineItem } from "./OutlinePanel";
import { RefinePanel, type RefineScope } from "./RefinePanel";
import "../docreview.css";

export default function DocReview() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const toast = useToast();
  const docId = Number(params.get("id")) || 1;

  /* ————— document + editable blocks ————— */
  const [doc, setDoc] = useState<DocMeta | null>(null); // null → not loaded yet
  const [apiUp, setApiUp] = useState<boolean | null>(null);
  const serverBodyRef = useRef(false); // server returned a non-empty body
  const [blocks, setBlocks] = useState<Block[]>([]);
  const [dirty, setDirty] = useState(false);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved">("idle");
  const [focusedId, setFocusedId] = useState<number | null>(null);
  const [docTags, setDocTags] = useState<string[]>([]);
  const [watched, setWatched] = useState(false);
  // Live heading text, captured on input so the outline updates while typing
  // without render ever reading the block refs.
  const [liveHeadings, setLiveHeadings] = useState<Record<number, string | undefined>>({});

  const blockEls = useRef(new Map<number, HTMLElement>());
  const focusNext = useRef<{ id: number; atEnd: boolean } | null>(null);
  const editorRef = useRef<HTMLDivElement>(null);

  const loadDoc = useCallback(async (preserveScroll = false) => {
    const d: any = await gql(
      `query($id: Int!) { document(id: $id) { id source title body author authorInitials date tags watched } }`,
      { id: docId },
    );
    const live = !!d?.document;
    setApiUp(live);
    if (!live) return; // offline / missing doc — the page shows an honest empty state
    const meta: DocMeta = {
      id: d.document.id ?? docId,
      title: d.document.title ?? "Untitled document",
      author: d.document.author ?? "",
      authorInitials: d.document.authorInitials ?? "",
      date: d.document.date ?? "",
      tags: d.document.tags ?? [],
      watched: !!d.document.watched,
      source: d.document.source ?? "docs",
    };
    setDoc(meta);
    setDocTags(meta.tags);
    setWatched(meta.watched);
    const rawBody: string = typeof d.document.body === "string" ? d.document.body : "";
    serverBodyRef.current = !!rawBody.trim();
    let bs = parseMarkdown(rawBody);
    if (!bs.some((b) => b.type === "h1")) bs = [{ id: nid(), type: "h1", html: mdInline(meta.title) }, ...bs];
    const y = window.scrollY;
    setLiveHeadings({});
    setBlocks(bs);
    setDirty(false);
    setFocusedId((f) => f ?? bs[0]?.id ?? null);
    if (preserveScroll) requestAnimationFrame(() => window.scrollTo(0, y));
  }, [docId]);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch; loadDoc sets state only after the request resolves
  useEffect(() => { void loadDoc(); }, [loadDoc]);

  /* ————— findings & facts ————— */
  const findingsQ = useQuery<Finding[]>(
    `{ findings(documentId: ${docId}) { id kind severity text note } }`,
    { map: (d) => d.findings ?? [] },
  );
  const findings = useMemo(() => findingsQ.data ?? [], [findingsQ.data]);

  const factsQ = useQuery<{ id: number; claim: string; source: string; owner: string; ownerTint: number; status: string; verified: string }[]>(
    `{ facts { id claim source owner ownerTint status verified } }`,
    { map: (d) => d.facts ?? [] },
  );
  const factsData = factsQ.data ?? [];

  /* ————— revision history (live) ————— */
  const revsQ = useQuery<Rev[]>(
    `{ revisions(documentId: ${docId}) { id actor verb target at } }`,
    { map: (d) => d.revisions ?? [] },
  );
  const revs = revsQ.data ?? [];
  const refetchRevs = revsQ.refetch;

  /* ————— rendered (decorated) block html ————— */
  const rendered = useMemo(() => {
    const done = new Set<number>();
    return blocks.map((b) => (b.type === "code" ? b.html : decorateBlock(b.html, findings, done)));
  }, [blocks, findings]);

  const bodyText = useMemo(() => blocks.map((b) => blockText(b.html)).join("\n"), [blocks]);

  /* ————— block editing ————— */
  const bindRef = (id: number) => (el: HTMLElement | null) => {
    if (el) blockEls.current.set(id, el);
    else blockEls.current.delete(id);
  };

  const domHtml = (b: Block): string | null => {
    const el = blockEls.current.get(b.id);
    if (!el) return null;
    return b.type === "code" ? escapeHtml(el.innerText.replace(/\n+$/, "")) : stripMarks(el.innerHTML);
  };

  const commitBlock = (id: number) => {
    setBlocks((bs) => bs.map((b) => {
      if (b.id !== id) return b;
      const html = domHtml(b);
      return html === null || html === b.html ? b : { ...b, html };
    }));
  };

  const collectAll = useCallback((): Block[] => blocks.map((b) => {
    const html = domHtml(b);
    return html === null || html === b.html ? b : { ...b, html };
  }), [blocks]);

  const onBlockInput = (b: Block) => {
    setDirty(true);
    if (b.type === "h2" || b.type === "h3") {
      const t = blockEls.current.get(b.id)?.textContent ?? "";
      setLiveHeadings((m) => (m[b.id] === t ? m : { ...m, [b.id]: t }));
    }
  };

  const onBlockKeyDown = (e: React.KeyboardEvent, b: Block) => {
    const el = blockEls.current.get(b.id);
    if (!el) return;
    if (e.key === "Enter" && !e.shiftKey && b.type !== "code") {
      e.preventDefault();
      let tail = "";
      const sel = window.getSelection();
      if (sel && sel.rangeCount) {
        const r = sel.getRangeAt(0);
        const rest = r.cloneRange();
        rest.selectNodeContents(el);
        rest.setStart(r.endContainer, r.endOffset);
        const tmp = document.createElement("div");
        tmp.appendChild(rest.extractContents());
        tail = stripMarks(tmp.innerHTML);
      }
      const head = stripMarks(el.innerHTML);
      const newId = nid();
      focusNext.current = { id: newId, atEnd: false };
      setDirty(true);
      if (b.type === "h2" || b.type === "h3") setLiveHeadings((m) => ({ ...m, [b.id]: blockText(head) }));
      setBlocks((bs) => {
        const ix = bs.findIndex((x) => x.id === b.id);
        if (ix === -1) return bs;
        const nb: Block = { id: newId, type: b.type === "li" ? "li" : "p", html: tail };
        return [...bs.slice(0, ix), { ...bs[ix], html: head }, nb, ...bs.slice(ix + 1)];
      });
    } else if (e.key === "Backspace" && (el.textContent ?? "").trim() === "" && blocks.length > 1) {
      e.preventDefault();
      setDirty(true);
      setBlocks((bs) => {
        const ix = bs.findIndex((x) => x.id === b.id);
        if (ix === -1) return bs;
        const prev = bs[ix - 1] ?? bs[ix + 1];
        if (prev) focusNext.current = { id: prev.id, atEnd: true };
        return bs.filter((x) => x.id !== b.id);
      });
    }
  };

  useEffect(() => {
    const f = focusNext.current;
    if (!f) return;
    focusNext.current = null;
    const el = blockEls.current.get(f.id);
    if (!el) return;
    el.focus();
    const r = document.createRange();
    r.selectNodeContents(el);
    r.collapse(!f.atEnd);
    const sel = window.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(r);
  }, [blocks]);

  /* ————— toolbar ————— */
  const [fmt, setFmt] = useState({ bold: false, italic: false, underline: false });
  const [hasSel, setHasSel] = useState(false);
  useEffect(() => {
    const onSel = () => {
      const sel = window.getSelection();
      const inEditor = !!sel && !sel.isCollapsed && !!sel.anchorNode &&
        !!editorRef.current?.contains(sel.anchorNode);
      setHasSel((prev) => (prev === inEditor ? prev : inEditor));
      try {
        const next = {
          bold: document.queryCommandState("bold"),
          italic: document.queryCommandState("italic"),
          underline: document.queryCommandState("underline"),
        };
        setFmt((p) => (p.bold === next.bold && p.italic === next.italic && p.underline === next.underline ? p : next));
      } catch { /* not supported — leave states as-is */ }
    };
    document.addEventListener("selectionchange", onSel);
    return () => document.removeEventListener("selectionchange", onSel);
  }, []);

  const exec = (cmd: "bold" | "italic" | "underline") => {
    document.execCommand(cmd);
    setDirty(true);
  };

  const focusedBlock = blocks.find((b) => b.id === focusedId) ?? null;
  const setBlockType = (type: BlockType) => {
    if (!focusedBlock || focusedBlock.type === "code") return;
    const html = domHtml(focusedBlock);
    setDirty(true);
    setBlocks((bs) => bs.map((b) => (b.id === focusedBlock.id ? { ...b, type, html: html ?? b.html } : b)));
  };

  const [justify, setJustify] = useState(false);
  const [airy, setAiry] = useState(false);

  /* ————— save ————— */
  const save = useCallback(async () => {
    if (!doc || !dirty || !apiUp || saveState === "saving" || !blocks.length) return;
    const collected = collectAll();
    setBlocks(collected);
    const md = serialize(collected);
    const h1 = collected.find((b) => b.type === "h1");
    const title = h1 ? blockText(h1.html) : doc.title;
    setSaveState("saving");
    const d: any = await gql(
      `mutation($id: Int!, $body: String!, $title: String) { updateDocument(id: $id, body: $body, title: $title) }`,
      { id: docId, body: md, title: title || doc.title },
    );
    if (d?.updateDocument) {
      serverBodyRef.current = true;
      setDirty(false);
      setSaveState("saved");
      if (title && title !== doc.title) setDoc((m) => (m ? { ...m, title } : m));
      refetchRevs();
      setTimeout(() => setSaveState("idle"), 2200);
    } else {
      setApiUp(false);
      setSaveState("idle");
    }
  }, [dirty, apiUp, saveState, blocks, collectAll, doc, docId, refetchRevs]);

  const saveRef = useRef(save);
  useEffect(() => { saveRef.current = save; }, [save]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void saveRef.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  /* ————— outline (derived from heading blocks) ————— */
  const outline = useMemo(() => {
    const items: OutlineItem[] = [];
    let h2 = 0, h3 = 0;
    for (const b of blocks) {
      if (b.type !== "h2" && b.type !== "h3") continue;
      const liveText = liveHeadings[b.id] ?? blockText(b.html);
      // Headings that carry their own numbering ("2.1 Phase 1") skip the auto number.
      if (b.type === "h2") {
        h2++; h3 = 0;
        items.push({ id: b.id, n: hasOwnNumbering(liveText) ? "" : `${h2}.`, t: liveText, sub: false });
      } else {
        h3++;
        items.push({ id: b.id, n: hasOwnNumbering(liveText) ? "" : `${h2}.${h3}`, t: liveText, sub: true });
      }
    }
    return items;
  }, [blocks, liveHeadings]);

  const flash = (el: Element | null | undefined) => {
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add("dr-flash");
    setTimeout(() => el.classList.remove("dr-flash"), 1400);
  };
  const jumpToBlock = (id: number) => flash(blockEls.current.get(id));
  const jumpToFinding = (fid: number) => flash(editorRef.current?.querySelector(`span.fmark[data-fid="${fid}"]`));

  /* ————— margin annotations, positioned next to their match ————— */
  const [annTops, setAnnTops] = useState<Record<string, number>>({});
  useLayoutEffect(() => {
    const cont = editorRef.current;
    if (!cont) return;
    const crect = cont.getBoundingClientRect();
    const tops: Record<string, number> = {};
    cont.querySelectorAll<HTMLElement>("span.fmark").forEach((s) => {
      const fid = s.dataset.fid;
      if (fid && !(fid in tops)) tops[fid] = Math.max(6, Math.round(s.getBoundingClientRect().top - crect.top));
    });
    setAnnTops((prev) => (JSON.stringify(prev) === JSON.stringify(tops) ? prev : tops));
  }, [rendered, findings]);

  const annots = useMemo<Annot[]>(() => {
    const fs = findings.filter((f) => f.severity === "warn" || f.severity === "error").slice(0, 6);
    const items = fs.map((f) => {
      const rule = f.id < 0 && f.kind === "prose" ? f.note : f.kind === "fact" ? "fact-check" : f.kind;
      const red = f.kind === "fact" || f.severity === "error";
      return { fid: f.id, rule, red, quote: `“${cleanText(f.text)}”`, top: annTops[String(f.id)] as number | undefined };
    });
    const placed = items.filter((i) => i.top !== undefined).sort((a, b) => a.top! - b.top!);
    let last = -46;
    for (const p of placed) { p.top = Math.max(p.top!, last + 46); last = p.top; }
    for (const u of items.filter((i) => i.top === undefined)) { last += 46; u.top = Math.max(6, last); }
    return items.slice().sort((a, b) => a.top! - b.top!);
  }, [findings, annTops]);

  /* ————— changes queue ————— */
  const [changes, setChanges] = useState<Change[]>([]);
  const [changesNonce, setChangesNonce] = useState(0);
  const changesLive = changes.some((c) => c.id > 0);
  useEffect(() => {
    let alive = true;
    gql(`{ changes(documentId: ${docId}) { id original replacement reason status } }`).then((d: any) => {
      if (alive && d?.changes) {
        setChanges(d.changes.map((c: any) => ({
          id: c.id, original: c.original, proposed: c.replacement, rule: c.reason,
          state: (c.status ?? "pending") as Change["state"],
        })));
      }
    });
    return () => { alive = false; };
  }, [docId, changesNonce]);

  const isApplied = (c: Change) => c.state === "pending" && !bodyText.includes(cleanText(c.original));

  const applyLocal = (orig: string, repl: string) => {
    const o = escapeHtml(cleanText(orig));
    const r = escapeHtml(cleanText(repl));
    if (!o) return;
    setLiveHeadings({}); // block html may change under the outline — fall back to it
    setBlocks((bs) => bs.map((b) => (b.html.includes(o) ? { ...b, html: b.html.split(o).join(r) } : b)));
  };

  const accept = async (c: Change) => {
    setChanges((cs) => cs.map((x) => (x.id === c.id ? { ...x, state: "accepted" } : x)));
    const wasDirty = dirty;
    applyLocal(c.original, c.proposed);
    const live = c.id > 0 && apiUp;
    if (live) await gql(`mutation { setChangeStatus(id: ${c.id}, status: "accepted") }`);
    if (live && serverBodyRef.current && !wasDirty) await loadDoc(true); // editor shows the server-applied text
    else setDirty(true); // applied locally — persists on the next save
  };

  const reject = (c: Change) => {
    setChanges((cs) => cs.map((x) => (x.id === c.id ? { ...x, state: "rejected" } : x)));
    if (c.id > 0) void gql(`mutation { setChangeStatus(id: ${c.id}, status: "rejected") }`);
  };

  const pending = changes.filter((c) => c.state === "pending");
  const acceptAll = async () => {
    const ps = pending;
    setChanges((cs) => cs.map((c) => (c.state === "pending" ? { ...c, state: "accepted" } : c)));
    const wasDirty = dirty;
    ps.forEach((c) => applyLocal(c.original, c.proposed));
    if (changesLive && apiUp) {
      await gql(`mutation { acceptAllChanges(documentId: ${docId}) }`);
      if (serverBodyRef.current && !wasDirty) await loadDoc(true);
      else setDirty(true);
      setChangesNonce((n) => n + 1);
    } else {
      setDirty(true);
    }
  };

  const [changesTab, setChangesTab] = useState<ChangeTab>("review");
  const visibleChanges = changesTab === "review" ? pending : changes;

  /* ————— refine panel ————— */
  const [skill, setSkill] = useState("Tighten");
  const [scope, setScope] = useState<RefineScope>("Whole document");
  const [refining, setRefining] = useState(false);
  const [lastRun, setLastRun] = useState<string | null>(null);
  const needsSelection = scope === "Current selection" && !hasSel;
  const runRefine = async () => {
    if (refining || needsSelection) return;
    setRefining(true);
    const api = SKILLS.find((s) => s.name === skill)?.api ?? "tighten";
    const d: any = await gql(`mutation { runRefinement(documentId: ${docId}, skill: "${api}") }`);
    setRefining(false);
    if (d && typeof d.runRefinement === "number") {
      setLastRun(`Last run: ${fmtDateTime(new Date())} (${skill}) · ${d.runRefinement} changes proposed`);
      setChangesNonce((n) => n + 1);
      findingsQ.refetch();
      refetchRevs();
      setChangesTab("review");
    } else {
      setLastRun("Last run failed — is the API running?");
    }
  };

  const errorN = findings.filter((f) => f.severity === "error").length;
  const warnN = findings.filter((f) => f.severity === "warn").length;
  const advisoryN = findings.filter((f) => f.severity !== "error" && f.severity !== "warn").length;

  const [showAllRevs, setShowAllRevs] = useState(false);

  /* ————— fact check panel ————— */
  const [fcTab, setFcTab] = useState<FactTab>("check");
  const [checking, setChecking] = useState(false);
  const runFactCheck = async () => {
    if (checking) return;
    setChecking(true);
    await gql(`mutation { factCheck(documentId: ${docId}) }`);
    setChecking(false);
    findingsQ.refetch();
  };

  const contra = findings.find((f) => f.kind === "fact" && f.severity === "error") ?? null;
  const contradictionN = findings.filter((f) => f.kind === "fact" && f.severity === "error").length;
  const unsupportedN = findings.filter((f) => (f.kind === "fact" && f.severity !== "error") || f.kind === "freshness").length;
  const supportedN = factsData.filter((f) => f.status === "Verified").length;
  const allClaimsN = supportedN + contradictionN + unsupportedN;
  const evidence = contra ? contra.note.replace(/^Contradicts verified fact:\s*/i, "") : "";

  /* ————— header actions ————— */
  const toggleWatch = async () => {
    setWatched((w) => !w); // optimistic
    const d: any = await gql(`mutation { toggleWatch(documentId: ${docId}) }`);
    if (d && typeof d.toggleWatch === "boolean") setWatched(d.toggleWatch);
  };

  const share = () => {
    navigator.clipboard?.writeText(window.location.href).catch(() => {});
    toast("Link copied to clipboard");
  };

  // Honest states before the document arrives: no canned doc, ever.
  if (!doc) {
    return (
      <>
        <PageHeader title="Document" backLink={{ to: "/knowledge", label: "Library" }} />
        <div className="card" style={{ display: "grid", placeItems: "center", minHeight: 220 }}>
          {apiUp === null ? (
            <Spinner size="md" label="Loading document" />
          ) : (
            <EmptyState icon={<Ic.Doc size={22} />}>
              API offline — this document can't be loaded right now.
            </EmptyState>
          )}
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={doc.title}
        backLink={{ to: "/knowledge", label: "Library" }}
        description={`Owner ${doc.author} · Last verified ${doc.date}`}
        actions={(
          <>
            {docTags.map((t) => <TagChip key={t} tag={t} />)}
            <TagPicker compact tags={docTags} onChange={setDocTags} />
            {dirty && apiUp === false && (
              <span className="card__hint dr-offline">API offline — can't save</span>
            )}
            {(dirty || saveState === "saving") && (
              <Button variant="primary" onClick={() => void save()} disabled={!apiUp || saveState === "saving"}>
                {saveState === "saving" ? "Saving…" : "Save"}
              </Button>
            )}
            {!dirty && saveState === "saved" && (
              <Chip tone="green" icon={<Ic.Check size={12} />}>Saved</Chip>
            )}
            <Button onClick={() => void toggleWatch()} aria-pressed={watched} className="dr-watch">
              <Ic.Bell size={14} /> {watched ? "Watching" : "Watch"}
            </Button>
            <Button onClick={share}><Ic.LineageIcon size={14} /> Share</Button>
          </>
        )}
      />

      <div className="dr-grid">
        {/* left rail */}
        <div className="stack">
          <OutlinePanel items={outline} onJump={jumpToBlock} />
          <RevisionsPanel revs={revs} showAll={showAllRevs} onToggleShowAll={() => setShowAllRevs((v) => !v)} />
        </div>

        <Editor
          blocks={blocks}
          rendered={rendered}
          focusedBlock={focusedBlock}
          fmt={fmt}
          justify={justify}
          airy={airy}
          onToggleJustify={() => setJustify((v) => !v)}
          onToggleAiry={() => setAiry((v) => !v)}
          onExec={exec}
          onSetBlockType={setBlockType}
          bindRef={bindRef}
          editorRef={editorRef}
          onBlockInput={onBlockInput}
          onBlockKeyDown={onBlockKeyDown}
          onBlockBlur={commitBlock}
          onBlockFocus={setFocusedId}
          annots={annots}
          onJumpToFinding={jumpToFinding}
        />

        <RefinePanel
          skill={skill}
          onPickSkill={setSkill}
          scope={scope}
          onPickScope={setScope}
          refining={refining}
          needsSelection={needsSelection}
          lastRun={lastRun}
          onRun={() => void runRefine()}
          errorN={errorN}
          warnN={warnN}
          advisoryN={advisoryN}
        />
      </div>

      {/* bottom: review before apply + fact check */}
      <div className="dr-bottom">
        <ChangeQueue
          tab={changesTab}
          onTab={setChangesTab}
          changes={changes}
          visible={visibleChanges}
          pendingCount={pending.length}
          isApplied={isApplied}
          onAccept={(c) => void accept(c)}
          onReject={reject}
          onAcceptAll={() => void acceptAll()}
        />
        <FindingsPanel
          tab={fcTab}
          onTab={setFcTab}
          supportedN={supportedN}
          contradictionN={contradictionN}
          unsupportedN={unsupportedN}
          allClaimsN={allClaimsN}
          checking={checking}
          onRunCheck={() => void runFactCheck()}
          claims={factsData}
          contra={contra}
          evidence={evidence}
          onJumpToFinding={jumpToFinding}
          onOpenSource={() => navigate("/knowledge?q=Auth%20RFC")}
        />
      </div>
    </>
  );
}
