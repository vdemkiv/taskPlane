from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from datetime import datetime

from taskplane import native_session_meter


def _fixture_reader(host, root, directory, **kwargs):
    """Exercise counter contracts with explicit logs when native inventory is unavailable.

    Fixture enumeration is setup, never metered directory-discovery evidence.
    Dedicated inventory tests below retain the production unsupported result.
    """
    roots = [directory] if sys.platform in ('darwin', 'linux') else []
    reader = native_session_meter.IncrementalUsageReader(host, root, roots, **kwargs)
    if not roots and (kwargs.get('checkpoint') is None or reader.checkpoint_invalidated):
        for path in sorted(directory.glob('*.jsonl')):
            reader.add_file(path)
    return reader


def _use_codex_fixture_files(monkeypatch, *paths):
    """Keep reconciliation and native parsing real without requiring directory APIs."""
    if sys.platform in ('darwin', 'linux'):
        return
    from taskplane import flow_usage
    original = flow_usage._reader
    monkeypatch.setattr(flow_usage, '_READERS', {})

    def explicit_reader(run, roots, cutoff, host):
        reader = original(run, [], cutoff, host)
        for path in paths:
            reader.add_file(path)
        return reader

    monkeypatch.setattr(flow_usage, '_reader', explicit_reader)


def _assert_unsupported_inventory(reader, result=None):
    """Assert the real fail-closed capability result; never claim discovery passed."""
    from scripts.benchmark_usage import IncompleteDrain, drain
    result = reader.refresh() if result is None else result
    assert not result['complete'] and not result['fully_discovered']
    assert 'native_directory_accounting_unavailable' in result['discovery_errors']
    assert result['directory_bytes'] == result['discovery_entries'] == 0
    assert result['continuation'] and result['coverage'] == 'partial'
    with pytest.raises(IncompleteDrain, match='unsupported_native_directory_api') as failure:
        drain(reader)
    assert len(failure.value.refreshes) == 1
    return result


def _interval_fixture(path, sid, points, parent=None, owned=False):
    metadata={'id':sid,'timestamp':'2026-09-01T00:00:00Z'}
    if parent: metadata['parent_thread_id']=parent
    rows=[{'type':'session_meta','payload':metadata}];before=0
    for ordinal,(second,total) in enumerate(points,1):
        usage={'input_tokens':total,'cached_input_tokens':0,'output_tokens':0,'total_tokens':total}
        payload={'thread_id':sid,'thread_token_usage':usage}
        if owned:
            payload.update(response_id=f'{sid}-{ordinal}',usage={**usage,'input_tokens':total-before,'total_tokens':total-before})
        rows.append({'type':'token_usage_record','ordinal':ordinal,
            'timestamp':f'2026-09-01T00:00:{second:02d}Z','payload':payload})
        before=total
    path.write_text('\n'.join(json.dumps(row) for row in rows)+'\n')
    return rows


@pytest.mark.parametrize('host', ['Codex', 'Claude'])
@pytest.mark.parametrize('total', [0, 10])
def test_incremental_missing_known_worker_is_unknown_until_counter_arrives(tmp_path, host, total):
    def write(path, sid, parent=None):
        if host == 'Codex':
            _interval_fixture(path, sid, [(1, total)], parent=parent)
        else:
            row = {'type': 'assistant', 'sessionId': 'root', 'timestamp': '2026-09-01T00:00:01Z',
                   'message': {'id': sid + '-response', 'model': 'fixture',
                               'usage': {'input_tokens': total, 'cache_read_input_tokens': 0,
                                         'cache_creation_input_tokens': 0, 'output_tokens': 0}}}
            if parent:
                row['agentId'] = sid
            path.write_text(json.dumps(row) + '\n')

    root = tmp_path / 'root.jsonl'
    write(root, 'root')
    reader = native_session_meter.IncrementalUsageReader(host, 'root', known=['worker'])
    reader.add_file(root)
    partial = reader.refresh()
    missing = next(row for row in partial['sessions'] if row['session'] == 'worker')
    assert partial['fully_discovered'] and not partial['complete']
    assert partial['totals']['total_tokens'] == total
    assert partial['attribution']['worker'] is None
    assert missing['usage'] is None and missing['native_usage'] is None
    assert missing['status'] == 'partial' and missing['attribution_unknown']

    child = tmp_path / 'worker.jsonl'
    write(child, 'worker', parent='root')
    reader.add_file(child)
    complete = reader.refresh()
    assert complete['complete']
    assert complete['totals']['total_tokens'] == 2 * total
    assert all(row['status'] == 'measured' for row in complete['sessions'])


@pytest.mark.parametrize('host', ['Codex', 'Claude'])
def test_incremental_absent_counters_have_unknown_total(tmp_path, host):
    reader = native_session_meter.IncrementalUsageReader(host, 'root')
    result = reader.refresh()
    assert not result['complete']
    assert result['totals'] is None
    assert result['attribution'] == {'root': None}
    assert result['sessions'][0]['status'] == 'partial'


def test_claude_unknown_message_boundary_survives_checkpoint_without_hiding_known_usage(tmp_path):
    from taskplane.tests.test_claude_flow import message, write
    path = tmp_path / 'root.jsonl'
    before = message('streamed', at='2026-09-15T00:00:05Z', output=1)
    del before['message']['usage']['cache_read_input_tokens']
    write(path, [before, message('streamed', at='2026-09-15T00:00:15Z'),
                 message('independent', at='2026-09-15T00:00:16Z')])
    start = datetime.fromisoformat('2026-09-15T00:00:10+00:00').timestamp()
    reader = native_session_meter.IncrementalUsageReader('Claude', 'root', start=start)
    reader.add_file(path)
    original = reader.refresh()['sessions'][0]
    checkpoint = json.loads(json.dumps(reader.checkpoint()))
    restored = native_session_meter.IncrementalUsageReader('Claude', 'root', start=start,
                                                          checkpoint=checkpoint)
    row = restored.refresh()['sessions'][0]
    assert row == original
    assert row['native_usage']['total_tokens'] == 284
    assert row['usage']['total_tokens'] == 142 and row['status'] == 'partial'
    assert 'message_boundary_unavailable' in row['errors']


