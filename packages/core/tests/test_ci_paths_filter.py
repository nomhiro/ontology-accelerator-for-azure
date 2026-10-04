"""CI の paths-filter と検査対象の依存関係を固定する。"""

from __future__ import annotations

from pathlib import Path

_WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "ci.yml"


def _filter_body(name: str) -> str:
    """`changes` job の指定した filter の本文を返す。"""
    lines = _WORKFLOW.read_text(encoding="utf-8").splitlines()
    marker = f"            {name}:"
    start = lines.index(marker) + 1
    end = next(
        index
        for index in range(start, len(lines))
        if lines[index].startswith("            ") and lines[index].strip().endswith(":")
    )
    return "\n".join(lines[start:end])


def test_python_filter_covers_scripts_and_runtime_contract_inputs() -> None:
    body = _filter_body("python")
    for path in (
        "scripts/**/*.py",
        "docker-compose.yml",
        ".env.example",
        "justfile",
        ".devcontainer/**",
        "azure.yaml",
        "README.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CODE_OF_CONDUCT.md",
        "NOTICE",
    ):
        assert f"'{path}'" in body, f"python filter に '{path}' が無い"


def test_related_filters_retain_required_runtime_jobs() -> None:
    assert "'docker-compose.yml'" in _filter_body("containers")
    assert "'azure.yaml'" in _filter_body("infra")
    assert "'scripts/**'" in _filter_body("shell")


def test_general_documentation_does_not_trigger_python() -> None:
    body = _filter_body("python")
    assert "'docs/**'" not in body
