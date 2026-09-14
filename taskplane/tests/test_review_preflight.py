import base64
import datetime
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))

import review  # noqa: E402
import review_evidence  # noqa: E402
import tp as taskplane_cli  # noqa: E402
from taskplane.tests.native_meter_support import attach_native_counter  # noqa: E402
from taskplane.tests.test_review_refusals import _run  # noqa: E402


@pytest.fixture
def opened_cli_review(tmp_path, monkeypatch):
    """Open a real CLI review; only host readiness is simulated by _run."""
    monkeypatch.setenv("CODEX_THREAD_ID", "review-fixture-session")
    taskplane_cli.tp.record_entry_tools(["Read", "Grep", "Glob", "Write"])
    workspace = tmp_path / "source"
    workspace.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "test@example.com"],
                 ["config", "user.name", "Test"]):
        subprocess.run(["git", *args], cwd=workspace, check=True)
    source = workspace / "service.py"
    source.write_text("def value():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "service.py"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=workspace, check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=workspace,
                                   text=True).strip()
    source.write_text("def value():\n    return 2\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "change"], cwd=workspace, check=True)

    rc, stdout, stderr = _run("review", "start", "HEAD", "--base", base,
                              "--workspace", str(workspace))
    assert stdout, (rc, stderr)
    opened = json.loads(stdout)
    assert rc == 2, (stdout, stderr)
    assert opened["status"] == "needs_user"
    assert opened["slots"] == []
    return workspace, opened


@pytest.mark.parametrize("selection", ["static", "dynamic", "dynamic-render"])
def test_cli_review_emits_usable_signed_worker_startup(
        tmp_path, opened_cli_review, selection, monkeypatch):
    workspace, opened = opened_cli_review
    monkeypatch.delenv("TASKPLANE_TASK", raising=False)
    parent = Path(taskplane_cli.tp.active_contract_path(str(workspace)))
    parent_before = parent.read_bytes()

    rc, stdout, stderr = _run("review", "option", selection, "--run-id",
                              opened["run_id"], "--workspace", str(workspace))
    ready = json.loads(stdout)
    assert rc == 0, (stdout, stderr)
    assert ready["status"] == "ready"
    assert len(ready["slots"]) in {4, 5}
    state = review._load_state(str(workspace), opened["run_id"])
    assert state["manifest"]["slots"] == ready["slots"]

    # Retrying the same choice re-emits startup for the same leases, so an
    # interrupted or previously unbound review can continue without restarting.
    rc, stdout, stderr = _run("review", "option", selection, "--run-id",
                              opened["run_id"], "--workspace", str(workspace))
    assert rc == 0, (stdout, stderr)
    retried = json.loads(stdout)
    assert retried["review_execution"] == ready["review_execution"]
    for original, current in zip(ready["slots"], retried["slots"], strict=True):
        assert current["lease"] == original["lease"]
        assert current["brief"] == original["brief"]
        assert current["contract_bootstrap"]["expected"] == \
            original["contract_bootstrap"]["expected"]

    for slot in ready["slots"]:
        bootstrap = slot["contract_bootstrap"]
        assert bootstrap["activation_order"] == "orchestrator_before_subagent_start"
        assert bootstrap["workspace"] == str(workspace.resolve())
        assert bootstrap["expected"]["run_id"] == opened["run_id"]
        assert bootstrap["expected"]["task_id"] == opened["contract"]["task_id"]
        result = subprocess.run(bootstrap["command_argv"], cwd=tmp_path,
                                capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        activated = json.loads(result.stdout)
        assert activated["task_slot"] == bootstrap["task_slot"]
        assert activated["lease_fingerprint"] == slot["lease"]["fingerprint"]
        active_path = Path(taskplane_cli.tp.active_contract_path(
            str(workspace), bootstrap["task_slot"]))
        active = json.loads(active_path.read_text())
        assert active["read_only"] is True
        assert active["write_allow"] == [slot["result_path"]]
        assert active["budget"]["max_tokens"] <= 100_000
        assert active["worker_scoped"] is True
        assert active["worker_lifecycle"]["status"] == "pending"
        assert activated["worker_binding"] == {
            "event": "SubagentStart", "status": "pending",
            "task_name": bootstrap["expected"]["worker_identity"],
        }

        # Simulate only the native Start event, with no inherited slot env.
        # The actual hook must select the signed child and register its lease.
        event = {
            "hook_event_name": "SubagentStart", "cwd": str(tmp_path),
            "session_id": "review-fixture-session", "turn_id": "review-test-turn",
            "agent_id": "child-" + slot["slot_id"],
            "task_name": bootstrap["expected"]["worker_identity"],
            "agent_type": bootstrap["expected"]["worker_identity"],
        }
        with monkeypatch.context() as native:
            native.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
            rc, stdout, stderr = _run("subagent-start")
        assert rc == 0, (stdout, stderr)
        bound = taskplane_cli.tp.load_active_for_event(str(workspace), event)
        assert bound["task_slot"] == bootstrap["task_slot"]
        assert bound["worker_lifecycle"]["owner"]["agent_id"] == event["agent_id"]
        assignment = json.loads(Path(review._producer_assignment_path(
            str(workspace), slot["lease"]["fingerprint"])).read_text())
        assert assignment["producer_child_id"] == event["agent_id"]
        assert assignment["contract_task_slot"] == bootstrap["task_slot"]
        assert taskplane_cli.tp.load_active(str(workspace))["task_id"] == \
            opened["contract"]["task_id"]

        # A replay cannot reset an already-bound native child to pending.
        replay = subprocess.run(bootstrap["command_argv"], cwd=tmp_path,
                                capture_output=True, text=True, check=False)
        assert replay.returncode == 0, replay.stdout + replay.stderr
        assert json.loads(active_path.read_text()) == bound

    # Child activation must not replace the parent or manufacture results.
    assert parent.read_bytes() == parent_before
    assert all(not Path(slot["result_path"]).exists() for slot in ready["slots"])


def test_native_review_dispatch_does_not_require_a_checkout_launcher(
        opened_cli_review):
    workspace, opened = opened_cli_review
    launcher = workspace / ".taskplane" / "codex-hook.py"
    assert not launcher.exists()
    rc, stdout, stderr = _run("review", "option", "static", "--run-id",
                              opened["run_id"], "--workspace", str(workspace))
    assert rc == 0, (stdout, stderr)
    assert not launcher.exists()
    assert not (workspace / ".codex" / "hooks.json").exists()
    assert subprocess.check_output(["git", "status", "--porcelain", "--",
                                    ".taskplane", ".codex", "service.py"],
                                   cwd=workspace, text=True) == ""


def test_native_review_dispatch_preserves_project_hook_bytes(
        opened_cli_review):
    workspace, opened = opened_cli_review
    hooks = workspace / ".codex" / "hooks.json"
    hooks.parent.mkdir()
    hooks.write_text(json.dumps({"hooks": taskplane_cli._codex_hook_rows()}), encoding="utf-8")
    original = hooks.read_bytes()
    rc, stdout, stderr = _run("review", "option", "static", "--run-id",
                              opened["run_id"], "--workspace", str(workspace))
    assert rc == 0, (stdout, stderr)
    assert hooks.read_bytes() == original
    assert not (workspace / ".taskplane" / "codex-hook.py").exists()


@pytest.mark.parametrize("mutation,reason", [
    ("signature", "signature is invalid"),
    ("worker_identity", "worker_identity identity mismatches worker"),
    ("lease_fingerprint", "lease_fingerprint identity mismatches lease"),
    ("workspace", "authority"),
])
def test_cli_review_rejects_altered_startup(
        tmp_path, opened_cli_review, mutation, reason):
    workspace, opened = opened_cli_review
    rc, stdout, stderr = _run("review", "option", "static", "--run-id",
                              opened["run_id"], "--workspace", str(workspace))
    assert rc == 0, (stdout, stderr)
    slot = json.loads(stdout)["slots"][0]
    bootstrap = slot["contract_bootstrap"]
    command = list(bootstrap["command_argv"])
    other_workspace = tmp_path / "other"
    other_workspace.mkdir()
    if mutation == "workspace":
        command[command.index("--workspace") + 1] = str(other_workspace)
    else:
        flag = "--signed-action" if mutation == "signature" else "--expected-identity"
        index = command.index(flag) + 1
        value = json.loads(base64.urlsafe_b64decode(command[index] + "=" * (-len(command[index]) % 4)))
        value[mutation] = "0" * 64
        command[index] = base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    result = subprocess.run(command, cwd=tmp_path, capture_output=True,
                            text=True, check=False)
    assert result.returncode != 0
    assert reason in result.stdout + result.stderr
    for root in (workspace, other_workspace):
        assert not Path(taskplane_cli.tp.active_contract_path(
            str(root), bootstrap["task_slot"])).exists()
    assert not Path(slot["result_path"]).exists()


def test_cli_review_refuses_missing_parent_before_recording_choice(opened_cli_review):
    workspace, opened = opened_cli_review
    Path(taskplane_cli.tp.active_contract_path(str(workspace))).unlink()
    before = review._load_state(str(workspace), opened["run_id"])
    rc, stdout, stderr = _run("review", "option", "static", "--run-id",
                              opened["run_id"], "--workspace", str(workspace))
    assert rc == 1, (stdout, stderr)
    assert "requires its read-only parent contract" in stdout
    assert review._load_state(str(workspace), opened["run_id"]) == before


def test_review_choice_retry_preserves_evidence_and_rejects_different_consent():
    workspace, opened = _start_review_without_execution_choice()
    review.configure_review_execution(
        workspace, selection="dynamic", run_id=opened["run_id"])
    review.record_review_execution(
        workspace, kind="dynamic_validation", status="failed",
        detail="fixture process failure", run_id=opened["run_id"])
    before = review._load_state(workspace, opened["run_id"])
    retried = review.configure_review_execution(
        workspace, selection="dynamic", run_id=opened["run_id"])
    assert retried == before["manifest"]
    assert retried["review_execution"]["dynamic_validation"]["status"] == "failed"
    for kwargs in ({"selection": "static"},
                   {"selection": "dynamic", "by": "another-human"},
                   {"selection": "dynamic", "approval_receipt":
                    before["review_execution"]["approval_receipt"]}):
        with pytest.raises(review.ReviewKernelError):
            review.configure_review_execution(
                workspace, run_id=opened["run_id"], **kwargs)
    assert review._load_state(workspace, opened["run_id"]) == before


def _host_receipt(*, action_id, response, actor="human", run_id="run-1"):
    receipt_id = f"receipt-{action_id}-{response}"
    owner_id = "b" * 64
    return review._HostObservedReviewAction(
        source="codex-session:user-message",
        receipt_id=receipt_id,
        run_id=run_id, action_id=action_id, response=response, actor=actor,
        authority=review._REVIEW_HOST_ACTION_AUTHORITY, owner_id=owner_id,
        action_digest=review._review_receipt_digest(
            owner_id, run_id, action_id, response, receipt_id, actor))


def _host_execution_receipt(*, action_id, kind="dynamic_validation",
                            run_id="run-1"):
    receipt_id = f"process-{action_id}-{kind}"
    owner_id = "c" * 64
    result_sha256 = "a" * 64
    return review._HostObservedReviewExecution(
        source="codex-session:tool-result",
        receipt_id=receipt_id, run_id=run_id,
        action_id=action_id, kind=kind, tool_name="exec",
        result_sha256=result_sha256, result_bytes=42, exit_code=0,
        authority=review._REVIEW_HOST_EXECUTION_AUTHORITY, owner_id=owner_id,
        action_digest=review._review_receipt_digest(
            owner_id, run_id, action_id, kind, receipt_id, result_sha256, 0))


def _forged_receipt(*, action_id, response, actor="human", run_id="run-1"):
    return {
        "schema": "taskplane.review-user-action-receipt/v1",
        "host_observed": True, "source": "caller-json",
        "receipt_id": f"forged-{action_id}-{response}", "run_id": run_id,
        "action_id": action_id, "response": response, "actor": actor,
    }


def _write_host_transcript(tmp_path, monkeypatch, host, *, prompt,
                           command="npm test", exit_code=0,
                           run_id="run-cross-host",
                           execution_action=None,
                           kind="dynamic_validation"):
    action_receipt = f"{host}-approval"
    process_receipt = f"{host}-process"
    if host == "codex":
        home = tmp_path / "codex"
        instant = datetime.datetime(
            2026, 8, 16, tzinfo=datetime.timezone.utc)
        milliseconds = int(instant.timestamp() * 1000)
        prefix = f"{milliseconds:012x}"
        thread_id = (f"{prefix[:8]}-{prefix[8:]}-7000-8000-"
                     "000000000001")
        path = (home / "sessions" / "2026" / "08" / "16" /
                f"rollout-2026-08-16T00-00-00-{thread_id}.jsonl")
        index = home / "session_index.jsonl"
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text(json.dumps({"id": thread_id}) + "\n",
                         encoding="utf-8")
        tool_input = command if execution_action is None else {
            "command": command,
            "taskplane_action": review._review_tool_action_binding(
                run_id, execution_action, kind),
        }
        rows = [
            {"type": "response_item", "payload": {
                "type": "message", "id": action_receipt, "role": "user",
                "content": [{"type": "input_text", "text": prompt}],
                "internal_chat_message_metadata_passthrough": {
                    "turn_id": "codex-approval-turn"}}},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call", "call_id": process_receipt,
                "name": "exec", "input": tool_input}},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call_output", "call_id": process_receipt,
                "output": {"structuredContent": {
                    "process_result": {"exit_code": exit_code}},
                           "output": "42 passed"}}},
        ]
        monkeypatch.setattr(
            review, "_canonical_host_root",
            lambda selected: str(home if selected == host else
                                 tmp_path / "absent-host"))
        monkeypatch.setenv("CODEX_HOME", str(tmp_path / "forged-codex"))
        monkeypatch.setenv("CODEX_THREAD_ID", "ambient-must-not-select")
        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    else:
        home = tmp_path / "claude"
        session_id = "claude-review-session"
        path = home / "projects" / "fixture" / f"{session_id}.jsonl"
        index = home / "history.jsonl"
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text(json.dumps({
            "sessionId": session_id, "project": "fixture",
        }) + "\n", encoding="utf-8")
        tool_input = {"command": command} if execution_action is None else {
            "command": command,
            "taskplane_action": review._review_tool_action_binding(
                run_id, execution_action, kind),
        }
        rows = [
            {"type": "user", "uuid": action_receipt,
             "sessionId": session_id,
             "message": {"role": "user", "content": prompt}},
            {"type": "assistant", "sessionId": session_id,
             "message": {"role": "assistant", "content": [{
                 "type": "tool_use", "id": process_receipt, "name": "Bash",
                 "input": tool_input}]}},
            {"type": "user", "sessionId": session_id,
             "message": {"role": "user", "content": [{
                 "type": "tool_result", "tool_use_id": process_receipt,
                 "content": {"structuredContent": {
                     "process_result": {"exit_code": exit_code}},
                             "output": "42 passed"},
                 "is_error": exit_code != 0}]}},
        ]
        monkeypatch.setattr(
            review, "_canonical_host_root",
            lambda selected: str(home if selected == host else
                                 tmp_path / "absent-host"))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "forged-claude"))
        monkeypatch.setenv("CLAUDE_SESSION_ID", "ambient-must-not-select")
        monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows),
                    encoding="utf-8")
    identity = thread_id if host == "codex" else session_id
    return (f"{host}:{identity}:{action_receipt}",
            f"{host}:{identity}:{process_receipt}")


