"""Native read compatibility must not authorize outer-shell writes."""

import json
import os
import shlex

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
