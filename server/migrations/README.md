# Database migrations

`init.sql` is migration `0001_baseline` and must not be edited after the
migration-ledger release. Add schema changes as `NNNN_description.sql`, using
four increasing digits, lowercase letters, numbers, and underscores.

Migrations run in one PostgreSQL transaction under an advisory lock. Applied
file checksums are recorded in `schema_migrations`; changing or removing an
applied migration intentionally blocks startup. Migrations must therefore be
backward compatible with the previous application release during a rolling
deployment.

Run locally with:

```sh
MARI_DB=postgresql://… python -m schema_migrations
```
