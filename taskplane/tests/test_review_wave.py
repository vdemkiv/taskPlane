"""Executable review-wave transport and public dispatch contracts."""
import base64
import contextlib
import io
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import taskplane_lite as tp  # noqa: E402
import tp as cli  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
WF = os.path.join(ROOT, "workflows", "review-wave.js")


def _node_workflow(args):
    """Execute the shipped module with observable host-runtime doubles."""
    with open(WF, "rb") as f:
        source = base64.b64encode(f.read()).decode("ascii")
    script = r"""
const mod = await import('data:text/javascript;base64,' + process.argv[1]);
const args = JSON.parse(process.argv[2]);
const events = [];
const calls = [];
const phase = (name) => events.push({kind: 'phase', name});
const agent = async (prompt, options) => {
  calls.push({prompt, options});
  events.push({kind: 'agent-start', label: options.label});
  await Promise.resolve();
  events.push({kind: 'agent-finish', label: options.label});
  return {label: options.label, result_path: options.resultPath};
};
const parallel = async (runs) => {
  events.push({kind: 'parallel', width: runs.length});
  return Promise.all(runs.map((run) => run()));
};
const result = await mod.default({args, agent, parallel, phase});
process.stdout.write(JSON.stringify({meta: mod.meta, result, calls, events}));
"""
    return subprocess.run(
        ["node", "--input-type=module", "-e", script, source,
         json.dumps(args)], check=False, capture_output=True, text=True,
        encoding="utf-8", errors="replace")


# ------------------------------------------------- capability detection


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


# ------------------------------------------------------------- CLI paths


def _repo(tmp_path) -> str:
    """A fixture change with real signals in it.

    v2.11.0 made the CLI route through the applicability engine, and the
    old fixture (`x = 1` -> `x = 2`) correctly routes NOTHING deep — a
    two-line constant edit summons no lens. That is the engine working, but
    it left these emit-parity tests with no briefs to compare. The diff is
    now a session-token change, which is what a review of this shape
    actually looks like: it carries auth/secret handling for security and
    an untested new path for qa, so the wave has deep briefs to pin.
    """
    ws = os.path.join(str(tmp_path), "ws")
    os.makedirs(os.path.join(ws, "src"))
    with open(os.path.join(ws, "src", "a.py"), "w", encoding="utf-8") as f:
        f.write("x = 1\n")
    for a in (["init", "-q"], ["config", "user.email", "e@e"],
              ["config", "user.name", "t"], ["add", "-A"],
              ["commit", "-qm", "base"]):
        subprocess.run(["git", *a], cwd=ws, capture_output=True)
    with open(os.path.join(ws, "src", "a.py"), "w", encoding="utf-8") as f:
        f.write("x = 2\n")
    with open(os.path.join(ws, "src", "auth.py"), "w", encoding="utf-8") as f:
        f.write("import hashlib, os\n\n"
                "SECRET_KEY = os.environ.get('SESSION_SECRET', '')\n\n"
                "def sign_session_token(user_id, password):\n"
                "    \"\"\"Sign a session token for an authenticated user.\"\"\"\n"
                "    raw = f'{user_id}:{password}:{SECRET_KEY}'\n"
                "    return hashlib.sha256(raw.encode()).hexdigest()\n\n"
                "def verify_session_token(user_id, password, token):\n"
                "    return sign_session_token(user_id, password) == token\n")
    return ws


def _dispatch(ws, *extra) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = cli.main(["lens", "dispatch", "--workspace", ws, *extra])
    return rc, out.getvalue()


