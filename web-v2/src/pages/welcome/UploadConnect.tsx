// Upload connect drawer — real multipart ingestion through POST /onboard/upload.
// Every per-file row (chunks/embedded/error) comes from the server's response;
// re-uploading unchanged content honestly shows 0 embedded (hash skip).

import { useRef, useState } from "react";
import * as Ic from "../../components/icons";
import { Button, Chip, Drawer, Spinner } from "../../components/ui";
import type { SessionConnection } from "./catalog";

const ACCEPT = ".md,.mdx,.markdown,.txt";
const MAX_FILES = 20;
const MAX_BYTES = 1_000_000;

export type UploadedFile = { name: string; docId: number | null; chunks: number; embedded: number; error?: string };
type UploadResponse = { ok: boolean; sourceId: number; files: UploadedFile[] };

export function UploadConnect({ onClose, onConnected }: {
  onClose: () => void;
  onConnected: (c: SessionConnection, files: UploadedFile[]) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<UploadResponse | null>(null);

  const upload = async (list: FileList | File[]) => {
    const files = Array.from(list);
    if (files.length === 0) return;
    if (files.length > MAX_FILES) {
      setError(`At most ${MAX_FILES} files per upload — you picked ${files.length}.`);
      return;
    }
    const tooBig = files.find((f) => f.size > MAX_BYTES);
    if (tooBig) {
      setError(`“${tooBig.name}” is over the 1 MB per-file limit.`);
      return;
    }
    setError("");
    setUploading(true);
    const form = new FormData();
    for (const f of files) form.append("files", f);
    try {
      const r = await fetch("/onboard/upload", { method: "POST", body: form });
      const j = await r.json().catch(() => null);
      if (!r.ok || !j || j.ok !== true) {
        setError(typeof j?.detail === "string" ? j.detail : `Upload failed — the server answered ${r.status}.`);
      } else {
        const res = j as UploadResponse;
        setResult(res);
        const ok = res.files.filter((f) => f.docId);
        if (ok.length > 0) {
          onConnected(
            { provider: "upload", name: "Upload", sourceId: res.sourceId, detail: `${ok.length} file${ok.length === 1 ? "" : "s"}`, polls: false },
            res.files,
          );
        }
      }
    } catch {
      setError("Upload failed — the API didn’t answer.");
    }
    setUploading(false);
  };

  const okFiles = result?.files.filter((f) => f.docId) ?? [];
  const totalChunks = okFiles.reduce((n, f) => n + f.chunks, 0);
  const totalEmbedded = okFiles.reduce((n, f) => n + f.embedded, 0);

  return (
    <Drawer
      open
      onOpenChange={(o) => { if (!o && !uploading) onClose(); }}
      title={<span className="wc-drawer-title"><Ic.Send size={17} /> Upload files</span>}
      width={560}
      footer={
        <div className="wc-foot">
          <span className="card__hint">.md / .txt · up to {MAX_FILES} files · 1 MB each</span>
          <Button variant="primary" onClick={onClose} disabled={uploading}>
            {result ? <>Done <Ic.CheckCircle size={14} /></> : "Close"}
          </Button>
        </div>
      }
    >
      <p className="wc-drawer-sub">
        Files go through the same document → chunk → embed pipeline GitHub sync uses, and land in the shared library.
      </p>

      <div
        className={`wc-drop${dragging ? " wc-drop--over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); void upload(e.dataTransfer.files); }}
      >
        {uploading ? (
          <span className="wc-center"><Spinner size="sm" /> Uploading and embedding…</span>
        ) : (
          <>
            <Ic.Send size={22} />
            <span>Drag files here, or</span>
            <Button compact onClick={() => inputRef.current?.click()}>Browse files</Button>
            <input
              ref={inputRef}
              type="file"
              accept={ACCEPT}
              multiple
              hidden
              onChange={(e) => { if (e.target.files) void upload(e.target.files); e.target.value = ""; }}
            />
          </>
        )}
      </div>

      {error && <div className="wc-error" role="alert">{error}</div>}

      {result && (
        <div className="wc-upload-results">
          <div className="wc-ok">
            <Ic.CheckCircle size={15} /> {okFiles.length} file{okFiles.length === 1 ? "" : "s"} ingested ·{" "}
            {totalChunks} chunks · {totalEmbedded} embedded
            {totalEmbedded < totalChunks && " (unchanged chunks skipped by content hash)"}
          </div>
          <ul className="wc-filelist">
            {result.files.map((f, i) => (
              <li key={`${f.name}-${i}`} className={f.docId ? "" : "wc-filelist__failed"}>
                <Ic.Doc size={14} />
                <span className="wc-filelist__name">{f.name}</span>
                {f.docId
                  ? <span className="card__hint">{f.chunks} chunks · {f.embedded} embedded</span>
                  : <Chip tone="red" caps>{f.error ?? "failed"}</Chip>}
              </li>
            ))}
          </ul>
          <span className="card__hint">You can drop more files to add them to the same Uploads source.</span>
        </div>
      )}
    </Drawer>
  );
}
