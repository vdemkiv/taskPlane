"""Recovery must be reachable through hooks without granting phase approval."""
from copy import deepcopy
import json
from pathlib import Path
import os
import shlex
import subprocess
import sys
import zipfile

import pytest

from taskplane import flow, workflow as w, workflow_host as h
from taskplane.tests.test_workflow_local import setup, submit, decide, decision


@pytest.mark.parametrize('initialized', [False, True])
def test_oversized_source_diagnosis_does_not_initialize_or_hash(tmp_path, initialized, monkeypatch):
    from taskplane import workflow_local as local
    from taskplane.tests.test_workflow_evidence import prepare
    state,_,_=prepare(tmp_path)
    c=h.Controller(tmp_path,'root',h.installed_adapter('codex'))
    harness=local.Harness(tmp_path,'root');harness.select('tp-engineering','fixture/review',c.report())
    with (tmp_path/'large.bin').open('wb') as stream:stream.truncate(local.MAX_SOURCE_BYTES+1)
    if initialized:
        with pytest.raises(w.Refusal,match='512 MiB.*flow diagnose'):
            c.start({'scope':state['scope'],'request_reference':'fixture/start'})
        assert c.adapter.state_exists() and not c.report().get('run')
    before={p.name:p.read_bytes() for p in (tmp_path/'.taskplane').glob('workflow-*.json')}
    harness.guard_bootstrap(runtime('flow','diagnose','--workspace',str(tmp_path)),c.report())
    monkeypatch.setattr(local.hashlib,'sha256',lambda *a,**k: (_ for _ in ()).throw(AssertionError('metadata diagnosis must not hash source')))
    result=local.diagnose(tmp_path)
    assert result['source_limit_exceeded'] is True
    assert result['largest_inspected_files'][0]['path']=='large.bin'
    assert {p.name:p.read_bytes() for p in (tmp_path/'.taskplane').glob('workflow-*.json')}==before


def test_diagnosis_walk_and_output_are_bounded(tmp_path,monkeypatch):
    from taskplane import workflow_local as local
    monkeypatch.setattr(local,'MAX_SOURCE_FILES',3)
    for i in range(8):(tmp_path/str(i)).write_bytes(b'x')
    result=local.diagnose(tmp_path)
    assert result['partial'] and not result['complete'] and result['inspected_entries']==3
    assert result['source_limit_exceeded'] is None and len(result['largest_inspected_files'])<=3


@pytest.mark.parametrize('stale',[False,True])
def test_only_exact_taskplane_native_administration_passes_seal(tmp_path,stale):
    c,s=setup(tmp_path);s=submit(c,s)
    if stale:(tmp_path/'product.json').write_text('{}')
    event={'tool_name':'mcp__codex_app__uninstall_plugin','tool_input':{'plugin':'taskplane@openai-curated-remote'}}
    c.guard(event,s['run'])
    for args in ({'plugin':'another-plugin'},{'plugin':'taskplane','command':'arbitrary'},{'plugin':['taskplane']}):
        with pytest.raises(w.Refusal):c.guard({**event,'tool_input':args},s['run'])


def test_clean_worktree_setup_requires_exact_observed_host_result(tmp_path):
    from taskplane import workflow_local as local
    source=tmp_path/'source';source.mkdir()
    def git(*args):
        result=subprocess.run(['git','-C',str(source),*args],capture_output=True,text=True)
        assert result.returncode==0,result.stderr
    git('init');git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-m','Fixture')
    c,s=setup(source);s=submit(c,s);harness=local.Harness(source,'root')
    event={'tool_name':'mcp__codex_app__create_worktree','tool_input':{'name':'recovery-fixture'},'call_id':'create-1'}
    c.guard(event,s['run'])
    destination=tmp_path/'clean';git('worktree','add','--detach',str(destination),'HEAD')
    start=runtime('flow','start','--workspace',str(destination),'--scope','.taskplane/bootstrap/scope.json','--request-reference','fixture/recovery')
    with pytest.raises(w.Refusal):c.guard(start,s['run'])
    result={'type':'created','worktreeGitRoot':str(destination),'worktreeWorkspaceRoot':str(destination),'registrationError':None}
    observed={**event,'hook_event_name':'PostToolUse','tool_response':{'content':[{'type':'text','text':json.dumps(result)}]}}
    harness.observe_recovery({**observed,'call_id':'foreign'},s)
    assert not harness.read().get('recovery_workspace')
    harness.observe_recovery(observed,s)
    assert harness.read()['recovery_workspace']['workspace']==str(destination)
    c.guard(start,s['run'])
    c.guard({'tool_name':'Write','tool_input':{'path':str(destination/'.taskplane/bootstrap/scope.json')}},s['run'])
    for other in [runtime('flow','start','--workspace',str(tmp_path/'unrelated')),
                  {'tool_name':'Write','tool_input':{'path':str(destination/'app.py')}},
                  {**event,'tool_input':{'ref':'untrusted-branch'}},
                  {**event,'tool_input':{'name':'../escape'}}]:
        with pytest.raises(w.Refusal):c.guard(other,s['run'])
    assert c.report()['revision']==s['revision'] and not c.report()['decisions']


