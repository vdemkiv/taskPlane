"""v0.9.6 fixes — the engineering-review HIGH pass on the pushed v0.9.5.

An independent 6-lens review of v0.9.5 found 3 real HIGHs (two were
incomplete v0.9.5 fixes). Each is reproduced here, then pinned fixed:

  H1 env -S bypass was order-dependent: a value-flag before -S (env -u X -S
     '…') slipped the payload past the screen. Now flag-aware.
  H2 dashboard onclick injection was NOT closed: HTML-entity escaping is
     decoded before the inline JS runs, so the quote broke out. Now
     JS-escaped (_jsattr).
  H3 the "PID-authoritative" wall never engaged (nothing sets
     TASKPLANE_AGENT_PID) so it degraded to pure-TTL and a live agent could
     wait out the TTL to shed the wall. Now a budget-EXHAUSTED contract is a
     human gate, NEVER idle-released.

Plus the wall-adjacent fixes: meter fails CLOSED on corruption; grant_budget
atomic; ancestor walk stops at the git worktree boundary; pid<=0 rejected.
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
    event = {"cwd": ws, "tool_name": tool_name, "tool_input": tool_input}
    e = dict(os.environ)
    e.update(env or {})
    r = subprocess.run([sys.executable, TP, "screen"],
                       input=json.dumps(event), capture_output=True,
                       text=True, env=e)
    if not r.stdout.strip():
        return {"decision": None}
    return json.loads(r.stdout)


def _governed_ws(max_actions=3, scope=("src/**",)):
    ws = tempfile.mkdtemp()
    c = tpl.build_contract("t", scope=list(scope), max_actions=max_actions)
    tpl.activate(ws, c, snapshot=None)
    return ws, c


# ------------------------------------------------ H1: env -S flag-aware
class TestEnvSplitStringFlagAware(unittest.TestCase):
    def test_value_flag_before_S_no_longer_bypasses(self):
        # the exact reported bypass
        self.assertIn("victim",
                      tpl.write_targets("env -u X -S 'rm -rf victim'"))
        self.assertIn("victim",
                      tpl.write_targets("env -C /tmp -S 'rm -rf victim'"))
        self.assertIn("victim",
                      tpl.write_targets("env -u A -u B -S 'tee victim'"))
        # still catches the plain form and normal env
        self.assertIn("v", tpl.write_targets("env -S 'rm v'"))
        self.assertEqual(tpl.write_targets("env -u X ls -la"), [])

    def test_read_only_contract_blocks_the_prefixed_form(self):
        c = tpl.build_contract("review", read_only=True,
                               write_allow=[".em-review/**"])
        allow, _ = tpl.screen_tool(
            c, "Bash", {"command": "env -u PATH -S 'rm -rf src/a.py'"}, None)
        self.assertFalse(allow)


# ------------------------------------------------ H2: dashboard XSS
class TestDashboardJsEscape(unittest.TestCase):
    def test_jsattr_backslash_escapes_quote_not_entity(self):
        import dashboard as d
        payload = "'-alert(document.cookie)-'"
        out = d._jsattr(payload)
        self.assertIn("\\'", out)          # JS-escaped quote (survives decode)
        self.assertNotIn("&#39;", out)     # NOT the entity that decodes back
        self.assertNotIn("'-alert", out.replace("\\'", ""))

    def test_selection_gate_button_has_no_breakout(self):
        import dashboard as d
        # a variant id carrying a quote must not break the onclick JS string
        state = {"step": "selection", "goal": "g",
                 "tasks": [{"id": "t1", "variant": "A'});evil//",
                            "status": "built"}]}
        # render the widget fragment and confirm the raw breakout is absent
        import loop
        ws = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q"], cwd=ws)
        open(os.path.join(ws, "a.py"), "w").write("x=1\n")
        subprocess.run(["git", "add", "-A"], cwd=ws)
        subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                        "commit", "-qm", "i"], cwd=ws)
        loop.save(ws, state)
        frag = d.widget(ws)
        # Isolate the variant button's onclick (the dangerous JS-string
        # context) and assert the payload quote is BACKSLASH-escaped there —
        # so it can't break out. (A raw quote in the visible label, via _esc,
        # is harmless HTML text content and is not what we're testing.)
        import re
        onclicks = re.findall(r'onclick="tpFire\(this,\'select variant[^"]*"',
                              frag)
        self.assertTrue(onclicks, "variant button onclick not found")
        oc = onclicks[0]
        self.assertIn("A\\'});evil", oc)      # escaped inside the JS string
        # no UNescaped payload quote (a bare ' not preceded by backslash)
        self.assertNotIn("A'});evil", oc)


# ------------------------------------------------ H3: exhausted = human gate
class TestExhaustedContractNeverIdleReleased(unittest.TestCase):
    def test_exhausted_no_pid_idle_is_NOT_orphaned(self):
        """The wall fix: an exhausted contract is a human gate — a live agent
        cannot wait out the TTL to shed it, even with no PID and an ancient
        activation."""
        ws, c = _governed_ws(max_actions=2)
        c.pop("activated_pid", None)
        c["orphan_ttl_seconds"] = 1
        c["activated_at"] = time.time() - 9999
        with open(os.path.join(tpl.tp_dir(ws), "meter.json"), "w") as f:
            json.dump({c["task_id"]: {"actions": 2, "denies": 5,
                                      "last_seen_ts": time.time() - 9999}}, f)
        orphaned, why = tpl.orphan_status(ws, c)
        self.assertFalse(orphaned, why)
        self.assertIn("budget-exhausted", why)

    def test_exhausted_contract_stays_blocked_after_ttl_via_screen(self):
        """End to end: exhausted + ancient + no PID → the screener still
        BLOCKS (does not auto-release and abstain)."""
        ws, c = _governed_ws(max_actions=2)
        cpath = os.path.join(tpl.tp_dir(ws), "active_contract.json")
        c["orphan_ttl_seconds"] = 1
        c["activated_at"] = time.time() - 9999
        c.pop("activated_pid", None)
        with open(cpath, "w") as f:
            json.dump(c, f)
        with open(os.path.join(tpl.tp_dir(ws), "meter.json"), "w") as f:
            json.dump({c["task_id"]: {"actions": 2, "denies": 0,
                                      "last_seen_ts": time.time() - 9999}}, f)
        d = _screen(ws, "Bash", {"command": "echo hi"})
        self.assertEqual(d["decision"], "block")
        self.assertIn("ACTION BUDGET", d["reason"])
        self.assertTrue(os.path.exists(cpath))         # NOT auto-released

    def test_nonexhausted_crashed_agent_still_releases(self):
        """The legit backstop still works: non-exhausted, no PID, idle past
        TTL → released + abstain."""
        ws, c = _governed_ws(max_actions=60)
        cpath = os.path.join(tpl.tp_dir(ws), "active_contract.json")
        c["orphan_ttl_seconds"] = 1
        c["activated_at"] = time.time() - 9999
        c.pop("activated_pid", None)
        with open(cpath, "w") as f:
            json.dump(c, f)
        with open(os.path.join(tpl.tp_dir(ws), "meter.json"), "w") as f:
            json.dump({c["task_id"]: {"actions": 3, "denies": 0,
                                      "last_seen_ts": time.time() - 9999}}, f)
        d = _screen(ws, "Bash", {"command": "echo hi"})
        self.assertIsNone(d["decision"])               # abstain
        self.assertFalse(os.path.exists(cpath))        # released

    def test_live_activity_keeps_nonexhausted_governed(self):
        """A working agent (recent last_seen) is not orphaned."""
        ws, c = _governed_ws(max_actions=60)
        c.pop("activated_pid", None)
        c["orphan_ttl_seconds"] = 3600
        with open(os.path.join(tpl.tp_dir(ws), "meter.json"), "w") as f:
            json.dump({c["task_id"]: {"actions": 3, "denies": 0,
                                      "last_seen_ts": time.time()}}, f)
        self.assertFalse(tpl.orphan_status(ws, c)[0])


# ------------------------------------------------ meter fail-closed
class TestMeterFailsClosed(unittest.TestCase):
    def test_corrupt_meter_blocks_governed_action(self):
        ws, c = _governed_ws(max_actions=5)
        with open(os.path.join(tpl.tp_dir(ws), "meter.json"), "w") as f:
            f.write("{not valid json")
        d = _screen(ws, "Bash", {"command": "echo hi"})
        self.assertEqual(d["decision"], "block")
        self.assertIn("meter", d["reason"].lower())

    def test_missing_meter_still_allows(self):
        # absent (not corrupt) meter reads as 0 used — normal first action
        ws, c = _governed_ws(max_actions=5, scope=("**",))
        d = _screen(ws, "Bash", {"command": "echo hi"})
        self.assertEqual(d["decision"], "approve")


# ------------------------------------------------ grant_budget atomic
class TestGrantBudgetAtomic(unittest.TestCase):
    def test_no_tmp_left_and_valid_after_grant(self):
        ws, c = _governed_ws(max_actions=3)
        tpl.grant_budget(ws, 10)
        d = tpl.tp_dir(ws)
        self.assertEqual(
            [f for f in os.listdir(d)
             if f.startswith("active_contract.json.tmp")], [])
        self.assertEqual(tpl.load_active(ws)["budget"]["max_actions"], 13)


# ------------------------------------------------ pid<=0 rejected
class TestPidZeroRejected(unittest.TestCase):
    def test_pid_zero_not_recorded(self):
        ws = tempfile.mkdtemp()
        os.environ["TASKPLANE_AGENT_PID"] = "0"
        try:
            tpl.activate(ws, tpl.build_contract("t", scope=["src/**"]),
                         snapshot=None)
        finally:
            del os.environ["TASKPLANE_AGENT_PID"]
        self.assertNotIn("activated_pid", tpl.load_active(ws))


# ------------------------------------------------ ancestor walk boundary
class TestAncestorWalkWorktreeBoundary(unittest.TestCase):
    def test_walk_stops_at_git_worktree_top(self):
        """A nested git worktree with no contract must NOT inherit a contract
        sitting at a parent directory — it abstains at the worktree boundary."""
        parent = tempfile.mkdtemp()
        # parent holds a contract
        tpl.activate(parent, tpl.build_contract("outer", scope=["**"]),
                     snapshot=None)
        # a real git repo nested under parent, with NO contract of its own
        child = os.path.join(parent, "child")
        os.makedirs(child)
        subprocess.run(["git", "init", "-q"], cwd=child)
        open(os.path.join(child, "a.py"), "w").write("x=1\n")
        subprocess.run(["git", "add", "-A"], cwd=child)
        subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                        "commit", "-qm", "i"], cwd=child)
        # an action in the child must abstain (not be screened by parent's
        # contract) — the walk stops at the child's git toplevel
        d = _screen(child, "Write",
                    {"file_path": os.path.join(child, "a.py"), "content": "y"})
        self.assertIsNone(d["decision"])


if __name__ == "__main__":
    unittest.main()
