"""v3 Phase 1 (R-0001): audit sweep cadence + router-regression auto-filing.

Covers:
  * the persistent completed-em-review counter and audit_due (every Nth,
    default 5, TASKPLANE_AUDIT_EVERY override with a min of 1, release flag);
  * router_audit converting EXACTLY the findings attributable to n/a-routed
    lenses into auto-filed router regressions (class regression, owner
    router, severity preserved, original finding nested);
  * the em action payload advertising audit mode when due;
  * atomic counter writes (no torn state);
  * gate integration: an n/a-lens finding in an audit-mode review is
    auto-filed into findings.json and BLOCKS the gate via the frozen
    v2.3.1 finding_blocks rule.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loop  # noqa: E402
import taskplane_lite as tp  # noqa: E402
import lens  # noqa: E402
import depgraph  # noqa: E402


def git_ws(tmp, tasks):
    ws = os.path.join(tmp, "ws")
    os.makedirs(os.path.join(ws, "plan"))
    os.makedirs(os.path.join(ws, "src", "todo"))
    open(os.path.join(ws, "src", "todo", "a.py"), "w").write("x=1\n")
    subprocess.run(["git", "init", "-q"], cwd=ws)
    subprocess.run(["git", "config", "user.email", "e@e"], cwd=ws)
    subprocess.run(["git", "config", "user.name", "t"], cwd=ws)
    subprocess.run(["git", "add", "-A"], cwd=ws)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=ws)
    json.dump({"tasks": tasks},
              open(os.path.join(ws, "plan", "tasks.json"), "w"))
    return ws


TASK = {"id": "t1", "scope": ["src/todo/**"], "tests": "true",
        "criteria": ["complete() marks done"]}


def submit_gate(ws, outcome="pass", task_id=None):
    submitted = loop.submit(ws, outcome, task_id=task_id)
    if "error" in submitted:
        return submitted
    return loop.gate(ws, outcome, task_id=task_id)


def pass_eval(ws):
    state = loop.load(ws)
    task = state["tasks"][state["current_task"]]
    act_ws = task.get("workspace") or ws
    routed = lens.route_git_diff(
        act_ws, base=state.get("baseline") or "HEAD",
        task_type=task.get("type"), breadth="routed")
    criteria = loop._criteria_for(ws, state, task)
    os.makedirs(os.path.join(act_ws, ".eval"), exist_ok=True)
    with open(os.path.join(act_ws, ".eval", "verdict.json"), "w") as f:
        json.dump({"task": task["id"], "verdict": "pass",
                   "criteria": [{"criterion": c, "status": "met",
                                 "evidence": "verified by test"}
                                for c in criteria],
                   "lenses": [{"lens": x["id"], "verdict": "pass",
                               "blockers": 0} for x in routed["lenses"]],
                   "failures": []}, f)
    return submit_gate(ws, "pass")


def pass_em(ws, coverage=None, findings_rows=None):
    if coverage is None:
        coverage = {x["id"]: "sweep" for x in lens.load_catalog()["lenses"]}
    os.makedirs(os.path.join(ws, ".em-review"), exist_ok=True)
    with open(os.path.join(ws, ".em-review", "report.md"), "w") as f:
        f.write("# Engineering review\n\nAll required evidence passed.\n")
    state = loop.load(ws)
    changed = [f for f in loop._diff_files(
        ws, state.get("baseline") or "HEAD")
        if not f.startswith(lens.LOOP_OWNED)]
    impact = depgraph.impact(ws, changed)
    with open(os.path.join(ws, ".em-review", "findings.json"), "w") as f:
        json.dump({"meta": {"lens_coverage": coverage, "impact": impact,
                            "tests": ["true"],
                            "gate": {"verdict": "recommend-pass"}},
                   "findings": findings_rows or []}, f)
    return submit_gate(ws, "pass")


def loop_to_em(tmp):
    """Drive a serial loop to the em step (plan gate skipped)."""
    ws = git_ws(tmp, [TASK])
    loop.init(ws, "g", spec_path="s", checkpoints=["em"])
    loop.next_action(ws); loop.gate(ws, "pass")           # plan → execute
    loop.next_action(ws); submit_gate(ws, "pass")         # execute → evaluate
    loop.next_action(ws); pass_eval(ws)                   # evaluate → em
    assert loop.load(ws)["step"] == "em"
    return ws


class AuditBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def ws(self):
        """A bare workspace with a state dir (no live loop needed for the
        cadence helpers)."""
        ws = os.path.join(self.tmp, "bare")
        os.makedirs(ws, exist_ok=True)
        return ws


class TestAuditCadence(AuditBase):
    def test_counter_starts_at_zero_and_increments(self):
        ws = self.ws()
        self.assertEqual(loop.audit_counter(ws), 0)
        self.assertEqual(loop.record_audit_review(ws), 1)
        self.assertEqual(loop.record_audit_review(ws), 2)
        self.assertEqual(loop.audit_counter(ws), 2)

    def test_audit_due_fires_at_every_fifth_by_default(self):
        ws = self.ws()
        # reviews 1..4 are not audits; the 5th is; the 6th is not; the 10th is
        for completed in range(4):
            self.assertFalse(loop.audit_due(ws), completed)
            loop.record_audit_review(ws)
        self.assertTrue(loop.audit_due(ws))          # upcoming review is #5
        loop.record_audit_review(ws)
        self.assertFalse(loop.audit_due(ws))         # #6
        for _ in range(4):
            loop.record_audit_review(ws)
        self.assertTrue(loop.audit_due(ws))          # #10

    def test_release_flag_forces_audit(self):
        ws = self.ws()
        self.assertFalse(loop.audit_due(ws, {}))
        self.assertTrue(loop.audit_due(ws, {"release_review": True}))
        self.assertTrue(loop.audit_due(
            ws, {"tasks": [{"id": "x", "release": True}]}))
        self.assertTrue(loop.audit_due(
            ws, {"tasks": [{"id": "x", "type": "release"}]}))

    def test_env_override_respected(self):
        ws = self.ws()
        with mock.patch.dict(os.environ, {"TASKPLANE_AUDIT_EVERY": "2"}):
            self.assertEqual(loop.audit_every(), 2)
            self.assertFalse(loop.audit_due(ws))     # upcoming #1
            loop.record_audit_review(ws)
            self.assertTrue(loop.audit_due(ws))      # upcoming #2

    def test_env_override_min_1_enforced(self):
        ws = self.ws()
        for raw in ("0", "-3", "1"):
            with mock.patch.dict(os.environ,
                                 {"TASKPLANE_AUDIT_EVERY": raw}):
                self.assertEqual(loop.audit_every(), 1, raw)
                self.assertTrue(loop.audit_due(ws), raw)   # every review

    def test_env_garbage_falls_back_to_default(self):
        with mock.patch.dict(os.environ,
                             {"TASKPLANE_AUDIT_EVERY": "often"}):
            self.assertEqual(loop.audit_every(), 5)

    def test_counter_write_is_atomic(self):
        """The counter goes through tp.atomic_write_json; a failed write
        leaves the previous valid state intact (no torn file)."""
        ws = self.ws()
        loop.record_audit_review(ws)
        calls = []
        real = tp.atomic_write_json

        def spy(path, data, **kw):
            calls.append(path)
            return real(path, data, **kw)

        with mock.patch.object(tp, "atomic_write_json", side_effect=spy):
            loop.record_audit_review(ws)
        self.assertTrue(any(os.path.basename(p) == "audit.json"
                            for p in calls))
        # crash mid-write: the on-disk counter is still the last good value
        with mock.patch.object(tp, "atomic_write_json",
                               side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                loop.record_audit_review(ws)
        self.assertEqual(loop.audit_counter(ws), 2)   # not torn, not bumped
        # and no temp litter in the state dir
        sd = loop.state_dir(ws)
        self.assertEqual([f for f in os.listdir(sd) if ".tmp." in f], [])

    def test_corrupt_counter_fails_toward_audit(self):
        ws = self.ws()
        path = loop._audit_path(ws)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").write("{not json")
        self.assertTrue(loop.audit_due(ws))       # more coverage, never less
        with self.assertRaises(tp.StateError):
            loop.audit_counter(ws)                # but the read fails closed


DECISION = {
    "i18n": {"verdict": "n/a", "score": 0,
             "negative_evidence": ["no user-facing strings detected"]},
    "security": {"verdict": "deep", "score": 4, "evidence": ["auth surface"]},
    "backend": {"verdict": "light", "score": 1, "evidence": ["weak signal"]},
    "architecture": {"verdict": "deep (forced)", "score": 0,
                     "evidence": ["governance floor"]},
    "mobile": {"verdict": "n/a", "score": 0,
               "negative_evidence": ["no mobile surface"]},
}


class TestRouterAudit(AuditBase):
    def test_converts_exactly_na_lens_findings(self):
        findings = [
            {"lens": "i18n", "severity": "med", "title": "hardcoded string",
             "file": "src/a.py", "line": 3},
            {"lens": "security", "severity": "high", "title": "real issue"},
            {"lens": "backend", "severity": "low", "title": "light note"},
            {"lens": "architecture", "severity": "med", "title": "floored"},
        ]
        regs = loop.router_audit(self.ws(), DECISION, findings)
        self.assertEqual(len(regs), 1)
        r = regs[0]
        self.assertEqual(r["class"], "regression")
        self.assertEqual(r["owner"], "router")
        self.assertEqual(r["domain"], "router+i18n")
        self.assertEqual(r["severity"], "med")        # severity preserved
        self.assertIn("router regression: n/a lens 'i18n' produced a "
                      "finding", r["title"])
        self.assertIn("detector missed a real signal", r["title"])
        self.assertEqual(r["finding"], findings[0])   # original nested

    def test_domain_field_and_string_verdicts_are_accepted(self):
        decision = {"mobile": "n/a", "qa": "deep"}
        findings = [{"domain": "mobile", "severity": "low", "title": "x"},
                    {"domain": "qa", "severity": "low", "title": "y"}]
        regs = loop.router_audit(self.ws(), decision, findings)
        self.assertEqual([r["domain"] for r in regs], ["router+mobile"])

    def test_unrouted_or_lensless_findings_are_ignored(self):
        findings = [{"severity": "high", "title": "no lens field"},
                    {"lens": "does-not-exist", "severity": "high",
                     "title": "unknown lens"},
                    "not-a-dict"]
        self.assertEqual(loop.router_audit(self.ws(), DECISION, findings), [])

    def test_auto_filed_regression_blocks_via_frozen_finding_blocks(self):
        regs = loop.router_audit(self.ws(), DECISION, [
            {"lens": "i18n", "severity": "low", "title": "missed"}])
        # class regression ALWAYS blocks under the frozen v2.3.1 rule —
        # even at low severity and outside the diff.
        self.assertTrue(loop.finding_blocks(regs[0], changed_files=[]))


def em_review_ws(na=("i18n",), findings_rows=(), audit=True):
    """A bare em-review workspace with a v2 (contract:findings-v2) meta:
    per-lens routing verdicts in meta.lens_coverage."""
    ws = tempfile.mkdtemp()
    d = os.path.join(ws, ".em-review")
    os.makedirs(d)
    coverage = {}
    for e in lens.load_catalog()["lenses"]:
        lid = e["id"]
        if lid in na:
            coverage[lid] = {"verdict": "n/a", "score": 0,
                             "negative_evidence": ["no signal detected"]}
        else:
            coverage[lid] = {"verdict": "deep", "score": 3,
                             "evidence": ["signal"]}
    meta = {"lens_coverage": coverage, "impact": {"touched": []},
            "tests": "pytest -q: pass", "gate": {"verdict": "recommend-pass"}}
    if audit:
        meta["audit"] = True
    with open(os.path.join(d, "findings.json"), "w") as f:
        json.dump({"meta": meta, "findings": list(findings_rows)}, f)
    with open(os.path.join(d, "report.md"), "w") as f:
        f.write("# review\nok\n")
    return ws


class TestGateIntegration(AuditBase):
    """The em gate math picks up auto-filed router regressions with NO
    change to finding_blocks (integration-style, fixture findings.json)."""

    def _rows(self, ws):
        with open(os.path.join(ws, ".em-review", "findings.json")) as f:
            return json.load(f)["findings"]

    def test_na_lens_finding_blocks_the_gate(self):
        ws = em_review_ws(findings_rows=[
            {"lens": "i18n", "severity": "med", "class": "observation",
             "title": "hardcoded locale string", "file": "src/a.py"}])
        errs = loop._engineering_review_errors(ws, None)
        self.assertTrue(any("router regression" in e for e in errs), errs)
        rows = self._rows(ws)                    # APPENDED to the findings set
        filed = [r for r in rows if r.get("owner") == "router"]
        self.assertEqual(len(filed), 1)
        self.assertEqual(filed[0]["class"], "regression")
        self.assertEqual(filed[0]["severity"], "med")
        self.assertEqual(filed[0]["domain"], "router+i18n")
        self.assertTrue(loop.finding_blocks(filed[0], changed_files=[]))

    def test_auto_filing_is_idempotent_across_gate_reruns(self):
        ws = em_review_ws(findings_rows=[
            {"lens": "i18n", "severity": "low", "title": "missed"}])
        loop._engineering_review_errors(ws, None)
        errs = loop._engineering_review_errors(ws, None)   # re-run the gate
        filed = [r for r in self._rows(ws) if r.get("owner") == "router"]
        self.assertEqual(len(filed), 1)                    # not duplicated
        self.assertTrue(any("router regression" in e for e in errs))

    def test_resolved_router_regression_stops_blocking(self):
        ws = em_review_ws(findings_rows=[
            {"lens": "i18n", "severity": "low", "title": "missed"}])
        loop._engineering_review_errors(ws, None)
        p = os.path.join(ws, ".em-review", "findings.json")
        doc = json.load(open(p))
        for r in doc["findings"]:
            if r.get("owner") == "router":
                r["status"] = "accepted"
        json.dump(doc, open(p, "w"))
        errs = loop._engineering_review_errors(ws, None)
        self.assertFalse(any("router regression" in e for e in errs), errs)

    def test_deep_lens_findings_are_not_filed(self):
        ws = em_review_ws(findings_rows=[
            {"lens": "security", "severity": "med", "class": "observation",
             "title": "note on a deep lens"}])
        errs = loop._engineering_review_errors(ws, None)
        self.assertFalse(any("router regression" in e for e in errs), errs)
        self.assertEqual(
            [r for r in self._rows(ws) if r.get("owner") == "router"], [])

    def test_legacy_meta_without_routing_decision_is_untouched(self):
        # legacy string coverage: no diff is computable, nothing is filed,
        # and the coverage validation still accepts the legacy shape.
        ws = tempfile.mkdtemp()
        d = os.path.join(ws, ".em-review")
        os.makedirs(d)
        coverage = {e["id"]: "sweep" for e in lens.load_catalog()["lenses"]}
        with open(os.path.join(d, "findings.json"), "w") as f:
            json.dump({"meta": {"lens_coverage": coverage,
                                "impact": {"touched": []},
                                "tests": "ok",
                                "gate": {"verdict": "recommend-pass"}},
                       "findings": [{"lens": "i18n", "severity": "low",
                                     "title": "note"}]}, f)
        with open(os.path.join(d, "report.md"), "w") as f:
            f.write("ok\n")
        errs = loop._engineering_review_errors(ws, None)
        self.assertEqual(errs, [])

    def test_v2_coverage_shape_passes_the_tier_validation(self):
        ws = em_review_ws(findings_rows=[])
        self.assertEqual(loop._engineering_review_errors(ws, None), [])


class TestEmPayloadAndCounterWiring(AuditBase):
    def test_em_payload_advertises_audit_mode_when_due(self):
        with mock.patch.dict(os.environ, {"TASKPLANE_AUDIT_EVERY": "1"}):
            ws = loop_to_em(self.tmp)
            act = loop.next_action(ws)
            self.assertEqual(act["step"], "em")
            audit = act.get("audit")
            self.assertIsInstance(audit, dict)
            self.assertTrue(audit["due"])
            self.assertIn("every-1", audit["reason"])
            # the POINT of audit mode: the routing decision is recorded so
            # the findings-vs-routing diff is computable at the gate.
            decision = audit.get("routing_decision")
            self.assertIsInstance(decision, dict)
            expected = {e["id"] for e in lens.load_catalog()["lenses"]}
            self.assertEqual(set(decision), expected)
            for v in decision.values():
                self.assertIn("verdict", v)

    def test_em_payload_reports_not_due_on_the_default_cadence(self):
        ws = loop_to_em(self.tmp)
        act = loop.next_action(ws)
        audit = act.get("audit")
        self.assertIsInstance(audit, dict)
        self.assertFalse(audit["due"])
        self.assertEqual(audit["every"], 5)
        self.assertEqual(audit["reviews_completed"], 0)

    def test_completed_em_review_increments_the_counter(self):
        ws = loop_to_em(self.tmp)
        self.assertEqual(loop.audit_counter(ws), 0)
        loop.next_action(ws)
        out = pass_em(ws)
        self.assertNotIn("error", out)
        self.assertEqual(loop.load(ws)["step"], "signoff")
        self.assertEqual(loop.audit_counter(ws), 1)

    def test_refused_em_gate_does_not_count_a_review(self):
        ws = loop_to_em(self.tmp)
        loop.next_action(ws)
        # missing evidence: the review is incomplete, not completed
        out = submit_gate(ws, "pass")
        self.assertIn("error", out)
        self.assertEqual(loop.audit_counter(ws), 0)


if __name__ == "__main__":
    unittest.main()
