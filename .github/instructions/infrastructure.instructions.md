---
applyTo: "infra/**,containers/**,scripts/**/*.sh,.github/workflows/**,azure.yaml,docker-compose.yml,justfile,.devcontainer/**"
---

# Infrastructure / containers / shell

Read `AGENTS.md` and the relevant ADRs before changing infrastructure.
`AUTH_MODE=disabled` is local-only. Keep internal store ingress and explicit
namespace authorization. Azure deployment requires approval and verified
`azd down --purge` cleanup; never deploy as a routine CI/environment check.

Use the relevant CI checks: Bicep build, shell tests, shellcheck v0.11.0,
reasoner/VKG real-container tests, license scanning. Use `uv run python`, never
bare `python` in shell scripts. Under `set -e`, capture nonzero exits with
`rc=0; command || rc=$?`. Preserve UTF-8/LF. Apply Git Bash path-conversion
suppression only to the specific docker/ARM-ID command, never export it.
Keep new Bicep parameters string-compatible with azd substitution.

Verify action tags exist before updating; setup-uv's exact `v10.1.0` must not
be guessed into `v10`. Respect paths-filter dependencies and main's
non-cancelling CI runs. A skipped check is not evidence of a test passing.
Container/JDBC changes must retain TLS verification and license/SHA records.
