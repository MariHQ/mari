---
name: design-system-first
description: The Mari console has no page code in this repo — every screen comes from the @mari-design/components library in vendor/mari-design, and web/src/data holds only query-to-props adapters. Load this before any console, styling, or page-data change.
---

# Design-system-first

The console is a thin app over a shared component library. `web/` contains no
pages, no primitives, and no stylesheet worth editing — only routing, a GraphQL
client, and one adapter per page.

## Where things live

| | |
|---|---|
| `vendor/mari-design/components` | the library: primitives, features, and all 23 pages. A git submodule of [mari-design](https://github.com/MariHQ/mari-design); imported as `@mari-design/components`. **Read-only from this repo** — change it in its own repo and bump the pointer. |
| `web/src/App.tsx` | routes over the library's `PAGES` registry |
| `web/src/data/<page>.ts` | a GraphQL query + a mapper onto the page's exported `XxxData` type |
| `web/tailwind.config.js` | brand tokens, mirrored from the library's own config |

## The rule

**A visual change is never a change to this repo.** If a card is wrong, a chip
is the wrong tone, or a page needs a new widget, that work happens in
mari-design. There is nothing here to patch — no page CSS, no local primitives,
no overrides. Do not add any.

**A data change is always a change to this repo,** and it lives in exactly one
file: `web/src/data/<page>.ts`.

## Writing an adapter

Read `web/src/data/overview.ts` first — it is the worked reference. The shape:

```ts
const QUERY = `{ … }`;                       // one document per page
export function usePage(): PageData<XxxData> // query state → page props
```

Library pages are pure presenters. Each takes `{ data, loading, error, mobile }`,
holds no demo content, and derives its own empty state from the data it is
given. So:

- **Never invent a value in a mapper.** Not a placeholder name, not a zero
  standing in for a count you did not fetch, not a default `[]` that hides a
  failed query. The page has no fallback precisely so a real user can never be
  shown a number nobody can trace.
- **If the API has no source for a field, fix the API.** Add it to
  `server/queries.py` / `server/gqltypes.py`, returning a real — possibly
  empty — result. Prefer extending a GraphQL field over contorting the mapper.
- **Return data, not display strings.** ISO dates and raw status words: the
  library formats (`fmtDate`, `fmtAgo`) and sorts on the raw value, so a
  pre-formatted `"Jul 8, 2026"` both double-formats and sorts alphabetically.
- **Map onto the library's vocabulary, honestly.** A status the library does
  not know should render as nothing, not as a guess.
- Wire `loading` / `error` straight from the query state. Errors surface
  verbatim — the string is shown to the user, so it must be real.

## Verify

```sh
cd web
npm run check   # tsc over src/, then server-render the adapted pages
npm run build
```

`npm run smoke` is the useful one: it renders each adapted page from a mock API
response and catches what types cannot — a mapper that satisfies the type but
hands the page something it will not draw.
