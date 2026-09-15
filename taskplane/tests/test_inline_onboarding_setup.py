"""Real setup submissions, safe persistence, and native/legacy chat delivery."""
from __future__ import annotations

from argparse import Namespace
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard
import tp as cli
import run_store
import storage
from taskplane import settings


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "project"
    ws.mkdir()
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "--allow-empty", "-qm", "base"], cwd=ws, check=True)
    monkeypatch.setenv("TASKPLANE_HOME", str(ws / ".taskplane"))
    monkeypatch.setenv("TASKPLANE_MANAGED_HOOK_POLICY", "supported")
    monkeypatch.setattr(cli, "_install_context", lambda: "personal")
    return str(ws)


def submission(ws, **changes):
    return {"schema": "taskplane.onboarding-setup/v1", "workspace": ws,
            "execution_storage": "keep-existing", **changes}


def test_apply_writes_only_changed_context_and_preserves_current_run(workspace):
    identity = storage.resolve_repository_identity(workspace)
    layout = storage.resolve_layout(identity, home=os.environ["TASKPLANE_HOME"], run_id="context-save")
    store = run_store.RunStore(home=layout.home)
    store.create(identity, run_id="context-save", checkout=workspace,
                 host={"kind": "codex"}, target={"kind": "repository"})
    effective = settings.load_settings(environment={})
    store.save_workflow("context-save", {"goal": "preserve this goal", "step": "plan",
        "settings": effective.to_dict(), "settings_digest": effective.digest})
    locator = Path(storage.write_workspace_locator(workspace, identity=identity,
                                                    layout=layout, run_id="context-save"))
    manifest = Path(layout.run_root, "manifest.json")
    before = manifest.read_bytes(), locator.read_bytes(), store.inspect("context-save")
    cfg = cli._onboarding_configuration(workspace)
    context = Path(cfg["context"]["product"]["path"]).parent
    context.mkdir(parents=True)
    (context / "product.md").write_text("Original product\n")
    (context / "workflow.md").write_text("Original workflow\n")
    cfg = cli._onboarding_configuration(workspace)
    result = cli._apply_onboarding_setup(workspace, submission(workspace, context={
        "product": {"text": "Changed product\n", "expected_digest": cfg["context"]["product"]["digest"]}}))
    assert result["status"] == "applied"
    assert [row["field"] for row in result["context_saved"]] == ["product"]
    assert (context / "product.md").read_text() == "Changed product\n"
    assert (context / "workflow.md").read_text() == "Original workflow\n"
    assert (manifest.read_bytes(), locator.read_bytes(), store.inspect("context-save")) == before
    assert store.inspect("context-save")["workflow"]["goal"] == "preserve this goal"
    assert not Path(workspace, ".codex", "hooks.json").exists()


def test_stale_form_refuses_without_overwriting(workspace):
    cfg = cli._onboarding_configuration(workspace)
    product = Path(cfg["context"]["product"]["path"])
    product.parent.mkdir(parents=True)
    product.write_text("Changed after form render")
    with pytest.raises(ValueError, match="changed; refresh"):
        cli._apply_onboarding_setup(workspace, submission(workspace, context={
            "product": {"text": "Stale submitted change", "expected_digest": None}}))
    assert product.read_text() == "Changed after form render"


def test_symlink_knowledge_ancestor_refuses(workspace, tmp_path):
    cfg = cli._onboarding_configuration(workspace)
    knowledge = Path(cfg["knowledge_home"])
    target = tmp_path / "outside"
    target.mkdir()
    knowledge.parent.mkdir(parents=True, exist_ok=True)
    knowledge.symlink_to(target, target_is_directory=True)
    with pytest.raises((ValueError, storage.StorageIdentityError), match="symlink|symbolic links"):
        cli._apply_onboarding_setup(workspace, submission(workspace, context={
            "product": {"text": "must not escape", "expected_digest": None}}))
    assert not list(target.iterdir())


def test_launcher_refuses_project_symlink_with_valid_external_binding(workspace, tmp_path, monkeypatch):
    home = tmp_path / "existing-external-storage"
    identity = storage.resolve_repository_identity(workspace)
    layout = storage.resolve_layout(identity, home=str(home), run_id="external-binding")
    store = run_store.RunStore(home=str(home))
    store.create(identity, run_id="external-binding", checkout=workspace,
                 host={"kind": "codex"}, target={"kind": "repository"})
    locator = Path(storage.write_workspace_locator(workspace, identity=identity,
        layout=layout, run_id="external-binding"))
    before = locator.read_bytes(), store.inspect("external-binding")
    target = tmp_path / "outside-launcher-location"
    target.mkdir()
    Path(workspace, ".taskplane").symlink_to(target, target_is_directory=True)
    monkeypatch.setenv("TASKPLANE_HOME", str(home))
    with pytest.raises(storage.StorageIdentityError, match="without symlinks"):
        cli._install_codex_hooks(workspace)
    assert not list(target.iterdir())
    assert (locator.read_bytes(), store.inspect("external-binding")) == before


