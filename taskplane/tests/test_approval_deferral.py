"""Pure consent regressions; no Controller, adapter, or workflow-store fixtures."""

import pytest

from taskplane import workflow_approval as approval


@pytest.mark.parametrize('text', [
    'Okay. Hold for my approval.',
    'LGTM. Wait for my sign-off.',
])
def test_original_deferred_approval_reproductions(text):
    assert approval.conversational_choice(text) is None


@pytest.mark.parametrize('text', [
    'Okay. I am withholding approval.',
    "Okay. I'll sign it off tomorrow.",
    'LGTM. My approval is still to come.',
    'Okay. Approval is to follow.',
])
def test_retained_engineering_reproductions(text):
    assert approval.conversational_choice(text) is None


@pytest.mark.parametrize('time_request', [
    'I need time', 'I need more time', 'We require additional time',
    'We need some more time', 'I still need a little more time',
    'I am needing extra time', 'The reviewer requires more time',
    'Give me some time',
])
@pytest.mark.parametrize('purpose', [
    'to think', 'to think about it', 'to really think this through',
    'for thinking it over carefully', 'to consider this proposal',
    'for carefully considering the plan', 'for further consideration',
    'to deliberate', 'to deliberate on this', 'for deliberating about it',
    'for deliberation',
])
@pytest.mark.parametrize('template', [
    'Okay. {request} {purpose}.', 'LGTM; "{request} {purpose}".',
    'Approved — {request} {purpose}!',
    '{request} {purpose}. Okay.',
    'Go ahead. {purpose}, {request}. Approved.',
])
def test_time_requests_compose_with_deliberation(time_request, purpose, template):
    assert approval.conversational_choice(template.format(request=time_request, purpose=purpose)) is None


@pytest.mark.parametrize('qualification', [
    'I {separator} need more time to think about it',
    'We need {separator} some more time to deliberate',
    'We require additional {separator} time for consideration',
    'I need more time {separator} to really think this through',
])
@pytest.mark.parametrize('separator', ['.', ';', ':', '\n', ' — ', ' " " ', ' “ ” '])
def test_punctuation_cannot_hide_a_composed_time_request(qualification, separator):
    assert approval.conversational_choice('Okay. ' + qualification.format(separator=separator)) is None


@pytest.mark.parametrize('template', [
    "Okay. '{}'.", 'Okay. “{}”.', 'Okay. `{}`.',
    'Okay.\n> {}.', 'Okay.\n```\n{}\n```',
])
@pytest.mark.parametrize('qualification', [
    'I need more time to think about it',
    'We require additional time to deliberate',
    'For further consideration, we need some more time',
])
def test_quoted_time_requests_keep_their_deliberation_purpose(template, qualification):
    assert approval.conversational_choice(template.format(qualification)) is None


@pytest.mark.parametrize('purpose', [
    '', 'please', 'to finalize', 'to process it', 'for the next step',
    'to implement', 'to implement the plan and deliberate',
    'to implement the plan for further consideration',
    'to implement the plan and then think about it',
    'to implement the plan tomorrow and finalize',
])
@pytest.mark.parametrize('template', [
    'Okay. We need more time {}.',
    'Approved. "We need more time {}". Approved.',
])
def test_unknown_or_incomplete_time_purposes_cannot_inherit_approval(purpose, template):
    assert approval.conversational_choice(template.format(purpose)) is None


@pytest.mark.parametrize('qualification', [
    'I need more time to think. We need more time to implement the plan',
    'We need more time to implement the plan. I need more time to think',
    'To deliberate, we need more time to implement the plan',
    'We need more time to implement the plan; to think about it, I need time',
    'I need. We need more time to implement the plan. More time to think',
])
def test_work_time_exception_cannot_hide_another_reservation(qualification):
    assert approval.conversational_choice('Approved. ' + qualification + '.') is None


@pytest.mark.parametrize('work', [
    'We need more time to implement the plan',
    'I need time to implement these changes',
    'We require some more time to build the implementation',
    'Our team needs additional time for implementation of the plan',
    'We need a little extra time to publish the release tomorrow',
])
def test_complete_benign_work_time_clauses_remain_supported(work):
    assert approval.conversational_choice('Approved. ' + work + '.') == 'approved'


