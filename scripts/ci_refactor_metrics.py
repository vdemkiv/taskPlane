"""Report H8/H10/H11 size bounds and explicit test-ratio exceptions."""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIMITS = {"production_lines": 120_000, "test_lines": 70_000, "test_files": 200,
          "loop_lines": 8_000, "loop_function_lines": 200}


def measure(root: Path) -> dict:
    sources = {path.stem: path for path in (root / "taskplane").glob("*.py")}
    tests = sorted((root / "taskplane/tests").glob("test_*.py"))
    source_lines = {name: len(path.read_text(encoding="utf-8").splitlines())
                    for name, path in sources.items()}
    attributed = {name: 0.0 for name in sources}
    test_files = {name: [] for name in sources}
    total_test_lines = 0
    for path in tests:
        source = path.read_text(encoding="utf-8")
        count = len(source.splitlines())
        total_test_lines += count
        owners = set()
        for node in ast.walk(ast.parse(source)):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name.removeprefix("taskplane.") for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = ([alias.name for alias in node.names] if node.module == "taskplane"
                         else [(node.module or "").removeprefix("taskplane.")])
            owners.update(name for name in names if name in sources)
        for owner in owners:
            attributed[owner] += count / len(owners)
            test_files[owner].append(path.relative_to(root).as_posix())
    loop_tree = ast.parse(sources["loop"].read_text(encoding="utf-8"))
    sizes = {"production_lines": sum(source_lines.values()), "test_lines": total_test_lines,
             "test_files": len(tests), "loop_lines": source_lines["loop"],
             "loop_function_lines": max(node.end_lineno - node.lineno + 1
                 for node in ast.walk(loop_tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))}
    exceptions_path = root / "taskplane/tests/fixtures/test-ratio-exceptions.json"
    exceptions = json.loads(exceptions_path.read_text(encoding="utf-8")) if exceptions_path.exists() else {}
    modules = {}
    violations = [f"{key}: {sizes[key]} exceeds {limit}" for key, limit in LIMITS.items()
                  if sizes[key] > limit]
    for name, count in sorted(source_lines.items()):
        if count <= 300:
            continue
        ratio = attributed[name] / count
        outlier = not 0.3 <= ratio <= 2.0
        reason = exceptions.get(name)
        if outlier and (not isinstance(reason, str) or not reason.strip()):
            violations.append(f"{name}: test/source ratio {ratio:.3f} needs a recorded reason")
        modules[name] = {"source_lines": count, "attributed_test_lines": round(attributed[name], 2),
                         "ratio": round(ratio, 3), "outlier": outlier,
                         "reason": reason if outlier else None, "test_files": test_files[name]}
    return {"schema": "taskplane.refactor-metrics/v1", "sizes": sizes, "limits": LIMITS,
            "ratio_method": "Physical test-file lines split equally among directly imported production modules; this is not branch coverage.",
            "modules": modules, "violations": violations}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = measure(ROOT)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return int(args.check and bool(report["violations"]))


if __name__ == "__main__":
    raise SystemExit(main())
