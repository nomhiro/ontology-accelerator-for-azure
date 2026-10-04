# Repository instructions

Ontology Accelerator for Azure is an Apache-2.0 project providing W3C
RDF/OWL/SPARQL/SHACL context to AI agents via MCP. Read `AGENTS.md`,
`CONTRIBUTING.md`, the task's GitHub Issue, `docs/backlog.md` (ID/source/history
index), `docs/roadmap.md`, and the relevant `docs/adr/` before making changes.
These links do not imply that a client automatically loads their contents.

## Issue-based development

GitHub Issues are the source of current task state. Preserve task IDs; include
them in commit messages. Comment on the Issue when starting, blocked, or
resuming. Report exact validation commands, results, and unverified criteria.
Use `Closes #number` only when the PR satisfies every acceptance criterion.
For partial work use `Refs #number`. Update the backlog index in the same
commit when IDs, sources, links, or design history change, not for state alone.
Create Issues for newly found work; never publish vulnerabilities or secrets
(follow `SECURITY.md`). If unable to update GitHub, say so. Optional/decision
Issues do not authorize implementation, billing, permission changes, or
assignment to an agent. Cloud setup remains separate from these instructions.

## Architectural invariants

- PostgreSQL metadata/audit and versioned Blob TTL are authoritative; Fuseki
  is a rebuildable projection. Write Blob, commit PostgreSQL, then Fuseki.
  Projection failure must not fail a durable canonical write; reconcile
  recovers it. `projected_at` records a write path, not current store content.
- Store failures use `SparqlStoreError` or `BlobStoreError`. Validate external
  namespace names with `validate_namespace_name`. Never put passwords in DSNs;
  Entra connections use a per-connection password callable.
- Published revisions are immutable. Never delete/reuse term IRIs; preserve
  deprecated terms with `owl:deprecated` and successor or reason.
- Authorization defaults to deny. `AUTH_MODE=disabled` is local-only.
  `platform-admin` does not bypass two-person approval. Responsibility routing
  fallback is not permission fallback; display its `source`.
- Competency criteria are namespace-level immutable revisions. Their authorship
  check shares `require_two_person_approval`; do not add a bypass switch.
  Approval checks use canonical TTL, never the projection store.
- Cross-domain mappings use only the five SKOS mapping predicates; never
  `owl:equivalentClass`. Do not invent reverse mappings or hide disputes.
- Publish and namespace deletion acquire the same namespace row lock before
  Blob writes or inspection. A DELETE statement's implicit lock is too late.

## Implementation and validation

Python 3.12, uv, ruff, strict mypy; Web uses Node 22, pnpm 9 and generated API
types. Prove a regression test fails before fixing the code. Preserve errors
and unknown/incomplete states; do not turn them into success or empty results.
Generate API types with `just gen-api` before Web type checks/builds. Never
run pytest processes simultaneously against the same DB (fixtures rebuild it).
`just check` is the fast Python lint/typecheck/unit-test entry point; `just
check-all` is the complete local validation entry point except Azure
provisioning, credential/permission changes, and destructive cleanup. Use the
full relevant commands in `AGENTS.md` and CI, and report what was not run.
Keep UTF-8/LF and follow OS-specific shell rules.

Azure deployment incurs costs: obtain explicit approval, then tear down with
`azd down --purge` and verify resources and soft-deleted vaults are gone.
Do not run provisioning or destructive cleanup as an environment check.
