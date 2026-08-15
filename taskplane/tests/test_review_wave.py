"""t5 (R-0002) — review-wave plugin workflow + capability detection +
MANDATORY byte-identical Task-dispatch fallback.

Pins:
  * workflows/review-wave.js ships in the plugin, meta is a PURE literal,
    the script is deterministic (no Date.now/Math.random/dynamic import)
    and honest to the Dynamic Workflows primitives (agent/parallel/phase/
    args) — static checks only, CI has no JS runtime;
  * the workflow consumes the canonical ReviewKernel slot manifest and each
    brief's leased schema/resume/result identity without remapping findings;
  * workflow_available(): conservative, env-based — Codex ALWAYS
    unavailable, TASKPLANE_WORKFLOWS=1 opt-in, =0 kill-switch, default
    unset with no marker = unavailable;
  * `tp lens dispatch --emit task` stdout is BYTE-IDENTICAL to the
    pre-change dispatch payload (json.dumps(lens.dispatch_briefs(...))) —
    Codex parity, R-0002's core promise; default (auto, bare env) equals
    --emit task byte-for-byte;
  * `--emit workflow` carries every deep brief with slots/contracts intact
    as workflow args; the chosen path is TRACED (review_dispatch_path) on
    BOTH paths, never printed on the task path;
  * no gate is reachable only via workflows: loop.py/lens.py carry zero
    workflow coupling — the em step instruction text is untouched.
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lens  # noqa: E402
import tp as cli  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
WF = os.path.join(ROOT, "workflows", "review-wave.js")


def _js() -> str:
    with open(WF, encoding="utf-8") as f:
        return f.read()


def _meta_block(src: str) -> str:
    m = re.search(r"const\s+meta\s*=\s*\{(.*?)\n\};", src, re.S)
    assert m, "workflows/review-wave.js must declare `const meta = {...};`"
    return m.group(1)


# ------------------------------------------------------------ workflow file


class TestWorkflowFile:
    def test_ships_in_plugin_workflows_dir(self):
        assert os.path.isfile(WF)

    def test_meta_is_a_pure_literal(self):
        meta = _meta_block(_js())
        # A pure literal has no calls, no interpolation, no identifiers
        # doing work — the runtime must be able to read it without
        # executing the script body.
        assert "(" not in meta and ")" not in meta
        assert "${" not in meta and "`" not in meta
        assert "'review-wave'" in meta
        assert "'Lenses'" in meta and "'Merge'" in meta
        assert re.search(r"description:\s*'[^']+'", meta)

    def test_deterministic_no_clock_no_random_no_dynamic_import(self):
        src = _js()
        assert "Date.now" not in src and "new Date" not in src
        assert "Math.random" not in src
        assert "import(" not in src and "require(" not in src
        assert "process." not in src

    def test_honest_to_dynamic_workflow_primitives(self):
        src = _js()
        assert "parallel(" in src
        assert "agent(" in src
        assert "phase('Lenses')" in src and "phase('Merge')" in src
        assert "args.slots" in src
        assert "'lens:' + b.slot_id" in src
        for field in ("b.result_schema", "b.resume_identity",
                      "b.result_path", "b.lease"):
            assert field in src

    def test_slot_contract_is_passed_to_the_agent_unchanged(self):
        src = _js()
        assert "schema: b.result_schema" in src
        assert "resumeKey: b.resume_identity" in src
        assert "resultPath: b.result_path" in src
        assert "lease: b.lease" in src

    def test_lens_prompts_instruct_every_schema_field(self):
        """EM blocker (v3 Phase 1): FINDINGS_SCHEMA requires `class` per
        finding, so the PROMPTS on BOTH dispatch paths must instruct it —
        otherwise the workflow path schema-rejects output the Task path
        accepts, breaking R-0002's parity promise."""
        entry = {"id": "security", "name": "Security",
                 "looks_for": "x", "checks": ["c1"]}
        deep_prompt = lens._lens_prompt(entry, "main")
        for field in ("severity", "class", "file", "line", "title",
                      "scenario", "fix"):
            assert f'"{field}"' in deep_prompt, \
                f"deep lens prompt no longer instructs {field!r}"
        assert "regression|pre-existing|observation" in deep_prompt
        # sweep prompt carries the class instruction too
        routing = {"lenses": [{"id": "i18n", "name": "i18n",
                               "tier": "sweep"}],
                   "context": {"changed_files": 1}}
        briefs = lens.dispatch_briefs(routing, base="main")
        assert briefs["sweep"] is not None
        assert "class" in briefs["sweep"]["prompt"]
        assert "regression|pre-existing|observation" in \
            briefs["sweep"]["prompt"]

    def test_returns_transport_receipts_only(self):
        src = _js()
        assert "receipts:" in src
        assert "per_lens" not in src
        assert "routing_decision" not in src


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


