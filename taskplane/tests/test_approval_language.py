"""Semantic candidates are interpreted by the orchestrator, never a phrase parser."""
from copy import deepcopy
import pytest
from taskplane import workflow as w, workflow_approval as approval
from taskplane.tests.test_workflow_local import setup, submit, decision, decide


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
