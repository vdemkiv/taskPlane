"""Opt-in result evidence, residual scheduling and admission budgets.

Controller integration contract:
* Validate native identity, current binding, permission, terminal/quiescent state
  and runtime pin through the controller's existing validators. A request field
  is never an authorization. Pass an internal ``authorize(database)`` callback
  to the transaction APIs; it runs under the controller lock before any writes.
* ``retain_result`` commits immutable artifacts and the disposition before a
  caller returns its structured rejection. ``raise_for_result`` is separate.
* ``reserve_dispatch`` commits all applicable run/task/visit reservations in one
  CAS before issuing a native call. Mark issued before the external call; an
  uncertain call is consumed. Reconcile observations on every scheduling/status
  boundary. Never release reservations merely because time elapsed.
  Every original or continuation native delivery cycle uses a distinct dispatch
  attempt/reservation key in the SAME frozen run/task/visit budgets. ``issued_call``
  comes from the actual host pre-call hook/correlation; never invent or guess it.
  Missing supported call identity prevents issue and remains an integration gap.
These are cooperative local-state controls, not host authority or a native kill.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Callable, cast

from . import primitives, storage, workflow as w, workflow_evidence as evidence
from .context import Store, digest
from .context_handoff import text_body
from .context_reuse import contribution_matches

SCHEMA = 'taskplane.worker-completion/v1'
RESULT_CONTRACT = 'taskplane.worker-result-contract/v1'
LEDGER = 'taskplane.residual-ledger/v1'
CONTRIBUTION = 'taskplane.contribution/v1'
BUDGET = 'taskplane.retry-budget/v1'
POLICY = 'taskplane.retry-policy/v1'
CAPABILITIES = {'durable-results', 'residual-dag', 'frozen-retry-budgets', 'runtime-pinning'}
REASONS = {'partial', 'truncated', 'max_turns', 'missing_artifacts', 'missing_evidence',
           'failed_verification', 'failed', 'interrupted', 'semantic_inadequacy'}


def _require(condition: Any, message: str, reason: str = 'invalid_evidence') -> None:
    w.require(bool(condition), reason, message)


def _integer(value: Any, name: str, minimum: int = 0, maximum: int | None = None) -> int:
    _require(type(value) is int and value >= minimum and (maximum is None or value <= maximum),
             f'{name} must be an explicit integer in the supported range.')
    return cast(int, value)


def _strings(value: Any, name: str, *, empty: bool = False) -> list[str]:
    _require(isinstance(value, list) and (empty or bool(value)) and len(value) <= 256
             and all(isinstance(x, str) and x.strip() for x in value)
             and len(value) == len(set(value)), f'{name} must contain unique nonempty strings.')
    return cast(list[str], value)


def validate_policy(value: Any) -> dict[str, Any]:
    _require(isinstance(value, dict) and {'schema'} < set(value)
             and set(value) <= {'schema', 'attempt_cap', 'elapsed_cap_ms', 'token_cap'}
             and value['schema'] == POLICY, 'At least one explicit supported retry cap is required.')
    if 'attempt_cap' in value:
        _integer(value['attempt_cap'], 'attempt_cap', 1, 8)
    for name in ('elapsed_cap_ms', 'token_cap'):
        if name in value:
            _integer(value[name], name)
    return deepcopy(value)


def policy_for_definition(definition: dict[str, Any]) -> dict[str, Any] | None:
    declared = contract(definition, required=False)
    if declared is None:
        return None
    return (deepcopy(declared['retry_policy']) if declared['schema'] == RESULT_CONTRACT else
            {'schema': POLICY, 'attempt_cap': declared['max_attempts']})


def contract(definition: dict[str, Any], *, required: bool = True) -> dict[str, Any] | None:
    """Unrelated or absent opaque metadata never opts in, nor is it rewritten."""
    value = definition.get('completion')
    schema = value.get('schema') if isinstance(value, dict) else None
    reserved = isinstance(schema, str) and schema.startswith(
        ('taskplane.worker-completion/', 'taskplane.worker-result-contract/'))
    if not reserved:
        _require(not required, 'An explicit result contract is required.', 'completion_contract_required')
        return None
    assert isinstance(value, dict)
    _require(schema in {SCHEMA, RESULT_CONTRACT}, 'Unknown reserved result contract version.')
    outputs = _strings(value.get('required_outputs'), 'required_outputs')
    paths = set(definition['paths'])
    criteria = set(_strings(evidence.task_criteria(definition), 'criteria'))
    _require(set(outputs) <= paths, 'Required outputs exceed task write authority.')
    if schema == SCHEMA:
        _require(set(value) == {'schema', 'required_outputs', 'criteria_evidence', 'max_attempts'},
                 'Unsupported completion fields.')
        declared = value['criteria_evidence']
        _require(len(outputs) <= 64, 'Completion v1 supports at most 64 required outputs.')
        _require(isinstance(declared, dict) and set(declared) == criteria, 'Every criterion needs evidence.')
        for items in declared.values():
            _require(set(_strings(items, 'criteria_evidence')) <= paths and len(items) <= 64,
                     'Completion v1 criterion evidence exceeds its scope or supported limit.')
        _integer(value['max_attempts'], 'max_attempts', 1, 8)
    else:
        _require(set(value) == {'schema', 'required_outputs', 'criterion_obligations', 'reuse_policy', 'retry_policy'},
                 'Unsupported ResultContract fields.')
        obligations = value['criterion_obligations']
        _require(isinstance(obligations, dict) and set(obligations) == criteria, 'Every criterion needs an obligation.')
        for cid, row in obligations.items():
            _require(isinstance(row, dict) and set(row) == {'outputs', 'evidence', 'depends_on', 'semantic_dependencies'},
                     'Invalid criterion obligation.')
            for field in ('outputs', 'evidence'):
                _require(set(_strings(row[field], field)) <= paths, 'Obligation artifact exceeds task authority.')
            for field in ('depends_on', 'semantic_dependencies'):
                _require(set(_strings(row[field], field, empty=True)) <= criteria, 'Unknown criterion dependency.')
        _execution_order({cid: row['depends_on'] for cid, row in obligations.items()})
        _require(value['reuse_policy'] == {'schema': 'taskplane.contribution-reuse/v1',
                                          'require_complete_fingerprints': True}, 'Unsupported reuse policy.')
        validate_policy(value['retry_policy'])
    return deepcopy(value)


def _obligations(declared: dict[str, Any]) -> dict[str, Any]:
    if declared['schema'] == RESULT_CONTRACT:
        return deepcopy(declared['criterion_obligations'])
    return {cid: {'outputs': paths, 'evidence': paths, 'depends_on': [], 'semantic_dependencies': []}
            for cid, paths in declared['criteria_evidence'].items()}


def _execution_order(dependencies: dict[str, list[str]]) -> list[str]:
    """Only the execution graph must be acyclic; semantic relations may cycle."""
    ready = sorted(cid for cid, deps in dependencies.items() if not deps)
    order: list[str] = []
    children: dict[str, list[str]] = {cid: [] for cid in dependencies}
    remaining = {cid: len(deps) for cid, deps in dependencies.items()}
    for cid, deps in dependencies.items():
        _require(set(deps) <= set(dependencies) and len(set(deps)) == len(deps), 'Invalid execution dependency.')
        for dep in deps:
            children[dep].append(cid)
    while ready:
        cid = ready.pop()
        order.append(cid)
        for child in children[cid]:
            remaining[child] -= 1
            if remaining[child] == 0:
                ready.append(child)
    _require(len(order) == len(dependencies), 'Residual execution dependencies contain a cycle.')
    return order


def runtime_pin(identity: dict[str, Any]) -> dict[str, Any]:
    _require(isinstance(identity, dict) and set(identity) == {'sha256', 'schemas', 'capabilities'},
             'Enforcing runtime identity is required.')
    _require(isinstance(identity['sha256'], str) and len(identity['sha256']) == 64
             and all(x in '0123456789abcdef' for x in identity['sha256']), 'Invalid enforcing runtime hash.')
    _require({SCHEMA, RESULT_CONTRACT, LEDGER, BUDGET} <= set(_strings(identity['schemas'], 'schemas'))
             and CAPABILITIES <= set(_strings(identity['capabilities'], 'capabilities')),
             'This runtime cannot enforce the active result policy.', 'runtime_incompatible')
    return deepcopy(identity)


def enforce_runtime(pinned: dict[str, Any], current: dict[str, Any], *, quiescent: bool = False,
                    migration: dict[str, Any] | None = None,
                    policy_digest: str | None = None) -> dict[str, Any]:
    old, new = runtime_pin(pinned), runtime_pin(current)
    if old == new:
        return {'identity': new, 'migration': None}
    _require(quiescent is True and isinstance(migration, dict)
             and set(migration) == {'from', 'to', 'compatible_policy_digest', 'evidence'}
             and migration['from'] == digest(old) and migration['to'] == digest(new)
             and isinstance(migration['compatible_policy_digest'], str)
             and len(migration['compatible_policy_digest']) == 64
             and migration['compatible_policy_digest'] == policy_digest
             and bool(_strings(migration['evidence'], 'migration evidence')),
             'Runtime hot switch/downgrade requires explicit quiescent compatible migration.', 'runtime_incompatible')
    return {'identity': new, 'migration': deepcopy(migration), 'prior_identity': old}


def new_ledger(binding: dict[str, Any], definition: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    declared = contract(definition)
    assert declared is not None
    obligations = _obligations(declared)
    return {'schema': LEDGER, 'binding': deepcopy(binding), 'contract_digest': digest(declared),
            'criteria': {cid: {'state': 'unmet', 'owner': definition['id'], **row,
                               'contributions': [], 'remaining_action': 'Root verification required.'}
                         for cid, row in obligations.items()},
            'dependencies': {cid: row['depends_on'] for cid, row in obligations.items()},
            'contribution_refs': [], 'events': [], 'revision': 0,
            'budget_policy_digest': digest(declared.get('retry_policy', {'max_attempts': declared.get('max_attempts')})),
            'runtime_pin': runtime_pin(runtime)}


def assess(workspace: Path, definition: dict[str, Any], request: dict[str, Any]) -> dict[str, Any] | None:
    declared = contract(definition, required=False)
    outcome = request.get('outcome', 'complete')
    _require(isinstance(outcome, str) and outcome in {'complete', 'partial', 'incomplete', 'rejected'}, 'Invalid declared outcome.')
    if declared is None:
        return None if outcome == 'complete' else {'status': 'incomplete', 'missing_criteria': [],
            'missing_outputs': [], 'contract_digest': None, 'semantic_complete': False}
    supplied = set(_strings(request.get('outputs'), 'outputs', empty=True))
    _require(supplied <= set(definition['paths']), 'Result output exceeds task authority.')
    checks = request.get('checks')
    _require(isinstance(checks, list) and all(isinstance(c, dict) and isinstance(c.get('status'), str)
             and c['status'] in {'pass', 'fail', 'unknown'}
             and isinstance(c.get('name'), str) and bool(c['name']) and c.get('evidence') in definition['paths']
             for c in checks), 'Malformed verification checks.')
    assert isinstance(checks, list)
    verified = ({c['evidence'] for c in checks if c['status'] == 'pass'}
                - {c['evidence'] for c in checks if c['status'] != 'pass'})
    obligations = _obligations(declared)
    needed = set(declared['required_outputs']) | {p for row in obligations.values() for p in row['outputs'] + row['evidence']}
    missing = set()
    for path in sorted(needed):
        try:
            if not evidence.read(workspace, path):
                missing.add(path)
        except (w.Refusal, OSError):
            missing.add(path)
    reviews = request.get('semantic_review', {})
    _require(isinstance(reviews, dict) and set(reviews) <= set(obligations), 'Unknown semantic criterion.')
    satisfied = []
    for cid, row in obligations.items():
        review = reviews.get(cid, {})
        _require(isinstance(review, dict) and set(review) <= {'satisfied', 'reviewer', 'reason'}
                 and ('satisfied' not in review or type(review['satisfied']) is bool)
                 and all(isinstance(review[key], str) for key in ('reviewer', 'reason') if key in review),
                 'Invalid semantic review.')
        semantic = (review.get('satisfied') is True and isinstance(review.get('reviewer'), str)
                    and bool(review['reviewer']) and isinstance(review.get('reason'), str) and bool(review['reason'].strip()))
        if (semantic and set(row['outputs']) <= supplied and set(row['evidence']) <= verified
                and not missing.intersection(row['outputs'] + row['evidence'])):
            satisfied.append(cid)
    missing_outputs = sorted((set(declared['required_outputs']) - supplied) | missing)
    unmet = sorted(set(obligations) - set(satisfied))
    complete = outcome == 'complete' and not missing_outputs and not unmet and all(c['status'] == 'pass' for c in checks)
    return {'status': 'verified' if complete else 'incomplete', 'missing_outputs': missing_outputs,
            'missing_criteria': unmet, 'satisfied_criteria': sorted(satisfied), 'semantic_complete': complete,
            'contract_digest': digest(declared), 'assurance': 'Controller-authorized root review; native completion alone is insufficient.'}


def _transaction(path: Path, expected_generation: int, authorize: Callable[[dict[str, Any]], None],
                 update: Callable[[dict[str, Any]], Any], *, limit: int = 8 * 1024 * 1024,
                 locked: bool = False) -> dict[str, Any]:
    """Internal controller API, never exposed as a user supplied authorization.

    locked=True is solely for an in-process controller already holding the exact
    primitives.file_lock(str(path)) continuously from fresh read through CAS.
    It is not a request/CLI field. The lock is not reentrant: do not reacquire it.
    Both modes retain the same bounded fresh read, generation and authority checks.
    """
    _require(callable(authorize), 'Controller authorization validator is required.', 'permission_required')
    _integer(expected_generation, 'expected_generation')
    _integer(limit, 'controller byte limit', 1)
    _require(type(locked) is bool, 'Internal locked mode must be boolean.')
    path = Path(path)
    _require(not any(p.is_symlink() for p in (path, *path.parents)), 'Controller path cannot follow symlinks.')
    with nullcontext() if locked else primitives.file_lock(str(path)):
        # The controller owns path provisioning and its schema/binding validation.
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as stream:
            _require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), 'Controller must be a regular file.')
            raw = stream.read(limit + 1)
        _require(len(raw) <= limit, 'Controller exceeds its byte limit.')
        database = json.loads(raw)
        _require(isinstance(database, dict), 'Controller must be an object.')
        actual = storage.storage_generation(database)
        if actual != expected_generation:
            raise storage.StorageConflict(expected_generation, actual)
        # A validator may only inspect. Never persist a callback's incidental edits.
        authorize(deepcopy(database))
        proposed = deepcopy(database)
        result = update(proposed)
        generation = storage.commit_controller(path, proposed, expected_generation=expected_generation, locked=True, limit=limit)
        return {'storage_generation': generation, 'result': result}


def retain_result(path: Path, workspace: Path, *, expected_generation: int,
                  authorize: Callable[[dict[str, Any]], None], ledger_id: str,
                  definition: dict[str, Any], binding: dict[str, Any], attempt_id: str,
                  request: dict[str, Any], fingerprints: dict[str, dict[str, Any]],
                  current_fingerprints: Callable[[], dict[str, dict[str, Any]]],
                  runtime: dict[str, Any], controller_limit: int = 8 * 1024 * 1024,
                  locked: bool = False) -> dict[str, Any]:
    """Persist valid authorized partial/rejected evidence before returning it.

    ``database['result_ledgers'][ledger_id]`` is provisioned by the controller at
    initial opt-in admission, never retroactively for legacy attempts. The caller
    validates request authority/native lifecycle inside ``authorize``. Repeated
    identical requests are idempotent; conflicting repeats refuse unchanged.
    """
    return _transaction(path, expected_generation, authorize,
        lambda database: retain_result_state(workspace, database, ledger_id=ledger_id,
            definition=definition, binding=binding, attempt_id=attempt_id, request=request,
            fingerprints=fingerprints, current_fingerprints=current_fingerprints,
            runtime=runtime, expected_generation=expected_generation),
        limit=controller_limit, locked=locked)


def retain_result_state(workspace: Path, database: dict[str, Any], *, ledger_id: str,
                        definition: dict[str, Any], binding: dict[str, Any], attempt_id: str,
                        request: dict[str, Any], fingerprints: dict[str, dict[str, Any]],
                        current_fingerprints: Callable[[], dict[str, dict[str, Any]]],
                        runtime: dict[str, Any], expected_generation: int = 0) -> dict[str, Any]:
    """Internal locked-controller mutation; caller validates authority and commits.

    The host must persist this state before reporting an incomplete disposition.
    This seam cannot be selected by request fields and grants no worker authority.
    """
    _require(isinstance(attempt_id, str) and bool(attempt_id), 'Attempt identity is required.')
    _require(callable(current_fingerprints), 'Current source/dependency fingerprint revalidation is required.')
    _require(set(request) <= {'outcome', 'outputs', 'checks', 'semantic_review', 'side_effects', 'reason', 'detail'},
             'Unknown result request fields.')
    _require(isinstance(request.get('side_effects'), str)
             and request['side_effects'] in {'none_observed', 'reconciled', 'unknown'}, 'Side-effect disposition is required.')
    _require('reason' not in request or (isinstance(request['reason'], str) and request['reason'] in REASONS),
             'Invalid result reason.')
    _require('detail' not in request or (isinstance(request['detail'], str)
             and 8 <= len(request['detail'].strip()) <= 2048), 'Invalid result detail.')
    if request.get('outcome') == 'rejected':
        _require(request.get('reason') in REASONS and isinstance(request.get('detail'), str)
                 and 8 <= len(request['detail'].strip()) <= 2048, 'Rejection requires an actionable reason and detail.')
    assessment = assess(workspace, definition, request)
    _require(assessment is not None and assessment['contract_digest'] is not None, 'Durable contributions require prior opt-in.')
    assert assessment is not None
    declared = contract(definition)
    assert declared is not None
    _require(isinstance(fingerprints, dict) and set(fingerprints) == set(_obligations(declared))
             and all(isinstance(fp, dict) and fp.get('schema') == 'taskplane.contribution-key/v1'
                     and fp.get('digest') == digest({k: v for k, v in fp.items() if k != 'digest'})
                     for fp in fingerprints.values()),
             'Each criterion requires its complete or explicitly unknown fingerprint.')
    request_digest = digest({'request': request, 'fingerprints': fingerprints, 'binding': binding})

    def update(database: dict[str, Any]) -> dict[str, Any]:
        ledger = database.get('result_ledgers', {}).get(ledger_id)
        _require(isinstance(ledger, dict) and ledger.get('schema') == LEDGER and ledger['binding'] == binding
                 and ledger['contract_digest'] == assessment['contract_digest'], 'Result ledger binding or contract changed.')
        enforce_runtime(ledger['runtime_pin'], runtime)
        old = [event for event in ledger['events'] if event.get('attempt_id') == attempt_id and event['kind'] == 'result']
        if old:
            _require(len(old) == 1 and old[0]['request_digest'] == request_digest, 'Attempt result is immutable.')
            return deepcopy(old[0]['result'])
        current_assessment = assess(workspace, definition, request)
        assert current_assessment is not None
        current_keys = current_fingerprints()
        _require(isinstance(current_keys, dict) and set(current_keys) == set(fingerprints),
                 'Current fingerprints do not cover the result contract.')
        refreshed = refresh_residuals(ledger, Store(workspace), current_keys)
        ledger.clear()
        ledger.update(refreshed)
        artifacts, unavailable = {}, []
        # All invalid-request and authority checks precede immutable publication.
        store = Store(workspace)
        primitives._durable_makedirs(str(store.root / 'objects'), mode=0o700, sync_existing=True)
        for name in definition['paths']:
            try:
                raw = evidence.read(workspace, name)
            except (w.Refusal, OSError):
                unavailable.append(name)
                continue
            artifacts[name] = {'sha256': hashlib.sha256(raw).hexdigest(),
                               'reference': store.put('unaccepted-worker-artifact', {'path': name, **text_body(raw)})}
        refs = []
        for cid in ledger['criteria']:
            fp = fingerprints[cid]
            row = ledger['criteria'][cid]
            explicit_negative = (request.get('semantic_review', {}).get(cid, {}).get('satisfied') is False
                                 or any(check['status'] != 'pass' and check['evidence'] in row['evidence']
                                        for check in request['checks']))
            if explicit_negative and row['contributions']:
                row['invalidated_contributions'] = deepcopy(row['contributions'])
                row['state'] = 'invalidated'
            valid = (contribution_matches(fp, current_keys[cid])
                     and all(name in artifacts and artifacts[name]['sha256'] == value
                             for name, value in fp.get('outputs', {}).items()))
            accepted = cid in current_assessment['satisfied_criteria'] and valid
            # Explicit rejection retains useful evidence but never silently accepts it.
            disposition = ('rejected' if request.get('outcome') == 'rejected' else
                           'accepted' if accepted else 'partial')
            body = {'schema': CONTRIBUTION, 'binding': deepcopy(binding), 'attempt_id': attempt_id,
                    'contract_digest': ledger['contract_digest'],
                    'criterion_ids': [cid], 'artifacts_with_hashes': deepcopy(artifacts),
                    'fingerprint': deepcopy(fp), 'checks': deepcopy(request['checks']),
                    'semantic_review': deepcopy(request.get('semantic_review', {}).get(cid)),
                    'side_effect_disposition': request['side_effects'], 'disposition': disposition,
                    'input_dependency_hashes': deepcopy(fp.get('source_dependencies', {})),
                    'source_manifest': deepcopy(fp.get('closure_census', [])),
                    'test_manifest': deepcopy(fp.get('verification', {}).get('tests', [])),
                    'runtime_identity': deepcopy(runtime),
                    'environment_identity': fp.get('verification', {}).get('environment')}
            dependencies = set(ledger['criteria'][cid]['depends_on'] + ledger['criteria'][cid]['semantic_dependencies'])
            body['dependency_fingerprints'] = {dep: current_keys[dep].get('digest') for dep in sorted(dependencies)}
            ref = store.put('worker-contribution', body)
            refs.append(ref)
            ledger['criteria'][cid]['contributions'].append(ref)
            if accepted and disposition != 'rejected':
                ledger['criteria'][cid]['state'] = 'satisfied'
        refreshed = refresh_residuals(ledger, store, current_keys)
        ledger.clear()
        ledger.update(refreshed)
        # Flush immutable object directory entries before the controller refers
        # to them. Physical power-loss guarantees remain platform dependent.
        primitives._fsync_directory(str(store.root / 'objects'))
        # Unknown fingerprints are evidence, never completion authority.
        all_satisfied = all(row['state'] == 'satisfied' for row in ledger['criteria'].values())
        status = ('rejected' if request.get('outcome') == 'rejected' else
                  'verified' if current_assessment['status'] == 'verified' and all_satisfied else 'incomplete')
        result = {'status': status, 'result_accepted': False, 'attempt_id': attempt_id,
                  'assessment': current_assessment, 'contribution_refs': refs, 'artifacts': artifacts,
                  'unavailable': unavailable, 'side_effects': request['side_effects'],
                  'root_acceptance_required': True}
        ledger['contribution_refs'].extend(refs)
        ledger['revision'] += 1
        ledger['events'].append({'kind': 'result', 'attempt_id': attempt_id,
                                 'request_digest': request_digest, 'result': deepcopy(result),
                                 'request': deepcopy(request), 'expected_generation': expected_generation,
                                 'committed_generation': expected_generation + 1})
        return result
    return update(database)


def commit_budget_observation(path: Path, *, expected_generation: int,
                              authorize: Callable[[dict[str, Any]], None],
                              scope_ids: list[str], reservation_id: str | None,
                              state: str | None, proof: dict[str, Any],
                              observations: dict[str, dict[str, Any]], runtime: dict[str, Any],
                              controller_limit: int = 8 * 1024 * 1024, locked: bool = False) -> dict[str, Any]:
    """Commit issue/terminal/uncertain and usage for all selected scopes together.

    Before native issue use state='issued' and the actual host pre-call ID. An
    issue failure stays consumed until an authentic no-launch reconciliation.
    The controller validator must bind scope IDs, proof provenance and the call.
    """
    _strings(scope_ids, 'scope_ids')
    _require(set(observations) == set(scope_ids), 'Every scope needs a usage observation.')
    def update(database: dict[str, Any]) -> dict[str, Any]:
        result, prepared = {}, {}
        for scope_id in scope_ids:
            old = database['retry_budgets'][scope_id]
            enforce_runtime(old['runtime_pin'], runtime)
            args = observations[scope_id]
            _require(set(args) <= {'now_ms', 'usage', 'policy', 'reconcile_clock', 'reconcile_usage', 'original_authorized_ms'} and {'now_ms', 'usage'} <= set(args),
                     'Malformed budget observation.')
            current = observe_budget(old, **args)
            prepared[scope_id] = current
        issue_admitted = state != 'issued' or all(_issue_ready(current, reservation_id) for current in prepared.values())
        for scope_id, current in prepared.items():
            if state is not None and issue_admitted:
                _require(reservation_id is not None, 'A lifecycle observation requires a reservation identity.')
                assert reservation_id is not None
                current = reservation_transition(current, reservation_id, state=state, proof=proof)
            database['retry_budgets'][scope_id] = current
            result[scope_id] = {'elapsed_highwater': current['elapsed_highwater'],
                                'token_highwater': current['token_highwater'], 'overshoot': current['overshoot']}
        return {'issue_admitted': issue_admitted, 'scopes': result}
    return _transaction(path, expected_generation, authorize, update, limit=controller_limit, locked=locked)


def raise_for_result(committed: dict[str, Any]) -> None:
    """Call only after retain_result returns its successful durable CAS."""
    result = committed['result']
    _require(result['status'] == 'verified',
             'Authorized result retained; inspect its immutable contributions and unmet criteria.', 'worker_result_incomplete')


def refresh_residuals(ledger: dict[str, Any], store: Store,
                      current: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Pure projection plus explicit invalidation events; old evidence stays immutable."""
    _require(set(current) == set(ledger['criteria']), 'Current fingerprints must cover every criterion.')
    proposed = deepcopy(ledger)
    reusable = {}
    for cid, row in ledger['criteria'].items():
        valid_refs = []
        dependencies = set(row['depends_on'] + row['semantic_dependencies'])
        quarantined = {ref['sha256'] for ref in row.get('invalidated_contributions', [])}
        for ref in row['contributions']:
            body = store.resolve(ref)
            _require(body.get('schema') == CONTRIBUTION and body.get('binding') == ledger['binding']
                     and body.get('criterion_ids') == [cid] and body.get('contract_digest') == ledger['contract_digest'],
                     'Foreign contribution.')
            if (ref['sha256'] not in quarantined and body.get('disposition') == 'accepted'
                    and body['semantic_review'].get('satisfied') is True
                    and contribution_matches(body['fingerprint'], current[cid])
                    and body.get('dependency_fingerprints') == {dep: current[dep].get('digest') for dep in sorted(dependencies)}):
                valid_refs.append(ref)
        reusable[cid] = valid_refs
    # Semantic/evidence coupling propagates invalidation, including around an
    # SCC, but never becomes a cyclic execution schedule. A root may reverify a
    # semantic group together in one result. Old invalidated refs stay quarantined.
    bad = {cid for cid, refs in reusable.items() if not refs}
    children: dict[str, set[str]] = {cid: set() for cid in ledger['criteria']}
    for cid, row in ledger['criteria'].items():
        for dep in set(row['depends_on'] + row['semantic_dependencies']):
            children[dep].add(cid)
    pending = list(bad)
    while pending:
        for child in children[pending.pop()]:
            if child not in bad:
                bad.add(child)
                pending.append(child)
    invalid = {cid for cid in bad if ledger['criteria'][cid]['contributions']
               or ledger['criteria'][cid]['state'] in {'satisfied', 'invalidated'}}
    changed = []
    for cid, row in proposed['criteria'].items():
        row['reusable_contributions'] = [] if cid in invalid else reusable[cid]
        if cid in invalid:
            before = deepcopy(row)
            row['state'] = 'invalidated'
            row['invalidated_contributions'] = deepcopy(row['contributions'])
            row['remaining_action'] = 'Reverify changed inputs and affected dependencies.'
            if row != before:
                changed.append(cid)
        elif reusable[cid]:
            row['state'] = 'satisfied'
    if changed:
        proposed['revision'] += 1
        proposed['events'].append({'kind': 'invalidated', 'criteria': sorted(changed),
                                   'current_fingerprint_digests': {c: current[c].get('digest') for c in sorted(changed)}})
    return proposed


