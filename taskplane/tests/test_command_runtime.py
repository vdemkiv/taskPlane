from copy import deepcopy
import json
import pytest
from taskplane import command_runtime as c, workflow as w


class Processes:
    def __init__(self):self.records={};self.complete=True;self.extra=[];self.cancelled=[]
    def read_process(self,handle):return deepcopy(self.records[handle])
    def process_census(self,root,run):
        return {'root':root,'run':run,'complete':self.complete,'active_handles':self.extra+[h for h,r in self.records.items() if r['binding']['run']==run and (r['state'] not in c.TERMINAL or not r['tree_quiescent'])]}
    def cancel_process(self,handle,binding):self.cancelled.append(handle)


def runtime(tmp_path):
    ws=tmp_path/'workspace';ws.mkdir()
    store=tmp_path/'commands.json';store.write_text(json.dumps({'schema':'taskplane.native-commands/v1','workspace':str(ws),'root':'root','handles':{}}))
    owner=Processes();binding={'workspace':str(ws),'root':'root','run':'run','visit':'visit','revision':2}
    owner.records['handle']={'handle':'handle','binding':binding,'process_start':'native-start-1','state':'running','tree_quiescent':False,'output':'PRIVATE'}
    rt=c.CommandRuntime(store,workspace=ws,root='root',observer=owner)
    return rt,owner,binding


def test_bound_handle_interrupt_reconnect_and_terminal_replay(tmp_path):
    rt,owner,binding=runtime(tmp_path)
    r=rt.create('handle',binding);assert r['revision']==0
    assert rt.reconnect('handle',binding)==r
    for wrong in [binding|{'revision':3},binding|{'visit':'other'}]:
        with pytest.raises(w.Refusal):rt.reconnect('handle',wrong)
    owner.records['handle']['state']='input_required'
    with pytest.raises(w.Refusal):rt.reconnect('handle',binding)
    owner.records['handle'].update(state='completed',tree_quiescent=True)
    r=rt.snapshot('handle');assert rt.snapshot('handle')==r
    assert rt.quiescent('run')
    assert 'PRIVATE' not in rt.path.read_text()
    owner.records['handle']['state']='running'
    with pytest.raises(w.Refusal):rt.snapshot('handle')


def test_cancel_revokes_input_but_does_not_manufacture_exit(tmp_path):
    rt,owner,binding=runtime(tmp_path);r=rt.create('handle',binding)
    result=rt.cancel('handle',expected_revision=r['revision'])
    assert result['revoked'] and result['state']=='running' and not rt.quiescent('run')
    with pytest.raises(w.Refusal):rt.reconnect('handle',binding)
    owner.records['handle'].update(state='cancelled',tree_quiescent=True)
    assert rt.quiescent('run') and owner.cancelled==['handle']
    with pytest.raises(w.Refusal):rt.cancel('handle',expected_revision=0)


def test_unknown_process_or_reused_start_identity_blocks_seal(tmp_path):
    rt,owner,binding=runtime(tmp_path);rt.create('handle',binding)
    owner.records['handle'].update(state='completed',tree_quiescent=True)
    assert rt.quiescent('run')
    owner.extra=['background-child'];assert not rt.quiescent('run')
    owner.extra=[];owner.complete=False;assert not rt.quiescent('run')
    owner.complete=True;owner.records['handle']['process_start']='reused'
    assert not rt.quiescent('run')
    with pytest.raises(w.Refusal):rt.create('handle',binding)


def test_restart_preserves_revocation_and_corruption_never_resets(tmp_path):
    rt,owner,binding=runtime(tmp_path);r=rt.create('handle',binding);rt.cancel('handle',expected_revision=r['revision'])
    restarted=c.CommandRuntime(rt.path,workspace=rt.workspace,root=rt.root,observer=owner)
    with pytest.raises(w.Refusal):restarted.reconnect('handle',binding)
    rt.path.write_text('{broken')
    with pytest.raises(w.Refusal):restarted.create('handle',binding)
    assert rt.path.read_text()=='{broken'


def test_lost_native_ownership_cannot_be_cancelled(tmp_path):
    rt,owner,binding=runtime(tmp_path);rt.create('handle',binding)
    owner.records['handle']['binding']=binding|{'run':'other'}
    with pytest.raises(w.Refusal):rt.cancel('handle',expected_revision=0)
    assert owner.cancelled==[]


def recovery_fixture(tmp_path):
    from taskplane.context import Store
    from taskplane.context_handoff import Session
    from taskplane.context_views import summary
    from taskplane.tests.test_workflow_evidence import prepare
    from taskplane import workflow_evidence as evidence
    state, _, _ = prepare(tmp_path)
    state = w.submit(state, evidence.seal(tmp_path, state, 'product.json', 'tasks.json'))
    # Populated sealed graph/task snapshots caused the live 27,576-node failure.
    w.current(state)['packet']['context']['graph']['snapshot'] = {
        'files': {f'file-{i}.py': {'symbols': list(range(12)), 'digest': 'same'} for i in range(2000)}}
    store = Store(tmp_path)
    session = Session(tmp_path, state)
    result = summary(store, {'workflow': state, 'status': 'blocked',
        'reason': 'approval_required', 'detail': 'A human checkpoint decision is required.'},
        'advance', session.descriptor())
    ref = result['errors']['details']
    roots = store.roots(session.read_binding)
    store.register(session.read_binding, [store.put('unrelated', {'number': i})
                   for i in range(243 - len(roots))])
    return state, store, session, ref


