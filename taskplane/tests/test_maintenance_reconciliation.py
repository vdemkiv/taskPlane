"""Explicit maintenance acknowledges bytes, never widens a phase grant."""
from copy import deepcopy
import json

import pytest

from taskplane import flow, primitives, workflow as w, workflow_evidence as evidence
from taskplane.tests.test_worker_runtime import setup, reserve, parent_pair


def maintenance_fixture(tmp_path):
    c, s = setup(tmp_path, count=1)
    (tmp_path / 'repair.py').write_text('fixed = True\n')
    request = {'schema': 'taskplane.maintenance-request/v1', 'request_reference': 'fixture/user-fix',
               'reason': 'Repair the recovery runtime before continuing this phase.',
               'changes': {'repair.py': {'before': None, 'after': primitives.content_fingerprint((tmp_path / 'repair.py').read_bytes())}}}
    path = tmp_path / '.taskplane/bootstrap/maintenance.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(request))
    return c, s, request, path


def test_reconciliation_pins_exact_bytes_without_expanding_scope_or_reusing_readiness(tmp_path, monkeypatch, capsys):
    c, s, request, path = maintenance_fixture(tmp_path)
    baseline = deepcopy(s['source_baseline'])
    with pytest.raises(w.Refusal, match='outside'): c.adapter.before_action(c.report(), 'worker')
    monkeypatch.setenv('CODEX_THREAD_ID', 'root')
    from taskplane.tests.test_workflow_recovery import runtime
    c.guard(runtime('flow', 'reconcile-maintenance', '--workspace', str(tmp_path), '--run', s['run'],
                    '--expected-revision', str(s['revision']), '--maintenance-file', str(path.relative_to(tmp_path))), s['run'])
    assert flow.main(['reconcile-maintenance', '--workspace', str(tmp_path), '--run', s['run'],
                      '--expected-revision', str(s['revision']), '--maintenance-file', str(path.relative_to(tmp_path))], governor=c) == 0
    receipt = json.loads(capsys.readouterr().out)
    current = c.report()
    assert current['revision'] == s['revision'] + 1
    assert current['scope'] == s['scope'] and current['decisions'] == s['decisions']
    assert current['source_baseline'] == {**baseline, 'repair.py': request['changes']['repair.py']['after']}
    assert current['history'][-1]['maintenance'] == receipt
    assert not current.get('parent_hook_readiness')
    assert not receipt['scope_changed'] and not receipt['approvals_changed']
    c.adapter.before_action(current, 'worker')
    with pytest.raises(w.Refusal):
        c.guard({'tool_name': 'Write', 'tool_input': {'file_path': 'repair.py'}}, s['run'])
    with pytest.raises(w.Refusal, match='automatic'):
        c.worker(s['run'], 'prepare', revision=current['revision'], task='T0',
                 request={'capacity': {'host_slots': 2, 'includes_root': True, 'reference': 'fixture'}})
    parent_pair(c)
    assert reserve(c, current)['grant']['binding']['revision'] == current['revision']
    (tmp_path / 'repair.py').write_text('fixed = False\n')
    with pytest.raises(w.Refusal, match='outside'): c.adapter.before_action(c.report(), 'worker')


@pytest.mark.parametrize('defect', ['revision', 'principal', 'profile', 'reference', 'reason', 'missing_path',
    'hash', 'before', 'extra_path', 'phase_path', 'symlink', 'live_worker', 'live_command', 'accepted_drift',
    'empty_changes', 'malformed_change', 'unknown_field', 'repeat', 'sealed'])
def test_reconciliation_refuses_incomplete_or_unbound_requests_atomically(tmp_path, monkeypatch, defect):
    c, s = setup(tmp_path, count=1)
    if defect == 'live_worker': reserve(c, s)
    (tmp_path / 'repair.py').write_text('fixed = True\n')
    request = {'schema': 'taskplane.maintenance-request/v1', 'request_reference': 'fixture/user-fix', 'reason': 'Repair.',
               'changes': {'repair.py': {'before': None, 'after': primitives.content_fingerprint((tmp_path / 'repair.py').read_bytes())}}}
    path = tmp_path / '.taskplane/bootstrap/maintenance.json'; path.parent.mkdir(parents=True, exist_ok=True)
    target = c._path()
    if defect == 'principal': c.principal = 'child'
    if defect == 'profile': monkeypatch.setattr(c.adapter, 'profile', 'protected_host')
    if defect == 'reference': request['request_reference'] = ''
    if defect == 'reason': request['reason'] = ' '
    if defect == 'missing_path': (tmp_path / 'extra.py').write_text('missing from manifest\n')
    if defect == 'hash': request['changes']['repair.py']['after'] = '0' * 64
    if defect == 'before': request['changes']['repair.py']['before'] = '0' * 64
    if defect == 'extra_path': request['changes']['extra.py'] = {'before': None, 'after': '0' * 64}
    if defect == 'phase_path':
        (tmp_path / 'T0.md').write_text('phase output\n')
        request['changes']['T0.md'] = {'before': None, 'after': primitives.content_fingerprint((tmp_path / 'T0.md').read_bytes())}
    if defect == 'symlink':
        (tmp_path / 'repair.py').unlink(); (tmp_path / 'repair.py').symlink_to(tmp_path / 'input.py')
    if defect == 'live_command':
        db = c._read(target); db['runs'][s['run']]['observed_handles'] = {'fixture': {'state': 'running'}}; c._write(target, db)
    if defect == 'accepted_drift': monkeypatch.setattr(evidence, 'changed', lambda *args, **kwargs: ('fixture', 'Accepted bytes changed.'))
    if defect == 'sealed': monkeypatch.setattr(w, 'current', lambda state: {'decision': 'pending'})
    if defect == 'empty_changes': request['changes'] = {}
    if defect == 'malformed_change': request['changes']['repair.py'] = []
    if defect == 'unknown_field': request['approve'] = True
    path.write_text(json.dumps(request))
    if defect == 'repeat': c.reconcile_maintenance(s['run'], s['revision'], str(path.relative_to(tmp_path)))
    before = target.read_bytes()
    with pytest.raises(w.Refusal):
        c.reconcile_maintenance(s['run'], s['revision'] + int(defect == 'revision'), str(path.relative_to(tmp_path)))
    assert target.read_bytes() == before
