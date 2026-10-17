"""Results library integration with real CAS and immutable Store.

The disposable database exercises the internal transaction seam, not a native
permission grant. Full host/controller admission remains an integration gate.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest

from taskplane import depgraph, primitives, storage, worker_results as results, workflow as w
from taskplane.context import Store, digest
from taskplane.context_reuse import contribution_key


def runtime():
    return {'sha256': hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
            'schemas': [results.SCHEMA, results.RESULT_CONTRACT, results.LEDGER, results.BUDGET],
            'capabilities': sorted(results.CAPABILITIES)}


def policy(**changes):
    return {'schema': results.POLICY, 'attempt_cap': 3, 'elapsed_cap_ms': 1000, 'token_cap': 100, **changes}


def result_fixture(tmp_path):
    workspace = tmp_path / 'project'
    workspace.mkdir()
    paths = []
    for name in ('a', 'b', 'u'):
        for folder in ('pkg_' + name, 'tests_' + name, 'deps_' + name):
            (workspace / folder).mkdir()
        (workspace / f'deps_{name}/rules.py').write_text('value = 3\n')
        (workspace / f'pkg_{name}/app.py').write_text(f'from deps_{name}.rules import value\n')
        (workspace / f'tests_{name}/check.py').write_text(f'from pkg_{name}.app import value\nassert value == 3\n')
        (workspace / f'pkg_{name}/result.txt').write_text('verified result\n')
        paths += [f'pkg_{name}/app.py', f'pkg_{name}/result.txt', f'tests_{name}/check.py', f'deps_{name}/rules.py']
    obligations = {name.upper(): {'outputs': [f'pkg_{name}/result.txt'], 'evidence': [f'pkg_{name}/result.txt'],
                                 'depends_on': ['A'] if name == 'b' else [], 'semantic_dependencies': []}
                   for name in ('a', 'b', 'u')}
    definition = {'id': 'unit-results', 'criteria': ['A', 'B', 'U'], 'paths': paths,
                  'completion': {'schema': results.RESULT_CONTRACT, 'required_outputs': [f'pkg_{n}/result.txt' for n in ('a', 'b', 'u')],
                    'criterion_obligations': obligations, 'retry_policy': policy(),
                    'reuse_policy': {'schema': 'taskplane.contribution-reuse/v1', 'require_complete_fingerprints': True}}}
    binding = {'run': 'unit-run', 'visit': 'unit-visit', 'task': definition['id']}
    ledger = results.new_ledger(binding, definition, runtime())
    controller = tmp_path / 'controller.json'
    storage.commit_controller(controller, {'fixture_binding': binding, 'result_ledgers': {'result': ledger}}, expected_generation=0)
    request = {'outcome': 'complete', 'outputs': definition['completion']['required_outputs'],
               'checks': [{'name': 'read ' + name, 'evidence': f'pkg_{name}/result.txt', 'status': 'pass'} for name in ('a', 'b', 'u')],
               'semantic_review': {cid: {'satisfied': True, 'reviewer': 'unit-root', 'reason': 'Expected answer and criterion reviewed.'}
                                   for cid in ('A', 'B', 'U')}, 'side_effects': 'none_observed'}
    return workspace, definition, binding, controller, request


def fingerprints(workspace, definition):
    depgraph.scan(str(workspace), decompose=True, strict=True)
    return {name.upper(): contribution_key(workspace, paths=[f'pkg_{name}/app.py'],
                tests=[f'tests_{name}/check.py'], outputs=[f'pkg_{name}/result.txt'],
                criteria={name.upper(): definition['completion']['criterion_obligations'][name.upper()]},
                command=[sys.executable, '-c', f'exec(open("tests_{name}/check.py").read())'],
                tool='unit-python', contract=results.RESULT_CONTRACT)
            for name in ('a', 'b', 'u')}


def submit(fixture, attempt='attempt-1', *, supplied=None, current=None, authorize=None, request=None, **kwargs):
    workspace, definition, binding, controller, original = fixture
    db = json.loads(controller.read_bytes())
    def validate(database):
        assert database['fixture_binding'] == binding
    return results.retain_result(controller, workspace, expected_generation=db['storage_generation'],
        authorize=authorize or validate, ledger_id='result', definition=definition, binding=binding,
        attempt_id=attempt, request=request or original, fingerprints=supplied or fingerprints(workspace, definition),
        current_fingerprints=current or (lambda: fingerprints(workspace, definition)), runtime=runtime(), **kwargs)


@pytest.mark.parametrize('case', ['complete', 'missing', 'empty', 'semantic', 'failed_check'])
def test_result_completeness_requires_artifacts_checks_and_semantic_review(tmp_path, case):
    fixture = result_fixture(tmp_path)
    workspace, definition, _, _, request = fixture
    if case == 'missing': (workspace / 'pkg_a/result.txt').unlink()
    if case == 'empty': (workspace / 'pkg_a/result.txt').write_text('')
    if case == 'semantic': request['semantic_review']['A']['satisfied'] = False
    if case == 'failed_check': request['checks'][0]['status'] = 'fail'
    assessment = results.assess(workspace, definition, request)
    assert (assessment['status'] == 'verified') == (case == 'complete')
    assert assessment['semantic_complete'] == (case == 'complete')
    request['semantic_review'] = {}
    assert results.assess(workspace, definition, request)['status'] == 'incomplete'


@pytest.mark.parametrize('outcome', ['partial', 'incomplete', 'rejected'])
def test_durable_partial_rejected_evidence_precedes_structured_response(tmp_path, outcome):
    fixture = result_fixture(tmp_path)
    workspace, _, _, controller, request = fixture
    request.update(outcome=outcome, reason='partial', detail='Criterion needs more verification.')
    request['checks'][0]['status'] = 'fail'
    committed = submit(fixture)
    saved_controller = controller.read_bytes()
    result = committed['result']
    original = (workspace / 'pkg_a/result.txt').read_bytes()
    (workspace / 'pkg_a/result.txt').write_text('replacement after rejection')
    stored = Store(workspace).resolve(result['artifacts']['pkg_a/result.txt']['reference'])
    assert stored['text'] == original.decode()
    assert result['result_accepted'] is False and result['root_acceptance_required'] is True
    assert result['side_effects'] == 'none_observed'
    refs = result['contribution_refs']
    bodies = [Store(workspace).resolve(ref) for ref in refs]
    assert all(body['checks'][0]['status'] == 'fail' for body in bodies)
    with pytest.raises(w.Refusal): results.raise_for_result(committed)
    assert controller.read_bytes() == saved_controller
    assert json.loads(saved_controller)['result_ledgers']['result']['events'][-1]['kind'] == 'result'


@pytest.mark.parametrize('case', ['unknown_field', 'foreign_output', 'bad_side_effects', 'bad_semantic_type',
                                 'unauthorized', 'stale_generation'])
def test_invalid_requests_do_not_mutate_controller_or_publish_objects(tmp_path, case):
    fixture = result_fixture(tmp_path)
    workspace, _, _, controller, request = fixture
    before = controller.read_bytes()
    def denied(database):
        raise w.Refusal('permission_required', 'Unit-test validator refuses this request.')
    if case == 'unknown_field': request['grant_authority'] = True
    if case == 'foreign_output': request['outputs'] = ['foreign.txt']
    if case == 'bad_side_effects': request['side_effects'] = True
    if case == 'bad_semantic_type': request['semantic_review']['A']['satisfied'] = 'yes'
    with pytest.raises((w.Refusal, storage.StorageConflict)):
        if case == 'stale_generation':
            results._transaction(controller, 0, denied, lambda db: db.update(bad=True))
        else: submit(fixture, authorize=denied if case == 'unauthorized' else None)
    assert controller.read_bytes() == before
    assert not (workspace / '.taskplane/context-v1/objects').exists()


def test_controller_size_limit_refuses_unchanged(tmp_path):
    fixture = result_fixture(tmp_path)
    controller = fixture[3]
    before = controller.read_bytes()
    with pytest.raises(w.Refusal): submit(fixture, controller_limit=len(before) - 1)
    assert controller.read_bytes() == before
    with pytest.raises(ValueError):
        results._transaction(controller, 1, lambda db: None,
                             lambda db: db.update(extra='x' * 4096), limit=len(before) + 2)
    assert controller.read_bytes() == before


def test_already_held_controller_lock_retains_generation_authority_and_size_checks(tmp_path):
    fixture = result_fixture(tmp_path)
    controller = fixture[3]
    seen = []
    with primitives.file_lock(str(controller)):
        before = controller.read_bytes()
        committed = results._transaction(controller, 1, lambda db: seen.append(db['storage_generation']),
                                         lambda db: db.update(observed='fixture'), locked=True)
        assert committed['storage_generation'] == 2 and seen == [1]
        current = controller.read_bytes()
        with pytest.raises(storage.StorageConflict):
            results._transaction(controller, 1, lambda db: None, lambda db: None, locked=True)
        with pytest.raises(w.Refusal):
            results._transaction(controller, 2, lambda db: None, lambda db: None, locked=True, limit=1)
        assert controller.read_bytes() == current and current != before


@pytest.mark.parametrize('unknown', [False, True])
def test_prior_satisfaction_cannot_mask_changed_or_unknown_current_fingerprint(tmp_path, unknown):
    fixture = result_fixture(tmp_path)
    first = submit(fixture)
    assert first['result']['status'] == 'verified'
    old = fingerprints(fixture[0], fixture[1])
    changed = deepcopy(old)
    if unknown:
        changed['A']['eligible'] = False
        changed['A']['coverage'] = 'unknown'
        changed['A']['digest'] = digest({k: v for k, v in changed['A'].items() if k != 'digest'})
    else:
        (fixture[0] / 'pkg_a/app.py').write_text('value = 4\n')
        changed = fingerprints(fixture[0], fixture[1])
    second = submit(fixture, 'attempt-2', supplied=old, current=lambda: changed)
    assert second['result']['status'] == 'incomplete'
    ledger = json.loads(fixture[3].read_bytes())['result_ledgers']['result']
    assert ledger['criteria']['A']['state'] == 'invalidated'
    assert ledger['criteria']['B']['state'] == 'invalidated'
    assert ledger['criteria']['U']['state'] == 'satisfied'


@pytest.mark.parametrize('value', [None, {'owner': 'opaque'}, {'schema': 'other/v99'}, 'legacy metadata'])
def test_absent_opaque_legacy_contract_remains_unchanged(value):
    definition = {'id': 'legacy', 'paths': [], 'criteria': [], 'completion': value}
    before = deepcopy(definition)
    assert results.contract(definition, required=False) is None
    assert results.policy_for_definition(definition) is None
    assert definition == before


@pytest.mark.parametrize('schema', ['taskplane.worker-completion/v0', 'taskplane.worker-result-contract/v2'])
def test_unknown_reserved_schema_refuses(schema):
    with pytest.raises(w.Refusal): results.contract({'completion': {'schema': schema}}, required=False)


def test_supported_completion_version_validates_and_only_freezes_attempt_cap():
    definition = {'id': 'old-opt-in', 'paths': ['result'], 'criteria': ['A'], 'completion': {
        'schema': results.SCHEMA, 'required_outputs': ['result'], 'criteria_evidence': {'A': ['result']}, 'max_attempts': 2}}
    assert results.contract(definition) == definition['completion']
    assert results.policy_for_definition(definition) == {'schema': results.POLICY, 'attempt_cap': 2}
    definition['completion']['max_attempts'] = True
    with pytest.raises(w.Refusal): results.contract(definition)


def test_runtime_pin_refuses_downgrade_hot_switch_and_unapproved_migration():
    old = runtime(); new = {**old, 'sha256': 'b' * 64}
    with pytest.raises(w.Refusal): results.enforce_runtime(old, new)
    weak = {**new, 'capabilities': ['durable-results']}
    with pytest.raises(w.Refusal): results.enforce_runtime(old, weak, quiescent=True)
    plan = {'from': digest(old), 'to': digest(new), 'compatible_policy_digest': 'c' * 64, 'evidence': ['explicit migration review']}
    with pytest.raises(w.Refusal): results.enforce_runtime(old, new, migration=plan, policy_digest='c' * 64)
    with pytest.raises(w.Refusal): results.enforce_runtime(old, new, migration=plan, quiescent=True, policy_digest='d' * 64)
    migrated = results.enforce_runtime(old, new, migration=plan, quiescent=True, policy_digest='c' * 64)
    assert migrated['prior_identity'] == old and migrated['migration'] == plan