class TestEmitTaskByteIdentity:
    def test_emit_task_equals_pre_change_dispatch_payload(self, tmp_path,
                                                          bare_host):
        """--emit task stdout must be EXACTLY json.dumps of the untouched
        lens.dispatch_briefs payload — the pre-change bytes. No new keys,
        no dispatch_path/reason/workflow on stdout (Codex parity)."""
        ws = _repo(tmp_path)
        rc, out = _dispatch(ws, "--emit", "task")
        assert rc == 0
        # stage="review" mirrors what cmd_lens now asks for (v2.11.0) —
        # the point of this test is that --emit task adds NO keys to the
        # payload, not which router produced it.
        # v2.13.0: dispatch writes the shared review context once and the
        # briefs cite it, so the comparison must be built the same way —
        # this test is about --emit task adding NO keys to the payload, not
        # about how the briefs got their context.
        import review as rvmod
        routing = lens.route_git_diff(ws, base="HEAD", task_type=None,
                                      only=None, skip=None, breadth="routed",
                                      stage="review")
        ctx = rvmod.write_context(
            ws, diff=cli.tp_target_diff(ws, "HEAD")[1], blast_radius="")
        expected = json.dumps(
            lens.dispatch_briefs(routing, base="HEAD", context_paths=ctx),
            indent=2) + "\n"
        assert out == expected  # byte-for-byte
        payload = json.loads(out)
        assert "dispatch_path" not in payload
        assert "workflow" not in payload
        assert "reason" not in payload

    def test_default_auto_on_bare_host_is_byte_identical_to_task(
            self, tmp_path, bare_host):
        ws = _repo(tmp_path)
        _, out_default = _dispatch(ws)
        _, out_task = _dispatch(ws, "--emit", "task")
        assert out_default == out_task

    def test_codex_env_always_gets_task_bytes(self, tmp_path, monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv("CODEX_HOME", "/x")
        monkeypatch.setenv("TASKPLANE_WORKFLOWS", "1")   # codex still wins
        ws = _repo(tmp_path)
        rc, out = _dispatch(ws)
        assert rc == 0
        assert "dispatch_path" not in json.loads(out)


class TestEmitWorkflow:
    def test_workflow_payload_carries_every_brief_and_slot(self, tmp_path,
                                                           bare_host):
        ws = _repo(tmp_path)
        rc, out = _dispatch(ws, "--emit", "workflow")
        assert rc == 0
        payload = json.loads(out)
        assert payload["dispatch_path"] == "workflow"
        assert payload["reason"]
        wf = payload["workflow"]
        assert wf["name"] == "review-wave"
        args = wf["args"]
        # args IS the dispatch payload: identical briefs, slots intact
        assert args["deep"] == payload["deep"]
        assert args["sweep"] == payload["sweep"]
        assert args["deep"], "fixture must route at least one deep lens"
        for b in args["deep"]:
            assert b["task_slot"] == f"lens-{b['id']}"
            assert b["contract"]["task_slot"] == b["task_slot"]
            assert f"export TASKPLANE_TASK={b['task_slot']}" in b["prompt"]
            assert b["output"] == f".em-review/lens-{b['id']}/findings.json"

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
        assert evs and evs[-1]["path"] == "task"
        assert evs[-1].get("reason")
        assert "dispatch_path" not in out          # stdout stays pre-change

    def test_workflow_path_is_traced(self, tmp_path, bare_host):
        ws = _repo(tmp_path)
        _dispatch(ws, "--emit", "workflow")
        evs = [e for e in _trace_events(ws)
               if e["event"] == "review_dispatch_path"]
        assert evs and evs[-1]["path"] == "workflow"
        assert evs[-1].get("reason")


# ------------------------------------------- no gate only via workflows


class TestNoWorkflowOnlyGate:
    def test_loop_and_lens_have_zero_workflow_coupling(self):
        """R-W2: no code path where workflows are the only route to a gate.
        The loop engine and the brief builder must not know workflows
        exist — the em step instruction text is untouched by t5."""
        for mod in ("loop.py", "lens.py"):
            with open(os.path.join(ROOT, "taskplane", mod), encoding="utf-8") as f:
                src = f.read()
            assert "workflow" not in src.lower(), \
                f"taskplane/{mod} must stay workflow-agnostic"
            assert "review-wave" not in src

    def test_em_step_uses_selective_dispatch(self):
        with open(os.path.join(ROOT, "taskplane", "loop.py"), encoding="utf-8") as f:
            src = f.read()
        assert '"all" if step == "em" else "routed"' not in src
