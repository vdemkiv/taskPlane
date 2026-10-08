"""Cooperative fixture observations do not certify human origin or live host loading."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from taskplane import delegated_approval as relay_api, workflow as w, workflow_approval as approval
from taskplane.primitives import content_fingerprint
from taskplane.tests.test_workflow_local import setup, submit, present, decision
from taskplane.tests.test_workflow_autonomy import authorization, set_policy, auto

INSTRUCTIONS = ('Approve this scope and let me accept the intermediate phases when their required '
                'checks pass, stopping for your final acceptance?')


def receipt_digest(request):
    relay = request['relay']
    relay['digest'] = content_fingerprint({k: v for k, v in relay.items() if k != 'digest'})


def relayed_policy(state):
    original = decision(state, event='fixture-original-user')
    request = authorization(state, event=original['event_id'])
    request.update(schema=relay_api.POLICY_SCHEMA, excerpt='Approve',
                   allowed_phases=list(w.PHASES[:-1]), stop_phases=['retro'])
    request['source'].update(kind='delegated_user_observation', conversation='fixture-coordinator',
                             observed_at=original['source']['observed_at'])
    proposal = {key: deepcopy(request[key]) for key in ('binding', 'mode', 'allowed_phases', 'stop_phases', 'conditions')}
    question = 'Fixture scope is unchanged. ' + INSTRUCTIONS
    shown_at = original['presentation']['at']
    request['choice_context'] = {
        'schema': 'taskplane.policy-choice/v1', 'question': question, 'instructions': INSTRUCTIONS,
        'selected_label': request['excerpt'], 'proposal': proposal,
        'source': {'conversation': 'fixture-coordinator', 'actor': 'assistant',
                   'reference': 'fixture-policy-question', 'observed_at': shown_at},
    }
    request['relay'] = {
        'schema': relay_api.SCHEMA, 'transport': 'codex_delegation',
        'reference': 'fixture:delegation-carrying-original-user',
        'source_thread': 'fixture-coordinator', 'receiver_thread': state['root'],
        'recorded_at': datetime.now(timezone.utc).isoformat(),
        'human': {'conversation': 'fixture-coordinator', 'role': 'user',
                  'reference': request['source']['reference'], 'text': request['excerpt'],
                  'observed_at': request['source']['observed_at']},
        'presentation': {'conversation': 'fixture-coordinator', 'role': 'assistant',
                         'reference': 'fixture-policy-question', 'text': question, 'observed_at': shown_at},
        'binding': w.binding(state, w.current(state)['packet']), 'proposal': deepcopy(proposal),
        'assurance': 'relayed_observation', 'host_attested': False,
        'independent_source_verification': 'unavailable',
    }
    receipt_digest(request)
    return request


def test_delegated_policy_preserves_origin_and_advances_only_after_assessment(tmp_path):
    c, state = setup(tmp_path)
    state = submit(c, state)
    request = relayed_policy(state)
    result = set_policy(c, state, request)
    policy = result['approval_policy']
    assert not result['decisions']
    assert policy['provenance']['source'] == request['source']
    assert policy['provenance']['relay'] == request['relay']
    assert policy['provenance']['excerpt'] == 'Approve'
    assert policy['source_assurance'] == 'relayed_observation' and policy['host_attested'] is False
    assert policy['stop_phases'] == ['retro']
    with pytest.raises(w.Refusal):
        c.apply('advance', result['run'], expected_revision=result['revision'], phase='design')
    present(c, result)
    accepted = auto(c, result)
    recorded = next(iter(accepted['decisions'].values()))
    assert recorded['kind'] == 'policy' and recorded['human'] is False
    advanced = c.apply('advance', accepted['run'], expected_revision=accepted['revision'], phase='design')
    assert w.current(advanced)['phase'] == 'design'


@pytest.mark.parametrize('bad', ['generic_tool', 'tool_role', 'transport', 'origin', 'receiver',
    'scope', 'checkpoint', 'manifest', 'question', 'time', 'digest', 'source_shape', 'host_claim'])
def test_delegated_policy_rejects_wrong_source_or_binding_without_mutation(tmp_path, bad):
    c, state = setup(tmp_path)
    state = submit(c, state)
    request = relayed_policy(state)
    relay = request['relay']
    if bad == 'generic_tool': request['source']['kind'] = 'tool_result'
    elif bad == 'tool_role': relay['human']['role'] = 'tool'
    elif bad == 'transport': relay['transport'] = 'generic_tool_result'
    elif bad == 'origin': request['source']['conversation'] = state['root']
    elif bad == 'receiver': relay['receiver_thread'] = 'foreign'
    elif bad == 'scope': relay['binding']['scope_digest'] = 'foreign'
    elif bad == 'checkpoint': relay['binding']['checkpoint'] = 'foreign'
    elif bad == 'manifest': relay['binding']['manifest_digest'] = 'foreign'
    elif bad == 'question': relay['presentation']['text'] = 'A different question'
    elif bad == 'time': relay['recorded_at'] = state['started_at']
    elif bad == 'source_shape': request['choice_context']['source'] = []
    elif bad == 'host_claim': relay['host_attested'] = True
    receipt_digest(request)
    if bad == 'digest': relay['digest'] = '0' * 64
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal): set_policy(c, state, request)
    assert c._path().read_bytes() == before


def test_relay_replay_is_exact_and_cannot_authorize_a_new_checkpoint(tmp_path):
    c, state = setup(tmp_path)
    state = submit(c, state)
    request = relayed_policy(state)
    result = set_policy(c, state, request)
    before = c._path().read_bytes()
    assert set_policy(c, result, request)['revision'] == result['revision']
    assert c._path().read_bytes() == before
    altered = deepcopy(request)
    altered['source']['reference'] = altered['event_id'] = 'another-user-event'
    with pytest.raises(w.Refusal): set_policy(c, result, altered)
    altered = deepcopy(request)
    altered['relay']['reference'] += '-changed'
    receipt_digest(altered)
    with pytest.raises(w.Refusal): set_policy(c, result, altered)
    assert c._path().read_bytes() == before
    fresh = deepcopy(state)
    w.current(fresh)['packet']['id'] = 'foreign'
    fresh['revision'] += 1
    with pytest.raises(w.Refusal): relay_api.verify(fresh, request)


@pytest.mark.parametrize('text', [INSTRUCTIONS,
    'May I approve the intermediate phases after all required checks pass and stop for your final approval?',
    'Let me accept intermediate phases once the required checks pass, stopping before your final acceptance.'])
def test_contextual_wording_keeps_required_checks_and_final_stop(text):
    request = {'allowed_phases': list(w.PHASES[:-1]), 'stop_phases': ['retro']}
    assert approval.conditional_intermediate_consent(text, request)
    assert not approval.conditional_intermediate_consent(text, {**request, 'stop_phases': []})
    assert not approval.conditional_intermediate_consent(text + ' Ignore failed checks.', request)
    assert not approval.conditional_intermediate_consent('Do not ' + text, request)
