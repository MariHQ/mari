# Console parity with the demo

Written 2026-08-17 for whoever continues wiring this console, human or agent.

## The rule

**mari.guru/demo is the reference for how this console looks and behaves.** Where cloud.mari.guru and the demo disagree, the demo is right and this app gets wired to match. Decide what a page does, where a control goes, and what a state looks like by opening the demo, not by reading the deployed console.

This is not a style preference. The demo renders the exact page components this app renders, from the exact commit of `@mari-design/components` this app pins (`vendor/mari-design`), on the library's own approved fixtures. Every screen the demo shows is a screen this app can ship, and every state the demo shows is a state an adapter here has to be able to produce. Anything the demo does that this app does not is a gap on this side.

Three places, one library:

| Name | Where | Owns |
| --- | --- | --- |
| library | `MariHQ/mari-design` `components/`, vendored at `vendor/mari-design` | pages, shell, widgets, canvas fixtures |
| console | this directory (`web/`) | routing, one read adapter per page (`src/data/<page>.ts`), one actions factory per page (`src/data/actions/<page>.ts`), the agent dock |
| demo | `MariHQ/mari-web` `src/redesign/components/canon/mock/` | the same pages, routed off the URL hash, fed the fixtures, wired with the navigation intents this app wires |

The demo and this app must pin the same library commit. Bump both together, always.

## Why the demo looked better, and what this branch did about it

Same components, same CSS, same commit. The differences were in what the host gives the pages.

**Fonts.** The library sets `font-display` to Inter and `font-term` to JetBrains Mono, and neither the library canvas nor this app ever loaded them. On a machine that has both installed the console already looked like the demo. On every other machine, every heading, chip, table header and count fell back to the system sans and Menlo. The demo's landing page loads both faces, so it never had this problem. Now this app ships them: `public/fonts/` (latin and latin-ext variable subsets, ~190 KB, OFL licences beside the files), `@font-face` in `src/styles.css`, preloads in `index.html`, and `server/app.py` registers the `woff2` mime type where it serves the SPA. No third-party request.

**Snippets.** `documents.snippet` was the first 180 characters of the body with whitespace collapsed. For repository documents that is `# Title commit 3f2a1c9 · author · 2026-07-28T18:33:24Z Title …`: a hash mark, the title the card already carries, a SHA, a timestamp, and the sentence pushed off the end. The demo's fixtures show a sentence. Now `server/excerpt.py` produces that sentence (front matter, HTML, the duplicated title, the machine line and Markdown syntax all go), at ingest for new rows and again at read time in `queries._doc`, so old rows render the same without a backfill. `src/data/text.ts` does the same at the presentation layer for the ⌘K rows and as a belt for older servers.

**Knowledge lands with a document open.** The demo opens Knowledge with a document in the rail. This app opened it with "Nothing selected". `src/data/knowledge.ts` now inspects the first result when the route names no `?doc=`. Choosing a row still writes `?doc=`, so a shared link opens on that document.

**Onboarding leaves the way the app moves.** `welcome.finish` was a hard `window.location.href = "/"`. It is a router push with the read cache cleared, so the Overview counts what onboarding just connected.

Verified locally against the native `mari_cloud` database with the auth bypass on: `npm run typecheck` and `npm run smoke` clean, fonts loading from `/fonts/`, Knowledge opening on the first result with prose excerpts, `search` returning sentences.

## Parity matrix

Status: **matches** (same both sides), **console gap** (demo shows it, this app does not yet), **library gap** (needs a library change first), **decision** (someone has to choose).

