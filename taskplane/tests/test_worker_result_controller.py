"""Public controller integration; native observations here are synthetic fixtures."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from taskplane import depgraph, worker_results as results, worker_runtime as workers, workflow as w
from taskplane.context import Store, digest
from taskplane.tests.test_worker_runtime import reserve, launch, consume
from taskplane import workflow_host as h


def configured(tmp_path, *, native=True, cap=2, token_cap=None, elapsed_cap_ms=None, run_cap=None):
    definition = {'id': 'T0', 'phase': 'product', 'owner': 'planned-native/reviewer',
                  'dependencies': [], 'criteria': ['AC']}
    (tmp_path / 'input.py').write_text('value = 1\n')
    scope = {'criteria': ['AC'], 'paths': {phase: [phase + '.json'] for phase in w.PHASES},
             'verification_inputs': ['input.py', 'pkg/app.py', 'tests/check.py']}
    scope['paths']['product'] += ['T0.md', 'evidence.md', 'pkg/app.py']
    if run_cap is not None:
        scope['retry_policy'] = {'schema': results.POLICY, 'attempt_cap': run_cap}
    if not native:
        definition.update(owner='root')
    definition.update(paths=['T0.md', 'evidence.md'])
    (tmp_path / 'pkg').mkdir(); (tmp_path / 'tests').mkdir()
    (tmp_path / 'pkg/app.py').write_text('value = 3\n')
    (tmp_path / 'tests/check.py').write_text('from pkg.app import value\nassert value == 3\n')
    definition['completion'] = {'schema': results.RESULT_CONTRACT, 'required_outputs': definition['paths'],
        'criterion_obligations': {'AC': {'outputs': ['T0.md'], 'evidence': ['evidence.md'],
                                       'depends_on': [], 'semantic_dependencies': []}},
        'reuse_policy': {'schema': 'taskplane.contribution-reuse/v1', 'require_complete_fingerprints': True},
        'retry_policy': {'schema': results.POLICY, 'attempt_cap': cap,
                        **({'token_cap': token_cap} if token_cap is not None else {}),
                        **({'elapsed_cap_ms': elapsed_cap_ms} if elapsed_cap_ms is not None else {})}}
    definition['verification'] = {'schema': 'taskplane.verification-strategy/v1', 'checks': [{
        'task': 'T0', 'criteria': ['AC'], 'source_inputs': ['pkg/app.py'], 'test_inputs': ['tests/check.py'],
        'command': [sys.executable, '-m', 'pytest', 'tests/check.py']}]}
    (tmp_path / 'tasks.json').write_text(json.dumps({'tasks': [definition]}))
    c = h.Controller(tmp_path, 'root', h.installed_adapter('codex'))
    state = c.start({'scope': scope, 'entry': 'product', 'standalone': True,
                     'tasks': 'tasks.json', 'request_reference': 'fixture/request'})
    return c, deepcopy(state)


def output_request(tmp_path, *, complete=True):
    (tmp_path / 'T0.md').write_bytes(b'Useful retained output\n')
    if complete:
        (tmp_path / 'evidence.md').write_text('Verification evidence\n')
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    return {'outputs': ['T0.md', 'evidence.md'] if complete else ['T0.md'],
            'checks': [{'name': 'fixture', 'status': 'pass', 'evidence': 'evidence.md'}] if complete else [],
            'semantic_review': {'AC': {'satisfied': complete, 'reviewer': 'root', 'reason': 'Root reviewed the criterion.'}},
            'side_effects': 'none_observed'}


def finish(c, state, prepared):
    launch(c, state, prepared)
    consume(c, state, prepared['grant'], 'native-0')
    c.observe({'hook_event_name': 'PostToolUse', 'session_id': 'root', 'call_id': 'terminal',
               'tool_name': 'collaboration.list_agents', 'tool_input': {},
               'tool_response': {'agents': [{'agent_id': 'native-0', 'status': 'completed'}]}}, state['run'])


@pytest.mark.parametrize('native', [False, True])
def test_controller_retains_incomplete_opted_in_result_without_accepting(tmp_path, native):
    c, state = configured(tmp_path, native=native)
    prepared = reserve(c, state) if native else None
    if prepared:
        finish(c, state, prepared)
    request = output_request(tmp_path, complete=False)
    result = c.worker(state['run'], 'accept-result', revision=state['revision'], task='T0',
        grant=prepared['grant']['grant_id'] if prepared else '', request=request)
    after = c.report()
    assert result['status'] == 'incomplete' and not result['accepted'] and not result['result_accepted']
    assert 'T0' not in after.get('task_results', {})
    assert after['result_ledgers']['T0']['events'][-1]['kind'] == 'result'
    assert Store(tmp_path).resolve(result['artifacts']['T0.md']['reference'])['text'] == 'Useful retained output\n'
    assert c.worker(state['run'], 'accept-result', revision=state['revision'], task='T0',
        grant=prepared['grant']['grant_id'] if prepared else '', request=request)['contribution_refs'] == result['contribution_refs']


@pytest.mark.parametrize('native', [False, True])
def test_controller_accepts_complete_current_semantically_reviewed_result(tmp_path, native):
    c, state = configured(tmp_path, native=native)
    prepared = reserve(c, state) if native else None
    if prepared:
        finish(c, state, prepared)
    result = c.worker(state['run'], 'accept-result', revision=state['revision'], task='T0',
        grant=prepared['grant']['grant_id'] if prepared else '', request=output_request(tmp_path))
    fp = workers._result_fingerprints(tmp_path, c.report(), workers.task(c.report(), 'T0'))['AC']
    assert result.get('accepted_at'), {key: fp.get(key) for key in ('coverage', 'eligible', 'verification', 'outputs')}
    assert c.report()['result_ledgers']['T0']['criteria']['AC']['state'] == 'satisfied'


@pytest.mark.parametrize('cap', [1, 2])
def test_controller_retry_needs_exact_plan_fresh_identity_and_frozen_cap(tmp_path, cap):
    c, state = configured(tmp_path, cap=cap)
    first = reserve(c, state)
    finish(c, state, first)
    result = c.worker(state['run'], 'accept-result', revision=state['revision'], task='T0',
        grant=first['grant']['grant_id'], request=output_request(tmp_path, complete=False))
    plan = {'prior_attempt': first['grant']['grant_id'], 'prior_outcome_digest': result['outcome_digest'],
            'remaining': ['AC'], 'side_effects': 'none_observed', 'evidence': ['T0.md']}
    before = c.report()['retry_budgets']['task/T0']['attempts_consumed']
    assert before == 1
    if cap == 1:
        with pytest.raises(workers.BudgetRefusal): reserve(c, state, retry_plan=plan)
        assert len(c.report()['workers']) == 1
    else:
        with pytest.raises(w.Refusal): reserve(c, state)
        with pytest.raises(w.Refusal): reserve(c, state, retry_plan=plan, worker_id='native-0')
        second = reserve(c, state, retry_plan=plan)
        assert second['grant']['residual_criteria'] == ['AC']
        assert second['grant']['worker_id'] is None
        assert c.report()['retry_budgets']['task/T0']['attempts_consumed'] == 1
        launch(c, state, second, 'native-1')
        assert c.report()['retry_budgets']['task/T0']['attempts_consumed'] == 2


def test_token_scope_unknown_native_usage_prevents_reissue_and_persists_observation(tmp_path):
    c, state = configured(tmp_path, token_cap=100)
    first = reserve(c, state, tokens_proposed=50)
    finish(c, state, first)
    result = c.worker(state['run'], 'accept-result', revision=state['revision'], task='T0',
        grant=first['grant']['grant_id'], request=output_request(tmp_path, complete=False))
    plan = {'prior_attempt': first['grant']['grant_id'], 'prior_outcome_digest': result['outcome_digest'],
            'remaining': ['AC'], 'side_effects': 'none_observed', 'evidence': ['T0.md']}
    with pytest.raises(workers.BudgetRefusal): reserve(c, state, retry_plan=plan, tokens_proposed=50)
    budget = c.report()['retry_budgets']['task/T0']
    assert not budget['coverage_known']
    assert budget['reservations'][first['grant']['grant_id']]['state'] == 'issued'
    assert budget['attempts_consumed'] == 1


def test_native_issue_rechecks_elapsed_limit_and_keeps_reserved_attempt(tmp_path):
    c, state = configured(tmp_path, elapsed_cap_ms=1000)
    first = reserve(c, state)
    db = c._read(c._path()); budget = db['runs'][state['run']]['retry_budgets']['task/T0']
    budget['first_authorized_ms'] -= 1001
    c._write(c._path(), db)
    with pytest.raises(workers.BudgetRefusal): launch(c, state, first)
    after = c.report()
    assert after['workers'][first['grant']['grant_id']]['call_id'] is None
    assert after['retry_budgets']['task/T0']['elapsed_highwater'] >= 1000
    assert after['retry_budgets']['task/T0']['attempts_consumed'] == 0


def test_legacy_opted_in_native_attempt_cannot_gain_retroactive_contract(tmp_path):
    c, state = configured(tmp_path)
    first = reserve(c, state)
    finish(c, state, first)
    db = c._read(c._path()); db['runs'][state['run']].pop('result_ledgers')
    c._write(c._path(), db)
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        c.worker(state['run'], 'accept-result', revision=state['revision'], task='T0',
                 grant=first['grant']['grant_id'], request=output_request(tmp_path))
    assert c._path().read_bytes() == before


@pytest.mark.parametrize('cap', [1, 2])
def test_continuation_uses_same_frozen_budget_and_native_call_identity(tmp_path, cap):
    c, state = configured(tmp_path, cap=cap)
    first = reserve(c, state)
    launch(c, state, first)
    consume(c, state, first['grant'], 'native-0')
    event = {'hook_event_name': 'PreToolUse', 'session_id': 'root', 'call_id': 'continue-call',
             'tool_name': 'collaboration.send_message',
             'tool_input': {'target': 'native-0', 'message': 'Continue the remaining verification.'}}
    if cap == 1:
        with pytest.raises(workers.BudgetRefusal): c.guard(event, state['run'])
    else:
        c.guard(event, state['run'])
        c.guard(event, state['run'])
    after = c.report()['retry_budgets']['task/T0']
    assert after['attempts_consumed'] == cap
    assert len(after['reservations']) == cap
    if cap == 2:
        assert after['reservations'][first['grant']['grant_id'] + '/continue-call']['issued_call'] == 'continue-call'


def test_native_retry_cannot_bind_previous_child_even_with_fresh_request(tmp_path):
    c, state = configured(tmp_path)
    first = reserve(c, state)
    finish(c, state, first)
    result = c.worker(state['run'], 'accept-result', revision=state['revision'], task='T0',
        grant=first['grant']['grant_id'], request=output_request(tmp_path, complete=False))
    second = reserve(c, state, retry_plan={'prior_attempt': first['grant']['grant_id'],
        'prior_outcome_digest': result['outcome_digest'], 'remaining': ['AC'],
        'side_effects': 'none_observed', 'evidence': ['T0.md']})
    launch(c, state, second, child='native-0')
    row = c.report()['workers'][second['grant']['grant_id']]
    assert row['state'] == 'unknown' and row['identity_conflict'] and not row.get('worker_id')
    assert c.report()['retry_budgets']['task/T0']['attempts_consumed'] == 2


def test_complete_but_unknown_dependency_coverage_retains_without_acceptance(tmp_path):
    c, state = configured(tmp_path, native=False)
    request = output_request(tmp_path)
    (tmp_path / 'pkg/app.py').write_text('import unpinned_external_package\n')
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    result = c.worker(state['run'], 'accept-result', revision=state['revision'], task='T0', request=request)
    current = c.report()
    assert result['status'] == 'incomplete'
    assert not result['result_accepted'] and 'T0' not in current.get('task_results', {})


def test_accepted_contribution_invalidation_requires_new_exact_residual_attempt(tmp_path):
    c, state = configured(tmp_path)
    first = reserve(c, state)
    finish(c, state, first)
    c.worker(state['run'], 'accept-result', revision=state['revision'], task='T0',
        grant=first['grant']['grant_id'], request=output_request(tmp_path))
    current = c.report()
    original = deepcopy(current['result_ledgers']['T0']['contribution_refs'])
    (tmp_path / 'T0.md').write_text('Changed after root acceptance.\n')
    depgraph.scan(str(tmp_path), decompose=True, strict=True)
    prior = current['workers'][first['grant']['grant_id']]['result_disposition']
    second = reserve(c, state, retry_plan={'prior_attempt': first['grant']['grant_id'],
        'prior_outcome_digest': digest(prior), 'remaining': ['AC'],
        'side_effects': 'reconciled', 'evidence': ['T0.md']})
    ledger = c.report()['result_ledgers']['T0']
    assert second['grant']['residual_criteria'] == ['AC']
    assert ledger['criteria']['AC']['state'] == 'invalidated'
    assert ledger['contribution_refs'] == original
    assert all(Store(tmp_path).resolve(ref)['disposition'] == 'accepted' for ref in original)


def test_controller_selectively_invalidates_dependency_contributions_before_retry(tmp_path):
    from taskplane.tests.test_worker_results import result_fixture
    workspace, definition, _, _, request = result_fixture(tmp_path)
    (workspace / 'input.py').write_text('# Parent automatic hook fixture.\n')
    definition.update(phase='product', owner='planned-native/reviewer', dependencies=[])
    definition['completion']['retry_policy'] = {'schema': results.POLICY, 'attempt_cap': 3}
    definition['verification'] = {'schema': 'taskplane.verification-strategy/v1', 'checks': [
        {'task': definition['id'], 'criteria': [name.upper()], 'source_inputs': [f'pkg_{name}/app.py'],
         'test_inputs': [f'tests_{name}/check.py'], 'command': [sys.executable, '-c',
             f'exec(open("tests_{name}/check.py").read())']} for name in ('a', 'b', 'u')]}
    scope = {'criteria': ['A', 'B', 'U'], 'paths': {phase: [phase + '.json'] for phase in w.PHASES},
             'verification_inputs': ['input.py']}
    scope['paths']['product'] += definition['paths']
    (workspace / 'tasks.json').write_text(json.dumps({'tasks': [definition]}))
    c = h.Controller(workspace, 'root', h.installed_adapter('codex'))
    state = c.start({'scope': scope, 'entry': 'product', 'standalone': True,
                     'tasks': 'tasks.json', 'request_reference': 'fixture/selective-residuals'})
    first = reserve(c, state, task=definition['id'])
    assert set(first['grant']['residual_criteria']) == {'A', 'U'}
    finish(c, state, first)
    request['semantic_review']['B']['satisfied'] = False
    request['checks'][1]['status'] = 'fail'
    depgraph.scan(str(workspace), decompose=True, strict=True)
    partial = c.worker(state['run'], 'accept-result', revision=state['revision'], task=definition['id'],
                       grant=first['grant']['grant_id'], request=request)
    ledger = c.report()['result_ledgers'][definition['id']]
    assert ledger['criteria']['A']['state'] == ledger['criteria']['U']['state'] == 'satisfied'
    preserved = deepcopy(ledger['criteria']['U']['contributions'])
    (workspace / 'deps_a/rules.py').write_text('value = 4\n')
    depgraph.scan(str(workspace), decompose=True, strict=True)
    second = reserve(c, state, task=definition['id'], retry_plan={
        'prior_attempt': first['grant']['grant_id'], 'prior_outcome_digest': partial['outcome_digest'],
        'remaining': ['A', 'B'], 'side_effects': 'reconciled', 'evidence': ['pkg_a/result.txt']})
    ledger = c.report()['result_ledgers'][definition['id']]
    assert ledger['criteria']['A']['state'] == ledger['criteria']['B']['state'] == 'invalidated'
    assert ledger['criteria']['U']['state'] == 'satisfied'
    assert ledger['criteria']['U']['contributions'] == preserved
    assert second['grant']['residual_criteria'] == ['A']



def test_run_and_task_scopes_reserve_atomically_and_run_limit_cannot_be_omitted(tmp_path):
    c, state = configured(tmp_path, run_cap=1, cap=3)
    first = reserve(c, state)
    finish(c, state, first)
    result = c.worker(state['run'], 'accept-result', revision=state['revision'], task='T0',
        grant=first['grant']['grant_id'], request=output_request(tmp_path, complete=False))
    before = c.report()['retry_budgets']
    assert set(before) == {'run', 'task/T0'}
    assert all(budget['attempts_consumed'] == 1 for budget in before.values())
    with pytest.raises(workers.BudgetRefusal):
        reserve(c, state, budget_scopes=['task/T0'], retry_plan={
            'prior_attempt': first['grant']['grant_id'], 'prior_outcome_digest': result['outcome_digest'],
            'remaining': ['AC'], 'side_effects': 'none_observed', 'evidence': ['T0.md']})
    after = c.report()['retry_budgets']
    assert all(list(budget['reservations']) == [first['grant']['grant_id']] for budget in after.values())
    assert len(c.report()['workers']) == 1



@pytest.mark.parametrize('replacement', [None, {'schema': 'unrelated/opaque'}])
def test_root_cannot_disable_previously_admitted_completion_contract(tmp_path, replacement):
    c, state = configured(tmp_path, native=False)
    c.worker(state['run'], 'accept-result', revision=state['revision'], task='T0',
             request=output_request(tmp_path, complete=False))
    db = c._read(c._path())
    db['runs'][state['run']]['initial_context_tasks'][0]['completion'] = replacement
    c._write(c._path(), db)
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal, match='cannot be removed'):
        c.worker(state['run'], 'accept-result', revision=state['revision'], task='T0',
                 request=output_request(tmp_path))
    assert c._path().read_bytes() == before
    assert 'T0' not in c.report().get('task_results', {})



@pytest.mark.parametrize('scope', ['run', 'visit'])
def test_removing_policy_metadata_cannot_remove_frozen_run_or_visit_scope(tmp_path, scope):
    c, state = configured(tmp_path, cap=3, run_cap=1 if scope == 'run' else None)
    if scope == 'visit':
        db = c._read(c._path())
        db['runs'][state['run']]['visits'][0]['retry_policy'] = {'schema': results.POLICY, 'attempt_cap': 1}
        c._write(c._path(), db)
    first = reserve(c, state)
    finish(c, state, first)
    result = c.worker(state['run'], 'accept-result', revision=state['revision'], task='T0',
        grant=first['grant']['grant_id'], request=output_request(tmp_path, complete=False))
    db = c._read(c._path()); current = db['runs'][state['run']]
    (current['scope'] if scope == 'run' else current['visits'][0]).pop('retry_policy')
    c._write(c._path(), db)
    with pytest.raises(workers.BudgetRefusal):
        reserve(c, state, retry_plan={'prior_attempt': first['grant']['grant_id'],
            'prior_outcome_digest': result['outcome_digest'], 'remaining': ['AC'],
            'side_effects': 'none_observed', 'evidence': ['T0.md']})
    after = c.report()
    scope_id = 'run' if scope == 'run' else 'visit/' + state['visits'][0]['id']
    assert scope_id in after['result_ledgers']['T0']['budget_scopes']
    assert after['retry_budgets'][scope_id]['frozen_effective_limits']['attempt_cap'] == 1
    assert len(after['workers']) == 1



def test_public_reject_result_retains_evidence_and_allows_only_exact_residual_retry(tmp_path):
    c, state = configured(tmp_path)
    first = reserve(c, state)
    finish(c, state, first)
    output_request(tmp_path, complete=False)
    request = {'request_reference': 'fixture/root-review', 'reason': 'Required evidence remains absent.'}
    result = c.worker(state['run'], 'reject-result', revision=state['revision'], task='T0',
                      grant=first['grant']['grant_id'], request=request)
    assert result['status'] == 'rejected' and not result['accepted']
    assert result['contribution_refs'] and result['outcome_digest']
    assert Store(tmp_path).resolve(result['artifacts']['T0.md']['reference'])['text'] == 'Useful retained output\n'
    before = c._path().read_bytes()
    assert c.worker(state['run'], 'reject-result', revision=state['revision'], task='T0',
                    grant=first['grant']['grant_id'], request=request) == result
    assert c._path().read_bytes() == before
    second = reserve(c, state, retry_plan={'prior_attempt': first['grant']['grant_id'],
        'prior_outcome_digest': result['outcome_digest'], 'remaining': ['AC'],
        'side_effects': 'reconciled', 'evidence': ['T0.md']})
    assert second['grant']['attempt'] == 2 and second['grant']['residual_criteria'] == ['AC']
