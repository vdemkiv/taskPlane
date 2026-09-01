import ast
import fnmatch
import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REQUIREMENT_AUTHORITIES = {
    "contract:configuration.effective-settings",
    "contract:delivery.flow-initialization",
    "contract:validation.test-strategy",
    "contract:ci.authoritative-validation",
    "contract:lifecycle.owned-cleanup",
    "contract:release.protected-main-green",
    "resource:configuration.effective-settings-receipt",
    "resource:lifecycle.cleanup-receipt",
    "resource:delivery.wave-metrics",
}
DESIGN_DASHBOARD_AUTHORITIES = {
    "contract:delivery.canonical-dashboard-state",
    "contract:delivery.phase-dependency-graphs",
    "resource:delivery.dashboard-publication-receipt",
}
CRITERION_IDS = {
    "AC-SET1",
    "AC-SET2",
    "AC-SET3",
    "AC-SET4",
    "AC-SET5",
    "AC-TST1",
    "AC-TST2",
    "AC-TST3",
    "AC-CI1",
    "AC-CI2",
    "AC-CLN1",
    "AC-CLN2",
    "AC-P0",
    "AC-REL",
    "AC-MET",
    "AC-REG",
}
PROTECTED_SELECTORS = {
    "taskplane/tests/test_governance_invariants.py::TestGovernanceInvariants::test_unknown_mutation_capability_is_denied",
    "taskplane/tests/test_governance_invariants.py::TestGovernanceInvariants::test_em_and_signoff_require_full_review_evidence",
    "taskplane/tests/test_consolidated_authority.py::test_human_decisions_fail_closed_for_silence_ambiguity_stale_and_replay",
    "taskplane/tests/test_consolidated_authority.py::test_one_receipt_covers_all_ten_production_flow_entry_points",
    "taskplane/tests/test_windows_portability.py::TestArtifactsAreByteIdenticalAcrossHosts::test_atomic_write_json_emits_lf_on_every_host",
    "taskplane/tests/test_windows_portability.py::TestExternalStorePathsStayBounded::test_long_checkout_keys_keep_a_bounded_readable_prefix",
    "taskplane/tests/test_stage_cross_host.py::test_cross_host_surfaces_preserve_one_canonical_bounded_startup",
    "taskplane/tests/test_stage_cross_host.py::test_host_adapter_cannot_add_predecessor_runtime_context",
    "taskplane/tests/test_host_native_dashboard.py::test_codex_and_claude_project_equal_semantics_for_all_components",
    "taskplane/tests/test_host_native_compatibility.py::test_recovery_fixtures_keep_one_identity_and_ordered_audit",
    "taskplane/tests/test_host_native_compatibility.py::test_late_stale_duplicates_never_reproject_or_reopen_terminal_state",
    "taskplane/tests/test_host_native_compatibility.py::test_each_capability_falls_back_independently_without_losing_truth",
    "taskplane/tests/test_stage_bounded_views.py::test_legacy_status_remains_compatible_and_stage_view_is_only_additive",
    "taskplane/tests/test_stage_bounded_views.py::test_corrupt_v4_is_visible_and_fails_closed_without_opening_objects",
    "taskplane/tests/test_review_production_integration.py::test_publication_and_renderer_failures_are_stable_non_success",
    "taskplane/tests/test_worker_contract_lifecycle.py::test_every_worker_terminal_path_removes_active_slot",
    "taskplane/tests/test_worker_contract_lifecycle.py::test_authenticated_release_refuses_before_terminal_and_tampering",
    "taskplane/tests/test_worktree_cleanup.py::test_preservation_matrix_fails_closed",
    "taskplane/tests/test_worktree_cleanup.py::test_no_force_revalidation_preserves_last_moment_dirty_tree",
    "taskplane/tests/test_release_tags.py::TestItCatchesAMisplacedTag::test_a_tag_off_the_mainline_is_C2",
    "taskplane/tests/test_release_tags.py::TestCiWiring::test_the_leg_that_runs_it_fetches_full_history_and_tags",
    "taskplane/tests/test_release_provenance.py::TestTheArtifactNamesItsCommit::test_a_clean_build_records_the_commit",
    "taskplane/tests/test_release_provenance.py::TestADirtyTreeIsRefused::test_packaging_a_dirty_tree_raises",
    "taskplane/tests/test_release_provenance.py::TestBothPackagersUseIt::test_each_packager_writes_a_provenance_record",
}