def test_claude_decreased_message_does_not_cancel_independent_observed_usage(tmp_path):
    from taskplane.tests.test_claude_flow import message, write
    path = tmp_path / 'root.jsonl'
    write(path, [message('streamed', at='2026-09-15T00:00:15Z', output=10),
                 message('streamed', at='2026-09-15T00:00:16Z', output=1),
                 message('independent', at='2026-09-15T00:00:17Z')])
    reader = native_session_meter.IncrementalUsageReader('Claude', 'root')
    reader.add_file(path)
    row = reader.refresh()['sessions'][0]
    assert row['usage']['total_tokens'] == 142
    assert row['status'] == 'partial' and 'counter_decreased' in row['errors']


def test_claude_foreign_parent_stays_unknown_after_checkpoint_restore(tmp_path):
    from taskplane.tests.test_claude_flow import message, write
    path = tmp_path / 'foreign.jsonl'
    row = message(agent='child')
    row['sessionId'] = 'unrelated'
    write(path, [row])
    reader = native_session_meter.IncrementalUsageReader('Claude', 'root', expected_root='root')
    reader.add_file(path)
    first = reader.refresh()
    restored = native_session_meter.IncrementalUsageReader('Claude', 'root', expected_root='root',
        checkpoint=json.loads(json.dumps(reader.checkpoint())))
    second = restored.refresh()
    assert first['sessions'] == second['sessions']
    child = next(row for row in second['sessions'] if row['session'] == 'child')
    assert child['usage'] is None and child['native_usage'] is None
    assert child['status'] == 'partial' and 'native_parent_mismatch' in child['errors']


def test_interval_reset_and_truncated_history_never_become_measured_zero(tmp_path, monkeypatch):
    path = tmp_path/'reset.jsonl'
    _interval_fixture(path,'child',[(5,100),(15,30)],'root')
    start=datetime.fromisoformat('2026-09-01T00:00:10+00:00').timestamp()
    result=native_session_meter.read_owned_interval([path],'child',start=start)
    assert result['usage'] is None and result['status']=='partial'
    assert 'counter_reset' in result['errors']
    monkeypatch.setattr(native_session_meter,'MAX_REPLAY_BYTES',100)
    result=native_session_meter.read_owned_interval([path],'child',start=start)
    assert result['status']=='partial' and result['usage'] is None


def test_owned_usage_categories_and_foreign_response_exclusion(tmp_path):
    path=tmp_path/'owned.jsonl'
    rows=_interval_fixture(path,'child',[(5,100),(15,150)],'root',owned=True)
    rows[-1]['payload']['usage'].update(cached_input_tokens=20,output_tokens=10,input_tokens=40,reasoning_output_tokens=5)
    rows[-1]['payload']['thread_token_usage'].update(cached_input_tokens=20,output_tokens=10,input_tokens=140,reasoning_output_tokens=5)
    # Foreign copied history may coexist but is never charged to this worker.
    foreign=json.loads(json.dumps(rows[-1]));foreign['payload']['thread_id']='parent'
    rows.append(foreign)
    path.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    start=datetime.fromisoformat('2026-09-01T00:00:10+00:00').timestamp()
    result=native_session_meter.read_owned_interval([path],'child',start=start)
    assert result['status']=='measured'
    assert result['usage']==dict(input_tokens=40,cached_input_tokens=20,uncached_input_tokens=20,
                                output_tokens=10,reasoning_tokens=5,total_tokens=50)


def _write_segment(
    path: Path,
    *,
    session_id: str,
    total: int | None,
    cached: int = 0,
    output: int = 1,
    parent: str | None = None,
    resumed: bool = False,
    ordinal: int = 7,
) -> None:
    metadata = {
        "session_id": "root-session",
        "id": session_id,
        "timestamp": "2026-09-01T00:00:00Z",
        "thread_source": "subagent" if parent else "agent_created_thread",
    }
    if parent:
        metadata.update({
            "forked_from_id": parent,
            "parent_thread_id": parent,
            "source": {"subagent": {"thread_spawn": {
                "parent_thread_id": parent,
                "agent_path": f"/root/{session_id}",
            }}},
        })
    if resumed:
        metadata["history_base"] = {
            "thread_id": session_id,
            "end_ordinal_exclusive": 100,
            "end_byte_offset": 200,
        }
    rows: list[dict] = [{
        "timestamp": metadata["timestamp"],
        "type": "session_meta",
        "payload": metadata,
    }]
    rows.append({
        "timestamp": "2026-09-01T00:00:01Z",
        "type": "response_item",
        "payload": {"type": "message", "content": "private conversation"},
    })
    if total is not None:
        input_tokens = total - output
        rows.append({
            "timestamp": f"2026-09-01T00:00:{ordinal:02d}Z",
            "ordinal": ordinal,
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {"total_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached,
                    "cache_write_input_tokens": 0,
                    "output_tokens": output,
                    "reasoning_output_tokens": 0,
                    "total_tokens": total,
                }},
            },
        })
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_reads_current_metadata_and_latest_native_counter_without_content(
    tmp_path: Path,
) -> None:
    source = tmp_path / "worker.jsonl"
    _write_segment(
        source, session_id="worker-1", parent="root-session",
        total=125, cached=100, output=5,
    )

    snapshot = native_session_meter.read_snapshot(str(source))

    assert snapshot["session_id"] == "worker-1"
    assert snapshot["parent_session_id"] == "root-session"
    assert snapshot["agent_path"] == "/root/worker-1"
    assert snapshot["usage"] == {
        "input_tokens": 120,
        "cached_input_tokens": 100,
        "uncached_input_tokens": 20,
        "output_tokens": 5,
        "reasoning_tokens": 0,
        "total_tokens": 125,
    }
    assert "private conversation" not in json.dumps(snapshot)