def _start_review_without_execution_choice():
    ws = tempfile.mkdtemp(prefix="tp-review-preflight-")
    os.makedirs(os.path.join(ws, "src"))
    with open(os.path.join(ws, "src", "service.py"), "w",
              encoding="utf-8") as stream:
        stream.write("def changed():\n    return 2\n")
    opened = review.start_review(
        ws,
        target={"fingerprint": "target-1", "head": "abc123"},
        graph={
            "meta": {"scanned_head": "abc123",
                     "content_fingerprint": "graph-1"},
            "modules": {"src": {"files": ["src/service.py"]}},
            "edges": [],
        },
        impact={"touched": ["src"], "impacted": {},
                "total_impacted": 1, "unknown": []},
        diff={"files": ["src/service.py"],
              "changed_symbols": ["changed"],
              "patch_artifact": {"fingerprint": "diff-1"}},
        runnability={"summary": "available"},
        requirement={"id": "R-1", "text": "safe change"},
        acceptance=["works"], contracts=["contract:api"],
        task_type="review",
    )
    return ws, opened


def test_review_preflight_exposes_one_structured_choice_without_side_effects(
        monkeypatch, opened_cli_review):
    run_id = "b" * 32
    row = review.review_execution_preflight(run_id=run_id)
    assert row["schema"] == "taskplane.review-execution-preflight/v1"
    assert row["status"] == "needs_user"
    assert row["static_only"] is True
    assert row["side_effects_started"] is False
    assert [choice["response"] for choice in row["action"]["choices"]] == [
        "dynamic", "dynamic-render", "static"]
    assert row["action"]["choices"][0]["requires"] == [
        "dependency-install", "process-execution"]
    assert row["action"]["choices"][1]["requires"] == [
        "dependency-install", "process-execution", "browser-access"]
    commands = [choice["command"] for choice in row["action"]["choices"]]
    engine = os.path.realpath(os.path.join(os.path.dirname(review.__file__), "tp.py"))
    command_args = [[sys.executable, engine, "review", "option", mode, "--run-id", run_id]
                    for mode in ("dynamic", "dynamic-render", "static")]
    quote = subprocess.list2cmdline if os.name == "nt" else shlex.join
    assert commands == [quote(args) for args in command_args]

    root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    guidance_paths = [
        "agents/tp-product.md", "agents/tp-engineering.md",
        "skills/tp-product/SKILL.md", "skills/tp-engineering/SKILL.md",
        "docs/cli-reference.md",
    ]
    for relative in guidance_paths:
        with open(os.path.join(root, relative), encoding="utf-8") as stream:
            guidance = stream.read()
        posix_launcher = "python3 .taskplane/codex-hook.py review option "
        assert posix_launcher + "dynamic --run-id <run-id>" in guidance
        assert posix_launcher + "dynamic-render --run-id <run-id>" in guidance
        assert posix_launcher + "static --run-id <run-id>" in guidance
        windows_launcher = "py .taskplane/codex-hook.py review option "
        assert windows_launcher + "dynamic --run-id <run-id>" in guidance
        assert windows_launcher + "dynamic-render --run-id <run-id>" in guidance
        assert windows_launcher + "static --run-id <run-id>" in guidance
        assert "visuals.workflow_and_wave.inline.path" in guidance

    with monkeypatch.context() as platform:
        platform.setattr(review.os, "name", "nt")
        windows_commands = [
            choice["command"] for choice in
            review.review_execution_preflight(
                run_id=run_id)["action"]["choices"]]
    assert windows_commands == [subprocess.list2cmdline(args) for args in command_args]

    workspace, opened = opened_cli_review
    workspace = str(workspace)
    static_command = next(
        choice["command"] for choice in
        opened["review_execution"]["action"]["choices"]
        if choice["response"] == "static")
    assert not Path(workspace, ".taskplane/codex-hook.py").exists()
    executed = subprocess.run(
        static_command if os.name == "nt" else shlex.split(static_command), cwd=workspace, text=True,
        encoding="utf-8", errors="replace",
        capture_output=True, check=False)
    assert executed.returncode == 0, executed.stderr
    continued = json.loads(executed.stdout)
    assert continued["run_id"] == opened["run_id"]
    assert continued["status"] == "ready"
    assert not Path(workspace, ".taskplane/codex-hook.py").exists()
    assert continued["review_execution"]["selection"] == "static"
    assert continued["visuals"]["workflow_and_wave"]["inline"]["path"]
    assert review._load_state(workspace, opened["run_id"])["run_id"] == \
        opened["run_id"]


