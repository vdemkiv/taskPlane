"""v0.9.5 fixes — the full-review HIGH pass.

Covers the confirmed engineering HIGHs from the 25-lens review, each a real
defect (several are regressions in the v0.9.4 lifecycle fix):

  A. orphan release is PID-authoritative — a LIVE recorded owner is never
     idle-released (no agent can wait out a TTL to shed the wall), and the
     no-PID idle backstop is measured from the last APPROVED action so a
     leaked exhausted contract (only denies) still releases;
  D. `git -C <path>` no longer hides its mutator subcommand from the screen;
  E. wrapper flags that take a separate argument (`env -u NAME`,
     `sudo -u user`, `timeout -s SIG`) no longer hide the real command;
  F. meter.json is written atomically and stamps last_action_ts only on
     approved actions;
  H. contract discovery walks up to the nearest governed ancestor, so a
     `cd` into a subdirectory can't escape the contract;
  M. an ungoverned workspace ABSTAINS (no forced approve).
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


# ---------------------------------------------------------------- A: orphan
class TestOrphanPidAuthoritative(unittest.TestCase):
    def test_live_pid_never_idle_released_even_past_ttl(self):
        """The wall fix: an ALIVE recorded owner is governed forever — it can
        never idle past a TTL to free itself, and a paused live run keeps its
        governance."""
        ws, c = _governed_ws()
        c["activated_pid"] = os.getpid()          # alive
        c["orphan_ttl_seconds"] = 1
        c["activated_at"] = time.time() - 99999   # ancient
        cpath = os.path.join(tpl.tp_dir(ws), "active_contract.json")
        with open(cpath, "w") as f:
            json.dump(c, f)
        old = time.time() - 99999
        os.utime(cpath, (old, old))
        orphaned, why = tpl.orphan_status(ws, tpl.load_active(ws))
        self.assertFalse(orphaned, why)           # alive owner => governed
        # and the screener keeps enforcing (out-of-scope write blocked)
        d = _screen(ws, "Write", {"file_path": os.path.join(ws, "x.md"),
                                  "content": "x"})
        self.assertEqual(d["decision"], "block")
        self.assertTrue(os.path.exists(cpath))

    def test_deny_does_not_stamp_last_action_ts(self):
        """A blocked action bumps the deny counter but must NOT refresh the
        liveness clock — otherwise a leaked exhausted contract's own retries
        would keep resetting the orphan backstop."""
        ws, c = _governed_ws(max_actions=1)
        # RECENT activation so the deny screen doesn't auto-release first —
        # we only want to observe the meter effect of a deny.
        with open(os.path.join(tpl.tp_dir(ws), "meter.json"), "w") as f:
            json.dump({c["task_id"]: {"actions": 1, "denies": 0}}, f)
        d = _screen(ws, "Bash", {"command": "echo hi"})   # blocked (exhausted)
        self.assertEqual(d["decision"], "block")
        m = json.load(open(os.path.join(tpl.tp_dir(ws), "meter.json")))
        self.assertEqual(m[c["task_id"]]["denies"], 1)
        self.assertNotIn("last_action_ts", m[c["task_id"]])

    def test_no_pid_nonexhausted_idle_is_orphaned(self):
        """The crashed-mid-work backstop (v0.9.6): no PID, NOT exhausted,
        activated long ago with no recent activity -> orphan_status fires.
        (An EXHAUSTED contract is a human gate and is covered separately in
        test_v096_fixes — it must NOT release here.)"""
        ws, c = _governed_ws(max_actions=60)
        c.pop("activated_pid", None)
        c["orphan_ttl_seconds"] = 30
        c["activated_at"] = time.time() - 9999
        with open(os.path.join(tpl.tp_dir(ws), "meter.json"), "w") as f:
            json.dump({c["task_id"]: {"actions": 3, "denies": 1}}, f)
        orphaned, why = tpl.orphan_status(ws, c)
        self.assertTrue(orphaned, why)

    def test_approved_action_stamps_and_refreshes_clock(self):
        ws, c = _governed_ws(max_actions=10, scope=("**",))
        _screen(ws, "Bash", {"command": "echo hi"})   # approved → stamps ts
        m = json.load(open(os.path.join(tpl.tp_dir(ws), "meter.json")))
        self.assertIn("last_action_ts", m[c["task_id"]])
        # fresh approved activity → not orphaned even with a short TTL
        c2 = tpl.load_active(ws)
        c2["orphan_ttl_seconds"] = 30
        c2.pop("activated_pid", None)
        self.assertFalse(tpl.orphan_status(ws, c2)[0])


# ------------------------------------------------------------ D: git -C
class TestGitDashCBypass(unittest.TestCase):
    def test_git_dash_C_mutator_is_screened(self):
        c = tpl.build_contract("review", read_only=True,
                               write_allow=[".em-review/**"])
        for cmd in ("git -C /tmp/other checkout .",
                    "git -C /repo reset --hard",
                    "git --git-dir=/r/.git --work-tree=/r restore x"):
            allow, reason = tpl.screen_tool(c, "Bash", {"command": cmd}, None)
            self.assertFalse(allow, cmd)

    def test_plain_git_status_still_allowed(self):
        c = tpl.build_contract("t", scope=["src/**"])
        allow, _ = tpl.screen_tool(c, "Bash", {"command": "git -C x status"},
                                   None)
        self.assertTrue(allow)


# ------------------------------------------------ E: wrapper value-flags
class TestWrapperValueFlags(unittest.TestCase):
    def test_separate_arg_flags_do_not_hide_command(self):
        self.assertIn("x", tpl.write_targets("env -u NAME rm x"))
        self.assertIn("x", tpl.write_targets("sudo -u user rm x"))
        self.assertIn("x", tpl.write_targets("timeout -s KILL rm x"))
        self.assertIn("x", tpl.write_targets("nice -n 5 rm x"))
        self.assertIn("x", tpl.write_targets("ionice -c 2 -n 4 rm x"))

    def test_glued_and_positional_forms_still_work(self):
        self.assertIn("y", tpl.write_targets("timeout 5 rm y"))
        self.assertIn("z", tpl.write_targets("env FOO=1 rm z"))

    def test_read_only_contract_blocks_these(self):
        c = tpl.build_contract("review", read_only=True,
                               write_allow=[".em-review/**"])
        for cmd in ("sudo -u root rm -rf src/a.py",
                    "timeout -k 1 -s TERM tee src/a.py"):
            allow, _ = tpl.screen_tool(c, "Bash", {"command": cmd}, None)
            self.assertFalse(allow, cmd)


# ---------------------------------------------------- F: meter atomic write
class TestMeterAtomic(unittest.TestCase):
    def test_no_tmp_files_left_and_valid_json(self):
        ws, c = _governed_ws(max_actions=10, scope=("**",))
        for _ in range(3):
            _screen(ws, "Bash", {"command": "echo hi"})
        d = tpl.tp_dir(ws)
        leftovers = [f for f in os.listdir(d) if f.startswith("meter.json.tmp")]
        self.assertEqual(leftovers, [])
        json.load(open(os.path.join(d, "meter.json")))   # parses cleanly


# ------------------------------------------------ H: ancestor contract walk
class TestAncestorContractWalk(unittest.TestCase):
    def test_subdirectory_is_still_governed(self):
        ws, c = _governed_ws(scope=("src/**",))
        sub = os.path.join(ws, "src", "deep", "nested")
        os.makedirs(sub)
        # an out-of-scope write from the SUBDIR cwd is still caught — the
        # screener walks up to the governed root instead of treating the
        # subdir as ungoverned
        d = _screen(sub, "Write", {"file_path": os.path.join(ws, "secret.txt"),
                                   "content": "x"})
        self.assertEqual(d["decision"], "block")

    def test_unrelated_sibling_stays_ungoverned(self):
        ws, c = _governed_ws()
        sibling = tempfile.mkdtemp()              # no contract anywhere above
        d = _screen(sibling, "Write",
                    {"file_path": os.path.join(sibling, "a.txt"),
                     "content": "x"})
        self.assertIsNone(d["decision"])          # abstain, not governed


# ---------------------------------------------------- M: ungoverned abstains
class TestUngovernedAbstains(unittest.TestCase):
    def test_ungoverned_emits_no_decision(self):
        ws = tempfile.mkdtemp()
        d = _screen(ws, "Write", {"file_path": os.path.join(ws, "a.py"),
                                  "content": "x"})
        self.assertIsNone(d["decision"])


if __name__ == "__main__":
    unittest.main()
