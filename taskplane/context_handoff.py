"""Current-visit context delivery and receipts; the Controller still owns every gate."""
from __future__ import annotations

import base64
import hashlib
from copy import deepcopy
import json
import re
import shlex
from pathlib import Path
from typing import Any, Sequence
import uuid

from . import depgraph, primitives, workflow as w, workflow_evidence as evidence
from .context import (Store, digest, encode, signed, PAGE_LIMIT, OBJECT_LIMIT, REFERENCE_SCHEMA,
                      PageIndex, DeliveryJournal, graph_coverage, count, bytes_read)
from .context_views import PHASE_BYTES, collection, shared_bodies, view, view_source_key
from .context_delivery import cli_metrics, observed

CONTRACT = "bounded/v1"
SEMANTIC_CONTRACT = "bounded/v2"
BRIEF_CONTRACT = "bounded/v3"
SELECTION_SCHEMA = "taskplane.context-selection/v1"
EXCLUSIONS = ["predecessor_conversation", "predecessor_tool_history", "private_runtime",
              "secret_values", "executable_approval"]


def consumed_inputs(keys: list[str]) -> list[str] | dict[str, Any]:
    """Bound receipt metadata; the immutable receipt retains every returned root."""
    if len(keys) <= 64:
        return keys
    return {"schema": "taskplane.consumed-inputs/v1", "count": len(keys), "sha256": digest(keys)}