def schedule_residuals(ledger: dict[str, Any], *, predecessor: dict[str, Any] | None,
                       retry_plan: dict[str, Any] | None, authorization: dict[str, Any],
                       budget: dict[str, Any], fresh_identity: bool) -> list[str]:
    """Plan only; controller must revalidate all evidence atomically at admission.

    ``authorization`` is the validated controller projection, never worker input.
    Returning candidates grants no dispatch authority.
    """
    if (authorization.get('authorized') is not True or authorization.get('quiescent') is not True
            or any(authorization.get(k) is not False for k in ('denied', 'cancelled', 'paused', 'conflicting'))
            or budget.get('dispatch_admitted') is not True):
        return []
    if predecessor is not None:
        if (not fresh_identity or predecessor.get('terminal') is not True
                or predecessor.get('disposition') not in {'partial', 'incomplete', 'rejected', 'failed'}
                or predecessor.get('binding') != ledger['binding']
                or not primitives.is_sha256(predecessor.get('outcome_digest'))
                or not isinstance(retry_plan, dict)
                or set(retry_plan) != {'prior_attempt', 'prior_outcome_digest', 'remaining', 'side_effects', 'evidence'}
                or retry_plan['prior_attempt'] != predecessor.get('attempt_id')
                or retry_plan['prior_outcome_digest'] != predecessor['outcome_digest']
                or retry_plan['side_effects'] not in {'none_observed', 'reconciled'}
                or not retry_plan['evidence']):
            return []
        _strings(retry_plan['evidence'], 'retry evidence')
        remaining = set(_strings(retry_plan['remaining'], 'remaining'))
        _require(remaining <= set(ledger['criteria']), 'Retry plan names unknown criteria.')
    else:
        _require(retry_plan is None, 'Initial work cannot name a predecessor retry plan.')
        remaining = set(ledger['criteria'])
    return [cid for cid in _execution_order(ledger['dependencies'])
            if cid in remaining and ledger['criteria'][cid]['state'] in {'unmet', 'unknown', 'invalidated'}
            and all(ledger['criteria'][dep]['state'] == 'satisfied' for dep in ledger['dependencies'][cid])]


