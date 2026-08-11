## What does this change?

<!-- Brief description of the change and why it's needed. Link issues with #123. -->

## How was it tested?

<!-- Commands run, manual steps, environment. -->

## Checklist

- [ ] All commits are signed off (`git commit -s`), agreeing to the [CLA](../CLA.md)
- [ ] `npm run check` and `npm run build` pass in `web/` (if the console changed)
- [ ] Schema changes are idempotent and live in a `server/init*.sql` file
- [ ] Visual changes went to the [mari-design](https://github.com/MariHQ/mari-design) library, not `web/`
- [ ] Page adapters map API responses only — no values invented in `web/src/data/`
- [ ] No credentials, tokens, or canned/placeholder data added
