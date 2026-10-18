"""Conservative verification reuse with source, runtime and command provenance.

These records preserve observations, including failures. They never carry gates.
"""
from __future__ import annotations

import hashlib
from copy import deepcopy
import os
from pathlib import Path
import platform
import subprocess
import sys
import uuid
from typing import Any, Callable

from . import depgraph, host_capabilities, primitives, workflow as w, workflow_evidence as evidence
from .context import Store, digest, encode

ENVIRONMENT = ("PATH", "LANG", "LC_ALL", "TZ", "PYTHONHASHSEED", "PYTHONPATH", "NODE_ENV", "CI",
               "HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
               "XDG_CACHE_HOME", "XDG_STATE_HOME", "TMPDIR", "TMP", "TEMP",
               "PYTHONDONTWRITEBYTECODE", "PYTEST_DISABLE_PLUGIN_AUTOLOAD")

INPUT_POLICY = "accepted-plan-roles/v1"
PUBLICATION = ".taskplane/context-v1/verification-publication"


def _module(graph: dict[str, Any], relative: str) -> str | None:
    matches = [name for name in graph.get("modules", {})
               if name == "." or relative == name or relative.startswith(name + "/")]
    return max(matches, key=len) if matches else None


def _packet_context(workspace: Path, state: dict[str, Any],
                    retained: list[dict[str, str]], output_path: str | None) -> list[dict[str, str]]:
    """Carry exact packet roles separately from the inputs they describe.

    A bound owned draft establishes its role before submission. Retained roles
    belong to their original visits, even when a later phase consumes the check.
    """
    stages = {stage["id"]: stage for stage in state["visits"][:state["index"] + 1]
              if stage["phase"] in {"build", "evaluate"} and not stage.get("superseded")}
    plan = w.accepted_plan(state)["packet"]["output"]

    def allowed(stage: dict[str, Any]) -> set[str]:
        paths = set(state["scope"]["paths"].get(stage["phase"], []))
        if stage["phase"] == "build":
            paths &= set(plan["write_scope"])
        return paths | set(stage.get("artifact_paths", []))

    roles: set[tuple[str, str]] = set()
    retained_roles: set[tuple[str, str]] = set()
    w.require(isinstance(retained, list), "invalid_context", "Invalid producing packet context.")
    for row in retained:
        w.require(isinstance(row, dict) and set(row) == {"visit", "path"}
                  and isinstance(row.get("visit"), str) and isinstance(row.get("path"), str),
                  "invalid_context", "Invalid producing packet context.")
        stage = stages.get(row["visit"], {})
        w.require(stage and row["path"] in allowed(stage), "scope_violation",
                  "Packet context is outside its original phase ownership.")
        evidence.path(workspace, row["path"])
        retained_roles.add((row["visit"], row["path"]))
    for captured_stage in evidence._packets(state):
        packet = captured_stage["packet"]
        stage = stages.get(packet.get("visit", captured_stage.get("id")), {})
        captured = packet.get("output_path")
        if stage and captured is not None and captured in allowed(stage):
            roles.add((stage["id"], captured))
    for stage in stages.values():
        for relative in allowed(stage):
            if not evidence.path(workspace, relative).is_file():
                continue
            try:
                draft = evidence.object_file(workspace, relative)
            except w.Refusal:
                continue
            if (draft.get("schema") == "taskplane.phase-output/v1"
                    and draft.get("run") == state["run"] and draft.get("visit") == stage["id"]
                    and draft.get("phase") == stage["phase"]):
                roles.add((stage["id"], relative))
    for visit, relative in retained_roles:
        w.require(not evidence.path(workspace, relative).exists() or (visit, relative) in roles,
                  "invalid_context", "Producing context cannot turn an existing input into a packet.")
        roles.add((visit, relative))
    if output_path is not None:
        stage = w.current(state)
        w.require(stage["id"] in stages and output_path in allowed(stage), "scope_violation",
                  "Packet path is outside phase ownership.")
        target = evidence.path(workspace, output_path)
        w.require(not target.exists() or (stage["id"], output_path) in roles,
                  "invalid_context", "An existing packet must be bound to its producing run and visit.")
        roles.add((stage["id"], output_path))
    return [{"visit": visit, "path": path} for visit, path in sorted(roles)]


