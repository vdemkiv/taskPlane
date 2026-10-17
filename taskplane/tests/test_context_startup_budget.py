"""Functional startup and immutable legacy-account fixtures; not host certification."""
from copy import deepcopy
import json

import pytest

from taskplane import flow, host_capabilities, workflow as w, workflow_host
from taskplane.context import encode
from taskplane.tests.test_worker_runtime import setup, reserve
from taskplane.tests.test_farm_workers import requires_native_reader


def claude_startup_event(c, state, parent, grant, *, actor='child-0', operation='stop', call='typed-startup'):
    import base64
    import shlex
    from taskplane import claude_worker_invocation as old, primitives
    from taskplane.tests.test_farm_workers import invocation_event
    request = ({'path': 'input.py'} if operation == 'source-probe' else {
        'schema': 'taskplane.startup-control/v1', 'request_id': call, 'request_reference': 'fixture/user',
        'run': state['run'], 'grant': grant, 'attempt': 1, 'reason': 'Fixture startup control'})
    if operation == 'omit-optional':
        request['inputs'] = ['support/history']
    encoded = base64.urlsafe_b64encode(primitives.canonical_bytes(request)).decode().rstrip('=')
    words = [*old._runtime(None, None), 'flow', 'worker', '--operation', operation, '--run', state['run'],
             '--grant', grant, '--workspace', str(c.workspace), '--startup-request', encoded]
    if actor == 'root':
        words += ['--expected-revision', str(state['revision'])]
    event = invocation_event(c, state, parent, actor=actor, call=call)
    event['tool_input']['command'] = shlex.join(words)
    return event


def claude_startup_fixture(tmp_path, monkeypatch, count=1):
    from taskplane.tests.test_farm_workers import integrated_claude
    from taskplane.context_handoff import binding
    c, state, parent, items = integrated_claude(tmp_path, monkeypatch, count=count)
    db = c._read(c._path()); current = db['runs'][state['run']]
    # Fixture upgrade only: production must use the validated quiescent upgrade API.
    current['context_contract'] = 'bounded/v3'
    for row in current['workers'].values():
        row['binding'] = binding(current)
        row['startup_supporting'] = ['support/history']
        row['control_epoch'] = 0
    c._write(c._path(), db)
    return c, state, parent, items


@requires_native_reader
@pytest.mark.parametrize('actor', ['root', 'child-0'])
@pytest.mark.parametrize('operation', ['startup-status', 'stop', 'omit-optional'])
def test_claude_typed_controls_before_claim_keep_identity_and_reservation(tmp_path, monkeypatch, actor, operation):
    from taskplane import context_handoff
    from taskplane.tests.test_farm_workers import rewritten
    c, s, parent, items = claude_startup_fixture(tmp_path, monkeypatch)
    grant = items[0]['grant']['grant_id']
    event = claude_startup_event(c, s, parent, grant, actor=actor, operation=operation)
    monkeypatch.setattr(context_handoff, 'Session', lambda *a, **k: pytest.fail('Control loaded startup context'))
    # Stale source bytes must not strand control of an otherwise exact live grant.
    (tmp_path/'input.py').write_text('changed = True\n')
    updated, argv = rewritten(event)
    assert rewritten(event)[1] == argv
    result = workflow_host.Controller.invoke_claude(argv)
    assert result['operation'] == operation and not result['reservation_released']
    assert not result['result_accepted'] and len(encode(result)) <= 4096
    row = c._read(c._path())['runs'][s['run']]['workers'][grant]
    assert not row.get('claimed_at') and not row.get('context_receipt')
    with pytest.raises(w.Refusal, match='consumed'):
        workflow_host.Controller.invoke_claude(argv)
    flow.hook({**event, 'hook_event_name': 'PostToolUse', 'tool_input': updated})
    if operation == 'stop':
        status = claude_startup_event(c, s, parent, grant, actor=actor, operation='startup-status', call='after-stop')
        _, status_argv = rewritten(status)
        assert workflow_host.Controller.invoke_claude(status_argv)['state'] == 'cancel_requested'
    assert not c._read(c._path())['runs'][s['run']]['decisions']


@requires_native_reader
def test_claude_typed_control_cannot_target_another_worker(tmp_path, monkeypatch):
    from taskplane.tests.test_farm_workers import rewritten
    c, s, parent, items = claude_startup_fixture(tmp_path, monkeypatch, count=2)
    event = claude_startup_event(c, s, parent, items[1]['grant']['grant_id'])
    with pytest.raises(w.Refusal, match='observed worker actor'):
        rewritten(event)
    assert all(not row.get('stop_requested') for row in c._read(c._path())['runs'][s['run']]['workers'].values())


@requires_native_reader
def test_claude_typed_probe_dispatch_keeps_incomplete_timing(tmp_path, monkeypatch):
    import hashlib
    from taskplane import worker_runtime as workers, workflow_write_guard as guard
    from taskplane.tests.test_farm_workers import rewritten
    c, s, parent, items = claude_startup_fixture(tmp_path, monkeypatch)
    grant = items[0]['grant']['grant_id']
    db = c._read(c._path()); row = db['runs'][s['run']]['workers'][grant]
    row.update(claimed_at='fixture', context_receipt={'fixture': 'complete'},
               input_manifest={'input.py': hashlib.sha256((tmp_path/'input.py').read_bytes()).hexdigest()})
    row['startup_account'] = guard.new_account(row, {'required_body_bytes': 10, 'startup_packing': {'normal_responses': 5}})
    row['startup_account']['stage'] = 'brief_ready'
    c._write(c._path(), db)
    class Delivered:
        def validate(self, receipt):
            assert receipt == {'fixture': 'complete'}
    monkeypatch.setattr(workers, 'worker_session', lambda *a: Delivered())
    event = claude_startup_event(c, s, parent, grant, operation='source-probe')
    updated, argv = rewritten(event)
    result = workflow_host.Controller.invoke_claude(argv)
    assert result['body'] == {'encoding': 'utf-8', 'text': 'value = 1\n'} and not result['implementation_permitted']
    flow.hook({**event, 'hook_event_name': 'PostToolUse', 'tool_input': updated,
               'tool_response': {'stdout': encode(result).decode()}})
    row = c._read(c._path())['runs'][s['run']]['workers'][grant]
    assert row['startup_account']['stage'] == 'source_probe_pending'
    assert row['startup_account']['execution'] == 'incomplete'


