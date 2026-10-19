"""Scoped native attempts. Callers serialize every mutation with the controller lock.

Native observations are cooperative evidence, not host authentication. A prompt
grant identifies a reservation; only observed lineage/result IDs bind a worker.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import re
import shlex
import sys
from typing import Any, cast
import uuid

from . import workflow as w, workflow_evidence as e
from .context import Store, digest
from .context_handoff import Brief, binding

# Codex desktop also flattens the namespace in actual hook payloads (observed
# collaborationlist_agents). Keep these exact aliases; never strip arbitrary prefixes.
SPAWN = {"spawn_agent", "collaboration.spawn_agent", "functions.collaboration.spawn_agent", "collaborationspawn_agent", "Agent", "Task"}
FOLLOW = {"followup_task", "collaboration.followup_task", "functions.collaboration.followup_task", "collaborationfollowup_task"}
MESSAGE = {"send_message", "collaboration.send_message", "functions.collaboration.send_message", "collaborationsend_message"}
INTERRUPT = {"interrupt_agent", "collaboration.interrupt_agent", "functions.collaboration.interrupt_agent", "collaborationinterrupt_agent"}
STATUS = {"list_agents", "collaboration.list_agents", "functions.collaboration.list_agents", "collaborationlist_agents"}
WAIT = {"wait_agent", "collaboration.wait_agent", "functions.collaboration.wait_agent", "collaborationwait_agent"}
HANDBACK = {"SubagentHandback"}
CLAUDE_CONTROL = {"SendMessage", "TaskStop"}
CLAUDE_STATUS = {"ListAgents"}
DISCOVERY = {"ToolSearch"}
TOOLS = SPAWN | FOLLOW | MESSAGE | INTERRUPT | STATUS | WAIT | HANDBACK | CLAUDE_CONTROL | CLAUDE_STATUS | DISCOVERY
CLAUDE_TOOLS = {"Agent", "Task"} | HANDBACK | CLAUDE_CONTROL | CLAUDE_STATUS | DISCOVERY
LIVE = {"prepared", "launch_pending", "bootstrapping", "running", "cancel_requested", "unknown"}
STATES = LIVE | {"result_pending", "accepted", "failed", "interrupted"}
MARKER = re.compile(r"(?m)^Taskplane grant: ([0-9a-f]{32})$")
NAME_GRANT = re.compile(r"[a-z0-9_]{1,46}__([0-9a-f]{32})")
RECOVERY_HOSTS = {'recover-unbound': 'codex', 'recover-unavailable': 'codex',
                  'recover-launch-denied': 'claude', 'reconcile-handback': 'claude'}


def recovery_capabilities(host: str) -> dict[str, Any]:
    """Host routing only. Eligibility still needs current state and native evidence."""
    return {
        'host': host,
        'operations': {operation: {'status': 'requires_evidence' if owner == host else 'unsupported_host',
                                   'host': owner}
                       for operation, owner in RECOVERY_HOSTS.items()},
        'abandon': 'Only a prepared reservation with no admitted launch; never erase an actual escaped launch.',
        'next_action': (
            'Claude recover-launch-denied only reconciles an exact proven legacy launch denial with no child/work. '
            'reconcile-handback revalidates an owned attempt\'s recorded handback and completion; it never accepts a result. '
            'For a launched owned worker, use exposed TaskStop and observed native completion under its current grant. '
            'An unbound identity conflict has no general recovery adapter: preserve the attempt and evidence, '
            'use flow wait for the capability repair, and do not promise a retry or substitute root work.'
            if host == 'claude' else
            'Codex recover-unbound requires an unadmitted prepared grant and distinct native spawn/terminal calls; '
            'recover-unavailable requires the exact interruption/not-found proof. Neither accepts results or proves process exit.'),
        'authority': 'No eligibility, tool availability, approval or process termination is inferred.'}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def records(state: dict[str, Any]) -> dict[str, Any]:
    value: dict[str, Any] = state.setdefault("workers", {})
    return value


def validate(state: dict[str, Any]) -> None:
    rows = state.get("workers", {})
    w.require(isinstance(rows, dict) and len(rows) <= 1024, "state_unavailable", "Invalid worker inventory.")
    for key, row in rows.items():
        w.require(isinstance(row, dict) and row.get("grant_id") == key and row.get("state") in STATES
                  and row.get("run") == state["run"] and row.get("root") == state["root"]
                  and type(row.get("attempt")) is int and isinstance(row.get("paths"), list),
                  "state_unavailable", "Invalid stored worker binding.")


def joined(state: dict[str, Any]) -> bool:
    return not any(r["state"] in LIVE or is_launch_denial(r) and (
        r.get('identity_conflict') or r.get('denial_freshness', {}).get('status') != 'launch_denied')
        for r in state.get("workers", {}).values())


def task(state: dict[str, Any], task_id: str) -> dict[str, Any]:
    rows = e.context_tasks(state)
    found = next((r for r in rows if r["id"] == task_id), None)
    w.require(found is not None, "invalid_evidence", "Unknown run-bound task.")
    assert found is not None
    return dict(e.task_definitions([found])[task_id])


def read_input_manifest(workspace: Path, session: Brief, owned_paths: list[str]) -> dict[str, Any]:
    """Fingerprint declared dependencies independently of context transport."""
    declarations = e.read_inputs(session.state, task(session.state, session.task)) if session.task else []
    return e.manifest(workspace, sorted(set(declarations) - set(owned_paths)))


def verification_plan(state: dict[str, Any], task_id: str) -> str | None:
    try:
        strategy = w.accepted_plan(state)['packet']['output'].get('verification_strategy', {})
    except w.Refusal:
        return None
    if not isinstance(strategy, dict) or strategy.get('schema') != e.VERIFICATION_STRATEGY:
        return None
    checks = [check for check in strategy.get('checks', []) if check.get('task') == task_id]
    return digest(checks) if checks else None


def retained_authority(workspace: Path, state: dict[str, Any], task_id: str,
                       result: dict[str, Any]) -> dict[str, Any] | None:
    """Validate historical execution evidence, never revive an execution grant.

    Ordinary current results keep their existing contract. Crossing an explicit
    Product revision additionally needs unchanged epochs, check definitions and
    complete current verification keys. Unknown coverage is a reuse miss.
    """
    definition = task(state, task_id)
    criteria = e.task_criteria(definition)
    if (result.get('task_epoch', 0) != state.get('task_epochs', {}).get(task_id, 0)
            or result.get('requirements_epochs', {c: 0 for c in criteria}) !=
                {c: state.get('criterion_epochs', {}).get(c, 0) for c in criteria}):
        return None
    events = [event for event in state.get('requirements_revisions', {}).values()
              if event.get('opened') and event['preview']['binding']['revision'] >= result.get('accepted_revision', 0)]
    transitions = [event for field in ('context_upgrades', 'context_rollbacks')
                   for event in state.get(field, {}).values()
                   if event['prior_revision'] >= result.get('accepted_revision', 0)]
    if not events and not transitions:
        return state
    if e.changed(workspace, state) is not None:
        return None
    if (any(event['status'] != 'completed' or task_id in event['preview']['tasks'] for event in events)
            or not result.get('verification_plan')
            or result['verification_plan'] != verification_plan(state, task_id)):
        return None
    from . import context_reuse, host_capabilities
    if result.get('runtime_digest') != digest(host_capabilities.runtime_identity()):
        return None
    store = Store(workspace)
    try:
        expected = [check for check in w.accepted_plan(state)['packet']['output']['verification_strategy']['checks']
                    if check['task'] == task_id]
        covered = set()
        for check in result.get('checks', []):
            reference = check.get('reuse_ref')
            if not isinstance(reference, dict):
                return None
            prior = store.resolve(reference)
            key = prior['key']
            matches = [item for item in expected
                if key.get('paths') == sorted(item['source_inputs'])
                and key.get('tests') == sorted(item['test_inputs'])
                and key.get('criteria') == sorted(item['criteria'])
                and key.get('command') == item['command']]
            if len(matches) != 1:
                return None
            covered.add(matches[0]['id'])
            current_key = context_reuse.revalidate(workspace, prior, state=state)
            if context_reuse.lookup(store, reference, current_key)['status'] != 'reused':
                return None
        if not result.get('checks') or not {item['id'] for item in expected if item.get('required')} <= covered:
            return None
        if not result.get('grant'):
            return state
        row = state.get('workers', {}).get(result['grant'])
        if not row:
            return None
        for event in sorted(events, key=lambda value: value['preview']['binding']['revision']):
            reference = event.get('completion_basis')
            if not reference:
                continue
            basis = store.resolve(reference)
            if not isinstance(basis, dict):
                return None
            old = basis.get('workers', {}).get(result['grant'])
            if (basis.get('run') == state['run'] and basis.get('root') == state['root']
                    and old and old.get('binding') == row.get('binding')
                    and basis.get('task_results', {}).get(task_id) == result
                    and digest(task(basis, task_id)) == digest(definition)):
                return basis
    except (w.Refusal, ValueError, KeyError, TypeError, OSError):
        return None
    return None


def native_result_valid(workspace: Path, state: dict[str, Any], task_id: str,
                        result: dict[str, Any]) -> bool:
    """Validate actual joined native identity, never an asserted reviewer label."""
    row = state.get("workers", {}).get(result.get("grant"))
    if not row or row.get("task_id") != task_id or row.get("state") != "accepted":
        return False
    if row.get('acceptance_invalidation'):
        return False
    if (not row.get("worker_id") or row["worker_id"] == state["root"]
            or not row.get('claimed_at') or row.get('revoked_at') or row.get('identity_conflict')
            or row["worker_id"] != result.get("worker_id")
            or row.get("terminal_status") not in {"completed", "idle"}
            or row.get("task_digest") != result.get("task_digest")
            or row.get("input_manifest") != result.get("input_manifest")
            or row.get("dependency_results") != result.get("dependency_results")):
        return False
    try:
        basis = retained_authority(workspace, state, task_id, result)
        if basis is None:
            return False
        worker_session(workspace, basis, row)
        return True
    except w.Refusal:
        return False


def result_valid(workspace: Path, state: dict[str, Any], task_id: str, seen: set[str] | None = None) -> bool:
    seen = set(seen or ())
    if task_id in seen:
        return False
    seen.add(task_id)
    definition = task(state, task_id)
    for stage in state["visits"][:state["index"]]:
        if (stage["decision"] == "approved" and not stage.get("superseded") and stage.get("packet")
                and definition.get("phase") != w.current(state)["phase"]):
            if any(t["id"] == task_id for t in stage["packet"].get("context", {}).get("tasks", [])
                   if t.get("phase") == stage["phase"]):
                return e.changed(workspace, state) is None
    result = state.get("task_results", {}).get(task_id)
    if not result or result.get("task_digest") != digest(definition):
        return False
    if retained_authority(workspace, state, task_id, result) is None:
        return False
    required_reads = set(e.read_inputs(state, definition)) - set(definition['paths'])
    if not required_reads <= set(result.get('input_manifest', {})):
        return False  # Older incomplete manifests need fresh verification too.
    if (result.get('grant') or definition.get("execution") == "native_required") and not native_result_valid(workspace, state, task_id, result):
        return False
    if not result.get("grant") and result.get("input_contract") != "declared-source/v1":
        return False
    try:
        return (result["manifest"] == e.manifest(workspace, list(result["manifest"]))
                and (not result.get("grant") or "input_manifest" in result)
                and result.get("input_manifest", {}) == e.manifest(workspace, list(result.get("input_manifest", {})))
                and result.get("dependency_results", {}) == dependency_results(state, definition["dependencies"])
                and all(result_valid(workspace, state, d, seen) for d in definition["dependencies"]))
    except w.Refusal:
        return False


def dependency_results(state: dict[str, Any], dependencies: list[str]) -> dict[str, str]:
    """Pin accepted result identities, including accepted earlier-phase packets."""
    result = {}
    for key in dependencies:
        prior = next((stage["packet"] for stage in state["visits"][:state["index"]]
                      if stage["decision"] == "approved" and not stage.get("superseded") and stage.get("packet")
                      and any(t["id"] == key and t.get("phase") == stage["phase"]
                              for t in stage["packet"].get("context", {}).get("tasks", []))), None)
        result[key] = digest(prior if prior is not None else state.get("task_results", {}).get(key))
    return result


def dependency_manifest(state: dict[str, Any], dependencies: list[str], seen: set[str] | None = None) -> dict[str, str]:
    seen = set(seen or ())
    files: dict[str, str] = {}
    for key in dependencies:
        if key in seen:
            continue
        seen.add(key)
        result = state.get("task_results", {}).get(key, {})
        files.update(result.get("manifest", {}))
        files.update(result.get("input_manifest", {}))
        files.update(dependency_manifest(state, task(state, key)["dependencies"], seen))
    return files


def current(state: dict[str, Any], row: dict[str, Any]) -> None:
    from . import workspace_binding
    from .workflow_write_guard import require_runtime
    require_runtime(state)
    w.require(not state.get('cancellation'), 'scope_violation', 'The cancelled run has no worker execution grants.')
    workspace_binding.ensure(Path(state["workspace"]), worker=True,
                             expected=state.get("workspace_contract"))
    w.require(not row.get('revoked_at'), 'scope_violation', 'Worker grant is permanently revoked.')
    w.require(row.get('task_epoch', 0) == state.get('task_epochs', {}).get(row['task_id'], 0)
              and row.get('requirements_epochs', {c: 0 for c in row['criteria']}) ==
                  {c: state.get('criterion_epochs', {}).get(c, 0) for c in row['criteria']},
              'stale_checkpoint', 'Worker requirements or affected task epoch changed.')
    w.require(row.get("workspace_contract") == state.get("workspace_contract"),
              "stale_checkpoint", "Worker workspace contract differs from its run.")
    w.require(row["binding"] == binding(state) and row["task_digest"] == digest(task(state, row["task_id"])),
              "stale_checkpoint", "Worker grant belongs to an old authority or task generation.")


def require_execution(row: dict[str, Any]) -> None:
    """Cancellation fences work immediately; it never proves process exit."""
    w.require(not row.get('stop_requested') and not row.get('cancel_requested')
              and row.get('state') != 'cancel_requested', 'scope_violation',
              'Worker was stopped or cancellation is pending; observe genuine termination before further work.')


def capacity(value: Any, *, host: str | None = None) -> int:
    if isinstance(value, dict) and value.get('host_slots', False) is None:
        w.require(host == 'claude' and value.get('includes_root') is False
                  and type(value.get('configured_limit')) is int and value['configured_limit'] > 0
                  and isinstance(value.get('reference'), str) and 0 < len(value['reference']) <= 512
                  and value.get('status') in (None, 'available'),
                  'invalid_evidence', 'Unknown Claude host capacity requires an explicit positive worker admission budget.')
        limit = value['configured_limit']
        if value.get('resource_limit') is not None:
            w.require(type(value['resource_limit']) is int and value['resource_limit'] >= 0,
                      'invalid_evidence', 'Invalid worker limit.')
            limit = min(limit, value['resource_limit'])
        return int(limit)
    w.require(isinstance(value, dict) and type(value.get("host_slots")) is int
              and value["host_slots"] >= 0 and type(value.get("includes_root")) is bool
              and isinstance(value.get("reference"), str) and 0 < len(value["reference"]) <= 512,
              "invalid_evidence", "Supply observed host capacity and its source; no default worker cap is assumed.")
    limits = [max(0, value["host_slots"] - int(value["includes_root"]))]
    for key in ("configured_limit", "resource_limit"):
        if value.get(key) is not None:
            w.require(type(value[key]) is int and value[key] >= 0, "invalid_evidence", "Invalid worker limit.")
            limits.append(value[key])
    w.require(value.get("status") in (None, "available", "unavailable"),
              "invalid_evidence", "Unknown native capacity status.")
    if value.get("status") == "unavailable":
        w.require(min(limits) == 0 and isinstance(value.get("reason"), str) and value["reason"].strip(),
                  "invalid_evidence", "Unavailable native capacity needs zero slots and an observed reason.")
    return int(min(limits))


def path_conflict(candidate: dict[str, Any], other: dict[str, Any]) -> bool:
    writes, other_writes = set(candidate['paths']), set(other['paths'])
    reads = set(candidate.get('input_manifest', {})) | set(candidate.get('dependency_manifest', {}))
    other_reads = set(other.get('input_manifest', {})) | set(other.get('dependency_manifest', {}))
    return bool(writes & (other_writes | other_reads) or reads & other_writes)


def readiness(row: dict[str, Any]) -> dict[str, Any]:
    if (row.get("state") in {"cancel_requested", "interrupted", "failed", "result_pending", "accepted"}
            or row.get("terminal_status") or row.get("revoked_at")
            or row.get("stop_requested") or row.get("cancel_requested")):
        return {"status": "inactive", "missing": ["live uncancelled attempt"],
                "basis": "Terminal or cancelled attempts have no execution permission."}
    missing = [label for label, valid in (("native identity", bool(row.get("worker_id"))),
                                        ("observed claim", bool(row.get("claimed_at"))),
                                        ("no identity conflict", not row.get("identity_conflict"))) if not valid]
    return {"status": "ready" if not missing else "pending", "missing": missing,
            "basis": "Observed native identity and current attempt."}


class BudgetRefusal(w.Refusal):
    """Validated budget observations must survive an admission refusal."""


def _result_runtime() -> dict[str, Any]:
    from . import worker_results as results, host_capabilities
    return {'sha256': digest(host_capabilities.runtime_identity()),
            'schemas': [results.SCHEMA, results.RESULT_CONTRACT, results.LEDGER, results.BUDGET],
            'capabilities': sorted(results.CAPABILITIES)}


def _result_binding(state: dict[str, Any], definition: dict[str, Any]) -> dict[str, Any]:
    return {'run': state['run'], 'task': definition['id'],
            'task_epoch': state.get('task_epochs', {}).get(definition['id'], 0),
            'requirements_epochs': {cid: state.get('criterion_epochs', {}).get(cid, 0)
                                    for cid in e.task_criteria(definition)}}


def _result_fingerprints(workspace: Path, state: dict[str, Any], definition: dict[str, Any]) -> dict[str, Any]:
    from . import worker_results as results, context_reuse
    declared = results.contract(definition)
    assert declared is not None
    try:
        strategy = w.accepted_plan(state)['packet']['output'].get('verification_strategy', {})
        plan_roles = e.typed_plan(w.accepted_plan(state)['packet']['output'])
    except w.Refusal:
        strategy = definition.get('verification', {})
        plan_roles = False
    checks = strategy.get('checks', []) if isinstance(strategy, dict) and strategy.get('schema') == e.VERIFICATION_STRATEGY else []
    allowed = set(e.read_inputs(state, definition)) | set(definition['paths'])
    keys = {}
    for cid, obligation in results._obligations(declared).items():
        matched = [check for check in checks if check.get('task', definition['id']) == definition['id']
                   and cid in check.get('criteria', [])]
        # An ambiguous or untyped verification plan cannot manufacture reusable
        # evidence. It still gets an immutable, explicitly unknown contribution.
        if len(matched) == 1:
            check = matched[0]
            source, tests, command = check.get('source_inputs'), check.get('test_inputs'), check.get('command')
            if (all(isinstance(items, list) and items and all(isinstance(p, str) for p in items)
                    for items in (source, tests, command)) and set(source + tests) <= allowed):
                keys[cid] = context_reuse.contribution_key(workspace, paths=source, tests=tests,
                    outputs=sorted(set(obligation['outputs'] + obligation['evidence'])),
                    criteria={cid: obligation}, command=command, tool='taskplane-root-verification',
                    contract=declared['schema'], **({'state': state, 'check_id': check['id']}
                                                   if plan_roles else {}))
                continue
        body = {'schema': 'taskplane.contribution-key/v1', 'coverage': 'unknown', 'eligible': False,
                'criteria': {cid: obligation}, 'reason': 'Complete current verification plan/fingerprints unavailable.'}
        keys[cid] = {**body, 'digest': digest(body)}
    return keys


def _result_policy(state: dict[str, Any], definition: dict[str, Any], *, initialize: bool) -> str | None:
    from . import worker_results as results
    declared = results.contract(definition, required=False)
    if declared is None:
        w.require(definition['id'] not in state.get('result_ledgers', {})
                  and 'task/' + definition['id'] not in state.get('retry_budgets', {}),
                  'completion_contract_required', 'An admitted completion/retry contract cannot be removed or made opaque.')
        return None
    ledger_id = str(definition['id'])
    ledgers = state.setdefault('result_ledgers', {})
    identity = _result_binding(state, definition)
    if ledger_id not in ledgers:
        w.require(initialize, 'completion_contract_required', 'Result contract was not admitted before this native attempt.')
        ledgers[ledger_id] = results.new_ledger(identity, definition, _result_runtime())
    ledger = ledgers[ledger_id]
    w.require(ledger['binding'] == identity and ledger['contract_digest'] == digest(declared),
              'stale_checkpoint', 'Result contract or requirements changed; explicit compatible migration is required.')
    results.enforce_runtime(ledger['runtime_pin'], _result_runtime())
    scopes = {'task/' + definition['id']: ({'run': state['run'], 'task': definition['id']},
                                         results.policy_for_definition(definition))}
    if state['scope'].get('retry_policy') is not None:
        scopes['run'] = ({'run': state['run']}, state['scope']['retry_policy'])
    stage = w.current(state)
    if stage.get('retry_policy') is not None:
        scopes['visit/' + stage['id']] = ({'run': state['run'], 'visit': stage['id']}, stage['retry_policy'])
    budgets = state.setdefault('retry_budgets', {})
    # Removing mutable policy metadata cannot remove an already frozen scope.
    for name, scope_binding in [('run', {'run': state['run']}),
            ('visit/' + stage['id'], {'run': state['run'], 'visit': stage['id']})]:
        if name in budgets and name not in scopes:
            scopes[name] = (scope_binding, None)
    for name, (scope_binding, policy) in scopes.items():
        if name not in budgets:
            w.require(initialize, 'completion_contract_required', 'Retry scope was not admitted before this attempt.')
            assert isinstance(policy, dict)
            budgets[name] = results.new_budget(scope_binding, policy, runtime=_result_runtime())
        else:
            w.require(budgets[name]['binding'] == scope_binding, 'stale_checkpoint', 'Retry scope changed.')
            results.enforce_runtime(budgets[name]['runtime_pin'], _result_runtime())
    ledger['budget_scopes'] = sorted(scopes)
    return ledger_id


def _retry_usage(workspace: Path, state: dict[str, Any], budget: dict[str, Any]) -> dict[str, Any]:
    """Only the native owned-interval reader supplies usage; request counters are ignored."""
    unknown = {'coverage': 'unknown', 'source': 'native owned intervals unavailable'}
    scope = budget['binding']
    owned = [row for row in records(state).values()
             if ('task' not in scope or row['task_id'] == scope['task'])
             and ('visit' not in scope or row['binding']['visit'] == scope['visit'])]
    if 'task' in scope and not any(row.get('call_id') for row in owned):
        return {'input': 0, 'output': 0, 'cached_input': 0, 'coverage': 'complete',
                'source': 'controller proves no native task delivery has issued'}
    if any(row.get('call_id') and not row.get('worker_id') for row in owned):
        return unknown
    try:
        from . import flow
        from . import live_telemetry
        start = live_telemetry.read(workspace, state)['metadata']
        measured = flow.reconcile_usage(start, [], state)
        coverage = measured['token_coverage']
        if any(coverage.get(key, 1) for key in ('unmeasured_sessions', 'partial_sessions', 'discovery_errors', 'inventory_errors')):
            return unknown
        identities = {row['worker_id'] for row in owned if row.get('worker_id')}
        sessions = [session for session in measured['sessions']
                    if session.get('role') != 'host_approval_review' and
                    ('task' not in scope and 'visit' not in scope or
                     session.get('session') in identities or session.get('agent') in identities)]
        if any(not session.get('usage') for session in sessions):
            return unknown
        usage = {field: sum(session['usage'].get(source, 0) for session in sessions)
                 for field, source in [('input', 'input_tokens'), ('output', 'output_tokens'),
                                       ('cached_input', 'cached_input_tokens')]}
        return {**usage, 'coverage': 'complete', 'source': 'native owned intervals/' + digest(measured)}
    except (OSError, ValueError, KeyError, TypeError):
        return unknown


def refresh_result_budgets(workspace: Path, state: dict[str, Any]) -> None:
    from . import worker_results as results
    stamp = int(datetime.now(timezone.utc).timestamp() * 1000)
    for name, budget in list(state.get('retry_budgets', {}).items()):
        results.enforce_runtime(budget['runtime_pin'], _result_runtime())
        policy = None
        if name.startswith('task/'):
            policy = results.policy_for_definition(task(state, budget['binding']['task']))
        elif name == 'run':
            policy = state['scope'].get('retry_policy')
        elif budget['binding'].get('visit') == w.current(state)['id']:
            policy = w.current(state).get('retry_policy')
        current_budget = results.observe_budget(budget, now_ms=stamp,
            usage=_retry_usage(workspace, state, budget), policy=policy)
        for key, reservation in list(current_budget['reservations'].items()):
            row = records(state).get(reservation['attempt_id'])
            if not row or reservation['state'] in {'terminal', 'no_launch'}:
                continue
            proof = {'source': 'controller worker/' + row['grant_id'],
                     'terminal': bool(row.get('terminal_status')) and row['state'] not in LIVE,
                     'usage_complete': current_budget['coverage_known']}
            if row.get('terminal_status') == 'not_launched' and not row.get('call_id'):
                current_budget = results.reservation_transition(current_budget, key, state='no_launch',
                    proof={**proof, 'authoritative_no_launch': True})
            elif (proof['terminal'] and reservation['state'] in {'issued', 'uncertain'}
                  and ('token_cap' not in current_budget['frozen_effective_limits'] or proof['usage_complete'])):
                current_budget = results.reservation_transition(current_budget, key, state='terminal', proof=proof)
        state['retry_budgets'][name] = current_budget


def _reserve_result_dispatch(workspace: Path, state: dict[str, Any], row: dict[str, Any],
                             request: dict[str, Any]) -> None:
    from . import worker_results as results
    definition = task(state, row['task_id'])
    prior = [r for r in records(state).values() if r['task_id'] == row['task_id']]
    ledger_id = _result_policy(state, definition, initialize=not prior)
    if ledger_id is None:
        return
    ledger = state['result_ledgers'][ledger_id]
    refreshed = results.refresh_residuals(ledger, Store(workspace), _result_fingerprints(workspace, state, definition))
    ledger.clear(); ledger.update(refreshed)
    refresh_result_budgets(workspace, state)
    proposed = request.get('tokens_proposed', 0)
    budgets = [state['retry_budgets'][key] for key in ledger['budget_scopes']]
    decisions = [results.budget_admission(budget, tokens_proposed=proposed) for budget in budgets]
    if not all(decision['dispatch_admitted'] for decision in decisions):
        raise BudgetRefusal('retry_budget_exhausted', 'Frozen retry limits or unknown native usage prevent dispatch.')
    previous = max(prior, key=lambda item: item['attempt']) if prior else None
    predecessor = None
    if previous:
        disposition = previous.get('result_disposition', {})
        predecessor = {'attempt_id': previous['grant_id'], 'binding': ledger['binding'],
            'terminal': bool(previous.get('terminal_status')) and previous['state'] not in LIVE,
            'disposition': ('incomplete' if disposition.get('status') == 'verified'
                            and any(item['state'] == 'invalidated' for item in ledger['criteria'].values()) else
                            disposition.get('status', 'failed' if previous['state'] in {'failed', 'interrupted'} else 'pending')),
            'outcome_digest': digest(disposition or {'state': previous['state'], 'terminal_status': previous.get('terminal_status'),
                                                     'events': previous.get('events')})}
    candidates = results.schedule_residuals(ledger, predecessor=predecessor, retry_plan=request.get('retry_plan'),
        authorization={'authorized': True, 'quiescent': not any(r['state'] in LIVE for r in prior),
                       'denied': bool(previous and is_launch_denial(previous) and previous.get('identity_conflict')),
                       'cancelled': bool(state.get('cancellation')), 'paused': bool(state.get('dispatch_paused')),
                       'conflicting': any(r.get('identity_conflict') for r in prior)},
        budget={'dispatch_admitted': True}, fresh_identity=request.get('worker_id') is None)
    w.require(candidates, 'worker_result_incomplete', 'No authorized residual work: reconcile the exact predecessor and retry plan.')
    stamp = int(datetime.now(timezone.utc).timestamp() * 1000)
    for budget in budgets:
        if not budget['authorization_started']:
            budget.update(authorization_started=True, first_authorized_ms=stamp)
        budget['reservations'][row['grant_id']] = {'attempt_id': row['grant_id'], 'tokens': proposed,
            'state': 'reserved', 'issued_call': None, 'history': [{'state': 'reserved', 'now_ms': stamp}]}
    row.update(result_ledger=ledger_id, result_binding=deepcopy(ledger['binding']),
               budget_scopes=list(ledger['budget_scopes']), tokens_proposed=proposed,
               residual_criteria=candidates, retry_plan=deepcopy(request.get('retry_plan')))


def _issue_result_dispatch(state: dict[str, Any], row: dict[str, Any], call: str, *, continuation: bool = False) -> None:
    from . import worker_results as results
    if not row.get('result_ledger'):
        w.require(results.contract(task(state, row['task_id']), required=False) is None,
                  'completion_contract_required', 'Native attempt lacks its admitted result contract.')
        return
    refresh_result_budgets(Path(state['workspace']), state)
    scopes = row['budget_scopes']
    key = row['grant_id'] + '/' + call if continuation else row['grant_id']
    prepared = {}
    for scope in scopes:
        budget = deepcopy(state['retry_budgets'][scope])
        if continuation:
            decision = results.budget_admission(budget, tokens_proposed=row['tokens_proposed'])
            if not decision['dispatch_admitted']:
                raise BudgetRefusal('retry_budget_exhausted', 'Continuation exceeds the same frozen retry limits.')
            budget['reservations'][key] = {'attempt_id': row['grant_id'], 'tokens': row['tokens_proposed'],
                'state': 'reserved', 'issued_call': None, 'history': [{'state': 'reserved', 'call_id': call}]}
        if not results._issue_ready(budget, key):
            raise BudgetRefusal('retry_budget_exhausted', 'Retry budget changed before native issue.')
        prepared[scope] = results.reservation_transition(budget, key, state='issued',
            proof={'source': 'native pre-call/' + call, 'call_id': call})
    state['retry_budgets'].update(prepared)


def prepare(workspace: Path, state: dict[str, Any], task_id: str, request: dict[str, Any], *,
            host: str | None = None) -> dict[str, Any]:
    from .workflow_write_guard import require_runtime
    require_runtime(state)
    w.require(not state.get('dispatch_paused'), 'approval_required',
              'New worker dispatch is paused for requirements review.')
    from . import workspace_binding
    workspace_binding.ensure(workspace, worker=True, expected=state.get("workspace_contract"))
    definition = task(state, task_id)
    stage = w.current(state)
    w.require(definition.get("execution") != "root", "invalid_evidence",
              "Root execution tasks cannot receive a native grant.")
    w.require(stage["decision"] in {"not_requested", "changes_requested", "rejected"}
              and definition.get("phase", stage["phase"]) == stage["phase"],
              "approval_required", "Worker needs an unsealed current phase task.")
    w.require(all(result_valid(workspace, state, d) for d in definition["dependencies"]),
              "scope_violation", "Task prerequisites need verified accepted results.")
    paths = definition["paths"]
    w.require(paths and set(paths) <= set(state["scope"]["paths"][stage["phase"]]),
              "scope_violation", "Worker task exceeds accepted phase paths.")
    for p in paths:
        e.path(workspace, p)
    rows = records(state)
    previous = [r for r in rows.values() if r["task_id"] == task_id]
    retry_reason = request.get("retry_reason")
    if previous and ("read_inputs" in definition or any(is_launch_denial(r) for r in previous)):
        w.require(isinstance(retry_reason, str) and 0 < len(retry_reason.strip()) <= 512,
                  "invalid_evidence", "A repeated scoped task needs a concrete retry_reason before another attempt.")
    live = [r for r in rows.values() if r["state"] in LIVE]
    limit = capacity(request.get("capacity"), host=host)
    w.require(len(live) < limit, "worker_capacity", "No available native worker slot; pending and unknown attempts count.")
    w.require(not any(set(paths) & set(r["paths"]) or r["task_id"] == task_id for r in live),
              "scope_violation", "Task has an active attempt or conflicting write ownership.")
    w.require(len(rows) < 1024, "state_unavailable", "Worker attempt limit reached.")
    name = request.get("task_name", task_id.lower().replace("-", "_"))
    w.require(isinstance(name, str) and re.fullmatch(r"[a-z0-9_]{1,46}", name),
              "invalid_evidence", "Native task name prefix must use 1-46 lowercase letters, digits or underscores.")
    key = uuid.uuid4().hex
    # The desktop host can encrypt message text before hooks see it. Carry the
    # same correlation token in the native name, without decrypting the message.
    name = f"{name}__{key}"
    w.require(not any(r["task_name"] == name for r in live), "scope_violation", "Active native task names must be distinct.")
    session = Brief(workspace, state, task_id)
    from .host_capabilities import runtime_identity
    # Preserve immutable content in the bounded object store, never the control DB.
    frozen = session.store.put("worker-input-snapshot", session.frozen)
    row = {"grant_id": key, "root": state["root"], "run": state["run"], "binding": binding(state),
           "workspace": str(workspace),
           "task_id": task_id, "task_digest": digest(definition), "task_generation": state.get("task_generation", 0),
           "paths": paths, "criteria": e.task_criteria(definition), "task_name": name,
           "attempt": 1 + max((r["attempt"] for r in rows.values() if r["task_id"] == task_id), default=0),
           "state": "prepared", "worker_id": request.get("worker_id"), "call_id": None,
           "snapshot": frozen, "input_manifest": read_input_manifest(workspace, session, paths),
           "dependency_results": dependency_results(state, definition["dependencies"]),
           "dependency_manifest": dependency_manifest(state, definition["dependencies"]),
           "prepared_at": now(), "events": {},
           "purpose": definition.get("purpose") or definition.get("review_lens") or task_id,
           "retry_reason": retry_reason,
           "expected_runtime": runtime_identity(),
           "requirements_epochs": {c: state.get('criterion_epochs', {}).get(c, 0)
                                   for c in e.task_criteria(definition)},
           "task_epoch": state.get('task_epochs', {}).get(task_id, 0),
           "workspace_contract": deepcopy(state.get("workspace_contract"))}
    if host == "claude" and state.get("claude_transcript_selection", {}).get("status") == "selected":
        # Claude writes tool_use before PreToolUse. Freeze the root hook's
        # verified boundary at preparation, before the grant can be dispatched.
        row["transcript_source"] = deepcopy(state.get("claude_transcript_source"))
        row["transcript_admission_offset"] = state.get("claude_transcript_watermark")
    w.require(not any(path_conflict(row, other) for other in live), "scope_violation",
              "Join workers before changing their read inputs or reading active writer outputs.")
    if row["worker_id"]:
        prior = [r for r in rows.values() if r.get("worker_id") == row["worker_id"]]
        w.require(prior and all(r["state"] not in LIVE for r in prior),
                  "scope_violation", "Follow-up needs a known joined native worker.")
        row['canonical_name'] = max(prior, key=lambda r: r['prepared_at']).get('canonical_name')
    _reserve_result_dispatch(workspace, state, row, request)
    rows[key] = row
    state["worker_capacity"] = {**request["capacity"], "effective_limit": limit, "observed_at": now(),
                                "binding": {"run": state["run"], "visit": w.current(state)["id"]}}
    state["worker_sequence"] = state.get("worker_sequence", 0) + 1
    return row






def dispatch_message(state: dict[str, Any], row: dict[str, Any]) -> str:
    return (f"Taskplane grant: {row['grant_id']}\n"
            f"Workspace: {state['workspace']}\nRun: {state['run']}\nTask: {row['task_id']}\n"
            f"Read the task brief using flow context --run {state['run']} --task {row['task_id']}.\n"
            "Stay within the assigned paths and return evidence. "
            "The grant is a correlation value, not approval or root authority.")


def find(state: dict[str, Any], worker: str) -> dict[str, Any] | None:
    found = [r for r in state.get("workers", {}).values()
             if worker and worker in {r.get("worker_id"), r.get("canonical_name")}]
    return max(found, key=lambda r: r["prepared_at"]) if found else None


def worker_session(workspace: Path, state: dict[str, Any], row: dict[str, Any]) -> Brief:
    current(state, row)
    w.require(row.get("worker_id"), "scope_violation", "Native worker identity is not bound.")
    consumer = {k: row[k] for k in ("worker_id", "grant_id", "attempt", "task_id", "task_generation")}
    frozen = Store(workspace).resolve(row["snapshot"])
    session = Brief(workspace, state, row["task_id"], consumer=consumer, persist=False)
    old = frozen.get("brief", {})
    owned = set(row["paths"])
    # Owned source references remain useful in the brief, but authorized output
    # writes cannot invalidate their own attempt. Freeze only read dependencies.
    def dependencies(inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [item for item in inputs if item.get("path") not in owned]
    w.require(frozen.get("binding") == session.binding and frozen.get("task") == row["task_id"]
              and old.get("task") == session.body["task"]
              and dependencies(old.get("inputs", [])) == dependencies(session.body["inputs"])
              and old.get("prerequisite_results") == session.body["prerequisite_results"],
              "invalid_context", "Relevant task read dependencies or authority changed since preparation.")
    session.reference = session.store.put("work-brief", session.body)
    session.store.register(session.binding, [session.reference])
    return session


def admit_discovery(args: dict[str, Any]) -> None:
    """Only select supported Claude recovery tools; selection is no grant."""
    query = args.get('query')
    w.require(set(args) <= {'query', 'max_results'} and isinstance(query, str)
              and re.fullmatch(r'select:(?:SendMessage|TaskStop|ListAgents)(?:,(?:SendMessage|TaskStop|ListAgents)){0,2}', query),
              'scope_violation', 'Claude discovery supports only exact SendMessage/TaskStop/ListAgents selection.')
    assert isinstance(query, str)
    names = query.removeprefix('select:').split(',')
    w.require(len(set(names)) == len(names) and ('max_results' not in args
              or type(args['max_results']) is int and len(names) <= args['max_results'] <= 3),
              'scope_violation', 'Claude discovery must use a bounded result count without duplicate tools.')


def _claude_control_target(tool: str, args: dict[str, Any]) -> str:
    """The local Claude 2.1.289 plain-message and exact task-stop contracts."""
    target = args.get('to' if tool == 'SendMessage' else 'task_id')
    w.require(isinstance(target, str) and 0 < len(target) <= 200
              and not any(char.isspace() for char in target),
              'scope_violation', 'Claude control requires one exact native worker identity.')
    assert isinstance(target, str)
    if tool == 'TaskStop':
        w.require(set(args) == {'task_id'}, 'scope_violation',
                  'Claude worker stop supports only task_id; shell and remote targets are unsupported.')
    else:
        fields = {'to', 'message', 'summary'}
        # Claude backfills these display-only fields before invoking hooks.
        # They never select the recipient or replace the canonical message.
        display = {'type', 'recipient', 'recipient_kind', 'content'} & set(args)
        message = args.get('message')
        w.require(set(args) <= fields | display and isinstance(message, str)
                  and bool(message.strip()) and len(message.encode('utf-8')) <= 32768
                  and ('summary' not in args or isinstance(args['summary'], str)
                       and len(args['summary']) <= 200),
                  'scope_violation', 'Claude worker messages require bounded plain text; other message operations are unsupported.')
        assert isinstance(message, str)
        if display:
            content = args.get('content')
            # Claude 2.1.293/294 adds recipient_kind in backfillObservableInput;
            # it is absent from the model-facing schema and is never authority.
            w.require(display in ({'type', 'recipient', 'content'},
                                  {'type', 'recipient', 'recipient_kind', 'content'})
                      and args.get('recipient_kind', 'agent') == 'agent' and args['type'] == 'message'
                      and args['recipient'] == target and isinstance(content, str)
                      and 0 < len(content.encode('utf-8')) <= 32768
                      and (content == message or content.endswith('…')
                           and bool(content[:-1]) and message.startswith(content[:-1])),
                      'scope_violation', 'Claude message display fields disagree with the canonical input.')
    return target


def admit_claude_control(state: dict[str, Any], event: dict[str, Any]) -> None:
    tool, args = event['tool_name'], event['tool_input']
    target = _claude_control_target(tool, args)
    row = find(state, target)
    w.require(row is not None and row.get('worker_id') == target and row.get('host') == 'claude'
              and row.get('root') == state['root'] and row.get('run') == state['run']
              and sum(item.get('worker_id') == target and item['state'] in LIVE
                      for item in records(state).values()) == 1,
              'scope_violation', 'Claude control requires an owned native worker in this run.')
    assert row is not None
    current(state, row)
    if tool != 'TaskStop':
        require_execution(row)
    observed = event.get('taskplane_observed_binding', {})
    call = event.get('tool_use_id') or event.get('call_id')
    w.require(row['state'] in ({'bootstrapping', 'running', 'cancel_requested'} if tool == 'TaskStop'
                              else {'bootstrapping', 'running'})
              and not row.get('identity_conflict') and not row.get('completion_conflict')
              and row.get('identity_observation', {}).get('status') == 'matched'
              and row.get('identity_freshness', {}).get('status') == 'matched'
              and event.get('taskplane_automatic_hook') is True
              and observed.get('root') == observed.get('principal') == state['root']
              and bool(row.get('expected_runtime'))
              and event.get('taskplane_runtime_identity') == row.get('expected_runtime')
              and isinstance(call, str) and 0 < len(call) <= 512,
              'scope_violation', 'Claude control requires the current live attempt and exact automatic root/runtime proof.')
    controls = state.setdefault('worker_controls', {})
    proof = {'tool': tool, 'input_digest': digest(args), 'grant': row['grant_id'],
             'attempt': row['attempt'], 'binding': binding(state)}
    w.require(call not in controls or controls[call] == proof, 'scope_violation',
              'Claude control call belongs to another attempt or input.')
    w.require(call in controls or len(controls) < 4096, 'state_unavailable',
              'Claude worker control observation limit reached.')
    if tool == 'SendMessage' and call not in controls:
        _issue_result_dispatch(state, row, cast(str, call), continuation=True)
    controls[call] = proof
    if tool == 'TaskStop':
        row['state'] = 'cancel_requested'  # A stop request or its tool response never joins work.


def admit(state: dict[str, Any], event: dict[str, Any]) -> bool:
    """Recognize only explicit native schemas. Called in the same locked commit as guard."""
    tool, args = event.get("tool_name") or event.get("tool"), event.get("tool_input", {})
    if tool not in TOOLS:
        return False
    w.require(isinstance(args, dict), "scope_violation", "Invalid native worker arguments.")
    if tool in DISCOVERY:
        admit_discovery(args)
        return True
    if tool in CLAUDE_STATUS:
        from .host_capabilities import runtime_identity
        # Claude 2.1.290's observed empty inventory call. Its output is display
        # only: never route these labels through Codex terminal-poll inference.
        observed = event.get('taskplane_observed_binding', {})
        call = event.get('tool_use_id') or event.get('call_id')
        w.require(not args and event.get('host') == 'claude'
                  and event.get('taskplane_automatic_hook') is True
                  and observed.get('profile') == 'native_workflow'
                  and observed.get('root') == observed.get('principal') == state['root']
                  and event.get('taskplane_runtime_identity') == runtime_identity()
                  and isinstance(call, str) and 0 < len(call) <= 512,
                  'scope_violation', 'Claude inventory requires empty input and exact automatic root/runtime proof.')
        return True
    if tool in STATUS | WAIT:
        w.require(set(args) <= ({"path_prefix"} if tool in STATUS else {"timeout_ms"}),
                  "scope_violation", "Unsupported worker status schema.")
        if tool in STATUS:
            call = event.get("tool_use_id") or event.get("call_id")
            if isinstance(call, str) and 0 < len(call) <= 512:
                polls = state.setdefault("worker_polls", {})
                w.require(call in polls or len(polls) < 4096, "state_unavailable", "Worker status observation limit reached.")
                # A delayed result must still address the attempts that existed
                # when this native status call was admitted.
                polls.setdefault(call, [r["grant_id"] for r in records(state).values() if r.get("call_id")])
        return True
    if tool not in INTERRUPT | {'TaskStop'}:
        w.require(not state.get('invalidation_pending') and w.current(state)['decision'] in
                  {'not_requested', 'changes_requested', 'rejected'},
                  'stale_checkpoint', 'Native input requires a current unsealed phase.')
    if tool in CLAUDE_CONTROL:
        admit_claude_control(state, {**event, 'tool_name': tool, 'tool_input': args})
        return True
    if tool in SPAWN | FOLLOW:
        w.require(not state.get('dispatch_paused'), 'approval_required',
                  'New native dispatch is paused for requirements review.')
        claude = tool in {"Agent", "Task"}
        fields = ({"prompt", "description", "subagent_type", "model", "run_in_background", "resume"} if claude else
                  {"target", "message"} if tool in FOLLOW else
                  {"task_name", "message", "fork_turns", "model", "reasoning_effort"})
        message = args.get("prompt" if claude else "message")
        w.require(not set(args) - fields and isinstance(message, str) and 0 < len(message) <= 32768,
                  "scope_violation", "Unsupported native worker dispatch schema.")
        matches = MARKER.findall(message)
        if tool in SPAWN and not claude:
            named = NAME_GRANT.fullmatch(str(args.get("task_name", "")))
            if named:
                w.require(not matches or matches == [named[1]], "scope_violation", "Dispatch grant name and message conflict.")
                matches = [named[1]]
        w.require(len(matches) == 1 and matches[0] in records(state), "scope_violation", "Dispatch requires one prepared grant.")
        row = records(state)[matches[0]]
        current(state, row)
        require_execution(row)
        workspace = Path(state['workspace'])
        definition = task(state, row['task_id'])
        w.require(all(result_valid(workspace, state, d) for d in definition['dependencies'])
                  and row.get('dependency_results') == dependency_results(state, definition['dependencies'])
                  and row['input_manifest'] == e.manifest(workspace, list(row['input_manifest'])),
                  'stale_checkpoint', 'Prepared worker inputs or prerequisites changed before launch.')
        w.require(not any(path_conflict(row, other) for other in records(state).values()
                          if other['grant_id'] != row['grant_id'] and other['state'] in LIVE),
                  'scope_violation', 'Native launch conflicts with active worker read/write ownership.')
        call = event.get("tool_use_id") or event.get("call_id")
        w.require(isinstance(call, str) and 0 < len(call) <= 512, "scope_violation", "Native dispatch call identity is missing.")
        if row["call_id"] == call:
            w.require(row.get("dispatch_digest") == digest(args), "scope_violation", "Conflicting native call replay.")
            return True
        w.require(row["state"] == "prepared" and row["call_id"] is None
                  and not any(r.get("call_id") == call for r in records(state).values()),
                  "scope_violation", "Conflicting or stale native launch.")
        if tool in FOLLOW:
            w.require(row.get("worker_id") and args.get("target") in {row["worker_id"], row.get("canonical_name")},
                      "scope_violation", "Follow-up target does not match its new attempt.")
        elif claude:
            # Current Claude fork mode omits this parameter. Admission never
            # binds a child: the returned structured async launch still must.
            w.require("run_in_background" not in args or args["run_in_background"] is True, "unsupported_authority",
                      "Claude workers require an asynchronous launch with structured child identity proof.")
            from .host_capabilities import runtime_identity
            observed = event.get('taskplane_observed_binding', {})
            w.require(event.get('host') == 'claude' and event.get('hook_event_name') == 'PreToolUse'
                      and event.get('taskplane_automatic_hook') is True
                      and observed.get('profile') == 'native_workflow'
                      and observed.get('root') == observed.get('principal') == state['root']
                      and event.get('session_id') == state['root']
                      and event.get('cwd') == state['workspace']
                      and not event.get('agent_id') and not event.get('parent_session_id')
                      and event.get('taskplane_runtime_identity') == row.get('expected_runtime') == runtime_identity(),
                      'unsupported_authority', 'Claude dispatch requires the current automatic root hook and prepared runtime.')
            w.require(event.get("taskplane_claude_root_source") is True
                      and row.get("transcript_source")
                      and row["transcript_source"] == state.get("claude_transcript_source")
                      and type(row.get("transcript_admission_offset")) is int
                      and type(state.get("claude_transcript_watermark")) is int
                      and 0 <= row["transcript_admission_offset"] <= state["claude_transcript_watermark"],
                      "unsupported_authority", "Claude dispatch requires its prepared and fresh automatic root transcript source.")
            w.require((args.get('resume') == row.get('worker_id') if row.get('worker_id')
                       else not args.get('resume')), 'scope_violation',
                      'Claude resume must target the exact prepared known worker attempt.')
            from . import claude_worker_observations as observations
            preflight = observations.preflight_launch(state['root'], row, event,
                                                       source=row['transcript_source'])
            if preflight.get('unadmitted_result'):
                row.update(state='unknown', preflight_blocker=preflight)
                raise w.Refusal('worker_readiness', 'Native evidence already contains an unadmitted launch result. '
                                'Preserve this unknown attempt and use flow wait for evidence-based recovery. '
                                'It cannot be abandoned, retried or treated as completed; no identity is inferred.')
            w.require(preflight['status'] in {'matched', 'awaiting_transcript'}, 'worker_readiness',
                      'Claude launch preflight ' + preflight['status'] + ': ' + preflight['reason'] + '. '
                      'No launch is admitted; the grant remains prepared. Repair the transcript source '
                      'or conflicting exposed-schema input before retrying. If unavailable, abandon the unlaunched reservation '
                      'and use flow wait for capability repair; required work and approvals are preserved.')
            row['launch_preflight'] = preflight
        else:
            w.require(args.get("task_name") == row["task_name"] and args.get("fork_turns") == "none"
                      and not row.get("worker_id"), "scope_violation", "Spawn needs the prepared name and a task-focused handoff.")
        w.require(sum(r["state"] in LIVE for r in records(state).values()) <=
                  state["worker_capacity"]["effective_limit"], "worker_capacity", "Observed capacity reduced before launch.")
        _issue_result_dispatch(state, row, cast(str, call))
        row.update(call_id=call, dispatch_digest=digest(args), state="launch_pending", launch_requested_at=now(), host="claude" if claude else "codex")
        return True
    w.require(set(args) <= ({"target", "message"} if tool in MESSAGE else {"target"})
              and isinstance(args.get("target"), str), "scope_violation", "Unsupported native worker control schema.")
    row = find(state, args["target"])
    w.require(row is not None, "scope_violation", "Unknown native worker target.")
    assert row is not None
    if tool in INTERRUPT:
        if row["state"] in LIVE:
            row["state"] = "cancel_requested"
        return True  # Cancellation is not a terminal observation.
    current(state, row)
    require_execution(row)
    w.require(row["state"] in {"bootstrapping", "running"}, "scope_violation", "New input requires a live current attempt.")
    from .worker_results import contract
    if tool in MESSAGE and (row.get('result_ledger') or contract(task(state, row['task_id']), required=False) is not None):
        call = event.get('tool_use_id') or event.get('call_id')
        w.require(isinstance(call, str) and bool(call), 'scope_violation', 'Continuation needs an exact native call.')
        seen = row.setdefault('continuation_calls', {})
        w.require(call not in seen or seen[call] == digest(args), 'scope_violation', 'Conflicting continuation replay.')
        if call not in seen:
            _issue_result_dispatch(state, row, cast(str, call), continuation=True)
            seen[call] = digest(args)
    return True


def bind_worker(state: dict[str, Any], row: dict[str, Any], identity: str, name: str | None = None) -> bool:
    w.require(isinstance(identity, str) and identity and identity != state["root"] and len(identity) <= 200,
              "scope_violation", "Invalid native worker identity.")
    if row.get('revoked_at'):
        _audit(row, 'late_identity', {'worker_id': identity})
        if is_launch_denial(row):
            _denial_unknown(row, 'conflict', 'Native child identity arrived after denial')
        return False
    if row.get('binding') != binding(state):
        _audit(row, 'historical_identity', {'worker_id': identity})
        return False
    if row.get("worker_id") not in (None, identity):
        row["state"] = "unknown"
        return False
    if any(r is not row and r.get("worker_id") == identity and r["state"] in LIVE for r in records(state).values()):
        row["state"] = "unknown"
        return False
    if row.get('result_ledger') and any(other is not row and other['task_id'] == row['task_id']
            and other.get('worker_id') == identity for other in records(state).values()):
        row.update(state='unknown', identity_conflict=True)
        _audit(row, 'identity_conflict', {'worker_id': identity, 'reason': 'Residual retry requires a fresh native identity.'})
        return False
    row["worker_id"] = identity
    if isinstance(name, str) and name:
        row["canonical_name"] = name[:200]
    if row["state"] == "launch_pending":
        row.update(state="bootstrapping", identity_bound_at=now())
    return True


def terminal(row: dict[str, Any], status: str, event_id: str) -> None:
    status = "completed" if status == "idle" else status
    if status not in {'completed', 'failed', 'interrupted'}:
        _audit(row, 'unsupported_terminal', {'status': status, 'event_id': event_id})
        return
    if row.get('revoked_at'):
        _audit(row, 'late_terminal', {'status': status, 'event_id': event_id})
        if is_launch_denial(row):
            _denial_unknown(row, 'conflict', 'Native terminal evidence arrived after denial')
        return
    previous = row["events"].get(event_id)
    if previous == status:
        return
    if previous is not None or row.get("terminal_status") not in (None, status):
        _audit(row, 'terminal_conflict', {'status': status, 'event_id': event_id})
        if row['state'] == 'accepted':
            # Terminal delivery cannot overwrite the independent acceptance
            # disposition. A contradictory observation explicitly invalidates
            # its current applicability while retaining the accepted record.
            row['acceptance_invalidation'] = {'reason': 'contradictory_native_terminal',
                'event_id': event_id, 'observed_status': status,
                'accepted_terminal_status': row.get('terminal_status')}
        else:
            row["state"] = "unknown"
        return
    if row.get("terminal_status") == status:
        return
    row["events"][event_id] = status
    row.update(terminal_status=status, ended_at=now(), state=(
        "result_pending" if status in {"completed", "idle"} else "interrupted" if status == "interrupted" else "failed"))


def _audit(row: dict[str, Any], kind: str, value: dict[str, Any]) -> None:
    events = row.setdefault('reconciliation_events', {})
    key = digest({'kind': kind, 'value': value})
    if key not in events and len(events) < 128:
        events[key] = {'kind': kind, 'value': deepcopy(value), 'observed_at': now()}
    elif key not in events:
        row['reconciliation_overflow'] = True


def is_launch_denial(row: dict[str, Any]) -> bool:
    return row.get('terminal_status') == 'launch_denied' or bool(row.get('denial_proof_ref'))


def denial_has_work(state: dict[str, Any], row: dict[str, Any]) -> bool:
    """A rejection must never acquire child, task-result or lifecycle evidence."""
    return (any(row.get(key) for key in ('worker_id', 'claimed_at', 'started_at', 'native_session_started_at',
        'identity_bound_at', 'identity_observation', 'context_receipt', 'context_delivery',
        'result', 'result_ref', 'events', 'launch_proof_ref', 'launch_proof_refs',
        'handback', 'stop_observation', 'stop_observations', 'startup_handbacks', 'completion_conflict',
        'denial_lifecycle_observation'))
        or any(result.get('grant') == row['grant_id'] for result in state.get('task_results', {}).values())
        or any(event.get('parent') == row['root'] and event.get('call_id') == row.get('call_id')
               for event in state.get('unbound_worker_events', {}).values()))


def _denial_unknown(row: dict[str, Any], status: str, reason: str) -> None:
    row['state'] = 'unknown'
    if status == 'conflict':
        row['identity_conflict'] = True
    value = {'status': status, 'reason': reason}
    row['denial_freshness'] = value
    _audit(row, 'denial_revalidation', value)


def record_launch_denial(state: dict[str, Any], row: dict[str, Any], observation: dict[str, Any], *,
                         persist_proof: bool = True) -> bool:
    """Commit only secure reader output; this is failure evidence, never authority."""
    from . import claude_worker_observations as claude
    proof = observation.get('denial_proof')
    historical = is_launch_denial(row)
    try:
        if not isinstance(proof, dict):
            raise ValueError('Missing secure denial proof')
        expected = claude._binding(state['root'], row)
        exact = (row.get('host') == 'claude' and row.get('workspace') == state['workspace']
            and row.get('run') == state['run'] and row.get('root') == state['root']
            and (historical or row.get('binding') == binding(state) and not row.get('revoked_at')
                 and row['state'] == 'launch_pending')
            and not row.get('identity_conflict') and not denial_has_work(state, row)
            and row.get('terminal_status') in (None, 'launch_denied')
            and claude._valid_seal(proof, 'taskplane.claude-launch-denial-proof/v1')
            and proof['binding'] == expected and proof['source'] == row.get('transcript_source')
            and proof['source'] == state.get('claude_transcript_source')
            and proof['source_sha256'] == proof['source']['sha256']
            and proof['admission_timestamps'] == {key: row[key] for key in
                ('launch_requested_at', 'admitted_at') if row.get(key) is not None}
            and set(proof['records']) == {'call', 'result'}
            and all(len(proof['records'][role]) == 1 for role in ('call', 'result'))
            and type(proof.get('watermark')) is int and proof['watermark'] >=
                proof['records']['result'][0]['reference']['offset'] + proof['records']['result'][0]['reference']['bytes']
            and observation.get('status') == 'launch_denied' and observation.get('host') == 'claude'
            and observation.get('schema') == 'taskplane.worker-identity-observation/v1'
            and observation.get('assurance') == 'observed'
            and not any(observation.get(key) for key in ('worker_id', 'started_at', 'proof', 'completion', 'peer_handback'))
            and observation.get('denial_kind') == 'automode-blocked'
            and observation.get('denial_code') == 'auto-mode-bypass'
            and observation.get('called_at') == proof['records']['call'][0]['projection']['timestamp']
            and observation.get('denied_at') == observation.get('observed_at') ==
                proof['records']['result'][0]['projection']['timestamp']
            and all(observation.get(key) == expected[key] for key in
                ('parent', 'workspace', 'call_id', 'grant_id', 'attempt', 'dispatch_digest'))
            and observation.get('record_sha256') == {role: proof['records'][role][0]['projection']['record_sha256']
                                                    for role in ('call', 'result')}
            and observation.get('evidence_sha256') == digest(observation['record_sha256']))
        store = Store(Path(state['workspace']))
        if exact and historical:
            old = store.resolve(row['denial_proof_ref'])
            latest = store.resolve(row['denial_latest_proof_ref'])
            exact = (row.get('terminal_status') == 'launch_denied' and bool(row.get('revoked_at'))
                     and row['state'] in {'failed', 'unknown'}
                     and row.get('ended_at') == old['records']['result'][0]['projection']['timestamp'])
            for retained in (old, latest):
                exact = exact and claude._valid_seal(retained, 'taskplane.claude-launch-denial-proof/v1') and all(
                    retained[key] == proof[key] for key in
                    ('binding', 'source', 'source_sha256', 'admission_timestamps', 'records'))
                exact = exact and type(retained['watermark']) is int and retained['watermark'] <= proof['watermark']
        if not exact:
            raise ValueError('Denial proof or no-child binding disagrees')
        reference = (store.put if persist_proof else store.reference)('claude-launch-denial-proof', proof)
        row.setdefault('denial_proof_ref', reference)
        row['denial_latest_proof_ref'] = reference
        row.setdefault('denial_observation', {key: deepcopy(value) for key, value in observation.items()
            if key not in {'cursor', 'budget', 'denial_proof'}})
        row.setdefault('revoked_at', now())
        row.setdefault('ended_at', observation['denied_at'])
        row.update(state='failed', terminal_status='launch_denied',
                   identity_freshness={'status': 'launch_denied', 'reason': observation.get('reason')},
                   denial_freshness={'status': 'launch_denied', 'proof_ref': reference,
                                     'watermark': proof['watermark']})
        _audit(row, 'launch_denied', {'proof_ref': row['denial_proof_ref']})
        return True
    except (w.Refusal, KeyError, TypeError, ValueError, IndexError):
        _denial_unknown(row, 'conflict', 'Denial proof or no-child binding disagrees')
        return False


def refresh_launch_denials(state: dict[str, Any], event: dict[str, Any] | None = None, *, budget: Any = None) -> None:
    """Caller holds the controller lock and persists progress before any refusal."""
    attempts = [row for row in records(state).values() if is_launch_denial(row)]
    observe_claude_launches(state, attempts, event or {}, budget=budget)


def launch_denials_verified(state: dict[str, Any]) -> bool:
    """Read-only quiescence: reobserve a copy without storing new proof objects."""
    from . import claude_worker_observations as claude
    if not any(is_launch_denial(row) for row in state.get('workers', {}).values()):
        return True
    candidate = deepcopy(state)
    attempts = [row for row in records(candidate).values() if is_launch_denial(row)]
    if len(attempts) > claude.MAX_CANDIDATES:
        return False
    result = claude.observe_many(candidate['root'], attempts, {}, source=candidate.get('claude_transcript_source'),
                                cursor=candidate.get('claude_transcript_cursor'))
    return all(observation['status'] == 'launch_denied'
               and record_launch_denial(candidate, row, observation, persist_proof=False)
               for row, observation in zip(attempts, result['observations']))


def reconcile(workspace: Path, state: dict[str, Any], row: dict[str, Any],
              observation: dict[str, Any]) -> dict[str, Any]:
    """Apply a parser observation under Controller lock, never asserted CLI facts.

    Old/revoked attempts retain diagnostic evidence without regaining execution.
    Completion is only pending evidence; claim/context/result validation still run.
    """
    grant_id = row.get('grant_id')
    w.require(isinstance(grant_id, str) and records(state).get(grant_id) is row
              and str(workspace) == state.get('workspace')
              and row.get('root') == state.get('root') and row.get('run') == state.get('run'),
              'scope_violation', 'Worker reconciliation belongs to another attempt or workspace.')
    status = observation.get('status')
    w.require(status in {'matched', 'launch_denied', 'not_yet_available', 'unsupported', 'conflict'},
              'invalid_evidence', 'Unsupported worker identity observation.')
    if status == 'launch_denied':
        accepted = record_launch_denial(state, row, observation)
        return {'status': 'launch_denied' if accepted else 'conflict', 'state': row['state']}
    if is_launch_denial(row):
        _denial_unknown(row, status if status in {'not_yet_available', 'unsupported'} else 'conflict',
                        observation.get('reason', 'Native evidence contradicts denial'))
        return {'status': status, 'state': row['state']}
    # Parsers return only compact metadata. Never persist transcript/prompt bytes.
    fields = ('status', 'reason', 'host', 'parent', 'workspace', 'call_id', 'worker_id',
              'grant_id', 'attempt', 'dispatch_digest', 'evidence_sha256', 'record_sha256',
              'references', 'called_at', 'started_at', 'observed_at', 'canonical_name')
    proof = {key: deepcopy(observation[key]) for key in fields if key in observation}
    _audit(row, 'identity_observation', proof)
    if status in {'not_yet_available', 'unsupported'}:
        return {'status': status, 'state': row['state']}
    exact = (observation.get('parent') == state['root']
             and observation.get('host') == row.get('host')
             and observation.get('workspace') == state['workspace']
             and observation.get('call_id') == row.get('call_id') and bool(row.get('call_id'))
             and observation.get('grant_id') == row['grant_id']
             and observation.get('attempt') == row['attempt']
             and observation.get('dispatch_digest') == row.get('dispatch_digest')
             and isinstance(observation.get('evidence_sha256'), str)
             and bool(re.fullmatch(r'[0-9a-f]{64}', observation['evidence_sha256'])))
    prior = row.get('identity_observation', {})
    if prior and prior.get('evidence_sha256') != observation.get('evidence_sha256'):
        exact = False
    if status == 'conflict' or not exact:
        row['identity_conflict'] = True
        if not row.get('revoked_at') and row['state'] not in {'accepted', 'failed', 'interrupted'}:
            row['state'] = 'unknown'
        return {'status': 'conflict', 'state': row['state']}
    if row.get('revoked_at') or row.get('binding') != binding(state):
        return {'status': 'historical', 'state': row['state']}
    if row.get('identity_conflict'):
        return {'status': 'conflict', 'state': row['state']}
    identity = observation.get('worker_id')
    w.require(isinstance(identity, str), 'scope_violation', 'Invalid native worker identity.')
    assert isinstance(identity, str)
    if not bind_worker(state, row, identity, observation.get('canonical_name')):
        return {'status': 'conflict', 'state': row['state']}
    if row['state'] == 'unknown' and not row.get('terminal_status') and not row.get('completion_conflict'):
        row.update(state='bootstrapping', identity_bound_at=now())
    row['identity_observation'] = proof
    # Call-less lifecycle observations are joined only after this exact launch
    # proof. Reused native IDs always require an attempt-correlated call ID.
    stops = {}
    for event_id, event in state.get('unbound_worker_events', {}).items():
        if event.get('worker_id') != row['worker_id'] or event.get('parent') != state['root']:
            continue
        if event.get('call_id') and event['call_id'] != row['call_id']:
            continue
        if not event.get('call_id') and sum(r.get('worker_id') == row['worker_id'] for r in records(state).values()) != 1:
            continue
        if event.get('observed_at', '') < row.get('launch_requested_at', row['prepared_at']):
            continue
        if event['event'] == 'SubagentStop':
            if event['status'] == 'stopped':
                source = row.get('transcript_source', {})
                expected_child = str(Path(source.get('path', '')).with_suffix('') / 'subagents' /
                                     ('agent-' + row['worker_id'] + '.jsonl'))
                if (event.get('transcript_path') == source.get('path')
                        and event.get('agent_transcript_path') == expected_child
                        and (not event.get('textless') or event.get('runtime') == row.get('expected_runtime'))):
                    stops[event_id] = {**event, 'event_id': event_id}
            else:
                terminal(row, event['status'], event_id)
        elif row['state'] in {'bootstrapping', 'running'}:
            row.setdefault('started_at', event['observed_at'])
    if stops:
        # The interactive host can stop repeatedly while requiring a handback.
        # Keep the most recent bounded sequence; different interim bodies are
        # not conflicting final results. The native notification pins a stop.
        row['stop_observations'] = sorted(stops.values(), key=lambda stop:
            (stop['observed_at'], stop['event_id']))[-32:]
        if not row.get('handback', {}).get('notification'):
            row['stop_observation'] = row['stop_observations'][-1]
    return {'status': 'conflict' if row.get('completion_conflict') else 'matched',
            'state': row['state'], 'worker_id': row['worker_id']}


def _handback_reports(row: dict[str, Any]) -> list[dict[str, Any]]:
    report = row.get('handback_call', row.get('handback', {}))
    return ([report] if report.get('envelope') else []) + list(row.get('startup_handbacks', {}).values())


def _redirect_delivery(state: dict[str, Any], row: dict[str, Any], completed: dict[str, Any],
                       peer: dict[str, Any] | None, stopped: dict[str, Any]) -> dict[str, Any] | None:
    """A redirect is not a report. Join the separate native peer and admitted ack."""
    if peer is None:
        return None
    textless = (stopped.get('textless') is True
        and stopped.get('agent_type') == peer.get('agent_type')
        and stopped.get('runtime') == row.get('expected_runtime'))
    # Claude 2.1.296 can stop ordinary final text before forcing handback. The
    # later native redirect, exact admission and acknowledgment prove delivery;
    # the interim text is never substituted for the report or a textless stop.
    source = row.get('transcript_source', {})
    pre_handback = (peer.get('host_version') == completed.get('host_version') == '2.1.296'
        and 'textless' not in stopped and 'runtime' not in stopped and 'agent_type' not in stopped
        and stopped.get('event') == 'SubagentStop' and stopped.get('status') == 'stopped'
        and stopped.get('parent') == state['root'] and stopped.get('worker_id') == row['worker_id']
        and stopped.get('call_id') in (None, row['call_id'])
        and stopped.get('transcript_path') == source.get('path')
        and stopped.get('agent_transcript_path') == str(Path(source.get('path', '')).with_suffix('')
            / 'subagents' / ('agent-' + row['worker_id'] + '.jsonl'))
        and all(isinstance(stopped.get(key), str) and re.fullmatch(r'[a-f0-9]{64}', stopped[key])
                for key in ('body_digest', 'notification_body_digest', 'event_id')))
    if not (textless or pre_handback):
        return None
    reports = []
    for report in _handback_reports(row):
        envelope, ack = report.get('envelope', {}), report.get('acknowledgment', {})
        transform = None
        body_digest, input_digest = peer['body_digest'], peer['input_digest']
        if (envelope.get('body_digest'), envelope.get('input_digest')) != (body_digest, input_digest):
            from .claude_worker_observations import SETTINGS_NOTICE
            candidate = peer.get('delivery_transform', {})
            if (isinstance(candidate, dict) and set(candidate) == {
                    'schema', 'host_version', 'notice_digest', 'delivered_body_digest',
                    'original_body_digest', 'original_input_digest'}
                    and candidate['schema'] == 'claude.handback-settings-notice/v1'
                    and candidate['host_version'] == '2.1.296'
                    and candidate['notice_digest'] == digest(SETTINGS_NOTICE)
                    and candidate['delivered_body_digest'] == body_digest):
                body_digest, input_digest = candidate['original_body_digest'], candidate['original_input_digest']
                transform = candidate
        expected = {'parent': state['root'], 'worker': row['worker_id'], 'grant': row['grant_id'],
                    'attempt': row['attempt'], 'task': row['task_id'], 'call_id': envelope.get('call_id'),
                    'binding': binding(state), 'input_digest': input_digest, 'body_digest': body_digest}
        if (envelope != expected or not isinstance(envelope.get('call_id'), str)
                or report.get('status') != 'acknowledged'
                or report.get('runtime') != row.get('expected_runtime')
                or ack != {'schema': 'claude.SubagentHandback.ack/v1', 'envelope_digest': digest(envelope),
                           'runtime': row.get('expected_runtime'), 'response_digest': digest({
                               'message': 'Report delivered to your caller.', 'success': True}),
                           'observed_at': report.get('observed_at')}
                or report.get('response_digest') != ack.get('response_digest')):
            continue
        try:
            def stamp(value: str) -> datetime:
                return datetime.fromisoformat(value.replace('Z', '+00:00'))
            # Worker acknowledgement/stop and parent delivery use different
            # timelines. A completion retains its enqueue time while the peer
            # can flush later; their validated transcript spans order delivery.
            observed_now = datetime.now(timezone.utc)
            sequence = (stamp(report['admitted_at']) <= stamp(ack['observed_at']) <= stamp(stopped['observed_at'])
                        if textless else
                        stamp(row['started_at']) <= stamp(stopped['observed_at']) < stamp(report['admitted_at'])
                        <= stamp(ack['observed_at']) <= stamp(completed['timestamp']))
            if not (sequence and stamp(report['admitted_at']) <= stamp(peer['timestamp']) <= observed_now
                    and stamp(stopped['observed_at']) <= stamp(completed['timestamp']) <= observed_now
                    and peer['reference']['offset'] < completed['reference']['offset']):
                continue
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
        joined = {**report, 'delivery_transform': deepcopy(transform)} if transform else report
        if pre_handback:
            joined = {**joined, 'completion_sequence': 'claude.stop-before-handback/v1'}
        reports.append(joined)
    return reports[0] if len(reports) == 1 else None


def observe_claude_launches(state: dict[str, Any], attempts: list[dict[str, Any]],
                           event: dict[str, Any], *, budget: Any = None) -> list[dict[str, Any]]:
    """One cursor and read budget for the selected root source, under Controller lock."""
    from . import claude_worker_observations as claude
    if not attempts:
        return []
    if len(attempts) > claude.MAX_CANDIDATES:
        # Do not verify a prefix and silently release the omitted candidates.
        for row in attempts:
            observation = {'status': 'unsupported', 'reason': 'Claude attempt observation bound exceeded.'}
            if is_launch_denial(row):
                _denial_unknown(row, observation['status'], observation['reason'])
            else:
                row['state'] = 'unknown'
        return [dict(observation) for _ in attempts]
    result = claude.observe_many(state["root"], attempts, event,
        source=state.get("claude_transcript_source"), cursor=state.get("claude_transcript_cursor"), budget=budget)
    if result["cursor"] is not None:
        state["claude_transcript_cursor"] = result["cursor"]
    state["claude_transcript_budget"] = result["budget"]
    store = Store(Path(state["workspace"]))
    for row, observation in zip(attempts, result["observations"]):
        if observation.get("proof") and not is_launch_denial(row):
            # References stay reachable in attempts and archived workflow state.
            ref = store.put("claude-launch-proof", observation["proof"])
            refs = row.setdefault("launch_proof_refs", [])
            if ref not in refs:
                w.require(len(refs) < 128, "state_unavailable", "Claude launch proof history bound reached.")
                refs.append(ref)
            row["launch_proof_ref"] = ref
        row["identity_freshness"] = {"status": observation["status"], "reason": observation.get("reason"),
                                     "observed_at": now()}
        reconcile(Path(state["workspace"]), state, row, observation)
        completed = observation.get('completion')
        stops = row.get('stop_observations', [row['stop_observation']] if row.get('stop_observation') else [])
        if (observation['status'] == 'matched' and completed and stops
                and not row.get('revoked_at') and row.get('binding') == binding(state)
                and not row.get('identity_conflict')):
            preceding = [stop for stop in stops if
                datetime.fromisoformat(completed['timestamp'].replace('Z', '+00:00')) >=
                datetime.fromisoformat(stop['observed_at'])]
            stopped = preceding[-1] if preceding else None
            old = row.get('handback', {}).get('notification')
            redirected = completed.get('handback_redirect') is True
            report = (_redirect_delivery(state, row, completed, observation.get('peer_handback'), stopped)
                      if redirected and stopped else None)
            if redirected and report is None:
                # Peer transcript, post-hook and stop can become readable on
                # different hooks. Missing proof cannot establish completion.
                if observation.get('peer_handback') and stopped and any(
                        item.get('status') == 'acknowledged' for item in _handback_reports(row)):
                    row['handback_reconciliation'] = {'status': 'blocked',
                        'reason': 'Recorded delivery differs from the admitted report or its exact native acknowledgment/stop proof.',
                        'next_action': 'Preserve the original attempt; reconcile-handback requires matching supported evidence, not another handback.'}
                    _audit(row, 'redirect_join_rejected', {
                        'notification': completed['record_sha256'],
                        'peer': observation['peer_handback']['record_sha256'],
                        'reason': 'Complete redirect proof failed exact admission, acknowledgment, runtime or chronology correlation.'})
                if old is not None:
                    row['completion_conflict'] = True
                    row['state'] = 'unknown'
                    _audit(row, 'completion_conflict', {'notification': completed['record_sha256']})
                continue
            # Older runtimes mistook differing interim stops / the no-report
            # sentinel for body tampering. Reclassify only this exact, freshly
            # revalidated pre-claim failure; successful or identity conflicts
            # cannot be repaired by a failure notification.
            if row.get('completion_conflict'):
                recoverable = (completed.get('no_report') is True and stopped and old is None
                    and row['state'] == 'unknown' and not row.get('terminal_status')
                    and not row.get('claimed_at') and not row.get('context_receipt')
                    and not any(result.get('grant') == row['grant_id']
                                for result in state.get('task_results', {}).values()))
                if not recoverable:
                    continue
                assert stopped is not None
                _audit(row, 'legacy_no_report_conflict_reclassified', {
                    'notification': completed['record_sha256'], 'stop_event': stopped['event_id']})
                row['completion_conflict'] = False
            if (stopped and (old is None or old == completed)
                    and (completed.get('no_report') is True
                         or redirected and report is not None
                         or completed['body_digest'] == stopped.get('notification_body_digest'))):
                unsuccessful = completed.get('no_report') is True or bool(row.get('startup_handbacks'))
                delivery = ({'peer_handback': observation['peer_handback'],
                             'admission': deepcopy(report)} if redirected and report is not None else {})
                if old is not None and any(row['handback'].get(key) != value for key, value in delivery.items()):
                    row['completion_conflict'] = True
                    row['state'] = 'unknown'
                    _audit(row, 'completion_conflict', {'notification': completed['record_sha256']})
                    continue
                row['handback'] = {'schema': 'claude.session-task-notification/v1',
                    'status': 'no_report' if completed.get('no_report') else 'startup_failed' if unsuccessful else 'delivered',
                    'body_digest': report['envelope']['body_digest'] if report else completed['body_digest'],
                    'notification': completed, **delivery,
                    'stop_event': stopped['event_id'],
                    'observed_at': row['handback'].get('observed_at', now()) if old is not None else now()}
                row['handback_reconciliation'] = {'status': 'joined',
                    'delivery_transform': report.get('delivery_transform', {}).get('schema') if report else None,
                    **({'completion_sequence': report['completion_sequence']} if report and report.get('completion_sequence') else {})}
                row['stop_observation'] = stopped
                if report and report.get('completion_sequence'):
                    _audit(row, 'stop_before_handback_joined', {
                        'sequence': report['completion_sequence'], 'stop_event': stopped['event_id'],
                        'notification': completed['record_sha256'],
                        'peer': observation['peer_handback']['record_sha256'],
                        'admission': digest({key: value for key, value in report.items()
                            if key not in {'completion_sequence', 'delivery_transform'}})})
                if unsuccessful:
                    _audit(row, 'unsuccessful_completion', {'notification': completed['record_sha256'],
                        'stop_event': stopped['event_id'], 'status': row['handback']['status']})
                terminal(row, 'failed' if unsuccessful else 'completed', digest(completed))
            else:
                row['completion_conflict'] = True
                row['state'] = 'unknown'
                _audit(row, 'completion_conflict', {'notification': completed['record_sha256']})
    return list(result["observations"])


def admit_handback(state: dict[str, Any], row: dict[str, Any], event: dict[str, Any]) -> None:
    """Historical implicit-parent {message} compatibility; delivery is a separate observation."""
    current(state, row)
    args = event.get("tool_input", {})
    startup = not row.get('claimed_at') or bool(row.get('startup_handbacks'))
    observed = event.get('taskplane_observed_binding', {})
    w.require(row.get("host") == "claude" and row['state'] in {'bootstrapping', 'running'}
              and not row.get("identity_conflict") and not row.get('completion_conflict')
              and row.get('identity_observation', {}).get('status') == 'matched'
              and row.get('identity_freshness', {}).get('status') == 'matched'
              and observed.get('root') == state['root'] and observed.get('principal') == row.get('worker_id')
              and row.get('expected_runtime') == event.get('taskplane_runtime_identity')
              and (startup or readiness(row)["status"] == "ready"),
              "scope_violation", "Handback requires the exact current observed Claude child and startup or ready context.")
    w.require(isinstance(args, dict) and set(args) == {"message"} and isinstance(args.get("message"), str)
              and 0 < len(args["message"].encode("utf-8")) <= 32768,
              "scope_violation", "SubagentHandback supports only bounded message text to its implicit own parent.")
    call = event.get("tool_use_id") or event.get("call_id")
    w.require(isinstance(call, str) and 0 < len(call) <= 512
              and event.get("taskplane_automatic_hook") is True,
              "scope_violation", "Handback requires an exact automatic call.")
    envelope = {"parent": state["root"], "worker": row["worker_id"], "grant": row["grant_id"],
                "attempt": row["attempt"], "task": row["task_id"], "call_id": call,
                "binding": binding(state), "input_digest": digest(args), "body_digest": digest(args["message"])}
    reports = row.setdefault('startup_handbacks', {}) if startup else None
    old = reports.get(call) if reports is not None else row.get("handback")
    w.require(old is None or old.get("envelope") == envelope,
              "scope_violation", "Conflicting or competing handback delivery; reconcile the existing call.")
    if old is None:
        report = {"envelope": envelope, "status": "delivery_unknown", "admitted_at": now(),
                  'runtime': deepcopy(event.get('taskplane_runtime_identity')),
                  "schema": "claude.SubagentHandback.message/v1"}
        if reports is not None:
            w.require(len(reports) < 8, 'scope_violation', 'Startup failure handback attempt bound reached.')
            reports[call] = report
        else:
            row['handback'] = report
            row['handback_call'] = report


def observe_handback(state: dict[str, Any], admission: dict[str, Any], event: dict[str, Any]) -> None:
    if admission.get("tool") not in HANDBACK:
        return
    reports = [(row, report) for row in records(state).values() if row.get('worker_id') == admission.get('principal')
               for report in _handback_reports(row)
               if report.get('envelope', {}).get('call_id') == admission.get('call_id')]
    if len(reports) != 1:
        return
    row, handback = reports[0]
    from .host_capabilities import runtime_identity
    envelope = handback['envelope']
    observed = event.get('taskplane_observed_binding', {})
    w.require(admission.get('automatic') is True and admission.get('host') == 'claude'
              and admission.get('run') == state['run'] and admission.get('root') == state['root']
              and admission.get('workspace') == state['workspace']
              and admission.get('binding') == envelope['binding'] == row.get('binding') == binding(state)
              and not row.get('revoked_at') and not row.get('identity_conflict')
              and admission.get('runtime') == event.get('taskplane_runtime_identity')
              == handback.get('runtime') == row.get('expected_runtime') == runtime_identity()
              and event.get('taskplane_automatic_hook') is True and event.get('hook_event_name') == 'PostToolUse'
              and (event.get('tool_name') or event.get('tool')) in HANDBACK
              and (event.get('tool_use_id') or event.get('call_id')) == envelope['call_id']
              and observed.get('root') == state['root'] and observed.get('principal') == row['worker_id']
              and admission.get('input_digest') == digest(event.get('tool_input')) == envelope['input_digest'],
              'scope_violation', 'Handback acknowledgment requires its exact automatic admission and runtime.')
    response_digest = digest(event.get("tool_response"))
    w.require(handback.get("response_digest", response_digest) == response_digest,
              "scope_violation", "Conflicting handback acknowledgement.")
    response = event.get('tool_response')
    acknowledged = (isinstance(response, dict) and response.get('success') is True
                    and response == {'message': 'Report delivered to your caller.', 'success': True}
                    and event.get('is_error') is not True)
    handback.update(response_digest=response_digest,
                    observed_at=handback.get('observed_at', now()),
                    status='acknowledged' if acknowledged else
                        'delivery_failed' if event.get('is_error') is True else 'delivery_unknown')
    if acknowledged:
        handback['acknowledgment'] = {'schema': 'claude.SubagentHandback.ack/v1',
            'envelope_digest': digest(envelope), 'runtime': deepcopy(row['expected_runtime']),
            'response_digest': response_digest, 'observed_at': handback['observed_at']}


def _observe_claude(state: dict[str, Any], event: dict[str, Any]) -> bool:
    """Cache unbound lifecycle data, then reconcile exact structured launches."""
    tool = event.get('tool_name') or event.get('tool')
    name = event.get('hook_event_name')
    call = event.get('tool_use_id') or event.get('call_id')
    identity = event.get('agent_id')
    attempts = [row for row in records(state).values() if row.get('host') == 'claude']
    relevant = (tool in {'Agent', 'Task'} or event.get('host') == 'claude'
                or (name in {'SubagentStart', 'SubagentStop'} and any(
                    row.get('call_id') == call if call else row.get('worker_id') == identity
                    or row['state'] in {'launch_pending', 'unknown', 'cancel_requested'} for row in attempts)))
    if not relevant:
        return False
    from . import claude_worker_observations as claude
    budget = claude.ReadBudget()
    observed = event.get("taskplane_observed_binding", {})
    event.pop('taskplane_claude_root_source', None)
    if (event.get("taskplane_automatic_hook") is True and observed.get("principal") == state["root"]
            and observed.get("root") == state["root"] and event.get("session_id") == state["root"]
            and not event.get("agent_id") and not event.get("parent_session_id")
            and event.get("transcript_path") and name in {"SessionStart", "PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop"}):
        selection = claude.select_source(state["root"], event, automatic=True,
                    previous=state.get("claude_transcript_source"), budget=budget)
        state["claude_transcript_selection"] = {k: v for k, v in selection.items() if k != "source"}
        if selection["status"] == "selected":
            state["claude_transcript_source"] = selection["source"]
            state["claude_transcript_watermark"] = selection["watermark"]
            event["taskplane_claude_root_source"] = True
    if name in {'SubagentStart', 'SubagentStop'}:
        parent = event.get('session_id') or event.get('thread_id')
        if parent == state['root'] and isinstance(identity, str) and 0 < len(identity) <= 200 and identity != parent:
            status = event.get('status')
            stopped = (name == 'SubagentStop' and status is None
                and event.get('stop_hook_active') is False
                and event.get('taskplane_automatic_hook') is True
                and observed.get('root') == state['root'] and observed.get('principal') == state['root']
                and ('last_assistant_message' not in event and isinstance(event.get('agent_type'), str)
                     and 0 < len(event['agent_type']) <= 200
                     or isinstance(event.get('last_assistant_message'), str)
                     and 0 < len(event['last_assistant_message'].encode('utf-8')) <= claude.MAX_LINE_BYTES))
            if name == "SubagentStart" or status in {'completed', 'failed', 'interrupted'} or stopped:
                value = {'event': name, 'parent': parent, 'worker_id': identity,
                         'call_id': call, 'status': 'stopped' if stopped else status}
                if stopped:
                    # The native notification XML-escapes &, < and > exactly once;
                    # quotes stay literal. Compare its bytes, never decode entities.
                    if 'last_assistant_message' in event:
                        value.update(body_digest=digest(event['last_assistant_message']),
                            notification_body_digest=digest(escape(event['last_assistant_message'], quote=False)))
                    else:
                        value.update(textless=True, agent_type=event['agent_type'],
                                     runtime=deepcopy(event.get('taskplane_runtime_identity')))
                    value.update(transcript_path=event.get('transcript_path'),
                        agent_transcript_path=event.get('agent_transcript_path'))
                event_id = digest(value | {'event_id': event.get('event_id')})
                # Exact automatic child evidence must survive the bounded
                # diagnostic cache, including before denial or legacy recovery.
                # Retaining it never binds a child or establishes completion.
                if (isinstance(call, str) and 0 < len(call) <= 512
                        and event.get('taskplane_automatic_hook') is True
                        and observed.get('root') == state['root'] and observed.get('principal') == state['root']):
                    for row in attempts:
                        if (row.get('call_id') == call and not row.get('worker_id')
                                and row.get('workspace') == state['workspace']):
                            row.setdefault('denial_lifecycle_observation',
                                {'event': name, 'parent': parent, 'worker_id': identity, 'call_id': call,
                                 'event_id': event_id, 'automatic': True, 'observed_at': now()})
                            if is_launch_denial(row):
                                _denial_unknown(row, 'conflict', 'Native lifecycle evidence contradicts denial')
                pending = state.setdefault('unbound_worker_events', {})
                if event_id not in pending and len(pending) < 1024:
                    pending[event_id] = {**value, 'observed_at': now()}
            else:
                state["claude_terminal_observation"] = {"status": "unsupported", "worker_id": identity,
                    "reason": "SubagentStop without a supported explicit terminal status cannot prove completion.",
                    "event_digest": digest({k: event.get(k) for k in ("agent_id", "session_id", "status", "event_id")})}
    correlated_call = call if tool in {'Agent', 'Task'} or name in {'SubagentStart', 'SubagentStop'} else None
    candidates = [row for row in attempts if is_launch_denial(row) or (not row.get('revoked_at')
                  and row['state'] not in {'accepted', 'failed', 'interrupted'}
                  and (row.get('call_id') == correlated_call if correlated_call else
                       (not identity or row.get('worker_id') in (None, identity))))]
    # A lifecycle event does not select the sole/latest child. Each candidate
    # must independently supply its exact admitted call/result/header proof.
    observe_claude_launches(state, candidates, event, budget=budget)
    return True


def observe(state: dict[str, Any], event: dict[str, Any]) -> None:
    name, tool = event.get("hook_event_name"), event.get("tool_name") or event.get("tool")
    call = event.get("tool_use_id") or event.get("call_id")
    response = event.get("tool_response")
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except ValueError:
            response = None
    claude = _observe_claude(state, event)
    if not claude and name == "PostToolUse" and tool in SPAWN | FOLLOW:
        row = next((r for r in records(state).values() if call and r.get("call_id") == call), None)
        w.require(row is not None, "scope_violation", "Native result has no admitted launch.")
        assert row is not None
        if isinstance(response, dict) and isinstance(response.get("agent_id"), str):
            bind_worker(state, row, response["agent_id"], response.get("task_name"))
        elif isinstance(response, dict) and isinstance(response.get("task_name"), str):
            row['canonical_name'] = response['task_name'][:200]
            correlate(state, row)
        elif tool in FOLLOW and row.get('worker_id'):
            bind_worker(state, row, row['worker_id'])
        elif row["state"] == "launch_pending":
            row["state"] = "unknown"  # Ambiguous failure never proves non-start.
    if not claude and name in {"SubagentStart", "SubagentStop"}:
        identity = event.get("agent_id")
        row = (next((r for r in records(state).values() if r.get("call_id") == call), None)
               if call else find(state, str(identity or "")))
        if row and (not isinstance(identity, str) or not identity):
            row['state'] = 'unknown'
            row = None
        if (row and call and isinstance(identity, str) and (row.get('worker_id') != identity or row['state'] == 'launch_pending')
                and not bind_worker(state, row, identity)):
            row = None
        if row and not call and sum(r.get("worker_id") == identity for r in records(state).values()) > 1:
            row = None  # Reused identities need attempt-correlated stop/status evidence.
        if row and name == "SubagentStop":
            terminal(row, "completed", str(event.get("event_id") or f"stop/{row['grant_id']}"))
        elif row and name == 'SubagentStart':
            row.setdefault('started_at', now())
    # Explicit native parent-call metadata can resolve a child-before-return race.
    # Parent identity or an echoed grant alone never selects the latest child.
    if not claude and event.get("parent_session_id") == state["root"] and event.get("parent_tool_call_id"):
        row = next((r for r in records(state).values()
                    if r.get("call_id") == event["parent_tool_call_id"]), None)
        identity = event.get("thread_id") or event.get("session_id")
        if row and isinstance(identity, str):
            bind_worker(state, row, identity)
    elif not claude and event.get('parent_session_id') == state['root']:
        identity = event.get('thread_id') or event.get('session_id')
        known = find(state, str(identity or ''))
        if known and known['state'] == 'launch_pending':
            bind_worker(state, known, str(identity))
        for row in records(state).values():
            if row['state'] == 'launch_pending' and row.get('host') == 'codex':
                correlate(state, row, expected_identity=identity)
    principal = event.get('taskplane_observed_binding', {}).get('principal') or event.get('thread_id') or event.get('session_id')
    executing = find(state, str(principal or ''))
    if executing and name == 'PreToolUse' and executing['state'] in {'bootstrapping', 'running'}:
        executing.setdefault('started_at', now())
    if name == "PostToolUse" and tool in STATUS and isinstance(response, dict):
        agents = response.get("agents")
        if isinstance(agents, list):
            for agent in agents:
                if not isinstance(agent, dict):
                    continue
                target = str(agent.get('agent_id') or agent.get('agent_name') or '')
                candidates = [r for r in records(state).values()
                              if target and target in {r.get('worker_id'), r.get('canonical_name')}]
                polls = state.get('worker_polls', {})
                if call in polls:
                    candidates = [r for r in candidates if r['grant_id'] in polls[call]]
                elif len(candidates) > 1:
                    candidates = []  # An uncorrelated old poll cannot join a new attempt.
                row = max(candidates, key=lambda r: r['prepared_at']) if candidates else None
                if row and not row.get('worker_id'):
                    correlate(state, row)
                status = agent.get('status', agent.get('agent_status'))
                if isinstance(status, dict):
                    status = next(iter(status)) if len(status) == 1 else None
                if row and status in {"completed", "idle", "failed", "interrupted"}:
                    if row.get('worker_id'):
                        terminal(row, status, str(call or "status") + "/" + row["grant_id"])
    # wait_agent is a mailbox wait; interrupt returns previous status. Neither joins work.


def correlate(state: dict[str, Any], row: dict[str, Any], expected_identity: str | None = None) -> None:
    from .host_capabilities import worker_identities
    identities = worker_identities(state['root'], canonical_name=row.get('canonical_name'),
                                   task_name=row['task_name'], since=row['prepared_at'])
    if len(identities) == 1 and (expected_identity is None or identities[0]['worker_id'] == expected_identity):
        identity = identities[0]
        bind_worker(state, row, identity['worker_id'], identity['canonical_name'])
        row['identity_basis'] = 'observed native session metadata and admitted task/call'
        row['native_session_started_at'] = identity['started_at']


def retain_rejection(workspace: Path, state: dict[str, Any], row: dict[str, Any],
                     request: dict[str, Any]) -> dict[str, Any] | None:
    """Retain an authorized reject-result; the host validates and commits its call.

    Rejection needs a genuine joined attempt, not successful startup or passing
    completion. Existing immutable partial evidence keeps its original identity.
    """
    from . import worker_results as results
    definition = task(state, row['task_id'])
    ledger_id = _result_policy(state, definition, initialize=False)
    if ledger_id is None:
        return None
    w.require(row.get('result_ledger') == ledger_id
              and row.get('result_binding') == state['result_ledgers'][ledger_id]['binding'],
              'completion_contract_required', 'Native rejection lacks its previously admitted completion ledger.')
    if row.get('result_disposition'):
        retained = deepcopy(row['result_disposition'])
    else:
        fingerprints = _result_fingerprints(workspace, state, definition)
        retained = results.retain_result_state(workspace, state, ledger_id=ledger_id, definition=definition,
            binding=state['result_ledgers'][ledger_id]['binding'], attempt_id=row['grant_id'],
            request={'outcome': 'rejected', 'outputs': [], 'checks': [], 'semantic_review': {},
                     'side_effects': 'unknown', 'reason': 'semantic_inadequacy',
                     'detail': ('Root rejected result: ' + request['reason'])[:2048]},
            fingerprints=fingerprints, current_fingerprints=lambda: _result_fingerprints(workspace, state, definition),
            runtime=_result_runtime())
        row['result_disposition'] = deepcopy(retained)
        refresh_result_budgets(workspace, state)
    return {**retained, 'outcome_digest': digest(retained)}


def accept_result(workspace: Path, state: dict[str, Any], task_id: str, request: dict[str, Any]) -> dict[str, Any]:
    definition = task(state, task_id)
    w.require(all(result_valid(workspace, state, d) for d in definition["dependencies"]),
              "invalid_evidence", "Prerequisite results are missing or changed.")
    grant = request.get("grant")
    row = records(state).get(grant) if grant else None
    if grant:
        w.require(definition.get("execution") != "root", "invalid_evidence",
                  "Root execution conflicts with a native result grant.")
        w.require(row and row["task_id"] == task_id and row["state"] == "result_pending",
                  "invalid_evidence", "Native attempt is not joined with a pending result.")
        assert row is not None
        w.require(not row.get('result_rejection'), 'invalid_evidence',
                  'This result was rejected; verification requires a fresh attempt.')
        current(state, row)
        if row.get("host") == "claude":
            fresh = observe_claude_launches(state, [row], {})
            w.require(fresh[0]["status"] == "matched" and not row.get("identity_conflict"),
                      "invalid_evidence", "Claude result needs freshly revalidated exact launch proof.")
            w.require(row.get("handback", {}).get("status") == "delivered"
                      and fresh[0].get('completion') == row.get('handback', {}).get('notification')
                      and not row.get('completion_conflict'), "invalid_evidence",
                      "Claude result needs verified handback delivery from its exact native attempt.")
        w.require(row.get('claimed_at') and not row.get('identity_conflict'), 'invalid_evidence',
                  'Worker must claim its exact non-conflicting attempt before accepting a result.')
        w.require(row["input_manifest"] == e.manifest(workspace, list(row["input_manifest"])),
                  "stale_checkpoint", "Worker read inputs changed; revalidate with a fresh attempt.")
        w.require(row.get("dependency_results") == dependency_results(state, definition["dependencies"]),
                  "stale_checkpoint", "Worker prerequisite results changed; consume a fresh attempt.")
        w.require(not any(h["state"] == "running" and h.get("worker_id") == row["worker_id"]
                          for h in state.get("observed_handles", {}).values()),
                  "scope_violation", "Worker commands remain live.")
    else:
        w.require(definition.get("execution") != "native_required", "invalid_evidence",
                  "Required native task needs its real joined worker result.")
        w.require(definition["owner"] in {"root", state["root"]}, "scope_violation", "Native task requires its real joined worker.")
    w.require(not any(r["state"] in LIVE and task_id in r.get("dependency_results", {})
                      for r in records(state).values()), "scope_violation",
              "Join dependent workers before replacing their prerequisite result.")
    outputs, checks = request.get("outputs"), request.get("checks")
    from . import worker_results as results
    declared = results.contract(definition, required=False)
    ledger_id = _result_policy(state, definition, initialize=row is None)
    if declared is not None:
        assert ledger_id is not None
        if row:
            w.require(row.get('result_ledger') == ledger_id
                      and row.get('result_binding') == state['result_ledgers'][ledger_id]['binding'],
                      'completion_contract_required', 'Native result has no previously admitted completion ledger.')
        fingerprints = _result_fingerprints(workspace, state, definition)
        result_request = {key: value for key, value in request.items() if key != 'grant'}
        result_request.setdefault('side_effects', 'unknown')
        retained = results.retain_result_state(workspace, state, ledger_id=ledger_id, definition=definition,
            binding=state['result_ledgers'][ledger_id]['binding'],
            attempt_id=row['grant_id'] if row else 'root/' + digest(binding(state)), request=result_request,
            fingerprints=fingerprints, current_fingerprints=lambda: _result_fingerprints(workspace, state, definition),
            runtime=_result_runtime())
        refresh_result_budgets(workspace, state)
        if row:
            row['result_disposition'] = deepcopy(retained)
        if retained['status'] != 'verified':
            # Controller.worker persists this structured disposition in its
            # existing locked CAS. No exception may discard retained evidence.
            return {**retained, 'accepted': False, 'grant': grant, 'task': task_id,
                    'outcome_digest': digest(retained), 'next_action': 'Review retained contributions and submit an exact residual retry plan.'}
    elif request.get('outcome', 'complete') != 'complete':
        raise w.Refusal('worker_result_incomplete', 'A declared incomplete result cannot be accepted.')
    w.require(isinstance(outputs, list) and outputs and all(isinstance(p, str) for p in outputs)
              and set(outputs) <= set(definition["paths"]), "invalid_evidence", "Result outputs must belong to the task.")
    w.require(isinstance(checks, list) and checks and all(isinstance(c, dict) and c.get("status") == "pass"
              and isinstance(c.get("name"), str) and c["name"] and isinstance(c.get("evidence"), str) for c in checks),
              "invalid_evidence", "Accept results only with passing named verification evidence.")
    assert isinstance(outputs, list) and isinstance(checks, list)
    files = outputs + [c["evidence"] for c in checks]
    from .host_capabilities import runtime_identity
    result = {"task_digest": digest(definition), "manifest": e.manifest(workspace, files),
              "outputs": list(outputs), "input_manifest": deepcopy(row["input_manifest"]) if row else
                  read_input_manifest(workspace, Brief(workspace, state, task_id), definition["paths"]),
              "input_contract": "declared-source/v1",
              "dependency_results": dependency_results(state, definition["dependencies"]),
              "checks": deepcopy(checks), "accepted_at": now(), "grant": grant,
              "reviewer": state["root"], "worker_id": row["worker_id"] if row else None,
              "accepted_revision": state['revision'], 'verification_plan': verification_plan(state, task_id),
              'runtime_digest': digest(runtime_identity()),
              'task_epoch': state.get('task_epochs', {}).get(task_id, 0),
              'requirements_epochs': {c: state.get('criterion_epochs', {}).get(c, 0) for c in e.task_criteria(definition)}}
    state.setdefault("task_results", {})[task_id] = result
    if row:
        row["state"] = "accepted"
    return result


def lens_summary(state: dict[str, Any], workspace: Path) -> list[dict[str, Any]]:
    result = []
    phase = w.current(state)["phase"]
    capacity = state.get("worker_capacity", {})
    stage = w.current(state)
    if phase != "engineering":
        stage = next((visit for visit in reversed(state["visits"][:state["index"] + 1])
                      if visit["phase"] == "engineering" and not visit.get("superseded")
                      and visit.get("packet")), {})
    if (stage.get("packet") and stage["decision"] in {"awaiting_human_approval", "approved"}
            and not state.get("invalidation_pending") and e.changed(workspace, state) is None):
        claims = stage["packet"]["output"].get("lens_coverage", [])
        return [{**claim, "status": claim.get("status") if stage["packet"].get("execution_evidence") == "native-results/v1"
                 and claim.get("task_id") else "legacy_unverified"}
                for claim in claims]
    definitions = [row for row in e.context_tasks(state)
                   if row.get("phase", phase) == phase and row.get("review_lens")]
    valid_results = {row["id"]: state["task_results"][row["id"]] for row in definitions
                     if row.get("execution") == "native_required" and result_valid(workspace, state, row["id"])}
    conflicts = e.native_lens_conflicts(definitions, valid_results)
    for definition in definitions:
        accepted = state.get("task_results", {}).get(definition["id"], {})
        native = definition.get("execution") == "native_required"
        conflict = definition["id"] in conflicts
        verified = definition["id"] in valid_results and not conflict
        result.append({"lens": definition["review_lens"], "task_id": definition["id"],
            "status": "native_verified" if verified else "serial_scope" if not native else
                "unavailable" if not conflict and capacity.get("status") == "unavailable" else "native_pending",
            "reviewer": accepted.get("worker_id") if verified else state["root"] if not native else None,
            "grant": accepted.get("grant") if verified else None,
            "outputs": accepted.get("outputs", []) if verified else [],
            "rationale": ("A distinct actual worker is required for each selected lens; reviewer "
                          + str(accepted["worker_id"]) + " completed multiple lenses.") if conflict else
                definition.get("execution_reason") if not native else capacity.get("reason"),
            "reference": definition.get("execution_reference") if not native else capacity.get("reference")})
    return result


def summary(state: dict[str, Any], workspace: Path | None = None) -> dict[str, Any]:
    rows = list(state.get("workers", {}).values())
    scheduling = []
    limit = state.get('worker_capacity', {}).get('effective_limit')
    active = [r for r in rows if r['state'] in LIVE]
    if workspace is not None:
        phase = w.current(state)['phase']
        for definition in e.context_tasks(state):
            if definition.get('phase', phase) != phase:
                continue
            key = definition['id']
            missing = [d for d in definition['dependencies'] if not result_valid(workspace, state, d)]
            candidate = {'paths': definition['paths'],
                         'input_manifest': set(e.read_inputs(state, definition)) - set(definition['paths']),
                         'dependency_manifest': dependency_manifest(state, definition['dependencies'])}
            reason = ('accepted' if result_valid(workspace, state, key) else
                      'active attempt' if any(r['task_id'] == key for r in active) else
                      'prerequisites: ' + ', '.join(missing) if missing else
                      'root execution' if definition.get('execution') == 'root' else
                      'read/write conflict' if any(path_conflict(candidate, r) for r in active) else
                      'capacity unknown' if limit is None else
                      'native unavailable: ' + str(state['worker_capacity'].get('reason') or 'observed zero capacity') if limit == 0 else
                      'capacity full' if len(active) >= limit else 'ready')
            scheduling.append({'task': key, 'reason': reason, 'execution': definition.get('execution', 'legacy'),
                               'review_lens': definition.get('review_lens'),
                               'reference': state.get('worker_capacity', {}).get('reference')})
    return {"capacity": state.get("worker_capacity"), "default_limit": None,
            "scheduling": scheduling,
            "lens_coverage": lens_summary(state, workspace) if workspace is not None else [],
            "pending": sum(r["state"] in {"prepared", "launch_pending", "unknown"} for r in rows),
            "live": sum(r["state"] in {"bootstrapping", "running", "cancel_requested"} for r in rows),
            "accepted": sum(r["state"] == "accepted" for r in rows),
            "counts": {"tasks": len({r["task_id"] for r in rows}), "reserved": len(rows),
                       "launched": sum(bool(r.get("call_id")) for r in rows),
                       "retries": sum(r["attempt"] > 1 for r in rows),
                       "failed": sum(r["state"] in {"failed", "interrupted"} for r in rows)},
            "context_cost": {"returned_bytes": sum(r.get("context_delivery", {}).get("returned_bytes", 0) for r in rows),
                             "responses": sum(r.get("context_delivery", {}).get("responses", 0) for r in rows),
                             "unknown_attempts": sum("context_delivery" not in r for r in rows),
                             "basis": "Delivered context bytes and responses, not native tokens or Codex allowance."},
            "attempts": [{**{k: r.get(k) for k in ("grant_id", "task_id", "worker_id", "attempt", "state",
                "prepared_at", "started_at", "ended_at", "purpose", "retry_reason", "context_preflight", "context_delivery")},
                "readiness": readiness(r),
                **({'launch_preflight': deepcopy(r['launch_preflight'])} if r.get('launch_preflight') else {}),
                **({'handback_reconciliation': deepcopy(r['handback_reconciliation'])} if r.get('handback_reconciliation') else {}),
                **({'launch_blocker': deepcopy(r['preflight_blocker'])} if r.get('preflight_blocker') else {}),
                **({'identity_freshness': deepcopy(r['identity_freshness'])} if r.get('identity_freshness') else {})}
                for r in rows]}
