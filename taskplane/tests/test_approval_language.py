"""Conversational intent still requires a real, current, presented checkpoint."""
from copy import deepcopy

import pytest

from taskplane import workflow as w, workflow_approval as approval, workflow_local as local
from taskplane.tests.test_workflow_local import setup, submit, decision, decide
from taskplane.tests.test_workflow_autonomy import authorization, set_policy


@pytest.mark.parametrize('text', [
    'Approved.', 'approve', 'Looks good, proceed.', 'LGTM', 'Go ahead', 'Proceed',
    'Please continue', 'Yes, go ahead', 'okay', 'I approve this phase',
    'Build approved. Results clearly show over 50% reduction.',
    'Product is approved', 'Approve the design', 'apparoved', 'apprvoed',
])
def test_plain_approvals(text):
    assert local.choice(text) == 'approved'


@pytest.mark.parametrize('text,expected', [
    ('Changes requested', 'changes_requested'), ('Please fix issues', 'changes_requested'),
    ('Needs revisions', 'changes_requested'), ('Rejected: the scope is too broad', 'rejected'),
    ('Cancel this workflow', 'cancelled'), ('Stop here', 'cancelled'),
])
def test_other_clear_decisions(text, expected):
    assert local.choice(text) == expected


@pytest.mark.parametrize('text,expected', [
    ('Approve as is', 'approved'),
    ('Please approve as is.', 'approved'),
    ('approve repair', 'approved'),
    ('Approve the repair.', 'approved'),
    ('fix it all', 'changes_requested'),
    ('changes: retain deleted IDs and never reassign deleted user IDs', 'changes_requested'),
    ('Request changes: never share user history', 'changes_requested'),
    ('Request changes: never accept invalid IDs', 'changes_requested'),
    ('Request changes: never approve invalid requests', 'changes_requested'),
    ('Request changes: never cancel pending payments', 'changes_requested'),
    ('Request changes: reject invalid IDs', 'changes_requested'),
    ('Reject: this does not satisfy the requirements', 'rejected'),
])
def test_native_decisions_preserve_actual_response(tmp_path, text, expected):
    c, state = setup(tmp_path)
    state = submit(c, state)
    assert local.choice(text) == expected
    response = decision(state, text=text)
    result = decide(c, state, response)
    assert result['decisions'][response['event_id']]['provenance']['excerpt'] == text
    assert result['decisions'][response['event_id']]['choice'] == expected


@pytest.mark.parametrize('text', [
    'Approve as is if tests pass', 'Approve as is, but fix it all',
    'changes: fix IDs. Approved.', 'Fix it all, then approve',
    'Request changes if tests fail', 'Stop before Retro',
    'Cancel nothing', 'Reject nothing', 'Request changes? no, approve',
])
def test_native_decisions_do_not_erase_qualification(text):
    assert local.choice(text) is None


@pytest.mark.parametrize('separator', [': ', '. ', '; ', '! ', '\n', ' — '])
@pytest.mark.parametrize('decision_text,qualification', [
    ('Cancel', 'if tests fail'),
    ('Reject', 'unless the missing tests pass'),
    ('Request changes', 'only after tests fail'),
    ('Cancel', 'hypothetically, if tests fail'),
    ('Cancel', 'request changes'),
    ('Request changes', 'cancel this workflow'),
    ('Cancel', 'never mind, do not cancel'),
    ('Cancel', "actually, I do not want to cancel"),
    ('Cancel', 'actually do not cancel'),
    ('Cancel', 'this must not be cancelled'),
    ('Request changes', 'instead cancel this workflow'),
    ('Cancel', 'actually do not cancel this workflow please'),
    ('Request changes', 'instead cancel this workflow please'),
    ('Cancel', 'actually do not cancel please'),
    ('Cancel', 'actually do not cancel this workflow because it is needed'),
    ('Request changes', 'instead cancel this workflow immediately'),
    ('Request changes', 'instead cancel the current workflow please'),
    ('Cancel', 'actually approve repair please'),
    ('Cancel', 'instead confirm the proposal please'),
    ('Cancel', 'I no longer want to cancel this workflow'),
    ('Cancel', 'I do not wish to cancel this workflow please'),
    ('Request changes', 'actually approve repair'),
    ('Reject', 'do not reject'),
    ('Request changes', 'approve repair'),
])
def test_dissent_qualifications_survive_punctuation(separator, decision_text, qualification):
    assert local.choice(decision_text + separator + qualification) is None


@pytest.mark.parametrize('text', [
    'approve repair if checks pass', 'Approve repair: only when ready',
    'Approve another repair', '"approve repair"', 'Do not approve repair',
    'Approve repair; cancel',
])
def test_repair_approval_still_requires_unambiguous_consent(text):
    assert local.choice(text) is None


