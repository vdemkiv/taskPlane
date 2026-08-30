import json
import base64
import itertools
import subprocess
from pathlib import Path

import evidence
import evaluation_output
import loop
import runtime_eval


ROOT = Path(__file__).resolve().parents[2]


def _workflow_brief(name, member):
    brief = {"id": member, "prompt": member,
             "resume_identity": f"handle-{member}"}
    if name == "evaluate":
        output_schema = evaluation_output.evaluator_output_schema()
        output_contract = {
            "output_schema": output_schema,
            "max_attempts": evaluation_output.MAX_ATTEMPTS,
        }
        brief.update({
            "output_schema": output_schema,
            "output_contract": output_contract,
            "max_attempts": output_contract["max_attempts"],
        })
    return brief


def _evaluator_receipt(member):
    return {
        "schema": "taskplane.evaluator-output/v2",
        "task": member,
        "requirement": "",
        "verdict": "pass",
        "criteria": [],
        "graph": {
            "dispositions": [],
            "requirements_checked": [],
            "contracts_checked": [],
        },
        "failures": [],
    }


def _run_workflow(name, args, order=None):
    """Run the production JS workflow with deterministic host doubles."""
    source = base64.b64encode(
        (ROOT / "workflows" / f"{name}-wave.js").read_bytes()).decode()
    script = r"""
const mod = await import('data:text/javascript;base64,' + process.argv[1]);
const args = JSON.parse(process.argv[2]);
const order = JSON.parse(process.argv[3]);
const name = process.argv[4];
const calls = [];
const agent = async (_prompt, options) => {
  calls.push(options.label);
  const member = options.label.split(':').slice(1).join(':');
  return name === 'execute'
    ? {task: member, outcome: 'pass', note: 'ok'}
    : {schema: 'taskplane.evaluator-output/v2', task: member,
       requirement: '', verdict: 'pass', criteria: [],
       graph: {dispositions: [], requirements_checked: [], contracts_checked: []},
       failures: []};
};
const parallel = async (runs) => {
  const sequence = order.length ? order : runs.map((_run, index) => index);
  const values = [];
  for (const index of sequence) values.push(await runs[index]());
  return values;
};
const result = await mod.default({args, agent, parallel, phase: () => {}});
process.stdout.write(JSON.stringify({result, calls}));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script, source,
         json.dumps(args), json.dumps(order or []), name],
        check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    return json.loads(completed.stdout)


def test_wave_suppresses_child_success_and_emits_one_aggregate():
    for ordering in itertools.permutations(["a", "b", "c"]):
        wave = loop.command_wave_create("wave-1", ["a", "b", "c"])
        events = []
        for member in ordering:
            events += loop.command_wave_update(wave, member, "succeeded")
        assert [event["state"] for event in events] == ["wave_completed"]
        assert wave["ordinary_completion_deliveries"] == 1


def test_attention_remains_visible_before_and_after_terminal_completion():
    for attention in ("approval_required", "input_required"):
        for ordering in (("succeeded", attention),
                         (attention, "succeeded")):
            wave = loop.command_wave_create("wave-1", ["a"])
            events = []
            for state in ordering:
                events += loop.command_wave_update(wave, "a", state)
            assert sum(e["state"] == attention for e in events) == 1
            assert sum(e["state"] == "wave_completed" for e in events) == 1
            assert wave["members"]["a"] == "succeeded"
            assert loop.command_wave_update(wave, "a", attention) == []


def test_attention_pauses_until_matching_explicit_resume():
    for attention, resume in (("approval_required", "authorization_granted"),
                              ("input_required", "input_provided")):
        wave = loop.command_wave_create("wave-1", ["a"])
        assert [event["state"] for event in loop.command_wave_update(
            wave, "a", attention)] == [attention]
        encoded = json.loads(json.dumps(wave))
        resumed = loop.command_wave_resume(encoded, ["a"])
        assert resumed["members"]["a"] == attention
        assert resumed["ordinary_completion_deliveries"] == 0
        assert loop.command_wave_update(resumed, "a", resume) == []
        assert resumed["members"]["a"] == "running"
        assert [event["state"] for event in loop.command_wave_update(
            resumed, "a", "succeeded")] == ["wave_completed"]


def test_production_workflows_aggregate_every_completion_order():
    for name in ("execute", "evaluate"):
        briefs = [_workflow_brief(name, member)
                  for member in ("a", "b", "c")]
        for ordering in itertools.permutations(range(3)):
            run = _run_workflow(name, {"briefs": briefs}, ordering)
            wave = run["result"]["command_wave"]
            events = run["result"]["command_events"]
            assert sum(e["state"] == "wave_completed" for e in events) == 1
            assert wave["ordinary_completion_deliveries"] == 1
            assert wave["handles"] == {
                "a": "handle-a", "b": "handle-b", "c": "handle-c"}


def test_production_workflows_resume_without_relaunch_and_surface_attention():
    for name in ("execute", "evaluate"):
        briefs = [_workflow_brief(name, member) for member in ("a", "b")]
        prior_receipt = ({"task": "a", "outcome": "pass", "note": "ok"}
                         if name == "execute" else
                         _evaluator_receipt("a"))
        wave = loop.command_wave_create(
            f"{name}-wave", ["a", "b"],
            handles={"a": "handle-a", "b": "handle-b"})
        loop.command_wave_update(wave, "a", "succeeded")
        wave["receipts"] = {"a": prior_receipt}
        run = _run_workflow(name, {"briefs": briefs, "command_wave": wave})
        assert run["calls"] == [f"{'task' if name == 'execute' else 'eval'}:b"]
        assert run["result"]["command_wave"]["launches"] == 2
        assert len(run["result"]["receipts"]) == 2

        for attention, resume in (
                ("approval_required", "authorization_granted"),
                ("input_required", "input_provided")):
            events = [{"member": "a", "state": attention}]
            single = [dict(briefs[0])]
            paused = _run_workflow(
                name, {"briefs": single, "command_events": events})
            emitted = [event["state"]
                       for event in paused["result"]["command_events"]]
            assert emitted.count(attention) == 1
            assert "wave_completed" not in emitted
            assert paused["calls"] == []
            paused_wave = paused["result"]["command_wave"]
            assert paused_wave["members"]["a"] == attention
            assert paused_wave["ordinary_completion_deliveries"] == 0

            # A process/runtime restart with no human transition remains
            # paused and neither invokes nor completes the member.
            restarted = _run_workflow(
                name, {"briefs": single, "command_wave": paused_wave})
            assert restarted["calls"] == []
            assert restarted["result"]["command_events"] == []
            assert restarted["result"]["command_wave"]["members"]["a"] == \
                attention

            # Only the matching explicit response continues on the same
            # durable handle.  It is a resume, not another launch.
            continued = _run_workflow(name, {
                "briefs": single,
                "command_wave": restarted["result"]["command_wave"],
                "command_events": [{"member": "a", "state": resume}],
            })
            assert continued["calls"] == [
                f"{'task' if name == 'execute' else 'eval'}:a"]
            final_wave = continued["result"]["command_wave"]
            assert final_wave["handles"]["a"] == "handle-a"
            assert final_wave["launches"] == 1
            assert final_wave["members"]["a"] == "succeeded"
            assert [event["state"] for event in
                    continued["result"]["command_events"]] == \
                ["wave_completed"]


def test_resume_reuses_bound_handles_and_preserves_interruption():
    wave = loop.command_wave_create(
        "wave-1", ["a", "b"], handles={"a": "handle-a", "b": "handle-b"})
    wave["interrupted"] = True
    encoded = json.loads(json.dumps(wave))
    resumed = loop.command_wave_resume(encoded, ["a", "b"])
    assert resumed["handles"] == {"a": "handle-a", "b": "handle-b"}
    assert resumed["interrupted"] is True
    assert resumed["launches"] == 2


def test_runtime_projection_is_bounded_and_never_manufactures_measurement():
    wave = loop.command_wave_create("wave-1", ["a"])
    projection = runtime_eval.command_wave_projection(
        wave, efficiency={"launches": 1, "model_wakes": 0,
                          "unchanged_model_polls": 0,
                          "polling_raw_tokens": 0,
                          "total_raw_tokens": None},
        artifacts=[{"path": "x" * 9000, "sha256": "abc", "bytes": 5}])
    assert projection["efficiency"]["measurement_status"] == "unproven"
    assert projection["efficiency"]["polling_raw_token_share"] is None
    assert len(projection["artifacts"][0]["path"].encode()) <= 512


def test_evidence_projects_existing_command_wave_only(monkeypatch):
    state = {"command_wave": loop.command_wave_create("wave-1", ["a"])}
    assert evidence.command_wave_evidence(state)["schema"] == \
        "taskplane.command-wave-evidence/v1"
    assert evidence.command_wave_evidence({}) is None
