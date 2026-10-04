---
applyTo: "apps/web/**"
---

# Web

Read `AGENTS.md`, ADR-0004, ADR-0044 and ADR-0045. Use Node 22 and pnpm 9;
do not introduce a second npm lockfile. Run `just gen-api` before
`pnpm --filter @ontology-accelerator/web build` (types, tests and build).
Never hand-edit ignored `schema.ts` or `openapi.json`. Handwritten diff types
are checked by `packages/api/tests/test_web_contract.py`.

Preserve unknown, uncomputable and incomplete states visibly; they are not
"no changes" or "no violations". Retain actionable approval-denial reasons,
two-person approval and MSAL authentication. Never log credentials.
Pure-function tests do not prove browser rendering or interactive sign-in;
report these separately. Changes to interactive controls must preserve
keyboard access, labels and focus behavior. Fluent UI Options with non-string
children require `text`.