def test_recovery_opens_exact_source_bound_result_before_large_state_or_other_objects(tmp_path, monkeypatch):
    from taskplane.context_handoff import binding
    state, store, session, ref = recovery_fixture(tmp_path)
    assert store.roots(binding(state)) == []
    assert len(store.roots(session.read_binding)) == 243
    def scalar_count(value):
        return 1 + sum(map(scalar_count, value.values() if isinstance(value, dict)
                         else value if isinstance(value, list) else []))
    assert scalar_count(state) > 27576
    monkeypatch.setattr(c, '_retained_references', lambda _: pytest.fail('Unrelated state was scanned'))
    # A registered sibling need not be opened to inspect this exact result.
    sibling = next(row for row in store.roots(session.read_binding) if row['kind'] == 'unrelated')
    store.path(sibling['sha256']).write_text('corrupt unrelated object')
    before = sorted(path.name for path in store.root.rglob('*.json'))
    result = c.inspect_recovery(tmp_path, state, 'result', ref['sha256'], 3, 11)
    assert result['text'].encode() == store._bytes(ref['sha256'])[3:14]
    assert result['next_offset'] == 14 and result['authority'] == 'none'
    assert sorted(path.name for path in store.root.rglob('*.json')) == before


@pytest.mark.parametrize('change', ['source', 'run', 'root', 'visit', 'revision', 'scope', 'generation'])
def test_recovery_rejects_stale_or_foreign_registered_result(tmp_path, change):
    state, _, _, ref = recovery_fixture(tmp_path)
    changed = deepcopy(state)
    if change == 'source': (tmp_path/'app.py').write_text('value = 2\n')
    elif change == 'visit': changed['visits'][changed['index']]['id'] = 'another-visit'
    elif change == 'scope': changed['scope']['criteria'].append('another-criterion')
    elif change == 'generation': changed['task_generation'] = 1
    elif change == 'revision': changed['revision'] += 1
    else: changed[change] = 'foreign'
    with pytest.raises(w.Refusal, match='not registered'):
        c.inspect_recovery(tmp_path, changed, 'result', ref['sha256'])


@pytest.mark.parametrize('damage', ['metadata', 'bytes', 'oversized', 'symlink', 'missing'])
def test_recovery_validates_selected_registered_object(tmp_path, damage):
    from taskplane.context import OBJECT_LIMIT, digest
    state, store, session, ref = recovery_fixture(tmp_path)
    path = store.path(ref['sha256'])
    if damage == 'metadata':
        index = store.root/'reads'/f'{digest(session.read_binding)}.json'
        value = json.loads(index.read_text())
        next(row for row in value['roots'] if row['sha256'] == ref['sha256'])['kind'] = 'forged'
        index.write_text(json.dumps(value))
    elif damage == 'bytes': path.write_text('{}')
    elif damage == 'oversized': path.write_bytes(b'x' * (OBJECT_LIMIT + 1))
    else:
        raw = path.read_bytes(); path.unlink()
        if damage == 'symlink':
            target = tmp_path/'outside.json'; target.write_bytes(raw); path.symlink_to(target)
    with pytest.raises(w.Refusal):
        c.inspect_recovery(tmp_path, state, 'result', ref['sha256'])


@pytest.mark.parametrize('source', ['packet', 'inherited', 'worker'])
def test_retained_result_and_children_survive_approval_revision(tmp_path, source):
    from taskplane.context import Store
    from taskplane.tests.test_workflow_evidence import prepare
    state, _, _ = prepare(tmp_path)
    store = Store(tmp_path)
    ref = store.put('finding-evidence', {'report': 'retained evidence ' * 1000})
    if source == 'packet':
        state['visits'][state['index']]['packet'] = {'output': {}, 'finding_evidence': {'report.md': ref},
                                                   'context': {'graph': {'scalar': list(range(30000))}}}
    elif source == 'inherited':
        state['inherited_findings'] = {'findings': [], 'verification_histories': [{'record_ref': ref}]}
    else:
        state['workers'] = {'attempt': {'launch_proof_refs': [ref]}}
    state['revision'] += 1
    child = store.children(store.node(ref))[0]
    for key in (ref['sha256'], child['sha256']):
        result = c.inspect_recovery(tmp_path, state, 'result', key)
        assert result['sha256'] == key
    foreign = store.put('private', 'must remain outside reachable evidence')
    with pytest.raises(w.Refusal, match='not registered'):
        c.inspect_recovery(tmp_path, state, 'result', foreign['sha256'])


@pytest.mark.parametrize('bound', ['nodes', 'bytes', 'depth'])
def test_unregistered_recovery_traversal_has_independent_bounds(tmp_path, monkeypatch, bound):
    from taskplane.context import Store
    from taskplane.context_handoff import binding
    from taskplane.tests.test_workflow_evidence import prepare
    state, _, _ = prepare(tmp_path)
    store = Store(tmp_path)
    leaf = store.put('result', 'owned result')
    root = leaf
    for i in range(35):
        root = store._object('result', [root], f'level-{i}', 'list')
    store.register(binding(state), [root])
    if bound == 'nodes': monkeypatch.setattr(c, 'RECOVERY_NODES', 3)
    if bound == 'bytes': monkeypatch.setattr(c, 'RECOVERY_BYTES', 100)
    with pytest.raises(w.Refusal, match='bound'):
        c.inspect_recovery(tmp_path, state, 'result', leaf['sha256'])
    # The exact official root remains directly readable; traversal limits do not
    # apply to unrelated children when reading an explicitly registered object.
    assert c.inspect_recovery(tmp_path, state, 'result', root['sha256'])['sha256'] == root['sha256']
