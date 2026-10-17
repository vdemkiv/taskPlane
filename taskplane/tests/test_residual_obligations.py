"""Dependency-aware reuse and scheduling, with separate semantic review."""
from copy import deepcopy
import json

import pytest

from taskplane import worker_results as results, workflow as w
from taskplane.context import Store, digest
from taskplane.tests.test_worker_results import result_fixture, submit, fingerprints, runtime


def test_repeated_invalidation_never_revives_dependants_without_new_evidence(tmp_path):
    fixture = result_fixture(tmp_path)
    submit(fixture)
    workspace, definition, _, controller, _ = fixture
    ledger = json.loads(controller.read_bytes())['result_ledgers']['result']
    original_refs = deepcopy(ledger['contribution_refs'])
    (workspace / 'deps_a/rules.py').write_text('value = 8\n')
    current = fingerprints(workspace, definition)
    first = results.refresh_residuals(ledger, Store(workspace), current)
    second = results.refresh_residuals(first, Store(workspace), current)
    assert first == second
    assert [second['criteria'][c]['state'] for c in ('A', 'B', 'U')] == ['invalidated', 'invalidated', 'satisfied']
    # New A evidence alone cannot resurrect the old B proof.
    old_a = Store(workspace).resolve(ledger['criteria']['A']['contributions'][0])
    new_a = {**old_a, 'attempt_id': 'new-a', 'fingerprint': current['A']}
    a_ref = Store(workspace).put('worker-contribution', new_a)
    second['criteria']['A']['contributions'].append(a_ref)
    third = results.refresh_residuals(second, Store(workspace), current)
    assert third['criteria']['A']['state'] == 'satisfied'
    assert third['criteria']['B']['state'] == 'invalidated'
    for ref in original_refs:
        assert Store(workspace).resolve(ref)['schema'] == results.CONTRIBUTION


def test_semantic_cycle_invalidates_coupled_evidence_without_execution_cycle(tmp_path):
    fixture = result_fixture(tmp_path)
    workspace, definition, binding, controller, request = fixture
    obligations = definition['completion']['criterion_obligations']
    obligations['A']['semantic_dependencies'] = ['B']
    obligations['B']['semantic_dependencies'] = ['A']
    # Build this fresh fixture's opt-in contract before any contribution exists.
    from taskplane import storage
    db = json.loads(controller.read_bytes())
    db['result_ledgers']['result'] = results.new_ledger(binding, definition, runtime())
    storage.commit_controller(controller, db, expected_generation=db['storage_generation'])
    submit(fixture)
    ledger = json.loads(controller.read_bytes())['result_ledgers']['result']
    assert all(row['state'] == 'satisfied' for row in ledger['criteria'].values())
    (workspace / 'pkg_b/result.txt').write_text('new result\n')
    refreshed = results.refresh_residuals(ledger, Store(workspace), fingerprints(workspace, definition))
    assert refreshed['criteria']['A']['state'] == refreshed['criteria']['B']['state'] == 'invalidated'
    assert refreshed['criteria']['U']['state'] == 'satisfied'
    obligations['A']['depends_on'] = ['B']
    with pytest.raises(w.Refusal): results.contract(definition)


