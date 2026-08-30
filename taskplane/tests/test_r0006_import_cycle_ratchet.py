"""Current-tree import-cycle inventory and non-growth contracts."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "taskplane" / "tests" / "fixtures" / "import-cycles.json"
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
