"""One exact legacy history-limit startup failure, never general conflict erasure."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import shlex
import os
from pathlib import Path
from typing import Any, Callable, Literal, TypedDict, TypeVar

from . import claude_worker_observations as native, workflow as w, workflow_evidence as evidence
from . import worker_runtime as workers
from .context import Store, digest
from .context_handoff import binding

LEGACY_CONFLICT = 'Native transcript source or cursor is unsafe, malformed or changed'
MAX_PASSES = 8
PEER_CONFLICT = 'Conflicting native peer_handback records'
CLAIM_DENIAL_SHA256 = '73ce20b67d4fc493791d1a2da830a8f530d3ade062d185acf1ff2f84f1f63496'

STARTUP_CONTROL_SCHEMA = 'taskplane.startup-control/v1'
MAX_STARTUP_CONTROL_BYTES = 4096
_ValidationResult = TypeVar('_ValidationResult')


class StartupControlRequest(TypedDict):
    """Closed diagnostic request; identity is checked against native observations."""
    schema: Literal['taskplane.startup-control/v1']
    request_id: str
    operation: Literal['startup-status', 'startup-failure', 'stop']
    run: str
    grant: str
    attempt: int
    code: Literal['status_requested', 'context_incomplete', 'startup_exhausted',
                  'source_probe_failed', 'unsupported_capability', 'stop_requested']


_STARTUP_CODES = {
    'startup-status': frozenset({'status_requested'}),
    'startup-failure': frozenset({'context_incomplete', 'startup_exhausted',
                                'source_probe_failed', 'unsupported_capability'}),
    'stop': frozenset({'stop_requested'}),
}
_STARTUP_STATES = frozenset({'draft', 'prepared', 'claimed', 'delivering', 'brief_ready',
                           'source_probe_pending', 'ready', 'stop_requested', 'exhausted', 'failed'})


def startup_control_report(state: dict[str, Any], grant: str, request: StartupControlRequest, *,
                           observed_event: dict[str, Any]) -> dict[str, Any]:
    """Return a <=4 KiB diagnostic for this observed Claude child's original parent.

    workflow_host calls this under its controller lock, BEFORE receipt validation,
    with an adapter-authenticated automatic event, never event JSON from the CLI.
    The adapter advertises ``taskplane_startup_control_schema`` only when its
    loaded runtime implements this contract. This helper checks that event against
    the current frozen grant and exact launch proof; a receipt is not identity.

    This is a pure projection, not admission or a state transition. In particular,
    ``stop`` returns intent, not evidence that a fence or cancellation occurred.
    The controller owner must deduplicate request_id/payload, persist the stop
    fence/control epoch under the reservation lock, and use its native transport
    to deliver only to the returned parent. Ack/stop/completion correlation and
    genuine quiescence remain necessary before releasing ownership. Preserve
    late replies without restoring ready or accepting a result. Root inspection,
    prelaunch root stop, optional omission and Codex transport belong to the
    shared controller; none can use this child-only helper as authority.

    Cancelled/stopped attempts may still report while their binding and native
    identity remain current. No source reads, context reconstruction, publication,
    or mutation occurs here. Unknown/stale identity must use root diagnostics.
    """
    def inspect() -> dict[str, Any]:
        w.require(isinstance(request, dict) and set(request) ==
                  {'schema', 'request_id', 'operation', 'run', 'grant', 'attempt', 'code'},
                  'invalid_evidence', 'Startup control requires the closed diagnostic request.')
        operation = request['operation']
        w.require(request['schema'] == STARTUP_CONTROL_SCHEMA
                  and isinstance(operation, str) and operation in _STARTUP_CODES
                  and isinstance(request['code'], str) and request['code'] in _STARTUP_CODES[operation]
                  and isinstance(request['request_id'], str)
                  and native._ID.fullmatch(request['request_id']) is not None
                  and type(request['attempt']) is int and request['attempt'] > 0,
                  'invalid_evidence', 'Unsupported startup operation, diagnostic or request identity.')
        w.require(state.get('context_contract') == 'bounded/v3'
                  and observed_event.get('taskplane_startup_control_schema') == STARTUP_CONTROL_SCHEMA,
                  'unsupported_authority', 'The loaded adapter must support bounded/v3 startup controls.')
        row = state['workers'][grant]
        w.require(request['run'] == row['run'] == state['run']
                  and request['grant'] == row['grant_id'] == grant
                  and request['attempt'] == row['attempt']
                  and row['root'] == state['root'] and row['workspace'] == state['workspace']
                  and row['binding'] == binding(state)
                  and row.get('workspace_contract') == state.get('workspace_contract')
                  and row['task_digest'] == digest(workers.task(state, row['task_id'])),
                  'stale_checkpoint', 'Startup control belongs to another current grant or binding.')
        observed = observed_event.get('taskplane_observed_binding', {})
        proof = row.get('identity_observation', {})
        child = row.get('worker_id')
        call = observed_event.get('tool_use_id') or observed_event.get('call_id')
        exact = {'host': 'claude', 'parent': state['root'], 'workspace': state['workspace'],
                 'worker_id': child, 'grant_id': grant, 'attempt': row['attempt'],
                 'call_id': row.get('call_id'), 'dispatch_digest': row.get('dispatch_digest')}
        w.require(row.get('host') == 'claude' and isinstance(child, str)
                  and native._ID.fullmatch(child) is not None and child != state['root']
                  and not row.get('identity_conflict') and not row.get('completion_conflict')
                  and proof.get('status') == 'matched'
                  and row.get('identity_freshness', {}).get('status') == 'matched'
                  and all(proof.get(k) == v for k, v in exact.items())
                  and isinstance(row.get('call_id'), str) and bool(row['call_id'])
                  and isinstance(proof.get('evidence_sha256'), str)
                  and native._SHA.fullmatch(proof['evidence_sha256']) is not None
                  and isinstance(row.get('dispatch_digest'), str)
                  and native._SHA.fullmatch(row['dispatch_digest']) is not None
                  and observed_event.get('taskplane_automatic_hook') is True
                  and observed.get('root') == state['root'] and observed.get('principal') == child
                  and bool(row.get('expected_runtime'))
                  and observed_event.get('taskplane_runtime_identity') == row['expected_runtime']
                  and isinstance(call, str) and 0 < len(call) <= 512,
                  'unsupported_authority', 'Startup reporting needs exact current native child, parent and runtime proof.')
        # A reused session does not select an attempt. No other current grant
        # may bind this child, including an unknown or stopping attempt.
        w.require(not any(key != grant and other.get('worker_id') == child
                          and other.get('binding') == row['binding']
                          and other.get('state') not in {'accepted', 'failed', 'interrupted', 'abandoned'}
                          for key, other in state['workers'].items()),
                  'scope_violation', 'Native child is ambiguous across current attempts.')
        account = row.get('startup_account', {})
        startup_state = account.get('state', 'unknown')
        w.require(startup_state == 'unknown' or startup_state in _STARTUP_STATES,
                  'invalid_evidence', 'Startup account has an unsupported state.')
        result = {
            'schema': 'taskplane.startup-control-report/v1',
            'request_id': request['request_id'], 'operation': operation, 'code': request['code'],
            'run': state['run'], 'grant': grant, 'attempt': row['attempt'], 'task': row['task_id'],
            'parent': state['root'], 'worker': child, 'call_id': call,
            'binding_sha256': digest(row['binding']), 'runtime_sha256': digest(row['expected_runtime']),
            'request_sha256': digest(request), 'startup_state': startup_state,
            'context_receipt_status': 'present_unvalidated' if row.get('context_receipt') else 'missing',
            'remaining_checks': ['current_mandatory_context', 'source_probe_accounting',
                                 'native_terminal_or_quiescence'],
            'stop_intent': operation == 'stop', 'stop_committed': False,
            'host_cancellation': 'not_observed', 'reservation_released': False,
            'authority': 'diagnostic_only', 'implementation_allowed': False, 'result_accepted': False,
        }
        wire = json.dumps(result, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False, allow_nan=False).encode('utf-8')
        w.require(len(wire) <= MAX_STARTUP_CONTROL_BYTES, 'context_overflow',
                  'Startup control response exceeds its separate 4 KiB limit.')
        return result
    return _bounded_validation(inspect)


def _file_stamp(info: os.stat_result) -> dict[str, int]:
    return {**native._identity(info), 'size': info.st_size,
            'mtime_ns': info.st_mtime_ns, 'ctime_ns': info.st_ctime_ns}


def _pairs(records: list[Any], state: dict[str, Any], row: dict[str, Any], *, child: bool) -> list[Any]:
    """Join native calls/results; messages and self-reported task success grant nothing."""
    calls: dict[str, Any] = {}
    results: dict[str, Any] = {}
    for record, reference in records:
        message = record.get('message', {})
        content = message.get('content') if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict) or item.get('type') not in {'tool_use', 'tool_result'}:
                continue
            role = item['type']
            key = item.get('id') if role == 'tool_use' else item.get('tool_use_id')
            if not child and role == 'tool_use':
                args = item.get('input', {})
                if not isinstance(args, dict) or not any(args.get(k) == row['worker_id'] for k in
                        ('to', 'recipient', 'task_id', 'resume', 'agent_id')):
                    continue
            if not child and role == 'tool_result' and key not in calls:
                continue
            w.require(isinstance(key, str) and key not in (calls if role == 'tool_use' else results)
                      and record.get('sessionId') == state['root'] and record.get('cwd') == state['workspace']
                      and record.get('isSidechain', False) is child
                      and (not child or record.get('agentId') == row['worker_id'])
                      and record.get('type') == ('assistant' if role == 'tool_use' else 'user')
                      and message.get('role') == ('assistant' if role == 'tool_use' else 'user')
                      and isinstance(record.get('uuid'), str) and native._UUID.fullmatch(record['uuid']),
                      'invalid_evidence', 'Startup transcript has ambiguous native action identity.')
            assert isinstance(key, str)
            (calls if role == 'tool_use' else results)[key] = (record, item, reference)
    w.require(set(calls) == set(results) and 0 < len(calls) <= 16, 'invalid_evidence',
              'Every startup action needs one bounded, native result.')
    pairs = []
    for key, call in calls.items():
        result = results[key]
        w.require(result[0].get('parentUuid') == result[0].get('sourceToolAssistantUUID') == call[0]['uuid']
                  and call[2]['offset'] < result[2]['offset']
                  and native._stamp(call[0]['timestamp']) <= native._stamp(result[0]['timestamp'])
                  <= native._stamp(workers.now()), 'invalid_evidence', 'Startup action result has different lineage or chronology.')
        pairs.append((call, result))
    return pairs


def _structured(pair: Any) -> Any:
    result, item, _ = pair[1]
    content = item.get('content')
    w.require(isinstance(content, list) and len(content) == 1 and content[0].get('type') == 'text',
              'invalid_evidence', 'Native startup result has an unsupported body.')
    parsed = json.loads(content[0]['text'])
    w.require(parsed == result.get('toolUseResult'), 'invalid_evidence', 'Native startup result body disagrees with its structured result.')
    return parsed


def _frozen_owned_unchanged(workspace: Path, state: dict[str, Any], row: dict[str, Any],
                            frozen: dict[str, Any]) -> bool:
    from .context_handoff import Session, text_body
    sources = {item['body']['path']: item['body']
               for item in Session(workspace, state, row['task_id'], snapshot=frozen, persist=False).items
               if item.get('kind') == 'source' and item.get('body', {}).get('path') in row['paths']}
    for relative in row['paths']:
        target = evidence.path(workspace, relative)
        if relative not in sources:
            if target.exists():
                return False  # No frozen bytes means an existing file is unproven.
        elif not target.exists() or sources[relative] != {'path': relative, **text_body(evidence.read(workspace, relative))}:
            return False
    return True


def _resumed_proof(workspace: Path, state: dict[str, Any], admissions: dict[str, Any],
                   row: dict[str, Any]) -> dict[str, Any]:
    """Read-only proof for the observed two-handback, denied-claim resume sequence."""
    from . import claude_worker_invocation as invocation
    w.require(row.get('host') == 'claude' and row.get('state') == 'unknown' and row.get('worker_id')
              and row.get('identity_conflict') is True
              and not any(row.get(k) for k in ('claimed_at', 'context_receipt', 'context_delivery', 'handback',
                                              'terminal_status', 'revoked_at', 'completion_conflict'))
              and not any(r.get('grant') == row['grant_id'] for r in state.get('task_results', {}).values())
              and not any(r.get('state') == 'running' for r in state.get('observed_handles', {}).values())
              and not any(r.get('state') in workers.LIVE for g, r in state['workers'].items() if g != row['grant_id']),
              'scope_violation', 'Resumed-startup recovery requires one unclaimed failed startup and no live work.')
    workers.current(state, row)
    history = row.get('reconciliation_events', {})
    w.require(history and len(history) < 128 and not row.get('reconciliation_overflow')
              and all(event.get('kind') in {'identity_observation', 'redirect_join_rejected'}
                      and (event['kind'] != 'identity_observation' or event.get('value', {}).get('status')
                           in {'matched', 'not_yet_available'} or event.get('value', {}).get('reason') == PEER_CONFLICT)
                      for event in history.values()), 'invalid_evidence', 'An unrelated startup conflict cannot be reconciled.')
    source, cursor = row.get('transcript_source'), deepcopy(state.get('claude_transcript_cursor'))
    w.require(isinstance(source, dict) and isinstance(cursor, dict)
              and source == state.get('claude_transcript_source')
              and native._valid_seal(source, 'taskplane.claude-transcript-source/v1')
              and native._valid_seal(cursor, 'taskplane.claude-transcript-cursor/v1')
              and cursor['source_sha256'] == source['sha256'] and not cursor.get('conflict'),
              'invalid_evidence', 'Original resumed-startup source or cursor is unavailable.')
    assert isinstance(source, dict) and isinstance(cursor, dict)
    native._check_cursor(cursor)
    entry = cursor['entries'].get(digest(native._binding(state['root'], row)))
    w.require(entry and entry.get('conflict') == PEER_CONFLICT
              and all(len(entry[k]) == n for k, n in
                      {'call': 1, 'result': 1, 'header': 1, 'peer_handback': 2, 'notification': 1}.items()),
              'invalid_evidence', 'Recovery requires the exact two-handback conflict with one original launch.')
    path = native._source_path(state['root'], source['path'])
    child_path = native._child_path(path, state['root'], row['worker_id'])
    fd, project = native._open_source(path)
    records: list[Any] = []
    child_records: list[Any] = []
    budget = native.ReadBudget()
    with os.fdopen(fd, 'rb', buffering=0) as stream:
        info = native._regular(stream.fileno())
        w.require(native._identity(info) == source['identity'] and project == source['project_identity']
                  and info.st_size >= cursor['size'] and source['parent'] == state['root'],
                  'invalid_evidence', 'Original startup transcript identity changed.')
        w.require(native._verify(stream.fileno(), source['session_reference'], budget) is not None
                  and native._validate_entry(stream.fileno(), path, state['root'], row, entry, budget, project),
                  'invalid_evidence', 'Retained startup references are unavailable or changed.')
        start = row.get('transcript_admission_offset')
        w.require(type(start) is int and 0 <= start <= entry['call'][0]['reference']['offset'],
                  'invalid_evidence', 'Original startup scan boundary is missing.')
        assert isinstance(start, int)
        end = native._scan(stream.fileno(), path, start, info.st_size, budget,
                           lambda item, ref: records.append((item, ref)))
        w.require(end == info.st_size, 'invalid_evidence', 'Startup parent tail is incomplete or exceeds its read budget.')
        with os.fdopen(native._open_child(child_path, project), 'rb', buffering=0) as child_stream:
            child_info = native._regular(child_stream.fileno())
            end = native._scan(child_stream.fileno(), child_path, 0, child_info.st_size, budget,
                               lambda item, ref: child_records.append((item, ref)))
            w.require(end == child_info.st_size and _file_stamp(child_info) == _file_stamp(os.fstat(child_stream.fileno()))
                      and _file_stamp(info) == _file_stamp(os.fstat(stream.fileno())),
                      'invalid_evidence', 'Startup transcript changed or child history is incomplete.')
    launch_entry = {**entry, 'conflict': None}
    launch = native._correlate(state['root'], row, launch_entry)
    w.require(launch['status'] == 'matched' and launch['evidence_sha256'] == row.get('identity_observation', {}).get('evidence_sha256'),
              'invalid_evidence', 'Original startup launch identity no longer agrees.')
    # The retained cursor stores at most two conflicting candidates. A third
    # report must not disappear behind that bound or authorize stale recovery.
    fresh_peers = [peer for record, _ in records if (peer := native._peer_handback(record)) is not None
                   and peer['worker_id'] == row['worker_id']]
    fresh_notes = [note | {'reference': ref} for record, ref in records if (note := native._notification(record)) is not None
                   and note.get('worker_id') == row['worker_id']]
    w.require(len(fresh_peers) == 2 and {p['record_sha256'] for p in fresh_peers}
              == {p['projection']['record_sha256'] for p in entry['peer_handback']}
              and len(fresh_notes) == 2 and fresh_notes[0]['record_sha256'] == entry['notification'][0]['projection']['record_sha256'],
              'invalid_evidence', 'Later or competing native startup deliveries require reconciliation.')
    owned = [a for a in admissions.values() if a.get('run') == state['run']
             and (a.get('principal') == row['worker_id'] or a.get('call_id') == row['call_id'])]
    by_call = {a['call_id']: a for a in owned}
    w.require(len(owned) == len(by_call) and all(a.get('state') in {'completed', 'expired'}
        and all(a.get(k) == v for k, v in {'automatic': True, 'host': 'claude', 'root': state['root'],
            'workspace': str(workspace), 'binding': row['binding'], 'runtime': row['expected_runtime']}.items())
        for a in owned), 'invalid_evidence', 'Startup admissions are ambiguous, open or differently bound.')
    launch_admission = by_call.get(row['call_id'], {})
    w.require(launch_admission.get('tool') == 'Agent' and launch_admission.get('state') == 'completed'
              and launch_admission.get('principal') == state['root']
              and launch_admission.get('input_digest') == row['dispatch_digest'],
              'invalid_evidence', 'Original launch admission is missing.')
    reports = row.get('startup_handbacks', {})
    w.require(len(reports) == 2 and all(r.get('status') == 'acknowledged' for r in reports.values()),
              'invalid_evidence', 'Both startup reports need their original acknowledged admissions.')
    cycle_observation = row.get('delivery_cycle_observation', {})
    cycles = cycle_observation.get('cycles', [])
    events = [event for cycle in cycles for event in cycle.get('events', {}).values()]
    declarations = [{k: v for k, v in cycle.items() if k not in
                     {'events', 'selected_report', 'observation_sha256'}} for cycle in cycles]
    joined = native.correlate_delivery_cycles(state['root'], row, declarations, events)
    w.require(joined['status'] == 'matched' and len(joined['cycles']) == 2,
              'invalid_evidence', 'B9 requires two explicitly linked complete native delivery cycles.')
    native.verify_delivery_spans(state['root'], row, source, declarations, events)
    ordered_cycles = joined['cycles']
    peers = []
    stops = []
    completed_notes = []
    all_stops = [{**event, 'event_id': key} for key, event in state.get('unbound_worker_events', {}).items()
                 if event.get('worker_id') == row['worker_id'] and event.get('event') == 'SubagentStop']
    w.require(len(all_stops) == 2, 'invalid_evidence', 'Both exact startup stops must remain available.')
    for cycle in ordered_cycles:
        selected = cycle['selected_report']
        selected_peers = [peer | {'reference': item['reference']} for item in entry['peer_handback']
                          for peer in [item['projection']]
                          if peer['body_digest'] == selected['envelope']['body_digest']
                          and peer['input_digest'] == selected['envelope']['input_digest']]
        selected_stops = [stop for stop in all_stops
                          if stop.get('delivery_call_id') == cycle['delivery_call_id']
                          and stop.get('event_id') == cycle['events']['stop_observed']['event_id']]
        notes = [note for note in fresh_notes if note['call_id'] == cycle['delivery_call_id']]
        w.require(len(selected_peers) == len(selected_stops) == len(notes) == 1
                  and selected_stops[0].get('parent') == state['root']
                  and selected_stops[0].get('status') == 'stopped'
                  and selected_stops[0].get('transcript_path') == str(path)
                  and selected_stops[0].get('agent_transcript_path') == str(child_path)
                  and selected['envelope']['call_id'] in reports
                  and selected == reports[selected['envelope']['call_id']],
                  'invalid_evidence', 'Startup cycle report, stop or completion has ambiguous identity.')
        peers.extend(selected_peers); stops.extend(selected_stops); completed_notes.extend(notes)
    completed = completed_notes[0]
    child_pairs = _pairs(child_records, state, row, child=True)
    w.require(all(native._stamp(a[1][0]['timestamp']) <= native._stamp(b[0][0]['timestamp'])
                  for a, b in zip(child_pairs, child_pairs[1:])),
              'invalid_evidence', 'Startup child actions overlap or regress in time.')
    handbacks: list[Any] = []
    denied: list[Any] = []
    refs = [x[2] for pair in child_pairs for x in pair]
    for call, result in child_pairs:
        action, answer = call[1], result[1]
        name, args, call_id = action.get('name'), action.get('input'), action['id']
        w.require(call[0].get('version') == result[0].get('version') == '2.1.296'
                  and isinstance(args, dict), 'invalid_evidence', 'Unsupported historical startup action format.')
        error = 'PreToolUse:' + str(name) + ' hook error: Claude child actor needs exact structured native launch proof.'
        if name == 'Bash':
            command = args.get('command')
            words = shlex.split(command) if isinstance(command, str) else []
            expected_tail = ['flow', 'worker', '--operation', 'claim', '--run', row['run'],
                             '--grant', row['grant_id'], '--workspace', str(workspace)]
            w.require(set(args) <= {'command', 'description'} and len(words) == 12
                      and words[1] == str(Path(row['expected_runtime']['root']) / 'taskplane/tp.py')
                      and words[2:] == expected_tail, 'invalid_evidence', 'A startup action exceeds the exact claim command.')
        elif name != 'SubagentHandback' or set(args) != {'message'} or not isinstance(args['message'], str):
            raise w.Refusal('invalid_evidence', 'An additional child action needs independent side-effect reconciliation.')
        if answer.get('is_error') is True and answer.get('content') == error and result[0].get('toolUseResult') == 'Error: ' + error:
            w.require(call_id not in by_call, 'invalid_evidence', 'A hook-denied startup action has an unexplained admission.')
            continue
        admission = by_call.get(call_id, {})
        w.require(admission.get('tool') == name and admission.get('principal') == row['worker_id']
                  and admission.get('input_digest') == digest(args)
                  and native._stamp(call[0]['timestamp']) <= native._stamp(admission['admitted_at'])
                  <= native._stamp(result[0]['timestamp']), 'invalid_evidence', 'Startup call admission differs from its native input.')
        if name == 'Bash':
            body = answer.get('content')
            w.require(answer.get('is_error') is True and isinstance(body, str)
                      and hashlib.sha256(body.encode()).hexdigest() == CLAIM_DENIAL_SHA256
                      and result[0].get('toolUseResult') == 'Error: ' + body
                      and admission.get('state') == 'expired', 'invalid_evidence', 'Claim lacks exact native permission-denial evidence.')
            inv = admission.get('invocation')
            invocation.validate_record(inv)
            w.require(inv['state'] == 'pending' and inv['consumed'] is None
                      and inv['argv'] == words and inv['original_input_sha256'] == digest(args)
                      and inv['binding']['kind'] == 'worker-claim' and inv['binding']['claimed'] is False
                      and all(inv['binding'].get(k) == v for k, v in {'root': state['root'], 'actor': row['worker_id'],
                          'grant_id': row['grant_id'], 'attempt': row['attempt'], 'call_id': call_id,
                          'launch_call_id': row['call_id'], 'binding_sha256': digest(row['binding'])}.items()),
                      'invalid_evidence', 'Denied claim invocation was consumed or differently bound.')
            denied.append((call, result))
        else:
            report = reports.get(call_id, {})
            ack = {'success': True, 'message': 'Report delivered to your caller.'}
            w.require(not answer.get('is_error') and _structured((call, result)) == ack
                      and admission.get('state') == 'completed'
                      and report.get('envelope') == {'parent': state['root'], 'worker': row['worker_id'],
                          'grant': row['grant_id'], 'attempt': row['attempt'], 'task': row['task_id'],
                          'call_id': call_id, 'binding': row['binding'], 'input_digest': digest(args),
                          'body_digest': digest(args['message'])}
                      and report.get('runtime') == row['expected_runtime']
                      and report.get('acknowledgment') == {'schema': 'claude.SubagentHandback.ack/v1',
                          'envelope_digest': digest(report['envelope']), 'runtime': row['expected_runtime'],
                          'response_digest': digest(ack), 'observed_at': report.get('observed_at')}
                      and report.get('response_digest') == digest(ack)
                      and native._stamp(call[0]['timestamp']) <= native._stamp(report['admitted_at'])
                      <= native._stamp(report['observed_at']) <= native._stamp(result[0]['timestamp']),
                      'invalid_evidence', 'Startup handback lacks exact admission and native acknowledgment.')
            handbacks.append((call, result, report))
    w.require(len(denied) == 1 and len(handbacks) == 2
              and set(by_call) == {row['call_id'], denied[0][0][1]['id'], *reports},
              'invalid_evidence', 'Unexpected or unresolved startup admissions remain.')
    handbacks = sorted(handbacks, key=lambda item: next(i for i, cycle in enumerate(ordered_cycles)
                        if cycle['selected_report']['envelope']['call_id'] == item[0][1]['id']))
    w.require(all(peer['worker_id'] == row['worker_id'] and peer['host_version'] == '2.1.296'
                  and peer['body_digest'] == handbacks[i][2]['envelope']['body_digest']
                  and peer['input_digest'] == handbacks[i][2]['envelope']['input_digest']
                  and native._stamp(handbacks[i][2]['admitted_at']) <= native._stamp(peer['timestamp'])
                  <= native._stamp(handbacks[i][1][0]['timestamp'])
                  for i, peer in enumerate(peers))
              and stops[1].get('textless') is True and stops[1].get('runtime') == row['expected_runtime']
              and stops[1].get('agent_type') == peers[1]['agent_type'],
              'invalid_evidence', 'Startup deliveries or final automatic stop disagree.')
    root_pairs = _pairs(records, state, row, child=False)
    w.require(len(root_pairs) == 1 and root_pairs[0][0][1].get('name') == 'SendMessage',
              'invalid_evidence', 'B9 requires one continuation and no other worker control.')
    resume = root_pairs[0]
    args = resume[0][1]['input']
    control = state.get('worker_controls', {}).get(resume[0][1]['id'], {})
    resumed = _structured(resume)
    w.require(workers._claude_control_target('SendMessage', args) == row['worker_id']
              and completed['call_id'] == row['call_id'] and completed.get('handback_redirect') is True
              and completed_notes[1]['call_id'] == resume[0][1]['id']
              and completed_notes[1].get('handback_redirect') is True
              and all(control.get(k) == v for k, v in
                  {'tool': 'SendMessage', 'grant': row['grant_id'], 'attempt': row['attempt'],
                   'binding': row['binding'], 'input_digest': digest(args)}.items())
              and not resume[1][1].get('is_error') and isinstance(resumed, dict)
              and resumed.get('success') is True and resumed.get('resumedAgentId') == row['worker_id'],
              'invalid_evidence', 'Native continuation is not exact.')
    times = [completed['timestamp'], resume[0][0]['timestamp'], resume[1][0]['timestamp'],
             denied[0][0][0]['timestamp'], denied[0][1][0]['timestamp'], handbacks[1][0][0]['timestamp'],
             handbacks[1][1][0]['timestamp'], completed_notes[1]['timestamp']]
    w.require(all(native._stamp(a) <= native._stamp(b) for a, b in zip(times, times[1:]))
              and child_pairs[-1][0][1]['id'] == handbacks[1][0][1]['id'],
              'invalid_evidence', 'Resumed startup chronology or final child action is ambiguous.')
    w.require(all(native._stamp(event['observed_at']) < native._stamp(stops[0]['observed_at'])
                  for event in state.get('unbound_worker_events', {}).values()
                  if event.get('worker_id') == row['worker_id'] and event.get('event') == 'SubagentStart'),
              'invalid_evidence', 'A later native start invalidates the startup terminal proof.')
    definition = workers.task(state, row['task_id'])
    frozen = Store(workspace).resolve(row['snapshot'])
    w.require(frozen['binding'] == row['binding'] and frozen['task'] == row['task_id']
              and row['task_digest'] == digest(definition) and row['paths'] == definition['paths']
              and row.get('workspace_contract') == state.get('workspace_contract')
              and _frozen_owned_unchanged(workspace, state, row, frozen)
              and row['input_manifest'] == evidence.manifest(workspace, list(row['input_manifest']))
              and row['dependency_results'] == workers.dependency_results(state, definition['dependencies'])
              and evidence.changed(workspace, state) is None,
              'stale_checkpoint', 'Prepared source, owned files, dependencies or approved evidence changed.')
    return {'schema': 'taskplane.claude-resumed-startup-proof/v1', 'launch': launch,
            'records': entry, 'resume_notification': completed_notes[1],
            'native_references': refs + [x[2] for pair in root_pairs for x in pair],
            'stops': stops, 'source': source, 'parent_stamp': _file_stamp(info), 'child_stamp': _file_stamp(child_info),
            'owned_manifest': evidence.manifest(workspace, row['paths'], allow_missing=True), 'snapshot': row['snapshot'],
            'denial': {'call_id': denied[0][0][1]['id'], 'code': 'Auto-Mode Bypass',
                       'status': 'native_permission_required', 'authority': 'none'}, 'read_budget': budget.report()}




def _history_limit(source: dict[str, Any], cursor: dict[str, Any]) -> dict[str, Any]:
    """Reproduce the old first-record bound on the pinned original source."""
    path = native._source_path(source['parent'], source['path'])
    fd, project = native._open_source(path)
    budget = native.ReadBudget()
    with os.fdopen(fd, 'rb', buffering=0) as stream:
        info = native._regular(stream.fileno())
        w.require(native._identity(info) == source['identity'] and project == source['project_identity']
                  and info.st_size >= cursor['size'], 'invalid_evidence', 'Original native history source changed.')
        session = native._verify(stream.fileno(), source['session_reference'], budget)
        w.require(session and session.get('sessionId') == source['parent'] and session.get('isSidechain') is not True,
                  'invalid_evidence', 'Original session reference changed.')
        raw = budget.read(stream.fileno(), 0, min(cursor['watermark'], budget.remaining))
        offset = 0
        for line in raw.splitlines(keepends=True):
            if len(line) > native.MAX_LINE_BYTES:
                w.require(native._historical_text(line, source['parent']), 'invalid_evidence',
                          'The original bound is not a supported unrelated human-text record.')
                return native._reference(path, info, offset, line)
            w.require(line.endswith(b'\n'), 'invalid_evidence', 'Original failure prefix is incomplete.')
            native._decode(line)
            offset += len(line)
    raise w.Refusal('invalid_evidence', 'The original bounded scan does not reproduce the legacy history limit.')


def _reconcile_legacy(workspace: Path, state: dict[str, Any], admissions: dict[str, Any], grant: str,
              request: dict[str, Any], *, store: Store) -> tuple[dict[str, Any], dict[str, Any]]:
    """Caller owns the root/store lock and exact run/revision/evidence checks.

    No supplied proof, identity override or file path is accepted. At most eight
    independent 4 MiB observation passes precede one commit-ready transition.
    Immutable before/proof objects may be retained after a refused commit.
    """
    row = state.get('workers', {}).get(grant)
    w.require(not request and row and row.get('host') == 'claude', 'invalid_evidence',
              'Select the exact Claude startup grant; supplied proofs and overrides are unsupported.')
    assert isinstance(row, dict)
    workers.current(state, row)
    if row.get('startup_recovery'):
        # Read-only idempotent replay: never reopen a previously reconciled grant.
        w.require(row['state'] == 'failed' and row.get('handback', {}).get('status') == 'no_report'
                  and not row.get('identity_conflict'), 'invalid_evidence', 'Recovered startup state changed.')
        return state, {'status': 'reconciled', 'grant': grant, 'task': row['task_id'],
                       'state': 'failed', 'result_accepted': False, 'retry_required': True}
    w.require(row['state'] == 'unknown' and row.get('identity_conflict') is True
              and not any(row.get(key) for key in ('worker_id', 'claimed_at', 'context_receipt', 'identity_observation',
                  'terminal_status', 'revoked_at', 'handback', 'launch_proof_ref', 'completion_conflict'))
              and not any(value.get('grant') == grant for value in state.get('task_results', {}).values())
              and not any(value['state'] == 'running' for value in state.get('observed_handles', {}).values())
              and not any(other.get('state') in workers.LIVE for key, other in state['workers'].items() if key != grant),
              'scope_violation', 'Recovery requires only one unbound, unclaimed attempt and no live commands or accepted result.')
    history = row.get('reconciliation_events', {})
    w.require(history and len(history) < 128 and not row.get('reconciliation_overflow')
              and all(item.get('kind') == 'identity_observation' and item.get('value', {}).get('status') == 'conflict'
                      and item['value'].get('reason') == LEGACY_CONFLICT for item in history.values()),
              'invalid_evidence', 'An independent or ambiguous identity conflict cannot be reconciled.')
    source, cursor = state.get('claude_transcript_source'), state.get('claude_transcript_cursor')
    w.require(isinstance(source, dict) and source == row.get('transcript_source')
              and native._valid_seal(source, 'taskplane.claude-transcript-source/v1')
              and isinstance(cursor, dict) and native._valid_seal(cursor, 'taskplane.claude-transcript-cursor/v1')
              and cursor.get('source_sha256') == source['sha256'] and cursor.get('conflict') == LEGACY_CONFLICT
              and cursor.get('offset') == 0, 'invalid_evidence', 'Original history-limit source/cursor is missing or different.')
    assert isinstance(source, dict) and isinstance(cursor, dict)
    native._check_cursor(cursor)
    key = digest(native._binding(state['root'], row))
    entry = cursor['entries'].get(key)
    w.require(set(cursor['entries']) == {key} and isinstance(entry, dict)
              and entry.get('binding') == native._binding(state['root'], row) and not entry.get('conflict')
              and all(entry.get(role) == [] for role in ('call', 'result', 'header', 'notification', 'peer_handback')),
              'invalid_evidence', 'Only the exact empty legacy candidate can be reobserved.')
    matched = [a for a in admissions.values() if a.get('run') == state['run'] and a.get('call_id') == row['call_id']]
    w.require(len(matched) == 1, 'invalid_evidence', 'Original automatic launch admission is missing or ambiguous.')
    admission = matched[0]
    w.require(all(admission.get(k) == v for k, v in {
        'root': state['root'], 'principal': state['root'], 'workspace': str(workspace), 'host': 'claude',
        'binding': row['binding'], 'input_digest': row['dispatch_digest'], 'automatic': True,
        'authority': 'phase_grant', 'runtime': row.get('expected_runtime')}.items())
        and admission.get('tool') == 'Agent' and admission.get('state') == 'completed',
        'invalid_evidence', 'Original launch admission differs from the exact owned attempt.')
    definition = workers.task(state, row['task_id'])
    frozen = Store(workspace).resolve(row['snapshot'])
    w.require(row['binding'] == binding(state) and row['task_digest'] == digest(definition)
              and row.get('workspace_contract') == state.get('workspace_contract')
              and frozen.get('binding') == row['binding'] and frozen.get('task') == row['task_id']
              and _frozen_owned_unchanged(workspace, state, row, frozen)
              and row['input_manifest'] == evidence.manifest(workspace, list(row['input_manifest']))
              and row.get('dependency_results') == workers.dependency_results(state, definition['dependencies'])
              and evidence.changed(workspace, state) is None, 'stale_checkpoint', 'Frozen inputs or accepted evidence changed.')
    exceeded = _history_limit(source, cursor)
    fresh = None
    answer: dict[str, Any] = {}
    for _ in range(MAX_PASSES):
        answer = native.observe(state['root'], row, {}, source=source, cursor=fresh)
        fresh = answer.get('cursor')
        if answer['status'] != 'not_yet_available':
            break
    w.require(answer.get('status') == 'matched' and answer.get('completion', {}).get('no_report') is True,
              'invalid_evidence', 'Exact native startup failure is not verifiable within the bounded recovery scan: '
              + answer.get('reason', 'no native evidence'))
    w.require(native._stamp(row['prepared_at']) <= native._stamp(admission['admitted_at'])
              <= native._stamp(answer['observed_at']), 'invalid_evidence', 'Original launch chronology disagrees.')
    candidate = deepcopy(state)
    changed = candidate['workers'][grant]
    candidate['claude_transcript_cursor'] = fresh
    changed['identity_conflict'] = False
    before_ref = store.put('claude-startup-recovery-before',
        {'worker': row, 'cursor': cursor, 'admission': admission, 'oversized_historical_record': exceeded})
    workers.reconcile(workspace, candidate, changed, answer)
    completion = answer['completion']
    stops = changed.get('stop_observations', [changed['stop_observation']] if changed.get('stop_observation') else [])
    w.require(len(stops) == 1 and stops[0].get('worker_id') == answer['worker_id']
              and stops[0].get('parent') == state['root']
              and native._stamp(stops[0]['observed_at']) <= native._stamp(completion['timestamp'])
              and not changed.get('identity_conflict') and not changed.get('claimed_at')
              and evidence.changed(workspace, state) is None,
              'invalid_evidence', 'Reobserved launch, unique stop and no-report completion do not form a failed startup.')
    changed['launch_proof_ref'] = store.put('claude-launch-proof', answer['proof'])
    changed.setdefault('launch_proof_refs', []).append(changed['launch_proof_ref'])
    changed['handback'] = {'schema': 'claude.session-task-notification/v1', 'status': 'no_report',
        'body_digest': completion['body_digest'], 'notification': completion,
        'stop_event': stops[0]['event_id'], 'observed_at': workers.now()}
    workers.terminal(changed, 'failed', digest(completion))
    changed['startup_recovery'] = {'schema': 'taskplane.claude-startup-recovery/v1',
        'before_ref': before_ref, 'proof_ref': changed['launch_proof_ref'], 'recovered_at': workers.now(),
        'authority': 'none', 'approval_changed': False, 'result_accepted': False}
    workers._audit(changed, 'legacy_startup_reconciled', changed['startup_recovery'])
    allowed = {'workers', 'claude_transcript_cursor', 'claude_transcript_budget'}
    w.require(all(candidate.get(k) == v for k, v in state.items() if k not in allowed),
              'invalid_evidence', 'Recovery unexpectedly changed a workflow field.')
    return candidate, {'status': 'reconciled', 'grant': grant, 'task': row['task_id'],
                       'state': 'failed', 'result_accepted': False, 'retry_required': True}


class _ProposalStore(Store):
    """Preview context refs without creating even immutable objects on refusal."""
    def __init__(self, workspace: Path):
        super().__init__(workspace)
        self.pending: list[dict[str, Any]] = []

    def put(self, kind: str, value: Any, source_key: str = '') -> dict[str, Any]:
        w.require(not source_key, 'invalid_evidence', 'Recovery uses canonical context references.')
        reference = self.reference(kind, value)
        self.pending.append({'kind': kind, 'value': deepcopy(value), 'reference': reference})
        return reference


def _retained_artifact(store: Store, record: dict[str, Any], reference: dict[str, Any]) -> Any:
    matches = [a for a in record.get('retained_artifacts', []) if a.get('reference') == reference]
    if matches:
        w.require(len(matches) == 1 and store.reference(matches[0]['kind'], matches[0]['value']) == reference,
                  'invalid_evidence', 'Retained startup artifact changed.')
        return deepcopy(matches[0]['value'])
    return store.resolve(reference)


def _resumed_result(row: dict[str, Any]) -> dict[str, Any]:
    return {'status': 'reconciled', 'grant': row['grant_id'], 'task': row['task_id'],
            'state': 'failed', 'terminal_status': 'startup_failed',
            'accepted': False, 'result_accepted': False, 'retry_required': True,
            'retry_allowed': False, 'retry_authorized': False,
            'permission_status': 'native_permission_required'}


def _reconcile_resumed(workspace: Path, state: dict[str, Any], admissions: dict[str, Any],
                       row: dict[str, Any], store: _ProposalStore) -> tuple[dict[str, Any], dict[str, Any]]:
    result = _resumed_result(row)
    if row.get('startup_recovery', {}).get('kind') == 'resumed_startup':
        record = row['startup_recovery']
        before = _retained_artifact(store, record, record['before_ref'])
        retained = _retained_artifact(store, record, record['proof_ref'])
        w.require(row['state'] == 'failed' and row.get('terminal_status') == 'startup_failed'
                  and row.get('identity_conflict') is True and not row.get('claimed_at')
                  and retained['denial'] == record['permission_blocker']
                  and before['worker']['grant_id'] == row['grant_id'],
                  'invalid_evidence', 'Recovered startup record or retained evidence changed.')
        replay = deepcopy(state)
        replay['workers'][row['grant_id']] = before['worker']
        replay['claude_transcript_cursor'] = before['cursor']
        proof = _resumed_proof(workspace, replay, admissions, replay['workers'][row['grant_id']])
        w.require(proof == retained, 'stale_checkpoint', 'Recovered startup native evidence or artifacts changed.')
        return state, result
    proof = _resumed_proof(workspace, state, admissions, row)
    before_ref = store.put('claude-resumed-startup-before', {'worker': row,
        'cursor': state['claude_transcript_cursor'], 'admissions': admissions})
    proof_ref = store.put('claude-resumed-startup-proof', proof)
    candidate = deepcopy(state)
    changed = candidate['workers'][row['grant_id']]
    changed.update(state='failed', terminal_status='startup_failed', ended_at=workers.now(), startup_recovery={
        'schema': 'taskplane.claude-startup-recovery/v1', 'kind': 'resumed_startup',
        'before_ref': before_ref, 'proof_ref': proof_ref, 'permission_blocker': proof['denial'],
        'authority': 'none', 'approval_changed': False, 'result_accepted': False,
        'retry_authorized': False, 'retry_allowed': False, 'accepted': False})
    workers._audit(changed, 'resumed_startup_reconciled', changed['startup_recovery'])
    return candidate, result


def _source_stamps(state: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    source = state.get('claude_transcript_source')
    w.require(isinstance(source, dict) and source == row.get('transcript_source')
              and native._valid_seal(source, 'taskplane.claude-transcript-source/v1'),
              'invalid_evidence', 'Selected startup source is missing or changed.')
    assert isinstance(source, dict)
    path = native._source_path(state['root'], source['path'])
    fd, project = native._open_source(path)
    stamps = {}
    with os.fdopen(fd, 'rb', buffering=0) as stream:
        info = native._regular(stream.fileno())
        w.require(native._identity(info) == source['identity'] and project == source['project_identity'],
                  'stale_checkpoint', 'Selected startup source identity changed.')
        stamps[str(path)] = _file_stamp(info)
    if row.get('worker_id'):
        path = native._child_path(path, state['root'], row['worker_id'])
        with os.fdopen(native._open_child(path, project), 'rb', buffering=0) as stream:
            stamps[str(path)] = _file_stamp(native._regular(stream.fileno()))
    return stamps


def _bounded_validation(operation: Callable[[], _ValidationResult]) -> _ValidationResult:
    """Preserve domain refusals before their ValueError superclass is handled."""
    try:
        return operation()
    except w.Refusal:
        raise
    except (ValueError, TypeError, KeyError, OSError, AttributeError, IndexError, OverflowError, StopIteration):
        raise w.Refusal('invalid_evidence', 'Malformed or unavailable selected startup evidence.') from None


def inspect_reconciliation(workspace: Path, state: dict[str, Any], admissions: dict[str, Any],
                           grant: str, request: dict[str, Any], *,
                           expected_storage_generation: int) -> dict[str, Any]:
    """Inspect a frozen run without writes, including immutable context writes.

    ``expected_storage_generation`` is the enclosing controller database's
    generation, NOT the run/phase revision. workflow_host must retain the exact
    database lock from revalidation through publication and commit_controller.
    The returned sealed proposal is process-local evidence, never request-level
    authority. Do not accept proposals from tool inputs.
    """
    def inspect() -> dict[str, Any]:
        from .storage import storage_generation
        generation = storage_generation({'storage_generation': expected_storage_generation})
        w.require(isinstance(state, dict) and isinstance(admissions, dict) and isinstance(request, dict)
                  and not request, 'invalid_evidence', 'Select startup recovery without supplied proof or overrides.')
        row = state['workers'][grant]
        workers.current(state, row)
        stamps = _source_stamps(state, row)
        owned = evidence.manifest(workspace, row['paths'], allow_missing=True)
        store = _ProposalStore(workspace)
        if row.get('worker_id') and (row.get('identity_conflict') or
                                     row.get('startup_recovery', {}).get('kind') == 'resumed_startup'):
            candidate, result = _reconcile_resumed(workspace, state, admissions, row, store)
        else:
            candidate, result = _reconcile_legacy(workspace, state, admissions, grant, request, store=store)
            result = {**result, 'accepted': False, 'retry_allowed': False, 'retry_authorized': False}
        if store.pending:
            # The single controller CAS retains proof bodies as well as their
            # references. No external artifact publication precedes acceptance
            # of this selected transition, so a refusal leaves artifact bytes
            # unchanged even if native sources change during inspection.
            candidate['workers'][grant]['startup_recovery']['retained_artifacts'] = deepcopy(store.pending)
        w.require(stamps == _source_stamps(state, row) and owned == evidence.manifest(workspace, row['paths'], allow_missing=True),
                  'stale_checkpoint', 'Selected startup evidence changed during inspection.')
        return native._sealed({'schema': 'taskplane.startup-reconciliation-proposal/v1',
            'workspace': str(workspace), 'grant': grant, 'binding': deepcopy(row['binding']),
            'expected_storage_generation': generation, 'state_sha256': digest(state),
            'admissions_sha256': digest(admissions), 'source_stamps': stamps, 'owned_manifest': owned,
            'candidate': candidate, 'result': result, 'artifacts': store.pending})
    return _bounded_validation(inspect)


def revalidate_reconciliation(workspace: Path, state: dict[str, Any], admissions: dict[str, Any],
                              proposal: dict[str, Any], *,
                              current_storage_generation: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read-only final validation under the controller lock, before any publish."""
    def validate() -> tuple[dict[str, Any], dict[str, Any]]:
        from .storage import storage_generation
        actual = storage_generation({'storage_generation': current_storage_generation})
        w.require(native._valid_seal(proposal, 'taskplane.startup-reconciliation-proposal/v1')
                  and proposal['workspace'] == str(workspace), 'invalid_evidence', 'Startup proposal changed.')
        w.require(actual == proposal['expected_storage_generation']
                  and digest(state) == proposal['state_sha256']
                  and digest(admissions) == proposal['admissions_sha256'],
                  'stale_checkpoint', 'Controller or admissions changed before startup recovery commit.')
        row = state['workers'][proposal['grant']]
        workers.current(state, row)
        w.require(row['binding'] == proposal['binding']
                  and _source_stamps(state, row) == proposal['source_stamps']
                  and evidence.manifest(workspace, row['paths'], allow_missing=True) == proposal['owned_manifest'],
                  'stale_checkpoint', 'Startup evidence changed before recovery commit.')
        # Re-run complete semantic checks and all native span reads. Stamps alone
        # are not authenticity evidence and are never sufficient for reuse.
        fresh = inspect_reconciliation(workspace, state, admissions, proposal['grant'], {},
                                       expected_storage_generation=actual)
        w.require(fresh['source_stamps'] == proposal['source_stamps']
                  and fresh['owned_manifest'] == proposal['owned_manifest'],
                  'stale_checkpoint', 'Startup evidence changed during recovery revalidation.')
        return deepcopy(proposal['candidate']), deepcopy(proposal['result'])
    return _bounded_validation(validate)


