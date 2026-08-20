# Agent outcome evals

Run `make test-agent-evals`. These are deterministic product evals, not wording
snapshots: a case passes only when the agent emits a successful tool result,
navigates to a shipped route, completes its SSE turn, and gives the concrete
next actions required by that workflow.

The matrix covers Home, Knowledge and document retrieval, unified Review,
Facts, Decisions, Approved answers, Lineage, Automations, documentation sites,
MCP, bots, priority connectors, Analytics, agent trajectories, Library, model
configuration, members and enterprise identity, API keys, audit logs,
repository audit, branding, workspace settings, preferences, and onboarding.
Inventory evals additionally assert that sources, Review items, automations,
and approved answers are grounded in live projection results rather than model
memory. The production-stack Playwright suite sends a real agent-chat request
for MCP setup and verifies the browser reaches the correct tab with usable
token/client/test instructions.

Add an eval whenever a product workflow or route is added. Do not satisfy an
eval by weakening required outcomes; either improve the grounded tool path or
make the unsupported limitation explicit to the user.
