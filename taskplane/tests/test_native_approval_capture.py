"""Real local controller plus synthetic native records; no live-host claim."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

import pytest

from taskplane import flow, native_approval as approval, workflow as w, workflow_local as local
from taskplane import claude_worker_observations as native
from taskplane.tests.test_workflow_local import setup, submit, decision, decide

pytestmark = pytest.mark.skipif(not native.supported_reader(), reason='Native no-follow reads unavailable')


def fixture(tmp_path, monkeypatch, *, text='approve'):
    c, state = setup(tmp_path)
    c.adapter.name = 'claude'
    state = submit(c, state)
    home = tmp_path.parent / (tmp_path.name + '-native-home')
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(home / '.claude'))
    path = home / '.claude/projects/fixture/root.jsonl'
    path.parent.mkdir(parents=True)
    def row(role, content):
        return {'type': role, 'sessionId': 'root', 'cwd': str(tmp_path), 'isSidechain': False,
            'uuid': str(uuid.uuid4()), 'timestamp': datetime.now(timezone.utc).isoformat(),
            'message': {'role': role, 'content': content},
            **({'origin': {'kind': 'human'}, 'turnOrigin': 'human', 'promptSource': 'typed'} if role == 'user' else {})}
    shown = row('assistant', f"Run {state['run']}, Product revision {state['revision']}. Approve Product or request changes.")
    human = row('user', text.replace('{checkpoint}', w.binding(state, w.current(state)['packet'])['checkpoint']))
    prefix = {'sessionId': 'root', 'cwd': str(tmp_path), 'isSidechain': False}
    def write(rows):
        path.write_text(''.join(json.dumps(r) + '\n' for r in [prefix, *rows]))
    write([shown, human])
    event = {'hook_event_name': 'UserPromptSubmit', 'host': 'claude', 'session_id': 'root',
             'cwd': str(tmp_path), 'transcript_path': str(path), 'prompt': human['message']['content']}
    return c, state, path, event, shown, human, write


@pytest.mark.parametrize('text', ['approve', 'Product approved', 'Approve: {checkpoint}'])
def test_ordinary_chat_approval_derives_real_ids_and_preserves_original_presentation(tmp_path, monkeypatch, text):
    c, state, _, event, shown, human, _ = fixture(tmp_path, monkeypatch, text=text)
    flow.hook(event, governor=c)
    after = c.report(diagnostics=False)
    assert after['status'] == 'approved' and after['revision'] == state['revision'] + 1
    proof = after['decisions'][human['uuid']]['provenance']
    assert proof['source']['reference'] == human['uuid']
    assert proof['source']['observed_at'] == human['timestamp']
    assert proof['presentation']['reference'] == shown['uuid']
    assert proof['presentation']['at'] == shown['timestamp']
    assert proof['recorder'] == 'native_prompt_hook'
    assert proof['native_proof']['human']['offset'] > proof['native_proof']['presentation']['offset']
    flow.hook(event, governor=c)
    assert c.report(diagnostics=False)['decisions'] == after['decisions']


def test_prompt_flush_is_deferred_then_captured_before_next_tool_guard(tmp_path, monkeypatch):
    c, state, _, event, shown, human, write = fixture(tmp_path, monkeypatch)
    write([shown])
    response = flow.hook(event, governor=c)
    assert c.report(diagnostics=False)['status'] == 'awaiting_human_approval'
    assert 'extract message IDs' in response['hookSpecificOutput']['additionalContext']
    write([shown, human])
    flow.hook({**event, 'hook_event_name': 'PreToolUse', 'tool_name': 'Read',
               'tool_input': {'file_path': 'product.json'}, 'tool_use_id': 'read-after-human'}, governor=c)
    assert c.report(diagnostics=False)['status'] == 'approved'


@pytest.mark.parametrize('defect', ['wrong_id', 'quoted', 'conditional', 'third_party', 'stale',
    'wrong_run', 'wrong_revision', 'foreign_session', 'later_presentation', 'newer_human', 'ambiguous', 'missing_id',
    'malformed_message', 'newer_malformed', 'external_prompt'])
def test_capture_rejects_nonhuman_stale_or_ambiguous_decisions(tmp_path, monkeypatch, defect):
    c, state, _, event, shown, human, write = fixture(tmp_path, monkeypatch)
    rows = [shown, human]
    if defect == 'wrong_id': human['message']['content'] = 'Approve: ' + 'f' * 32
    if defect == 'quoted': human['message']['content'] = '> approve'
    if defect == 'conditional': human['message']['content'] = 'approve if tests pass'
    if defect == 'third_party': human.update(origin={'kind': 'peer'}, turnOrigin='peer')
    if defect == 'stale': human['timestamp'] = '2000-01-01T00:00:00Z'
    if defect == 'wrong_run': shown['message']['content'] = shown['message']['content'].replace(state['run'], 'f' * 32)
    if defect == 'wrong_revision': shown['message']['content'] = shown['message']['content'].replace(f"revision {state['revision']}", 'revision 999')
    if defect == 'foreign_session': human['sessionId'] = 'foreign'
    if defect == 'later_presentation': rows.reverse()
    if defect == 'newer_human':
        latest = deepcopy(human); latest.update(uuid=str(uuid.uuid4()), timestamp=datetime.now(timezone.utc).isoformat())
        latest['message']['content'] = 'I have another question'; rows.append(latest)
    if defect == 'ambiguous':
        shown['message']['content'] += '\n1. Approve Product\n2. Select another setting'
        human['message']['content'] = 'yes'
    if defect == 'missing_id': human.pop('uuid')
    if defect == 'malformed_message': human['message'] = []
    if defect == 'newer_malformed':
        latest = deepcopy(human); latest.update(uuid=str(uuid.uuid4()), message=[])
        rows.append(latest)
    if defect == 'external_prompt': human['promptSource'] = 'remote-tool'
    write(rows)
    message = rows[-1].get('message', {})
    event['prompt'] = message.get('content') if isinstance(message, dict) else None
    flow.hook(event, governor=c)
    after = c.report(diagnostics=False)
    assert after['status'] == 'awaiting_human_approval' and not after['decisions']
    assert after['revision'] == state['revision']


@pytest.mark.parametrize('host', ['codex', 'claude'])
@pytest.mark.parametrize('wrong', [False, True])
def test_explicit_colon_checkpoint_uses_shared_semantics_and_exact_binding(tmp_path, host, wrong):
    c, state = setup(tmp_path); c.adapter.name = host; state = submit(c, state)
    checkpoint = w.binding(state, w.current(state)['packet'])['checkpoint']
    text = 'Approve: ' + ('f' * 32 if wrong else checkpoint)
    value = decision(state, text=text)
    if wrong:
        with pytest.raises(w.Refusal, match='different checkpoint'): decide(c, state, value)
    else:
        assert w.current(decide(c, state, value))['decision'] == 'approved'


def test_unavailable_host_and_child_never_read_arbitrary_transcripts(tmp_path, monkeypatch):
    c, state, _, event, _, _, _ = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(native, '_open_source', lambda *args: pytest.fail('Must not open an unselected source'))
    assert approval.capture(state, host='codex', presentation={}, event=event)['status'] == 'unsupported_host'
    assert approval.capture(state, host='claude', presentation={}, event={**event, 'agent_id': 'child'})['status'] == 'unsupported'


@pytest.mark.parametrize('text,choice', [('reject', 'rejected'), ('request changes', 'changes_requested'), ('cancel', 'cancelled')])
def test_native_negative_decision_preserves_human_authority_despite_artifact_drift(tmp_path, monkeypatch, text, choice):
    c, state, _, event, _, human, _ = fixture(tmp_path, monkeypatch, text=text)
    (tmp_path / 'product.json').write_text('{}')
    flow.hook(event, governor=c)
    after = json.loads(c._path().read_text())['runs'][state['run']]
    assert after['decisions'][human['uuid']]['choice'] == choice
    assert after['revision'] == state['revision'] + 1