def test_resume_sums_reset_physical_segment_and_fork_counters_once(
    tmp_path: Path,
) -> None:
    first = tmp_path / "root-1.jsonl"
    resumed = tmp_path / "root-2.jsonl"
    child = tmp_path / "child.jsonl"
    _write_segment(first, session_id="root", total=90, ordinal=7)
    _write_segment(
        resumed, session_id="root", total=90, resumed=True, ordinal=11,
    )
    _write_segment(
        child, session_id="child", parent="root", total=40, ordinal=5,
    )

    wave = native_session_meter.aggregate([
        native_session_meter.read_snapshot(str(first)),
        native_session_meter.read_snapshot(str(resumed)),
        native_session_meter.read_snapshot(str(child)),
        native_session_meter.read_snapshot(str(resumed)),
    ])

    assert wave["physical_segments"] == 3
    assert wave["logical_sessions"] == 2
    assert wave["usage"]["total_tokens"] == 220
    assert {row["session_id"]: row["segments"] for row in wave["sessions"]} \
        == {"child": 1, "root": 2}
    assert {row["session_id"]: row["total_tokens"]
            for row in wave["sessions"]} == {"child": 40, "root": 180}
    _write_segment(resumed, session_id="root", total=90, ordinal=11)
    with pytest.raises(native_session_meter.NativeSessionMeterError, match="restart evidence"):
        native_session_meter.aggregate([native_session_meter.read_snapshot(str(first)),
                                        native_session_meter.read_snapshot(str(resumed))])


def test_missing_counter_and_backwards_same_segment_refuse_instead_of_zero(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.jsonl"
    _write_segment(missing, session_id="missing", total=None)
    with pytest.raises(
        native_session_meter.NativeSessionMeterError,
        match="no complete token counter",
    ):
        native_session_meter.read_snapshot(str(missing))

    source = tmp_path / "same-segment.jsonl"
    _write_segment(source, session_id="root", total=100, ordinal=6)
    older = native_session_meter.read_snapshot(str(source))
    _write_segment(source, session_id="root", total=99, ordinal=9)
    newer = native_session_meter.read_snapshot(str(source))
    with pytest.raises(
        native_session_meter.NativeSessionMeterError,
        match="physical-segment counter moved backwards",
    ):
        native_session_meter.aggregate([older, newer])
def test_verified_fork_history_requires_own_thread_counter(tmp_path):
    p = tmp_path/'fork.jsonl'
    _write_segment(p, session_id='child', total=1000, parent='parent')
    rows = [json.loads(line) for line in p.read_text().splitlines()]
    rows[0]['payload']['history_base'] = {'thread_id':'parent','end_ordinal_exclusive':99,'end_byte_offset':100}
    p.write_text('\n'.join(json.dumps(row) for row in rows)+'\n')
    with pytest.raises(ValueError, match='current-thread counter'):
        native_session_meter.read_snapshot(p)
    own = {'input_tokens':7,'cached_input_tokens':2,'output_tokens':3,'reasoning_output_tokens':1,'total_tokens':10}
    rows.append({'type':'token_usage_record','timestamp':'2026-09-01T00:00:10Z','ordinal':10,
                 'payload':{'thread_id':'child','thread_token_usage':own}})
    p.write_text('\n'.join(json.dumps(row) for row in rows)+'\n')
    assert native_session_meter.read_snapshot(p)['usage']['total_tokens'] == 10
    rows[0]['payload']['history_base']['thread_id'] = 'foreign'
    p.write_text('\n'.join(json.dumps(row) for row in rows)+'\n')
    with pytest.raises(ValueError, match='identity'):
        native_session_meter.read_snapshot(p)


def _write_thread_segment(path, *, total, cached=0, ordinal=5, resumed=False):
    _write_segment(path, session_id='root', total=total, cached=cached,
                   output=0, ordinal=ordinal, resumed=resumed)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    counter = rows[-1]
    usage = counter['payload']['info']['total_token_usage']
    counter.update(type='token_usage_record',
                   payload={'thread_id': 'root', 'thread_token_usage': usage})
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))


@pytest.mark.parametrize('total,cached', [(30, 0), (150, 10), (150, 100)])
@pytest.mark.parametrize('reverse', [False, True])
def test_resumed_thread_rejects_each_category_reset_in_both_readers(tmp_path, total, cached, reverse):
    first, resumed = tmp_path / 'first.jsonl', tmp_path / 'resumed.jsonl'
    _write_thread_segment(first, total=100, cached=20)
    _write_thread_segment(resumed, total=total, cached=cached, ordinal=15, resumed=True)
    paths = [resumed, first] if reverse else [first, resumed]
    snapshots = [native_session_meter.read_snapshot(str(path)) for path in paths]
    with pytest.raises(ValueError, match='backwards'):
        native_session_meter.aggregate(snapshots)
    with pytest.raises(ValueError, match='backwards'):
        native_session_meter.read_logical_snapshot(paths, 'root')


def test_valid_thread_resume_and_duplicate_source_conserve_latest_total(tmp_path):
    first, resumed = tmp_path / 'first.jsonl', tmp_path / 'resumed.jsonl'
    _write_thread_segment(first, total=100, cached=20)
    _write_thread_segment(resumed, total=150, cached=30, ordinal=15, resumed=True)
    snapshots = [native_session_meter.read_snapshot(str(path)) for path in [resumed, first, resumed]]
    aggregate = native_session_meter.aggregate(snapshots)
    logical = native_session_meter.read_logical_snapshot([resumed, first, resumed], 'root')
    assert aggregate['usage'] == logical['usage']
    assert logical['usage']['total_tokens'] == 150 and not logical['partial']
    assert aggregate['physical_segments'] == 2


def test_same_source_out_of_order_observations_still_detect_reset(tmp_path):
    path = tmp_path / 'root.jsonl'
    _write_thread_segment(path, total=100, ordinal=5)
    before = native_session_meter.read_snapshot(str(path))
    _write_thread_segment(path, total=30, ordinal=15)
    after = native_session_meter.read_snapshot(str(path))
    with pytest.raises(ValueError, match='backwards'):
        native_session_meter.aggregate([after, before])


def test_conflicting_thread_boundary_refuses_both_readers(tmp_path):
    first, resumed = tmp_path / 'first.jsonl', tmp_path / 'resumed.jsonl'
    _write_thread_segment(first, total=100)
    _write_thread_segment(resumed, total=150, resumed=True)
    with pytest.raises(ValueError, match='conflicting'):
        native_session_meter.aggregate([native_session_meter.read_snapshot(str(p)) for p in [first, resumed]])
    with pytest.raises(ValueError, match='conflicting'):
        native_session_meter.read_logical_snapshot([first, resumed], 'root')