def test_launcher_never_follows_existing_temporary_or_final_symlink(workspace, tmp_path, monkeypatch):
    launcher_home = Path(workspace, ".taskplane")
    launcher_home.mkdir()
    target = tmp_path / "must-remain-unchanged.py"
    target.write_text("original outside content")
    occupied = launcher_home / ".codex-hook-occupied.tmp"
    occupied.symlink_to(target)
    launcher = launcher_home / "codex-hook.py"
    launcher.symlink_to(target)
    monkeypatch.setattr(tempfile, "_get_candidate_names", lambda: iter(("occupied", "fresh")))
    result = cli._install_codex_hooks(workspace)
    assert result["ok"] is True
    assert target.read_text() == "original outside content"
    assert occupied.is_symlink()
    assert not launcher.is_symlink()
    assert "Generated locally by taskplane onboarding" in launcher.read_text()
    assert not (launcher_home / ".codex-hook-fresh.tmp").exists()


@pytest.mark.parametrize("change", [
    {"command": "touch /tmp/unwanted"}, {"environment": {"TASKPLANE_HOME": "/other"}},
    {"knowledge_plan": "whatever"}, {"initialize": "true"},
    {"context": {"../outside": {"text": "bad", "expected_digest": None}}},
    {"context": {"product": {"text": "bad"}}},
    {"context": {"product": {"text": "x" * 12001, "expected_digest": None}}},
    {"workspace": "/somewhere-else"},
])
def test_submission_rejects_unbounded_fields(workspace, monkeypatch, change):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(submission(workspace, **change))))
    with pytest.raises(ValueError):
        cli._read_onboarding_setup("-", workspace)


def test_json_stdin_treats_malicious_text_as_data(workspace, monkeypatch):
    hostile = '</textarea><script>alert("bad")</script>\n`$(touch /tmp/no)` & \'quoted\''
    value = submission(workspace, context={"product": {"text": hostile, "expected_digest": None}})
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(value)))
    decoded = cli._read_onboarding_setup("-", workspace)
    assert decoded == value
    cli._apply_onboarding_setup(workspace, decoded)
    assert Path(cli._onboarding_configuration(workspace)["context"]["product"]["path"]).read_text() == hostile


def test_cli_submission_uses_workspace_from_another_cwd(workspace, tmp_path):
    value = submission(workspace, context={
        "product": {"text": "Saved through the actual CLI", "expected_digest": None}})
    environment = dict(os.environ)
    environment.pop("TASKPLANE_HOME", None)
    environment.pop("TASKPLANE_HOOK_PATH", None)
    result = subprocess.run([
        sys.executable, str(Path(cli.__file__).resolve()), "onboard", "--workspace", workspace,
        "--apply-setup", "-", "--json"], input=json.dumps(value), text=True,
        capture_output=True, cwd=tmp_path, env=environment)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["setup_result"]["status"] == "applied"
    assert report["configuration"]["execution_home"] == str(Path(workspace, ".taskplane"))
    assert Path(report["configuration"]["context"]["product"]["path"]).read_text() == "Saved through the actual CLI"
    assert not Path(tmp_path, ".taskplane").exists()


def test_refusal_reports_completed_storage_selection(workspace, monkeypatch, capsys):
    values = submission(workspace, context={"product": {"text": "stale", "expected_digest": "a" * 64}})
    selection = {"status": "selected", "home": str(Path(workspace, ".taskplane"))}
    args = Namespace(workspace=workspace, setup_values=values, storage_selection=selection,
                     json=True, out=None)
    assert cli.cmd_onboard(args) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["setup_result"]["status"] == "refused"
    assert result["storage_selection"] == selection
    assert result["setup_result"]["run_preserved"] is True


def test_native_observation_requires_no_project_hook_rows(workspace, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(Path(workspace, "codex")))
    monkeypatch.setenv("CODEX_THREAD_ID", "setup-session")
    monkeypatch.setenv("TASKPLANE_NATIVE_HOOKS_LOADED", "supported")
    monkeypatch.setenv("TASKPLANE_NATIVE_ENFORCEMENT", "supported")
    cli._install_codex_hooks(workspace)
    Path(cli.tp.kb_root(workspace), "context").mkdir(parents=True)
    cli.host_caps.record_runtime_hook_receipt(
        os.environ["TASKPLANE_HOME"], hook_path="native", event={
            "session_id": "setup-session", "hook_event_name": "PreToolUse", "tool_use_id": "setup-call", "cwd": workspace})
    report = cli._onboard_report(workspace)
    assert report["host_capabilities"]["effective_path"]["value"] == "native_effective"
    assert report["ready"] is True
    assert report["codex_hooks"]["registration"] == "plugin"
    assert not Path(workspace, ".codex", "hooks.json").exists()