@pytest.mark.parametrize('text,incorrect_choice', [
    ('Cancel: if tests fail', 'cancelled'),
    ('Reject: unless the missing tests pass', 'rejected'),
    ('Request changes: if tests fail', 'changes_requested'),
    ('Cancel: request changes', 'cancelled'),
    ('Cancel: never mind, do not cancel', 'cancelled'),
    ('Cancel. Actually do not cancel', 'cancelled'),
    ('Request changes: instead cancel this workflow', 'changes_requested'),
    ('Cancel. Actually do not cancel this workflow please', 'cancelled'),
    ('Request changes: instead cancel this workflow please', 'changes_requested'),
])
def test_qualified_dissent_does_not_change_checkpoint_or_policy(tmp_path, text, incorrect_choice):
    c, state = setup(tmp_path)
    state = set_policy(c, state)
    state = submit(c, state)
    response = decision(state, text=text)
    response['choice'] = incorrect_choice
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal) as refused:
        decide(c, state, response)
    assert refused.value.result()['category'] == 'decision_grammar'
    assert c._path().read_bytes() == before
    current = c.report()
    assert current['revision'] == state['revision']
    assert current['decisions'] == state['decisions']
    assert current['approval_policy'] == state['approval_policy']
    assert current.get('policy_suspension') == state.get('policy_suspension')
    assert w.current(current)['decision'] == 'awaiting_human_approval'


@pytest.mark.parametrize('bad,category', [
    ('grammar', 'decision_grammar'), ('provenance', 'decision_provenance'),
    ('binding', 'decision_binding'), ('chronology', 'decision_chronology'),
])
def test_decision_failures_identify_the_failed_contract(tmp_path, bad, category):
    c, state = setup(tmp_path); state = submit(c, state)
    response = decision(state, text='Approve as is')
    if bad == 'grammar': response['excerpt'] += ' if tests pass'
    elif bad == 'provenance': response['source']['actor'] = 'assistant'
    elif bad == 'binding': response['binding']['root'] = 'other'
    else: response['presentation']['at'] = response['source']['observed_at']
    original_response = deepcopy(response)
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal) as refused:
        decide(c, state, response)
    assert refused.value.reason == 'invalid_evidence'
    assert refused.value.result() == {
        'status': 'blocked', 'reason': 'invalid_evidence',
        'category': category, 'detail': refused.value.detail,
    }
    assert str(refused.value) == refused.value.detail
    assert refused.value.detail.startswith(category + ': ')
    assert response == original_response
    assert c._path().read_bytes() == before


def test_long_descriptive_dissent_is_preserved_completely(tmp_path):
    c, state = setup(tmp_path); state = submit(c, state)
    excerpt = 'changes: never reassign deleted user IDs. ' + 'Retain all historical references. ' * 22
    response = decision(state, text=excerpt)
    result = decide(c, state, response)
    assert result['decisions'][response['event_id']]['provenance']['excerpt'] == excerpt


def policy_choice(state):
    from datetime import datetime, timezone
    request = authorization(state)
    request['excerpt'] = 'approve'
    request['choice_context'] = {
        'schema': 'taskplane.policy-choice/v1',
        'question': 'Approve automatic phase approvals for this run under these instructions?',
        'instructions': 'Auto-approve all phases.', 'selected_label': 'approve',
        'proposal': {key: deepcopy(request.get(key)) for key in ('binding', 'mode', 'allowed_phases', 'stop_phases', 'conditions')},
        'source': {'actor': 'assistant', 'conversation': state['root'], 'reference': 'assistant/policy-question',
                   'observed_at': state['started_at']},
    }
    request['source']['observed_at'] = datetime.now(timezone.utc).isoformat()
    return request


def test_brief_policy_choice_requires_and_retains_actual_context(tmp_path):
    c, state = setup(tmp_path)
    request = policy_choice(state)
    result = set_policy(c, state, request)
    assert result['approval_policy']['provenance']['excerpt'] == 'approve'
    assert result['approval_policy']['provenance']['choice_context'] == request['choice_context']
    assert result['approval_policy']['conditions'][0]['instruction'] == 'Auto-approve all phases.'
    assert not result['decisions']


@pytest.mark.parametrize('bad', ['missing', 'foreign', 'stale', 'label', 'conditional', 'question_time', 'instructions'])
def test_brief_policy_choice_cannot_invent_or_change_consent(tmp_path, bad):
    c, state = setup(tmp_path); request = policy_choice(state)
    context = request['choice_context']
    if bad == 'missing': request.pop('choice_context')
    elif bad == 'foreign': context['source']['conversation'] = 'other'
    elif bad == 'stale': context['proposal']['binding']['revision'] += 1
    elif bad == 'label': context['selected_label'] = 'Yes'
    elif bad == 'conditional': request['excerpt'] = context['selected_label'] = 'approve if tests pass'
    elif bad == 'question_time': context['source']['observed_at'] = request['source']['observed_at']
    else: context['instructions'] = 'Keep manual approval.'
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal): set_policy(c, state, request)
    assert c._path().read_bytes() == before


