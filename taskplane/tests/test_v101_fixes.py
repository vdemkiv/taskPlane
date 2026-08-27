"""v1.0.1 fixes — regression tests.

3.4  FUSE-safe removal: clear()/legacy-migrate/cmd_clear must survive
     filesystems that forbid unlink (rename-tombstone fallback).
3.2  Dispatch expectation queue + verify-dispatch audit.
3.1  screen-dispatch hook: inert by default; warn/strict on mismatch.
3.5  done/external statuses satisfy deps; resolve defer.
3.7  onboard report exposes resolved model tiers.
"""
import builtins
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loop  # noqa: E402
import taskplane_lite as tp  # noqa: E402

TPPY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "tp.py")


def _git(ws, *a):
    subprocess.run(["git", *a], cwd=ws, capture_output=True)


def _repo(tmp):
    ws = os.path.join(tmp, "ws")
    os.makedirs(os.path.join(ws, "src"))
    open(os.path.join(ws, "src", "a.py"), "w", encoding="utf-8").write("x = 1\n")
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "e@e")
    _git(ws, "config", "user.name", "t")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "base")
    return ws


def _deny_unlink():
    """Patch os.remove/os.unlink to behave like a no-unlink FUSE mount."""
    def boom(*a, **k):
        raise PermissionError(1, "Operation not permitted")
    return mock.patch.multiple(os, remove=boom, unlink=boom)


