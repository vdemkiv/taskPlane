"""Current shipped workflow transport and task identity admission."""
import base64
import json
import os
import shutil
import subprocess
import pytest
import taskplane_lite as tp_lite
import tp as cli
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WF_DIR = os.path.join(ROOT, "workflows")

def _path(stage: str) -> str:
    return os.path.join(WF_DIR, f"{stage}.js")



def _run_public_workflow(stage: str, args: dict) -> dict:
    """Import and invoke the shipped workflow under a behavioral Node host."""
    if shutil.which("node") is None:
        pytest.skip("Node.js is required for shipped workflow execution")
    with open(_path(stage), "rb") as stream:
        source = base64.b64encode(stream.read()).decode()
    script = r"""
const mod = await import('data:text/javascript;base64,' + process.argv[1]);
const args = JSON.parse(process.argv[2]);
const stage = process.argv[3];
const calls = [];
const phases = [];
const parallelWidths = [];
const agent = async (prompt, options) => {
  calls.push({prompt, options});
  const task = options.label.split(':').slice(1).join(':');
  if (stage === 'evaluate-wave') {
    return {schema: 'taskplane.evaluator-output/v2', task, requirement: '',
      verdict: 'pass', criteria: [], graph: {dispositions: [],
      requirements_checked: [], contracts_checked: []}, failures: []};
  }
  return {task, outcome: 'pass', note: 'ok'};
};
const parallel = async (runs) => {
  parallelWidths.push(runs.length);
  return Promise.all(runs.map((run) => run()));
};
const result = await mod.default({
  args, agent, parallel, phase: (value) => phases.push(value),
});
process.stdout.write(JSON.stringify({meta: mod.meta, calls, phases,
  parallelWidths, result}));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script, source,
         json.dumps(args), stage], check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    return json.loads(completed.stdout)



@pytest.mark.parametrize("stage,key,prefix,first_phase", [
    ("execute-wave", "briefs", "task", "Build"),
    ("evaluate-wave", "briefs", "eval", "Evaluate"),
    ("fix-wave", "verdicts", "fix", "Fix"),
])
def test_shipped_workflow_executes_public_transport_contract(
        stage, key, prefix, first_phase):
    output_schema = ({
        "$id": "taskplane.evaluator-output/v2",
        "additionalProperties": False,
    } if stage == "evaluate-wave" else {
        "$id": f"taskplane.{stage}-test-receipt/v1",
        "additionalProperties": False,
    })
    entries = []
    for member in ("a", "b"):
        output_contract = {"output_schema": output_schema}
        entry = {
            "id": member, "prompt": f"exact prompt {member}",
            "output_contract": output_contract,
        }
        if stage == "evaluate-wave":
            output_contract["max_attempts"] = 2
            entry.update({"resume_identity": f"resume-{member}",
                          "max_attempts": 2})
        entries.append(entry)
    observed = _run_public_workflow(stage, {
        "settings_digest": "0" * 64, key: entries})

    assert observed["meta"]["name"] == stage
    assert observed["phases"] == [first_phase, "Collect"]
    assert observed["parallelWidths"] == [2]
    assert [row["prompt"] for row in observed["calls"]] == [
        "exact prompt a", "exact prompt b"]
    assert [row["options"]["label"] for row in observed["calls"]] == [
        f"{prefix}:a", f"{prefix}:b"]
    assert all(row["options"]["schema"] == output_schema
               for row in observed["calls"])
    assert [row["task"] for row in observed["result"]["receipts"]] == [
        "a", "b"]
    assert observed["result"]["settings_digest"] == "0" * 64



def _trace_events(ws, event):
    p = os.path.join(tp_lite.tp_dir(ws), "trace.jsonl")
    if not os.path.isfile(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(l) for l in f
                if l.strip() and json.loads(l).get("event") == event]



def _assert_audit_value(event, key, value):
    assert event[key] == tp_lite.audit_record("test", {key: value}, observed_at=0)[key], (key, event[key], value)



class TestPlanGateRefusesUnslottableTaskIds:
    """Phase 3 EM review, deep3 finding #2, the EARLY half: nothing
    validated task ids against the slot charset before the human plan
    gate, so a plan carrying `feat/login` cleared approval and only broke
    at execute/evaluate/fix — where the remedy (renaming ids in
    plan/tasks.json) costs a re-plan and a re-approval. The check now runs
    at BOTH plan transitions, via taskplane_lite.plan_task_id_refusal."""

    def test_plan_task_id_errors_names_every_offender(self):
        errs = tp_lite.plan_task_id_errors([
            {"id": "t1"}, {"id": "feat/login"}, {"id": "a b"},
            {"id": "." + "x"}, {"id": None}, {"id": "x" * 65},
            "not-a-dict",
        ])
        assert len(errs) == 5
        for bad in ("feat/login", "a b", ".x", "None", "x" * 65):
            assert any(bad in e for e in errs), bad
        assert tp_lite._TASK_SLOT_RE.pattern in errs[0]
        assert tp_lite.plan_task_id_errors([{"id": "t1"},
                                            {"id": "fix.a-2_b"}]) == []

    def test_gate_refuses_a_plan_with_a_bad_id(self, tmp_path):
        ws = str(tmp_path / "ws")
        os.makedirs(ws)
        refusal = tp_lite.plan_task_id_refusal(
            ws, [{"id": "feat/login"}, {"id": "t2"}], "gate")
        assert refusal is not None
        assert refusal["step"] == "plan"
        assert "feat/login" in refusal["error"]
        assert refusal["task_ids"]
        evs = _trace_events(ws, "loop_gate_blocked")
        _assert_audit_value(evs[-1], "reason", "task_id")

    def test_approve_refuses_the_same_plan(self, tmp_path):
        ws = str(tmp_path / "ws")
        os.makedirs(ws)
        refusal = tp_lite.plan_task_id_refusal(
            ws, [{"id": "feat/login"}], "approve", by="human")
        assert refusal is not None and refusal["step"] == "plan_approval"
        assert "feat/login" in refusal["error"]
        evs = _trace_events(ws, "loop_approve_blocked")
        _assert_audit_value(evs[-1], "reason", "task_id")
        assert evs[-1]["by"] == tp_lite._audit_pseudonym("human")

    def test_good_ids_still_approve(self, tmp_path):
        ws = str(tmp_path / "ws")
        os.makedirs(ws)
        for where in ("gate", "approve"):
            assert tp_lite.plan_task_id_refusal(
                ws, [{"id": "t1"}, {"id": "fix.a-2_b", "deps": ["t1"]}],
                where) is None, where


# Shared capability detection for the active stage workflow emitters.
CODEX_VARS = ("CODEX_HOME", "CODEX_THREAD_ID")
WF_VARS = ("TASKPLANE_WORKFLOWS", "CLAUDE_CODE_WORKFLOWS")


def _clean_env(monkeypatch):
    for v in CODEX_VARS + WF_VARS:
        monkeypatch.delenv(v, raising=False)


class TestWorkflowAvailable:
    def test_codex_always_unavailable(self, monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv("CODEX_HOME", "/x")
        got = cli.workflow_available(".")
        assert got["available"] is False and "codex" in got["reason"].lower()

    def test_codex_beats_explicit_opt_in(self, monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv("CODEX_THREAD_ID", "t1")
        monkeypatch.setenv("TASKPLANE_WORKFLOWS", "1")
        assert cli.workflow_available(".")["available"] is False

    def test_opt_in_enables(self, monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv("TASKPLANE_WORKFLOWS", "1")
        got = cli.workflow_available(".")
        assert got["available"] is True and "TASKPLANE_WORKFLOWS" in got["reason"]

    def test_kill_switch_beats_marker(self, monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv("TASKPLANE_WORKFLOWS", "0")
        monkeypatch.setenv("CLAUDE_CODE_WORKFLOWS", "1")
        got = cli.workflow_available(".")
        assert got["available"] is False and "TASKPLANE_WORKFLOWS=0" in got["reason"]

    def test_claude_marker_enables(self, monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv("CLAUDE_CODE_WORKFLOWS", "1")
        assert cli.workflow_available(".")["available"] is True

    def test_kill_switch_accepts_conventional_falsey_spellings(self, monkeypatch):
        # EM v3: 'false'/'no'/'off' must hit the kill-switch, not silently
        # fall through to the marker/default (fail toward disabled).
        for val in ("false", "no", "off", "FALSE", " Off "):
            _clean_env(monkeypatch)
            monkeypatch.setenv("TASKPLANE_WORKFLOWS", val)
            monkeypatch.setenv("CLAUDE_CODE_WORKFLOWS", "1")
            got = cli.workflow_available(".")
            assert got["available"] is False, val

    def test_opt_in_accepts_conventional_truthy_spellings(self, monkeypatch):
        for val in ("true", "yes", "on", "TRUE"):
            _clean_env(monkeypatch)
            monkeypatch.setenv("TASKPLANE_WORKFLOWS", val)
            got = cli.workflow_available(".")
            assert got["available"] is True, val

    def test_falsey_marker_does_not_enable(self, monkeypatch):
        for val in ("0", "false", "no", "off"):
            _clean_env(monkeypatch)
            monkeypatch.setenv("CLAUDE_CODE_WORKFLOWS", val)
            assert cli.workflow_available(".")["available"] is False, val

    def test_default_unset_is_conservatively_unavailable(self, monkeypatch):
        _clean_env(monkeypatch)
        got = cli.workflow_available(".")
        assert got["available"] is False
        assert "reason" in got and got["reason"]