class Fragment(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.script = ""
        self.root = {}
        self.in_script = False
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "section":
            self.root = attrs
        if tag == "script":
            self.in_script = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_script = False

    def handle_data(self, data):
        if self.in_script:
            self.script += data


def representative_report():
    return {"workspace": '/tmp/Project "quoted" <x>', "host": "codex", "ready": False,
        "has_context": True, "next_action": "start_new_session",
        "codex_hooks": {"launcher_ready": True},
        "configuration": {"execution_home": "/tmp/project/.taskplane", "knowledge_plan": "personal",
            "context": {"product": {"label": "Product", "text": '</textarea><script>evil()</script>',
                "digest": "a" * 64, "path": "/tmp/project/.taskplane/knowledge/context/product.md"}}},
        "checks": [{"id": "loaded", "label": "Native hooks", "ok": False, "detail": "unknown",
                    "hint": "Enable TaskPlane in this task."}],
        "settings": {"stages": {"product": {"model": None, "reasoning": "high"}}}}


@pytest.mark.parametrize("mode", ["native", "legacy", "unavailable", "rejected"])
def test_rendered_form_sends_actual_values_and_fails_honestly(mode):
    fragment = dashboard.render_onboarding(representative_report())
    assert '<script>evil()' not in fragment
    assert 'document.currentScript' not in fragment
    assert 'value="/tmp/Project &quot;quoted&quot; &lt;x&gt;"' in fragment
    parsed = Fragment(fragment)
    node = shutil.which("node")
    assert node, "Node is required for the inline setup interaction test"
    harness = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const input=JSON.parse(fs.readFileSync(0,'utf8'));let submit,calls=[],refresh,continuation,modelChange,reasoningChange;
const status={textContent:''},manual={value:''},buttons=[{disabled:false},{disabled:false}];
const changed={dataset:{context:'product',digest:'a'.repeat(64)},defaultValue:'before',value:'</textarea><script>evil()</script> $(do not execute)'};
const unchanged={dataset:{context:'workflow',digest:'b'.repeat(64)},defaultValue:'same',value:'same'};
const model={dataset:{stage:'product',field:'model'},value:'inherit'};
const reasoning={dataset:{stage:'product',field:'reasoning'},value:'high'};
const fallback={hidden:true,querySelector:()=>manual};
const form={elements:{execution_storage:{value:'project'},knowledge_plan:{value:'keep-existing'},
common_model:{value:'gpt-5.5',addEventListener:(n,f)=>modelChange=f},
common_reasoning:{value:'medium',addEventListener:(n,f)=>reasoningChange=f},
initialize:{checked:false},install_launcher:{checked:true}},reportValidity:()=>true,
addEventListener:(name,fn)=>submit=fn,querySelectorAll:s=>({'[data-stage]':[model,reasoning],
'[data-field=model]':[model],'[data-field=reasoning]':[reasoning],
'textarea[data-context]':[changed,unchanged]}[s]||[])};
const root={dataset:{workspace:input.workspace,settingsDigest:'c'.repeat(64),ready:'false',nextDetail:'enable native hooks'},
querySelector:(s)=>({'form':form,'[data-status]':status,'[data-fallback]':fallback,
'[data-refresh]':{addEventListener:(n,f)=>refresh=f},'[data-continue]':{addEventListener:(n,f)=>continuation=f}}[s]),
querySelectorAll:()=>buttons};
const sandbox={document:{getElementById:(id)=>{assert.equal(id,input.id);return root},
currentScript:{parentElement:{}}},window:{}};
if(input.mode==='native')sandbox.window.openai={sendFollowUpMessage:async data=>calls.push(data.prompt)};
if(input.mode==='legacy')sandbox.sendPrompt=async prompt=>calls.push(prompt);
if(input.mode==='rejected')sandbox.window.openai={sendFollowUpMessage:async()=>{throw new Error('blocked')}};
vm.runInNewContext(input.script,sandbox);
modelChange.call(form.elements.common_model);reasoningChange.call(form.elements.common_reasoning);
assert.equal(model.value,'gpt-5.5');assert.equal(reasoning.value,'medium');
// An individual Advanced edit overrides the common choice before submission.
reasoning.value='high';submit({preventDefault(){}});
setImmediate(()=>{assert(buttons.every(b=>!b.disabled));
if(input.mode==='native'||input.mode==='legacy'){
assert.equal(calls.length,1);assert(status.textContent.includes('Waiting'));assert(fallback.hidden);
const value=JSON.parse(calls[0].slice(calls[0].indexOf('\n')+1));
assert.equal(value.workspace,input.workspace);assert.equal(value.execution_storage,'project');
assert.equal(value.install_launcher,true);assert.equal(value.initialize,false);
assert.equal(value.context.product.text,changed.value);assert(!value.context.workflow);
assert.equal(value.settings.expected_digest,'c'.repeat(64));
assert.deepEqual(value.settings.stages,{product:{model:'gpt-5.5',reasoning:'high'}});
}else{assert.equal(calls.length,0);assert(!fallback.hidden);assert(status.textContent.includes('Nothing has been saved'));
assert(manual.value.includes('onboard --apply-setup'));}
assert(!status.textContent.includes('Setup is complete'));console.log('ok');});
'''
    result = subprocess.run([node, "-e", harness], input=json.dumps({
        "mode": mode, "script": parsed.script, "id": parsed.root["id"],
        "workspace": representative_report()["workspace"]}), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
