"""v2.3.0 fix-wave regression tests — loop.py + track.py findings.

Covers: per-user state-dir ownership (track imports loop's rule), budget
exhaustion visible in the plain-text summary, locked next_action RMW,
corrupt-state fail-closed, mutate() locking, one canonical severity map
(unknowns map UP and block the EM gate), init-over-in-flight refusal,
design-contracts sanctioned graph path, attestation-warning pinning, gate
TOCTOU re-attest under the lock, claim lock shrink, artifact-churn caps,
design_rebaseline trace, plan-gate H2 carry-over under concurrency, EM
refusal naming meta.gate.verdict, unknown gate --task diagnosis, cheap-tier
inherit visibility, track registry safety, and the three design_contract
wiring hooks (requirement attach, approval notices, review notices).
"""
import contextlib
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import depgraph  # noqa: E402
import design_contract as dc  # noqa: E402
import lens  # noqa: E402
import loop  # noqa: E402
import requirements as reqs  # noqa: E402
import taskplane_lite as tp  # noqa: E402
import track  # noqa: E402


TASK = {"id": "t1", "scope": ["src/todo/**"], "tests": "true",
        "criteria": ["complete() marks done"]}


def git_ws(tasks=None):
    ws = tempfile.mkdtemp()
    os.makedirs(os.path.join(ws, "plan"))
    os.makedirs(os.path.join(ws, "src", "todo"))
    open(os.path.join(ws, "src", "todo", "a.py"), "w", encoding="utf-8").write("x=1\n")
    subprocess.run(["git", "init", "-q"], cwd=ws)
    subprocess.run(["git", "config", "user.email", "e@e"], cwd=ws)
    subprocess.run(["git", "config", "user.name", "t"], cwd=ws)
    subprocess.run(["git", "add", "-A"], cwd=ws)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=ws)
    if tasks is not None:
        with open(os.path.join(ws, "plan", "tasks.json"), "w", encoding="utf-8") as f:
            json.dump({"tasks": tasks}, f)
    return ws


def read_trace(ws):
    p = os.path.join(tp.tp_dir(ws), "trace.jsonl")
    if not os.path.exists(p):
        return []
    out = []
    for ln in open(p, encoding="utf-8"):
        with contextlib.suppress(ValueError):
            if ln.strip():
                out.append(json.loads(ln))
    return out


class TestStateDirOwnership(unittest.TestCase):
    """H: loop.state_dir is the single owner of the per-user-state rule."""

    def test_team_plan_keeps_loop_and_track_state_private(self):
        ws = tempfile.mkdtemp()
        os.makedirs(os.path.join(ws, ".taskplane-kb"))
        with open(os.path.join(ws, ".taskplane-kb", "config.json"), "w", encoding="utf-8") as f:
            json.dump({"plan": "team", "store": "repo"}, f)
        # t9 (R-0011 E2): this pop is a real mutation of the caller's env —
        # restore it, or an exported TASKPLANE_STORE vanishes for every
        # later test module (conftest's _env_mutation_guard fails on it).
        _old_store = os.environ.get("TASKPLANE_STORE")
        self.addCleanup(
            lambda: (os.environ.__setitem__("TASKPLANE_STORE", _old_store)
                     if _old_store is not None
                     else os.environ.pop("TASKPLANE_STORE", None)))
        os.environ.pop("TASKPLANE_STORE", None)
        # the KNOWLEDGE store is shared (repo) on a team plan…
        self.assertEqual(tp.get_mode(ws)["store"], "repo")
        # …but loop coordination state stays PRIVATE (external)…
        loop_path = loop._loop_path(ws)
        self.assertNotIn(".taskplane-kb", loop_path)
        self.assertTrue(loop_path.startswith(tp.external_store_root(ws)))
        # …and track state resolves via loop's exported rule — SAME dir.
        self.assertEqual(track._live_loop(ws), loop_path)
        self.assertEqual(track._state_dir(ws), loop.state_dir(ws))
        self.assertNotIn(".taskplane-kb", track._reg_path(ws))

    def test_store_env_repo_is_the_single_exception(self):
        ws = tempfile.mkdtemp()
        old = os.environ.get("TASKPLANE_STORE")
        os.environ["TASKPLANE_STORE"] = "repo"
        try:
            self.assertIn(".taskplane-kb", loop.state_dir(ws))
            self.assertEqual(track._state_dir(ws), loop.state_dir(ws))
        finally:
            if old is None:
                os.environ.pop("TASKPLANE_STORE", None)
            else:
                os.environ["TASKPLANE_STORE"] = old

    def test_partially_migrated_project_agrees_on_live_loop(self):
        # legacy loop.json + an existing external knowledge dir: the old
        # kb_root-based track resolution preferred the (empty) external dir
        # and archived NOTHING; the engine kept reading the legacy file.
        ws = tempfile.mkdtemp()
        legacy = os.path.join(ws, "knowledge", "state")
        os.makedirs(legacy)
        with open(os.path.join(legacy, "loop.json"), "w", encoding="utf-8") as f:
            json.dump({"step": "pm", "goal": "g", "tasks": None,
                       "current_task": 0, "max_fix_cycles": 2,
                       "checkpoints": []}, f)
        os.makedirs(os.path.join(tp.external_store_root(ws), "knowledge",
                                 "state"), exist_ok=True)
        self.assertEqual(track._live_loop(ws), loop._loop_path(ws))
        self.assertEqual(track._live_loop(ws),
                         os.path.join(legacy, "loop.json"))


