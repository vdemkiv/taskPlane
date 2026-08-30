"""Deterministic file-level import-cycle inventory and non-growth ratchet.

The scanner deliberately uses only the standard library.  It follows imports
at every AST depth (including the deferred imports that commonly hide cycles),
reduces the direct ``taskplane/*.py`` graph with Tarjan's algorithm, and emits
one canonical record suitable for a checked-in policy.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import shlex
import subprocess
import sys
from typing import Iterable, Mapping, Sequence


SCHEMA = "taskplane.import-cycle-ratchet/v1"
CHECK_SCHEMA = "taskplane.import-cycle-check/v1"
HISTORY_SCHEMA = "taskplane.import-cycle-history/v1"
HISTORY_RESOLUTIONS_SCHEMA = \
    "taskplane.import-cycle-history-resolutions/v1"
WORKFLOW_SEAL_SCHEMA = "taskplane.workflow-ratchet-seal/v1"
PACKAGE = "taskplane"
POLICY_RELATIVE = Path("taskplane/tests/fixtures/import-cycles.json")
HISTORY_RESOLUTIONS_RELATIVE = Path(
    "taskplane/tests/fixtures/import-cycle-history-resolutions.json")
WORKFLOW_RELATIVE = Path(".github/workflows/ci.yml")
MODULE_RELATIVE = Path("taskplane/import_cycles.py")
WORKFLOW_SHA256 = \
    "9f477b02afa5101b7f10de5fc36b39b0ba05fda7f5454b07f94a2f0d2d718c22"
SEALED_WORKFLOW_SHA256 = \
    "e61df03fbec44633d945490f9df0c7c2f56e074b5f2da2915343035377bfb505"
TRUSTED_WORKFLOW_PREDECESSOR_SHA256S = frozenset({
    "ad14a00ec79956f401d3c9151fe106c4997959f2a0762061c3a31eb9765b0b45",
    "bacbceab1fcd8fa45803b37824f6b6b901bd6b224f389508fcf42d596dd9282e",
    "85436df4f422037a99cace6634cbef8cee2c36a5c76366dd9815153ea4d17c19",
    "417463d582eabf317cd2cdcbaa1c9f2e67cf397fa0c23e4bc4e59d6ffe41e0e7",
    "23a7f87fe42cf153318bd703f1f93ddc3f9479e4262177de49568cc69aa50c15",
    "9f736826cfe9fb44abe64462fe604114fc9055d62baff69994d331e89ed5f5bb",
})
SEALED_SCANNER_SHA256 = \
    "fdb1e859898e05323afa2ae77a0189cba164edebb9644edc02daeac8168aace5"
# Every post-seal scanner predecessor is an exact reviewed artifact, never a
# semantic pattern. Adding the current HEAD scanner to history on the next
# revision therefore requires naming the immediately previous trusted bytes;
# an unlisted intermediate mutation still fails the continuous-history proof.
TRUSTED_SCANNER_PREDECESSOR_SHA256S = frozenset({
    "c89eddc3d2ed09846b63495a31f927e8678db2052ffe47bca7795636b1d787b0",
    "1728a688ffb8a6e09f7410c9d6ba3da88ec8bfc0590b377cdf5fe7b7d8792752",
    "77a9adf2e9876ba56867bac07676290706df6b59fbc2b56ffb3c5dfd71865d91",
    "e48c475b598a32b33c489c5087416cf40229d0b5e1c8263db85d04708801cd7b",
})
# Exact, one-time policy growth accepted for the reviewed R-0002 remediation
# integration. A receipt binds the complete pre-rebaseline violation span and
# the exact measured policy and the exact repair offset after that source.
# The repair commit itself is discovered from first-parent history, so no
# commit is asked to contain its own (impossible) Git id. Any other bound
# raise, source revision, repair position, affected module, or historical
# observation remains a refusal.
TRUSTED_POLICY_REBASELINES = ({
    "policy_sha256":
        "55ab2022bdcde4c6a1c363e2b46064ac1e4d583d0c9a900a495c7a83867c5735",
    "introduced_revision": "95901f238ca3e72066fb493d3cb8456a4054ef0e",
    "source_revision": "bdfd522bbcce19ca71d107569c441a183ac74025",
    "commit_count": 50,
    "repair_commit_offset": 10,
    "violation_codes": (
        "new-cyclic-member", "new-scc", "physical-loc-growth"),
    "affected_modules": (
        "taskplane.audit", "taskplane.checkpoint", "taskplane.collision",
        "taskplane.dashboard",
        "taskplane.defect_claim", "taskplane.depgraph",
        "taskplane.design_contract", "taskplane.evidence",
        "taskplane.governed_commands", "taskplane.lens",
        "taskplane.lens_signals", "taskplane.loop",
        "taskplane.loop_status", "taskplane.regression",
        "taskplane.requirements", "taskplane.retro", "taskplane.review",
        "taskplane.review_evidence", "taskplane.review_progression",
        "taskplane.review_repair", "taskplane.review_retry",
        "taskplane.runtime_eval", "taskplane.stage_entities",
        "taskplane.stage_handoff", "taskplane.taskplane_lite",
        "taskplane.views",
    ),
    "history_sha256":
        "c2d601bc8a3a80ce871c39d59088016d7c785e55fde0559be289ac4375bfe1ba",
}, {
    "policy_sha256":
        "8a6261fc8e1918cceaf7ae25d2239f2c0155d38a167f766b71ecf1d7299c0ddd",
    "introduced_revision": "aa4cf3ddc549fa7afd99ad528fe3c4f6a4498b66",
    "source_revision": "337dc7de7be978e62695227e37cb19a796d7b3cf",
    "commit_count": 34,
    "repair_commit_offset": 1,
    "violation_codes": ("new-internal-edge", "physical-loc-growth"),
    "affected_modules": (
        "taskplane.audit", "taskplane.collision", "taskplane.dashboard",
        "taskplane.defect_claim", "taskplane.depgraph",
        "taskplane.design_contract", "taskplane.evidence",
        "taskplane.lens", "taskplane.lens_signals", "taskplane.loop",
        "taskplane.loop_status", "taskplane.regression",
        "taskplane.requirements", "taskplane.retro", "taskplane.review",
        "taskplane.review_evidence", "taskplane.review_progression",
        "taskplane.review_repair", "taskplane.review_retry",
        "taskplane.runtime_eval", "taskplane.stage_entities",
        "taskplane.stage_handoff", "taskplane.taskplane_lite",
        "taskplane.views",
    ),
    "history_sha256":
        "d8995bbcadb9ee889a7e76ae67dcda4b58e4adba0da8cbf9319dc7f2f57af878",
})
RATCHET_JOB_ID = "wave3-contracts"
RATCHET_CHECK_NAME = "R-0006 graph + CLI contracts"
RATCHET_STEP_NAME = "Import-cycle inventory, bounds, and activation order"
CHECKOUT_ACTION = \
    "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_PYTHON_ACTION = \
    "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97"
CANONICAL_RATCHET_WORDS = (
    "python3", MODULE_RELATIVE.as_posix(),
    "--root", ".",
    "--policy", POLICY_RELATIVE.as_posix(),
    "--check", "--verify-history",
)

# These are the five S1/S2 edges recorded in the R-0006 accepted design.
# The history proof requires all five in the activation commit; later commits
# may remove them, but cannot claim that the ratchet landed after a cut.
TARGET_CUT_EDGES = (
    ("taskplane.lens", "taskplane.review"),
    ("taskplane.depgraph", "taskplane.decompose"),
    ("taskplane.decompose", "taskplane.depgraph"),
    ("taskplane.decompose", "taskplane.lens_signals"),
    ("taskplane.taskplane_lite", "taskplane.depgraph"),
)


class CycleScanError(RuntimeError):
    """A source graph could not be measured completely."""


class CyclePolicyError(RuntimeError):
    """A checked-in policy is malformed or cannot be trusted."""


class CycleHistoryError(RuntimeError):
    """Repository history does not prove ratchet-before-cuts ordering."""


def _run_git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=False, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise CycleHistoryError(
            f"git {' '.join(args)} failed ({result.returncode}): {detail}")
    return result.stdout


def git_revision(root: Path, revision: str = "HEAD") -> str:
    """Resolve *revision* to one full commit id."""
    value = _run_git(Path(root), "rev-parse", f"{revision}^{{commit}}").strip()
    if not value:
        raise CycleHistoryError(f"empty git revision for {revision!r}")
    return value


def _working_sources(root: Path) -> dict[str, tuple[str, str]]:
    package_root = Path(root) / PACKAGE
    if not package_root.is_dir():
        raise CycleScanError(f"missing package directory: {package_root}")
    rows: dict[str, tuple[str, str]] = {}
    for path in sorted(package_root.glob("*.py")):
        relative = path.relative_to(root).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise CycleScanError(
                f"cannot read {relative}: {exc.__class__.__name__}: {exc}") \
                from exc
        rows[f"{PACKAGE}.{path.stem}"] = (relative, source)
    if not rows:
        raise CycleScanError(f"no direct Python modules found under {package_root}")
    return rows


def _revision_sources(root: Path, revision: str) -> dict[str, tuple[str, str]]:
    resolved = git_revision(root, revision)
    listing = _run_git(
        Path(root), "ls-tree", "-r", "--name-only", resolved, "--", PACKAGE,
    ).splitlines()
    rows: dict[str, tuple[str, str]] = {}
    for raw in sorted(listing):
        path = PurePosixPath(raw)
        if path.parent != PurePosixPath(PACKAGE) or path.suffix != ".py":
            continue
        source = _run_git(Path(root), "show", f"{resolved}:{path.as_posix()}")
        rows[f"{PACKAGE}.{path.stem}"] = (path.as_posix(), source)
    if not rows:
        raise CycleHistoryError(
            f"revision {resolved} has no direct {PACKAGE}/*.py modules")
    return rows


def _resolve_imports(tree: ast.AST, modules: set[str]) -> set[str]:
    stems = {module.removeprefix(f"{PACKAGE}."): module
             for module in modules}
    resolved: set[str] = set()

    def add_name(name: str) -> None:
        if name in stems:
            resolved.add(stems[name])
            return
        prefix = f"{PACKAGE}."
        if name.startswith(prefix):
            stem = name[len(prefix):].split(".", 1)[0]
            if stem in stems:
                resolved.add(stems[stem])

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add_name(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.module:
                    add_name(node.module.split(".", 1)[0])
                else:
                    for alias in node.names:
                        add_name(alias.name)
            elif node.module == PACKAGE:
                for alias in node.names:
                    add_name(alias.name)
            elif node.module:
                add_name(node.module)
    return resolved


def _scan_graph(
        sources: Mapping[str, tuple[str, str]]) -> tuple[dict[str, set[str]],
                                                         dict[str, int]]:
    modules = set(sources)
    graph: dict[str, set[str]] = {}
    physical_loc: dict[str, int] = {}
    for module in sorted(sources):
        relative, source = sources[module]
        try:
            tree = ast.parse(source, filename=relative)
        except SyntaxError as exc:
            line = f" line {exc.lineno}" if exc.lineno else ""
            reason = (exc.msg or "invalid syntax").replace("\n", " ")
            raise CycleScanError(
                f"cannot parse {relative}:{line}: SyntaxError: {reason}") from exc
        graph[module] = _resolve_imports(tree, modules)
        physical_loc[module] = len(source.splitlines())
    return graph, physical_loc


def _tarjan(graph: Mapping[str, set[str]]) -> list[list[str]]:
    index = 0
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indexes[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in sorted(graph[node]):
            if target not in indexes:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indexes[target])
        if lowlinks[node] != indexes[node]:
            return
        members: list[str] = []
        while True:
            member = stack.pop()
            on_stack.remove(member)
            members.append(member)
            if member == node:
                break
        if len(members) > 1:
            components.append(sorted(members))

    for module in sorted(graph):
        if module not in indexes:
            visit(module)
    return sorted(components)


def _inventory_from_sources(
        sources: Mapping[str, tuple[str, str]], *, source_revision: str) -> dict:
    if not isinstance(source_revision, str) or not source_revision.strip():
        raise CyclePolicyError("source_revision must be a non-empty string")
    graph, loc = _scan_graph(sources)
    rows = []
    for members in _tarjan(graph):
        member_set = set(members)
        edges = sorted(
            [source, target]
            for source in members
            for target in graph[source]
            if target in member_set and target != source
        )
        rows.append({
            "members": members,
            "internal_edges": edges,
            "member_count": len(members),
            "edge_count": len(edges),
            "physical_loc": sum(loc[member] for member in members),
        })
    inventory = {
        "schema": SCHEMA,
        "package": PACKAGE,
        "source_revision": source_revision,
        "sccs": rows,
    }
    validate_inventory(inventory)
    return inventory


def build_inventory(root: Path, *, source_revision: str | None = None) -> dict:
    root = Path(root).resolve()
    revision = source_revision if source_revision is not None else git_revision(root)
    return _inventory_from_sources(
        _working_sources(root), source_revision=revision)


def build_inventory_at_revision(root: Path, revision: str) -> dict:
    root = Path(root).resolve()
    resolved = git_revision(root, revision)
    return _inventory_from_sources(
        _revision_sources(root, resolved), source_revision=resolved)


def canonical_json(record: Mapping) -> str:
    return json.dumps(record, sort_keys=True, indent=2,
                      ensure_ascii=False) + "\n"


def policy_inventory_digest(record: Mapping) -> str:
    """Content-address one complete measured cycle policy."""
    return hashlib.sha256(canonical_json(record).encode("utf-8")).hexdigest()


def _require_keys(record: Mapping, expected: set[str], label: str) -> None:
    actual = set(record)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise CyclePolicyError(
            f"{label} keys are not closed: missing={missing}, "
            f"unexpected={unexpected}")


def validate_inventory(record: Mapping) -> None:
    if not isinstance(record, Mapping):
        raise CyclePolicyError("inventory must be an object")
    _require_keys(record, {"schema", "package", "source_revision", "sccs"},
                  "inventory")
    if record["schema"] != SCHEMA:
        raise CyclePolicyError(
            f"inventory schema must be {SCHEMA!r}, got {record['schema']!r}")
    if record["package"] != PACKAGE:
        raise CyclePolicyError(f"inventory package must be {PACKAGE!r}")
    if not isinstance(record["source_revision"], str) or not \
            record["source_revision"].strip():
        raise CyclePolicyError("source_revision must be a non-empty string")
    if not isinstance(record["sccs"], list):
        raise CyclePolicyError("sccs must be a list")

    expected_row_keys = {
        "members", "internal_edges", "member_count", "edge_count",
        "physical_loc",
    }
    seen_members: set[str] = set()
    previous_members: list[str] | None = None
    for index, row in enumerate(record["sccs"]):
        if not isinstance(row, Mapping):
            raise CyclePolicyError(f"sccs[{index}] must be an object")
        _require_keys(row, expected_row_keys, f"sccs[{index}]")
        members = row["members"]
        if not isinstance(members, list) or len(members) < 2 or \
                not all(isinstance(member, str) and
                        member.startswith(f"{PACKAGE}.") for member in members):
            raise CyclePolicyError(
                f"sccs[{index}].members must contain at least two package modules")
        if members != sorted(set(members)):
            raise CyclePolicyError(
                f"sccs[{index}].members must be sorted and unique")
        if previous_members is not None and previous_members >= members:
            raise CyclePolicyError("sccs must be sorted by members and unique")
        previous_members = members
        overlap = seen_members.intersection(members)
        if overlap:
            raise CyclePolicyError(
                f"modules occur in more than one SCC: {sorted(overlap)}")
        seen_members.update(members)

        edges = row["internal_edges"]
        if not isinstance(edges, list) or any(
                not isinstance(edge, list) or len(edge) != 2 or
                not all(isinstance(item, str) for item in edge)
                for edge in edges):
            raise CyclePolicyError(
                f"sccs[{index}].internal_edges must be [source, target] pairs")
        if edges != sorted(edges) or len({tuple(edge) for edge in edges}) != len(edges):
            raise CyclePolicyError(
                f"sccs[{index}].internal_edges must be sorted and unique")
        if any(source not in members or target not in members or source == target
               for source, target in edges):
            raise CyclePolicyError(
                f"sccs[{index}] has an edge outside its members or a self-edge")
        for field, expected in (
                ("member_count", len(members)),
                ("edge_count", len(edges))):
            value = row[field]
            if isinstance(value, bool) or not isinstance(value, int) or \
                    value != expected:
                raise CyclePolicyError(
                    f"sccs[{index}].{field} must equal {expected}")
        physical_loc = row["physical_loc"]
        if isinstance(physical_loc, bool) or not isinstance(physical_loc, int) \
                or physical_loc < 0:
            raise CyclePolicyError(
                f"sccs[{index}].physical_loc must be a non-negative integer")


def load_policy(path: Path) -> dict:
    try:
        raw = Path(path).read_text(encoding="utf-8")
        record = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CyclePolicyError(
            f"cannot load cycle policy {path}: {exc.__class__.__name__}: {exc}") \
            from exc
    validate_inventory(record)
    return record


def _measure(row: Mapping) -> dict[str, int]:
    return {field: row[field]
            for field in ("member_count", "edge_count", "physical_loc")}


def _violation(code: str, current: Mapping, *, baseline: Mapping | None,
               affected_modules: Iterable[str],
               affected_edges: Iterable[Sequence[str]]) -> dict:
    return {
        "code": code,
        "affected_modules": sorted(set(affected_modules)),
        "affected_edges": sorted([list(edge) for edge in affected_edges]),
        "measured": _measure(current),
        "bounds": _measure(baseline) if baseline is not None else None,
    }


def check_inventory(policy: Mapping, current: Mapping) -> dict:
    validate_inventory(policy)
    validate_inventory(current)
    baseline_rows = policy["sccs"]
    violations = []
    descendants: dict[int, list[Mapping]] = {
        index: [] for index in range(len(baseline_rows))}

    for row in current["sccs"]:
        members = set(row["members"])
        candidate_indexes = [
            index for index, baseline in enumerate(baseline_rows)
            if members.issubset(set(baseline["members"]))
        ]
        if not candidate_indexes:
            overlaps = sorted(
                baseline_rows,
                key=lambda baseline: len(
                    members.intersection(baseline["members"])),
                reverse=True,
            )
            closest = overlaps[0] if overlaps and members.intersection(
                overlaps[0]["members"]) else None
            if closest is None:
                violations.append(_violation(
                    "new-scc", row, baseline=None,
                    affected_modules=row["members"],
                    affected_edges=row["internal_edges"]))
            else:
                new_members = members - set(closest["members"])
                violations.append(_violation(
                    "new-cyclic-member", row, baseline=closest,
                    affected_modules=new_members,
                    affected_edges=row["internal_edges"]))
            continue

        baseline_index = candidate_indexes[0]
        baseline = baseline_rows[baseline_index]
        descendants[baseline_index].append(row)
        baseline_edges = {tuple(edge) for edge in baseline["internal_edges"]}
        current_edges = {tuple(edge) for edge in row["internal_edges"]}
        new_edges = current_edges - baseline_edges
        if new_edges:
            violations.append(_violation(
                "new-internal-edge", row, baseline=baseline,
                affected_modules={item for edge in new_edges for item in edge},
                affected_edges=new_edges))

    # One baseline SCC may legitimately split into several current SCCs.  Its
    # bound applies to the descendants in aggregate, otherwise two children
    # could each consume the full parent LOC allowance and silently grow.
    for baseline_index, rows in descendants.items():
        if not rows:
            continue
        baseline = baseline_rows[baseline_index]
        aggregate = {
            "members": sorted({member for row in rows
                               for member in row["members"]}),
            "internal_edges": sorted({tuple(edge) for row in rows
                                      for edge in row["internal_edges"]}),
            "member_count": sum(row["member_count"] for row in rows),
            "edge_count": sum(row["edge_count"] for row in rows),
            "physical_loc": sum(row["physical_loc"] for row in rows),
        }
        if aggregate["physical_loc"] > baseline["physical_loc"]:
            violations.append(_violation(
                "physical-loc-growth", aggregate, baseline=baseline,
                affected_modules=aggregate["members"],
                affected_edges=aggregate["internal_edges"]))

    baseline_members = {member for row in baseline_rows
                        for member in row["members"]}
    current_members = {member for row in current["sccs"]
                       for member in row["members"]}
    baseline_edges = {tuple(edge) for row in baseline_rows
                      for edge in row["internal_edges"]}
    current_edges = {tuple(edge) for row in current["sccs"]
                     for edge in row["internal_edges"]}
    return {
        "schema": CHECK_SCHEMA,
        "status": "fail" if violations else "pass",
        "policy_source_revision": policy["source_revision"],
        "current_source_revision": current["source_revision"],
        "policy_sccs": baseline_rows,
        "current_sccs": current["sccs"],
        "delta": {
            "added_members": sorted(current_members - baseline_members),
            "removed_members": sorted(baseline_members - current_members),
            "added_edges": sorted([list(edge)
                                   for edge in current_edges - baseline_edges]),
            "removed_edges": sorted([list(edge)
                                     for edge in baseline_edges - current_edges]),
        },
        "violations": violations,
    }


def format_failures(result: Mapping) -> str:
    lines = []
    for row in result.get("violations", []):
        measured = row["measured"]
        modules = ", ".join(row["affected_modules"]) or "(none)"
        edges = ", ".join(
            f"{source} -> {target}"
            for source, target in row["affected_edges"]) or "(none)"
        text = (
            f"{row['code']}: modules=[{modules}] edges=[{edges}] measured "
            f"members={measured['member_count']} edges={measured['edge_count']} "
            f"physical_loc={measured['physical_loc']}")
        bounds = row.get("bounds")
        if bounds is not None:
            text += (
                f"; bound members={bounds['member_count']} "
                f"edges={bounds['edge_count']} "
                f"physical_loc={bounds['physical_loc']}")
        lines.append(text)
    return "\n".join(lines)


def history_resolution_digest(records: Sequence[Mapping]) -> str:
    """Content address one complete, ordered historical violation span."""
    body = json.dumps(list(records), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _closed_history_keys(record: Mapping, expected: set[str], label: str) -> None:
    actual = set(record)
    if actual != expected:
        raise CycleHistoryError(
            f"{label} keys are not closed: "
            f"missing={sorted(expected - actual)}, "
            f"unexpected={sorted(actual - expected)}")


def _hex_string(value: object, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and all(
        char in "0123456789abcdef" for char in value)


def _load_history_resolutions(
        root: Path, protected_commits: Sequence[str]) -> list[dict]:
    """Load exact repaired violation intervals, never a pattern waiver.

    A repaired interval names every affected first-parent commit indirectly
    through its closed [introduced, repaired) bounds and binds their complete
    measured violations with one digest. The repair commit itself remains
    subject to the ordinary ratchet and must pass. This lets the verifier
    retain truthful evidence of a historical defect without making the
    protected line permanently unshippable or accepting any future growth.
    """
    path = root / HISTORY_RESOLUTIONS_RELATIVE
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CycleHistoryError(
            f"history resolution ledger is unreadable: {exc}") from exc
    if not isinstance(value, Mapping):
        raise CycleHistoryError("history resolution ledger must be an object")
    _closed_history_keys(value, {"schema", "resolutions"},
                         "history resolution ledger")
    if value["schema"] != HISTORY_RESOLUTIONS_SCHEMA or not isinstance(
            value["resolutions"], list):
        raise CycleHistoryError("history resolution ledger schema is invalid")

    positions = {commit: index for index, commit in enumerate(protected_commits)}
    expected = {
        "introduced_revision", "repaired_revision", "commit_count",
        "violation_codes", "affected_modules", "history_sha256", "reason",
    }
    out = []
    previous_repair = -1
    for index, row in enumerate(value["resolutions"]):
        label = f"history resolution[{index}]"
        if not isinstance(row, Mapping):
            raise CycleHistoryError(f"{label} must be an object")
        _closed_history_keys(row, expected, label)
        introduced = row["introduced_revision"]
        repaired = row["repaired_revision"]
        if not _hex_string(introduced, 40) or not _hex_string(repaired, 40):
            raise CycleHistoryError(f"{label} revisions must be full commit ids")
        if introduced not in positions or repaired not in positions:
            raise CycleHistoryError(
                f"{label} revisions are not both on protected first-parent history")
        start = positions[introduced]
        end = positions[repaired]
        if start >= end or start < previous_repair:
            raise CycleHistoryError(
                f"{label} must be ordered, non-overlapping, and repaired later")
        previous_repair = end
        if isinstance(row["commit_count"], bool) or not isinstance(
                row["commit_count"], int) or row["commit_count"] != end - start:
            raise CycleHistoryError(
                f"{label} commit_count must equal its exact protected span")
        for field in ("violation_codes", "affected_modules"):
            values = row[field]
            if not isinstance(values, list) or not values or values != sorted(
                    set(values)) or not all(isinstance(item, str) and item
                                            for item in values):
                raise CycleHistoryError(
                    f"{label} {field} must be a sorted non-empty string list")
        if not _hex_string(row["history_sha256"], 64):
            raise CycleHistoryError(f"{label} history_sha256 is invalid")
        if not isinstance(row["reason"], str) or len(row["reason"].strip()) < 80:
            raise CycleHistoryError(
                f"{label} needs a specific repair reason (at least 80 characters)")
        out.append({**row, "_start": start, "_end": end})
    return out


def _show_optional(root: Path, revision: str, path: Path) -> str | None:
    result = subprocess.run(
        ["git", "show", f"{revision}:{path.as_posix()}"], cwd=root,
        check=False, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=30,
    )
    return result.stdout if result.returncode == 0 else None


def _show_optional_bytes(root: Path, revision: str, path: Path) -> bytes | None:
    result = subprocess.run(
        ["git", "show", f"{revision}:{path.as_posix()}"], cwd=root,
        check=False, capture_output=True, timeout=30,
    )
    return result.stdout if result.returncode == 0 else None


def _strip_yaml_comment(line: str) -> str:
    """Remove a YAML comment without treating quoted ``#`` as a comment."""
    single_quoted = False
    double_quoted = False
    escaped = False
    for index, character in enumerate(line):
        if double_quoted and character == "\\" and not escaped:
            escaped = True
            continue
        if character == "'" and not double_quoted and not escaped:
            single_quoted = not single_quoted
        elif character == '"' and not single_quoted and not escaped:
            double_quoted = not double_quoted
        elif character == "#" and not single_quoted and not double_quoted \
                and (index == 0 or line[index - 1].isspace()):
            return line[:index]
        escaped = False
    return line


