"""Scoped requirements revisions and runtime compatibility checks."""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
from typing import Any
from . import workflow as w
from .context import digest, encode
REVISION_SCHEMA = 'taskplane.requirements-revision/v1'

def invocation_transport(record: dict[str, Any]) -> Any:
    from . import claude_worker_invocation
    w.require(record.get('schema') == claude_worker_invocation.SCHEMA, 'incompatible_runtime',
              'Historical startup invocation is readable only with its original runtime.')
    return claude_worker_invocation

def revision_preview(workspace: Path, state: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    """Compute a complete conservative closure from accepted interfaces and graph.

    This function is read-only. Missing mappings or graph coverage expand the
    affected closure instead of treating an unobserved dependency as absent.
    """
    from . import workflow_evidence as e, depgraph, worker_runtime as workers
    fields = {'schema', 'request_id', 'request_reference', 'run', 'expected_revision',
              'expected_generation', 'scope_digest', 'requirements_digest', 'plan_digest',
              'changes', 'affected_artifacts', 'scope_amendment', 'reason', 'return_phase'}
    w.require(isinstance(request, dict) and set(request) == fields and len(encode(request)) <= 262144
              and request['schema'] == REVISION_SCHEMA and request['return_phase'] in {'product', 'plan'}
              and all(isinstance(request[k], str) and 0 < len(request[k].strip()) <= limit
                      for k, limit in [('request_id', 128), ('request_reference', 512), ('reason', 2048)]),
              'invalid_evidence', 'A bounded typed revision needs the actual human request, exact changes and Product or Plan return.')
    w.require(request['run'] == state['run'] and type(request['expected_revision']) is int
              and type(request['expected_generation']) is int and request['expected_revision'] == state['revision']
              and request['expected_generation'] == state.get('storage_generation', 0)
              and request['scope_digest'] == digest(state['scope']),
              'stale_checkpoint', 'Revision run, generation, scope or state changed.')
    w.require(w.current(state)['phase'] == 'build' and w.current(state)['decision'] in
              {'not_requested', 'changes_requested', 'rejected'} and not state.get('finished')
              and not state.get('cancellation') and not state.get('active_requirements_revision'),
              'approval_required', 'Begin a revision only from an active unsealed Build without another revision.')
    plan = w.accepted_plan(state)
    product = next((v for v in reversed(state['visits'][:state['index']])
                    if v['phase'] == 'product' and v['decision'] == 'approved'
                    and not v['superseded'] and v.get('packet')), None)
    w.require(product and request['requirements_digest'] == digest(product['packet']['output'])
              and request['plan_digest'] == digest(plan['packet']),
              'stale_checkpoint', 'The accepted Product or Plan differs from this revision request.')
    assert product is not None
    original = {row['id']: row for row in product['packet']['output']['acceptance_criteria']}
    changes = request['changes']
    plan_only = request['return_phase'] == 'plan'
    w.require(isinstance(changes, dict) and (changes == {} if plan_only else bool(changes))
              and set(changes) <= set(original)
              and all(isinstance(row, dict) and row.get('id') == key and e.substantive(row.get('statement'))
                      and row != original[key] for key, row in changes.items()),
              'invalid_evidence', 'Product changes need exact replacement criteria; a Plan amendment must leave requirements unchanged.')
    new_scope = deepcopy(request['scope_amendment'] or state['scope'])
    e.valid_scope(workspace, new_scope)
    w.require(new_scope['criteria'] == state['scope']['criteria'], 'scope_violation',
              'A selective revision must preserve the full criterion register.')
    if plan_only:
        preserved = deepcopy(new_scope)
        for phase in ('plan', 'build'):
            preserved['paths'][phase] = deepcopy(state['scope']['paths'][phase])
        w.require(preserved == state['scope']
                  and set(new_scope['paths']['build']) != set(state['scope']['paths']['build']),
                  'scope_violation', 'A Plan-only amendment may change only Plan outputs and exact Build paths; '
                  'Product, Design and all other scope boundaries remain accepted.')
    historical = {p for visit in state['visits'] if visit.get('packet')
                  for p in visit['packet']['manifest']}
    renewal = ['plan'] if plan_only else ['product', 'design', 'plan']
    w.require(all(not set(new_scope['paths'][phase]) & historical for phase in renewal),
              'scope_violation', 'Specify fresh outputs for renewed phases; sealed history cannot be overwritten.')
    definitions = {row['id']: row for row in plan['packet']['output']['task_dag']}
    w.require(definitions and all('read_inputs' in row for row in definitions.values()),
              'invalid_evidence', 'Selective closure is unknown: every accepted task needs explicit read interfaces.')
    coverage = plan['packet']['output']['acceptance_coverage']
    w.require(set(coverage) == set(original) and all(set(v) <= set(definitions) and v for v in coverage.values()),
              'invalid_evidence', 'Selective closure is unknown: accepted criterion/task coverage is incomplete.')
    strategy = plan['packet']['output'].get('verification_strategy')
    w.require(isinstance(strategy, dict) and strategy.get('schema') == e.VERIFICATION_STRATEGY
              and isinstance(strategy.get('checks'), list) and strategy['checks'],
              'invalid_evidence', 'Selective closure is unknown: accepted check definitions are required.')
    checks = strategy['checks']
    w.require(all(set(row.get('criteria', [])) <= set(original) and row.get('task') in definitions
                  for row in checks) and {c for row in checks for c in row['criteria']} == set(original),
              'invalid_evidence', 'Selective closure is unknown: checks must cover every criterion and exact task.')
    allowed = {p for paths in state['scope']['paths'].values() for p in paths}
    allowed.update(p for row in definitions.values() for p in row['read_inputs'])
    artifacts = request['affected_artifacts']
    w.require(isinstance(artifacts, dict) and set(artifacts) <= allowed
              and artifacts == e.manifest(workspace, list(artifacts)),
              'stale_checkpoint', 'Affected artifacts must be current exact accepted-scope references.')
    graph = depgraph.load(str(workspace))
    quality = depgraph.scan_quality(graph)
    graph_complete = bool(graph.get('meta', {}).get('graph_scan_quality') and not quality['degraded']
              and quality.get('mode') == 'components'
              and graph.get('meta', {}).get('source_coverage', {}).get('complete') is True
              and graph.get('meta', {}).get('module_confidence') != 'low'
              and depgraph.source_inputs_current(str(workspace), graph))
    if not graph_complete:
        # Unknown source edges expand the closure to every approved task. They
        # cannot veto a human correction or authorize selective reuse.
        graph = {'modules': {}, 'edges': [], 'meta': {'coverage': 'unknown'}}
    module_ids = depgraph.declared_module_ids(graph)
    def module(path: str) -> str:
        return path if path in graph['modules'] else depgraph.module_of(path, module_ids)
    reads = {key: set(row['read_inputs']) for key, row in definitions.items()}
    writes = {key: set(row['paths']) for key, row in definitions.items()}
    # The scanner intentionally omits Taskplane's evidence store. Exact declared
    # data artifacts still participate in task/read/write closure; they are not
    # source modules. Never extend this exception to executable or declared
    # implementation/test paths, or to undeclared files under the same folder.
    intent = state['scope'].get('implementation_intent', {})
    source_paths = set(intent.get('implementation_paths', [])) | set(intent.get('test_paths', []))
    data_artifacts = {p for paths in writes.values() for p in paths
                      if Path(p).parts[0] == '.taskplane'
                      and Path(p).suffix in {'.json', '.jsonl', '.md', '.txt', '.log'}
                      and p not in source_paths}
    affected = {key for criterion in changes for key in coverage[criterion]}
    # Newly added paths have no accepted task owner yet. A Plan amendment must
    # rebind every Build task rather than infer selective reuse from that gap.
    if plan_only or not graph_complete:
        affected = set(definitions)
    files = set(artifacts)
    if plan_only:
        files.update(set(new_scope['paths']['build']) ^ set(state['scope']['paths']['build']))
    modules: set[str] = set()
    # Fixed point includes downstream task dependencies, read interfaces,
    # shared writers, reverse source edges and the accepted integration checks.
    while True:
        before = (frozenset(affected), frozenset(files), frozenset(modules))
        files.update(p for key in affected for p in writes[key])
        modules.update(module(p) for p in files if module(p) in graph['modules'])
        for edge in graph['edges']:
            if edge['to'] in modules:
                modules.add(edge['from'])
        for key, row in definitions.items():
            if (set(row['dependencies']) & affected or (reads[key] | writes[key]) & files
                    or any(module(p) in modules for p in reads[key] | writes[key])):
                affected.add(key)
        for check in checks:
            if set(check['criteria']) & set(changes) or set(check.get('source_inputs', []) +
                    check.get('test_inputs', [])) & files or check['task'] in affected:
                affected.add(check['task'])
        if before == (frozenset(affected), frozenset(files), frozenset(modules)):
            break
    unknown = [p for p in files if e.path(workspace, p).exists()
               and module(p) not in graph['modules'] and p not in data_artifacts]
    if unknown:
        affected = set(definitions)
        files.update(p for row in definitions.values() for p in row['paths'])
    artifact_manifest = e.manifest(workspace, sorted(files & data_artifacts), allow_missing=True)
    affected_criteria = {c for key in affected for c in e.task_criteria(definitions[key])}
    renewed = sorted(set(changes))
    result = {'schema': 'taskplane.requirements-revision-preview/v1', 'request_id': request['request_id'],
        'request_digest': digest(request), 'binding': {'run': state['run'], 'revision': state['revision'],
            'storage_generation': state.get('storage_generation', 0)},
        'criteria': renewed, 'affected_criteria': sorted(affected_criteria), 'tasks': sorted(affected),
        'files': sorted(files), 'modules': sorted(modules), 'graph_digest': digest(graph),
        'artifact_manifest': artifact_manifest,
        'graph_coverage': 'complete' if graph_complete and not unknown else 'unknown; all tasks affected',
        'checks': sorted(check['id'] for check in checks if check['task'] in affected
                         or set(check['criteria']) & set(changes)),
        'affected_grants': sorted(key for key, row in workers.records(state).items() if row['task_id'] in affected),
        'retained_results': sorted(key for key in state.get('task_results', {}) if key not in affected),
        'approvals_requiring_renewal': renewal, 'scope': new_scope,
        'requirements': [deepcopy(changes.get(key, value)) for key, value in original.items()],
        'status': 'preview', 'approvals_created': 0}
    return {**result, 'preview_digest': digest(result)}

def require_runtime(state: dict[str, Any]) -> None:
    """A migrated contract must keep the reviewed enforcing runtime for writes."""
    w.require(state.get("schema") == "taskplane.workflow/v2", "incompatible_runtime",
              "An active historical run requires its original runtime; start a new compact run.")
    if state.get("profile") == "native_workflow":
        from .host_capabilities import runtime_identity
        w.require(state.get("runtime_identity") == runtime_identity(), "incompatible_runtime",
                  "The active run belongs to another installation/package identity. Use its original runtime or start a fresh run.")


def validate_control_history(state: dict[str, Any]) -> None:
    """Reject corrupt revision/migration records before they can affect a grant."""
    def checksum(value: Any) -> bool:
        return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)
    pin = state.get('context_runtime_digest')
    w.require(pin is None or checksum(pin) and state.get('context_contract') == 'bounded/v3',
              'state_unavailable', 'Invalid enforcing context runtime pin.')
    for name, kind in [('context_upgrades', 'upgrade'), ('context_rollbacks', 'rollback')]:
        events = state.get(name, {})
        w.require(isinstance(events, dict) and len(events) <= 256,
                  'state_unavailable', 'Invalid context transition history.')
        for key, event in events.items():
            w.require(isinstance(event, dict) and event.get('schema') == f'taskplane.context-{kind}-event/v1'
                      and checksum(event.get('request_digest')) and checksum(event.get('preview_digest'))
                      and isinstance(event.get('result'), dict) and event['result'].get('request_id') == key
                      and event['result'].get('run') == state['run'] and event['result'].get('status') == 'committed'
                      and event['result'].get('context_contract') == 'bounded/v3'
                      and event['result'].get('approvals_created') == 0 and pin is not None
                      and type(event.get('prior_revision')) is int and event['prior_revision'] >= 0
                      and type(event['result'].get('revision')) is int
                      and event['result']['revision'] == event['prior_revision'] + 1,
                      'state_unavailable', 'Invalid committed context transition.')
    events = state.get('requirements_revisions', {})
    w.require(isinstance(events, dict) and len(events) <= 256,
              'state_unavailable', 'Invalid requirements revision history.')
    pending = []
    for key, event in events.items():
        w.require(isinstance(event, dict) and event.get('schema') == 'taskplane.requirements-revision-event/v1'
                  and isinstance(event.get('request'), dict) and event['request'].get('request_id') == key
                  and event['request'].get('run') == state['run']
                  and event.get('request_digest') == digest(event['request'])
                  and isinstance(event.get('preview'), dict) and isinstance(event.get('result'), dict)
                  and type(event.get('opened')) is bool and isinstance(event.get('history'), list)
                  and event.get('status') in {'pending', 'review', 'aborted', 'completed', 'superseded'},
                  'state_unavailable', 'Invalid requirements revision event.')
        preview = event['preview']; result = event['result']
        w.require(preview.get('request_digest') == event['request_digest']
                  and preview.get('preview_digest') == digest({k: v for k, v in preview.items() if k != 'preview_digest'})
                  and isinstance(preview.get('binding'), dict) and preview['binding'].get('run') == state['run']
                  and isinstance(preview.get('tasks'), list) and isinstance(preview.get('criteria'), list)
                  and result.get('run') == state['run'] and result.get('request_id') == key
                  and result.get('status') == event['status'] and result.get('approvals_created') == 0
                  and (event['status'] not in {'review', 'completed'} or event['opened']),
                  'state_unavailable', 'Requirements revision proof or status is inconsistent.')
        if event['status'] not in {'completed', 'superseded'}:
            pending.append(key)
    active = state.get('active_requirements_revision')
    w.require((not pending and active is None) or (pending == [active] and state.get('dispatch_paused') is True),
              'state_unavailable', 'Pending requirements revision must retain its dispatch fence.')


def validate_historical_invocation(record: dict[str, Any]) -> None:
    """Read retained startup correlation metadata; never issue or consume it."""
    from .primitives import manifest_record
    w.require(isinstance(record, dict) and record.get('schema') == 'taskplane.claude-startup-invocation/v1'
              and record.get('fingerprint') == manifest_record(record)['fingerprint']
              and isinstance(record.get('binding'), dict) and record.get('state') in {'pending','consumed'},
              'state_unavailable', 'Historical invocation metadata is corrupt.')
