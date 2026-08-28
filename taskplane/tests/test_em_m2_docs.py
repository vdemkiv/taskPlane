"""Focused evidence for the M2-C public documentation contract."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _generated_cli_reference() -> str:
    completed = subprocess.run(
        [sys.executable, "taskplane/tp.py", "help", "--md"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout


def test_m12_primary_journey_matches_current_approval_flow() -> None:
    readme = _read("README.md")
    journey = readme.split("- **The governed Evaluate-Loop.**", 1)[1].split(
        "\n- **Enforced contracts.**", 1)[0]
    assert "one consolidated pre-implementation authorization" in journey
    assert "final human sign-off" in journey
    assert "human Design approval" not in journey
    assert "human plan approval" not in journey


def test_m13_marketplace_copy_describes_routed_review_truthfully() -> None:
    manifest = json.loads(_read(".codex-plugin/plugin.json"))
    copy = manifest["interface"]["longDescription"]
    assert "complete 26-lens disposition" in copy
    assert "evidence-selected deep reviewers" in copy
    assert "at most one light sweep" in copy
    assert "light or n/a rationale disclosed" in copy
    assert "full 26-lens engineering review" not in copy


def test_m21_public_python_floor_is_3_10_or_newer() -> None:
    readme = _read("README.md")
    prerequisite = readme.split("Requires `git`", 1)[1].split(
        "\n## Quickstarts", 1)[0]
    assert "CPython 3.10 or newer" in prerequisite
    assert "CPython 3.10–3.13" in prerequisite
    assert "docs/configuration.md#supported-python-runtime" in prerequisite
    assert "and Python 3\n" not in prerequisite


def test_m23_onboarding_discloses_legacy_KB_migration() -> None:
    onboarding = _read("docs/onboarding.md")
    init_step = onboarding.split("3. **`tp init`**", 1)[1].split(
        "\n## Claude onboarding", 1)[0]
    for expected in (
        "tracked legacy `knowledge/` directory",
        "moves that directory into the external project store",
        "`git rm --cached`",
        "adds `knowledge/` to `.gitignore`",
        "`.taskplane-kb/knowledge/`",
        "state-spec.md#migration-from-an-in-repo-knowledge-base",
    ):
        assert expected in init_step


def test_l05_builtin_tour_is_version_neutral_or_release_derived() -> None:
    help_skill = _read("skills/tp-help/SKILL.md")
    current = help_skill.split("**What's changed in the installed release.**", 1)[1]
    current = current.split("\n**In v2.13", 1)[0]
    assert ".codex-plugin/plugin.json" in current
    assert "CHANGELOG.md" in current
    assert "installed version" in current
    assert "What's new in v2.16" not in help_skill


def test_l07_generated_positional_enum_choices_are_documented() -> None:
    generated = _generated_cli_reference()
    assert generated == _read("docs/cli-reference.md")
    submit = generated.split("## `tp.py loop submit`", 1)[1].split(
        "\n## `tp.py loop verify-dispatch`", 1)[0]
    assert "`outcome` (required; choices: `pass`, `fail`, `unavailable`)" in submit
    req_signoff = generated.split("## `tp.py req signoff`", 1)[1].split(
        "\n## `tp.py review`", 1)[0]
    assert "`decision` (required; choices: `approve`, `changes`)" in req_signoff
    review_signoff = generated.split("## `tp.py review signoff`", 1)[1].split(
        "\n## `tp.py review start`", 1)[0]
    assert "`decision` (required; choices: `approve`, `changes`)" in review_signoff


def test_l08_primary_docs_link_generated_CLI_reference() -> None:
    going_deeper = _read("README.md").split("## Going deeper", 1)[1]
    assert "[docs/cli-reference.md](docs/cli-reference.md)" in going_deeper
    assert "complete generated command" in going_deeper


def test_l09_onboarding_uses_one_consistent_four_document_model() -> None:
    onboarding = _read("docs/onboarding.md")
    context = onboarding.split("## Context storage (token efficiency)", 1)[1]
    assert "four context docs" in context
    assert "three context docs" not in onboarding
    for name in ("current-state.md", "product.md", "tech-stack.md", "workflow.md"):
        assert name in context
    assert "fill it first" in context
