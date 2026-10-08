"""Approval repair regressions; all human/hook observations are isolated fixtures."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from taskplane import flow, workflow as w, workflow_host as h, workflow_local as local
from taskplane.tests.test_workflow_local import setup, submit, decision, decide

AFFIRMATIVES = ['Approved.', 'I approve this checkpoint.', 'Looks good, proceed.', 'I sign off on this checkpoint.', 'I confirm the checkpoint.', 'I authorize this work.', 'My approval is given.', 'You have my approval.', 'Looks good to me.', 'Approved, thanks.', 'Yes, please proceed.', 'This meets the requirements; go ahead.', 'I am happy with this checkpoint. Please proceed.', 'Everything checks out. I approve this checkpoint.', 'That works for me; proceed.', 'The current checkpoint is approved.', 'We approve this checkpoint.', 'I hereby approve this checkpoint.', 'Yes, approved as presented.', 'I approve the Product phase.']

UNAUTHORIZED = ['I do not approve this checkpoint.', 'Approved if the tests pass.', 'Approved. Only for documentation.', 'Approved, but not the current checkpoint.', 'Approve the tests only.', 'I approve the Product phase, not the Design phase.', 'Approved. Actually, I withdraw my approval.', 'Approved. I revoke that approval.', 'Approved. Wait, stop.', 'Approved. Never mind.', 'Approved. On second thought, no.', 'I approved the previous checkpoint.', 'I will approve this checkpoint.', 'My manager said "Approved".', 'The assistant says approved.', '"Approved."', 'Could you approve it?', 'Okay, keep reviewing.', 'Proceed with the review, not implementation.', 'Yes, explain the changes.', 'Approved pending legal review.', 'I approve some of these changes.', 'All but the deployment is approved.', 'Approved for staging only.', 'Approved unless the scope changed.']


@pytest.mark.parametrize('text', AFFIRMATIVES)
def test_clear_paraphrases_preserve_exact_human_excerpt(tmp_path, text):
    c, s = setup(tmp_path); s = submit(c, s)
    value = decision(s, text=text)
    accepted = decide(c, s, value)
    assert w.current(accepted)['decision'] == 'approved'
    assert accepted['decisions'][value['event_id']]['provenance']['excerpt'] == text


@pytest.mark.parametrize('text', UNAUTHORIZED + [
    'Thanks.', 'The tests passed.', 'Everything checks out.',
    'This meets the requirements.', 'I sign off on some of this.',
    'I sign off on this checkpoint pending review.',
    'My approval is given only for tests.', 'You have my approval if tests pass.',
    'Looks good to me except for the safety check.',
    'We approve this checkpoint. I withdraw my approval.',
    'Approved, thanks. I still need to approve this.',
    'Everything checks out. "I approve this checkpoint."',
    'I sign off on this checkpoint?','Cancel. You have my approval.',
    'Do not revoke my approval.', 'Withdraw my approval if tests fail.',
    'Revoke my approval. Never mind.',
])
def test_unknown_conditional_partial_and_retracted_intent_cannot_approve(tmp_path, text):
    c, s = setup(tmp_path); s = submit(c, s)
    value = decision(s, text=text); value['choice'] = 'approved'
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal): decide(c, s, value)
    assert c._path().read_bytes() == before


@pytest.mark.parametrize('text', [
    'I sign off on Build.', 'The Design phase is approved.',
    'I confirm the Engineering phase.', 'I authorize the Plan phase.',
    'Product approved. Design approved.', 'Product approved and accept Build.',
    'I sign off on this checkpoint. Build is signed off.',
    'I sign the Build phase off.', 'I am happy with Design.', 'Apparoved Build.',
])
def test_every_named_phase_remains_bound(tmp_path, text):
    c, s = setup(tmp_path); s = submit(c, s)
    assert local.choice(text) == 'approved'
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal, match='different phase'):
        decide(c, s, decision(s, text=text))
    assert c._path().read_bytes() == before


@pytest.mark.parametrize('timing', ['pending', 'approved', 'policy-approved'])
@pytest.mark.parametrize('route', ['controller', 'hook'])
@pytest.mark.parametrize('text', ['Cancel this workflow.', 'I withdraw my approval.'])
def test_cancellation_is_durable_append_only_and_revokes_continuation(tmp_path, timing, route, text):
    c, s = setup(tmp_path)
    if timing == 'policy-approved':
        from taskplane.tests.test_workflow_autonomy import set_policy, auto
        s = set_policy(c, s)
    s = submit(c, s)
    approval = decision(s)
    if timing == 'approved': s = decide(c, s, approval)
    elif timing == 'policy-approved': s = auto(c, s)
    old = deepcopy(s)
    value = decision(s, text=text, event='later-human-cancellation')
    if route == 'controller':
        cancelled = decide(c, s, value)
    else:
        flow.hook({'hook_event_name':'UserPromptSubmit', 'cwd':str(tmp_path),
                   'session_id':'root', 'prompt':text, 'taskplane_decision':value}, governor=c)
        cancelled = c.report()
    assert cancelled['revision'] == old['revision'] + 1
    assert w.current(cancelled)['decision'] == 'cancelled'
    assert all(cancelled['decisions'][k] == v for k, v in old['decisions'].items())
    assert cancelled['history'][:-1] == old['history']
    assert cancelled['history'][-1] == {'cancellation':cancelled['cancellation']}
    assert cancelled['cancellation']['binding'] == value['binding']
    assert cancelled['decisions'][value['event_id']]['provenance']['excerpt'] == text
    if timing == 'policy-approved': assert cancelled['policy_suspension']
    resumed = h.Controller(tmp_path, 'root', h.installed_adapter('codex'))
    raw = c._path().read_bytes()
    assert decide(resumed, s, value)['cancellation'] == cancelled['cancellation']
    if timing == 'approved':
        replayed = resumed.apply('decide', s['run'], expected_revision=approval['binding']['revision'],
                                 native_reference=json.dumps(approval))
        assert replayed['cancellation'] == cancelled['cancellation']
    assert c._path().read_bytes() == raw
    for action in ('advance', 'finish', 'submit'):
        with pytest.raises(w.Refusal, match='cancelled'):
            resumed.apply(action, s['run'], expected_revision=cancelled['revision'],
                          phase='design', output='product.json', tasks='tasks.json')
    with pytest.raises(w.Refusal):
        resumed.guard({'tool_name':'Write', 'tool_input':{'path':'product.json'}}, s['run'])
    # Drift, resume, and a previously returned context cannot reopen the run.
    (tmp_path/'product.json').write_text('{}')
    assert resumed.report()['status'] == 'cancelled'
    with pytest.raises(w.Refusal):
        resumed.guard({'tool_name':'Write', 'tool_input':{'path':'product.json'}}, s['run'])
    assert c._path().read_bytes() == raw
    assert flow.hook({'hook_event_name':'Stop', 'cwd':str(tmp_path), 'session_id':'root'}, governor=resumed) == {}


@pytest.mark.parametrize('defect', ['root','run','visit','revision','checkpoint','scope_digest',
                                  'manifest_digest','actor','automatic','conversation','chronology'])
def test_cancellation_never_accepts_stale_or_fabricated_binding(tmp_path, defect):
    c, s = setup(tmp_path); s = decide(c, submit(c, s))
    value = decision(s, text='Cancel this workflow.', event='cancel')
    if defect in value['binding']:
        value['binding'][defect] = 'foreign'
    elif defect == 'actor': value['source']['actor'] = 'assistant'
    elif defect == 'automatic': value['source']['automatic'] = True
    elif defect == 'conversation': value['source']['conversation'] = 'foreign'
    else:
        earlier = next(iter(s['decisions'].values()))['provenance']
        value['source']['observed_at'] = earlier['source']['observed_at']
        value['presentation'] = earlier['presentation']
    raw = c._path().read_bytes()
    with pytest.raises(w.Refusal): decide(c, s, value)
    assert c._path().read_bytes() == raw
    assert c.report()['status'] == 'approved'


def test_only_explicit_new_run_can_restart_cancellation(tmp_path):
    from taskplane.tests.test_workflow_recovery import restart_request
    c, s = setup(tmp_path); s = decide(c, submit(c, s), None)
    s = decide(c, s, decision(s, text='Cancel.', event='cancel'))
    old = deepcopy(s)
    replacement = c.start(restart_request(s))
    assert replacement['run'] != old['run'] and not replacement.get('cancellation')
    assert replacement['decisions'] == {} and w.current(replacement)['decision'] == 'not_requested'
    historical = c.report(old['run'])
    assert historical['decisions'] == old['decisions']
    assert historical['cancellation'] == old['cancellation']


def test_cancelled_worker_grants_do_not_imply_process_termination(tmp_path):
    from taskplane.tests.test_worker_runtime import setup as worker_setup, reserve, launch
    from taskplane import worker_runtime as workers
    c, s = worker_setup(tmp_path, count=1)
    prepared = reserve(c, s); launch(c, s, prepared)
    # Exercise the current-grant predicate without pretending a native worker
    # completed. A late observation must still be joined independently.
    db = c._read(c._path()); s = db['runs'][s['run']]
    row = s['workers'][prepared['grant']['grant_id']]
    s['cancellation'] = {'event_id':'fixture-only'}
    before = deepcopy(row)
    with pytest.raises(w.Refusal, match='cancelled'): workers.current(s, row)
    assert row == before and not workers.joined(s)
    assert workers.admit(s, {'tool_name':'interrupt_agent', 'tool_input':{'target':row['worker_id']}})
    assert row['state'] == 'cancel_requested' and not workers.joined(s)
    workers.terminal(row, 'interrupted', 'fixture-native-terminal')
    assert workers.joined(s)
    with pytest.raises(w.Refusal, match='cancelled'): workers.current(s, row)


def test_pure_cancelled_state_cannot_be_resubmitted_or_invalidated_into_work():
    from taskplane.tests.test_workflow import state, ready, answer
    s = ready(state()); s = w.decide(s, answer(s, 'cancelled'))
    for operation in (lambda: ready(s), lambda: w.advance(s, 'design'), lambda: w.finish(s)):
        with pytest.raises(w.Refusal): operation()
    assert w.invalidate(s, w.current(s)['id'], 'changed') == s


def test_seven_phases_across_fresh_processes(tmp_path):
    """No in-memory controller survives a checkpoint transition.

    Explicit synthetic approval fixture; tests persistence, not human/native
    authority. Each child uses the copied installed code and isolated host dirs.
    """
    c, initial = setup(tmp_path)
    runtime_root = str(Path(__file__).resolve().parents[2])
    probe = "\nimport json, sys\nfrom pathlib import Path\nsys.path.insert(0, sys.argv[1])\nfrom taskplane import workflow as w, workflow_host as h, workflow_local as local\nfrom taskplane.tests.test_workflow_local import submit, decision, decide\nc = h.Controller(Path(sys.argv[2]), 'root', h.installed_adapter('codex'))\ns = c.report()\nmode = sys.argv[3]\nphase = w.current(s)['phase']\nif mode == 'submit':\n    s = submit(c, s)\nelif mode == 'decide':\n    value = decision(s, event='synthetic-human-' + phase)\n    (c.workspace / '.taskplane' / ('decision-' + phase + '.json')).write_text(json.dumps(value))\n    s = decide(c, s, value)\nelif mode == 'replay':\n    value = json.loads((c.workspace / '.taskplane' / ('decision-' + phase + '.json')).read_text())\n    raw = c._path().read_bytes()\n    s = c.apply('decide', s['run'], expected_revision=value['binding']['revision'], native_reference=json.dumps(value))\n    assert c._path().read_bytes() == raw\nelif mode == 'advance':\n    index = w.PHASES.index(phase)\n    s = c.apply('advance' if index < 6 else 'finish', s['run'], expected_revision=s['revision'], phase=w.PHASES[index+1] if index < 6 else '')\nelif mode == 'guard':\n    raw = c._path().read_bytes()\n    try:\n        c.guard({'tool_name':'Write', 'tool_input':{'path':'app.py'}}, s['run'])\n    except w.Refusal:\n        assert c._path().read_bytes() == raw\n    else:\n        raise AssertionError('Protected source mutation admitted while awaiting approval')\nprint(json.dumps({'run':s['run'], 'revision':s['revision'], 'phase':w.current(s)['phase'], 'decisions':s['decisions'], 'visits':s['visits'], 'finished':s.get('finished'), 'mode':mode}))\n"
    rows = []
    prior_decisions = {}
    for index, phase in enumerate(w.PHASES):
        for mode in ('report', 'submit', 'guard', 'decide', 'replay', 'advance'):
            checked = subprocess.run([sys.executable, '-I', '-c', probe, runtime_root, str(tmp_path), mode], capture_output=True, text=True, env=os.environ.copy(), timeout=45)
            assert checked.returncode == 0, checked.stdout + checked.stderr
            state = json.loads(checked.stdout)
            assert state['run'] == initial['run']
            assert all((state['decisions'].get(k) == v for k, v in prior_decisions.items()))
            prior_decisions = deepcopy(state['decisions'])
            rows.append({'phase_under_test': phase, 'mode': mode, 'result': state})
    assert state['finished'] and len(state['decisions']) == 7


def test_bounded_context_reconstructed_by_separate_cli_processes(tmp_path):
    from taskplane.tests.test_native_workflow_cli import create
    from taskplane.tests.test_context_cli import command
    create(tmp_path)
    start, size = command(tmp_path, 'start', '--scope', '.taskplane/scope.json', '--request-reference', 'synthetic-review/request')
    prepared, _ = command(tmp_path, 'context', '--run', start['run'])
    key = prepared['handoff_ref']['sha256']
    delivered, size = command(tmp_path, 'context', '--drain', key)
    assert delivered['done'] and delivered['remaining_required'] == 0
    assert delivered['context_receipt'] and size <= 32768
    replay, replay_size = command(tmp_path, 'context', '--drain', key)
    assert replay['done'] and replay['pages'] == []
    after, _ = command(tmp_path, 'report', '--full')
    assert after['workflow']['run'] == start['run']
    assert after['workflow']['context_contract'] == 'bounded/v2'
    assert after['workflow']['decisions'] == {}


def test_cancellation_write_failure_is_not_acknowledged(tmp_path, monkeypatch):
    c, s = setup(tmp_path); s = decide(c, submit(c, s))
    value = decision(s, text='Cancel this workflow.', event='cancel')
    raw = c._path().read_bytes()
    with monkeypatch.context() as patch:
        def failed(*args): raise OSError('fixture durable storage failure')
        patch.setattr(c, '_write', failed)
        with pytest.raises(OSError, match='durable storage'):
            decide(c, s, value)
    assert c._path().read_bytes() == raw
    assert c.report()['status'] == 'approved'
    cancelled = decide(c, s, value)
    assert cancelled['cancellation']['event_id'] == 'cancel'


def test_cancel_and_advance_serialize_on_revision(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    c, s = setup(tmp_path); s = decide(c, submit(c, s))
    value = decision(s, text='Cancel this workflow.', event='cancel')
    def invoke(action):
        controller = h.Controller(tmp_path, 'root', h.installed_adapter('codex'))
        try:
            if action == 'cancel': decide(controller, s, value)
            else: controller.apply('advance', s['run'], expected_revision=s['revision'], phase='design')
            return action, True
        except w.Refusal:
            return action, False
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = dict(pool.map(invoke, ['cancel', 'advance']))
    assert sum(outcomes.values()) == 1
    result = c.report()
    assert bool(result.get('cancellation')) == outcomes['cancel']
    assert (w.current(result)['phase'] == 'design') == outcomes['advance']
    assert all(result['decisions'][key] == value for key, value in s['decisions'].items())


@pytest.mark.parametrize('defect', ['history', 'event', 'binding', 'status', 'event_type'])
def test_durable_cancellation_cannot_lose_its_audit_record(tmp_path, defect):
    c, s = setup(tmp_path); s = submit(c, s)
    s = decide(c, s, decision(s, text='Cancel.', event='cancel'))
    if defect == 'history': s['history'].clear()
    elif defect == 'event': s['decisions'].pop('cancel')
    elif defect == 'binding': s['cancellation']['binding']['revision'] += 1
    elif defect == 'status': s['visits'][s['index']]['decision'] = 'stale'
    elif defect == 'event_type': s['cancellation']['event_id'] = []
    with pytest.raises(w.Refusal): w.validate_state(s)