@pytest.mark.parametrize('text', [
    'not approved', 'Do not proceed', 'Looks good but fix issues first',
    'Approved if tests pass', 'Proceed after tests pass', 'Approve when ready',
    'Yes unless the checks fail', 'maybe approved', 'Approved?',
    '"Approved"', '`proceed`', '> go ahead', 'Example: approve',
    'The reviewer said approved', 'Approved by another reviewer',
    'Yes, explain the implementation', 'Continue reviewing', 'Approve, no, stop',
    'approved; request changes', 'Please keep working', 'approval is a feature', 'Stop before Retro',
    'Approved, "only if tests pass".', 'Looks good. "Do not proceed" is my final instruction.',
])
def test_ambiguous_or_nonconsent_stays_unapproved(text):
    assert local.choice(text) is None


@pytest.mark.parametrize('text', [
    'Approved. Needs changes.',
    'Looks good, please revise the token table.',
    'LGTM. Needs revisions.',
    'Go ahead. I need changes.',
    'Product approved. Change requested: correct the accounting.',
    'Yes, I decline this checkpoint.',
    'Approved. "Needs changes" is my final instruction.',
    'Okay. Please fix the issues.',
])
def test_mixed_assent_and_dissent_requires_clarification(text):
    assert local.choice(text) is None


@pytest.mark.parametrize('text', [
    'Approved. Needs changes.', 'Looks good, please revise the token table.',
])
def test_mixed_decision_cannot_accept_the_presented_checkpoint(tmp_path, text):
    c, s = setup(tmp_path); s = submit(c, s)
    response = decision(s, text=text)
    response['choice'] = 'approved'  # A recorder's label cannot override the user's words.
    with pytest.raises(w.Refusal, match='unclear'):
        decide(c, s, response)
    assert w.current(c.report())['decision'] == 'awaiting_human_approval'


@pytest.mark.parametrize('text', [
    'Now I need to run auto-approved full workflow which should resolve untracked token usage.',
    'Start a full auto-approved workflow through Retro.',
    'Run an auto-approved full workflow.', 'Please run an autonomous workflow.',
    'For this release, auto-approve all phases through Retro.',
    'run all release phases automatically through Retro.',
    'I would like to run an auto-approved workflow for this task.',
])
def test_explicit_automatic_workflow_language(text):
    assert approval.affirmative_consent(text)


@pytest.mark.parametrize('text', [
    'proceed with end to end auto-approved flow to fix all',
    'Please proceed with end-to-end auto-approved workflow to fix all.',
    'run full end to end flow with auto-approve',
])
def test_rca_automatic_workflow_word_order(text):
    assert approval.affirmative_consent(text)


@pytest.mark.parametrize('text', [
    'Do not proceed with end to end auto-approved flow to fix all',
    'proceed with end to end auto-approved flow if I approve later',
    '"proceed with end to end auto-approved flow to fix all"',
    'Example: proceed with end to end auto-approved flow to fix all',
    'proceed with end to end auto-approved flow. Keep manual approval.',
    'proceed with end to end auto-approved flow unless tests fail',
])
def test_rca_automatic_wording_does_not_hide_nonconsent(text):
    assert not approval.affirmative_consent(text)


@pytest.mark.parametrize('text', [
    'proceed with end to end auto-approved flow after I approve it',
    'proceed with end to end auto-approved flow, but wait for my approval before proceeding',
    'Auto-approve all phases. Wait for my approval before proceeding.',
    'Auto-approve all phases after our approval.',
    'Auto-approve all phases before the reviewer confirms. Wait for approval.',
    'Auto-approve all phases. "Wait for my approval" is required.',
    'Auto-approve all phases after\nI have approved it.',
    'Auto-approve all phases. Wait for\nmy approval.',
])
def test_future_human_decision_prevents_automatic_authorization(tmp_path, text):
    assert not approval.affirmative_consent(text)
    c, s = setup(tmp_path)
    request = authorization(s)
    request['excerpt'] = text
    with pytest.raises(w.Refusal, match='intent is unclear'):
        set_policy(c, s, request)
    assert not c.report().get('approval_policy')


@pytest.mark.parametrize('position', ['after', 'before'])
@pytest.mark.parametrize('qualification', [
    'the reviewer approves', 'the user confirms', 'the reviewer authorizes',
    'I have approved it', 'the reviewer has confirmed', 'we have authorized it',
    'Jane has approved it', 'approval is granted', 'the reviewer gives consent',
])
def test_temporal_decision_forms_cannot_enable_policy(tmp_path, position, qualification):
    # Each case stands alone; no separate wait clause may mask its failure.
    text = f'Auto-approve all phases {position} {qualification}.'
    assert not approval.affirmative_consent(text)
    c, s = setup(tmp_path)
    before = deepcopy(s)
    request = authorization(s)
    request['excerpt'] = text
    with pytest.raises(w.Refusal, match='intent is unclear'):
        set_policy(c, s, request)
    assert s == before
    assert not c.report().get('approval_policy')
    assert not c.report()['decisions']