@pytest.mark.parametrize('dimension', ['source', 'dependency', 'output', 'test', 'runtime', 'environment', 'command', 'criterion'])
def test_each_fingerprint_change_invalidates_only_affected_contributions(tmp_path, dimension):
    fixture = result_fixture(tmp_path)
    workspace, definition, _, controller, _ = fixture
    submit(fixture)
    ledger = json.loads(controller.read_bytes())['result_ledgers']['result']
    current = fingerprints(workspace, definition)
    files = {'source': 'pkg_a/app.py', 'dependency': 'deps_a/rules.py',
             'output': 'pkg_a/result.txt', 'test': 'tests_a/check.py'}
    if dimension in files:
        (workspace / files[dimension]).write_text('changed = 7\n')
        current = fingerprints(workspace, definition)
    else:
        # Explicitly mutate exactly one captured dimension, rehashing the envelope.
        # Actual file/dependency capture is exercised above and in context reuse.
        fp = current['A']
        if dimension == 'criterion': fp['criteria']['A'] = {'meaning': 'different requirement'}
        else:
            fp['verification'][dimension] = {'changed': True} if dimension == 'runtime' else 'changed'
            fp['verification']['digest'] = digest({k: v for k, v in fp['verification'].items() if k != 'digest'})
        fp['digest'] = digest({k: v for k, v in fp.items() if k != 'digest'})
    changed = results.refresh_residuals(ledger, Store(workspace), current)
    assert {c for c, row in changed['criteria'].items() if row['state'] == 'invalidated'} == {'A', 'B'}
    assert changed['criteria']['U']['state'] == 'satisfied'
    assert changed['contribution_refs'] == ledger['contribution_refs']


def scheduling_inputs(ledger):
    return dict(predecessor={'attempt_id': 'previous', 'terminal': True, 'disposition': 'rejected',
                            'binding': ledger['binding'], 'outcome_digest': 'a' * 64},
                retry_plan={'prior_attempt': 'previous', 'prior_outcome_digest': 'a' * 64, 'remaining': list(ledger['criteria']),
                            'side_effects': 'reconciled', 'evidence': ['exact prior outcome']},
                authorization={'authorized': True, 'quiescent': True, 'denied': False,
                               'cancelled': False, 'paused': False, 'conflicting': False},
                budget={'dispatch_admitted': True}, fresh_identity=True)


def test_schedule_only_dependency_ready_residuals_and_preserve_semantic_root_review(tmp_path):
    fixture = result_fixture(tmp_path)
    ledger = json.loads(fixture[3].read_bytes())['result_ledgers']['result']
    options = scheduling_inputs(ledger)
    assert set(results.schedule_residuals(ledger, **options)) == {'A', 'U'}
    ledger['criteria']['A']['state'] = 'satisfied'
    assert set(results.schedule_residuals(ledger, **options)) == {'B', 'U'}
    # A native completion claim is never a semantic review.
    fixture[4]['semantic_review'] = {}
    assert results.assess(fixture[0], fixture[1], fixture[4])['missing_criteria'] == ['A', 'B', 'U']


@pytest.mark.parametrize('case', ['denied', 'cancelled', 'paused', 'conflicting', 'unauthorized',
                                 'live', 'unknown_budget', 'exhausted', 'wrong_predecessor',
                                 'wrong_outcome', 'foreign_binding', 'unknown_side_effects',
                                 'same_identity', 'not_terminal', 'missing_plan'])
def test_residual_dispatch_blockers_are_not_scheduled(tmp_path, case):
    fixture = result_fixture(tmp_path)
    ledger = json.loads(fixture[3].read_bytes())['result_ledgers']['result']
    options = scheduling_inputs(ledger)
    if case in ('denied', 'cancelled', 'paused', 'conflicting'): options['authorization'][case] = True
    elif case == 'unauthorized': options['authorization']['authorized'] = False
    elif case == 'live': options['authorization']['quiescent'] = False
    elif case in ('unknown_budget', 'exhausted'): options['budget']['dispatch_admitted'] = None if case == 'unknown_budget' else False
    elif case == 'wrong_predecessor': options['retry_plan']['prior_attempt'] = 'foreign'
    elif case == 'wrong_outcome': options['retry_plan']['prior_outcome_digest'] = 'b' * 64
    elif case == 'foreign_binding': options['predecessor']['binding'] = {'run': 'foreign'}
    elif case == 'unknown_side_effects': options['retry_plan']['side_effects'] = 'unknown'
    elif case == 'same_identity': options['fresh_identity'] = False
    elif case == 'not_terminal': options['predecessor']['terminal'] = False
    elif case == 'missing_plan': options['retry_plan'] = None
    assert results.schedule_residuals(ledger, **options) == []
