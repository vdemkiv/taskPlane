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
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loop  # noqa: E402
import audit  # noqa: E402
import taskplane_lite as tp  # noqa: E402
import lens  # noqa: E402
import depgraph  # noqa: E402
import review  # noqa: E402
import review_evidence  # noqa: E402
import producer_observation  # noqa: E402
from taskplane.tests.review_kernel_support import complete_review  # noqa: E402


def git_ws(tmp, tasks):
    ws = os.path.join(tmp, "ws")
    os.makedirs(os.path.join(ws, "plan"))
    os.makedirs(os.path.join(ws, "src", "todo"))
    open(os.path.join(ws, "src", "todo", "a.py"), "w", encoding="utf-8").write("x=1\n")
    subprocess.run(["git", "init", "-q"], cwd=ws)
    subprocess.run(["git", "config", "user.email", "e@e"], cwd=ws)
    subprocess.run(["git", "config", "user.name", "t"], cwd=ws)
    subprocess.run(["git", "add", "-A"], cwd=ws)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=ws)
    json.dump({"tasks": tasks},
              open(os.path.join(ws, "plan", "tasks.json"), "w", encoding="utf-8"))
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
    kernel = review._load_state(act_ws)
    store = review_evidence.ArtifactStore(act_ws)
    for index, slot in enumerate(kernel["slots"]):
        lease = store.read(slot["lease"])
        brief = store.read(slot["brief"])
        row = {**lease, "schema": "taskplane.lens-slot-output/v2",
               "authored_by": "lens-slot", "findings": [],
               "lens_results": [
                   {"lens": lens_id, "verdict": "pass", "blockers": 0,
                    "checked_evidence": [{
                        "file": "src/todo/a.py", "line": 1,
                        "claim": ("audit cadence fixture inspected the "
                                  "changed task source"),
                    }]}
                   for lens_id in lease["lens_ids"]]}
        content = json.dumps(row, sort_keys=True, separators=(",", ":"))
        event = {"session_id": "audit-eval-session",
                 "agent_id": f"audit-eval-child-{index}",
                 "tool_name": "Write",
                 "tool_input": {"file_path": slot["result_path"],
                                "content": content}}
        producer = brief["producer_contract"]
        contract = {"task": producer["task"], "read_only": True,
                    "write_allow": producer["write_allow"]}
        review.register_slot_producer(
            act_ws, event=event, contract=contract,
            task_slot=producer["task_slot"])
        review.record_slot_write_observation(
            act_ws, event=event, contract=contract,
            task_slot=producer["task_slot"])
        path = os.path.join(act_ws, slot["result_path"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(content)
    with open(os.path.join(act_ws, ".eval", "verdict.json"), "w", encoding="utf-8") as f:
        json.dump({"schema": "taskplane.evaluator-output/v2",
                   "task": task["id"],
                   "requirement": task.get("req") or
                                  state.get("requirement_id") or "",
                   "verdict": "pass",
                   "evaluation": {"status": "complete",
                                  "reason_code": "none", "detail": ""},
                   "criteria": [{"criterion": c, "status": "met",
                                 "evidence": "verified by test"}
                                for c in criteria],
                   "graph": {"dispositions": [],
                             "requirements_checked": [],
                             "contracts_checked": []},
                   "failures": []}, f)
    material = loop.producer_output_identity(
        act_ws, state, task, "evaluate",
        active_contract=tp.load_active(act_ws) or {})
    event = {
        "hook_event_name": "SubagentStop",
        "session_id": "audit-eval-session",
        "turn_id": "audit-eval-turn",
        "agent_id": "audit-evaluator",
        "agent_type": material["producer_dispatch"]["task_name"],
        "task_name": material["producer_dispatch"]["task_name"],
    }
    claim = hashlib.sha256(tp.hook_event_identity(
        act_ws, "subagent-stop", event).encode("utf-8")).hexdigest()
    producer_observation.record_codex_subagent_stop(
        event=event, hook_claim_id=claim, **material)
    return submit_gate(ws, "pass")


def pass_em(ws, coverage=None, findings_rows=None):
    if coverage is None:
        coverage = {x["id"]: "sweep" for x in lens.load_catalog()["lenses"]}
    state = loop.load(ws)
    changed = [f for f in loop._diff_files(
        ws, state.get("baseline") or "HEAD")
        if not f.startswith(lens.LOOP_OWNED)]
    impact = depgraph.impact(ws, changed)
    complete_review(
        ws, coverage=coverage, impact=impact, tests=["true"],
        findings=findings_rows or [],
        report="# Engineering review\n\nAll required evidence passed.\n")
    task = loop._current_task(state)
    material = loop.producer_output_identity(
        ws, state, task, "em", active_contract=tp.load_active(ws) or {})
    event = {
        "hook_event_name": "SubagentStop",
        "session_id": "audit-em-session", "turn_id": "audit-em-turn",
        "agent_id": "audit-engineering",
        "agent_type": material["producer_dispatch"]["task_name"],
        "task_name": material["producer_dispatch"]["task_name"],
    }
    claim = hashlib.sha256(tp.hook_event_identity(
        ws, "subagent-stop", event).encode("utf-8")).hexdigest()
    producer_observation.record_codex_subagent_stop(
        event=event, hook_claim_id=claim, **material)
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
        open(path, "w", encoding="utf-8").write("{not json")
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
    with open(os.path.join(d, "findings.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "findings": list(findings_rows)}, f)
    with open(os.path.join(d, "report.md"), "w", encoding="utf-8") as f:
        f.write("# review\nok\n")
    return ws


class TestGateIntegration(AuditBase):
    """The em gate math picks up auto-filed router regressions with NO
    change to finding_blocks (integration-style, fixture findings.json)."""

    def _rows(self, ws):
        with open(os.path.join(ws, ".em-review", "findings.json"), encoding="utf-8") as f:
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
        doc = json.load(open(p, encoding="utf-8"))
        for r in doc["findings"]:
            if r.get("owner") == "router":
                r["status"] = "accepted"
        json.dump(doc, open(p, "w", encoding="utf-8"))
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
        with open(os.path.join(d, "findings.json"), "w", encoding="utf-8") as f:
            json.dump({"meta": {"lens_coverage": coverage,
                                "impact": {"touched": []},
                                "tests": "ok",
                                "gate": {"verdict": "recommend-pass"}},
                       "findings": [{"lens": "i18n", "severity": "low",
                                     "title": "note"}]}, f)
        with open(os.path.join(d, "report.md"), "w", encoding="utf-8") as f:
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



class TestUnattributedFindingWarnRows(AuditBase):
    """A5 (R-0007): findings with missing/unknown lens attribution were
    silently skipped by the router audit — an attribution-omission evasion.
    They now surface as non-blocking WARN rows in the approved Design
    Contract's shape — severity PRESERVED, class observation, owner router,
    warn true, original finding nested — appended into findings.json at the
    gate, traced router_audit_unattributed. router_audit's own return for
    such inputs is byte-frozen by the differential corpus and stays
    unchanged (see test_unrouted_or_lensless_findings_are_ignored above)."""

    def _rows(self, ws):
        with open(os.path.join(ws, ".em-review", "findings.json"), encoding="utf-8") as f:
            return json.load(f)["findings"]

    def _warn_rows(self, ws):
        return [r for r in self._rows(ws)
                if isinstance(r, dict) and r.get("warn") is True]

    def _trace(self, ws):
        path = os.path.join(ws, ".taskplane", "trace.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f]

    def test_lensless_finding_surfaces_as_warn_row_and_does_not_block(self):
        ws = em_review_ws(findings_rows=[
            {"severity": "med", "title": "no lens field",
             "file": "src/a.py", "line": 3}])
        errs = loop._engineering_review_errors(ws, None)
        self.assertEqual(errs, [])                     # non-blocking
        warns = self._warn_rows(ws)
        self.assertEqual(len(warns), 1)
        w = warns[0]
        self.assertEqual(w["class"], "observation")
        self.assertEqual(w["owner"], "router")           # contract shape
        self.assertEqual(w["severity"], "med")           # PRESERVED
        self.assertIs(w["warn"], True)                   # warn flag present
        self.assertEqual(w["domain"], "router+unattributed")
        self.assertTrue(w["title"].startswith("unattributed finding"))
        self.assertEqual(w["finding"]["title"], "no lens field")
        self.assertFalse(loop.finding_blocks(w, changed_files=[]))
        events = [e for e in self._trace(ws)
                  if e.get("event") == "router_audit_unattributed"]
        self.assertEqual(len(events), 1)               # traced, never silent
        self.assertEqual(events[0].get("count"), 1)

    def test_unknown_lens_variant_names_the_lens(self):
        ws = em_review_ws(findings_rows=[
            {"lens": "does-not-exist", "severity": "low", "title": "x"}])
        loop._engineering_review_errors(ws, None)
        warns = self._warn_rows(ws)
        self.assertEqual([w["domain"] for w in warns],
                         ["router+unknown:does-not-exist"])
        self.assertIn("does-not-exist", warns[0]["title"])

    def test_warn_filing_is_idempotent_across_gate_reruns(self):
        ws = em_review_ws(findings_rows=[
            {"severity": "low", "title": "no lens field"}])
        loop._engineering_review_errors(ws, None)
        errs = loop._engineering_review_errors(ws, None)     # re-run
        self.assertEqual(errs, [])
        self.assertEqual(len(self._warn_rows(ws)), 1)        # not duplicated
        events = [e for e in self._trace(ws)
                  if e.get("event") == "router_audit_unattributed"]
        self.assertEqual(len(events), 1)                     # traced once

    def test_attributed_findings_produce_no_warn_rows(self):
        # the working path: every finding attributed to a known lens →
        # zero warn rows, zero trace events, findings.json untouched
        ws = em_review_ws(findings_rows=[
            {"lens": "security", "severity": "low", "class": "observation",
             "title": "attributed note"}])
        p = os.path.join(ws, ".em-review", "findings.json")
        before = open(p, "rb").read()
        errs = loop._engineering_review_errors(ws, None)
        self.assertEqual(errs, [])
        self.assertEqual(self._warn_rows(ws), [])
        self.assertEqual(open(p, "rb").read(), before)       # bytes stable
        self.assertEqual([e for e in self._trace(ws)
                          if e.get("event") == "router_audit_unattributed"],
                         [])

    def test_na_regression_and_warn_row_coexist(self):
        # a mixed review: the n/a-lens finding still auto-files a BLOCKING
        # router regression (frozen behavior), the lensless one only warns
        ws = em_review_ws(findings_rows=[
            {"lens": "i18n", "severity": "med", "title": "missed"},
            {"severity": "med", "title": "no lens field"}])
        errs = loop._engineering_review_errors(ws, None)
        self.assertTrue(any("router regression" in e for e in errs))
        rows = self._rows(ws)
        regressions = [r for r in rows
                       if r.get("owner") == "router" and not r.get("warn")]
        self.assertEqual(len(regressions), 1)
        self.assertEqual(len(self._warn_rows(ws)), 1)

    def test_high_severity_warn_row_is_preserved_and_never_blocks(self):
        # the contract's severity-preservation is safe: finding_blocks
        # checks CLASS before severity, and 'observation' never blocks
        # under the frozen v2.3.1 rule — a preserved 'high' cannot turn
        # the warn row ITSELF into a sign-off blocker. (The ORIGINAL high
        # row still blocks via the em gate's pre-existing raw severity
        # sweep — surfacing is not laundering.)
        ws = em_review_ws(findings_rows=[
            {"severity": "high", "class": "observation",
             "title": "no lens field", "file": "src/a.py", "line": 3}])
        errs = loop._engineering_review_errors(ws, None)
        self.assertEqual(len(errs), 1)                   # the original only
        self.assertNotIn("unattributed", errs[0])        # not the warn row
        w = self._warn_rows(ws)[0]
        self.assertEqual(w["severity"], "high")          # preserved UP too
        self.assertIs(w["warn"], True)
        self.assertEqual(w["owner"], "router")
        self.assertFalse(loop.finding_blocks(w))                 # no diff ctx
        self.assertFalse(loop.finding_blocks(w, ["src/a.py"]))   # in-diff too

    def test_underlying_high_finding_still_gates_on_its_own_class(self):
        # surfacing is NOT laundering: an unclassified high original still
        # blocks the gate itself — the warn row adds no SECOND blocker
        ws = em_review_ws(findings_rows=[
            {"severity": "high", "title": "no lens field",
             "file": "src/a.py", "line": 3}])
        errs = loop._engineering_review_errors(ws, None)
        self.assertEqual(len(errs), 1)                   # the original only
        self.assertIn("high finding", errs[0])
        self.assertEqual(self._warn_rows(ws)[0]["severity"], "high")
        # re-run: the filed high warn row must not become a SECOND blocker
        self.assertEqual(len(loop._engineering_review_errors(ws, None)), 1)

    def test_hand_authored_warn_flag_cannot_evade_the_high_backstop(self):
        # fix-cycle-2 regression (the evaluator's evasion repro): a bare
        # `warn: true` bolted onto a genuine finding must NOT exempt it
        # from the v2.3.0 raw-high sweep — only the FULL machinery shape
        # (warn + owner router + class observation + router+ domain +
        # nested finding dict) is exempt. Every spoof still blocks:
        spoofs = [
            {"lens": "security", "warn": True, "severity": "high",
             "title": "spoofed high"},
            {"lens": "security", "warn": True, "severity": "blocker",
             "title": "spoofed blocker"},
            {"lens": "security", "warn": True, "severity": "high",
             "class": "regression", "title": "spoofed regression"},
            # partial machinery shape (no router+ domain, no nested
            # finding) is still a spoof
            {"lens": "security", "warn": True, "owner": "router",
             "class": "observation", "severity": "high",
             "title": "partial-shape spoof"},
        ]
        for row in spoofs:
            ws = em_review_ws(findings_rows=[dict(row)])
            errs = loop._engineering_review_errors(ws, None)
            self.assertTrue(
                any("unresolved" in e and row["title"] in e for e in errs),
                (row["title"], errs))
            self.assertFalse(loop._is_machinery_warn_row(row))
        # and the machinery's own filed rows ARE the shape it exempts
        ws = em_review_ws(findings_rows=[
            {"severity": "high", "title": "no lens field"}])
        loop._engineering_review_errors(ws, None)
        self.assertTrue(loop._is_machinery_warn_row(self._warn_rows(ws)[0]))

    def test_warn_rows_are_not_reaudited_on_rerun(self):
        # the appended warn row itself (owner router, warn true, domain
        # router+unattributed) must never be diffed again as an
        # unknown-lens finding on later gate runs
        ws = em_review_ws(findings_rows=[
            {"severity": "low", "title": "no lens field"}])
        for _ in range(3):
            loop._engineering_review_errors(ws, None)
        self.assertEqual(len(self._warn_rows(ws)), 1)


class TestMachineryWarnCostumeCannotEvadeTheBackstop(AuditBase):
    """Phase 3 EM review, deep3 finding #1 (HIGH regression): the A5
    exemption keyed on SHAPE alone, and all five shape fields live in
    worker-authored .em-review/findings.json — so a real blocker wearing
    the full costume (warn + owner router + class observation + router+
    domain + nested finding dict) walked straight past the v2.3.0
    unresolved-high backstop the em gate depends on.

    The exemption is now RE-DERIVED at gate time (audit._machinery_warn_rows
    recomputes _unattributed_rows over the findings actually on disk) and the
    row must MATCH one of the derived rows field for field
    (_machinery_warn_matches), not merely share its
    `_router_regression_key`. A costume that corresponds to no genuinely
    unattributed finding blocks — and so does one that copies a GENUINE
    row's key while carrying a severity of its own (light3 finding #4)."""

    def _rows(self, ws):
        with open(os.path.join(ws, ".em-review", "findings.json"), encoding="utf-8") as f:
            return json.load(f)["findings"]

    def _warn_rows(self, ws):
        return [r for r in self._rows(ws)
                if isinstance(r, dict) and r.get("warn") is True]

    @staticmethod
    def _costume(**over):
        row = {"severity": "blocker", "class": "observation",
               "owner": "router", "warn": True,
               "domain": "router+unattributed",
               "title": "auth bypass in login",
               "finding": {"severity": "blocker",
                           "title": "auth bypass in login",
                           "file": "src/auth.py", "line": 10},
               "status": "open"}
        row.update(over)
        return row

    def test_full_costume_rows_still_block_the_gate(self):
        # the reviewer's exact repro payload, plus the severity/class
        # variations of it: every one is a hand-authored row that no
        # unattributed finding on disk justifies, so every one must block
        spoofs = [
            ("blocker+costume", self._costume()),
            ("high+costume", self._costume(
                severity="high", title="high wearing the costume")),
            ("regression-class+costume", self._costume(
                **{"class": "regression",
                   "title": "regression wearing the costume"})),
            # partial costume: machinery-ish but missing the router+ domain
            ("partial costume", self._costume(
                domain="unattributed", title="partial costume")),
        ]
        for label, row in spoofs:
            with self.subTest(label):
                ws = em_review_ws(findings_rows=[dict(row)])
                errs = loop._engineering_review_errors(ws, None)
                self.assertTrue(
                    any("unresolved" in e and row["title"] in e
                        for e in errs), (label, errs))

    def test_the_machinerys_own_warn_rows_are_still_exempt(self):
        # the legitimate population: an unattributed high original files a
        # high-severity warn row, and re-running the gate must yield the
        # ONE error for the original — never a second one for the warn row
        ws = em_review_ws(findings_rows=[
            {"severity": "high", "title": "no lens field",
             "file": "src/a.py", "line": 3}])
        for _ in range(3):
            errs = loop._engineering_review_errors(ws, None)
            self.assertEqual(len(errs), 1, errs)
            self.assertIn("no lens field", errs[0])
        warn = self._warn_rows(ws)
        self.assertEqual(len(warn), 1)
        self.assertEqual(warn[0]["severity"], "high")
        self.assertTrue(loop._is_machinery_warn_row(warn[0]))

    def test_a_costume_riding_a_genuine_key_is_no_longer_exempt(self):
        # light3 finding #4 (LOW regression): the forged row keys to a REAL
        # unattributed finding, so key-membership alone exempted it while it
        # carried a severity and title of its own. Both rows must block now:
        # the original on its own severity, the costume because it is NOT
        # what the machinery would have filed for that finding.
        original = {"severity": "blocker", "title": "auth bypass in login",
                    "file": "src/auth.py", "line": 10}
        ws = em_review_ws(findings_rows=[dict(original), self._costume()])
        errs = [e for e in loop._engineering_review_errors(ws, None)
                if "unresolved" in e]
        self.assertIn("engineering review has an unresolved blocker finding: "
                      "auth bypass in login", errs)
        self.assertEqual(len(errs), 2, errs)   # the costume blocks too

    def test_a_costume_riding_a_genuine_key_with_a_lesser_original(self):
        # the sharpest form: the genuine unattributed finding is a LOW, so
        # nothing blocks except the forged blocker riding its key.
        original = {"severity": "low", "title": "cosmetic",
                    "file": "src/a.py", "line": 3}
        forged = self._costume(
            finding={"title": "cosmetic", "file": "src/a.py", "line": 3})
        ws = em_review_ws(findings_rows=[dict(original), forged])
        errs = [e for e in loop._engineering_review_errors(ws, None)
                if "unresolved" in e]
        self.assertEqual(
            errs, ["engineering review has an unresolved blocker finding: "
                   "auth bypass in login"], errs)

    def test_the_machinerys_own_row_stays_exempt_across_triage_edits(self):
        # the legitimate population must not become unclearable: the filed
        # warn row carries a SNAPSHOT of the original, so resolving the
        # original must not turn its machinery row into a blocker.
        ws = em_review_ws(findings_rows=[
            {"severity": "high", "title": "no lens field",
             "file": "src/a.py", "line": 3}])
        self.assertEqual(len(loop._engineering_review_errors(ws, None)), 1)
        path = os.path.join(ws, ".em-review", "findings.json")
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        for r in doc["findings"]:
            if not r.get("warn"):
                r["status"] = "resolved"       # triage the original
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        self.assertEqual(loop._engineering_review_errors(ws, None), [])

    def test_exemption_set_is_derived_not_read_from_the_file(self):
        # the unit-level statement of the fix: shape alone is not enough
        row = self._costume()
        self.assertTrue(loop._is_machinery_warn_row(row))   # shape: yes
        self.assertFalse(audit._machinery_warn_exempt(row, []))
        self.assertEqual(audit._machinery_warn_rows({}, [row]), [])
        # a row whose KEY is re-derived from a real unattributed finding is
        # still not exempt — the whole row has to match
        meta = {"routing_decision": {"security": {"verdict": "deep"}}}
        rows = [{"severity": "high", "title": "no lens field"}]
        legit = audit._machinery_warn_rows(meta, rows)
        self.assertEqual(len(legit), 1)
        self.assertFalse(audit._machinery_warn_exempt(row, legit))
        riding = dict(legit[0])
        riding["severity"] = "blocker"          # only the severity differs
        self.assertFalse(audit._machinery_warn_exempt(riding, legit))
        self.assertTrue(audit._machinery_warn_exempt(dict(legit[0]), legit))


if __name__ == "__main__":
    unittest.main()