@pytest.mark.parametrize('text', [
    'Auto-approve all phases after required checks pass.',
    'Auto-approve all phases after required checks pass. Stop before Retro.',
    'Now I need to run full workflow with auto-approval.',
    'proceed with end to end auto-approved flow to fix all',
    'Auto-approve all phases. Stop before Retro.',
    'Auto-approve all phases. My approval is not required.',
    'Auto-approve all phases. Our approval is no longer required.',
    'Auto-approve all phases. Your confirmation is not necessary.',
])
def test_check_qualifications_and_phase_stops_keep_explicit_consent(tmp_path, text):
    assert approval.affirmative_consent(text)
    c, s = setup(tmp_path)
    request = authorization(s)
    request['excerpt'] = text
    if 'Stop before Retro' in text:
        request['stop_phases'] = ['retro']
    request['conditions'] = [{'id': 'tests', 'kind': 'required_check',
                              'instruction': 'Required checks must pass.', 'check': 'tests'}]
    accepted = set_policy(c, s, request)
    policy = accepted['approval_policy']
    assert policy['provenance']['excerpt'] == text
    assert policy['provenance']['source'] == request['source']
    assert policy['conditions'][1:] == request['conditions']
    assert policy['stop_phases'] == request['stop_phases']
    assert not accepted['decisions']


@pytest.mark.parametrize('separator', ['.', ';', '!'])
@pytest.mark.parametrize('qualification', [
    ' after Dr{separator} Jane has approved it.',
    ' before Dr{separator} Jane confirms.',
    ' after{separator}\napproval is granted.',
    '. Wait for Dr{separator} Jane to approve.',
    '. Await{separator} the reviewer confirmation.',
    '. Ask Dr{separator} Jane for authorization.',
    '. Pending{separator}\nour approval.',
    '. Obtain{separator}\nmy approval before proceeding.',
    '. We need{separator}\nmy approval before proceeding.',
])
def test_punctuation_cannot_discard_human_conditions(tmp_path, separator, qualification):
    text = 'Auto-approve all phases' + qualification.format(separator=separator)
    assert not approval.affirmative_consent(text)
    c, s = setup(tmp_path)
    before = deepcopy(s)
    request = authorization(s)
    request['excerpt'] = text
    original_request = deepcopy(request)
    with pytest.raises(w.Refusal, match='intent is unclear'):
        set_policy(c, s, request)
    assert s == before and request == original_request
    assert not c.report().get('approval_policy')
    assert not c.report()['decisions']


@pytest.mark.parametrize('possessive', ['my', 'our', 'your'])
@pytest.mark.parametrize('separator', ['. ', '\n'])
@pytest.mark.parametrize('qualification', [
    '{possessive} approval is required before proceeding.',
    'With {possessive} approval required first.',
])
def test_subject_first_human_requirement_refuses_policy(tmp_path, possessive, separator, qualification):
    text = 'Auto-approve all phases' + separator + qualification.format(possessive=possessive)
    assert not approval.affirmative_consent(text)
    c, s = setup(tmp_path)
    before = deepcopy(s)
    request = authorization(s)
    request['excerpt'] = text
    original_request = deepcopy(request)
    with pytest.raises(w.Refusal, match='intent is unclear'):
        set_policy(c, s, request)
    assert s == before and request == original_request
    assert not c.report().get('approval_policy')
    assert not c.report()['decisions']


@pytest.mark.parametrize('text', [
    'proceed with end to end auto-approved flow to fix all. My approval is required before proceeding.',
    'Auto-approve all phases, with my approval required first.',
    'Auto-approve all phases. Our confirmation remains necessary.',
    'Auto-approve all phases. Your authorization will be required.',
    'Auto-approve all phases. My consent is still needed.',
    'Auto-approve all phases. Reviewer approval must be mandatory.',
])
def test_subject_first_requirement_forms_preserve_state(tmp_path, text):
    assert not approval.affirmative_consent(text)
    c, s = setup(tmp_path)
    before = deepcopy(s)
    request = authorization(s)
    request['excerpt'] = text
    original_request = deepcopy(request)
    with pytest.raises(w.Refusal, match='intent is unclear'):
        set_policy(c, s, request)
    assert s == before and request == original_request
    assert not c.report().get('approval_policy')
    assert not c.report()['decisions']


@pytest.mark.parametrize('text', [
    'Implement an auto-approved full workflow option.',
    'Do not run an auto-approved workflow.', 'Run an autonomous workflow?',
    'If I decide later, run an auto-approved workflow.',
    'Example: run an auto-approved workflow.', '"Run an autonomous workflow."',
    'Run an autonomous workflow. Keep manual approval.',
    'For this task, auto-approve all phases. "No automatic approvals" is my final instruction.',
])
def test_workflow_feature_descriptions_and_ambiguity_are_not_authorization(text):
    assert not approval.affirmative_consent(text)


