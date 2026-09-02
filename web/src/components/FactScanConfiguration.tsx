import { useEffect, useRef, useState } from "react";
import { useQuery } from "../lib/api";

export type FactScanConfig = {
  source_ids: number[];
  query: string;
  tag: string;
  path_glob: string;
  limit: number;
  claims_per_document: number;
  extraction_max_calls: number;
  extraction_max_input_tokens: number;
  extraction_max_output_tokens: number;
  retrieval_backend: "postgres";
  fact_neighbors: number;
  evidence_neighbors: number;
  max_components: number;
  minimum_fact_similarity: number;
  minimum_evidence_similarity: number;
  adjudication_mode: "off" | "llm";
  adjudication_max_calls: number;
  adjudication_max_input_tokens: number;
  adjudication_max_output_tokens: number;
  cluster_label_mode: "off" | "llm";
  cluster_minimum_similarity: number;
  cluster_max_llm_calls: number;
  schedule_minutes: number;
  review_mode: "human" | "ai";
  review_instructions: string;
  publish_status: "needs_review" | "verified";
};

type ReviewStrategy = "human" | "guided" | "auto";

type RequestDetail = { finish: (config: FactScanConfig | null) => void };
const EVENT = "mari:configure-fact-scan";
// Tab stops the dialog may land on. Disabled controls are skipped so the trap
// never parks focus on a greyed-out adjudication field.
const FOCUSABLE = "input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled])";

export function requestFactScanConfiguration(): Promise<FactScanConfig | null> {
  return new Promise((finish) => {
    window.dispatchEvent(new CustomEvent<RequestDetail>(EVENT, { detail: { finish } }));
  });
}

const SOURCE_QUERY = `{
  sourcePulse { id name }
  workflows { name nodes trigger }
}`;

type ConfigQuery = {
  sourcePulse: { id: number; name: string }[];
  workflows: { name: string; nodes: { kind?: string; config?: Record<string, unknown> }[]; trigger: Record<string, unknown> }[];
};

