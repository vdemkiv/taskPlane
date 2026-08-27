"""R-0006 S4/S7: measure every import SCC and reject cycle growth."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "taskplane" / "tests" / "fixtures" / "import-cycles.json"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
ACTIVE_SCANNER = """\
def check_inventory():
    pass

def verify_history():
    pass

def main():
    pass

OPTIONS = ("--check", "--verify-history")
"""
MARKER_PRESERVING_NOOP_SCANNER = """\
def check_inventory(*args, **kwargs):
    return {"status": "pass"}

def verify_history(*args, **kwargs):
    return {"status": "pass"}

def main(*args, **kwargs):
    return 0

OPTIONS = ("--check", "--verify-history")
"""
ACTIVE_WORKFLOW = WORKFLOW.read_text(encoding="utf-8")
sys.path.insert(0, str(ROOT))

from taskplane import import_cycles as cycles  # noqa: E402


def _write_modules(root: Path, sources: dict[str, str]) -> None:
    package = root / "taskplane"
    package.mkdir(parents=True, exist_ok=True)
    for name, source in sources.items():
        (package / f"{name}.py").write_text(source, encoding="utf-8")


def _inventory(root: Path, revision: str = "fixture-revision") -> dict:
    return cycles.build_inventory(root, source_revision=revision)


def _row(inventory: dict, *members: str) -> dict:
    wanted = sorted(f"taskplane.{member}" for member in members)
    return next(row for row in inventory["sccs"]
                if row["members"] == wanted)


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "cycles@example.test"],
                   cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Cycle Fixture"],
                   cwd=root, check=True)


def _commit(root: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=root, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True,
        capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()


def _start_history_fixture(
        root: Path, *, active_workflow: str = ACTIVE_WORKFLOW,
) -> tuple[Path, Path, str]:
    _init_repo(root)
    _write_modules(root, {
        "lens": "def f():\n    import review\n",
        "review": "import lens\n",
        "depgraph": "def f():\n    import decompose\n",
        "decompose": "import depgraph\nimport lens_signals\n",
        "lens_signals": "import depgraph\n",
        "taskplane_lite": "def f():\n    import depgraph\n",
    })
    before = _commit(root, "before ratchet")
    policy = root / "taskplane" / "tests" / "fixtures" / \
        "import-cycles.json"
    policy.parent.mkdir(parents=True)
    policy.write_text(cycles.canonical_json(cycles.build_inventory(
        root, source_revision=before)), encoding="utf-8")
    (root / "taskplane" / "import_cycles.py").write_text(
        ACTIVE_SCANNER, encoding="utf-8")
    workflow = root / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    active = active_workflow
    workflow.write_text(active, encoding="utf-8")
    _commit(root, "activate ratchet")
    return policy, workflow, active


def _workflow_variant(
        step_fields: list[str], *, job_if: str | None = None,
        job_shell: str | None = None,
        workflow_shell: str | None = None) -> str:
    workflow_defaults = (
        "defaults:\n"
        "  run:\n"
        f"    shell: {workflow_shell}\n"
        if workflow_shell is not None else "")
    job_condition = f"    if: {job_if}\n" if job_if is not None else ""
    job_defaults = (
        "    defaults:\n"
        "      run:\n"
        f"        shell: {job_shell}\n"
        if job_shell is not None else "")
    fields = "\n".join(
        [f"      - {step_fields[0]}"]
        + [f"        {field}" for field in step_fields[1:]])
    return (
        "name: CI\n\n"
        "on:\n"
        "  push:\n"
        "    branches: [main]\n"
        "  pull_request:\n"
        "    branches: [main]\n\n"
        f"{workflow_defaults}"
        "jobs:\n"
        "  wave3-contracts:\n"
        "    name: R-0006 graph + CLI contracts\n"
        f"{job_condition}"
        "    runs-on: ubuntu-latest\n"
        f"{job_defaults}"
        "    steps:\n"
        "      - uses: actions/checkout@"
        "3d3c42e5aac5ba805825da76410c181273ba90b1\n"
        "        with:\n"
        "          fetch-depth: 0\n"
        "          persist-credentials: false\n"
        "      - uses: actions/setup-python@"
        "5fda3b95a4ea91299a34e894583c3862153e4b97\n"
        "        with:\n"
        "          python-version: \"3.12\"\n"
        f"{fields}\n"
    )


def _wave3_variant(old: str, new: str) -> str:
    start = ACTIVE_WORKFLOW.index("  wave3-contracts:\n")
    end = ACTIVE_WORKFLOW.index("\n  pushed-sha-proof:\n", start)
    job = ACTIVE_WORKFLOW[start:end]
    if old not in job:
        raise AssertionError(f"wave3 fixture anchor is missing: {old!r}")
    return ACTIVE_WORKFLOW[:start] + job.replace(old, new, 1) + \
        ACTIVE_WORKFLOW[end:]


def test_ast_tarjan_inventory_is_complete_deterministic_and_file_level(
        tmp_path: Path) -> None:
    _write_modules(tmp_path, {
        "a": "def load():\n    import b\n    from . import c\n",
        "b": "from taskplane import a\n",
        "c": "from taskplane.a import load\n",
        "d": "import e\n",
        "e": "from . import d\n",
        "leaf": "import a\n",
    })

    first = _inventory(tmp_path)
    second = _inventory(tmp_path)

    assert cycles.canonical_json(first) == cycles.canonical_json(second)
    assert [row["members"] for row in first["sccs"]] == [
        ["taskplane.a", "taskplane.b", "taskplane.c"],
        ["taskplane.d", "taskplane.e"],
    ]
    abc = _row(first, "a", "b", "c")
    assert abc["internal_edges"] == [
        ["taskplane.a", "taskplane.b"],
        ["taskplane.a", "taskplane.c"],
        ["taskplane.b", "taskplane.a"],
        ["taskplane.c", "taskplane.a"],
    ]
    assert abc["member_count"] == 3
    assert abc["edge_count"] == 4
    assert abc["physical_loc"] == 5


def test_syntax_error_fails_named_instead_of_dropping_a_node(
        tmp_path: Path) -> None:
    _write_modules(tmp_path, {"broken": "def nope(:\n"})

    with pytest.raises(cycles.CycleScanError, match=r"taskplane/broken\.py.*line 1"):
        _inventory(tmp_path)


def test_external_imported_symbols_and_self_imports_do_not_make_sccs(
        tmp_path: Path) -> None:
    _write_modules(tmp_path, {
        "a": "from external import b\nimport a\n",
        "b": "from another_external import a\n",
    })

    assert _inventory(tmp_path)["sccs"] == []


def test_known_scc_may_shrink(tmp_path: Path) -> None:
    _write_modules(tmp_path, {
        "a": "import b\nimport c\n",
        "b": "import a\n",
        "c": "import a\n",
    })
    policy = _inventory(tmp_path, "before")
    _write_modules(tmp_path, {
        "a": "import b\n",
        "b": "import a\n",
        "c": "VALUE = 1\n",
    })

    result = cycles.check_inventory(policy, _inventory(tmp_path, "after"))

    assert result["status"] == "pass"
    assert result["violations"] == []
    assert result["delta"]["removed_members"] == ["taskplane.c"]


def test_one_known_scc_may_split_into_multiple_smaller_sccs(
        tmp_path: Path) -> None:
    _write_modules(tmp_path, {
        "a": "import b\n", "b": "import a\nimport c\n",
        "c": "import d\n", "d": "import c\nimport a\n",
    })
    policy = _inventory(tmp_path, "before")
    _write_modules(tmp_path, {
        "a": "import b\n", "b": "import a\n",
        "c": "import d\n", "d": "import c\n",
    })

    result = cycles.check_inventory(policy, _inventory(tmp_path, "after"))

    assert result["status"] == "pass"
    assert [row["members"] for row in result["current_sccs"]] == [
        ["taskplane.a", "taskplane.b"],
        ["taskplane.c", "taskplane.d"],
    ]


def test_split_descendants_cannot_exceed_the_parent_aggregate_loc_bound(
        tmp_path: Path) -> None:
    _write_modules(tmp_path, {
        "a": "import b\n", "b": "import a\nimport c\n",
        "c": "import d\n", "d": "import c\nimport a\n",
    })
    policy = _inventory(tmp_path, "before")
    _write_modules(tmp_path, {
        "a": "import b\n# growth a\n", "b": "import a\n# growth b\n",
        "c": "import d\n# growth c\n", "d": "import c\n# growth d\n",
    })

    result = cycles.check_inventory(policy, _inventory(tmp_path, "after"))

    assert [row["code"] for row in result["violations"]] == [
        "physical-loc-growth"
    ]
    violation = result["violations"][0]
    assert violation["measured"]["physical_loc"] == 8
    assert violation["bounds"]["physical_loc"] == 6
    assert violation["affected_modules"] == [
        "taskplane.a", "taskplane.b", "taskplane.c", "taskplane.d"
    ]


def test_new_scc_and_new_member_fail_with_measured_diagnostics(
        tmp_path: Path) -> None:
    _write_modules(tmp_path, {"a": "import b\n", "b": "import a\n"})
    policy = _inventory(tmp_path, "before")
    _write_modules(tmp_path, {
        "a": "import b\nimport c\n",
        "b": "import a\n",
        "c": "import a\n",
        "x": "import y\n",
        "y": "import x\n",
    })

    result = cycles.check_inventory(policy, _inventory(tmp_path, "after"))
    rendered = cycles.format_failures(result)

    assert result["status"] == "fail"
    assert {row["code"] for row in result["violations"]} == {
        "new-cyclic-member", "new-scc",
    }
    assert "taskplane.c" in rendered
    assert "taskplane.x" in rendered and "taskplane.y" in rendered
    assert "members=3" in rendered and "edges=4" in rendered
    assert "physical_loc=4" in rendered


def test_new_internal_edge_is_rejected_even_without_member_or_loc_growth(
        tmp_path: Path) -> None:
    _write_modules(tmp_path, {
        "a": "import b\nimport sys\n",
        "b": "import c\nimport a\n",
        "c": "import a\n",
    })
    policy = _inventory(tmp_path, "before")
    _write_modules(tmp_path, {
        "a": "import b\nimport c\n",
        "b": "import c\nimport sys\n",
        "c": "import a\n",
    })

    result = cycles.check_inventory(policy, _inventory(tmp_path, "after"))
    rendered = cycles.format_failures(result)

    assert [row["code"] for row in result["violations"]] == [
        "new-internal-edge"
    ]
    assert "taskplane.a -> taskplane.c" in rendered
    assert "members=3" in rendered and "edges=4" in rendered


def test_physical_loc_growth_is_rejected_and_reports_bound(
        tmp_path: Path) -> None:
    _write_modules(tmp_path, {"a": "import b\n", "b": "import a\n"})
    policy = _inventory(tmp_path, "before")
    (tmp_path / "taskplane" / "a.py").write_text(
        "import b\n\n# unapproved growth\n", encoding="utf-8")

    result = cycles.check_inventory(policy, _inventory(tmp_path, "after"))
    rendered = cycles.format_failures(result)

    assert [row["code"] for row in result["violations"]] == [
        "physical-loc-growth"
    ]
    assert "physical_loc=4" in rendered
    assert "; bound members=2 edges=2 physical_loc=2" in rendered


@pytest.mark.parametrize("mutation", [
    lambda policy: policy.update(schema="wrong"),
    lambda policy: policy["sccs"][0].update(member_count=99),
    lambda policy: policy["sccs"][0]["members"].reverse(),
    lambda policy: policy["sccs"][0]["internal_edges"].append(
        ["taskplane.a", "taskplane.missing"]),
    lambda policy: policy["sccs"].append(dict(policy["sccs"][0])),
])
def test_malformed_policy_fails_closed(tmp_path: Path, mutation) -> None:
    _write_modules(tmp_path, {"a": "import b\n", "b": "import a\n"})
    policy = _inventory(tmp_path, "before")
    mutation(policy)

    with pytest.raises(cycles.CyclePolicyError):
        cycles.check_inventory(policy, _inventory(tmp_path, "after"))


def test_invalid_policy_json_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(cycles.CyclePolicyError, match="JSONDecodeError"):
        cycles.load_policy(path)


def test_checked_in_policy_is_exact_measured_wave4_start_inventory() -> None:
    policy = cycles.load_policy(POLICY_PATH)
    measured = cycles.build_inventory_at_revision(ROOT,
                                                  policy["source_revision"])

    assert cycles.canonical_json(measured) == cycles.canonical_json(policy)
    assert cycles.check_inventory(policy, _inventory(
        ROOT, cycles.git_revision(ROOT)))["status"] == "pass"


def test_target_cut_edge_set_is_exactly_closed() -> None:
    assert cycles.TARGET_CUT_EDGES == (
        ("taskplane.lens", "taskplane.review"),
        ("taskplane.depgraph", "taskplane.decompose"),
        ("taskplane.decompose", "taskplane.depgraph"),
        ("taskplane.decompose", "taskplane.lens_signals"),
        ("taskplane.taskplane_lite", "taskplane.depgraph"),
    )


def test_trusted_scanner_predecessor_receipts_are_exactly_closed() -> None:
    assert cycles.TRUSTED_SCANNER_PREDECESSOR_SHA256S == frozenset({
        "c89eddc3d2ed09846b63495a31f927e8678db2052ffe47bca7795636b1d787b0",
        "1728a688ffb8a6e09f7410c9d6ba3da88ec8bfc0590b377cdf5fe7b7d8792752",
    })
    assert cycles.TRUSTED_WORKFLOW_PREDECESSOR_SHA256S == frozenset({
        "ad14a00ec79956f401d3c9151fe106c4997959f2a0762061c3a31eb9765b0b45",
    })


def test_history_proof_finds_activation_before_all_five_cuts(
        tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _write_modules(tmp_path, {
        "lens": "def f():\n    import review\n",
        "review": "import lens\n",
        "depgraph": "def f():\n    import decompose\n",
        "decompose": "import depgraph\ndef f():\n    import lens_signals\n",
        "lens_signals": "import depgraph\n",
        "taskplane_lite": "def f():\n    import depgraph\n",
    })
    before = _commit(tmp_path, "before ratchet")
    policy_path = tmp_path / "taskplane" / "tests" / "fixtures" / \
        "import-cycles.json"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(
        cycles.canonical_json(cycles.build_inventory(
            tmp_path, source_revision=before)), encoding="utf-8")
    (tmp_path / "taskplane" / "import_cycles.py").write_text(
        ACTIVE_SCANNER, encoding="utf-8")
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(ACTIVE_WORKFLOW, encoding="utf-8")
    activation = _commit(tmp_path, "activate ratchet")

    proof = cycles.verify_history(tmp_path, policy_path)

    assert proof["status"] == "pass"
    assert proof["activation_revision"] == activation
    assert proof["seal_revision"] == activation
    assert proof["measurement_revision"] == before
    assert proof["target_edges"] == [list(edge)
                                      for edge in cycles.TARGET_CUT_EDGES]

    (tmp_path / "README.md").write_text("unrelated\n", encoding="utf-8")
    _commit(tmp_path, "ordinary unrelated commit")
    assert cycles.verify_history(tmp_path, policy_path)["status"] == "pass"

    (tmp_path / "taskplane" / "lens.py").write_text(
        "def f():\n    return None\n", encoding="utf-8")
    _commit(tmp_path, "first cut")
    assert cycles.verify_history(tmp_path, policy_path)["status"] == "pass"


def test_history_proof_handles_no_ff_cut_merge(tmp_path: Path) -> None:
    policy, _, _ = _start_history_fixture(tmp_path)
    base_branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=tmp_path, check=True,
        capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()
    subprocess.run(["git", "switch", "-qc", "cut-branch"], cwd=tmp_path,
                   check=True)
    (tmp_path / "taskplane" / "lens.py").write_text(
        "def f():\n    return None\n", encoding="utf-8")
    _commit(tmp_path, "cut target edge on branch")
    subprocess.run(["git", "switch", "-q", base_branch], cwd=tmp_path,
                   check=True)
    subprocess.run(
        ["git", "merge", "--no-ff", "-qm", "merge protected cut",
         "cut-branch"],
        cwd=tmp_path, check=True,
    )

    assert cycles.verify_history(tmp_path, policy)["status"] == "pass"


def test_history_proof_allows_ordinary_evolution_before_seal(
        tmp_path: Path) -> None:
    policy, workflow, active = _start_history_fixture(tmp_path)
    scanner = tmp_path / "taskplane" / "import_cycles.py"
    workflow.write_text(active + "# pre-seal workflow comment\n", encoding="utf-8")
    _commit(tmp_path, "ordinary pre-seal workflow evolution")
    scanner.write_text(
        ACTIVE_SCANNER + "# sealed scanner revision\n", encoding="utf-8")
    workflow.write_text(active, encoding="utf-8")
    seal = _commit(tmp_path, "activate content-addressed seal")

    proof = cycles.verify_history(tmp_path, policy)

    assert proof["status"] == "pass"
    assert proof["seal_revision"] == seal


def test_history_proof_allows_only_the_exact_versioned_workflow_transitions(
        tmp_path: Path) -> None:
    full_history_checkout = (
        "        with:\n"
        "          fetch-depth: 0\n"
        "          persist-credentials: false\n"
    )
    forward_release_step = (
        "\n"
        "      # R-0001 forward-only release integration. The 3.12 leg already owns\n"
        "      # the complete suite, so this adds one deterministic package/runtime\n"
        "      # proof without replaying it on the compatibility smoke legs.\n"
        "      - name: Verify forward-release manifests, archives, and runtime\n"
        "        if: matrix.python == '3.12'\n"
        "        run: python scripts/ci_evals.py --verify-release-surface --json\n"
    )
    assert forward_release_step in ACTIVE_WORKFLOW
    previous_workflow = ACTIVE_WORKFLOW.replace(full_history_checkout, "", 1)
    assert cycles.workflow_seal_digest(previous_workflow) in \
        cycles.TRUSTED_WORKFLOW_PREDECESSOR_SHA256S
    sealed_workflow = previous_workflow.replace(forward_release_step, "", 1)
    assert cycles.workflow_seal_digest(sealed_workflow) == \
        cycles.SEALED_WORKFLOW_SHA256
    policy, workflow, _ = _start_history_fixture(
        tmp_path, active_workflow=sealed_workflow)
    (tmp_path / "taskplane" / "lens.py").write_text(
        "def f():\n    return None\n", encoding="utf-8")
    _commit(tmp_path, "cut protected edge")
    workflow.write_text(previous_workflow, encoding="utf-8")
    _commit(tmp_path, "advance to forward-release workflow seal")
    workflow.write_text(ACTIVE_WORKFLOW, encoding="utf-8")
    _commit(tmp_path, "advance to full-history workflow seal")

    assert cycles.verify_history(tmp_path, policy)["status"] == "pass"


def test_history_proof_rejects_trusted_head_workflow_hash_mismatch(
        tmp_path: Path) -> None:
    policy, workflow, active = _start_history_fixture(tmp_path)
    workflow.write_text(active.replace(
        "      - name: Import-cycle inventory, bounds, and activation order\n",
        "      # protected raw-byte mutation\n"
        "      - name: Import-cycle inventory, bounds, and activation order\n",
        1), encoding="utf-8")
    _commit(tmp_path, "mutate trusted HEAD workflow")

    with pytest.raises(
            cycles.CycleHistoryError,
            match="trusted HEAD workflow hash mismatch"):
        cycles.verify_history(tmp_path, policy)


def test_history_proof_rejects_seal_after_cut_and_restore(
        tmp_path: Path) -> None:
    policy, _, _ = _start_history_fixture(tmp_path)
    lens = tmp_path / "taskplane" / "lens.py"
    lens.write_text("def f():\n    return None\n", encoding="utf-8")
    cut = _commit(tmp_path, "cut protected edge before seal")
    lens.write_text("def f():\n    import review\n", encoding="utf-8")
    _commit(tmp_path, "restore protected edge")
    scanner = tmp_path / "taskplane" / "import_cycles.py"
    scanner.write_text(
        ACTIVE_SCANNER + "# post-cut scanner seal\n", encoding="utf-8")
    _commit(tmp_path, "attempt seal after restored cut")

    with pytest.raises(
            cycles.CycleHistoryError,
            match=rf"strictly precede.*{cut}"):
        cycles.verify_history(tmp_path, policy)


def test_history_proof_rejects_policy_bound_raise(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _write_modules(tmp_path, {
        "lens": "import review\n", "review": "import lens\n",
        "depgraph": "import decompose\n",
        "decompose": "import depgraph\nimport lens_signals\n",
        "lens_signals": "import depgraph\n",
        "taskplane_lite": "import depgraph\n",
    })
    before = _commit(tmp_path, "before ratchet")
    policy_path = tmp_path / "taskplane" / "tests" / "fixtures" / \
        "import-cycles.json"
    policy_path.parent.mkdir(parents=True)
    policy = cycles.build_inventory(tmp_path, source_revision=before)
    policy_path.write_text(cycles.canonical_json(policy), encoding="utf-8")
    (tmp_path / "taskplane" / "import_cycles.py").write_text(
        ACTIVE_SCANNER, encoding="utf-8")
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(ACTIVE_WORKFLOW, encoding="utf-8")
    _commit(tmp_path, "activate ratchet")

    policy["sccs"][0]["physical_loc"] += 100
    policy_path.write_text(cycles.canonical_json(policy), encoding="utf-8")
    _commit(tmp_path, "raise bound")

    with pytest.raises(cycles.CycleHistoryError, match="policy growth"):
        cycles.verify_history(tmp_path, policy_path)


def test_history_proof_rejects_intermediate_raise_then_restore(
        tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _write_modules(tmp_path, {
        "lens": "import review\n", "review": "import lens\n",
        "depgraph": "import decompose\n",
        "decompose": "import depgraph\nimport lens_signals\n",
        "lens_signals": "import depgraph\n",
        "taskplane_lite": "import depgraph\n",
    })
    before = _commit(tmp_path, "before ratchet")
    policy_path = tmp_path / "taskplane" / "tests" / "fixtures" / \
        "import-cycles.json"
    policy_path.parent.mkdir(parents=True)
    original = cycles.build_inventory(tmp_path, source_revision=before)
    policy_path.write_text(cycles.canonical_json(original), encoding="utf-8")
    (tmp_path / "taskplane" / "import_cycles.py").write_text(
        ACTIVE_SCANNER, encoding="utf-8")
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(ACTIVE_WORKFLOW, encoding="utf-8")
    _commit(tmp_path, "activate ratchet")

    # Make a real larger tree, then bless its exact inventory in the next
    # commit.  Restoring the original policy later must not hide this bypass.
    lens = tmp_path / "taskplane" / "lens.py"
    lens.write_text("import review\n# temporary growth\n", encoding="utf-8")
    grown_revision = _commit(tmp_path, "grow cycle")
    raised = cycles.build_inventory(tmp_path, source_revision=grown_revision)
    policy_path.write_text(cycles.canonical_json(raised), encoding="utf-8")
    _commit(tmp_path, "temporarily raise bound")
    lens.write_text("import review\n", encoding="utf-8")
    _commit(tmp_path, "remove temporary growth")
    policy_path.write_text(cycles.canonical_json(original), encoding="utf-8")
    _commit(tmp_path, "restore original policy")

    with pytest.raises(
            cycles.CycleHistoryError,
            match="cycle ratchet violation|policy growth"):
        cycles.verify_history(tmp_path, policy_path)


def test_history_proof_accepts_only_an_exact_repaired_violation_span(
        tmp_path: Path) -> None:
    policy, _, _ = _start_history_fixture(tmp_path)
    lens = tmp_path / "taskplane" / "lens.py"
    lens.write_text("def f():\n    import review\n    import extra\n",
                    encoding="utf-8")
    (tmp_path / "taskplane" / "extra.py").write_text(
        "import lens\n", encoding="utf-8")
    introduced = _commit(tmp_path, "introduce one cyclic member")
    (tmp_path / "README.md").write_text(
        "violation still present\n", encoding="utf-8")
    lingering = _commit(tmp_path, "ordinary commit during repair")
    lens.write_text("def f():\n    import review\n", encoding="utf-8")
    repaired = _commit(tmp_path, "remove the cyclic member")

    observations = []
    for revision in (introduced, lingering):
        baseline = json.loads(cycles._show_optional(
            tmp_path, revision, cycles.POLICY_RELATIVE))
        measured = cycles._inventory_from_sources(
            cycles._revision_sources(tmp_path, revision),
            source_revision=revision)
        result = cycles.check_inventory(baseline, measured)
        assert result["status"] == "fail"
        observations.append({
            "revision": revision, "violations": result["violations"]})

    ledger = {
        "schema": cycles.HISTORY_RESOLUTIONS_SCHEMA,
        "resolutions": [{
            "introduced_revision": introduced,
            "repaired_revision": repaired,
            "commit_count": 2,
            "violation_codes": ["new-cyclic-member"],
            "affected_modules": ["taskplane.extra"],
            "history_sha256": cycles.history_resolution_digest(observations),
            "reason": (
                "The added module temporarily joined the protected SCC; the "
                "named repair removed its reverse edge, and this receipt binds "
                "every failing revision rather than permitting a pattern."),
        }],
    }
    resolution_path = tmp_path / cycles.HISTORY_RESOLUTIONS_RELATIVE
    resolution_path.write_text(
        json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    proof = cycles.verify_history(tmp_path, policy)
    assert proof["resolved_history"] == [{
        "introduced_revision": introduced,
        "repaired_revision": repaired,
        "commit_count": 2,
        "history_sha256": ledger["resolutions"][0]["history_sha256"],
    }]

    ledger["resolutions"][0]["history_sha256"] = "0" * 64
    resolution_path.write_text(
        json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(
            cycles.CycleHistoryError, match="exact measured violations"):
        cycles.verify_history(tmp_path, policy)


def test_history_proof_rejects_a_cut_in_the_activation_commit(
        tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _write_modules(tmp_path, {
        "lens": "import review\n", "review": "import lens\n",
        "depgraph": "import decompose\n",
        "decompose": "import depgraph\nimport lens_signals\n",
        "lens_signals": "import depgraph\n",
        "taskplane_lite": "import depgraph\n",
    })
    before = _commit(tmp_path, "before ratchet")
    policy_path = tmp_path / "taskplane" / "tests" / "fixtures" / \
        "import-cycles.json"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(cycles.canonical_json(cycles.build_inventory(
        tmp_path, source_revision=before)), encoding="utf-8")
    (tmp_path / "taskplane" / "import_cycles.py").write_text(
        ACTIVE_SCANNER, encoding="utf-8")
    (tmp_path / "taskplane" / "lens.py").write_text(
        "def no_review_import():\n    return None\n", encoding="utf-8")
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(ACTIVE_WORKFLOW, encoding="utf-8")
    _commit(tmp_path, "activation wrongly includes cut")

    with pytest.raises(
            cycles.CycleHistoryError,
            match=r"taskplane\.lens -> taskplane\.review"):
        cycles.verify_history(tmp_path, policy_path)


def test_history_proof_rejects_disable_cut_restore_gap(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _write_modules(tmp_path, {
        "lens": "def f():\n    import review\n",
        "review": "import lens\n",
        "depgraph": "def f():\n    import decompose\n",
        "decompose": "import depgraph\nimport lens_signals\n",
        "lens_signals": "import depgraph\n",
        "taskplane_lite": "def f():\n    import depgraph\n",
    })
    before = _commit(tmp_path, "before ratchet")
    policy_path = tmp_path / "taskplane" / "tests" / "fixtures" / \
        "import-cycles.json"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(cycles.canonical_json(cycles.build_inventory(
        tmp_path, source_revision=before)), encoding="utf-8")
    (tmp_path / "taskplane" / "import_cycles.py").write_text(
        ACTIVE_SCANNER, encoding="utf-8")
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    active_workflow = ACTIVE_WORKFLOW
    workflow.write_text(active_workflow, encoding="utf-8")
    _commit(tmp_path, "activate ratchet")

    workflow.write_text("name: enforcement disabled\n", encoding="utf-8")
    disabled = _commit(tmp_path, "disable CI")
    (tmp_path / "taskplane" / "lens.py").write_text(
        "def f():\n    return None\n", encoding="utf-8")
    _commit(tmp_path, "cut while disabled")
    workflow.write_text(active_workflow, encoding="utf-8")
    _commit(tmp_path, "restore CI")

    with pytest.raises(
            cycles.CycleHistoryError,
            match=rf"workflow inactive at revision {disabled}"):
        cycles.verify_history(tmp_path, policy_path)


@pytest.mark.parametrize(("case", "disabled_workflow"), [
    (
        "commented-command",
        _workflow_variant([
            "name: disabled",
            "# run: python3 taskplane/import_cycles.py --check --verify-history",
        ]),
    ),
    (
        "job-if-false",
        _workflow_variant([
            "run: python3 taskplane/import_cycles.py --check --verify-history",
        ], job_if="false"),
    ),
    (
        "step-if-false",
        _workflow_variant([
            "if: false",
            "run: python3 taskplane/import_cycles.py --check --verify-history",
        ]),
    ),
    (
        "renamed-runner",
        _workflow_variant([
            "runner: python3 taskplane/import_cycles.py --check --verify-history",
        ]),
    ),
    (
        "ignored-exit",
        _workflow_variant([
            "run: python3 taskplane/import_cycles.py --check --verify-history "
            "|| true",
        ]),
    ),
    (
        "continue-on-error",
        _workflow_variant([
            "continue-on-error: true",
            "run: python3 taskplane/import_cycles.py --check --verify-history",
        ]),
    ),
    (
        "inert-true-shell",
        _workflow_variant([
            "name: disabled by custom shell",
            "shell: true {0}",
            "run: python3 taskplane/import_cycles.py --check --verify-history",
        ]),
    ),
    (
        "inert-echo-shell",
        _workflow_variant([
            "name: disabled by custom shell",
            "shell: echo {0}",
            "run: python3 taskplane/import_cycles.py --check --verify-history",
        ]),
    ),
    (
        "unknown-step-shell",
        _workflow_variant([
            "shell: unknown-shell {0}",
            "run: python3 taskplane/import_cycles.py --check --verify-history",
        ]),
    ),
    (
        "inert-job-default-shell",
        _workflow_variant([
            "run: python3 taskplane/import_cycles.py --check --verify-history",
        ], job_shell="true {0}"),
    ),
    (
        "inert-workflow-default-shell",
        _workflow_variant([
            "run: python3 taskplane/import_cycles.py --check --verify-history",
        ], workflow_shell="echo {0}"),
    ),
    (
        "script-long-help",
        _workflow_variant([
            "run: python3 taskplane/import_cycles.py --check --verify-history "
            "--help",
        ]),
    ),
    (
        "script-short-help",
        _workflow_variant([
            "run: python3 taskplane/import_cycles.py --check --verify-history -h",
        ]),
    ),
    (
        "interpreter-long-help",
        _workflow_variant([
            "run: python3 --help taskplane/import_cycles.py --check "
            "--verify-history",
        ]),
    ),
    (
        "interpreter-short-help",
        _workflow_variant([
            "run: python3 -h taskplane/import_cycles.py --check "
            "--verify-history",
        ]),
    ),
    (
        "interpreter-question-help",
        _workflow_variant([
            "run: python3 -? taskplane/import_cycles.py --check "
            "--verify-history",
        ]),
    ),
    (
        "interpreter-short-version",
        _workflow_variant([
            "run: python3 -V taskplane/import_cycles.py --check "
            "--verify-history",
        ]),
    ),
    (
        "interpreter-verbose-version",
        _workflow_variant([
            "run: python3 -VV taskplane/import_cycles.py --check "
            "--verify-history",
        ]),
    ),
    (
        "interpreter-long-version",
        _workflow_variant([
            "run: python3 --version taskplane/import_cycles.py --check "
            "--verify-history",
        ]),
    ),
    (
        "interpreter-help-env",
        _workflow_variant([
            "run: python3 --help-env taskplane/import_cycles.py --check "
            "--verify-history",
        ]),
    ),
    (
        "interpreter-help-xoptions",
        _workflow_variant([
            "run: python3 --help-xoptions taskplane/import_cycles.py --check "
            "--verify-history",
        ]),
    ),
    (
        "interpreter-help-all",
        _workflow_variant([
            "run: python3 --help-all taskplane/import_cycles.py --check "
            "--verify-history",
        ]),
    ),
    (
        "unknown-interpreter-control",
        _workflow_variant([
            "run: python3 --future-control taskplane/import_cycles.py --check "
            "--verify-history",
        ]),
    ),
    (
        "job-continue-on-error",
        _wave3_variant(
            "    runs-on: ubuntu-latest\n",
            "    runs-on: ubuntu-latest\n"
            "    continue-on-error: true\n",
        ),
    ),
    (
        "job-env-path-shadow",
        _wave3_variant(
            "    runs-on: ubuntu-latest\n",
            "    runs-on: ubuntu-latest\n"
            "    env:\n"
            "      PATH: /tmp/shadow-bin\n",
        ),
    ),
    (
        "push-trigger-path-exclusion",
        ACTIVE_WORKFLOW.replace(
            "  push:\n"
            "    branches: [main]\n",
            "  push:\n"
            "    branches: [main]\n"
            "    paths-ignore: ['taskplane/**']\n",
        ),
    ),
    (
        "invalid-later-job-yaml",
        ACTIVE_WORKFLOW.replace(
            "  pushed-sha-proof:\n",
            "  pushed-sha-proof:\n    malformed: [unterminated\n", 1),
    ),
    (
        "top-level-alias",
        "shared: &shared\n  PATH: /tmp/shadow-bin\n"
        + ACTIVE_WORKFLOW,
    ),
    (
        "quoted-top-level-env",
        '"env":\n  PATH: /tmp/shadow-bin\n' + ACTIVE_WORKFLOW,
    ),
    (
        "quoted-top-level-defaults",
        '"defaults":\n  run:\n    shell: true {0}\n' + ACTIVE_WORKFLOW,
    ),
    (
        "malformed-global-quote",
        ACTIVE_WORKFLOW.replace("name: CI\n", 'name: "CI\n', 1),
    ),
    (
        "malformed-global-colon",
        "malformed: value: extra\n" + ACTIVE_WORKFLOW,
    ),
])
def test_history_proof_rejects_inert_workflow_text_before_cut(
        tmp_path: Path, case: str, disabled_workflow: str) -> None:
    policy, workflow, active = _start_history_fixture(tmp_path)
    workflow.write_text(disabled_workflow, encoding="utf-8")
    disabled = _commit(tmp_path, case)
    (tmp_path / "taskplane" / "lens.py").write_text(
        "def f():\n    return None\n", encoding="utf-8")
    _commit(tmp_path, "cut target edge")
    workflow.write_text(active, encoding="utf-8")
    _commit(tmp_path, "restore workflow")

    with pytest.raises(
            cycles.CycleHistoryError,
            match=rf"workflow inactive at revision {disabled}"):
        cycles.verify_history(tmp_path, policy)


def test_workflow_parser_accepts_canonical_prefix_and_harmless_post_steps(
        ) -> None:
    assert cycles._workflow_ratchet_error(ACTIVE_WORKFLOW) is None
    assert cycles._workflow_ratchet_error(
        WORKFLOW.read_text(encoding="utf-8")) is None


def test_checked_in_workflow_matches_content_addressed_seal() -> None:
    assert cycles.workflow_seal_digest(WORKFLOW.read_bytes()) == \
        cycles.WORKFLOW_SHA256


def test_workflow_seal_binds_the_exact_full_workflow() -> None:
    baseline = cycles.workflow_seal_digest(ACTIVE_WORKFLOW)
    unrelated = ACTIVE_WORKFLOW.replace("name: CI\n", "name: CI evolved\n", 1)
    protected = ACTIVE_WORKFLOW.replace(
        "      - name: Import-cycle inventory, bounds, and activation order\n",
        "      # protected raw-byte mutation\n"
        "      - name: Import-cycle inventory, bounds, and activation order\n",
        1)

    assert cycles._workflow_ratchet_error(unrelated) is None
    assert cycles.workflow_seal_digest(unrelated) != baseline
    assert cycles._workflow_ratchet_error(protected) is None
    assert cycles.workflow_seal_digest(protected) != baseline


def test_cli_generates_the_checked_in_workflow_seal() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "taskplane" / "import_cycles.py"),
         "--root", str(ROOT), "--generate-workflow-seal"],
        text=True, encoding="utf-8", capture_output=True, check=True,
    )

    generated = json.loads(completed.stdout)
    assert generated == cycles.generate_workflow_seal(ROOT)
    assert generated["schema"] == cycles.WORKFLOW_SEAL_SCHEMA
    assert generated["sha256"] == cycles.WORKFLOW_SHA256


@pytest.mark.parametrize(("case", "workflow"), [
    (
        "workflow-env",
        ACTIVE_WORKFLOW.replace(
            "on:\n", "env:\n  PATH: /tmp/shadow-bin\n\non:\n", 1),
    ),
    (
        "workflow-defaults",
        ACTIVE_WORKFLOW.replace(
            "on:\n", "defaults:\n  run:\n    shell: true {0}\n\non:\n", 1),
    ),
    (
        "trigger-path-filter",
        ACTIVE_WORKFLOW.replace(
            "    branches: [main]\n",
            "    branches: [main]\n    paths: ['docs/**']\n", 1),
    ),
    (
        "missing-pull-request",
        ACTIVE_WORKFLOW.replace(
            "  pull_request:\n    branches: [main]\n", "", 1),
    ),
    (
        "job-needs",
        _wave3_variant(
            "    runs-on: ubuntu-latest\n",
            "    runs-on: ubuntu-latest\n    needs: setup\n"),
    ),
    (
        "job-strategy",
        _wave3_variant(
            "    runs-on: ubuntu-latest\n",
            "    runs-on: ubuntu-latest\n"
            "    strategy:\n      matrix: {python: []}\n"),
    ),
    (
        "checkout-mutable-tag",
        _wave3_variant(
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "actions/checkout@v7"),
    ),
    (
        "checkout-ref-substitution",
        _wave3_variant(
            "          fetch-depth: 0\n",
            "          fetch-depth: 0\n          ref: attacker\n"),
    ),
    (
        "setup-mutable-tag",
        _wave3_variant(
            "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
            "actions/setup-python@v7"),
    ),
    (
        "setup-version-substitution",
        _wave3_variant(
            '          python-version: "3.12"\n',
            '          python-version: "3.13"\n'),
    ),
    (
        "ratchet-step-env",
        _wave3_variant(
            "        run: >-\n",
            "        env:\n          PATH: /tmp/shadow-bin\n"
            "        run: >-\n"),
    ),
    (
        "ratchet-working-directory",
        _wave3_variant(
            "        run: >-\n",
            "        working-directory: /tmp\n        run: >-\n"),
    ),
    (
        "pre-ratchet-mutation-step",
        _wave3_variant(
            "      - name: Import-cycle inventory, bounds, and activation order\n",
            "      - name: Mutate scanner\n"
            "        run: sed -i.bak s/check/pass/ taskplane/import_cycles.py\n"
            "      - name: Import-cycle inventory, bounds, and activation order\n"),
    ),
    (
        "job-merge-alias",
        _wave3_variant(
            "    name: R-0006 graph + CLI contracts\n",
            "    <<: *untrusted\n"
            "    name: R-0006 graph + CLI contracts\n"),
    ),
    (
        "duplicate-runs-on",
        _wave3_variant(
            "    runs-on: ubuntu-latest\n",
            "    runs-on: ubuntu-latest\n"
            "    runs-on: ubuntu-latest\n"),
    ),
])
def test_workflow_parser_rejects_skip_mask_substitute_and_premutate(
        case: str, workflow: str) -> None:
    assert cycles._workflow_ratchet_error(workflow), case


@pytest.mark.parametrize("help_flag", [
    "-h", "-hignored", "--h", "--he", "--hel", "--help",
])
@pytest.mark.parametrize("before_script", [False, True])
def test_command_parser_rejects_successful_help_exit_family(
        help_flag: str, before_script: bool) -> None:
    words = ["python3", "taskplane/import_cycles.py", "--check",
             "--verify-history"]
    words.insert(1 if before_script else len(words), help_flag)

    assert not cycles._run_invokes_ratchet(" ".join(words))


@pytest.mark.parametrize("command", [
    "python3 taskplane/import_cycles.py --check --verify-history",
    "python3 taskplane/import_cycles.py --root . "
    "--policy taskplane/tests/fixtures/import-cycles.json "
    "--check --verify-history",
])
def test_command_parser_accepts_only_canonical_ratchet_forms(
        command: str) -> None:
    assert cycles._run_invokes_ratchet(command)


@pytest.mark.parametrize("interpreter_control", [
    "-?", "-V", "-VV", "--version", "--help-env", "--help-xoptions",
    "--help-all", "--future-control",
])
def test_command_parser_rejects_all_pre_script_controls(
        interpreter_control: str) -> None:
    command = (
        f"python3 {interpreter_control} taskplane/import_cycles.py "
        "--check --verify-history")

    assert not cycles._run_invokes_ratchet(command)


def test_history_proof_rejects_noop_scanner_cut_restore_gap(
        tmp_path: Path) -> None:
    policy, _, _ = _start_history_fixture(tmp_path)
    scanner = tmp_path / "taskplane" / "import_cycles.py"
    scanner.write_text(MARKER_PRESERVING_NOOP_SCANNER, encoding="utf-8")
    _commit(tmp_path, "replace scanner with marker-preserving no-op")
    (tmp_path / "taskplane" / "lens.py").write_text(
        "def f():\n    return None\n", encoding="utf-8")
    _commit(tmp_path, "cut target edge under no-op scanner")
    scanner.write_text(ACTIVE_SCANNER, encoding="utf-8")
    _commit(tmp_path, "restore trusted scanner")

    with pytest.raises(
            cycles.CycleHistoryError,
            match="sealed cycle scanner changed"):
        cycles.verify_history(tmp_path, policy)


def test_history_proof_rejects_noop_scanner_reverse_edge_cut_restore(
        tmp_path: Path) -> None:
    policy, _, _ = _start_history_fixture(tmp_path)
    scanner = tmp_path / "taskplane" / "import_cycles.py"
    scanner.write_text(MARKER_PRESERVING_NOOP_SCANNER, encoding="utf-8")
    _commit(tmp_path, "replace scanner with marker-preserving no-op")
    (tmp_path / "taskplane" / "decompose.py").write_text(
        "import lens_signals\n", encoding="utf-8")
    _commit(tmp_path, "cut decompose to depgraph under no-op scanner")
    scanner.write_text(ACTIVE_SCANNER, encoding="utf-8")
    _commit(tmp_path, "restore trusted scanner")

    with pytest.raises(
            cycles.CycleHistoryError,
            match="sealed cycle scanner changed"):
        cycles.verify_history(tmp_path, policy)


def test_ci_runs_full_history_ratchet_before_structural_tests() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    start = source.index("  wave3-contracts:\n")
    end = source.index("\n  pushed-sha-proof:\n", start)
    job = source[start:end]

    assert "fetch-depth: 0" in job
    assert "persist-credentials: false" in job
    assert "python3 taskplane/import_cycles.py" in job
    assert "--check" in job and "--verify-history" in job
    assert job.index("--check") < job.index("pytest")
    assert "taskplane/tests/test_r0006_import_cycle_ratchet.py" in job


def test_cli_returns_nonzero_and_names_growth(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _write_modules(tmp_path, {"a": "import b\n", "b": "import a\n"})
    revision = _commit(tmp_path, "baseline")
    policy = tmp_path / "policy.json"
    policy.write_text(cycles.canonical_json(cycles.build_inventory(
        tmp_path, source_revision=revision)), encoding="utf-8")
    _write_modules(tmp_path, {
        "a": "import b\nimport c\n",
        "b": "import a\n",
        "c": "import a\n",
    })

    completed = subprocess.run(
        [sys.executable, str(ROOT / "taskplane" / "import_cycles.py"),
         "--root", str(tmp_path), "--policy", str(policy), "--check"],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )

    assert completed.returncode == 1
    assert "taskplane.c" in completed.stderr
    assert "taskplane.a -> taskplane.c" in completed.stderr
    assert "measured members=3 edges=4 physical_loc=4" in completed.stderr
    assert "bound members=2 edges=2 physical_loc=2" in completed.stderr