@pytest.mark.parametrize('response',[{}, {'isError':True}, {'content':[{'type':'text','text':'unknown'}]}])
def test_unknown_worktree_result_grants_no_recovery_workspace(tmp_path,response):
    from taskplane import workflow_local as local
    harness=local.Harness(tmp_path,'root');event={'tool_name':'mcp__codex_app__create_worktree','call_id':'x','tool_input':{}}
    harness.update(recovery_pending={'call_id':'x','input_digest':local.primitives.content_fingerprint({}),'binding':harness.binding({})})
    harness.observe_recovery({**event,'tool_response':response},{})
    assert not harness.read().get('recovery_workspace') and not harness.read().get('recovery_pending')


def restart_request(state):
    return {"scope": deepcopy(state["scope"]), "request_reference": "conversation/new-run",
            "entry": "product", "standalone": False, "goal": "New authorized repair",
            "replace_run": state["run"], "expected_revision": state["revision"]}


def command(*words):
    return {"tool_name": "exec_command", "tool_input": {"cmd": shlex.join(words)}}


def runtime(*words):
    return command(sys.executable, str(Path(h.__file__).with_name("tp.py")), *words)


@pytest.mark.parametrize("drift", ["out_of_scope", "evidence", "none"])
def test_explicit_new_run_preserves_old_checkpoint_and_revokes_its_grants(tmp_path, drift):
    c, old = setup(tmp_path)
    old = submit(c, old)
    if drift == "out_of_scope":
        (tmp_path / "upload.zip").write_bytes(b"user requested package")
    elif drift == "evidence":
        (tmp_path / "product.json").write_text("{}")
    request = restart_request(old)
    new = c.start(request)
    assert new["run"] != old["run"]
    assert new["revision"] == 0 and new["decisions"] == {}
    assert w.current(new)["phase"] == "product" and w.current(new)["packet"] is None
    assert not new.get("approval_policy") and not new["finished"]
    prior = c.report(old["run"])
    assert prior["status"] == "superseded" and prior["superseded_by"] == new["run"]
    assert prior["visits"] == old["visits"] and prior["decisions"] == old["decisions"]
    assert not prior["finished"]
    from taskplane.flow_dashboard import _workflow
    old_view = _workflow({"workflow": prior})
    assert "Historical run. Replaced by" in old_view and 'class="stage current"' not in old_view
    assert c.start(request)["run"] == new["run"]  # interrupted response/retry
    with pytest.raises(w.Refusal):
        c.guard({"tool_name": "Write", "tool_input": {"path": "product.json"}}, old["run"])
    with pytest.raises(w.Refusal):
        c.apply("finish", old["run"], expected_revision=prior["revision"])
    assert c.report()["run"] == new["run"]


