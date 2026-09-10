"""CI source inventory: settings ownership and explicit subprocess encoding."""
from __future__ import annotations
import ast
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from taskplane.settings import load_settings
RUNNERS = {"run", "Popen", "check_output", "call", "check_call"}

def _semantic_leaf_paths(value: object, prefix: tuple[str, ...] = ()) -> set[str]:
    if isinstance(value, dict):
        result: set[str] = set()
        for key, item in value.items():
            if not prefix and key == "schema":
                continue
            result.update(_semantic_leaf_paths(item, prefix + (str(key),)))
        return result
    # A list is one governed setting, never one default owner per element.
    path = list(prefix)
    if len(path) >= 2 and path[0] == "stages":
        path[1] = "*"
    if len(path) >= 3 and path[0] == "lenses" and path[1] in {
            "routing", "counts"}:
        path[2] = "*"
    return {".".join(path)}

def _literal_environment_reads(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return set()
    names: set[str] = set()

    def scope_nodes(scope: ast.AST) -> list[ast.AST]:
        result: list[ast.AST] = []

        def descend(node: ast.AST) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (
                        ast.FunctionDef, ast.AsyncFunctionDef,
                        ast.ClassDef, ast.Lambda)):
                    continue
                result.append(child)
                descend(child)

        descend(scope)
        return result

    scopes: list[ast.AST] = [tree]
    scopes.extend(node for node in ast.walk(tree) if isinstance(
        node, (ast.FunctionDef, ast.AsyncFunctionDef)))
    for scope in scopes:
        nodes = scope_nodes(scope)
        aliases = {"environ"}
        assignments = [node for node in nodes
                       if isinstance(node, (ast.Assign, ast.AnnAssign))]
        changed = True
        while changed:
            changed = False
            for node in assignments:
                targets = (node.targets if isinstance(node, ast.Assign)
                           else [node.target])
                if node.value is None:
                    continue
                candidates = ([node.value.body, node.value.orelse]
                              if isinstance(node.value, ast.IfExp)
                              else list(node.value.values)
                              if isinstance(node.value, ast.BoolOp)
                              else [node.value])
                source_is_environment = any(
                    (isinstance(candidate, ast.Attribute) and
                     candidate.attr == "environ") or
                    (isinstance(candidate, ast.Name) and
                     candidate.id in aliases)
                    for candidate in candidates)
                if not source_is_environment:
                    continue
                for target in targets:
                    if isinstance(target, ast.Name) and \
                            target.id not in aliases:
                        aliases.add(target.id)
                        changed = True
        for node in nodes:
            if isinstance(node, ast.Call) and \
                    isinstance(node.func, ast.Attribute) and \
                    node.func.attr == "get" and node.args:
                target = node.func.value
                if ((isinstance(target, ast.Attribute) and
                     target.attr == "environ") or
                        (isinstance(target, ast.Name) and
                         target.id in aliases)) and \
                        isinstance(node.args[0], ast.Constant) and \
                        isinstance(node.args[0].value, str):
                    names.add(node.args[0].value)
            if isinstance(node, ast.Subscript) and (
                    (isinstance(node.value, ast.Attribute) and
                     node.value.attr == "environ") or
                    (isinstance(node.value, ast.Name) and
                     node.value.id in aliases)):
                key = node.slice
                if isinstance(key, ast.Constant) and \
                        isinstance(key.value, str):
                    names.add(key.value)
    # Adapter-owned dynamic tables are reads even though the lookup key is a
    # loop variable. Treat every environment-shaped table member as a read.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        target_names = {target.id for target in targets
                        if isinstance(target, ast.Name)}
        if not any("ENV" in name for name in target_names):
            continue
        for child in ast.walk(node.value):
            if isinstance(child, ast.Constant) and isinstance(child.value, str) \
                    and re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", child.value):
                names.add(child.value)
    return names