def test_conversational_decision_preserves_provenance_and_is_replay_safe(tmp_path):
    c, s = setup(tmp_path); s = submit(c, s)
    response = decision(s, text='Looks good, proceed.')
    accepted = decide(c, s, response)
    record = accepted['decisions'][response['event_id']]
    assert record['provenance']['excerpt'] == response['excerpt']
    assert record['human'] and not record['automatic']
    assert decide(c, s, response) == accepted
    bad = deepcopy(response); bad['source']['actor'] = 'assistant'
    with pytest.raises(w.Refusal):
        decide(c, s, bad)


def test_named_phase_must_match_the_bound_visit(tmp_path):
    c, s = setup(tmp_path); s = submit(c, s)
    with pytest.raises(w.Refusal, match='different phase'):
        decide(c, s, decision(s, text='Build approved'))
    with pytest.raises(w.Refusal, match='different phase'):
        decide(c, s, decision(s, text='I approve this Build'))
    with pytest.raises(w.Refusal, match='different phase'):
        decide(c, s, decision(s, text='Accept the current plan'))
    accepted = decide(c, s, decision(s, text='Product approved'))
    assert w.current(accepted)['decision'] == 'approved'


def test_original_request_enables_policy_without_changing_its_words(tmp_path):
    c, s = setup(tmp_path)
    request = authorization(s)
    request['excerpt'] = ("Now I need to run auto-approved full workflow which should resolve untracked token usage, "
        "over 3.5M tokens were spend outside any of the phases, it should be properly tracked where it's used "
        "for proper tracking and father analysis. Second improvement is coming from retro as well: remove exact "
        "word expectation for phase approval, either extend vocabulary or just accept plain text which indicate "
        "approval. it's ok for ask for clarification, but never ask for specific 100% match on the words to be used to proceed.")
    accepted = set_policy(c, s, request)
    assert accepted['approval_policy']['provenance']['excerpt'] == request['excerpt']
    assert not accepted['decisions']  # Consent configures policy; unseen output is not accepted.
@pytest.mark.parametrize('excerpt', [
    'Start end to end flow with auto-approval.',
    'Run a full workflow with auto approval.',
    '[@taskplane](plugin://taskplane@openai-curated-remote) use 36-hour audit and retrospective document as an input and start end to end flow with auto-approval. we need to address and resolve all the issues. additionally run security lens and include its findings into the scope.',
])
def test_explicit_end_to_end_policy_consent(tmp_path, excerpt):
    assert approval.affirmative_consent(excerpt)
    c, s = setup(tmp_path)
    request = authorization(s)
    request['excerpt'] = excerpt
    accepted = set_policy(c, s, request)
    assert accepted['approval_policy']['provenance']['excerpt'] == excerpt
    assert accepted['approval_policy']['provenance']['source'] == request['source']
    assert not accepted['decisions']


@pytest.mark.parametrize('excerpt', [
    'Add an option to start end to end flow with auto-approval.',
    'Do not start end to end flow with auto-approval.',
    'Example: start end to end flow with auto-approval.',
    '"Start end to end flow with auto-approval."',
    'Start end to end flow with auto-approval. Keep manual approval.',
])
def test_end_to_end_policy_keeps_negative_and_quoted_guards(excerpt):
    from taskplane.workflow_approval import affirmative_consent
    assert not affirmative_consent(excerpt)


@pytest.mark.parametrize('text', [
    'Proceed with fixes with end to end auto-approved flow',
    'Please proceed with fixes with end-to-end auto-approved workflow.',
    'Stop at Engineering. Auto-approve all phases.',
    'Stop before Retro. Auto-approve all phases.',
    'Auto-approve all phases. Stop at Engineering.',
    'Pause at Retro. Proceed with fixes with end to end auto-approved flow.',
    'Hold at Retro. Automatically approve all phases.',
])
def test_fixes_imperative_and_named_stops_preserve_explicit_consent(text):
    assert approval.affirmative_consent(text)


@pytest.mark.parametrize('text', [
    'Proceed',
    'Do not proceed with fixes with end to end auto-approved flow',
    'Never proceed with fixes with end to end auto-approved flow',
    '"Proceed with fixes with end to end auto-approved flow"',
    'Add a feature to proceed with fixes with end to end auto-approved flow',
    'If I approve, proceed with fixes with end to end auto-approved flow',
    'Proceed with fixes with end to end auto-approved flow unless tests fail',
    'Proceed with fixes with end to end auto-approved flow. Hold for my approval.',
])
def test_fixes_imperative_requires_unqualified_direct_consent(text):
    assert not approval.affirmative_consent(text)