def test_physical_alias_is_not_an_extra_legacy_segment(tmp_path):
    first, alias = tmp_path / 'first.jsonl', tmp_path / 'alias.jsonl'
    _write_segment(first, session_id='root', total=100)
    alias.hardlink_to(first)
    snapshots = [native_session_meter.read_snapshot(str(p)) for p in [first, alias]]
    result = native_session_meter.aggregate(snapshots)
    assert result['physical_segments'] == 1 and result['usage']['total_tokens'] == 100
    assert native_session_meter.read_logical_snapshot([alias, first], 'root')['usage'] == result['usage']


def test_thread_chronology_uses_instants_not_timestamp_spelling(tmp_path):
    first, resumed = tmp_path / 'first.jsonl', tmp_path / 'resumed.jsonl'
    _write_thread_segment(first, total=100, ordinal=5)
    _write_thread_segment(resumed, total=150, ordinal=15, resumed=True)
    rows = [json.loads(line) for line in first.read_text().splitlines()]
    rows[-1]['timestamp'] = '2026-09-01T01:00:05+01:00'
    first.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    assert native_session_meter.read_logical_snapshot([resumed, first], 'root')['usage']['total_tokens'] == 150


def test_exact_thread_record_copy_does_not_require_fictional_restart(tmp_path):
    first, copy = tmp_path / 'first.jsonl', tmp_path / 'copy.jsonl'
    _write_thread_segment(first, total=100)
    copy.write_bytes(first.read_bytes())
    snapshots = [native_session_meter.read_snapshot(str(p)) for p in [first, copy]]
    result = native_session_meter.aggregate(snapshots)
    assert result['physical_segments'] == 2 and result['usage']['total_tokens'] == 100
    assert native_session_meter.read_logical_snapshot([copy, first], 'root')['usage'] == result['usage']
    # A different observation is not proved to be a copy of this physical segment.
    _write_thread_segment(copy, total=150, ordinal=15)
    with pytest.raises(ValueError, match='restart evidence'):
        native_session_meter.read_logical_snapshot([copy, first], 'root')


@pytest.mark.parametrize('host', ['Codex', 'Claude'])
def test_incremental_owned_interval_budget_and_continuation(tmp_path, host):
    from scripts.benchmark_usage import fixture, oracle
    directory = tmp_path / 'native'
    ids = fixture(directory, host, 20, 3, 65536)
    expected, attributed = oracle(directory, host, ids)
    reader = _fixture_reader(host, 'root', directory, start=1767225600.0)
    prior = None
    observations = []
    for _ in range(300):
        result = reader.refresh(native_session_meter.UsageBudget(bytes_limit=32768, records_limit=40, entries_limit=128))
        observations.append(result)
        assert result['read_bytes'] <= 32768
        assert result['parsed_records'] <= 40
        assert result['discovery_entries'] <= 128
        assert result['max_record_bytes'] <= 1048576
        assert result['resumed_from'] == prior
        if result['complete']:
            break
        assert result['continuation'] and result['coverage'] == 'partial'
        prior = result['continuation']
    else:
        evidence=tmp_path/'incomplete-refreshes.json'
        evidence.write_text(json.dumps(observations))
        pytest.fail('actual bounded drain did not complete; raw evidence: ' + str(evidence))
    assert any(not item['complete'] for item in observations)
    assert result['totals'] == expected
    assert result['attribution'] == attributed
    # Checkpoint serialization contains no transcript/padding text and supports
    # fresh readers, rather than depending on producer object identity.
    detached = json.loads(json.dumps(reader.state))
    assert 'xxx' not in json.dumps(detached)
    restored = _fixture_reader(host, 'root', directory, start=1767225600.0, checkpoint=detached)
    warm = restored.refresh()
    assert warm['complete'] and warm['totals'] == expected
    assert warm['unrelated_scans'] == 0
    assert warm['read_bytes'] <= 3 * 8192
    assert warm['max_prefix_bytes'] <= 4096 and warm['max_tail_bytes'] <= 4096
    assert warm['parsed_records'] == 0


@pytest.mark.parametrize('host', ['Codex', 'Claude'])
def test_incremental_inflight_record_append_and_owned_dedup(tmp_path, host):
    from scripts.benchmark_usage import fixture, oracle, encoded, event, drain
    directory = tmp_path / 'native'
    ids = fixture(directory, host, 0, 1, 65536)
    reader = _fixture_reader(host, 'root', directory, start=1767225600.0)
    drain(reader)
    path = directory / 'root.jsonl'
    record = encoded(event(host, 'root', 65))
    cut = len(record) // 2
    with path.open('ab') as stream: stream.write(record[:cut])
    partial = reader.refresh()
    assert not partial['complete'] and partial['continuation']
    with path.open('ab') as stream: stream.write(record[cut:] + record)
    final = reader.refresh()
    assert final['resumed_from'] == partial['continuation']
    assert final['complete']
    expected, attributed = oracle(directory, host, ids)
    assert final['totals'] == expected and final['attribution'] == attributed
    assert final['totals']['total_tokens'] == 65 * 13
    assert final['read_bytes'] <= 8192 + len(record) * 2
    assert final['unrelated_scans'] == 0


@pytest.mark.parametrize('mutation', ['truncate', 'replace', 'middle'])
def test_incremental_mutation_remains_unknown(tmp_path, mutation):
    from scripts.benchmark_usage import fixture, drain
    directory = tmp_path / 'native'
    fixture(directory, 'Codex', 0, 1, 65536)
    path = directory / 'root.jsonl'
    reader = _fixture_reader('Codex', 'root', directory, start=1767225600.0)
    drain(reader)
    data = path.read_bytes()
    if mutation == 'truncate':
        path.write_bytes(data[:data.index(b'\n') + 1])
    elif mutation == 'replace':
        replacement=tmp_path/'new'; replacement.write_bytes(data); replacement.replace(path)
    else:
        offset=data.index(b'xxx')+15000
        with path.open('r+b') as stream: stream.seek(offset); stream.write(b'y')
    result = reader.refresh()
    assert not result['complete']
    assert result['sessions'][0]['status'] == 'partial'
    assert result['sessions'][0]['attribution_unknown']
    assert 'not a cryptographic proof' in result['trust_assumptions']


