"""Semantic candidates are interpreted by the orchestrator, never a phrase parser."""
from copy import deepcopy
import pytest
from taskplane import workflow as w, workflow_approval as approval
from taskplane.tests.test_workflow_local import setup, submit, decision, decide


def delayed_product(c, state):
    from taskplane import workflow_evidence as evidence
    from taskplane.primitives import content_fingerprint
    value = decision(state,event='delayed-product-approval')
    # Reproduce real ordering: content shown, human approves, delayed submit.
    value['presentation']['at'] = state['started_at']
    value['source']['observed_at'] = state['started_at']
    from datetime import datetime, timedelta
    value['source']['observed_at'] = (datetime.fromisoformat(state['started_at']) + timedelta(microseconds=1)).isoformat()
    packet = w.current(state)['packet']
    value['presentation']['content_binding'] = {
        'output':content_fingerprint(packet['output']),
        'scope':content_fingerprint(state['scope']),
        'tasks':content_fingerprint(evidence.task_definitions(packet['context']['tasks'])),
    }
    return value


def test_unchanged_first_product_preserves_approval_after_delayed_submit(tmp_path):
    c, state = setup(tmp_path)
    state = submit(c, state, presentation=False)
    value = delayed_product(c, state)
    assert value['source']['observed_at'] < w.current(state)['submitted_at']
    result = decide(c, state, value)
    assert w.current(result)['decision'] == 'approved'
    proof = result['decisions'][value['event_id']]['provenance']
    assert proof['source'] == value['source']
    assert proof['presentation'] == value['presentation']
    assert proof['chronology'] == 'content-bound-product/v1'
    assert c.apply('decide',result['run'],expected_revision=value['binding']['revision'],
                   native_reference=__import__('json').dumps(value)) == result


@pytest.mark.parametrize('defect',['output','scope','tasks','chronology','future','missing-content','foreign-source'])
def test_delayed_product_refuses_changed_content_or_invalid_chronology(tmp_path, defect):
    c, state = setup(tmp_path)
    state = submit(c, state, presentation=False)
    value = delayed_product(c, state)
    if defect in {'output','scope','tasks'}:
        value['presentation']['content_binding'][defect] = 'f' * 64
    elif defect == 'chronology':
        value['presentation']['at'] = '2000-01-01T00:00:00+00:00'
    elif defect == 'future':
        value['source']['observed_at'] = '2999-01-01T00:00:00+00:00'
    elif defect == 'foreign-source':
        value['source']['conversation'] = 'foreign'
    else:
        del value['presentation']['content_binding']
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        decide(c, state, value)
    assert c._path().read_bytes() == before


def test_delayed_product_binding_cannot_approve_a_later_phase(tmp_path):
    c, state = setup(tmp_path)
    state = decide(c,submit(c,state))
    state = c.apply('advance',state['run'],expected_revision=state['revision'],phase='design')
    state = submit(c,state,presentation=False)
    value = delayed_product(c,state)
    with pytest.raises(w.Refusal, match='Delayed Product approval'):
        decide(c,state,value)


def test_full_product_presentation_and_assent_are_preserved(tmp_path):
    c,state=setup(tmp_path);state=submit(c,state,presentation=False)
    value=decision(state,text='Looks good. Keep the separate phase decisions.',candidate='approved')
    value['presentation']['text']='Product covers the proposed outcome.\n- Remove unnecessary checks.\n- Preserve useful evidence.\nApprove this phase to proceed?'
    result=decide(c,state,value)
    event=result['decisions'][value['event_id']]
    assert event['provenance']['excerpt']==value['excerpt']
    assert event['provenance']['presentation']==value['presentation']
    assert result.get('approval_policy') is None
    assert c.apply("decide", result["run"], expected_revision=value["binding"]["revision"], native_reference=__import__("json").dumps(value)) == result


