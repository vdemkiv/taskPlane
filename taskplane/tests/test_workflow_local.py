"""Real local adapter tests; observations are cooperative, not host attestation."""
from copy import deepcopy
import json
import pytest
from taskplane import workflow as w, workflow_host as h, workflow_local as local
from taskplane.tests.test_workflow_evidence import prepare


def setup(workspace, *, entry="product", standalone=False):
    workspace=workspace.resolve()
    state, _, _=prepare(workspace)
    (workspace/"build-check.txt").write_text("Fixture Build check passed")
    (workspace/"check.txt").write_text("Observed fixture check result")
    c=h.Controller(workspace,"root",h.installed_adapter("codex"))
    s=c.start({"scope":state["scope"],"request_reference":"conversation/user-1",
               "entry":entry,"standalone":standalone})
    return c,s


def submit(c,s):
    phase=w.current(s)["phase"]
    _,out,_=prepare(c.workspace,phase)
    out.update(run=s["run"],visit=w.current(s)["id"])
    (c.workspace/(phase+".json")).write_text(json.dumps(out))
    return c.apply("submit",s["run"],expected_revision=s["revision"],output=phase+".json",tasks="tasks.json")


def decision(s, *, text="Approved", event="message-1"):
    binding=w.binding(s,w.current(s)["packet"])
    return {"schema":"taskplane.observed-decision/v1","event_id":event,"choice":local.choice(text),
            "binding":binding,"excerpt":text,"recorder":"root_orchestrator",
            "source":{"kind":"conversation","reference":event,"conversation":s["root"],"actor":"user",
                      "automatic":False,"observed_at":"2026-09-16T22:02:00+00:00"},
            "presentation":{"checkpoint":binding["checkpoint"],"reference":"assistant/presentation",
                            "at":"2026-09-16T22:01:00+00:00"}}


def decide(c,s,value=None):
    return c.apply("decide",s["run"],expected_revision=s["revision"],native_reference=json.dumps(value or decision(s)))


def test_local_profile_is_available_without_certifying_host(tmp_path):
    c,s=setup(tmp_path)
    report=c.report()
    assert report["workflow_available"] and not report["authority_verified"]
    assert all(report["capabilities"][k] is False for k in ("human_origin","protected_store","tool_containment","process_tracking"))
    assert report["coverage"]["process_census"]=="unknown"
    assert h.Controller(c.workspace,"root",h.installed_adapter("codex")).report()["run"]==s["run"]
    protected=h.Controller(c.workspace,"root",h.installed_adapter("codex","protected_host"))
    assert not protected.availability()["workflow_available"]
    with pytest.raises(w.Refusal,match="unverified"):protected.start({})


@pytest.mark.parametrize("bad",["missing","marker","corrupt","identity","profile","symlink"])
def test_local_initialized_state_cannot_silently_reset(tmp_path,bad):
    c,s=setup(tmp_path)
    path=c.adapter.control_path(c.workspace,c.root)
    if bad=="missing":path.unlink()
    elif bad=="marker":path.with_name(c.adapter.markername).unlink()
    elif bad=="corrupt":path.write_text("{")
    elif bad=="symlink":
        backup=path.with_suffix(".saved");path.rename(backup);path.symlink_to(backup)
    else:
        value=json.loads(path.read_text());value["root" if bad=="identity" else "profile"]="wrong";path.write_text(json.dumps(value))
    with pytest.raises((w.Refusal,ValueError)):c.start({})


@pytest.mark.parametrize("bad",["actor","automatic","source","choice","ordering","binding","boolean_revision","no_presentation","actor_only"])
def test_observed_decision_rejects_invalid_provenance_and_binding(tmp_path,bad):
    c,s=setup(tmp_path);s=submit(c,s);value=decision(s)
    if bad=="actor":value["source"]["actor"]="assistant"
    elif bad=="automatic":value["source"]["automatic"]=True
    elif bad=="source":value["source"]["kind"]="tool_result"
    elif bad=="choice":value["excerpt"]="Please keep working"
    elif bad=="ordering":value["source"]["observed_at"]="2026-09-16T22:00:00+00:00"
    elif bad=="binding":value["binding"]["root"]="another"
    elif bad=="boolean_revision":value["binding"]["revision"]=True
    elif bad=="no_presentation":value.pop("presentation")
    else:value={"human":True,"actor":"human","choice":"approved"}
    with pytest.raises(w.Refusal):decide(c,s,value)
    assert c.report()["status"]=="awaiting_human_approval"


