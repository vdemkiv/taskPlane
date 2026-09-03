"""Current behavioral contracts for stage workflow transport and fallback."""
import base64
import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tp as cli  # noqa: E402
import loop  # noqa: E402
import taskplane_lite as tp_lite  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
WF_DIR = os.path.join(ROOT, "workflows")
BRIEFS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "fixtures", "briefs")

# The shared live journey is imported explicitly so collection order cannot
# decide which engine the direct producer comparison captures.
_sf_spec = importlib.util.spec_from_file_location(
    "stage_fixture", os.path.join(BRIEFS, "stage_fixture.py"))
stage_fixture = importlib.util.module_from_spec(_sf_spec)
_sf_spec.loader.exec_module(stage_fixture)
GATE_VERBS = ("gate", "approve", "signoff", "resolve")


def _path(stage: str) -> str:
    return os.path.join(WF_DIR, f"{stage}.js")


def _run_public_workflow(stage: str, args: dict) -> dict:
    """Import and invoke the shipped workflow under a behavioral Node host."""
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


# =====================================================================
# t4 — the tp.py stage emitter + Codex parity
# =====================================================================


def _clean_env(monkeypatch):
    for v in stage_fixture.SCRUB_VARS:
        # Register an undo even when the variable starts absent: the shared
        # real-workspace fixture writes TASKPLANE_SESSION_ID directly.
        monkeypatch.setenv(v, "")
        monkeypatch.delenv(v)


_REAL_CLI_TIME = cli._time


class _FrozenCliClock:
    """Keep sequential rail captures at one host-observation instant."""

    time = staticmethod(_REAL_CLI_TIME.time)
    gmtime = staticmethod(_REAL_CLI_TIME.gmtime)

    @staticmethod
    def strftime(fmt, *args):
        if fmt == "%Y-%m-%dT%H:%M:%SZ":
            return "2026-01-01T00:00:00Z"
        return _REAL_CLI_TIME.strftime(fmt, *args)


def _freeze_cli_clock(monkeypatch):
    monkeypatch.setattr(cli, "_time", _FrozenCliClock)


def _trace_events(ws, event):
    p = os.path.join(tp_lite.tp_dir(ws), "trace.jsonl")
    if not os.path.isfile(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(l) for l in f
                if l.strip() and json.loads(l).get("event") == event]


def _audit_value(value):
    """Exact privacy-preserving value written by the durable trace sink."""
    return tp_lite._audit_minimized(value)


def _assert_audit_value(event, key, value):
    assert event[key] == _audit_value(value), (key, event[key], value)


def _stage_problem(payload):
    mapped = cli._stage_wave_run(payload)
    assert mapped is not None and mapped[2] is not None, payload
    return mapped[2]


def _task_path_problem_reason(payload):
    return (_stage_problem(payload)["reason"]
            + " — Task path unaffected (no shell line is composed there); "
              "fix the id in plan/tasks.json before the next plan gate")


def _stderr_reason(err):
    return err.strip().removeprefix("taskplane: ")


