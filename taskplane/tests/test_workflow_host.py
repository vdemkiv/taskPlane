"""Protected-store tests use internal adapters, never production bypass flags."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import pytest
from taskplane import workflow as w, workflow_host as h
from taskplane.tests.test_workflow_evidence import prepare


def test_denial_controller_recovery_regression(tmp_path, monkeypatch):
    from taskplane.tests.test_worker_runtime import denied_launch
    from taskplane import claude_worker_observations as observations
    from taskplane.context import digest
    host, grant, event, _, _ = denied_launch(tmp_path, monkeypatch, legacy=True)
    state = host.controller.report()
    row = state['workers'][grant]
    entry = state['claude_transcript_cursor']['entries'][digest(observations._binding('root', row))]
    request = dict(request_reference='human/recover-observed-denial', call_id=event['tool_use_id'],
                   **{f'expected_{role}_sha256': entry[role][0]['reference']['sha256'] for role in ('call', 'result')})
    recovered = host.controller.worker(state['run'], 'recover-launch-denied', revision=state['revision'],
                                       grant=grant, request=request)
    assert recovered['state'] == 'failed' and recovered['terminal_status'] == 'launch_denied'
    assert recovered.get('denial_recovery') and not recovered.get('identity_conflict')


def denial_request(state, grant):
    from taskplane import claude_worker_observations as observations
    from taskplane.context import digest
    row = state['workers'][grant]
    entry = state['claude_transcript_cursor']['entries'][digest(observations._binding('root', row))]
    return dict(request_reference='human/recover-observed-denial', call_id=row['call_id'],
                **{f'expected_{role}_sha256': entry[role][0]['reference']['sha256'] for role in ('call', 'result')})


@pytest.mark.parametrize('variant', ['start', 'explicit', 'modern', 'textless'])
@pytest.mark.parametrize('timing', ['before_recovery', 'after_recovery'])
def test_denial_lifecycle_ingestion_blocks_saturated_legacy_recovery(tmp_path, monkeypatch, variant, timing):
    from taskplane.tests.test_worker_runtime import denied_launch, denial_lifecycle_event, ingest_unrelated_lifecycle
    from taskplane import worker_runtime as workers
    host, grant, event, _, _ = denied_launch(tmp_path, monkeypatch, legacy=True)
    c = host.controller; run = host.state['run']
    ingest_unrelated_lifecycle(host, 1024)
    original = c.report(); request = denial_request(original, grant)
    if timing == 'after_recovery':
        c.worker(run, 'recover-launch-denied', revision=original['revision'], grant=grant, request=request)
        assert c.can_seal(run)
    before = deepcopy(c.report()['workers'][grant])
    c.observe(denial_lifecycle_event(host, event['tool_use_id'], variant), run)
    with pytest.raises(w.Refusal):
        c.worker(run, 'recover-launch-denied', revision=original['revision'], grant=grant, request=request)
    after = c.report()
    row = after['workers'][grant]
    assert row['state'] == 'unknown' and row['identity_conflict']
    assert not c.can_seal(run) and not workers.joined(after)
    assert not row.get('worker_id') and not row.get('claimed_at') and not row.get('context_receipt')
    for key in ('denial_recovery', 'denial_proof_ref', 'revoked_at', 'ended_at', 'terminal_status'):
        assert row.get(key) == before.get(key)
    assert after['decisions'] == original['decisions'] and after['history'] == original['history']
    assert not after.get('task_results')


@pytest.mark.parametrize('defect', ['child', 'wrong_root', 'wrong_run', 'wrong_revision', 'boolean_revision',
    'call', 'call_hash', 'result_hash', 'missing_reference', 'extra_field', 'binding', 'digest',
    'admission', 'admission_hash', 'admission_time', 'sealed', 'inactive', 'source_drift',
    'source_absent', 'cursor', 'global_conflict', 'unrelated_history', 'history_overflow', 'worker',
    'claim', 'context', 'context_delivery', 'native_start', 'terminal', 'result', 'live_handle', 'revoked', 'codex'])
def test_denial_recovery_refusals_preserve_original_attempt(tmp_path, monkeypatch, defect):
    from taskplane.tests.test_worker_runtime import denied_launch
    from taskplane import claude_worker_observations as observations, worker_runtime as workers
    host, grant, _, _, _ = denied_launch(tmp_path, monkeypatch, legacy=True)
    c = host.controller; db = c._read(c._path()); state = db['runs'][host.state['run']]
    row = state['workers'][grant]; request = denial_request(state, grant)
    revision, run = state['revision'], state['run']
    if defect == 'child': c.principal = 'other-worker'
    if defect == 'wrong_root': c.root = 'foreign-root'
    if defect == 'wrong_run': run = 'foreign-run'
    if defect == 'wrong_revision': revision += 1
    if defect == 'boolean_revision': revision = False
    if defect == 'call': request['call_id'] = 'foreign-call'
    if defect == 'call_hash': request['expected_call_sha256'] = '0' * 64
    if defect == 'result_hash': request['expected_result_sha256'] = '0' * 64
    if defect == 'missing_reference': request['request_reference'] = ''
    if defect == 'extra_field': request['force'] = True
    if defect == 'binding': row['binding']['revision'] += 1
    if defect == 'digest': row['dispatch_digest'] = '0' * 64
    if defect.startswith('admission'):
        admitted = next((key, value) for key, value in db['admissions'].items() if value['call_id'] == row['call_id'])
        if defect == 'admission': del db['admissions'][admitted[0]]
        if defect == 'admission_hash': admitted[1]['input_digest'] = '0' * 64
        if defect == 'admission_time': admitted[1]['admitted_at'] = '2999-01-01T00:00:00Z'
    if defect == 'sealed':
        stage = state['visits'][state['index']]
        stage.update(decision='awaiting_human_approval', packet=dict(phase=stage['phase'], visit=stage['id'],
            checkpoint='fixture', manifest={}, source_manifest={}, context={}, output={}))
    if defect == 'inactive': db['active'] = None
    if defect == 'source_drift': (tmp_path / 'input.py').write_text('changed = 1\n')
    if defect == 'source_absent': host.parent.unlink()
    if defect == 'cursor': state['claude_transcript_cursor']['sha256'] = '0' * 64
    if defect == 'global_conflict':
        state['claude_transcript_cursor']['conflict'] = 'Independent source contradiction'
        state['claude_transcript_cursor'] = observations._sealed(state['claude_transcript_cursor'])
    if defect == 'unrelated_history': workers._audit(row, 'late_identity', {'worker_id': 'unrelated-child'})
    if defect == 'history_overflow': row['reconciliation_overflow'] = True
    for key, field in [('worker', 'worker_id'), ('claim', 'claimed_at'), ('context', 'context_receipt'),
                       ('context_delivery', 'context_delivery'), ('native_start', 'native_session_started_at'),
                       ('terminal', 'terminal_status'), ('result', 'result_ref'), ('revoked', 'revoked_at')]:
        if defect == key: row[field] = 'contradictory-evidence'
    if defect == 'live_handle':
        state['observed_handles']['123'] = dict(state='running', revision=state['revision'], visit=w.current(state)['id'])
    if defect == 'codex': c.adapter.name = 'codex'
    target = host.controller.adapter.control_path(tmp_path, 'root')
    # Fixture mutation only, preserving the full original controller shape.
    target.write_text(json.dumps(db))
    before = target.read_bytes()
    with pytest.raises(w.Refusal):
        c.worker(run, 'recover-launch-denied', revision=revision, grant=grant, request=request)
    after = json.loads(target.read_text())['runs'][host.state['run']]['workers'][grant]
    assert {k: v for k, v in after.items() if k != 'denial_recovery_progress'} == row
    assert not after.get('denial_recovery')
    if defect != 'source_absent': assert target.read_bytes() == before


def test_denial_recovery_cli_preserves_before_history_and_revalidates_replay(tmp_path, monkeypatch, capsys):
    from taskplane.tests.test_worker_runtime import denied_launch
    from taskplane import flow, claude_worker_observations as observations
    from taskplane.context import Store, digest
    host, grant, _, _, result = denied_launch(tmp_path, monkeypatch, legacy=True)
    c = host.controller; db = c._read(c._path()); state = db['runs'][host.state['run']]
    request = denial_request(state, grant)
    # An unrelated cursor entry's sticky conflict is never reset by recovery.
    entry = deepcopy(next(iter(state['claude_transcript_cursor']['entries'].values())))
    entry['binding']['grant_id'] = 'another-grant'; entry['binding']['call_id'] = 'another-call'
    entry['conflict'] = 'Independent sticky contradiction'
    other_key = digest(entry['binding'])
    state['claude_transcript_cursor']['entries'][other_key] = entry
    state['claude_transcript_cursor'] = observations._sealed(state['claude_transcript_cursor'])
    c._write(c._path(), db)
    before = deepcopy(state)
    command = ['worker', '--workspace', str(tmp_path), '--run', state['run'], '--operation', 'recover-launch-denied',
               '--grant', grant, '--expected-revision', str(state['revision']), '--worker-json', json.dumps(request)]
    assert flow.main(command, governor=c) == 0
    answer = json.loads(capsys.readouterr().out)
    retained = Store(tmp_path).resolve(answer['denial_recovery']['before_ref'])
    assert retained['row'] == before['workers'][grant]
    assert retained['cursor'] == before['claude_transcript_cursor']
    assert retained['admission']['input_digest'] == before['workers'][grant]['dispatch_digest']
    current = c.report()
    assert current['claude_transcript_cursor']['entries'][other_key] == entry
    assert current['decisions'] == before['decisions'] and current['history'] == before['history']
    sequence = current['worker_sequence']
    replay = c.worker(state['run'], 'recover-launch-denied', revision=state['revision'], grant=grant, request=request)
    assert replay == answer and c.report()['worker_sequence'] == sequence
    changed = deepcopy(result); changed['extra'] = 'competing result'
    host.append(changed)
    with pytest.raises(w.Refusal):
        c.worker(state['run'], 'recover-launch-denied', revision=state['revision'], grant=grant, request=request)
    failed = c.report()['workers'][grant]
    assert failed['state'] == 'unknown' and failed['identity_conflict']
    assert failed['denial_recovery'] == answer['denial_recovery']


def test_denial_recovery_partial_scan_stays_private_and_resumes(tmp_path, monkeypatch):
    from taskplane.tests.test_worker_runtime import denied_launch
    from taskplane.tests.test_claude_worker_lifecycle import encoded
    from taskplane import claude_worker_observations as observations
    host, grant, _, _, _ = denied_launch(tmp_path, monkeypatch, legacy=True)
    c = host.controller; state = c.report(); request = denial_request(state, grant)
    with host.parent.open('ab') as stream:
        stream.write(encoded({'progress': 'x' * 8192}) * 600)
    with pytest.raises(w.Refusal, match='incomplete'):
        c.worker(state['run'], 'recover-launch-denied', revision=state['revision'], grant=grant, request=request)
    partial = c.report(); row = partial['workers'][grant]
    assert row['state'] == 'unknown' and row['identity_conflict'] and not row.get('revoked_at')
    assert partial['claude_transcript_cursor'] == state['claude_transcript_cursor']
    progress = row['denial_recovery_progress']['cursor']
    assert progress.get('legacy_denial_reobservation')
    assert observations.observe('root', row, {}, source=state['claude_transcript_source'], cursor=progress)['status'] == 'conflict'
    unchanged = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        c.worker(state['run'], 'recover-launch-denied', revision=state['revision'], grant=grant,
                 request={**request, 'expected_result_sha256': '0' * 64})
    assert c._path().read_bytes() == unchanged
    # Ordinary hooks may move the shared scan and refresh diagnostic history;
    # they cannot consume the private recovery cursor or clear its conflict.
    c.observe(dict(host='claude', hook_event_name='PostToolUse', session_id='root'), state['run'])
    fixed = c.worker(state['run'], 'recover-launch-denied', revision=state['revision'], grant=grant, request=request)
    assert fixed['state'] == 'failed' and not fixed.get('denial_recovery_progress')
    assert 'legacy_denial_reobservation' not in c.report()['claude_transcript_cursor']


@pytest.mark.parametrize('boundary', ['prepare', 'dispatch', 'write', 'update_tasks', 'prevalidate',
                                    'submit', 'advance', 'finish', 'auto-decide', 'retire', 'replace', 'stop', 'maintenance'])
@pytest.mark.parametrize('defect', ['late_result', 'missing', 'partial'])
def test_denial_mutable_boundaries_refresh_and_persist_before_refusal(tmp_path, monkeypatch, boundary, defect):
    from taskplane.tests.test_worker_runtime import recorded_denial
    from taskplane import flow
    host, grant, event, _, result = recorded_denial(tmp_path, monkeypatch)
    c = host.controller; state = c.report()
    if boundary == 'maintenance':
        (tmp_path / '.taskplane/maintenance.json').write_text(json.dumps(dict(schema='taskplane.maintenance-request/v1',
            request_reference='human/maintenance', reason='Authorized exact maintenance.',
            changes={'unrelated.py': {'before': None, 'after': '0' * 64}})))
    if defect == 'late_result':
        changed = deepcopy(result); changed['extra'] = 'late competing result'; host.append(changed)
    if defect == 'missing': host.parent.unlink()
    if defect == 'partial':
        with host.parent.open('ab') as stream: stream.write(b'{')
    run, revision = state['run'], state['revision']
    def execute():
        if boundary == 'prepare':
            return c.worker(run, 'prepare', revision=revision, task='T0',
                request={'retry_reason': 'Host conditions changed in explicit request.',
                         'capacity': dict(host_slots=5, includes_root=True, reference='fixture')})
        if boundary == 'dispatch': return c.guard(event, run)
        if boundary == 'write': return c.guard(dict(tool_name='Write', tool_input={'file_path': 'T0.md'}), run)
        if boundary == 'update_tasks': return c.update_tasks(run, revision, 'tasks.json')
        if boundary == 'prevalidate': return c.prevalidate(run, revision=revision, output='product.json', tasks='tasks.json')
        if boundary == 'maintenance': return c.reconcile_maintenance(run, revision, '.taskplane/maintenance.json')
        if boundary == 'replace':
            return c.start(dict(replace_run=run, expected_revision=revision, request_reference='human/new-run',
                                scope=state['scope'], entry='product', standalone=True, tasks='tasks.json'))
        if boundary == 'stop':
            return flow._hook(dict(hook_event_name='Stop', session_id='root', cwd=str(tmp_path)), governor=c)
        return c.apply(boundary, run, expected_revision=revision, output='product.json', tasks='tasks.json',
                       native_reference=json.dumps(dict(request_reference='human/retire', reason='Requested retirement.')))
    if boundary == 'stop':
        assert 'quiescence' in execute()['systemMessage']
    else:
        with pytest.raises(w.Refusal): execute()
    persisted = json.loads(c._path().read_text())['runs'][run]
    row = persisted['workers'][grant]
    assert row['state'] == 'unknown' and row['denial_proof_ref'] == state['workers'][grant]['denial_proof_ref']
    assert bool(row.get('identity_conflict')) is (defect == 'late_result')
    assert persisted['revision'] == revision and persisted['decisions'] == state['decisions']


@pytest.mark.parametrize('defect', [None, 'late_result', 'missing', 'partial', 'budget', 'sticky'])
def test_denial_direct_resume_and_adapter_checks_are_read_only(tmp_path, monkeypatch, defect):
    from taskplane.tests.test_worker_runtime import recorded_denial
    from taskplane import workflow_continuation as continuation
    from taskplane.context import Store
    from datetime import datetime, timezone
    host, grant, _, _, result = recorded_denial(tmp_path, monkeypatch)
    c = host.controller; state = c.report()
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(Path.home() / '.claude'))
    request = dict(schema=continuation.SCHEMA, binding=continuation.binding(state),
        excerpt=f"continue run {state['run']}", recorder='root_orchestrator', source=dict(kind='conversation',
            actor='user', automatic=False, conversation='root', reference='human/resume',
            observed_at=datetime.now(timezone.utc).isoformat()))
    if defect == 'late_result':
        changed = deepcopy(result); changed['extra'] = 'late competing result'; host.append(changed)
    if defect == 'missing': host.parent.unlink()
    if defect == 'partial':
        with host.parent.open('ab') as stream: stream.write(b'{')
    if defect == 'budget':
        from taskplane import claude_worker_observations as observations
        budget_type = observations.ReadBudget
        monkeypatch.setattr(observations, 'ReadBudget', lambda: budget_type(max_bytes=0))
    if defect == 'sticky':
        db = c._read(c._path()); db['runs'][state['run']]['workers'][grant]['identity_conflict'] = True
        c._write(c._path(), db); state = c.report()
    if defect is None: host.append({'progress': 'new captured watermark needs no CAS write'})
    before = c._path().read_bytes()
    store = Store(tmp_path)
    objects = {p.name: p.read_bytes() for p in store.root.rglob('*.json')}
    assert c.adapter.can_seal(state) is (defect is None)
    if defect is None:
        answer = continuation.resume(c, actor='root', run=state['run'], revision=state['revision'],
                                     mode='verify', request=request)
        assert answer['status'] == 'resumed' and answer['state_changed'] is False
    else:
        with pytest.raises(w.Refusal, match='Stop or join'):
            continuation.resume(c, actor='root', run=state['run'], revision=state['revision'],
                                 mode='verify', request=request)
    assert c._path().read_bytes() == before
    assert {p.name: p.read_bytes() for p in store.root.rglob('*.json')} == objects


@pytest.mark.parametrize('choice', ['changes_requested', 'rejected', 'cancelled'])
def test_denial_does_not_veto_bound_negative_human_decision(tmp_path, monkeypatch, choice):
    from taskplane.tests.test_worker_runtime import recorded_denial
    from taskplane.tests.test_workflow_local import decision
    from taskplane.worker_runtime import now
    host, grant, _, _, _ = recorded_denial(tmp_path, monkeypatch)
    c = host.controller; db = c._read(c._path()); state = db['runs'][host.state['run']]
    stage = state['visits'][state['index']]
    stage.update(decision='awaiting_human_approval', submitted_at=now(), packet=dict(phase=stage['phase'], visit=stage['id'],
        checkpoint='fixture', manifest={}, source_manifest={}, context={}, output={}))
    c._write(c._path(), db)
    original = deepcopy(state['workers'][grant])
    host.parent.unlink()
    words = {'changes_requested': 'Changes requested', 'rejected': 'Rejected', 'cancelled': 'Cancelled'}[choice]
    updated = c.apply('decide', state['run'], expected_revision=state['revision'],
                      native_reference=json.dumps(decision(state, text=words)))
    assert w.current(updated)['decision'] == choice
    assert updated['workers'][grant] == original
    assert not c.can_seal(state['run'])


class FixtureHost(h.HostAdapter):
    name = "isolated-test-host"

    def __init__(self, path, scope):
        self.store = path
        self.scope = scope
        self.events = {}
        self.live = False
        self.process_grant = None

    def capabilities(self):
        return {"protected_store": True, "human_origin": True, "tool_containment": True, "process_tracking": True}

    def control_path(self, workspace, root):
        return self.store

    def verify_start(self, workspace, root, request):
        return {"scope": self.scope, "entry": request.get("entry", "product"), "standalone": request.get("standalone", False)}

    def verify_decision(self, native_reference, expected):
        if native_reference not in self.events:
            raise w.Refusal("unsupported_authority", "No independently observed human event")
        return deepcopy(self.events[native_reference])

    def quiescent(self, workspace, root, run):
        return not self.live

    def process_revision(self, event):
        return self.process_grant


def controller(tmp_path, request=None):
    workspace = tmp_path/"workspace"
    workspace.mkdir()
    initial, _, _ = prepare(workspace)
    target = tmp_path/"protected.json"
    target.write_text(json.dumps({"schema": "taskplane.control/v1", "workspace": str(workspace),
                                 "root": "root", "active": None, "runs": {}}))
    host = FixtureHost(target, initial["scope"])
    c = h.Controller(workspace, "root", host)
    s = c.start(request or {})
    return c, host, s


def submit(c, s):
    phase = w.current(s)["phase"]
    _, output, _ = prepare(c.workspace, phase)
    output.update(run=s["run"], visit=w.current(s)["id"])
    (c.workspace/(phase+".json")).write_text(json.dumps(output))
    return c.apply("submit", s["run"], expected_revision=s["revision"], output=phase+".json", tasks="tasks.json")


def native(host, s, key="human-event", **extra):
    host.events[key] = {"event_id": key, "human": True, "automatic": False,
                        "choice": "approved", "binding": w.binding(s, w.current(s)["packet"]), **extra}
    return key


def test_standalone_repair_refusal_preserves_protected_authorization(tmp_path):
    c, host, s = controller(tmp_path, {"entry": "engineering", "standalone": True})
    s = submit(c, s)
    target = c.workspace/"engineering.json"
    out = json.loads(target.read_text())
    out["route_change"] = {"kind": "repair"}
    target.write_text(json.dumps(out))
    s = c.apply("submit", s["run"], expected_revision=s["revision"], output="engineering.json", tasks="tasks.json")
    event = native(host, s)
    before = host.store.read_bytes()
    with pytest.raises(w.Refusal, match="accepted Plan"):
        c.apply("decide", s["run"], expected_revision=s["revision"], native_reference=event)
    assert host.store.read_bytes() == before
    with pytest.raises(w.Refusal):
        c.apply("advance", s["run"], expected_revision=s["revision"], phase="build")
    with pytest.raises(w.Refusal):
        c.guard({"tool_name": "Write", "tool_input": {"path": "app.py"}}, s["run"])
    assert host.store.read_bytes() == before


def test_native_origin_required_and_concurrent_duplicate_applied_once(tmp_path):
    c, host, s = controller(tmp_path)
    s = submit(c, s)
    before = host.store.read_bytes()
    with pytest.raises(w.Refusal):
        c.apply("decide", s["run"], expected_revision=s["revision"], native_reference="workspace-receipt")
    assert host.store.read_bytes() == before
    event = native(host, s)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: c.apply("decide", s["run"], expected_revision=s["revision"], native_reference=event), range(2)))
    assert results[0] == results[1]
    assert len(results[0]["decisions"]) == 1
    resumed = h.Controller(c.workspace, "root", host)
    assert resumed.report(s["run"])["visits"][0]["decision"] == "approved"


@pytest.mark.parametrize("failure", ["missing", "corrupt", "wrong-root", "symlink", "bad-visit", "fake-finish", "boolean-revision"])
def test_control_state_never_resets_after_loss_or_corruption(tmp_path, failure):
    c, host, s = controller(tmp_path)
    if failure == "missing":
        host.store.unlink()
    elif failure == "corrupt":
        host.store.write_text("{broken")
    elif failure == "wrong-root":
        data = json.loads(host.store.read_text())
        data["root"] = "different"
        host.store.write_text(json.dumps(data))
    elif failure == "symlink":
        other = tmp_path/"other"
        other.write_bytes(host.store.read_bytes())
        host.store.unlink()
        host.store.symlink_to(other)
    else:
        db = json.loads(host.store.read_text())
        stored = db["runs"][s["run"]]
        if failure == "bad-visit":
            stored["visits"][0]["phase"] = "deploy"
        elif failure == "fake-finish":
            stored["finished"] = True
        else:
            stored["revision"] = True
        host.store.write_text(json.dumps(db))
    with pytest.raises(w.Refusal):
        c.start({})


def test_stale_source_revokes_acceptance_and_keeps_history(tmp_path):
    c, host, s = controller(tmp_path)
    s = submit(c, s)
    event = native(host, s)
    s = c.apply("decide", s["run"], expected_revision=s["revision"], native_reference=event)
    (c.workspace/"product.json").write_text("{}")
    with pytest.raises(w.Refusal):
        c.apply("advance", s["run"], expected_revision=s["revision"], phase="design")
    stored = json.loads(host.store.read_text())["runs"][s["run"]]
    assert stored["visits"][0]["decision"] == "stale"
    assert stored["decisions"][event]["choice"] == "approved"


def test_opaque_and_out_of_scope_writes_and_live_process_boundary(tmp_path):
    c, host, s = controller(tmp_path)
    for event in [
        {"tool_name": "Write", "tool_input": {"path": "app.py"}},
        {"tool_name": "Bash", "tool_input": {"cmd": "touch app.py", "read_only": True}},
        {"tool_name": "write_stdin", "tool_input": {"session_id": 1}},
    ]:
        with pytest.raises(w.Refusal):
            c.guard(event, s["run"])
    c.guard({"tool_name": "Write", "tool_input": {"path": "product.json"}}, s["run"])
    host.live = True
    with pytest.raises(w.Refusal, match="Live tool"):
        submit(c, s)


@pytest.mark.parametrize("adapter", [h.CodexAdapter, h.ClaudeAdapter])
def test_installed_profiles_cannot_be_enabled_by_forged_environment(tmp_path, monkeypatch, adapter):
    monkeypatch.setenv("TASKPLANE_TEST_APPROVAL", "approved")
    monkeypatch.setenv("TASKPLANE_CONTROL_ROOT", str(tmp_path))
    c = h.Controller(tmp_path, "root", adapter())
    assert c.report()["status"] == "capability_blocked"
    with pytest.raises(w.Refusal, match="unverified"):
        c.start({"scope": {"actor": "human"}})


@pytest.mark.parametrize("bad", [{"automatic": True}, {"human": False}])
def test_automatic_or_nonhuman_decision_cannot_accept(tmp_path, bad):
    c, host, s = controller(tmp_path)
    s = submit(c, s)
    before = host.store.read_bytes()
    key = native(host, s, **bad)
    with pytest.raises(w.Refusal):
        c.apply("decide", s["run"], expected_revision=s["revision"], native_reference=key)
    assert host.store.read_bytes() == before


def test_process_grant_revoked_on_submit_and_old_revision_rejected(tmp_path):
    c, host, s = controller(tmp_path)
    host.process_grant = s["revision"]
    event = {"tool_name": "write_stdin", "tool_input": {"session_id": "known"}}
    c.guard(event, s["run"])
    s = submit(c, s)
    with pytest.raises(w.Refusal):
        c.guard(event, s["run"])
    key = native(host, s, choice="changes_requested", prompt="DO NOT PERSIST NATIVE PROMPT")
    s = c.apply("decide", s["run"], expected_revision=s["revision"], native_reference=key)
    assert "DO NOT PERSIST" not in host.store.read_text()
    with pytest.raises(w.Refusal, match="expired"):
        c.guard(event, s["run"])
    with pytest.raises(w.Refusal, match="revision"):
        c.apply("submit", s["run"], expected_revision=s["revision"]-1, output="product.json", tasks="tasks.json")


def test_stale_predecessor_returns_to_its_scope_and_can_be_resubmitted(tmp_path):
    c, host, s = controller(tmp_path)
    s = submit(c, s)
    key = native(host, s)
    s = c.apply("decide", s["run"], expected_revision=s["revision"], native_reference=key)
    s = c.apply("advance", s["run"], expected_revision=s["revision"], phase="design")
    s = submit(c, s)
    (c.workspace/"product.json").write_text("changed predecessor")
    projected = c.report()
    assert projected["phase"] == "product" and projected["revision"] == s["revision"]
    with pytest.raises(w.Refusal, match="changed"):
        c.apply("advance", s["run"], expected_revision=projected["revision"], phase="plan")
    s = c.report()
    c.guard({"tool_name": "Write", "tool_input": {"path": "product.json"}}, s["run"])
    with pytest.raises(w.Refusal):
        c.guard({"tool_name": "Write", "tool_input": {"path": "app.py"}}, s["run"])
    s = submit(c, s)
    key = native(host, s, key="corrected-product")
    s = c.apply("decide", s["run"], expected_revision=s["revision"], native_reference=key)
    s = c.apply("advance", s["run"], expected_revision=s["revision"], phase="design")
    assert w.current(s)["decision"] == "stale"
    s = submit(c, s)
    assert w.current(s)["decision"] == "awaiting_human_approval"
    assert len(s["decisions"]) == 2


@pytest.mark.parametrize('margin', [-1, 0, 1])
def test_protected_store_persisted_byte_boundary(tmp_path, monkeypatch, margin):
    from taskplane.tests.test_workflow_local import check_store_byte_boundary
    c, _, _ = controller(tmp_path)
    check_store_byte_boundary(c, monkeypatch, {'nested':[['é', '東京'] * 40] * 4}, margin)


def test_protected_historical_finish_preserves_new_active_run(tmp_path):
    from taskplane.tests.test_workflow_local import check_historical_finish_preserves_active
    c, host, a = controller(tmp_path, {'standalone':True})
    a = submit(c, a)
    a = c.apply('decide', a['run'], expected_revision=a['revision'], native_reference=native(host, a))
    a = c.apply('finish', a['run'], expected_revision=a['revision'])
    assert c.report()['status'] == 'no_workflow'
    assert c.apply('finish', a['run'], expected_revision=a['revision']) == a
    b = c.start({})
    check_historical_finish_preserves_active(c, a, b)


@pytest.mark.parametrize('defect', [None, 'question', 'metadata', 'answer-key', 'answer-type',
                                  'annotation', 'call', 'session', 'tool', 'codex'])
def test_claude_question_answer_enrichment_preserves_admitted_identity(tmp_path, defect):
    from taskplane import flow
    from taskplane.tests.test_worker_runtime import setup
    c, s = setup(tmp_path)
    c.adapter.name = 'claude'
    original = {'questions': [{'question': 'Proceed?', 'header': 'Next step',
                'options': [{'label': 'Yes', 'description': 'Continue'},
                            {'label': 'No', 'description': 'Stop'}], 'multiSelect': False}]}
    event = dict(hook_event_name='PreToolUse', session_id='root', cwd=str(tmp_path),
                 tool_name='AskUserQuestion', tool_use_id='question-1', tool_input=original)
    flow.hook(event, governor=c)
    enriched = {**deepcopy(original), 'answers': {'Proceed?': 'A freeform answer'}, 'annotations': {}}
    post = {**event, 'hook_event_name': 'PostToolUse', 'tool_input': enriched}
    if defect == 'question': enriched['questions'][0]['question'] = 'Changed?'
    if defect == 'metadata': enriched['other'] = 'changed'
    if defect == 'answer-key': enriched['answers'] = {'Other question': 'Yes'}
    if defect == 'answer-type': enriched['answers'] = {'Proceed?': ['Yes']}
    if defect == 'annotation': enriched['annotations'] = {'unverified': 'shape'}
    if defect == 'call': post['tool_use_id'] = 'other-call'
    if defect == 'session':
        identity = c._call(post)[1]
        assert not c._post_matches({**identity, 'principal': 'other'}, identity, post)
        return
    if defect == 'tool': post['tool_name'] = 'Bash'
    if defect == 'codex': c.adapter.name = 'codex'
    if defect == 'call':
        assert c.complete_admission(post) is None
    elif defect is not None:
        with pytest.raises(w.Refusal, match='Completion conflicts'):
            flow.hook(post, governor=c)
    else:
        flow.hook(post, governor=c)
        row = c.complete_admission(post)
        assert row['state'] == 'completed'
        assert 'A freeform answer' not in json.dumps(row)
