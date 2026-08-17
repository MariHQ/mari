/* Snippet hygiene.
 *
 * `documents.snippet` is the first stretch of a document's body with its
 * whitespace collapsed to one line, and a body that came out of a repository
 * is Markdown with a machine preamble. What the store holds looks like:
 *
 *   # Remove the frozen landing commit c7206f597418 · disquee · 2026-07-28T18:33:24Z Remove the frozen landing …
 *   # Add a License section PR #4 · disquee · closed · updated 2026-07-28T16:20:28Z ## What does this change? …
 *   --- title: AI Document Monitoring Runbook owner: platform status: active --- # AI Document Monitoring Runbook Use this …
 *   <div align="center"> # 🌿 Mari **The product knowledge cloud …
 *
 * The result cards on Knowledge and the ⌘K rows showed that raw: a hash mark,
 * a duplicate of the title the card already carries, a SHA and an ISO
 * timestamp, and the actual sentence pushed off the end of the two lines the
 * card gives it. mari.guru/demo shows those same cards on the library's
 * fixtures, where the snippet is a sentence. That is the reference. This
 * turns the stored snippet into the sentence the card was designed for, at
 * the presentation layer, without touching what the server stores or what
 * search matches on. */

/** YAML front matter, flattened onto one line by the snippet builder. */
const FRONT_MATTER = /^\s*---\s+[\s\S]*?\s---\s+/;

/** A run of ` · `-separated machine fields that ends in an ISO timestamp:
    `commit <sha> · author · <iso>`, `PR #4 · author · closed · updated <iso>`. */
const META_RUN =
  /\b(?:commit\s+[0-9a-f]{7,40}|(?:PR|MR|issue|Issue)\s+#\d+)(?:\s*·\s*[^·]*?)*?\s*·\s*(?:updated\s+)?\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?\s*/g;

/** Markdown syntax that reads as noise in a two-line excerpt. */
function stripMarkdown(s: string): string {
  return s
    .replace(/<[^>]+>/g, " ")
    .replace(/```[a-z]*/g, " ")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/(\*\*|__)(.*?)\1/g, "$2")
    .replace(/(^|\s)(\*|_)(\S[^*_]*?)\2(?=\s|$|[.,;:!?])/g, "$1$3")
    // Heading marks anywhere: the snippet is one line, so `## What changed`
    // sits mid-sentence.
    .replace(/(^|\s)#{1,6}\s+/g, "$1")
    .replace(/(^|\s)>\s?/g, "$1")
    .replace(/(^|\s)(?:[-*+]|\d+[.)])\s+/g, "$1")
    .replace(/(^|\s)(?:-{3,}|\*{3,}|_{3,})(?=\s|$)/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}

/** Drop `title` from the front of `s`, as many times as it repeats there
    (a commit's subject line is echoed at the top of its body). */
function dropLeadingTitle(s: string, title: string): string {
  const t = title.trim().toLowerCase();
  if (!t) return s;
  let out = s;
  for (let i = 0; i < 3 && out.toLowerCase().startsWith(t); i++) {
    out = out.slice(t.length).replace(/^[\s:.\-–—·]+/, "");
  }
  return out;
}

/** The snippet a card should show: the body's first real sentence or two,
    without the title it duplicates or the machine line above it. Falls back
    to the cleaned original when stripping the preamble leaves nothing. */
export function cleanSnippet(raw: string | null | undefined, title?: string): string {
  if (!raw) return "";
  let s = raw.replace(/\r\n?|\n/g, " ");
  s = s.replace(FRONT_MATTER, "");
  s = stripMarkdown(s);
  if (title) {
    s = dropLeadingTitle(s, title);
    s = s.replace(META_RUN, "");
    s = dropLeadingTitle(s, title);
  } else {
    s = s.replace(META_RUN, "");
  }
  s = s.replace(/\s+/g, " ").trim();
  return s || stripMarkdown(raw);
}