@pytest.mark.parametrize("bad", ["foreign", "revision", "boolean", "reference", "scope", "live"])
def test_new_run_requires_bound_request_and_preserves_store_on_failure(tmp_path, bad):
    c, old = setup(tmp_path)
    old = submit(c, old)
    request = restart_request(old)
    if bad == "foreign": request["replace_run"] = "other"
    elif bad == "revision": request["expected_revision"] += 1
    elif bad == "boolean": request["expected_revision"] = True
    elif bad == "reference": request["request_reference"] = ""
    elif bad == "scope": request["scope"]["paths"]["product"] = ["../escape"]
    else:
        # A pre-existing process cannot be abandoned by a new-run request.
        event = {"hook_event_name": "PostToolUse", "tool_name": "exec_command",
                 "tool_input": {}, "tool_response": {"session_id": "running"}}
        c.observe(event, old["run"])
    target = c.adapter.control_path(c.workspace, c.root)
    before = target.read_bytes()
    with pytest.raises(w.Refusal): c.start(request)
    assert target.read_bytes() == before


def test_conflicting_start_does_not_silently_return_previous_run(tmp_path):
    c, old = setup(tmp_path)
    request = restart_request(old)
    request.pop("replace_run")
    with pytest.raises(w.Refusal, match="replace-run"): c.start(request)
    assert c.report()["run"] == old["run"]


def test_failed_replacement_write_keeps_old_run(tmp_path, monkeypatch):
    c, state = setup(tmp_path)
    state = submit(c, state)
    target = c.adapter.control_path(c.workspace, c.root)
    before = target.read_bytes()
    def failed(*args, **kwargs):
        raise OSError("simulated publication failure")
    monkeypatch.setattr(c, "_write", failed)
    with pytest.raises(OSError): c.start(restart_request(state))
    assert target.read_bytes() == before


def test_sealed_diagnostic_can_be_polled_or_interrupted_but_not_sent_code(tmp_path):
    c, state = setup(tmp_path)
    state = submit(c, state)
    event = command("rg", "-n", "recovery", ".")
    c.guard(event, state["run"])
    event.update(hook_event_name="PostToolUse", tool_response={"session_id": 88})
    c.observe(event, state["run"])
    for chars in ("", "\x03"):
        c.guard({"tool_name": "write_stdin", "tool_input": {"session_id": 88, "chars": chars}}, state["run"])
    with pytest.raises(w.Refusal):
        c.guard({"tool_name": "write_stdin", "tool_input": {"session_id": 88, "chars": "touch app.py\n"}}, state["run"])
    with pytest.raises(w.Refusal, match="Live tool processes"):
        c.start(restart_request(state))
    event["tool_response"]["exit_code"] = 0
    c.observe(event, state["run"])
    assert c.start(restart_request(state))["run"] != state["run"]


def test_bootstrap_recovery_cannot_overwrite_sealed_evidence(tmp_path):
    c, state = setup(tmp_path)
    state = submit(c, state)
    # A sealed path under bootstrap must not get the fresh-proposal exception.
    path = ".taskplane/bootstrap/sealed.md"
    (tmp_path / path).parent.mkdir()
    (tmp_path / path).write_text("evidence")
    packet = w.current(state)["packet"]
    packet["manifest"][path] = "stored digest"
    from taskplane.workflow_local import bootstrap_write
    assert not bootstrap_write(c.workspace, {"tool_name": "Write", "tool_input": {"path": path}}, state)


@pytest.mark.parametrize("text", ["Changes requested", "Rejected", "Cancelled"])
@pytest.mark.parametrize("drift", ["out_of_scope", "evidence"])
def test_negative_response_records_without_approving_changed_source(tmp_path, text, drift):
    c, state = setup(tmp_path)
    state = submit(c, state)
    if drift == "out_of_scope": (tmp_path / "upload.zip").write_bytes(b"package")
    else: (tmp_path / "product.json").write_text("{}")
    value = decision(state, text=text)
    result = decide(c, state, value)
    assert result["decisions"][value["event_id"]]["choice"] == value["choice"]
    assert result["source_baseline"] == state["source_baseline"]
    assert w.current(result)["decision"] == value["choice"]
    assert decide(c, state, value) == result
    with pytest.raises(w.Refusal):
        c.apply("advance", state["run"], expected_revision=result["revision"], phase="design")


@pytest.mark.parametrize("drift", ["out_of_scope", "evidence"])
def test_approval_still_refuses_drift(tmp_path, drift):
    c, state = setup(tmp_path)
    state = submit(c, state)
    if drift == "out_of_scope": (tmp_path / "upload.zip").write_bytes(b"package")
    else: (tmp_path / "product.json").write_text("{}")
    with pytest.raises(w.Refusal): decide(c, state)
    assert not c.report()["decisions"]


