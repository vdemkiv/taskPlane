"""Actual Codex v2 metadata shape, confined to simulated temporary stores."""
from datetime import datetime, timezone
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import codex_identity
import tp as cli
from taskplane import taskplane_lite as kernel


CHILD = "01a07da3-a886-7261-aae9-1126caff4b6c"
PARENT = "01a07d5a-864e-7e23-913d-42bb850e742b"
NAME = "tp_step_product_pm_13722ee8"
NOW = datetime(2026, 9, 7, tzinfo=timezone.utc)


@pytest.fixture
def native(tmp_path):
    home = tmp_path / "codex"
    path = home / "sessions/2026/09/07" / f"rollout-2026-09-07T16-51-12-{CHILD}.jsonl"
    path.parent.mkdir(parents=True)
    metadata = {
        "type": "session_meta", "payload": {
            "id": CHILD, "session_id": PARENT, "cwd": str(tmp_path),
            "parent_thread_id": PARENT, "agent_path": f"/root/{NAME}",
            "source": {"subagent": {"thread_spawn": {
                "parent_thread_id": PARENT, "depth": 1,
                "agent_path": f"/root/{NAME}", "agent_role": None}}}}}
    path.write_text(json.dumps(metadata) + "\nconversation-must-never-be-read\n")
    event = {"hook_event_name": "SubagentStart", "session_id": PARENT,
             "turn_id": "turn-1", "agent_id": CHILD, "agent_type": "default",
             "cwd": str(tmp_path)}
    return home, path, metadata, event


@pytest.mark.parametrize("kind", ["SubagentStart", "SubagentStop"])
def test_generic_host_role_resolves_exact_child_without_reading_messages(native, kind):
    home, path, _, event = native
    event["hook_event_name"] = kind
    if kind == "SubagentStop":
        event["agent_transcript_path"] = str(path)
    normalized = codex_identity.normalize_lifecycle(event, codex_home=str(home), now=NOW)
    assert normalized == {**event, "task_name": NAME}
    assert normalized["agent_type"] == "default"  # preserve the actual host fact
    assert "task_name" not in event


@pytest.mark.parametrize("case", ["foreign-child", "foreign-session", "foreign-parent",
    "foreign-workspace", "foreign-path", "unrelated-task", "oversized", "symlink", "ambiguous"])
def test_foreign_missing_or_ambiguous_metadata_cannot_claim_a_slot(native, case, tmp_path):
    home, path, metadata, event = native
    meta = metadata["payload"]
    if case == "foreign-child":
        meta["id"] = PARENT
    elif case == "foreign-session":
        meta["session_id"] = "foreign"
    elif case == "foreign-parent":
        meta["parent_thread_id"] = "foreign"
    elif case == "foreign-workspace":
        meta["cwd"] = str(tmp_path / "other")
    elif case == "foreign-path":
        meta["source"]["subagent"]["thread_spawn"]["agent_path"] = "/root/foreign"
    elif case == "unrelated-task":
        event["task_name"] = "unrelated"
    path.write_text(json.dumps(metadata) + "\n")
    if case == "oversized":
        path.write_text(" " * (codex_identity.MAX_METADATA_BYTES + 1) + "\n")
    elif case == "symlink":
        real = tmp_path / "foreign.jsonl"
        path.rename(real)
        path.symlink_to(real)
    elif case == "ambiguous":
        duplicate = path.with_name(f"rollout-other-{CHILD}.jsonl")
        duplicate.write_bytes(path.read_bytes())
    assert codex_identity.normalize_lifecycle(event, codex_home=str(home), now=NOW) == event


def test_hook_wrapper_normalizes_before_claim_and_passes_start_claim(native, monkeypatch, capsys):
    home, path, _, event = native
    event["agent_transcript_path"] = str(path)
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("TASKPLANE_HOOK_PATH", "native")
    # Storage bootstrap has separate end-to-end launcher coverage. This
    # fixture isolates normalization and the real idempotent hook claim.
    monkeypatch.setattr(cli.runtime_storage, "bind_hook_taskplane_home",
        lambda workspace, environment, **kwargs: environment["TASKPLANE_HOME"])
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(event)))
    seen = []

    def handler(args):
        seen.append(json.load(cli.sys.stdin))
        return 0

    assert cli._run_hook_command(SimpleNamespace(
        cmd="subagent-start", workspace=None, fn=handler)) == 0
    assert seen[0]["task_name"] == NAME
    assert seen[0]["agent_type"] == "default"
    assert seen[0]["_taskplane_hook_claim_id"]
    capsys.readouterr()


def test_generic_native_child_binds_one_contract_and_not_its_orchestrator(native):
    from taskplane.tests.test_worker_contract_lifecycle import _active_worker
    home, _, _, event = native
    workspace = Path(event["cwd"])
    contract = _active_worker(workspace, name=NAME)
    normalized = codex_identity.normalize_lifecycle(event, codex_home=str(home), now=NOW)
    bound = kernel.bind_worker_contract_event(str(workspace), normalized)
    assert bound["slot"] == contract["task_slot"]
    assert bound["contract"]["worker_lifecycle"]["owner"]["agent_id"] == CHILD
    assert kernel.load_active(str(workspace)) is None


def test_generic_host_role_can_produce_exact_claimed_terminal_evidence(tmp_path):
    from taskplane.tests.test_r0013_codex_producer_adapter import _args
    from taskplane.producer_observation import record_codex_subagent_stop, consume_matching_observation
    import hashlib
    common, event, _ = _args(tmp_path)
    event["agent_type"] = "default"
    claim = hashlib.sha256(kernel.hook_event_identity(
        str(tmp_path), "subagent-stop", event).encode()).hexdigest()
    receipt = record_codex_subagent_stop(event=event, hook_claim_id=claim, **common)
    assert consume_matching_observation(**common) == receipt


@pytest.mark.parametrize("case", ["measured", "foreign-task", "foreign-cwd", "later-counter", "resumed", "missing-counter"])
def test_terminal_usage_uses_only_exact_current_child_counter(native, case):
    home, path, metadata, event = native
    if case == "resumed":
        metadata["payload"]["history_base"] = {"thread_id": CHILD}
    counter = {"type": "event_msg", "ordinal": 5,
        "timestamp": "2026-09-07T00:00:02Z" if case == "later-counter" else "2026-09-07T00:00:00Z",
        "payload": {"type": "token_count", "info": {"total_token_usage": {
            "input_tokens": 95, "cached_input_tokens": 70, "output_tokens": 5,
            "reasoning_output_tokens": 1, "total_tokens": 100}}}}
    path.write_text(json.dumps(metadata) + "\n" + (
        "" if case == "missing-counter" else json.dumps(counter) + "\n"))
    terminal = {"observed_at": NOW.timestamp() + 1,
        "owner": {"agent_id": CHILD, "session_id": PARENT, "agent_type": "default",
                  "task_name": "foreign" if case == "foreign-task" else NAME}}
    before = path.read_bytes()
    workspace = event["cwd"] + "/foreign" if case == "foreign-cwd" else event["cwd"]
    if case == "measured":
        snapshot = codex_identity.terminal_usage(workspace, terminal, codex_home=str(home))
        assert snapshot["usage"]["total_tokens"] == 100
        assert snapshot["session_id"] == CHILD
    else:
        with pytest.raises(ValueError):
            codex_identity.terminal_usage(workspace, terminal, codex_home=str(home))
    assert path.read_bytes() == before
