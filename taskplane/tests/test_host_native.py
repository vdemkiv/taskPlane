from copy import deepcopy
import json
import os
import sys
from pathlib import Path
import pytest
from taskplane import host_native as n, workflow as w, workflow_host as h, storage
from taskplane.tests.test_workflow_evidence import prepare


class Owner:
    """Internal test owner, never a production native identity claim."""
    def __init__(self, workspace, control, scope):
        self.context={'host':'codex','version':'test-1','workspace':str(workspace),'root':'root'}
        self.control=control;self.events={};self.scope=scope;self.revoked=False
    def identity(self): return deepcopy(self.context)
    def capabilities(self):return {k:not self.revoked for k in n.caps.CAPABILITIES}
    def control_path(self):return self.control
    def read_event(self, reference):return deepcopy(self.events[reference])
    def read_scope(self, reference):return deepcopy(self.events[reference])


def native_owner(tmp_path):
    ws=tmp_path/'workspace';ws.mkdir();initial,_,_=prepare(ws)
    control=tmp_path/'protected.json';control.write_text(json.dumps({'schema':'taskplane.control/v1','workspace':str(ws),'root':'root','active':None,'runs':{}}))
    owner=Owner(ws,control,initial['scope'])
    session=n.NativeSession(owner,host='codex',version='test-1',workspace=ws,root='root')
    return owner,session


def human(owner, reference, checkpoint=None, **overrides):
    owner.events[reference]={'reference':reference,'event_id':reference,'session':deepcopy(owner.context),
                             'origin':'human','automatic':False,'resolved':True,'choice':'approved','binding':checkpoint,**overrides}


def test_independent_event_binding_and_revocation(tmp_path):
    owner,session=native_owner(tmp_path)
    expected={'workspace':str(session.workspace),'root':'root','run':'r','visit':'v','revision':1,'manifest_digest':'output','scope_digest':'scope','checkpoint':'c'}
    human(owner,'real',expected)
    result=session.verify_decision('real',expected)
    assert result=={'event_id':'real','human':True,'automatic':False,'choice':'approved','binding':expected}
    for change in [{'origin':'automation'},{'automatic':True},{'resolved':False},{'session':owner.context|{'version':'stale'}},{'binding':expected|{'revision':0}},{'actor':'user','origin':None}]:
        human(owner,'bad',expected,**change)
        with pytest.raises(w.Refusal):session.verify_decision('bad',expected)
    with pytest.raises(w.Refusal):session.verify_decision('workspace-receipt',expected)
    owner.revoked=True
    assert not any(session.capabilities().values())
    with pytest.raises(w.Refusal):session.verify_decision('real',expected)


def test_start_uses_native_scope_and_checks_requested_entry(tmp_path):
    owner,session=native_owner(tmp_path)
    authorization={'entry':'product','standalone':False,'scope':owner.scope}
    human(owner,'scope',authorization=authorization)
    adapter=h.CodexAdapter(session)
    c=h.Controller(session.workspace,'root',adapter)
    with pytest.raises(w.Refusal):c.start({'actor':'human','authenticated':True})
    request={'entry':'product','standalone':False,'native_reference':'scope'}
    assert session.verify_start(request)['scope']==owner.scope
    # Session provenance without a matching native process owner is insufficient.
    with pytest.raises(w.Refusal):c.start(request)
    with pytest.raises(w.Refusal):session.verify_start({'native_reference':'scope','entry':'engineering','standalone':True})
    assert not h.ClaudeAdapter(session).capabilities()['human_origin']
    assert not h.installed_adapter('codex').capabilities()['human_origin']


def test_changed_native_identity_or_control_location_refuses(tmp_path):
    owner,session=native_owner(tmp_path)
    assert session.capabilities()['protected_store']
    owner.context['root']='other'
    assert not session.capabilities()['protected_store']
    owner.context['root']='root';owner.control=session.workspace/'forged.json';owner.control.write_text('{}')
    assert not session.capabilities()['protected_store']
    with pytest.raises(w.Refusal):session.control_path(tmp_path,'other')


def test_control_dot_path_cannot_escape_workspace_check(tmp_path):
    workspace=tmp_path/'repo';workspace.mkdir();(workspace/'control.json').write_text('{}')
    (tmp_path/'outside').mkdir()
    disguised=tmp_path/'outside/../repo/control.json'
    with pytest.raises(ValueError):storage.control_file(workspace,disguised)
    protected=tmp_path/'control.json';protected.write_text('{}')
    assert storage.control_file(workspace,protected)==protected


@pytest.mark.skipif(sys.platform not in {'darwin','linux'}, reason='This platform has no implemented native process-start reader')
def test_real_process_start_identity_is_stable_and_not_ownership():
    assert n.process_start_identity(os.getpid())==n.process_start_identity(os.getpid())
    for value in [True,0,-1]:
        with pytest.raises(OSError):n.process_start_identity(value)