class TestBudgetExhaustionVisible(unittest.TestCase):
    """H: budget exhaustion is a human gate — the plain-text summary says so."""

    def _ws_with_meter(self, used, max_actions=3):
        ws = git_ws()
        loop.init(ws, "budget goal")
        contract = tp.build_contract("EXECUTE: t1", scope=["src/todo/**"],
                                     max_actions=max_actions)
        tp.activate(ws, contract)
        tid = contract.get("task_id", "_")
        with open(os.path.join(tp.tp_dir(ws), "meter.json"), "w", encoding="utf-8") as f:
            json.dump({tid: {"actions": used, "denies": 0}}, f)
        return ws

    def test_exhausted_budget_blocks_the_summary(self):
        ws = self._ws_with_meter(used=3, max_actions=3)
        out = loop.user_summary(ws)
        self.assertTrue(out["action_required"])
        self.assertIn("tp budget --grant", out["decision"])
        self.assertTrue(out["headline"].startswith(
            "Blocked — action budget exhausted (3/3)"))
        self.assertEqual(out["budget"],
                         {"used": 3, "max": 3, "exhausted": True})

    def test_on_budget_summary_is_not_blocked(self):
        ws = self._ws_with_meter(used=1, max_actions=3)
        out = loop.user_summary(ws)
        self.assertFalse(out["action_required"])
        self.assertNotIn("Blocked", out["headline"])
        self.assertEqual(out["budget"]["exhausted"], False)

    def test_human_gate_decision_is_not_overridden(self):
        ws = self._ws_with_meter(used=3, max_actions=3)
        st = loop.load(ws)
        st["step"] = "plan_approval"
        loop.save(ws, st)
        out = loop.user_summary(ws)
        self.assertTrue(out["action_required"])
        self.assertIn("approve the implementation plan",
                      out["decision"].lower())
        self.assertTrue(out["budget"]["exhausted"])


class TestCorruptStateFailsClosed(unittest.TestCase):
    """M: corrupt state raises tp.StateError with a remedy — no bare
    traceback, no silent reinitialization."""

    def test_corrupt_loop_json_raises_state_error(self):
        ws = git_ws()
        loop.init(ws, "g")
        with open(loop._loop_path(ws), "w", encoding="utf-8") as f:
            f.write("{ truncated")
        with self.assertRaises(tp.StateError) as cm:
            loop.load(ws)
        msg = str(cm.exception)
        self.assertIn("loop state file", msg)
        self.assertIn(loop._loop_path(ws), msg)
        self.assertIn("restore", msg)          # a named remedy

    def test_mutate_fails_closed_and_preserves_the_corrupt_file(self):
        ws = git_ws()
        loop.init(ws, "g")
        with open(loop._loop_path(ws), "w", encoding="utf-8") as f:
            f.write("{ torn")
        with self.assertRaises(tp.StateError):
            with loop.mutate(ws):
                pass
        # the corrupt bytes are still there for forensics — NOT re-inited
        self.assertEqual(open(loop._loop_path(ws), encoding="utf-8").read(), "{ torn")

    def test_missing_loop_json_still_returns_none(self):
        self.assertIsNone(loop.load(tempfile.mkdtemp()))

    def test_corrupt_track_registry_raises_state_error(self):
        ws = git_ws()
        os.makedirs(track._state_dir(ws), exist_ok=True)
        with open(track._reg_path(ws), "w", encoding="utf-8") as f:
            f.write("not json")
        with self.assertRaises(tp.StateError) as cm:
            track.list_(ws)
        self.assertIn("track registry", str(cm.exception))


class TestMutateLocking(unittest.TestCase):
    """M: mutate() serializes through tp.file_lock (never silently
    lock-free — the foundation falls back to mkdir or raises)."""

    def test_mutate_takes_the_shared_file_lock(self):
        ws = git_ws()
        loop.init(ws, "g")
        calls = []
        orig = tp.file_lock

        @contextlib.contextmanager
        def rec(path, **kw):
            calls.append(path)
            with orig(path, **kw):
                yield
        tp.file_lock = rec
        try:
            with loop.mutate(ws) as st:
                st["marker"] = 1
        finally:
            tp.file_lock = orig
        self.assertIn(loop._loop_path(ws), calls)
        self.assertEqual(loop.load(ws)["marker"], 1)


