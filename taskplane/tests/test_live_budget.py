"""Tool-boundary budget and no-retry regressions; no model calls in this suite."""
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from taskplane import codex_identity, loop, review_evidence, tp as cli
from taskplane.tests.phase_fixture import _supporting_pristine_phase_run, phase_pending
from taskplane.tests.test_native_terminal_telemetry import SOURCE_SHA, _write_codex_transcript


def test_phase_limit_reaches_the_active_hook_contract(tmp_path, monkeypatch):
    ws, *_ = _supporting_pristine_phase_run(tmp_path, monkeypatch)
    assert "error" not in loop.next_action(ws)
    material = review_evidence.ArtifactStore(ws).read(phase_pending(ws)["reference"])
    contract = cli.tp.load_json(cli.tp.active_contract_path(ws, material["contract_slot"]))
    budget = contract["budget"]
    assert budget["max_tokens"] == material["bindings"]["budget"]["tokens"] == 100_000
    assert 0 < budget["target_tokens"] < budget["max_tokens"]
    assert budget["token_usage_required"] is True


@pytest.fixture
def screen(tmp_path, monkeypatch, capsys):
    contract = cli.tp.build_contract("bounded worker")
    cli._apply_contract_token_ceiling(contract, 100)
    cli.tp.activate(str(tmp_path), contract, snapshot=SOURCE_SHA)

    def invoke(tool, args, *, tokens=100, available=True):
        transcript = tmp_path / "native.jsonl"
        _write_codex_transcript(transcript, label="meter", input_tokens=tokens,
                               cached_tokens=tokens, output_tokens=0)
        event = {"cwd": str(tmp_path), "turn_id": "turn-budget", "tool_name": tool,
                 "tool_input": args}
        if available:
            event["transcript_path"] = str(transcript)
        monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(event)))
        assert cli.cmd_screen(None) == 0
        return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]

    return invoke


@pytest.mark.parametrize("tool,args", [
    ("Read", {"path": "input.txt"}),
    ("collaboration.wait_agent", {"timeout_ms": 60000}),
    ("collaboration.send_message", {"target": "lens", "message": "status?"}),
    ("collaboration.spawn_agent", {"task_name": "another_review"}),
])
def test_cached_tokens_block_reads_and_coordination(screen, tool, args):
    result = screen(tool, args)
    assert any(row.get("decision") == "block" and "100/100 native tokens" in row["reason"]
               for row in result)


@pytest.mark.parametrize("available", [True, False])
def test_status_remains_reachable_when_usage_is_exhausted_or_missing(screen, available):
    result = screen("Bash", {"command": "python3 taskplane/tp.py status"}, available=available)
    assert not any(row.get("decision") == "block" for row in result)


def test_missing_usage_is_not_zero(screen):
    result = screen("Read", {"path": "input.txt"}, available=False)
    assert any("telemetry unavailable" in row.get("reason", "") for row in result)


def test_short_polling_refused_but_event_wait_allowed(screen):
    result = screen("collaboration.wait_agent", {"timeout_ms": 10}, tokens=10)
    assert any("short polling is disabled" in row.get("reason", "") for row in result)
    assert not any(row.get("decision") == "block" for row in
                   screen("collaboration.wait_agent", {"timeout_ms": 60000}, tokens=20))


def test_hook_matches_all_tools_including_reads_and_messages():
    hooks = json.loads((Path(__file__).parents[2] / "hooks/hooks.json").read_text())
    assert hooks["hooks"]["PreToolUse"][0]["matcher"] == ".*"


def test_live_child_counter_never_uses_parent_transcript(monkeypatch):
    owner = {"session_id": "parent", "agent_id": "child", "task_name": "worker"}
    contract = {"worker_scoped": True, "worker_lifecycle": {
        "owner": owner, "status": "active", "expected_task_name": "worker"}}
    seen = []
    def match(event):
        seen.append(event)
        return "worker", "/exact-child.jsonl", "fingerprint"
    monkeypatch.setattr(codex_identity, "_matching_child", match)
    assert cli._budget_transcript("/workspace", contract,
                                  {"transcript_path": "/parent.jsonl", "agent_transcript_path": "/exact-child.jsonl"}, "codex") == "/exact-child.jsonl"
    assert seen == [{**owner, "cwd": "/workspace", "agent_transcript_path": "/exact-child.jsonl"}]
    monkeypatch.setattr(codex_identity, "_matching_child", lambda _: None)
    with pytest.raises(ValueError, match="no exact bound child"):
        cli._budget_transcript("/workspace", contract, {"transcript_path": "/parent.jsonl"}, "codex")


@pytest.mark.parametrize("command", ["subagent-stop", "session-verify"])
def test_budget_stop_and_replay_cannot_request_another_turn(command, capsys):
    cli._emit_budget_stop("budget exhausted")
    output = capsys.readouterr().out
    assert json.loads(output)["continue"] is False
    assert cli._hook_response_class(command, output, 0) == "stop"
    assert "stop" in cli.tp._HOOK_RESPONSE_CLASSES
    assert cli._replay_hook_response(command, "stop") == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["continue"] is False
    assert replay.get("decision") != "block"


def test_stop_budget_precedes_missing_submission_retry(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.tp, "load_active_for_event", lambda *_: {})
    monkeypatch.setattr(cli, "_budget_stop_reason", lambda *_: "budget exhausted")
    monkeypatch.setattr(cli, "_submission_stop_check", lambda *_a, **_k: pytest.fail("must not retry"))
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("{}"))
    assert cli.cmd_session_verify(SimpleNamespace(workspace=str(tmp_path))) == 0
    assert json.loads(capsys.readouterr().out)["continue"] is False


@pytest.mark.parametrize("parent_cap, expected", [(None, 100_000), (25_000_000, 100_000), (500, 500)])
def test_lens_cannot_inherit_a_huge_or_missing_parent_budget(tmp_path, monkeypatch, parent_cap, expected):
    monkeypatch.setattr(cli.tp, "tp_dir", lambda _: str(tmp_path))
    monkeypatch.setattr(cli.tp, "load_json", lambda *_a, **_k: {"budget": {"max_tokens": parent_cap}})
    monkeypatch.setattr(cli.tp, "activate_review_contract_action", lambda *_a, **_k: {"budget": {"max_actions": 40}})
    monkeypatch.setattr(cli.tp, "active_contract_path", lambda *_: str(tmp_path / "slot.json"))
    contract = cli._activate_visible_review_bootstrap(str(tmp_path), action={}, expected={}, task_slot="lens")
    assert contract["budget"]["max_tokens"] == expected
    assert contract["budget"]["token_usage_required"] is True
    assert contract["budget"]["max_actions"] == 8
    assert json.loads((tmp_path / "slot.json").read_text()) == contract