@pytest.fixture(scope="module")
def rails():
    """Capture every transport rail from one payload per delivery stage.

    Execute uses the real two-task wave. Evaluate and Fix use minimal current
    engine payloads because this module owns transport compilation, not graph
    quality, evaluator evidence, or failure routing; those have dedicated
    end-to-end suites. Replaying one payload keeps transport assertions from
    minting extra dispatches or timing data merely to compare routing.

    Env is managed by hand (not monkeypatch) because the fixture outlives
    any single test's autouse TASKPLANE_HOME patch."""
    saved = {v: os.environ.get(v) for v in
             stage_fixture.SCRUB_VARS + ("TASKPLANE_HOME",)}
    saved_cli_time = cli._time
    for v in stage_fixture.SCRUB_VARS:
        os.environ.pop(v, None)
    os.environ["TASKPLANE_HOME"] = tempfile.mkdtemp(prefix="tp-stage-store-")
    cli._time = _FrozenCliClock
    try:
        ws = stage_fixture.build_repo(tempfile.mkdtemp(prefix="tp-stage-ws-"))
        caps = {}

        def grab(stage, supplied=None):
            expected_trace = []
            available = cli.workflow_available(ws)
            producer_name = "wave" if stage == "execute" else "next_action"
            producer = getattr(loop, producer_name)
            if supplied is not None:
                setattr(loop, producer_name,
                        lambda *_args, **_kwargs: json.loads(json.dumps(supplied)))
            try:
                bare = stage_fixture.capture_stage(ws, stage)
            finally:
                setattr(loop, producer_name, producer)
            expected_trace.append(("task", available["reason"]))
            frozen = json.loads(bare)

            def replay(*_args, **_kwargs):
                return json.loads(json.dumps(frozen))

            setattr(loop, producer_name, replay)
            try:
                task = stage_fixture.capture_stage(
                    ws, stage, "--emit", "task")
                expected_trace.append(("task", "explicit --emit task"))
                os.environ["CODEX_HOME"] = "/x"
                os.environ["TASKPLANE_WORKFLOWS"] = "1"
                codex_available = cli.workflow_available(ws)
                codex = stage_fixture.capture_stage(ws, stage)
                expected_trace.append(("task", codex_available["reason"]))
                os.environ.pop("CODEX_HOME")
                workflow_available = cli.workflow_available(ws)
                wf = stage_fixture.capture_stage(
                    ws, stage)   # opt-in, no codex
                expected_trace.append(
                    ("workflow", workflow_available["reason"]))
                os.environ.pop("TASKPLANE_WORKFLOWS")
            finally:
                setattr(loop, producer_name, producer)
                os.environ.pop("CODEX_HOME", None)
                os.environ.pop("TASKPLANE_WORKFLOWS", None)
            caps[stage] = {"bare": bare, "task": task, "codex": codex,
                           "wf": wf, "trace": expected_trace}

        stage_fixture.start_loop(ws)
        grab("execute")
        settings_digest = cli._effective_settings_snapshot().digest
        task = {"id": "t1", "workspace": os.path.join(ws, ".tp-work", "t1")}
        delivery = {
            "mode": "inline",
            "artifacts": {
                name: {"status": "available", "path": f"dashboard.{name}"}
                for name in ("json", "markdown")},
        }
        for stage in ("evaluate", "fix"):
            grab(stage, {
                "step": stage, "task": task,
                "settings_digest": settings_digest,
                "instruction": "Run the assigned work, then `loop submit "
                               "pass|fail`. The orchestrator alone runs the "
                               "matching `loop gate`.",
                "dashboard": {"delivery": delivery},
            })
        yield {"ws": ws, "caps": caps,
               # resolved WHILE the journey's TASKPLANE_HOME is in effect
               "store": stage_fixture.store_root(ws)}
    finally:
        cli._time = saved_cli_time
        for v, val in saved.items():
            if val is None:
                os.environ.pop(v, None)
            else:
                os.environ[v] = val


# --------------------------------------- live task/workflow transport parity


class TestStageTaskPathContract:
    """The default and explicit Task rails select the same live contract."""

    def test_bare_default_and_explicit_task_select_same_contract(self, rails):
        for stage in stage_fixture.STAGES:
            c = rails["caps"][stage]
            assert json.loads(c["bare"]) == json.loads(c["task"]), stage

    def test_task_stdout_never_carries_workflow_keys(self, rails):
        for stage in stage_fixture.STAGES:
            stdout = rails["caps"][stage]["bare"]
            payload = json.loads(stdout)
            for key in ("dispatch_path", "workflow", "reason"):
                assert key not in payload, (stage, key)
            for activation in ("export TASKPLANE_TASK", "set TASKPLANE_TASK"):
                assert activation not in stdout, (stage, activation)

    def test_next_action_preserves_stable_delivery_contract(self, rails):
        """Production next_action exposes how and where complete evidence
        was delivered without embedding host/sequence-dependent receipts."""
        for stage in ("evaluate", "fix"):
            delivery = json.loads(
                rails["caps"][stage]["bare"])["dashboard"]["delivery"]
            assert delivery["mode"] in {"inline", "complete-markdown"}
            for artifact in ("json", "markdown"):
                receipt = delivery["artifacts"][artifact]
                assert receipt["status"] == "available"
                assert receipt["path"]
                assert "bytes" not in receipt
                assert "sha256" not in receipt
            assert "semantic_bytes" not in delivery
            assert "semantic_sha256" not in delivery

    def test_codex_env_gets_task_bytes_even_when_opted_in(self, rails):
        """CODEX_HOME + TASKPLANE_WORKFLOWS=1: Codex always wins — stdout
        carries the same semantic contract as the bare Task path."""
        for stage in stage_fixture.STAGES:
            c = rails["caps"][stage]
            codex = json.loads(c["codex"])
            bare = json.loads(c["bare"])
            # Capability and enforcement receipts are host observations and
            # deliberately differ. Remove only that routing metadata for the
            # canonical cross-host artifact comparison; retain it in both
            # real payloads for dispatch audit and strict enforcement.
            def canonical(payload):
                payload = json.loads(json.dumps(payload))

                def strip(value):
                    if isinstance(value, dict):
                        return {k: strip(v) for k, v in value.items()
                                if k not in ("dispatch_route",
                                             "dispatch_blocked",
                                             "enforcement")}
                    if isinstance(value, list):
                        return [strip(v) for v in value]
                    return value
                return strip(payload)

            assert canonical(codex) == canonical(bare), stage
            def contains_key(value, key):
                if isinstance(value, dict):
                    return key in value or any(
                        contains_key(child, key) for child in value.values())
                if isinstance(value, list):
                    return any(contains_key(child, key) for child in value)
                return False

            assert contains_key(codex, "dispatch_route") == \
                contains_key(bare, "dispatch_route"), stage
            assert "dispatch_path" not in codex, stage

    def test_chosen_path_is_traced_on_both_rails(self, rails):
        evs = _trace_events(rails["ws"], "stage_dispatch_path")
        assert evs, "stage_dispatch_path must be traced"
        for e in evs:
            assert e["stage"] in stage_fixture.STAGES
            assert e["path"] in (_audit_value("task"),
                                  _audit_value("workflow"))
            assert e.get("reason")
        by_stage = {s: [e for e in evs if e["stage"] == s]
                    for s in stage_fixture.STAGES}
        for stage, sevs in by_stage.items():
            # bare, --emit task, codex → task; the opt-in capture → workflow
            expected = rails["caps"][stage]["trace"]
            assert len(sevs) == len(expected), stage
            for event, (path, reason) in zip(sevs, expected):
                _assert_audit_value(event, "path", path)
                _assert_audit_value(event, "reason", reason)