@pytest.mark.parametrize('qualification', [
    'Pause for my approval.', 'Hold for my confirmation.',
    'Pause; for approval.', 'Hold! For Dr. Jane to approve.',
    'Halt for authorization.', 'Stop for the reviewer to confirm.',
    '"Pause for my approval" is required.',
    'Hold at Engineering. My confirmation remains required.',
    'Hold for the reviewer to auto-approve all phases.',
    'Wait for the user to automatically approve all phases.',
])
def test_pause_hold_human_conditions_refuse_policy_without_mutation(tmp_path, qualification):
    c, s = setup(tmp_path)
    before = c._path().read_bytes()
    request = authorization(s)
    request['excerpt'] = 'Auto-approve all phases. ' + qualification
    assert not approval.affirmative_consent(request['excerpt'])
    with pytest.raises(w.Refusal, match='intent is unclear'):
        set_policy(c, s, request)
    assert c._path().read_bytes() == before


@pytest.mark.parametrize('separator', ['. ', '; ', '! ', '\n', ': '])
@pytest.mark.parametrize('qualification', [
    'Wait for my sign-off.',
    'Await our sign off.',
    'Hold for the reviewer to signoff.',
    'Pause for the user to sign off.',
    'Stop for Dr. Jane to sign-off.',
    'After I have signed off.',
    'Before the reviewer signs off.',
    'Approval from me is required.',
    'Confirmation from us remains necessary.',
    'Authorization by the user will be required.',
    'Consent from you must be mandatory.',
    'Sign-off by the reviewer is needed.',
    'My sign off is still required.',
    'Our signoff: required.',
    'Approval from me; required.',
    'Obtain approval from the reviewer.',
    'We need confirmation from the user.',
    'I must approve first.',
    'The reviewer needs to sign off.',
    'You have to confirm first.',
    '"Wait for my sign-off" is required.',
    '"Approval from me is required" is my condition.',
])
def test_human_source_and_signoff_qualifications_prevent_consent(separator, qualification):
    assert not approval.affirmative_consent('Auto-approve all phases' + separator + qualification)


@pytest.mark.parametrize('text', [
    'Auto-approve all phases. My sign-off is not required.',
    'Auto-approve all phases. Our sign off is no longer needed.',
    'Auto-approve all phases. Approval from me is not required.',
    'Auto-approve all phases. Confirmation by the user is not necessary.',
    'Auto-approve all phases. Signoff from the reviewer is not mandatory.',
    'Auto-approve all phases. You do not need my sign-off.',
    'Auto-approve all phases. There is no need to obtain approval from me.',
    'Auto-approve all phases. Do not wait for my sign-off.',
    'Auto-approve all phases. My approval is not required. Stop before Retro.',
    'Pause at Engineering. Proceed with fixes with end to end auto-approved flow',
])
def test_explicitly_unnecessary_human_decisions_preserve_consent(text):
    assert approval.affirmative_consent(text)


@pytest.mark.parametrize('text', [
    'Do not auto-approve all phases. My sign-off is not required.',
    '"Auto-approve all phases. My sign-off is not required."',
    'Implement an option to auto-approve all phases without my sign-off.',
    'Auto-approve all phases. Do not wait for checks; wait for my sign-off.',
    'Auto-approve all phases. My sign-off is not only required, it is mandatory.',
    'Auto-approve all phases. No need to wait for checks. Approval from me is required.',
])
def test_unnecessary_decision_wording_cannot_mask_nonconsent(text):
    assert not approval.affirmative_consent(text)


