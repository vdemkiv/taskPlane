"""Native read compatibility must not authorize outer-shell writes."""

import json
import os
import shlex
import datetime
import io
from argparse import Namespace
from pathlib import Path

import pytest

from taskplane import host_capabilities as host
from taskplane import review


pytestmark = pytest.mark.skipif(os.name != "posix", reason="native POSIX sandbox mapping")


@pytest.fixture
def native(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(("LD_", "DYLD_", "BASH_FUNC_")) or key in {
                "ENV", "BASH_ENV", "SHELLOPTS", "BASHOPTS"}:
            monkeypatch.delenv(key)
    monkeypatch.setenv("CODEX_THREAD_ID", "native-test")
    monkeypatch.delenv("CODEX_SANDBOX", raising=False)
    executable = tmp_path / "host" / "codex"
    executable.parent.mkdir()
    executable.write_text("fixture identity; never executed")
    executable.chmod(0o700)
    monkeypatch.setattr(host.shutil, "which", lambda _: str(executable))
    workspace = tmp_path / "reviewed"
    workspace.mkdir()
    return str(workspace)


def _call(request, *, call_id="read-call", wrapper=True):
    return {"type": "response_item", "payload": {
        "type": "custom_tool_call" if wrapper else "function_call",
        "name": "exec" if wrapper else "functions.exec_command",
        "input" if wrapper else "arguments": (
            "text(await tools.exec_command(" + json.dumps(request) + "));"
            if wrapper else json.dumps(request)),
        "call_id": call_id,
    }}


@pytest.fixture
def transcript(monkeypatch):
    rows = []
    monkeypatch.setattr(review, "_host_review_transcripts", lambda: [("codex", "host-records")])
    monkeypatch.setattr(review, "_host_review_records", lambda _: rows)
    return rows


@pytest.mark.parametrize("tool", ["exec_command", "functions.exec_command"])
def test_request_uses_native_managed_readonly_profile_and_fixed_outer_shell(native, tool):
    request = host.codex_readonly_command(["/bin/cat", "README.md"], native)
    assert shlex.split(request["cmd"])[1:] == [
        "sandbox", "--include-managed-config", "-P", ":read-only", "--", "/bin/cat", "README.md"]
    assert request["shell"] == "/bin/sh" and request["login"] is False
    assert request["workdir"] == native
    assert host.is_codex_readonly_invocation(tool, request, native)


def test_native_seatbelt_request_remains_subject_to_host_approval(native, monkeypatch):
    monkeypatch.setenv("CODEX_SANDBOX", "seatbelt")
    request = host.codex_readonly_command(["/bin/cat", "README.md"], native)
    assert request["sandbox_permissions"] == "require_escalated"
    assert host.is_codex_readonly_invocation("exec_command", request, native)


@pytest.mark.parametrize("suffix", [
    "; touch source.py", " > source.py", "\n/bin/true", " && true", " $(touch source.py)",
])
def test_outer_shell_effects_are_rejected(native, suffix):
    request = host.codex_readonly_command(["/bin/cat", "README.md"], native)
    request["cmd"] += suffix
    assert not host.is_codex_readonly_invocation("exec_command", request, native)


def test_quoted_filename_is_data_inside_native_sandbox(native):
    filename = "$(touch source.py); arbitrary filename"
    request = host.codex_readonly_command(["/bin/cat", filename], native)
    assert shlex.split(request["cmd"])[-1] == filename
    assert host.is_codex_readonly_invocation("exec_command", request, native)


@pytest.mark.parametrize("changes", [
    {"shell": "/bin/bash"}, {"login": True}, {"workdir": "/"},
    {"env": {"PATH": "untrusted"}}, {"receipt": {"approved": True}},
])
def test_untrusted_shell_or_caller_permission_metadata_is_rejected(native, changes):
    request = host.codex_readonly_command(["/bin/cat", "README.md"], native)
    assert not host.is_codex_readonly_invocation("exec_command", {**request, **changes}, native)


@pytest.mark.parametrize("before,after", [
    (":read-only", ":workspace"), (" sandbox ", " exec "),
    (" --include-managed-config", ""),
])
def test_profile_cannot_be_widened_or_managed_policy_removed(native, before, after):
    request = host.codex_readonly_command(["/bin/cat", "README.md"], native)
    request["cmd"] = request["cmd"].replace(before, after)
    assert not host.is_codex_readonly_invocation("exec_command", request, native)


@pytest.mark.parametrize("wrapper", [True, False])
def test_projection_requires_matching_pending_native_call(native, transcript, wrapper):
    request = host.codex_readonly_command(["/bin/cat", "README.md"], native)
    projection = {"command": request["cmd"]}
    assert not host.is_codex_readonly_invocation("Bash", projection, native)
    transcript.append(_call(request, wrapper=wrapper))
    assert host.is_codex_readonly_invocation("Bash", projection, native)
    assert not host.is_codex_readonly_invocation("Bash", {**projection, "shell": "/bin/sh"}, native)


@pytest.mark.parametrize("changes", [{"shell": "/reviewed/hostile-shell"}, {"login": True}])
def test_projection_recovers_actual_shell_and_login(native, transcript, changes):
    request = host.codex_readonly_command(["/bin/cat", "README.md"], native)
    transcript.append(_call({**request, **changes}))
    assert not host.is_codex_readonly_invocation("Bash", {"command": request["cmd"]}, native)


def test_completed_or_ambiguous_call_is_not_authority(native, transcript):
    request = host.codex_readonly_command(["/bin/cat", "README.md"], native)
    projection = {"command": request["cmd"]}
    transcript.extend([_call(request), {"type": "response_item", "payload": {
        "type": "custom_tool_call_output", "call_id": "read-call", "output": "done"}}])
    assert not host.is_codex_readonly_invocation("Bash", projection, native)
    transcript[:] = [_call(request), _call(request, call_id="another-call")]
    assert not host.is_codex_readonly_invocation("Bash", projection, native)


def test_arbitrary_wrapper_cannot_claim_a_pending_native_read(native, transcript):
    request = host.codex_readonly_command(["/bin/cat", "README.md"], native)
    record = _call(request)
    record["payload"]["input"] += 'text({"approved":true});'
    transcript.append(record)
    assert not host.is_codex_readonly_invocation("Bash", {"command": request["cmd"]}, native)


@pytest.mark.parametrize("name", ["BASH_ENV", "ENV", "DYLD_INSERT_LIBRARIES", "LD_PRELOAD"])
def test_inherited_launch_overrides_disable_adapter(native, monkeypatch, name):
    monkeypatch.setenv(name, "untrusted")
    assert host.codex_readonly_runtime(native) is None
    with pytest.raises(ValueError, match="unavailable"):
        host.codex_readonly_command(["/bin/cat", "README.md"], native)


@pytest.mark.parametrize("symlink", [False, True])
def test_checkout_executable_or_host_symlink_into_checkout_is_rejected(native, tmp_path, monkeypatch, symlink):
    binary = tmp_path / "reviewed" / "codex"
    binary.write_text("checkout executable")
    binary.chmod(0o700)
    selected = tmp_path / "host" / "alias" if symlink else binary
    if symlink:
        selected.symlink_to(binary)
    monkeypatch.setattr(host.shutil, "which", lambda _: str(selected))
    assert host.codex_readonly_runtime(native) is None


def test_missing_host_or_executable_is_explicitly_unsupported(native, monkeypatch):
    monkeypatch.delenv("CODEX_THREAD_ID")
    assert host.codex_readonly_runtime(native) is None
    monkeypatch.setenv("CODEX_THREAD_ID", "native-test")
    monkeypatch.setattr(host.shutil, "which", lambda _: None)
    assert host.codex_readonly_runtime(native) is None


def test_native_read_refusal_names_missing_host_identity_without_payload(native, monkeypatch):
    request = host.codex_readonly_command(["/bin/cat", "private-filename"], native)
    monkeypatch.delenv("CODEX_THREAD_ID")
    diagnostic = {}
    assert not host.is_codex_readonly_invocation(
        "Bash", {"command": request["cmd"]}, native, diagnostic=diagnostic)
    assert diagnostic == {"stage": "native_runtime", "native_session_present": False}
    assert "private-filename" not in json.dumps(diagnostic)


def test_native_read_refusal_names_missing_pending_record(native, transcript):
    request = host.codex_readonly_command(["pwd"], native)
    diagnostic = {}
    assert not host.is_codex_readonly_invocation(
        "Bash", {"command": request["cmd"]}, native, diagnostic=diagnostic)
    assert diagnostic["stage"] == "one_pending_native_call"
    assert diagnostic["record_count"] == 0 and diagnostic["pending_calls"] == 0
    assert "cmd" not in diagnostic


def test_native_flat_js_argument_keys_match_actual_finish_shape(native, transcript):
    request = {"cmd": "python3 /installed/taskplane/tp.py clear --task-id product --workspace " + native,
               "shell": "/bin/sh", "login": False, "workdir": native}
    record = _call(request)
    arguments = ",".join(key + ":" + json.dumps(value) for key, value in request.items())
    record["payload"]["input"] = "text(await tools.exec_command({" + arguments + "}));\n"
    transcript.append(record)
    assert host.pending_codex_tool_call("exec_command") == request


def test_native_literal_quoted_scalar_escaping_is_preserved_as_data():
    value = 'a "quote", a slash \\, a newline\n$(never execute)'
    assert host._native_argument_literal('{cmd:' + json.dumps(value) + ',login:false}') == {
        "cmd": value, "login": False}
    assert host._native_argument_literal("{" + ",".join(
        "key" + str(index) + ":0" for index in range(33)) + "}") is None


@pytest.mark.parametrize("arguments", [
    '{cmd: makeCommand()}', '{...request}', '{["cmd"]: "pwd"}',
    '{get cmd() {return "pwd"}}', '{cmd: `pwd`}', '{cmd: "pwd", // comment\nlogin:false}',
    '{cmd:"pwd",cmd:"different"}', '{cmd: "pwd", env: {HOME: "/tmp"}}',
])
def test_native_argument_literals_cannot_execute_js_or_replace_keys(arguments, transcript):
    transcript.append({"type": "response_item", "payload": {
        "type": "custom_tool_call", "call_id": "bad-native-expression", "name": "exec",
        "input": "text(await tools.exec_command(" + arguments + "));"}})
    assert host.pending_codex_tool_call("exec_command") is None


@pytest.fixture
def fresh_cli(native, tmp_path, monkeypatch):
    """Real CLI record shape; neither a host receipt nor execution proof."""
    instant = datetime.datetime(2026, 9, 15, 2, 41, 20, tzinfo=datetime.timezone.utc)
    prefix = f"{int(instant.timestamp() * 1000):012x}"
    session_id = f"{prefix[:8]}-{prefix[8:]}-7000-8000-000000000001"
    root = tmp_path / "codex-home"
    root.mkdir()
    monkeypatch.setattr(review, "_canonical_host_root", lambda _: str(root))
    monkeypatch.setenv("CODEX_THREAD_ID", session_id)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    local = instant.astimezone()
    path = root / "sessions" / local.strftime("%Y/%m/%d") / (
        "rollout-" + local.strftime("%Y-%m-%dT%H-%M-%S") + "-" + session_id + ".jsonl")
    path.parent.mkdir(parents=True)
    metadata = {"type": "session_meta", "payload": {
        "id": session_id, "session_id": session_id, "source": "exec", "cwd": native}}
    request = host.codex_readonly_command(["pwd"], native)
    call = _call(request)
    call["payload"]["input"] += "\n"  # Actual installed canary code-mode shape.
    path.write_text(json.dumps(metadata) + "\n" + json.dumps(call) + "\n")
    return root, path, metadata, call, request


@pytest.mark.parametrize("index_exists", [False, True])
def test_fresh_cli_read_resolves_exact_native_header_before_index_update(fresh_cli, native, index_exists):
    root, _, _, _, request = fresh_cli
    if index_exists:
        (root / "session_index.jsonl").write_text('{"id":"some-other-session"}\n')
    assert host.pending_codex_tool_call("exec_command") == request
    assert host.is_codex_readonly_invocation("Bash", {"command": request["cmd"]}, native)


@pytest.mark.parametrize("damage", ["foreign-id", "wrong-type", "missing-newline", "duplicate-index", "symlink"])
def test_unindexed_cli_identity_conflicts_are_refused(fresh_cli, native, damage):
    root, path, metadata, call, request = fresh_cli
    if damage == "foreign-id":
        metadata["payload"]["id"] = "other-session"
    elif damage == "wrong-type":
        metadata["type"] = "response_item"
    elif damage == "duplicate-index":
        (root / "session_index.jsonl").write_text(
            (json.dumps({"id": metadata["payload"]["id"]}) + "\n") * 2)
    path.write_text(json.dumps(metadata) + ("" if damage == "missing-newline" else
                    "\n" + json.dumps(call) + "\n"))
    if damage == "symlink":
        actual = path.with_suffix(".actual")
        path.rename(actual)
        path.symlink_to(actual)
    assert host.pending_codex_tool_call("exec_command") is None
    assert not host.is_codex_readonly_invocation("Bash", {"command": request["cmd"]}, native)


def test_two_exact_timezone_candidates_are_not_selected_by_recency(fresh_cli, monkeypatch):
    root, path, metadata, call, _ = fresh_cli
    real_datetime = datetime.datetime

    class FixedLocalDateTime(real_datetime):
        @classmethod
        def fromtimestamp(cls, timestamp, tz=None):
            value = real_datetime.fromtimestamp(timestamp, datetime.timezone.utc)
            return value.astimezone(tz) if tz is not None else (
                value.replace(tzinfo=None) - datetime.timedelta(hours=4))

    monkeypatch.setattr(review.datetime, "datetime", FixedLocalDateTime)
    session_id = metadata["payload"]["id"]
    for stamp in ("2026-09-14T22-41-20", "2026-09-15T02-41-20"):
        candidate = root / "sessions" / stamp[:10].replace("-", "/") / (
            "rollout-" + stamp + "-" + session_id + ".jsonl")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text(json.dumps(metadata) + "\n" + json.dumps(call) + "\n")
    assert host.pending_codex_tool_call("exec_command") is None


def test_fresh_cli_identity_works_without_optional_posix_open_flags(fresh_cli, monkeypatch):
    _, _, _, _, request = fresh_cli
    for name in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
        monkeypatch.delattr(review.os, name, raising=False)
    assert host.pending_codex_tool_call("exec_command") == request


def test_fresh_cli_identity_refuses_replaced_path_without_posix_open_flags(fresh_cli, monkeypatch):
    _, path, _, _, _ = fresh_cli
    replacement = path.with_suffix(".replacement")
    replacement.write_bytes(path.read_bytes())
    original_open = review.os.open

    def replace_after_open(selected, flags, *args, **kwargs):
        descriptor = original_open(selected, flags, *args, **kwargs)
        if selected == str(path):
            os.replace(replacement, path)
        return descriptor

    for name in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
        monkeypatch.delattr(review.os, name, raising=False)
    monkeypatch.setattr(review.os, "open", replace_after_open)
    assert host.pending_codex_tool_call("exec_command") is None


@pytest.mark.parametrize("hook_path", ["native", "bridge", None])
def test_only_claimed_native_hook_binds_missing_session_temporarily(fresh_cli, native, monkeypatch, hook_path):
    import tp as cli
    _, path, metadata, _, request = fresh_cli
    session = metadata["payload"]["id"]
    for key in list(os.environ):
        if key.startswith("TASKPLANE_") or key == "CODEX_THREAD_ID":
            monkeypatch.delenv(key)
    if hook_path:
        monkeypatch.setenv("TASKPLANE_HOOK_PATH", hook_path)
    event = {"hook_event_name": "PreToolUse", "session_id": session,
             "tool_use_id": "native-hook-read", "transcript_path": str(path),
             "cwd": native, "tool_name": "Bash", "tool_input": {"command": request["cmd"]}}
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(event)))
    monkeypatch.setattr(cli.tp, "load_active_for_event", lambda *_: {"read_only": True})
    observed = []

    def invoke(args, workspace):
        observed.append((os.environ.get("CODEX_THREAD_ID"),
                         host.is_codex_readonly_invocation("Bash", event["tool_input"], workspace)))
        return 0

    monkeypatch.setattr(cli, "_invoke_run_command", invoke)
    assert cli._run_hook_command(Namespace(cmd="screen", workspace=native)) == 0
    expected = [(session, True)] if hook_path == "native" else [(None, False)]
    assert observed == expected
    assert "CODEX_THREAD_ID" not in os.environ