def publish_reconciliation_artifacts(workspace: Path, proposal: dict[str, Any]) -> None:
    """Optional materialization AFTER the selected controller CAS has committed.

    Inspection embeds these exact proof bodies in the selected worker record,
    so its one durable generation commit is self-contained. Never call this on
    the validation/refusal path. Publication failure after commit is a separate
    materialization diagnostic; it must not be reported as reconciliation
    refusal or undo the committed transition. Replay resolves the retained
    inline proof even when optional immutable-object publication has not run.
    """
    w.require(native._valid_seal(proposal, 'taskplane.startup-reconciliation-proposal/v1')
              and proposal['workspace'] == str(workspace), 'invalid_evidence', 'Startup proposal changed.')
    store = Store(workspace)
    for artifact in proposal['artifacts']:
        w.require(store.put(artifact['kind'], artifact['value']) == artifact['reference'],
                  'invalid_evidence', 'Recovery artifact differs from its inspected reference.')


def reconcile(workspace: Path, state: dict[str, Any], admissions: dict[str, Any], grant: str,
              request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Legacy caller guard: use inspect/revalidate/publish with database generation.

    Keeping this symbol gives older adapters a specific fail-closed diagnostic
    instead of silently committing dangling preview references or unguarded state.
    """
    raise w.Refusal('invalid_evidence', 'Startup recovery requires generation-bound inspect/revalidate before commit.')