class TestSeverityCanonical(unittest.TestCase):
    """M: ONE severity map; unknown/unmapped severities map UP and BLOCK."""

    def test_unknowns_and_blocking_vocabularies_map_up_to_high(self):
        for s in ("blocker", "major", "critical", "CRITICAL", "P0", "p1",
                  "sev1", "garbage-severity", "", None, "urgent!!"):
            self.assertEqual(loop.normalize_severity(s), "high", s)

    def test_known_lower_severities_map_down_only_when_mapped(self):
        self.assertEqual(loop.normalize_severity("medium"), "med")
        self.assertEqual(loop.normalize_severity("med"), "med")
        self.assertEqual(loop.normalize_severity("minor"), "low")
        self.assertEqual(loop.normalize_severity("low"), "low")
        self.assertEqual(loop.normalize_severity("question"), "info")
        self.assertEqual(loop.normalize_severity("praise"), "info")

    def _em_ws(self, findings_rows, gate=None):
        ws = tempfile.mkdtemp()
        d = os.path.join(ws, ".em-review")
        os.makedirs(d)
        coverage = {e["id"]: "sweep"
                    for e in lens.load_catalog().get("lenses") or []}
        meta = {"lens_coverage": coverage, "impact": {"touched": []},
                "tests": "pytest -q: pass"}
        if gate is not None:
            meta["gate"] = gate
        with open(os.path.join(d, "findings.json"), "w", encoding="utf-8") as f:
            json.dump({"meta": meta, "findings": findings_rows}, f)
        with open(os.path.join(d, "report.md"), "w", encoding="utf-8") as f:
            f.write("# review\nok\n")
        return ws

    def test_foreign_severities_block_the_em_gate(self):
        for sev in ("blocker", "major", "critical", "P0", "??unknown??"):
            ws = self._em_ws([{"severity": sev, "status": "open",
                               "title": "boom"}], gate={"verdict": "pass"})
            errs = loop._engineering_review_errors(ws, None)
            self.assertTrue(any("unresolved" in e and "boom" in e
                                for e in errs), (sev, errs))

    def test_resolved_blocker_and_open_minor_do_not_block(self):
        ws = self._em_ws([{"severity": "blocker", "status": "resolved",
                           "title": "fixed"},
                          {"severity": "minor", "status": "open",
                           "title": "nit"}], gate={"verdict": "pass"})
        self.assertEqual(loop._engineering_review_errors(ws, None), [])

    def test_em_refusal_names_the_recommendation_field(self):
        ws = self._em_ws([], gate={})     # no verdict recorded
        errs = loop._engineering_review_errors(ws, None)
        self.assertTrue(any("meta.gate.verdict" in e for e in errs), errs)


class TestInitOverInflight(unittest.TestCase):
    """M: init over an in-flight loop refuses; --force archives, never
    silently discards."""

    def test_init_refuses_without_force(self):
        ws = git_ws()
        loop.init(ws, "first goal")
        out = loop.init(ws, "second goal")
        self.assertTrue(out.get("refused"))
        self.assertIn("active loop already exists", out["error"])
        self.assertEqual(loop.load(ws)["goal"], "first goal")

    def test_force_archives_the_prior_state(self):
        ws = git_ws()
        loop.init(ws, "first goal")
        out = loop.init(ws, "second goal", force=True)
        self.assertEqual(out["goal"], "second goal")
        archived = out["previous_loop_archived"]
        self.assertTrue(os.path.exists(archived))
        self.assertEqual(json.load(open(archived, encoding="utf-8"))["goal"], "first goal")
        self.assertTrue(any(e["event"] == "loop_init_replaced"
                            for e in read_trace(ws)))

    def test_init_over_terminal_loop_needs_no_force(self):
        ws = git_ws()
        loop.init(ws, "first goal")
        st = loop.load(ws)
        st["step"] = "done"
        loop.save(ws, st)
        out = loop.init(ws, "second goal")
        self.assertNotIn("refused", out)
        self.assertEqual(loop.load(ws)["goal"], "second goal")

    def test_completed_design_only_can_seed_plan_with_exact_approval(self):
        ws = git_ws()
        loop.save(ws, {
            "step": "done", "goal": "design", "design_only": True,
            "design_required": True, "requirement_id": "R-0006",
            "spec_path": "specs/spec.md", "design_fingerprint": "f" * 64,
            "design_approved_by": "human:vdemkiv",
        })

        with mock.patch.object(loop, "_design_current_errors",
                               return_value=[]):
            out = loop.init(
                ws, "build", spec_path="specs/spec.md",
                requirement_id="R-0006", parallel=True,
                by="human:vdemkiv", reuse_approved_design=True)

        state = loop.load(ws)
        self.assertEqual(state["step"], "plan")
        self.assertTrue(state["design_required"])
        self.assertFalse(state["design_only"])
        self.assertEqual(state["design_fingerprint"], "f" * 64)
        self.assertEqual(state["design_reused_by"], "human:vdemkiv")
        self.assertTrue(os.path.exists(out["previous_loop_archived"]))

    def test_design_reuse_refuses_requirement_drift_without_mutation(self):
        ws = git_ws()
        prior = {
            "step": "done", "goal": "design", "design_only": True,
            "design_required": True, "requirement_id": "R-0006",
            "spec_path": "specs/spec.md", "design_fingerprint": "f" * 64,
            "design_approved_by": "human:vdemkiv",
        }
        loop.save(ws, prior)

        with mock.patch.object(loop, "_design_current_errors",
                               return_value=[]):
            out = loop.init(
                ws, "build", spec_path="specs/spec.md",
                requirement_id="R-9999", by="human:vdemkiv",
                reuse_approved_design=True)

        self.assertTrue(out["refused"])
        self.assertIn("requirement does not match", " ".join(out["blockers"]))
        self.assertEqual(loop.load(ws), prior)


