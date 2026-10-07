"""Exact language regressions; controller persistence is covered by representative cases."""
import pytest
from taskplane import workflow_approval as approval


@pytest.mark.parametrize('text', [
    'Okay. I am withholding approval.', "Okay. I'll sign it off tomorrow.",
    'LGTM. My approval is still to come.', 'Okay. Approval is to follow.',
    'Okay. I still need to sign this off.', 'Okay. Approval is forthcoming.',
    'Okay. Approval remains my responsibility.',
    'Okay. I will give the go-ahead tomorrow.', 'Okay. My green light will come later.',
    "Okay. I'll give my OK tomorrow.", 'Okay. I need more time.',
    'Okay. Let me think about it.', 'Okay. I will decide tomorrow.',
    'LGTM; "Allow us to consider this".',
    'Okay. I need more time to think about it.',
    'Okay. We require additional time to deliberate.',
    'Okay. I need time to think about it.',
    'Okay. We need some more time to deliberate.',
    'Okay. I need more time to really think this through.',
    'Okay. We require additional time for consideration.',
    'Okay. We need more time to finalize.',
    'Okay. "I need more time to think about it."',
    'Okay. “We require additional time to deliberate.”',
    "Okay. 'I need time to think about it.'",
    'Okay. `We need some more time to deliberate.`',
    'Okay.\n> I need more time to really think this through.',
    'Okay.\n```\nWe require additional time for consideration.\n```',
    'Approved. "We need more time to finalize." Approved.',
    'I need more time to think about it. Okay.',
    'We require additional time to deliberate. Okay.',
    'Go ahead. For further consideration, we need some more time. Approved.',
    'Approved. I need. More time to really think this through.',
    'Okay. We require additional; time for consideration.',
    'Approved. I need more time to think. We need more time to implement the plan.',
    'Approved. We need more time to implement the plan. I need more time to think.',
    'Approved. To deliberate, we need more time to implement the plan.',
    'Approved. We need more time to implement the plan; to think about it, I need time.',
    'Approved. I need. We need more time to implement the plan. More time to think.',
    # Shared purposes must remain part of the complete time request.
    'Okay. We need more time to implement the plan\nand to deliberate.',
    'Okay. We need more time to implement the plan; and to deliberate.',
    'Okay. We need more time to implement the plan and to deliberate.',
    'Okay. We need more time to implement the plan. And to deliberate.',
    'Okay. We need more time to implement the plan, and to deliberate.',
    'Okay. We need more time to implement the plan: and to deliberate.',
    'Okay. We need more time to implement the plan — and to deliberate.',
    'Approved. "We need more time to implement the plan; and to deliberate." Approved.',
    'Okay. We need more time to implement the plan; “and to deliberate”.',
    'Okay. We need more time to implement the plan; `and to deliberate`.',
    'Okay. We need more time to implement the plan\n> and to deliberate.',
    'Okay. We need more time to implement the plan.\n```\nand to deliberate\n```',
    'Okay. We need more time to deliberate\nand to implement the plan.',
    'Go ahead. For contemplation; we need more time to implement the plan. Approved.',
    'We need more time to implement the plan; and to deliberate. Okay.',
    'Okay. We need more time to implement the plan; for further contemplation.',
    'Okay. We need more time to implement the plan\nand for carefully considering the plan.',
    'Okay. We need more time to implement the plan; and to finalize.',
    'Okay. We need more time to implement the plan; for the next step.',
    'Okay. We need more time to implement the plan; to quuxify the decision process.',
    'Okay. We need more time to implement the plan; this remains undecided.',
    'Approved. We need more time to implement the plan. Approved. quux.',
    'Approved. We need more time to implement the plan; and to publish the release.',
    'Approved. We need more time to implement the plan\nplease and thank you.',
    # Broad time-request detection must not grant unknown quantity modifiers.
    'Approved. We need deliberation time to implement the plan.',
    'Okay. We need thinking time to build the feature. I approve this phase now.',
    'Approved. We need further consideration time to implement the plan.',
    'Go ahead. “I require my approval time for implementation of these changes”.',
    'Approved. We need permission time to implement the plan.',
    'Okay. We need quux time to build the feature. I approve this phase now.',
])
def test_checkpoint_language_retained_consent_refusal_preserves_pending_store(text):
    assert approval.conversational_choice(text) is None


