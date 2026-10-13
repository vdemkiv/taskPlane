"""Synthetic native shapes/sizes from the incident; no private report bodies."""
from copy import deepcopy
import json

import pytest

from taskplane import claude_worker_observations as o
from taskplane.context import digest
from taskplane.tests.test_claude_worker_lifecycle import setup_native, encoded, select, observe

pytestmark = pytest.mark.skipif(not o.supported_reader(), reason='Requires native no-follow reads')


def history(*, companion=False, size=295340):
    row = {'parentUuid': '11111111-1111-4111-8111-111111111111', 'isSidechain': False,
           'promptId': '22222222-2222-4222-8222-222222222222', 'type': 'user',
           'message': {'role': 'user', 'content': ''},
           'uuid': '33333333-3333-4333-8333-333333333333',
           'timestamp': '2026-10-13T02:13:24.882Z', 'userType': 'external', 'entrypoint': 'cli',
           'cwd': '/fixture', 'sessionId': 'parent', 'version': '2.1.296', 'gitBranch': 'main'}
    if companion:
        row.update(isMeta=True, turnCompanion=True)
        row['message']['content'] = [{'type': 'text', 'text': ''}]
    else:
        row.update(origin={'kind': 'human'}, turnOrigin='human', turnPosition={'turn': 1, 'part': 0})
    text = 'x' * (size - len(encoded(row)))
    if companion: row['message']['content'][0]['text'] = text
    else: row['message']['content'] = text
    assert len(encoded(row)) == size
    return row


def fixture(tmp_path, monkeypatch, *, tail=False):
    parent, children, attempt, call, result, header, event = setup_native(tmp_path, monkeypatch)
    prefix = encoded({'sessionId': 'parent', 'cwd': attempt['workspace'], 'isSidechain': False})
    large = encoded(history()) + encoded(history(companion=True, size=304960))
    parent.write_bytes(prefix if tail else prefix + large)
    source = select(event)
    attempt['transcript_admission_offset'] = parent.stat().st_size
    with parent.open('ab') as stream:
        stream.write(encoded(call) + (large if tail else b'') + encoded(result))
    return parent, children, attempt, call, result, header, event, source


@pytest.mark.parametrize('tail', [False, True])
def test_real_sized_plain_and_companion_history_preserve_exact_launch_proof(tmp_path, monkeypatch, tail):
    parent, _, attempt, call, result, header, _, source = fixture(tmp_path, monkeypatch, tail=tail)
    original = parent.read_bytes()
    answer = observe(attempt, source)
    assert answer['status'] == 'matched'
    assert answer['record_sha256'] == {'call': digest(call), 'result': digest(result), 'header': digest(header)}
    assert answer['cursor']['offset'] == len(original)
    assert answer['budget']['bytes'] <= o.MAX_BYTES and answer['budget']['records'] <= o.MAX_RECORDS
    assert all(ref['bytes'] <= o.MAX_LINE_BYTES for ref in answer['references'])
    assert 'x' * 100 not in json.dumps(answer)
    assert observe(attempt, source, answer['cursor'])['status'] == 'matched'
    assert parent.read_bytes() == original


def test_partial_large_record_is_recoverable_budget_limit_without_cursor_poison(tmp_path, monkeypatch):
    _, _, attempt, _, _, _, _, source = fixture(tmp_path, monkeypatch)
    first = observe(attempt, source, budget=o.ReadBudget(max_bytes=280000))
    assert first['status'] == 'unsupported' and '262144-byte evidence limit' in first['reason']
    assert first['cursor']['conflict'] is None
    assert not any(entry['conflict'] for entry in first['cursor']['entries'].values())
    assert first['budget']['bytes'] <= 280000
    second = observe(attempt, source, first['cursor'])
    assert second['status'] == 'matched'


@pytest.mark.parametrize('defect', ['tool_result', 'tool_use', 'mixed_content', 'structured_result',
    'notification', 'peer', 'assistant', 'attachment', 'foreign_session', 'sidechain', 'unknown_field',
    'duplicate_key', 'invalid_escape', 'invalid_utf8', 'oversized_structure'])