class TestNextActionLockedRMW(unittest.TestCase):
    """M: next_action's built→evaluate flip happens under mutate() on a
    FRESH read — a concurrently gated task is never reverted."""

    def test_stale_snapshot_does_not_clobber_concurrent_gate(self):
        ws = git_ws()
        state = {"step": "execute", "parallel": True, "goal": "g",
                 "submission_required": False, "max_fix_cycles": 2,
                 "checkpoints": [], "current_task": 0, "baseline": "HEAD",
                 "tasks": [
                     {"id": "t1", "scope": ["src/todo/**"], "tests": "true",
                      "status": "built", "fix_cycles": 0},
                     {"id": "t2", "scope": ["src/todo/**"], "tests": "true",
                      "status": "passed", "fix_cycles": 0}]}
        loop.save(ws, state)
        # the UNLOCKED initial read sees a stale snapshot where t2 is still
        # running (simulating a wave worker's gate landing mid-call)
        stale = copy.deepcopy(state)
        stale["tasks"][1]["status"] = "running"
        orig_load, calls = loop.load, {"n": 0}

        def fake(w):
            calls["n"] += 1
            return stale if calls["n"] == 1 else orig_load(w)
        loop.load = fake
        try:
            loop.next_action.__wrapped__(ws)
        finally:
            loop.load = orig_load
        after = loop.load(ws)
        self.assertEqual(after["tasks"][1]["status"], "passed")  # NOT reverted
        self.assertEqual(after["step"], "evaluate")
        self.assertEqual(after["current_task"], 0)


class TestGateStalenessInsideLock(unittest.TestCase):
    """M (TOCTOU): the final staleness re-attest runs inside the locked
    critical section — a workspace edit during validation is refused."""

    def test_mutation_during_validation_is_caught(self):
        ws = git_ws([TASK])
        loop.init(ws, "g", spec_path="s", checkpoints=[])
        loop.gate(ws, "pass")                       # plan → execute
        self.assertEqual(loop.load(ws)["step"], "execute")
        with open(os.path.join(ws, "src", "todo", "a.py"), "a", encoding="utf-8") as f:
            f.write("y=2\n")
        self.assertTrue(loop.submit(ws, "pass")["submitted"])
        orig = loop._task_dod_errors

        def sabotage(*a, **k):
            with open(os.path.join(ws, "src", "todo", "a.py"), "a", encoding="utf-8") as f:
                f.write("z=3\n")                    # eager editor mid-gate
            return []
        loop._task_dod_errors = sabotage
        try:
            out = loop.gate(ws, "pass")
        finally:
            loop._task_dod_errors = orig
        self.assertIn("error", out)
        self.assertIn("changed after worker submission", out["error"])
        self.assertEqual(loop.load(ws)["step"], "execute")   # did NOT advance

    def test_contract_cleared_only_after_advancing_gate(self):
        ws = git_ws([TASK])
        loop.init(ws, "g", spec_path="s", checkpoints=[])
        loop.next_action(ws)                        # activates plan contract
        cpath = os.path.join(tp.tp_dir(ws), "active_contract.json")
        self.assertTrue(os.path.exists(cpath))
        loop.gate(ws, "pass")
        self.assertFalse(os.path.exists(cpath))     # released on transition


class TestClaimLockShrink(unittest.TestCase):
    """L: claim() snapshots git info BEFORE the global lock; the claim
    itself re-checks claimability under the lock."""

    def _parallel_ws(self):
        ws = git_ws([dict(TASK, id="t1")])
        loop.init(ws, "g", spec_path="s", checkpoints=[], parallel=True)
        loop.gate(ws, "pass")                       # plan → execute
        return ws

    def test_git_snapshot_happens_before_the_lock(self):
        ws = self._parallel_ws()
        agent_ws = os.path.join(ws, ".tp-work", "t1")
        subprocess.run(["git", "worktree", "add", "-q", agent_ws, "-b",
                        "tp/t1"], cwd=ws)
        order = []
        orig_head, orig_mutate = tp.git_head, loop.mutate
        tp.git_head = lambda w: (order.append("git_head"), orig_head(w))[1]

        @contextlib.contextmanager
        def rec_mutate(w):
            order.append("mutate")
            with orig_mutate(w) as st:
                yield st
        loop.mutate = rec_mutate
        try:
            out = loop.claim(ws, "t1", agent_ws)
        finally:
            tp.git_head, loop.mutate = orig_head, orig_mutate
        self.assertEqual(out["claimed"], "t1")
        self.assertIn("git_head", order)
        self.assertIn("mutate", order)
        self.assertLess(order.index("git_head"), order.index("mutate"))

    def test_claim_rechecks_status_under_the_lock(self):
        ws = self._parallel_ws()
        agent_ws = os.path.join(ws, ".tp-work", "t1")
        subprocess.run(["git", "worktree", "add", "-q", agent_ws, "-b",
                        "tp/t1"], cwd=ws)
        orig_dor = tp.dor_check

        def concurrent_winner(contract, w, snap):
            st = json.load(open(loop._loop_path(ws), encoding="utf-8"))
            st["tasks"][0]["status"] = "passed"     # settled while preparing
            tp.atomic_write_json(loop._loop_path(ws), st, indent=2)
            return orig_dor(contract, w, snap)
        tp.dor_check = concurrent_winner
        try:
            out = loop.claim(ws, "t1", agent_ws)
        finally:
            tp.dor_check = orig_dor
        self.assertIn("error", out)
        self.assertIn("not claimable", out["error"])