@pytest.mark.parametrize("conflict", ["environment", "header", "missing"])
def test_native_hook_identity_conflict_explicitly_blocks_and_replays_at_public_boundary(
        fresh_cli, native, monkeypatch, capsys, conflict):
    import tp as cli
    root, path, metadata, call, _ = fresh_cli
    session = metadata["payload"]["id"]
    for key in list(os.environ):
        if key.startswith("TASKPLANE_") or key == "CODEX_THREAD_ID":
            monkeypatch.delenv(key)
    monkeypatch.setenv("TASKPLANE_HOOK_PATH", "native")
    if conflict == "environment":
        monkeypatch.setenv("CODEX_THREAD_ID", "different-session")
    elif conflict == "missing":
        path.unlink()
    else:
        (root / "session_index.jsonl").write_text(json.dumps({"id": session}) + "\n")
        metadata["payload"]["id"] = "different-session"
        path.write_text(json.dumps(metadata) + "\n" + json.dumps(call) + "\n")
    event = {"hook_event_name": "PreToolUse", "session_id": session,
             "tool_use_id": "native-hook-conflict", "transcript_path": str(path), "cwd": native,
             "tool_name": "Bash"}
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(event)))
    monkeypatch.setattr(cli.tp, "load_active_for_event", lambda *_: {"read_only": True})
    monkeypatch.setattr(cli, "_invoke_run_command", lambda *_: pytest.fail("conflicting identity reached screen"))
    assert cli.main(["screen"]) == 0
    first = capsys.readouterr()
    assert not first.err
    refusal = json.loads(first.out)
    assert refusal["decision"] == "block"
    assert refusal["hookSpecificOutput"] == {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": refusal["reason"],
    }
    assert "native_hook_identity" in refusal["reason"]
    assert native not in first.out and session not in first.out
    assert "different-session" not in first.out
    assert os.environ.get("CODEX_THREAD_ID") == ("different-session" if conflict == "environment" else None)
    journal = Path(cli.tp.hook_claim_journal_path(native))
    recorded = journal.read_bytes()
    claims = json.loads(recorded)["claims"]
    assert len(claims) == 1
    assert claims[0]["response_class"] == "block"
    assert claims[0]["status"] == "completed"

    monkeypatch.setattr(review, "_codex_session_paths", lambda *_a, **_k: pytest.fail(
        "duplicate hook re-resolved native identity"))
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(event)))
    assert cli.main(["screen"]) == 0
    replay = capsys.readouterr()
    assert not replay.err
    assert json.loads(replay.out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert journal.read_bytes() == recorded


def test_native_hook_session_is_restored_after_screen_error(fresh_cli, native, monkeypatch):
    import tp as cli
    _, path, metadata, _, _ = fresh_cli
    for key in list(os.environ):
        if key.startswith("TASKPLANE_") or key == "CODEX_THREAD_ID":
            monkeypatch.delenv(key)
    monkeypatch.setenv("TASKPLANE_HOOK_PATH", "native")
    event = {"hook_event_name": "PreToolUse", "session_id": metadata["payload"]["id"],
             "tool_use_id": "native-hook-error", "transcript_path": str(path), "cwd": native,
             "tool_name": "Bash"}
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(event)))
    monkeypatch.setattr(cli.tp, "load_active_for_event", lambda *_: {"read_only": True})

    def fail(*_):
        assert os.environ["CODEX_THREAD_ID"] == metadata["payload"]["id"]
        raise RuntimeError("screen failure")

    monkeypatch.setattr(cli, "_invoke_run_command", fail)
    with pytest.raises(RuntimeError, match="screen failure"):
        cli._run_hook_command(Namespace(cmd="screen", workspace=native))
    assert "CODEX_THREAD_ID" not in os.environ