def new_budget(binding: dict[str, Any], policy: dict[str, Any], *, runtime: dict[str, Any]) -> dict[str, Any]:
    policy = validate_policy(policy)
    return {'schema': BUDGET, 'binding': deepcopy(binding), 'policy_history': [policy],
            'frozen_effective_limits': {k: policy[k] for k in policy if k != 'schema'},
            'runtime_pin': runtime_pin(runtime), 'first_authorized_ms': None, 'last_observed_ms': None,
            'authorization_started': False,
            'elapsed_highwater': 0, 'token_highwater': 0, 'clock_known': False, 'coverage_known': False,
            'clock_fault_active': False,
            'usage_fault_active': False,
            'reservations': {}, 'observations': [], 'attempts_consumed': 0, 'overshoot': []}


def observe_budget(budget: dict[str, Any], *, now_ms: int | None,
                   usage: dict[str, Any], policy: dict[str, Any] | None = None,
                   reconcile_clock: bool = False, reconcile_usage: bool = False,
                   original_authorized_ms: int | None = None) -> dict[str, Any]:
    """Cumulative usage is native input+output; cached input is a subset, once.

    Usage adapter must supply complete coverage of all owned intervals, including
    continuation cycles. Reset/unknown evidence is retained and blocks admission.
    Only an authoritative reconciliation may repair a backward-clock observation.
    """
    _require(budget.get('schema') == BUDGET, 'Unsupported retry budget.')
    _require(type(reconcile_clock) is bool and type(reconcile_usage) is bool, 'Reconciliation flags must be boolean.')
    result = deepcopy(budget)
    if policy is not None:
        validated = validate_policy(policy)
        if validated not in result['policy_history']:
            result['policy_history'].append(validated)
        for name, cap in validated.items():
            if name != 'schema':
                result['frozen_effective_limits'][name] = min(result['frozen_effective_limits'].get(name, cap), cap)
    if now_ms is not None:
        _integer(now_ms, 'authoritative now_ms')
    if original_authorized_ms is not None:
        _integer(original_authorized_ms, 'original_authorized_ms')
        _require(reconcile_clock is True and result['authorization_started'] is True
                 and now_ms is not None and original_authorized_ms <= now_ms,
                 'An original timestamp requires authoritative clock reconciliation.')
        previous = result['first_authorized_ms']
        _require(previous is None or original_authorized_ms <= previous, 'Reconciliation cannot move the start later.')
        result['first_authorized_ms'] = original_authorized_ms
    _require(isinstance(usage, dict) and set(usage) <= {'input', 'output', 'cached_input', 'coverage', 'source'},
             'Invalid usage observation.')
    counters = [usage.get(k) for k in ('input', 'output', 'cached_input')]
    known = usage.get('coverage') == 'complete' and all(type(n) is int and n >= 0 for n in counters)
    observed: int | None = None
    if known:
        numeric_counters = cast(list[int], counters)
        known = numeric_counters[2] <= numeric_counters[0]
        if known:
            observed = numeric_counters[0] + numeric_counters[1]
    reset = observed is not None and observed < result['token_highwater']
    result['usage_fault_active'] = reset or (result.get('usage_fault_active', False) and not reconcile_usage)
    result['coverage_known'] = bool(known and not result['usage_fault_active'])
    if observed is not None:
        result['token_highwater'] = max(result['token_highwater'], observed)
    last = result['last_observed_ms']
    regression = now_ms is not None and last is not None and now_ms < last
    missing_start = result['authorization_started'] and result['first_authorized_ms'] is None
    fault = now_ms is None or regression or missing_start
    result['clock_fault_active'] = fault or (result.get('clock_fault_active', False) and not reconcile_clock)
    result['clock_known'] = not result['clock_fault_active']
    if now_ms is not None:
        result['last_observed_ms'] = max(last if last is not None else now_ms, now_ms)
        start = result['first_authorized_ms']
        if start is not None:
            result['elapsed_highwater'] = max(result['elapsed_highwater'], max(0, now_ms - start))
    result['observations'].append({'now_ms': now_ms, 'usage': deepcopy(usage), 'counter_reset': reset,
                                   'clock_fault': fault, 'original_authorized_ms': original_authorized_ms,
                                   'authoritative_usage_reconciliation': reconcile_usage is True,
                                   'authoritative_clock_reconciliation': reconcile_clock is True})
    limits = result['frozen_effective_limits']
    excess = {'tokens': max(0, result['token_highwater'] - limits['token_cap']) if 'token_cap' in limits else 0,
              'elapsed_ms': max(0, result['elapsed_highwater'] - limits['elapsed_cap_ms']) if 'elapsed_cap_ms' in limits else 0}
    if any(excess.values()):
        result['overshoot'].append(excess)
    return result