class TestGateUnknownTask(unittest.TestCase):
    """L: gate --task with an unknown id names the fault and the members."""

    def test_unknown_task_id_is_diagnosed(self):
        ws = git_ws([dict(TASK, id="ta"), dict(TASK, id="tb")])
        loop.init(ws, "g", spec_path="s", checkpoints=[], parallel=True)
        loop.gate(ws, "pass")                       # plan → execute
        out = loop.gate(ws, "pass", task_id="zz")
        self.assertIn("unknown task id 'zz'", out["error"])
        self.assertIn("ta", out["error"])
        self.assertIn("tb", out["error"])


class TestCheapTierInheritVisible(unittest.TestCase):
    """L: a non-standard tier resolving to inherit is stated in the brief."""

    def test_brief_states_the_inert_routing(self):
        ws = git_ws([dict(TASK, model="cheap")])
        loop.init(ws, "g", spec_path="s", checkpoints=[])
        loop.gate(ws, "pass")                       # plan → execute
        orig = tp.model_for_tier
        tp.model_for_tier = lambda tier: None
        try:
            out = loop.next_action.__wrapped__(ws)
        finally:
            tp.model_for_tier = orig
        self.assertIn("TASKPLANE_MODEL_CHEAP", out["model_note"])
        self.assertIn("no effect", out["model_note"])

    def test_resolved_tier_carries_no_note(self):
        ws = git_ws([dict(TASK, model="cheap")])
        loop.init(ws, "g", spec_path="s", checkpoints=[])
        loop.gate(ws, "pass")
        orig = tp.model_for_tier
        tp.model_for_tier = lambda tier: "some-cheap-model"
        try:
            out = loop.next_action.__wrapped__(ws)
        finally:
            tp.model_for_tier = orig
        self.assertNotIn("model_note", out)


class TestAttestationWarning(unittest.TestCase):
    """M: the unattributed-approve branch is fully pinned — warning payload,
    trace event, and the signoff variant."""

    def _at_plan_approval(self):
        ws = git_ws([TASK])
        loop.init(ws, "g", spec_path="s", checkpoints=["plan"])
        loop.gate(ws, "pass")
        self.assertEqual(loop.load(ws)["step"], "plan_approval")
        return ws

    def test_plan_approval_without_by_warns_and_traces(self):
        ws = self._at_plan_approval()
        out = loop.approve(ws)
        self.assertIn("without --by", out["warning"])
        ev = [e for e in read_trace(ws)
              if e["event"] == "loop_approve_unattributed"]
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["gate"], "plan_approval")
        self.assertEqual(out["step"], "execute")

    def test_plan_approval_with_by_does_not_warn(self):
        ws = self._at_plan_approval()
        out = loop.approve(ws, by="Dana R. — thread approval")
        self.assertNotIn("warning", out)
        self.assertFalse(any(e["event"] == "loop_approve_unattributed"
                             for e in read_trace(ws)))

    def test_signoff_without_by_warns_and_traces(self):
        ws = git_ws([TASK])
        loop.init(ws, "g", spec_path="s", checkpoints=[])
        st = loop.load(ws)
        st["step"] = "signoff"
        st["signoff_evidence"] = {
            "schema": "taskplane.signoff-evidence/v1",
            "integration_revision": tp.git_head(ws),
            "dod": {"passed": True, "errors": [], "notices": [],
                    "scope": [], "baseline": None},
            "notices": [],
        }
        loop.save(ws, st)
        orig = loop._signoff_dod
        loop._signoff_dod = lambda w, s: {"passed": True, "errors": [],
                                          "scope": [], "baseline": None}
        try:
            out = loop.approve(ws)
        finally:
            loop._signoff_dod = orig
        self.assertIn("without --by", out["warning"])
        ev = [e for e in read_trace(ws)
              if e["event"] == "loop_approve_unattributed"]
        self.assertEqual(ev[0]["gate"], "signoff")
        self.assertEqual(out["step"], "retro")


