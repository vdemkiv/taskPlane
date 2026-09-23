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