def test_startup_invocation_schema_replay_failure_and_expiry_are_distinct_from_claim(monkeypatch):
    import base64
    import os
    import shlex
    import sys
    from taskplane import claude_worker_invocation as old, primitives
    from taskplane.workflow_write_guard import StartupInvocationTransport as transport
    from taskplane.tests.test_claude_worker_invocation import binding, RUNTIME
    if os.name != 'posix' or sys.platform not in ('darwin', 'linux'):
        with pytest.raises(w.Refusal, match='transport is unavailable') as refusal:
            old._runtime(None, None)
        assert refusal.value.reason == 'unsupported_authority'
        return
    clock = {'utc_ns': 1_800_000_000_000_000_000, 'monotonic_ns': 10_000_000_000, 'boot_id': 'fixture-boot'}
    monkeypatch.setattr(old, '_clock', lambda: dict(clock))
    monkeypatch.setattr(old, 'runtime_digest', lambda: RUNTIME)
    request = {'path': 'input.py'}
    encoded = base64.urlsafe_b64encode(primitives.canonical_bytes(request)).decode().rstrip('=')
    argv = [*old._runtime(None, None), 'flow', 'worker', '--operation', 'source-probe', '--run', 'run',
            '--grant', 'grant', '--workspace', '/workspace', '--startup-request', encoded]
    command = transport.parse_command(shlex.join(argv))
    who = binding(); who.update(kind='worker-startup', workspace='/workspace', claimed=True,
                                operation='source-probe', request_sha256=primitives.content_fingerprint(request))
    record, rewritten = transport.issue_record(command, {'command': command.command}, who)
    with pytest.raises(w.Refusal): old.validate_record(record)
    with pytest.raises(w.Refusal): old.parse_command(command.command)
    parsed, reference = transport.parse_cli(shlex.split(rewritten['command'])[1:])
    assert parsed.argv == command.argv and reference == record['reference']
    stable = deepcopy(record)
    def fail_save(value):
        assert value['state'] == 'consumed'
        raise OSError('fixture disk failure')
    with pytest.raises(w.Refusal, match='durably saved'):
        transport.consume_record(record, parsed, reference, who, persist=fail_save,
                                 dispatch=lambda: pytest.fail('Dispatched before successful save'))
    assert record['state'] == 'consumed'
    with pytest.raises(w.Refusal, match='consumed'):
        transport.consume_record(record, parsed, reference, who, persist=lambda r: None, dispatch=lambda: None)
    clock['monotonic_ns'] += old.TTL_NS
    with pytest.raises(w.Refusal, match='expired'):
        transport.consume_record(stable, parsed, reference, who, persist=lambda r: None, dispatch=lambda: None)


@pytest.mark.parametrize('encoded', ['e30=', 'e30\n', 'eyJwYXRoIjoiaW5wdXQucHkiLCAicGF0aCI6Im90aGVyIn0', 'bnVsbA', 'W10', 'x'*5500],
                         ids=['padding', 'newline', 'duplicate-key', 'null', 'array', 'oversized'])
def test_startup_invocation_payload_rejects_ambiguous_or_oversized_encoding(encoded):
    from taskplane.workflow_write_guard import StartupInvocationTransport
    with pytest.raises(w.Refusal): StartupInvocationTransport.request(encoded)


@requires_native_reader
@pytest.mark.parametrize('defect', ['operation', 'argv-order', 'foreign-reference', 'attempt', 'runtime', 'unobserved-root'])
def test_claude_typed_transport_cannot_expand_an_issued_authority(tmp_path, monkeypatch, defect):
    from taskplane import claude_worker_invocation as old
    from taskplane.tests.test_farm_workers import rewritten
    c, s, parent, items = claude_startup_fixture(tmp_path, monkeypatch)
    grant = items[0]['grant']['grant_id']
    event = claude_startup_event(c, s, parent, grant, actor='root', operation='startup-status')
    _, argv = rewritten(event)
    if defect == 'operation':
        argv[argv.index('--operation') + 1] = 'stop'
    elif defect == 'argv-order':
        argv[3:7] = argv[5:7] + argv[3:5]
    elif defect == 'foreign-reference':
        argv[-1] = old._reference('foreign-root')
    elif defect in {'attempt', 'unobserved-root'}:
        db = c._read(c._path())
        if defect == 'attempt':
            db['runs'][s['run']]['workers'][grant]['attempt'] += 1
        else:
            next(a for a in db['admissions'].values() if a['call_id'] == 'typed-startup')['root_observed'] = False
        c._write(c._path(), db)
    else:
        runtime = host_capabilities.runtime_identity()
        monkeypatch.setattr(host_capabilities, 'runtime_identity', lambda: {**runtime, 'fixture_changed': True})
    with pytest.raises(w.Refusal):
        workflow_host.Controller.invoke_claude(argv)
    assert not c._read(c._path())['runs'][s['run']]['workers'][grant].get('stop_requested')


@requires_native_reader
def test_claude_typed_control_concurrent_replay_dispatches_once(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from taskplane.tests.test_farm_workers import rewritten
    c, s, parent, items = claude_startup_fixture(tmp_path, monkeypatch)
    grant = items[0]['grant']['grant_id']
    _, argv = rewritten(claude_startup_event(c, s, parent, grant, actor='root'))
    def invoke(_):
        try:
            return workflow_host.Controller.invoke_claude(argv)['state']
        except w.Refusal as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(invoke, range(2)))
    assert results.count('cancel_requested') == 1
    assert sum('consumed' in result for result in results) == 1
    row = c._read(c._path())['runs'][s['run']]['workers'][grant]
    assert row['control_epoch'] == 1 and row['startup_control_responses'] == 1


def budget_fixture(*, legacy=True):
    from taskplane import workflow_write_guard as guard
    from taskplane.context import digest
    row = {'run': 'r', 'grant_id': 'g', 'attempt': 1, 'binding': {'run': 'r'},
           'expected_runtime': {'root': 'fixture-runtime'}, 'state': 'bootstrapping', 'control_epoch': 0}
    # Historical persisted records are fixtures, never new_account output.
    capability = host_capabilities.startup_timing_capability('unknown', row['expected_runtime'])
    policy = {'mode': 'required' if host_capabilities.qualified_startup_timing(
        capability, 'unknown', row['expected_runtime']) else 'advisory',
        'host': 'unknown', 'capability': capability, 'requirements_epochs': {}, 'task_epoch': 0, 'source': {}}
    row['startup_account_schema'] = 'taskplane.startup-account/v1' if legacy else 'taskplane.startup-account/v2'
    row['startup_account'] = {
        'schema': row['startup_account_schema'],
        'binding': {k: deepcopy(row[k]) for k in ('run', 'grant_id', 'attempt', 'binding', 'expected_runtime')},
        'timing_policy': policy, 'timing_policy_sha256': digest(policy),
        'limits': deepcopy(guard.STARTUP_LIMITS), 'stage': 'prepared',
        'mandatory_unique_bytes': 100, 'packing': {'normal_responses': 5},
        'operations': {}, 'processing_ns': 0 if legacy else None,
        'observed_processing_ns': None, 'execution': 'incomplete',
        'responses_reserved': 0, 'responses_emitted': 0, 'response_bytes': 0,
        'created_monotonic_ns': 0, 'measurement': 'incomplete'}
    return row


