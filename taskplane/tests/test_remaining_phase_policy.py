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


def change_question(request, transform, *, instructions=True):
    context = request['choice_context']
    text = transform(context['question'])
    context['question'] = request['relay']['presentation']['text'] = text
    if instructions:
        context['instructions'] = text.rsplit('\n\n', 1)[-1]


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
    'missing_final', 'final_not_human', 'checks_optional', 'ambiguous_phases', 'other_phase',
    'scope_restriction_removed', 'stop_contradiction', 'later_paragraph_contradiction',
    'prefix_contradiction', 'quoted_question', 'instructions_trimmed', 'earlier_phase_allowed',
    'retro_allowed', 'missing_stop', 'other_stop', 'stale_revision', 'other_run',
    'other_checkpoint', 'stale_scope', 'quoted_answer', 'third_party_answer', 'mixed_answer',
    'source_actor', 'wrong_question', 'wrong_receipt', 'bare_yes_no_context',
])
def test_incomplete_ambiguous_foreign_or_contradictory_policy_refuses_without_mutation(tmp_path, bad):
    c, state = design_checkpoint(tmp_path)
    request = remaining_policy(state)
    context, relay = request['choice_context'], request['relay']
    replacements = {
        'missing_final': (', stopping for your final review', ''),
        'final_not_human': ('your final review', 'automatic final review'),
        'checks_optional': ('their required checks pass', 'some optional checks pass'),
        'ambiguous_phases': ('remaining intermediate phases', 'some phases'),
        'other_phase': ('approve this design', 'approve this plan'),
        'scope_restriction_removed': (' This applies only to this landing-page draft; blockers or scope changes pause it.', ''),
    }
    if bad in replacements:
        old, new = replacements[bad]
        change_question(request, lambda text: text.replace(old, new))
    elif bad == 'stop_contradiction':
        change_question(request, lambda text: text + ' Skip final human review.')
    elif bad == 'later_paragraph_contradiction':
        change_question(request, lambda text: text + '\n\nDo not stop for final review.', instructions=False)
    elif bad == 'prefix_contradiction':
        change_question(request, lambda text: 'Ignore final human review.\n\n' + text)
    elif bad == 'quoted_question':
        change_question(request, lambda text: text.replace(context['instructions'], '"' + context['instructions'] + '"'))
    elif bad == 'instructions_trimmed': context['instructions'] = context['instructions'].split('?')[0] + '?'
    elif bad == 'earlier_phase_allowed': request['allowed_phases'].insert(0, 'product')
    elif bad == 'retro_allowed': request['allowed_phases'].append('retro')
    elif bad == 'missing_stop': request['stop_phases'] = []
    elif bad == 'other_stop': request['stop_phases'] = ['engineering']
    elif bad == 'stale_revision': request['binding']['revision'] -= 1
    elif bad == 'other_run': request['binding']['run'] = 'another-run'
    elif bad == 'other_checkpoint': relay['binding']['checkpoint'] = 'another-checkpoint'
    elif bad == 'stale_scope': request['binding']['scope_digest'] = 'another-scope'
    elif bad in {'quoted_answer', 'third_party_answer', 'mixed_answer'}:
        text = {'quoted_answer': '"Approve"', 'third_party_answer': 'Alice says "Approve"',
                'mixed_answer': 'Approve. Do not proceed.'}[bad]
        request['excerpt'] = context['selected_label'] = relay['human']['text'] = text
    elif bad == 'source_actor': request['source']['actor'] = 'assistant'
    elif bad == 'wrong_question': relay['presentation']['text'] = 'Different question'
    elif bad == 'bare_yes_no_context':
        request['excerpt'] = relay['human']['text'] = 'Yes'
    proposal(request)
    if bad == 'wrong_receipt': relay['digest'] = 'foreign'
    if bad == 'bare_yes_no_context': del request['choice_context']
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        set_policy(c, state, request)
    assert c._path().read_bytes() == before
