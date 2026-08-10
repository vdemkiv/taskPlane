"""v2.3.0 guardrails fix wave — regression tests (fix agent A_guardrails).

Covers the 18 findings owned by this agent, both directions where
enforcement is touched: previously-blocked malicious forms STAY blocked
(TestNoLoosening), and the new blocks/behaviors work.

  HIGH  hooks.json Windows fallback fails CLOSED (both roots, exit /b 2)
  HIGH  requirements.py index writes locked + atomic (no lost updates,
        no duplicate R-ids under concurrency)
  HIGH  loop-authored specs/.gitignore/docs exempt from the DoD scope diff
        (with a named recovery) WITHOUT widening what a worker may write
  HIGH  per-task contract slots: TASKPLANE_TASK selection, sibling
        isolation, fail-closed on unknown/corrupt slots, and the
        most-restrictive UNION when several contracts are active with no
        TASKPLANE_TASK (never ungoverned, never one picked arbitrarily)
  HIGH  unittest-runner store isolation verified EMPIRICALLY (subprocess)
  MED   mode.json atomic + corrupt-mode fails toward PRIVATE
  MED   deny-pattern precision: anchored per-segment matching, with the
        conservative raw-text fallback whenever an unscreenable executor
        is present (ambiguity stays BLOCKED)
  MED   `python file.py` (and -m/stdin/bare) blocked under read-only
  MED   file_lock: never silently lock-free (mkdir fallback + StateError);
        caller-body exceptions propagate unchanged
  MED   dispatch-audit truncation is counted and reported, never silent
  MED   storage lifecycle: gc_runtime prunes ONLY runtime artifacts
  LOW   --max-actions 0 is a ZERO ceiling (never unmetered); negatives
        rejected; zero-ceiling contracts are grantable and never
        idle-released
  LOW   trace() warns ONCE per process when the audit trail goes dark
  LOW   legacy-store adoption never destructively moves an unprovable store
  LOW   host() is the single host-detection seam (env-injectable)
  LOW   FUSE tombstone/lock GC (part of gc_runtime)
  LOW   screen_liveness: 'is the wall actually up?' probe
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import taskplane_lite as tpl  # noqa: E402
import requirements as reqs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TPPY = os.path.join(ROOT, "taskplane", "tp.py")
HOOKS_JSON = os.path.join(ROOT, "hooks", "hooks.json")


def _git_repo(tmp):
    ws = os.path.join(tmp, "ws")
    os.makedirs(os.path.join(ws, "src"))
    with open(os.path.join(ws, "src", "a.py"), "w", encoding="utf-8") as f:
        f.write("x = 1\n")
    for a in (["init", "-q"], ["config", "user.email", "e@e"],
              ["config", "user.name", "t"], ["add", "-A"],
              ["commit", "-qm", "base"]):
        subprocess.run(["git", *a], cwd=ws, capture_output=True)
    return ws


class _StoreIsolated(unittest.TestCase):
    """Belt for tests that touch the store, independent of the pytest
    autouse fixture (so a plain unittest run stays isolated per-test)."""

    def setUp(self):
        self._old_home = os.environ.get("TASKPLANE_HOME")
        self._old_task = os.environ.pop("TASKPLANE_TASK", None)
        self._home = tempfile.mkdtemp(prefix="tp-guardrails-")
        os.environ["TASKPLANE_HOME"] = self._home
        self._tmp = tempfile.mkdtemp(prefix="tp-guardrails-ws-")

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self._old_home
        if self._old_task is not None:
            os.environ["TASKPLANE_TASK"] = self._old_task
        else:
            os.environ.pop("TASKPLANE_TASK", None)
        shutil.rmtree(self._home, ignore_errors=True)
        shutil.rmtree(self._tmp, ignore_errors=True)


# =====================================================================
# THE NO-LOOSENING BATTERY — every known-bad form stays refused under a
# read-only contract, deny-precision and interpreter changes included.
# =====================================================================

class TestNoLoosening(unittest.TestCase):
    """Enforcement must not loosen: a battery of known-bad commands is
    refused under a read-only review contract, and the scoped-build screen
    still blocks its classics. If any of these ever passes, a v2.3.0
    'precision' fix went too far."""

    RO = tpl.build_contract("review", read_only=True,
                            write_allow=[".em-review/**"], tools=["Bash"])
    BUILD_CODING = tpl.build_contract("build", scope=["src/**"])["coding"]

    READ_ONLY_BATTERY = [
        # rm out of scope / at root
        "rm -rf /",
        "rm -rf //",
        "rm -rf ../elsewhere",
        "rm -rf src",
        # VCS rewrites
        "git reset --hard",
        "git checkout -- .",
        "git -C sub reset --hard HEAD~1",
        "git stash",
        # find mutators
        "find . -delete",
        "find src -name '*.py' -exec rm {} ;",
        # redirects out of the allowlist
        "echo pwned > src/main.py",
        "echo pwned >> ../notes.txt",
        "echo pwned >| src/main.py",
        # sed -i out of the allowlist
        "sed -i s/a/b/ src/main.py",
        "sed --in-place s/a/b/ src/main.py",
        # nested shells
        "sh -c 'rm -rf src'",
        "bash -lc 'echo x > src/main.py'",
        "sh -c \"sed -i s/a/b/ src/main.py\"",
        # wrapper prefixes
        "env FOO=1 nohup rm -rf src",
        "sudo rm -rf src",
        "timeout 5 sh -c 'rm -rf src'",
        "command rm -rf src",
        "exec rm -rf src",
        "env -u NAME rm -rf src",
        "env -S 'rm -rf src'",
        # interpreters: inline code AND script files/modules/stdin
        "python3 -c \"open('src/main.py','w').write('x')\"",
        "python3 .em-review/evil.py",
        "python3 evil.py",
        "python -m evil",
        "python3",
        "node evil.js",
        "ruby evil.rb",
        "perl evil.pl",
        # deny list
        "git push",
        "git push --force origin main",
        "/usr/bin/git push",
        "git -C sub push",
        "echo 'git push' | sh",
        # unscopeable mutators
        "xargs rm",
        "patch -p1",
        "dd if=/dev/zero of=src/main.py",
        "tee src/main.py",
        "truncate -s 0 src/main.py",
        "mv x src/main.py",
        "cp x src/main.py",
        "sort -o src/main.py x",
        "chmod 777 src/main.py",
        # obfuscation
        "eval $'rm \\x2drf src'",
        "$(rm -rf src)",
    ]

    def test_read_only_battery_all_refused(self):
        for cmd in self.READ_ONLY_BATTERY:
            ok, reason = tpl.screen_tool(self.RO, "Bash", {"command": cmd},
                                         None)
            self.assertFalse(
                ok, f"LOOSENED: read-only contract now approves {cmd!r}")
            self.assertTrue(reason)

    def test_read_only_write_tools_stay_confined(self):
        for tool, tin in (
                ("Write", {"file_path": "src/main.py", "content": "x"}),
                ("Edit", {"file_path": "../outside.py"}),
                ("NotebookEdit", {"notebook_path": "src/nb.ipynb"})):
            ok, _ = tpl.screen_tool(self.RO, tool, tin, None)
            self.assertFalse(ok, f"LOOSENED: {tool} {tin}")

    BUILD_BATTERY = [
        "git push",                        # deny
        "rm -rf /",                        # deny + escape
        "git reset --hard",                # destructive
        "git checkout -- .",               # destructive
        "find . -delete",                  # destructive
        "find . -exec rm {} ;",            # destructive
        "xargs rm",                        # destructive
        "patch -p1",                       # destructive
        "echo pwned > README.md",          # redirect out of scope
        "echo pwned >> /etc/passwd",       # redirect escape
        "sed -i s/a/b/ README.md",         # sed out of scope
        "sh -c 'echo x > README.md'",      # nested redirect out of scope
        "bash -lc 'sed -i s/a/b/ README.md'",
        "env FOO=1 nohup tee README.md",   # wrapper + write out of scope
        "sudo rm README.md",
        "eval $'rm \\x2drf README.md'",
        "echo 'git push' | sh",            # deny via unscreenable executor
    ]

    def test_build_battery_all_refused(self):
        for cmd in self.BUILD_BATTERY:
            v = tpl.screen_command(cmd, self.BUILD_CODING, None)
            self.assertIsNotNone(
                v, f"LOOSENED: scoped build contract now approves {cmd!r}")

    def test_python_dash_c_write_still_blocked_read_only(self):
        # The read-only branch blocks interpreter-opaque exactly as before.
        ok, reason = tpl.screen_tool(
            self.RO, "Bash",
            {"command": "python3 -c \"print('x')\""}, None)
        self.assertFalse(ok)
        self.assertIn("read-only", reason)


# =====================================================================
# Deny precision (the ONLY sanctioned loosening: provably-safe forms)
# =====================================================================

class TestDenyPrecision(unittest.TestCase):
    DENY = ["git push"]

    def test_false_denies_removed(self):
        for cmd in ('git commit -m ok && echo push',
                    'grep "git push" docs/release.md',
                    'echo "never git push"'):
            self.assertIsNone(tpl.deny_violation(cmd, self.DENY), cmd)

    def test_ambiguous_flag_value_stays_conservatively_denied(self):
        # 'push' here is a flag VALUE, not the subcommand — telling the two
        # apart needs git-specific parsing, and the binding rule is that
        # ambiguity stays BLOCKED (otherwise `git --no-pager push` would
        # need the same loophole).
        self.assertEqual(
            tpl.deny_violation("git log --grep push", self.DENY), "git push")

    def test_true_denies_kept(self):
        for cmd in ("git push",
                    "git push --force",
                    "/usr/bin/git push",
                    "git -C sub push",
                    "git --no-pager push origin main",
                    "env GIT_TRACE=1 git push",
                    "command git push",
                    "sh -c 'git push'",
                    "eval 'git push'"):
            self.assertEqual(tpl.deny_violation(cmd, self.DENY), "git push",
                             cmd)

    def test_conservative_fallback_on_unscreenable_executor(self):
        # Precise parsing can't see through these executors — the raw
        # substring match must still fire (ambiguity stays BLOCKED).
        for cmd in ("echo 'git push' | sh",
                    "sh run.sh 'git push'",
                    "bash deploy.sh 'git push'",
                    "python3 do.py 'git push'",
                    "echo 'git push' | xargs -I{} true"):
            self.assertEqual(tpl.deny_violation(cmd, self.DENY), "git push",
                             cmd)

    def test_rm_rf_root_double_slash_still_denied(self):
        self.assertEqual(
            tpl.deny_violation("rm -rf //", ["rm -rf /"]), "rm -rf /")


# =====================================================================
# Interpreter screening gap (python file.py) — the new block works
# =====================================================================

class TestInterpreterTightening(unittest.TestCase):
    def test_script_file_is_interpreter_opaque(self):
        for cmd in ("python3 evil.py", "python evil.py", "node evil.js",
                    "ruby evil.rb", "perl evil.pl", "python3 -m evil",
                    "python3"):
            _, opaque = tpl._analyze(cmd)
            self.assertIsNotNone(opaque, cmd)
            self.assertEqual(opaque[0], "interpreter", cmd)

    def test_version_probes_stay_transparent(self):
        for cmd in ("python3 --version", "node --version", "python3 -V"):
            _, opaque = tpl._analyze(cmd)
            self.assertIsNone(opaque, cmd)

    def test_read_only_blocks_script_after_staging_it(self):
        # The live bypass: write evil.py into write_allow, then run it.
        ro = tpl.build_contract("review", read_only=True,
                                write_allow=[".em-review/**"])
        ok, _ = tpl.screen_tool(
            ro, "Write",
            {"file_path": ".em-review/evil.py", "content": "boom"}, None)
        self.assertTrue(ok)                      # staging is allowed…
        ok, reason = tpl.screen_tool(
            ro, "Bash", {"command": "python3 .em-review/evil.py"}, None)
        self.assertFalse(ok)                     # …running it is not
        self.assertIn("read-only", reason)

    def test_build_contract_keeps_documented_interpreter_gap(self):
        # Unchanged for build contracts: opaque interpreters pass (the
        # documented cooperative-screen limitation).
        coding = tpl.build_contract("b", scope=["src/**"])["coding"]
        self.assertIsNone(tpl.screen_command("python3 tool.py", coding, None))


# =====================================================================
# Per-task contract slots + most-restrictive union (HIGH)
# =====================================================================

class TestPerTaskContractSlots(_StoreIsolated):
    def _activate(self, slot, **kw):
        os.environ["TASKPLANE_TASK"] = slot
        c = tpl.build_contract(f"task {slot}", **kw)
        tpl.activate(self._tmp, c, snapshot=None)
        return c

    def test_slot_files_and_sibling_isolation(self):
        ca = self._activate("tA", scope=["src/**"], max_actions=10)
        cb = self._activate("tB", read_only=True,
                            write_allow=[".em-review/**"], max_actions=5)
        act = os.path.join(tpl.tp_dir(self._tmp), "active")
        self.assertTrue(os.path.exists(os.path.join(act, "tA.json")))
        self.assertTrue(os.path.exists(os.path.join(act, "tB.json")))
        os.environ["TASKPLANE_TASK"] = "tA"
        self.assertEqual(tpl.load_active(self._tmp)["task_id"],
                         ca["task_id"])
        tpl.clear(self._tmp)                     # clears ONLY tA
        self.assertFalse(os.path.exists(os.path.join(act, "tA.json")))
        self.assertTrue(os.path.exists(os.path.join(act, "tB.json")))
        os.environ["TASKPLANE_TASK"] = "tB"
        self.assertEqual(tpl.load_active(self._tmp)["task_id"],
                         cb["task_id"])

    def test_unknown_slot_fails_closed(self):
        self._activate("tA", scope=["src/**"])
        os.environ["TASKPLANE_TASK"] = "nope"
        with self.assertRaises(tpl.StateError):
            tpl.load_active(self._tmp)

    def test_illformed_slot_fails_closed(self):
        os.environ["TASKPLANE_TASK"] = "../evil"
        with self.assertRaises(tpl.StateError):
            tpl.task_slot()

    def test_corrupt_slot_fails_closed_even_in_union(self):
        self._activate("tA", scope=["src/**"])
        act = os.path.join(tpl.tp_dir(self._tmp), "active")
        with open(os.path.join(act, "tC.json"), "w", encoding="utf-8") as f:
            f.write("{torn")
        os.environ.pop("TASKPLANE_TASK", None)
        with self.assertRaises(tpl.StateError):
            tpl.load_active(self._tmp)

    def test_no_task_env_with_slots_governs_by_union(self):
        self._activate("tA", scope=["src/**"], max_actions=10)
        self._activate("tB", read_only=True,
                       write_allow=[".em-review/**"], max_actions=5)
        os.environ.pop("TASKPLANE_TASK", None)
        u = tpl.load_active(self._tmp)
        self.assertIsNotNone(u, "multiple active contracts must NOT leave "
                                "an env-less process ungoverned")
        self.assertTrue(u["task_id"].startswith("union-"))
        self.assertTrue(u.get("read_only"))       # any member read-only
        self.assertEqual(u["budget"]["max_actions"], 5)   # min ceiling
        # blocked by member B (read-only): a write A alone would allow
        ok, reason = tpl.screen_tool(
            u, "Bash", {"command": "echo x > src/main.py"}, self._tmp)
        self.assertFalse(ok)
        self.assertIn("union", reason)
        # blocked by member A (scope): a write B alone would allow
        ok, _ = tpl.screen_tool(
            u, "Bash", {"command": "echo x > .em-review/notes.md"},
            self._tmp)
        self.assertFalse(ok)
        # harmless command passes every member
        ok, _ = tpl.screen_tool(u, "Bash", {"command": "echo hi"}, self._tmp)
        self.assertTrue(ok)

    def test_single_slot_no_env_governs_by_that_contract(self):
        ca = self._activate("tA", scope=["src/**"])
        os.environ.pop("TASKPLANE_TASK", None)
        c = tpl.load_active(self._tmp)
        self.assertEqual(c["task_id"], ca["task_id"])

    def test_union_never_auto_released_and_never_blanket_granted(self):
        self._activate("tA", scope=["src/**"], max_actions=10)
        self._activate("tB", scope=["docs/**"], max_actions=5)
        os.environ.pop("TASKPLANE_TASK", None)
        u = tpl.load_active(self._tmp)
        orphaned, why = tpl.orphan_status(
            self._tmp, u, now=time.time() + 10 * 24 * 3600)
        self.assertFalse(orphaned)
        self.assertIn("never", why)
        with self.assertRaises(tpl.StateError):
            tpl.grant_budget(self._tmp, 100)

    def test_legacy_single_slot_behavior_unchanged(self):
        os.environ.pop("TASKPLANE_TASK", None)
        self.assertIsNone(tpl.load_active(self._tmp))   # ungoverned
        c = tpl.build_contract("legacy", scope=["src/**"])
        tpl.activate(self._tmp, c, snapshot=None)
        self.assertEqual(tpl.load_active(self._tmp)["task_id"], c["task_id"])

    def test_screen_hook_blocks_on_unknown_slot(self):
        # Integration: the real hook entrypoint fails CLOSED when the
        # dispatch env names a slot that has no contract.
        c = tpl.build_contract("t", scope=["src/**"])
        tpl.activate(self._tmp, c, snapshot=None)
        env = dict(os.environ)
        env["TASKPLANE_TASK"] = "ghost"
        event = {"cwd": self._tmp, "tool_name": "Bash",
                 "tool_input": {"command": "echo hi"}}
        r = subprocess.run([sys.executable, TPPY, "screen"],
                           input=json.dumps(event), capture_output=True,
                           text=True, env=env, encoding="utf-8")
        d = json.loads(r.stdout)
        self.assertEqual(d["decision"], "block")


# =====================================================================
# requirements.py index locking (HIGH)
# =====================================================================

class TestRequirementsIndexLocking(_StoreIsolated):
    def test_concurrent_writers_mint_unique_ids_and_lose_nothing(self):
        n, errs = 8, []

        def work(i):
            try:
                reqs.record_requirement(self._tmp, f"req {i}")
            except Exception as e:  # noqa: BLE001
                errs.append(e)

        ts = [threading.Thread(target=work, args=(i,)) for i in range(n)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertFalse(errs)
        idx = reqs.load_index(self._tmp)
        ids = [r["id"] for r in idx["requirements"]]
        self.assertEqual(len(ids), n)
        self.assertEqual(len(set(ids)), n, f"duplicate R-ids minted: {ids}")

    def test_index_write_is_atomic_and_corruption_raises(self):
        reqs.record_requirement(self._tmp, "one")
        p = os.path.join(reqs.kb_dir(self._tmp), "index.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{torn")
        with self.assertRaises(tpl.StateError):
            reqs.load_index(self._tmp)

    def test_requirements_and_kb_serialize_on_same_lock_file(self):
        reqs.record_requirement(self._tmp, "one")
        # the shared lock artifact kb.mutate flocks
        self.assertTrue(os.path.exists(
            os.path.join(reqs.kb_dir(self._tmp), "index.json.lock")))


# =====================================================================
# DoD scope diff: loop-authored files (HIGH) — no write-widening
# =====================================================================

class TestScopeDiffLoopArtifacts(unittest.TestCase):
    def test_loop_authored_files_exempt_from_dod_diff(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ws = _git_repo(tmp)
        head = tpl.git_head(ws)
        c = tpl.build_contract("t", scope=["src/**"])
        os.makedirs(os.path.join(ws, "specs"))
        os.makedirs(os.path.join(ws, "docs"))
        os.makedirs(os.path.join(ws, "context"))
        for rel in ("specs/spec.md", "docs/a.md", "context/b.md",
                    ".gitignore"):
            with open(os.path.join(ws, rel), "w", encoding="utf-8") as f:
                f.write("loop artifact\n")
        errors = tpl.dod_check(c, ws, head)
        self.assertEqual(errors, [], errors)

    def test_out_of_scope_source_still_fails_with_named_recovery(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ws = _git_repo(tmp)
        head = tpl.git_head(ws)
        c = tpl.build_contract("t", scope=["src/**"])
        os.makedirs(os.path.join(ws, "other"))
        with open(os.path.join(ws, "other", "x.py"), "w", encoding="utf-8") as f:
            f.write("rogue\n")
        errors = tpl.dod_check(c, ws, head)
        self.assertTrue(any("diff_scope: 'other/x.py'" in e for e in errors))
        self.assertTrue(any("loop next" in e for e in errors),
                        "recovery path must be named in the refusal")

    def test_exemption_does_not_widen_the_write_screen(self):
        # specs/docs/.gitignore are DoD-exempt but still NOT writable by a
        # worker whose scope is src/** — attribution changed, permission
        # did not.
        c = tpl.build_contract("t", scope=["src/**"])
        for rel in ("specs/spec.md", "docs/a.md", ".gitignore"):
            ok, _ = tpl.screen_tool(
                c, "Write", {"file_path": rel, "content": "x"}, None)
            self.assertFalse(ok, f"write screen widened for {rel}")
            v = tpl.screen_command(f"echo x > {rel}", c["coding"], None)
            self.assertIsNotNone(v, f"command screen widened for {rel}")


# =====================================================================
# unittest-runner store isolation (HIGH) — verified empirically
# =====================================================================

class TestUnittestRunnerIsolation(unittest.TestCase):
    def test_unittest_run_never_touches_real_home_store(self):
        fake_home = tempfile.mkdtemp(prefix="tp-fakehome-")
        self.addCleanup(shutil.rmtree, fake_home, ignore_errors=True)
        env = dict(os.environ)
        env.pop("TASKPLANE_HOME", None)          # the belt must catch it
        env["HOME"] = fake_home
        r = subprocess.run(
            [sys.executable, "-m", "unittest",
             "taskplane.tests.test_requirements"],
            cwd=ROOT, capture_output=True, text=True, env=env, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertFalse(
            os.path.exists(os.path.join(fake_home, ".taskplane")),
            "a plain `python -m unittest` run wrote into ~/.taskplane — "
            "the tests/__init__.py session belt is not effective")


# =====================================================================
# mode.json durability + private fail-safe (MED/LOW)
# =====================================================================

class TestModeFailsTowardPrivate(_StoreIsolated):
    def test_set_mode_persists_and_corruption_resolves_private(self):
        tpl.set_mode(self._tmp, private=True)
        self.assertTrue(tpl.get_mode(self._tmp)["private"])
        mode_file = tpl._mode_file(self._tmp)
        with open(mode_file, "w", encoding="utf-8") as f:
            f.write("{torn")                     # simulate the torn write
        m = tpl.get_mode(self._tmp)
        self.assertTrue(m["private"],
                        "corrupt mode.json downgraded private -> shared")
        self.assertEqual(m["store"], "external")

    def test_corrupt_mode_beats_committed_shared_config(self):
        os.makedirs(tpl.repo_store_root(self._tmp), exist_ok=True)
        with open(os.path.join(tpl.repo_store_root(self._tmp),
                               "config.json"), "w", encoding="utf-8") as f:
            json.dump({"plan": "team", "store": "repo"}, f)
        tpl.set_mode(self._tmp, private=True)
        with open(tpl._mode_file(self._tmp), "w", encoding="utf-8") as f:
            f.write("not json")
        m = tpl.get_mode(self._tmp)
        self.assertTrue(m["private"])
        self.assertEqual(m["store"], "external")

    def test_mode_write_is_atomic(self):
        tpl.set_mode(self._tmp, private=True)
        # atomic_write_json leaves no temp droppings next to mode.json
        d = os.path.dirname(tpl._mode_file(self._tmp))
        self.assertFalse([n for n in os.listdir(d) if ".tmp." in n])

    def test_inherited_shared_store_carries_the_notice(self):
        os.makedirs(tpl.repo_store_root(self._tmp), exist_ok=True)
        with open(os.path.join(tpl.repo_store_root(self._tmp),
                               "config.json"), "w", encoding="utf-8") as f:
            json.dump({"plan": "team", "store": "repo"}, f)
        m = tpl.get_mode(self._tmp)              # no personal setting
        self.assertEqual(m["store"], "repo")
        self.assertIn("team-visible", m.get("notice", ""))


# =====================================================================
# file_lock: never silently lock-free (MED)
# =====================================================================

class TestFileLock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.p = os.path.join(self.tmp, "state.json")

    def test_body_exception_propagates_unchanged(self):
        with self.assertRaises(KeyError):
            with tpl.file_lock(self.p):
                raise KeyError("boom")
        with tpl.file_lock(self.p):              # lock was released
            pass

    def test_body_oserror_propagates_not_swallowed(self):
        with self.assertRaises(OSError):
            with tpl.file_lock(self.p):
                raise OSError("disk full")

    def test_flockless_host_uses_mkdir_fallback_not_no_lock(self):
        with mock.patch("fcntl.flock", side_effect=OSError("no flock")):
            lockdir = self.p + ".lockdir"
            with tpl.file_lock(self.p):
                self.assertTrue(os.path.isdir(lockdir))
            self.assertFalse(os.path.isdir(lockdir))

    def test_unacquirable_lock_raises_stateerror(self):
        with mock.patch("fcntl.flock", side_effect=OSError("no flock")):
            os.makedirs(self.p + ".lockdir")     # someone else holds it
            with self.assertRaises(tpl.StateError):
                with tpl.file_lock(self.p, timeout=0.3):
                    pass

    def test_stale_mkdir_lock_is_stolen(self):
        with mock.patch("fcntl.flock", side_effect=OSError("no flock")):
            lockdir = self.p + ".lockdir"
            os.makedirs(lockdir)
            old = time.time() - 10 * tpl._LOCK_STALE_S
            os.utime(lockdir, (old, old))
            with tpl.file_lock(self.p, timeout=2):
                pass                             # stolen, not StateError

    def test_file_lock_alias_preserved(self):
        self.assertIs(tpl._file_lock, tpl.file_lock)


# =====================================================================
# Dispatch-audit truncation is named (MED)
# =====================================================================

class TestDispatchAuditTruncation(_StoreIsolated):
    def test_truncation_counted_and_reported(self):
        path = tpl._dispatch_path(self._tmp, "expected_dispatch.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        q = [{"ts": i, "agent": "tp-lens", "matched": False}
             for i in range(250)]
        tpl._save_queue(path, q)
        self.assertEqual(len(tpl._load_queue(path)), tpl._QUEUE_CAP)
        self.assertEqual(tpl._queue_dropped(path), 50)
        rep = tpl.dispatch_report(self._tmp)
        self.assertTrue(rep["truncated"])
        self.assertEqual(rep["expected_dropped"], 50)
        self.assertIn("LOWER BOUND", rep["truncated_note"])

    def test_untruncated_report_carries_no_bound_note(self):
        tpl.record_expected_dispatch(self._tmp, "loop", "tp-executor",
                                     "standard", None)
        rep = tpl.dispatch_report(self._tmp)
        self.assertFalse(rep["truncated"])
        self.assertNotIn("truncated_note", rep)
        self.assertEqual(rep["expected_dropped"], 0)


# =====================================================================
# Storage lifecycle: gc_runtime (MED) + FUSE tombstone/lock GC (LOW)
# =====================================================================

class TestRuntimeGC(_StoreIsolated):
    def _old(self, p):
        old = time.time() - 48 * 3600
        os.utime(p, (old, old))

    def test_gc_prunes_only_stale_runtime_artifacts(self):
        d = tpl.tp_dir(self._tmp)
        os.makedirs(os.path.join(d, "active"), exist_ok=True)
        stale = ["active_contract.json.removed.123.0",
                 ".meter.json.tmp.999",
                 "expected_dispatch.json.lock",
                 os.path.join("active", "tA.json.removed.1.0")]
        keep = ["active_contract.json", "trace.jsonl", "meter.json",
                os.path.join("active", "tB.json")]
        for rel in stale + keep:
            p = os.path.join(d, rel)
            with open(p, "w", encoding="utf-8") as f:
                f.write("{}")
            self._old(p)
        fresh = os.path.join(d, "fresh.json.removed.9.0")
        with open(fresh, "w", encoding="utf-8") as f:
            f.write("{}")
        lockdir = os.path.join(d, "graph.json.lockdir")
        os.makedirs(lockdir)
        self._old(lockdir)
        out = tpl.gc_runtime(self._tmp)
        self.assertEqual(out["removed"], len(stale) + 1)   # + lockdir
        for rel in stale:
            self.assertFalse(os.path.exists(os.path.join(d, rel)), rel)
        for rel in keep:                       # governance records survive
            self.assertTrue(os.path.exists(os.path.join(d, rel)), rel)
        self.assertTrue(os.path.exists(fresh))  # fresh tombstone survives
        self.assertFalse(os.path.isdir(lockdir))

    def test_activate_sweeps_opportunistically(self):
        d = tpl.tp_dir(self._tmp)
        os.makedirs(d, exist_ok=True)
        tomb = os.path.join(d, "x.json.removed.5.0")
        with open(tomb, "w", encoding="utf-8") as f:
            f.write("{}")
        self._old(tomb)
        tpl.activate(self._tmp, tpl.build_contract("t", scope=["src/**"]),
                     snapshot=None)
        self.assertFalse(os.path.exists(tomb))


# =====================================================================
# Zero action ceiling (LOW)
# =====================================================================

class TestZeroBudgetCeiling(_StoreIsolated):
    def test_zero_ceiling_means_zero_actions(self):
        c = tpl.build_contract("t", scope=["src/**"], max_actions=0)
        ok, reason = tpl.budget_status(c, 0)
        self.assertFalse(ok, "--max-actions 0 must be a ZERO ceiling, "
                             "never an unmetered contract")
        self.assertIn("BUDGET", reason)

    def test_negative_ceiling_rejected(self):
        with self.assertRaises(ValueError):
            tpl.build_contract("t", scope=["src/**"], max_actions=-1)

    def test_absent_ceiling_is_the_only_unmetered_form(self):
        ok, reason = tpl.budget_status({"budget": {"max_actions": None}}, 5)
        self.assertTrue(ok)
        self.assertIn("no action ceiling", reason)

    def test_zero_ceiling_is_a_human_gate_never_idle_released(self):
        c = tpl.build_contract("t", scope=["src/**"], max_actions=0)
        tpl.activate(self._tmp, c, snapshot=None)
        orphaned, why = tpl.orphan_status(
            self._tmp, c, now=time.time() + 10 * 24 * 3600)
        self.assertFalse(orphaned)
        self.assertIn("human gate", why)

    def test_zero_ceiling_is_grantable(self):
        c = tpl.build_contract("t", scope=["src/**"], max_actions=0)
        tpl.activate(self._tmp, c, snapshot=None)
        out = tpl.grant_budget(self._tmp, 5)
        self.assertEqual(out["budget"]["max_actions"], 5)


# =====================================================================
# trace() goes dark LOUDLY, once (LOW)
# =====================================================================

class TestTraceDarknessWarning(unittest.TestCase):
    def test_warns_once_per_process_never_crashes(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        blocker = os.path.join(tmp, "afile")
        with open(blocker, "w", encoding="utf-8") as f:
            f.write("x")
        ws = os.path.join(blocker, "ws")         # makedirs will fail
        old = tpl._TRACE_FAILED_WARNED
        tpl._TRACE_FAILED_WARNED = False
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                tpl.trace(ws, "event_one")       # no exception
                tpl.trace(ws, "event_two")
            out = err.getvalue()
            self.assertIn("audit trace write failed", out)
            self.assertEqual(out.count("audit trace write failed"), 1,
                             "warning must fire exactly once per process")
        finally:
            tpl._TRACE_FAILED_WARNED = old


# =====================================================================
# Legacy-store adoption never destructively moves on ambiguity (LOW)
# =====================================================================

class TestLegacyStoreAdoption(_StoreIsolated):
    def _legacy(self, ws, meta_workspace=None):
        legacy = os.path.join(tpl.store_home(), "projects",
                              tpl._path_slug(ws))
        os.makedirs(os.path.join(legacy, "knowledge"), exist_ok=True)
        with open(os.path.join(legacy, "knowledge", "index.json"), "w", encoding="utf-8") as f:
            json.dump({"decisions": []}, f)
        if meta_workspace is not None:
            with open(os.path.join(legacy, "meta.json"), "w", encoding="utf-8") as f:
                json.dump({"workspace": meta_workspace}, f)
        return legacy

    def test_no_meta_copies_and_preserves_original(self):
        ws = os.path.join(self._tmp, "proj")
        os.makedirs(ws)
        legacy = self._legacy(ws)                # ownership unprovable
        new_root = tpl.external_store_root(ws)
        self.assertTrue(os.path.isdir(new_root))
        self.assertTrue(os.path.isdir(legacy),
                        "ambiguous legacy store was destructively moved")

    def test_proven_ownership_moves(self):
        ws = os.path.join(self._tmp, "proj")
        os.makedirs(ws)
        legacy = self._legacy(ws, meta_workspace=ws)
        new_root = tpl.external_store_root(ws)
        self.assertTrue(os.path.isdir(new_root))
        self.assertFalse(os.path.isdir(legacy))

    def test_siblings_store_never_stolen(self):
        ws = os.path.join(self._tmp, "proj")
        other = os.path.join(self._tmp, "elsewhere")
        os.makedirs(ws)
        os.makedirs(other)
        legacy = self._legacy(ws, meta_workspace=other)
        tpl.external_store_root(ws)
        self.assertTrue(os.path.isdir(legacy),
                        "a sibling project's store was taken")


# =====================================================================
# host() single seam (LOW)
# =====================================================================

class TestHostSeam(unittest.TestCase):
    def test_host_detection_env_injectable(self):
        self.assertEqual(tpl.host({"CODEX_HOME": "/x"}), "codex")
        self.assertEqual(tpl.host({"CODEX_THREAD_ID": "t1"}), "codex")
        self.assertEqual(tpl.host({"TASKPLANE_STORE": "repo"}), "claude-tag")
        self.assertEqual(tpl.host({}), "claude")

    def test_tier_defaults_consume_the_seam(self):
        with mock.patch.dict(os.environ, {"CODEX_HOME": "/x"}, clear=False):
            self.assertIsNone(tpl._default_tier_models()["cheap"])
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(tpl._default_tier_models()["cheap"], "haiku")


# =====================================================================
# Hook wiring: Windows fail-closed + liveness probe (HIGH/LOW)
# =====================================================================

class TestHookWiring(unittest.TestCase):
    def _entries(self):
        with open(HOOKS_JSON, encoding="utf-8") as f:
            cfg = json.load(f)
        for group in cfg["hooks"].values():
            for matcher in group:
                for h in matcher["hooks"]:
                    yield h

    def test_windows_commands_resolve_roots_and_enforcement_fails_closed(self):
        entries = list(self._entries())
        self.assertGreaterEqual(len(entries), 3)
        for h in entries:
            self.assertIn("${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}",
                          h["command"])
            w = h.get("commandWindows", "")
            self.assertIn("if defined PLUGIN_ROOT", w)
            self.assertIn("CLAUDE_PLUGIN_ROOT", w)
            command = h["command"]
            if command.endswith(" screen") or command.endswith(
                    " screen-dispatch") or command.endswith(" context"):
                self.assertIn("exit /b 2", w,
                              "enforcement/context roots fail closed")
            else:
                self.assertNotIn("exit /b 2", w,
                                 "lifecycle tracing is advisory")

    def test_screen_liveness_probe(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.assertFalse(tpl.screen_liveness(tmp, contract=None)["governed"])
        c = tpl.build_contract("t", scope=["src/**"])
        c["activated_at"] = time.time() - 300
        out = tpl.screen_liveness(tmp, contract=c)
        self.assertTrue(out["governed"])
        self.assertFalse(out["hook_seen"])
        self.assertIn("ZERO screen activity", out["warning"])
        os.makedirs(tpl.tp_dir(tmp), exist_ok=True)
        with open(os.path.join(tpl.tp_dir(tmp), "meter.json"), "w", encoding="utf-8") as f:
            json.dump({c["task_id"]: {"actions": 2,
                                      "last_seen_ts": time.time()}}, f)
        out = tpl.screen_liveness(tmp, contract=c)
        self.assertTrue(out["hook_seen"])
        self.assertIsNone(out["warning"])


if __name__ == "__main__":
    unittest.main()