def _json(relative_path):
    value = json.loads((ROOT / relative_path).read_text(encoding="utf-8"))
    assert isinstance(value, dict), relative_path
    return value


def _criterion_id(text):
    match = re.fullmatch(r"(AC-[A-Z0-9]+): .+", text)
    assert match, text
    return match.group(1)


def _tracked_and_untracked_files():
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    return {
        line
        for line in result.stdout.splitlines()
        if line and (ROOT / line).is_file()
    }


def _matches_scope(path, pattern):
    if pattern.endswith("/**"):
        root = pattern[:-3]
        return path == root or path.startswith(root + "/")
    return fnmatch.fnmatchcase(path, pattern)


def _selector_is_defined(selector):
    parts = selector.split("::")
    assert len(parts) in {2, 3}, selector
    tree = ast.parse((ROOT / parts[0]).read_text(encoding="utf-8"))
    if len(parts) == 2:
        return any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == parts[1]
            for node in tree.body
        )
    owner = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == parts[1]
        ),
        None,
    )
    return owner is not None and any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == parts[2]
        for node in owner.body
    )


def _assert_acyclic(tasks):
    dependencies = {task["id"]: set(task["deps"]) for task in tasks}
    assert all(dependencies.values()) or dependencies
    assert not set().union(*dependencies.values()) - set(dependencies)
    completed = set()
    while len(completed) < len(dependencies):
        ready = {
            task_id
            for task_id, deps in dependencies.items()
            if task_id not in completed and deps <= completed
        }
        assert ready, "Plan task dependency graph is cyclic"
        completed.update(ready)


