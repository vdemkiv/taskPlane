"""Locked-contract incident regressions — lifecycle safety, wall intact.

A review agent activated a contract in the session home, exhausted its
40-action budget, and died without releasing it; the leaked contract then
governed the whole session. The DESIGN OF RECORD keeps the wall: a governed
agent must never free itself (`tp clear`), remove its own contract file, or
grant itself budget. The fix class is LIFECYCLE:

  1. agents/engine release contracts in try/finally (loop gate clears in
     both outcomes);
  2. an orphaned contract (dead recorded PID / idle past TTL) auto-releases;
  3. budget exhaustion is a HUMAN approval gate — `tp budget --grant N` is
     for the human / ungoverned main session;
  4. `tp new` refuses the session home / bare root, so a leak can't govern
     a whole session.

These tests pin every path — including that the wall itself still stands.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import taskplane_lite as tpl  # noqa: E402

TP = os.path.join(os.path.dirname(__file__), "..", "tp.py")


def _screen(ws, tool_name, tool_input, env=None):
    """Run the real hook entrypoint the way Cowork does: stdin event JSON."""
    event = {"cwd": ws, "tool_name": tool_name, "tool_input": tool_input}
    e = dict(os.environ)
    e.update(env or {})
    r = subprocess.run([sys.executable, TP, "screen"],
                       input=json.dumps(event), capture_output=True,
                       text=True, env=e)
    # Empty stdout = ABSTAIN (ungoverned / auto-released → defer to Claude
    # Code's normal permission flow, no forced decision).
    if not r.stdout.strip():
        return {"decision": None}
    return json.loads(r.stdout)


def _governed_ws(max_actions=3, exhaust=False):
    """A workspace with an active contract; optionally budget-exhausted."""
    ws = tempfile.mkdtemp()
    c = tpl.build_contract("t", scope=["src/**"], max_actions=max_actions)
    tpl.activate(ws, c, snapshot=None)
    if exhaust:
        with open(os.path.join(tpl.tp_dir(ws), "meter.json"), "w") as f:
            json.dump({c["task_id"]: {"actions": max_actions, "denies": 0}},
                      f)
    return ws, c


class TestTheWallHolds(unittest.TestCase):
    """An exhausted budget blocks EVERYTHING in the governed workspace —
    including self-service escapes. The wall is intentional."""

    def test_exhausted_budget_blocks_normal_commands(self):
        ws, _ = _governed_ws(exhaust=True)
        d = _screen(ws, "Bash", {"command": "echo hi"})
        self.assertEqual(d["decision"], "block")
        self.assertIn("ACTION BUDGET", d["reason"])

    def test_block_message_escalates_to_the_human(self):
        ws, _ = _governed_ws(exhaust=True)
        d = _screen(ws, "Bash", {"command": "echo hi"})
        self.assertIn("ask the human", d["reason"].lower())
        self.assertIn("--grant", d["reason"])

    def test_exhausted_agent_cannot_clear_its_own_contract(self):
        ws, _ = _governed_ws(exhaust=True)
        for cmd in (f"python3 {TP} clear",
                    f"python3 {TP} budget --grant 100",
                    f"rm {os.path.join(tpl.tp_dir(ws), 'active_contract.json')}"):
            d = _screen(ws, "Bash", {"command": cmd})
            self.assertEqual(d["decision"], "block", cmd)

    def test_exhausted_budget_blocks_write_tools(self):
        ws, _ = _governed_ws(exhaust=True)
        d = _screen(ws, "Write", {"file_path": os.path.join(ws, "src/a.py"),
                                  "content": "x"})
        self.assertEqual(d["decision"], "block")

    def test_within_budget_agent_can_release_normally(self):
        """The finally-block path: `tp clear` is an ordinary allowed command
        while budget remains, so agents CAN release on success/error."""
        ws, _ = _governed_ws(max_actions=10)
        d = _screen(ws, "Bash", {"command": f"python3 {TP} clear"})
        self.assertEqual(d["decision"], "approve")


class TestBudgetGrantHumanGate(unittest.TestCase):
    """Exhaustion asks the human; the human's grant unblocks the work."""

    def test_grant_raises_ceiling_and_unblocks(self):
        ws, c = _governed_ws(max_actions=3, exhaust=True)
        self.assertEqual(_screen(ws, "Bash", {"command": "echo hi"})
                         ["decision"], "block")
        # the human / ungoverned main session runs the CLI directly
        r = subprocess.run([sys.executable, TP, "budget", "--grant", "20",
                            "--workspace", ws], capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ceiling now 23", r.stdout)
        self.assertEqual(tpl.load_active(ws)["budget"]["max_actions"], 23)
        self.assertEqual(_screen(ws, "Bash", {"command": "echo hi"})
                         ["decision"], "approve")

    def test_agents_own_grant_attempt_is_still_screened(self):
        """A governed agent invoking --grant goes through the hook like any
        command — and an exhausted budget blocks it (no self-grant)."""
        ws, _ = _governed_ws(exhaust=True)
        d = _screen(ws, "Bash",
                    {"command": f"python3 {TP} budget --grant 50"})
        self.assertEqual(d["decision"], "block")

    def test_grant_requires_positive(self):
        ws, _ = _governed_ws()
        r = subprocess.run([sys.executable, TP, "budget", "--grant", "0",
                            "--workspace", ws], capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 1)

    def test_budget_without_spent_or_grant_errors(self):
        ws, _ = _governed_ws()
        r = subprocess.run([sys.executable, TP, "budget",
                            "--workspace", ws], capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("--grant", r.stderr)

    def test_grant_traced_for_audit(self):
        ws, _ = _governed_ws()
        subprocess.run([sys.executable, TP, "budget", "--grant", "5",
                        "--workspace", ws], capture_output=True, text=True)
        trace = open(os.path.join(tpl.tp_dir(ws), "trace.jsonl")).read()
        self.assertIn("budget_granted", trace)


class TestOrphanAutoRelease(unittest.TestCase):
    """A contract whose owner is gone must not keep governing the workspace
    — the mechanical backstop for an agent that died without clearing."""

    def test_dead_pid_releases_contract(self):
        ws, c = _governed_ws()
        p = subprocess.Popen(["true"])
        p.wait()
        cpath = os.path.join(tpl.tp_dir(ws), "active_contract.json")
        c["activated_pid"] = p.pid
        with open(cpath, "w") as f:
            json.dump(c, f)
        d = _screen(ws, "Bash", {"command": "echo hi"})
        self.assertIsNone(d["decision"])              # abstain → normal flow
        self.assertFalse(os.path.exists(cpath))       # auto-cleared

    def test_dead_pid_releases_even_when_exhausted(self):
        """The incident shape exactly: exhausted budget + dead owner."""
        ws, c = _governed_ws(exhaust=True)
        p = subprocess.Popen(["true"])
        p.wait()
        cpath = os.path.join(tpl.tp_dir(ws), "active_contract.json")
        c["activated_pid"] = p.pid
        with open(cpath, "w") as f:
            json.dump(c, f)
        d = _screen(ws, "Bash", {"command": "echo hi"})
        self.assertIsNone(d["decision"])              # abstain → normal flow
        self.assertFalse(os.path.exists(cpath))

    def test_live_pid_stays_governed(self):
        ws, c = _governed_ws()
        cpath = os.path.join(tpl.tp_dir(ws), "active_contract.json")
        c["activated_pid"] = os.getpid()              # this test process
        with open(cpath, "w") as f:
            json.dump(c, f)
        d = _screen(ws, "Write", {"file_path": os.path.join(ws, "x.md"),
                                  "content": "x"})
        self.assertEqual(d["decision"], "block")      # out of scope, governed
        self.assertTrue(os.path.exists(cpath))

    def test_idle_past_ttl_releases_contract(self):
        ws, c = _governed_ws()
        cpath = os.path.join(tpl.tp_dir(ws), "active_contract.json")
        c["activated_at"] = time.time() - 9999
        c["orphan_ttl_seconds"] = 60
        with open(cpath, "w") as f:
            json.dump(c, f)
        old = time.time() - 9999
        os.utime(cpath, (old, old))
        d = _screen(ws, "Bash", {"command": "echo hi"})
        self.assertIsNone(d["decision"])              # abstain → normal flow
        self.assertFalse(os.path.exists(cpath))

    def test_recent_activity_within_ttl_stays_governed(self):
        ws, _ = _governed_ws()                        # fresh mtime = now
        d = _screen(ws, "Bash", {"command": "git push"})
        self.assertEqual(d["decision"], "block")      # denied cmd, governed

    def test_orphan_release_traced(self):
        ws, c = _governed_ws()
        p = subprocess.Popen(["true"])
        p.wait()
        c["activated_pid"] = p.pid
        with open(os.path.join(tpl.tp_dir(ws), "active_contract.json"),
                  "w") as f:
            json.dump(c, f)
        _screen(ws, "Bash", {"command": "echo hi"})
        trace = open(os.path.join(tpl.tp_dir(ws), "trace.jsonl")).read()
        self.assertIn("contract_orphan_released", trace)

    def test_orphan_status_kernel_rule(self):
        ws, _ = _governed_ws()
        self.assertFalse(tpl.orphan_status(ws, tpl.load_active(ws))[0])
        self.assertTrue(
            tpl.orphan_status(ws, tpl.load_active(ws),
                              now=time.time() + 10 * 24 * 3600)[0])


class TestEngineReleasesOnGate(unittest.TestCase):
    """The loop engine is the mechanical try/finally for loop-driven roles:
    `loop gate` clears the step contract on PASS and on FAIL."""

    def _loop_ws(self):
        ws = tempfile.mkdtemp()
        open(os.path.join(ws, "a.py"), "w").write("x=1\n")
        subprocess.run(["git", "init", "-q"], cwd=ws)
        subprocess.run(["git", "add", "-A"], cwd=ws)
        subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                        "commit", "-qm", "i"], cwd=ws)
        return ws

    def test_gate_clears_contract_on_both_outcomes(self):
        import loop as loopmod
        for outcome in ("pass", "fail"):
            ws = self._loop_ws()
            loopmod.init(ws, "goal", checkpoints=[])
            loopmod.next_action(ws)                  # activates step contract
            cpath = os.path.join(tpl.tp_dir(ws), "active_contract.json")
            self.assertTrue(os.path.exists(cpath), outcome)
            loopmod.gate(ws, outcome)
            self.assertFalse(os.path.exists(cpath), outcome)