def test_review_dor_classifies_commit_claims_and_review_directives(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"],
                   cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path,
                   check=True)
    (tmp_path / "README.md").write_text(
        "Please review the codebase and identify issues. Consider:\n"
        "- Security vulnerabilities\n- Usability issues\n"
        "- Performance concerns at scale\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=tmp_path,
                   check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                   cwd=tmp_path, text=True, encoding="utf-8",
                                   errors="replace").strip()
    (tmp_path / "app.js").write_text("export const analytics = true;\n",
                                     encoding="utf-8")
    subprocess.run(["git", "add", "app.js"], cwd=tmp_path, check=True)
    subprocess.run([
        "git", "commit", "-q", "-m", "Add analytics and endpoints",
        "-m", "- Add AnalyticsSummary component\n- Add analytics API endpoint"],
        cwd=tmp_path, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                   cwd=tmp_path, text=True, encoding="utf-8",
                                   errors="replace").strip()

    dor = review.review_dor_evidence(
        str(tmp_path), {"merge_base": base, "head": head,
                        "changed_files": ["app.js"]})
    requested = review._directive_lens_ids(
        dor["review_directives"], __import__("lens").load_catalog())

    assert dor["status"] == "ready"
    assert dor["acceptance_source"] == "pr_commit_claims"
    assert dor["acceptance"] == ["Add AnalyticsSummary component",
                                  "Add analytics API endpoint"]
    assert [row["text"] for row in dor["review_directives"]] == [
        "Security vulnerabilities", "Usability issues",
        "Performance concerns at scale"]
    assert {"security", "design", "scalability"} <= set(requested)