def test_decision_restart_replay_changes_requested_and_drift(tmp_path):
    c,s=setup(tmp_path);s=submit(c,s);value=decision(s,text="Changes requested")
    s=decide(c,s,value);assert w.current(s)["decision"]=="changes_requested"
    s=submit(c,s);value=decision(s,event="message-2")
    accepted=decide(c,s,value)
    assert accepted["decisions"]["message-2"]["assurance"]=="observed"
    resumed=h.Controller(c.workspace,"root",h.installed_adapter("codex"))
    assert decide(resumed,s,value)==accepted
    conflict=deepcopy(value);conflict["source"]["reference"]="different"
    with pytest.raises(w.Refusal,match="replay"):decide(resumed,s,conflict)
    (tmp_path/"product.json").write_text("{}")
    with pytest.raises(w.Refusal,match="changed"):
        resumed.apply("advance",s["run"],expected_revision=accepted["revision"],phase="design")
    assert resumed.report()["status"]=="stale"


@pytest.mark.parametrize("change",["modify","create","delete"])
def test_out_of_scope_source_drift_prevents_sealing(tmp_path,change):
    c,s=setup(tmp_path)
    if change=="modify":(tmp_path/"app.py").write_text("value = 9")
    elif change=="create":(tmp_path/"extra.py").write_text("value = 9")
    else:(tmp_path/"app.py").unlink()
    with pytest.raises(w.Refusal,match="outside this phase scope"):
        c.adapter.before_action(c.report(),"submit")


def test_known_processes_block_seal_and_stale_input(tmp_path):
    c,s=setup(tmp_path)
    event={"hook_event_name":"PostToolUse","tool_name":"exec_command","tool_input":{},"tool_response":{"session_id":123}}
    c.observe(event,s["run"])
    assert not c.adapter.can_seal(c.report())
    c.guard({"tool_name":"write_stdin","tool_input":{"session_id":123}},s["run"])
    with pytest.raises(w.Refusal,match="Live tool processes"):submit(c,s)
    event["tool_response"]["exit_code"]=0;c.observe(event,s["run"])
    with pytest.raises(w.Refusal,match="terminal"):c.guard({"tool_name":"write_stdin","tool_input":{"session_id":123}},s["run"])
    s=submit(c,s);assert w.current(s)["decision"]=="awaiting_human_approval"
    with pytest.raises(w.Refusal,match="sealed"):c.guard({"tool_name":"Write","tool_input":{"path":"product.json"}},s["run"])


def test_structured_scope_and_exact_control_command_at_gate(tmp_path):
    c,s=setup(tmp_path)
    c.guard({"tool_name":"Write","tool_input":{"path":"product.json"}},s["run"])
    with pytest.raises(w.Refusal,match="outside"):c.guard({"tool_name":"Write","tool_input":{"path":"app.py"}},s["run"])
    s=submit(c,s)
    import sys,shlex
    from pathlib import Path
    command=shlex.join([sys.executable,str(Path(h.__file__).with_name("tp.py")),"flow","report","--workspace",str(c.workspace)])
    c.guard({"tool_name":"exec_command","tool_input":{"cmd":command}},s["run"])
    with pytest.raises(w.Refusal,match="sealed"):c.guard({"tool_name":"exec_command","tool_input":{"cmd":command+"; touch app.py"}},s["run"])