class TestBareRootRefusal(unittest.TestCase):
    """A contract must never be scoped to the session home / bare root."""

    def test_new_refuses_at_fake_home(self):
        fake_home = tempfile.mkdtemp()                # not a git repo
        env = dict(os.environ, HOME=fake_home)
        r = subprocess.run([sys.executable, TP, "new", "--scope", "src/**",
                            "--workspace", fake_home, "goal"],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("REFUSING", r.stderr)
        self.assertFalse(os.path.exists(
            os.path.join(fake_home, ".taskplane", "active_contract.json")))

    def test_new_allows_committed_project_at_home(self):
        """A REAL git project that happens to live at $HOME is a genuine
        workspace (mirrors the onboarding rule) — not refused."""
        fake_home = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q"], cwd=fake_home)
        open(os.path.join(fake_home, "a.py"), "w").write("x=1\n")
        subprocess.run(["git", "add", "-A"], cwd=fake_home)
        subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                        "commit", "-qm", "i"], cwd=fake_home)
        env = dict(os.environ, HOME=fake_home)
        r = subprocess.run([sys.executable, TP, "new", "--scope", "src/**",
                            "--workspace", fake_home, "goal"],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestActivateRecordsProvenance(unittest.TestCase):
    def test_activated_at_recorded_pid_only_from_env(self):
        ws = tempfile.mkdtemp()
        tpl.activate(ws, tpl.build_contract("t", scope=["src/**"]),
                     snapshot=None)
        c = tpl.load_active(ws)
        self.assertIn("activated_at", c)
        # the CLI's transient PID is never recorded implicitly — it dies
        # the moment `tp new` exits and would auto-release instantly
        self.assertNotIn("activated_pid", c)

    def test_agent_pid_env_recorded(self):
        ws = tempfile.mkdtemp()
        os.environ["TASKPLANE_AGENT_PID"] = str(os.getpid())
        try:
            tpl.activate(ws, tpl.build_contract("t", scope=["src/**"]),
                         snapshot=None)
        finally:
            del os.environ["TASKPLANE_AGENT_PID"]
        self.assertEqual(tpl.load_active(ws)["activated_pid"], os.getpid())


if __name__ == "__main__":
    unittest.main()
