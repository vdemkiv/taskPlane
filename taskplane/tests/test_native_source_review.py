"""Reported Claude failure boundary, with simulated host events and counters."""
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from contextlib import redirect_stdout, redirect_stderr

import pytest
import review
import storage
import taskplane_lite as lite
import tp as cli
from scripts import package_claude

ROOT = Path(cli.__file__).resolve().parents[1]


@pytest.fixture
def source(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(("CLAUDE_", "CODEX_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("TASKPLANE_HOME", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "native-claude")
    ws = tmp_path / "source"
    ws.mkdir()
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    (ws / "source.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "add", "."], cwd=ws, check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c",
        "user.email=fixture@example.invalid", "commit", "-qm", "source"], cwd=ws, check=True)
    return ws


def invoke(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli.main(list(args))
    return rc, out.getvalue(), err.getvalue()


def observation(total=3_750_000, cached=3_000_000):
    return {"status": "available", "provider": "claude", "session_id": "native-claude",
            "usage": {"total_tokens": total, "cached_input_tokens": cached,
                      "input_tokens": total - 100, "output_tokens": 100}}


def test_native_identity_matches_hook_and_legacy_compatibility(source, monkeypatch):
    expected = storage.project_taskplane_home(str(source))
    with storage.hook_session({"session_id": "native-claude", "cwd": str(source)}):
        assert storage.project_taskplane_home(str(source)) == expected
    monkeypatch.setenv("CLAUDE_SESSION_ID", "native-claude")
    assert storage.project_taskplane_home(str(source)) == expected
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    assert storage.project_taskplane_home(str(source)) == expected
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "different")
    rc, out, err = invoke("contracts", "--workspace", str(source))
    assert rc != 0 and "identities disagree" in out + err


def test_long_session_starts_without_setup_contract_or_source_gate(source, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("ordinary source review invoked delivery setup or enforcement")
    monkeypatch.setattr(cli, "_initialize_entry", forbidden)
    monkeypatch.setattr(cli, "_enforcement_check", forbidden)
    monkeypatch.setattr(cli, "_source_review_usage_snapshot", lambda ws: observation())
    rc, out, err = invoke("review", "start", "--scope", "repository",
                          "--max-tokens", "1000000", "--workspace", str(source))
    assert rc == 0, (out, err)
    manifest = json.loads(out)
    assert manifest["status"] == "ready" and len(out.encode()) < 2048
    assert not lite.load_active(str(source))
    assert not (source / ".gitignore").exists()
    selected = review.review_evidence_runtime.ArtifactStore(str(source)).read(manifest["source"])
    assert selected["files"] == ["source.py"]
    event = {"session_id": "native-claude", "cwd": str(source), "hook_event_name": "PreToolUse",
             "tool_name": "Read", "tool_use_id": "first-source-read",
             "tool_input": {"file_path": str(source / "source.py")}}
    result = subprocess.run([sys.executable, str(ROOT / "taskplane/tp.py"), "screen"],
        input=json.dumps(event), text=True, capture_output=True, cwd=source,
        env={**os.environ, "TASKPLANE_HOOK_PATH": "native"})
    assert result.returncode == 0 and not result.stdout, (result.stdout, result.stderr)
    assert (source / selected["files"][0]).read_text() == "VALUE = 1\n"
    monkeypatch.setattr(cli, "_source_review_usage_snapshot", lambda ws: observation(5_140_000, 4_000_000))
    # Retry does not silently reset attribution to a later conversation total.
    assert invoke("review", "start", "--scope", "repository", "--workspace", str(source))[0] == 0
    rc, out, err = invoke("budget", "--workspace", str(source))
    assert rc == 0, (out, err)
    usage = json.loads(out)
    assert usage["usage"]["total_tokens"] == 1_390_000
    assert usage["usage"]["cached_input_tokens"] == 1_000_000
    assert usage["budget"]["mode"] == "advisory"
    assert usage["budget"]["max_tokens"] == 1_000_000


def test_new_review_preserves_old_contract(source):
    lite.record_entry_tools(["Read", "Grep", "Glob", "Write"])
    contract = lite.build_contract("old review", read_only=True)
    lite.activate(str(source), contract, snapshot=lite.git_head(str(source)))
    path = Path(lite.active_contract_path(str(source)))
    before = path.read_bytes()
    rc, out, err = invoke("review", "start", "--scope", "repository", "--workspace", str(source))
    assert rc == 0, (out, err)
    assert path.read_bytes() == before


@pytest.mark.parametrize("change", ["missing", "provider", "session", "backward", "unknown"])
def test_usage_does_not_fabricate_zero_or_mix_observations(change):
    start = observation()
    current = copy.deepcopy(start)
    if change == "missing":
        current = {"status": "unavailable"}
    elif change in {"provider", "session"}:
        current["session_id" if change == "session" else "provider"] = "other"
    elif change == "backward":
        current["usage"]["total_tokens"] -= 1
    else:
        current["usage"]["cached_input_tokens"] = None
    result = review.source_review_usage({"budget": {"mode": "advisory"}, "usage_start": start}, current)
    assert result["status"] == "unavailable" and "usage" not in result


def test_no_delivery_session_context_is_quiet(source, monkeypatch):
    monkeypatch.setenv("TASKPLANE_HOOK_PATH", "native")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "session_id": "native-claude", "cwd": str(source), "hook_event_name": "SessionStart"})))
    rc, out, err = invoke("context")
    assert rc == 0 and not out, (out, err)