def measured_response(row, name, kind='context', start=100, end=200, result=None):
    from taskplane import workflow_write_guard as guard
    from taskplane.context import digest
    result = result or {'response': name}
    guard.reserve_response(row, name, kind, digest({'operation': name}))
    guard.emitted_response(row, name, result)
    spans = [{'clock': 'fixture-boot', 'start_ns': start, 'end_ns': end, 'source': name}]
    guard.complete_response(row, name, digest(result), spans, native_reference='fixture/' + name,
                            automatic=True, runtime=row['expected_runtime'])
    return spans


def test_startup_account_spends_crashed_reservation_and_retries_do_not_reset():
    from taskplane import workflow_write_guard as guard
    from taskplane.context import digest
    row = budget_fixture()
    op = guard.reserve_response(row, 'native-1', 'claim', digest('claim'))
    assert op['status'] == 'reserved'
    restored = json.loads(json.dumps(row))
    with pytest.raises(w.Refusal, match='unknown output or timing'):
        guard.reserve_response(restored, 'retry-2', 'claim', digest('claim'))
    assert restored['startup_account']['responses_reserved'] == 1
    assert guard.reserve_response(restored, 'native-1', 'claim', digest('claim')) == op
    with pytest.raises(w.Refusal, match='Conflicting'):
        guard.reserve_response(restored, 'native-1', 'claim', digest('other'))
    guard.emitted_response(restored, 'native-1', {'claimed': True})
    with pytest.raises(w.Refusal, match='unknown output or timing'):
        guard.reserve_response(restored, 'retry-2', 'claim', digest('claim'))
    assert restored['startup_account']['measurement'] == 'incomplete'


def test_startup_account_counts_each_emitted_retry_and_enforces_eight_slots():
    from taskplane import workflow_write_guard as guard
    from taskplane.context import digest
    row = budget_fixture()
    for n in range(8):
        measured_response(row, str(n), start=n*100, end=n*100+50)
    value = row['startup_account']
    assert value['responses_reserved'] == value['responses_emitted'] == 8
    assert value['processing_ns'] == 400
    with pytest.raises(w.Refusal, match='eight'):
        guard.reserve_response(row, 'ninth', 'context', digest('ninth'))
    assert value['responses_reserved'] == 8


def test_nested_intervals_count_once_and_conflicting_clock_refuses():
    from taskplane import workflow_write_guard as guard
    spans = [{'clock': 'boot', 'start_ns': start, 'end_ns': end, 'source': 'fixture'}
             for start, end in [(100, 400), (150, 200), (300, 500), (700, 800)]]
    assert guard.interval_total(spans) == 500
    spans[-1]['clock'] = 'other-boot'
    with pytest.raises(w.Refusal, match='different host clocks'):
        guard.interval_total(spans)


def test_native_probe_overrun_is_retained_and_never_ready():
    from taskplane import workflow_write_guard as guard
    from taskplane.context import digest
    row = budget_fixture()
    row['context_receipt'] = {'fixture': True}
    row['startup_account']['stage'] = 'brief_ready'
    measured_response(row, 'first-source', 'source-probe', 0, 60_000_000_001)
    assert row['startup_account']['stage'] == 'exhausted'
    assert row['startup_account']['overshoot_ns'] == 1
    assert row['state'] == 'bootstrapping'
    with pytest.raises(w.Refusal, match='exhausted'):
        guard.reserve_response(row, 'later', 'context', digest('later'))


def test_late_native_completion_cannot_undo_stop_and_requires_actual_output():
    from taskplane import workflow_write_guard as guard
    from taskplane.context import digest
    row = budget_fixture()
    row['context_receipt'] = {'fixture': True}
    row['startup_account']['stage'] = 'brief_ready'
    guard.reserve_response(row, 'probe', 'source-probe', digest('probe'))
    guard.emitted_response(row, 'probe', {'read': 'bytes'})
    row.update(stop_requested={'fixture': True}, control_epoch=1, state='cancel_requested')
    spans = [{'clock': 'boot', 'start_ns': 1, 'end_ns': 10, 'source': 'native/probe'}]
    stable = deepcopy(row)
    with pytest.raises(w.Refusal, match='matching native output'):
        guard.complete_response(row, 'probe', digest('forged'), spans,
            native_reference='native/probe', automatic=True, runtime=row['expected_runtime'])
    assert row == stable
    guard.complete_response(row, 'probe', digest({'read': 'bytes'}), spans,
        native_reference='native/probe', automatic=True, runtime=row['expected_runtime'])
    assert row['startup_account']['stage'] == 'stop_requested'
    assert row['state'] == 'cancel_requested'
    assert row['startup_account']['processing_ns'] == 9


def test_oversized_response_leaves_reserved_uncertainty_not_free_retry():
    from taskplane import workflow_write_guard as guard
    from taskplane.context import digest
    row = budget_fixture()
    guard.reserve_response(row, 'large', 'context', digest('large'))
    with pytest.raises(w.Refusal, match='reserved envelope'):
        guard.emitted_response(row, 'large', {'body': 'a' * 32768})
    assert row['startup_account']['operations']['large']['status'] == 'reserved'
    assert row['startup_account']['responses_reserved'] == 1


def test_exact_packing_accounts_for_metadata_and_matches_actual_drain(tmp_path):
    from taskplane.context_handoff import Session
    from taskplane.tests.test_context_handoff import compact_fixture
    state, _, _ = compact_fixture(tmp_path)
    session = Session(tmp_path, state, 'T1')
    packing = session.preflight()['startup_packing']
    responses = []
    while not responses or not responses[-1]['done']:
        responses.append(session.drain(session.handoff_ref['sha256']))
    assert packing['normal_responses'] == 4 + len(responses)
    assert all(len(encode(r)) + 1 <= bound <= 32768
               for r, bound in zip(responses, packing['body_response_upper_bounds']))
    session.validate(responses[-1]['context_receipt'])