@pytest.mark.parametrize("drift", [False, True])
def test_recovery_controls_and_diagnostics_pass_through_declared_hook(tmp_path, drift):
    c, state = setup(tmp_path)
    state = submit(c, state)
    if drift: (tmp_path / "product.json").write_text("{}")
    events = [
        runtime("flow", "start", "--workspace", str(tmp_path), "--replace-run", state["run"],
                "--expected-revision", str(state["revision"]), "--scope", ".taskplane/bootstrap/new-scope.json",
                "--request-reference", "conversation/new-run"),
        runtime("flow", "--help"), runtime("version"), runtime("flow", "diagnose", "--workspace", str(tmp_path)),
        {"tool_name": "mcp__codex_app__uninstall_plugin", "tool_input": {"plugin": "taskplane"}},
        command("cat", str(Path(h.__file__).parents[1] / "docs/cli-reference.md")),
        command("rg", "-n", "recovery", "taskplane"), command("pwd"),
        {"tool_name": "apply_patch", "tool_input": {"input":
            "*** Begin Patch\n*** Add File: .taskplane/bootstrap/new-scope.json\n+{}\n*** End Patch"}},
    ]
    for event in events:
        event.update(hook_event_name="PreToolUse", cwd=str(tmp_path), session_id="root")
        result = flow.hook(event, governor=c)
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"
    assert not c.report()["decisions"]
    for event in [command("cat", "product.json", ";", "touch", "app.py"),
                  command("rg", "--pre=touch", "pattern"), command("rg", "--hostname-bin", "touch", "pattern"),
                  command("python3", "-c", "print('opaque')"),
                  {"tool_name": "Write", "tool_input": {"path": "app.py"}},
                  {"tool_name": "Write", "tool_input": {"path": ".taskplane/bootstrap/../../app.py"}}]:
        with pytest.raises(w.Refusal): c.guard(event, state["run"])


@pytest.mark.parametrize("phase", w.PHASES)
def test_replacement_is_available_at_every_delivery_checkpoint(tmp_path, phase):
    c, state = setup(tmp_path)
    for current in w.PHASES:
        state = submit(c, state)
        if current == phase: break
        state = decide(c, state, decision(state, event="human-" + current))
        state = c.apply("advance", state["run"], expected_revision=state["revision"],
                        phase=w.PHASES[w.PHASES.index(current) + 1])
    replacement = restart_request(state)
    c.guard(runtime("flow", "start", "--workspace", str(tmp_path), "--replace-run", state["run"],
                    "--expected-revision", str(state["revision"])), state["run"])
    fresh = c.start(replacement)
    assert fresh["decisions"] == {} and w.current(fresh)["phase"] == "product"
    assert c.report(state["run"])["visits"] == state["visits"]


