"""One exact legacy history-limit startup failure, never general conflict erasure."""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
from typing import Any

from . import claude_worker_observations as native, workflow as w, workflow_evidence as evidence
from . import worker_runtime as workers
from .context import Store, digest
from .context_handoff import binding

LEGACY_CONFLICT = 'Native transcript source or cursor is unsafe, malformed or changed'
MAX_PASSES = 8


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


def reconcile(workspace: Path, state: dict[str, Any], admissions: dict[str, Any], grant: str,
              request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
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
    before_ref = Store(workspace).put('claude-startup-recovery-before',
        {'worker': row, 'cursor': cursor, 'admission': admission, 'oversized_historical_record': exceeded})
    result = workers.observe_claude_launches(candidate, [changed], {})
    w.require(result[0]['status'] == 'matched' and changed['state'] == 'failed'
              and changed.get('handback', {}).get('status') == 'no_report'
              and not changed.get('identity_conflict') and not changed.get('claimed_at')
              and evidence.changed(workspace, state) is None, 'invalid_evidence',
              'Reobserved launch, stop and no-report completion do not form a failed startup.')
    changed['startup_recovery'] = {'schema': 'taskplane.claude-startup-recovery/v1',
        'before_ref': before_ref, 'proof_ref': changed['launch_proof_ref'], 'recovered_at': workers.now(),
        'authority': 'none', 'approval_changed': False, 'result_accepted': False}
    workers._audit(changed, 'legacy_startup_reconciled', changed['startup_recovery'])
    allowed = {'workers', 'claude_transcript_cursor', 'claude_transcript_budget'}
    w.require(all(candidate.get(k) == v for k, v in state.items() if k not in allowed),
              'invalid_evidence', 'Recovery unexpectedly changed a workflow field.')
    return candidate, {'status': 'reconciled', 'grant': grant, 'task': row['task_id'],
                       'state': 'failed', 'result_accepted': False, 'retry_required': True}