def control_fixture(tmp_path):
    controller, state = setup(tmp_path, count=1)
    prepared = reserve(controller, state); grant = prepared['grant']['grant_id']
    # Fixture-only opt-in; production must use the separately validated upgrade API.
    db = controller._read(controller._path()); current = db['runs'][state['run']]
    current['context_contract'] = 'bounded/v3'
    row = current['workers'][grant]
    from taskplane.context_handoff import binding
    row['binding'] = binding(current)
    row['startup_supporting'] = ['support/history']
    row['control_epoch'] = 0
    controller._write(controller._path(), db)
    request = {'schema': 'taskplane.startup-control/v1', 'request_id': 'stop-one',
               'request_reference': 'fixture/user-request', 'run': state['run'],
               'grant': grant, 'attempt': row['attempt'], 'reason': 'Stop this startup.'}
    return controller, state, grant, request


def test_stop_before_context_keeps_reservation_and_replay_is_idempotent(tmp_path, monkeypatch):
    from taskplane import context_handoff
    c, s, grant, request = control_fixture(tmp_path)
    path = c._path()
    before = c._read(c._path())['runs'][s['run']]
    monkeypatch.setattr(context_handoff, 'Session', lambda *a, **k: pytest.fail('Control loaded context'))
    result = c.worker(s['run'], 'stop', revision=s['revision'], grant=grant, request=request)
    assert len(encode(result)) <= 4096 and result['state'] == 'cancel_requested'
    assert not result['reservation_released'] and not result['result_accepted']
    assert result['process_exit'] == 'unknown'
    assert c.worker(s['run'], 'stop', revision=s['revision'], grant=grant, request=request) == result
    after = c._read(c._path())['runs'][s['run']]
    assert after['workers'][grant]['control_epoch'] == 1
    for key in ('decisions', 'revision', 'scope', 'history', 'task_results'):
        assert after.get(key) == before.get(key)
    stable = path.read_bytes()
    with pytest.raises(w.Refusal, match='Conflicting'):
        c.worker(s['run'], 'stop', revision=s['revision'], grant=grant,
                 request={**request, 'reason': 'Changed replay'})
    assert path.read_bytes() == stable


@pytest.mark.parametrize('defect', ['foreign', 'runtime', 'attempt', 'run', 'grant', 'schema', 'oversized', 'protected'])
def test_control_refuses_bad_identity_binding_or_payload_without_mutation(tmp_path, monkeypatch, defect):
    c, s, grant, request = control_fixture(tmp_path)
    path = c._path()
    if defect == 'foreign': c.principal = 'other-worker'
    elif defect == 'runtime': monkeypatch.setattr(host_capabilities, 'runtime_identity', lambda: {'root': 'foreign'})
    elif defect == 'attempt': request['attempt'] += 1
    elif defect in ('run', 'grant', 'schema'): request[defect] = 'foreign'
    elif defect == 'oversized': request['reason'] = 'x' * 5000
    elif defect == 'protected': c.adapter.profile = 'protected_host'
    stable = path.read_bytes()
    with pytest.raises(w.Refusal):
        c.worker(s['run'], 'stop', revision=s['revision'], grant=grant, request=request)
    assert path.read_bytes() == stable


def test_optional_omission_never_completes_mandatory_coverage(tmp_path):
    c, s, grant, request = control_fixture(tmp_path)
    result = c.worker(s['run'], 'omit-optional', revision=s['revision'], grant=grant,
                      request={**request, 'inputs': ['support/history']})
    assert result['omitted_supporting'] == 1 and result['remaining_required'] == 'unknown'
    row = c._read(c._path())['runs'][s['run']]['workers'][grant]
    assert row['context_receipt'] is None and row['state'] == 'prepared'
    stable = c._path().read_bytes()
    with pytest.raises(w.Refusal, match='mandatory'):
        c.worker(s['run'], 'omit-optional', revision=s['revision'], grant=grant,
                 request={**request, 'request_id': 'omit-required', 'inputs': ['task-brief']})
    assert c._path().read_bytes() == stable


def test_stop_blocks_later_claim_and_context_even_with_prior_receipt(tmp_path, monkeypatch):
    from taskplane import context_handoff
    c, s, grant, request = control_fixture(tmp_path)
    db = c._read(c._path()); row = db['runs'][s['run']]['workers'][grant]
    row.update(worker_id='child', claimed_at='fixture', state='running', context_receipt={'fixture': True})
    c._write(c._path(), db)
    c.worker(s['run'], 'stop', revision=s['revision'], grant=grant, request=request)
    child = workflow_host.Controller(tmp_path, 'root', workflow_host.installed_adapter('codex'), principal='child')
    monkeypatch.setattr(context_handoff, 'Session', lambda *a, **k: pytest.fail('Stopped work loaded context'))
    stable = c._path().read_bytes()
    with pytest.raises(w.Refusal, match='stopped'):
        child.worker(s['run'], 'claim', grant=grant)
    db = c._read(c._path())
    with pytest.raises(w.Refusal, match='stopped'):
        child._context_locked(db, s['run'], task='T0')
    assert c._path().read_bytes() == stable
    status = child.worker(s['run'], 'startup-status', grant=grant,
                          request={**request, 'request_id': 'child-status'})
    assert status['parent'] == 'root' and status['state'] == 'cancel_requested'


def test_public_control_cli_returns_small_response_without_context(tmp_path, monkeypatch, capsys):
    from taskplane import context_handoff
    c, s, grant, request = control_fixture(tmp_path)
    monkeypatch.setattr(context_handoff, 'Session', lambda *a, **k: pytest.fail('Control CLI loaded context'))
    args = ['worker', '--workspace', str(tmp_path), '--run', s['run'], '--operation', 'stop',
            '--grant', grant, '--expected-revision', str(s['revision']), '--worker-json', json.dumps(request)]
    assert flow.main(args, governor=c, compact=True) == 0
    wire = capsys.readouterr().out
    assert len(wire.encode()) <= 4096
    assert json.loads(wire)['state'] == 'cancel_requested'
    args[-1] = json.dumps({**request, 'attempt': 99})
    assert flow.main(args, governor=c, compact=True) == 2
    wire = capsys.readouterr().out
    assert len(wire.encode()) <= 4096 and json.loads(wire)['reason'] == 'invalid_evidence'


