# Console review — every page

A page-by-page audit of the Mari console: the 25 page modules in
`vendor/mari-design/components/pages`, the features behind them, the shared
`PageFrame`, and the query-to-props adapters in `web/src/data`.

Read against `vendor/mari-design/components/CONVENTIONS.md` (section numbers
below refer to it) and against the rule in `.claude/skills/design-system-first`:
a page never invents a value, and a control that cannot do anything must not be
drawn.

Every finding carries a stable id so work can be assigned against it.

**Where fixes land.** Visual and behavioural findings are changes to
`vendor/mari-design` (its own repo; this checkout is a submodule, so it is
committed there and the pointer bumped here). Findings marked **[web]** are
changes to `web/src/data/*.ts` or `web/src/App.tsx` in this repo.

---

## Contents

- [Cross-cutting (C)](#cross-cutting-c)
- [Markdown editing (M)](#markdown-editing-m)
- [Lineage (L)](#lineage-l)
- [Per page (P)](#per-page-p)
- [Suggested sequencing](#suggested-sequencing)

---

## Cross-cutting (C)

### C1. Local state seeded from `data` never resyncs
`useState(data.x)` appears on nearly every page: Tasks (`tasks`), Answers
(`filter`), Decisions (`filter`), Facts (`tab`), Library (`tab`), Publish
(`tab`), Sources (`tab`), `KnowledgeBrowser` (its whole facet set),
`DocReviewEditor` (`blocks`). After any refetch the page keeps rendering the
first response.

Only `PublishPage.SiteList` does the right thing
(`useEffect(() => setRows(sites), [sites])`). `WelcomePage` and `SetupPage` use
a `seen`-sentinel resync during render. Adopt the sentinel idiom everywhere:

```tsx
const [current, setCurrent] = useState(data.x);
const [seen, setSeen] = useState(data.x);
if (seen !== data.x) { setSeen(data.x); setCurrent(data.x); }
```

### C2. Server-side counts over client-side rows
Facts (`data.filters[].count` vs `data.facts.filter(...)`), Answers (counts
derived from the loaded page only), Decisions. A tab can promise 240 rows and
render 40. Either count what is on screen, or paginate honestly and say the
count is workspace-wide.

### C3. §3 `SortHeader` not honoured on hand-rolled tables
`SettingsAuditLogPage.AuditInline` uses bare `<th>`; `PublishPage.SiteList` uses
`SortHeader … sortable={false}`; `FactsPage.FactsTable` and
`FactsPage.ReviewTaskAudit` pass plain strings to `Table head={[…]}`. No column
in the console sorts except inside `KnowledgeBrowser`.

### C4. §12 "truncate, don't pack" violated in the densest tables
`FactsPage.FactsTable` uses `break-words` on claim, source and owner;
`TasksPage.TaskRow` uses `[overflow-wrap:anywhere]` on the title;
`TasksPage.StressStrip` likewise. These are exactly the arbitrarily-long user
values the rule names. Use `<Truncate>` / `<Truncate lines={2}>`.

### C5. Notifications are structurally dead **[web]**
`PageFrame` renders `NotificationBell items={chrome?.notifications ?? []}` on
every page, and `web/src/App.tsx` never populates `chrome.notifications` (nor
`recentSearches`). The bell is a permanent zero on all 25 pages. Either wire a
source or hide the bell when there is none, the way the search trigger already
hides itself without `onSearch`.

### C6. Pages unreachable from the nav
`NAV` in `PageFrame.tsx` lists ten workspace items plus Settings. **Tasks**
(reachable only via the Overview back-link), **Repository Audit**
(`navFor("audit") → "facts"`, but nothing links to `/audit`), **Doc Review** and
**Preferences** have no sidebar entry point.

### C7. Duplicate `useGlobalSearch` instance
`PageFrame` calls the hook at the top *and* again inside `MobileFrame` /
`DesktopStatic`. On mobile and in static render two ⌘K listeners are bound and an
unrendered overlay's state toggles alongside the visible one.

### C8. Two incompatible timezone vocabularies
`SettingsGeneralPage` uses `{utc, pt, et}` with IANA labels; `PreferencesPage`
uses IANA values with prose labels. Neither offers a full list, and the two can
disagree about the same account. Pick one canonical list (a `tokens/timezones.ts`
alongside `tokens/regions.ts`) and use it in both.

### C9. §5 no em/en dashes in user-visible copy
`PreferencesPage.TIMEZONES` ("Pacific — Los Angeles", ×4),
`SourcesPage` ("Selected files — {n}"), `LineageToolbar` path hint
("From "X" — click the other end").

### C10. Dead imports
`SettingsApiKeysPage` (`Input`, `Field`, `Scrollable`, `Chip`, `Alert`,
`TokenReveal`, `fmtDate`, `FORM_GRID`), `SettingsMembersPage` (`Input`,
`Select`, `Field`, `Chip`, `Scrollable`, `ChevronDown`, `Check`, `Mail`,
`FORM_GRID`), `FactsPage` (`Progress`, `ChipStatus`). Left over from the
inline-twin deletions; they obscure what each page actually uses.

### C11. No 404 **[web]**
`App.tsx` sends every unknown path to `/` with no message.

---

## Markdown editing (M)

The console has two markdown editors. `DocReviewEditor` (contentEditable blocks
over `data-display/markdown.ts`) is the one Doc Review uses and the one that
saves. `data-display/MarkdownEditor.tsx` (source + live preview) is used
nowhere.

### M1. Saving a real document destroys most of its markdown — **highest severity**
`data-display/markdown.ts` supports only `h1`–`h3`, `p`, `li`, fenced `code`,
and inline `**bold**` / `*italic*` / `` `code` ``. `DocReviewPage` saves
`serializeBlocks(blocks)` back to the server. Everything else round-trips to
nothing:

| Input | After a save |
|---|---|
| `[text](url)` | plain text, URL gone |
| images, tables, blockquotes, footnotes, task lists | paragraphs, or dropped |
| ordered lists (`1.`) | paragraphs, numbering gone |
| `h4`–`h6` | not matched by `HEADING` (`{1,3}`); literal `####` in the body |
| `---`, `***`, `___`, YAML front matter | silently dropped |
| nested list indentation | flattened |
| hard line breaks, indentation in prose | collapsed by `.replace(/\s+/g, " ")` |

Silent, irreversible data loss on Save. Nothing else in this document is close.

Options, in order of preference:
1. Extend the parser/serializer to a lossless subset (links, ordered lists,
   blockquotes, tables, `h4`–`h6`, rules, front matter passthrough) **and** keep
   unknown constructs as opaque blocks that serialize back verbatim.
2. Failing that, refuse to save a body whose re-parse does not round-trip, and
   say so.

### M2. Underline has no markdown representation
The toolbar offers `U` (`execCommand("underline")`); `blockToMarkdown` strips
`<u>` via the catch-all `.replace(/<[^>]*>/g, "")`. The user formats, saves, and
the formatting vanishes with no warning. Either drop the control or map it to a
real construct.

### M3. `document.execCommand` is deprecated
Its output varies by browser (`<b>` vs `<span style>`); a `<span>` is stripped
on serialize, so bold can survive in one browser and not another.

### M4. Missing toolbar essentials
No link, inline code, blockquote, ordered list, heading beyond H3, image, or
undo/redo. `⌘B` / `⌘I` / `⌘S` are unbound. Undo is effectively broken: block
splits and merges go through React state, so the browser's native undo stack
cannot cross a block boundary.

### M5. `spellCheck={false}` on the prose editor
On the editing surface of an editorial-quality product.

### M6. `justify` and `airy` masquerade as formatting
Local view toggles sitting in the same button group as Bold/Italic, never
persisted. Move them out of the formatting group or persist them.

### M7. Hardcoded app route inside a library component
`window.open("/knowledge", "_blank")` on the toolbar's `ExternalLink` button —
the exact coupling `WelcomeActions.navigate` exists to eliminate. Emit an intent.

### M8. The editor never re-parses `body`
`useState(() => parseMarkdown(body))` runs once. Restoring a revision, or any
refetch, leaves the old text on screen while `save` writes the stale body back.

### M9. Silent cap on annotations
`annots` does `.slice(0, 6)` with no "+N more". Findings 7+ do not exist in the
margin.

### M10. Finding decoration is first-literal-match only
`decorateBlock` does `out.indexOf(t)` against HTML. A finding whose quoted text
repeats, or spans an inline `<b>`, is silently not underlined and its margin
chip gets a synthetic position.

### M11. Two editors, and the better one is unused
`MarkdownEditor` is controlled, has a live preview, and does not lose data
because it never re-serializes. Either adopt it as a source mode in Doc Review
(with the block editor as the rich view) or delete it.

---

## Lineage (L)

### L1. The time scrubber does nothing
`LineageTimeScrubber` holds `idx` in local state and exposes no `onChange`;
`LineageGraph` never reads an `asOf` value. The caption under the control states
"Nodes created after this date are hidden; edits after it show dashed" — a
factual claim about behaviour that does not exist. Wire `asOf` into the shared
control store and filter/dash in the graph, or remove the control and its
caption.

### L2. Drawers cannot be closed
`LineagePage.Drawer` destructures `onClose` and never uses it, and none of the
four drawer components receive a close handler. Once you click a node the rail
is stuck until you pick another.

### L3. Two competing search UIs, one of them wrong
`LineageToolbar` has a working typeahead over `controls.query`. `LineagePage`
*also* renders its own `SearchResults` dropdown from `data.search`, absolutely
positioned at `left-2 top-[52px]` — on top of the toolbar's own. Its filter is
incorrect:

```ts
n.title.toLowerCase().includes(q) ||
  (n.tags ?? []).some((t) => t.includes("customer")) ||
  n.docKind === "decision"
```

It returns nodes that do not match the query. Delete the page-level dropdown.

### L4. Two sources of truth for lens/layout
`LineagePage` passes `data.lens` / `data.layout` as props; `LineageGraph` pushes
them into the shared store on mount. The `key={`${data.lens}-${data.layout}-…`}`
on `LineageGraph` forces a remount, discarding pan, drag positions and `moved`
state, and resetting the store's lens to the data's — so a user's "Color by"
choice can be silently reverted.

### L5. The adapter pins every view field to null **[web]**
`web/src/data/lineage.ts` hardcodes
`lens:"source", layout:"flow", focalId:null, trace:null, asOf:null, drawer:null, action:null`.
Impact/provenance tracing, focal closure and deep-linked drawers are therefore
unreachable in the real console — they exist only as canvas states.

### L6. `deriveLinks` fabricates a result when unwired
`LineageToolbar.derive` with no handler sleeps 1600ms then does
`setDerived(d => d + 3)` and reports "3 links proposed" that were never
proposed. Invented data in the library.

### L7. `saveView` writes but nothing reads
`onSaveView({ name, state: JSON.stringify(controls) })` has no counterpart: the
Views menu lists four hardcoded presets and never lists a saved view.

### L8. Node drawer history is dropped on selection
`openNode` sets `history: []` for any node other than the one `data.drawer`
happened to name, so clicking a node opens a drawer whose revision timeline is
permanently empty.

### L9. The graph has no keyboard or assistive path
Nodes are selectable by mouse only; there is no focus order, no arrow-key
traversal, and no text alternative. On a page whose entire content is a canvas,
that is the whole page being inaccessible.

---

## Per page (P)

### Overview — `P-OV`
- **P-OV-1** `Greeting` hardcodes "Good morning" regardless of time of day or the
  user's timezone (which Preferences collects).
- **P-OV-2** `activityPollMs` defaults to `0` — the "Live activity" widget is not
  live unless the adapter opts in.
- **P-OV-3** No date-range or "what changed since I was last here" control; the
  digest and stats carry no period label on the page.
- **P-OV-4** `isEmpty` requires all six collections empty, so a workspace with one
  stale source renders a dashboard of zeros instead of the onboarding state.

### Knowledge — `P-KN`
- **P-KN-1 [web]** **Search never reaches the server.** `useKnowledge(query = "")`
  is called by `adapterFor` with no arguments, so `q` is always `""`.
  `KnowledgeBrowser` owns the search input in local state and exposes no
  `onQuery`. Typing filters the 40 already-loaded rows; the hybrid-search
  backend is unreachable from the UI. Needs both a library prop
  (`query` / `onQueryChange`) and adapter threading through the URL.
- **P-KN-2 [web]** Hard `k: 40` with no pagination and no total — "Showing all 40
  results" is a claim about the corpus that is not true.
- **P-KN-3 [web]** Inspector `facts: []` and `related: []` are hardcoded, so two
  named rail sections are permanently empty on every document.
- **P-KN-4** Facets (source/type/owner/status/freshness) are client-side over
  those 40 rows, so a facet can read "0" for a source with thousands of docs.
- **P-KN-5 [web]** Selection lives in `?doc=`, but the query and facets do not — a
  shared Knowledge URL loses everything except the selected row.

### Doc Review — `P-DR`
- **P-DR-1** `HeaderActions` renders `<TagChip tag="canonical" />` and
  `<TagChip tag="verified" />` **unconditionally** on every document, regardless
  of its tags. Invented data at the top of the editing surface.
- **P-DR-2** `watched` initialises to `false` always; a document you already watch
  shows "Watch".
- **P-DR-3 [web]** `data.save` is hardcoded `"saved"` by the adapter and no `save`
  mutation is wired, so the Save button is permanently disabled in the app.
- **P-DR-4 [web]** `claims: []` hardcoded → the Fact check tab's claims table is
  always empty.
- **P-DR-5** `initialTab` is a `const "changes"`; the bottom tab strip cannot be
  deep-linked.
- **P-DR-6 [web]** The five `pane` deep-link views exist in the type and are never
  produced (`pane: "workspace"` always).
- Plus everything in [Markdown editing](#markdown-editing-m).

### Answers — `P-AN`
- **P-AN-1** The answer body is a plain `<Textarea>`. For text served verbatim
  there is no markdown, no preview, no length guidance.
- **P-AN-2** No way to edit an existing answer from this page; only create.
- **P-AN-3** Coverage rail truncates to `questions.slice(0, 2)` with no count and
  no "see all" (the `coverage` pane exists; nothing navigates to it).
- **P-AN-4** `HARVEST_SOURCES` is a hardcoded three-item list in the library; a
  workspace with Notion/Jira/Zendesk connected cannot harvest from them.
- **P-AN-5** Harvest wizard uses `key={r.question}` — two candidates with the same
  question collide and one disappears.
- **P-AN-6** Tab counts computed over the loaded page (see C2).

### Decisions — `P-DE`
- **P-DE-1** `Composer` hand-rolls `<input>` / `<textarea>` with a local `field`
  class instead of `forms/Input`, `forms/Textarea` and `Field` — **placeholders
  used as labels**, so the form has no accessible names (§7).
- **P-DE-2** The ratify pane's `DecisionCard` gets `onRatify={() => {}}` — the
  primary action of that view is inert.
- **P-DE-3** The rail matches decisions **by statement string** to find an id; a
  decision outside the current filter silently falls back to a local-only
  "Ratified" chip that never persists.
- **P-DE-4** "Awaiting sign-off" renders an empty `<ul>` with no empty state.
- **P-DE-5** Ratify is a governance action with no `ConfirmButton` and no undo.

### Library — `P-LI`
- **P-LI-1** The Rules tab has no actions by design: the registry is compiled
  RegExps inside `LibraryRulesPanel`, so a workspace cannot add, edit, disable or
  scope a rule. The one Library tab that cannot be curated.
- **P-LI-2** `data.counts` is a separate record from the collections the panels
  render; the two can disagree.
- **P-LI-3** No cross-tab search; five tabs each with their own filter.

### Lineage — `P-LN`
See [Lineage (L)](#lineage-l).

### Facts — `P-FA`
- **P-FA-1** `factChip(status)` dispatches on the **English display string**
  ("Verified", "Stale", "Contradicted"); a status the API spells differently
  silently becomes "Needs review".
- **P-FA-2** The table has no sort, no pagination, no search — the `many` state is
  declared and the table just grows.
- **P-FA-3** No way to edit or retire a claim; only verify and create.
- **P-FA-4** The local `verified` overlay never clears on refetch, so a claim
  un-verified server-side keeps showing as verified until reload.
- **P-FA-5** The no-handler scan invents progress: `progress + 20` every 260ms to a
  fabricated "passed".
- **P-FA-6** `Progress` and `ChipStatus` imported and unused (C10).

### Repository Audit — `P-AU`
- **P-AU-1** **No run visibility.** `runAudit` with no handler does
  `await new Promise(r => setTimeout(r, 700))`; with a handler it awaits and
  forgets. Facts and Decisions both use `ScanRunCard` with polling for the same
  concept — Audit should too.
- **P-AU-2** History rail rows carry `hover:bg-flysch` and no `onClick` — a hover
  affordance with no behaviour (§2). Past runs cannot be opened.
- **P-AU-3** `ranAt` arrives pre-formatted, so it cannot be sorted or
  re-localised (contradicts "return data, not display strings").
- **P-AU-4** Unreachable from the nav (C6).

### Flows — `P-FL`
- **P-FL-1** `Body` destructures `mobile` and never uses it — **Flows has no
  mobile layout at all** while every other page does.
- **P-FL-2** `showsHeader` returns false for the default list view, so Flows is the
  only console page whose `PageHeader` appears and disappears by state.
- **P-FL-3** `editTriggerFor` resolves the flow **by name**; two flows with the
  same name open the wrong trigger editor.
- **P-FL-4** No `deleteFlow` / `duplicateFlow` in `FlowsActions`.

### Publish — `P-PU`
- **P-PU-1** **The site-editor tab bar is not clickable.** `EditorTabs` renders
  `<span>` elements; the active tab comes only from `data.editorTab`. Content /
  Theme / Preview / Domains are unreachable by clicking.
- **P-PU-2** The Theme "Density" control is three `<span>`s with the first
  hardcoded selected — decoration (§2).
- **P-PU-3** `PreviewBody` reads `site.accents[0]`, not the accent just picked, so
  the live preview never reflects a theme change.
- **P-PU-4** `Progress value={62}` during publishing is a hardcoded fake
  percentage.
- **P-PU-5** The nav tree shows a `GripVertical` handle with no drag-reorder;
  "Add section" / "Remove last" are local-only, so the nav editor cannot edit the
  nav.
- **P-PU-6** `SiteList` columns are `SortHeader … sortable={false}` (C3); no
  pagination.
- **P-PU-7** No delete-site action anywhere.

### Insights — `P-IN`
- **P-IN-1** No date-range control, no comparison period, no export — a metrics
  dashboard whose window is fixed by the adapter (`since` is data the user
  cannot change).
- **P-IN-2** No drill-through: no widget links to the documents behind a number.
- **P-IN-3** `isEmpty` requires everything empty, so a workspace with freshness
  data but no readability rows renders a mostly-blank grid instead of per-widget
  empty states.

### Tasks — `P-TA`
- **P-TA-1** The composer collects only title and kind. §7 requires owner,
  priority and due date on one line, and `Task` already carries `due` / `who` —
  so you can see a due date you can never set, and every created task is assigned
  to yourself.
- **P-TA-2** No assignee combobox (§7 requires a searchable one).
- **P-TA-3** Optimistic rows use `id: Date.now()`, which can collide with a real
  server id; `create` does not return the created row.
- **P-TA-4** `Task.due` is a pre-formatted string: unsortable, unlocalisable, and
  `overdue` is computed server-side with no timezone stated.
- **P-TA-5** No filter (assigned to me / overdue), no sort, and no link from a task
  to the document it is about.
- **P-TA-6** Board state never resyncs (C1) — the most visible instance, since this
  page mutates constantly.

### Sources — `P-SO`
- **P-SO-1** **Invented data in the library**: `connectorSyncSource()` returns
  `lastSyncAt: "2026-07-21T14:12:00"`, a hardcoded timestamp shown as a real
  sync time.
- **P-SO-2** `SyncPanel … onRetry={() => {}}` on the sync-status view — the retry
  button is inert.
- **P-SO-3** `SourcesSyncStatus animate={false}` is rendered with no props at the
  bottom of the connectors grid; it carries its own content rather than the
  workspace's.
- **P-SO-4** The Bots tab receives `slack` / `github` status but **no `actions`**,
  so nothing on that tab can write.
- **P-SO-5** Two tab rows stacked (`SettingsTabs` plus the page's own segmented
  tabs) reads as competing navigation.
- **P-SO-6** No per-source sync schedule or interval control anywhere.

### Preferences — `P-PR`
- **P-PR-1** Em dashes in every timezone label (C9); only eight zones offered.
- **P-PR-2** No sessions list, no "sign out everywhere", no 2FA enrolment — yet
  `LoginPage` has a full two-factor screen, so 2FA can be challenged and never
  enrolled.
- **P-PR-3** No delete/deactivate account, no language, no theme.
- **P-PR-4** `NotificationsCard` local echo never resyncs from
  `data.notifications` (C1).

### Settings → General — `P-SG`
- **P-SG-1** Timezone vocabulary conflict (C8); only three options.
- **P-SG-2** `transferWorkspace` fires immediately on click with **no
  `ConfirmButton`**, while the delete row beside it correctly uses one.
  Transferring ownership is at least as irreversible.
- **P-SG-3** Branding lives here *and* on Settings → Design & brand;
  `data.section === "branding"` renders `BrandingEditor` with **no `actions`**,
  so the copy inside General cannot save.

### Settings → Members — `P-SM`
- **P-SM-1** No search, no pagination, no role filter — the `many` state is
  declared with nothing to manage it.
- **P-SM-2** The remove confirmation is driven by `data.interaction`, not by the
  page, so in the app it depends on the adapter setting a field it never sets.
- **P-SM-3** Nine unused imports (C10).

### Settings → Models — `P-SD`
- **P-SD-1** **The whole `ModelsInline` view is inert.** Every `<Select>` uses
  `defaultValue` with no `onChange`; both "Save changes" buttons and "Test
  connection" have no `onClick`; `SecretField` keeps a `draft` nothing reads. Any
  `phase` other than `"config"` renders a form that cannot do anything.
- **P-SD-2** Embedding dimensions are a hardcoded `768 / 1536 / 3072` list
  unrelated to the selected model — you can pick 3072 for a 768-dim model.
- **P-SD-3** Changing the embedding model re-indexes the corpus (the rail says so)
  with no confirmation step.

### Settings → API keys — `P-SK`
- **P-SK-1** No expiry, no scope-selection UI (scopes are a free-text
  `draft.scopes` string against a rail documenting four fixed scopes), no
  last-used column.
- **P-SK-2** Eight unused imports (C10).

### Settings → Access log — `P-SA`
- **P-SA-1** **`AuditInline` is a picture of a log.** The filter input is
  `readOnly`; the filter's `X` is a bare icon, not a button; the expand chevron
  is not a button and rows are not clickable; `Pagination onChange={() => {}}`.
  Every interactive affordance on the filtered / expanded / paginated variants is
  dead.
- **P-SA-2** Bare `<th>` headers (C3); no export; no date-range picker despite a
  "filtered by date" state.
- **P-SA-3** No actions counterpart for the filter, so filtering is entirely a
  canvas concept.

### Settings → Design & brand — `P-SS`
- **P-SS-1** No error state for the `BrandingEditor` itself, no empty state, no
  `isEmpty` — the least-developed Settings page.

### Login — `P-LG`
- **P-LG-1** **No "Forgot password?" anywhere.** Magic link is offered as a
  sign-in method, not as recovery, and only when a handler exists.
- **P-LG-2** The submit button reads "Signing in…" while registering.
- **P-LG-3** "Email me a magic link instead" fires `actions?.magicLink?.(email)`
  with no email validation, no busy state and no feedback; with no handler it is
  silently inert.
- **P-LG-4** `TwoFactorForm.submit` returns early when `verifyCode` is absent, so
  an enabled "Verify & continue" does nothing.
- **P-LG-5** No "trust this device", no resend on the 2FA screen (resend exists
  only on magic-link).
- **P-LG-6** Register collects name/email/password with no password rules, while
  Preferences enforces 8+ characters and a confirmation field.

### Setup — `P-ST`
- **P-ST-1** The password field has no confirmation and no minimum — inconsistent
  with Preferences and with any first-run flow.
- **P-ST-2** `CodeBlock … copy={false}` on the block containing the one-time
  token, and the help text says "Run the command above" when the block is a
  **log excerpt**, not a command.
- **P-ST-3** The token is not validated on step 1, so a typo is discovered only
  after filling in four more fields.

### Welcome — `P-WE`
- **P-WE-1** **Two competing primary actions on every connector sub-step.** The
  wizard's global footer renders "Continue" alongside the step's own "Connect &
  sync"; pressing Continue skips connecting entirely.
- **P-WE-2** `back()` uses `SPINE[step - 1]`, so "← Back" from `connect-github`
  lands on **Hero**, not on the connector grid.
- **P-WE-3** The three `DoneStep` cards (Explore Knowledge / See Lineage / Set up
  bots) are `<div>`s with `hover:border-ink/35` and **no `onClick`** — dead links
  at the end of onboarding.
- **P-WE-4** `ConnectorHeader`'s "Where do I get these? ↗" is a `<span>`, not a
  link.
- **P-WE-5** The Hero's right-hand panel is a dashed placeholder box reading
  "Onboarding journey" — a design placeholder shipped as product.
- **P-WE-6** `ConnectorHeader` hardcodes the chip "Step 2 · Connect" on steps that
  are not step 2 in every path.

### Lookbook — `P-LB`
- **P-LB-1** The only page with no adapter (`stubs.ts` hands it `null`). It is in
  `PAGES`, so it is routed at its path and renders the library's own catalog
  inside the customer console. Exclude it from `PAGES` in app builds, or gate it.

---

## Suggested sequencing

1. **M1, M2** — markdown round-trip data loss on Save. Everything else is
   cosmetic next to silently deleting a customer's links and tables.
2. **P-KN-1** — the flagship search is a client-side filter over 40 rows.
3. **Inert controls that look live** — P-PU-1, P-SD-1, P-SA-1, L2, L1.
4. **Invented data** — P-DR-1, P-SO-1, P-PU-4, L6, P-FA-5.
5. **C1** — the resync idiom, once, applied everywhere.
