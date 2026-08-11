"""The evals layer: was the machinery USED? (WS-F)

Everything else in this suite asks whether the machinery is CORRECT. Nothing
asked whether it was used, and that is the gap the product actually fell into:

    "here we go again no inline dashboard visualisation. no report nothing?"
    "this is not the graph and dependency visualisation we designed"

In both cases the engine rendered the artifact, wrote it to disk, pointed at
it in the payload and told the assistant to show it. Every unit test passed,
because nothing was wrong with any unit. The engine could not see the
failure, so nothing recorded it.

Two properties carry this design and both are pinned here.

THE ABSENCE IS THE MEASUREMENT. An obligation issued and never acknowledged
is the complaint above, in a ledger, countable. So issuance must be
unconditional and non-blocking, and the missing acknowledgement must cost
nothing — the moment it costs a gate it stops being an instrument.

A SUBSTITUTE IS NOT A SKIP. The second complaint is a different failure from
the first: something WAS shown, just not the thing the product built. The
obligation carries the engine artifact's content fingerprint, so an assistant
that draws its own chart has nothing to cite. `test_a_substitute_is_counted_
separately_from_a_skip` is the one to read first.

Every assertion here was observed FAILING before it was kept.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import ci_evals  # noqa: E402
import loop  # noqa: E402
import obligations  # noqa: E402
import taskplane_lite as tp  # noqa: E402


class _Ws(unittest.TestCase):
    def setUp(self):
        self._home = os.environ.get("TASKPLANE_HOME")
        self.home = tempfile.mkdtemp(prefix="tp-ob-home-")
        os.environ["TASKPLANE_HOME"] = self.home
        self.ws = tempfile.mkdtemp(prefix="tp-ob-ws-")
        os.makedirs(os.path.join(self.ws, "specs"))
        with open(os.path.join(self.ws, "specs", "spec.md"), "w",
                  encoding="utf-8") as f:
            f.write("# spec\n")
        with open(os.path.join(self.ws, "a.py"), "w", encoding="utf-8") as f:
            f.write("x = 1\n")
        for args in (["init", "-q"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "t"], ["add", "-A"],
                     ["commit", "-qm", "base"]):
            subprocess.run(["git", *args], cwd=self.ws, capture_output=True)

    def tearDown(self):
        if self._home is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self._home
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.ws, ignore_errors=True)

    def _artifact(self, body="<div>engine built this</div>"):
        p = os.path.join(self.ws, "art.html")
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        return p


class TestTheLedgerRecordsWhatWasDemanded(_Ws):
    def test_an_unacknowledged_obligation_is_the_measurement(self):
        oid = obligations.issue(self.ws, "render_dashboard",
                                detail="show it", step="em",
                                artifact=self._artifact())
        st = obligations.status(self.ws)
        self.assertEqual(st["issued"], 1)
        self.assertEqual(st["acknowledged"], 0)
        self.assertEqual([o["id"] for o in st["open"]], [oid])

    def test_an_acknowledgement_closes_it(self):
        art = self._artifact()
        oid = obligations.issue(self.ws, "render_dashboard", detail="d",
                                artifact=art)
        obligations.acknowledge(
            self.ws, oid, fingerprint=obligations.artifact_fingerprint(art))
        st = obligations.status(self.ws)
        self.assertEqual(st["open"], [])
        self.assertEqual(st["acknowledged"], 1)

    def test_a_substitute_is_counted_separately_from_a_skip(self):
        """THE assertion. 'this is not the graph we designed' is a different
        failure from 'no graph at all', and collapsing them would lose the
        distinction that names the fix."""
        art = self._artifact()
        oid = obligations.issue(self.ws, "render_graph", detail="d",
                                artifact=art)
        obligations.acknowledge(self.ws, oid, fingerprint="0000not-the-thing")
        st = obligations.status(self.ws)
        self.assertEqual(st["open"], [], "it WAS acknowledged")
        self.assertEqual(len(st["mismatched"]), 1)
        self.assertEqual(st["mismatched"][0]["kind"], "render_graph")

    def test_the_fingerprint_is_of_content_not_of_the_path(self):
        """A path proves nothing — the artifact has to be the one built."""
        art = self._artifact("<div>one</div>")
        first = obligations.artifact_fingerprint(art)
        with open(art, "w", encoding="utf-8") as f:
            f.write("<div>two</div>")
        self.assertNotEqual(first, obligations.artifact_fingerprint(art))

    def test_only_what_the_engine_cannot_see_is_an_obligation(self):
        """Fan-out, step order and gate attribution are already engine
        records. A second record of the same fact would be free to disagree
        with the first."""
        self.assertEqual(obligations.KINDS, obligations.RENDER_KINDS)
        self.assertIsNone(obligations.issue(self.ws, "dispatch_lenses",
                                            detail="not ours"))

    def test_an_unreadable_ledger_reports_nothing_rather_than_lying(self):
        self.assertEqual(obligations.read(self.ws), [])
        self.assertEqual(obligations.status(self.ws)["issued"], 0)


class TestTheInstrumentGatesNothing(_Ws):
    """The contract yield_meter.py already holds. An instrument that can cost
    someone a gate is a gate, and people route around gates."""

    def test_a_transition_still_succeeds_when_the_ledger_cannot_be_written(self):
        real = obligations.ledger_path

        def boom(_ws):
            raise OSError("store is gone")
        obligations.ledger_path = boom
        self.addCleanup(setattr, obligations, "ledger_path", real)
        loop.init(self.ws, "goal")
        out = loop.next_action(self.ws)
        self.assertNotIn("error", out)
        self.assertIn("dashboard", out)

    def test_leaving_an_obligation_open_blocks_no_gate(self):
        loop.init(self.ws, "goal")
        loop.next_action(self.ws)
        self.assertTrue(obligations.status(self.ws)["open"])
        out = loop.gate(self.ws, "pass")
        self.assertNotIn("error", out)

    def test_the_engine_never_reads_the_ledger(self):
        """Pinned the way the yield meter is: the engine may WRITE the
        instrument and must never let it influence a decision."""
        for name in ("loop.py", "taskplane_lite.py", "lens.py",
                     "evidence.py", "audit.py"):
            src = open(os.path.join(REPO, "taskplane", name),
                       encoding="utf-8").read()
            with self.subTest(module=name):
                self.assertNotIn("obligations.status", src)
                self.assertNotIn("obligations.read", src)


class TestThePayloadCarriesTheObligation(_Ws):
    def test_a_transition_names_the_id_and_how_to_discharge_it(self):
        loop.init(self.ws, "goal")
        d = loop.next_action(self.ws)["dashboard"]
        self.assertTrue(d["obligation"].startswith("o-"))
        self.assertIn("tp ack", d["ack"])
        self.assertIn(d["obligation"], d["ack"])

    def test_the_obligation_fingerprints_the_dashboard_the_engine_built(self):
        loop.init(self.ws, "goal")
        oid = loop.next_action(self.ws)["dashboard"]["obligation"]
        row = next(r for r in obligations.read(self.ws)
                   if r.get("id") == oid and r.get("event") == "issued")
        self.assertEqual(
            row["fingerprint"],
            obligations.artifact_fingerprint(
                os.path.join(tp.tp_dir(self.ws), "dashboard.html")))

    def test_a_failed_render_issues_no_obligation(self):
        """Demanding that someone show an artifact that was never built
        would make the instrument's own numbers fiction."""
        import dashboard
        original = dashboard.widget
        dashboard.widget = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("renderer down"))
        self.addCleanup(setattr, dashboard, "widget", original)
        import views
        views._VIEW_FAILED_WARNED = True
        loop.init(self.ws, "goal")
        out = loop.next_action(self.ws)
        self.assertIn("error", out["dashboard"])
        self.assertNotIn("obligation", out["dashboard"])