def test_controller_probe_is_bounded_read_and_post_hook_does_not_invent_complete_timing(tmp_path, monkeypatch):
    import hashlib
    import shlex
    import sys
    from taskplane import worker_runtime as workers, workflow_write_guard as guard
    c, s, grant, _ = control_fixture(tmp_path)
    (tmp_path/'input.py').write_bytes(b'x' * 20000)
    db = c._read(c._path()); state = db['runs'][s['run']]; row = state['workers'][grant]
    row.update(worker_id='child', claimed_at='fixture', state='bootstrapping',
               context_receipt={'fixture': 'complete'},
               input_manifest={'input.py': hashlib.sha256((tmp_path/'input.py').read_bytes()).hexdigest()})
    row['startup_account'] = guard.new_account(row, {'required_body_bytes': 10,
        'startup_packing': {'normal_responses': 5}})
    row['startup_account']['stage'] = 'brief_ready'
    c._write(c._path(), db)
    child = workflow_host.Controller(tmp_path, 'root', workflow_host.installed_adapter('codex'), principal='child')
    class Delivered:
        def validate(self, receipt):
            assert receipt == {'fixture': 'complete'}
    monkeypatch.setattr(workers, 'worker_session', lambda *a: Delivered())
    command = shlex.join([sys.executable, str(__import__('pathlib').Path(flow.__file__).with_name('tp.py')),
        'flow', 'worker', '--workspace', str(tmp_path), '--run', s['run'],
        '--operation', 'source-probe', '--grant', grant, '--worker-json', json.dumps({'path': 'input.py'})])
    event = {'hook_event_name': 'PreToolUse', 'tool_name': 'exec_command', 'call_id': 'probe-native',
             'tool_input': {'cmd': command}, 'taskplane_automatic_hook': True,
             'taskplane_runtime_identity': host_capabilities.runtime_identity()}
    db = c._read(c._path()); state = db['runs'][s['run']]
    admission = child._admission(db, event, state, 'phase_grant')
    c._write(c._path(), db)
    result = child.worker(s['run'], 'source-probe', grant=grant, request={'path': 'input.py'})
    assert result['returned_bytes'] == 16384 and result['more_source_bytes']
    assert len(encode(result)) + 1 <= 32768 and result['implementation_permitted'] is False
    paired = child.complete_admission({**event, 'hook_event_name': 'PostToolUse',
                                      'tool_response': {'output': encode(result).decode() + '\n'}})
    assert paired['startup_post_observation']['complete_measurement'] is False
    current = c._read(c._path())['runs'][s['run']]['workers'][grant]
    assert current['startup_account']['stage'] == 'source_probe_pending'
    assert current['startup_account']['execution'] == 'incomplete'
    assert workers.readiness(current)['status'] == 'pending'
    with pytest.raises(w.Refusal, match='must complete'):
        child.worker(s['run'], 'source-probe', grant=grant, request={'path': 'input.py'})
    assert (tmp_path/'input.py').read_bytes() == b'x' * 20000
    post = {**event, 'hook_event_name': 'PostToolUse', 'taskplane_admission': paired,
            'tool_response': {'output': encode(result).decode() + '\n'}}
    flow.hook({**post, 'cwd': str(tmp_path), 'session_id': 'child'}, governor=child)
    # A committed end-of-hook record still does not itself claim full timing or
    # admit implementation. The next automatic native event reconciles it.
    assert c._read(c._path())['runs'][s['run']]['workers'][grant]['startup_account']['stage'] == 'source_probe_pending'
    flow.hook({**event, 'call_id': 'next-native', 'cwd': str(tmp_path), 'session_id': 'child',
               'tool_name': 'Read', 'tool_input': {'file_path': str(tmp_path/'input.py')}}, governor=child)
    current = c._read(c._path())['runs'][s['run']]['workers'][grant]
    assert workers.readiness(current)['status'] == 'ready'
    assert current['startup_account']['execution'] == 'complete'
    assert current['startup_account']['contract'] == 'execution_only'
    assert 'processing_ns' not in current['startup_account']
    stable = c._path().read_bytes()
    child.reconcile_startup({**event, 'call_id': 'later-native'})
    assert c._path().read_bytes() == stable
    with pytest.raises(w.Refusal, match='Conflicting native startup output'):
        child.complete_admission({**post, 'tool_response': {'output': '{"forged":true}\n'}})
    assert c._path().read_bytes() == stable


def test_unknown_native_admission_cannot_reserve_startup_or_emit_context(tmp_path, monkeypatch):
    from taskplane import worker_runtime as workers, workflow_write_guard as guard
    c, s, grant, _ = control_fixture(tmp_path)
    db = c._read(c._path()); row = db['runs'][s['run']]['workers'][grant]
    row.update(worker_id='child', state='bootstrapping')
    row['startup_account'] = guard.new_account(row, {'required_body_bytes': 10,
        'startup_packing': {'normal_responses': 5}})
    c._write(c._path(), db)
    child = workflow_host.Controller(tmp_path, 'root', workflow_host.installed_adapter('codex'), principal='child')
    monkeypatch.setattr(workers, 'worker_session', lambda *a: pytest.fail('Unadmitted startup loaded context'))
    stable = c._path().read_bytes()
    with pytest.raises(w.Refusal, match='automatic native command admission'):
        child.worker(s['run'], 'claim', grant=grant)
    assert c._path().read_bytes() == stable


def test_native_result_cannot_be_accepted_before_measured_source_probe(tmp_path):
    c, s, grant, _ = control_fixture(tmp_path)
    db = c._read(c._path()); row = db['runs'][s['run']]['workers'][grant]
    row.update(worker_id='child', claimed_at='fixture', state='result_pending',
               terminal_status='completed', context_receipt={'fixture': 'delivered'})
    c._write(c._path(), db)
    stable = c._path().read_bytes()
    with pytest.raises(w.Refusal, match='complete startup execution'):
        c.worker(s['run'], 'accept-result', revision=s['revision'], task='T0', grant=grant,
                 request={'outputs': ['T0.md'], 'checks': [{'name': 'fixture', 'status': 'pass', 'evidence': 'T0.md'}]})
    assert c._path().read_bytes() == stable


def test_foreign_clock_completion_refuses_without_changing_account():
    from taskplane import workflow_write_guard as guard
    from taskplane.context import digest
    row = budget_fixture()
    measured_response(row, 'first')
    guard.reserve_response(row, 'second', 'context', digest('second'))
    guard.emitted_response(row, 'second', {'second': True})
    stable = deepcopy(row)
    with pytest.raises(w.Refusal, match='different host clocks'):
        guard.complete_response(row, 'second', digest({'second': True}),
            [{'clock': 'different-boot', 'start_ns': 1, 'end_ns': 10, 'source': 'fixture/second'}],
            native_reference='fixture/second', automatic=True, runtime=row['expected_runtime'])
    assert row == stable


