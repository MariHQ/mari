// markdown ⇄ blocks for the DocReview editor, plus finding decoration and the
// word-level diff used by the change queue. Browser-only (uses the DOM for
// html ⇄ text conversion).

import type { Block, Finding } from "./data";

let blockSeq = 1;
export const nid = () => blockSeq++;

export const escapeHtml = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

export const mdInline = (s: string) =>
  escapeHtml(s)
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, "$1<i>$2</i>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");

// A heading is 1–3 hashes optionally followed by whitespace ("##1. Overview"
// is a real heading in live docs). `tight` records the missing space so the
// serializer can round-trip the body unchanged.
const HEADING = /^(#{1,3})(?!#)([ \t]*)(\S.*)$/;

export function parseMarkdown(md: string): Block[] {
  const blocks: Block[] = [];
  const lines = md.replace(/\r\n/g, "\n").split("\n");
  let para: string[] = [];
  const flush = () => {
    if (!para.length) return;
    const chunk = para;
    para = [];
    if (chunk.every((l) => /^[-*]\s+/.test(l))) {
      for (const l of chunk) blocks.push({ id: nid(), type: "li", html: mdInline(l.replace(/^[-*]\s+/, "")) });
      return;
    }
    const m = HEADING.exec(chunk[0]);
    if (m) {
      blocks.push({
        id: nid(),
        type: (["h1", "h2", "h3"] as const)[m[1].length - 1],
        html: mdInline(m[3]),
        tight: m[2] === "" || undefined,
      });
      const rest = chunk.slice(1).join(" ").trim();
      if (rest) blocks.push({ id: nid(), type: "p", html: mdInline(rest) });
      return;
    }
    const joined = chunk.join(" ").trim();
    if (joined && !/^(-{3,}|_{3,}|\*{3,})$/.test(joined)) blocks.push({ id: nid(), type: "p", html: mdInline(joined) });
  };
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const fence = /^```(\w*)\s*$/.exec(line);
    if (fence) {
      flush();
      const code: string[] = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) { code.push(lines[i]); i++; }
      i++; // closing fence
      blocks.push({ id: nid(), type: "code", lang: fence[1], html: escapeHtml(code.join("\n")) });
      continue;
    }
    if (line.trim() === "") flush();
    else if (HEADING.test(line)) { flush(); para = [line]; flush(); }
    else para.push(line);
    i++;
  }
  flush();
  return blocks;
}

/** Plain text of a block's html. */
export function blockText(html: string): string {
  const div = document.createElement("div");
  div.innerHTML = html;
  return (div.textContent ?? "").replace(/\u00a0/g, " ").trim();
}

/** html → markdown inline text: keep <b>/<strong> → **, <i>/<em> → *, <code> → `; strip the rest. */
function htmlToMd(html: string): string {
  const div = document.createElement("div");
  div.innerHTML = html;
  const walk = (node: Node): string => {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent ?? "";
    if (node.nodeType !== Node.ELEMENT_NODE) return "";
    const el = node as HTMLElement;
    const inner = Array.from(el.childNodes).map(walk).join("");
    switch (el.tagName) {
      case "B": case "STRONG": return inner.trim() ? `**${inner}**` : inner;
      case "I": case "EM": return inner.trim() ? `*${inner}*` : inner;
      case "CODE": return inner.trim() ? `\`${inner}\`` : inner;
      case "BR": return " ";
      case "DIV": case "P": return inner + " ";
      default: return inner; // u, spans, marks → plain text
    }
  };
  return walk(div).replace(/\u00a0/g, " ").replace(/[ \t]+/g, " ").trim();
}

export function serialize(blocks: Block[]): string {
  const parts: string[] = [];
  let i = 0;
  while (i < blocks.length) {
    const b = blocks[i];
    if (b.type === "li") {
      const items: string[] = [];
      while (i < blocks.length && blocks[i].type === "li") { items.push("- " + htmlToMd(blocks[i].html)); i++; }
      parts.push(items.join("\n"));
      continue;
    }
    if (b.type === "code") {
      parts.push("```" + (b.lang ?? "") + "\n" + blockText(b.html) + "\n```");
    } else {
      const text = htmlToMd(b.html);
      // Tight headings ("##1. Overview") keep their missing space on save.
      const hashes = { h1: "#", h2: "##", h3: "###", p: "" }[b.type];
      if (text) parts.push(hashes + (hashes && !b.tight ? " " : "") + text);
    }
    i++;
  }
  return parts.join("\n\n") + "\n";
}

/** Remove the finding-underline spans we inject at render time. */
export function stripMarks(html: string): string {
  if (!html.includes("fmark")) return html;
  const div = document.createElement("div");
  div.innerHTML = html;
  div.querySelectorAll("span.fmark").forEach((s) => {
    const parent = s.parentNode;
    if (!parent) return;
    while (s.firstChild) parent.insertBefore(s.firstChild, s);
    parent.removeChild(s);
  });
  return div.innerHTML;
}

export const cleanText = (s: string) => s.replace(/^[…\s]+/, "").replace(/[…\s]+$/, "").trim();

/** Does the heading text carry its own section number ("2. Rollout phases")? */
export const hasOwnNumbering = (s: string) => /^\s*\d+(\.\d+)*[.)]?(\s|$)/.test(s);

/** Wrap the first occurrence of each finding's text in an underline span. */
export function decorateBlock(html: string, findings: Finding[], done: Set<number>): string {
  let out = html;
  for (const f of findings) {
    if (done.has(f.id)) continue;
    const t = escapeHtml(cleanText(f.text));
    if (!t) continue;
    const ix = out.indexOf(t);
    if (ix === -1) continue;
    const red = f.kind === "fact" || f.severity === "error";
    out =
      out.slice(0, ix) +
      `<span class="fmark ${red ? "fmark--red" : "fmark--gold"}" data-fid="${f.id}">` +
      t + "</span>" + out.slice(ix + t.length);
    done.add(f.id);
  }
  return out;
}

/* word-level common prefix/suffix diff for the change queue rows */
export function diffChange(orig: string, repl: string) {
  const a = orig.split(" ");
  const b = repl.split(" ");
  let pre = 0;
  while (pre < a.length && pre < b.length && a[pre] === b[pre]) pre++;
  let suf = 0;
  while (suf < a.length - pre && suf < b.length - pre && a[a.length - 1 - suf] === b[b.length - 1 - suf]) suf++;
  return {
    pre: a.slice(0, pre).join(" "),
    delA: a.slice(pre, a.length - suf).join(" "),
    delB: b.slice(pre, b.length - suf).join(" "),
    suf: suf ? a.slice(a.length - suf).join(" ") : "",
  };
}