def _inputs(provenance: dict[str, Any]) -> dict[str, Any]:
    return {name: value for name, value in provenance.items() if name != "packet_context"}


def same_inputs(prior: dict[str, Any], current: dict[str, Any]) -> bool:
    """Packet-role transport is context, not a source dependency or a gate."""
    return _inputs(prior) == _inputs(current)


def _roles(workspace: Path, state: dict[str, Any], *, check_id: str,
           history_path: str | None = None, log_path: str | None = None,
           visit: str | None = None, packet_context: list[dict[str, str]] | None = None,
           graph: dict[str, Any] | None = None) -> set[str]:
    """Resolve exact write-only metadata from authority, never from suffixes.

    Existing histories supply roles for other producers even before submission.
    Their immutable attempts must prove the role; writable source is not metadata.
    """
    plan = w.accepted_plan(state)["packet"]["output"]
    w.require(evidence.typed_plan(plan), "invalid_context", "Input roles need an accepted typed Plan.")
    writes = set(plan["write_scope"])
    writes.update(p for paths in state["scope"]["paths"].values() for p in paths)
    writes.update(p for stage in state["visits"] for p in stage.get("artifact_paths", []))
    w.require(not {Path(p).as_posix() for p in writes} & {PUBLICATION, PUBLICATION + ".lock"},
              "invalid_context", "Verification publication coordination cannot be a task output.")
    checks = {row["id"]: row for row in plan["verification_strategy"]["checks"]}
    w.require(check_id in checks, "invalid_context", "Input roles need a declared check.")
    check = checks[check_id]
    tasks = {row["id"]: row for row in plan["task_dag"]}
    task = tasks[check["task"]]
    reads = set(check["source_inputs"] + check["test_inputs"] + evidence.read_inputs(state, task))
    stages = {row["id"]: row for row in state["visits"][:state["index"] + 1]}
    producers = [row for row in stages.values() if row["phase"] in {"build", "evaluate"}]
    producer = stages.get(visit, {}) if visit is not None else (producers[-1] if producers else {})
    w.require(producer.get("phase") in {"build", "evaluate"}, "invalid_context",
              "Input roles need a producing Build or Evaluate visit.")

    def owned(cid: str, stage: dict[str, Any], history: str, log: str | None) -> None:
        requirement = checks[cid]
        if stage["phase"] == "build":
            owner = tasks[requirement["task"]]
            histories = {row["path"] for row in plan.get("build_outputs", [])
                         if row["kind"] == "verification_history" and row["task"] == owner["id"]}
            if not any(row["kind"] == "verification_history" for row in plan.get("build_outputs", [])):
                histories = set(owner["paths"])
            logs = set(requirement["evidence_outputs"])
        else:
            histories = logs = set(state["scope"]["paths"].get("evaluate", []) + stage.get("artifact_paths", []))
        w.require(history in histories and (log is None or log in logs and log != history),
                  "invalid_context", "Verification metadata needs its exact producing owner.")
        evidence.path(workspace, history)
        if log is not None:
            evidence.path(workspace, log)

    histories = {row["path"] for row in plan.get("build_outputs", []) if row["kind"] == "verification_history"}
    history_sources: list[tuple[str, dict[str, Any]]] = []
    metadata = {row["path"] for row in plan.get("build_outputs", [])}
    metadata.update(p for row in checks.values() for p in row["evidence_outputs"])
    own = set(check["evidence_outputs"])
    if history_path is not None:
        owned(check_id, producer, history_path, log_path)
        histories.add(history_path)
        own.update([history_path, history_path + ".lock"])
        metadata.update(own)
    w.require(log_path is None or history_path is not None, "invalid_context", "A log needs its history role.")
    if log_path is not None:
        own.add(log_path)
        metadata.add(log_path)

    # A phase's whole write scope can also contain source. Only captured or
    # bound draft packets establish metadata roles before submission.
    for stage in evidence._packets(state):
        packet = stage["packet"]
        if packet.get("output_path"):
            metadata.add(packet["output_path"])
        captured = packet.get("verification", {})
        if captured.get("history_path"):
            metadata.add(captured["history_path"])
            histories.add(captured["history_path"])
            if isinstance(captured.get("history"), dict):
                history_sources.append((captured["history_path"], captured["history"]))
    metadata.update(row["path"] for row in packet_context or [])

    # Expand known and discovered histories alike. Being metadata does not
    # mean a history's sibling log roles have already been resolved. Captured
    # snapshots retain old roles if their mutable history file is unavailable;
    # current files also supply attempts appended since the snapshot.
    candidates = (set(plan["write_scope"]) | set(state["scope"]["paths"].get("evaluate", []))
                  | histories | {p for stage in producers for p in stage.get("artifact_paths", [])})
    store = Store(workspace)
    for relative in sorted(candidates):
        target = evidence.path(workspace, relative)
        if not target.is_file():
            continue
        try:
            history = evidence.object_file(workspace, relative)
        except w.Refusal:
            continue
        history_sources.append((relative, history))
    for relative, history in history_sources:
        stage = stages.get(history.get("visit"), {}) if isinstance(history.get("visit"), str) else {}
        attempts = history.get("attempts")
        if (history.get("schema") != evidence.VERIFICATION_HISTORY or history.get("run") != state["run"]
                or stage.get("phase") not in {"build", "evaluate"} or not isinstance(attempts, list) or not attempts):
            continue
        try:
            for row in attempts:
                cid = row["check_id"]
                w.require(cid in checks and row["run"] == state["run"] and row["visit"] == stage["id"],
                          "invalid_context", "Foreign metadata role.")
                owned(cid, stage, relative, row["evidence"])
                record = store.resolve(row["record_ref"])
                w.require(isinstance(record, dict)
                          and record.get("schema") == "taskplane.verification-record/v1",
                          "invalid_context", "History needs an immutable verification record.")
                context = record.get("source_context", {})
                w.require(isinstance(context, dict)
                          and record.get("verification") == {name: row[name] for name in
                          ("id", "check_id", "run", "visit", "kind", "environment")}
                          and record.get("log_path") == row["evidence"]
                          and all(context.get(name) == row[name] for name in ("run", "visit", "check_id"))
                          and context.get("log_path") == row["evidence"]
                          and isinstance(context.get("history_path"), str),
                          "invalid_context", "History lacks immutable producing context.")
                # A complete history alias preserves original record context.
                # Both filenames need the same producing ownership; continuity
                # and log integrity are independently checked at submission.
                owned(cid, stage, context["history_path"], row["evidence"])
            metadata.add(relative)
            histories.add(relative)
            metadata.update(row["evidence"] for row in attempts)
        except (w.Refusal, KeyError, TypeError):
            continue
    # Locks are derived from typed history roles, not a blanket *.lock rule.
    metadata.update(p + ".lock" for p in histories)
    # Literal aliases must not give the same file contradictory roles.
    metadata = {Path(p).as_posix() for p in metadata}
    reads = {Path(p).as_posix() for p in reads}
    own = {Path(p).as_posix() for p in own}
    # Scanner-proven reads also outrank an output declaration. A generated
    # report consumed by real source is a dependency even when the task omitted
    # the explicit read. Evidence's own references do not prove runtime reads.
    graph = depgraph.load(str(workspace)) if graph is None else graph
    module: Callable[[str], str | None] = lambda p: _module(graph, p)
    while True:
        excluded = metadata - reads
        edges = _source_edges(graph, excluded, module)
        closure = {module(p) for p in reads}
        while True:
            expanded = closure | {edge.get(side) for edge in edges for side in ("from", "to")
                                   if edge.get("from") in closure or edge.get("to") in closure}
            if expanded == closure:
                break
            closure = expanded
        dependencies: set[str] = set()
        imports: set[str] = set()
        for relative, row in graph.get("files", {}).items():
            if relative not in excluded and module(relative) in closure:
                dependencies.update(Path(p).as_posix() for p in row.get("refs", []))
                imports.update(row.get("imports", []))
        dependencies.update(p for p in excluded if p in graph.get("files", {})
                            and not graph["files"][p].get("artifact") and module(p) in imports)
        discovered = dependencies & excluded
        if not discovered:
            break
        reads.update(discovered)
    w.require(not own & reads, "invalid_context", "A check cannot read and overwrite its own verification metadata.")
    return metadata - reads