class TestHumanRequirementComposition:
    @pytest.mark.parametrize('subject', [
        'Approval from the human reviewer', 'Approval from the project owner',
        'Confirmation by our designated security reviewer',
        'The lead reviewer’s approval', 'Our project owner\'s sign-off',
        'My sign-off', 'Your approval and confirmation',
        'Approval and sign-off by the project owner',
        'My approval and the human reviewer\'s confirmation',
    ])
    @pytest.mark.parametrize('predicate', [
        'is required', 'will still be required', 'still will be needed',
        'will be explicitly required', 'will also be mandatory',
        'must still remain necessary', ': required',
        'is not only required', 'is not always required', 'will eventually be required',
        'is not necessarily required', 'is not usually required', 'is normally not required',
        'is not yet required', 'is no longer always required',
    ])
    def test_required_subjects_and_predicates(self, subject, predicate):
        assert not approval.affirmative_consent(f'Auto-approve all phases. {subject} {predicate}.')

    @pytest.mark.parametrize('subject', [
        'My sign-off', 'Approval from the human reviewer',
        'Approval by our project owner', 'The lead reviewer’s approval',
        'My approval and the human reviewer\'s confirmation',
    ])
    @pytest.mark.parametrize('predicate', [
        'is still not required', 'will not be required', 'will still not be necessary',
        'will no longer be needed', 'will also not be mandatory',
        'is unnecessary', 'is optional', 'will not still be required', "won't be required",
    ])
    def test_explicitly_unnecessary_subjects_and_predicates(self, subject, predicate):
        assert approval.affirmative_consent(f'Auto-approve all phases. {subject} {predicate}.')

    @pytest.mark.parametrize('qualification', [
        'Required: approval from the human reviewer.',
        'Still required; the project owner\'s confirmation.',
        'The project owner must still sign off.',
        'Our lead reviewer will also have to approve.',
        'The human reviewer is still required to confirm.',
        'My approval will not be required. The project owner must still sign off.',
        'Approval by our project owner is optional, but my sign-off remains mandatory.',
        'My sign-off remains mandatory, although approval by our project owner is optional.',
        'My approval is not required but is still mandatory.',
        'My approval is not required and confirmation is still necessary.',
        'My sign-off is required and approval by the human reviewer is not required.',
        'Do not wait for my approval, which remains required.',
        'My approval is not not required.',
        '"Approval from the project owner will still be required" is my condition.',
        '"Required: approval from our designated release owner" is my condition.',
        'Our designated release approver must personally confirm.',
        'The project owner must not only sign off.',
        'The human reviewer will still need to approve.',
        'The human reviewer may still need to approve.',
        'My approval is not required and still needed.',
        'My approval is required and not needed.',
    ])
    def test_required_combinations(self, qualification):
        assert not approval.affirmative_consent('Auto-approve all phases. ' + qualification)

    @pytest.mark.parametrize('qualification', [
        'Not required: approval from the human reviewer.',
        'No longer needed; the project owner\'s confirmation.',
        'The human reviewer does not need to confirm.',
        'Our project owner need not sign off.',
        'My approval will not be required and confirmation is also not necessary.',
        'My approval is not required, and the human reviewer\'s sign-off is optional.',
        'Do not wait for approval from the human reviewer.',
        'You do not need approval by our project owner.',
        'Our human project owner need not still approve.',
        'The human reviewer will still not need to approve.',
        'Never required: approval by our assigned reviewer.',
        'Tests required. My approval is unnecessary.',
    ])
    def test_unnecessary_combinations(self, qualification):
        assert approval.affirmative_consent('Auto-approve all phases. ' + qualification)


class TestCompleteDecisionQualifications:
    @pytest.mark.parametrize('subject', [
        'My approvals', 'Our confirmations', 'Human sign-offs',
        'All human authorizations', 'My permission', 'My acceptance',
        'The change board\'s assent', 'Our clearances',
    ])
    @pytest.mark.parametrize('predicate', ['remain mandatory', 'are required', 'are compulsory'])
    def test_decision_inflections_and_synonyms_refuse(self, subject, predicate):
        assert not approval.affirmative_consent(f'Auto-approve all phases. {subject} {predicate}.')

    @pytest.mark.parametrize('subject', [
        'Approval by our lead reviewer', 'My sign-off', 'Confirmation',
        'Approval from Jane', 'The release captain\'s consent',
    ])
    @pytest.mark.parametrize('predicate', [
        'will, however, still be required', 'will (however) still be required',
        'will — however — still be required', 'will, in every case, be required',
        'is, as always, necessary', 'will still (personally) be needed',
        'remains compulsory', 'is essential', 'will always be my responsibility',
    ])
    def test_unknown_or_required_predicate_refuses_regardless_of_actor(self, subject, predicate):
        assert not approval.affirmative_consent(f'Auto-approve all phases. {subject} {predicate}.')

    @pytest.mark.parametrize('join', [', yet ', '; nevertheless ', '. However, ',
                                      ' and ', ' (but ', ' — although '])
    @pytest.mark.parametrize('tail', [
        'confirmation remains mandatory', 'it remains compulsory', 'is still necessary',
        'the release captain must approve', 'approval by our lead reviewer is required',
    ])
    def test_safe_prefix_cannot_hide_explicit_or_shared_requirement(self, join, tail):
        text = 'Auto-approve all phases. My approval is not required' + join + tail + '.'
        assert not approval.affirmative_consent(text)

    @pytest.mark.parametrize('qualification', [
        'My approval is not required except for launch.',
        'My approval is not required for tests but required for delivery.',
        'My approval is optional today but mandatory tomorrow.',
        'My approval is optional. It remains my prerogative.',
        'My approval is not required. Checks pass. It remains mandatory.',
        'My approval is not required. Tests pass, but it remains mandatory.',
        'My approval is not required. Stop before Retro, though it remains necessary.',
        'My approval is not required. Checks require it.',
        'My approval is not required; confirmation is still compulsory.',
        'I reserve final approval.', 'You must obtain approval.',
        'Approval from the change board is required.',
        'My approval is not (always) required.',
        'My approval is (not only) required.',
        'It is not true that my approval is not required.',
        'My approval is not required and not unnecessary.',
        'My approval is optional, except that it is not optional.',
        'My approval is not required, but it is not unnecessary.',
        'Do not wait for my approval except for launch.',
        'No need to obtain my approval for tests; still needed for delivery.',
        'Remove exact word expectation for phase approval, but my approval is required.',
        'Remove exact word expectation for phase approval, which remains mandatory.',
        '"Remove exact word expectation for phase approval" is required; approval remains compulsory.',
        'My approval is not required. "Confirmation" remains compulsory.',
    ])
    def test_unclassified_qualifications_and_logical_negation_refuse(self, qualification):
        assert not approval.affirmative_consent('Auto-approve all phases. ' + qualification)

    @pytest.mark.parametrize('subject', ['My approval', 'Approval from the human reviewer', 'Confirmation'])
    @pytest.mark.parametrize('predicate', [
        'is (still) not required', 'will, still, not be needed',
        'is not required yet is not mandatory',
        'is unnecessary; it is also optional',
        'is not required, however confirmation is not necessary',
        'is not required and approval from the project owner is also optional',
    ])
    def test_complete_unnecessary_dependencies_preserve_consent(self, subject, predicate):
        assert approval.affirmative_consent(f'Auto-approve all phases. {subject} {predicate}.')

    @pytest.mark.parametrize('qualification', [
        'My approval is not required. Required checks must pass. Stop before Retro.',
        'My approval is not required. Stop before Retro. Confirmation is optional.',
        'Remove exact word expectation for phase approval.',
        'Update the wording requirement for phase approval.',
    ])
    def test_bounded_checks_stops_and_technical_descriptions(self, qualification):
        assert approval.affirmative_consent('Auto-approve all phases. ' + qualification)


