"""Focused cooperative-source fixtures. These do not authenticate live accounts."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from taskplane import delegated_approval as relay_api, workflow as w
from taskplane.primitives import content_fingerprint
from taskplane.tests.test_workflow_local import setup, submit, decision, decide


def digest(request):
    relay = request['relay']
    relay['digest'] = content_fingerprint({k: v for k, v in relay.items() if k != 'digest'})


def observation(state, kind='owner_reaction'):
    native = decision(state)
    bound = deepcopy(native['binding'])
    phase = w.current(state)['phase']
    question = {'message_id': 'fixture-question', 'channel': 'chatgpt',
                'text': f'Do you accept this {phase} with the recorded limits?',
                'sent_at': native['presentation']['at'], 'phase': phase, 'binding': deepcopy(bound)}
    message = {'message_id': question['message_id'], 'channel': 'chatgpt', 'author_id': 'fixture-assistant',
               'content': {'text': question['text']}, 'sent_at': question['sent_at'], 'deleted_at': None,
               'reactions': {'fixture-owner': '👍'}, 'reply_to_id': None}
    if kind == 'owner_text':
        message.update(message_id='fixture-answer', author_id='fixture-owner', content={'text': 'Approved'},
                       sent_at=native['source']['observed_at'], reactions={}, reply_to_id=question['message_id'])
    source = {'kind': kind, 'channel': 'chatgpt', 'message_id': message['message_id'], 'owner': 'fixture-owner'}
    if kind == 'owner_reaction':
        source['reaction'] = '👍'
    request = {'schema': relay_api.DECISION_SCHEMA, 'event_id': 'delegated:' + content_fingerprint(source),
               'recorder': 'root_orchestrator', 'choice': 'approved', 'binding': bound}
    request['relay'] = {'schema': relay_api.CHECKPOINT_SCHEMA, 'transport': 'codex_delegation',
        'reference': 'fixture:explicit-parent-observation', 'source_thread': 'fixture-parent',
        'receiver_thread': state['root'], 'assurance': 'relayed_observation', 'host_attested': False,
        'independent_source_verification': 'unavailable', 'binding': deepcopy(bound), 'question': question,
        'owner': {'user_id': 'fixture-owner', 'verified': True, 'reference': 'fixture-owner-verification', 'source_thread': 'fixture-parent'},
        'observation': {'kind': kind, 'method': 'user_message.read_messages', 'current': True,
            'complete_message': True, 'precision': 'second', 'observed_at': datetime.now(timezone.utc).isoformat(),
            'raw': {'message': message, 'before': [], 'after': [], 'partial': True}},
        'recorded_at': datetime.now(timezone.utc).isoformat()}
    digest(request)
    return request


@pytest.mark.parametrize('kind', ['owner_reaction', 'owner_text'])
def test_owner_observation_applies_exact_checkpoint_and_preserves_source(tmp_path, kind):
    c, state = setup(tmp_path)
    state = submit(c, state)
    request = observation(state, kind)
    result = decide(c, state, request)
    event = result['decisions'][request['event_id']]
    assert w.current(result)['decision'] == 'approved'
    assert event['human'] and not event['automatic']
    assert event['provenance']['relay'] == request['relay']
    assert event['provenance']['host_attested'] is False
    assert event['provenance']['source_assurance'] == 'relayed_observation'
    if kind == 'owner_reaction':
        assert event['provenance']['excerpt'] is None
        assert event['provenance']['normalization']['typed_text'] is None
        assert event['provenance']['normalization']['raw_reaction'] == '👍'
    else:
        assert event['provenance']['excerpt'] == 'Approved'
    original = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        decide(c, result, request)
    assert c._path().read_bytes() == original
    advanced = c.apply('advance', result['run'], expected_revision=result['revision'], phase='design')
    with pytest.raises(w.Refusal):
        decide(c, submit(c, advanced), request)


@pytest.mark.parametrize('bad', ['owner', 'owner_unverified', 'owner_source', 'question', 'target', 'phase',
    'scope', 'checkpoint', 'manifest', 'revision', 'receiver', 'source_thread', 'generic_tool', 'generic_emoji',
    'deleted', 'missing_deleted', 'old_observation', 'incomplete_message', 'before_submission', 'future',
    'host_claim', 'choice', 'event', 'digest', 'plain_emoji_question', 'negative_question', 'conditional_question'])
def test_wrong_or_stale_observations_are_rejected_without_mutation(tmp_path, bad):
    c, state = setup(tmp_path)
    state = submit(c, state)
    request = observation(state)
    relay = request['relay']; obs = relay['observation']; raw = obs['raw']['message']
    if bad == 'owner': relay['owner']['user_id'] = 'someone-else'
    elif bad == 'owner_unverified': relay['owner']['verified'] = False
    elif bad == 'owner_source': relay['owner']['source_thread'] = 'unrelated'
    elif bad == 'question': relay['question']['text'] += ' Changed question.'
    elif bad == 'target': raw['message_id'] = 'another-question'
    elif bad == 'phase': relay['question']['phase'] = 'retro'
    elif bad in {'scope', 'checkpoint', 'manifest', 'revision'}:
        field = {'scope': 'scope_digest', 'manifest': 'manifest_digest'}.get(bad, bad)
        request['binding'][field] = 'wrong'
    elif bad == 'receiver': relay['receiver_thread'] = 'wrong'
    elif bad == 'source_thread': relay['source_thread'] = state['root']
    elif bad == 'generic_tool': relay['transport'] = 'tool_result'
    elif bad == 'generic_emoji': obs['method'] = 'emoji_tool'
    elif bad == 'deleted': raw['deleted_at'] = obs['observed_at']
    elif bad == 'missing_deleted': del raw['deleted_at']
    elif bad == 'old_observation': obs['current'] = False
    elif bad == 'incomplete_message': obs['complete_message'] = False
    elif bad == 'before_submission': relay['question']['sent_at'] = state['started_at']
    elif bad == 'future': relay['recorded_at'] = '2100-01-01T00:00:00Z'
    elif bad == 'host_claim': relay['host_attested'] = True
    elif bad == 'choice': request['choice'] = 'rejected'
    elif bad == 'event': request['event_id'] = 'arbitrary-event'
    elif bad in {'plain_emoji_question', 'negative_question', 'conditional_question'}:
        text = {'plain_emoji_question': 'Like this screenshot?', 'negative_question': 'Do you not accept this product?',
                'conditional_question': 'Do you accept this product if the unrun checks pass?'}[bad]
        relay['question']['text'] = raw['content']['text'] = text
    digest(request)
    if bad == 'digest': relay['digest'] = '0' * 64
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        decide(c, state, request)
    assert c._path().read_bytes() == before


def test_raw_thumb_is_not_added_to_generic_text_parser():
    from taskplane.workflow_approval import conversational_choice
    assert conversational_choice('👍') is None