class TestSafeRemove(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_clear_survives_no_unlink_mount(self):
        ws = _repo(self.tmp)
        tp.activate(ws, {"task_id": "t", "goal": "g",
                         "coding": {"scope_paths": ["src/**"],
                                    "dod": {"test_command": None}}})
        path = os.path.join(tp.tp_dir(ws), "active_contract.json")
        self.assertTrue(os.path.exists(path))
        with _deny_unlink():
            tp.clear(ws)                      # must not raise
        self.assertFalse(os.path.exists(path))
        self.assertIsNone(tp.load_active(ws))

    def test_gate_advances_on_no_unlink_mount(self):
        ws = _repo(self.tmp)
        loop.init(ws, "g")
        os.makedirs(os.path.join(ws, 'specs'), exist_ok=True); open(os.path.join(ws, 'specs', 'spec.md'), 'w', encoding="utf-8").write('# spec\n')
        with _deny_unlink():
            out = loop.gate(ws, "pass")       # pm -> plan calls tp.clear
        self.assertNotIn("error", out)
        self.assertEqual(loop.load(ws)["step"], "plan")

    def test_safe_remove_plain_delete_still_works(self):
        p = os.path.join(self.tmp, "f")
        open(p, "w", encoding="utf-8").write("x")
        tp.safe_remove(p)
        self.assertFalse(os.path.exists(p))
        tp.safe_remove(p)                     # missing: no raise


class TestDispatchQueue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_loop_next_records_expectation(self):
        ws = _repo(self.tmp)
        loop.init(ws, "g")
        os.makedirs(os.path.join(ws, 'specs'), exist_ok=True); open(os.path.join(ws, 'specs', 'spec.md'), 'w', encoding="utf-8").write('# spec\n')
        loop.next_action(ws)                  # pm brief
        q = tp._load_queue(tp._dispatch_path(ws, "expected_dispatch.json"))
        self.assertTrue(q)
        self.assertEqual(q[-1]["agent"], "tp-product")
        self.assertIn(q[-1]["model_tier"], tp.MODEL_TIERS)
        self.assertRegex(q[-1]["task_name"], r"^[a-z0-9_]+$")
        self.assertEqual(q[-1]["reasoning_effort"], "high")

    def test_consume_matches_oldest_by_agent_and_namespaced(self):
        ws = _repo(self.tmp)
        os.makedirs(tp.tp_dir(ws), exist_ok=True)
        tp.record_expected_dispatch(ws, "lens", "tp-lens", "cheap", "haiku",
                                    ref="sweep")
        tp.record_expected_dispatch(ws, "lens", "tp-lens", "deep", None,
                                    ref="security")
        e = tp.consume_expectation(ws, "taskplane:tp-lens")
        self.assertEqual(e["ref"], "sweep")   # oldest first
        e2 = tp.consume_expectation(ws, "tp-lens")
        self.assertEqual(e2["ref"], "security")
        self.assertIsNone(tp.consume_expectation(ws, "tp-lens"))

    def test_report_flags_hook_inactive(self):
        ws = _repo(self.tmp)
        os.makedirs(tp.tp_dir(ws), exist_ok=True)
        tp.record_expected_dispatch(ws, "step", "tp-executor", "standard",
                                    None, ref="t1")
        rep = tp.dispatch_report(ws)
        self.assertFalse(rep["hook_active"])
        self.assertIn("TASKPLANE_ENFORCE_DISPATCH", rep["note"])

    def test_report_mismatch(self):
        ws = _repo(self.tmp)
        os.makedirs(tp.tp_dir(ws), exist_ok=True)
        exp = {"kind": "lens", "agent": "tp-lens", "ref": "sweep",
               "model_tier": "cheap", "model": "haiku"}
        tp.record_observed_dispatch(ws, "tp-lens", None, exp, ok=False)
        rep = tp.dispatch_report(ws)
        self.assertTrue(rep["hook_active"])
        self.assertEqual(len(rep["mismatches"]), 1)
        self.assertEqual(rep["mismatches"][0]["expected_model"], "haiku")


class TestScreenDispatchHook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = _repo(self.tmp)
        os.makedirs(tp.tp_dir(self.ws), exist_ok=True)

    def _run(self, event, env_mode=None):
        env = {**os.environ}
        env.pop("TASKPLANE_ENFORCE_DISPATCH", None)
        if env_mode:
            env["TASKPLANE_ENFORCE_DISPATCH"] = env_mode
        return subprocess.run(
            [sys.executable, TPPY, "screen-dispatch"],
            input=json.dumps(event), text=True, capture_output=True, env=env, encoding="utf-8", errors="replace")

    def _event(self, model=None):
        ti = {"subagent_type": "taskplane:tp-lens", "prompt": "x"}
        if model is not None:
            ti["model"] = model
        return {"tool_name": "Task", "tool_input": ti, "cwd": self.ws}

    def _codex_event(self, task_name, model=None, effort=None,
                     role="tp-executor"):
        ti = {"task_name": task_name,
              "message": tp.role_marker(role) + "\nx"}
        if model is not None:
            ti["model"] = model
        if effort is not None:
            ti["reasoning_effort"] = effort
        return {"turn_id": "turn-1", "tool_name": "spawn_agent",
                "tool_input": ti, "cwd": self.ws}

    def test_inert_without_env(self):
        tp.record_expected_dispatch(self.ws, "lens", "tp-lens", "cheap",
                                    "haiku", ref="sweep")
        r = self._run(self._event(model=None))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_native_spawn_observation_is_always_on_and_idempotent(self):
        loop.init(self.ws, "native observation")
        with loop.mutate(self.ws) as state:
            state["tasks"] = [{
                "id": "t1", "scope": ["src/**"], "tests": "true",
                "deps": [], "status": "pending",
            }]
        task_name = tp.dispatch_task_name("step", "tp-executor", "t1")

        def emit_and_observe():
            tp.record_expected_dispatch(
                self.ws, "step", "tp-executor", "standard", None,
                ref="t1", task_name=task_name, reasoning_effort="medium",
                intent_id="native-intent-t1")
            result = self._run(
                self._codex_event(task_name, effort="medium"))
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "")

        emit_and_observe()
        emit_and_observe()
        ledger = loop.load(self.ws)["dispatch_telemetry"]
        self.assertEqual(len(ledger["bindings"]), 1)
        self.assertEqual(
            ledger["bindings"][0]["dispatch_id"], "native-intent-t1")
        self.assertEqual(ledger["bindings"][0]["task_id"], "t1")
        self.assertEqual(ledger["bindings"][0]["thread_id"], task_name)

    def test_severed_native_observation_is_traced_and_budget_fails_closed(self):
        loop.init(self.ws, "severed native observation", parallel=True)
        with loop.mutate(self.ws) as state:
            state["step"] = "execute"
            state["tasks"] = [{
                "id": "t1", "scope": ["src/**"], "tests": "true",
                "deps": [], "status": "pending",
            }]
            state["dispatch_telemetry"] = {"schema": "severed"}
        task_name = tp.dispatch_task_name("step", "tp-executor", "t1")
        tp.record_expected_dispatch(
            self.ws, "step", "tp-executor", "standard", None,
            ref="t1", task_name=task_name, reasoning_effort="medium",
            intent_id="native-intent-t1")

        result = self._run(self._codex_event(task_name, effort="medium"))
        self.assertEqual(result.returncode, 0)
        denied = json.loads(result.stdout)
        self.assertEqual(
            denied["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn(
            "native dispatch telemetry failed closed",
            denied["hookSpecificOutput"]["permissionDecisionReason"])
        trace_path = os.path.join(tp.tp_dir(self.ws), "trace.jsonl")
        with open(trace_path, encoding="utf-8") as handle:
            trace = [json.loads(line) for line in handle if line.strip()]
        self.assertTrue(any(
            row.get("event") == "native_dispatch_telemetry_unavailable"
            and row.get("task") == "t1" for row in trace))
        wave = loop.wave(self.ws)
        self.assertIn("dispatch telemetry refused before wave", wave["error"])

    def test_warn_on_missing_model(self):
        tp.record_expected_dispatch(self.ws, "lens", "tp-lens", "cheap",
                                    "haiku", ref="sweep")
        r = self._run(self._event(model=None), env_mode="warn")
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertIn("haiku", out["systemMessage"])
        self.assertIn("sweep", out["systemMessage"])

    def test_strict_denies_mismatch(self):
        tp.record_expected_dispatch(self.ws, "lens", "tp-lens", "cheap",
                                    "haiku", ref="sweep")
        r = self._run(self._event(model="opus"), env_mode="strict")
        out = json.loads(r.stdout)
        self.assertEqual(
            out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_legacy_strict_mismatch_does_not_consume_retry(self):
        tp.record_expected_dispatch(self.ws, "lens", "tp-lens", "cheap",
                                    "haiku", ref="sweep")
        denied = self._run(self._event(model="opus"), env_mode="strict")
        self.assertEqual(json.loads(denied.stdout)["hookSpecificOutput"]
                         ["permissionDecision"], "deny")
        exact = self._run(self._event(model="haiku"), env_mode="strict")
        self.assertEqual(exact.stdout.strip(), "")

    def test_match_and_inherit_expected_are_silent(self):
        tp.record_expected_dispatch(self.ws, "lens", "tp-lens", "cheap",
                                    "haiku", ref="sweep")
        r = self._run(self._event(model="haiku"), env_mode="warn")
        self.assertEqual(r.stdout.strip(), "")
        tp.record_expected_dispatch(self.ws, "step", "tp-executor",
                                    "standard", None, ref="t1")
        ev = self._event(model=None)
        ev["tool_input"]["subagent_type"] = "taskplane:tp-executor"
        r = self._run(ev, env_mode="warn")
        self.assertEqual(r.stdout.strip(), "")   # expected None = inherit ok

    def test_unexpected_agent_is_silent(self):
        r = self._run(self._event(model=None), env_mode="warn")
        self.assertEqual(r.stdout.strip(), "")

    def test_native_codex_exact_fields_pass_strict(self):
        name = tp.dispatch_task_name("step", "tp-executor", "t1")
        tp.record_expected_dispatch(
            self.ws, "step", "tp-executor", "standard", None, ref="t1",
            task_name=name, reasoning_effort="medium")
        r = self._run(self._codex_event(name, effort="medium"),
                      env_mode="strict")
        self.assertEqual(r.stdout.strip(), "")

    def test_native_codex_reasoning_mismatch_is_denied(self):
        name = tp.dispatch_task_name("lens", "tp-lens", "security")
        tp.record_expected_dispatch(
            self.ws, "lens", "tp-lens", "deep", None, ref="security",
            task_name=name, reasoning_effort="high")
        r = self._run(self._codex_event(name, effort="low", role="tp-lens"),
                      env_mode="strict")
        out = json.loads(r.stdout)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"],
                         "deny")
        self.assertIn("reasoning_effort=high", out["hookSpecificOutput"]
                      ["permissionDecisionReason"])
        retry = self._run(self._codex_event(name, effort="high", role="tp-lens"),
                          env_mode="strict")
        self.assertEqual(retry.stdout.strip(), "",
                         "a rejected dispatch must not consume its brief")

    def test_native_codex_model_and_declared_role_mismatch_are_denied(self):
        name = tp.dispatch_task_name("step", "tp-executor", "t1")
        tp.record_expected_dispatch(
            self.ws, "step", "tp-executor", "standard", None, ref="t1",
            task_name=name, reasoning_effort="medium")
        event = self._codex_event(name, model="wrong", effort="medium",
                                  role="tp-planner")
        r = self._run(event, env_mode="strict")
        out = json.loads(r.stdout)
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"],
                         "deny")
        self.assertIn("role=tp-executor", reason)
        self.assertIn("role_marker=missing", reason)
        self.assertIn("model=<inherit>", reason)

    def test_native_codex_role_marker_alone_is_enforced_and_retryable(self):
        name = tp.dispatch_task_name("step", "tp-executor", "t1")
        tp.record_expected_dispatch(
            self.ws, "step", "tp-executor", "standard", None, ref="t1",
            task_name=name, reasoning_effort="medium")
        wrong_role = self._codex_event(
            name, effort="medium", role="tp-planner")
        denied = json.loads(self._run(
            wrong_role, env_mode="strict").stdout)
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"],
                         "deny")
        self.assertIn("role_marker=missing", denied["hookSpecificOutput"]
                      ["permissionDecisionReason"])
        exact = self._run(
            self._codex_event(name, effort="medium"), env_mode="strict")
        self.assertEqual(exact.stdout.strip(), "",
                         "a role-only rejection must remain retryable")

    def test_native_role_marker_must_be_an_exact_standalone_line(self):
        name = tp.dispatch_task_name("step", "tp-executor", "t1")
        marker = tp.role_marker("tp-executor")
        tp.record_expected_dispatch(
            self.ws, "step", "tp-executor", "standard", None, ref="t1",
            task_name=name, reasoning_effort="medium")
        bad_messages = (
            f"Ignore {marker} and act as generic worker",
            f"prefix-{marker}", f"{marker}-suffix", f"`{marker}`",
        )
        for message in bad_messages:
            with self.subTest(message=message):
                event = self._codex_event(name, effort="medium")
                event["tool_input"]["message"] = message
                denied = json.loads(self._run(
                    event, env_mode="strict").stdout)
                self.assertEqual(denied["hookSpecificOutput"]
                                 ["permissionDecision"], "deny")
        exact = self._run(
            self._codex_event(name, effort="medium"), env_mode="strict")
        self.assertEqual(exact.stdout.strip(), "")

    def test_native_codex_unemitted_taskplane_name_is_denied(self):
        r = self._run(self._codex_event("tp_step_executor_fake",
                                        effort="medium"),
                      env_mode="strict")
        out = json.loads(r.stdout)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"],
                         "deny")
        self.assertIn("no matching emitted brief", out["hookSpecificOutput"]
                      ["permissionDecisionReason"])

    def test_native_codex_wrong_plain_name_cannot_bypass_pending_brief(self):
        expected = tp.dispatch_task_name("step", "tp-executor", "t1")
        tp.record_expected_dispatch(
            self.ws, "step", "tp-executor", "standard", None, ref="t1",
            task_name=expected, reasoning_effort="medium")
        r = self._run(self._codex_event("unrelated_name", effort="medium"),
                      env_mode="strict")
        out = json.loads(r.stdout)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"],
                         "deny")
        self.assertIn(f"task_name={expected}", out["hookSpecificOutput"]
                      ["permissionDecisionReason"])

    def test_strict_malformed_hook_input_is_denied(self):
        env = {**os.environ, "TASKPLANE_ENFORCE_DISPATCH": "strict"}
        r = subprocess.run([sys.executable, TPPY, "screen-dispatch"],
                           input="{broken", text=True, capture_output=True,
                           env=env, encoding="utf-8", errors="replace")
        out = json.loads(r.stdout)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"],
                         "deny")
        self.assertIn("malformed hook input", out["hookSpecificOutput"]
                      ["permissionDecisionReason"])

    def test_strict_corrupt_expectation_queue_is_denied(self):
        path = tp._dispatch_path(self.ws, "expected_dispatch.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{broken")
        r = self._run(self._codex_event("worker", effort="medium"),
                      env_mode="strict")
        out = json.loads(r.stdout)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"],
                         "deny")
        self.assertIn("strict verification", out["hookSpecificOutput"]
                      ["permissionDecisionReason"])

    def test_audit_write_failure_leaves_expectation_pending(self):
        name = tp.dispatch_task_name("step", "tp-executor", "t1")
        tp.record_expected_dispatch(
            self.ws, "step", "tp-executor", "standard", None, ref="t1",
            task_name=name, reasoning_effort="medium")
        expected = tp.peek_expectation(self.ws, name, strict=True)
        with mock.patch.object(tp, "_save_queue",
                               side_effect=OSError("audit disk full")):
            with self.assertRaises(OSError):
                tp.commit_dispatch_verification(
                    self.ws, name, None, expected, True, "medium",
                    strict=True)
        retry = tp.peek_expectation(self.ws, name, strict=True)
        self.assertIsNotNone(retry)
        self.assertFalse(retry["matched"])