def _source_edges(graph: dict[str, Any], excluded: set[str],
                  module: Callable[[str], str | None]) -> list[dict[str, Any]]:
    """Remove scanner edges supported only by write-only metadata references."""
    removed: set[tuple[str | None, str | None, str]] = set()
    retained: set[tuple[str | None, str | None, str]] = set()
    for relative, row in graph.get("files", {}).items():
        origins = removed if relative in excluded else retained
        source = module(relative)
        origins.update((source, module(target), depgraph._ref_kind(relative, target)) for target in row.get("refs", []))
        origins.update((source, target, "imports") for target in row.get("imports", []))
    return [edge for edge in graph.get("edges", []) if edge.get("recorded")
            or edge.get("source") != "scanner"
            or (edge.get("from"), edge.get("to"), edge.get("kind")) not in removed - retained]


def key(workspace: Path, *, paths: list[str], tests: list[str], criteria: list[str],
        command: list[str], tool: str, contract: str,
        environment: dict[str, str] | None = None, state: dict[str, Any] | None = None,
        check_id: str | None = None, history_path: str | None = None,
        log_path: str | None = None, visit: str | None = None,
        output_path: str | None = None,
        packet_context: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Snapshot real inputs consistently with governed evidence publication.

    The short internal lock covers fingerprinting/publication, never command
    execution. A concurrent producer's half-published history is not source.
    """
    options: dict[str, Any] = dict(paths=paths, tests=tests, criteria=criteria, command=command,
                   tool=tool, contract=contract, environment=environment, state=state,
                   check_id=check_id, history_path=history_path, log_path=log_path,
                   visit=visit, output_path=output_path, packet_context=packet_context)
    if state is None:
        return _key(workspace, **options)
    with primitives.file_lock(str(evidence.path(workspace, PUBLICATION))):
        return _key(workspace, **options)


def _key(workspace: Path, *, paths: list[str], tests: list[str], criteria: list[str],
         command: list[str], tool: str, contract: str,
         environment: dict[str, str] | None = None, state: dict[str, Any] | None = None,
         check_id: str | None = None, history_path: str | None = None,
         log_path: str | None = None, visit: str | None = None,
         output_path: str | None = None,
         packet_context: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Fingerprint relevant inputs; optional graph coverage can qualify reuse.

    Missing coverage never prevents a fresh check. An unrelated workspace edit
    cannot invalidate a covered component by making a global scan stale.
    """
    workspace = workspace.resolve()
    graph = depgraph.load(str(workspace))
    excluded: set[str] = set()
    if state is not None:
        w.require(check_id is not None, "invalid_context", "Input roles need the declared check ID.")
        assert check_id is not None
        requirement: dict[str, Any] = next((row for row in w.accepted_plan(state)["packet"]["output"]
                            ["verification_strategy"]["checks"] if row["id"] == check_id), {})
        w.require(set(paths) == set(requirement.get("source_inputs", []))
                  and set(tests) == set(requirement.get("test_inputs", []))
                  and bool(criteria) and set(criteria) <= set(requirement.get("criteria", []))
                  and command == requirement.get("command"), "invalid_context",
                  "Verification source contract differs from its accepted check.")
        packet_context = _packet_context(workspace, state, packet_context or [], output_path)
        excluded = _roles(workspace, state, check_id=check_id, history_path=history_path,
                          log_path=log_path, visit=visit, packet_context=packet_context, graph=graph)
        w.require(not excluded & set(paths + tests), "invalid_context", "Verification inputs conflict with metadata roles.")
    else:
        w.require(all(value is None for value in (check_id, history_path, log_path, visit, output_path, packet_context)),
                  "invalid_context", "Metadata roles require explicit workflow state.")
    complete = bool(graph)
    quality = depgraph.scan_quality(graph) if graph else {"degraded": True}
    meta = graph.get("meta", {})
    complete = (complete and not quality.get("degraded") and quality.get("mode") == "components"
                and meta.get("source_coverage", {}).get("complete") is True
                and meta.get("module_confidence") != "low")
    modules = graph.get("modules", {})
    def module(path: str) -> str | None:
        return _module(graph, path)
    edges = _source_edges(graph, excluded, module) if excluded else graph.get("edges", [])
    selected = {module(path) for path in paths + tests}
    complete = complete and None not in selected and bool(paths and tests and criteria and command and tool and contract)
    closure = {name for name in selected if name is not None}
    while True:
        expanded = closure | {str(edge[side]) for edge in edges
                               for side in ("from", "to")
                               if edge.get("from") in closure or edge.get("to") in closure}
        if expanded == closure:
            break
        closure = expanded
    # A complete workspace scan is not complete runtime dependency coverage.
    # External nodes name packages, but do not bind their installed contents;
    # even an unchanged PYTHONPATH can point at a changed implementation.
    unverified_dependencies = sorted(str(name) for name in closure
                                    if name not in modules
                                    or modules[name].get("kind") == "external")
    complete = complete and not unverified_dependencies
    # Reuse the scanner's artifact, manifest and exclusion policy. Enumerate
    # names once, but hash only covered components and shared root resolution
    # inputs. Ordinary root documents and unrelated components are not inputs.
    inventory, code, artifacts, excludes, exclude_error = depgraph._scan_inventory(str(workspace))
    inventory = {p: value for p, value in inventory.items() if p not in excluded}
    code = [p for p in code if p not in excluded]
    artifacts = [p for p in artifacts if p not in excluded]
    scanned_inputs = meta.get("source_inputs", {})
    candidates = {relative: True for relative in set(inventory) | set(scanned_inputs.get("files", []))
                  if relative not in excluded and (module(relative) in closure or "/" not in relative)}
    current_inputs = depgraph.source_inputs(str(workspace), inventory=(candidates,
        [relative for relative in code if module(relative) in closure],
        [relative for relative in artifacts if module(relative) in closure], excludes, exclude_error))
    census = current_inputs["content"]
    scanned = {relative: fingerprint for relative, fingerprint in scanned_inputs.get("content", {}).items()
               if relative not in excluded and (module(relative) in closure or relative in census)}
    resolution = {field: current_inputs[field] for field in
                  ("components", "scanner", "excludes", "exclude_error")}
    complete = (complete and census == scanned and all(census.values())
                and all(value == scanned_inputs.get(field) for field, value in resolution.items())
                and exclude_error is None)
    # Even unsupported file formats can change name-based source resolution.
    # Their addition/removal requires a fresh scan rather than assuming that
    # absence from the scanner's content parsers means no dependency exists.
    resolution["files"] = sorted(relative for relative in inventory
                                 if module(relative) in closure or relative in census)
    complete = complete and resolution["files"] == sorted(
        relative for relative in scanned_inputs.get("files", [])
        if relative not in excluded and (module(relative) in closure or relative in census))
    # Parsing coverage and verification identity are different: an admitted
    # runtime data/config file still affects execution when no parser knows its
    # format. Bind its contents at record creation and every later comparison.
    files = (set(paths + tests) | set(resolution["files"]) | set(census) | set(scanned)
             | {path for path in graph.get("files", {}) if path not in excluded and module(path) in closure})
    fingerprints: dict[str, str] = {}
    for path in sorted(files):
        target = evidence.path(workspace, path)
        fingerprints[path] = hashlib.sha256(evidence.read(workspace, path)).hexdigest() if target.is_file() else "missing"
    executable = Path(command[0]) if command else Path("/nonexistent")
    complete = complete and executable.is_absolute() and executable.is_file()
    executable_hash = hashlib.sha256(executable.read_bytes()).hexdigest() if executable.is_file() else "unknown"
    # Only hashes of explicitly allowlisted nonsecret settings enter persisted data.
    env = environment if environment is not None else dict(os.environ)
    body = {"schema": "taskplane.verification-key/v1", "eligible": bool(complete),
            "paths": sorted(paths), "tests": sorted(tests),
            "files": fingerprints, "modules": sorted(str(x) for x in closure),
            "source_resolution": resolution,
            "criteria": sorted(criteria), "command": command, "tool": tool, "contract": contract,
            "runtime": {"executable": str(executable), "sha256": executable_hash,
                        "python": sys.version, "platform": platform.platform(),
                        "taskplane": host_capabilities.runtime_identity()},
            "environment": digest({name: env.get(name) for name in ENVIRONMENT}),
            "unverified_dependencies": unverified_dependencies,
            "coverage": "complete" if complete else "unknown"}
    if state is not None:
        body["input_policy"] = INPUT_POLICY
        body["dependency_edges"] = sorted([edge for edge in edges if edge.get("from") in closure
                                          or edge.get("to") in closure], key=encode)
    result = {**body, "digest": digest(body)}
    if state is not None:
        result["packet_context"] = packet_context
    return deepcopy(result)


def contribution_key(workspace: Path, *, paths: list[str], tests: list[str],
                     outputs: list[str], criteria: dict[str, Any], command: list[str],
                     tool: str, contract: str,
                     environment: dict[str, str] | None = None, state: dict[str, Any] | None = None,
                     check_id: str | None = None, history_path: str | None = None,
                     log_path: str | None = None, visit: str | None = None,
                     output_path: str | None = None) -> dict[str, Any]:
    """Bind an individually reusable contribution, including its output bytes.

    Call again at acceptance/reuse. Optional relevant graph coverage is not
    evidence of external runtime dependency identity. Such dependencies remain
    unknown and cannot qualify reused verification.
    A local closure census detects added/deleted dependency files without making
    an unrelated component's edit invalidate every contribution.
    """
    provenance = key(workspace, paths=paths, tests=tests, criteria=sorted(criteria),
                     command=command, tool=tool, contract=contract, environment=environment,
                     state=state, check_id=check_id, history_path=history_path, log_path=log_path,
                     visit=visit, output_path=output_path)
    # Contribution identity binds actual inputs and outputs, not incidental
    # packet-role transport used to reconstruct those inputs.
    provenance = _inputs(provenance)
    graph = depgraph.load(str(workspace))
    closure = set(provenance['modules'])
    artifacts = {}
    for path in sorted(outputs):
        target = evidence.path(workspace, path)
        artifacts[path] = hashlib.sha256(evidence.read(workspace, path)).hexdigest() if target.is_file() else 'missing'
    body = {"schema": "taskplane.contribution-key/v1", "verification": provenance,
            "source_dependencies": dict(provenance['files']),
            "dependency_edges": provenance.get('dependency_edges', sorted([edge for edge in graph.get('edges', [])
                                         if edge.get('from') in closure or edge.get('to') in closure],
                                        key=lambda edge: encode(edge))),
            "closure_census": sorted(provenance['files']), "outputs": artifacts,
            "execution_environment_digest": digest(environment if environment is not None else dict(os.environ)),
            "criteria": criteria, "coverage": provenance['coverage'],
            "eligible": provenance['eligible'] and bool(outputs and criteria)
                        and all(value != 'missing' for value in artifacts.values())}
    return deepcopy({**body, "digest": digest(body)})


def contribution_matches(prior: dict[str, Any], current: dict[str, Any]) -> bool:
    """Equality never transfers semantic acceptance or worker/context identity."""
    def valid(value: dict[str, Any]) -> bool:
        if not isinstance(value, dict):
            return False
        verification = value.get('verification', {})
        return (isinstance(verification, dict)
                and verification.get('schema') == 'taskplane.verification-key/v1'
                and verification.get('eligible') is True and verification.get('coverage') == 'complete'
                and verification.get('digest') == digest({k: v for k, v in verification.items() if k != 'digest'})
                and bool(verification.get('tests') and verification.get('paths') and verification.get('command'))
                and not verification.get('unverified_dependencies')
                and isinstance(value.get('outputs'), dict) and bool(value['outputs'])
                and all(primitives.is_sha256(item) for item in value['outputs'].values())
                and value.get('source_dependencies') == verification.get('files')
                and value.get('closure_census') == sorted(verification.get('files', {}))
                and sorted(value.get('criteria', {})) == verification.get('criteria')
                and value.get('schema') == 'taskplane.contribution-key/v1'
                and value.get('eligible') is True and value.get('coverage') == 'complete'
                and value.get('digest') == digest({k: v for k, v in value.items() if k != 'digest'}))
    return valid(prior) and valid(current) and prior == current


def record(store: Store, provenance: dict[str, Any], *, status: str, producer: str,
           result: dict[str, Any], findings: list[dict[str, Any]] | None = None,
           verification: dict[str, Any] | None = None, log_ref: dict[str, Any] | None = None,
           log_path: str | None = None, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    w.require(status in {"pass", "fail", "unknown"} and bool(producer),
              "invalid_context", "Verification needs an observed status and producer.")
    w.require(status != "pass" or result.get("returncode") == 0,
              "invalid_context", "Passing verification requires a successful observed command.")
    body: dict[str, Any] = {"schema": "taskplane.verification-record/v1", "key": provenance,
            "status": status, "producer": producer, "result": result,
            "findings": findings or [], "authority": "none"}
    if verification is not None:
        body.update(verification=verification, log_ref=log_ref, log_path=log_path)
    if source_context is not None:
        body["source_context"] = source_context
    return store.put("verification-record", body)


def lookup(store: Store, reference: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    prior = store.resolve(reference)
    w.require(isinstance(prior, dict) and prior.get("schema") == "taskplane.verification-record/v1",
              "invalid_context", "Invalid verification record.")
    eligible = (current.get("eligible") is True and same_inputs(prior.get("key", {}), current)
                and prior.get("status") == "pass" and prior.get("result", {}).get("returncode") == 0)
    return {"status": "reused" if eligible else "miss", "prior_status": prior.get("status"),
            "producer": prior.get("producer"), "findings": prior.get("findings", []),
            "evidence_ref": reference, "key_digest": current.get("digest"), "approval": "not_transferred"}


def run_check(workspace: Path, provenance: dict[str, Any], *, producer: str,
              timeout: float = 120, history_path: str | None = None,
              run: str | None = None, visit: str | None = None, check_id: str | None = None,
              kind: str | None = None, environment: str | None = None,
              log_path: str | None = None, coverage_details: dict[str, Any] | None = None,
              state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute once, preserving fail/unknown attempts before returning their ref.

    Typed callers explicitly bind run/visit and owned history/log paths. There is
    no implicit active-run lookup. Legacy callers retain the reference-only API.
    """
    typed = history_path is not None
    if typed:
        w.require(state is not None and state.get("run") == run and w.current(state)["id"] == visit,
                  "invalid_context", "Typed checks need their explicit current workflow state.")
        w.require(all(isinstance(value, str) and value for value in (run, visit, check_id, log_path))
                  and kind in evidence.CHECK_KINDS and environment in evidence.CHECK_ENVIRONMENTS,
                  "invalid_context", "Typed checks need run, visit, check ID, kind, environment and log path.")
        assert history_path is not None and log_path is not None
        evidence.path(workspace, history_path)
        w.require(history_path != log_path and not evidence.path(workspace, log_path).exists(),
                  "invalid_context", "Each attempt needs a new distinct declared log path.")
        assert state is not None and check_id is not None
        requirement: dict[str, Any] = next((row for row in w.accepted_plan(state)["packet"]["output"]
                            ["verification_strategy"]["checks"] if row["id"] == check_id), {})
        w.require(requirement.get("kind") == kind and requirement.get("environment") == environment,
                  "invalid_context", "Typed check kind/environment differs from its Plan.")
        _roles(workspace, state, check_id=check_id, history_path=history_path,
               log_path=log_path, visit=visit,
               packet_context=_packet_context(workspace, state, provenance.get("packet_context", []), None))
    else:
        w.require(all(value is None for value in (run, visit, check_id, kind, environment, log_path)),
                  "invalid_context", "Typed check metadata requires an explicit history path.")

    def current() -> dict[str, Any]:
        options: dict[str, Any] = {"state": state, "check_id": check_id, "history_path": history_path,
                                   "log_path": log_path, "visit": visit,
                                   "packet_context": provenance.get("packet_context", [])} if typed else {}
        return key(workspace, paths=provenance["paths"], tests=provenance["tests"],
                   criteria=provenance["criteria"], command=provenance["command"],
                   tool=provenance["tool"], contract=provenance["contract"],
                   **options)
    status = "unknown"
    result: dict[str, Any] = {"returncode": None, "stdout": "", "stderr": ""}
    try:
        if not same_inputs(current(), provenance):
            if not typed:
                raise w.Refusal("invalid_context", "Verification inputs changed before execution.")
            result["reason"] = "input_drift_before_execution"
        else:
            completed = subprocess.run(provenance["command"], cwd=workspace, capture_output=True,
                text=True, timeout=timeout, check=False,
                env={name: os.environ[name] for name in ENVIRONMENT if name in os.environ})
            result.update(returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)
            status = "pass" if completed.returncode == 0 else "fail"
            if not same_inputs(current(), provenance):
                status = "unknown"
                result["reason"] = "input_drift_during_execution"
    except subprocess.TimeoutExpired as exc:
        def decoded(value: Any) -> str:
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")
        result.update(reason="timeout", timeout=timeout, stdout=decoded(exc.stdout), stderr=decoded(exc.stderr))
        status = "unknown"
    except OSError as exc:
        result.update(reason="launch_error", error_type=type(exc).__name__, errno=exc.errno)
        status = "unknown"
    except w.Refusal:
        if not typed:
            raise
        result["reason"] = "input_fingerprint_unavailable"
        status = "unknown"
    if coverage_details is not None:
        result["coverage_details"] = coverage_details
    store = Store(workspace)
    if not typed:
        return record(store, provenance, status=status, producer=producer, result=result)
    assert history_path is not None and log_path is not None
    target = evidence.path(workspace, history_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with primitives.file_lock(str(evidence.path(workspace, PUBLICATION))), \
            primitives.file_lock(str(target)):
        history = evidence.object_file(workspace, history_path) if target.exists() else {
            "schema": evidence.VERIFICATION_HISTORY, "run": run, "visit": visit,
            "coverage": "declared_producer", "attempts": []}
        w.require(history.get("schema") == evidence.VERIFICATION_HISTORY and history.get("run") == run
                  and history.get("visit") == visit
                  and history.get("coverage") == "declared_producer" and isinstance(history.get("attempts"), list),
                  "invalid_context", "Cannot append to a foreign verification history.")
        attempts = history["attempts"]
        w.require(all(isinstance(row, dict) and row.get("sequence") == number
                      for number, row in enumerate(attempts, 1)),
                  "invalid_context", "Existing verification history sequence is corrupt.")
        binding = {"id": uuid.uuid4().hex, "check_id": check_id, "run": run, "visit": visit,
                   "kind": kind, "environment": environment}
        raw = encode(result) + b"\n"
        log_target = evidence.path(workspace, log_path)
        log_target.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation protects old failure evidence, including races with
        # another producer. Only the owning task should append this history.
        fd = os.open(log_target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        log_ref = store.put("verification-log", {"path": log_path, "encoding": "utf-8",
                            "text": raw.decode(), "sha256": hashlib.sha256(raw).hexdigest()})
        ref = record(store, provenance, status=status, producer=producer, result=result,
                     verification=binding, log_ref=log_ref, log_path=log_path,
                     source_context={"run": run, "visit": visit, "check_id": check_id,
                                     "history_path": history_path, "log_path": log_path,
                                     "packet_context": provenance.get("packet_context", [])})
        attempts.append({**binding, "sequence": len(attempts) + 1, "status": status,
                         "record_ref": ref, "log_ref": log_ref, "evidence": log_path})
        history["visit"] = visit
        primitives.atomic_json(target, history)
        return ref


def revalidate(workspace: Path, prior: dict[str, Any], *, state: dict[str, Any] | None = None,
               output_path: str | None = None) -> dict[str, Any]:
    """Rebuild a record's source identity using its actual producing context."""
    saved = prior["key"]
    options: dict[str, Any] = {}
    if saved.get("input_policy") == INPUT_POLICY:
        context = prior.get("source_context", {})
        binding = prior.get("verification", {})
        w.require(state is not None and context.get("run") == state.get("run")
                  and all(context.get(name) == binding.get(name) for name in ("run", "visit", "check_id"))
                  and context.get("log_path") == prior.get("log_path"),
                  "invalid_context", "Role-aware reuse needs the producing workflow/check context.")
        options = {name: context[name] for name in ("visit", "check_id", "history_path", "log_path")}
        options.update(state=state, output_path=output_path,
                       packet_context=context.get("packet_context", []))
    return key(workspace, **{name: saved[name] for name in
               ("paths", "tests", "criteria", "command", "tool", "contract")}, **options)


def inherited(workspace: Path, reference: dict[str, Any], *, state: dict[str, Any] | None = None) -> dict[str, Any]:
    store = Store(workspace)
    prior = store.resolve(reference)
    try:
        current = revalidate(workspace, prior, state=state)
    except w.Refusal:
        current = {"eligible": False}
    return lookup(store, reference, current)
