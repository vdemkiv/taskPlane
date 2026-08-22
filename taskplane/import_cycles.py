"""Deterministic file-level import-cycle inventory and non-growth ratchet.

The scanner deliberately uses only the standard library.  It follows imports
at every AST depth (including the deferred imports that commonly hide cycles),
reduces the direct ``taskplane/*.py`` graph with Tarjan's algorithm, and emits
one canonical record suitable for a checked-in policy.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path, PurePosixPath
import shlex
import subprocess
import sys
from typing import Iterable, Mapping, Sequence


SCHEMA = "taskplane.import-cycle-ratchet/v1"
CHECK_SCHEMA = "taskplane.import-cycle-check/v1"
HISTORY_SCHEMA = "taskplane.import-cycle-history/v1"
PACKAGE = "taskplane"
POLICY_RELATIVE = Path("taskplane/tests/fixtures/import-cycles.json")
WORKFLOW_RELATIVE = Path(".github/workflows/ci.yml")
MODULE_RELATIVE = Path("taskplane/import_cycles.py")
RATCHET_JOB_ID = "wave3-contracts"
RATCHET_CHECK_NAME = "R-0006 graph + CLI contracts"

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


def _show_optional(root: Path, revision: str, path: Path) -> str | None:
    result = subprocess.run(
        ["git", "show", f"{revision}:{path.as_posix()}"], cwd=root,
        check=False, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=30,
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


def _yaml_key_value(content: str, key: str) -> str | None:
    stripped = content.strip()
    if stripped.startswith("- "):
        stripped = stripped[2:].lstrip()
    prefix = f"{key}:"
    if not stripped.startswith(prefix):
        return None
    return stripped[len(prefix):].strip()


def _workflow_lines(source: str) -> list[tuple[int, str]]:
    rows = []
    for raw in source.splitlines():
        active = _strip_yaml_comment(raw).rstrip()
        if not active.strip():
            continue
        indentation = len(active) - len(active.lstrip(" "))
        rows.append((indentation, active.lstrip(" ")))
    return rows


def _condition_is_unconditional(value: str) -> bool:
    normalized = value.strip().strip('"\'').strip()
    if normalized.startswith("${{") and normalized.endswith("}}"):
        normalized = normalized[3:-2].strip()
    return normalized.lower() == "true"


def _defaults_run_shell(
        rows: Sequence[tuple[int, str]], start: int, end: int,
        *, defaults_indent: int) -> tuple[str | None, str | None]:
    """Read one ``defaults.run.shell`` mapping at the requested scope."""
    defaults_rows = [
        index for index in range(start, end)
        if rows[index][0] == defaults_indent
        and _yaml_key_value(rows[index][1], "defaults") is not None
    ]
    if not defaults_rows:
        return None, None
    if len(defaults_rows) != 1:
        return None, "multiple defaults mappings are not trusted"
    defaults_index = defaults_rows[0]
    defaults_value = _yaml_key_value(rows[defaults_index][1], "defaults")
    if defaults_value:
        return None, "inline defaults mapping is not trusted"
    defaults_end = defaults_index + 1
    while defaults_end < end and rows[defaults_end][0] > defaults_indent:
        defaults_end += 1
    run_indent = defaults_indent + 2
    run_rows = [
        index for index in range(defaults_index + 1, defaults_end)
        if rows[index][0] == run_indent
        and _yaml_key_value(rows[index][1], "run") is not None
    ]
    if not run_rows:
        return None, None
    if len(run_rows) != 1:
        return None, "multiple defaults.run mappings are not trusted"
    run_index = run_rows[0]
    if _yaml_key_value(rows[run_index][1], "run"):
        return None, "inline defaults.run mapping is not trusted"
    run_end = run_index + 1
    while run_end < defaults_end and rows[run_end][0] > run_indent:
        run_end += 1
    shell_rows = [
        _yaml_key_value(rows[index][1], "shell")
        for index in range(run_index + 1, run_end)
        if rows[index][0] == run_indent + 2
        and _yaml_key_value(rows[index][1], "shell") is not None
    ]
    if not shell_rows:
        return None, None
    if len(shell_rows) != 1 or not shell_rows[0]:
        return None, "defaults.run.shell must be one non-empty scalar"
    return shell_rows[0].strip().strip('"\''), None


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
    if len(words) < 4:
        return False
    if any(word and set(word).issubset({";", "&", "|"}) for word in words):
        return False
    executable = PurePosixPath(words[0]).name
    python_runner = executable in {"python", "python3"} or (
        executable.startswith("python3.")
        and executable.removeprefix("python3.").isdigit())
    if not python_runner:
        return False
    script_index = 1
    while script_index < len(words) and words[script_index].startswith("-"):
        if words[script_index] in {"-c", "-m"}:
            return False
        script_index += 1
    if script_index >= len(words) or \
            words[script_index] != MODULE_RELATIVE.as_posix():
        return False
    arguments = set(words[script_index + 1:])
    return {"--check", "--verify-history"}.issubset(arguments)


def _workflow_ratchet_error(source: str) -> str | None:
    """Prove an unconditional active ``run:`` invokes the ratchet.

    This deliberately parses only the small GitHub Actions YAML surface needed
    by the contract. Commented text and renamed keys are ignored. A condition
    on the containing job or step is accepted only when it is statically true;
    dynamic conditions cannot prove that the protected-line check always ran.
    """
    rows = _workflow_lines(source)
    jobs_index = next(
        (index for index, (_, content) in enumerate(rows)
         if content == "jobs:"), None)
    blocks: list[tuple[int, int, int]] = []
    workflow_shell = None
    workflow_shell_error = None
    if jobs_index is not None:
        jobs_indent = rows[jobs_index][0]
        workflow_shell, workflow_shell_error = _defaults_run_shell(
            rows, 0, len(rows), defaults_indent=jobs_indent)
        job_indent = jobs_indent + 2
        starts = [
            index for index in range(jobs_index + 1, len(rows))
            if rows[index][0] == job_indent
            and rows[index][1].endswith(":")
            and not rows[index][1].startswith("-")
        ]
        for position, start in enumerate(starts):
            if rows[start][1] != f"{RATCHET_JOB_ID}:":
                continue
            end = starts[position + 1] if position + 1 < len(starts) \
                else len(rows)
            blocks.append((start, end, job_indent))
    if workflow_shell_error is not None:
        return f"workflow shell is not trusted: {workflow_shell_error}"

    disabled_candidates = []
    for start, end, job_indent in blocks:
        check_names = [
            value.strip().strip('"\'')
            for indent, content in rows[start:end]
            if indent == job_indent + 2
            if (value := _yaml_key_value(content, "name")) is not None
        ]
        if check_names != [RATCHET_CHECK_NAME]:
            disabled_candidates.append(
                f"check name must be {RATCHET_CHECK_NAME!r}")
            continue
        job_shell, job_shell_error = _defaults_run_shell(
            rows, start + 1, end, defaults_indent=job_indent + 2)
        if job_shell_error is not None:
            disabled_candidates.append(
                f"job shell is not trusted: {job_shell_error}")
            continue
        job_conditions = [
            value for indent, content in rows[start:end]
            if indent == job_indent + 2
            if (value := _yaml_key_value(content, "if")) is not None
        ]
        for index in range(start, end):
            run_indent, content = rows[index]
            inline = _yaml_key_value(content, "run")
            if inline is None:
                continue
            if inline in {">", ">-", "|", "|-", "|+", ">+"}:
                body = []
                cursor = index + 1
                while cursor < end and rows[cursor][0] > run_indent:
                    body.append(rows[cursor][1])
                    cursor += 1
                command = " ".join(body)
            else:
                command = inline
            if not _run_invokes_ratchet(command):
                continue

            step_start = index
            while step_start > start:
                previous_indent, previous = rows[step_start - 1]
                if previous_indent < run_indent and previous.startswith("- "):
                    step_start -= 1
                    break
                if previous_indent < run_indent:
                    break
                step_start -= 1
            step_indent = rows[step_start][0] if step_start < index else run_indent
            step_end = index + 1
            while step_end < end:
                candidate_indent, candidate = rows[step_end]
                if candidate_indent == step_indent and candidate.startswith("- "):
                    break
                if candidate_indent < step_indent:
                    break
                step_end += 1
            step_conditions = [
                value for indent, candidate in rows[step_start:step_end]
                if indent in {step_indent, step_indent + 2, run_indent}
                if (value := _yaml_key_value(candidate, "if")) is not None
            ]
            ignored_failures = [
                value for indent, candidate in rows[step_start:step_end]
                if indent in {step_indent, step_indent + 2, run_indent}
                if (value := _yaml_key_value(
                    candidate, "continue-on-error")) is not None
                and value.strip().strip('"\'').lower() != "false"
            ]
            step_shells = [
                value.strip().strip('"\'')
                for indent, candidate in rows[step_start:step_end]
                if indent in {step_indent, step_indent + 2, run_indent}
                if (value := _yaml_key_value(candidate, "shell")) is not None
            ]
            conditions = job_conditions + step_conditions
            if conditions and not all(
                    _condition_is_unconditional(value) for value in conditions):
                disabled_candidates.extend(conditions)
                continue
            if ignored_failures:
                disabled_candidates.extend(
                    f"continue-on-error: {value}" for value in ignored_failures)
                continue
            if len(step_shells) > 1 or (step_shells and not step_shells[0]):
                disabled_candidates.append(
                    "step shell must be one non-empty scalar")
                continue
            effective_shell = step_shells[0] if step_shells else (
                job_shell if job_shell is not None else workflow_shell)
            if effective_shell not in {None, "bash"}:
                disabled_candidates.append(
                    f"effective shell is not trusted: {effective_shell!r}")
                continue
            return None

    if disabled_candidates:
        return f"ratchet job or step is conditional: {disabled_candidates}"
    return f"no active unconditional {RATCHET_JOB_ID!r} / " \
        f"{RATCHET_CHECK_NAME!r} run invokes import_cycles.py with --check " \
        "and --verify-history"


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
    trusted_scanner = _show_optional(root, "HEAD", MODULE_RELATIVE)
    if trusted_scanner is None or _scanner_contract_error(
            trusted_scanner) is not None:
        raise CycleHistoryError("trusted HEAD cycle scanner is unavailable")

    previous_policy = activation_policy
    last_policy_commit = activation
    protected_cut_seen = False
    for commit in protected_commits:
        module = _show_optional(root, commit, MODULE_RELATIVE)
        if module is None:
            raise CycleHistoryError(
                f"cycle scanner was removed at revision {commit}")
        scanner_error = _scanner_contract_error(module)
        if scanner_error is not None:
            raise CycleHistoryError(
                f"cycle scanner inactive at revision {commit}: {scanner_error}")

        workflow = _show_optional(root, commit, WORKFLOW_RELATIVE)
        workflow_error = None if workflow is None else \
            _workflow_ratchet_error(workflow)
        if workflow is None or workflow_error is not None:
            raise CycleHistoryError(
                f"cycle ratchet workflow inactive at revision {commit}: "
                f"{workflow_error or 'workflow file is missing'}")

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
            if monotonic["status"] != "pass":
                raise CycleHistoryError(
                    f"policy growth at revision {commit}: " +
                    format_failures(monotonic))
            policy_parent = git_revision(root, f"{commit}^")
            if candidate["source_revision"] != policy_parent:
                raise CycleHistoryError(
                    f"cycle policy at revision {commit} must measure its parent: "
                    f"policy={candidate['source_revision']} parent={policy_parent}")
            measured_policy = build_inventory_at_revision(
                root, candidate["source_revision"])
            if canonical_json(measured_policy) != canonical_json(candidate):
                raise CycleHistoryError(
                    f"cycle policy at revision {commit} is not exact for its "
                    "source_revision")
            previous_policy = candidate
            last_policy_commit = commit

        commit_sources = _revision_sources(root, commit)
        commit_graph, _ = _scan_graph(commit_sources)
        if any(target not in commit_graph.get(source, set())
               for source, target in TARGET_CUT_EDGES):
            protected_cut_seen = True
        if protected_cut_seen and module != trusted_scanner:
            raise CycleHistoryError(
                f"cycle scanner at or after protected cut revision {commit} "
                "does not match the trusted HEAD scanner blob")

        measured_commit = _inventory_from_sources(
            commit_sources, source_revision=commit)
        enforced = check_inventory(candidate, measured_commit)
        if enforced["status"] != "pass":
            raise CycleHistoryError(
                f"cycle ratchet violation at revision {commit}: " +
                format_failures(enforced))

    if canonical_json(previous_policy) != canonical_json(current_policy):
        raise CycleHistoryError(
            f"working policy differs from the last committed policy at "
            f"{last_policy_commit}")

    return {
        "schema": HISTORY_SCHEMA,
        "status": "pass",
        "activation_revision": activation,
        "measurement_revision": activation_parent,
        "current_policy_revision": current_policy["source_revision"],
        "target_edges": [list(edge) for edge in TARGET_CUT_EDGES],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="check the deterministic taskplane import-cycle ratchet")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--policy", type=Path, default=POLICY_RELATIVE)
    parser.add_argument("--check", action="store_true",
                        help="compare the working tree with the policy")
    parser.add_argument("--verify-history", action="store_true",
                        help="prove the ratchet activated before target cuts")
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