class TestCodexReasoningTiers(unittest.TestCase):
    def test_defaults_and_valid_override(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            for tier in tp.MODEL_TIERS:
                os.environ.pop("TASKPLANE_REASONING_" + tier.upper(), None)
            self.assertEqual([tp.reasoning_for_tier(t) for t in tp.MODEL_TIERS],
                             ["low", "medium", "high"])
            os.environ["TASKPLANE_REASONING_DEEP"] = "xhigh"
            self.assertEqual(tp.reasoning_for_tier("deep"), "xhigh")

    def test_invalid_override_falls_back_without_provider_default(self):
        with mock.patch.dict(os.environ,
                             {"TASKPLANE_REASONING_CHEAP": "turbo"}):
            self.assertEqual(tp.reasoning_for_tier("cheap"), "low")

    def test_task_name_is_stable_valid_and_bounded(self):
        a = tp.dispatch_task_name("step", "tp-product", "Feature/One")
        b = tp.dispatch_task_name("step", "tp-product", "Feature/One")
        long_name = tp.dispatch_task_name("lens", "tp-lens", "x" * 200)
        self.assertEqual(a, b)
        self.assertRegex(a, r"^[a-z0-9_]+$")
        self.assertLessEqual(len(long_name), 64)

    def test_lossy_normalization_cannot_collide(self):
        hyphen = tp.dispatch_task_name("step", "tp-executor", "api-v2")
        underscore = tp.dispatch_task_name("step", "tp-executor", "api_v2")
        self.assertNotEqual(hyphen, underscore)


class TestCodexParallelWaveDispatch(unittest.TestCase):
    """The main parallel path emits and registers exact native identities."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = _repo(self.tmp)
        loop.init(self.ws, "parallel native dispatch", parallel=True)
        state = loop.load(self.ws)
        state["step"] = "execute"
        state["tasks"] = [{
            "id": "api-v2", "scope": ["src/**"], "tests": "true",
            "deps": [], "status": "pending", "model": "deep",
        }]
        loop.save(self.ws, state)

    def _wave(self):
        env = {**os.environ, "TASKPLANE_WORKFLOWS": "0"}
        result = subprocess.run(
            [sys.executable, TPPY, "loop", "--workspace", self.ws,
             "wave", "--emit", "task"],
            text=True, capture_output=True, env=env, encoding="utf-8", errors="replace")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_wave_fields_register_and_strict_dispatch_retries(self):
        payload = self._wave()
        self.assertEqual(len(payload["wave"]), 1)
        entry = payload["wave"][0]
        expected_name = tp.dispatch_task_name(
            "step", "tp-executor", "api-v2")
        self.assertEqual(entry["task_name"], expected_name)
        self.assertEqual(entry["role"], "tp-executor")
        self.assertEqual(entry["role_marker"],
                         "taskplane-role:tp-executor")
        self.assertTrue(os.path.isabs(entry["role_instructions"]))
        role_path = os.path.normpath(entry["role_instructions"])
        self.assertEqual(os.path.basename(role_path), "tp-executor.md")
        self.assertEqual(os.path.basename(os.path.dirname(role_path)),
                         "agents")
        self.assertTrue(os.path.isfile(role_path))
        self.assertEqual(entry["model_tier"], "deep")
        self.assertEqual(entry["reasoning_effort"], "high")

        repeated = self._wave()
        self.assertEqual(repeated["wave"][0]["task_name"], expected_name)
        queue = tp._load_queue(
            tp._dispatch_path(self.ws, "expected_dispatch.json"))
        pending = [row for row in queue if not row.get("matched")]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["task_name"], expected_name)

        hook = TestScreenDispatchHook()
        hook.ws = self.ws
        mismatch = hook._run(
            hook._codex_event(expected_name, effort="low"),
            env_mode="strict")
        denied = json.loads(mismatch.stdout)
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"],
                         "deny")
        exact = hook._run(
            hook._codex_event(expected_name, effort="high"),
            env_mode="strict")
        self.assertEqual(exact.stdout.strip(), "",
                         "a rejected wave dispatch must remain retryable")


class TestStatuses(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _seed(self, ws, statuses):
        loop.init(ws, "g", parallel=True)
        os.makedirs(os.path.join(ws, 'specs'), exist_ok=True); open(os.path.join(ws, 'specs', 'spec.md'), 'w', encoding="utf-8").write('# spec\n')
        st = loop.load(ws)
        st["step"] = "execute"
        st["tasks"] = [
            {"id": "t1", "scope": ["src/a/**"], "status": statuses[0]},
            {"id": "t2", "scope": ["src/b/**"], "deps": ["t1"],
             "status": statuses[1]},
        ]
        loop.save(ws, st)

    def test_done_seed_satisfies_dep(self):
        ws = _repo(self.tmp)
        self._seed(ws, ["done", "pending"])
        w = loop.wave(ws)
        ids = [e["task"]["id"] for e in w.get("wave", [])]
        self.assertIn("t2", ids, w)

    def test_external_satisfies_dep(self):
        ws = _repo(self.tmp)
        self._seed(ws, ["external", "pending"])
        w = loop.wave(ws)
        ids = [e["task"]["id"] for e in w.get("wave", [])]
        self.assertIn("t2", ids, w)

    def test_resolve_defer_sets_external(self):
        ws = _repo(self.tmp)
        loop.init(ws, "g")
        os.makedirs(os.path.join(ws, 'specs'), exist_ok=True); open(os.path.join(ws, 'specs', 'spec.md'), 'w', encoding="utf-8").write('# spec\n')
        st = loop.load(ws)
        st.update({"step": "escalated", "current_task": 0,
                   "tasks": [{"id": "t1", "scope": ["src/**"],
                              "status": "running"}]})
        loop.save(ws, st)
        out = loop.resolve(ws, "defer")
        self.assertNotIn("error", out)
        self.assertEqual(loop.load(ws)["tasks"][0]["status"], "external")

    def test_bad_decision_lists_defer(self):
        ws = _repo(self.tmp)
        loop.init(ws, "g")
        os.makedirs(os.path.join(ws, 'specs'), exist_ok=True); open(os.path.join(ws, 'specs', 'spec.md'), 'w', encoding="utf-8").write('# spec\n')
        st = loop.load(ws)
        st.update({"step": "escalated",
                   "tasks": [{"id": "t1", "status": "running"}],
                   "current_task": 0})
        loop.save(ws, st)
        self.assertIn("defer", loop.resolve(ws, "nope")["error"])


class TestOnboardTiers(unittest.TestCase):
    def test_onboard_reports_resolved_tiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _repo(tmp)
            env = {**os.environ, "TASKPLANE_MODEL_DEEP": "opus"}
            env.pop("CODEX_HOME", None)
            env.pop("CODEX_THREAD_ID", None)
            r = subprocess.run([sys.executable, TPPY, "onboard", "--json",
                                "--workspace", ws], capture_output=True,
                               text=True, env=env, encoding="utf-8", errors="replace")
            rep = json.loads(r.stdout)
            self.assertEqual(rep["model_tiers"]["cheap"], "haiku")
            self.assertEqual(rep["model_tiers"]["standard"], "inherit")
            self.assertEqual(rep["model_tiers"]["deep"], "opus")
            self.assertEqual(rep["reasoning_tiers"], {
                "cheap": "low", "standard": "medium", "deep": "high"})


if __name__ == "__main__":
    unittest.main()
