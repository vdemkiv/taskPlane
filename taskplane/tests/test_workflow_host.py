"""Protected-store tests use internal adapters, never production bypass flags."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import pytest
from taskplane import workflow as w, workflow_host as h
from taskplane.tests.test_workflow_evidence import prepare


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


def test_collaborative_refinement_race_replay_and_worker_denial(tmp_path):
    from taskplane.tests.test_workflow_local import collaborative_setup, refinement
    c, state, _ = collaborative_setup(tmp_path)
    request = refinement(state, 'hold', {'reason':'Explore options'})
    def apply(value):
        return c.refine(state['run'], state['revision'], json.dumps(value))
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(apply, [request, request]))
    assert result[0] == result[1] and result[0]['revision'] == state['revision'] + 1
    assert len(result[0]['refinement_events']) == 1
    with pytest.raises(w.Refusal, match='Conflicting'):
        apply({**request, 'payload':{'reason':'Changed claim'}})
    other = {**request, 'event_id':'second'}
    with pytest.raises(w.Refusal, match='exact run'): apply(other)
    c.principal = 'child'
    with pytest.raises(w.Refusal, match='Workers'): apply(request)


def test_collaborative_creation_is_explicit_and_does_not_migrate_legacy(tmp_path):
    from taskplane.tests.test_workflow_local import setup
    c, state = setup(tmp_path)
    assert w.phase_contract(state) is None
    before = c._path().read_bytes()
    reused = c.start({'request_reference':'conversation/user-1'}, phase_contract=w.PHASE_CONTRACT)
    assert w.phase_contract(reused) is None and c._path().read_bytes() == before


def test_collaborative_root_gate_retains_failed_attempt_causal_obligation(tmp_path):
    from taskplane.tests.test_workflow_local import collaborative_setup, collaborative_seal
    c, state, output = collaborative_setup(tmp_path)
    state = collaborative_seal(c, state, output)
    # A selected passing result cannot erase its captured failed attempt.
    w.current(state)['packet']['verification'] = {'history': {'attempts': [
        {'check_id':'REGRESSION', 'status':'fail'}, {'check_id':'REGRESSION', 'status':'pass'}]}}
    before = deepcopy(state)
    status = h.phase_readiness(tmp_path, state, w.current(state)['packet']['output'])
    assert not status['ready'] and any('REGRESSION' in blocker for blocker in status['blockers'])
    assert state == before
    with pytest.raises(w.Refusal, match='causal corrections'): c._phase_ready(state)


def collaborative_repair_submit(c, state, output):
    from taskplane import depgraph
    from taskplane.context_handoff import Session, consume_required
    from taskplane.tests.test_workflow_local import present
    depgraph.scan(str(c.workspace), decompose=True, strict=True)
    output['context_receipt'], _ = consume_required(Session(c.workspace, state))
    path = output['phase'] + '.json'
    (c.workspace / path).write_text(json.dumps(output))
    state = c.apply('submit', state['run'], expected_revision=state['revision'], output=path, tasks='tasks.json')
    present(c, state)
    return state


def collaborative_repair_setup(tmp_path):
    from taskplane.tests.test_workflow_local import collaborative_setup
    # Check evidence exists before the native source baseline is captured.
    prepare(tmp_path, 'build')
    prepare(tmp_path, 'evaluate')
    return collaborative_setup(tmp_path)


def collaborative_repair_evaluate(c, state):
    from taskplane.tests.test_workflow_evidence import collaborative_review
    from taskplane.tests.test_workflow_local import decide, decision
    import hashlib
    evidence = {'path': 'check.txt', 'sha256': hashlib.sha256(
        (c.workspace / 'check.txt').read_bytes()).hexdigest()}
    for index, phase in enumerate(w.PHASES[:5]):
        _, output, _ = prepare(c.workspace, phase)
        output.update(run=state['run'], visit=w.current(state)['id'],
                      phase_review=collaborative_review(state, evidence))
        if phase == 'evaluate':
            output['unknowns_and_failures'] = []
            return state, output, evidence
        if phase == 'build':
            output['known_gaps'] = []
        state = collaborative_repair_submit(c, state, output)
        state = decide(c, state, decision(state, event='accept-' + phase))
        state = c.apply('advance', state['run'], expected_revision=state['revision'], phase=w.PHASES[index + 1])
    raise AssertionError('Evaluate was not reached')


@pytest.mark.parametrize('outcome', ['fail', 'unknown', 'listed'])
def test_collaborative_repair_evaluate_manual_refuses_outcomes_but_allows_negative(tmp_path, outcome):
    from taskplane.tests.test_workflow_local import decide, decision
    c, state, _ = collaborative_repair_setup(tmp_path)
    state, output, _ = collaborative_repair_evaluate(c, state)
    if outcome == 'listed':
        output['unknowns_and_failures'] = ['Scenario remains unresolved']
    else:
        output['criterion_results']['AC1']['status'] = outcome
    state = collaborative_repair_submit(c, state, output)
    original = deepcopy(w.current(state)['packet'])
    with pytest.raises(w.Refusal, match='Evaluate'):
        decide(c, state, decision(state, event='positive'))
    gates = h.phase_readiness(c.workspace, state)
    assert not gates['ready']
    assert any('causal investigation: AC1' in item for item in gates['blockers']) == (outcome == 'fail')
    state = decide(c, state, decision(state, text='Changes requested', event='negative'))
    assert w.current(state)['decision'] == 'changes_requested'
    assert w.current(state)['packet'] == original


@pytest.mark.parametrize('route', ['direct', 'draft'])
@pytest.mark.parametrize('defect', ['required', 'default_required', 'affected_checks', 'required_type', 'checks_type'])
def test_collaborative_repair_controller_preserves_causal_obligations(tmp_path, route, defect):
    from taskplane.tests.test_workflow_evidence import collaborative_cause
    from taskplane.tests.test_workflow_local import collaborative_setup, refine, refinement, decide
    c, state, output = collaborative_setup(tmp_path)
    cause = collaborative_cause(tmp_path, output['phase_review']['dor'][0]['evidence'][0])
    cause['affected_checks'].append('integration')
    if defect == 'default_required':
        cause.pop('required')
    output['phase_review']['causes'] = [cause]
    if route == 'draft':
        (tmp_path / 'product.json').write_text(json.dumps(output))
        state = refine(c, state, refinement(state, 'draft', {'output': 'product.json'}, human=False))
    else:
        state = collaborative_repair_submit(c, state, output)
    original = deepcopy(state)
    persisted = c.adapter.control_path(c.workspace, c.root).read_bytes()
    output['phase_review']['revision'] = 2
    if defect == 'affected_checks':
        cause['affected_checks'].remove('integration')
    elif defect == 'checks_type':
        cause['affected_checks'] = 'regression'
    elif defect == 'required_type':
        cause['required'] = 'true'
    else:
        cause['required'] = False
    with pytest.raises(w.Refusal, match='Causal'):
        if route == 'draft':
            (tmp_path / 'product.json').write_text(json.dumps(output))
            refine(c, state, refinement(state, 'draft', {'output': 'product.json'}, human=False))
        else:
            collaborative_repair_submit(c, state, output)
    assert c.adapter.control_path(c.workspace, c.root).read_bytes() == persisted
    # Restoring, reordering and strengthening the original obligation permits
    # closure only after every affected check has matching verification.
    cause['required'] = True
    cause['affected_checks'] = ['integration', 'regression', 'additional']
    cause['verification'].extend({**deepcopy(cause['verification'][0]), 'id': key, 'kind': 'integration'}
                                for key in ('integration', 'additional'))
    if route == 'draft':
        (tmp_path / 'product.json').write_text(json.dumps(output))
        state = refine(c, state, refinement(state, 'draft', {'output': 'product.json'}, human=False))
    state = collaborative_repair_submit(c, state, output)
    assert h.phase_readiness(tmp_path, state)['ready']
    if route == 'direct':
        assert any(row.get('packet') == w.current(original)['packet'] for row in state['history'])
    state = decide(c, state)
    state = c.apply('advance', state['run'], expected_revision=state['revision'], phase='design')
    assert w.current(state)['phase'] == 'design'


@pytest.mark.parametrize('phase', ['evaluate', 'engineering'])
def test_collaborative_accepted_repair_keeps_bound_inputs_and_live_gates(tmp_path, phase):
    from taskplane import workflow_evidence as evidence
    from taskplane.tests.test_workflow_local import decide, decision, collaborative_output
    c, state, _ = collaborative_repair_setup(tmp_path)
    state, output, _ = collaborative_repair_evaluate(c, state)
    if phase == 'engineering':
        state = collaborative_repair_submit(c, state, output)
        state = decide(c, state, decision(state, event='accept-evaluate'))
        state = c.apply('advance', state['run'], expected_revision=state['revision'], phase=phase)
        _, output, _ = prepare(tmp_path, phase)
        output.update(run=state['run'], visit=w.current(state)['id'])
        collaborative_output(tmp_path, state, output)
    output['route_change'] = {'kind': 'repair', 'findings': ['fixture'],
                             'reason': 'Repair the observed defect', 'proposed_paths': ['app.py']}
    state = collaborative_repair_submit(c, state, output)
    state = decide(c, state, decision(state, event='accept-repair'))
    assert w.current(state)['superseded']
    assert evidence.accepted_review_inputs(state) == output['phase_review']['inputs']
    before = deepcopy(state)
    for defect in ('packet', 'predecessor', 'approval', 'hold'):
        broken = deepcopy(state)
        stage = broken['visits'][broken['index']]
        if defect == 'packet':
            stage['packet']['output']['phase_review']['inputs'] = []
        elif defect == 'predecessor':
            broken['visits'][0]['packet']['output']['summary'] = 'changed'
        elif defect == 'approval':
            broken['decisions'].pop('accept-repair')
        else:
            stage['hold'] = {'active': True}
        with pytest.raises(w.Refusal):
            w.advance(broken, 'build')
    changed = c.apply('advance', state['run'], expected_revision=state['revision'], phase='build')
    assert w.current(changed)['phase'] == 'build'
    assert changed['decisions'] == before['decisions']
    assert changed['visits'] == before['visits']


def test_collaborative_unsubmitted_transition_refuses_as_approval_without_mutation(tmp_path):
    from taskplane.tests.test_workflow_local import collaborative_setup
    c, state, _ = collaborative_setup(tmp_path)
    before = c._path().read_bytes()
    for action in ('advance', 'finish'):
        with pytest.raises(w.Refusal) as refused:
            c.apply(action, state['run'], expected_revision=state['revision'], phase='design')
        assert refused.value.reason == 'approval_required'
        assert c._path().read_bytes() == before
    with pytest.raises(w.Refusal) as refused:
        w.finish(state)
    assert refused.value.reason == 'approval_required'


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
    assert b.pop('start_created') is True  # Start outcome is response-only, not persisted state.
    check_historical_finish_preserves_active(c, a, b)