def test_incremental_interval_late_records_and_reset(tmp_path):
    from scripts.benchmark_usage import encoded, event
    path=tmp_path/'native.jsonl'
    metadata={'type':'session_meta','payload':{'id':'root','timestamp':'2026-01-01T00:00:00Z'}}
    # Deliberately physical out-of-order. Ownership is native timestamp, [2,4).
    path.write_bytes(encoded(metadata)+b''.join(encoded(event('Codex','root',n)) for n in [1,3,2,4]))
    reader=native_session_meter.IncrementalUsageReader('Codex','root',start=1767225602.0,end=1767225604.0)
    reader.add_file(path)
    result=reader.refresh()
    assert result['complete'] and result['totals']['total_tokens']==26
    reset=event('Codex','root',3)
    reset['ordinal']=30
    reset['payload']['thread_token_usage'].update(input_tokens=1,cached_input_tokens=0,output_tokens=0,total_tokens=1)
    with path.open('ab') as stream: stream.write(encoded(reset))
    result=reader.refresh()
    assert not result['complete'] and 'counter_reset' in result['sessions'][0]['errors']


def test_usage_budget_nested_helpers_share_one_clock_and_byte_cap(tmp_path):
    from scripts.benchmark_usage import fixture
    directory=tmp_path/'native';fixture(directory,'Codex',0,2,65536)
    reader=_fixture_reader('Codex','root',directory,start=1767225600.0)
    budget=native_session_meter.UsageBudget(bytes_limit=20000)
    with native_session_meter.usage_refresh(budget) as outer:
        with native_session_meter.usage_refresh() as inner:
            assert inner is outer
            first=reader.refresh()
        second=reader.refresh()
    assert first['read_bytes'] <= 20000 and second['read_bytes'] <= 20000
    assert second['exhausted'] and not second['complete']


@pytest.mark.taskplane_capacity
def test_usage_complete_grid_matches_independent_oracle(tmp_path, record_property):
    from scripts.benchmark_usage import samples
    measurements=[]
    try:
        for sample in samples(tmp_path/'grid'):
            measurements.append(sample)
            assert sample['complete'] is True
            assert sample['totals'] == sample['oracle_totals']
            assert sample['attribution'] == sample['oracle_attribution']
            prior=None
            for refresh in sample['refreshes']:
                assert refresh['read_bytes'] <= 16777216
                assert refresh['parsed_records'] <= 100000
                assert refresh['discovery_entries'] <= 2000
                assert refresh['max_record_bytes'] <= 1048576
                assert refresh['max_prefix_bytes'] <= 4096
                assert refresh['max_tail_bytes'] <= 4096
                if prior is not None: assert refresh['resumed_from'] == prior
                prior=refresh['continuation']
            if sample['mode'] != 'cold':
                assert all(r['unrelated_scans']==0 for r in sample['refreshes'])
                assert all(r['read_bytes'] <= sample['owned_sessions']*8192+r['appended_bytes'] for r in sample['refreshes'])
        assert len(measurements)==810
        assert len({s['sample_id'] for s in measurements})==810
    finally:
        record_property('remediation_measurement', json.dumps({'schema':'taskplane.remediation-measurement/v1',
            'subcheck':'LOCAL-SCA-02','scenario':'LOCAL-SCA-02:3','kind':'usage','samples':measurements}))


@pytest.mark.parametrize('host', ['Codex', 'Claude'])
def test_incremental_persisted_inventory_over_ten_thousand_unrelated(tmp_path, host):
    import sys
    from scripts.benchmark_usage import fixture, oracle
    directory=tmp_path/'native';ids=fixture(directory,host,10000,3,65536)
    expected,attributed=oracle(directory,host,ids)
    reader=native_session_meter.IncrementalUsageReader(host,'root',[directory],start=1767225600.0)
    if sys.platform not in ('darwin', 'linux'):
        _assert_unsupported_inventory(reader)
        return
    previous=None
    for _ in range(200):
        result=reader.refresh()
        assert result['discovery_entries'] <= 2000 and result['read_bytes'] <= 16777216
        assert result['resumed_from']==previous
        if result['complete']:break
        previous=result['continuation']
        if sys.platform in ('darwin','linux'):
            reader=native_session_meter.IncrementalUsageReader(host,'root',[directory],start=1767225600.0,
                checkpoint=json.loads(json.dumps(reader.state)))
    else: pytest.fail('persisted discovery did not complete')
    assert result['totals']==expected and result['attribution']==attributed
    warm=reader.refresh()
    assert warm['complete'] and warm['unrelated_scans']==0 and warm['read_bytes']<=3*8192


@pytest.mark.parametrize('name_character', ['x','é','漢'])
@pytest.mark.parametrize('initial_bytes', [12,32,64,128,256,512,640,768,1024,4096])
def test_incremental_long_directory_entries_resume_without_false_eof(tmp_path, initial_bytes, name_character, record_property):
    from scripts.benchmark_usage import encoded, event
    directory=tmp_path/'children';directory.mkdir()
    if sys.platform not in ('darwin', 'linux'):
        reader=native_session_meter.IncrementalUsageReader('Codex','root',[directory])
        result=_assert_unsupported_inventory(reader)
        record_property('usage_directory_observations',json.dumps({
            'initial_bytes':initial_bytes,'name_character':name_character,
            'supported':False,'reason':'unsupported_native_directory_api','refreshes':[result]}))
        return
    root_path=tmp_path/'root.jsonl'
    for n in range(6):
        sid='root' if n==0 else f'child-{n}'
        path=root_path if n==0 else directory/(str(n)+name_character*230+'.jsonl')
        meta={'type':'session_meta','payload':{'id':sid,'timestamp':'2026-01-01T00:00:00Z',
              **({'parent_thread_id':'root'} if n else {})}}
        try:
            path.write_bytes(encoded(meta)+encoded(event('Codex',sid,1)))
        except OSError as exc:
            import errno
            if exc.errno != errno.ENAMETOOLONG: raise
            record_property('usage_directory_observations',json.dumps({
                'initial_bytes':initial_bytes,'name_character':name_character,
                'name_bytes':len(path.name.encode()),'supported':False,'errno':exc.errno}))
            pytest.skip('native filesystem rejects this multibyte filename length')
    reader=native_session_meter.IncrementalUsageReader('Codex','root',[directory])
    reader.add_file(root_path)
    observations=[]
    try:
        for turn in range(20):
            budget=native_session_meter.UsageBudget(bytes_limit=initial_bytes) if turn==0 else None
            result=reader.refresh(budget);observations.append(result)
            assert result['read_bytes'] <= (initial_bytes if turn==0 else 16777216)
            if turn: assert result['resumed_from']==observations[-2]['continuation']
            if result['complete']:
                assert len(result['sessions'])==6 and result['totals']['total_tokens']==78
                break
            reader=native_session_meter.IncrementalUsageReader('Codex','root',[directory],
                checkpoint=json.loads(json.dumps(reader.checkpoint())))
        else: pytest.fail('bounded directory discovery did not resume to completion')
    finally:
        raw={'initial_bytes':initial_bytes,'name_character':name_character,'supported':True,
             'expected_sessions':6,'expected_total_tokens':78,
             'refreshes':observations}
        (tmp_path/'observations.json').write_text(json.dumps(raw))
        record_property('usage_directory_observations',json.dumps(raw))