# ------------------------------------------------ workflow-path emission


class TestStageEmitterWorkflowPath:
    def test_execute_wave_compiles_to_one_run_covering_the_wave(self, rails):
        """At most ONE workflow run per stage between human gates: a single
        workflow{name, args} object whose briefs cover EVERY wave entry."""
        out = rails["caps"]["execute"]["wf"]
        payload = json.loads(out)
        assert payload["dispatch_path"] == "workflow"
        assert payload["reason"]
        assert out.count('"workflow":') == 1          # exactly one run
        wf = payload["workflow"]
        assert isinstance(wf, dict) and not isinstance(wf, list)
        assert wf["name"] == "execute-wave"
        briefs = wf["args"]["briefs"]
        assert [b["id"] for b in briefs] == \
            [e["task"]["id"] for e in payload["wave"]]
        assert len(briefs) == len(payload["wave"]) == 2

    def test_stage_payloads_bind_settings_and_expected_workflow_inputs(self,
                                                                       rails):
        from taskplane.settings import load_settings

        expected_digest = load_settings(environment=os.environ).digest

        def settings_digests(value):
            found = []
            if isinstance(value, dict):
                found.extend(
                    item for key, item in value.items()
                    if key == "settings_digest"
                )
                for item in value.values():
                    found.extend(settings_digests(item))
            elif isinstance(value, list):
                for item in value:
                    found.extend(settings_digests(item))
            return found

        for stage in stage_fixture.STAGES:
            task_payload = json.loads(rails["caps"][stage]["task"])
            workflow_payload = json.loads(rails["caps"][stage]["wf"])
            assert set(settings_digests(task_payload)) == {expected_digest}
            assert set(settings_digests(workflow_payload)) == {expected_digest}
        ev = json.loads(rails["caps"]["evaluate"]["wf"])["workflow"]
        fx = json.loads(rails["caps"]["fix"]["wf"])["workflow"]
        assert ev["args"]["settings_digest"] == expected_digest
        assert fx["args"]["settings_digest"] == expected_digest
        assert ev["args"]["briefs"][0]["id"] == "t1"
        assert fx["args"]["verdicts"][0]["id"] == "t1"
        assert ev["args"]["briefs"][0]["worktree"].endswith(".tp-work/t1")

    def test_agent_prompts_are_task_path_bytes_verbatim(self, rails):
        """contract:wave-workflow — prompts are VERBATIM on both rails:
        each brief prompt is the per-task slot export + the Task-path
        payload's own instruction (the claim/submit-not-advance protocol)
        + the Task-path entry serialized EXACTLY as the Task path prints
        it. Nothing is rewritten."""
        # execute: per-brief entry = the wave entry
        wf_payload = json.loads(rails["caps"]["execute"]["wf"])
        task_payload = json.loads(rails["caps"]["execute"]["task"])
        assert wf_payload["wave"] == task_payload["wave"]
        for brief, entry in zip(wf_payload["workflow"]["args"]["briefs"],
                                task_payload["wave"]):
            tid = entry["task"]["id"]
            assert brief["prompt"].startswith(
                f"export TASKPLANE_TASK={tid}\n\n")
            assert task_payload["instruction"] in brief["prompt"]
            assert json.dumps(entry, indent=2) in brief["prompt"]
            # submit-not-advance, verbatim from the Task-path instruction
            assert "loop submit" in brief["prompt"]
            assert "The orchestrator alone runs the matching `loop gate`" \
                in brief["prompt"]
        # evaluate/fix: the entry is the WHOLE Task-path payload — the
        # prompt embeds the exact bytes the Task path printed on stdout
        for stage in ("evaluate", "fix"):
            task_out = rails["caps"][stage]["task"]
            task_payload = json.loads(task_out)
            wf = json.loads(rails["caps"][stage]["wf"])["workflow"]
            key = "verdicts" if stage == "fix" else "briefs"
            brief = wf["args"][key][0]
            assert brief["prompt"].startswith(
                "export TASKPLANE_TASK=t1\n\n")
            assert task_payload["instruction"] in brief["prompt"]
            assert json.dumps(task_payload, indent=2) in brief["prompt"], \
                f"{stage}: prompt must embed the Task-path stdout verbatim"
            assert "loop submit" in brief["prompt"]

    # ---------------------------------------------- C1 (R-0009)

    def _all_briefs(self, rails):
        """(stage, brief) for EVERY brief the three stage runs emit."""
        for stage in stage_fixture.STAGES:
            wf = json.loads(rails["caps"][stage]["wf"])["workflow"]
            key = "verdicts" if stage == "fix" else "briefs"
            for brief in wf["args"][key]:
                yield stage, brief

    def test_every_prompt_carries_both_activation_lines(self, rails):
        """C1 (R-0009): `export TASKPLANE_TASK=<slot>` is POSIX-only, so a
        cmd.exe agent silently never activates its slot and lands in the
        slot-less union screen. Every emitted stage prompt therefore also
        carries the LABELLED cmd form `set TASKPLANE_TASK=<slot>` for the
        SAME slot — the hooks.json commandWindows precedent. The POSIX
        line stays FIRST and unchanged in wording (the Windows line is an
        ADDITION, never a replacement), and both name the same slot: a
        drifting pair would activate the wrong contract on one host."""
        seen = 0
        for stage, brief in self._all_briefs(rails):
            slot, prompt = brief["id"], brief["prompt"]
            assert prompt.startswith(f"export TASKPLANE_TASK={slot}\n"), \
                f"{stage}: the POSIX line must stay first and unchanged"
            assert f"\nset TASKPLANE_TASK={slot}\n" in prompt, \
                f"{stage}: no cmd-form activation line for slot {slot}"
            label = prompt.split(f"\nset TASKPLANE_TASK={slot}\n")[0]
            assert "Windows" in label and "cmd.exe" in label, \
                f"{stage}: the cmd form must be LABELLED for its host"
            # exactly one activation line per form, and the SAME slot in
            # both — no second slot may reach either line
            found = re.findall(r"^(export|set) TASKPLANE_TASK=(\S+)$",
                               prompt, re.M)
            assert [f[0] for f in found] == ["export", "set"], \
                f"{stage}: expected exactly one POSIX then one cmd line"
            assert {f[1] for f in found} == {slot}, (stage, found)
            seen += 1
        assert seen == 4, "2 execute briefs + evaluate + fix"

    def test_activation_block_is_ascii_for_cmd_consoles(self, rails):
        """The activation block is read (and retyped) in a cmd.exe console
        whose default code page is not UTF-8 — it stays pure ASCII. Only
        the block is pinned; the Task-path bytes that follow are VERBATIM
        and not this test's to constrain."""
        for stage, brief in self._all_briefs(rails):
            block = brief["prompt"].split(
                f"\nset TASKPLANE_TASK={brief['id']}\n")[0]
            block.encode("ascii")     # raises → non-ASCII in the block

    def test_stage_agent_prompt_only_prepends_the_activation_block(self):
        """C1 is emitter-side and additive: everything after the activation
        block is still the Task-path instruction + the entry serialized
        exactly as the Task path prints it."""
        entry = {"task": {"id": "t7"}}
        prompt = cli._stage_agent_prompt("t7", "INSTRUCTION", entry)
        tail = "INSTRUCTION\n\n" + json.dumps(entry, indent=2)
        assert prompt.endswith(tail)
        head = prompt[:-len(tail)]
        assert head.startswith("export TASKPLANE_TASK=t7\n")
        assert "set TASKPLANE_TASK=t7\n" in head
        assert "TASKPLANE_TASK" not in tail

    def test_emitted_run_contains_no_gate_step(self, rails):
        """R-0004: no generated stage run contains an approval/gate step.
        The run STRUCTURE (workflow name, args keys, brief ids/worktrees)
        is scanned for gate verbs; the agent prompts are excluded because
        they carry the Task-path protocol text VERBATIM (which necessarily
        SAYS 'only the orchestrator gates') — that text is pinned verbatim
        above, and gates stay at conversation level by construction."""
        for stage in stage_fixture.STAGES:
            wf = json.loads(json.dumps(
                json.loads(rails["caps"][stage]["wf"])["workflow"]))
            key = "verdicts" if stage == "fix" else "briefs"
            for brief in wf["args"][key]:
                brief.pop("prompt")
            low = json.dumps(wf).lower()
            for verb in GATE_VERBS:
                assert verb not in low, (stage, verb)


