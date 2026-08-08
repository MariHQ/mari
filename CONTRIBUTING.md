# Contributing to Mari

Thanks for taking the time to contribute. This guide covers how to get the project
running, what we expect from a change, and how to sign your commits off.

## Developer Certificate of Origin

This project uses the [Developer Certificate of Origin](https://developercertificate.org/)
(DCO), reproduced in full in [DCO](DCO). There is no CLA to sign — instead, every
commit must carry a `Signed-off-by` line certifying that you have the right to
submit the work under this project's license:

```
Signed-off-by: Jane Doe <jane@example.com>
```

Git adds it for you with `-s`:

```sh
git commit -s -m "Fix chunk hash comparison on resync"
```

Use your real name, and make sure the name and email match the ones on the commit.
For the address, either one that reaches you or your GitHub noreply address works.
The noreply form is `<id>+<username>@users.noreply.github.com` and it's listed on
your [email settings](https://github.com/settings/emails) page. Nobody needs to
publish a personal address to contribute here, and if you've enabled "Block command
line pushes that expose my email" on GitHub, the noreply address is the one to use.

To make sign-off automatic for this repo:

```sh
git config user.name "Jane Doe"
git config user.email "jane@example.com"   # or 1234567+jane@users.noreply.github.com
git config format.signOff true
```

Forgot to sign off? Amend the last commit with `git commit -s --amend --no-edit`,
or fix a whole branch with `git rebase --signoff origin/main`, then force-push.

## Getting set up

You'll need Postgres (with pgvector available), Python 3.11+, and Node 20+.
[ollama](https://ollama.com) is optional — every LLM feature has a deterministic
fallback, so the app works without it.

```sh
# 1. Database
createdb mari_cloud
for f in server/init*.sql; do psql mari_cloud -f "$f"; done   # idempotent

# 2. API — http://localhost:8000 (/graphql, /healthz)
cd server
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app:app --reload --port 8000

# 3. Web — http://localhost:5173 (proxies API routes to :8000)
cd web
npm install
npm run dev
```

Copy `.env.example` to `.env` and `mari.toml.example` to `mari.toml` if you need
to override defaults. Never commit real credentials — connector tokens, database
URLs, and API keys belong in your local `.env` only.

## Before you open a pull request

Run the checks that CI and reviewers will run:

```sh
cd web && npm run check     # tsc + eslint + stylelint + knip + jscpd
cd web && npm run build     # production build must succeed
```

For server changes, exercise the affected path against a local database — schema
changes go in a new `server/init*.sql` file and must be **idempotent**, since the
init scripts are re-run on every deploy.

## What we look for in a change

- **One concern per pull request.** Small, reviewable diffs land faster.
- **UI changes go to the component library, not here.** Every screen comes from
  [mari-design](https://github.com/MariHQ/mari-design), pinned as the
  `vendor/mari-design` submodule; `web/` holds no page code and nothing to
  restyle. What lives here is `web/src/data/<page>.ts` — a GraphQL query plus a
  mapper onto the page's exported data type. See `src/data/overview.ts`.
- **Never invent data in a mapper.** Library pages are pure presenters with no
  fallback content, deliberately: a page cannot show a real user an invented
  number. If a page needs something the API has no source for, add the field to
  the backend returning a real (possibly empty) result.
- **Honest by construction.** No canned data in the UI, no placeholder integrations,
  metrics count real events, and failures surface verbatim rather than being
  swallowed.
- **LLM-optional.** Anything that calls a model needs a deterministic fallback so
  the system degrades instead of breaking when ollama is offline.
- **Keep ingestion incremental.** Connectors feed the shared fetch → chunk →
  content-hash → embed pipeline. Unchanged content must never be re-embedded.
- **Frozen contracts stay frozen.** The `*-CONTRACT.md` files describe integration
  boundaries; changing one is a deliberate, separately discussed step.

## Commit messages

Write a short imperative subject line (under ~72 characters) describing what the
commit does, and use the body to explain *why* when it isn't obvious. Reference
issues with `#123`. Every commit needs its `Signed-off-by` line.

## Reporting bugs and proposing features

Open an issue with what you expected, what happened, and the smallest set of steps
that reproduces it. Include the relevant log output verbatim, plus your Postgres
version and whether ollama was running. For larger features, open an issue to
discuss the approach before writing the code — it saves everyone a rewrite.

## Security

Please do not open a public issue for security vulnerabilities. Report them
privately to the maintainers so a fix can ship before details are public.

## License

By contributing, you agree that your contributions are licensed under the same
license as this project, and you certify the DCO as described above.