@pytest.mark.parametrize('corruption', ['schema','parser','binding','offset','usage','directory','file_parser','cached_usage','cached_foreign'])
def test_incremental_checkpoint_rejects_malformed_or_foreign_state(tmp_path, corruption):
    from scripts.benchmark_usage import fixture, oracle, drain
    directory=tmp_path/'native';ids=fixture(directory,'Codex',0,2,65536)
    expected,attribution=oracle(directory,'Codex',ids)
    reader=_fixture_reader('Codex','root',directory)
    drain(reader);checkpoint=reader.checkpoint();file=next(iter(checkpoint['files'].values()))
    if corruption=='schema': checkpoint['schema']='foreign'
    elif corruption=='parser': checkpoint['parser_version']+=1
    elif corruption=='binding': checkpoint['binding']='foreign'
    elif corruption=='offset': file['last_complete_record_offset']=-1
    elif corruption=='usage': next(iter(file['dedup_state'].values()))[1]['total_tokens']=-1
    elif corruption=='directory': checkpoint['directories']=[{'path':'/foreign','cookie':0}]
    elif corruption=='file_parser': file['parser_version']+=1
    elif corruption=='cached_usage': next(iter(checkpoint['session_summaries'].values()))['row']['usage']['total_tokens']=-1
    elif corruption=='cached_foreign':
        summary=checkpoint['session_summaries'].pop('root');summary['row']['session']='foreign'
        checkpoint['session_summaries']['foreign']=summary
    restored=_fixture_reader('Codex','root',directory,checkpoint=checkpoint)
    partial=restored.refresh(native_session_meter.UsageBudget(bytes_limit=1))
    assert partial['checkpoint_invalidated']=='incompatible_or_malformed_checkpoint'
    assert not partial['complete'] and partial['coverage']=='partial'
    final=drain(restored)[-1]
    assert final['totals']==expected and final['attribution']==attribution


def test_incremental_checkpoint_and_returned_rows_are_detached(tmp_path):
    from scripts.benchmark_usage import fixture, oracle, drain
    directory=tmp_path/'native';ids=fixture(directory,'Codex',0,2,65536)
    expected,attributed=oracle(directory,'Codex',ids)
    reader=_fixture_reader('Codex','root',directory)
    drain(reader)
    checkpoint=reader.checkpoint()
    restored=_fixture_reader('Codex','root',directory,checkpoint=checkpoint)
    checkpoint['files'].clear()
    result=restored.refresh()
    assert result['complete'] and result['unrelated_scans']==0
    assert result['totals']==expected and result['attribution']==attributed
    result['sessions'][0]['errors'].append('external annotation')
    result['sessions'][0]['usage']['total_tokens']=999999
    result['sessions'][0]['native_usage']['total_tokens']=999999
    again=restored.refresh()
    assert again['complete'] and again['totals']==expected
    assert all(not row['errors'] for row in again['sessions'])
    for file in restored.checkpoint()['files'].values():
        assert file['schema']=='taskplane.usage-checkpoint/v1'
        assert {'host_format','parser_version','lineage_digest','interval_digest','file_identity',
            'size','mtime_ns','prefix_digest','tail_digest','last_complete_record_offset',
            'counter_state','dedup_state','coverage','continuation'} <= file.keys()


def test_incremental_oversized_record_and_cooperative_clock_remain_partial(tmp_path):
    from scripts.benchmark_usage import fixture, drain
    directory=tmp_path/'native';fixture(directory,'Codex',0,1,65536)
    reader=_fixture_reader('Codex','root',directory)
    ticks=iter(n/10 for n in range(10000))
    # A deterministic semantic clock, deliberately excluded from real measurements.
    partial=reader.refresh(native_session_meter.UsageBudget(clock=lambda:next(ticks)))
    assert partial['exhausted'] and not partial['complete']
    assert drain(reader)[-1]['resumed_from'] is not None
    with (directory/'root.jsonl').open('ab') as stream:
        stream.write(b'x'*(1048576+1))
    oversized=reader.refresh()
    assert not oversized['complete'] and oversized['continuation'] is not None
    assert 'oversized_record' in oversized['sessions'][0]['errors']
    assert oversized['read_bytes']<=16777216 and oversized['max_record_bytes']<=1048576


def test_claude_transcript_lookup_shares_budget_and_bounds_cache(tmp_path, monkeypatch):
    from scripts.benchmark_usage import fixture
    from taskplane import claude_flow_usage
    for reader in claude_flow_usage._READERS.values(): reader.close()
    claude_flow_usage._READERS.clear()
    home=tmp_path/'claude';directory=home/'projects'/'project'
    fixture(directory,'Claude',1000,1,65536)
    monkeypatch.setenv('CLAUDE_CONFIG_DIR',str(home))
    if sys.platform not in ('darwin', 'linux'):
        assert claude_flow_usage.transcript('root',{}) is None
        reader=next(iter(claude_flow_usage._READERS.values()))
        _assert_unsupported_inventory(reader)
        return
    budget=native_session_meter.UsageBudget(bytes_limit=4096)
    with native_session_meter.usage_refresh(budget):
        assert claude_flow_usage.transcript('root',{}) is None
        assert claude_flow_usage.transcript('root',{}) is None
    assert budget.read_bytes<=4096 and budget.exhausted
    for _ in range(20):
        found=claude_flow_usage.transcript('root',{})
        if found is not None: break
    assert found==directory/'root.jsonl'
    for n in range(20):
        claude_flow_usage.read_snapshot(found,'root',cutoff=1767225700.0+n)
    assert len(claude_flow_usage._READERS)==16


