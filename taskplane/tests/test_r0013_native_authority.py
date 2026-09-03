"""R-0013 AC1: Codex owns native execution authority."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import subprocess

import pytest

from taskplane import loop, native_authority
from tests.root_session_fixture import open_delivery_root


ROOT = Path(__file__).resolve().parents[2]


def _authority_design_and_plan() -> tuple[dict, dict]:
    selector = (
        "taskplane/tests/test_r0013_native_authority.py::"
        "test_complete_native_capability_map_is_required_by_design_and_plan"
    )
    criterion = "AC1: current-tree native authority is complete."
    inventory = {
        "schema": native_authority.CAPABILITY_INVENTORY_SCHEMA,
        "pinned_source_sha": native_authority.APPROVED_PINNED_SOURCE_SHA,
        "rows": [
            {
                "capability": capability,
                "native_owner": native_authority.REQUIRED_NATIVE_OWNERS[
                    capability],
                "taskplane_role": native_authority.REQUIRED_NATIVE_ROLES[
                    capability],
                "forbidden_taskplane_authority": list(
                    native_authority.REQUIRED_FORBIDDEN_BY_CAPABILITY[
                        capability]),
                "sources": list(native_authority.REQUIRED_EVIDENCE_SOURCES[
                    capability]),
            }
            for capability in native_authority.REQUIRED_CAPABILITIES
        ],
        "completeness_rule": "All seven current capabilities are required.",
        "host_gap_rule": (
            "A host gap is a human Design blocker and never authorizes "
            "Taskplane."
        ),
    }
    design = {
        "requirement": "R-0013",
        "native_capability_inventory": inventory,
        "authority_boundary": {
            "schema": native_authority.NATIVE_AUTHORITY_SCHEMA,
            "allowed_taskplane_roots": list(
                native_authority.REQUIRED_ALLOWED_ROOTS),
            "forbidden_from_native_dispatch_roots": list(
                native_authority.REQUIRED_FORBIDDEN_AUTHORITIES),
            "stage_journal_disposition": "Current state remains inspectable.",
            "static_rule": "Forbidden current-tree edges fail closed.",
            "behavioral_rule": "Native dispatch remains Codex-owned.",
        },
        "acceptance_map": [{"criterion": criterion, "tests": [selector]}],
        "contracts": [{
            "id": native_authority.NATIVE_CAPABILITY_CONTRACT,
            "relation": "provides",
        }],
    }
    plan = {
        "requirement": "R-0013",
        "tasks": [{
            "id": "t01-native-authority",
            "scope": [
                "taskplane/native_authority.py",
                "taskplane/tests/test_r0013_native_authority.py",
            ],
            "deps": [],
            "type": "architecture",
            "criteria": [criterion],
            "contracts": [native_authority.NATIVE_CAPABILITY_CONTRACT],
            "tests": f"python3 -m pytest -q {selector}",
            "new_modules": ["taskplane/native_authority.py"],
            "design_edges": [
                "design->contract:design.codex-native-capability-inventory:provides",
                "contract:design.codex-native-capability-inventory->"
                "taskplane/native_authority.py:validated-by",
                "taskplane/native_authority.py->plan:blocks",
                "taskplane/native_authority.py->taskplane:consumed-by",
            ],
        }],
    }
    return design, plan


def test_complete_native_capability_map_is_required_by_design_and_plan():
    design, plan = _authority_design_and_plan()

    receipt = native_authority.validate_design_and_plan(design, plan)

    assert receipt["schema"] == "taskplane.acceptance-leaf-readiness/v1"
    assert receipt["status"] == "ready"
    assert receipt["outcome"] == "AC1"
    assert receipt["capability_count"] == 7
    assert len(receipt["fingerprint"]) == 64

    missing_mapping = deepcopy(design)
    missing_mapping["native_capability_inventory"]["rows"].pop()
    with pytest.raises(
            native_authority.NativeAuthorityError,
            match="seven ordered Codex-native capability"):
        native_authority.validate_design_and_plan(missing_mapping, plan)

    duplicate_owner = deepcopy(design)
    duplicate_owner["native_capability_inventory"]["rows"][0][
        "native_owner"] = "Taskplane spawn runner"
    with pytest.raises(
            native_authority.NativeAuthorityError,
            match="native owner embeds forbidden Taskplane authority"):
        native_authority.validate_design_and_plan(duplicate_owner, plan)

    codex_named_scheduler = deepcopy(design)
    codex_named_scheduler["native_capability_inventory"]["rows"][1][
        "native_owner"] = (
            "Codex facade backed by a Taskplane capacity reservation and "
            "admission scheduler"
        )
    with pytest.raises(
            native_authority.NativeAuthorityError,
            match="native owner embeds forbidden Taskplane authority"):
        native_authority.validate_design_and_plan(codex_named_scheduler, plan)

    merely_codex_named = deepcopy(design)
    merely_codex_named["native_capability_inventory"]["rows"][0][
        "native_owner"] = "not-really-codex"
    with pytest.raises(
            native_authority.NativeAuthorityError,
            match="closed Codex native owner"):
        native_authority.validate_design_and_plan(merely_codex_named, plan)

    duplicate_authority = deepcopy(design)
    duplicate_authority["native_capability_inventory"]["rows"][1][
        "taskplane_role"] = "Taskplane owns an admission queue"
    with pytest.raises(
            native_authority.NativeAuthorityError,
            match="forbidden duplicate authority"):
        native_authority.validate_design_and_plan(duplicate_authority, plan)

    omitted_row_restriction = deepcopy(design)
    omitted_row_restriction["native_capability_inventory"]["rows"][1][
        "forbidden_taskplane_authority"].remove("admission queue")
    omitted_row_restriction["native_capability_inventory"]["rows"][1][
        "taskplane_role"] = "Taskplane owns the admission queue"
    with pytest.raises(
            native_authority.NativeAuthorityError,
            match="forbidden duplicate authority: admission queue"):
        native_authority.validate_design_and_plan(
            omitted_row_restriction, plan)

    weakened_row_policy = deepcopy(design)
    weakened_row_policy["native_capability_inventory"]["rows"][1][
        "forbidden_taskplane_authority"].remove("admission queue")
    with pytest.raises(
            native_authority.NativeAuthorityError,
            match="closed canonical policy"):
        native_authority.validate_design_and_plan(weakened_row_policy, plan)

    invented_local_evidence = deepcopy(design)
    invented_local_evidence["native_capability_inventory"]["rows"][0][
        "sources"] = ["taskplane/native_authority.py:1-20"]
    with pytest.raises(
            native_authority.NativeAuthorityError,
            match="exact approved inventory"):
        native_authority.validate_design_and_plan(
            invented_local_evidence, plan)

    changed_line_claim = deepcopy(design)
    changed_line_claim["native_capability_inventory"]["rows"][0][
        "sources"][0] = (
            "skills/tp-go/references/codex-native-dispatch.md:1-57")
    with pytest.raises(
            native_authority.NativeAuthorityError,
            match="exact approved inventory"):
        native_authority.validate_design_and_plan(changed_line_claim, plan)

    arbitrary_valid_sha = deepcopy(design)
    arbitrary_valid_sha["native_capability_inventory"][
        "pinned_source_sha"] = "0" * 40
    with pytest.raises(
            native_authority.NativeAuthorityError,
            match="approved exact source SHA"):
        native_authority.validate_design_and_plan(arbitrary_valid_sha, plan)

    incomplete_boundary = deepcopy(design)
    incomplete_boundary["authority_boundary"][
        "forbidden_from_native_dispatch_roots"].remove("execution DAG")
    with pytest.raises(
            native_authority.NativeAuthorityError,
            match="closed Design boundary"):
        native_authority.validate_design_and_plan(incomplete_boundary, plan)

    unbound_plan = deepcopy(plan)
    unbound_plan["tasks"][0]["contracts"] = []
    with pytest.raises(
            native_authority.NativeAuthorityError,
            match="native capability contract"):
        native_authority.validate_design_and_plan(design, unbound_plan)


def test_static_delivery_roots_reject_forbidden_taskplane_authority():
    receipt = native_authority.validate_delivery_roots(ROOT)
    assert receipt["schema"] == "taskplane.native-delivery-authority/v1"
    assert receipt["status"] == "ready"
    assert receipt["forbidden_edge_count"] == 0

    duplicated = (
        "def select_ready_tasks(*args, **kwargs):\n"
        "    return []\n\n"
        "def _native_dispatch_intent(*args, **kwargs):\n"
        "    return {}\n\n"
        "def scheduler(*args, **kwargs):\n"
        "    return None\n\n"
        "def wave(ws):\n"
        "    select_ready_tasks([])\n"
        "    _native_dispatch_intent(ws)\n"
        "    return scheduler(ws)\n"
    )
    with pytest.raises(
            native_authority.NativeAuthorityError, match="scheduler"):
        native_authority.validate_delivery_roots(
            ROOT,
            source_overrides={"taskplane/loop.py": duplicated},
        )


def _wave_workspace(tmp_path: Path) -> str:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    (workspace / "README.md").write_text("native wave\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=workspace, check=True)
    subprocess.run([
        "git", "-c", "user.name=Taskplane", "-c",
        "user.email=taskplane@example.invalid", "commit", "-qm", "base",
    ], cwd=workspace, check=True)
    state = loop.init(
        str(workspace), "native-only execution", spec_path="spec.md",
        parallel=True,
    )
    state.update({
        "step": "execute",
        "tasks": [{
            "id": "t01", "scope": ["README.md"], "tests": "true",
            "deps": [], "status": "pending",
        }],
    })
    loop.save(str(workspace), state)
    return str(workspace)


def test_loop_wave_never_calls_stage_split_resume_or_execution_root_claim(
        tmp_path, monkeypatch):
    workspace = _wave_workspace(tmp_path)
    forbidden_calls: list[str] = []

    def forbidden(name):
        def refuse(*_args, **_kwargs):
            forbidden_calls.append(name)
            raise AssertionError(name)
        return refuse

    monkeypatch.setattr(
        loop, "_stage_loop_wave_dispatches", forbidden("split/resume"))
    monkeypatch.setattr(
        loop, "_stage_loop_dispatch", forbidden("per-agent-stage-dispatch"))
    monkeypatch.setattr(
        loop.runtime_storage, "claim_stage_execution_root_for_run",
        forbidden("execution-root-claim"),
    )
    monkeypatch.setattr(
        loop.tp, "stage_runtime_dispatch", forbidden("stage-runtime-dispatch"))

    authority = open_delivery_root(workspace)
    result = loop.wave(
        workspace, root_observation_authority=authority)

    assert "error" not in result, result
    assert [row["task"]["id"] for row in result["wave"]] == ["t01"]
    assert forbidden_calls == []
    assert all("stage_runtime_dispatch" not in row for row in result["wave"])
    assert not (loop.load(workspace) or {}).get("_stage_bindings")


def test_cut_ready_to_native_intent_fails_before_worktree_or_state(tmp_path):
    state = tmp_path / "state.json"
    state.write_text('{"revision":1}\n', encoding="utf-8")
    before = state.read_bytes()
    worktree = tmp_path / "worktree"
    severed = (
        "def select_ready_tasks(*args, **kwargs):\n"
        "    return []\n\n"
        "def wave(ws):\n"
        "    return select_ready_tasks([])\n"
    )

    with pytest.raises(
            native_authority.NativeAuthorityError,
            match="missing-required-native-edge.*native_dispatch_intent"):
        native_authority.validate_delivery_roots(
            ROOT,
            source_overrides={"taskplane/loop.py": severed},
        )

    assert state.read_bytes() == before
    assert not worktree.exists()