def budget_admission(budget: dict[str, Any], *, tokens_proposed: int) -> dict[str, Any]:
    _integer(tokens_proposed, 'tokens_proposed')
    limits = budget['frozen_effective_limits']
    active = [r for r in budget['reservations'].values() if r['state'] in {'reserved', 'issued', 'uncertain'}]
    reserved_attempts = sum(r['state'] == 'reserved' for r in active)
    reserved_tokens = sum(r['tokens'] for r in active)
    result = {'attempt_cap': limits.get('attempt_cap'), 'attempts_consumed': budget['attempts_consumed'],
              'attempts_reserved': reserved_attempts, 'elapsed_ms': budget['elapsed_highwater'],
              'elapsed_cap_ms': limits.get('elapsed_cap_ms'), 'tokens_observed': budget['token_highwater'],
              'tokens_reserved': reserved_tokens, 'tokens_proposed': tokens_proposed,
              'token_cap': limits.get('token_cap'), 'coverage_known': budget['coverage_known'],
              'clock_known': budget['clock_known']}
    result['dispatch_admitted'] = bool(
        ('token_cap' not in limits or (result['coverage_known'] and tokens_proposed > 0
            and result['tokens_observed'] + reserved_tokens + tokens_proposed <= result['token_cap']))
        and ('elapsed_cap_ms' not in limits or (result['clock_known'] and result['elapsed_ms'] < result['elapsed_cap_ms']))
        and ('attempt_cap' not in limits or result['attempts_consumed'] + reserved_attempts + 1 <= result['attempt_cap']))
    return result