class TestDesignRebaselineTrace(unittest.TestCase):
    """L: the pre-approval design rebaseline leaves its audit trace."""

    def test_rebaseline_traces_old_and_new_prefixes(self):
        ws = git_ws()
        depgraph.scan(ws)
        current = (depgraph.load(ws).get("meta") or {}).get(
            "content_fingerprint")
        self.assertTrue(current)
        loop.save(ws, {"step": "design", "design_required": True,
                       "goal": "g", "max_fix_cycles": 2, "checkpoints": [],
                       "tasks": None, "current_task": 0,
                       "design_graph_fingerprint": "stale-fp"})
        loop.next_action.__wrapped__(ws)   # DoR may block; rebaseline first
        ev = [e for e in read_trace(ws) if e["event"] == "design_rebaseline"]
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["old"], "stale-fp"[:12])
        self.assertEqual(ev[0]["new"], (current or "")[:12])
        self.assertEqual(loop.load(ws)["design_graph_fingerprint"], current)


class TestPlanGateCarryOverConcurrency(unittest.TestCase):
    """L (H2 twin): plan-gate carry-over fields survive a concurrent state
    update landing mid-validation."""

    def test_ab_parallel_selection_graphdor_carry_over(self):
        variants = [dict(TASK, id="ta", variant="A"),
                    dict(TASK, id="tb", variant="B")]
        ws = git_ws(variants)
        loop.init(ws, "g", spec_path="s", checkpoints=["plan"])
        st = loop.load(ws)
        st["selection"] = {"choice": "stale-round"}   # round-scoped leftover
        loop.save(ws, st)
        orig = depgraph.readiness

        def concurrent(w, tasks):
            s = json.load(open(loop._loop_path(ws), encoding="utf-8"))
            s["_concurrent_marker"] = 1               # another writer lands
            tp.atomic_write_json(loop._loop_path(ws), s, indent=2)
            return orig(w, tasks)
        depgraph.readiness = concurrent
        try:
            out = loop.gate(ws, "pass")
        finally:
            depgraph.readiness = orig
        self.assertEqual(out["step"], "plan_approval")
        after = loop.load(ws)
        self.assertEqual(after["_concurrent_marker"], 1)  # not clobbered
        self.assertTrue(after["ab"])                       # from validation
        self.assertTrue(after["parallel"])                 # ab forces it
        self.assertIn("graph_dor", after)
        self.assertNotIn("selection", after)               # stale key popped


class TestPublishArtifactsChurn(unittest.TestCase):
    """L: gate snapshots stop re-copying an unchanged graph and stop reading
    all of HEADLINES.md per gate (tail read + rotation cap)."""

    def _root(self, ws):
        state = loop.load(ws)
        import re
        slug = re.sub(r"[^a-z0-9]+", "-",
                      str(state.get("goal")).lower()).strip("-")[:60]
        return os.path.join(tp.store_root(ws), "artifacts", slug)

    def test_graph_snapshot_skipped_when_fingerprint_unchanged(self):
        ws = git_ws()
        loop.init(ws, "pub goal")
        fake = {"modules": {"m": {"kind": "module"}},
                "meta": {"content_fingerprint": "fp1"}}
        orig = depgraph.load
        depgraph.load = lambda w: fake
        try:
            root = loop._publish_artifacts(ws)
            gp = os.path.join(root, "graph.json")
            self.assertTrue(os.path.exists(gp))
            marker = {"meta": {"content_fingerprint": "fp1"},
                      "modules": {"m": {}}, "marker": True}
            with open(gp, "w", encoding="utf-8") as f:
                json.dump(marker, f)
            loop._publish_artifacts(ws)             # same fp → no rewrite
            self.assertTrue(json.load(open(gp, encoding="utf-8")).get("marker"))
            fake["meta"]["content_fingerprint"] = "fp2"
            loop._publish_artifacts(ws)             # new fp → rewritten
            self.assertNotIn("marker", json.load(open(gp, encoding="utf-8")))
        finally:
            depgraph.load = orig

    def test_headlines_rotate_past_the_cap(self):
        ws = git_ws()
        loop.init(ws, "pub goal")
        root = self._root(ws)
        os.makedirs(root, exist_ok=True)
        p = os.path.join(root, "HEADLINES.md")
        filler = "- 2026-01-01 00:00 UTC · old line " + "x" * 480
        with open(p, "w", encoding="utf-8") as f:
            f.write("# pub goal — progress log\n\n")
            for i in range(600):
                f.write(f"{filler} {i}\n")
        self.assertGreater(os.path.getsize(p), 262144)
        self.assertIsNotNone(loop._publish_artifacts(ws))
        lines = open(p, encoding="utf-8").read().splitlines()
        body = [l for l in lines if l.strip() and not l.startswith("#")]
        self.assertLessEqual(len(body), 500)
        self.assertIn("taskplane loop", body[-1])   # newest line survived
        self.assertIn("599", body[-2])              # …after the newest OLD one