def _javascript_operational_defaults(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    pattern = re.compile(
        r"\b(?:maxAttempts|timeout(?:Seconds)?|shards|concurrency)\s*:"
        r"[^\n]*(?:\|\||\?\?)\s*(?:\d+|true|false|['\"])")
    return [match.group(0) for match in pattern.finditer(text)]

def _assigned_names(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return set()
    result: set[str] = set()
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else [])
        result.update(target.id for target in targets
                      if isinstance(target, ast.Name))
    return result

def _decoding_calls_without_encoding(src):
    """[(lineno, runner)] for calls that decode using the ambient locale."""
    found = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(
            fn, "id", "")
        if name not in RUNNERS:
            continue
        kwargs = {k.arg for k in node.keywords if k.arg}
        decodes = any(
            k.arg in ("text", "universal_newlines")
            and isinstance(k.value, ast.Constant) and k.value.value is True
            for k in node.keywords)
        if decodes and "encoding" not in kwargs:
            found.append((node.lineno, name))
    return found


def telemetry_pair_errors(root: Path) -> list[str]:
    """A missing-counter assertion needs positive-counter coverage nearby."""
    problems = []
    for path in sorted((root / "taskplane/tests").glob("test_*.py")):
        absent = positive = 0
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Assert):
                for comparison in ast.walk(node.test):
                    if not isinstance(comparison, ast.Compare) or "token" not in ast.unparse(comparison.left).lower():
                        continue
                    for operator, value in zip(comparison.ops, comparison.comparators):
                        if not isinstance(value, ast.Constant):
                            continue
                        absent += isinstance(operator, ast.Is) and value.value is None
                        if type(value.value) in {int, float}:
                            positive += (isinstance(operator, ast.Eq) and value.value > 0 or
                                isinstance(operator, ast.Gt) and value.value >= 0 or
                                isinstance(operator, ast.GtE) and value.value > 0)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args \
                    and "token" in ast.unparse(node.args[0]).lower():
                absent += node.func.attr == "assertIsNone"
                if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) \
                        and type(node.args[1].value) in {int, float}:
                    positive += (node.func.attr == "assertEqual" and node.args[1].value > 0 or
                                 node.func.attr == "assertGreater" and node.args[1].value >= 0)
        if absent and not positive:
            problems.append(f"{path.relative_to(root)}: missing positive-token sibling assertion")
    return problems


class _NormalizeBody(ast.NodeTransformer):
    def visit_Name(self, node):
        node.id = "name"
        return node

    def visit_Constant(self, node):
        node.value = type(node.value).__name__
        return node

    def visit_arg(self, node):
        node.arg = "arg"
        return self.generic_visit(node)

    def visit_FunctionDef(self, node):
        node.name = "function"
        return self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef


def duplicate_body_errors(root: Path) -> list[str]:
    """H9: compare substantial function bodies across production modules."""
    bodies = {}
    for path in sorted((root / "taskplane").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = function.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                body = body[1:]
            if not body or body[-1].end_lineno - body[0].lineno + 1 < 8:
                continue
            # Parse a copy so normalizing an outer function cannot change
            # a nested function before its independent comparison.
            copied = ast.parse("\n".join(ast.unparse(node) for node in body))
            normalized = _NormalizeBody().visit(copied)
            key = ast.dump(normalized, include_attributes=False)
            bodies.setdefault(key, []).append((path.name, function.lineno, function.name))
    return ["duplicate function bodies: " + ", ".join(f"{p}:{line} ({name})" for p, line, name in rows)
            for rows in bodies.values() if len({row[0] for row in rows}) > 1]


def main() -> int:
    inventory = json.loads((ROOT / "taskplane/settings_inventory.json").read_text())
    settings = load_settings(ROOT / inventory["authority"]["canonical_source"])
    problems = telemetry_pair_errors(ROOT) + duplicate_body_errors(ROOT)
    if _semantic_leaf_paths(settings.to_dict()) != set(inventory["canonical_keys"]):
        problems.append("settings inventory differs from canonical keys")
    classified = {name for row in inventory["environment_dispositions"] for name in row["names"]}
    prohibited = set(inventory["prohibited_direct_environment"])
    symbols = set(inventory["prohibited_default_symbols"])
    sources = sorted(p for directory in ("taskplane", "hooks", "scripts")
                     for p in (ROOT / directory).glob("*.py"))
    for path in sources:
        relative = path.relative_to(ROOT).as_posix()
        if relative != "taskplane/settings.py":
            reads = _literal_environment_reads(path)
            for name in sorted((reads - classified) | (reads & prohibited)):
                problems.append(f"{relative}: unclassified or prohibited environment read {name}")
            for name in sorted(_assigned_names(path) & symbols):
                problems.append(f"{relative}: duplicate default {name}")
        if relative != "scripts/ci_local.py":
            for line, runner in _decoding_calls_without_encoding(path.read_text(encoding="utf-8")):
                problems.append(f"{relative}:{line}: {runner} decodes without an explicit encoding")
    for path in sorted((ROOT / "workflows").glob("*.js")):
        for violation in _javascript_operational_defaults(path):
            problems.append(f"{path.relative_to(ROOT)}: duplicate default {violation}")
    for problem in problems:
        print(problem)
    print(f"source policy: {len(sources)} modules, {len(problems)} violations")
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main())