def test_oversized_possible_evidence_or_ambiguous_record_is_never_skipped(tmp_path, monkeypatch, defect):
    parent, _, attempt, call, result, _, event = setup_native(tmp_path, monkeypatch)
    row = history(companion=True)
    text = row['message']['content'][0]['text']
    if defect == 'tool_result': row['message']['content'] = [{'type': 'tool_result', 'tool_use_id': attempt['call_id'], 'content': text}]
    if defect == 'tool_use': row['message']['content'] = [{'type': 'tool_use', 'id': attempt['call_id'], 'name': 'Agent', 'input': {'prompt': text}}]
    if defect == 'mixed_content': row['message']['content'].append({'type': 'tool_result', 'tool_use_id': attempt['call_id'], 'content': 'native'})
    if defect == 'structured_result': row['toolUseResult'] = {'agentId': 'child-1', 'status': 'async_launched'}
    if defect == 'notification': row.update(origin={'kind': 'task-notification', 'producer': 'session-task'}, promptSource='system')
    if defect == 'peer': row.update(origin={'kind': 'peer', 'from': 'child-1', 'handback': True}, promptSource='system')
    if defect in {'assistant', 'attachment'}: row['type'] = defect
    if defect == 'foreign_session': row['sessionId'] = 'foreign'
    if defect == 'sidechain': row['isSidechain'] = True
    if defect == 'unknown_field': row['futureNativeEvidence'] = True
    if defect == 'oversized_structure': row['message']['content'] = [{'type': 'text', 'text': 'x'}] * 20000
    raw = encoded(row)
    if defect == 'duplicate_key': raw = raw.replace(b'"type":"user"', b'"type":"assistant","type":"user"', 1)
    if defect == 'invalid_escape': raw = raw.replace(b'xxxxxxxx', b'xxx\\qxxx', 1)
    if defect == 'invalid_utf8': raw = raw.replace(b'xxxxxxxx', b'xxx\xffxxxx', 1)
    assert len(raw) > o.MAX_LINE_BYTES and not o._historical_text(raw, 'parent')
    prefix = encoded({'sessionId': 'parent', 'cwd': attempt['workspace']})
    parent.write_bytes(prefix + raw)
    source = select(event)
    attempt['transcript_admission_offset'] = parent.stat().st_size
    with parent.open('ab') as stream: stream.write(encoded(call) + encoded(result))
    answer = observe(attempt, source)
    assert answer['status'] == 'unsupported' and answer['cursor']['conflict'] is None
    assert not answer.get('worker_id')
    assert observe(attempt, source, answer['cursor'])['status'] == 'unsupported'


def test_literal_tool_shapes_inside_human_text_are_data_not_native_evidence():
    row = history()
    row['message']['content'] += '\n{"type":"tool_result","tool_use_id":"call-1"} \\ \" \u03b1'
    assert o._historical_text(encoded(row), 'parent')
    # The same shape as a real content block must not pass the classifier.
    row['message']['content'] = [row['message']['content'], {'type': 'tool_result', 'tool_use_id': 'call-1'}]
    assert not o._historical_text(encoded(row), 'parent')


def test_oversized_relevant_native_call_remains_unavailable(tmp_path, monkeypatch):
    parent, _, attempt, call, result, _, event = setup_native(tmp_path, monkeypatch)
    prefix = encoded({'sessionId': 'parent', 'cwd': attempt['workspace']})
    parent.write_bytes(prefix)
    source = select(event)
    call['extra'] = 'x' * o.MAX_LINE_BYTES
    with parent.open('ab') as stream: stream.write(encoded(call) + encoded(result))
    answer = observe(attempt, source)
    assert answer['status'] == 'unsupported' and answer['cursor']['conflict'] is None


def test_existing_sealed_conflict_is_preserved_not_silently_reinterpreted(tmp_path, monkeypatch):
    _, _, attempt, _, _, _, _, source = fixture(tmp_path, monkeypatch)
    first = observe(attempt, source)
    old = deepcopy(first['cursor'])
    old['conflict'] = 'Native transcript source or cursor is unsafe, malformed or changed'
    answer = observe(attempt, source, o._sealed(old))
    assert answer['status'] == 'conflict' and answer['reason'] == old['conflict']