class TestTrackSafety(unittest.TestCase):
    """M/L: atomic registry writes, engine-lock over switch/close, archive/
    restore against the engine's own loop path."""

    def test_registry_written_atomically(self):
        ws = git_ws()
        calls = []
        orig = tp.atomic_write_json

        def rec(path, data, **kw):
            calls.append(path)
            return orig(path, data, **kw)
        tp.atomic_write_json = rec
        try:
            track.new(ws, "t1", "goal one")
        finally:
            tp.atomic_write_json = orig
        self.assertIn(track._reg_path(ws), calls)

    def test_switch_and_close_take_the_engine_lock(self):
        ws = git_ws()
        track.new(ws, "t1", "goal one")
        track.new(ws, "t2", "goal two")
        calls = []
        orig = tp.file_lock

        @contextlib.contextmanager
        def rec(path, **kw):
            calls.append(path)
            with orig(path, **kw):
                yield
        tp.file_lock = rec
        try:
            track.switch(ws, "t2")
            track.close(ws, "t2")
        finally:
            tp.file_lock = orig
        self.assertTrue(all(c == loop._loop_path(ws) for c in calls))
        self.assertGreaterEqual(len(calls), 2)

    def test_switch_archives_and_restores_the_engine_loop(self):
        ws = git_ws()
        track.new(ws, "t1", "goal one")             # first → auto-active
        loop.init(ws, "track one loop")
        live = loop._loop_path(ws)
        self.assertTrue(os.path.exists(live))
        track.new(ws, "t2", "goal two")
        out = track.switch(ws, "t2")
        self.assertEqual(out["active"], "t2")
        self.assertFalse(out["has_loop_state"])
        archived = os.path.join(track._tracks_dir(ws, "t1"), "loop.json")
        self.assertTrue(os.path.exists(archived))
        self.assertIsNone(loop.load(ws))
        back = track.switch(ws, "t1")
        self.assertTrue(back["has_loop_state"])
        self.assertEqual(loop.load(ws)["goal"], "track one loop")