| Surface | Demo | This app | Status |
| --- | --- | --- | --- |
| Sidebar, Settings tabs, account menu, sign out, ⌘K, deep links, back button | routes | routes | matches |
| Overview range picker, "Connect sources" | shown, `/welcome` | shown, `/welcome` | matches |
| Insights doc drill-through, range | routes | routes | matches |
| Flows open/back, Publish MCP tab and open site, Lineage open document and focal node, Doc review back and share, Welcome finish and Setup guide | routes | routes | matches |
| Brand fonts on any machine | yes | yes, this branch | matches |
| Result-card and ⌘K snippets | a sentence | a sentence, this branch | matches |
| Knowledge on arrival | document open | document open, this branch | matches |
| Notification bell rows | dead (no href) | dead (no href) | **library gap**: `ShellNotification` has no `href`; `NotificationBell` supports `onItemClick` but `PageFrame` never passes one. Add `href` to the type, pass it through in the frame, map notification kinds to routes in `src/data/chrome.ts`. |
| Tasks: open a task's document | hidden | `openDoc` unwired | **decision**: task rows record no document. Either `tasks` grows a `documentId` and both sides wire `openDoc`, or the control stays text. |
| Insights freshness drill-through | hidden | `openFreshness` unwired | **decision**: needs a destination first. |
| Agent dock | absent | present, real model | **decision**, see below |
| Settings > General "At a glance" | plan, members, region, created | members, documents | small **console gap**: plan is a settings row already read on this page, region and created need a source. |
| Writes (approve, ratify, save, invite, revoke, run, deploy…) | local fallback, visibly responds | GraphQL mutations | matches in behaviour, differs in effect, by design |
| Loading and error states | never shown | shown from real query state | fine |
| Auth: login, 2FA, setup | excluded | real | fine |
| Library-offered writes neither side implements (decisions ignore/supersede, facts edit/retire, lineage deleteGraphView, login reset/resend/recovery, preferences language/deleteAccount, publish deleteSite/setSiteNav, settings-general transfer/delete, settings-models chunking/testConnection, setup checkToken) | hidden | hidden | matches, wire here when the server has the mutation |

Two library observations that affect both sides equally: `features/LineageDataModel.tsx` keeps lens, layout, zoom, query, as-of and the path finder in a module-level singleton, so those controls survive unmount and are shared by every Lineage instance in the process. And `settings-design` declares an `empty` state and `welcome` a `connect-generic` state with no fixture behind them.

## The agent dock

This app ships the Mari agent as a floating dock over every page (`src/components/AgentDock.tsx`): a real model behind `POST /agent/chat`, streaming tool calls, cited answers, and `navigate` events that move the console under the conversation. The demo has no dock. That is the one place the demo is behind this app, and it is deliberate: every way of putting a dock on a public static page is a product call (leave it out, a visibly scripted one, or a real one against a capped demo endpoint). Until that call is made, the dock stays as it is here and stays absent there.

## Working agreement

1. **Before wiring a page here, open it in the demo** (`mari.guru/demo#/<route>`, or `localhost:8080/demo#/<route>` from a mari-web checkout) and click every control. What the demo does is the target. Where the demo hides a control, this app may hide it. Where the demo routes, this app routes to the same href.
2. **New page in the library:** the demo picks it up on the next submodule bump with no code. This app needs a read adapter in `src/data/` and, if it has intents, an actions factory in `src/data/actions/`. If it has navigation intents, the demo's `demoActions.ts` (mari-web) gets the same lines.
3. **Bump both submodules to the same commit,** then run `npm run check` here and `npm test` in mari-web.
4. **Data shape is look.** If a fixture populates a field and the adapter here leaves it empty, null, or raw, that is a parity gap even though the component is identical. The snippet and the Knowledge selection above are that kind of gap.
5. **Never put demo bypasses on cloud.mari.guru.** The console is founders-only. Anything demo-shaped lives at mari.guru/demo.
6. **Copy discipline:** no em dashes, no semicolons in user-facing strings, product claims say checks and flags, never guarantees.

## Comparing locally

```bash
# console, from this repo
./dev.sh                       # postgres in docker + API on :8000 + this app on :5173

# demo, from a mari-web checkout
git submodule update --init --recursive && npm install && npm run dev   # :8080/demo
```

Both should be on the same `vendor/mari-design` commit (`git submodule status` in each).