def exercise_declared_recovery(workspace, root, entry="product"):
    """Use the shipped hook commands and CLI, including in an extracted package."""
    from taskplane.tests.test_native_workflow_cli import create, output
    workspace.mkdir(parents=True)
    create(workspace)
    hooks = json.loads((root / "hooks/hooks.json").read_text())["hooks"]
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CODEX_", "CLAUDE_", "TASKPLANE_", "PLUGIN_ROOT"))}
    env.update(PLUGIN_ROOT=str(root), CLAUDE_PLUGIN_ROOT=str(root), CODEX_THREAD_ID="root",
               PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])

    def hook(name, **payload):
        event = {"hook_event_name": name, "cwd": str(workspace), "thread_id": "root", **payload}
        declared = hooks[name][0]["hooks"][0]["commandWindows" if os.name == "nt" else "command"]
        result = subprocess.run(declared, shell=True, cwd=workspace, env=env,
                                input=json.dumps(event), text=True, capture_output=True)
        assert result.returncode == 0, (result.stdout, result.stderr)
        return json.loads(result.stdout)

    def run(*args, code=0):
        launcher = ["py", "-3"] if os.name == "nt" else [sys.executable]
        argv = [*launcher, str(root / "taskplane/tp.py"), "flow", *args, "--full", "--workspace", str(workspace)]
        guarded = hook("PreToolUse", tool_name="exec_command", tool_input={"cmd": shlex.join(argv)})
        assert guarded.get("hookSpecificOutput", {}).get("permissionDecision") != "deny", guarded
        result = subprocess.run(argv, cwd=workspace, env=env, text=True, capture_output=True)
        assert result.returncode == code, (result.stdout, result.stderr)
        hook("PostToolUse", tool_name="exec_command", tool_input={"cmd": shlex.join(argv)},
             tool_response={"exit_code": result.returncode})
        return json.loads(result.stdout)

    hook("SessionStart")
    run("activate", "--phase", "tp-" + entry, "--request-reference", "test/initial-request")
    state = run("start", "--standalone", "--phase", entry, "--scope", ".taskplane/scope.json",
                "--request-reference", "test/initial-request")["workflow"]
    target = output(workspace, state)
    state = run("submit", "--output", target, "--tasks", "tasks.json", "--expected-revision",
                str(state["revision"]))["workflow"]
    assert run("diagnose")["limits"]["source_files"] == 20000
    assert hook("PreToolUse", tool_name="mcp__codex_app__uninstall_plugin",
                tool_input={"plugin":"taskplane"}).get("hookSpecificOutput",{}).get("permissionDecision") != "deny"
    # The exact incident: an upload and Finder metadata appear after sealing.
    (workspace / "dist").mkdir()
    (workspace / "dist/upload.zip").write_bytes(b"user requested upload")
    (workspace / ".DS_Store").write_bytes(b"Finder metadata")
    denied = hook("PreToolUse", tool_name="Write", tool_input={"path": "app.py"})
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    negative = decision(state, text="Changes requested")
    state = run("decide", "--expected-revision", str(state["revision"]),
                "--decision-json", json.dumps(negative))["workflow"]
    assert negative["event_id"] in state["decisions"]
    # Restart after additional evidence drift; restart must not require old approval.
    (workspace / target).write_text("{}")
    args = ["start", "--replace-run", state["run"], "--expected-revision", str(state["revision"]),
            "--scope", ".taskplane/scope.json", "--request-reference", "test/new-run-request"]
    fresh_report = run(*args)
    fresh = fresh_report["workflow"]
    assert fresh["run"] != state["run"] and fresh["revision"] == 0 and not fresh["decisions"]
    assert fresh["phase"] == "product" and len(fresh["visits"]) == 7
    assert fresh_report["harness"]["entry"] == "tp-go"
    assert fresh_report["harness"]["presentation"] is None
    assert run(*args)["workflow"]["run"] == fresh["run"]
    selection = json.loads((workspace / ".taskplane/dashboard.selection.json").read_text())
    assert selection["run"] == fresh["run"] and selection["revision"] == 0
    prior = run("report", "--run", state["run"])
    assert prior["workflow"]["status"] == "superseded" and prior["historical"]
    assert not prior["workflow"]["pending_checkpoint"]
    # The new task is actually usable, but cannot skip its Product acceptance.
    target = output(workspace, fresh)
    fresh = run("submit", "--output", target, "--tasks", "tasks.json",
                "--expected-revision", "0")["workflow"]
    refusal = run("advance", "--phase", "design", "--expected-revision", str(fresh["revision"]), code=2)
    assert refusal["reason"] == "approval_required"
    return fresh


@pytest.mark.parametrize("entry", ["product", "design", "engineering"])
def test_declared_codex_hooks_recover_full_and_standalone_runs(tmp_path, entry):
    exercise_declared_recovery((tmp_path / "workspace").resolve(), Path(h.__file__).parents[1], entry)


def test_extracted_codex_package_recovery(tmp_path):
    root = Path(h.__file__).parents[1]
    result = subprocess.run([sys.executable, str(root / "scripts/package_openai.py"),
                             "--output-dir", str(tmp_path / "packages")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    package = json.loads(result.stdout)
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(package["archive"]) as archive:
        archive.extractall(extracted)
    for name in ("workflow_host.py", "workflow_local.py", "flow.py"):
        assert (extracted / "taskplane" / name).read_bytes() == (root / "taskplane" / name).read_bytes()
    exercise_declared_recovery((tmp_path / "workspace").resolve(), extracted.resolve())