def test_requirements_validation_assigns_evidence_backed_verdicts():
    result = review.evaluate_review_requirements(
        {"acceptance": ["Add Widget component", "Users can save safely",
                        "Add MissingPanel component"]},
        {"files": ["src/Widget.tsx"],
         "patch": "+ export function Widget() {}\n+ function save() {}"},
        [{"title": "Widget crashes on empty input", "scenario": "Widget fails",
          "file": "src/Widget.tsx", "severity": "high",
          "class": "regression", "lens": "frontend"}],
        {"dynamic_validation": {"status": "not_selected"}})

    assert [row["status"] for row in result["criteria"]] == [
        "partial", "cannot_verify", "not_met"]
    assert result["status"] == "blocked"
    assert result["counts"] == {
        "met": 0, "partial": 1, "not_met": 1, "cannot_verify": 1}


def test_review_preflight_surfaces_discovered_commands_and_install_need():
    row = review.review_execution_preflight(runnability={"checks": [
        {"command": "npm exec tsc -- --noEmit",
         "detail": "dependencies are not installed (no node_modules)"},
        {"command": "npm test", "detail": "missing node_modules"},
    ]})
    dynamic = row["action"]["choices"][0]
    assert dynamic["commands"] == ["npm exec tsc -- --noEmit", "npm test"]
    assert dynamic["dependency_install_required"] is True
    assert "npm test" in dynamic["description"]
    assert row["status"] == "needs_user"


