"""Resource-bound and exact-accounting regressions using synthetic native logs."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest
from taskplane import claude_flow_usage as claude, claude_usage_cache as cache
from taskplane.claude_flow_usage import timestamp, normalize_usage, USAGE_KEYS
from taskplane.tests.test_claude_flow import message, write
from typing import Any

# Frozen baseline reducer, retained only as a small-fixture semantic oracle.
def legacy_snapshot(path: Path, session: str, *, agent: str | None = None,
                  cutoff: float | None = None, start: float | None = None) -> dict[str, Any]:
    messages: dict[str, list[tuple[float | None, dict[str, int] | None]]] = {}
    errors: set[str] = set()
    started = None
    matched = False
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except ValueError:
                continue  # A final line can still be in flight.
            if not isinstance(row, dict) or row.get('sessionId') != session:
                continue
            if agent and row.get('agentId') != agent:
                continue
            if not agent and (row.get('agentId') or row.get('isSidechain')):
                continue
            at = timestamp(row.get('timestamp'))
            matched = True
            if at is not None:
                started = at if started is None else min(started, at)
            message = row.get('message')
            if row.get('type') != 'assistant' or not isinstance(message, dict):
                continue
            key = message.get('id') or row.get('requestId')
            if not key or message.get('model') == '<synthetic>':
                continue
            if at is None and (cutoff is not None or start is not None):
                errors.add('missing_message_timestamp')
                continue
            if cutoff is not None and at is not None and at >= cutoff:
                continue
            raw_usage = message.get('usage')
            usage = normalize_usage(raw_usage if isinstance(raw_usage, dict) else {}, provider='claude')
            if not usage['available']:
                messages.setdefault(key, []).append((at, None))
                continue
            # Cache writes are uncached input in the shared host-neutral view.
            uncached = usage['uncached_input_tokens'] + usage['cache_creation_tokens']
            value = {'input_tokens': uncached + usage['cached_input_tokens'],
                     'cached_input_tokens': usage['cached_input_tokens'],
                     'uncached_input_tokens': uncached,
                     'output_tokens': usage['output_tokens'],
                     'reasoning_tokens': usage['reasoning_tokens'],
                     'total_tokens': usage['raw_total_tokens']}
            messages.setdefault(key, []).append((at, value))
    if not matched:
        raise ValueError('Claude session identity unavailable')
    measured = []
    active = False
    for records in messages.values():
        # Stable order preserves streamed rows sharing a native timestamp.
        records.sort(key=lambda row: row[0] if row[0] is not None else float('-inf'))
        present = [value for _, value in records if value is not None]
        if any(any(b[k] < a[k] for k in USAGE_KEYS) for a, b in zip(present, present[1:])):
            errors.add('counter_decreased')
            continue
        end_value = records[-1][1]
        if start is None:
            active = True
            if end_value is None:
                errors.add('missing_message_usage')
            else:
                measured.append(end_value)
            continue
        owned = [(at, value) for at, value in records if at is not None and at >= start]
        active = active or bool(owned)
        if not owned:
            continue
        prior = [value for at, value in records if at is not None and at < start]
        baseline = prior[-1] if prior else dict.fromkeys(USAGE_KEYS, 0)
        if baseline is None or end_value is None:
            errors.add('missing_message_boundary')
            continue
        measured.append({k: end_value[k] - baseline[k] for k in USAGE_KEYS})
    total = ({key: sum(m[key] for m in measured) for key in USAGE_KEYS}
             if measured or (start is not None and not errors) else None)
    sample_times = [at for records in messages.values() for at, value in records if at is not None and value is not None]
    return {'usage': total, 'started': started,
            'measured_at': datetime.fromtimestamp(max(sample_times), timezone.utc).isoformat() if sample_times else None,
            'interval': {'start': start, 'end_exclusive': cutoff},
            'partial': bool(errors), 'errors': sorted(errors), 'active': active}



def take(path, *, budget=None, workspace=None, **kwargs):
    budget = budget or cache.Budget()
    with claude.action_budget(workspace, budget=budget):
        result = claude.read_snapshot(path, 'root', **kwargs)
    assert budget.source_bytes <= budget.max_bytes
    assert budget.records <= budget.max_records
    assert budget.rows <= budget.max_rows
    assert budget.vm_steps <= budget.max_vm_steps
    return result, budget


def catch_up(path, **kwargs):
    for _ in range(100):
        result, _ = take(path, **kwargs)
        if not result.get('coverage', {}).get('backlog_bytes') and not result.get('coverage', {}).get('pending_updates'):
            return result
    pytest.fail('bounded catch-up did not converge')


def test_cold_warm_and_large_append_reconcile_in_bounded_pages(tmp_path):
    path = tmp_path / 'root.jsonl'
    write(path, [message('m' + str(i)) for i in range(2200)])
    first, budget = take(path)
    assert first['partial'] and first['coverage']['backlog_bytes'] > 0
    assert first['coverage']['offset'] > 0 and budget.records == cache.MAX_RECORDS
    result = catch_up(path)
    assert result['usage']['total_tokens'] == 2200 * 142 and not result['partial']
    warm, budget = take(path)
    assert warm == result and budget.records == 0
    assert budget.source_bytes <= 4 * cache.ANCHOR_BYTES and budget.rows < 10
    with path.open('a') as stream:
        stream.write(''.join(json.dumps(message('new' + str(i))) + '\n' for i in range(1700)))
    pending, _ = take(path)
    assert pending['partial']
    assert catch_up(path)['usage']['total_tokens'] == 3900 * 142


def test_fresh_processes_resume_offsets_and_only_read_new_bytes(tmp_path):
    path = tmp_path / 'root.jsonl'
    write(path, [message('m' + str(i)) for i in range(500)])
    code = '''import json,sys
from pathlib import Path
from taskplane import claude_usage_cache as c
b=c.Budget(max_records=73)
with c.action_budget(Path(sys.argv[2]),budget=b):
 r=c.read_snapshot(Path(sys.argv[1]),'root')
print(json.dumps([r,vars(b)],default=str))'''
    outputs = []
    for _ in range(15):
        output = subprocess.check_output([sys.executable, '-c', code, str(path), str(tmp_path)], text=True)
        result, metric = json.loads(output)
        outputs.append((result, metric))
        if not result['partial']:
            break
    assert len(outputs) > 1 and not result['partial']
    assert [o[0]['coverage']['offset'] for o in outputs] == sorted(o[0]['coverage']['offset'] for o in outputs)
    again, metric = json.loads(subprocess.check_output([sys.executable,'-c',code,str(path),str(tmp_path)],text=True))
    assert again['usage'] == result['usage'] and metric['records'] == 0
    assert metric['source_bytes'] <= 4 * cache.ANCHOR_BYTES


@pytest.mark.parametrize('start,cutoff', [(None,None),(None,3),(2,None),(2,3),(0,1),(4,None)])
def test_stream_order_missing_samples_and_filters_match_oracle(tmp_path, start, cutoff):
    rng = random.Random(47)
    rows = []
    for i in range(60):
        row = message('m' + str(rng.randrange(9)), output=rng.randrange(1,12),
                      at='1970-01-01T00:00:0' + str(rng.randrange(1,5)) + 'Z')
        if i % 7 == 0: row['message']['usage'] = {}
        if i % 13 == 0: row['timestamp'] = None
        if i % 17 == 0: row['agentId'] = 'other'
        if i % 19 == 0: row['message']['model'] = '<synthetic>'
        rows.append(row)
    path = tmp_path / 'root.jsonl'; write(path, rows)
    expected = legacy_snapshot(path, 'root', start=start, cutoff=cutoff)
    actual = catch_up(path, start=start, cutoff=cutoff)
    assert {key:actual[key] for key in expected} == expected


def test_late_intermediate_decrease_invalidates_completed_queries(tmp_path):
    path = tmp_path / 'root.jsonl'
    rows = [message(output=2,at='1970-01-01T00:00:01Z'), message(output=10,at='1970-01-01T00:00:03Z')]
    write(path, rows)
    assert not take(path)[0]['partial']
    assert not take(path, cutoff=4)[0]['partial']
    with path.open('a') as stream:
        stream.write(json.dumps(message(output=1,at='1970-01-01T00:00:02Z'))+'\n')
    for args in ({},{'cutoff':4},{'start':3}):
        actual = take(path, **args)[0]
        assert actual['usage'] is None and actual['errors'] == ['counter_decreased']
    before = take(path, cutoff=2)[0]
    assert not before['partial'] and before['usage']['output_tokens'] == 2


def test_boundary_and_missing_final_recovery_match_oracle(tmp_path):
    path = tmp_path / 'root.jsonl'
    rows = [message(output=1,at='1970-01-01T00:00:01Z'), message(output=10,at='1970-01-01T00:00:03Z')]
    rows[0]['message']['usage'] = {}
    write(path, rows)
    assert take(path,start=2)[0]['errors'] == ['missing_message_boundary']
    assert take(path)[0]['usage']['total_tokens'] == 142
    missing = message(at='1970-01-01T00:00:04Z'); missing['message']['usage'] = {}
    with path.open('a') as stream: stream.write(json.dumps(missing)+'\n')
    assert take(path)[0]['errors'] == ['missing_message_usage']
    with path.open('a') as stream:
        stream.write(json.dumps(message(output=11,at='1970-01-01T00:00:05Z'))+'\n')
    assert take(path)[0]['usage']['total_tokens'] == 143


def test_equal_timestamps_use_stable_physical_order(tmp_path):
    path=tmp_path/'root.jsonl'
    write(path,[message(output=3),message(output=2),message(output=10)])
    assert take(path)[0]['errors'] == ['counter_decreased']


def test_missing_time_edge_excluded_from_bounded_query(tmp_path):
    path=tmp_path/'root.jsonl'
    write(path,[message(at=None,output=12),message(at='1970-01-01T00:00:01Z',output=2),
                message(at='1970-01-01T00:00:02Z',output=3)])
    actual=take(path,start=0)[0]
    assert actual['errors']==['missing_message_timestamp']
    assert actual['usage']['output_tokens']==3


def test_many_queries_do_not_scan_existing_query_inventory(tmp_path):
    path=tmp_path/'root.jsonl'; write(path,[message()])
    for i in range(80): take(path,start=i)
    result,budget=take(path)
    assert result['usage']['total_tokens']==142 and budget.rows < 20
    _, warm=take(path)
    assert warm.rows < 10 and warm.vm_steps < 10000


def test_one_action_shares_source_record_query_and_discovery_limits(tmp_path, monkeypatch):
    path=tmp_path/'root.jsonl'; write(path,[message('m'+str(i)) for i in range(30)])
    for i in range(20):
        write(path.with_suffix('')/('subagents/agent-c'+str(i)+'.jsonl'),[message(agent='c'+str(i))])
    budget=cache.Budget(max_records=5,max_entries=4)
    with claude.action_budget(budget=budget) as outer:
        with claude.action_budget() as nested: assert outer is nested
        one=claude.read_snapshot(path,'root')
        two=claude.read_snapshot(path,'root')
        sessions,errors=claude.sessions({'session':'root','run':'r','transcript_path':str(path),'usage':one['usage']},[],None)
    assert budget.records == 5 and two['partial'] and errors > 0
    assert budget.entries <= 4 and budget.files <= 4 and len(sessions) <= 5


def test_partial_discovery_never_claims_unique_root(tmp_path, monkeypatch):
    home=tmp_path/'claude'; projects=home/'projects'; projects.mkdir(parents=True)
    for i in range(9): write(projects/str(i)/'root.jsonl',[message()])
    monkeypatch.setenv('CLAUDE_CONFIG_DIR',str(home)); monkeypatch.delenv('TASKPLANE_CLAUDE_SESSION_ID',raising=False)
    budget=cache.Budget(max_entries=1)
    with claude.action_budget(budget=budget):
        assert claude.transcript('root',{}) is None
    assert budget.discovery_incomplete and budget.entries == 1


@pytest.mark.parametrize('kind',['truncate','replace','same_size','anchor'])
def test_source_generation_changes_never_mix_totals(tmp_path, kind):
    path=tmp_path/'root.jsonl'; write(path,[message('old'),message('other')])
    initial=take(path)[0]
    if kind=='replace':
        new=tmp_path/'new'; write(new,[message('new')]); new.replace(path)
    elif kind=='same_size':
        original=path.read_bytes(); path.write_bytes(original.replace(b'old',b'NEW'))
    elif kind=='anchor':
        write(path,[message('new'),message('other'),message('append')])
    else: write(path,[message('new')])
    result=take(path)[0]
    assert result['partial'] and 'source_generation_changed' in result['errors']
    assert result['coverage']['source_generation'] != initial['coverage']['source_generation']
    assert result['usage']['total_tokens'] == {'replace':142,'truncate':142,'same_size':284,'anchor':426}[kind]


@pytest.mark.parametrize('tail',[b'{"partial":',b'{"text":"\xf0\x9f',json.dumps(message()).encode()])
def test_torn_records_remain_pending_until_newline(tmp_path, tail):
    path=tmp_path/'root.jsonl'; write(path,[message('first')])
    good=path.stat().st_size
    with path.open('ab') as stream: stream.write(tail)
    result=take(path)[0]
    assert result['partial'] and result['coverage']['offset']==good
    with path.open('ab') as stream: stream.write(b'\n'+json.dumps(message('next')).encode()+b'\n')
    result=take(path)[0]
    assert result['coverage']['backlog_bytes']==0
    if tail.startswith(b'{"partial') or b'\xf0' in tail:
        assert 'malformed_record' in result['errors']
        assert result['usage']['total_tokens']==284
    else:
        assert not result['partial'] and result['usage']['total_tokens']==426


def test_oversize_record_discards_in_bounded_resumable_chunks(tmp_path, monkeypatch):
    monkeypatch.setattr(cache,'MAX_RECORD_BYTES',1024)
    path=tmp_path/'root.jsonl'; path.write_bytes(b'x'*10000+b'\n'+json.dumps(message()).encode()+b'\n')
    offsets=[]
    for _ in range(30):
        result,budget=take(path,budget=cache.Budget(max_bytes=2500))
        offsets.append(result['coverage']['offset'])
        if result['coverage']['backlog_bytes']==0: break
    assert len(offsets)>1 and offsets==sorted(offsets)
    assert result['usage']['total_tokens']==142 and result['errors']==['oversized_record']


def test_large_valid_line_uses_finite_chunks(tmp_path, monkeypatch):
    path=tmp_path/'root.jsonl'; row=message(); row['message']['content']='private'*100000
    write(path,[row])
    calls=[]; original=cache._read
    def observed(stream,size,budget):
        calls.append(size); return original(stream,size,budget)
    monkeypatch.setattr(cache,'_read',observed)
    result,budget=take(path)
    assert result['usage']['total_tokens']==142 and not result['partial']
    assert len(calls)<30 and max(calls)<=65536
    assert budget.source_bytes <= path.stat().st_size+2*cache.ANCHOR_BYTES


def test_corrupt_cache_rebuild_is_bounded_and_source_unchanged(tmp_path):
    path=tmp_path/'root.jsonl'; write(path,[message('m'+str(i)) for i in range(1500)])
    original=path.read_bytes(); take(path,workspace=tmp_path)
    location=cache.cache_path(path,'root',None,tmp_path)
    location.write_bytes(b'not a database')
    failed=take(path,workspace=tmp_path)[0]
    assert failed['partial'] and failed['usage'] is None
    pending=take(path,workspace=tmp_path)[0]
    assert pending['partial'] and pending['coverage']['backlog_bytes']>0
    assert not catch_up(path,workspace=tmp_path)['partial']
    assert path.read_bytes()==original


def test_cache_lock_contention_does_not_block_or_replay(tmp_path):
    path=tmp_path/'root.jsonl'; write(path,[message()])
    location=cache.cache_path(path,'root',None,tmp_path); take(path,workspace=tmp_path)
    with open(str(location)+'.lock','a+b') as lock:
        release = cache._acquire_lock(lock)
        try:
            result,budget=take(path,workspace=tmp_path)
        finally:
            release()
    assert result['errors']==['cache_busy'] and budget.records==budget.source_bytes==0


def test_vm_interrupt_does_not_rollback_ingested_source_checkpoint(tmp_path, monkeypatch):
    path=tmp_path/'root.jsonl'; write(path,[message()])
    original=cache._query
    def interrupted(*args): raise sqlite3.OperationalError('interrupted')
    monkeypatch.setattr(cache,'_query',interrupted)
    assert take(path,workspace=tmp_path)[0]['partial']
    monkeypatch.setattr(cache,'_query',original)
    result,budget=take(path,workspace=tmp_path)
    assert not result['partial'] and budget.records==0


def test_concurrent_readers_cannot_double_count(tmp_path):
    path=tmp_path/'root.jsonl'; write(path,[message('m'+str(i)) for i in range(100)])
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:take(path,workspace=tmp_path)[0],range(4)))
    result=catch_up(path,workspace=tmp_path)
    assert not result['partial'] and result['usage']['total_tokens']==14200
    assert all(r['partial'] or r['usage']==result['usage'] for r in results)


def test_cache_contains_metadata_only_and_queries_are_indexed(tmp_path):
    path=tmp_path/'root.jsonl'; row=message(); row['message']['content']='secret prompt body XYZ'
    write(path,[row]); take(path,workspace=tmp_path)
    location=cache.cache_path(path,'root',None,tmp_path)
    assert b'secret prompt body XYZ' not in location.read_bytes()
    with closing(sqlite3.connect(location)) as db, db:
        plan=db.execute('EXPLAIN QUERY PLAN SELECT value FROM samples WHERE gen=? AND mid=? AND value IS NOT NULL '
                        'AND (at,pos)<(?,?) ORDER BY at DESC,pos DESC LIMIT 1',('g','m',1,2)).fetchall()
        assert all('TEMP B-TREE' not in str(row) and 'SCAN samples' not in str(row) for row in plan)


def test_missing_source_allows_existing_recorded_recovery(tmp_path):
    path=tmp_path/'missing.jsonl'
    with pytest.raises(FileNotFoundError): take(path)


def test_small_record_budget_rotates_known_children_across_actions(tmp_path):
    path=tmp_path/'root.jsonl'; write(path,[message('m'+str(i)) for i in range(50)])
    child=path.with_suffix('')/'subagents/agent-child.jsonl'; write(child,[message(agent='child')])
    run={'session':'root','run':'r','transcript_path':str(path),'usage':dict.fromkeys(USAGE_KEYS,0)}
    with claude.action_budget(tmp_path,budget=cache.Budget(max_records=1)):
        first,_=claude.sessions(run,[],None)
    with claude.action_budget(tmp_path,budget=cache.Budget(max_records=1)):
        second,_=claude.sessions(run,[],None)
    assert next(s for s in first if s['session']=='child')['usage'] is None
    assert next(s for s in second if s['session']=='child')['usage']['total_tokens']==142


def test_incompatible_cache_quarantines_recovery_sidecars(tmp_path, monkeypatch):
    path=tmp_path/'root.jsonl'; write(path,[message()]); take(path,workspace=tmp_path)
    location=cache.cache_path(path,'root',None,tmp_path)
    with closing(sqlite3.connect(location)) as db, db:
        meta=json.loads(db.execute('SELECT body FROM meta').fetchone()[0]); meta['schema']=-1
        db.execute('UPDATE meta SET body=?',(json.dumps(meta),))
    # Simulate leftover sidecars after the DB connection has opened. Recovery
    # must move all of them with the rejected database, not attach them later.
    original=cache._ingest
    def rejected(*args,**kwargs):
        for suffix in ('-wal','-shm'):
            Path(str(location)+suffix).write_bytes(b'orphan recovery data')
        return original(*args,**kwargs)
    monkeypatch.setattr(cache,'_ingest',rejected)
    assert take(path,workspace=tmp_path)[0]['partial']
    for suffix in ('-wal','-shm'):
        assert not Path(str(location)+suffix).exists()
        assert Path(str(location)+suffix+'.invalid').exists()
    monkeypatch.setattr(cache,'_ingest',original)
    assert not take(path,workspace=tmp_path)[0]['partial']


def test_recovery_size_guard_refuses_unbounded_sqlite_recovery(tmp_path):
    path=tmp_path/'root.jsonl'; write(path,[message()]); take(path,workspace=tmp_path)
    location=cache.cache_path(path,'root',None,tmp_path)
    with Path(str(location)+'-journal').open('wb') as stream:
        stream.truncate(cache.MAX_RECOVERY_BYTES+1)
    result,budget=take(path,workspace=tmp_path)
    assert result['usage'] is None and result['partial'] and budget.source_bytes==0


def test_sqlite_vm_cap_interrupts_without_unbounded_fallback(tmp_path):
    path=tmp_path/'root.jsonl'; write(path,[message('m'+str(i)) for i in range(200)])
    result,budget=take(path,workspace=tmp_path,budget=cache.Budget(max_vm_steps=1000))
    assert result['partial'] and budget.vm_steps<=1000
    assert not catch_up(path,workspace=tmp_path)['partial']


def test_explicit_child_path_wins_over_discovered_default(tmp_path):
    path=tmp_path/'root.jsonl'; write(path,[message()])
    write(path.with_suffix('')/'subagents/agent-child.jsonl',[message(agent='child',output=1)])
    child=tmp_path/'custom.jsonl'; write(child,[message(agent='child',output=20)])
    run={'run':'r','session':'root','transcript_path':str(path),'usage':dict.fromkeys(USAGE_KEYS,0)}
    rows=[{'run':'r','child':'child','agent_transcript_path':str(child)}]
    sessions,_=claude.sessions(run,rows,None)
    assert next(s for s in sessions if s['session']=='child')['usage']['output_tokens']==20


def test_nonfinite_message_identity_is_a_progressing_gap(tmp_path):
    path=tmp_path/'root.jsonl'; row=message(); row['message']['id']=float('nan')
    write(path,[row,message('valid')])
    result=take(path)[0]
    assert result['usage']['total_tokens']==142
    assert result['errors']==['invalid_message_identity']
    assert result['coverage']['backlog_bytes']==0


@pytest.mark.skipif(not hasattr(os,'mkfifo'),reason='POSIX FIFO guard')
def test_nonregular_source_is_rejected_before_blocking_read(tmp_path):
    path=tmp_path/'fifo'; os.mkfifo(path)
    result=take(path)[0]
    assert result['partial'] and result['usage'] is None


def test_default_large_discovery_is_explicitly_incomplete_and_known_pages_advance(tmp_path):
    path=tmp_path/'root.jsonl'; write(path,[message()])
    children=['c'+str(i) for i in range(cache.MAX_ENTRIES+20)]
    for child in children:
        write(path.with_suffix('')/('subagents/agent-'+child+'.jsonl'),[message(agent=child)])
    run={'run':'r','session':'root','transcript_path':str(path),'usage':dict.fromkeys(USAGE_KEYS,0)}
    with claude.action_budget(tmp_path) as budget:
        unknown,errors=claude.sessions(run,[],None)
    assert errors>0 and budget.entries==cache.MAX_ENTRIES and len(unknown)<=cache.MAX_ENTRIES+1
    seen=set()
    for _ in range(3):
        with claude.action_budget(tmp_path) as budget:
            result,errors=claude.sessions({**run,'worker_sessions':children},[],None)
        assert errors>0 and budget.entries<=cache.MAX_ENTRIES
        seen.update(s['session'] for s in result if s['usage'] is not None)
    assert set(children)<=seen


def test_copied_foreign_filter_cache_cannot_supply_root_totals(tmp_path):
    import shutil
    path=tmp_path/'root.jsonl'; write(path,[message(),message(agent='worker')])
    take(path,workspace=tmp_path)
    root=cache.cache_path(path,'root',None,tmp_path)
    foreign=cache.cache_path(path,'root','worker',tmp_path)
    shutil.copyfile(root,foreign)
    result=take(path,workspace=tmp_path,agent='worker')[0]
    assert result['usage'] is None and result['partial']
    result=take(path,workspace=tmp_path,agent='worker')[0]
    assert not result['partial'] and result['usage']['total_tokens']==142


def test_corrupt_negative_query_vector_is_not_published(tmp_path):
    path=tmp_path/'root.jsonl'; write(path,[message()]); take(path,workspace=tmp_path)
    location=cache.cache_path(path,'root',None,tmp_path)
    with closing(sqlite3.connect(location)) as db, db:
        state=json.loads(db.execute('SELECT body FROM queries').fetchone()[0])
        state['total']['total_tokens']=-1
        db.execute('UPDATE queries SET body=?',(json.dumps(state),))
    result=take(path,workspace=tmp_path)[0]
    assert result['partial'] and result['usage'] is None
    assert take(path,workspace=tmp_path)[0]['usage']['total_tokens']==142


def test_interrupted_ingestion_replays_once_without_advancing_cursor(tmp_path, monkeypatch):
    path=tmp_path/'root.jsonl'; write(path,[message('a'),message('b')])
    original=cache._sample
    def crash(db,budget,meta,*args):
        original(db,budget,meta,*args)
        if meta['rev']==2: raise sqlite3.OperationalError('simulated crash before commit')
    monkeypatch.setattr(cache,'_sample',crash)
    assert take(path,workspace=tmp_path)[0]['partial']
    monkeypatch.setattr(cache,'_sample',original)
    result,budget=take(path,workspace=tmp_path)
    assert not result['partial'] and result['usage']['total_tokens']==284 and budget.records==2


def test_recovery_bytes_share_the_action_budget(tmp_path):
    path=tmp_path/'root.jsonl'; write(path,[message()]); take(path,workspace=tmp_path)
    location=cache.cache_path(path,'root',None,tmp_path)
    Path(str(location)+'-journal').write_bytes(b'xx')
    result,budget=take(path,workspace=tmp_path,budget=cache.Budget(max_recovery_bytes=1))
    assert result['errors']==['action_budget_exhausted'] and budget.recovery_bytes<=1
    assert budget.source_bytes==0


def test_sqlite_external_lock_returns_without_waiting_or_source_replay(tmp_path):
    path=tmp_path/'root.jsonl'; write(path,[message()]); take(path,workspace=tmp_path)
    location=cache.cache_path(path,'root',None,tmp_path)
    with closing(sqlite3.connect(location)) as other, other:
        other.execute('BEGIN EXCLUSIVE')
        result,budget=take(path,workspace=tmp_path)
    assert result['partial'] and result['usage'] is None and budget.source_bytes==0


def test_partial_child_cannot_be_excluded_by_prefix_start_time(tmp_path):
    path=tmp_path/'root.jsonl'; write(path,[message(at='1970-01-01T00:00:00Z')])
    child=path.with_suffix('')/'subagents/agent-child.jsonl'
    write(child,[message('future',agent='child',at='1970-01-01T00:00:03Z'),
                 message('older',agent='child',at='1970-01-01T00:00:01Z')])
    run={'run':'r','session':'root','transcript_path':str(path),'usage':dict.fromkeys(USAGE_KEYS,0)}
    with claude.action_budget(tmp_path,budget=cache.Budget(max_records=2)):
        result,_=claude.sessions(run,[],2)
    assert next(s for s in result if s['session']=='child')['status']=='partial'
    result,_=claude.sessions(run,[],2)
    assert next(s for s in result if s['session']=='child')['usage']['total_tokens']==142


def test_windows_nonblocking_lock_protocol_fixture(tmp_path,monkeypatch):
    from types import SimpleNamespace
    calls=[]
    module=SimpleNamespace(LK_NBLCK=2,LK_UNLCK=0,locking=lambda fd,operation,size:calls.append((operation,size)))
    monkeypatch.setitem(sys.modules,'msvcrt',module)
    with (tmp_path/'lock').open('a+b') as stream:
        release=cache._acquire_lock(stream,'nt')
        release()
    assert calls==[(2,1),(0,1)] and (tmp_path/'lock').read_bytes()==b'\0'


@pytest.mark.parametrize('error,reason',[(ImportError('missing API'),'cache_lock_unsupported'),
                                       (OSError('native busy'),'cache_busy')])
def test_native_lock_failures_never_run_unlocked(tmp_path,monkeypatch,error,reason):
    path=tmp_path/'root.jsonl'; write(path,[message()])
    def denied(_): raise error
    monkeypatch.setattr(cache,'_acquire_lock',denied)
    result,budget=take(path,workspace=tmp_path)
    assert result['errors']==[reason] and budget.source_bytes==0


@pytest.mark.parametrize('suffix', ['-journal', '-wal', '-shm'])
def test_recovery_guard_observes_sidecars_after_lock_acquisition(tmp_path, monkeypatch, suffix):
    path = tmp_path / 'root.jsonl'
    write(path, [message()])
    take(path, workspace=tmp_path)
    location = cache.cache_path(path, 'root', None, tmp_path)
    original = location.read_bytes()
    acquire = cache._acquire_lock
    def previous_writer_crashed(lock):
        release = acquire(lock)
        with Path(str(location) + suffix).open('wb') as stream:
            stream.truncate(cache.MAX_RECOVERY_BYTES + 1)
        return release
    monkeypatch.setattr(cache, '_acquire_lock', previous_writer_crashed)
    def no_recovery(*args, **kwargs):
        pytest.fail('SQLite opened a sidecar larger than the recovery budget')
    monkeypatch.setattr(cache.sqlite3, 'connect', no_recovery)
    result, budget = take(path, workspace=tmp_path)
    assert result['partial'] and result['usage'] is None and budget.source_bytes == 0
    assert location.read_bytes() == original
    assert Path(str(location) + suffix).stat().st_size == cache.MAX_RECOVERY_BYTES + 1
    assert not Path(str(location) + '.invalid').exists()


def test_codex_replay_persists_bounded_progress_and_suffix_only_append(tmp_path, monkeypatch):
    from taskplane import codex_usage_cache as cache, native_session_meter as meter
    from taskplane.tests.test_native_session_meter import _usage_file, _usage_record
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    monkeypatch.setattr(meter, 'MAX_RECORD_BYTES', 1024)
    monkeypatch.setattr(meter, 'MAX_METADATA_BYTES', 1024)
    path = _usage_file(tmp_path / 'large.jsonl', [
        item for n in range(1, 31) for item in (
            {'type': 'response_item', 'payload': {'text': 'private-' + 'x' * 600}},
            _usage_record(n, n))])
    offsets = []
    for _ in range(60):
        budget = cache.Budget(max_bytes=8192, max_records=8)
        result = meter.read_owned_interval([path], 'child', start=0, budget=budget)
        assert budget.source_bytes <= budget.max_bytes
        assert budget.records <= budget.max_records
        with closing(sqlite3.connect(cache.cache_path())) as db, db:
            offsets.append(json.loads(db.execute('SELECT meta FROM sources').fetchone()[0])['offset'])
        if result['status'] == 'measured':
            break
    assert result['usage']['total_tokens'] == 30
    assert len(set(offsets)) > 2 and offsets == sorted(offsets)
    warm = cache.Budget(max_bytes=8192, max_records=8)
    assert meter.read_owned_interval([path], 'child', start=0, budget=warm)['usage']['total_tokens'] == 30
    assert warm.records == 0 and warm.source_bytes < 2048
    with path.open('a') as stream:
        stream.write(json.dumps(_usage_record(35, 35)) + '\n')
    append = cache.Budget(max_bytes=8192, max_records=8)
    assert meter.read_owned_interval([path], 'child', start=0, budget=append)['usage']['total_tokens'] == 35
    assert append.records == 1 and append.source_bytes < 4096
    assert b'private-' not in cache.cache_path().read_bytes()


def test_codex_replay_resumes_in_fresh_process(tmp_path, monkeypatch):
    from taskplane import codex_usage_cache as cache, native_session_meter as meter
    from taskplane.tests.test_native_session_meter import _usage_file, _usage_record
    import subprocess
    import sys
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    path = _usage_file(tmp_path / 'restart.jsonl', [_usage_record(n, n) for n in range(1, 14)])
    code = '''import json,sys
from taskplane import codex_usage_cache as c,native_session_meter as m
m.MAX_RECORD_BYTES=1024;m.MAX_METADATA_BYTES=1024
b=c.Budget(max_bytes=8192,max_records=4)
r=m.read_owned_interval([sys.argv[1]],'child',start=0,budget=b)
print(json.dumps([r,b.records]))'''
    results = [json.loads(subprocess.check_output([sys.executable, '-c', code, str(path)], text=True)) for _ in range(9)]
    assert results[-1][0]['usage']['total_tokens'] == 13
    assert results[-1][1] == 0
    assert any(r[0]['status'] == 'partial' for r in results[:-1])
    assert meter.read_owned_interval([path], 'child', start=0)['usage']['total_tokens'] == 13


def test_codex_cache_corruption_and_replacement_are_not_measured_zero(tmp_path, monkeypatch):
    from taskplane import codex_usage_cache as cache, native_session_meter as meter
    from taskplane.tests.test_native_session_meter import _usage_file, _usage_record
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    path = _usage_file(tmp_path / 'generation.jsonl', [_usage_record(10, 1)])
    assert meter.read_owned_interval([path], 'child', start=0)['usage']['total_tokens'] == 10
    cache.cache_path().write_bytes(b'broken derived state')
    assert meter.read_owned_interval([path], 'child', start=0)['status'] == 'partial'
    assert meter.read_owned_interval([path], 'child', start=0)['usage']['total_tokens'] == 10
    replacement = _usage_file(tmp_path / 'replacement.jsonl', [_usage_record(30, 2)])
    replacement.replace(path)
    result = meter.read_owned_interval([path], 'child', start=0)
    assert result['usage'] is None and 'source_generation_changed' in result['errors']
    assert meter.read_owned_interval([path], 'child', start=0)['usage'] is None


def test_codex_query_failure_does_not_rollback_ingested_offset(tmp_path, monkeypatch):
    from taskplane import codex_usage_cache as cache, native_session_meter as meter
    from taskplane.tests.test_native_session_meter import _usage_file, _usage_record
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    path = _usage_file(tmp_path / 'atomic.jsonl', [_usage_record(10, 1)])
    original = cache._reduce
    monkeypatch.setattr(cache, '_reduce', lambda *args: (_ for _ in ()).throw(cache.Exhausted('query')))
    assert meter.read_owned_interval([path], 'child', start=0)['status'] == 'partial'
    monkeypatch.setattr(cache, '_reduce', original)
    budget = cache.Budget()
    assert meter.read_owned_interval([path], 'child', start=0, budget=budget)['usage']['total_tokens'] == 10
    assert budget.records == 0


def test_codex_source_commit_crash_rolls_back_cursor_and_rows(tmp_path, monkeypatch):
    from taskplane import codex_usage_cache as cache, native_session_meter as meter
    from taskplane.tests.test_native_session_meter import _usage_file, _usage_record
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    path = _usage_file(tmp_path / 'atomic-source.jsonl', [_usage_record(10, 1)])
    original = cache._ingest
    def crash(*args):
        original(*args)
        raise ValueError('before source commit')
    monkeypatch.setattr(cache, '_ingest', crash)
    assert meter.read_owned_interval([path], 'child', start=0)['status'] == 'partial'
    monkeypatch.setattr(cache, '_ingest', original)
    assert meter.read_owned_interval([path], 'child', start=0)['usage']['total_tokens'] == 10


def test_codex_torn_record_and_impossible_budget_are_explicit(tmp_path, monkeypatch):
    from taskplane import codex_usage_cache as cache, native_session_meter as meter
    from taskplane.tests.test_native_session_meter import _usage_file, _usage_record
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    path = _usage_file(tmp_path / 'torn.jsonl', [_usage_record(10, 1)])
    with path.open('a') as stream:
        stream.write(json.dumps(_usage_record(20, 2)))
    assert meter.read_owned_interval([path], 'child', start=0)['status'] == 'partial'
    with path.open('a') as stream:
        stream.write('\n')
    assert meter.read_owned_interval([path], 'child', start=0)['usage']['total_tokens'] == 20
    too_small = meter.read_owned_interval([path], 'child', start=0, budget=cache.Budget(max_bytes=1024))
    assert too_small['usage'] is None and too_small['errors'] == ['budget_too_small']


def _codex_catalog_fixture(home, entries):
    with closing(sqlite3.connect(home / 'state_5.sqlite')) as db, db:
        db.execute('CREATE TABLE threads(id TEXT PRIMARY KEY,rollout_path TEXT NOT NULL)')
        db.executemany('INSERT INTO threads VALUES(?,?)', [(sid, str(path)) for sid, path in entries])


def _codex_native(path, sid, parent=None, total=10):
    from taskplane.tests.test_native_session_meter import _usage_record
    record = _usage_record(total, 1)
    record['payload']['thread_id'] = sid
    rows = [{'type': 'session_meta', 'payload': {'id': sid, 'parent_thread_id': parent,
             'timestamp': '2026-09-01T00:00:00Z'}}, record]
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    return path


def test_codex_catalog_keysets_reach_late_child_without_glob_or_prefix_restart(tmp_path, monkeypatch):
    from taskplane import codex_usage_cache as codex, native_session_meter as meter
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    monkeypatch.setattr(meter, 'MAX_RECORD_BYTES', 1024)
    monkeypatch.setattr(meter, 'MAX_METADATA_BYTES', 1024)
    root = _codex_native(tmp_path / 'root.jsonl', 'root')
    entries = [(f'a{n:03}', _codex_native(tmp_path / f'{n}.jsonl', f'a{n:03}')) for n in range(60)]
    late = _codex_native(tmp_path / 'late.jsonl', 'z-child', 'root')
    _codex_catalog_fixture(tmp_path, [*entries, ('root', root), ('z-child', late)])
    monkeypatch.setattr(Path, 'glob', lambda *_: (_ for _ in ()).throw(AssertionError('inventory fallback')))
    found = False
    cursors = []
    for _ in range(25):
        budget = codex.Budget(max_bytes=8192, max_entries=16)
        metadata, diagnostics = codex.discover({'run': 'r', 'session': 'root', 'transcript_path': str(root)}, budget)
        assert budget.entries <= 16 and budget.files <= 16 and budget.source_bytes <= 8192
        assert budget.rows <= budget.max_rows and budget.vm_steps <= budget.max_vm_steps
        assert not budget.coverage()['native_inventory_complete']
        with closing(sqlite3.connect(codex.cache_path())) as db, db:
            cursors.append(json.loads(db.execute('SELECT state FROM cursors').fetchone()[0])['last'])
        found = found or any(row['session_id'] == 'z-child' for row in metadata)
        if found:
            break
    assert found and len(set(cursors)) > 5
    assert diagnostics[-1]['scope'] == 'inventory'


def test_codex_known_worker_catalog_lookup_precedes_background_sweep(tmp_path, monkeypatch):
    from taskplane import codex_usage_cache as codex
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    root = _codex_native(tmp_path / 'root.jsonl', 'root')
    child = _codex_native(tmp_path / 'child.jsonl', 'z-known', 'root')
    _codex_catalog_fixture(tmp_path, [('root', root), ('z-known', child)])
    metadata, _ = codex.discover({'run': 'r', 'session': 'root', 'worker_sessions': ['z-known']}, codex.Budget())
    assert {m['session_id'] for m in metadata} == {'root', 'z-known'}


def test_codex_catalog_unavailable_preserves_exact_paths_and_explicit_incompleteness(tmp_path, monkeypatch):
    from taskplane import flow_usage
    from taskplane.tests.test_usage_accounting import counts
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    root = _codex_native(tmp_path / 'root.jsonl', 'root', total=40)
    run = {'run': 'r', 'session': 'root', 'at': '2026-09-01T00:00:00Z',
           'usage': counts(0), 'transcript_path': str(root)}
    result = flow_usage.reconcile(run, [])
    assert result['tokens']['total_tokens'] == 40
    assert result['token_coverage']['discovery_status'] == 'partial'
    assert result['token_coverage']['catalog_status'] == 'partial'
    assert result['token_coverage']['native_inventory_complete'] is False


def test_codex_run_subtotal_survives_rotating_session_pages(tmp_path, monkeypatch):
    from taskplane import flow_usage, codex_usage_cache as codex, native_session_meter as meter
    from taskplane.tests.test_usage_accounting import counts
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    monkeypatch.setattr(meter, 'MAX_RECORD_BYTES', 1024)
    monkeypatch.setattr(meter, 'MAX_METADATA_BYTES', 1024)
    root = _codex_native(tmp_path / 'root.jsonl', 'root', total=40)
    children = [(f'child-{n:02}', _codex_native(tmp_path / f'child-{n}.jsonl', f'child-{n:02}', 'root', n + 1))
                for n in range(12)]
    _codex_catalog_fixture(tmp_path, [('root', root), *children])
    run = {'run': 'r', 'session': 'root', 'at': '2026-09-01T00:00:00Z',
           'usage': counts(0), 'transcript_path': str(root)}
    totals = []
    for _ in range(24):
        with codex.action_budget(budget=codex.Budget(max_entries=24, max_bytes=32768)) as budget:
            result = flow_usage.reconcile(run, [])
            assert budget.files <= budget.max_entries
            totals.append(result['tokens']['total_tokens'] if result['tokens'] else None)
    assert totals[-1] == 40 + sum(range(1, 13))
    assert totals[-4:] == [118] * 4
    assert result['token_coverage']['measured_sessions'] == 13


def test_codex_cache_lock_and_recovery_budget_fail_without_source_reads(tmp_path, monkeypatch):
    from taskplane import codex_usage_cache as codex, native_session_meter as meter
    from taskplane.tests.test_native_session_meter import _usage_file, _usage_record
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    source = _usage_file(tmp_path / 'locked.jsonl', [_usage_record(10, 1)])
    location = codex.cache_path()
    with location.with_suffix('.lock').open('a+b') as lock:
        release = cache._acquire_lock(lock)
        try:
            budget = codex.Budget()
            result = meter.read_owned_interval([source], 'child', start=0, budget=budget)
            assert result['status'] == 'partial' and result['usage'] is None
            assert budget.source_bytes == 0
        finally:
            release()
    journal = Path(str(location) + '-journal')
    journal.write_bytes(b'x' * 2048)
    budget = codex.Budget(max_recovery_bytes=1024)
    result = meter.read_owned_interval([source], 'child', start=0, budget=budget)
    assert result['status'] == 'partial' and 'recovery_bytes' in result['errors']
    assert budget.source_bytes == 0


def test_codex_flow_import_and_cache_paths_without_posix_apis(tmp_path):
    script = '''
import os, sys
from pathlib import Path
root = Path(sys.argv[1])
os.environ['HOME'] = str(root)
sys.modules['fcntl'] = None
if hasattr(os, 'getuid'):
    del os.getuid
from taskplane import flow, codex_usage_cache as codex
codex.tempfile.gettempdir = lambda: str(root)
standalone = codex.cache_path()
assert standalone.parent.is_dir()
assert standalone == codex.cache_path()
workspace = codex.cache_path(root)
assert workspace.is_relative_to(root.resolve())
assert workspace.name.startswith('codex-usage-') and workspace.suffix == '.sqlite3'
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('fail_body', [False, True])
def test_codex_database_uses_windows_lock_and_releases_on_exit(tmp_path, monkeypatch, fail_body):
    from types import SimpleNamespace
    from taskplane import codex_usage_cache as codex
    location = tmp_path / 'usage.sqlite3'
    monkeypatch.setattr(codex, 'cache_path', lambda _: location)
    calls = []
    def locking(fd, operation, size):
        assert size == 1
        calls.append(operation)
    monkeypatch.setitem(sys.modules, 'msvcrt', SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=locking))
    acquire = cache._acquire_lock
    monkeypatch.setattr(cache, '_acquire_lock', lambda stream: acquire(stream, 'nt'))
    def use_database():
        with codex._database(codex.Budget()) as db:
            assert calls == [2]
            assert db.execute('SELECT 1').fetchone() == (1,)
            if fail_body:
                raise RuntimeError('body failed')
    if fail_body:
        with pytest.raises(RuntimeError, match='body failed'):
            use_database()
    else:
        use_database()
    assert calls == [2, 0]
    assert location.with_suffix('.lock').read_bytes() == b'\0'


@pytest.mark.parametrize('error', [ImportError('missing API'), OSError('native busy')])
def test_codex_native_lock_failure_never_opens_database_or_sources(tmp_path, monkeypatch, error):
    from taskplane import codex_usage_cache as codex, native_session_meter as meter
    from taskplane.tests.test_native_session_meter import _usage_file, _usage_record
    source = _usage_file(tmp_path / 'child.jsonl', [_usage_record(10, 1)])
    monkeypatch.setattr(codex, 'cache_path', lambda _: tmp_path / 'usage.sqlite3')
    def denied(_):
        raise error
    monkeypatch.setattr(cache, '_acquire_lock', denied)
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **kw: pytest.fail('unlocked database open'))
    budget = codex.Budget()
    result = meter.read_owned_interval([source], 'child', start=0, budget=budget)
    assert result['status'] == 'partial' and result['usage'] is None
    assert budget.source_bytes == 0
    if isinstance(error, ImportError):
        assert 'cache_lock_unsupported' in result['errors']


@pytest.mark.parametrize('target', ['database', 'lock'])
def test_codex_cache_rejects_symlinks_before_locking(tmp_path, monkeypatch, target):
    from taskplane import codex_usage_cache as codex
    location = tmp_path / 'usage.sqlite3'
    destination = tmp_path / 'outside'
    destination.write_bytes(b'preserve')
    candidate = location if target == 'database' else location.with_suffix('.lock')
    candidate.symlink_to(destination)
    monkeypatch.setattr(codex, 'cache_path', lambda _: location)
    monkeypatch.setattr(cache, '_acquire_lock', lambda _: pytest.fail('symlink reached locking'))
    with pytest.raises(ValueError, match='symlinked'):
        with codex._database(codex.Budget()):
            pytest.fail('symlinked cache opened')
    assert destination.read_bytes() == b'preserve'


def test_codex_oversized_record_discards_forward_with_permanent_gap(tmp_path, monkeypatch):
    from taskplane import codex_usage_cache as codex, native_session_meter as meter
    from taskplane.tests.test_native_session_meter import _usage_file, _usage_record
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    monkeypatch.setattr(meter, 'MAX_RECORD_BYTES', 1024)
    monkeypatch.setattr(meter, 'MAX_METADATA_BYTES', 1024)
    path = _usage_file(tmp_path / 'oversized.jsonl', [
        {'type': 'response_item', 'payload': {'text': 'SECRET_PAYLOAD_' * 1500}}, _usage_record(20, 1)])
    for _ in range(20):
        result = meter.read_owned_interval([path], 'child', start=0, budget=codex.Budget(max_bytes=8192))
        if result['native_usage'] is not None:
            break
    assert result['native_usage']['total_tokens'] == 20
    assert result['usage'] is None and 'oversized_record' in result['errors']
    assert result['status'] == 'partial'
    assert b'SECRET_PAYLOAD_' not in codex.cache_path().read_bytes()


def test_codex_interval_changes_cannot_reuse_prior_subtotal(tmp_path, monkeypatch):
    from taskplane import native_session_meter as meter
    from taskplane.tests.test_native_session_meter import _usage_file, _usage_record
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    path = _usage_file(tmp_path / 'interval.jsonl', [
        _usage_record(10, 1, at='2026-09-01T00:00:05Z'),
        _usage_record(30, 2, at='2026-09-01T00:00:15Z')])
    start = datetime.fromisoformat('2026-09-01T00:00:10+00:00').timestamp()
    assert meter.read_owned_interval([path], 'child', start=0)['usage']['total_tokens'] == 30
    assert meter.read_owned_interval([path], 'child', start=start)['usage']['total_tokens'] == 20
    assert meter.read_owned_interval([path], 'child', start=0, end=start)['usage']['total_tokens'] == 10