@pytest.mark.parametrize('candidate',['changes_requested','rejected','cancelled'])
def test_dissent_candidate_keeps_checkpoint_unapproved(tmp_path,candidate):
    c,state=setup(tmp_path);state=submit(c,state,presentation=False)
    value=decision(state,text='Please apply the correction we discussed.',candidate=candidate)
    result=decide(c,state,value)
    assert w.current(result)['decision']==candidate
    with pytest.raises(w.Refusal):c.apply('advance',result['run'],expected_revision=result['revision'],phase='design')


@pytest.mark.parametrize('host', ['codex', 'claude'])
@pytest.mark.parametrize('phase', ['product', 'design', 'plan'])
@pytest.mark.parametrize('dissent', ['changes_requested', 'rejected'])
def test_later_human_approval_reconsiders_unchanged_checkpoint(tmp_path, host, phase, dissent):
    c, state = setup(tmp_path)
    c.adapter.name = host
    while w.current(state)['phase'] != phase:
        state = submit(c, state, presentation=False)
        state = decide(c, state, decision(state, event='human/prior/' + w.current(state)['phase']))
        state = c.apply('advance', state['run'], expected_revision=state['revision'],
                        phase=w.PHASES[state['index'] + 1])
    state = submit(c, state, presentation=False)
    original = deepcopy(w.current(state))
    correction = decision(state, event='human/correction', candidate=dissent)
    state = decide(c, state, correction)
    assent = decision(state, event='human/reconsideration')
    assent['presentation'] = correction['presentation']
    result = decide(c, state, assent)
    assert w.current(result)['decision'] == 'approved'
    assert w.current(result)['packet'] == original['packet']
    assert w.current(result)['submitted_at'] == original['submitted_at']
    assert result['decisions']['human/correction'] == state['decisions']['human/correction']
    assert result['decisions']['human/reconsideration']['provenance']['presentation'] == correction['presentation']
    assert result['revision'] == state['revision'] + 1


@pytest.mark.parametrize('bad', ['ordering', 'source-drift', 'old-binding'])
def test_reconsideration_refuses_older_or_changed_checkpoint(tmp_path, bad):
    c, state = setup(tmp_path)
    state = submit(c, state, presentation=False)
    correction = decision(state, event='human/correction', candidate='changes_requested')
    state = decide(c, state, correction)
    assent = decision(state, event='human/reconsideration')
    assent['presentation'] = correction['presentation']
    if bad == 'ordering':
        assent['source']['observed_at'] = correction['source']['observed_at']
    elif bad == 'source-drift':
        (tmp_path / 'product.json').write_text('{}')
    else:
        assent['binding'] = correction['binding']
    with pytest.raises(w.Refusal):
        decide(c, state, assent)
    observed = c.report(state['run'], diagnostics=False)
    assert observed['decisions'] == state['decisions']
    assert w.current(observed)['decision'] != 'approved'


@pytest.mark.parametrize('bad',['foreign-source','automated','binding','future','missing-candidate','replay'])
def test_observed_candidate_cannot_replace_provenance_or_current_target(tmp_path,bad):
    c,state=setup(tmp_path);state=submit(c,state,presentation=False);value=decision(state)
    if bad=='foreign-source':value['source']['conversation']='foreign'
    elif bad=='automated':value['source']['automatic']=True
    elif bad=='binding':value['binding']['visit']='foreign'
    elif bad=='future':value['source']['observed_at']='2999-01-01T00:00:00+00:00'
    elif bad=='missing-candidate':value['choice']=None
    else:
        result=decide(c,state,value);value['choice']='rejected';state=result
    before=c._path().read_bytes()
    with pytest.raises(w.Refusal):decide(c,state,value)
    assert c._path().read_bytes()==before


@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_unrelated_identifiers_are_preserved_with_current_semantic_target(tmp_path, host):
    c, state = setup(tmp_path)
    c.adapter.name = host
    state = submit(c, state, presentation=False)
    value = decision(state, text='Approve Product; related run ' + 'e' * 32 + ' remains separate.')
    result = decide(c, state, value)
    assert result['decisions'][value['event_id']]['provenance']['excerpt'] == value['excerpt']
    assert w.current(result)['decision'] == 'approved'