def test_local_concurrent_replay_commits_one_decision(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    c,s=setup(tmp_path);s=submit(c,s);value=decision(s)
    def record(_):
        resumed=h.Controller(c.workspace,"root",h.installed_adapter("codex"))
        return decide(resumed,s,value)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(record,range(8)))
    assert all(result==results[0] for result in results)
    assert len(c.report()["decisions"])==1
    assert c.report()["revision"]==s["revision"]+1


def test_interrupted_initialization_requires_recovery(tmp_path,monkeypatch):
    initial,_,_=prepare(tmp_path)
    c=h.Controller(tmp_path.resolve(),"root",h.installed_adapter("codex"))
    original=local.primitives.atomic_json
    def interrupted(path,value,**kwargs):
        if str(path).endswith(c.adapter.filename):
            raise OSError("simulated interruption after marker")
        return original(path,value,**kwargs)
    monkeypatch.setattr(local.primitives,"atomic_json",interrupted)
    with pytest.raises(OSError,match="interruption"):
        c.start({"scope":initial["scope"],"request_reference":"test/start"})
    monkeypatch.setattr(local.primitives,"atomic_json",original)
    with pytest.raises(w.Refusal,match="recovery"):
        c.start({"scope":initial["scope"],"request_reference":"test/retry"})


def test_native_prompt_needs_envelope_and_journal_failure_keeps_decision(tmp_path,monkeypatch):
    from taskplane import flow
    c,s=setup(tmp_path);s=submit(c,s)
    event={"cwd":str(c.workspace),"session_id":"root","hook_event_name":"UserPromptSubmit","prompt":"Approve"}
    result=flow.hook(event)
    assert "prompt alone" in result["hookSpecificOutput"]["additionalContext"]
    assert c.report()["status"]=="awaiting_human_approval"
    def failed(*args,**kwargs):raise OSError("simulated optional observation failure")
    monkeypatch.setattr(flow,"_observe_hook",failed)
    value=decision(s);value["source"]["kind"]="native_prompt";value["recorder"]="native_prompt_hook"
    event["taskplane_decision"]=value
    assert flow.hook(event)=={}
    resumed=h.Controller(c.workspace,"root",h.installed_adapter("codex"))
    assert resumed.report()["status"]=="approved"
    assert resumed.report()["phase"]=="product"


def check_store_byte_boundary(c, monkeypatch, payload, margin):
    """Use actual persisted bytes, including escaping/indentation/newline, as the boundary."""
    target = c.adapter.control_path(c.workspace, c.root)
    before = target.read_bytes()
    original = json.loads(before)
    candidate = deepcopy(original)
    candidate['size_probe'] = payload
    c._write(target, candidate)
    measured = target.read_bytes()
    c._write(target, original)
    assert target.read_bytes() == before
    monkeypatch.setattr(local, 'MAX_BYTES', len(measured) + margin)
    if margin < 0:
        with pytest.raises(w.Refusal, match='size limit'):
            c._write(target, candidate)
        assert target.read_bytes() == before
    else:
        c._write(target, candidate)
        assert target.read_bytes() == measured
        assert len(measured) <= local.MAX_BYTES
    resumed = h.Controller(c.workspace, c.root, c.adapter).report()
    active = original['runs'][original['active']]
    assert resumed['run'] == active['run'] and resumed['revision'] == active['revision']
    assert resumed['status'] == 'not_requested'


@pytest.mark.parametrize('margin', [-1, 0, 1])
@pytest.mark.parametrize('payload', [[['item'] * 40] * 4, {'unicode': ['é', '東京', '🙂'] * 40}])
def test_local_persisted_byte_boundary_preserves_readable_state(tmp_path, monkeypatch, payload, margin):
    c, _ = setup(tmp_path)
    check_store_byte_boundary(c, monkeypatch, payload, margin)