# ---------------------------------------------------- kill-switch matrix


class TestStageKillSwitchMatrix:
    """The emitter's path choice is pinned to tp.workflow_available's truth
    table in BOTH directions: each case asserts the expected availability
    on the detector itself AND that the stage stdout carries workflow keys
    exactly when the detector says available — reuse, no drift."""

    @pytest.fixture()
    def wave_ws(self, tmp_path, monkeypatch):
        _clean_env(monkeypatch)
        ws = stage_fixture.build_repo(str(tmp_path))
        stage_fixture.start_loop(ws)
        return ws

    def _paths_match_detector(self, ws, expected_available):
        got = cli.workflow_available(ws)
        assert got["available"] is expected_available
        out = stage_fixture.capture_stage(ws, "execute")
        payload = json.loads(out)
        if expected_available:
            assert payload["dispatch_path"] == "workflow"
            assert payload["workflow"]["name"] == "execute-wave"
        else:
            for key in ("dispatch_path", "workflow", "reason"):
                assert key not in payload

    def test_falsey_spellings_always_force_the_task_path(self, wave_ws,
                                                         monkeypatch):
        for val in ("0", "false", "no", "off", "FALSE", " Off "):
            _clean_env(monkeypatch)
            monkeypatch.setenv("TASKPLANE_WORKFLOWS", val)
            monkeypatch.setenv("CLAUDE_CODE_WORKFLOWS", "1")  # loses
            self._paths_match_detector(wave_ws, False)

    def test_truthy_spellings_opt_in_to_the_workflow_path(self, wave_ws,
                                                          monkeypatch):
        for val in ("1", "true", "yes", "on", "TRUE"):
            _clean_env(monkeypatch)
            monkeypatch.setenv("TASKPLANE_WORKFLOWS", val)
            self._paths_match_detector(wave_ws, True)

    def test_codex_markers_always_win_over_opt_in(self, wave_ws,
                                                  monkeypatch):
        for marker in ("CODEX_HOME", "CODEX_THREAD_ID"):
            _clean_env(monkeypatch)
            monkeypatch.setenv(marker, "/x")
            monkeypatch.setenv("TASKPLANE_WORKFLOWS", "1")
            self._paths_match_detector(wave_ws, False)

    def test_claude_marker_enables_falsey_marker_does_not(self, wave_ws,
                                                          monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv("CLAUDE_CODE_WORKFLOWS", "1")
        self._paths_match_detector(wave_ws, True)
        for val in ("0", "false", "no", "off"):
            _clean_env(monkeypatch)
            monkeypatch.setenv("CLAUDE_CODE_WORKFLOWS", val)
            self._paths_match_detector(wave_ws, False)

    def test_conservative_default_is_the_task_path(self, wave_ws,
                                                   monkeypatch):
        _clean_env(monkeypatch)
        self._paths_match_detector(wave_ws, False)

def test_double_submit_same_outcome_is_a_noop(tmp_path, monkeypatch):
    """Engine-side idempotence (the other half of resume safety): a
    resumed agent that already submitted re-submits the SAME outcome and
    the engine returns the ORIGINAL submission unchanged — byte-equal,
    including fingerprint and submitted_at — pinned against loop.py's
    REAL submit through a throwaway workspace."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "stage-fixture")
    ws = stage_fixture.build_repo(str(tmp_path))
    stage_fixture.start_loop(ws)
    aws = os.path.join(ws, ".tp-work", "t1")
    stage_fixture._git(ws, "worktree", "add", "-q", aws, "-b", "tp/t1")
    assert loop.claim(ws, "t1", aws)["claimed"] == "t1"
    with open(os.path.join(aws, "src", "alpha", "m.py"), "w", encoding="utf-8") as f:
        f.write("x = 2\n")
    stage_fixture._git(aws, "add", "-A")
    stage_fixture._git(aws, "commit", "-qm", "t1")
    first = loop.submit(ws, "pass", task_id="t1")
    second = loop.submit(ws, "pass", task_id="t1")
    assert first["submitted"] and second["submitted"]
    assert second["submission"] == first["submission"]   # the no-op
    stored = next(t for t in loop.load(ws)["tasks"] if t["id"] == "t1")
    assert stored["_submission"] == first["submission"]
    # and neither submission advanced state — workers never transition
    assert first["transitioned"] is False
    assert second["transitioned"] is False
    assert loop.load(ws)["step"] == "execute"


# =====================================================================
# A6 (R-0007) — malformed wave entries degrade to the Task path
# E5 (R-0011) — un-slottable ids refuse emission at compose time
# =====================================================================


def _loop_cli(ws, *argv):
    """Run `tp loop ...` in-process capturing BOTH streams."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(["loop", "--workspace", ws, *argv])
    return rc, out.getvalue(), err.getvalue()


def _malformed_wave_payload():
    """A wave payload whose SECOND entry lost its task.id — the shape the
    A6 negative fixture pins (pre-A6 this KeyError'd the emitter)."""
    return {"step": "execute", "parallel": True,
            "instruction": "Dispatch ONE governed subagent per wave entry.",
            "wave": [
                {"task": {"id": "t1"}, "worktree": "w1"},
                {"task": {"scope": ["src/**"]}, "worktree": "w2"},
            ]}


class TestMalformedWaveEntryFailOpen:
    """A6: a malformed wave entry (missing task/id) must NEVER crash the
    emitter — it degrades to the Task path (the mandatory fallback can
    always print what the loop printed) with a traced reason."""

    @pytest.fixture()
    def ws(self, tmp_path, monkeypatch):
        _clean_env(monkeypatch)
        _freeze_cli_clock(monkeypatch)
        return stage_fixture.build_repo(str(tmp_path))

    def _run_with_payload(self, ws, payload, monkeypatch, *extra):
        monkeypatch.setattr(loop, "wave", lambda _ws, **_kwargs: payload)
        return _loop_cli(ws, "wave", *extra)

    def test_missing_task_id_degrades_to_task_path_stdout(self, ws,
                                                          monkeypatch):
        payload = _malformed_wave_payload()
        rc, out, err = self._run_with_payload(ws, payload, monkeypatch)
        assert rc == 0                                   # no crash, no error
        assert out == json.dumps(payload, indent=2) + "\n"  # Task-path bytes
        for key in ("dispatch_path", "workflow"):
            assert key not in json.loads(out)
        evs = _trace_events(ws, "stage_dispatch_path")
        assert evs
        _assert_audit_value(evs[-1], "path", "task")
        _assert_audit_value(
            evs[-1], "reason", _stage_problem(payload)["reason"])

    def test_degrade_wins_even_under_explicit_emit_workflow(self, ws,
                                                            monkeypatch):
        """--emit workflow cannot force a brief that cannot be composed:
        the malformed entry aborts workflow wrapping BEFORE the emit
        override is consulted — Task-path stdout, traced reason, exit 0."""
        monkeypatch.setenv("TASKPLANE_WORKFLOWS", "1")
        payload = _malformed_wave_payload()
        rc, out, err = self._run_with_payload(ws, payload, monkeypatch,
                                              "--emit", "workflow")
        assert rc == 0
        assert out == json.dumps(payload, indent=2) + "\n"
        evs = _trace_events(ws, "stage_dispatch_path")
        _assert_audit_value(evs[-1], "path", "task")
        _assert_audit_value(
            evs[-1], "reason", _stage_problem(payload)["reason"])

    def test_entry_not_a_dict_and_task_not_a_dict_both_degrade(self, ws,
                                                               monkeypatch):
        for bad in ("just-a-string", {"task": "t1"}, {"task": None}):
            payload = {"step": "execute", "parallel": True,
                       "instruction": "x",
                       "wave": [{"task": {"id": "t1"}}, bad]}
            rc, out, _ = self._run_with_payload(ws, payload, monkeypatch)
            assert rc == 0, bad
            assert json.loads(out) == payload, bad
            event = _trace_events(ws, "stage_dispatch_path")[-1]
            _assert_audit_value(event, "path", "task")
            _assert_audit_value(
                event, "reason", _stage_problem(payload)["reason"])

    def test_malformed_single_task_step_degrades_traced(self, ws,
                                                        monkeypatch):
        """The same validation covers the single-task step shapes: an
        evaluate payload whose task lost its id degrades to the Task
        path with the traced reason, never a silent skip or a crash."""
        payload = {"step": "evaluate", "instruction": "x",
                   "task": {"workspace": "w1"}}
        monkeypatch.setattr(loop, "next_action",
                            lambda _ws, rid=None, **_kwargs: payload)
        _clean_env(monkeypatch)
        rc, out, _ = _loop_cli(ws, "next")
        assert rc == 0
        assert json.loads(out) == payload
        evs = _trace_events(ws, "stage_dispatch_path")
        assert evs
        _assert_audit_value(evs[-1], "path", "task")
        assert evs[-1]["stage"] == "evaluate"
        _assert_audit_value(
            evs[-1], "reason", _stage_problem(payload)["reason"])

    def test_well_formed_wave_unchanged_on_the_workflow_rail(self, rails):
        """The A6 guard adds validation only: the frozen journey's
        well-formed emission is byte-covered by the existing golden and
        workflow-path pins (re-asserted here against the capture)."""
        payload = json.loads(rails["caps"]["execute"]["wf"])
        assert payload["dispatch_path"] == "workflow"
        assert len(payload["workflow"]["args"]["briefs"]) == 2


class TestSlotCharsetRefusalAtEmission:
    """E5: an id the emitter would embed into `export TASKPLANE_TASK=<id>`
    must already BE a valid slot (taskplane_lite._TASK_SLOT_RE — the ONE
    enforced charset). Invalid → refuse the WORKFLOW emission fail-closed,
    traced, BEFORE any prompt line is composed. Never sanitize.

    Phase 3 EM review (deep3 finding #2): the refusal is scoped to the
    workflow RAIL. The Task path composes no shell line, is the MANDATORY
    fallback and the only rail on Codex, so it degrades instead of being
    denied — see TestSlotCharsetNeverDeniesTheTaskPath below."""

    @pytest.fixture()
    def ws(self, tmp_path, monkeypatch):
        _clean_env(monkeypatch)
        _freeze_cli_clock(monkeypatch)
        return stage_fixture.build_repo(str(tmp_path))

    def _wave_with_id(self, tid):
        return {"step": "execute", "parallel": True, "instruction": "x",
                "wave": [{"task": {"id": tid}, "worktree": "w"}]}

    def test_shell_metacharacter_id_refuses_on_the_workflow_rail(
            self, ws, monkeypatch):
        monkeypatch.setenv("TASKPLANE_WORKFLOWS", "1")   # workflow-capable
        monkeypatch.setattr(loop, "wave",
                            lambda _ws, **_kwargs:
                            self._wave_with_id("t1;rm"))
        rc, out, err = _loop_cli(ws, "wave")             # default auto
        assert rc != 0
        assert out == ""                       # nothing composed, no payload
        # no COMPOSED export line anywhere (the reason may cite the
        # template form, but never an activation line carrying the id)
        assert "export TASKPLANE_TASK=t1;rm" not in out + err
        assert "t1;rm" in err                  # reason names the id...
        assert tp_lite._TASK_SLOT_RE.pattern in err   # ...and the charset
        evs = _trace_events(ws, "stage_dispatch_path")
        assert evs
        _assert_audit_value(evs[-1], "path", "refused")
        _assert_audit_value(evs[-1], "reason", _stderr_reason(err))

    def test_invalid_id_refuses_on_explicit_emit_workflow_too(self, ws,
                                                              monkeypatch):
        monkeypatch.setenv("TASKPLANE_WORKFLOWS", "1")
        monkeypatch.setattr(loop, "wave",
                            lambda _ws, **_kwargs:
                            self._wave_with_id("$(evil)"))
        rc, out, err = _loop_cli(ws, "wave", "--emit", "workflow")
        assert rc != 0
        assert out == ""
        assert "$(evil)" in err
        evs = _trace_events(ws, "stage_dispatch_path")
        _assert_audit_value(evs[-1], "path", "refused")
        _assert_audit_value(evs[-1], "reason", _stderr_reason(err))

    def test_single_task_step_ids_are_validated_too(self, ws, monkeypatch):
        monkeypatch.setenv("TASKPLANE_WORKFLOWS", "1")
        payload = {"step": "fix", "instruction": "x",
                   "task": {"id": "t 1", "workspace": "w"}}
        monkeypatch.setattr(loop, "next_action",
                            lambda _ws, rid=None, **_kwargs: payload)
        rc, out, err = _loop_cli(ws, "next")
        assert rc != 0 and out == ""
        assert "t 1" in err
        evs = _trace_events(ws, "stage_dispatch_path")
        _assert_audit_value(evs[-1], "path", "refused")
        _assert_audit_value(evs[-1], "reason", _stderr_reason(err))
        assert evs[-1]["stage"] == "fix"

    def test_slot_charset_accepts_safe_ids_and_rejects_unsafe_ids(self):
        """Accepted and rejected ids stay aligned at the public boundary."""
        assert cli._valid_slot_id("t1") and cli._valid_slot_id("fix.a-2_b")
        for bad in ("t1;rm", "$(x)", "a b", "", ".hidden", None, 7):
            assert not cli._valid_slot_id(bad), bad


class TestSlotCharsetNeverDeniesTheTaskPath:
    """Phase 3 EM review, deep3 finding #2 (HIGH regression): E5's charset
    refusal fired in `_emit_stage` BEFORE the rail was chosen and regardless
    of --emit, so a task id like `feat/login` made `loop wave` / `loop next`
    exit 1 with EMPTY stdout on every host — including a definitively
    workflow-less one, where the Task path is the only rail there is. The
    same docstring calls that path "the MANDATORY fallback and the only
    Codex path", and C3's own refusal text points refused users at it.

    The Task path interpolates the id into nothing (it prints the engine's
    own payload verbatim), so a slot-charset problem can never be a reason
    to deny it."""

    BAD = "feat/login"
    GOOD = "feat-login"

    @pytest.fixture()
    def ws(self, tmp_path, monkeypatch):
        _clean_env(monkeypatch)
        _freeze_cli_clock(monkeypatch)
        return stage_fixture.build_repo(str(tmp_path))

    def _payload(self, tid):
        return {"step": "evaluate", "instruction": "Evaluate the task.",
                "task": {"id": tid, "workspace": "w"}}

    def _run_next(self, ws, monkeypatch, tid, *argv):
        monkeypatch.setattr(loop, "next_action",
                            lambda _ws, rid=None, **_kwargs:
                            self._payload(tid))
        return _loop_cli(ws, "next", *argv)

    def test_task_path_emits_normally_for_a_bad_id_on_a_codex_host(
            self, ws, monkeypatch):
        monkeypatch.setenv("CODEX_HOME", "/codex")   # definitively no runtime
        rc, out, err = self._run_next(ws, monkeypatch, self.BAD)
        assert rc == 0, err
        assert json.loads(out)["task"]["id"] == self.BAD
        assert "dispatch_path" not in json.loads(out)
        evs = _trace_events(ws, "stage_dispatch_path")
        _assert_audit_value(evs[-1], "path", "task")  # NOT refused
        _assert_audit_value(
            evs[-1], "reason",
            _task_path_problem_reason(self._payload(self.BAD)))

    def test_task_path_bytes_differ_only_by_the_id(self, ws, monkeypatch):
        """The mandatory-fallback invariant: a bad id changes the id and
        nothing else about what the Task path prints."""
        for emit in ([], ["--emit", "task"]):
            _, bad_out, _ = self._run_next(ws, monkeypatch, self.BAD, *emit)
            _, good_out, _ = self._run_next(ws, monkeypatch, self.GOOD, *emit)
            assert bad_out and good_out
            assert bad_out.replace(self.BAD, self.GOOD) == good_out, emit

    def test_explicit_emit_task_is_never_refused_even_where_workflows_run(
            self, ws, monkeypatch):
        monkeypatch.setenv("TASKPLANE_WORKFLOWS", "1")
        rc, out, err = self._run_next(ws, monkeypatch, self.BAD,
                                      "--emit", "task")
        assert rc == 0, err
        assert json.loads(out)["task"]["id"] == self.BAD
        event = _trace_events(ws, "stage_dispatch_path")[-1]
        _assert_audit_value(event, "path", "task")
        _assert_audit_value(
            event, "reason",
            _task_path_problem_reason(self._payload(self.BAD)))

    def test_workflow_rail_still_refuses_the_same_id(self, ws, monkeypatch):
        """The guard itself is intact where it belongs: the workflow rail
        composes the export line, so it still refuses fail-closed."""
        monkeypatch.setenv("TASKPLANE_WORKFLOWS", "1")
        rc, out, err = self._run_next(ws, monkeypatch, self.BAD,
                                      "--emit", "workflow")
        assert rc != 0 and out == ""
        assert self.BAD in err
        event = _trace_events(ws, "stage_dispatch_path")[-1]
        _assert_audit_value(event, "path", "refused")
        _assert_audit_value(event, "reason", _stderr_reason(err))


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