def test_startup_refusal_cli_does_not_build_hidden_context(tmp_path, monkeypatch, capsys):
    from taskplane import context_handoff
    c, s, grant, _ = control_fixture(tmp_path)
    child = workflow_host.Controller(tmp_path, 'root', workflow_host.installed_adapter('codex'), principal='unknown')
    monkeypatch.setattr(context_handoff, 'Session', lambda *a, **k: pytest.fail('Failure emitted hidden context'))
    assert flow.main(['worker', '--workspace', str(tmp_path), '--run', s['run'],
                      '--operation', 'claim', '--grant', grant], governor=child, compact=True) == 2
    raw = capsys.readouterr().out
    assert len(raw.encode()) <= 4096
    value = json.loads(raw)
    assert value['schema'] == 'taskplane.startup-failure/v1'
    assert value['permissions']['implementation'] is False
    assert 'context' not in value


def test_context_cli_does_not_reconcile_unrelated_history_or_usage(tmp_path, monkeypatch, capsys):
    c, state = setup(tmp_path, count=1)
    for name in ('read_events', 'counter', 'reconcile_usage'):
        monkeypatch.setattr(flow, name, lambda *a, **k: pytest.fail('Startup replayed history or usage'))
    assert flow.main(['context', '--workspace', str(tmp_path), '--run', state['run'], '--task', 'T0'],
                     governor=c, compact=True) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['schema'] == 'taskplane.context-preparation/v1'


def v2_complete(row, name, *, kind='context', intervals=None, timing=None, execution_change=None):
    from taskplane import workflow_write_guard as guard
    from taskplane.context import digest
    result = {'response': name}
    guard.reserve_response(row, name, kind, digest(name))
    guard.emitted_response(row, name, result)
    value = row['startup_account']; op = value['operations'][name]
    proof = {'schema': 'taskplane.startup-execution/v1', 'operation': name,
        'run': row['run'], 'grant': row['grant_id'], 'attempt': row['attempt'],
        'binding': value['binding'], 'request_digest': op['request_digest'],
        'response_digest': digest(result), 'response_bytes': op['emitted_bytes'],
        'native_reference': 'fixture/' + name, 'runtime': row['expected_runtime'],
        'automatic': True, 'post_hook_committed': True}
    if execution_change: proof.update(execution_change)
    guard.complete_response(row, name, digest(result), intervals or [],
        native_reference='fixture/' + name, automatic=True, runtime=row['expected_runtime'],
        execution=proof, timing=timing)


@pytest.mark.parametrize('intervals', [[], [{'bad': 'clock'}],
    [{'clock': 'fixture', 'start_ns': 0, 'end_ns': 60_000_000_001, 'source': 'partial'}]])
def test_advisory_timing_does_not_block_proven_execution(intervals):
    from taskplane import workflow_write_guard as guard
    row = budget_fixture(legacy=False)
    row['context_receipt'] = {'fixture': True}
    row['startup_account']['stage'] = 'brief_ready'
    v2_complete(row, 'probe', kind='source-probe', intervals=intervals)
    value = row['startup_account']
    assert value['stage'] == 'ready' and value['execution'] == 'complete'
    assert value['measurement'] == 'deferred' and value['processing_ns'] is None
    assert row['state'] == 'running'
    assert guard.startup_status(row)['timing_mode'] == 'advisory'


@pytest.mark.parametrize('change', [{'post_hook_committed': False}, {'grant': 'foreign'},
    {'response_bytes': 0}, {'automatic': False}, {'binding': {}}, {'runtime': {}}])
def test_advisory_mode_never_waives_execution_authority(change):
    row = budget_fixture(legacy=False)
    with pytest.raises(w.Refusal, match='durable automatic hook'):
        v2_complete(row, 'claim', kind='claim', execution_change=change)
    assert row['startup_account']['execution'] == 'incomplete'
    assert row['startup_account']['operations']['claim']['status'] == 'emission_pending'


def qualified_capability(host, runtime):
    from taskplane.context import digest
    return {'schema': 'taskplane.startup-timing-capability/v1', 'status': 'qualified',
        'host': host, 'runtime_sha256': digest(runtime), 'boundary': list(host_capabilities.STARTUP_TIMING_BOUNDARY),
        'coverage': 'complete', 'gaps': [], 'provider': 'fixture-provider', 'clock': 'fixture-boot',
        'native_version': 'fixture-v1', 'qualification_reference': 'fixture/native-qualification',
        'boundary_test_reference': 'fixture/complete-boundary-tests', 'correlation_contract': 'fixture/exact-call'}


@pytest.mark.parametrize('elapsed,stage', [(59_999_999_999, 'ready'), (60_000_000_000, 'ready'),
                                          (60_000_000_001, 'exhausted')])
def test_required_mode_full_boundary_limits(monkeypatch, elapsed, stage):
    from taskplane.context import digest
    monkeypatch.setattr(host_capabilities, 'startup_timing_capability', qualified_capability)
    row = budget_fixture(legacy=False)
    row['context_receipt'] = {'fixture': True}; row['startup_account']['stage'] = 'brief_ready'
    cap = row['startup_account']['timing_policy']['capability']
    timing = {'capability_sha256': digest(cap), 'operation': 'probe', 'native_reference': 'fixture/probe',
              'coverage': 'complete', 'gaps': [], 'boundary': cap['boundary'], 'provider': cap['provider']}
    v2_complete(row, 'probe', kind='source-probe', timing=timing,
                intervals=[{'clock': cap['clock'], 'start_ns': 0, 'end_ns': elapsed, 'source': cap['provider']}])
    assert row['startup_account']['stage'] == stage
    assert row['startup_account']['processing_ns'] == elapsed
    assert (row['state'] == 'running') == (stage == 'ready')


def test_required_attempt_keeps_mode_after_provider_loss(monkeypatch):
    from taskplane import workflow_write_guard as guard
    from taskplane.context import digest
    original = host_capabilities.startup_timing_capability
    monkeypatch.setattr(host_capabilities, 'startup_timing_capability', qualified_capability)
    row = budget_fixture(legacy=False)
    monkeypatch.setattr(host_capabilities, 'startup_timing_capability', original)
    v2_complete(row, 'claim', kind='claim')
    assert row['startup_account']['timing_policy']['mode'] == 'required'
    assert row['startup_account']['execution'] == 'complete'
    assert row['startup_account']['measurement'] == 'incomplete'
    with pytest.raises(w.Refusal): guard.reserve_response(row, 'next', 'context', digest('next'))
    assert budget_fixture(legacy=False)['startup_account']['timing_policy']['mode'] == 'advisory'