def test_all_approved_modules_edges_contracts_depth_and_acceptance_are_realized():
    design = _json("design/contract.json")
    compatibility = _json("design/compatibility.json")
    plan = _json("plan/tasks.json")
    strategy = _json("taskplane/tests/fixtures/test-strategy/r0001.json")
    specification = (ROOT / "specs/spec.md").read_text(encoding="utf-8")
    components = (ROOT / "components.yaml").read_text(encoding="utf-8")

    assert {
        path.name for path in (ROOT / "design").iterdir() if path.is_file()
    } == {"compatibility.json", "contract.json", "design.md", "visual.html"}
    exclude_block = components.split("exclude:", 1)[1].split(
        "terminal_capability_custody:", 1
    )[0]
    assert re.findall(r"^  - ([^\s#]+)$", exclude_block, re.M) == [
        "corpus",
        "taskplane/tests/fixtures",
    ]
    assert "expanded_route_authority_custody:" in components

    graph = design["graph"]
    modules = graph["proposed_modules"]
    edges = graph["proposed_edges"]
    assert len(modules) == len(set(modules)) == 42
    assert len(edges) == 58
    assert design["modules"]["existing"] + design["modules"]["new"] == modules
    assert set(design["modules"]["existing"]).isdisjoint(
        design["modules"]["new"]
    )
    for module in modules:
        realized = ROOT if module == "(root)" else ROOT / module
        assert realized.exists(), f"approved module is unrealized: {module}"

    contract_rows = design["contracts"]
    contract_ids = [row["id"] for row in contract_rows]
    all_authorities = REQUIREMENT_AUTHORITIES | DESIGN_DASHBOARD_AUTHORITIES
    assert len(contract_ids) == len(set(contract_ids)) == 12
    assert set(contract_ids) == all_authorities
    assert all(
        re.fullmatch(r"(?:contract|resource):[a-z0-9.-]+", contract_id)
        for contract_id in contract_ids
    )
    assert all(
        row["relation"] == (
            "provides" if row["id"].startswith("resource:") else "changes"
        )
        and not row["id"].startswith(row["relation"] + ":")
        for row in contract_rows
    )
    known_nodes = set(modules) | all_authorities
    assert all(
        set(edge) == {"from", "to", "kind", "reason"}
        and edge["from"] in known_nodes
        and edge["to"] in known_nodes
        and str(edge["kind"]).strip()
        and str(edge["reason"]).strip()
        for edge in edges
    )

    # Product remains the one requirement-scope owner. Revised Design may add
    # dashboard authorities, but cannot rewrite or relation-prefix these nine.
    handoff = specification.split("## Contract handoff", 1)[1].split(
        "## Dependencies and open questions", 1
    )[0]
    declared = re.findall(r"^  - ((?:contract|resource):[^\s]+)$", handoff, re.M)
    assert len(declared) == len(set(declared)) == 9
    assert set(declared) == REQUIREMENT_AUTHORITIES

    depth = {
        "local_depth": 3,
        "boundary_mode": "contract-only",
        "contract_depth": 1,
        "requirement_depth": 1,
    }
    assert graph["depth_policy"] == depth
    assert plan["impact"]["policy"] == depth
    assert all(task["impact_policy"] == depth for task in plan["tasks"])
    assert plan["impact"]["unknown_modules"] == []
    assert plan["impact"]["scan_quality"] == "complete"
    assert plan["impact"]["truncated"] is False
    assert plan["impact"]["depth_truncated"] is False

    criteria = [row["criterion"] for row in design["acceptance_map"]]
    criterion_ids = [_criterion_id(criterion) for criterion in criteria]
    assert len(criteria) == len(set(criteria)) == 16
    assert set(criterion_ids) == CRITERION_IDS
    planned_criteria = [
        criterion
        for task in plan["tasks"]
        for criterion in task["criteria"]
        if criterion.startswith("AC-")
    ]
    assert Counter(planned_criteria) == Counter(criteria)
    assert {
        row["id"] for row in strategy["acceptance_criteria"]
    } == CRITERION_IDS

    # The Plan records the current human authority and release slice. Runtime
    # approval receipts own freshness; this test checks realized semantics.
    assert plan["design_contract_current"] is True
    assert plan["plan_authority"] == (
        "human:vdemkiv approved zero-lens Build at the consolidated Plan gate; "
        "Taskplane 2.18.4 compatibility projection only"
    )
    assert compatibility["window"]["current"] == "2.18.4"
    assert compatibility["window"]["unknown_generation"] == "refuse"
    assert compatibility["baseline_rebind"]["next_generation"] == "2.18.4"
    assert all(
        row["release"].startswith(("refuse-", "historical-"))
        for row in compatibility["matrix"]
        if row["plugin"] != "2.18.4" or row["host"] != "2.18.4"
    )

    tasks = plan["tasks"]
    waves = plan["waves"]
    assert len(tasks) == len({task["id"] for task in tasks}) == 17
    assert len(waves) == len({wave["id"] for wave in waves}) == 10
    _assert_acyclic(tasks)
    wave_index = {}
    for index, wave in enumerate(waves):
        assert wave["parallel"] and str(wave["serialization"]).strip()
        for task_id in wave["parallel"]:
            assert task_id not in wave_index
            wave_index[task_id] = index
        assert all(wave_index[task_id] < index for task_id in wave.get("after", []))
    assert set(wave_index) == {task["id"] for task in tasks}
    assert all(
        wave_index[dependency] < wave_index[task["id"]]
        for task in tasks
        for dependency in task["deps"]
    )

    # FINAL-CONFORMANCE is a read-only join across the four authority trees.
    # All producer slices before it retain exactly one expanded file owner.
    final = next(task for task in tasks if task["id"] == "FINAL-CONFORMANCE")
    assert final["scope"] == [
        "components.yaml",
        "design/**",
        "plan/**",
        "specs/**",
        "taskplane/tests/test_design_conformance_r0001.py",
    ]
    owned = defaultdict(list)
    for path in _tracked_and_untracked_files():
        for task in tasks:
            if task["id"] == "FINAL-CONFORMANCE":
                continue
            if any(_matches_scope(path, pattern) for pattern in task["scope"]):
                owned[path].append(task["id"])
    assert {
        path: owners for path, owners in owned.items() if len(owners) > 1
    } == {}

    ac_reg = next(
        row for row in design["acceptance_map"] if row["criterion"].startswith("AC-REG:")
    )
    assert set(ac_reg["tests"]) == PROTECTED_SELECTORS
    assert all(_selector_is_defined(selector) for selector in PROTECTED_SELECTORS)