@pytest.mark.parametrize('text', [
    'Approved.', 'Looks good, proceed.', 'Okay. I sign this off now.',
    'Okay. I give my green light now.', 'Okay. I grant the go-ahead.',
    'Approved. We will publish tomorrow.',
    'Approved. We need more time to implement the plan.',
    'Product approved. Results clearly show over 50% reduction.',
    'Approved. We need time to implement the plan and to build the feature.',
    'Approved. We need time to build the feature and publish the release.',
    'Okay. I require additional time for implementation of these changes.',
    'LGTM. We need time to ship the code.\nWe need time to publish the documentation.',
    'Go ahead. "We need more time to implement the plan". Approved.',
    'Approved. Our team needs a little extra time to publish the release tomorrow.',
    'Okay. We need more time to implement the plan. I approve this phase now.',
    'Okay. My approval is granted. We need time to build the feature. I sign this off now.',
])
def test_checkpoint_language_clear_consent_advances_and_retains_exact_excerpt(text):
    assert approval.conversational_choice(text) == 'approved'


@pytest.mark.parametrize('clause', [
    "I'll need more time to deliberate", 'I’ll need more time to deliberate',
    'I need 5 minutes to think it over', 'We need a day to think it over',
    'The reviewers need more time to deliberate',
    'I will need more time to deliberate', 'I need five minutes to think it over',
    'The reviewer needs more time to deliberate',
    'The release council needs another week for contemplation',
])
@pytest.mark.parametrize('template', [
    'Okay. {}.', 'LGTM; “{}”. Approved.', '{}.\nOkay.',
])
def test_checkpoint_language_request_form_never_bypasses_complete_response(clause, template):
    assert approval.conversational_choice(template.format(clause)) is None


@pytest.mark.parametrize('residue', [
    'The amber lantern must glow', 'Quux', 'Zeta owns the final call', '△',
])
@pytest.mark.parametrize('template', [
    'Approved, {}!', 'Okay. `{}`.', 'Approved.\n> {}.',
    'Approved.\n```\n{}\n```', '{}. Looks good, proceed.',
    'Okay. We will publish tomorrow. {}. I approve this phase now.',
])
def test_checkpoint_language_unknown_residue_requires_no_negative_keyword(residue, template):
    assert approval.conversational_choice(template.format(residue)) is None


@pytest.mark.parametrize('request_text', [
    "I'll need 5 minutes", 'We need a day', 'The reviewers need more time',
    'The release council needs another week',
])
@pytest.mark.parametrize('template', [
    'Okay. {} to implement the plan; and to deliberate.',
    'Go ahead. "{} to deliberate\nand to implement the plan". Approved.',
    'Approved. We need time to implement the plan. {} to think it over.',
])
def test_checkpoint_language_mixed_work_cannot_exempt_unknown_request(request_text, template):
    assert approval.conversational_choice(template.format(request_text)) is None


@pytest.mark.parametrize('clause', [
    'All required checks passed', 'The results are ready',
    'Results clearly show over 50% reduction', 'We will implement the plan',
    'We need time to implement the plan and publish the release',
    'I sign this off now',
])
@pytest.mark.parametrize('template', [
    'Looks good, proceed. {}.', 'Okay; “{}”. I approve this phase now.',
])
def test_checkpoint_language_complete_clear_work_and_explanation_retain_provenance(clause, template):
    assert approval.conversational_choice(template.format(clause)) == 'approved'