@pytest.mark.parametrize('separator', ['\n', '; '])
def test_retained_shared_purpose_reproductions(separator):
    text = 'Okay. We need more time to implement the plan' + separator + 'and to deliberate.'
    assert approval.conversational_choice(text) is None


@pytest.mark.parametrize('purpose', [
    'to deliberate', 'to really think this through', 'for deliberating about it',
    'for carefully considering the plan', 'for further consideration',
    'for deliberation', 'for contemplation', 'to finalize',
    'for the next step', 'to quuxify the decision process',
])
@pytest.mark.parametrize('separator', [
    ' and ', '\nand ', '; and ', '. And ', ', and ',
    ' — and ', ' “and” ', '\n> and ',
])
@pytest.mark.parametrize('template', [
    'Okay. We need more time to implement the plan{separator}{purpose}.',
    'Okay. We need more time {purpose}{separator}to implement the plan.',
    'Approved. “We need more time to implement the plan{separator}{purpose}”. Approved.',
    'Go ahead. {purpose}{separator}we need more time to implement the plan. Approved.',
])
def test_shared_time_purposes_are_audited_as_a_complete_response(purpose, separator, template):
    assert approval.conversational_choice(template.format(purpose=purpose, separator=separator)) is None


@pytest.mark.parametrize('fragment', [
    'and think it over', 'still deliberating', 'for final deliberation',
    'to resolve the remaining uncertainty', 'and to finalize', 'or to decide',
    'this remains undecided', 'the amber lantern must glow', 'quux',
    'and approve', 'and to approve', 'and',
])
@pytest.mark.parametrize('template', [
    'Okay. We need more time to implement the plan; {}.',
    'Okay. {}. We need more time to implement the plan.',
    'Approved. We need more time to implement the plan. Approved. {}.',
    'Approved. We need more time to implement the plan.\n```\n{}\n```',
])
def test_work_time_requires_all_residual_fragments_to_be_classified(fragment, template):
    assert approval.conversational_choice(template.format(fragment)) is None


@pytest.mark.parametrize('work', [
    'We need more time to implement the plan',
    'I require additional time for implementation of these changes',
    'Our team needs extra time to publish the release tomorrow',
    'We need time to implement the plan and to build the feature',
    'We need time to build the feature and publish the release',
    'We need time to ship the code. We need time to publish the documentation',
    'We need time to implement the plan. I approve this phase now',
])
@pytest.mark.parametrize('template', [
    'Approved. {}.', 'Okay; {}!', 'LGTM.\n{}.',
    'I approve this phase now. “{}”.',
    'Go ahead. "{}". Approved.',
    'Okay. My approval is granted. {}. I sign this off now.',
])
def test_complete_benign_time_responses_consume_all_clauses(work, template):
    assert approval.conversational_choice(template.format(work)) == 'approved'


@pytest.mark.parametrize('fragment', [
    'to publish the release', 'and to publish the release',
    'please and thank you', 'the results look good',
])
@pytest.mark.parametrize('separator', ['\n', '; ', '. '])
def test_unsupported_time_response_fragments_conservatively_require_clarification(fragment, separator):
    # Work/courtesy words alone are not complete independent clauses. No
    # arbitrary remainder can become exempt through a benign-looking prefix.
    text = 'Approved. We need more time to implement the plan' + separator + fragment + '.'
    assert approval.conversational_choice(text) is None


@pytest.mark.parametrize('modifier', [
    'deliberation', 'thinking', 'further consideration', 'my approval',
    'additional decision', 'permission', 'quux', 'still unresolved',
])
@pytest.mark.parametrize('template', [
    'Approved. We need {} time to implement the plan.',
    'Okay. We need {} time to build the feature. I approve this phase now.',
    'Go ahead. “I require {} time for implementation of these changes”.',
])
def test_work_permission_uses_narrower_modifiers_than_time_request_detection(modifier, template):
    assert approval.conversational_choice(template.format(modifier)) is None


