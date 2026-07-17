---
name: design-system-first
description: Before making any UI change in web/src, update the component library (src/components/ui) and design tokens first — pages compose primitives, never hand-roll. Load this before styling, theming, or building any page UI.
---

# Design-system-first

Mari Cloud's UI is centralized in a small component library. Every visual
change flows through it — never patch styles or build bespoke elements
directly in a page.

## The rule

When a page needs something visual:

1. **Check the library first.** `web/src/components/ui/index.ts` re-exports
   every primitive (Card, Button, Chip trio, Drawer, Menu, Tabs, Table,
   Field/Input/Select/Textarea, Stat, EmptyState, Spinner, Stepper, Avatar,
   Sparkline, Toast/useToast, ConfirmButton, IconRing, Tooltip, Switch,
   Popover, PageHeader, fmtDate/fmtTime/fmtDateTime). Exhibits and usage
   rules live at `/lookbook` (`web/src/pages/Lookbook.tsx`).
2. **If a primitive almost fits, extend the primitive** (a variant, a prop,
   a tone) — in `src/components/ui/`, with an exhibit added to the Lookbook.
   Then use it from the page.
3. **If nothing fits, add a new primitive** to `src/components/ui/`, export
   it from `index.ts`, exhibit it in the Lookbook, then use it.
4. Only content-specific composition lives in pages. Zero bespoke cards,
   menus, chips, buttons, tables, or empty states in page files.

## Tokens, not colors

All colors/fonts/spacing come from the CSS custom properties in
`web/src/styles.css` `:root` (--paper, --ink*, --terra*, --green, --blue,
--red, --gold, --serif/--sans/--display/--mono…). Workspace branding
overrides these at runtime (see `src/lib/branding.tsx`), so:

- **Never hardcode hex values** in page CSS or inline styles — use a token.
  If no token expresses the intent, add a semantic token to `:root` first.
- New primitives must be brand-safe: they inherit tokens, they don't restate
  palette values.

## Style rules

- `styles.css` holds tokens + shared idioms only; page css files hold only
  layout/composition specific to that page. Delete page css that a primitive
  makes redundant.
- Behavior-bearing components (menus, dialogs, tooltips, toasts, switches)
  wrap Radix UI — extend those wrappers, don't hand-roll focus/dismiss/aria
  logic.
- eslint warns on large inline style objects; that warning means "move it to
  a primitive or a class".
- Verify with `npx tsc --noEmit`, `npx eslint <files>`, and check the
  Lookbook page still exhibits what you changed.