class TestEveryDirectiveIsClassified:
    @pytest.mark.parametrize('directive', [
        'Wait for my go-ahead.', 'Hold for my OK.', 'Pause for me to say yes.',
        'Proceed only on my green light.', 'Pause for my decision.',
        'Hold for instructions from me.',
        'Await the final decision from the release owner.',
        'You need to ask me first.', 'Wait for the human reviewer.',
        'Stop for my go-ahead.', 'Do not proceed without my OK.',
    ])
    def test_independent_evaluation_reproductions(self, directive):
        assert not approval.affirmative_consent('Auto-approve all phases. ' + directive)

    @pytest.mark.parametrize('directive', [
        'Await the release captain\'s semaphore.', 'The amber lantern must glow.',
        'Respect the quux protocol.', 'Zyxwvu frobnicator.',
        'Proceed only on my green light.', 'x', '\u2603', '42',
    ])
    @pytest.mark.parametrize('template', [
        '{} Auto-approve all phases.',
        'Auto-approve all phases. {} Run an autonomous workflow.',
        'Auto-approve all phases. {}',
    ])
    def test_unknown_directives_fail_in_every_position(self, directive, template):
        assert not approval.affirmative_consent(template.format(directive))

    @pytest.mark.parametrize('clause', [
        'Auto-approve all phases',
        'Start a full auto-approved workflow through Retro',
        'proceed with end to end auto-approved flow to fix all',
        'Proceed with fixes with end to end auto-approved flow',
        'run all release phases automatically through Retro',
        'Run a full workflow with auto approval',
        'Auto-approve all phases after required checks pass',
        'Required checks must pass', 'Stop before Retro', 'Pause at Engineering',
        'My approval is not required', 'Do not wait for my sign-off',
        'Remove exact word expectation for phase approval',
        'Use taskplane to implement the settings page',
        'We need to address and resolve all the issues',
        'Additionally run security lens and include its findings into the scope',
        'Now I need to run auto-approved full workflow which should resolve untracked token usage',
    ])
    @pytest.mark.parametrize('tail', [
        ', but await the lantern', ' and await the lantern',
        ' only with the lantern', ': await the lantern', '; await the lantern',
        '. Await the lantern', '! Await the lantern', '\nAwait the lantern',
        ' (await the lantern)', ' — await the lantern',
        ' / await the lantern', ' "await the lantern"',
        'xyz', '/xyz', '#xyz', ' <xyz>',
    ])
    def test_every_allowed_clause_requires_a_complete_match(self, clause, tail):
        assert not approval.affirmative_consent('Auto-approve all phases. ' + clause + tail + '.')

    @pytest.mark.parametrize('text', [
        'Required checks must pass. Auto-approve all phases. Stop before Retro.',
        'Stop before Retro. Auto-approve all phases. Required checks must pass.',
        'Auto-approve all phases. My approval is not required. Required checks must pass.',
        'My approval is not required. Auto-approve all phases. Stop before Retro.',
        'Remove exact word expectation for phase approval. Auto-approve all phases.',
        'Auto-approve all phases. Run an autonomous workflow.',
    ])
    def test_known_complete_clauses_remain_compatible(self, text):
        assert approval.affirmative_consent(text)