def _trace_events(ws):
    p = os.path.join(ws, ".taskplane", "trace.jsonl")
    if not os.path.isfile(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


@pytest.fixture()
def bare_host(monkeypatch):
    """No codex vars, no workflow opt-in/marker — the conservative default."""
    _clean_env(monkeypatch)


class TestEmitTask:
    def test_default_bare_host_returns_a_decoded_task_manifest(
            self, tmp_path, bare_host):
        ws = _repo(tmp_path)
        rc, out = _dispatch(ws)
        assert rc == 0
        payload = json.loads(out)
        assert payload["nothing_to_review"] is False
        assert isinstance(payload["deep"], list)
        assert isinstance(payload["sweep"], dict)
        assert len(payload["settings_digest"]) == 64
        assert not ({"dispatch_path", "workflow", "reason"} & payload.keys())

    def test_codex_env_always_gets_the_task_manifest(self, tmp_path,
                                                     monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv("CODEX_HOME", "/x")
        monkeypatch.setenv("TASKPLANE_WORKFLOWS", "1")   # codex still wins
        ws = _repo(tmp_path)
        rc, out = _dispatch(ws)
        assert rc == 0
        payload = json.loads(out)
        assert not ({"dispatch_path", "workflow", "reason"} & payload.keys())


class TestEmitWorkflow:
    def test_public_payload_executes_every_slot_through_the_node_runtime(
            self, tmp_path, bare_host):
        ws = _repo(tmp_path)
        rc, out = _dispatch(ws, "--all", "--emit", "workflow")
        assert rc == 0
        payload = json.loads(out)
        assert payload["dispatch_path"] == "workflow"
        assert payload["reason"]
        wf = payload["workflow"]
        assert wf["name"] == "review-wave"
        args = wf["args"]
        briefs = payload["deep"] + [payload["sweep"]]
        slots = args["slots"]
        assert len(slots) == len(briefs) > 1
        assert len({slot["resume_identity"] for slot in slots}) == len(slots)
        for brief, slot in zip(briefs, slots):
            expected_id = brief.get("id") or "sweep"
            assert slot["slot_id"] == expected_id
            assert slot["prompt"] == brief["prompt"]
            assert slot["result_path"] == brief["output"]
            assert slot["lease"] == brief["contract"]
            assert slot["lease"]["task_slot"] == brief["task_slot"]
            for field in ("task_name", "agent", "role_marker", "model",
                          "model_tier", "reasoning_effort"):
                assert slot[field] == brief[field]
            assert slot["max_attempts"] >= 1
            assert slot["result_schema"]["required"] == ["lens", "findings"]
            assert len(slot["resume_identity"]) == 64

        completed = _node_workflow(args)
        assert completed.returncode == 0, completed.stderr
        runtime = json.loads(completed.stdout)
        assert runtime["meta"]["name"] == "review-wave"
        assert runtime["meta"]["phases"] == [
            {"title": "Lenses"}, {"title": "Merge"}]
        expected_calls = [{
            "prompt": slot["prompt"],
            "options": {
                "label": "lens:" + slot["slot_id"], "phase": "Lenses",
                "schema": slot["result_schema"],
                "resumeKey": slot["resume_identity"],
                "resultPath": slot["result_path"], "lease": slot["lease"],
                "maxAttempts": slot["max_attempts"],
                "taskName": slot["task_name"], "agent": slot["agent"],
                "roleMarker": slot["role_marker"], "model": slot["model"],
                "modelTier": slot["model_tier"],
                "reasoningEffort": slot["reasoning_effort"],
            },
        } for slot in slots]
        assert runtime["calls"] == expected_calls
        labels = [call["options"]["label"] for call in expected_calls]
        events = runtime["events"]
        assert events[0] == {"kind": "phase", "name": "Lenses"}
        assert events[1] == {"kind": "parallel", "width": len(slots)}
        assert events[-1] == {"kind": "phase", "name": "Merge"}
        starts = [event["label"] for event in events
                  if event["kind"] == "agent-start"]
        finishes = [event["label"] for event in events
                    if event["kind"] == "agent-finish"]
        assert starts == labels and finishes == labels
        first_finish = next(i for i, event in enumerate(events)
                            if event["kind"] == "agent-finish")
        assert all(i < first_finish for i, event in enumerate(events)
                   if event["kind"] == "agent-start")
        expected_receipts = [{"label": "lens:" + slot["slot_id"],
                              "result_path": slot["result_path"]}
                             for slot in slots]
        assert runtime["result"] == {
            "receipts": expected_receipts,
            "settings_digest": args["settings_digest"],
        }

    def test_runtime_refuses_a_workflow_without_governed_slots(self):
        completed = _node_workflow({"settings_digest": "0" * 64})
        assert completed.returncode != 0
        assert "lacks canonical slots" in completed.stderr

    def test_auto_picks_workflow_when_opted_in(self, tmp_path, monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv("TASKPLANE_WORKFLOWS", "1")
        ws = _repo(tmp_path)
        rc, out = _dispatch(ws)
        assert rc == 0
        payload = json.loads(out)
        assert payload["dispatch_path"] == "workflow"
        assert payload["workflow"]["name"] == "review-wave"


class TestPathTracing:
    def test_task_path_is_traced_not_printed(self, tmp_path, bare_host):
        ws = _repo(tmp_path)
        _, out = _dispatch(ws, "--emit", "task")
        evs = [e for e in _trace_events(ws)
               if e["event"] == "review_dispatch_path"]
        assert evs and evs[-1]["path"] == tp._audit_minimized("task")
        assert evs[-1].get("reason")
        assert "dispatch_path" not in json.loads(out)

    def test_workflow_path_is_traced(self, tmp_path, bare_host):
        ws = _repo(tmp_path)
        _dispatch(ws, "--emit", "workflow")
        evs = [e for e in _trace_events(ws)
               if e["event"] == "review_dispatch_path"]
        assert evs and evs[-1]["path"] == tp._audit_minimized("workflow")
        assert evs[-1].get("reason")