def test_incremental_missing_response_and_resumed_baseline_are_unknown(tmp_path):
    path=tmp_path/'native.jsonl'
    rows=_interval_fixture(path,'worker',[(5,100),(10,150),(15,170)],'root',owned=True)
    del rows[2]['payload']['usage']
    path.write_text('\n'.join(json.dumps(row) for row in rows)+'\n')
    start=datetime.fromisoformat('2026-09-01T00:00:10+00:00').timestamp()
    missing=native_session_meter.read_owned_interval([path],'worker',start=start)
    assert missing['usage']['total_tokens']==20 and missing['responses']==1
    assert missing['status']=='partial' and 'owned_counter_mismatch' in missing['errors']
    rows=_interval_fixture(path,'worker',[(15,20)],'root',owned=True)
    rows[0]['payload']['history_base']={'thread_id':'worker','end_ordinal_exclusive':8,'end_byte_offset':200}
    path.write_text('\n'.join(json.dumps(row) for row in rows)+'\n')
    resumed=native_session_meter.read_owned_interval([path],'worker',start=start)
    assert resumed['usage']['total_tokens']==20 and resumed['status']=='partial'
    assert 'resumed_baseline_unavailable' in resumed['errors']


@pytest.mark.parametrize('host', ['Codex','Claude'])
def test_incremental_owned_file_replaced_by_foreign_session_stays_unknown(tmp_path, host, record_property):
    from scripts.benchmark_usage import fixture, drain, encoded, event
    directory=tmp_path/'native';fixture(directory,host,0,2,65536)
    reader=_fixture_reader(host,'root',directory)
    before=drain(reader)[-1]
    if host=='Codex':
        meta={'type':'session_meta','payload':{'id':'foreign','timestamp':'2026-01-01T00:00:00Z'}}
        response=event(host,'foreign',1)
    else:
        meta={'type':'user','sessionId':'foreign','timestamp':'2026-01-01T00:00:00Z'}
        response=event(host,'root',1);response['sessionId']='foreign'
    replacement=tmp_path/'replacement';replacement.write_bytes(encoded(meta)+encoded(response))
    replacement.replace(directory/'owned-1.jsonl')
    after=reader.refresh()
    restored=_fixture_reader(host,'root',directory,
        checkpoint=json.loads(json.dumps(reader.checkpoint())))
    resumed=restored.refresh()
    record_property('usage_owner_replacement_observations',json.dumps({'before':before,'after':after,'restored':resumed}))
    for result in (after,resumed):
        assert not result['complete'] and result['continuation'] is not None
        lost=next(row for row in result['sessions'] if row['session']=='owned-1')
        assert lost['usage'] is None and lost['status']=='partial' and lost['attribution_unknown']
        assert 'native_identity_reset' in lost['errors']
        assert result['attribution']['owned-1'] is None


def test_incremental_small_inventory_admission_is_partial_without_hidden_reads(tmp_path):
    from scripts.benchmark_usage import fixture, oracle, drain
    directory=tmp_path/'native';ids=fixture(directory,'Codex',0,2,65536)
    expected,attribution=oracle(directory,'Codex',ids)
    reader=native_session_meter.IncrementalUsageReader('Codex','root',[directory])
    partial=reader.refresh(native_session_meter.UsageBudget(entries_limit=10))
    if sys.platform not in ('darwin', 'linux'):
        _assert_unsupported_inventory(reader, partial)
        return
    assert not partial['complete'] and not partial['fully_discovered'] and partial['exhausted']
    assert partial['read_bytes']==0 and partial['discovery_entries']==0
    restored=native_session_meter.IncrementalUsageReader('Codex','root',[directory],checkpoint=reader.checkpoint())
    complete=drain(restored)[-1]
    assert complete['resumed_from']==partial['continuation']
    assert complete['totals']==expected and complete['attribution']==attribution


def test_incremental_unsupported_inventory_api_remains_unknown(tmp_path, monkeypatch):
    import sys
    from scripts.benchmark_usage import fixture
    directory=tmp_path/'native';fixture(directory,'Codex',0,1,65536)
    reader=native_session_meter.IncrementalUsageReader('Codex','root',[directory])
    with monkeypatch.context() as patch:
        patch.setattr(sys,'platform','unsupported-abi-test')
        result=reader.refresh()
    assert not result['complete'] and not result['fully_discovered']
    assert result['read_bytes']==0 and result['discovery_entries']==0
    assert reader.state['discovery_errors']==['native_directory_accounting_unavailable']
    from scripts.benchmark_usage import drain, IncompleteDrain
    with pytest.raises(IncompleteDrain, match='unsupported_native_directory_api') as failure:
        drain(reader)
    assert len(failure.value.refreshes)==1


@pytest.mark.parametrize('option,value', [('bytes_limit',16777217),('records_limit',100001),
    ('entries_limit',2001),('seconds_limit',1.01),('bytes_limit',-1),('records_limit',True),
    ('seconds_limit',float('nan')),('seconds_limit',float('inf'))])
def test_usage_budget_cannot_raise_aggregate_caps(option, value):
    with pytest.raises(ValueError): native_session_meter.UsageBudget(**{option:value})


def test_incremental_metadata_replacement_before_replay_is_unknown(tmp_path, monkeypatch):
    from scripts.benchmark_usage import fixture
    directory=tmp_path/'native';fixture(directory,'Codex',0,2,65536)
    child=directory/'owned-1.jsonl';replacement=tmp_path/'replacement'
    replacement.write_bytes(child.read_bytes().replace(b'owned-1',b'foreign'))
    reader=_fixture_reader('Codex','root',directory)
    original=reader._file
    def replace_before_replay(file, budget):
        if file['path']==str(child) and replacement.exists():
            assert file['metadata']['session_id']=='owned-1'
            # Metadata's handle has closed. Windows correctly refuses replacing
            # an open file; exercise the actual gap before replay opens it.
            replacement.replace(child)
        return original(file,budget)
    monkeypatch.setattr(reader,'_file',replace_before_replay)
    result=reader.refresh()
    assert not result['complete']
    lost=next(row for row in result['sessions'] if row['session']=='owned-1')
    assert lost['status']=='partial' and lost['attribution_unknown']
    assert 'native_identity_reset' in lost['errors']