def _workflow_lines(source: str) -> list[tuple[int, str]]:
    rows = []
    for raw in source.splitlines():
        active = _strip_yaml_comment(raw).rstrip()
        if not active.strip():
            continue
        indentation = len(active) - len(active.lstrip(" "))
        rows.append((indentation, active.lstrip(" ")))
    return rows


def _run_invokes_ratchet(command: str) -> bool:
    scalar = command.strip()
    if len(scalar) >= 2 and scalar[0] == scalar[-1] \
            and scalar[0] in {'"', "'"}:
        scalar = scalar[1:-1]
    try:
        lexer = shlex.shlex(scalar, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        lexer.commenters = "#"
        words = list(lexer)
    except ValueError:
        return False
    if len(words) < 4 or words[0] != "python3":
        return False
    # Closed grammar: no interpreter option may precede the script. This
    # excludes every Python early-success form (-V, extended help, and future
    # controls) without maintaining a denylist that can drift with Python.
    if words[1] != MODULE_RELATIVE.as_posix():
        return False
    arguments = tuple(words[2:])
    return arguments in {
        ("--check", "--verify-history"),
        (
            "--root", ".",
            "--policy", POLICY_RELATIVE.as_posix(),
            "--check", "--verify-history",
        ),
    }


def _yaml_mapping_entry(content: str) -> tuple[str, str] | None:
    stripped = content.strip()
    if stripped.startswith("- "):
        stripped = stripped[2:].lstrip()
    if ":" not in stripped:
        return None
    key, value = stripped.split(":", 1)
    key = key.strip()
    if not key or any(character.isspace() for character in key):
        return None
    return key, value.strip()


def _yaml_scalar(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] \
            and stripped[0] in {'"', "'"}:
        return stripped[1:-1]
    return stripped


def _block_end(
        rows: Sequence[tuple[int, str]], start: int, indent: int) -> int:
    end = start + 1
    while end < len(rows) and rows[end][0] > indent:
        end += 1
    return end


def _direct_fields(
        rows: Sequence[tuple[int, str]], start: int, end: int,
        indent: int) -> list[tuple[int, str, str]] | None:
    fields = []
    for index in range(start, end):
        if rows[index][0] != indent:
            continue
        entry = _yaml_mapping_entry(rows[index][1])
        if entry is None:
            return None
        fields.append((index, *entry))
    return fields


def _exact_with_step(
        rows: Sequence[tuple[int, str]], start: int, end: int,
        *, action: str, inputs: tuple[tuple[str, str], ...]) -> str | None:
    step_indent = rows[start][0]
    root = _yaml_mapping_entry(rows[start][1])
    if root != ("uses", action):
        return f"step must use exactly {action!r}"
    fields = _direct_fields(rows, start + 1, end, step_indent + 2)
    if fields is None or [(key, value) for _, key, value in fields] != [
            ("with", "")]:
        return "action step may contain only one with mapping"
    with_index = fields[0][0]
    values = _direct_fields(rows, with_index + 1, end, step_indent + 4)
    if values is None or [
            (key, _yaml_scalar(value)) for _, key, value in values
    ] != list(inputs):
        return f"action inputs must be exactly {list(inputs)!r}"
    if any(indent > step_indent + 4 for indent, _ in rows[start + 1:end]):
        return "nested action input structures are not trusted"
    return None


def _exact_ratchet_step(
        rows: Sequence[tuple[int, str]], start: int, end: int) -> str | None:
    step_indent = rows[start][0]
    root = _yaml_mapping_entry(rows[start][1])
    if root is None or root[0] != "name" \
            or _yaml_scalar(root[1]) != RATCHET_STEP_NAME:
        return f"third step name must be exactly {RATCHET_STEP_NAME!r}"
    fields = _direct_fields(rows, start + 1, end, step_indent + 2)
    if fields is None or len(fields) != 1 or fields[0][1] != "run":
        return "ratchet step may contain only one run field"
    run_index, _, run_value = fields[0]
    if run_value not in {">", ">-", ">+", "|", "|-", "|+"}:
        command = run_value
        if run_index + 1 != end:
            return "inline ratchet command may not have nested content"
    else:
        body = rows[run_index + 1:end]
        if not body or any(
                indent != step_indent + 4 for indent, _ in body):
            return "ratchet run block must contain only command lines"
        command = " ".join(content for _, content in body)
    try:
        words = tuple(shlex.split(command, comments=True, posix=True))
    except ValueError:
        return "ratchet run command is malformed"
    if words != CANONICAL_RATCHET_WORDS:
        return "ratchet run command is not the canonical history proof"
    return None


def _workflow_ratchet_error(source: str) -> str | None:
    """Match the one closed-world CI activation grammar trusted by R-0006."""
    if any("\t" in raw[:len(raw) - len(raw.lstrip(" \t"))]
           for raw in source.splitlines()):
        return "tab-indented workflow structures are not trusted"
    rows = _workflow_lines(source)
    top = _direct_fields(rows, 0, len(rows), 0)
    if top is None:
        return "top-level workflow mapping is malformed"
    top_keys = [key for _, key, _ in top]
    if top_keys.count("on") != 1 or top_keys.count("jobs") != 1:
        return "workflow must contain exactly one on mapping and one jobs mapping"
    if any(key in {"env", "defaults"} for key in top_keys):
        return "workflow-level env/defaults are not trusted"

    on_index, _, on_value = next(row for row in top if row[1] == "on")
    if on_value:
        return "workflow triggers must use the trusted block mapping"
    on_end = _block_end(rows, on_index, 0)
    events = _direct_fields(rows, on_index + 1, on_end, 2)
    if events is None or [(key, value) for _, key, value in events] != [
            ("push", ""), ("pull_request", "")]:
        return "triggers must be exactly push and pull_request"
    for position, (event_index, event, _) in enumerate(events):
        event_end = events[position + 1][0] if position + 1 < len(events) \
            else on_end
        filters = _direct_fields(rows, event_index + 1, event_end, 4)
        if filters is None or [
                (key, value) for _, key, value in filters
        ] != [("branches", "[main]")]:
            return f"{event} must target only main without path filters"
        if any(indent > 4 for indent, _ in rows[event_index + 1:event_end]):
            return f"nested {event} trigger structures are not trusted"

    jobs_index, _, jobs_value = next(row for row in top if row[1] == "jobs")
    if jobs_value:
        return "jobs must use the trusted block mapping"
    jobs_end = _block_end(rows, jobs_index, 0)
    job_rows = _direct_fields(rows, jobs_index + 1, jobs_end, 2)
    if job_rows is None:
        return "jobs mapping is malformed"
    target_jobs = [row for row in job_rows if row[1] == RATCHET_JOB_ID]
    if len(target_jobs) != 1 or target_jobs[0][2]:
        return f"workflow must contain one block job {RATCHET_JOB_ID!r}"
    job_index = target_jobs[0][0]
    following_jobs = [index for index, _, _ in job_rows if index > job_index]
    job_end = following_jobs[0] if following_jobs else jobs_end
    job_fields = _direct_fields(rows, job_index + 1, job_end, 4)
    expected_job = [
        ("name", RATCHET_CHECK_NAME),
        ("runs-on", "ubuntu-latest"),
        ("steps", ""),
    ]
    if job_fields is None or [
            (key, _yaml_scalar(value)) for _, key, value in job_fields
    ] != expected_job:
        return "ratchet job fields are not the exact trusted name/runner/steps"

    steps_index = job_fields[2][0]
    step_starts = [
        index for index in range(steps_index + 1, job_end)
        if rows[index][0] == 6 and rows[index][1].startswith("- ")
    ]
    if any(rows[index][0] == 6 and not rows[index][1].startswith("- ")
           for index in range(steps_index + 1, job_end)):
        return "steps must be one unambiguous block sequence"
    if len(step_starts) < 3:
        return "ratchet job must begin with checkout, setup-python, and proof"
    step_ends = step_starts[1:] + [job_end]
    checkout_inputs = (
        (("fetch-depth", "0"), ("persist-credentials", "false")),
        (("ref", "${{ github.event.pull_request.head.sha || github.sha }}"),
         ("fetch-depth", "0"), ("persist-credentials", "false")),
    )
    checkout_errors = [
        _exact_with_step(
            rows, step_starts[0], step_ends[0], action=CHECKOUT_ACTION,
            inputs=inputs)
        for inputs in checkout_inputs
    ]
    if all(error is not None for error in checkout_errors):
        return f"checkout step is not trusted: {checkout_errors[0]}"
    setup_error = _exact_with_step(
        rows, step_starts[1], step_ends[1], action=SETUP_PYTHON_ACTION,
        inputs=(("python-version", "3.12"),),
    )
    if setup_error is not None:
        return f"setup-python step is not trusted: {setup_error}"
    ratchet_error = _exact_ratchet_step(
        rows, step_starts[2], step_ends[2])
    if ratchet_error is not None:
        return f"ratchet step is not trusted: {ratchet_error}"
    return None


def workflow_seal_bytes(workflow: bytes | str) -> bytes:
    """Return exact full-workflow bytes after closed-grammar validation."""
    if isinstance(workflow, bytes):
        try:
            source = workflow.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CycleHistoryError(
                "cycle ratchet workflow is not UTF-8") from exc
    else:
        source = workflow
    error = _workflow_ratchet_error(source)
    if error is not None:
        raise CycleHistoryError(
            f"cycle ratchet workflow is not sealable: {error}")
    return source.encode("utf-8")


def workflow_seal_digest(workflow: bytes | str) -> str:
    """Generate the content address for the protected workflow surface."""
    return hashlib.sha256(workflow_seal_bytes(workflow)).hexdigest()


def generate_workflow_seal(root: Path) -> dict:
    """Generate the checked-in workflow seal from repository bytes."""
    path = Path(root).resolve() / WORKFLOW_RELATIVE
    try:
        workflow = path.read_bytes()
    except OSError as exc:
        raise CycleHistoryError(
            f"cannot read {WORKFLOW_RELATIVE.as_posix()}: {exc}") from exc
    sealed = workflow_seal_bytes(workflow)
    return {
        "schema": WORKFLOW_SEAL_SCHEMA,
        "path": WORKFLOW_RELATIVE.as_posix(),
        "protected_bytes": len(sealed),
        "sha256": hashlib.sha256(sealed).hexdigest(),
    }


def _scanner_contract_error(source: str) -> str | None:
    """Reject malformed scanner artifacts before applying semantic proof."""
    try:
        tree = ast.parse(source, filename=MODULE_RELATIVE.as_posix())
    except SyntaxError as exc:
        line = f" line {exc.lineno}" if exc.lineno else ""
        return f"scanner is not valid Python{line}: {exc.msg or 'invalid syntax'}"
    functions = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing_functions = sorted(
        {"check_inventory", "verify_history", "main"} - functions)
    strings = {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    missing_options = sorted({"--check", "--verify-history"} - strings)
    reasons = []
    if missing_functions:
        reasons.append(f"missing functions {missing_functions}")
    if missing_options:
        reasons.append(f"missing CLI options {missing_options}")
    return "; ".join(reasons) or None


def _first_activation(root: Path, policy_relative: Path) -> str:
    commits = _run_git(
        root, "rev-list", "--first-parent", "--reverse", "HEAD", "--",
        MODULE_RELATIVE.as_posix(), policy_relative.as_posix(),
        WORKFLOW_RELATIVE.as_posix(),
    ).splitlines()
    for commit in commits:
        module = _show_optional(root, commit, MODULE_RELATIVE)
        policy = _show_optional(root, commit, policy_relative)
        workflow = _show_optional(root, commit, WORKFLOW_RELATIVE)
        if module is not None and policy is not None and workflow is not None \
                and _scanner_contract_error(module) is None \
                and _workflow_ratchet_error(workflow) is None:
            return commit
    raise CycleHistoryError(
        "no first-parent ratchet activation commit contains an active scanner, "
        "valid policy path, and CI --check --verify-history invocation")


def _protected_edges_at_revision(root: Path, revision: str) -> set[tuple[str, str]]:
    modules = sorted({module for edge in TARGET_CUT_EDGES for module in edge})
    sources = {}
    for module in modules:
        relative = Path(*module.split(".")).with_suffix(".py")
        source = _show_optional(root, revision, relative)
        if source is not None:
            sources[module] = (relative.as_posix(), source)
    if not sources:
        return set()
    try:
        graph, _ = _scan_graph(sources)
    except CycleScanError as exc:
        raise CycleHistoryError(
            f"cannot inspect protected edges at revision {revision}: {exc}") \
            from exc
    return {
        edge for edge in TARGET_CUT_EDGES
        if edge[1] in graph.get(edge[0], set())
    }


def _first_protected_cut(root: Path) -> str | None:
    paths = sorted({
        Path(*module.split(".")).with_suffix(".py").as_posix()
        for edge in TARGET_CUT_EDGES for module in edge
    })
    commits = _run_git(
        root, "rev-list", "--first-parent", "--reverse", "HEAD", "--",
        *paths,
    ).splitlines()
    armed = False
    expected = set(TARGET_CUT_EDGES)
    for commit in commits:
        edges = _protected_edges_at_revision(root, commit)
        if edges == expected:
            armed = True
        elif armed:
            return commit
    return None


def _trusted_scanner_blob(blob: bytes | None, trusted_scanner: bytes) -> bool:
    """Accept only content-addressed scanner lineage or trusted HEAD bytes."""
    if blob is None:
        return False
    digest = hashlib.sha256(blob).hexdigest()
    return (
        blob == trusted_scanner
        or digest == SEALED_SCANNER_SHA256
        or digest in TRUSTED_SCANNER_PREDECESSOR_SHA256S)


def _sealed_workflow_blob(blob: bytes | None) -> bool:
    if blob is None:
        return False
    try:
        return workflow_seal_digest(blob) in {
            SEALED_WORKFLOW_SHA256, WORKFLOW_SHA256,
        } | TRUSTED_WORKFLOW_PREDECESSOR_SHA256S
    except CycleHistoryError:
        return False


def _seal_activation(root: Path, trusted_scanner: bytes) -> str:
    commits = _run_git(
        root, "rev-list", "--first-parent", "--reverse", "HEAD", "--",
        MODULE_RELATIVE.as_posix(), WORKFLOW_RELATIVE.as_posix(),
    ).splitlines()
    expected_edges = set(TARGET_CUT_EDGES)
    activation = next((
        commit for commit in commits
        if _trusted_scanner_blob(
            _show_optional_bytes(root, commit, MODULE_RELATIVE),
            trusted_scanner)
        and _sealed_workflow_blob(
            _show_optional_bytes(root, commit, WORKFLOW_RELATIVE))
        and _protected_edges_at_revision(root, commit) == expected_edges
    ), None)
    if activation is None:
        missing = expected_edges - _protected_edges_at_revision(root, "HEAD")
        rendered = ", ".join(
            f"{source} -> {target}" for source, target in sorted(missing))
        detail = f"; missing: {rendered}" if rendered else ""
        raise CycleHistoryError(
            "no first-parent content-addressed seal contains the trusted "
            f"scanner/workflow blobs and all protected edges{detail}")

    sealed_commits = _run_git(
        root, "rev-list", "--first-parent", "--reverse",
        f"{activation}^..HEAD",
    ).splitlines()
    for commit in sealed_commits:
        if not _trusted_scanner_blob(
                _show_optional_bytes(root, commit, MODULE_RELATIVE),
                trusted_scanner):
            raise CycleHistoryError(
                f"sealed cycle scanner changed at revision {commit}")
        workflow = _show_optional_bytes(root, commit, WORKFLOW_RELATIVE)
        if not _sealed_workflow_blob(workflow):
            raise CycleHistoryError(
                f"cycle ratchet workflow inactive at revision {commit}: "
                "sealed workflow surface changed or was removed")

    first_cut = _first_protected_cut(root)
    if first_cut is not None:
        history = _run_git(
            root, "rev-list", "--first-parent", "--reverse", "HEAD",
        ).splitlines()
        positions = {commit: index for index, commit in enumerate(history)}
        if positions[activation] >= positions[first_cut]:
            raise CycleHistoryError(
                f"content-addressed seal {activation} must strictly precede "
                f"protected cut revision {first_cut}")
    return activation


def verify_history(root: Path, policy_path: Path) -> dict:
    root = Path(root).resolve()
    policy_path = Path(policy_path).resolve()
    try:
        policy_relative = policy_path.relative_to(root)
    except ValueError as exc:
        raise CycleHistoryError("cycle policy must be inside the repository") \
            from exc
    shallow = _run_git(root, "rev-parse", "--is-shallow-repository").strip()
    if shallow == "true":
        raise CycleHistoryError(
            "history proof requires a full checkout (actions/checkout fetch-depth: 0)")

    current_policy = load_policy(policy_path)
    activation = _first_activation(root, policy_relative)
    try:
        activation_parent = git_revision(root, f"{activation}^")
    except CycleHistoryError as exc:
        raise CycleHistoryError("ratchet activation commit has no parent") from exc

    raw_activation_policy = _show_optional(root, activation, policy_relative)
    if raw_activation_policy is None:  # guarded by _first_activation
        raise CycleHistoryError("activation policy is missing")
    try:
        activation_policy = json.loads(raw_activation_policy)
    except json.JSONDecodeError as exc:
        raise CycleHistoryError(
            f"activation policy is invalid JSON: {exc}") from exc
    try:
        validate_inventory(activation_policy)
    except CyclePolicyError as exc:
        raise CycleHistoryError(f"activation policy is invalid: {exc}") from exc
    if activation_policy["source_revision"] != activation_parent:
        raise CycleHistoryError(
            "activation policy source_revision must equal the activation "
            f"parent: policy={activation_policy['source_revision']} "
            f"parent={activation_parent}")

    measured_activation_parent = build_inventory_at_revision(
        root, activation_parent)
    if canonical_json(measured_activation_parent) != canonical_json(
            activation_policy):
        raise CycleHistoryError(
            "activation policy is not the exact measured pre-cut inventory")

    activation_sources = _revision_sources(root, activation)
    activation_graph, _ = _scan_graph(activation_sources)
    missing_edges = [edge for edge in TARGET_CUT_EDGES
                     if edge[1] not in activation_graph.get(edge[0], set())]
    if missing_edges:
        rendered = ", ".join(f"{source} -> {target}"
                             for source, target in missing_edges)
        raise CycleHistoryError(
            f"ratchet activation did not precede the target cuts; missing: {rendered}")

    trusted_scanner = _show_optional_bytes(root, "HEAD", MODULE_RELATIVE)
    if trusted_scanner is None:
        raise CycleHistoryError("trusted HEAD cycle scanner is unavailable")
    try:
        trusted_scanner_source = trusted_scanner.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CycleHistoryError(
            "trusted HEAD cycle scanner is not UTF-8") from exc
    if _scanner_contract_error(trusted_scanner_source) is not None:
        raise CycleHistoryError("trusted HEAD cycle scanner is unavailable")
    trusted_workflow = _show_optional_bytes(root, "HEAD", WORKFLOW_RELATIVE)
    if trusted_workflow is None:
        raise CycleHistoryError("trusted HEAD workflow is unavailable")
    workflow_digest = workflow_seal_digest(trusted_workflow)
    if workflow_digest != WORKFLOW_SHA256:
        raise CycleHistoryError(
            "trusted HEAD workflow hash mismatch: "
            f"expected={WORKFLOW_SHA256} actual={workflow_digest}")
    seal_activation = _seal_activation(root, trusted_scanner)

    # Audit every protected-line commit from activation through HEAD, not only
    # commits that changed the policy. This makes continuity part of the proof:
    # CI, scanner, or policy cannot be disabled for a cut and restored later.
    # Commits before activation are deliberately outside this interval, and
    # unrelated commits remain valid because their inherited triplet and graph
    # continue to satisfy the same checks.
    protected_commits = _run_git(
        root, "rev-list", "--first-parent", "--reverse",
        f"{activation}^..HEAD",
    ).splitlines()
    if not protected_commits or protected_commits[0] != activation:
        raise CycleHistoryError(
            "ratchet activation is not on the HEAD first-parent history")

    resolutions = _load_history_resolutions(root, protected_commits)
    resolution_for_commit = {}
    resolution_repair_for_commit = {}
    resolution_observations = [[] for _ in resolutions]
    for resolution_index, resolution in enumerate(resolutions):
        repaired = resolution["repaired_revision"]
        if repaired in resolution_repair_for_commit:
            raise CycleHistoryError(
                f"multiple history resolutions repair at revision {repaired}")
        resolution_repair_for_commit[repaired] = resolution_index
        for commit in protected_commits[
                resolution["_start"]:resolution["_end"]]:
            resolution_for_commit[commit] = resolution_index

    positions = {commit: index for index, commit in enumerate(protected_commits)}
    rebaseline_for_commit = {}
    rebaseline_observations = [[] for _ in TRUSTED_POLICY_REBASELINES]
    rebaseline_repairs = [None for _ in TRUSTED_POLICY_REBASELINES]
    rebaseline_expected_repairs = [None for _ in TRUSTED_POLICY_REBASELINES]
    rebaseline_keys = {
        "policy_sha256", "introduced_revision", "source_revision",
        "commit_count", "repair_commit_offset", "violation_codes",
        "affected_modules", "history_sha256",
    }
    for rebaseline_index, rebaseline in enumerate(
            TRUSTED_POLICY_REBASELINES):
        label = f"trusted policy rebaseline[{rebaseline_index}]"
        if set(rebaseline) != rebaseline_keys:
            raise CycleHistoryError(f"{label} fields are not closed")
        introduced = rebaseline["introduced_revision"]
        source_revision = rebaseline["source_revision"]
        if introduced not in positions or source_revision not in positions:
            continue
        start = positions[introduced]
        source = positions[source_revision]
        offset = rebaseline["repair_commit_offset"]
        if isinstance(offset, bool) or not isinstance(offset, int) or \
                offset < 1:
            raise CycleHistoryError(
                f"{label} repair_commit_offset must be a positive integer")
        end = source + offset
        if start >= end or end >= len(protected_commits) or \
                rebaseline["commit_count"] != end - start:
            raise CycleHistoryError(
                f"{label} does not bind its exact first-parent span")
        rebaseline_expected_repairs[rebaseline_index] = protected_commits[end]
        for commit in protected_commits[start:end]:
            if commit in resolution_for_commit or commit in rebaseline_for_commit:
                raise CycleHistoryError(
                    f"{label} overlaps another historical resolution")
            rebaseline_for_commit[commit] = rebaseline_index

    previous_policy = activation_policy
    last_policy_commit = activation
    protected_cut_seen = False
    seal_seen = seal_activation not in protected_commits
    for commit in protected_commits:
        if commit == seal_activation:
            seal_seen = True
        module_blob = _show_optional_bytes(root, commit, MODULE_RELATIVE)
        if module_blob is None:
            raise CycleHistoryError(
                f"cycle scanner was removed at revision {commit}")
        try:
            module = module_blob.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CycleHistoryError(
                f"cycle scanner is not UTF-8 at revision {commit}") from exc
        scanner_error = _scanner_contract_error(module)
        if scanner_error is not None:
            raise CycleHistoryError(
                f"cycle scanner inactive at revision {commit}: {scanner_error}")
        if seal_seen and not _trusted_scanner_blob(
                module_blob, trusted_scanner):
            raise CycleHistoryError(
                f"sealed cycle scanner changed at revision {commit}")

        workflow_blob = _show_optional_bytes(root, commit, WORKFLOW_RELATIVE)
        if workflow_blob is None:
            raise CycleHistoryError(
                f"cycle ratchet workflow inactive at revision {commit}: "
                "workflow file is missing")
        if seal_seen:
            if not _sealed_workflow_blob(workflow_blob):
                raise CycleHistoryError(
                    f"cycle ratchet workflow inactive at revision {commit}: "
                    "sealed workflow surface changed")
        else:
            try:
                workflow = workflow_blob.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise CycleHistoryError(
                    f"cycle ratchet workflow inactive at revision {commit}: "
                    "workflow is not UTF-8") from exc
            workflow_error = _workflow_ratchet_error(workflow)
            if workflow_error is not None:
                raise CycleHistoryError(
                    f"cycle ratchet workflow inactive at revision {commit}: "
                    f"{workflow_error}")

        raw_policy = _show_optional(root, commit, policy_relative)
        if raw_policy is None:
            raise CycleHistoryError(
                f"cycle policy was removed at revision {commit}")
        try:
            candidate = json.loads(raw_policy)
            validate_inventory(candidate)
        except (json.JSONDecodeError, CyclePolicyError) as exc:
            raise CycleHistoryError(
                f"cycle policy at revision {commit} is invalid: {exc}") from exc

        if canonical_json(candidate) != canonical_json(previous_policy):
            monotonic = check_inventory(previous_policy, candidate)
            candidate_digest = policy_inventory_digest(candidate)
            rebaseline_index = next((
                index for index, receipt in enumerate(
                    TRUSTED_POLICY_REBASELINES)
                if receipt["policy_sha256"] == candidate_digest
                and receipt["source_revision"] == candidate["source_revision"]
            ), None)
            resolution_repair_index = resolution_repair_for_commit.get(commit)
            resolution_repair_matches = False
            if resolution_repair_index is not None and \
                    monotonic["status"] != "pass":
                resolution = resolutions[resolution_repair_index]
                codes = sorted({row["code"]
                                for row in monotonic["violations"]})
                modules = sorted({module
                                  for row in monotonic["violations"]
                                  for module in row["affected_modules"]})
                resolution_repair_matches = \
                    codes == resolution["violation_codes"] and \
                    modules == resolution["affected_modules"]
            if monotonic["status"] != "pass" and rebaseline_index is None \
                    and not resolution_repair_matches:
                raise CycleHistoryError(
                    f"policy growth at revision {commit}: " +
                    format_failures(monotonic))
            policy_parent = git_revision(root, f"{commit}^")
            if candidate["source_revision"] != policy_parent and \
                    rebaseline_index is None:
                raise CycleHistoryError(
                    f"cycle policy at revision {commit} must measure its parent: "
                    f"policy={candidate['source_revision']} parent={policy_parent}")
            measured_policy = build_inventory_at_revision(
                root, candidate["source_revision"])
            if canonical_json(measured_policy) != canonical_json(candidate):
                raise CycleHistoryError(
                    f"cycle policy at revision {commit} is not exact for its "
                    "source_revision")
            if rebaseline_index is not None:
                if commit != rebaseline_expected_repairs[rebaseline_index]:
                    raise CycleHistoryError(
                        "trusted policy rebaseline was repaired at the wrong "
                        f"revision {commit}")
                if rebaseline_repairs[rebaseline_index] is not None:
                    raise CycleHistoryError(
                        "trusted policy rebaseline was applied more than once")
                rebaseline_repairs[rebaseline_index] = commit
            previous_policy = candidate
            last_policy_commit = commit

        commit_sources = _revision_sources(root, commit)
        commit_graph, _ = _scan_graph(commit_sources)
        if any(target not in commit_graph.get(source, set())
               for source, target in TARGET_CUT_EDGES):
            protected_cut_seen = True
        if protected_cut_seen and not _trusted_scanner_blob(
                module_blob, trusted_scanner):
            raise CycleHistoryError(
                f"cycle scanner at or after protected cut revision {commit} "
                "does not match the trusted HEAD scanner blob")

        measured_commit = _inventory_from_sources(
            commit_sources, source_revision=commit)
        enforced = check_inventory(candidate, measured_commit)
        resolution_index = resolution_for_commit.get(commit)
        if resolution_index is not None:
            resolution_observations[resolution_index].append({
                "revision": commit,
                "violations": enforced["violations"],
            })
        rebaseline_index = rebaseline_for_commit.get(commit)
        if rebaseline_index is not None:
            rebaseline_observations[rebaseline_index].append({
                "revision": commit,
                "violations": enforced["violations"],
            })
        if enforced["status"] != "pass" and resolution_index is None and \
                rebaseline_index is None:
            raise CycleHistoryError(
                f"cycle ratchet violation at revision {commit}: " +
                format_failures(enforced))

    resolution_proof = []
    for resolution, observations in zip(
            resolutions, resolution_observations):
        label = resolution["introduced_revision"]
        if len(observations) != resolution["commit_count"] or not observations \
                or observations[0]["revision"] != label or any(
                    not row["violations"] for row in observations):
            raise CycleHistoryError(
                f"history resolution {label} does not cover one continuous "
                "failing interval")
        codes = sorted({violation["code"] for row in observations
                        for violation in row["violations"]})
        modules = sorted({module for row in observations
                          for violation in row["violations"]
                          for module in violation["affected_modules"]})
        digest = history_resolution_digest(observations)
        if codes != resolution["violation_codes"] or \
                modules != resolution["affected_modules"] or \
                digest != resolution["history_sha256"]:
            raise CycleHistoryError(
                f"history resolution {label} does not match the exact "
                "measured violations")
        resolution_proof.append({
            "introduced_revision": label,
            "repaired_revision": resolution["repaired_revision"],
            "commit_count": len(observations),
            "history_sha256": digest,
        })

    rebaseline_proof = []
    for rebaseline, observations, repair in zip(
            TRUSTED_POLICY_REBASELINES, rebaseline_observations,
            rebaseline_repairs):
        if rebaseline["introduced_revision"] not in positions:
            continue
        label = rebaseline["introduced_revision"]
        if repair is None or len(observations) != rebaseline["commit_count"] \
                or not observations or observations[0]["revision"] != label \
                or observations[-1]["revision"] != git_revision(
                    root, f"{repair}^") or any(
                    not row["violations"] for row in observations):
            raise CycleHistoryError(
                f"trusted policy rebaseline {label} does not cover one exact "
                "continuous failing interval")
        codes = sorted({violation["code"] for row in observations
                        for violation in row["violations"]})
        modules = sorted({module for row in observations
                          for violation in row["violations"]
                          for module in violation["affected_modules"]})
        digest = history_resolution_digest(observations)
        if codes != list(rebaseline["violation_codes"]) or \
                modules != list(rebaseline["affected_modules"]) or \
                digest != rebaseline["history_sha256"]:
            raise CycleHistoryError(
                f"trusted policy rebaseline {label} does not match the exact "
                "measured violations")
        rebaseline_proof.append({
            "introduced_revision": label,
            "source_revision": rebaseline["source_revision"],
            "repair_revision": repair,
            "commit_count": len(observations),
            "policy_sha256": rebaseline["policy_sha256"],
            "history_sha256": digest,
        })

    if canonical_json(previous_policy) != canonical_json(current_policy):
        raise CycleHistoryError(
            f"working policy differs from the last committed policy at "
            f"{last_policy_commit}")

    return {
        "schema": HISTORY_SCHEMA,
        "status": "pass",
        "activation_revision": activation,
        "seal_revision": seal_activation,
        "measurement_revision": activation_parent,
        "current_policy_revision": current_policy["source_revision"],
        "target_edges": [list(edge) for edge in TARGET_CUT_EDGES],
        "resolved_history": resolution_proof,
        "policy_rebaselines": rebaseline_proof,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="check the deterministic taskplane import-cycle ratchet")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--policy", type=Path, default=POLICY_RELATIVE)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="compare the working tree with the policy")
    mode.add_argument(
        "--generate-workflow-seal", action="store_true",
        help="print the content address of the protected CI ratchet surface")
    parser.add_argument("--verify-history", action="store_true",
                        help="prove the ratchet activated before target cuts")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.generate_workflow_seal:
        if args.verify_history:
            parser.error(
                "--generate-workflow-seal cannot be combined with "
                "--verify-history")
        try:
            print(canonical_json(generate_workflow_seal(root)), end="")
        except CycleHistoryError as exc:
            print(f"import-cycle ratchet refused: {exc}", file=sys.stderr)
            return 2
        return 0
    policy_path = args.policy
    if not policy_path.is_absolute():
        policy_path = root / policy_path
    try:
        policy = load_policy(policy_path)
        current = build_inventory(root)
        result = check_inventory(policy, current)
        if args.verify_history:
            result["history"] = verify_history(root, policy_path)
    except (CycleScanError, CyclePolicyError, CycleHistoryError) as exc:
        print(f"import-cycle ratchet refused: {exc}", file=sys.stderr)
        return 2
    print(canonical_json(result), end="")
    if result["status"] != "pass":
        print(format_failures(result), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
