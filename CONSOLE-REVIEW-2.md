# Console review, round 2 — the layer under the pages

Round 1 (`CONSOLE-REVIEW.md`) audited the 25 console pages and was fixed in a
single wave. This round audits what that wave could not see: the primitives the
pages are built from, the subsystems no page imports, the Python backend, and
whether the eight isolated fix agents agreed with each other.

Four parallel audits fed this document:

| audit | scope | ids |
|---|---|---|
| primitives | `data-display/`, `forms/`, `actions/`, `feedback/`, `navigation/`, `layout/`, `tokens/` | `DD-`, `FRM-`, `ACT-`, `FDB-`, `NAV-`, `LAY-`, `TOK-` |
| chat / workflow / shell | `chat/`, `workflow/`, `shell/`, the Flows features, the orphans | `I-`, `A-`, `CH-`, `WF-`, `SH-`, `X-`, `PV-` |
| backend | `server/` | `MIG-`, `FACT-`, `SRCH-`, `STATS-`, `SQL-`, `AUTH-`, `ERR-`, `FLOW-`, `DEAD-` |
| consistency + accessibility | everything, cross-cutting | `XA-`, `ACC-`, `CPY-` |

**Round 1 was about honesty and polish. This round contains genuine security
defects.** Three of them are exploitable, and one is live on the public
deployment right now.

---

## Contents

