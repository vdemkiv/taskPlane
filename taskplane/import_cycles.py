"""Deterministic file-level import-cycle topology inventory and ratchet.

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
import subprocess
import sys
from typing import Iterable, Mapping, Sequence


SCHEMA = "taskplane.import-cycle-topology/v2"
CHECK_SCHEMA = "taskplane.import-cycle-check/v1"
PACKAGE = "taskplane"
POLICY_RELATIVE = Path("taskplane/tests/fixtures/import-cycles.json")


class CycleScanError(RuntimeError):
    """A source graph could not be measured completely."""


class CyclePolicyError(RuntimeError):
    """A checked-in policy is malformed or cannot be trusted."""


class CycleRepositoryError(RuntimeError):
    """The requested checked-out repository state cannot be measured."""


def _run_git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=False, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise CycleRepositoryError(
            f"git {' '.join(args)} failed ({result.returncode}): {detail}")
    return result.stdout


def git_revision(root: Path, revision: str = "HEAD") -> str:
    """Resolve *revision* to one full commit id."""
    value = _run_git(Path(root), "rev-parse", f"{revision}^{{commit}}").strip()
    if not value:
        raise CycleRepositoryError(f"empty git revision for {revision!r}")
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
        raise CycleRepositoryError(
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
        sources: Mapping[str, tuple[str, str]]) -> dict[str, set[str]]:
    modules = set(sources)
    graph: dict[str, set[str]] = {}
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
    return graph


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
    graph = _scan_graph(sources)
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
            for field in ("member_count", "edge_count")}


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
        baseline_edges = {tuple(edge) for edge in baseline["internal_edges"]}
        current_edges = {tuple(edge) for edge in row["internal_edges"]}
        new_edges = current_edges - baseline_edges
        if new_edges:
            violations.append(_violation(
                "new-internal-edge", row, baseline=baseline,
                affected_modules={item for edge in new_edges for item in edge},
                affected_edges=new_edges))

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
            f"members={measured['member_count']} edges={measured['edge_count']}")
        bounds = row.get("bounds")
        if bounds is not None:
            text += (
                f"; bound members={bounds['member_count']} "
                f"edges={bounds['edge_count']}")
        lines.append(text)
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="check the current taskplane import-cycle inventory")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--policy", type=Path, default=POLICY_RELATIVE)
    parser.add_argument("--check", action="store_true",
                        help="compare the checked-out tree with the policy")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    policy_path = args.policy
    if not policy_path.is_absolute():
        policy_path = root / policy_path
    try:
        policy = load_policy(policy_path)
        current = build_inventory(root)
        result = check_inventory(policy, current)
    except (CycleScanError, CyclePolicyError, CycleRepositoryError) as exc:
        print(f"import-cycle check refused: {exc}", file=sys.stderr)
        return 2
    print(canonical_json(result), end="")
    if result["status"] != "pass":
        print(format_failures(result), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