def reserve_dispatch(path: Path, *, expected_generation: int,
                     authorize: Callable[[dict[str, Any]], None],
                     scopes: dict[str, dict[str, Any]], reservation_id: str,
                     attempt_id: str, tokens_proposed: int, now_ms: int | None,
                     runtime: dict[str, Any], controller_limit: int = 8 * 1024 * 1024,
                     locked: bool = False) -> dict[str, Any]:
    """Atomically reserve the same attempt against ALL controller-selected scopes.

    Each scope carries binding, policy (None retains frozen caps), and authoritative
    cumulative usage. Scope selection is validated by authorize against the real
    controller; omitting a run/task/visit scope is not a supported escape hatch.
    Existing scopes must already be initialized at initial opt-in admission.
    A refusal to admit valid work still commits observations/highwater, never a
    launch. Malformed/unauthorized requests do not commit anything.
    """
    _require(bool(scopes) and isinstance(reservation_id, str) and bool(reservation_id)
             and isinstance(attempt_id, str) and bool(attempt_id), 'Reservation identity/scopes required.')
    _integer(tokens_proposed, 'tokens_proposed')

    def update(database: dict[str, Any]) -> dict[str, Any]:
        budgets = database.get('retry_budgets', {})
        proposed, decisions = {}, {}
        for scope_id, request in scopes.items():
            _require(set(request) == {'binding', 'policy', 'usage'}, 'Invalid budget scope request.')
            old = budgets.get(scope_id)
            _require(isinstance(old, dict) and old['binding'] == request['binding'], 'Budget scope/binding missing or changed.')
            enforce_runtime(old['runtime_pin'], runtime)
            _require(reservation_id not in old['reservations'], 'Reservation identity already exists; inspect it before reissuing.')
            _require(not any(r['attempt_id'] == attempt_id for r in old['reservations'].values()),
                     'An attempt cannot reserve a second launch.')
            current = observe_budget(old, now_ms=now_ms, usage=request['usage'], policy=request['policy'])
            decisions[scope_id] = budget_admission(current, tokens_proposed=tokens_proposed)
            proposed[scope_id] = current
        admitted = all(row['dispatch_admitted'] for row in decisions.values())
        for scope_id, current in proposed.items():
            if admitted:
                if not current['authorization_started']:
                    current['first_authorized_ms'] = now_ms
                    current['authorization_started'] = True
                current['reservations'][reservation_id] = {'attempt_id': attempt_id, 'tokens': tokens_proposed,
                    'state': 'reserved', 'issued_call': None, 'history': [{'state': 'reserved', 'now_ms': now_ms}]}
            budgets[scope_id] = current
        return {'dispatch_admitted': admitted, 'reservation_id': reservation_id,
                'scope_decisions': decisions, 'authority': 'controller-validation-required-at-issue'}
    return _transaction(path, expected_generation, authorize, update, limit=controller_limit, locked=locked)


