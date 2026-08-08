# Security policy

We take security seriously and appreciate reports from the community.

## Reporting a vulnerability

Please do not open a public issue for a security vulnerability. Public disclosure
before a fix is available puts every deployment at risk.

Instead, report it privately through GitHub's
[private vulnerability reporting](https://github.com/MariHQ/mari/security/advisories/new).
This opens a private channel with the maintainers where we can confirm the issue,
work on a fix, and coordinate disclosure.

When you report, include:

- What the vulnerability lets an attacker do.
- The smallest set of steps that demonstrates it.
- The affected version or commit, and how you were running Mari (Docker compose,
  local dev, desktop app, or a hosted instance).

## What to expect

- We aim to acknowledge a report within a few business days.
- We will keep you updated as we confirm the issue and work on a fix.
- Once a fix ships, we are glad to credit you in the advisory unless you would
  rather stay anonymous.

## Scope

Mari is self-hosted and LLM-optional by design. A few things are working as
intended rather than vulnerabilities:

- The demo auth bypass (`auth.bypass_enabled` / `MARI_AUTH_BYPASS`) is **off by
  default** and exists only so evaluation instances can skip sign-in. Turning it
  on is a deliberate choice for a throwaway demo, documented in the README.
- Connector credentials you enter are stored so syncs can run unattended. Protect
  the host and database the way you would any system holding integration tokens.

If you are unsure whether something counts, report it privately and we will help
you figure it out.