@pytest.mark.parametrize('resumed_first', [True,False])
def test_incremental_resumed_physical_segments_are_order_independent(tmp_path, resumed_first):
    original=tmp_path/'original.jsonl';resumed=tmp_path/'resumed.jsonl'
    _interval_fixture(original,'worker',[(5,100)],'root',owned=True)
    rows=_interval_fixture(resumed,'worker',[(15,150)],'root',owned=True)
    rows[0]['payload']['history_base']={'thread_id':'worker','end_ordinal_exclusive':2,'end_byte_offset':original.stat().st_size}
    rows[1]['payload']['response_id']='worker-new'
    rows[1]['payload']['usage'].update(input_tokens=50,total_tokens=50)
    resumed.write_text('\n'.join(json.dumps(row) for row in rows)+'\n')
    start=datetime.fromisoformat('2026-09-01T00:00:10+00:00').timestamp()
    result=native_session_meter.read_owned_interval([resumed,original] if resumed_first else [original,resumed],
        'worker',start=start)
    assert result['status']=='measured' and result['responses']==1
    assert result['usage']['total_tokens']==50 and result['native_usage']['total_tokens']==150


def test_incremental_partial_checkpoint_preserves_physical_size_for_mutation_detection(tmp_path):
    from scripts.benchmark_usage import fixture
    directory=tmp_path/'native';fixture(directory,'Codex',0,1,1048576)
    reader=_fixture_reader('Codex','root',directory)
    partial=reader.refresh(native_session_meter.UsageBudget(bytes_limit=200000))
    assert not partial['complete'] and partial['exhausted']
    checkpoint=reader.checkpoint();file=next(iter(checkpoint['files'].values()))
    assert file['size']==1048576 and 0 < file['last_complete_record_offset'] < file['size']
    path=directory/'root.jsonl'
    with path.open('r+b') as stream: stream.seek(100000);stream.write(b'y')
    restored=_fixture_reader('Codex','root',directory,checkpoint=checkpoint)
    result=restored.refresh()
    assert not result['complete'] and result['sessions'][0]['attribution_unknown']
    assert 'uncertain_middle_mutation' in result['sessions'][0]['errors']


@pytest.mark.parametrize('host,missing', [('Codex','cached_input_tokens'),
    ('Claude','cache_read_input_tokens'),('Claude','cache_creation_input_tokens')])
def test_incremental_missing_cache_category_is_unknown(tmp_path, host, missing):
    from scripts.benchmark_usage import fixture, event, encoded
    directory=tmp_path/'native';fixture(directory,host,0,1,65536)
    path=directory/'root.jsonl';metadata=path.read_bytes().split(b'\n',1)[0]+b'\n'
    row=event(host,'root',1)
    usage=row['payload']['usage'] if host=='Codex' else row['message']['usage']
    del usage[missing];path.write_bytes(metadata+encoded(row))
    reader=_fixture_reader(host,'root',directory)
    result=reader.refresh()
    assert not result['complete'] and result['sessions'][0]['attribution_unknown']
    assert 'invalid_counter_record' in result['sessions'][0]['errors']


def test_incremental_aggregation_clock_exhaustion_keeps_partial_then_resumes(tmp_path, monkeypatch):
    from scripts.benchmark_usage import fixture, drain, encoded, event, oracle
    directory=tmp_path/'native';ids=fixture(directory,'Codex',0,2,65536)
    reader=_fixture_reader('Codex','root',directory);drain(reader)
    for sid in ids:
        with (directory/f'{sid}.jsonl').open('ab') as stream: stream.write(encoded(event('Codex',sid,65)))
    now=[0.0];original=reader._sessions
    def expire_before_aggregation(budget):
        now[0]=1.1
        return original(budget)
    monkeypatch.setattr(reader,'_sessions',expire_before_aggregation)
    # Semantic injected clock only, never emitted as real performance evidence.
    partial=reader.refresh(native_session_meter.UsageBudget(clock=lambda:now[0]))
    assert partial['exhausted'] and not partial['complete'] and partial['continuation']
    assert all(row['status']=='partial' and row['attribution_unknown'] for row in partial['sessions'])
    monkeypatch.setattr(reader,'_sessions',original)
    completed=reader.refresh();expected,attributed=oracle(directory,'Codex',ids)
    assert completed['resumed_from']==partial['continuation'] and completed['complete']
    assert completed['totals']==expected and completed['attribution']==attributed


def test_incremental_dense_aggregation_progress_survives_fresh_checkpoint_readers(tmp_path, record_property):
    from scripts.benchmark_usage import fixture, drain, encoded, event, oracle
    directory=tmp_path/'native';ids=fixture(directory,'Codex',0,4,65536)
    for sid in ids:
        path=directory/f'{sid}.jsonl';metadata=path.read_bytes().split(b'\n',1)[0]+b'\n'
        with path.open('wb') as stream:
            stream.write(metadata)
            for number in range(1,2049): stream.write(encoded(event('Codex',sid,number)))
    reader=_fixture_reader('Codex','root',directory);drain(reader)
    for sid in ids:
        with (directory/f'{sid}.jsonl').open('ab') as stream: stream.write(encoded(event('Codex',sid,2049)))
    checkpoint=reader.checkpoint();observations=[]
    expected,attributed=oracle(directory,'Codex',ids)
    for _ in range(6):
        reader=_fixture_reader('Codex','root',directory,
            checkpoint=json.loads(json.dumps(checkpoint)))
        now=[0.0];budget=native_session_meter.UsageBudget(clock=lambda:now[0]);original=reader._sessions
        def bounded_aggregation(shared):
            # Deterministically spend the clock on aggregation admissions. The
            # fixture/counters/byte counts are real; this is not a timing sample.
            def advance(): now[0]+=0.4;return now[0]
            shared.clock=advance
            return original(shared)
        reader._sessions=bounded_aggregation
        result=reader.refresh(budget);observations.append(result);checkpoint=reader.checkpoint()
        if result['complete']:break
    else: pytest.fail('fresh CLI readers repeated completed aggregation without progress')
    record_property('usage_dense_checkpoint_observations',json.dumps({
        'measurement_class':'semantic_injected_clock','native_records':4*2049,'refreshes':observations}))
    assert any(not item['complete'] for item in observations)
    assert any('aggregation_budget_exhausted' in row['errors'] for item in observations for row in item['sessions'])
    assert result['totals']==expected and result['attribution']==attributed