class TestDesignWiringHooks(unittest.TestCase):
    """v2.3.0 wiring for the design_contract owner's landed helpers."""

    def _design_ws(self):
        ws = git_ws()
        depgraph.scan(ws)
        loop.save(ws, {"step": "design", "design_required": True,
                       "goal": "g", "max_fix_cycles": 2, "checkpoints": [],
                       "tasks": None, "current_task": 0})
        return ws

    # -- hook 1: requirement attach on loop next / loop gate ---------------
    def test_next_attaches_a_valid_requirement_and_persists(self):
        ws = self._design_ws()
        rid = reqs.record_requirement(ws, "Attachable",
                                      acceptance=["works"])["id"]
        out = loop.next_action.__wrapped__(ws, rid=rid)
        self.assertNotEqual(out.get("error"), "requirement attach failed")
        self.assertEqual(loop.load(ws)["requirement_id"], rid)
        self.assertTrue(any(e["event"] == "design_requirement_attached"
                            for e in read_trace(ws)))

    def test_next_blocks_on_invalid_requirement(self):
        ws = self._design_ws()
        out = loop.next_action.__wrapped__(ws, rid="R-9999")
        self.assertEqual(out["error"], "requirement attach failed")
        self.assertTrue(any("does not exist" in b for b in out["blockers"]))
        self.assertIsNone(loop.load(ws).get("requirement_id"))

    def test_next_refuses_to_swap_an_anchored_requirement(self):
        ws = self._design_ws()
        r1 = reqs.record_requirement(ws, "One", acceptance=["a"])["id"]
        r2 = reqs.record_requirement(ws, "Two", acceptance=["b"])["id"]
        loop.next_action.__wrapped__(ws, rid=r1)
        out = loop.next_action.__wrapped__(ws, rid=r2)
        self.assertEqual(out["error"], "requirement attach failed")
        self.assertTrue(any("refusing to swap" in b for b in out["blockers"]))
        self.assertEqual(loop.load(ws)["requirement_id"], r1)

    def test_gate_attaches_a_requirement_before_evaluating(self):
        ws = git_ws([TASK])
        loop.init(ws, "free-text goal", checkpoints=[])   # → pm
        os.makedirs(os.path.join(ws, "specs"), exist_ok=True)
        open(os.path.join(ws, "specs", "spec.md"), "w", encoding="utf-8").write("# spec\n")
        rid = reqs.record_requirement(
            ws, "Gate attach", functional=["attach before evaluation"],
            acceptance=["done"],
            nfr={"security": "no new trust boundary",
                 "architecture": "retain the existing loop boundary"})["id"]
        out = loop.gate(ws, "pass", rid=rid)
        self.assertNotIn("blockers", out)
        after = loop.load(ws)
        self.assertEqual(after["requirement_id"], rid)
        self.assertEqual(after["step"], "plan")

    def test_gate_blocks_on_bad_requirement_without_evaluating(self):
        ws = git_ws([TASK])
        loop.init(ws, "free-text goal", checkpoints=[])
        out = loop.gate(ws, "pass", rid="R-4242")
        self.assertEqual(out["error"], "requirement attach failed — the "
                                       "gate was not evaluated")
        self.assertEqual(loop.load(ws)["step"], "pm")     # gate NOT evaluated

    # -- hook 2: design approval notices ----------------------------------
    def _fake_contract(self):
        return {"decision": "Design approved.",
                "contracts": [{"id": "contract:order-cancelled-v2",
                               "relation": "provides"}],
                "graph": {"proposed_modules": []},
                "lens_evidence": [{"lens": "security",
                                   "self_attested": True,
                                   "produced_by": "designer"}]}

    def test_next_at_design_approval_merges_notices(self):
        ws = self._design_ws()
        st = loop.load(ws)
        st["step"] = "design_approval"
        loop.save(ws, st)
        orig_dod = loop._design_dod_errors
        orig_fp = loop._design_evidence_fingerprint
        orig_contract = dc.design_contract
        loop._design_dod_errors = lambda w, s: []
        loop._design_evidence_fingerprint = lambda w, c=None: "f" * 40
        dc.design_contract = lambda w: (self._fake_contract(), [])
        try:
            out = loop.next_action.__wrapped__(ws)
        finally:
            loop._design_dod_errors = orig_dod
            loop._design_evidence_fingerprint = orig_fp
            dc.design_contract = orig_contract
        self.assertTrue(out["paused"])
        self.assertTrue(any("SELF-ATTESTED" in n for n in out["notices"]))

    def test_approve_merges_notices_and_records_design_contracts(self):
        ws = self._design_ws()
        rid = reqs.record_requirement(ws, "Anchor", acceptance=["a"])["id"]
        st = loop.load(ws)
        st["step"] = "design_approval"
        st["requirement_id"] = rid
        loop.save(ws, st)
        recorded = {}
        orig_dod = loop._design_dod_errors
        orig_fp = loop._design_evidence_fingerprint
        orig_contract = loop._design_contract
        orig_rec = loop.kb.record_decision
        loop._design_dod_errors = lambda w, s: []
        loop._design_evidence_fingerprint = lambda w, c=None: "f" * 40
        loop._design_contract = lambda w: (self._fake_contract(), [])
        loop.kb.record_decision = \
            lambda *a, **k: recorded.update(k) or {"id": "D-0001"}
        try:
            out = loop.approve(ws, by="Dana")
        finally:
            loop._design_dod_errors = orig_dod
            loop._design_evidence_fingerprint = orig_fp
            loop._design_contract = orig_contract
            loop.kb.record_decision = orig_rec
        # notices in the response AND in the recorded approval decision
        self.assertTrue(any("SELF-ATTESTED" in n for n in out["notices"]))
        self.assertIn("SELF-ATTESTED", recorded["context"])
        # the sanctioned mechanical path: design contract now IN the graph
        g = depgraph.load(ws)
        self.assertIn("contract:order-cancelled-v2", g["modules"])
        self.assertTrue(any(e["from"] == f"req:{rid}"
                            and e["to"] == "contract:order-cancelled-v2"
                            for e in g["edges"]))
        self.assertTrue(any(e["event"] == "design_contracts_recorded"
                            for e in read_trace(ws)))
        # …and graph readiness for a plan task declaring it no longer blocks
        ready = depgraph.readiness(ws, [dict(
            TASK, contracts=["contract:order-cancelled-v2"])])
        self.assertFalse(any("contracts are not recorded" in e
                             for e in ready["errors"]), ready["errors"])

    # -- hook 3: EM/sign-off design review notices -------------------------
    def _signoff_ws(self):
        ws = git_ws([TASK])
        loop.init(ws, "g", spec_path="s", checkpoints=[])
        d = os.path.join(ws, ".em-review")
        os.makedirs(d, exist_ok=True)
        meta = {"design": {"accepted_drift": [
            {"drift": "renamed module", "reason": "clearer",
             "accepted_by": "Dana"}]}}
        with open(os.path.join(d, "findings.json"), "w", encoding="utf-8") as f:
            json.dump({"meta": meta, "findings": []}, f)
        st = loop.load(ws)
        st["step"] = "signoff"
        st["signoff_evidence"] = {
            "schema": "taskplane.signoff-evidence/v1",
            "integration_revision": tp.git_head(ws),
            "dod": {"passed": True, "errors": [], "notices": [],
                    "scope": [], "baseline": None},
            "notices": dc.design_review_notices(meta),
        }
        loop.save(ws, st)
        return ws

    def test_signoff_payload_includes_design_review_notices(self):
        ws = self._signoff_ws()
        orig = loop._signoff_dod
        loop._signoff_dod = lambda w, s: {"passed": False, "errors": ["x"],
                                          "scope": [], "baseline": None}
        try:
            out = loop.next_action.__wrapped__(ws)
        finally:
            loop._signoff_dod = orig
        self.assertTrue(any("accepted design drift (by Dana)" in n
                            for n in out["notices"]))

    def test_signoff_approve_includes_design_review_notices(self):
        ws = self._signoff_ws()
        orig = loop._signoff_dod
        loop._signoff_dod = lambda w, s: {"passed": True, "errors": [],
                                          "scope": [], "baseline": None}
        try:
            out = loop.approve(ws, by="Dana")
        finally:
            loop._signoff_dod = orig
        self.assertEqual(out["step"], "retro")
        self.assertTrue(any("accepted design drift" in n
                            for n in out["notices"]))


if __name__ == "__main__":
    unittest.main()