- [Ship-stoppers](#ship-stoppers)
- [Security](#security)
- [Lies on shipping pages](#lies-on-shipping-pages)
- [Accessibility](#accessibility)
- [Where the round-1 waves disagreed](#where-the-round-1-waves-disagreed)
- [Primitives](#primitives)
- [Backend](#backend)
- [Dead and duplicated subsystems](#dead-and-duplicated-subsystems)
- [What the frontend needs and does not have](#what-the-frontend-needs-and-does-not-have)
- [Sequencing](#sequencing)

---

## Ship-stoppers

Nine findings that are either exploitable, destroy user work, or make a whole
page unusable. Everything else in this document can wait behind these.

| id | what | where |
|---|---|---|
| **AUTH-1** | `Cookie: mari_session=mari-bypass` grants workspace admin. Default-on. **Live on cloud.mari.guru.** | `server/auth.py:32,137` |
| **DD-68** | Stored XSS: `javascript:` URLs in synced markdown render as live links | `data-display/markdown.ts` |
| **AUTH-12** | Stored XSS into `/sites`, same origin as the session cookie | `server/sitebuilder.py:183,312,315` |
| **AUTH-11** | SSRF, reflecting, in six connectors — the correct guard exists twice in the repo and was not applied | `server/connectors_api.py:97` |
| **A1** | "Save step" clears the dirty flag without saving. Every pipeline edit is discarded, and Run then executes the previous version | `features/FlowsPipelineEditor.tsx:343` |
| **ACC-03** | The Doc Review formatting toolbar binds `onMouseDown` only — bold, italic, link, lists, undo are **keyboard-inoperable** | `features/DocReviewEditor.tsx:778+` |
| **ACC-02** | `forms/Field` is a `<div>`, not a `<label>`. **66 controls have no accessible name**, including sign-in and first-run | `forms/Field.tsx:4` |
| **I1** | Flows "Re-run" fabricates a run that never started and reports its number | `features/FlowsRunPanel.tsx:269` |
| **ERR-1** | `askMari` throws `IndexError` on every install — `ask_answers` has no writer anywhere | `server/queries.py:695` |

---

## Security

### AUTH-1 · The bypass is a guessable static cookie, on by default — **critical**
```python
BYPASS_TOKEN = "mari-bypass"                       # auth.py:32
if token == BYPASS_TOKEN and config.get("auth", "bypass_enabled", False):
    return conn.execute("SELECT * FROM users ORDER BY (role = 'admin') DESC, id ASC LIMIT 1")
```
Nobody needs to call `POST /auth/bypass`. `graphql_context` and `require_user`
both accept the literal, so the entire GraphQL surface, the REST connectors,
agent chat and onboarding are unauthenticated in the default configuration.
`config.py:21` sets `bypass_enabled: True`; `deploy/lambda/template.yaml` sets
`MARI_AUTH_BYPASS: "true"`.

The demo bypass is a documented, wanted feature (README:136). A guessable static
cookie that skips the endpoint is not. **Whether the public deployment should
carry the feature at all is a product decision, not a fix.**

### DD-68 · Stored XSS through synced markdown — **critical**
```ts
`<a href="${attr(href)}"${title ? ` title="${attr(title)}"` : ""}>${text}</a>`
```
`attr` escapes only `"`. `MarkdownView` injects the result with
`dangerouslySetInnerHTML`. The comment says "safe for trusted markdown" — but
this product's markdown is synced from Slack, GitHub, Confluence, Google Drive
and user uploads. `[x](javascript:alert(1))` in any synced document is a live
link in the console. Needs a scheme allowlist covering the usual evasions
(whitespace, `java\tscript:`, entities, case, `data:`).

### AUTH-12 · Stored XSS into `/sites` — **high**
`sitebuilder.py` sanitises bodies with `nh3` and escapes titles, then
interpolates three values raw:
```python
<body class="{body_class}" data-density="{density}">
```
`density` and `accent` come from `sites.theme`, written by
`updateSiteTheme(theme: JSON)` with **zero validation** and by `aiCustomizeSite`
straight from LLM output. `site['name']` is unescaped at `:312` and escaped five
lines later at `:317`. Builds mount at `/sites` on the same origin as `/graphql`.

### AUTH-11 · Reflecting SSRF in six connectors — **high**
`site_url` / `subdomain` flow into `urllib.request.urlopen` with no scheme
allowlist, no address check, and default redirect-following. `jira._get` embeds
the target's response body in an error that `connectors_api.validate` returns to
the caller — a port scanner. **The correct guard exists twice in this repo
already** (`brandimport.py:38-137`, `connectors/website.py:67-184`: scheme
allowlist, resolve-and-check-every-address, per-redirect revalidation,
`getpeername` rebinding check) and was simply not applied here.

### AUTH-4 · 7 of ~90 mutations are admin-gated — **high**
Ungated in the same file as the gated ones: `connect_source`,
`disconnect_source`, `revoke_api_key`, `connect_github_repo`, `sync_source`,
`resync_source`, `update_source_config`, `run_repo_audit`, `fix_audit_finding`,
`dismiss_audit_finding`, plus everything in `mutations_knowledge.py` and
`mutations_publish.py`. Sharpest edge:
```python
UPDATE sources SET config = config || %s::jsonb    # mutations_admin.py:226
```
Any signed-in user can overwrite another source's stored connector credentials.

### AUTH-5 · The audit log names one hardcoded person — **high**
`db.py:29` `ME = "Daniel Henneberger"` is the default `actor`. Only the seven
gated mutations pass a real caller. Worst case:
```python
rows[paused_at]["detail"] = f"approved by {ME}"    # mutations_publish.py:46
```
An approval gate records a name unrelated to who approved. `notifications` and
`watches` are keyed on `ME` too, so **all users share one inbox and one watch
list**.

### AUTH-2 · Open registration — **high**
`POST /auth/register`: no invite check, no setting, no gate on
`setup_complete`. Anyone reaching the port gets an account, and a session is all
GraphQL requires.

### AUTH-6 · API keys authenticate nothing — **medium, and a missing feature**
`create_api_key` returns `mari_sk_…` and stores only `token[:12] + "…"` — no
hash, no verifiable material. No code path reads `api_keys` for authentication.
`last_used` defaults to the string `'never'` and is never updated. The table,
the mutations, the Settings page and the documented scopes describe a credential
system that does not exist. This is a feature to build deliberately, not a fix.

### Also
`AUTH-3` sessions never expire server-side · `AUTH-8` no rate limiting on login,
register, magic-link or setup · `AUTH-10` the GitHub webhook skips signature
verification entirely when no secret is set (Slack's equivalent correctly 401s)
· `AUTH-13` the GitHub token rides in `git` argv and in `TimeoutExpired.cmd` ·
`AUTH-14` path traversal into the audit clone dir via a user-writable `repo`
slug · `AUTH-15` every settings row is readable by every user, masked by
allowlist so anything new leaks by default · `AUTH-16` connector credentials are
plaintext at rest · `SQL-2` unescaped `'` interpolated into a Google Drive `q=`
expression · `AUTH-9` magic links are consumable by a mail-client prefetch.

**`SQL-1`: no SQL injection anywhere in the Python.** Every f-string in SQL
interpolates module-level literals; all user values ride `%s`. That part is
clean.

---

## Lies on shipping pages

Round 1 removed a dozen fabrications from the pages. These are the same class,
one layer down, on surfaces users reach.

### A1 · "Save step" discards every pipeline edit — **critical**
```tsx
onSave={() => setDirty(false)}      // FlowsPipelineEditor.tsx:343
```
`ConfigPanel`'s Save button calls this, then shows "Step saved." The footer
flips to "Saved.", **"Save flow" goes disabled** (`disabled={!dirty}`), and
`run()` no longer saves first — so **Run executes the previous saved version
while the UI says the edit is saved**.

### I1 · Flows "Re-run" mints a run that never started — **critical**
```tsx
const nextNumber = Math.max(...runs.map((x) => x.number)) + 1;
const fresh: WorkflowRun = { ...r, id: `r${nextNumber}`, number: nextNumber,
  status: "running", started: new Date().toISOString(), headline: "Re-running…" };
setRuns((rs) => [fresh, ...rs]);
setNote(`Started run #${nextNumber}…`);
```
No handler exists. The row is permanently "running" in a table headed "Durable
and complete: every run", and vanishes on refetch. `X9` compounds it: the
component never resyncs from props, so the ghost survives every poll.

The stated reason for leaving `rerunRun` unwired — "a run row does not carry
which workflow it belongs to" — **is factually wrong**. `workflow_runs.workflow_id`
exists and `WorkflowRun.workflowId` is already in the schema. No backend work is
needed.

### I2 · Approving a gate fabricates the outcome of steps that never ran — **critical**
```tsx
row.status === "pending" ? { ...row, status: "passed", detail: "Deployed" } : row
```
Approval only unblocks steps; the engine has not run them. A user approves a
deploy gate and the panel reports the deploy succeeded.

### I3 · A document count that is a function of string length — **high**
```ts
// Deterministic pseudo-count from the query so the demo reads as live.
const n = 3 + (q.length % 22);
…"Currently matches {n} documents"
```
Under the trigger's *Scope query* and Fetch docs' *Search query*, in
biscay-blue, on the real Flows editor. A user tuning a query watches a number
respond to their typing. The most deceptive value in the library.

### A2 · "Save flow" on the Overview strip is indistinguishable from Cancel
Both call `setConfiguring(false)`. The panel above is a read-only `<dl>` — there
is nothing to save.

### ACT-08 · A failed copy reports success — **high**
```tsx
try { await navigator.clipboard?.writeText(value); }
catch { /* clipboard blocked — still flip UI so the action feels responsive */ }
setCopied(true);
```
Over plain HTTP or with permissions denied the button shows "Copied" having
copied nothing, and the user pastes a **stale API key**. The `?.` also means an
undefined clipboard never enters the catch.

### Also
`DD-01` `DigestCard` renders "API offline" from a boolean, diagnosing a failure
it cannot see · `DD-02` `ConnectorCard` health **defaults to "Healthy"**, so
unknown renders green · `DD-03` unknown severity silently becomes MINOR ·
`DD-04` unknown date preset reads "All time" · `FRM-16` a server storage policy
asserted as a fallback for a missing description · `NAV-01`/`I10` "Hybrid search
· BM25 + embeddings" rendered regardless of what the host's `onSearch` does ·
`I8` an enabled cron flow described as "Runs on every doc change" · `I7`
`ChatDockFeature` simulates an entire agent with timers, substitutes an offline
notice **into the assistant's own message content**, and hardcodes "Tools run
with your own permissions · gemma3 local" · `ACC-20` `SourcesSyncStatus` still
ships a self-advancing fake progress timer behind `animate` defaulting true.

---

## Accessibility

### ACC-01 · No page has an `<h1>`; the title is an `<h3>` — **critical, one-line fix**
```tsx
<h3 className="text-[22px] font-bold …">{title}</h3>   // layout/PageHeader.tsx:51
```
On all 22 framed pages: zero `h1`, zero `h2`, page title and card titles both at
`h3`, document outline starting at level 3. The stray `h2`s that exist are
*nested inside* the `h3` region, inverting the hierarchy (`AnswerCard.tsx:213`
is a 13px `h2`). `KnowledgeInspector` reaches `h5` with no `h4` above it.

### ACC-02 · 66 controls have no accessible name — **critical**
`forms/Field` is a `<div>` with a `<span>` label, no `htmlFor`, no `id`
threading. Its sibling `FormField` does it correctly. 67 controls sit inside
`Field`; 66 are `Input`/`Select`/`textarea` and are **unnamed to assistive
tech** — including Login (name, email, password, confirm, workspace), Setup
(admin token and five more), 18 controls in the pipeline editor, and every
dynamic credential field in both connect flows.

A fourth idiom — a bare `SectionLabel` beside a control with no wrapper — adds
more in the Library panels and `PublishSiteEditor`.

### ACC-03 · The Doc Review toolbar cannot be operated by keyboard — **critical**
```tsx
onMouseDown={(e) => { e.preventDefault(); runMark(tag); }}   // no onClick
```
Keyboard activation fires `click`, never `mousedown`. Bold, italic,
strikethrough, inline code, link, bullet list, numbered list, undo and redo are
Tab-reachable and **inoperable**. `aria-label` and `aria-pressed` are both
correct, which hides the failure from a static audit. Same in the Lineage
typeahead (`LineageToolbar.tsx:346`).

### ACC-07 · `ConfirmButton` arms silently — **high**
The label swaps to "Really delete?" with no live region. A screen-reader user
hears the original label, clicks twice, and the record is gone. This is the
console's **only** destructive-action pattern. The 4s auto-disarm is also an
unadjustable time limit (WCAG 2.2.1).

### ACC-10 / SH2 · The mobile nav is a modal with no modal behaviour — **high**
No `role="dialog"`, no `aria-modal`, **no focus trap** (Tab walks out behind the
scrim into a fully operable page), no focus move or restore, no scroll lock. On
every page, on mobile. `layout/Drawer.tsx` two files away is exemplary and does
all of it.

### ACC-08 · 19 tab bars point at panels that do not exist — **high**
`RTabs.Content` is rendered **nowhere in the library**, so every trigger emits
`aria-controls` for a nonexistent element and no `role="tabpanel"` is exposed.
Adjacent: five different ARIA idioms for "which one is selected".

### ACC-04 · Global search: no name, no live region — **high**
Placeholder-as-label in the chrome every page renders; no `role="combobox"`,
`aria-expanded`, `aria-controls` or `aria-activedescendant`, so arrow-key
movement is a colour change only; `role="listbox"` containing non-`option`
children; nothing announced when results arrive 180ms after typing stops.

### ACC-05 · Nothing that polls announces itself — **high**
`ScanRunCard` (polled every 1500ms from three pages), `OverviewLiveActivity`
(a feed named "Live activity"), `SyncPanel`, `WelcomeSyncPanel`, the debounced
Knowledge search — none has a live region.

### ACC-19 / L9 · The lineage canvas is still mouse-only — **high**
Round 1 asked for keyboard access; what landed is an `sr-only focus-within:`
readout. There is still no focus order over nodes, no arrow traversal, no roving
tabindex. On a page whose entire content is the canvas, that is the whole page.

### SH3 · Collapsed sidebar items have no accessible name — **high**
The icon is `aria-hidden` and the label is `{!collapsed && …}`, so a railed
sidebar is a column of 13 unnamed buttons. The Radix `Tooltip` contributes
`aria-describedby` only while open; it does not name the trigger.

### Long tail
`ACC-06` every progress bar is an unnamed `progressbar` · `ACC-09`
`aria-invalid` appears on 3 controls in the whole console, and the
password-mismatch message is rendered three different ways on three pages, one
of them in neutral grey · `ACC-12` icon-only buttons with state and no name ·
`ACC-13` colour as the only signal, incl. `Pill.verified` **blue** where §4
mandates green · `ACC-14` ~25 sites below the §6 contrast floors, including the
disabled palette §6 itself points at (`ACT-01`, `text-ink/55`) ·
`ACC-15` two `disabled:opacity-*` ghosts · `ACC-16` five hand-rolled chips ·
`ACC-17` three bare `overflow-*-auto` regions outside `Scrollable` · `ACC-18`
interactive `<mark>`, `<div role=option>`, `<tr onClick>`, hover-only controls ·
`SH5` no skip link and an unlabelled `<main>` · `DD-22` clickable table rows are
`<tr onClick>` with no role, tabIndex or key handler — the primary navigation
affordance of every list in the console is mouse-only · `DD-46` `role="tree"`
with no `treeitem` children · `FRM-05` the searchable combobox §7 *mandates* for
every assignee picker is keyboard-inoperable.

---

## Where the round-1 waves disagreed

Eight agents fixed 25 pages in isolation. They could not see each other's work,
and it shows. These rot fastest because each new page picks whichever idiom it
happens to copy.

### XA-01 · A read error is surfaced eight different ways — **critical**
17 pages hardcode the string `"API offline"` (which §8 forbids and which
duplicates `ERRORS["server.unavailable"].title`), split between icon and no-icon
`EmptyState`. Three use the §8-correct `<ErrorMessage id="server.unavailable" />`.
One passes the server's text **as the Alert title**. One renders a bare `<div>`.
One writes a bespoke inline string. And an `EmptyState` — a "nothing here yet"
surface — is being used to report a failure.

### XA-02 · Two rival surfaces for a failed write — **critical**
`WriteError` (a banner, documented as *the* failure surface for a page action)
in 15 files; `FieldError` (12px inline text, documented as *"sits directly under
an input"*) used for page-action failures in 24 files. Same event, two
completely different renderings.

### XA-03 · `useWrite` was extracted, then half the console ignored it
15 files use it. **31 hand-rolled `busy` states across 16 files do not.**

### XA-04 · One 1-line helper, written out 10 times, exported never
```ts
const why = (e: unknown, fallback: string) => (e instanceof Error && e.message ? e.message : fallback);
```
Byte-identical in ten files, inlined at ~25 more sites.

### XA-25 · Status vocabulary disagrees across three chip tables — **high**
`Chip.tsx` declares itself "the single source of truth for every status pill";
`TagChip.tsx` and `Pill.tsx` keep two more. `Pill.verified` is **blue** where §4
says green. `Stale` is red in the freshness chart and clay everywhere else.
`running` is green-pulsing in one place and clay in another. Failure has five
spellings. **And P-FA-1 has already regressed**: `DocReviewFindingsPanel` still
does `claims.filter(f => f.status === "Verified")`.

### X2 · The same finished run is "Approved" in Flows and "Succeeded" in Facts
Four separate status maps (`CHIP_OF`, `RUN_CHIP`, `SCAN_CHIP`,
`OUTCOME_STATUS`) for one six-value vocabulary, in the two places a user
compares.

### XA-07 · `ResultCount` exists, says it is the standard, and ~25 surfaces hand-roll a different sentence
Sub-findings inside that set: three report a **constant** rather than the
rendered count; three place the strip **below** the list, violating §13; one
says "Showing 3 of 3"; five silently cap with no expand control; thousands
separators and typography vary throughout.

### XA-16 · Cards clamp themselves inside full-width columns — **medium-high**
Four ceilings for one idea (640/680/720/860). `DecisionCardFeature` is the
entire Decisions main column, so on a 1400px frame the ledger stops at 720px and
leaves ~250px dead **inside** the main column, beside a rail. §11 names this the
single most visible inconsistency in the console, and §11's own rule is
"constrain the column, not the card".

### Long tail
`XA-05` the scan poll loop written three ways, one of which tears down and
recreates its interval every tick · `XA-06` the same run card in a different
place on each of its three pages (§16) · `XA-08` eight phrasings of one "show
the rest" toggle · `XA-11` two spellings of "time zone" on two pages a user
flips between · `XA-12` §12 read in **opposite directions** by two waves, with
`[overflow-wrap:anywhere]` added to the shared button classes so every button
breaks its label mid-word · `XA-13` `SPLIT[420]`/`SPLIT[460]` exported and used
by nobody while Lineage hand-rolls them without the collapse prefix · `XA-14`
rail widths of 340/380/400/190/220 that exist in no convention · `XA-17` `mt-5`
in 15 places · `XA-18` three card-gallery idioms, and the non-compliant one is
the most used · `XA-19` six `Tabs` call sites render the wrong bar, four by
omission (the default is `"seg"`, the reference is `"underline"`) · `XA-21` the
resync idiom landed cleanly, with two stragglers and no extracted hook ·
`XA-22` two pages both cite §13 for search placement and reach opposite answers
· `XA-23` "Create review task", "Open document", "Export" and "Refresh" each
appear in 3-6 places with different positions, icons, heights and labels (§16) ·
`XA-26` `Toaster` is never mounted, so `useToast` is a silent no-op · `XA-28`
the mono eyebrow label hand-rolled ~40 ways while `SectionLabel` exists ·
`XA-29` `App.tsx` discards the search scope it draws a filter for.

---

## Primitives

Highest-priority set: `DD-01`, `DD-02`, `DD-15`, `DD-22`, `DD-24`, `DD-31`,
`DD-33`, `DD-39`, `DD-64`, `DD-68`, `FRM-03`, `FRM-05`, `FRM-09`, `FRM-15`,
`FRM-16`, `FRM-20`, `ACT-05`, `ACT-08`, `NAV-01`, `NAV-02`, `LAY-01`, `LAY-04`,
`LAY-05`, `LAY-07`, `LAY-12`.

### Correctness
- **`DD-15`** — `useSort`'s default accessor is the *rendered cell text*, and §5
  renders dates as `Jul 16, 2026`. **Every table that does not pass an explicit
  `sort` orders its date column alphabetically**: Apr, Aug, Dec, Feb…
- **`DD-64`** — `DateRangePicker`'s draft resets mid-edit because `value` is in
  the effect deps and is an object literal at nearly every call site.
- **`NAV-02`** — `GlobalSearch` clears the scope filter on every keystroke, for
  the same reason.
- **`LAY-07`** — `Drawer`'s focus effect has `onClose` in its deps and every
  documented call site passes an inline arrow, so **typing in a drawer input
  loses focus on every keystroke that re-renders the parent**.
- **`DD-33`** — `Truncate` forces `display:block`, so its own documented
  canonical example (`<Truncate as="td">`) drops the cell out of table layout.
  The documented contract does not work.
- **`DD-13`** — the Scrubber's last event date can never be selected.
- **`DD-21`** — `SelectableTable`'s selection never resyncs from `rows`, so a
  parent keeps holding rows that no longer exist.
- **`WF3`/`WF4`** — both run tables sort only the visible page, and sort
  duration as a string (`"9s"` after `"12m"`).

### Structure and convention
- **`LAY-01`/`LAY-02`/`LAY-03`** — `Card` does not compose `CardShell`, so §1's
  twelve-slot order is enforced nowhere, `CardBody` renders children in whatever
  order they were written, and the whole `CardShell` API is undocumented.
- **`LAY-12`** — `layout/Page.tsx` encodes none of §11: no `max-w-[1400px]`, no
  `mt-6`, no `gap-5`, no rail recipe. Those live only in `pages/PageFrame.tsx`.
- **`LAY-04`/`FRM-20`/`ACC-11`** — `Dialog` and both connect flows put the
  primary action bottom **right**, and `Dialog.md` teaches it.
- **`DD-39`** — `SkeletonPage` is built from banned breakpoint stacking at
  `max-w-6xl`, a width §11 explicitly retired. Every page loads at one width and
  settles at another.
- **`DD-24`/`DD-52`/`DD-56`/`NAV-16`** — the status dot is a circle everywhere,
  which §6 says reads as "choose me". `DecisionCard`'s own header comment says
  it was changed to a square; the code still draws a circle.
- **`DD-31`** — `Badge.md` sanctions a §4 violation in writing
  ("uppercase-agnostic") for a component that is `Chip` with different padding.
- **`ACT-05`** — `ConfirmButton` arming is announced to nobody (see ACC-07).
- **`ACT-02`** — two button systems (`buttons.ts` and `Button.tsx`), already
  drifted on three border values.

### Duplication with an intended survivor
| keep | delete or fold in |
|---|---|
| `ConnectorWizard` | `ConnectDrawer` (`FRM-22`) |
| `GlobalSearch` | `CommandPalette`; `SearchField` → trigger-only button (`NAV-15`) |
| `PageHeader` | `layout/Page`'s duplicate header block (`LAY-15`) |
| `EmptyState` | three hand-rolled copies in the table components (`DD-60`) |
| `Chip` | `CountChip`, `TableToolbar`'s filter tokens, `ScopeChip`, 5 more (`ACC-16`) |
| `SectionLabel` | ~40 hand-rolled eyebrows (`XA-28`) |
| `ResultCount` | ~25 hand-rolled count strips (`XA-07`) |

### tokens/
`TOK-01` the `format.ts` header documents an output that omits the year, which
§5 forbids · `TOK-02` `shared-tokens.md` documents a same-year-omits-year rule
that does not exist and that `format.ts` explicitly rejects · `TOK-04` `fmtAgo`
clamps the future to "just now" · `TOK-05` it never ticks, and two `.md` files
tell callers to precompute it, freezing it harder · **`TOK-06` every formatter
uses browser-local time and none accepts a zone, so the timezone setting the
console asks users to configure changes nothing** · `TOK-11`/`TOK-12` Brisbane
and Sydney share a label though they differ by an hour half the year, and Mexico
City is labelled "Central Time" though it abolished DST in 2022 · `TOK-13`
`LEGACY` invents two ids its own comment says never shipped · `TOK-17` `docHref`
exists three times with two id types.

---

## Backend

### The changes that just landed
- **`FACT-1`** — `scan_facts` now makes up to **8 sequential LLM calls** at
  `timeout=120` each, on a request thread, while the claim ceiling stays at 4.
  Up to 16 minutes for one GraphQL mutation.
- **`FACT-2`** — claims are dropped silently when the budget runs out
  mid-document, and the two newest documents can consume the whole budget every
  scan, so older ones are never mined.
- **`FACT-3`** — both scanners crash on a non-dict model element; two sibling
  scanners in the same file guard correctly.
- **`FACT-4`** — the seeded flow's `fetch_docs` step is decorative: the scan
  re-runs its own query and never reads `ctx["doc_ids"]`, so changing `k` in the
  editor does nothing.
- **`MIG-1`** — `ADD COLUMN IF NOT EXISTS … REFERENCES` skips the FK when the
  column exists, so index and constraint can diverge permanently.
- **`MIG-2`** — `ON DELETE SET NULL` means a resync that deletes a document
  turns recorded provenance into hand-written-looking provenance, which is
  exactly what the column was added to prevent.
- **`SRCH-1`** — `search` and `searchTotal` each compute their own embedding, so
  the shared CTE is shared in source only: if one `llm.embed` times out, the
  count describes a keyword match set while the rows describe a hybrid one.
- **`SRCH-2`** — no stable tiebreaker, so paging can duplicate and skip rows.
- **`SRCH-4`** — every "show more" re-issues `search` and logs another
  `usage_log` row, so `insightStats.searches` now **inflates with pagination**.
- **`STATS-1`** — `findings.created_at`/`changes.created_at` added with
  `DEFAULT now()` fills every pre-existing row with one identical timestamp, so
  a "last 24 hours" window on a freshly-migrated database reports every finding
  ever recorded as drift caught yesterday.
- **`STATS-2`** — `driftCaught` filters on `kind IN ('fact','freshness')` and
  nothing ever writes `'freshness'`.

### Performance
`SRCH-5` the scoring CTE scans the whole corpus twice with **no ANN index on
either embedding column** · `STATS-4` `events` has no index beyond its primary
key, so every access-log page sorts the whole table and the `ILIKE` search is
three full scans · `STATS-5` `relatedDocuments` is unbounded, undeduplicated,
and its inbound half is a full scan for want of an `edges(to_doc)` index ·
`SQL-4` N+1 and unbounded scans in nine more places · `FLOW-8` `buildSite` runs
`npm install` (900s) then `npm run build` (300s) **synchronously in a GraphQL
mutation**, and `/glossary-harvest` makes up to 13 sequential 120s LLM calls.

### Correctness
`ERR-1` `askMari` throws on every install · `ERR-2` a site is marked live
whether or not the S3 upload succeeded, and the failure reason is reduced to an
exception class name · `ERR-4` seven multi-statement sequences with no
transaction, each able to half-apply · `ERR-5` a bare `except` around the insert
that persists an agent answer, while the stream still emits `done` · `FLOW-1` a
restart marks legitimately-*waiting* approval runs as failed · `FLOW-2` a run
whose process dies stays `running` until someone restarts · `FLOW-4` unbounded
threads, each opening its own connection outside the pool.

### Dead
19 findings. The largest: **`DEAD-1`** `llm.py` hardcodes the models and reads
no settings, so `settings.llm` and `settings.embedding` are written, masked,
displayed and never read — **Settings → Models changes nothing**. **`DEAD-2`**
`MARI_OLLAMA_HOST` is set in compose and read by nothing, and the hardcoded
`localhost:11434` inside a container *is* the container, so **the compose
deployment can never reach ollama**. **`DEAD-6`** the entire style-guide/rule
registry — 4 packs, 14 rules, 5 mutations, 3 resolvers — has no checker writing
findings against any of it. Plus `DEAD-11` a 491-line agent endpoint with no
caller, `DEAD-9` a 150-line glossary harvester with no caller, and `DEAD-14` a
qualifier map missing the keys that would stop gdrive/dropbox/airtable
colliding on the `sources.provider` unique constraint.

---

## Dead and duplicated subsystems

**`WF1`** — `workflow/*` (PipelineView, RunHistory, RunPanel, WorkflowScreen)
is exported and imported by **zero pages and zero app code**, only `.preview/`.
~600 lines maintained in parallel with the `features/Flows*` twins that ship and
that carry `I1`, `I2`, `A1` and `I3`. `X1` documents the drift between them,
including a §6 spinner fix that landed in the dead copy and not the live one.

**`CH5`** — `chat/*` is likewise unreachable. Its empty state promises *"I can
operate the console for you: search, edit and tag docs, sync sources, run flows,
and steer this screen"* — there is no agent endpoint, no tool registry and no
permission model behind it, and the only implementation of those promises is the
`setTimeout` mock in `I7`. Before any chat backend is built the dock needs a
failure model (`CH1`: no error state, and `Composer` clears the textarea *before*
the send can fail, losing the message), a real tool-result field (`CH2`: the
expanded view labels the human summary as the machine `result`), and copy that
matches what the product will do.

**`OR1` — delete `features/PublishSiteEditor.tsx`.** No action props at all, so
every mutation it draws is necessarily fake: `askMari()` returns the same purple
accent for any instruction in any language, `deploy()` and rollback are
`setTimeout` theatre, "Save deploy config" writes nothing, and it seeds
`acme-docs-prod` / `us-east-1` / `docs.acme.com` as if read from the site. It is
**publicly exported**, so an app can ship a Deploy button that deploys nothing.
`PublishPage` already implements the same editor against real handlers. Keep
`MockPreview` if useful; drop the rest with its `.preview` entries.

**`OR2` — `features/AnswersHarvestWizard.tsx`: the fabrication finding is
stale**, but the duplication is not. Its `SourceId` is
`"slack" | "docs" | "chat"` while the page's is `"slack" | "docs" | "history"`,
with the adapter mapping between them. Either delete it, or give it a `sources`
prop and have `AnswersPage` compose *it* instead of an inline twin.

**`PV2`** — the canvas renders every page with **no `actions`**, which is the
exact path where `A1`, `A2` and `I1` look correct. The harness cannot catch this
class of bug by construction; honesty findings need a fixture with stub actions
that reject.

---

## What the frontend needs and does not have

| need | what it requires |
|---|---|
| `deleteSite` | mutation + cascade `releases` (the FK has no `ON DELETE`) + `rmtree` the build dir |
| `editFact` / `retireFact` | `facts.claim` is UNIQUE, so an edit needs conflict handling; add a `retired` status |
| Task doc link + priority | `ALTER TABLE tasks ADD document_id, priority`; `due_date` and `setTaskDue` already exist and the composer simply does not call them |
| Freshness-band drill-through | `freshnessDocuments(sourceId, band)` — the `bucketed` CTE already computes the classification and discards the ids |
| Password reset | a `password_resets` table; the magic-link flow is the exact template |
| 2FA | no second factor exists at all: needs `users.totp_secret`, enrol/verify/challenge, and a partial-session state |
| Setup-token pre-check | split the existing hash comparison out without deleting the row |
| Account language / deletion | `users.name` is the join key for notifications, watches, `events.actor` and glossary owners, none FKs — deletion needs an anonymise policy first |
| API key expiry | moot until keys authenticate anything (`AUTH-6`) |
| Editable prose rules | the registry already exists and is writable; **the missing piece is the checker** that evaluates a rule and writes a `findings` row |
| Insights comparison period | `since`/`until` already exist; blocked in practice by `STATS-1` |
| Bulk task creation | one transaction, vs N autocommitted statements and N misleading audit rows |
| Flow re-run | **needs nothing** — `workflow_runs.workflow_id` and `WorkflowRun.workflowId` both already exist |

---

## Sequencing

1. **Security.** `AUTH-1`, `DD-68`, `AUTH-12`, `AUTH-11`, then `AUTH-4`/`AUTH-5`,
   `AUTH-2`, `AUTH-10`.
2. **Destroys user work.** `A1` (pipeline edits discarded), `LAY-07` (drawer
   focus), `DD-64`/`NAV-02` (input reset), `CH1` (chat drops the message on a
   failed send).
3. **Lies on shipping pages.** `I1`, `I2`, `I3`, `A2`, `ACT-08`, `DD-01`,
   `DD-02`.
4. **Blocks a class of user.** `ACC-01` (one line), `ACC-02`, `ACC-03`,
   `ACC-07`, `ACC-10`, `ACC-19`, `SH3`, `DD-22`, `FRM-05`.
5. **Always-wrong.** `ERR-1`, `DEAD-1`, `DEAD-2`, `DD-15` (every date column
   mis-sorts), `TOK-06` (the timezone setting does nothing).
6. **Settle one idiom each**, collapsing ~100 divergent sites: `XA-01`
   (read error), `XA-02` (write failure), `XA-25`/`X2` (status vocabulary),
   `XA-07` (count strips), `XA-16` (card widths).
7. **Delete.** `OR1`, `WF1`, `DEAD-9`, `DEAD-11`, and the duplication table's
   right-hand column.
8. **Performance.** The missing indexes (`SRCH-5`, `STATS-4`, `STATS-5`), then
   `FACT-1` and `FLOW-8` off the request thread.
9. Everything else by severity.