@pytest.mark.parametrize('qualification', [
    'I withhold approval', 'We are withholding consent',
    'My approval is withheld', 'The reviewer has withheld permission',
    'I am withholding', 'I have withheld my sign-off',
    "I'll sign it off tomorrow", 'I will sign this checkpoint off',
    'We shall sign the current Build phase off later',
    'I still need to sign this off', 'Sign it off later',
    "I'll give approval later", 'I plan to give my confirmation',
    'My approval is still to come', 'Approval is to follow',
    'Our sign-off is forthcoming', 'Approval remains pending',
    'My consent comes in due course', 'Approval remains my responsibility',
    'I retain final approval', 'My permission is conditional',
    'My approval is contingent on success', 'Approval is subject to review',
    'The approval remains undecided', 'I am considering approval',
    'My approval has been neither given nor denied',
    'The confirmation belongs to the next stage',
    'I approve the current plan tomorrow',
    'I will give the go-ahead tomorrow', 'My green light will come later',
    "I'll give my OK tomorrow",
    'I need more time', 'Let me think about it', 'I will decide tomorrow',
    'We still require additional time', 'Allow us to consider this',
    'I need to think about it', 'I am still deliberating',
    'My decision is forthcoming',
])
@pytest.mark.parametrize('template', [
    'Okay. {}.', 'LGTM; "{}".', 'Approved — {}!',
    'Go ahead. {}. Approved.',
])
def test_consent_clauses_require_a_complete_present_grant(qualification, template):
    assert approval.conversational_choice(template.format(qualification)) is None


@pytest.mark.parametrize('qualification', [
    'I am {separator} withholding approval',
    'I will sign {separator} this off tomorrow',
    'My approval is {separator} still to come',
    'Approval {separator} is to follow',
    'My sign-off is {separator} forthcoming',
])
@pytest.mark.parametrize('separator', ['.', ';', ':', '\n', ' — ', ' " " ', ' “ ” '])
def test_punctuation_does_not_hide_withheld_or_future_consent(qualification, separator):
    text = 'Okay. ' + qualification.format(separator=separator)
    assert approval.conversational_choice(text) is None


@pytest.mark.parametrize('qualification', [
    "I'll sign this off tomorrow", 'My approval is forthcoming',
    'Approval is to follow', 'I am withholding consent',
])
@pytest.mark.parametrize('template', [
    "Okay. '{}'.", 'Okay. “{}”.', 'Okay. `{}`.',
    'Okay.\n> {}.', 'Okay.\n```\n{}\n```',
])
def test_quoted_consent_reservations_are_still_audited(qualification, template):
    assert approval.conversational_choice(template.format(qualification)) is None


@pytest.mark.parametrize('qualification', [
    'Hold for my approval', 'Wait for my sign-off', 'Await our sign off',
    'Defer approval', 'Pause for the reviewer', 'Postpone my decision',
    'Reserve acceptance', 'Delay confirmation', 'My approval is pending',
    'Approval remains outstanding', 'Await the release captain\'s semaphore',
    'I will approve it', 'I’ll sign off tomorrow', 'We shall confirm later',
    'I am going to approve it', 'I plan to approve it', 'I intend to approve',
    'Expect my approval tomorrow', 'My sign-off will follow',
    'Approve it later', 'The reviewer signs off next time',
    'My approval is required', 'Approval from me is necessary',
    'Our signoff remains mandatory', 'Obtain my consent',
    'Ask the reviewer for approval', 'I must approve first',
    'The reviewer needs to sign off', 'You have to confirm first',
])
@pytest.mark.parametrize('template', [
    'Okay. {}.', 'LGTM; {}!', 'Approved — {}.', 'Yes:\n{}.',
    'Go ahead. "{}".', 'Proceed. “{}”.', 'Looks good. `{}`.',
    'Approved.\n> {}.', '{}. Okay.', 'Okay. {}. Approved.',
])
def test_qualifications_anywhere_cannot_be_hidden(qualification, template):
    assert approval.conversational_choice(template.format(qualification)) is None