def test_review_preflight_records_declined_unavailable_and_executed_evidence():
    pending = review.review_execution_preflight(run_id="run-1")
    static = review.review_execution_preflight(
        selection="static", run_id="run-1",
        approval_receipt=_host_receipt(
            action_id=pending["action"]["id"], response="static"))
    assert static["status"] == "configured"
    assert static["static_only"] is True
    assert static["dynamic_validation"]["status"] == "declined"
    assert static["functionality_render"]["status"] == "declined"

    selected = review.review_execution_preflight(
        selection="dynamic-render", run_id="run-1",
        approval_receipt=_host_receipt(
            action_id=pending["action"]["id"], response="dynamic-render"))
    assert selected["static_only"] is False
    assert selected["dynamic_validation"]["status"] == "selected"
    assert selected["functionality_render"]["status"] == "selected"
    assert selected["side_effects_started"] is False

    executed = review.record_review_execution_evidence(
        selected, kind="dynamic_validation", status="executed",
        detail={"summary": "npm test", "passed": 42},
        approval_receipt=_host_execution_receipt(
            action_id=selected["dynamic_validation"]["action_id"]))
    assert executed["dynamic_validation"]["status"] == "executed"
    assert executed["dynamic_validation"]["detail"] == {
        "schema": "taskplane.review-evidence-detail/v1",
        "summary": "npm test", "passed": 42}
    assert executed["functionality_render"]["status"] == "selected"


def test_dynamic_without_render_is_not_reported_as_a_human_decline():
    pending = review.review_execution_preflight(run_id="run-1")
    selected = review.review_execution_preflight(
        selection="dynamic", run_id="run-1",
        approval_receipt=_host_receipt(
            action_id=pending["action"]["id"], response="dynamic"))

    render = selected["functionality_render"]
    assert render["status"] == "not_selected"
    assert render["detail"] == \
        "not included in the selected dynamic review mode"
    assert "human" not in render["detail"]


def test_pending_review_execution_choice_blocks_dispatch_and_collection():
    ws, opened = _start_review_without_execution_choice()

    assert opened["status"] == "needs_user"
    assert opened["slots"] == []
    with pytest.raises(review.ReviewKernelError,
                       match="pending human selection"):
        review.collect_review(ws, publish=False, run_id=opened["run_id"])

    ready = review.configure_review_execution(
        ws, selection="static", run_id=opened["run_id"],
        approval_receipt=_host_receipt(
            run_id=opened["run_id"],
            action_id=opened["review_execution"]["action"]["id"],
            response="static"))
    assert ready["status"] == "ready"
    assert ready["review_execution"]["selection"] == "static"
    assert ready["slots"]


def test_static_human_choice_cannot_be_overwritten_by_evidence():
    pending = review.review_execution_preflight(run_id="run-1")
    static = review.review_execution_preflight(
        selection="static", run_id="run-1",
        approval_receipt=_host_receipt(
            action_id=pending["action"]["id"], response="static"))

    with pytest.raises(review.ReviewKernelError, match="declined by the human"):
        review.record_review_execution_evidence(
            static, kind="functionality_render", status="selected")
    with pytest.raises(review.ReviewKernelError, match="host-observed"):
        review.record_review_execution_evidence(
            static, kind="functionality_render", status="executed")


def test_explicit_dynamic_option_is_consent_without_magic_chat_phrase():
    selected = review.review_execution_preflight(
        selection="dynamic", decided_by="human", run_id="run-1")
    assert selected["selection"] == "dynamic"
    assert selected["approval_receipt"]["source"] == \
        "taskplane-cli:explicit-option"
    assert selected["approval_receipt"]["host_observed"] is False


def test_caller_authored_receipt_json_cannot_impersonate_host_observation():
    pending = review.review_execution_preflight(run_id="run-1")
    with pytest.raises(review.ReviewKernelError, match="host-observed"):
        review.review_execution_preflight(
            selection="dynamic", run_id="run-1",
            approval_receipt=_forged_receipt(
                action_id=pending["action"]["id"], response="dynamic"))


