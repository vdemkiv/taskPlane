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