@pytest.mark.parametrize('separator', ['.', ';', '!', ':', '\n', ' — ', ' “ ” '])
@pytest.mark.parametrize('qualification', [
    'Hold{separator}for my approval',
    'Wait{separator}for my sign-off',
    'Await{separator}the reviewer confirmation',
    'I will{separator}approve it',
    'My sign-off{separator}is required',
    'Obtain{separator}approval from Dr{separator}Jane',
])
def test_punctuation_cannot_split_a_deferral(separator, qualification):
    text = 'Okay. ' + qualification.format(separator=separator)
    assert approval.conversational_choice(text) is None


@pytest.mark.parametrize('text', [
    'Approved.', 'approve', 'Looks good, proceed.', 'LGTM', 'Go ahead',
    'Please continue', 'Yes, go ahead', 'okay', 'I approve this phase',
    'Build approved. Results clearly show over 50% reduction.',
    'Product is approved', 'Approve the design', 'apparoved', 'apprvoed',
    'Approved. All required checks passed.',
    'Approved. Outstanding work.',
    'I approve. We will implement the plan.',
    'Accept the current plan',
    'Okay. I approve this phase now.',
    'Okay. I explicitly accept the current checkpoint.',
    'Okay. I have approved the plan.',
    'Okay. The current Build phase is approved.',
    'Okay. I sign this off now.',
    'Okay. We have signed the Build phase off.',
    'Okay. I sign off on this checkpoint.',
    'Okay. My approval is granted.',
    'Okay. Our consent has been given.',
    'Okay. I hereby grant my approval.',
    'Okay. We have given our consent.',
    'Okay. I am giving my approval now.',
    'Okay. I give my green light now.',
    'Okay. I grant the go-ahead.',
    'Okay. I give my OK.',
    'Go ahead. We will implement the plan.',
    'Okay. The results look okay.',
    'Approved. We need more time to implement the plan.',
    'Approved. We will publish tomorrow.',
    'Approved. The results are ready. We will publish tomorrow.',
])
def test_clear_current_approvals_remain_supported(text):
    assert approval.conversational_choice(text) == 'approved'


@pytest.mark.parametrize('text, expected', [
    ('Changes requested', 'changes_requested'),
    ('Rejected: the scope is too broad', 'rejected'),
    ('Cancel this workflow', 'cancelled'), ('Stop here', 'cancelled'),
])
def test_clear_dissent_remains_supported(text, expected):
    assert approval.conversational_choice(text) == expected


@pytest.mark.parametrize('text', [
    'Approved if tests pass', 'Approved. Needs changes.',
    'Approved, "only if tests pass".', 'Approved?', '"Approved"',
    'Yes, explain the implementation', 'Continue reviewing',
])
def test_existing_ambiguity_stays_unapproved(text):
    assert approval.conversational_choice(text) is None


@pytest.mark.parametrize('text', [
    'start end to end auto-approved flow to fix it all.',
    'Please start end-to-end auto-approved flow to fix it all.',
    'proceed with end to end auto-approved flow to fix all',
    'Run a full auto-approved workflow to fix it all.',
    'Auto-approve all phases after required checks pass.',
    'Auto-approve all phases. My approval is not required.',
    'Auto-approve all phases. Do not wait for my sign-off.',
])
def test_bounded_automatic_policy_purpose_and_positive_controls(text):
    assert approval.affirmative_consent(text)


@pytest.mark.parametrize('qualification', [
    'Hold for my approval.', 'Wait for my sign-off.', 'Await our confirmation.',
    'I will approve later.', 'The reviewer needs to sign off.',
    'I am withholding approval.', "I'll sign it off tomorrow.",
    'My approval is still to come.', 'Approval is to follow.',
    'My approval is required.', 'The amber lantern must glow.',
    'Respect the quux protocol.', 'x',
])
@pytest.mark.parametrize('template', [
    '{} start end to end auto-approved flow to fix it all.',
    'start end to end auto-approved flow to fix it all. {}',
    'start end to end auto-approved flow to fix it all. {} Auto-approve all phases.',
    'start end to end auto-approved flow to fix it all. "{}"',
])
def test_new_purpose_cannot_discard_deferred_or_unknown_conditions(qualification, template):
    assert not approval.affirmative_consent(template.format(qualification))