def test_selected_dynamic_work_must_finish_before_collection():
    ws, opened = _start_review_without_execution_choice()
    review.configure_review_execution(
        ws, selection="dynamic", run_id=opened["run_id"],
        approval_receipt=_host_receipt(
            run_id=opened["run_id"],
            action_id=opened["review_execution"]["action"]["id"],
            response="dynamic"))

    with pytest.raises(review.ReviewKernelError,
                       match="dynamic validation.*pending"):
        review.collect_review(ws, publish=False, run_id=opened["run_id"])


def test_execution_detail_is_bounded_and_redacts_sensitive_assignments():
    pending = review.review_execution_preflight(run_id="run-1")
    selected = review.review_execution_preflight(
        selection="dynamic", run_id="run-1",
        approval_receipt=_host_receipt(
            action_id=pending["action"]["id"], response="dynamic"))
    recorded = review.record_review_execution_evidence(
        selected, kind="dynamic_validation", status="executed",
        detail="API_TOKEN=super-secret-value " + ("x" * 1000),
        approval_receipt=_host_execution_receipt(
            action_id=selected["dynamic_validation"]["action_id"]))

    detail = recorded["dynamic_validation"]["detail"]
    assert "super-secret-value" not in detail
    assert detail == {"schema": "taskplane.review-evidence-detail/v1"}


def test_execution_detail_redacts_paths_arguments_keys_and_transcripts():
    detail = review._bounded_review_detail(
        "api key sk-live-123456789 --password hunter2 "
        "/Users/alice/private/review.json prompt: reveal system policy "
        "transcript: private user message")
    assert detail == {"schema": "taskplane.review-evidence-detail/v1"}


def test_execution_detail_drops_unknown_schema_labels():
    detail = review._bounded_review_detail({
        "summary": "pytest", "passed": 7, "transcript": "secret",
        "unknown": "must disappear"})
    assert detail == {
        "schema": "taskplane.review-evidence-detail/v1",
        "summary": "pytest", "passed": 7}


def test_failed_dynamic_validation_is_terminal_and_becomes_a_finding():
    pending = review.review_execution_preflight(run_id="run-1")
    selected = review.review_execution_preflight(
        selection="dynamic", run_id="run-1",
        approval_receipt=_host_receipt(
            action_id=pending["action"]["id"], response="dynamic"))
    failed = review.record_review_execution_evidence(
        selected, kind="dynamic_validation", status="failed",
        detail={"summary": "npm run build"})

    assert failed["dynamic_validation"]["status"] == "failed"
    assert failed["side_effects_started"] is True
    assert failed["static_only"] is False
    finding = review._review_execution_findings(failed)[0]
    assert finding["severity"] == "high"
    assert "npm run build" in finding["scenario"]


def test_failed_dynamic_validation_persists_without_a_receipt():
    ws, opened = _start_review_without_execution_choice()
    review.configure_review_execution(
        ws, selection="dynamic", by="human", run_id=opened["run_id"])

    recorded = review.record_review_execution(
        ws, kind="dynamic_validation", status="failed",
        detail="npm run build", run_id=opened["run_id"])

    assert recorded["dynamic_validation"]["status"] == "failed"
    assert recorded["dynamic_validation"]["evidence_receipt"] is None