def test_claude_projection_contains_only_host_hook_commands():
    projected = package_claude.load_hook_manifest()
    assert len(json.dumps(projected, separators=(",", ":")).encode()) < 2048
    for rows in projected["hooks"].values():
        for row in rows:
            for hook in row["hooks"]:
                assert "commandWindows" not in hook
                assert "cmd.exe" not in hook["command"]
                assert "--host codex" not in hook["command"]
    assert sum((ROOT / "skills" / skill / "SKILL.md").stat().st_size
               for skill in ("taskplane", "tp-engineering")) <= 8192


def test_native_identity_does_not_require_environment_handoff(source, tmp_path, monkeypatch):
    envfile = tmp_path / "host.env"
    envfile.write_text("unchanged\n")
    monkeypatch.setenv("CLAUDE_ENV_FILE", str(envfile))
    cli._persist_claude_hook_session({"hook_event_name": "SessionStart", "session_id": "native-claude"})
    assert envfile.read_text() == "unchanged\n"


def test_usage_delta_uses_existing_native_transcript_projection(source, tmp_path, monkeypatch):
    transcript = tmp_path / "claude.jsonl"
    def row(identity, fresh, cached, creation, output):
        return json.dumps({"id": identity, "usage": {"input_tokens": fresh,
            "cache_read_input_tokens": cached, "cache_creation_input_tokens": creation,
            "output_tokens": output}}) + "\n"
    transcript.write_text(row("before-review", 700000, 3000000, 40000, 10000))
    monkeypatch.setattr(review, "_host_review_transcripts", lambda: [("claude", str(transcript))])
    rc, out, err = invoke("review", "start", "--scope", "repository", "--workspace", str(source))
    assert rc == 0, (out, err)
    with transcript.open("a") as stream:
        stream.write(row("during-review", 100, 200, 30, 40))
    rc, out, err = invoke("budget", "--workspace", str(source))
    assert rc == 0, (out, err)
    result = json.loads(out)
    assert result["status"] == "available"
    assert result["usage"]["total_tokens"] == 370
    assert result["usage"]["cached_input_tokens"] == 200
    assert result["usage"]["output_tokens"] == 40
    assert result["usage"]["cache_creation_tokens"] == 30
    assert result["usage"]["uncached_input_tokens"] == 100
