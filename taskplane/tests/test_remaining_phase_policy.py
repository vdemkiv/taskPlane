"""Synthetic complete offer with the relevant scope and final-review conditions.

Private project text is omitted. Fixture identities/times are synthetic.
No fixture claims account authentication or host loading.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from taskplane import workflow as w, workflow_approval as approval
from taskplane.tests.test_delegated_approval import relayed_policy, receipt_digest
from taskplane.tests.test_workflow_local import setup, submit, decide, present
from taskplane.tests.test_workflow_autonomy import set_policy, auto, assessment


def design_checkpoint(tmp_path):
    c, state = setup(tmp_path)
    state = decide(c, submit(c, state))
    state = c.apply('advance', state['run'], expected_revision=state['revision'], phase='design')
    return c, submit(c, state)


def proposal(request):
    value = {k: deepcopy(request[k]) for k in ('binding', 'mode', 'allowed_phases', 'stop_phases', 'conditions')}
    request['choice_context']['proposal'] = deepcopy(value)
    request['relay']['proposal'] = value
    receipt_digest(request)


def remaining_policy(state):
    request = relayed_policy(state)
    fixture = json.loads((Path(__file__).parent/'fixtures/approval/remaining-phase-policy.json').read_text())
    answer, shown = fixture['message'], fixture['before'][0]
    text = shown['content']['text']
    request['event_id'] = request['source']['reference'] = answer['message_id']
    request['excerpt'] = answer['content']['text']
    request['allowed_phases'] = list(w.PHASES[1:-1])
    context, relay = request['choice_context'], request['relay']
    context.update(question=text, instructions=text.rsplit('\n\n', 1)[-1], selected_label=request['excerpt'])
    context['source']['reference'] = shown['message_id']
    relay['human'].update(reference=answer['message_id'], text=request['excerpt'])
    relay['presentation'].update(reference=shown['message_id'], text=text)
    proposal(request)
    return request


def test_complete_offer_keeps_fixture_text_and_only_remaining_phases(tmp_path):
    c, state = design_checkpoint(tmp_path)
    request = remaining_policy(state)
    original = deepcopy(request)
    result = set_policy(c, state, request)
    policy = result['approval_policy']
    assert request == original
    assert policy['provenance']['relay'] == original['relay']
    assert policy['provenance']['choice_context'] == original['choice_context']
    assert policy['conditions'][0]['instruction'] == original['choice_context']['instructions']
    assert policy['allowed_phases'] == ['design', 'plan', 'build', 'evaluate', 'engineering']
    assert policy['stop_phases'] == ['retro']
    assert w.current(result)['decision'] == 'awaiting_human_approval'
    assert result['decisions'] == state['decisions']
    # Recording policy alone grants no phase; failed/unknown checks still pause.
    present(c, result)
    uncertain = assessment(result)
    uncertain['conditions'][0]['status'] = 'unknown'
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        auto(c, result, uncertain)
    assert c._path().read_bytes() == before
    accepted = auto(c, result)
    assert w.current(accepted)['decision'] == 'approved'
    event = list(accepted['decisions'].values())[-1]
    assert event['automatic'] is True and event['human'] is False
    final = deepcopy(result)
    final['visits'][final['index']]['phase'] = 'retro'
    with pytest.raises(w.Refusal, match='human checkpoint'):
        approval.automatic_decision(final, assessment(final))


@pytest.mark.parametrize('bad', [
    'stale_revision', 'other_run', 'other_checkpoint', 'stale_scope',
    'source_actor', 'wrong_question', 'wrong_receipt',
])
def test_incomplete_ambiguous_foreign_or_contradictory_policy_refuses_without_mutation(tmp_path, bad):
    c, state = design_checkpoint(tmp_path)
    request = remaining_policy(state)
    context, relay = request['choice_context'], request['relay']
    if bad == 'stale_revision': request['binding']['revision'] -= 1
    elif bad == 'other_run': request['binding']['run'] = 'another-run'
    elif bad == 'other_checkpoint': relay['binding']['checkpoint'] = 'another-checkpoint'
    elif bad == 'stale_scope': request['binding']['scope_digest'] = 'another-scope'
    elif bad == 'source_actor': request['source']['actor'] = 'assistant'
    elif bad == 'wrong_question': relay['presentation']['text'] = 'Different question'
    proposal(request)
    if bad == 'wrong_receipt': relay['digest'] = 'foreign'
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        set_policy(c, state, request)
    assert c._path().read_bytes() == before
