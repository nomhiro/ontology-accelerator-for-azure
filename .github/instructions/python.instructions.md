---
applyTo: "packages/**/*.py,scripts/**/*.py"
---

# Python / API

Read `AGENTS.md` and the relevant ADRs. Use uv, Python 3.12, ruff and strict
mypy. Run focused pytest tests, `uv run ruff check .`,
`uv run ruff format --check .`, and `uv run mypy packages` for code changes.
Check Docker and published ports before integration tests; never run parallel
pytest processes against the same DB.

Preserve Blob -> PostgreSQL commit -> Fuseki ordering and canonical success on
projection failure. Roll back failed sessions; do not access expired ORM rows
outside async IO. Use `ontology_core.db.by_identifier` for identifier ordering,
not human-readable labels. Indexes must agree between ORM and migrations.
Keep test basenames unique across `packages/*/tests`. Prove regression tests
fail before fixing. Intentional MCP refusals use `ToolError`, not `ValueError`.
Scripts use `ontology_core.console.say`/`warn`, not `print`; decode piped JSON
as UTF-8 bytes. Do not silently default failures into empty/valid results.