def binding(state: dict[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if state.get('context_contract') == BRIEF_CONTRACT:
        from .host_capabilities import runtime_identity
        extra = {'context_contract': BRIEF_CONTRACT,
                 'selection_digest': digest(state.get('context_selection')),
                 'requirements_epoch': state.get('requirements_epoch', 0),
                 'runtime_digest': digest(runtime_identity())}
    return {**{key: state[key] for key in ("workspace", "root", "run", "revision")},
            "visit": w.current(state)["id"], "scope_digest": primitives.content_fingerprint(state["scope"]),
            **({"task_generation": state["task_generation"]} if "task_generation" in state else {}), **extra}


def context_action(workspace: Path, run: str, task: str | None = None, *,
                   operation: str | None = None, key: str | None = None) -> str:
    """An executable logical command retaining exactly this consumer's selectors."""
    args = ["flow", "context", "--workspace", str(workspace.resolve()), "--run", run]
    if task is not None:
        args += ["--task", task]
    if operation is not None and key is not None:
        args += ["--" + operation, key]
    return shlex.join(args)


def text_body(raw: bytes) -> dict[str, Any]:
    try:
        return {"encoding": "utf-8", "text": raw.decode("utf-8")}
    except UnicodeError:
        return {"encoding": "base64", "text": base64.b64encode(raw).decode("ascii")}


def source_read(workspace: Path, relative: str) -> bytes:
    return evidence.read(workspace, relative)


def select_bytes(raw: bytes, selector: dict[str, Any]) -> bytes:
    """Versioned exact extraction; no evaluation, fuzzy search or semantic selection."""
    w.require(isinstance(selector, dict) and selector.get('version') == 1,
              'invalid_context', 'An exact versioned input selector is required.')
    kind = selector.get('kind')
    if kind == 'utf8-bytes':
        start, end = selector.get('start'), selector.get('end')
        w.require(set(selector) == {'version', 'kind', 'start', 'end'}
                  and type(start) is int and type(end) is int and 0 <= start < end <= len(raw),
                  'invalid_context', 'Invalid UTF-8 input range.')
        try:
            raw.decode('utf-8'); raw[:start].decode('utf-8'); selected = raw[start:end]
            selected.decode('utf-8')
        except UnicodeError:
            raise w.Refusal('invalid_context', 'Input range must preserve UTF-8 character boundaries.') from None
        return selected
    w.require(kind == 'json-pointer' and set(selector) == {'version', 'kind', 'pointer'},
              'invalid_context', 'Unsupported input selector.')
    pointer = selector.get('pointer')
    w.require(isinstance(pointer, str) and (pointer == '' or pointer.startswith('/'))
              and not re.search(r'~(?:[^01]|$)', pointer), 'invalid_context', 'Invalid JSON pointer.')
    try:
        value = json.loads(raw)
        for part in pointer.split('/')[1:] if pointer else []:
            key = part.replace('~1', '/').replace('~0', '~')
            if isinstance(value, list):
                w.require(bool(re.fullmatch(r'0|[1-9][0-9]*', key)), 'invalid_context',
                          'JSON array selector requires an exact index.')
                value = value[int(key)]
            else:
                w.require(isinstance(value, dict), 'invalid_context', 'JSON pointer crosses a scalar.')
                value = value[key]
    except (ValueError, UnicodeError, KeyError, IndexError):
        raise w.Refusal('invalid_context', 'Input JSON pointer is missing or malformed.') from None
    return encode(value)


def compact_inputs(workspace: Path, state: dict[str, Any], task: str, *, preview_dependencies: bool = False
                   ) -> tuple[list[dict[str, Any]], list[str], list[str], dict[str, Any]]:
    """Build only the reviewed task brief; retain full predecessor records as support."""
    phase = w.current(state)['phase']
    rows = evidence.context_tasks(state)
    definition = next((r for r in rows if r['id'] == task and r.get('phase', phase) == phase), None)
    w.require(definition is not None, 'invalid_context', 'Task is not part of this current phase.')
    assert definition is not None
    evidence.validate_read_inputs(workspace, state['scope'], definition)
    selection = state.get('context_selection', {})
    w.require(selection.get('schema') == SELECTION_SCHEMA and selection.get('run') == state['run']
              and selection.get('scope_digest') == digest(state['scope'])
              and selection.get('task_generation') == state.get('task_generation', 0),
              'invalid_context', 'Compact context requires the current reviewed selection manifest.')
    chosen = selection.get('tasks', {}).get(task)
    w.require(isinstance(chosen, dict), 'invalid_context', f'Missing compact context mapping for {task}.')
    criteria = sorted(evidence.task_criteria(definition))
    criterion_map = chosen.get('criteria', {})
    prerequisites = chosen.get('prerequisites', {})
    w.require(isinstance(criterion_map, dict) and set(criterion_map) == set(criteria)
              and isinstance(prerequisites, dict)
              and set(prerequisites) == set(definition['dependencies']),
              'invalid_context', 'Every assigned criterion and prerequisite needs an exact input mapping.')
    declarations = chosen.get('inputs')
    w.require(isinstance(declarations, list) and declarations
              and all(isinstance(d, dict) and isinstance(d.get('id'), str) and d['id'] for d in declarations),
              'invalid_context', 'Compact inputs require stable IDs.')
    ids = {d['id'] for d in declarations}
    w.require(len(ids) == len(declarations), 'invalid_context', 'Duplicate compact input ID.')
    for label, mapped in [*criterion_map.items(), *prerequisites.items()]:
        w.require(isinstance(mapped, list) and mapped and all(isinstance(x, str) for x in mapped)
                  and set(mapped) <= ids, 'invalid_context', f'Unmapped mandatory obligation: {label}.')
    owned = set(definition['paths'])
    reads = set(evidence.read_inputs(state, definition))
    w.require(not owned & reads, 'invalid_context', 'A mandatory input cannot depend on this task output.')
    accepted = []
    accepted_sources: dict[str, Any] = {}
    items = []
    for visit in state['visits'][:state['index']]:
        packet = visit.get('packet')
        if visit.get('superseded') or visit['decision'] != 'approved' or not packet:
            continue
        accepted.append({'visit': visit['id'], 'phase': visit['phase'],
                         'checkpoint': packet['checkpoint'], 'evidence_digest': digest(packet)})
        accepted_sources.update(packet['manifest'])
        items.append({'id': 'accepted/' + visit['id'], 'kind': 'accepted-output', 'required': False,
                      'body': {k: v for k, v in packet['output'].items() if k != 'context_receipt'}})
    all_findings = evidence.finding_register(state)
    assigned = chosen.get('findings', [])
    w.require(isinstance(assigned, list) and all(isinstance(x, str) for x in assigned)
              and len(assigned) == len(set(assigned)), 'invalid_context', 'Invalid finding selection.')
    register = {f['origin_run'] + '/' + f['id']: f for f in all_findings if f['disposition'] != 'resolved'}
    w.require(set(assigned) <= set(register), 'invalid_context', 'Finding selection is missing or stale.')
    # Full obligations remain available regardless of this task's selected findings.
    items.append({'id': 'finding-register', 'kind': 'finding-register', 'body': all_findings, 'required': False})
    bodies: dict[str, bytes] = {}
    coverage = []
    for declaration in declarations:
        relative = declaration.get('path')
        w.require(isinstance(relative, str) and Path(relative).as_posix() == relative and '\\' not in relative
                  and relative in reads | set(accepted_sources) and relative not in owned,
                  'scope_violation', 'Compact input must be an exact declared dependency or accepted source.')
        evidence.path(workspace, relative)
        w.require(declaration.get('kind') in {'requirement', 'interface', 'brief', 'finding', 'source'}
                  and isinstance(declaration.get('reason'), str) and declaration['reason'].strip(),
                  'invalid_context', 'Input kind and criterion/interface reason are required.')
        if relative not in bodies:
            bodies[relative] = source_read(workspace, relative)
        raw = bodies[relative]
        source_digest = hashlib.sha256(raw).hexdigest()
        w.require(source_digest == declaration.get('sha256')
                  and (relative not in accepted_sources or accepted_sources[relative] == source_digest),
                  'invalid_context', f'Changed compact input: {declaration["id"]}.')
        body = select_bytes(raw, declaration.get('selector'))
        item_id = 'selected/' + declaration['id']
        items.append({'id': item_id, 'kind': 'selected-input', 'required': True,
                      'body': text_body(body)})
        coverage.append({**declaration, 'input_id': item_id, 'body_digest': hashlib.sha256(body).hexdigest(),
                         'encoded_bytes': len(body)})
    # Explicit internal briefs may not vanish behind the .taskplane prefix.
    declared = {d['path'] for d in declarations}
    w.require(all(not p.startswith('.taskplane/') or p in declared for p in reads),
              'invalid_context', 'Every declared internal brief needs a mandatory selector.')
    read_interfaces: list[dict[str, Any]] = []
    for relative in sorted(reads):
        read_raw = bodies.get(relative)
        if read_raw is None and preview_dependencies and not evidence.path(workspace, relative).exists():
            producers = [row for row in rows if row.get('phase', phase) == phase
                         and relative in row.get('paths', [])]
            if len(producers) == 1 and producers[0]['id'] in prerequisites:
                from .worker_runtime import result_valid
                producer = producers[0]['id']
                w.require(not result_valid(workspace, state, producer), 'invalid_context',
                          f'Verified prerequisite output is missing: {relative}.')
                # An upgrade preview precedes these producers. Record absence
                # explicitly; ordinary delivery still reads every required file.
                read_interfaces.append({'path': relative, 'sha256': None,
                                        'availability': 'future_result_required',
                                        'producer_task': producer})
                continue
        if read_raw is None:
            read_raw = source_read(workspace, relative)
        read_interfaces.append({'path': relative, 'sha256': hashlib.sha256(read_raw).hexdigest()})
        if relative not in declared:
            items.append({'id': 'source/' + relative, 'kind': 'source', 'required': False,
                          'body': {'path': relative, **text_body(read_raw)}})
    dependencies = []
    if prerequisites:
        from .worker_runtime import result_valid
        for dependency in prerequisites:
            verified = result_valid(workspace, state, dependency)
            w.require(verified or preview_dependencies, 'invalid_context',
                      f'Prerequisite interface has no current verified result: {dependency}.')
            dependencies.append({'task': dependency, 'result_digest': digest(state['task_results'][dependency]) if verified else None,
                                 'interface_inputs': prerequisites[dependency],
                                 'verification_status': 'verified' if verified else 'future_result_required_before_launch'})
    request_ref = chosen.get('request_reference')
    w.require(isinstance(request_ref, str) and request_ref.strip() and state.get('goal'),
              'invalid_context', 'Compact brief requires the actual request and its reference.')
    brief = {'schema': 'taskplane.task-brief/v1', 'binding': binding(state), 'task': task,
             'request_reference': request_ref, 'request_text': state['goal'],
             'criteria': criterion_map, 'owned_paths': sorted(owned), 'read_interfaces': read_interfaces,
             'relevant_findings': [register[key] for key in sorted(assigned)],
             'prerequisites': dependencies, 'coverage': coverage, 'selector_version': 1,
             'startup_contract': 'execution_only',
             'context_is_authority': False}
    items.insert(0, {'id': 'task-brief', 'kind': 'task-brief', 'body': brief, 'required': True})
    authority = {'binding': binding(state), 'phase': phase, 'decision': w.current(state)['decision'],
                 'criteria': criteria, 'paths': sorted(owned), 'accepted_inputs': accepted,
                 'context_is_authority': False}
    return items, [task], criteria, {'authority': authority, 'coverage': {
        'request_text': 'supplied', 'untrusted_bodies': True, 'graph': 'supporting',
        'host_conversation': 'not_removed', 'model_attention': 'unobservable',
        'supporting_artifacts': 'referenced', 'selection_digest': digest(selection)}}


def inputs(workspace: Path, state: dict[str, Any], task: str | None, *, preview_dependencies: bool = False) -> tuple[list[dict[str, Any]], list[str], list[str], dict[str, Any]]:
    if state.get('context_contract') == BRIEF_CONTRACT and task is not None:
        return compact_inputs(workspace, state, task, preview_dependencies=preview_dependencies)
    phase = w.current(state)["phase"]
    rows = evidence.context_tasks(state)
    accepted: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    semantic = state.get("context_contract") in {SEMANTIC_CONTRACT, BRIEF_CONTRACT}
    artifact_ids: set[str] = set()
    finding_bodies: dict[str, Any] = {}
    finding_store = Store(workspace)
    revision = state.get('requirements_revisions', {}).get(state.get('active_requirements_revision'))
    if revision and revision.get('opened') and w.current(state).get('requirements_revision') == state.get('active_requirements_revision'):
        items.append({'id': 'requirements-revision', 'kind': 'requirements', 'body': {
            'request': revision['request'], 'affected_tasks': revision['preview']['tasks'],
            'requirements': revision['preview']['requirements'], 'status': revision['status'],
            'approvals_created': 0}})
    # Original obligations are independent of the new task's narrower criteria.
    # Their immutable bodies survive predecessor archival and path replacement.
    for finding in evidence.finding_register(state):
        if finding["disposition"] == "resolved":
            continue
        identity = finding["origin_run"] + "/" + finding["id"]
        items.append({"id": "finding/" + identity, "kind": "finding", "body": finding})
        for ref in finding.get("evidence_refs", []):
            # Multiple obligations can name the same immutable evidence. Read
            # and verify it once in this preparation, preserving every role.
            key = digest(ref)
            if key not in finding_bodies:
                finding_bodies[key] = finding_store.resolve(ref)
            items.append({"id": "finding-evidence/" + identity + "/" + ref["sha256"],
                          "kind": "finding-evidence", "body": finding_bodies[key]})
    for visit in state["visits"][:state["index"]]:
        packet = visit.get("packet")
        if visit.get("superseded") or visit["decision"] != "approved" or not packet:
            continue
        accepted.append({"visit": visit["id"], "phase": visit["phase"],
                         "checkpoint": packet["checkpoint"], "evidence_digest": digest(packet)})
        # Preserve the normative predecessor output and referenced artifact bodies,
        # without embedding executable decision envelopes or host transcripts.
        items.append({"id": "accepted/" + visit["id"], "kind": "accepted-output",
                      "body": {k:v for k,v in packet["output"].items() if k != "context_receipt"}})
        for number, check in enumerate(packet["output"].get("build_checks", [])):
            if isinstance(check, dict) and isinstance(check.get("reuse_ref"), dict):
                from .context_reuse import inherited
                items.append({"id": f"verification/{visit['id']}/{number}", "kind": "verification",
                              "body": inherited(workspace, check["reuse_ref"])})
        for relative in sorted(packet["manifest"]):
            if semantic and relative in artifact_ids:
                continue
            artifact_ids.add(relative)
            raw = source_read(workspace, relative)
            try:
                count("source_parse")
                document = json.loads(raw)
            except (ValueError, UnicodeError):
                document = None
            if isinstance(document, dict) and document.get("schema") == "taskplane.phase-output/v1":
                continue  # The exact normative body is already represented above.
            if relative == packet.get("context", {}).get("tasks_path"):
                continue  # Task definitions are selected separately; observations are not normative.
            # v2 keeps every normative output required. Supporting bytes remain
            # accessible through bound, digest-verified refs; authors can mark a
            # particular artifact required for specific downstream phases.
            declaration: dict[str, Any] = next((a for a in packet["output"].get("artifacts", [])
                                if a.get("path") == relative), {})
            required = (not semantic or declaration.get("kind") not in {"verification", "supporting", "raw-log", "source"}
                        or phase in declaration.get("required_for", []))
            items.append({"id": "artifact/" + relative, "kind": "accepted-artifact", "required": required,
                          "body": {"path": relative, **text_body(raw)}})
    # A workspace-global task file may belong to a different run. Initial tasks
    # are frozen by start; accepted packets provide their later definitions.
    selected = [row for row in rows if row.get("phase", phase) == phase]
    if task:
        selected = [row for row in selected if row["id"] == task]
        w.require(selected, "invalid_context", "Task is not part of this current phase.")
        for dependency in selected[0].get("dependencies", []):
            result = state.get("task_results", {}).get(dependency)
            if not result:
                continue  # Earlier-phase evidence is already supplied above.
            items.append({"id": "prerequisite/" + dependency, "kind": "prerequisite-result",
                          "body": {"task": dependency, "result": deepcopy(result)}})
            for relative in result.get("outputs", result.get("manifest", {})):
                items.append({"id": "prerequisite-artifact/" + dependency + "/" + relative,
                              "kind": "prerequisite-artifact",
                              "body": {"path": relative, **text_body(source_read(workspace, relative))}})
    criteria = sorted({c for row in selected for c in evidence.task_criteria(row)}) or state["scope"]["criteria"]
    paths = (sorted({p for row in selected for p in row.get("paths", [])}
                    & set(state["scope"]["paths"][phase])) if selected else state["scope"]["paths"][phase])
    items.insert(0, {"id": "requirements-and-scope", "kind": "requirements",
                    "body": {"goal": state.get("goal", ""), "criteria": criteria, "phase": phase, "paths": paths,
                             "tasks": [{k:v for k,v in row.items() if k not in evidence.TASK_OBSERVATIONS}
                                       for row in selected]}})
    if "workflow_binding" in state["scope"]:
        from .workflow_local import verify_workflow
        checked = verify_workflow(workspace, state)
        assert checked is not None
        for name in ("definition", "compilation"):
            items.append({"id": "workflow/" + name, "kind": "workflow-provenance",
                          "body": checked[name]})
        names = {ref["input"] for row in checked["definition"]["tasks"]
                 if not task or row["id"] == task for ref in
                 [*row["read_bindings"], row["criteria_binding"],
                  *[output.get("binding", output.get("directory")) for output in row["outputs"]]]
                 if "input" in ref}
        bound = checked["bindings"]
        items.append({"id": "workflow/bindings", "kind": "workflow-provenance", "body": {
            "workflow_binding": checked["workflow_binding"], "task": task,
            "values": {key: value for key, value in bound["values"].items() if not task or key in names},
            "git_refs": bound["git_refs"], "source_manifest": bound["source_manifest"],
            "unresolved_inputs": bound["unresolved_inputs"], "context_is_authority": False}})
        for relative in checked["compilation"]["snapshots"].values():
            if not task or any(relative in row.get("read_inputs", []) for row in selected):
                items.append({"id": "workflow/source/" + relative, "kind": "source",
                              "body": {"path": relative, **text_body(source_read(workspace, relative))}})
    declared_reads = ({p for row in selected for p in evidence.read_inputs(state, row)}
                      if selected else set(state["scope"].get("verification_inputs", [])))
    source_paths = sorted(set(paths) | declared_reads)
    for relative in source_paths:
        if relative.startswith(".taskplane/"):
            continue
        target = evidence.path(workspace, relative)
        if not target.exists():
            continue
        raw = source_read(workspace, relative)
        try:
            count("source_parse")
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            value = None
        if isinstance(value, dict) and (value.get("schema") == "taskplane.phase-output/v1" or "tasks" in value):
            continue  # Avoid making an output/receipt depend on its own bytes.
        items.append({"id": "source/" + relative, "kind": "source", "required": False,
                      "priority": 0 if any("read_inputs" in row and relative in row["read_inputs"] for row in selected) else 1,
                      "body": {"path": relative, **text_body(raw)}})
    graph = depgraph.load(str(workspace))
    current_graph = bool(graph) and depgraph.source_inputs_current(str(workspace), graph)
    # Graph transport is semantic source structure. Scan timestamps and output
    # JSON digests must not make a receipt depend on its own serialization.
    graph_body = {key: graph.get(key, {}) for key in ("modules", "edges", "components", "recorded")}
    graph_body["files"] = {name: {k:v for k,v in row.items() if k not in {"mtime", "mtime_ns"}}
                           for name, row in graph.get("files", {}).items()}
    items.append({"id": "source-graph", "kind": "graph", "body": graph_body, "required": False})
    authority = {"binding": binding(state), "phase": phase, "decision": w.current(state)["decision"],
                 "criteria": criteria, "paths": paths, "accepted_inputs": accepted,
                 "context_is_authority": False}
    coverage = {"graph": "current" if current_graph else "unknown_or_stale",
                "request_text": "supplied" if state.get("goal") else "not_recorded",
                "untrusted_bodies": True, "host_conversation": "not_removed",
                "model_attention": "unobservable", "supporting_artifacts": "referenced" if semantic else "required"}
    return items, [row["id"] for row in selected], criteria, {"authority": authority, "coverage": coverage}


def current_read_binding(workspace: Path, state: dict[str, Any]) -> dict[str, Any]:
    """Recompute root-source freshness without building a view or walking its trees."""
    w.require(state.get("run") and state.get("visits") and not state.get("superseded_by"),
              "invalid_context", "Context requires the current bound run.")
    items, _, _, metadata = inputs(workspace, state, None)
    store = Store(workspace)
    refs = {item["id"]: store.reference(item.get("kind", "input"), item["body"]) for item in items}
    current = binding(state)
    return {**current, "source_key": view_source_key(current, refs, metadata["authority"], items)}


def preparation_sources(workspace: Path, state: dict[str, Any], task: str | None) -> dict[str, Any]:
    """Exact external dependencies of a root preparation, including absent files.

    The controller separately pins the complete state. This only saves repeated
    canonical tree construction; accepted evidence and mutable sources are still
    checked before reuse. Unknown compiled-workflow dependencies decline reuse.
    """
    if 'workflow_binding' in state['scope']:
        return {'cacheable': False}
    phase = w.current(state)['phase']
    rows = [row for row in evidence.context_tasks(state)
            if row.get('phase', phase) == phase and (task is None or row['id'] == task)]
    paths = {p for row in rows for p in row.get('paths', [])}
    paths.update(p for row in rows for p in evidence.read_inputs(state, row))
    if not rows:
        paths.update(state['scope']['paths'][phase])
        paths.update(state['scope'].get('verification_inputs', []))
    for visit in state['visits'][:state['index']]:
        if visit.get('packet') and not visit.get('superseded') and visit['decision'] == 'approved':
            paths.update(visit['packet']['manifest'])
    for row in rows:
        for dependency in row.get('dependencies', []):
            result = state.get('task_results', {}).get(dependency, {})
            paths.update(result.get('outputs', result.get('manifest', {})))
    selection = state.get('context_selection', {}).get('tasks', {})
    for key, selected in selection.items():
        if task is None or key == task:
            paths.update(item['path'] for item in selected.get('inputs', []))
    graph = depgraph.load(str(workspace))
    graph_current = bool(graph) and depgraph.source_inputs_current(str(workspace), graph)
    return {'cacheable': True, 'sources': evidence.manifest(workspace, sorted(paths), allow_missing=True),
            'graph_digest': digest(graph), 'graph_current': graph_current}


def selection_preview(workspace: Path, state: dict[str, Any], selection: dict[str, Any]) -> dict[str, Any]:
    """Validate the complete frozen task/criterion register without issuing grants."""
    plan = w.accepted_plan(state)
    phase = w.current(state)['phase']
    definitions = [t for t in evidence.context_tasks(state) if t.get('phase', phase) == phase]
    approved = [t for t in plan['packet']['output']['task_dag'] if t.get('phase', phase) == phase]
    w.require(evidence.task_definitions(definitions) == evidence.task_definitions(approved),
              'invalid_context', 'Context upgrade cannot change accepted Plan task definitions or ownership.')
    w.require(isinstance(selection, dict) and set(selection.get('tasks', {})) == {t['id'] for t in definitions},
              'invalid_context', 'Selection must account for every accepted task, including root integration.')
    covered = {criterion for t in definitions for criterion in evidence.task_criteria(t)}
    w.require(covered == set(state['scope']['criteria']), 'invalid_context', 'Selection does not cover the whole criterion register.')
    findings = {f['origin_run'] + '/' + f['id'] for f in evidence.finding_register(state) if f['disposition'] != 'resolved'}
    mapped = {f for t in selection['tasks'].values() for f in t.get('findings', [])}
    w.require(mapped == findings, 'invalid_context', 'Every open finding needs an explicit task mapping.')
    proposed = deepcopy(state)
    proposed.update(context_contract=BRIEF_CONTRACT, context_selection=deepcopy(selection),
                    task_generation=state.get('task_generation', 0) + 1,
                    revision=state['revision'] + 1)
    briefs = []
    sources: dict[str, str] = {}
    future_sources: dict[str, str] = {}
    for definition in definitions:
        maximum_consumer = {'worker_id': '\x00' * 200, 'grant_id': '0' * 32, 'attempt': 1024,
                            'task_id': definition['id'], 'task_generation': proposed['task_generation']}
        session = Session(workspace, proposed, definition['id'], persist=False, preview_dependencies=True,
                          consumer=maximum_consumer if definition.get('execution') != 'root' else None)
        measured = session.preflight()
        brief = next(i['body'] for i in session.items if i['id'] == 'task-brief')
        for reference in brief['read_interfaces']:
            if reference.get('availability') == 'future_result_required':
                future_sources[reference['path']] = reference['producer_task']
            else:
                sources[reference['path']] = reference['sha256']
        for declaration in brief['coverage']:
            sources[declaration['path']] = declaration['sha256']
        briefs.append({'task': definition['id'], 'criteria': evidence.task_criteria(definition),
                       'preflight': measured, 'brief_digest': digest(brief),
                       'future_results': [d['task'] for d in brief['prerequisites']
                                          if d['verification_status'] != 'verified']})
    return {'schema': 'taskplane.selection-preview/v1', 'selection_digest': digest(selection),
            'task_generation': proposed['task_generation'], 'criteria': sorted(covered),
            'findings': sorted(findings), 'briefs': briefs, 'source_sha256': sources,
            'future_prerequisite_sources': future_sources,
            'launch_permission': False, 'future_results_must_be_verified_at_prepare': True}


class _PreviewStore(Store):
    """Exact canonical trees in memory, with read-through to existing evidence."""

    def __init__(self, workspace: Path):
        super().__init__(workspace)
        self.objects: dict[str, bytes] = {}

    def _object(self, kind: str, data: Any, source_key: str, form: str) -> dict[str, Any]:
        raw = encode({"schema": "taskplane.context-object/v1", "kind": kind,
                      "source_key": source_key, "form": form, "data": data})
        w.require(len(raw) <= OBJECT_LIMIT, "context_overflow", "Canonical context node is oversized.")
        count("hash")
        key = hashlib.sha256(raw).hexdigest()
        self.objects[key] = raw
        return {"schema": REFERENCE_SCHEMA, "kind": kind, "sha256": key,
                "bytes": len(raw), "source_key": source_key}

    def _bytes(self, key: str) -> bytes:
        if key in self.objects:
            self._last_read_size = 0
            return self.objects[key]
        return super()._bytes(key)

    def register(self, binding: dict[str, Any], refs: list[dict[str, Any]]) -> None:
        return None


class Session:
    @observed('context-construction')
    def __init__(self, workspace: Path, state: dict[str, Any], task: str | None = None, *,
                 consumer: dict[str, Any] | None = None, snapshot: dict[str, Any] | None = None,
                 persist: bool = True, preview_dependencies: bool = False) -> None:
        w.require(state.get("run") and state.get("visits") and not state.get("superseded_by"),
                  "invalid_context", "Context requires the current bound run.")
        self.workspace, self.state, self.task = workspace.resolve(), state, task
        w.require(not preview_dependencies or (not persist and snapshot is None),
                  'invalid_context', 'Future prerequisite previews cannot create launchable snapshots or delivery receipts.')
        if snapshot is not None and "prepared_ref" in snapshot:
            self._resume(snapshot, consumer, persist)
            return
        # A frozen worker snapshot cannot bypass current package/runtime checks.
        if snapshot is not None:
            from .workflow_local import verify_workflow
            verify_workflow(self.workspace, state)
        self.store = Store(self.workspace) if persist else _PreviewStore(self.workspace)
        self.binding = binding(state)
        self.phase = w.current(state)["phase"]
        if snapshot is None:
            self.items, task_ids, criteria, metadata = inputs(self.workspace, state, task,
                                                             preview_dependencies=preview_dependencies)
        else:
            w.require(snapshot.get("binding") == self.binding and snapshot.get("task") == task,
                      "invalid_context", "Frozen task inputs belong to another grant revision.")
            self.items, task_ids, criteria, metadata = deepcopy(snapshot["inputs"])
        self.frozen = {"binding": deepcopy(self.binding), "task": task,
                       "inputs": deepcopy([self.items, task_ids, criteria, metadata])}
        if consumer is not None:
            w.require(set(consumer) == {"worker_id", "grant_id", "attempt", "task_id", "task_generation"}
                      and consumer["task_id"] == task and bool(consumer["worker_id"])
                      and consumer["task_generation"] == state.get("task_generation", 0),
                      "invalid_context", "Worker context needs its bound consumer identity.")
            self.binding = {**self.binding, "consumer": deepcopy(consumer)}
            metadata["authority"] = {**metadata["authority"], "binding": self.binding}
            if state.get('context_contract') == BRIEF_CONTRACT:
                for item in self.items:
                    if item['id'] == 'task-brief':
                        item['body'] = {**item['body'], 'binding': deepcopy(self.binding)}
        self.input_refs = {}
        written = {}
        for item in self.items:
            identity = (item.get('kind', 'input'), encode(item['body']))
            if identity not in written:
                written[identity] = self.store.put(identity[0], item['body'])
            self.input_refs[item['id']] = written[identity]
        streamed = bool(task and any(row['id'] == task and 'read_inputs' in row
                                    for row in evidence.context_tasks(state)))
        self.view = view(self.store, self.binding, self.phase, task_ids, criteria,
                         metadata["authority"], self.items, metadata["coverage"],
                         _prepared_refs=self.input_refs, _streamed=streamed)
        self.view_ref = self.store.put("context-view", self.view)
        self.read_binding = {**self.binding, "source_key": self.view["source_key"]}
        self.required = [{"id": item["id"], "ref": self.input_refs[item["id"]]}
                         for item in self.items if item.get("required", True)]
        self.handoff = signed({"schema": "taskplane.context-handoff/v1", "binding": self.binding,
                               "view_ref": self.view_ref,
                               "required_inputs": collection(self.store, self.required, "required-inputs", 0),
                               "accepted_inputs": collection(self.store, metadata["authority"]["accepted_inputs"], "accepted-inputs", 0),
                               "unknowns": metadata["coverage"], "exclusions": EXCLUSIONS})
        self.handoff_ref = self.store.put("context-handoff", self.handoff)
        self.refs = list(self.input_refs.values())
        self.refs += [self.view_ref, self.handoff_ref, self.view["authority_ref"]]
        for field in (self.view["references"], self.view["required_inputs"], self.handoff["accepted_inputs"]):
            if field.get("details"):
                self.refs.append(field["details"])
        reuse = shared_bodies(self.items, self.input_refs)
        reuse_ref = self.view.get("body_reuse", {}).get("details")
        if reuse_ref:
            self.refs.append(reuse_ref)
        self.store.register(self.read_binding, self.refs)
        self.allowed: set[str] | None = None
        self.ledger_path = evidence.path(self.workspace,
            f".taskplane/context-v1/delivery/{self.handoff_ref['sha256']}.json")
        obligations = {item["ref"]["sha256"]: {"root": item["ref"], "body": item["ref"], "provenance": []}
                       for item in self.required}
        if reuse_ref:
            for group in reuse:
                for item in group["inputs"]:
                    if item["required"]:
                        obligations[item["ref"]["sha256"]].update(body=group["body_ref"], provenance=[reuse_ref])
        roots = {ref["sha256"]: ref for row in obligations.values()
                 for ref in [row["body"], *row["provenance"]]}
        self.coverage_ref = self.store._object("context-index-manifest", {
            "schema": "taskplane.context-required-manifest/v1", "binding": self.binding,
            "required": list(obligations.values())}, "", "value")
        self.page_index = PageIndex.build(self.store, list(roots.values()))
        self.index = PageIndex(self.store, self.page_index["root"])
        self.journal = DeliveryJournal(self.store, self.handoff_ref["sha256"], self.binding)
        self._legacy_loaded = True
        data_ref = self.store._object("context-index-session-data", {
            "items": [{k: v for k, v in item.items() if k != "body"} | {"ref": self.input_refs[item["id"]]}
                      for item in self.items], "required": self.required, "refs": self.refs,
            "reuse": reuse}, "", "value")
        self._preflight = self.preflight()
        self.prepared_ref = self.store._object("context-index-prepared", {
            "schema": "taskplane.context-index/v1", "format_version": 1,
            "binding": self.binding, "binding_digest": digest(self.binding), "task": self.task,
            "immutable_manifest_root": self.coverage_ref, "required_coverage_digest": self.coverage_ref["sha256"],
            "ordered_node_and_page_index": self.page_index, "view_ref": self.view_ref,
            "handoff_ref": self.handoff_ref, "handoff": self.handoff,
            "view_spine": {k: v for k, v in self.view.items() if k != "inline"}, "data_ref": data_ref,
            "required_roots": len(obligations), "preflight": self.preflight()}, "", "value")
        self.frozen = (snapshot if consumer is not None and snapshot is not None else
                       {"binding": binding(state), "task": task, "prepared_ref": self.prepared_ref})

    def _resume(self, snapshot: dict[str, Any], consumer: dict[str, Any] | None, persist: bool) -> None:
        # The Controller supplies the immutable prepared reference from its bound
        # snapshot. No mutable cache may choose a replacement authority manifest.
        self.store = Store(self.workspace) if persist else _PreviewStore(self.workspace)
        current = binding(self.state)
        w.require(snapshot.get("binding") == current and snapshot.get("task") == self.task,
                  "invalid_context", "Frozen task inputs belong to another grant revision.")
        from .workflow_local import verify_workflow
        verify_workflow(self.workspace, self.state)
        self.prepared_ref = snapshot["prepared_ref"]
        node = self.store.node(self.prepared_ref)
        prepared = node["data"]
        w.require(node["kind"] == "context-index-prepared" and prepared.get("binding") == current
                  and prepared.get("binding_digest") == digest(current) and prepared.get("task") == self.task,
                  "invalid_context", "Prepared index belongs to another current binding.")
        if consumer is not None and self.state.get('context_contract') == BRIEF_CONTRACT:
            # Only the bounded mandatory bodies are materialized for a fresh
            # consumer. Historical/supporting bodies remain verified references.
            # Rebuilding this small brief changes its actual returned bytes, not
            # merely an outer receipt that still names the parent's old brief.
            data = self.store.node(prepared['data_ref'])['data']
            items, supporting = [], []
            for item in data['items']:
                ref = item['ref']
                required = item.get('required', True)
                body = (self.store.resolve(ref) if required else
                        {'schema': 'taskplane.supporting-reference/v1', 'source_ref': ref,
                         'reason': 'Supporting bytes are available by verified reference; not mandatory startup content.'})
                items.append({k: v for k, v in item.items() if k != 'ref'} | {'body': body})
                if not required:
                    supporting.append(ref)
            authority = self.store.resolve(prepared['view_spine']['authority_ref'])
            frozen = {'binding': current, 'task': self.task,
                      'inputs': [items, [self.task], authority['criteria'],
                                 {'authority': authority, 'coverage': prepared['view_spine']['coverage']}]}
            type(self).__init__(self, self.workspace, self.state, self.task, consumer=consumer, snapshot=frozen, persist=persist)
            self.store.register(self.read_binding, supporting)
            return
        self._prepared = prepared
        self._legacy_loaded = False
        self.binding, self.phase = current, w.current(self.state)["phase"]
        self.view_ref, self.handoff_ref = prepared["view_ref"], prepared["handoff_ref"]
        self.view = deepcopy(prepared["view_spine"])
        self.handoff = deepcopy(prepared["handoff"])
        if consumer is not None:
            w.require(set(consumer) == {"worker_id", "grant_id", "attempt", "task_id", "task_generation"}
                      and consumer["task_id"] == self.task and bool(consumer["worker_id"])
                      and consumer["task_generation"] == self.state.get("task_generation", 0),
                      "invalid_context", "Worker context needs its bound consumer identity.")
            self.binding = {**current, "consumer": deepcopy(consumer)}
            # Frozen workers already have a source-preparation view. The bound
            # consumer gets a small spine and all bodies through the page index,
            # avoiding repeated serialization of the predecessor's inline bodies.
            self.view = signed({k: v for k, v in self.view.items() if k != "digest"} | {
                "binding": self.binding, "inline": {},
                "source_key": digest({"base": self.view["source_key"], "consumer": consumer}),
                "omissions": [{"reason": "Read every required body through the bound page index.",
                               "count": prepared["required_roots"]}]})
            self.view_ref = self.store.put("context-view", self.view)
            self.handoff = signed({k: v for k, v in self.handoff.items() if k != "digest"} | {
                "binding": self.binding, "view_ref": self.view_ref})
            self.handoff_ref = self.store.put("context-handoff", self.handoff)
        self.read_binding = {**self.binding, "source_key": self.view["source_key"]}
        self.coverage_ref = prepared["immutable_manifest_root"]
        w.require(prepared["required_coverage_digest"] == self.coverage_ref["sha256"],
                  "invalid_context", "Coverage manifest binding differs.")
        self.page_index = prepared["ordered_node_and_page_index"]
        self.index = PageIndex(self.store, self.page_index["root"])
        self.journal = DeliveryJournal(self.store, self.handoff_ref["sha256"], self.binding)
        self.ledger_path = evidence.path(self.workspace,
            f".taskplane/context-v1/delivery/{self.handoff_ref['sha256']}.json")
        self.allowed = None
        self.frozen = snapshot

    def __getattr__(self, name: str) -> Any:
        tree_names = {"original_trees", "delivery_trees", "required_trees"}
        if name not in {"items", "required", "refs", "input_refs"} | tree_names:
            raise AttributeError(name)
        if not self.__dict__.get("_legacy_loaded", True):
            self._legacy_loaded = True
            data = self.store.node(self._prepared["data_ref"])["data"]
            self.required, self.refs = data["required"], data["refs"]
            self.input_refs = {row["id"]: row["ref"] for row in data["items"]}
            self.items = [{k: v for k, v in row.items() if k != "ref"} |
                          {"body": self.store.resolve(row["ref"])} for row in data["items"]]
            self.store.register(self.read_binding, [*self.refs, self.view_ref, self.handoff_ref])
        if name in tree_names:
            self.original_trees = {key: self.store.descendants(ref) for key, ref in
                {item["ref"]["sha256"]: item["ref"] for item in self.required}.items()}
            self.delivery_trees = dict(self.original_trees)
            self.required_trees = dict(self.original_trees)
            reuse_ref = self.view.get("body_reuse", {}).get("details")
            if reuse_ref:
                provenance = self.store.descendants(reuse_ref)
                for group in shared_bodies(self.items, self.input_refs):
                    body = self.store.descendants(group["body_ref"])
                    for item in group["inputs"]:
                        key = item["ref"]["sha256"]
                        self.delivery_trees[key] = body
                        if item["required"]:
                            self.required_trees[key] = body | provenance
        if name not in self.__dict__:
            raise AttributeError(name)
        return self.__dict__[name]

    def descriptor(self) -> dict[str, Any]:
        return {"status": "ready", "contract": self.state.get("context_contract", "legacy"),
                "source_key": self.view["source_key"],
                "handoff_ref": self.handoff_ref, "view_ref": self.view_ref,
                "required_inputs": self.preflight()["required_roots"], "phase": self.phase, "preflight": self.preflight(),
                **({"measurement_graph": {"manifest": self.coverage_ref,
                       "prepared_ref": self.prepared_ref, "index": self.page_index}}
                   if cli_metrics() is not None else {}),
                "next_action": context_action(self.workspace, self.state["run"], self.task,
                    operation="drain" if self.task and any(row["id"] == self.task and "read_inputs" in row
                        for row in evidence.context_tasks(self.state)) else "consume", key=self.handoff_ref["sha256"])}

    def preflight(self) -> dict[str, Any]:
        if "_preflight" in self.__dict__:
            return deepcopy(self._preflight)
        if not self.__dict__.get("_legacy_loaded", True):
            return deepcopy(self._prepared["preflight"])
        required = {item["ref"]["sha256"]: item for item in self.required}
        bodies = {digest(item["body"]): item for item in self.items if item.get("required", True)}
        sizes = sorted(({"id": item["id"], "bytes": len(encode(item["body"]))} for item in bodies.values()),
                       key=lambda item: (-item["bytes"], item["id"]))
        result = {"required_roots": len(required), "required_body_bytes": sum(item["bytes"] for item in sizes),
                "unique_required_bodies": len(bodies),
                "required_pages": self.page_index["pages"],
                "supporting_roots": sum(not item.get("required", True) for item in self.items),
                "largest_required": sizes[:5], "basis": "Unique required bodies; page envelopes add transport cost."}
        if self.state.get('context_contract') == BRIEF_CONTRACT and self.task:
            # Match read_required's actual canonical page packing. Reserve the
            # prepare, claim, context descriptor and first-source response even
            # when they contain no mandatory body. No optimistic compression.
            batches, used, pages = 0, 0, 0
            wire_pages = []
            for offset in range(self.page_index['pages']):
                sha, number, expected = self.index.entry(offset)
                page = self.store.page(sha, number)
                w.require(page['pages'] == expected, 'invalid_context', 'Packing index extent changed.')
                size = len(encode(page)) + 1
                w.require(size <= 32768 - 6144, 'context_overflow', 'Mandatory page exceeds startup packing bound.')
                if pages and (used + size > 32768 - 6144 or pages == 64):
                    wire_pages.append(used + 6144)
                    batches += 1
                    used, pages = 0, 0
                used += size
                pages += 1
            if pages or not batches:
                batches += 1
                wire_pages.append(used + 6144)
            result['startup_packing'] = {'schema': 'taskplane.startup-packing/v1',
                'reserved_metadata_responses': 4, 'body_responses': batches,
                'normal_responses': batches + 4, 'response_limit': 32768,
                'body_response_upper_bounds': wire_pages, 'receipt_envelope_reserve': 6144,
                'basis': 'Exact canonical page sizes with conservative bounded envelopes; retries count again.'}
        return result

    def ledger(self) -> dict[str, Any]:
        if not self.ledger_path.exists():
            return {"binding": self.binding, "seen": [], "pages": {}, "receipts": [], "returned_bytes": 0}
        w.require(self.ledger_path.stat().st_size <= 8 * 1024 * 1024,
                  "context_overflow", "Context delivery ledger is oversized.")
        value = evidence.object_file(self.workspace, str(self.ledger_path.relative_to(self.workspace)))
        w.require(value.get("binding") == self.binding, "invalid_context", "Foreign delivery receipt binding.")
        return value

    def _preview(self, payload: dict[str, Any], ledger: dict[str, Any], seen: set[str],
                 updates: list[tuple[str, int, int]], *, event_id: str,
                 persist: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
        from copy import deepcopy
        ledger = deepcopy(ledger)
        seen = seen | set(ledger["seen"])
        for key, number, total in updates:
            pages = sorted(set(ledger["pages"].get(key, [])) | {number})
            ledger["pages"][key] = pages
            if pages == list(range(total)):
                seen.add(key)
        for key, nodes in self.original_trees.items():
            if nodes <= seen and (self.required_trees[key] - self.delivery_trees[key]) <= seen:
                seen |= self.delivery_trees[key]
        returned = sorted(key for key, nodes in self.required_trees.items()
                          if nodes <= seen or self.original_trees[key] <= seen)
        receipt: dict[str, Any] = {"schema": "taskplane.context-delivery-receipt/v1",
            "binding": self.binding, "handoff_digest": self.handoff["digest"],
            "view_digest": self.view["digest"], "returned_refs": returned,
            "returned_bytes": ledger["returned_bytes"], "event_id": event_id,
            "status": "returned", "model_attention": "unobservable"}
        response: dict[str, Any] = {**payload, "remaining_required": len(self.required_trees) - len(returned)}
        if payload.get("schema") == "taskplane.context-drain/v1":
            response["done"] = response["remaining_required"] == 0
            response["next_action"] = None
        if response["remaining_required"]:
            response["next_action"] = context_action(self.workspace, self.state["run"], self.task,
                operation="drain" if "done" in response else "read-required", key=self.handoff_ref["sha256"])
        for _ in range(4):
            reference = (self.store.put if persist else self.store.reference)("delivery-receipt", receipt)
            response["context_receipt"] = {"receipt": reference, "consumed_inputs": consumed_inputs(returned)}
            total_bytes = ledger["returned_bytes"] + len(encode(response))
            if total_bytes == receipt["returned_bytes"]:
                break
            receipt["returned_bytes"] = total_bytes
        else:
            raise w.Refusal("invalid_context", "Receipt byte accounting did not converge.")
        ledger.update(seen=sorted(seen), returned_bytes=receipt["returned_bytes"])
        ledger["receipts"].append(reference["sha256"])
        return response, ledger

    def _deliver(self, payload: dict[str, Any], seen: set[str],
                 page: tuple[str, int, int] | None = None, *, limit: int = 16384,
                 page_updates: list[tuple[str, int, int]] | None = None) -> dict[str, Any]:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with primitives.file_lock(str(self.ledger_path)):
            response, ledger = self._preview(payload, self.ledger(), seen,
                ([page] if page else []) + (page_updates or []), event_id="returned/" + uuid.uuid4().hex,
                persist=True)
            # Preview may create immutable objects, but only a returned response
            # commits delivery. A refusal cannot grant a receipt or advance pages.
            w.require(len(encode(response)) < limit, "context_overflow", "Returned context exceeds its envelope budget.")
            w.require(len(ledger["receipts"]) <= 10000, "context_overflow", "Current delivery event limit reached.")
            primitives.atomic_json(self.ledger_path, ledger)
            self.store.register(self.read_binding, [response["context_receipt"]["receipt"]])
            return response

    def consume(self, key: str) -> dict[str, Any]:
        if "inline" not in self.view:
            self.view = self.store.resolve(self.view_ref)
        w.require(key == self.handoff_ref["sha256"], "invalid_context", "Stale or foreign handoff.")
        seen: set[str] = set()
        for item in self.items:
            if item["id"] in self.view["inline"]:
                ref = self.input_refs[item["id"]]
                seen |= self.delivery_trees[ref["sha256"]] if ref["sha256"] in self.delivery_trees else self.store.descendants(ref)
        response = self._deliver({"schema": "taskplane.context-consumption/v1", "view": self.view}, seen,
                                 limit=PHASE_BYTES[self.phase])
        w.require(len(encode(response)) <= PHASE_BYTES[self.phase], "context_overflow", "Consumed view exceeds phase budget.")
        return response

    @observed('context-delivery')
    def read(self, key: str, page: int = 0, section: str | None = None) -> dict[str, Any]:
        if self.allowed is None:
            if not self.__dict__.get("_legacy_loaded", True):
                self.refs  # Materialize/register compatibility references only for explicit reads.
            self.allowed = set()
            for ref in self.store.roots(self.read_binding):
                self.allowed |= self.store.descendants(ref)
            if self.journal.path.exists():
                with primitives.file_lock(str(self.journal.path)):
                    _, receipts = self.journal.replay(self.journal.load())
                for row in receipts:
                    self.allowed |= self.store.descendants(row["ref"])
        w.require(key in self.allowed, "invalid_context", "Reference was not supplied for this current binding.")
        result = self.store.page(key, page, section)
        response = self._deliver({"schema": "taskplane.context-read/v1", "page": result}, set(),
                                (key, page, result["pages"]) if section is None else None)
        w.require(len(encode(response)) <= 16384, "context_overflow", "Read envelope exceeds its page budget.")
        return response

    def drain(self, key: str) -> dict[str, Any]:
        """Bound the combined bodies and receipt, not each page independently."""
        return self.read_required(key, _drain=True)

    @observed('completion-reconciliation')
    def _reconcile(self, checkpoint: dict[str, Any], additional: Sequence[tuple[str, int]] = ()) -> tuple[list[str], list[dict[str, Any]]]:
        # The controller must retain this work inside its full-command measurement.
        count("selection", len(additional))
        observed, receipts = self.journal.replay(checkpoint)
        observed.update(additional)
        manifest = self.store.node(self.coverage_ref)["data"]
        w.require(manifest.get("schema") == "taskplane.context-required-manifest/v1"
                  and manifest.get("binding") in (binding(self.state), self.binding),
                  "invalid_context", "Coverage manifest belongs to another binding.")
        roots = {ref["sha256"]: ref for row in manifest["required"]
                 for ref in [row["body"], *row["provenance"]]}
        covered = graph_coverage(self.store, list(roots.values()), observed)
        count("sort", len(manifest["required"]))
        returned = sorted(row["root"]["sha256"] for row in manifest["required"]
                          if all(covered[ref["sha256"]] for ref in [row["body"], *row["provenance"]]))
        w.require(len(returned) == len(manifest["required"]), "invalid_context",
                  "Independent reconciliation found unreturned required context.")
        return returned, receipts

    @observed('context-delivery')
    def read_required(self, key: str, *, _drain: bool = False) -> dict[str, Any]:
        """Read only the next indexed pages; reconcile the full graph once at completion."""
        w.require(key == self.handoff_ref["sha256"], "invalid_context", "Stale or foreign handoff.")
        primitives._durable_makedirs(str(self.journal.path.parent), mode=0o700)
        limit = 32768 if _drain else PAGE_LIMIT
        with primitives.file_lock(str(self.journal.path)):
            checkpoint = self.journal.load()
            total = self.page_index["pages"]
            w.require(checkpoint["cursor"] <= total, "invalid_context", "Delivery cursor exceeds index.")
            # Convert explicit legacy consume/read observations once. The compact
            # bitmap is only a skip hint; final coverage comes from actual pages.
            if checkpoint["sequence"] == 0 and self.ledger_path.exists():
                legacy = self.ledger()
                observed = {(sha, n) for sha, numbers in legacy["pages"].items() for n in numbers}
                for sha in legacy["seen"]:
                    first = self.store.page(sha)
                    observed.update((sha, n) for n in range(first["pages"]))
                mask = 0
                for offset in range(total):
                    sha, number, _ = self.index.entry(offset)
                    if (sha, number) in observed:
                        mask |= 1 << offset
                checkpoint["skip_bitmap"] = hex(mask)
                checkpoint = self.journal.append(checkpoint, sorted(observed), None, 0, legacy["returned_bytes"])
            cursor = checkpoint['cursor']
            pages: list[dict[str, Any]] = []
            updates: list[tuple[str, int]] = []
            skip = int(checkpoint.get("skip_bitmap", "0"), 16)
            # A fixed reserve bounds the receipt, continuation and handoff metadata.
            # Completion lists use a digest above 64 roots, never a growing prefix.
            used = 0
            while cursor < total and len(pages) < 64:
                count("selection")
                if skip & (1 << cursor):
                    count("index_lookup")
                    cursor += 1
                    continue
                sha, number, expected_pages = self.index.entry(cursor)
                page = self.store.page(sha, number)
                w.require(page["pages"] == expected_pages, "invalid_context", "Indexed page extent changed.")
                size = len(encode(page)) + 1
                if used + size > limit - 6144:
                    break
                pages.append(page)
                updates.append((sha, number))
                used += size
                cursor += 1
            w.require(cursor == total or pages, "context_overflow", "No required page fits the batch response budget.")
            done = cursor == total
            if done and checkpoint.get("final_receipt"):
                receipt_ref = checkpoint["final_receipt"]["ref"]
                receipt = self.store.resolve(receipt_ref)
                returned, terminal_receipts = self._reconcile(checkpoint)
                w.require(receipt.get("binding") == self.binding
                          and receipt.get("handoff_digest") == self.handoff["digest"]
                          and receipt.get("view_digest") == self.view["digest"]
                          and receipt.get("returned_refs") == returned
                          and any(row.get("complete") and row.get("ref") == receipt_ref for row in terminal_receipts),
                          "invalid_context", "Cached terminal receipt is not a complete delivered receipt.")
                response: dict[str, Any] = {"schema": "taskplane.context-drain/v1" if _drain else "taskplane.context-read-batch/v1",
                            "handoff_ref": self.handoff_ref, "pages": [], "remaining_required": 0,
                            "next_action": None, "context_receipt": {"receipt": receipt_ref,
                            "consumed_inputs": consumed_inputs(returned)}}
                if _drain:
                    response["done"] = True
                w.require(len(encode(response)) < limit, "context_overflow", "Terminal receipt exceeds budget.")
                return response
            elif done:
                returned, _ = self._reconcile(checkpoint, updates)
                receipt_ref = None
            else:
                returned, receipt_ref = [], None
            remaining = 0 if done else self.preflight()["required_roots"]
            response = {"schema": "taskplane.context-drain/v1" if _drain else "taskplane.context-read-batch/v1",
                        "handoff_ref": self.handoff_ref, "pages": pages, "remaining_required": remaining,
                        "next_action": None if done else context_action(self.workspace, self.state["run"], self.task,
                            operation="drain" if _drain else "read-required", key=key)}
            if _drain:
                response["done"] = done
            receipt = {"schema": "taskplane.context-delivery-receipt/v1", "binding": self.binding,
                       "handoff_digest": self.handoff["digest"], "view_digest": self.view["digest"],
                       "returned_refs": returned, "returned_bytes": checkpoint["returned_bytes"],
                       "event_id": "returned/" + uuid.uuid4().hex,
                       "status": "returned", "model_attention": "unobservable"}
            for _ in range(4):
                reference = self.store.reference("delivery-receipt", receipt)
                response["context_receipt"] = {"receipt": reference, "consumed_inputs": consumed_inputs(returned)}
                amount = checkpoint["returned_bytes"] + len(encode(response))
                if receipt["returned_bytes"] == amount:
                    break
                receipt["returned_bytes"] = amount
            else:
                raise w.Refusal("invalid_context", "Receipt byte accounting did not converge.")
            w.require(len(encode(response)) < limit, "context_overflow", "Returned context exceeds its envelope budget.")
            reference = self.store.put("delivery-receipt", receipt)
            count("receipt_write")
            response["context_receipt"]["receipt"] = reference
            self.journal.append(checkpoint, updates, {"ref": reference, "complete": done}, cursor, receipt["returned_bytes"])
            return response

    def validate(self, value: Any) -> None:
        w.require(isinstance(value, dict) and isinstance(value.get("receipt"), dict),
                  "invalid_context", "Phase output needs its returned context receipt.")
        receipt = self.store.resolve(value["receipt"])
        if self.journal.path.exists():
            with primitives.file_lock(str(self.journal.path)):
                checkpoint = self.journal.load()
                required, receipts = self._reconcile(checkpoint)
            w.require(any(row["ref"] == value["receipt"] and row.get("complete") for row in receipts)
                      and receipt.get("binding") == self.binding
                      and receipt.get("handoff_digest") == self.handoff["digest"]
                      and receipt.get("view_digest") == self.view["digest"]
                      and receipt.get("schema") == "taskplane.context-delivery-receipt/v1"
                      and receipt.get("status") == "returned"
                      and receipt.get("returned_refs") == required
                      and value.get("consumed_inputs") in (required, consumed_inputs(required)),
                      "invalid_context", "Receipt was not completely returned for this current handoff.")
            return
        w.require(isinstance(receipt, dict) and receipt.get("schema") == "taskplane.context-delivery-receipt/v1"
                  and receipt.get("status") == "returned" and receipt.get("binding") == self.binding
                  and receipt.get("handoff_digest") == self.handoff["digest"]
                  and receipt.get("view_digest") == self.view["digest"]
                  and value["receipt"]["sha256"] in self.ledger()["receipts"],
                  "invalid_context", "Receipt was not returned for this current handoff.")
        required = sorted({item["ref"]["sha256"] for item in self.required})
        # Exact legacy lists remain valid, including receipts issued before this
        # compact representation. Neither form can replace the bound ledger proof.
        w.require(receipt.get("returned_refs") == required
                  and value.get("consumed_inputs") in (required, consumed_inputs(required)),
                  "invalid_context", "Required inherited context has not been returned to the consumer.")


def consume_required(session: Session) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Programmatic consumer using the same public operations; retain every returned body."""
    responses = [session.consume(session.handoff_ref["sha256"])]
    while responses[-1]["remaining_required"]:
        responses.append(session.read_required(session.handoff_ref["sha256"]))
    return responses[-1]["context_receipt"], responses