@pytest.mark.parametrize('failure_call', [1, 2])
def test_control_write_does_not_acknowledge_failed_file_or_directory_sync(tmp_path, monkeypatch, failure_call):
    from taskplane import primitives
    c, s = setup(tmp_path)
    target = c.adapter.control_path(c.workspace, c.root)
    before = target.read_bytes()
    candidate = json.loads(before)
    candidate['sync_probe'] = 'updated'
    original = primitives.os.fsync
    calls = []
    def fail_sync(fd):
        calls.append(fd)
        if len(calls) == failure_call:
            raise OSError('simulated sync failure')
        return original(fd)
    with monkeypatch.context() as patch:
        patch.setattr(primitives.os, 'fsync', fail_sync)
        with pytest.raises(w.Refusal, match='not acknowledged'):
            c._write(target, candidate)
    assert len(calls) == failure_call
    if failure_call == 1:
        assert target.read_bytes() == before
    else:
        assert json.loads(target.read_bytes()) == candidate
    assert not list(target.parent.glob('.'+target.name+'.*.tmp'))
    assert h.Controller(c.workspace, c.root, c.adapter).report()['revision'] == s['revision']


@pytest.mark.parametrize('profile', ['local', 'protected'])
def test_control_state_remains_private_under_permissive_umask(tmp_path, profile):
    import os
    import stat
    from taskplane.tests.test_workflow_host import controller
    previous = os.umask(0o022)
    try:
        c = setup(tmp_path)[0] if profile == 'local' else controller(tmp_path)[0]
        target = c.adapter.control_path(c.workspace, c.root)
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        state = json.loads(target.read_bytes())
        state['privacy_probe'] = 'updated'
        c._write(target, state)
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert json.loads(target.read_bytes()) == state
    finally:
        os.umask(previous)


def check_historical_finish_preserves_active(c, finished, active):
    """Shared by real local, protected fixture and isolated extracted-runtime checks."""
    target = c.adapter.control_path(c.workspace, c.root)
    before = target.read_bytes()
    replay = c.apply('finish', finished['run'], expected_revision=finished['revision'])
    assert replay == finished
    assert target.read_bytes() == before
    db = json.loads(before)
    assert db['active'] == active['run'] and db['runs'][active['run']] == active
    resumed = h.Controller(c.workspace, c.root, c.adapter)
    assert resumed.report()['run'] == active['run']
    assert resumed.report()['revision'] == active['revision']
    resumed.guard({'tool_name':'Write', 'tool_input':{'path':'product.json'}}, active['run'])
    try:
        resumed.guard({'tool_name':'Write', 'tool_input':{'path':'app.py'}}, active['run'])
    except w.Refusal as exc:
        assert exc.reason == 'scope_violation'
    else:
        raise AssertionError('New active run lost its scope guard after historical finish')
    for state, revision, reason in [(active, active['revision'], 'approval_required'),
                                    (finished, finished['revision']-1, 'stale_checkpoint')]:
        try:
            resumed.apply('finish', state['run'], expected_revision=revision)
        except w.Refusal as exc:
            assert exc.reason == reason
        else:
            raise AssertionError('Invalid completion accepted')
        assert target.read_bytes() == before


def test_local_historical_finish_preserves_active_run_and_refusals(tmp_path):
    c, a = setup(tmp_path, standalone=True)
    a = decide(c, submit(c, a))
    a = c.apply('finish', a['run'], expected_revision=a['revision'])
    assert c.report()['status'] == 'no_workflow'
    assert c.apply('finish', a['run'], expected_revision=a['revision']) == a
    b = c.start({'scope':a['scope'], 'request_reference':'test/new-run'})
    check_historical_finish_preserves_active(c, a, b)
    # No output for B overwrites A's evidence. An actual change to A still invalidates A only.
    (c.workspace/'product.json').write_text('{}')
    with pytest.raises(w.Refusal, match='changed'):
        c.apply('finish', a['run'], expected_revision=a['revision'])
    target = c.adapter.control_path(c.workspace, c.root)
    stale = json.loads(target.read_bytes())['runs'][a['run']]
    before = target.read_bytes()
    with pytest.raises(w.Refusal, match='human acceptance'):
        c.apply('finish', a['run'], expected_revision=stale['revision'])
    assert target.read_bytes() == before
    assert c.report()['run'] == b['run'] and c.report()['revision'] == b['revision']