class TestTheAckCommand(_Ws):
    def _tp(self, *args):
        return subprocess.run(
            [sys.executable, os.path.join(REPO, "taskplane", "tp.py"), "ack",
             *args, "--workspace", self.ws],
            capture_output=True, text=True, env=dict(os.environ))

    def test_ack_resolves_the_fingerprint_from_the_artifact_it_names(self):
        """The honest path has to be the SHORT one, or nobody takes it."""
        loop.init(self.ws, "goal")
        oid = loop.next_action(self.ws)["dashboard"]["obligation"]
        r = self._tp(oid, "--evidence", "shown inline")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(obligations.status(self.ws)["open"], [])
        self.assertEqual(obligations.status(self.ws)["mismatched"], [])

    def test_status_lists_what_is_still_open(self):
        loop.init(self.ws, "goal")
        oid = loop.next_action(self.ws)["dashboard"]["obligation"]
        out = json.loads(self._tp("--status").stdout)
        self.assertEqual([o["id"] for o in out["open"]], [oid])
        self.assertIn("CLAIM", out["note"])

    def test_ack_without_an_id_refuses(self):
        self.assertEqual(self._tp().returncode, 1)


class TestTheScorer(unittest.TestCase):
    FP = "aaaabbbbccccdddd"

    def _score(self, trace, ledger, dispatch):
        return ci_evals.score(trace, ledger, dispatch)

    def test_a_skipped_render_scores_zero_not_unknown(self):
        res = self._score(
            [], [{"event": "issued", "id": "o-1", "kind": "render_dashboard",
                  "fingerprint": self.FP}],
            {"expected": 0, "unobserved": 0, "hook_active": True})
        self.assertEqual(res["artifact_surfacing"]["rate"], 0.0)
        self.assertEqual(res["artifact_surfacing"]["skipped"], 1)

    def test_no_obligations_scores_unknown_not_zero(self):
        """An instrument must not slander a session for a step it never
        reached — the discipline the yield meter uses for undispositioned
        findings."""
        res = self._score([], [], {"expected": 0, "unobserved": 0,
                                   "hook_active": True})
        self.assertIsNone(res["artifact_surfacing"]["rate"])

    def test_fanout_without_the_hook_is_unknown_not_zero(self):
        """With no PreToolUse Task hook the engine sees zero dispatches,
        which is indistinguishable from dispatching none."""
        res = self._score([], [], {"expected": 8, "unobserved": 8,
                                   "hook_active": False})
        self.assertIsNone(res["agent_fanout"]["rate"])
        self.assertIn("UNKNOWN", res["agent_fanout"]["note"])

    def test_fanout_with_the_hook_is_scored(self):
        res = self._score([], [], {"expected": 8, "unobserved": 6,
                                   "hook_active": True})
        self.assertEqual(res["agent_fanout"]["rate"], 0.25)

    def test_a_self_approved_gate_is_caught(self):
        res = self._score(
            [{"event": "loop_approve"}, {"event": "loop_approve_unattributed"}],
            [], {"expected": 0, "unobserved": 0, "hook_active": True})
        self.assertEqual(res["gate_discipline"]["rate"], 0.0)

    def test_step_order_is_scored_against_the_engines_own_machine(self):
        """A hand-written list of steps would drift from the loop the first
        time someone added one. The fixture deliberately spans BOTH sources
        the engine declares — an agent step from STEP_ROLE and a human gate
        from HUMAN_STEPS — so a partial copy scores a real step as unknown
        and fails here."""
        real = sorted(loop.STEP_ROLE)[:2] + sorted(loop.HUMAN_STEPS)[:2]
        self.assertGreaterEqual(len(real), 4)
        rows = [{"event": "loop_step", "step": s} for s in real]
        rows.append({"event": "loop_step", "step": "not-a-real-step"})
        res = self._score(rows, [], {"expected": 0, "unobserved": 0,
                                     "hook_active": True})
        self.assertEqual(res["skill_flow"]["unrecognised"],
                         ["not-a-real-step"])
        self.assertEqual(res["skill_flow"]["rate"], len(real) / (len(real) + 1))

    def test_parity_needs_two_hosts_before_it_says_anything(self):
        one = self._score([{"event": "loop_step", "step": "pm",
                            "host": "claude"}], [],
                          {"expected": 0, "unobserved": 0, "hook_active": True})
        self.assertIsNone(one["cross_host"]["rate"])
        two = self._score([], [
            {"event": "issued", "id": "a", "kind": "render_dashboard",
             "host": "claude"},
            {"event": "issued", "id": "b", "kind": "render_dashboard",
             "host": "codex"}],
            {"expected": 0, "unobserved": 0, "hook_active": True})
        self.assertEqual(two["cross_host"]["rate"], 1.0)

    def test_claims_and_facts_never_share_a_column(self):
        res = self._score([], [], {"expected": 0, "unobserved": 0,
                                   "hook_active": True})
        self.assertEqual(res["artifact_surfacing"]["source"], "claim")
        self.assertEqual(res["product_graph"]["source"], "claim")
        for area in ("agent_fanout", "skill_flow", "gate_discipline",
                     "cross_host"):
            self.assertEqual(res[area]["source"], "fact")


