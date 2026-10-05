#!/usr/bin/env python3
"""Taskplane: human-gated delivery, native observations, graph and dashboard."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

if sys.version_info < (3, 10):
    raise SystemExit("Taskplane requires Python 3.10 or newer")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "taskplane"))

from taskplane import depgraph, flow, graph_primitives, primitives, storage, workspace_binding


def _workflow_inputs(path: Path) -> dict[str, Any]:
    from taskplane import blueprint as b
    import math
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise b.BlueprintError("duplicate_key", "/inputs/" + key, "Duplicate input JSON key.")
            result[key] = value
        return result
    def invalid(value: str) -> Any:
        raise b.BlueprintError("invalid_json", "/inputs", "Non-finite number: " + value)
    def number(value: str) -> float:
        result = float(value)
        return result if math.isfinite(result) else invalid(value)
    raw = b._read_file(path)
    if len(raw) > b.MAX_DEFINITION_BYTES:
        raise b.BlueprintError("inputs_too_large", "/inputs", "Inputs exceed 256 KiB.")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=invalid, parse_float=number)
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, b.BlueprintError):
            raise
        raise b.BlueprintError("invalid_json", "/inputs", str(exc)) from None
    if not isinstance(value, dict):
        raise b.BlueprintError("invalid_inputs", "/inputs", "Inputs must be a JSON object.")
    return value


def workflow_main(arguments: list[str]) -> int:
    """Data operations precede generic persistence and never start a workflow."""
    from taskplane import blueprint as b, blueprint_catalog as catalog, blueprint_compile as compiler
    from taskplane import workflow as w, workflow_local as local
    try:
        if arguments in (["--help"], ["-h"]):
            print("workflow catalog|validate|preview|save|compile|check --workspace ROOT\n"
                  "validate/preview/save/compile: --definition FILE; preview: [--inputs FILE];\n"
                  "save: --out FILE; compile: --inputs FILE --out DIR; check: --run RUN")
            return 0
        action, options = local.workflow_options(arguments)
        workspace = workspace_binding.resolve_workspace(options["--workspace"])
        if not workspace.is_dir():
            raise b.BlueprintError("workspace_unavailable", "/workspace", "Workspace is not a directory.")
        if action == "catalog":
            result = catalog.catalog()
        elif action == "check":
            # Read the existing controller; no Session, graph scan or initialization.
            controller = flow._controller(workspace, flow.session_id({}))
            w.require(controller.adapter.state_exists(), "state_unavailable",
                      "The requested run has no existing workflow store.")
            state = controller.report(options["--run"], diagnostics=False)
            result = local.workflow_status(workspace, state) or {
                "status": "not_bound", "run": options["--run"], "diagnostics": []}
        else:
            definition = b.load_definition(workspace / options["--definition"])
            if action == "validate":
                result = {"status": "valid", "schema": definition["schema"],
                          "definition_digest": b.definition_digest(definition), "definition": definition}
            elif action == "save":
                result = b.save_definition(workspace, definition, options["--out"])
            else:
                inputs = _workflow_inputs(workspace / options["--inputs"]) if "--inputs" in options else {}
                result = (compiler.preview(workspace, definition, inputs) if action == "preview" else
                          compiler.compile_package(workspace, definition, inputs, options["--out"]))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
        return 2 if result.get("status") in {"invalid", "blocked"} or result.get("compatible") is False else 0
    except b.BlueprintError as exc:
        print(json.dumps(exc.result(), ensure_ascii=False))
        return 2
    except (w.Refusal, OSError, ValueError, KeyError, primitives.StateError) as exc:
        error = b.BlueprintError(getattr(exc, "reason", "invalid_workflow"), "/", str(exc),
                                 "Correct the request or inspect the existing run; no run was started.")
        print(json.dumps(error.result(), ensure_ascii=False))
        return 2


def _git(workspace: str, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=workspace, text=True,
                            encoding="utf-8", errors="replace", capture_output=True)
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Git could not read this workspace")
    return result.stdout


def _changed(workspace: str, base: str) -> list[str]:
    return sorted(set(_git_paths(workspace, "diff", "--name-only", "-z", base, "--")
                      + _git_paths(workspace, "ls-files", "-z", "--others", "--exclude-standard")))


def _git_paths(workspace: str, *args: str) -> list[str]:
    result = subprocess.run(["git", *args], cwd=workspace, capture_output=True)
    if result.returncode:
        raise ValueError(os.fsdecode(result.stderr).strip() or "Git could not read this workspace")
    return [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]


def _version(verify: bool) -> dict[str, Any]:
    manifests = [p for p in (ROOT / ".codex-plugin/plugin.json", ROOT / ".claude-plugin/plugin.json") if p.is_file()]
    versions = [json.loads(p.read_text())["version"] for p in manifests]
    market = ROOT / ".claude-plugin/marketplace.json"
    if market.is_file():
        data = json.loads(market.read_text())
        versions.extend([data["version"], data["plugins"][0]["version"]])
    result: dict[str, Any] = {"version": versions[0]}
    if verify:
        result["ok"] = len(set(versions)) == 1
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["workspace"]:
        return workspace_binding.main(arguments[1:])
    if arguments[:1] == ["workflow"]:
        return workflow_main(arguments[1:])
    if arguments[:1] == ["flow"]:
        return flow.main(arguments[1:], compact=True)
    if arguments and arguments[0] in flow.HOOK_NAMES:
        if len(arguments) != 1:
            print(json.dumps({"error": "Named native hooks accept their event on stdin only."}))
            return 2
        return flow.run_hook(arguments[0])
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("flow", help="Human-gated delivery and observations; use flow --help")
    commands.add_parser("workspace", help="Inspect, bind or recover the selected workspace; use workspace --help")
    commands.add_parser("workflow", help="Author, validate and compile reusable definitions; use workflow --help")
    version = commands.add_parser("version", help="Report the installed version")
    version.add_argument("--verify", action="store_true")
    help_command = commands.add_parser("help", help="Show the supported commands")
    help_command.add_argument("--md", action="store_true")
    graph = commands.add_parser("graph", help="Scan, inspect, and render source dependencies")
    graph.add_argument("--workspace")
    actions = graph.add_subparsers(dest="action", required=True)
    scan = actions.add_parser("scan")
    scan.add_argument("--decompose", action="store_true")
    scan.add_argument("--strict", action="store_true")
    impact = actions.add_parser("impact")
    impact.add_argument("--files")
    impact.add_argument("--base", default="HEAD")
    impact.add_argument("--depth", type=int, default=3)
    impact.add_argument("--json", action="store_true")
    html = actions.add_parser("html")
    html.add_argument("--files")
    html.add_argument("--base", default="HEAD")
    html.add_argument("--out")
    html.add_argument("--focus")
    edge = actions.add_parser("edge")
    edge.add_argument("src")
    edge.add_argument("dst")
    edge.add_argument("--kind", default="runtime")
    edge.add_argument("--note", default="")
    edge.add_argument("--confidence", default="high")
    board = commands.add_parser("dashboard", help="Render the shared delivery dashboard")
    board.add_argument("--workspace")
    board.add_argument("--out")
    board.add_argument("--run")
    review = commands.add_parser("review", help="Capture a source inventory for native review")
    review.add_argument("action", choices=["start"])
    review.add_argument("--workspace")
    review.add_argument("--scope", choices=["repository", "diff"], default="diff")
    review.add_argument("--base", default="HEAD")
    review.add_argument("--paths", help="Comma-separated source paths")
    lens = commands.add_parser("lens", help="Suggest lenses from source signals")
    lens.add_argument("--workspace")
    lens.add_argument("--files", required=True)
    lens.add_argument("--stage")
    args = parser.parse_args(arguments)
    try:
        if args.command == "help":
            if args.md:
                print((ROOT / "docs/cli-reference.md").read_text(), end="")
            else:
                parser.print_help()
            return 0
        if args.command == "version":
            result = _version(args.verify)
            print(json.dumps(result, indent=2))
            return 0 if result.get("ok", True) else 1
        workspace = str(workspace_binding.resolve_workspace(args.workspace))
        workspace_binding.ensure(workspace)
        if args.command == "graph":
            if args.action == "scan":
                data = depgraph.scan(workspace, decompose=args.decompose)
                flow.record_scan(Path(workspace), data)
                result = {"modules": len(data["modules"]), "edges": len(data["edges"]),
                          "files": len(data["files"]), "stored": depgraph._path(workspace)}
                if args.decompose:
                    result["components"] = len(data.get("components", []))
                quality = depgraph.scan_quality(data)
                if quality.get("degraded"):
                    result["graph_quality"] = quality
                print(json.dumps(result, indent=2))
                return int(args.strict and bool(quality.get("degraded")))
            if args.action == "edge":
                result = depgraph.record_edge(workspace, args.src, args.dst,
                    kind=args.kind, note=args.note, confidence=args.confidence)
            else:
                files = args.files.split(",") if args.files else _changed(workspace, args.base)
                if args.action == "impact":
                    result = depgraph.impact(workspace, files, max_depth=args.depth)
                else:
                    if not depgraph.load(workspace).get("modules"):
                        raise ValueError("No graph recorded; run graph scan first")
                    print(depgraph.to_html(workspace, files, out=args.out, focus=args.focus))
                    return 0
        elif args.command == "dashboard":
            output = flow.publish_dashboard(Path(workspace), args.run,
                output=Path(args.out) if args.out else None, select=bool(args.run))
            print(str(output))
            return 0
        elif args.command == "lens":
            result = graph_primitives.route_verdicts(workspace, args.files.split(","), stage=args.stage)
        else:
            head = _git(workspace, "rev-parse", "HEAD").strip()
            if args.scope == "repository":
                if _git(workspace, "diff", "HEAD", "--").strip():
                    raise ValueError("Tracked source differs from HEAD; use a diff review")
                files = _git_paths(workspace, "ls-files", "-z")
            else:
                files = _changed(workspace, args.base)
            if args.paths:
                wanted = args.paths.split(",")
                files = [p for p in files if p in wanted]
            files = [p for p in files if not p.startswith(".taskplane/")]
            for filename in files:
                (Path(workspace) / filename).resolve().relative_to(Path(workspace))
            if not files:
                raise ValueError("No selected source; use --scope repository to review tracked source")
            patch = "" if args.scope == "repository" else _git(workspace, "--literal-pathspecs", "diff", args.base, "--", *files)
            source = {"head": head, "scope": args.scope, "base": args.base,
                      "files": files, "patch": patch}
            output = Path(storage.tp_dir(workspace)) / "review-source.json"
            primitives.atomic_json(output, source)
            result = {"status": "ready", "source": str(output), "files": len(files)}
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError, primitives.StateError) as exc:
        print(f"taskplane: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