@pytest.mark.parametrize('defect', ['missing', 'changed'])
def test_v2_missing_or_changed_mode_refuses_without_default(defect):
    from taskplane import workflow_write_guard as guard
    row = budget_fixture(legacy=False)
    if defect == 'missing': row['startup_account']['timing_policy'].pop('mode')
    else: row['startup_account']['timing_policy']['mode'] = 'required'
    with pytest.raises(w.Refusal, match='timing mode'): guard.account(row)


def test_advisory_crash_reservation_and_late_stop_are_preserved():
    from taskplane import workflow_write_guard as guard
    from taskplane.context import digest
    row = budget_fixture(legacy=False)
    guard.reserve_response(row, 'crash', 'claim', digest('crash'))
    restarted = json.loads(json.dumps(row))
    with pytest.raises(w.Refusal): guard.reserve_response(restarted, 'retry', 'claim', digest('retry'))
    assert restarted['startup_account']['responses_reserved'] == 1
    row = budget_fixture(legacy=False)
    row['context_receipt'] = {'fixture': True}; row['startup_account']['stage'] = 'brief_ready'
    guard.reserve_response(row, 'probe', 'source-probe', digest('probe'))
    guard.emitted_response(row, 'probe', {'response': 'probe'})
    op = row['startup_account']['operations']['probe']
    proof = {'schema': 'taskplane.startup-execution/v1', 'operation': 'probe',
        'run': row['run'], 'grant': row['grant_id'], 'attempt': 1,
        'binding': row['startup_account']['binding'], 'request_digest': op['request_digest'],
        'response_digest': op['response_digest'], 'response_bytes': op['emitted_bytes'],
        'native_reference': 'fixture/probe', 'runtime': row['expected_runtime'],
        'automatic': True, 'post_hook_committed': True}
    row.update(stop_requested={'fixture': True}, control_epoch=1, state='cancel_requested')
    guard.complete_response(row, 'probe', op['response_digest'], [], native_reference='fixture/probe',
        automatic=True, runtime=row['expected_runtime'], execution=proof)
    assert row['startup_account']['stage'] == 'stop_requested' and row['state'] == 'cancel_requested'
    stable = deepcopy(row)
    guard.complete_response(row, 'probe', op['response_digest'], [], native_reference='fixture/probe',
        automatic=True, runtime=row['expected_runtime'], execution=proof)
    assert row == stable


def test_advisory_eight_response_limit_is_still_mandatory():
    from taskplane import workflow_write_guard as guard
    from taskplane.context import digest
    row = budget_fixture(legacy=False)
    for n in range(8): v2_complete(row, str(n))
    with pytest.raises(w.Refusal, match='eight'): guard.reserve_response(row, 'ninth', 'context', digest('ninth'))
    assert row['startup_account']['responses_reserved'] == 8
    assert row['startup_account']['measurement'] == 'deferred'


def test_exact_required_limit_cannot_start_more_work(monkeypatch):
    from taskplane import workflow_write_guard as guard
    from taskplane.context import digest
    monkeypatch.setattr(host_capabilities, 'startup_timing_capability', qualified_capability)
    row = budget_fixture(legacy=False)
    cap = row['startup_account']['timing_policy']['capability']
    timing = {'capability_sha256': digest(cap), 'operation': 'claim', 'native_reference': 'fixture/claim',
              'coverage': 'complete', 'gaps': [], 'boundary': cap['boundary'], 'provider': cap['provider']}
    v2_complete(row, 'claim', kind='claim', timing=timing,
        intervals=[{'clock': cap['clock'], 'start_ns': 0, 'end_ns': 60_000_000_000, 'source': cap['provider']}])
    with pytest.raises(w.Refusal, match='exhausted'): guard.reserve_response(row, 'next', 'context', digest('next'))


def test_advisory_timing_cannot_bypass_missing_brief_or_probe():
    from taskplane import workflow_write_guard as guard
    from taskplane.context import digest
    row = budget_fixture(legacy=False)
    v2_complete(row, 'claim', kind='claim')
    with pytest.raises(w.Refusal, match='complete current mandatory context'):
        guard.reserve_response(row, 'probe', 'source-probe', digest('probe'))
    assert row['startup_account']['stage'] != 'ready'


@pytest.mark.parametrize('failure', ['output', 'commit', 'none'])
def test_production_hook_completion_follows_successful_output(monkeypatch, failure):
    import io
    import sys
    events = []
    class Completion:
        def finish_startup_hook(self, event):
            events.append('commit')
            if failure == 'commit': raise OSError('fixture storage failure')
            events.append('durable')
    class Output(io.StringIO):
        def write(self, value):
            if value.strip():
                events.append('output')
                if failure == 'output': raise OSError('fixture output failure')
            return super().write(value)
        def flush(self): events.append('flush')
    def completed_hook(event, *, governor=None, completion=None):
        completion.update(controller=Completion(), event=event)
        return {'fixture': 'successful mandatory hook body'}
    monkeypatch.setattr(flow, 'hook', completed_hook)
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{"hook_event_name":"PostToolUse"}'))
    monkeypatch.setattr(sys, 'stdout', Output())
    if failure == 'none':
        assert flow.run_hook() == 0
        assert events.index('output') < events.index('flush') < events.index('commit') < events.index('durable')
    else:
        with pytest.raises(OSError): flow.run_hook()
        assert 'durable' not in events
        if failure == 'output': assert 'commit' not in events


def execution_fixture():
    from taskplane import workflow_write_guard as guard
    row = {'run': 'r', 'grant_id': 'g', 'attempt': 1, 'binding': {'run': 'r'},
           'expected_runtime': {'root': 'fixture-runtime'}, 'state': 'bootstrapping', 'control_epoch': 0,
           'startup_account_schema': 'taskplane.startup-account/v3',
           'requirements_epochs': {'AC': 1}, 'task_epoch': 2, 'input_manifest': {'input.py': 'a' * 64}}
    row['startup_account'] = guard.new_account(row, {'required_body_bytes': 300000,
        'startup_packing': {'normal_responses': 20}})
    return row