def _issue_ready(budget: dict[str, Any], reservation_id: str | None) -> bool:
    row = budget['reservations'].get(reservation_id)
    if row is None or row['state'] != 'reserved':
        return False
    active = [r for r in budget['reservations'].values() if r['state'] in {'reserved', 'issued', 'uncertain'}]
    limits = budget['frozen_effective_limits']
    return bool(('elapsed_cap_ms' not in limits or (budget['clock_known'] and budget['elapsed_highwater'] < limits['elapsed_cap_ms']))
                and ('token_cap' not in limits or (budget['coverage_known'] and row['tokens'] > 0
                     and budget['token_highwater'] + sum(r['tokens'] for r in active) <= limits['token_cap']))
                and ('attempt_cap' not in limits or
                     budget['attempts_consumed'] + sum(r['state'] == 'reserved' for r in active) <= limits['attempt_cap']))


def reservation_transition(budget: dict[str, Any], reservation_id: str, *, state: str,
                           proof: dict[str, Any]) -> dict[str, Any]:
    """Apply controller-validated observation; caller atomically commits ALL scopes.

    issued must be durably committed BEFORE external dispatch. uncertain never
    refunds an attempt. Terminal release requires complete authoritative usage;
    no_launch requires authoritative proof that the call was never issued.
    """
    result = deepcopy(budget)
    row = result['reservations'].get(reservation_id)
    _require(row is not None and isinstance(proof, dict) and bool(proof.get('source')),
             'Reservation and authoritative observation reference are required.')
    allowed = {'reserved': {'issued', 'no_launch'}, 'issued': {'uncertain', 'terminal'},
               'uncertain': {'terminal', 'no_launch'}, 'terminal': set(), 'no_launch': set()}
    _require(state in allowed[row['state']], 'Invalid reservation lifecycle transition.')
    if state == 'issued':
        _require(isinstance(proof.get('call_id'), str) and bool(proof['call_id']), 'Exact issued call identity required.')
        row['issued_call'] = proof['call_id']
        result['attempts_consumed'] += 1
    elif state == 'terminal':
        _require(proof.get('terminal') is True and ('token_cap' not in result['frozen_effective_limits'] or
                 (proof.get('usage_complete') is True and result['coverage_known'] is True)),
                 'Token-limited terminal release requires complete native usage.')
    elif state == 'no_launch':
        _require(proof.get('authoritative_no_launch') is True, 'Timeout is not no-launch evidence.')
        if row['state'] == 'uncertain':
            result['attempts_consumed'] -= 1
    row['state'] = state
    row['history'].append({'state': state, 'proof': deepcopy(proof)})
    return result