class TestTheCorpusProvesTheScorer(unittest.TestCase):
    """The ci_graph_accuracy pattern: sessions whose answer is known, so the
    scorer is provable without a host."""

    CORPUS = os.path.join(REPO, "evals")

    def test_every_profile_scores_exactly_what_it_declares(self):
        profiles = sorted(d for d in os.listdir(self.CORPUS)
                          if os.path.isdir(os.path.join(self.CORPUS, d)))
        self.assertGreaterEqual(len(profiles), 4)
        for name in profiles:
            d = os.path.join(self.CORPUS, name)
            with io.open(os.path.join(d, "expected.json"), encoding="utf-8") as f:
                expected = json.load(f)
            res = ci_evals.score(
                ci_evals._rows(os.path.join(d, "trace.jsonl")),
                ci_evals._rows(os.path.join(d, "obligations.jsonl")),
                json.load(io.open(os.path.join(d, "dispatch.json"),
                                  encoding="utf-8")))
            for area, want in expected["rates"].items():
                with self.subTest(profile=name, area=area):
                    self.assertEqual(res[area]["rate"], want)

    def test_the_corpus_contains_both_complaint_shapes(self):
        """The two failures this layer exists for must both be represented,
        or the scorer is only proven against the happy path."""
        for profile in ("skipped-render", "substitute-graph"):
            self.assertTrue(os.path.isdir(os.path.join(self.CORPUS, profile)))

    def test_the_harness_runs_and_gates_nothing(self):
        r = subprocess.run(
            [sys.executable, os.path.join(REPO, "scripts", "ci_evals.py"),
             "--corpus"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no evidence", r.stdout)


if __name__ == "__main__":
    unittest.main()