@pytest.mark.parametrize('tail', [
    ' if I approve later', ' after my approval', ' only on my green light',
    ', but wait for my approval', ' and await the lantern', 'xyz',
])
def test_new_purpose_still_requires_a_complete_match(tail):
    assert not approval.affirmative_consent(
        'start end to end auto-approved flow to fix it all' + tail + '.')


@pytest.mark.parametrize('request_text', [
    'I need', 'I will need', "I'll need", 'I’ll need',
    'We need', 'We will need', "We'll need", 'We’ll need',
    'The reviewer needs', 'The reviewers need',
    'The release council needs', 'Quux needs',
])
@pytest.mark.parametrize('quantity', ['more time', '5 minutes', 'five minutes', 'a day'])
@pytest.mark.parametrize('purpose', ['to deliberate', 'to think it over'])
def test_request_spelling_quantity_and_requester_cannot_bypass_whole_response(request_text, quantity, purpose):
    assert approval.conversational_choice(f'Okay. {request_text} {quantity} {purpose}.') is None


@pytest.mark.parametrize('residue', [
    'The amber lantern must glow', 'Quux', 'The matter remains unsettled',
    'Zeta owns the final call', 'It merits another look',
    'Additional contemplation is essential', 'Circle back next week',
    'The committee is still forming its view', 'Nothing is settled',
    '△', 'okayish', 'x=1',
])
@pytest.mark.parametrize('template', [
    'Okay. {}.', 'Approved, {}!', 'LGTM — {}.',
    'Go ahead. “{}”. Approved.', 'Approved.\n> {}.',
    'Approved.\n```\n{}\n```', '{}. Approved.',
    'Okay. We will publish tomorrow. {}. I approve this phase now.',
])
def test_unclassified_content_never_inherits_an_affirmative_opening(residue, template):
    # None of these clauses needs a recognized time/consent keyword to make the
    # complete response ambiguous. Work and repeated grants cannot exempt it.
    assert approval.conversational_choice(template.format(residue)) is None


@pytest.mark.parametrize('request_text', [
    "I'll need 5 minutes", 'We need a day', 'The reviewers need more time',
    'The release council needs another week',
])
@pytest.mark.parametrize('template', [
    'Okay. {request} to implement the plan and to deliberate.',
    'Okay. {request} to implement the plan; and to deliberate.',
    'Approved. “{request} to implement the plan\nand to deliberate”.',
    'LGTM. {request} to deliberate and to implement the plan.',
    'Go ahead. To deliberate, {request} to implement the plan. Approved.',
    'Approved. We will publish tomorrow. {request} to think it over.',
])
def test_mixed_purposes_stay_unapproved_across_requester_and_presentation(request_text, template):
    assert approval.conversational_choice(template.format(request=request_text)) is None


@pytest.mark.parametrize('fragment', [
    'I will', 'need', '5 minutes', 'to deliberate', 'reviewers', 'quux',
])
@pytest.mark.parametrize('separator', ['. ', '; ', '\n', ' — ', ', '])
def test_unknown_fragments_cannot_be_stitched_to_benign_clauses(fragment, separator):
    text = f'Approved. We need time to implement the plan{separator}{fragment}.'
    assert approval.conversational_choice(text) is None


@pytest.mark.parametrize('clause', [
    'All required checks passed', 'Outstanding work',
    'The results look okay', 'The results are ready',
    'Results clearly show over 50% reduction', 'We will implement the plan',
    'We will publish tomorrow', 'We need time to implement the plan and publish the release',
])
@pytest.mark.parametrize('template', [
    'Approved. {}.', 'Okay; “{}”. I approve this phase now.',
    'Looks good, proceed. {}. My approval is granted.',
])
def test_supported_explanations_and_work_remain_complete_positive_controls(clause, template):
    assert approval.conversational_choice(template.format(clause)) == 'approved'


@pytest.mark.parametrize('text', [
    'The results are ready.', 'We will implement the plan.',
    '“Approved.”', '`Go ahead`', '> I approve this phase',
    '“Approved.” The results are ready.', 'Approvedness',
])
def test_benign_or_quoted_content_cannot_supply_unquoted_assent(text):
    assert approval.conversational_choice(text) is None