@pytest.mark.parametrize("contract_kind", ["none", "build"])
@pytest.mark.parametrize("metadata_state", ["missing", "foreign"])
def test_ordinary_and_build_commands_have_no_new_transcript_prerequisite(
        fresh_cli, native, monkeypatch, capsys, contract_kind, metadata_state):
    import tp as cli
    _, path, metadata, call, _ = fresh_cli
    session = metadata["payload"]["id"]
    for key in list(os.environ):
        if key.startswith("TASKPLANE_") or key == "CODEX_THREAD_ID":
            monkeypatch.delenv(key)
    monkeypatch.setenv("TASKPLANE_HOOK_PATH", "native")
    if metadata_state == "missing":
        path.unlink()
    else:
        metadata["payload"]["id"] = "foreign-session"
        path.write_text(json.dumps(metadata) + "\n" + json.dumps(call) + "\n")
    contract = None if contract_kind == "none" else cli.tp.build_contract(
        "Ordinary Build command", scope=["src/**"])
    monkeypatch.setattr(cli.tp, "load_active_for_event", lambda *_: contract)
    monkeypatch.setattr(review, "_codex_session_paths", lambda *_a, **_k: pytest.fail(
        "ordinary/Build command inspected native history"))
    event = {"hook_event_name": "PreToolUse", "session_id": session,
             "tool_use_id": "ordinary-command", "transcript_path": str(path), "cwd": native,
             "tool_name": "Bash", "tool_input": {"command": "/bin/true"}}
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(event)))
    assert cli._run_hook_command(Namespace(cmd="screen", workspace=native, fn=cli.cmd_screen)) == 0
    output = capsys.readouterr().out
    if contract_kind == "none":
        assert not output  # No contract still defers to the host.
    else:
        # Build keeps its incumbent usage-evidence check. This fixture has
        # no provider totals; the new identity adapter must not run first.
        refusal = json.loads(output)
        assert refusal["decision"] == "block"
        assert "TOKEN BUDGET telemetry unavailable" in refusal["reason"]
    assert "CODEX_THREAD_ID" not in os.environ