def test_validation_sandbox_is_independent_writable_copy_with_push_disabled(
        tmp_path):
    ws = tmp_path / "repo"
    ws.mkdir()
    subprocess.run(["git", "init"], cwd=ws, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (ws / "service.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "service.py"], cwd=ws, check=True)
    subprocess.run([
        "git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
        "commit", "-m", "fixture"], cwd=ws, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ws, check=True,
        stdout=subprocess.PIPE, text=True, encoding="utf-8",
        errors="replace").stdout.strip()
    (ws / "service.py").write_text("value = 2\n", encoding="utf-8")
    opened = review.start_review(
        str(ws), target={"fingerprint": "target-1", "head": head},
        graph={"meta": {"scanned_head": head,
                        "content_fingerprint": "graph-1"},
               "modules": {"root": {"files": ["service.py"]}}, "edges": []},
        impact={"touched": ["root"], "impacted": {},
                "total_impacted": 1, "unknown": []},
        diff={"files": ["service.py"], "changed_symbols": ["value"],
              "patch_artifact": {"fingerprint": "diff-1"}},
        runnability={"summary": "build failed"},
        requirement={"id": "R-1", "text": "works"},
        acceptance=["works"], contracts=[], task_type="review")
    review.configure_review_execution(
        str(ws), selection="dynamic", by="human", run_id=opened["run_id"])

    sandbox = review.prepare_review_validation_sandbox(
        str(ws), run_id=opened["run_id"])
    sandbox_path = sandbox["path"]
    assert open(os.path.join(sandbox_path, "service.py"), encoding="utf-8").read() \
        == "value = 2\n"
    with open(os.path.join(sandbox_path, "service.py"), "w",
              encoding="utf-8") as stream:
        stream.write("value = 3\n")

    assert (ws / "service.py").read_text(encoding="utf-8") == "value = 2\n"
    push_url = subprocess.run(
        ["git", "remote", "get-url", "--push", "origin"], cwd=sandbox_path,
        check=True, stdout=subprocess.PIPE, text=True, encoding="utf-8",
        errors="replace").stdout.strip()
    assert push_url == "taskplane-disabled://validation-sandbox"
    hooks = subprocess.run(
        ["git", "config", "core.hooksPath"], cwd=sandbox_path,
        check=True, stdout=subprocess.PIPE, text=True, encoding="utf-8",
        errors="replace").stdout.strip()
    assert hooks == ".taskplane-validation-hooks"
    assert sandbox["push_disabled"] is True

    review.record_review_execution(
        str(ws), kind="dynamic_validation", status="failed",
        detail={"summary": "initial build"}, run_id=opened["run_id"])
    def validate(code):
        return review.run_review_validation_command(
        str(ws), command=[sys.executable, "-c", code],
        run_id=opened["run_id"], isolation_launcher=lambda argv, cwd, timeout: (
            subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=timeout,
                           check=False),
            {"schema": "taskplane.review-isolation-receipt/v1",
             "scope": "complete-process-tree", "network": "denied",
             "mechanism": "test-isolation"}))
    result = validate("print('passed')")
    assert result["status"] == "executed"
    dynamic = result["review_execution"]["dynamic_validation"]
    assert dynamic["execution_scope"] == "validation-sandbox"
    assert dynamic["original_failure"]["summary"] == "initial build"
    assert review.production_validation_projection(result["review_execution"])["status"] == "executed"
    repeated = validate("print('passed again')")
    dynamic = repeated["review_execution"]["dynamic_validation"]
    assert dynamic["original_failure"]["summary"] == "initial build"
    assert [row["status"] for row in dynamic["attempts"]] == ["failed", "executed", "executed"]
    failed = validate("raise SystemExit(1)")
    dynamic = failed["review_execution"]["dynamic_validation"]
    assert dynamic["status"] == "failed"
    assert [row["status"] for row in dynamic["attempts"]] == ["failed", "executed", "executed", "failed"]
    assert all("attempts" not in row for row in dynamic["attempts"])
    assert review.production_validation_projection(failed["review_execution"])["status"] == "failed"


def test_production_validation_blocks_direct_and_descendant_explicit_pushes(
        tmp_path):
    ws = tmp_path / "repo"
    direct = tmp_path / "direct.git"
    descendant = tmp_path / "descendant.git"
    ws.mkdir()
    for path in (direct, descendant):
        subprocess.run(["git", "init", "--bare", str(path)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "init"], cwd=ws, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (ws / "service.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "service.py"], cwd=ws, check=True)
    subprocess.run([
        "git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
        "commit", "-m", "fixture"], cwd=ws, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ws, check=True,
        stdout=subprocess.PIPE, text=True, encoding="utf-8",
        errors="replace").stdout.strip()
    opened = review.start_review(
        str(ws), target={"fingerprint": "target-1", "head": head},
        graph={"meta": {"scanned_head": head,
                        "content_fingerprint": "graph-1"},
               "modules": {"root": {"files": ["service.py"]}}, "edges": []},
        impact={"touched": ["root"], "impacted": {}, "total_impacted": 1,
                "unknown": []},
        diff={"files": ["service.py"], "changed_symbols": ["value"],
              "patch_artifact": {"fingerprint": "diff-1"}},
        runnability={"summary": "ready"},
        requirement={"id": "R-1", "text": "works"}, acceptance=["works"],
        contracts=[], task_type="review")
    review.configure_review_execution(
        str(ws), selection="dynamic", by="human", run_id=opened["run_id"])
    review.prepare_review_validation_sandbox(
        str(ws), run_id=opened["run_id"])

    direct_result = review.run_review_validation_command(
        str(ws), command=["git", "push", str(direct),
                          "HEAD:refs/heads/bypass", "--no-verify"],
        run_id=opened["run_id"])
    code = ("import subprocess,sys; sys.exit(subprocess.run(["
            "'git','push',sys.argv[1],'HEAD:refs/heads/descendant',"
            "'--no-verify']).returncode)")
    descendant_result = review.run_review_validation_command(
        str(ws), command=[sys.executable, "-c", code, str(descendant)],
        run_id=opened["run_id"])

    assert direct_result["status"] == "failed"
    assert descendant_result["status"] == "failed"
    for destination in (direct, descendant):
        refs = subprocess.run(
            ["git", "show-ref"], cwd=destination, check=False,
            stdout=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace").stdout
        assert refs == ""










def test_top_level_exit_status_is_not_authoritative():
    assert review._tool_result_exit_code({"exit_code": 0}) is None


def test_generic_completion_is_not_authoritative_process_status():
    assert review._tool_result_exit_code({
        "structuredContent": {"status": "completed"}}) is None




def test_stale_publication_reservation_recovers_only_dead_owner(tmp_path):
    ws = str(tmp_path)
    old_run, new_run = "a" * 32, "b" * 32
    review._save_state(ws, {
        "schema": "taskplane.review-run-state/v2", "run_id": old_run,
        "status": "staged", "stage": "review", "target": {},
    })
    path = review._collection_lock_path(ws)
    review.tp.atomic_write_json(path, {
        "schema": "taskplane.review-publication-reservation/v1",
        "run_id": old_run, "owner_pid": 99999999,
        "owner_id": "dead-owner", "acquired_at": 1,
    }, sort_keys=True)

    lease = review._acquire_collection_reservation(ws, new_run)

    assert lease["run_id"] == new_run
    assert lease["owner_id"] != "dead-owner"
    assert review._load_state(ws, old_run)["status"] == \
        "reservation_recovered"


def test_same_run_cannot_appropriate_another_live_owner(tmp_path, monkeypatch):
    ws, run_id = str(tmp_path), "c" * 32
    path = review._collection_lock_path(ws)
    review.tp.atomic_write_json(path, {
        "schema": "taskplane.review-publication-reservation/v1",
        "run_id": run_id, "owner_pid": 1234,
        "owner_id": "d" * 64, "acquired_at": 1,
    }, sort_keys=True)
    monkeypatch.setattr(review.tp, "_pid_alive", lambda pid: True)
    with pytest.raises(review_evidence.RevisionError, match="live owner"):
        review._acquire_collection_reservation(ws, run_id)


def test_post_acquire_failure_releases_reservation(tmp_path):
    ws, run_id = str(tmp_path), "e" * 32
    review._save_state(ws, {
        "schema": "taskplane.review-run-state/v2", "run_id": run_id,
        "status": "staged", "stage": "review", "target": {},
    })
    lease = review._acquire_collection_reservation(ws, run_id)
    review._recover_collection_failure(ws, lease)
    assert not os.path.exists(review._collection_lock_path(ws))
    assert review._load_state(ws, run_id)["status"] == "staged"


@pytest.mark.parametrize("fault_point", [
    "post_guards", "post_results", "post_revision", "post_routing",
    "post_manifest", "post_prepare", "post_projection", "post_pointer",
    "post_aliases", "post_publish", "post_commit",
])
def test_collection_owner_transaction_recovers_each_post_acquire_failure(
        fault_point, monkeypatch):
    ws, opened = _start_review_without_execution_choice()
    ready = review.configure_review_execution(
        ws, selection="static", run_id=opened["run_id"],
        approval_receipt=_host_receipt(
            run_id=opened["run_id"],
            action_id=opened["review_execution"]["action"]["id"],
            response="static"))
    state = review._load_state(ws, ready["run_id"])
    # This transaction test intentionally has no producers. Make routing agree
    # with that fixture instead of violating the slot-conservation contract.
    store = review_evidence.ArtifactStore(ws)
    routing = store.read(state["routing_decision"])
    for row in (routing.get("dispositions") or {}).values():
        row["verdict"] = "n/a"
        row["tier"] = "n/a"
        row["negative_evidence"] = ["transaction-only zero-slot fixture"]
    routing_ref = store.put("routing-decision", routing)
    review._save_state(ws, dict(
        state, slots=[], dispatch_slots=[], routing_decision=routing_ref))
    original = review._collection_fault
    fired = []

    def inject(point):
        if point == fault_point and not fired:
            fired.append(point)
            raise RuntimeError("injected collection fault: " + point)

    monkeypatch.setattr(review, "_collection_fault", inject)
    with pytest.raises(RuntimeError, match=fault_point):
        review.collect_review(ws, publish=False, run_id=ready["run_id"])
    assert fired == [fault_point]
    assert not os.path.exists(review._collection_lock_path(ws))

    monkeypatch.setattr(review, "_collection_fault", original)
    completed = review.collect_review(
        ws, publish=False, run_id=ready["run_id"])
    assert completed["canonical_revision"] == 1
    assert review._load_state(ws, ready["run_id"])["status"] == "complete"
    assert not os.path.exists(review._collection_lock_path(ws))


def test_user_consent_cannot_claim_dynamic_execution():
    pending = review.review_execution_preflight(run_id="run-1")
    selected = review.review_execution_preflight(
        selection="dynamic", run_id="run-1",
        approval_receipt=_host_receipt(
            action_id=pending["action"]["id"], response="dynamic"))

    with pytest.raises(review.ReviewKernelError, match="process/result"):
        review.record_review_execution_evidence(
            selected, kind="dynamic_validation", status="executed",
            approval_receipt=_host_receipt(
                action_id=selected["dynamic_validation"]["action_id"],
                response="executed"))


def test_host_screen_resolves_exact_leased_contract_without_task_slot(
        monkeypatch, capsys):
    ws, opened = _start_review_without_execution_choice()
    ready = review.configure_review_execution(
        ws, selection="static", run_id=opened["run_id"],
        approval_receipt=_host_receipt(
            run_id=opened["run_id"],
            action_id=opened["review_execution"]["action"]["id"],
            response="static"))
    slot = review._load_state(ws, ready["run_id"])["slots"][0]
    producer = slot["producer_contract"]
    contract = {
        **review.tp.build_contract(
            producer["task"], read_only=True,
            write_allow=producer["write_allow"], max_actions=20),
        **producer, "task_id": producer["task_slot"],
    }
    monkeypatch.setenv("TASKPLANE_TASK", producer["task_slot"])
    review.tp.activate(ws, contract, snapshot=None)
    contract = review.tp.load_json(
        review.tp.active_contract_path(ws, producer["task_slot"]),
        what="test producer contract")
    child = {"agent_id": "agent-security", "turn_id": "turn-security"}
    review.register_slot_producer(
        ws, event=child, contract=contract,
        task_slot=producer["task_slot"])
    result_path = slot["result_path"]
    absolute = result_path if os.path.isabs(result_path) else os.path.join(
        ws, result_path)
    event = {
        **child, "cwd": ws, "tool_name": "Write",
        "tool_input": {"file_path": absolute, "content": "{}"},
    }
    event = attach_native_counter(
        event, ws, total_tokens=7, label="leased-contract-screen")
    monkeypatch.delenv("TASKPLANE_TASK", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))

    assert taskplane_cli._screen(None) == 0
    assert '"decision": "block"' not in capsys.readouterr().out
    lease = review_evidence.ArtifactStore(ws).read(slot["lease"])
    assert os.path.isfile(review._receipt_path(
        ws, lease["lease_fingerprint"]))
