"""Frozen admission arithmetic and real locked reservations; no native kill claim."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json

import pytest

from taskplane import storage, worker_results as results, workflow as w
from taskplane.tests.test_worker_results import policy, runtime


def usage(total=0):
    return {'input': total, 'output': 0, 'cached_input': total // 2,
            'coverage': 'complete', 'source': 'fixture native cumulative observation'}


class Harness:
    """Disposable storage integration only; authorize is an internal test seam."""
    def __init__(self, tmp_path, configured=None):
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.path = tmp_path / 'controller.json'
        self.binding = {'run': 'test-run', 'visit': 'test-visit', 'task': 'test-task'}
        self.policy = configured or policy()
        self.calls = 0
        self.total = 0
        budget = results.new_budget(self.binding, self.policy, runtime=runtime())
        storage.commit_controller(self.path, {'retry_budgets': {'task': budget}}, expected_generation=0)

    def read(self):
        return json.loads(self.path.read_bytes())

    def budget(self):
        return self.read()['retry_budgets']['task']

    def authorize(self, database):
        assert database['retry_budgets']['task']['binding'] == self.binding

    def reserve(self, identity=None, *, now=0, proposed=1, observed=None, configured=None, generation=None):
        self.calls += 1
        identity = identity or f'reservation-{self.calls}'
        return results.reserve_dispatch(self.path,
            expected_generation=self.read()['storage_generation'] if generation is None else generation,
            authorize=self.authorize, scopes={'task': {'binding': self.binding, 'policy': configured,
                'usage': observed if observed is not None else usage(self.total)}}, reservation_id=identity,
            attempt_id=identity, tokens_proposed=proposed, now_ms=now, runtime=runtime())

    def transition(self, identity, state, *, now=0, total=None, proof=None):
        if total is not None: self.total = total
        return results.commit_budget_observation(self.path,
            expected_generation=self.read()['storage_generation'], authorize=self.authorize,
            scope_ids=['task'], reservation_id=identity, state=state,
            proof=proof or {'source': 'fixture observation', 'call_id': identity, 'terminal': True, 'usage_complete': True},
            observations={'task': {'now_ms': now, 'usage': usage(self.total)}}, runtime=runtime())

    def complete(self, identity, *, total=0):
        assert self.reserve(identity)['result']['dispatch_admitted']
        assert self.transition(identity, 'issued')['result']['issue_admitted']
        self.transition(identity, 'terminal', total=total)


def measured_sample(case, identifier, before, after, proposed=1):
    return {'sample_id': identifier, 'case': case, 'measurement_class': 'injected_clock',
            **results.budget_admission(after, tokens_proposed=proposed),
            'elapsed_highwater_before': before['elapsed_highwater'], 'elapsed_highwater_after': after['elapsed_highwater'],
            'token_highwater_before': before['token_highwater'], 'token_highwater_after': after['token_highwater']}


def test_retry_numeric_contract(record_property, tmp_path):
    """LOCAL-SRE-03:4: every sample comes from the actual budget/reservation API."""
    samples = []
    # Original attempts are consumed by actual issued transitions. Exact limit
    # means the proposed attempt would equal the cap; it is the final admission.
    for count, label in [(1, 'limit-1'), (2, 'exact limit'), (3, 'limit+1')]:
        h = Harness(tmp_path / ('attempt-' + str(count)))
        for i in range(count): h.complete(f'call-{i}')
        before = h.budget(); after = results.observe_budget(before, now_ms=0, usage=usage())
        sample = measured_sample(label, 'attempt-' + str(count), before, after)
        assert sample['dispatch_admitted'] == (count < 3)
        samples.append(sample)
    h = Harness(tmp_path / 'boundaries'); h.complete('original')
    baseline = h.budget()
    for elapsed, label in [(999, 'limit-1'), (1000, 'exact limit'), (1001, 'limit+1')]:
        after = results.observe_budget(baseline, now_ms=elapsed, usage=usage())
        sample = measured_sample(label, 'elapsed-' + str(elapsed), baseline, after)
        assert sample['dispatch_admitted'] == (elapsed < 1000)
        samples.append(sample)
    for tokens, label in [(98, 'limit-1'), (99, 'exact limit'), (100, 'limit+1')]:
        after = results.observe_budget(baseline, now_ms=1, usage=usage(tokens))
        sample = measured_sample(label, 'tokens-' + str(tokens), baseline, after)
        assert sample['dispatch_admitted'] == (tokens < 100)
        samples.append(sample)
    # Reset remains unknown even after later counters exceed the old highwater.
    high = results.observe_budget(baseline, now_ms=10, usage=usage(50))
    reset = results.observe_budget(high, now_ms=11, usage=usage(2))
    grown = results.observe_budget(reset, now_ms=12, usage=usage(60))
    assert not grown['coverage_known'] and grown['token_highwater'] == 60
    samples.append(measured_sample('reset', 'reset-growth', reset, grown))
    absent = results.observe_budget(baseline, now_ms=1, usage={'coverage': 'unknown'})
    samples.append(measured_sample('missing counters', 'missing', baseline, absent))
    restarted = results.observe_budget(json.loads(json.dumps(high)), now_ms=900, usage=usage(50))
    assert restarted['first_authorized_ms'] == 0 and restarted['elapsed_highwater'] == 900
    samples.append(measured_sample('restart', 'restart', high, restarted))
    backward = results.observe_budget(high, now_ms=9, usage=usage(50))
    assert not backward['clock_known']
    samples.append(measured_sample('clock regression', 'backward', high, backward))
    tight = results.observe_budget(high, now_ms=11, usage=usage(50), policy=policy(attempt_cap=1, elapsed_cap_ms=20, token_cap=50))
    removed = results.observe_budget(tight, now_ms=12, usage=usage(50), policy=None)
    assert removed['frozen_effective_limits'] == tight['frozen_effective_limits']
    samples.append(measured_sample('cap tightening/removal', 'tightened-removed', tight, removed))
    exhausted = Harness(tmp_path / 'no-progress')
    for i in range(3): exhausted.complete(f'failed-without-progress-{i}')
    before = exhausted.budget()
    refused = exhausted.reserve('fourth')
    assert not refused['result']['dispatch_admitted']
    samples.append(measured_sample('exhausted no-progress retries', 'no-progress', before, exhausted.budget()))
    uncertain = Harness(tmp_path / 'uncertain')
    uncertain.reserve('maybe', proposed=100)
    uncertain.transition('maybe', 'issued'); uncertain.transition('maybe', 'uncertain')
    state = uncertain.budget()
    assert state['attempts_consumed'] == 1 and state['reservations']['maybe']['state'] == 'uncertain'
    samples.append(measured_sample('uncertain launch', 'uncertain', state, state))
    continuation = Harness(tmp_path / 'continuation', policy(attempt_cap=2))
    continuation.complete('original-cycle', total=50)
    prior_cycle = continuation.budget()
    assert continuation.reserve('continuation-cycle', now=20, proposed=10)['result']['dispatch_admitted']
    assert continuation.transition('continuation-cycle', 'issued', now=20, total=70)['result']['issue_admitted']
    continued = results.observe_budget(continuation.budget(), now_ms=20,
        usage={'input': 60, 'output': 10, 'cached_input': 40, 'coverage': 'complete', 'source': 'same owned continuation intervals'})
    assert continued['attempts_consumed'] == prior_cycle['attempts_consumed'] + 1 == 2
    assert continued['token_highwater'] == 70 and continued['first_authorized_ms'] == 0
    assert not continuation.reserve('another-cycle', now=20)['result']['dispatch_admitted']
    samples.append(measured_sample('continuation', 'continued', prior_cycle, continued))
    simultaneous = Harness(tmp_path / 'simultaneous', policy(attempt_cap=1))
    generation = simultaneous.read()['storage_generation']
    def contender(identity):
        try: return simultaneous.reserve(identity, generation=generation)
        except storage.StorageConflict: return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(contender, ['one', 'two']))
    assert sum(isinstance(row, dict) and row['result']['dispatch_admitted'] for row in outcomes) == 1
    assert outcomes.count('conflict') == 1
    before = simultaneous.budget()
    assert not simultaneous.reserve('retry-loser')['result']['dispatch_admitted']
    samples.append(measured_sample('simultaneous reservations', 'simultaneous', before, simultaneous.budget()))
    overshot = results.observe_budget(high, now_ms=1001, usage=usage(120))
    assert overshot['overshoot'] and not results.budget_admission(overshot, tokens_proposed=1)['dispatch_admitted']
    samples.append(measured_sample('limit+1', 'actual-inflight-overshoot', high, overshot))
    assert len({s['sample_id'] for s in samples}) == len(samples)
    record_property('remediation_measurement', json.dumps({'schema': 'taskplane.remediation-measurement/v1',
        'subcheck': 'LOCAL-SRE-03', 'scenario': 'LOCAL-SRE-03:4', 'kind': 'retry', 'samples': samples}))


@pytest.mark.parametrize('cap', [0, 9, -1, True, 1.5, None])
def test_attempt_caps_have_no_implicit_default(cap):
    with pytest.raises(w.Refusal): results.validate_policy(policy(attempt_cap=cap))


@pytest.mark.parametrize('configured', [
    {'schema': results.POLICY, 'attempt_cap': 2},
    {'schema': results.POLICY, 'elapsed_cap_ms': 20},
    {'schema': results.POLICY, 'token_cap': 20}])
def test_optional_caps_do_not_invent_unrelated_constraints(tmp_path, configured):
    h = Harness(tmp_path, configured)
    if 'token_cap' in configured:
        result = h.reserve(now=None, proposed=1, observed=usage())
    elif 'elapsed_cap_ms' in configured:
        result = h.reserve(now=0, proposed=0, observed={'coverage': 'unknown'})
    else:
        result = h.reserve(now=None, proposed=0, observed={'coverage': 'unknown'})
    assert result['result']['dispatch_admitted'] is True
    assert h.budget()['frozen_effective_limits'] == {k: v for k, v in configured.items() if k != 'schema'}


def test_later_elapsed_cap_cannot_restart_an_unknown_original_clock(tmp_path):
    h = Harness(tmp_path, {'schema': results.POLICY, 'attempt_cap': 3})
    assert h.reserve('first', now=None, proposed=0, observed={'coverage': 'unknown'})['result']['dispatch_admitted']
    assert h.budget()['authorization_started'] and h.budget()['first_authorized_ms'] is None
    later = h.reserve('later', now=1000, proposed=0, configured={'schema': results.POLICY, 'elapsed_cap_ms': 20})
    assert not later['result']['dispatch_admitted']
    assert h.budget()['first_authorized_ms'] is None
    recovered = results.observe_budget(h.budget(), now_ms=1000, usage=usage(), reconcile_clock=True, original_authorized_ms=0)
    assert recovered['elapsed_highwater'] == 1000
    assert not results.budget_admission(recovered, tokens_proposed=0)['dispatch_admitted']


def test_zero_reservation_and_missing_coverage_refuse_without_launch(tmp_path):
    h = Harness(tmp_path)
    assert not h.reserve('zero', proposed=0)['result']['dispatch_admitted']
    assert not h.reserve('unknown', observed={'coverage': 'unknown'})['result']['dispatch_admitted']
    assert h.budget()['reservations'] == {}
    assert h.budget()['observations']


def test_issue_rechecks_tightened_caps_and_retains_observed_overshoot(tmp_path):
    h = Harness(tmp_path)
    h.reserve('reserved', proposed=10)
    result = h.transition('reserved', 'issued', now=1001, total=110)
    assert not result['result']['issue_admitted']
    budget = h.budget()
    assert budget['reservations']['reserved']['state'] == 'reserved'
    assert budget['overshoot'] and budget['token_highwater'] == 110
    assert not h.reserve('next', now=1001)['result']['dispatch_admitted']


def test_uncertain_attempt_requires_authoritative_no_launch_proof(tmp_path):
    h = Harness(tmp_path)
    h.reserve('maybe'); h.transition('maybe', 'issued'); h.transition('maybe', 'uncertain')
    before = h.path.read_bytes()
    with pytest.raises(w.Refusal):
        h.transition('maybe', 'no_launch', proof={'source': 'timeout alone'})
    assert h.path.read_bytes() == before
    assert h.budget()['attempts_consumed'] == 1
    h.transition('maybe', 'no_launch', proof={'source': 'authoritative call reconciliation', 'authoritative_no_launch': True})
    assert h.budget()['attempts_consumed'] == 0
    assert [row['state'] for row in h.budget()['reservations']['maybe']['history']] == ['reserved', 'issued', 'uncertain', 'no_launch']


def test_reset_and_clock_faults_require_explicit_reconciliation(tmp_path):
    h = Harness(tmp_path); h.complete('original', total=50)
    high = results.observe_budget(h.budget(), now_ms=50, usage=usage(50))
    bad = results.observe_budget(high, now_ms=40, usage=usage(2))
    grown = results.observe_budget(bad, now_ms=60, usage=usage(60))
    assert not grown['coverage_known'] and not grown['clock_known']
    recovered = results.observe_budget(grown, now_ms=70, usage=usage(60), reconcile_clock=True, reconcile_usage=True)
    assert recovered['coverage_known'] and recovered['clock_known']
    subsequent = results.observe_budget(recovered, now_ms=80, usage=usage(60))
    assert subsequent['clock_known'] and subsequent['coverage_known']
    assert subsequent['elapsed_highwater'] >= grown['elapsed_highwater']
    assert subsequent['token_highwater'] >= grown['token_highwater']


def test_all_applicable_scopes_reserve_in_one_atomic_commit(tmp_path):
    h = Harness(tmp_path)
    db = h.read()
    db['retry_budgets']['run'] = results.new_budget({'run': 'test-run'}, policy(token_cap=0), runtime=runtime())
    storage.commit_controller(h.path, db, expected_generation=db['storage_generation'])
    result = results.reserve_dispatch(h.path, expected_generation=h.read()['storage_generation'], authorize=h.authorize,
        scopes={scope: {'binding': row['binding'], 'policy': None, 'usage': usage()}
                for scope, row in h.read()['retry_budgets'].items()}, reservation_id='one', attempt_id='one',
        tokens_proposed=1, now_ms=0, runtime=runtime())
    assert not result['result']['dispatch_admitted']
    assert all(not row['reservations'] and row['observations'] for row in h.read()['retry_budgets'].values())