def test_execution_only_startup_has_no_performance_provider_or_response_quota(monkeypatch):
    from taskplane import workflow_write_guard as guard
    monkeypatch.setattr(host_capabilities, 'startup_timing_capability',
                        lambda *a: pytest.fail('New execution-only startup queried timing'))
    row = execution_fixture()
    for n in range(12):
        v2_complete(row, str(n), intervals=[{'irrelevant': 'not a measurement'}])
    row['context_receipt'] = {'fixture': 'complete'}
    row['startup_account']['stage'] = 'brief_ready'
    v2_complete(row, 'probe', kind='source-probe', intervals=[
        {'clock': 'old', 'start_ns': 0, 'end_ns': 900000000000, 'source': 'irrelevant'}])
    value = row['startup_account']
    assert value['stage'] == 'ready' and value['execution'] == 'complete'
    assert len(value['operations']) == 13 and row['state'] == 'running'
    assert not {'timing_policy', 'processing_ns', 'measurement', 'limits', 'packing',
                'mandatory_unique_bytes', 'responses_reserved'} & value.keys()
    assert guard.startup_status(row) == {'contract': 'execution_only', 'stage': 'ready',
        'execution': 'complete', 'pending_operations': [], 'stop_requested': False}


@pytest.mark.parametrize('change', [{'post_hook_committed': False}, {'grant': 'foreign'},
    {'response_bytes': 0}, {'automatic': False}, {'binding': {}}, {'runtime': {}}])
def test_execution_only_still_requires_exact_native_output_and_durable_hook(change):
    row = execution_fixture()
    with pytest.raises(w.Refusal, match='durable automatic hook'):
        v2_complete(row, 'claim', kind='claim', execution_change=change)
    assert row['startup_account']['execution'] == 'incomplete'
    assert row['startup_account']['operations']['claim']['status'] == 'emission_pending'


@pytest.mark.parametrize('field', ['requirements_epochs', 'task_epoch', 'input_manifest', 'startup_account_schema'])
def test_execution_only_input_or_schema_change_refuses(field):
    from taskplane import workflow_write_guard as guard
    row = execution_fixture()
    row[field] = {} if field in ('requirements_epochs', 'input_manifest') else 99
    with pytest.raises(w.Refusal): guard.account(row)


def test_execution_only_crash_replay_and_stop_never_release_unknown_work():
    from taskplane import workflow_write_guard as guard
    from taskplane.context import digest
    row = execution_fixture()
    op = guard.reserve_response(row, 'claim', 'claim', digest('claim'))
    restored = json.loads(json.dumps(row))
    assert guard.reserve_response(restored, 'claim', 'claim', digest('claim')) == op
    with pytest.raises(w.Refusal): guard.reserve_response(restored, 'other', 'claim', digest('other'))
    with pytest.raises(w.Refusal): guard.reserve_response(restored, 'claim', 'claim', digest('changed'))
    restored.update(stop_requested={'request_id': 'actual-stop'}, control_epoch=1, state='cancel_requested')
    guard.emitted_response(restored, 'claim', {'response': 'claim'})
    value = restored['startup_account']; emitted = value['operations']['claim']
    proof = {'schema': 'taskplane.startup-execution/v1', 'operation': 'claim', 'run': 'r', 'grant': 'g',
             'attempt': 1, 'binding': value['binding'], 'request_digest': emitted['request_digest'],
             'response_digest': emitted['response_digest'], 'response_bytes': emitted['emitted_bytes'],
             'native_reference': 'fixture/claim', 'runtime': restored['expected_runtime'],
             'automatic': True, 'post_hook_committed': True}
    guard.complete_response(restored, 'claim', emitted['response_digest'], [],
        native_reference='fixture/claim', automatic=True, runtime=restored['expected_runtime'], execution=proof)
    assert restored['state'] == 'cancel_requested' and restored['startup_account']['stage'] == 'stop_requested'
    stable = deepcopy(restored)
    guard.complete_response(restored, 'claim', emitted['response_digest'], [],
        native_reference='fixture/claim', automatic=True, runtime=restored['expected_runtime'], execution=proof)
    assert restored == stable
    with pytest.raises(w.Refusal): guard.reserve_response(restored, 'later', 'context', digest('later'))


def test_execution_only_still_requires_context_and_valid_transport_envelope():
    from taskplane import workflow_write_guard as guard
    from taskplane.context import digest
    row = execution_fixture()
    v2_complete(row, 'claim', kind='claim')
    with pytest.raises(w.Refusal, match='complete current mandatory context'):
        guard.reserve_response(row, 'probe', 'source-probe', digest('probe'))
    guard.reserve_response(row, 'large', 'context', digest('large'))
    with pytest.raises(w.Refusal, match='reserved envelope'):
        guard.emitted_response(row, 'large', {'body': 'x' * 32768})
    assert row['startup_account']['operations']['large']['status'] == 'reserved'


@pytest.mark.parametrize('defect', ['empty-ready', 'missing-proof', 'foreign-proof'])
def test_execution_only_rejects_corrupt_ready_records(defect):
    from taskplane import workflow_write_guard as guard
    row = execution_fixture()
    if defect != 'empty-ready':
        v2_complete(row, 'claim', kind='claim')
        op = row['startup_account']['operations']['claim']
        if defect == 'missing-proof': op.pop('execution')
        else: op['execution']['grant'] = 'foreign'
    row['startup_account'].update(stage='ready', execution='complete')
    with pytest.raises(w.Refusal): guard.account(row)


def test_execution_only_native_launch_requires_completed_prepare_hook(tmp_path):
    from taskplane import workflow_write_guard as guard
    from taskplane.tests.test_worker_runtime import launch, parent_pair
    c, s, grant, _ = control_fixture(tmp_path)
    db = c._read(c._path()); state = db['runs'][s['run']]; row = state['workers'][grant]
    row['startup_account_schema'] = 'taskplane.startup-account/v3'
    row['startup_account'] = guard.new_account(row, {'required_body_bytes': 200000})
    prepared = {'grant': deepcopy(row), 'message': __import__(
        'taskplane.worker_runtime', fromlist=['dispatch_message']).dispatch_message(state, row)}
    c._write(c._path(), db)
    parent_pair(c)
    with pytest.raises(w.Refusal, match='completed preparation'):
        launch(c, s, prepared)
    db = c._read(c._path()); row = db['runs'][s['run']]['workers'][grant]
    v2_complete(row, 'prepare-fixture', kind='prepare')
    c._write(c._path(), db)
    launch(c, s, prepared)
    assert c._read(c._path())['runs'][s['run']]['workers'][grant]['worker_id'] == 'native-0'
