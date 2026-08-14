"""T3: the graph/evidence kernel is the normal review path."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lens  # noqa: E402
import review  # noqa: E402
import review_evidence  # noqa: E402
import taskplane_lite as tp  # noqa: E402


class TestSelectiveReviewKernel(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="tp-review-kernel-")
        os.makedirs(os.path.join(self.ws, "src"))
        with open(os.path.join(self.ws, "src", "service.py"), "w",
                  encoding="utf-8") as stream:
            stream.write("def changed():\n    return 2\n")
        self.target = {"fingerprint": "target-1", "head": "abc123"}
        self.graph = {"meta": {"scanned_head": "abc123",
                                "content_fingerprint": "graph-1"},
                      "modules": {"src": {"files": ["src/service.py"]}},
                      "edges": []}
        self.impact = {"touched": ["src"], "impacted": {},
                       "total_impacted": 1, "unknown": []}
        self.diff = {"files": ["src/service.py"],
                     "changed_symbols": ["changed"],
                     "patch_artifact": {"fingerprint": "diff-1"}}

    def _start(self, **kw):
        args = {"target": self.target, "graph": self.graph,
                "impact": self.impact, "diff": self.diff,
                "runnability": {"summary": "available"},
                "requirement": {"id": "R-1", "text": "safe change"},
                "acceptance": ["works"], "contracts": ["contract:api"]}
        args.update(kw)
        return review.start_review(self.ws, **args)

    def _write_slot_results(self, *, findings=None, verdict="pass",
                            run_id=None):
        state = review._load_state(self.ws, run_id)
        store = review_evidence.ArtifactStore(self.ws)
        for index, slot in enumerate(state["slots"]):
            lease = store.read(slot["lease"])
            brief = store.read(slot["brief"])
            row = {
                **lease,
                "schema": "taskplane.lens-slot-output/v2",
                "authored_by": "lens-slot",
                "lens_results": [
                    {"lens": lens_id, "verdict": verdict,
                     "blockers": 0 if verdict == "pass" else 1}
                    for lens_id in lease["lens_ids"]
                ],
                "findings": list(findings or []),
            }
            content = json.dumps(row, sort_keys=True, separators=(",", ":"))
            event = {"session_id": f"lens-session-{state['run_id']}",
                     "agent_id": f"lens-child-{state['run_id'][:8]}-{index}",
                     "tool_name": "Write",
                     "tool_input": {"file_path": slot["result_path"],
                                    "content": content}}
            contract = {
                "task": brief["producer_contract"]["task"],
                "task_id": "lens-contract-1", "read_only": True,
                "write_allow": [slot["result_path"]],
            }
            review.register_slot_producer(
                self.ws, event=event, contract=contract,
                task_slot=brief["producer_contract"]["task_slot"])
            review.record_slot_write_observation(
                self.ws, event=event, contract=contract,
                task_slot=brief["producer_contract"]["task_slot"])
            path = os.path.join(self.ws, slot["result_path"])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(content)

    def test_start_maps_all_lenses_but_dispatches_only_deep_plus_one_sweep(self):
        def route_with_one_light_lens():
            routing = lens.route(
                self.diff["files"], breadth="routed", stage="review",
                workspace=self.ws, requirement_text="safe change")
            for row in routing["lenses"]:
                if row.get("tier") == "light" and row["id"] != "architecture":
                    row["tier"] = row["verdict"] = "n/a"
                    row["negative_evidence"] = ["single-light test fixture"]
            return routing

        out = self._start(router=route_with_one_light_lens)
        self.assertEqual(out["status"], "ready")
        self.assertEqual(sum(out["routing_counts"].values()),
                         len(lens.load_catalog()["lenses"]))
        sweeps = [row for row in out["slots"]
                  if row["slot_id"] == "light-sweep"]
        self.assertEqual(len(sweeps), 1)
        state = review._load_state(self.ws, out["run_id"])
        sweep = next(row for row in state["slots"]
                     if row["slot_id"] == "light-sweep")
        brief = review_evidence.ArtifactStore(self.ws).read(sweep["brief"])
        dispatch = tp.dispatch_fields(
            "lens", "tp-lens", "sweep", "cheap")
        self.assertEqual(brief["role"], {
            "agent": "tp-lens",
            **{key: dispatch[key] for key in (
                "model_tier", "reasoning_effort", "task_name",
                "role_marker")}
        })
        self.assertNotIn("breadth", json.dumps(out).lower())
        self.assertLessEqual(len(json.dumps(out).encode()), 16 * 1024)

    def test_changed_content_is_extracted_once_from_the_canonical_patch(self):
        patch = ("diff --git a/src/service.py b/src/service.py\n"
                 "--- a/src/service.py\n+++ b/src/service.py\n"
                 "@@ -1,2 +1,2 @@\n-password = old\n+value = 2\n"
                 " unchanged context\n")
        self.assertEqual(review.changed_content_from_patch(patch), {
            "src/service.py": "password = old\nvalue = 2\n"})

    def test_impact_uncertainty_dispatches_zero(self):
        graph = {**self.graph,
                 "meta": {**self.graph["meta"], "truncated": True}}
        out = self._start(graph=graph)
        self.assertEqual(out["status"], "impact_incomplete")
        self.assertEqual(out["slots"], [])
        self.assertEqual(out["agents"], [])

    def test_start_collects_informational_runnability_when_omitted(self):
        out = review.start_review(
            self.ws, target=self.target, graph=self.graph,
            impact=self.impact, diff=self.diff,
            requirement={"id": "R-1", "text": "safe change"})
        self.assertEqual(out["status"], "ready")
        state = review._load_state(self.ws)
        import review_evidence
        envelope = review_evidence.ArtifactStore(self.ws).read(
            state["envelope"])
        self.assertIn("summary", envelope["runnability"])

    def test_collect_accepts_each_leased_slot_once_and_commits_one_revision(self):
        started = self._start()
        self._write_slot_results()
        out = review.collect_review(self.ws, publish=False)
        self.assertEqual(out["status"], "complete")
        self.assertEqual(out["canonical_revision"], 1)
        self.assertEqual(out["context_fingerprint"],
                         started["context_fingerprint"])
        self.assertEqual(out["counters"]["top_level_cli_count"], 2)

    def test_floor_marker_survives_stage_narrowing_when_already_satisfied(self):
        # Pin the input explicitly. Routing the taskPlane checkout vs HEAD made
        # this test pass only while the implementation was uncommitted, then
        # turn into an empty-diff n/a at the orchestrator gate.
        with open(os.path.join(self.ws, "src", "service.py"), "w",
                  encoding="utf-8") as stream:
            stream.write("event bus architecture coupling data flow\n")
        routing = lens.route(
            ["src/service.py"], stage="build", breadth="routed",
            workspace=self.ws)
        arch = next(row for row in routing["lenses"]
                    if row["id"] == "architecture")
        self.assertEqual(arch["tier"], "deep")
        self.assertIn("floor", arch)

    def test_body_only_change_cannot_turn_zero_symbols_into_complete_coverage(self):
        self.assertEqual(review.changed_symbols_from_patch(
            "@@ -2 +2 @@\n-    return 1\n+    return 2\n"), [])
        out = self._start(
            impact={**self.impact, "module_confidence": "low"},
            diff={"files": ["src/service.py"], "changed_symbols": []},
            caller_expander=lambda **_: {
                "complete": True, "callers": [], "contracts": [],
                "unresolved": []})
        self.assertEqual(out["status"], "impact_incomplete")
        quality = review_evidence.ArtifactStore(self.ws).read(
            review._load_state(self.ws)["quality"])
        self.assertEqual(
            quality["changed_symbol_caller_coverage"]["status"], "incomplete")
        self.assertNotEqual(
            quality["changed_symbol_caller_coverage"]["ratio"], 1.0)

    def test_body_only_change_fails_closed_even_with_high_module_confidence(self):
        """A module aggregate cannot prove which callers a body edit reaches."""
        out = self._start(
            diff={"files": ["src/service.py"], "changed_symbols": []})
        self.assertEqual(out["status"], "impact_incomplete")
        self.assertEqual(out["slots"], [])
        quality = review_evidence.ArtifactStore(self.ws).read(
            review._load_state(self.ws, out["run_id"])["quality"])
        self.assertEqual(
            quality["changed_symbol_caller_coverage"]["status"], "incomplete")
        self.assertIsNone(
            quality["changed_symbol_caller_coverage"]["ratio"])

    def test_brief_is_sufficient_to_author_canonical_slot_output(self):
        self._start()
        state = review._load_state(self.ws)
        store = review_evidence.ArtifactStore(self.ws)
        brief = store.read(state["slots"][0]["brief"])
        self.assertEqual(brief["authored_by"], "lens-slot")
        schema = brief["result_schema"]
        self.assertEqual(schema["schema"], "taskplane.lens-slot-output/v2")
        self.assertIn("lens_results", schema["required"])
        self.assertIn("producer_contract", brief)

    def test_self_asserted_authorship_without_hook_receipt_is_rejected(self):
        self._start()
        state = review._load_state(self.ws)
        store = review_evidence.ArtifactStore(self.ws)
        for slot in state["slots"]:
            lease = store.read(slot["lease"])
            row = {**lease, "schema": "taskplane.lens-slot-output/v2",
                   "authored_by": "lens-slot", "findings": [],
                   "lens_results": [
                       {"lens": lid, "verdict": "pass", "blockers": 0}
                       for lid in lease["lens_ids"]]}
            path = os.path.join(self.ws, slot["result_path"])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as stream:
                json.dump(row, stream)
        with self.assertRaisesRegex(review_evidence.ProvenanceError,
                                    "hook-observed"):
            review.collect_review(self.ws, publish=False)

    def test_receipt_binds_exact_result_bytes_not_only_the_write_path(self):
        """A PreToolUse receipt for blocker bytes cannot bless a later pass."""
        self._start()
        self._write_slot_results()
        state = review._load_state(self.ws)
        slot = state["slots"][0]
        path = os.path.join(self.ws, slot["result_path"])
        row = json.load(open(path, encoding="utf-8"))
        row["lens_results"] = [
            {"lens": item["lens"], "verdict": "fail", "blockers": 1}
            for item in row["lens_results"]]
        row["findings"] = [{
            "lens": row["lens_ids"][0], "severity": "blocker",
            "class": "regression", "file": "src/service.py", "line": 1,
            "title": "changed after observation", "scenario": "production",
            "fix": "preserve observed bytes",
        }]
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(row, stream, sort_keys=True, separators=(",", ":"))
        with self.assertRaisesRegex(review_evidence.ProvenanceError,
                                    "exact observed bytes"):
            review.collect_review(self.ws, publish=False)

    def test_real_lifecycle_binds_after_child_activates_producer_contract(self):
        """SubagentStart observes the child before its leased contract exists."""
        self._start()
        state = review._load_state(self.ws)
        store = review_evidence.ArtifactStore(self.ws)
        slot = state["slots"][0]
        lease = store.read(slot["lease"])
        brief = store.read(slot["brief"])
        content = json.dumps({
            **lease, "schema": "taskplane.lens-slot-output/v2",
            "authored_by": "lens-slot", "findings": [],
            "lens_results": [{"lens": lid, "verdict": "pass", "blockers": 0}
                             for lid in lease["lens_ids"]],
        }, sort_keys=True, separators=(",", ":"))
        lifecycle = {"turn_id": "turn-real", "agent_id": "child-real"}
        parent = {"task": "EVALUATE: parent", "read_only": True,
                  "write_allow": [".eval/**"]}
        self.assertIsNone(review.register_slot_producer(
            self.ws, event=lifecycle, contract=parent, task_slot="evaluate"))
        event = {**lifecycle, "tool_name": "Write",
                 "tool_input": {"file_path": slot["result_path"],
                                "content": content}}
        producer = {"task": brief["producer_contract"]["task"],
                    "read_only": True,
                    "write_allow": brief["producer_contract"]["write_allow"]}
        review.record_slot_write_observation(
            self.ws, event=event, contract=producer,
            task_slot=brief["producer_contract"]["task_slot"])
        sibling = {**event, "agent_id": "sibling"}
        with self.assertRaisesRegex(review.ReviewKernelError,
                                    "dispatched child"):
            review.record_slot_write_observation(
                self.ws, event=sibling, contract=producer,
                task_slot=brief["producer_contract"]["task_slot"])

    def test_malformed_findings_are_rejected_before_canonical_commit(self):
        self._start()
        self._write_slot_results(findings=[{"title": "missing evidence"}])
        with self.assertRaisesRegex(review_evidence.ProvenanceError,
                                    "finding schema"):
            review.collect_review(self.ws, publish=False)
        self.assertIsNone(review_evidence._read_current(
            review_evidence.ArtifactStore(self.ws)))

    def test_blocking_finding_cannot_hide_behind_pass_zero_summary(self):
        self._start()
        self._write_slot_results(findings=[{
            "severity": "blocker", "class": "regression",
            "file": "src/service.py", "line": 1,
            "title": "unsafe behavior", "scenario": "production request",
            "fix": "repair the invariant",
        }])
        with self.assertRaisesRegex(review_evidence.ProvenanceError,
                                    "blocking finding"):
            review.collect_review(self.ws, publish=False)
        self.assertIsNone(review_evidence._read_current(
            review_evidence.ArtifactStore(self.ws)))

    def test_publication_failure_restores_prior_revision_and_retry_completes(self):
        first = self._start()
        self._write_slot_results(run_id=first["run_id"])
        first_out = review.collect_review(
            self.ws, publish=False, run_id=first["run_id"])
        store = review_evidence.ArtifactStore(self.ws)
        first_identity = {
            key: first_out[key] for key in ("target_fingerprint",
                                             "context_fingerprint",
                                             "findings_fingerprint",
                                             "canonical_revision")}

        started = self._start()
        self._write_slot_results(run_id=started["run_id"])
        with mock.patch("views.publish_report", return_value=None):
            with self.assertRaisesRegex(review.ReviewKernelError,
                                        "publication failed"):
                review.collect_review(
                    self.ws, publish=True, run_id=started["run_id"])
        self.assertEqual(review_evidence._read_current(store), first_identity)
        with open(os.path.join(self.ws, ".em-review", "findings.json"),
                  encoding="utf-8") as stream:
            meta = json.load(stream)["meta"]
        self.assertEqual({key: meta[key] for key in first_identity},
                         first_identity)
        with mock.patch("views.publish_report",
                        return_value={"root": ".em-review", "withheld": []}):
            out = review.collect_review(
                self.ws, publish=True, run_id=started["run_id"])
        self.assertEqual(out["status"], "complete")
        self.assertEqual(review_evidence._read_current(store), {
            key: out[key] for key in ("target_fingerprint",
                                      "context_fingerprint",
                                      "findings_fingerprint",
                                      "canonical_revision")})

    def test_projections_prepare_before_pointer_without_shared_visibility(self):
        self._start()
        self._write_slot_results()
        findings = os.path.join(self.ws, ".em-review", "findings.json")
        report = os.path.join(self.ws, ".em-review", "report.md")
        publish = mock.Mock(return_value={"root": ".em-review", "withheld": []})

        def interrupt(*_args, **_kwargs):
            self.assertFalse(os.path.exists(findings))
            self.assertFalse(os.path.exists(report))
            self.assertFalse(publish.called)
            artifact_root = os.path.join(
                self.ws, ".taskplane", "review-artifacts-v2")
            visible_kinds = {name for _root, directories, _files in
                             os.walk(artifact_root) for name in directories
                             if name.startswith("projection-") or
                             name == "report-body"}
            self.assertEqual(visible_kinds, {
                "projection-findings", "projection-report",
                "projection-dashboard", "projection-gate", "report-body"})
            raise review_evidence.RevisionError("interrupt before CAS")

        with mock.patch("review_evidence._advance_current",
                        side_effect=interrupt), \
                mock.patch("views.publish_report", publish):
            with self.assertRaisesRegex(review_evidence.RevisionError,
                                        "interrupt before CAS"):
                review.collect_review(self.ws, publish=True)
        self.assertFalse(os.path.exists(findings))
        self.assertFalse(os.path.exists(report))
        self.assertFalse(publish.called)

    def test_second_revision_post_pointer_crash_restores_prior_visibility(self):
        first = self._start()
        self._write_slot_results(run_id=first["run_id"])
        first_out = review.collect_review(
            self.ws, publish=False, run_id=first["run_id"])
        first_identity = {
            key: first_out[key] for key in ("target_fingerprint",
                                             "context_fingerprint",
                                             "findings_fingerprint",
                                             "canonical_revision")}
        second = self._start(
            target={"fingerprint": "target-2", "head": "abc123"})
        self._write_slot_results(run_id=second["run_id"])
        real_save = review._save_state

        def crash_after_pointer(workspace, state):
            real_save(workspace, state)
            if state.get("run_id") == second["run_id"] and \
                    state.get("status") == "committed":
                raise RuntimeError("crash after pointer")

        with mock.patch.object(review, "_save_state",
                               side_effect=crash_after_pointer):
            with self.assertRaisesRegex(RuntimeError, "crash after pointer"):
                review.collect_review(
                    self.ws, publish=False, run_id=second["run_id"])
        store = review_evidence.ArtifactStore(self.ws)
        self.assertEqual(review_evidence._read_current(store), first_identity)
        with open(os.path.join(self.ws, ".em-review", "findings.json"),
                  encoding="utf-8") as stream:
            meta = json.load(stream)["meta"]
        self.assertEqual({key: meta[key] for key in first_identity},
                         first_identity)

        out = review.collect_review(
            self.ws, publish=False, run_id=second["run_id"])
        self.assertEqual(out["canonical_revision"], 2)
        self.assertEqual(review_evidence._read_current(store), {
            key: out[key] for key in ("target_fingerprint",
                                      "context_fingerprint",
                                      "findings_fingerprint",
                                      "canonical_revision")})

    def test_second_revision_projection_prepare_crash_keeps_prior_current(self):
        first = self._start()
        self._write_slot_results(run_id=first["run_id"])
        first_out = review.collect_review(
            self.ws, publish=False, run_id=first["run_id"])
        first_identity = {
            key: first_out[key] for key in ("target_fingerprint",
                                             "context_fingerprint",
                                             "findings_fingerprint",
                                             "canonical_revision")}
        second = self._start(
            target={"fingerprint": "target-2", "head": "abc123"})
        self._write_slot_results(run_id=second["run_id"])
        real_projection = review_evidence.create_projection

        def interrupt(store, revision, *, kind, body):
            if kind == "dashboard":
                raise RuntimeError("crash during projection preparation")
            return real_projection(store, revision, kind=kind, body=body)

        with mock.patch("review_evidence.create_projection",
                        side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError,
                                        "projection preparation"):
                review.collect_review(
                    self.ws, publish=False, run_id=second["run_id"])
        store = review_evidence.ArtifactStore(self.ws)
        self.assertEqual(review_evidence._read_current(store), first_identity)
        with open(os.path.join(self.ws, ".em-review", "findings.json"),
                  encoding="utf-8") as stream:
            meta = json.load(stream)["meta"]
        self.assertEqual({key: meta[key] for key in first_identity},
                         first_identity)
        self.assertEqual(review.collect_review(
            self.ws, publish=False, run_id=second["run_id"]
        )["canonical_revision"], 2)

    def test_second_revision_alias_write_crash_rolls_back_and_retries(self):
        first = self._start()
        self._write_slot_results(run_id=first["run_id"])
        first_out = review.collect_review(
            self.ws, publish=False, run_id=first["run_id"])
        first_identity = {
            key: first_out[key] for key in ("target_fingerprint",
                                             "context_fingerprint",
                                             "findings_fingerprint",
                                             "canonical_revision")}
        second = self._start(
            target={"fingerprint": "target-2", "head": "abc123"})
        self._write_slot_results(run_id=second["run_id"])
        real_write = review.tp.atomic_write_json
        findings_path = os.path.join(self.ws, ".em-review", "findings.json")

        def interrupt(path, data, **kwargs):
            real_write(path, data, **kwargs)
            if path == findings_path:
                raise RuntimeError("crash after findings visibility")

        with mock.patch.object(review.tp, "atomic_write_json",
                               side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "findings visibility"):
                review.collect_review(
                    self.ws, publish=False, run_id=second["run_id"])
        store = review_evidence.ArtifactStore(self.ws)
        self.assertEqual(review_evidence._read_current(store), first_identity)
        with open(findings_path, encoding="utf-8") as stream:
            meta = json.load(stream)["meta"]
        self.assertEqual({key: meta[key] for key in first_identity},
                         first_identity)
        self.assertEqual(review.collect_review(
            self.ws, publish=False, run_id=second["run_id"]
        )["canonical_revision"], 2)

    def test_concurrent_collect_loser_never_publishes_authoritative_views(self):
        first = self._start()
        self._write_slot_results(run_id=first["run_id"])
        second = self._start(
            target={"fingerprint": "target-2", "head": "abc123"})
        self._write_slot_results(run_id=second["run_id"])
        entered = threading.Event()
        release = threading.Event()
        calls = []
        outcomes = {}

        def publish(_ws):
            name = threading.current_thread().name
            calls.append(name)
            if name == "winner":
                entered.set()
                release.wait(5)
            return {"root": ".em-review", "withheld": []}

        def collect(name, run_id):
            try:
                outcomes[name] = review.collect_review(
                    self.ws, publish=True, run_id=run_id)
            except Exception as exc:  # expected only for the losing lease
                outcomes[name] = exc

        with mock.patch("views.publish_report", side_effect=publish):
            winner = threading.Thread(
                target=collect, args=("winner", first["run_id"]),
                name="winner")
            loser = threading.Thread(
                target=collect, args=("loser", second["run_id"]),
                name="loser")
            winner.start()
            self.assertTrue(entered.wait(5))
            loser.start()
            time.sleep(0.1)
            release.set()
            winner.join(5)
            loser.join(5)
        self.assertEqual(calls, ["winner"])
        self.assertEqual(outcomes["winner"]["status"], "complete")
        self.assertIsInstance(outcomes["loser"], review_evidence.RevisionError)

    def test_two_active_runs_are_addressable_and_never_overwrite(self):
        first = self._start()
        second = self._start(
            target={"fingerprint": "target-2", "head": "abc123"})
        self.assertNotEqual(first["run_id"], second["run_id"])
        with self.assertRaisesRegex(review.ReviewKernelError, "run-id"):
            review._load_state(self.ws)
        self.assertEqual(review._load_state(
            self.ws, first["run_id"])["target"]["fingerprint"], "target-1")
        self.assertEqual(review._load_state(
            self.ws, second["run_id"])["target"]["fingerprint"], "target-2")

    def test_manifest_counters_equal_the_final_canonical_bytes(self):
        out = self._start()
        self.assertEqual(out["manifest_bytes"],
                         len(review_evidence.canonical_bytes(out)))
        self.assertEqual(out["counters"]["emitted_bytes"],
                         out["manifest_bytes"])

    def test_collect_commits_empty_revision_when_all_lenses_are_na(self):
        catalog = lens.load_catalog()["lenses"]
        routing = {"lenses": [
            {"id": row["id"], "tier": "n/a", "mode": "none",
             "score": 0, "negative_evidence": ["no matching signal"]}
            for row in catalog], "context": {"signals": {}}}
        started = self._start(router=lambda: routing)
        self.assertEqual(started["slots"], [])
        out = review.collect_review(self.ws, publish=False)
        self.assertEqual(out["status"], "complete")
        self.assertEqual(out["canonical_revision"], 1)


if __name__ == "__main__":
    unittest.main()