export function FactScanConfiguration() {
  const sources = useQuery<ConfigQuery>(SOURCE_QUERY);
  const [request, setRequest] = useState<RequestDetail | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [query, setQuery] = useState("");
  const [tag, setTag] = useState("");
  const [pathGlob, setPathGlob] = useState("");
  const [limit, setLimit] = useState(50);
  const [claims, setClaims] = useState(2);
  const [extractionCalls, setExtractionCalls] = useState(50);
  const [extractionInput, setExtractionInput] = useState(100000);
  const [extractionOutput, setExtractionOutput] = useState(20000);
  const [factNeighbors, setFactNeighbors] = useState(8);
  const [evidenceNeighbors, setEvidenceNeighbors] = useState(8);
  const [maxComponents, setMaxComponents] = useState(12);
  const [factSimilarity, setFactSimilarity] = useState(.72);
  const [evidenceSimilarity, setEvidenceSimilarity] = useState(.68);
  const [reviewStrategy, setReviewStrategy] = useState<ReviewStrategy>("auto");
  const [adjudicationCalls, setAdjudicationCalls] = useState(50);
  const [adjudicationInput, setAdjudicationInput] = useState(120000);
  const [adjudicationOutput, setAdjudicationOutput] = useState(40000);
  const [clusterLabelMode, setClusterLabelMode] = useState<"off" | "llm">("off");
  const [clusterSimilarity, setClusterSimilarity] = useState(.78);
  const [clusterCalls, setClusterCalls] = useState(5);
  const [schedule, setSchedule] = useState(60);
  const [reviewInstructions, setReviewInstructions] = useState("");
  const [publishStatus, setPublishStatus] = useState<"needs_review" | "verified">("needs_review");
  const dialogRef = useRef<HTMLElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const backdropPressRef = useRef(false);
  const backdropReleaseRef = useRef(false);

  useEffect(() => {
    const open = (event: Event) => {
      // Captured here, inside the click that opened us, not in the effect
      // below: the page disables the Scan button in the same commit that
      // mounts the dialog, and a disabled button is blurred before any
      // effect can read activeElement.
      openerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      const workflow = (sources.data?.workflows ?? []).find((row) =>
        (row.nodes ?? []).some((node) => node.kind === "scan_facts"));
      const fetch = (workflow?.nodes ?? []).find((node) => node.kind === "fetch_docs")?.config ?? {};
      const scan = (workflow?.nodes ?? []).find((node) => node.kind === "scan_facts")?.config ?? {};
      const retrieval = (workflow?.nodes ?? []).find((node) => node.kind === "map_fact_impact")?.config ?? {};
      const adjudication = (workflow?.nodes ?? []).find((node) => node.kind === "adjudicate_facts")?.config ?? {};
      const cluster = (workflow?.nodes ?? []).find((node) => node.kind === "cluster_facts")?.config ?? {};
      const review = (workflow?.nodes ?? []).find((node) => node.kind === "review_facts")?.config ?? {};
      const publish = (workflow?.nodes ?? []).find((node) => node.kind === "publish_facts")?.config ?? {};
      setSelected(Array.isArray(fetch.source_ids) ? fetch.source_ids.map(Number) : []);
      setQuery(String(fetch.query ?? ""));
      setTag(String(fetch.tag ?? ""));
      setPathGlob(String(fetch.path_glob ?? ""));
      setLimit(Number(fetch.k ?? 50));
      setClaims(Number(scan.claims_per_document ?? 2));
      setExtractionCalls(Number(scan.max_llm_calls ?? 50));
      setExtractionInput(Number(scan.max_input_tokens ?? 100000));
      setExtractionOutput(Number(scan.max_output_tokens ?? 20000));
      setFactNeighbors(Number(retrieval.fact_neighbors ?? 8));
      setEvidenceNeighbors(Number(retrieval.evidence_neighbors ?? 8));
      setMaxComponents(Number(retrieval.max_components ?? 12));
      setFactSimilarity(Number(retrieval.minimum_fact_similarity ?? .72));
      setEvidenceSimilarity(Number(retrieval.minimum_evidence_similarity ?? .68));
      setReviewStrategy(adjudication.mode !== "llm"
        ? "human"
        : review.mode === "ai" ? "auto" : "guided");
      setAdjudicationCalls(Number(adjudication.max_calls ?? 50));
      setAdjudicationInput(Number(adjudication.max_input_tokens ?? 120000));
      setAdjudicationOutput(Number(adjudication.max_output_tokens ?? 40000));
      setClusterLabelMode(cluster.label_mode === "llm" ? "llm" : "off");
      setClusterSimilarity(Number(cluster.minimum_similarity ?? .78));
      setClusterCalls(Number(cluster.max_llm_clusters ?? 5));
      setReviewInstructions(String(review.instructions ?? scan.instructions ?? ""));
      setPublishStatus(publish.status === "verified" ? "verified" : "needs_review");
      // Opening the dialog can race the workflow query. A missing row while
      // loading is not evidence that the seeded hourly workflow is manual.
      setSchedule(workflow
        ? (workflow.trigger?.on === "schedule"
          ? Number(workflow.trigger.every_minutes ?? 60)
          : 0)
        : 60);
      setRequest((event as CustomEvent<RequestDetail>).detail);
    };
    window.addEventListener(EVENT, open);
    return () => window.removeEventListener(EVENT, open);
  }, [sources.data]);

  // aria-modal promises a modal; these keep the promise. Focus enters the
  // first field on open, Tab cycles inside the dialog, and Escape cancels.
  // The listener sits on the document so a click on non-focusable text inside
  // the dialog (which focuses the section itself, tabIndex -1) does not let
  // Tab escape. An overlay stacked above us (the command palette, a Radix
  // layer) that already consumed the key keeps it: cancelling this dialog on
  // a second-hand Escape would discard the form.
  useEffect(() => {
    if (!request) return;
    dialogRef.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      const dialog = dialogRef.current;
      if (!dialog || event.defaultPrevented) return;
      if (event.key === "Escape") {
        event.preventDefault();
        request.finish(null);
        setRequest(null);
        return;
      }
      if (event.key !== "Tab") return;
      const stops = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (stops.length === 0) return;
      const first = stops[0];
      const last = stops[stops.length - 1];
      const active = document.activeElement;
      // The section is a focus target but not a tab stop, so focus on it
      // wraps the same way focus outside the dialog does; otherwise Shift+Tab
      // from the section walks to the previous tabbable behind the modal.
      const inside = active instanceof HTMLElement && active !== dialog && dialog.contains(active);
      if (!inside || active === (event.shiftKey ? first : last)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      // Every way out (Escape, Cancel, Save, backdrop) lands here. Hand focus
      // back to whatever opened the dialog, so a keyboard user is not dropped
      // at the top of the page. Deferred a frame so the page has settled.
      // After Save the Scan button stays disabled for the whole run, and
      // focus() on a disabled control is a silent no-op that leaves focus on
      // body; park it on the main landmark instead.
      const opener = openerRef.current;
      openerRef.current = null;
      if (!opener) return;
      window.requestAnimationFrame(() => {
        if (opener.isConnected && !opener.matches(":disabled")) {
          opener.focus();
          return;
        }
        document.getElementById("main-content")?.focus();
      });
    };
  }, [request]);

  if (!request) return null;
  const close = (value: FactScanConfig | null) => {
    request.finish(value);
    setRequest(null);
  };
  const rows = sources.data?.sourcePulse ?? [];

  return (
    <div
      className="fixed inset-0 z-[100] grid place-items-center bg-ink/30 p-4"
      role="presentation"
      // A click that both starts and ends on the backdrop cancels. Tracking
      // the press and the release keeps a drag that crosses the dialog edge in
      // either direction from closing it: the browser dispatches click to the
      // common ancestor, which is the backdrop itself.
      onMouseDown={(event) => { backdropPressRef.current = event.target === event.currentTarget; }}
      onMouseUp={(event) => { backdropReleaseRef.current = event.target === event.currentTarget; }}
      onClick={(event) => {
        if (backdropPressRef.current && backdropReleaseRef.current && event.target === event.currentTarget) close(null);
        backdropPressRef.current = false;
        backdropReleaseRef.current = false;
      }}
    >
      <section
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="fact-scan-title"
        tabIndex={-1}
        className="max-h-[calc(100vh-2rem)] w-full max-w-[620px] overflow-y-auto rounded-md border border-ink/15 bg-paper p-5 shadow-xl"
      >
        <div className="mb-5">
          <div className="font-term text-[11px] uppercase tracking-[0.16em] text-ink/60">Fact workflow</div>
          <h2 id="fact-scan-title" className="mt-1 text-[20px] font-semibold text-ink">Configure fact extraction</h2>
          <p className="mt-1 text-[13px] text-ink/65">
            These parameters are saved on the Fact extraction workflow and apply to manual and scheduled runs.
          </p>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <label className="text-[12px] font-medium text-ink">
            Passage text
            <input className="mt-1 w-full rounded border border-ink/20 bg-white px-3 py-2 font-normal"
              value={query} onChange={(event) => setQuery(event.target.value)} placeholder="e.g. retention policy" />
          </label>
          <label className="text-[12px] font-medium text-ink">
            Required tag
            <input className="mt-1 w-full rounded border border-ink/20 bg-white px-3 py-2 font-normal"
              value={tag} onChange={(event) => setTag(event.target.value)} placeholder="e.g. canonical" />
          </label>
          <label className="text-[12px] font-medium text-ink sm:col-span-2">
            Path or folder glob
            <input className="mt-1 w-full rounded border border-ink/20 bg-white px-3 py-2 font-normal"
              value={pathGlob} onChange={(event) => setPathGlob(event.target.value)}
              placeholder="e.g. docs/security/**" />
            <span className="mt-1 block text-[10.5px] font-normal leading-4 text-ink/60">
              Sources, path, tag, and passage text combine. Each matching passage is scanned once per content version.
            </span>
          </label>
          <label className="text-[12px] font-medium text-ink">
            Documents per run
            <input type="number" min={1} max={200}
              className="mt-1 w-full rounded border border-ink/20 bg-white px-3 py-2 font-normal"
              value={limit} onChange={(event) => setLimit(Number(event.target.value))} />
          </label>
          <label className="text-[12px] font-medium text-ink">
            Claims per passage
            <input type="number" min={1} max={10}
              className="mt-1 w-full rounded border border-ink/20 bg-white px-3 py-2 font-normal"
              value={claims} onChange={(event) => setClaims(Number(event.target.value))} />
          </label>
          <label className="text-[12px] font-medium text-ink sm:col-span-2">
            Schedule
            <select className="mt-1 w-full rounded border border-ink/20 bg-white px-3 py-2 font-normal"
              value={schedule} onChange={(event) => setSchedule(Number(event.target.value))}>
              <option value={0}>Manual only</option>
              <option value={60}>Hourly</option>
              <option value={360}>Every 6 hours</option>
              <option value={1440}>Daily</option>
              <option value={10080}>Weekly</option>
            </select>
          </label>
          <label className="text-[12px] font-medium text-ink">
            Review strategy
            <select className="mt-1 w-full rounded border border-ink/20 bg-white px-3 py-2 font-normal"
              value={reviewStrategy} onChange={(event) => setReviewStrategy(event.target.value as ReviewStrategy)}>
              <option value="human">Human only — embedding evidence</option>
              <option value="guided">AI-guided — AI proposes, you decide</option>
              <option value="auto">Bounded AI — apply confident proposals</option>
            </select>
            <span className="mt-1 block text-[10.5px] font-normal leading-4 text-ink/60">
              {reviewStrategy === "human" && "No review LLM calls. Every candidate waits for your verdict."}
              {reviewStrategy === "guided" && "AI recommendations, confidence, and rationale appear beside each candidate; you retain the gate."}
              {reviewStrategy === "auto" && "High-confidence recommendations are applied; uncertain candidates still wait for you."}
            </span>
          </label>
          <label className="text-[12px] font-medium text-ink">
            Accepted facts enter as
            <select className="mt-1 w-full rounded border border-ink/20 bg-white px-3 py-2 font-normal"
              value={publishStatus} onChange={(event) => setPublishStatus(event.target.value as "needs_review" | "verified")}>
              <option value="needs_review">Needs review</option>
              <option value="verified">Verified</option>
            </select>
          </label>
          <label className="text-[12px] font-medium text-ink sm:col-span-2">
            Extraction and review instructions
            <textarea rows={3} className="mt-1 w-full resize-y rounded border border-ink/20 bg-white px-3 py-2 font-normal"
              value={reviewInstructions} onChange={(event) => setReviewInstructions(event.target.value)}
              placeholder="e.g. Only durable operating limits; reject plans, opinions, and temporary status updates." />
          </label>
        </div>

        <fieldset className="mt-4 rounded border border-ink/12 p-3">
          <legend className="px-1 text-[12px] font-medium text-ink">Sources</legend>
          <p className="mb-2 text-[11.5px] text-ink/60">No selection scans all connected sources.</p>
          <div className="grid max-h-32 gap-2 overflow-auto sm:grid-cols-2">
            {rows.map((source) => (
              <label key={source.id} className="flex items-center gap-2 text-[12.5px] text-ink/80">
                <input type="checkbox" checked={selected.includes(source.id)} onChange={(event) =>
                  setSelected((current) => event.target.checked
                    ? [...current, source.id]
                    : current.filter((id) => id !== source.id))} />
                {source.name}
              </label>
            ))}
            {!sources.loading && rows.length === 0 && <span className="text-[12px] text-ink/60">No sources connected.</span>}
          </div>
        </fieldset>

        <fieldset className="mt-4 rounded border border-ink/12 p-3">
          <legend className="px-1 text-[12px] font-medium text-ink">Embedding retrieval</legend>
          <p className="mb-3 text-[11.5px] text-ink/60">
            PostgreSQL vectors compare bounded claim components, related facts, and source evidence. This stage does not call an LLM.
          </p>
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="text-[12px] font-medium text-ink">Fact neighbors
              <input type="number" min={1} max={50} className="mt-1 w-full rounded border border-ink/20 bg-white px-3 py-2 font-normal"
                value={factNeighbors} onChange={(event) => setFactNeighbors(Number(event.target.value))} />
            </label>
            <label className="text-[12px] font-medium text-ink">Evidence spans
              <input type="number" min={1} max={50} className="mt-1 w-full rounded border border-ink/20 bg-white px-3 py-2 font-normal"
                value={evidenceNeighbors} onChange={(event) => setEvidenceNeighbors(Number(event.target.value))} />
            </label>
            <label className="text-[12px] font-medium text-ink">Vector components
              <input type="number" min={1} max={32} className="mt-1 w-full rounded border border-ink/20 bg-white px-3 py-2 font-normal"
                value={maxComponents} onChange={(event) => setMaxComponents(Number(event.target.value))} />
            </label>
            <label className="text-[12px] font-medium text-ink">Fact similarity
              <input type="number" min={-1} max={1} step={0.01} className="mt-1 w-full rounded border border-ink/20 bg-white px-3 py-2 font-normal"
                value={factSimilarity} onChange={(event) => setFactSimilarity(Number(event.target.value))} />
            </label>
            <label className="text-[12px] font-medium text-ink">Evidence similarity
              <input type="number" min={-1} max={1} step={0.01} className="mt-1 w-full rounded border border-ink/20 bg-white px-3 py-2 font-normal"
                value={evidenceSimilarity} onChange={(event) => setEvidenceSimilarity(Number(event.target.value))} />
            </label>
            <label className="text-[12px] font-medium text-ink">Cluster similarity
              <input type="number" min={-1} max={1} step={0.01} className="mt-1 w-full rounded border border-ink/20 bg-white px-3 py-2 font-normal"
                value={clusterSimilarity} onChange={(event) => setClusterSimilarity(Number(event.target.value))} />
            </label>
          </div>
        </fieldset>

        <fieldset className="mt-4 rounded border border-ink/12 p-3">
          <legend className="px-1 text-[12px] font-medium text-ink">Bounded LLM work</legend>
          <p className="mb-3 text-[11.5px] text-ink/60">
            Extraction is required. Evidence adjudication and human-readable cluster labels are optional. Every run records these limits and actual usage.
          </p>
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="text-[12px] font-medium text-ink">Extraction calls
              <input type="number" min={0} max={200} className="mt-1 w-full rounded border border-ink/20 bg-white px-3 py-2 font-normal"
                value={extractionCalls} onChange={(event) => setExtractionCalls(Number(event.target.value))} />
            </label>
            <label className="text-[12px] font-medium text-ink">Input-token cap
              <input type="number" min={0} max={2000000} className="mt-1 w-full rounded border border-ink/20 bg-white px-3 py-2 font-normal"
                value={extractionInput} onChange={(event) => setExtractionInput(Number(event.target.value))} />
            </label>
            <label className="text-[12px] font-medium text-ink">Output-token cap
              <input type="number" min={0} max={400000} className="mt-1 w-full rounded border border-ink/20 bg-white px-3 py-2 font-normal"
                value={extractionOutput} onChange={(event) => setExtractionOutput(Number(event.target.value))} />
            </label>
            <div className="text-[12px] font-medium text-ink">AI evidence adjudication
              <div className="mt-1 rounded border border-ink/12 bg-ink/[0.025] px-3 py-2 font-normal text-ink/70">
                {reviewStrategy === "human" ? "Off — embedding context only" : "On — grounded recommendations"}
              </div>
            </div>
            <label className="text-[12px] font-medium text-ink">Adjudication calls
              <input type="number" min={0} max={100} disabled={reviewStrategy === "human"}
                className="mt-1 w-full rounded border border-ink/20 bg-white px-3 py-2 font-normal disabled:opacity-50"
                value={adjudicationCalls} onChange={(event) => setAdjudicationCalls(Number(event.target.value))} />
            </label>
            <label className="text-[12px] font-medium text-ink">Adjudication input / output
              <div className="mt-1 grid grid-cols-2 gap-1">
                <input aria-label="Adjudication input-token cap" type="number" min={0} disabled={reviewStrategy === "human"}
                  className="w-full rounded border border-ink/20 bg-white px-2 py-2 font-normal disabled:opacity-50" value={adjudicationInput}
                  onChange={(event) => setAdjudicationInput(Number(event.target.value))} />
                <input aria-label="Adjudication output-token cap" type="number" min={0} disabled={reviewStrategy === "human"}
                  className="w-full rounded border border-ink/20 bg-white px-2 py-2 font-normal disabled:opacity-50" value={adjudicationOutput}
                  onChange={(event) => setAdjudicationOutput(Number(event.target.value))} />
              </div>
            </label>
            <label className="text-[12px] font-medium text-ink">Cluster labels
              <select className="mt-1 w-full rounded border border-ink/20 bg-white px-3 py-2 font-normal" value={clusterLabelMode}
                onChange={(event) => setClusterLabelMode(event.target.value as "off" | "llm")}>
                <option value="off">Off — stable generated key</option>
                <option value="llm">On — label bounded clusters</option>
              </select>
            </label>
            <label className="text-[12px] font-medium text-ink">Cluster-label calls
              <input type="number" min={0} max={50} disabled={clusterLabelMode === "off"}
                className="mt-1 w-full rounded border border-ink/20 bg-white px-3 py-2 font-normal disabled:opacity-50"
                value={clusterCalls} onChange={(event) => setClusterCalls(Number(event.target.value))} />
            </label>
          </div>
          <div className="mt-3 rounded bg-ink/[0.04] px-3 py-2 font-term text-[11px] text-ink/65">
            Per-run ceiling: {Math.max(0, extractionCalls)} extraction call{extractionCalls === 1 ? "" : "s"}
            {reviewStrategy !== "human" ? ` + ${Math.max(0, adjudicationCalls)} adjudication` : ""}
            {clusterLabelMode === "llm" ? ` + ${Math.max(0, clusterCalls)} cluster labels` : ""}.
          </div>
        </fieldset>

        <div className="mt-5 flex justify-end gap-2">
          <button className="rounded border border-ink/20 px-4 py-2 text-[13px]" onClick={() => close(null)}>Cancel</button>
          <button className="rounded bg-ink px-4 py-2 text-[13px] font-medium text-white" onClick={() => close({
            source_ids: selected,
            query: query.trim(),
            tag: tag.trim(),
            path_glob: pathGlob.trim(),
            limit: Math.max(1, Math.min(limit || 1, 200)),
            claims_per_document: Math.max(1, Math.min(claims || 1, 10)),
            extraction_max_calls: Math.max(0, Math.min(extractionCalls || 0, 200)),
            extraction_max_input_tokens: Math.max(0, Math.min(extractionInput || 0, 2000000)),
            extraction_max_output_tokens: Math.max(0, Math.min(extractionOutput || 0, 400000)),
            retrieval_backend: "postgres",
            fact_neighbors: Math.max(1, Math.min(factNeighbors || 1, 50)),
            evidence_neighbors: Math.max(1, Math.min(evidenceNeighbors || 1, 50)),
            max_components: Math.max(1, Math.min(maxComponents || 1, 32)),
            minimum_fact_similarity: Math.max(-1, Math.min(factSimilarity, 1)),
            minimum_evidence_similarity: Math.max(-1, Math.min(evidenceSimilarity, 1)),
            adjudication_mode: reviewStrategy === "human" ? "off" : "llm",
            adjudication_max_calls: Math.max(0, Math.min(adjudicationCalls || 0, 100)),
            adjudication_max_input_tokens: Math.max(0, adjudicationInput || 0),
            adjudication_max_output_tokens: Math.max(0, adjudicationOutput || 0),
            cluster_label_mode: clusterLabelMode,
            cluster_minimum_similarity: Math.max(-1, Math.min(clusterSimilarity, 1)),
            cluster_max_llm_calls: Math.max(0, Math.min(clusterCalls || 0, 50)),
            schedule_minutes: schedule,
            review_mode: reviewStrategy === "auto" ? "ai" : "human",
            review_instructions: reviewInstructions.trim(),
            publish_status: publishStatus,
          })}>Save &amp; run now</button>
        </div>
      </section>
    </div>
  );
}
