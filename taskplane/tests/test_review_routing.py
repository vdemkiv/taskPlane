"""T3: the graph/evidence kernel is the normal review path."""
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lens  # noqa: E402
import lens_route_policy  # noqa: E402
import loop  # noqa: E402
import depgraph  # noqa: E402
import review  # noqa: E402
import review_evidence  # noqa: E402
import review_retry  # noqa: E402
import run_store  # noqa: E402
import storage  # noqa: E402
import taskplane_lite as tp  # noqa: E402
import tp as cli  # noqa: E402


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
            slot_findings = findings(lease) if callable(findings) else findings
            row = {
                **lease,
                "schema": "taskplane.lens-slot-output/v2",
                "authored_by": "lens-slot",
                "lens_results": [
                    {"lens": lens_id, "verdict": verdict,
                     "blockers": 0 if verdict == "pass" else 1,
                     **({"checked_evidence": [{
                         "file": "src/service.py", "line": 1,
                         "claim": "reviewed the changed service behavior",
                     }]} if verdict == "pass" else {})}
                    for lens_id in lease["lens_ids"]
                ],
                "findings": list(slot_findings or []),
            }
            if brief.get("language_references"):
                row["references_applied"] = list(
                    brief["language_references"])
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

    def _write_pending_slots(self, *, findings_by_lens=None, run_id=None):
        """Author only newly dispatched slots, deriving summaries per lens."""
        state = review._load_state(self.ws, run_id)
        store = review_evidence.ArtifactStore(self.ws)
        findings_by_lens = findings_by_lens or {}
        for index, slot in enumerate(state["slots"]):
            path = os.path.join(self.ws, slot["result_path"])
            if os.path.isfile(path):
                continue
            lease = store.read(slot["lease"])
            brief = store.read(slot["brief"])
            slot_findings = [row for lens_id in lease["lens_ids"]
                             for row in findings_by_lens.get(lens_id, [])]
            lens_results = []
            for lens_id in lease["lens_ids"]:
                blockers = sum(
                    1 for row in slot_findings
                    if row["lens"] == lens_id and
                    loop.finding_blocks(row))
                lens_results.append({
                    "lens": lens_id,
                    "verdict": "fail" if blockers else "pass",
                    "blockers": blockers,
                    **({"checked_evidence": [{
                        "file": "src/service.py", "line": 1,
                        "claim": "reviewed the changed service behavior",
                    }]} if not blockers else {}),
                })
            row = {
                **lease, "schema": "taskplane.lens-slot-output/v2",
                "authored_by": "lens-slot", "lens_results": lens_results,
                "findings": slot_findings,
            }
            if brief.get("language_references"):
                row["references_applied"] = list(
                    brief["language_references"])
            content = json.dumps(row, sort_keys=True, separators=(",", ":"))
            event = {"session_id": f"adaptive-session-{state['run_id']}",
                     "agent_id": f"adaptive-child-{index}",
                     "tool_name": "Write",
                     "tool_input": {"file_path": slot["result_path"],
                                    "content": content}}
            contract = {
                "task": brief["producer_contract"]["task"],
                "task_id": f"adaptive-contract-{index}", "read_only": True,
                "write_allow": [slot["result_path"]],
            }
            review.register_slot_producer(
                self.ws, event=event, contract=contract,
                task_slot=brief["producer_contract"]["task_slot"])
            review.record_slot_write_observation(
                self.ws, event=event, contract=contract,
                task_slot=brief["producer_contract"]["task_slot"])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(content)

    def test_start_maps_all_lenses_and_dispatches_bounded_singleton_sweeps(self):
        out = self._start()
        self.assertEqual(out["status"], "ready")
        self.assertEqual(sum(out["routing_counts"].values()),
                         len(lens.load_catalog()["lenses"]))
        sweeps = list(out["slots"])
        self.assertGreaterEqual(len(sweeps), 4)
        self.assertLessEqual(len(sweeps), 5)
        self.assertTrue(all(row["slot_id"] ==
                            f"sweep.{row['lens_ids'][0]}" and
                            len(row["lens_ids"]) == 1 for row in sweeps))
        state = review._load_state(self.ws, out["run_id"])
        sweep = state["slots"][0]
        brief = review_evidence.ArtifactStore(self.ws).read(sweep["brief"])
        dispatch = tp.dispatch_fields(
            "lens", "tp-lens", "sweep", "cheap")
        expected_role = {
            "agent": "tp-lens",
            **{key: dispatch[key] for key in (
                "model_tier", "reasoning_effort",
                "role_marker")}
        }
        self.assertEqual({key: brief["role"][key] for key in expected_role},
                         expected_role)
        lease = review_evidence.ArtifactStore(self.ws).read(sweep["lease"])
        self.assertEqual(
            brief["role"]["task_name"],
            f"{dispatch['task_name'][:55]}_{lease['lease_fingerprint'][:8]}")
        self.assertNotIn("breadth", json.dumps(out).lower())
        self.assertLessEqual(len(json.dumps(out).encode()), 16 * 1024)

    def test_incremental_retry_membership_keeps_floor_and_bounded_sweep(self):
        prior_run = "a" * 32
        opened = self._start(
            retry_lenses={"architecture", "code-quality"},
            retry_source_run_id=prior_run)
        state = review._load_state(self.ws, opened["run_id"])
        selected = {lens_id for slot in state["slots"]
                    for lens_id in slot["lens_ids"]}
        self.assertTrue({"architecture", "code-quality"} <= selected)
        self.assertGreaterEqual(len(selected), 4)
        self.assertLessEqual(len(selected), 5)
        decision = review_evidence.ArtifactStore(self.ws).read(
            state["routing_decision"])["dispositions"]
        self.assertEqual(decision["architecture"]["verdict"], "sweep")
        self.assertEqual(decision["code-quality"]["verdict"], "sweep")
        self.assertTrue(all(
            row["verdict"] == "n/a" for lens_id, row in decision.items()
            if lens_id not in selected))
        envelope = review_evidence.ArtifactStore(self.ws).read(
            state["envelope"])
        self.assertEqual(
            envelope["change"]["dor"]["incremental_retry"], {
                "source_run_id": prior_run,
                "lenses": ["architecture", "code-quality"],
                "reuse": "sealed-pass-dispositions",
            })

    def test_standalone_signoff_requires_collection_and_human_words(self):
        opened = self._start()
        with self.assertRaises(review.ReviewKernelError):
            review.signoff_review(
                self.ws, decision="approve", by="approved",
                run_id=opened["run_id"])
        self._write_slot_results(run_id=opened["run_id"])
        review.collect_review(
            self.ws, publish=False, run_id=opened["run_id"])
        with self.assertRaises(review.ReviewKernelError):
            review.signoff_review(
                self.ws, decision="approve", by="",
                run_id=opened["run_id"])
        signed = review.signoff_review(
            self.ws, decision="approve", by="approved by user",
            run_id=opened["run_id"])
        self.assertEqual(signed["signoff"]["decision"], "approve")
        self.assertTrue(review.signoff_review(
            self.ws, decision="approve", by="approved by user",
            run_id=opened["run_id"])["idempotent"])

    def test_changed_content_is_extracted_once_from_the_canonical_patch(self):
        patch = ("diff --git a/src/service.py b/src/service.py\n"
                 "--- a/src/service.py\n+++ b/src/service.py\n"
                 "@@ -1,2 +1,2 @@\n-password = old\n+value = 2\n"
                 " unchanged context\n")
        self.assertEqual(review.changed_content_from_patch(patch), {
            "src/service.py":
                "password = old\nvalue = 2\nunchanged context\n"})

    def test_generated_codex_bridge_is_not_part_of_the_review_diff(self):
        ws = tempfile.mkdtemp(prefix="tp-review-diff-")
        subprocess = __import__("subprocess")
        subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
        subprocess.run(["git", "-c", "user.name=T", "-c",
                        "user.email=t@example.com", "commit", "-q",
                        "--allow-empty", "-m", "base"], cwd=ws, check=True)
        base = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ws, text=True,
            encoding="utf-8", errors="replace").strip()
        os.makedirs(os.path.join(ws, ".codex"))
        with open(os.path.join(ws, ".codex", "hooks.json"), "w",
                  encoding="utf-8") as handle:
            json.dump({"hooks": {"Stop": [{"hooks": [{
                "command": "python3 .taskplane/codex-hook.py session-verify"
            }]}]}}, handle)
        with open(os.path.join(ws, "actual.py"), "w",
                  encoding="utf-8") as handle:
            handle.write("value = 1\n")

        rc, patch = review.canonical_diff_patch(ws, base)

        self.assertEqual(rc, 0)
        self.assertIn("actual.py", patch)
        self.assertNotIn(".codex/hooks.json", patch)

    def test_canonical_diff_scope_excludes_unrelated_bytes_and_overflow_is_explicit(self):
        ws = tempfile.mkdtemp(prefix="tp-review-scoped-diff-")
        subprocess = __import__("subprocess")
        subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
        for rel in ("task.py", "unrelated.md"):
            with open(os.path.join(ws, rel), "w", encoding="utf-8") as handle:
                handle.write("base\n")
        subprocess.run(["git", "add", "task.py", "unrelated.md"],
                       cwd=ws, check=True)
        subprocess.run(["git", "-c", "user.name=T", "-c",
                        "user.email=t@example.com", "commit", "-q",
                        "-m", "base"], cwd=ws, check=True)
        base = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ws, text=True,
            encoding="utf-8", errors="replace").strip()
        with open(os.path.join(ws, "task.py"), "w", encoding="utf-8") as handle:
            handle.write("value = 2\n")
        with open(os.path.join(ws, "task_test.py"), "w",
                  encoding="utf-8") as handle:
            handle.write("def test_value():\n    assert True\n")
        with open(os.path.join(ws, "unrelated.md"), "w",
                  encoding="utf-8") as handle:
            handle.write("x" * 20_000)

        rc, patch = review.canonical_diff_patch(
            ws, base, paths=["task.py", "task_test.py"], max_bytes=4_000)

        self.assertEqual(rc, 0)
        self.assertIn("task.py", patch)
        self.assertIn("task_test.py", patch)
        self.assertNotIn("unrelated.md", patch)
        overflow_rc, overflow_patch = review.canonical_diff_patch(
            ws, base, paths=["task.py", "task_test.py"], max_bytes=16)
        self.assertEqual(overflow_rc, review.CANONICAL_DIFF_TOO_LARGE)
        self.assertEqual(overflow_patch, "")

    def test_canonical_diff_explicit_empty_scope_stays_empty(self):
        ws = tempfile.mkdtemp(prefix="tp-review-empty-scope-")
        subprocess = __import__("subprocess")
        subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
        with open(os.path.join(ws, "unrelated.txt"), "w",
                  encoding="utf-8") as handle:
            handle.write("base\n")
        subprocess.run(["git", "add", "unrelated.txt"], cwd=ws, check=True)
        subprocess.run(["git", "-c", "user.name=T", "-c",
                        "user.email=t@example.com", "commit", "-q",
                        "-m", "base"], cwd=ws, check=True)
        base = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ws, text=True,
            encoding="utf-8", errors="replace").strip()
        with open(os.path.join(ws, "unrelated.txt"), "w",
                  encoding="utf-8") as handle:
            handle.write("changed\n")

        rc, patch = review.canonical_diff_patch(ws, base, paths=[])

        self.assertEqual(rc, 0)
        self.assertEqual(patch, "")

    def test_loop_review_kernel_requests_only_governed_implementation_files(self):
        changed = [
            "taskplane/review.py",
            "taskplane/tests/new_regression.py",
            "design/contract.json",
            "unrelated/large.bin",
        ]
        task = {
            "id": "t-scoped-review",
            "scope": ["taskplane/review.py", "taskplane/tests/**"],
            "contracts": [],
        }
        manifest = {"run_id": "a" * 32, "status": "ready"}
        quality_ref = {"kind": "graph-quality", "fingerprint": "q" * 64}
        caller_expander = mock.Mock(name="caller_expander")
        with mock.patch.object(loop, "_diff_files", return_value=changed), \
                mock.patch.object(
                    loop, "_strict_review_graph_quality",
                    return_value=({}, quality_ref, caller_expander)), \
                mock.patch.object(
                    review, "canonical_diff_patch",
                    return_value=(0, "diff --git a/taskplane/review.py "
                                      "b/taskplane/review.py\n")) as derive, \
                mock.patch.object(review, "start_review",
                                  return_value=manifest), \
                mock.patch.object(review, "_load_state",
                                  return_value={"quality": quality_ref,
                                                "routing": {"lenses": []}}):
            opened, _routing = loop._review_kernel(
                self.ws, self.ws, base="HEAD", step="evaluate", task=task,
                graph=self.graph, impact=self.impact,
                requirement={"acceptance": []})

        self.assertEqual(opened, manifest)
        self.assertEqual(derive.call_args.kwargs["paths"], [
            "taskplane/review.py", "taskplane/tests/new_regression.py"])

    def test_slot_registration_loads_only_ready_run_summaries(self):
        opened = self._start()
        state = review._load_state(self.ws, opened["run_id"])
        slot = state["slots"][0]
        expected = slot["producer_contract"]
        index = review._load_index(self.ws)
        for number in range(256):
            run_id = f"{number + 1:032x}"
            if run_id == opened["run_id"]:
                continue
            index["runs"][run_id] = {
                "state": f"missing/{run_id}.json",
                "status": "complete",
                "stage": "review",
                "kernel_policy": review.KERNEL_POLICY_VERSION,
                "target_fingerprint": f"historical-{number}",
            }
        tp.atomic_write_json(review._index_path(self.ws), index, sort_keys=True)
        original_load_json = review.tp.load_json
        loaded_states = []

        def observed_load(path, *args, **kwargs):
            if str(path).endswith("state.json"):
                loaded_states.append(os.path.realpath(path))
            return original_load_json(path, *args, **kwargs)

        event = {"session_id": "bounded-registration-session",
                 "agent_id": "bounded-registration-child"}
        contract = {"task": expected["task"], "read_only": True,
                    "write_allow": list(expected["write_allow"])}
        with mock.patch.object(review.tp, "load_json",
                               side_effect=observed_load):
            registered = review.register_slot_producer(
                self.ws, event=event, contract=contract,
                task_slot=expected["task_slot"])

        self.assertEqual(registered["run_id"], opened["run_id"])
        self.assertEqual(loaded_states, [os.path.realpath(
            review._state_path(self.ws, opened["run_id"]))])

    def test_changed_hunk_context_is_bounded_before_lens_routing(self):
        oversized = "x" * (review.MAX_ROUTING_FILE_BYTES + 100)
        patch = ("diff --git a/src/auth.py b/src/auth.py\n"
                 "--- a/src/auth.py\n+++ b/src/auth.py\n"
                 "@@ -1,2 +1,2 @@ def authorize(user):\n"
                 " if user.is_admin:\n-old = 1\n+" + oversized + "\n")
        content = review.changed_content_from_patch(patch)
        self.assertIn("if user.is_admin", content["src/auth.py"])
        self.assertLessEqual(
            len(content["src/auth.py"].encode("utf-8")),
            review.MAX_ROUTING_FILE_BYTES)

    def test_impact_uncertainty_uses_diff_fallback_for_pr_review(self):
        graph = {**self.graph,
                 "meta": {**self.graph["meta"], "truncated": True}}
        out = self._start(graph=graph)
        self.assertEqual(out["status"], "ready")
        self.assertTrue(out["graph_degraded"])
        self.assertTrue(out["slots"])
        quality = review_evidence.ArtifactStore(self.ws).read(
            review._load_state(self.ws, out["run_id"])["quality"])
        self.assertEqual(quality["review_fallback"]["mode"],
                         "immutable_diff")

    def test_legacy_sparse_run_cannot_block_the_current_diff_fallback(self):
        legacy_run = "0" * 32
        state_path = review._state_path(self.ws, legacy_run)
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        tp.atomic_write_json(state_path, {
            "schema": "taskplane.review-run-state/v2",
            "run_id": legacy_run, "status": "impact_incomplete",
            "stage": "review", "target": self.target,
        }, sort_keys=True)
        tp.atomic_write_json(review._index_path(self.ws), {
            "schema": "taskplane.review-run-index/v2",
            "latest": legacy_run,
            "runs": {legacy_run: {
                "state": os.path.relpath(state_path, self.ws),
                "status": "impact_incomplete", "stage": "review",
                "target_fingerprint": self.target["fingerprint"],
            }},
        }, sort_keys=True)

        with self.assertRaisesRegex(review.ReviewKernelError,
                                    "no matching review kernel run"):
            review._load_state(self.ws)

        current = self._start(
            graph={**self.graph,
                   "meta": {**self.graph["meta"], "truncated": True}})

        self.assertEqual(current["status"], "ready")
        self.assertNotEqual(current["run_id"], legacy_run)
        self.assertEqual(review._load_state(self.ws)["run_id"],
                         current["run_id"])

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

    def test_major_sweep_finding_requires_correction_without_human_deep_auth(self):
        started = self._start()
        state = review._load_state(self.ws, started["run_id"])
        decision = review_evidence.ArtifactStore(self.ws).read(
            state["routing_decision"])["dispositions"]
        light_lens = next(lens_id for lens_id, row in decision.items()
                          if row["verdict"] == "sweep")
        finding = {
            "lens": light_lens, "kind": "defect", "severity": "major",
            "class": "regression", "file": "src/service.py", "line": 1,
            "title": "Sweep discovered a high-risk regression",
            "scenario": "Calling changed() returns unsafe output.",
            "fix": "Correct and cover the changed behavior.",
            "claim": {
                "trigger": "call changed with the documented fixture input",
                "outcome": "the function returns an unsafe output value",
                "repro": "invoke changed in the fixture and inspect its output",
            },
        }
        self._write_pending_slots(
            findings_by_lens={light_lens: [finding]},
            run_id=started["run_id"])

        followup = review.collect_review(
            self.ws, publish=False, run_id=started["run_id"])

        self.assertEqual(followup["status"], "complete")
        self.assertEqual(followup["context_fingerprint"],
                         started["context_fingerprint"])
        promoted = review._load_state(self.ws, started["run_id"])
        self.assertNotIn("adaptive_wave", promoted)
        correction = next(row for row in promoted["quick_corrections"]
                          if row["lens"] == light_lens)
        self.assertEqual(correction["slot"], f"sweep.{light_lens}")
        self.assertEqual(correction["tier"], "sweep")
        self.assertFalse(correction["deep_dispatch"])
        effective = review_evidence.ArtifactStore(self.ws).read(
            promoted["routing_decision"])["dispositions"][light_lens]
        self.assertEqual(effective["verdict"], "sweep")

    def test_light_sweep_normalizes_duplicate_replay_and_cross_charter_risks(self):
        store = review_evidence.ArtifactStore(self.ws)
        decision_ref = store.put("routing-decision", {
            "schema": "taskplane.routing-decision/v2",
            "dispositions": {"security": {"verdict": "light"}},
        })
        accepted = {
            "lens": "security", "kind": "defect", "severity": "major",
            "class": "regression", "file": "src/service.py", "line": 1,
            "title": "Authorization can be bypassed",
            "scenario": "A caller can reach protected data without permission.",
            "claim": {
                "trigger": "invoke the endpoint without authorization",
                "outcome": "protected data is returned",
                "repro": "call the endpoint without a permission token",
            },
        }
        cross_charter = {
            **accepted, "title": "Query needs an index", "line": 2,
            "scenario": "The database query performs a table scan.",
            "claim": {
                "trigger": "run the database query without an index",
                "outcome": "the table scan is slow",
                "repro": "inspect the SQL query plan",
            },
        }
        result_ref = store.put("lens-result", {
            "slot_id": "light-sweep",
            "findings": [accepted, dict(accepted), cross_charter],
        })

        resolved = review._resolve_sweep_corrections(
            store, {"routing_decision": decision_ref}, [result_ref])

        self.assertEqual(len(resolved["corrections"]), 1)
        self.assertEqual(resolved["corrections"][0]["slot"], "light-sweep")
        self.assertEqual(resolved["corrections"][0]["tier"], "sweep")
        self.assertFalse(resolved["corrections"][0]["deep_dispatch"])
        self.assertEqual(
            [row["reason"] for row in resolved["rejections"]],
            ["duplicate", "out-of-charter"],
        )
        self.assertEqual(resolved["outcome"], "correction_required")

    def test_progressive_policy_is_unavailable_until_human_command_is_shipped(self):
        with self.assertRaisesRegex(
                review.ReviewKernelError,
                "adaptive deep review unavailable: "
                "direct-human-command-not-shipped"):
            review.review_depth_policy({
                "id": "R-1", "review_policy": {"depth": "progressive"}})

    def test_low_light_sweep_finding_does_not_dispatch_a_deep_wave(self):
        started = self._start()
        state = review._load_state(self.ws, started["run_id"])
        decision = review_evidence.ArtifactStore(self.ws).read(
            state["routing_decision"])["dispositions"]
        light_lens = next(lens_id for lens_id, row in decision.items()
                          if row["verdict"] == "sweep")
        finding = {
            "lens": light_lens, "kind": "defect", "severity": "minor",
            "class": "observation", "file": "src/service.py", "line": 1,
            "title": "Minor sweep observation",
            "scenario": "The fixture could be clearer.",
            "fix": "Clarify the fixture.",
            "claim": {
                "trigger": "read the service fixture during routine review",
                "outcome": "the wording leaves a minor behavioral ambiguity",
                "repro": "inspect the changed function in src service.py",
            },
        }
        self._write_pending_slots(
            findings_by_lens={light_lens: [finding]},
            run_id=started["run_id"])

        completed = review.collect_review(
            self.ws, publish=False, run_id=started["run_id"])

        self.assertEqual(completed["status"], "complete")
        self.assertNotIn("adaptive_wave", review._load_state(
            self.ws, started["run_id"]))

    def test_review_visuals_reuse_the_sealed_context_and_show_the_human_gate(self):
        depgraph.save(self.ws, self.graph)
        started = self._start()
        visuals, obligations = cli._review_visuals(
            self.ws, started, final=False)
        self.assertEqual(set(visuals), {"workflow_and_wave"})
        self.assertTrue(all("ack" not in row for row in obligations))
        self.assertEqual(next(row for row in obligations
                              if row["kind"] == "render_dashboard")["path"],
                         ".em-review/dashboard.html")
        for row in visuals.values():
            self.assertTrue(os.path.isfile(os.path.join(self.ws, row["path"])))
            inline = os.path.join(self.ws, row["inline"]["path"])
            self.assertTrue(os.path.isfile(inline))
            with open(inline, encoding="utf-8") as stream:
                fragment = stream.read()
            self.assertNotIn("<!doctype", fragment.lower())
            self.assertIn('id="tp-dependency-flow"', fragment)

        self._write_slot_results()
        collected = review.collect_review(self.ws, publish=False)
        final, obligations = cli._review_visuals(
            self.ws, collected, final=True)
        path = os.path.join(self.ws, final["final_dashboard"]["path"])
        with open(path, encoding="utf-8") as stream:
            body = stream.read()
        self.assertLess(body.index('id="tp-review-workflow"'),
                        body.index('id="tp-dependency-flow"'))
        self.assertIn("your decision", body)
        self.assertIn("approve · request changes", body)
        self.assertEqual(next(row for row in obligations
                              if row["kind"] == "render_dashboard")["path"],
                         ".em-review/dashboard.html")
        self.assertTrue(all(row.get("ack") for row in obligations))

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
        self.assertEqual(arch["tier"], "sweep")
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
        self.assertEqual(out["status"], "ready")
        self.assertTrue(out["graph_degraded"])
        quality = review_evidence.ArtifactStore(self.ws).read(
            review._load_state(self.ws)["quality"])
        self.assertEqual(
            quality["changed_symbol_caller_coverage"]["status"], "incomplete")
        self.assertNotEqual(
            quality["changed_symbol_caller_coverage"]["ratio"], 1.0)

    def test_body_only_pr_with_complete_module_graph_is_not_degraded(self):
        """A strong module graph does not need synthetic symbol evidence."""
        out = self._start(
            diff={"files": ["src/service.py"], "changed_symbols": []})
        self.assertEqual(out["status"], "ready")
        self.assertTrue(out["slots"])
        self.assertFalse(out["graph_degraded"])
        quality = review_evidence.ArtifactStore(self.ws).read(
            review._load_state(self.ws, out["run_id"])["quality"])
        self.assertEqual(
            quality["changed_symbol_caller_coverage"]["status"], "complete")
        self.assertEqual(
            quality["changed_symbol_caller_coverage"]["ratio"], 1.0)

    def test_managed_pr_flow_collects_leased_artifacts_from_parent(self):
        """The marketplace PR journey does not depend on host receipt timing."""
        home = tempfile.mkdtemp(prefix="tp-managed-review-home-")
        checkout = tempfile.mkdtemp(prefix="tp-managed-review-checkout-")
        parent = tempfile.mkdtemp(prefix="tp-managed-review-parent-")
        subprocess = __import__("subprocess")
        subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"],
                       cwd=checkout, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=checkout,
                       check=True)
        source = os.path.join(checkout, "PluginHeader.tsx")
        with open(source, "w", encoding="utf-8") as stream:
            stream.write("export const PluginHeader = () => null;\n")
        subprocess.run(["git", "add", "PluginHeader.tsx"], cwd=checkout,
                       check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=checkout,
                       check=True)
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=checkout, text=True,
            encoding="utf-8", errors="replace").strip()
        identity = storage.resolve_repository_identity(checkout)
        repository_run_id = "managed-pr-review"
        layout = storage.resolve_layout(
            identity, home=home, run_id=repository_run_id)
        run_store.RunStore(home=home).create(
            identity, run_id=repository_run_id, checkout=checkout,
            host={"kind": "codex"},
            target={"kind": "pr", "head": head})
        storage.write_workspace_locator(
            checkout, identity=identity, layout=layout,
            run_id=repository_run_id)
        unrelated_external = os.path.join(home, "outside", "result.json")
        self.assertFalse(tp.writable_target(
            unrelated_external, [unrelated_external], checkout),
            "absolute write authority must stay inside validated run roots")
        graph = {
            "files": {"PluginHeader.tsx": {"hash": "a"}},
            "modules": {"ui": {"files": ["PluginHeader.tsx"]}},
            "edges": [],
            "meta": {"scanned_head": head,
                     "content_fingerprint": "managed-graph",
                     "scanners": {"typescript": {
                         "coverage": "complete", "covered_files": 1,
                         "total_files": 1}}},
        }
        impact = {
            "touched": ["ui"], "impacted": {}, "unknown": [],
            "total_impacted": 1, "truncated": True,
            "depth_truncated": True,
            "policy": {"local_depth": 3, "contract_depth": 1,
                       "requirement_depth": 1,
                       "boundary_mode": "contract-only"},
            "policy_blocked": [],
        }
        target = {"fingerprint": "managed-target", "head": head,
                  "merge_base": "b" * 40}
        diff = {"files": ["PluginHeader.tsx"], "changed_symbols": [],
                "patch_artifact": {"fingerprint": "managed-diff"}}
        oracle_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "evals", "managed-pr-review", "oracle.json")
        with open(oracle_path, encoding="utf-8") as stream:
            oracle = json.load(stream)

        with mock.patch.dict(os.environ, {"TASKPLANE_HOME": home,
                                          "CODEX_THREAD_ID": ""}):
            opened = review.start_review(
                checkout, target=target, graph=graph, impact=impact,
                diff=diff, runnability={"summary": "available"},
                requirement={"id": "R-managed", "text": "review PR"},
                acceptance=["collect exact leased results"],
                contracts=["contract:ui"])
            self.assertEqual(opened["status"], oracle["expected"]["status"])
            self.assertEqual(opened["graph_degraded"],
                             oracle["expected"]["graph_degraded"])
            self.assertEqual(sum(opened["routing_counts"].values()),
                             oracle["expected"]["lens_dispositions"])
            state = review._load_state(checkout, opened["run_id"])
            artifact_store = review_evidence.ArtifactStore(checkout)
            for index, slot in enumerate(state["slots"]):
                lease = artifact_store.read(slot["lease"])
                brief = artifact_store.read(slot["brief"])
                self.assertTrue(os.path.isdir(os.path.dirname(
                    slot["result_path"])),
                    "review start owns the leased result directory")
                producer = brief["producer_contract"]
                contract = tp.build_contract(
                    producer["task"], read_only=True,
                    write_allow=producer["write_allow"], tools=["Write"])
                with mock.patch.dict(os.environ, {
                        "TASKPLANE_TASK": producer["task_slot"]}):
                    tp.activate(checkout, contract,
                                snapshot=tp.git_head(checkout))
                result = {
                    **lease, "schema": "taskplane.lens-slot-output/v2",
                    "authored_by": "lens-slot",
                    "lens_results": [{"lens": lens_id, "verdict": "pass",
                                      "blockers": 0,
                                      "checked_evidence": [{
                                          "file": "PluginHeader.tsx",
                                          "line": 1,
                                          "claim": "reviewed source"}]}
                                     for lens_id in lease["lens_ids"]],
                    "findings": [],
                }
                if brief.get("language_references"):
                    result["references_applied"] = list(
                        brief["language_references"])
                content = json.dumps(
                    result, sort_keys=True, separators=(",", ":"))
                event = {
                    "turn_id": f"managed-child-turn-{index}",
                    "agent_id": f"managed-child-{index}", "cwd": parent,
                    "tool_name": "Write",
                    "tool_input": {"file_path": slot["result_path"],
                                   "content": content}}
                review.register_slot_producer(
                    checkout, event=event, contract=contract,
                    task_slot=producer["task_slot"])
                if index == 0:
                    screened = __import__("io").StringIO()
                    with mock.patch.dict(os.environ, {
                            "TASKPLANE_HOME": home,
                            "TASKPLANE_TASK": producer["task_slot"]}), \
                            mock.patch("sys.stdin", __import__("io").StringIO(
                                json.dumps(event))), \
                            __import__("contextlib").redirect_stdout(screened):
                        self.assertEqual(cli.main(["screen"]), 0)
                    self.assertEqual(screened.getvalue().strip(), "",
                                     "Codex leased writes stay advisory")
                else:
                    review.record_slot_write_observation(
                        checkout, event=event, contract=contract,
                        task_slot=producer["task_slot"])
                self.assertEqual(
                    review.leased_result_workspace(
                        parent, [slot["result_path"]]), checkout)
                os.makedirs(os.path.dirname(slot["result_path"]),
                            exist_ok=True)
                with open(slot["result_path"], "w",
                          encoding="utf-8") as stream:
                    stream.write(content)
            resolved = review.resolve_review_workspace(
                parent, opened["run_id"])
            self.assertEqual(resolved, os.path.realpath(checkout))
            output = __import__("io").StringIO()
            with mock.patch.object(cli, "_review_visuals",
                                   return_value=({}, [])), \
                    __import__("contextlib").redirect_stdout(output):
                rc = cli.main([
                    "review", "collect", "--run-id", opened["run_id"],
                    "--workspace", parent, "--no-publish"])
            self.assertEqual(rc, 0, output.getvalue())
            collected = json.loads(output.getvalue())
            self.assertEqual(collected["status"],
                             oracle["expected"]["collected_status"])
            self.assertEqual(collected["canonical_revision"], 1)
            self.assertEqual(len(collected["result_validations"]),
                             len(state["slots"]))
            validations = [artifact_store.read(ref)
                           for ref in collected["result_validations"]]
            self.assertEqual({row["trust"] for row in validations},
                             {"host-observed"})
            self.assertEqual(tp.list_task_slots(checkout), [],
                             "canonical collect releases finished lens slots")
            output = __import__("io").StringIO()
            with __import__("contextlib").redirect_stdout(output):
                rc = cli.main([
                    "review", "signoff", "approve", "--by",
                    "human reviewer", "--run-id", opened["run_id"],
                    "--workspace", parent])
            self.assertEqual(rc, 0, output.getvalue())
            signed = json.loads(output.getvalue())
            self.assertEqual(signed["signoff"]["decision"], "approve")

    def test_brief_is_sufficient_to_author_canonical_slot_output(self):
        self._start()
        state = review._load_state(self.ws)
        store = review_evidence.ArtifactStore(self.ws)
        brief = store.read(state["slots"][0]["brief"])
        self.assertEqual(brief["authored_by"], "lens-slot")
        schema = brief["result_schema"]
        self.assertEqual(schema["schema"], "taskplane.lens-slot-output/v2")
        self.assertIn("lens_results", schema["required"])
        self.assertEqual(schema["lens_result"]["blockers"],
                         {"type": "integer", "minimum": 0})
        self.assertEqual(schema["findings"],
                         {"type": "array", "items": "finding"})
        self.assertIn("lens", schema["finding"]["required"])
        self.assertEqual(
            schema["codex_completion_receipt"]["advisory_lines"],
            ["taskplane-result-path:<result_path>",
             "taskplane-result-sha256:<sha256>"])
        self.assertEqual(
            schema["codex_completion_receipt"]["authority"],
            "the sealed, validated lease artifact")
        self.assertIn("producer_contract", brief)

    def test_language_reference_ack_is_exact_and_canonical(self):
        self._start()
        state = review._load_state(self.ws)
        store = review_evidence.ArtifactStore(self.ws)
        slot = next(row for row in state["slots"]
                    if store.read(row["brief"]).get("language_references"))
        brief = store.read(slot["brief"])
        self.assertEqual(
            brief["result_schema"]["references_applied"]["exact"],
            brief["language_references"])
        self._write_slot_results()
        path = os.path.join(self.ws, slot["result_path"])
        row = json.load(open(path, encoding="utf-8"))
        row.pop("references_applied")
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(row, stream, sort_keys=True, separators=(",", ":"))
        manifest = review.collect_review(self.ws, publish=False)
        self.assertEqual(manifest["status"], "incomplete")
        self.assertTrue(any("exact language references" in gap["reason"]
                            for gap in manifest["gaps"]))

    def test_legacy_codex_session_inference_is_not_provenance(self):
        """Session prose cannot replace the sealed leased-result contract."""
        import hashlib

        self._start()
        state = review._load_state(self.ws)
        store = review_evidence.ArtifactStore(self.ws)
        codex_home = tempfile.mkdtemp(prefix="tp-codex-session-")
        session_dir = os.path.join(codex_home, "sessions", "2026", "08", "14")
        os.makedirs(session_dir)
        parent = "codex-parent-thread"
        for index, slot in enumerate(state["slots"]):
            lease = store.read(slot["lease"])
            brief = store.read(slot["brief"])
            row = {**lease, "schema": "taskplane.lens-slot-output/v2",
                   "authored_by": "lens-slot", "findings": [],
                   "lens_results": [
                       {"lens": lens_id, "verdict": "pass", "blockers": 0,
                        "checked_evidence": [{"file": "src/service.py",
                                              "line": 1,
                                              "claim": "reviewed source"}]}
                       for lens_id in lease["lens_ids"]]}
            if brief.get("language_references"):
                row["references_applied"] = list(
                    brief["language_references"])
            raw = json.dumps(row, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
            result = os.path.join(self.ws, slot["result_path"])
            os.makedirs(os.path.dirname(result), exist_ok=True)
            with open(result, "wb") as stream:
                stream.write(raw)
            role = brief["role"]
            final = ("review complete\n"
                     f"taskplane-result-path:{slot['result_path']}\n"
                     "taskplane-result-sha256:"
                     f"{hashlib.sha256(raw).hexdigest()}")
            events = [
                {"type": "session_meta", "payload": {
                    "id": f"child-{index}", "source": {"subagent": {
                        "thread_spawn": {
                            "parent_thread_id": parent,
                            "agent_path": "/root/" + role["task_name"]}}}}},
                {"type": "turn_context", "payload": {
                    "model": "gpt-test",
                    "reasoning_effort": role["reasoning_effort"]}},
                {"type": "event_msg", "payload": {
                    "type": "task_complete", "last_agent_message": final}},
            ]
            rollout = os.path.join(session_dir, f"rollout-{index}.jsonl")
            with open(rollout, "w", encoding="utf-8") as stream:
                for event in events:
                    stream.write(json.dumps(event) + "\n")
        with mock.patch.dict(os.environ, {
                "CODEX_HOME": codex_home, "CODEX_THREAD_ID": parent}):
            for slot in state["slots"]:
                lease = store.read(slot["lease"])
                with open(os.path.join(self.ws, slot["result_path"]),
                          "rb") as stream:
                    raw = stream.read()
                self.assertIsNone(review._codex_session_receipt(
                    self.ws, store, slot, lease, raw))
            out = review.collect_review(self.ws, publish=False)
        self.assertEqual(out["status"], "complete")

    def test_valid_leased_artifacts_collect_without_hook_receipts(self):
        self._start()
        state = review._load_state(self.ws)
        store = review_evidence.ArtifactStore(self.ws)
        for slot in state["slots"]:
            lease = store.read(slot["lease"])
            brief = store.read(slot["brief"])
            row = {**lease, "schema": "taskplane.lens-slot-output/v2",
                   "authored_by": "lens-slot", "findings": [],
                   "lens_results": [
                       {"lens": lid, "verdict": "pass", "blockers": 0,
                        "checked_evidence": [{"file": "src/service.py",
                                              "line": 1,
                                              "claim": "reviewed source"}]}
                       for lid in lease["lens_ids"]]}
            if brief.get("language_references"):
                row["references_applied"] = list(
                    brief["language_references"])
            path = os.path.join(self.ws, slot["result_path"])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as stream:
                json.dump(row, stream)
        collected = review.collect_review(self.ws, publish=False)
        self.assertEqual(collected["status"], "complete")
        validations = [store.read(ref)
                       for ref in collected["result_validations"]]
        self.assertEqual({row["trust"] for row in validations},
                         {"leased-artifact"})

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
            "lens": row["lens_ids"][0], "kind": "defect",
            "severity": "blocker",
            "class": "regression", "file": "src/service.py", "line": 1,
            "title": "changed after observation", "scenario": "production",
            "fix": "preserve observed bytes",
            "claim": {
                "trigger": "change a leased result after hook observation",
                "outcome": "collection accepts bytes the hook never observed",
                "repro": "write a blocker after recording pass result bytes"},
        }]
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(row, stream, sort_keys=True, separators=(",", ":"))
        manifest = review.collect_review(self.ws, publish=False)
        self.assertEqual(manifest["status"], "incomplete")
        self.assertTrue(any("exact observed bytes" in gap["reason"]
                            for gap in manifest["gaps"]))

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
            "lens_results": [{"lens": lid, "verdict": "pass", "blockers": 0,
                              "checked_evidence": [{
                                  "file": "src/service.py", "line": 1,
                                  "claim": "reviewed source"}]}
                             for lid in lease["lens_ids"]],
            **({"references_applied": list(brief["language_references"])}
               if brief.get("language_references") else {}),
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

    def test_observed_result_releases_exact_producer_contract(self):
        self._start()
        state = review._load_state(self.ws)
        slot = state["slots"][0]
        producer = slot["producer_contract"]
        contract = {**producer, "task_id": producer["task_slot"],
                    "budget": {"max_actions": 20}}
        tp.activate(self.ws, contract, snapshot=None)
        self._write_slot_results(run_id=state["run_id"])
        self.assertFalse(os.path.exists(
            tp.active_contract_path(self.ws, producer["task_slot"])))

    def test_dead_ready_collection_owner_is_recoverable(self):
        opened = self._start()
        path = review._collection_lock_path(self.ws)
        tp.atomic_write_json(path, {
            "schema": "taskplane.review-publication-reservation/v1",
            "run_id": opened["run_id"], "owner_pid": 99999999,
            "owner_id": "dead-owner", "acquired_at": 1,
        }, sort_keys=True)
        with mock.patch.object(tp, "_pid_alive", return_value=False):
            lease = review._acquire_collection_reservation(
                self.ws, opened["run_id"])
        self.assertEqual(lease["run_id"], opened["run_id"])
        self.assertEqual(review._load_state(
            self.ws, opened["run_id"])["status"], "ready")

    def test_malformed_findings_are_rejected_before_canonical_commit(self):
        self._start()
        self._write_slot_results(findings=[{"title": "missing evidence"}])
        manifest = review.collect_review(self.ws, publish=False)
        self.assertEqual(manifest["status"], "incomplete")
        self.assertTrue(any("finding schema" in gap["reason"]
                            for gap in manifest["gaps"]))
        self.assertIsNone(review_evidence._read_current(
            review_evidence.ArtifactStore(self.ws)))

    def test_blocking_finding_cannot_hide_behind_pass_zero_summary(self):
        self._start()
        self._write_slot_results(findings=lambda lease: [{
            "lens": lease["lens_ids"][0], "kind": "defect",
            "severity": "high",
            "class": "regression", "file": "src/service.py", "line": 1,
            "title": "unsafe behavior", "scenario": "production request",
            "fix": "repair the invariant",
            "claim": {
                "trigger": "send a production request through the unsafe path",
                "outcome": "the request violates the required safety invariant",
                "repro": "run the failing request against the changed service"},
        }])
        manifest = review.collect_review(self.ws, publish=False)
        store = review_evidence.ArtifactStore(self.ws)
        validations = [store.read(ref)
                       for ref in manifest["result_validations"]]

        self.assertEqual(manifest["status"], "complete")
        self.assertTrue(manifest["findings"])
        self.assertTrue(all(row["repair"]["equivalence"] == "proven"
                            for row in validations))
        self.assertTrue(all(row["repair"]["derivation_authority"] ==
                            "canonical-admissible-findings/v1"
                            for row in validations))
        self.assertIsNotNone(review_evidence._read_current(store))

    def test_collection_reports_every_invalid_slot_in_one_repair_batch(self):
        self._start()
        self._write_slot_results(findings=lambda lease: [{
            "lens": lease["lens_ids"][0], "kind": "defect",
            "severity": "high", "class": "regression",
            "file": "src/service.py", "line": 1,
            "title": "unsafe behavior", "scenario": "production request",
            "fix": "repair the invariant",
            "claim": {
                "trigger": "send a production request through the unsafe path",
                "outcome": "the request violates the required safety invariant",
                "repro": "run the failing request against the changed service"},
        }])
        state = review._load_state(self.ws)
        manifest = review.collect_review(self.ws, publish=False)
        store = review_evidence.ArtifactStore(self.ws)
        validations = [store.read(ref)
                       for ref in manifest["result_validations"]]
        self.assertTrue({row["slot_id"] for row in state["slots"]} <=
                        {row["slot_id"] for row in validations})
        self.assertTrue(all(row["repair"]["equivalence"] == "proven"
                            for row in validations))
        self.assertTrue(all(
                            row["repair"]["equivalence_fingerprint_before"] ==
                            row["repair"]["equivalence_fingerprint_after"]
                            for row in validations))
        self.assertEqual(manifest["schema"],
                         "taskplane.review-collect-manifest/v2")
        self.assertEqual(manifest["status"], "complete")
        self.assertNotIn("gaps", manifest)

    def test_review_blocking_policy_matches_the_canonical_class_rule(self):
        cases = [
            ({"lens": "security", "severity": "high",
              "class": "regression"}, True),
            ({"lens": "security", "severity": "low",
              "class": "regression"}, True),
            ({"lens": "security", "severity": "high",
              "class": "pre-existing"}, False),
            ({"lens": "security", "severity": "high",
              "class": "observation"}, False),
        ]
        for finding, expected in cases:
            with self.subTest(finding=finding):
                counts = review.blocking_findings_by_lens([finding])
                self.assertEqual(bool(counts), expected)
                self.assertEqual(bool(counts), loop.finding_blocks(finding))

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
        winner_transaction_lock_acquired = threading.Event()
        winner_publish_entered = threading.Event()
        winner_publish_release = threading.Event()
        loser_lock_attempted = threading.Event()
        loser_lock_acquired = threading.Event()
        loser_reservation_rejected = threading.Event()
        loser_lock_released = threading.Event()
        winner_reservation_released = threading.Event()
        winner_collection_entries = {"count": 0}
        event_order = []
        event_order_lock = threading.Lock()
        calls = []
        outcomes = {}
        real_file_lock = review.tp.file_lock
        real_acquire_reservation = review._acquire_collection_reservation
        real_release_reservation = review._release_collection_reservation
        collection_lock_path = review._collection_lock_path(self.ws)

        def record_event(label, event):
            with event_order_lock:
                event_order.append(label)
            event.set()

        def publish(_ws):
            name = threading.current_thread().name
            calls.append(name)
            if name == "winner":
                self.assertTrue(winner_transaction_lock_acquired.is_set())
                record_event("winner-publish-entered", winner_publish_entered)
                self.assertTrue(winner_publish_release.wait(5))
            return {"root": ".em-review", "withheld": []}

        def collect(name, run_id):
            try:
                outcomes[name] = review.collect_review(
                    self.ws, publish=True, run_id=run_id)
            except Exception as exc:  # expected only for the losing lease
                outcomes[name] = exc

        @contextlib.contextmanager
        def ordered_file_lock(path, *, timeout=10.0):
            if path != collection_lock_path:
                with real_file_lock(path, timeout=timeout):
                    yield
                return
            name = threading.current_thread().name
            entry_number = None
            if name == "winner":
                winner_collection_entries["count"] += 1
                entry_number = winner_collection_entries["count"]
                # The third entry releases the winner's reservation. Hold it
                # until the loser has acquired the real lock and had its
                # conflicting publication reservation rejected.
                if entry_number == 3:
                    self.assertTrue(
                        loser_reservation_rejected.wait(timeout))
            elif name == "loser":
                record_event("loser-lock-attempted", loser_lock_attempted)
            try:
                with real_file_lock(path, timeout=timeout):
                    if name == "winner" and entry_number == 2:
                        record_event(
                            "winner-transaction-lock-acquired",
                            winner_transaction_lock_acquired)
                    elif name == "loser":
                        record_event("loser-lock-acquired",
                                     loser_lock_acquired)
                    yield
            finally:
                if name == "loser" and loser_lock_acquired.is_set():
                    record_event("loser-lock-released", loser_lock_released)

        def observed_acquire_reservation(ws, run_id):
            try:
                return real_acquire_reservation(ws, run_id)
            except review_evidence.RevisionError:
                if threading.current_thread().name == "loser":
                    record_event("loser-reservation-rejected",
                                 loser_reservation_rejected)
                raise

        def observed_release_reservation(ws, lease):
            result = real_release_reservation(ws, lease)
            if threading.current_thread().name == "winner":
                record_event("winner-reservation-released",
                             winner_reservation_released)
            return result

        with mock.patch("views.publish_report", side_effect=publish), \
                mock.patch.object(
                    review.tp, "file_lock", side_effect=ordered_file_lock), \
                mock.patch.object(
                    review, "_acquire_collection_reservation",
                    side_effect=observed_acquire_reservation), \
                mock.patch.object(
                    review, "_release_collection_reservation",
                    side_effect=observed_release_reservation):
            winner = threading.Thread(
                target=collect, args=("winner", first["run_id"]),
                name="winner")
            loser = threading.Thread(
                target=collect, args=("loser", second["run_id"]),
                name="loser")
            winner.start()
            self.assertTrue(winner_publish_entered.wait(5))
            loser.start()
            self.assertTrue(loser_lock_attempted.wait(5))
            self.assertFalse(loser_lock_acquired.is_set())
            record_event("winner-publish-release", winner_publish_release)
            winner.join(5)
            loser.join(5)
        self.assertFalse(winner.is_alive())
        self.assertFalse(loser.is_alive())
        self.assertTrue(loser_lock_acquired.is_set())
        self.assertTrue(loser_reservation_rejected.is_set())
        self.assertTrue(loser_lock_released.is_set())
        self.assertTrue(winner_reservation_released.is_set())
        self.assertEqual(winner_collection_entries["count"], 3)
        self.assertEqual(event_order, [
            "winner-transaction-lock-acquired",
            "winner-publish-entered",
            "loser-lock-attempted",
            "winner-publish-release",
            "loser-lock-acquired",
            "loser-reservation-rejected",
            "loser-lock-released",
            "winner-reservation-released",
        ])
        self.assertEqual(calls, ["winner"])
        self.assertEqual(outcomes["winner"]["status"], "complete")
        self.assertIsInstance(outcomes["loser"], review_evidence.RevisionError)

    def test_completed_run_releases_dead_exact_reservation_before_next_revision(self):
        first = self._start()
        self._write_slot_results(run_id=first["run_id"])
        first_out = review.collect_review(
            self.ws, publish=False, run_id=first["run_id"])
        reservation_path = review._collection_lock_path(self.ws)
        dead_lease = {
            "schema": "taskplane.review-publication-reservation/v1",
            "run_id": first["run_id"], "owner_pid": 99999999,
            "owner_id": "dead-after-durable-complete", "acquired_at": 1,
        }
        review.tp.atomic_write_json(
            reservation_path, dead_lease, sort_keys=True)

        with mock.patch.object(review.tp, "_pid_alive", return_value=False):
            self.assertEqual(review.collect_review(
                self.ws, publish=False, run_id=first["run_id"]), first_out)
        self.assertFalse(os.path.exists(reservation_path))

        second = self._start(
            target={"fingerprint": "target-2", "head": "abc123"})
        self._write_slot_results(run_id=second["run_id"])
        self.assertEqual(review.collect_review(
            self.ws, publish=False, run_id=second["run_id"]
        )["canonical_revision"], 2)

    def test_completed_run_rejects_live_different_owner_without_releasing_it(self):
        opened = self._start()
        self._write_slot_results(run_id=opened["run_id"])
        review.collect_review(
            self.ws, publish=False, run_id=opened["run_id"])
        reservation_path = review._collection_lock_path(self.ws)
        live_lease = {
            "schema": "taskplane.review-publication-reservation/v1",
            "run_id": opened["run_id"], "owner_pid": 1234,
            "owner_id": "live-different-owner", "acquired_at": 1,
        }
        review.tp.atomic_write_json(
            reservation_path, live_lease, sort_keys=True)

        with mock.patch.object(review.tp, "_pid_alive", return_value=True):
            with self.assertRaisesRegex(
                    review_evidence.RevisionError, "live owner"):
                review.collect_review(
                    self.ws, publish=False, run_id=opened["run_id"])
        self.assertEqual(review.tp.load_json(
            reservation_path, what="test reservation"), live_lease)

    def test_completed_run_leaves_another_runs_reservation_untouched(self):
        opened = self._start()
        self._write_slot_results(run_id=opened["run_id"])
        manifest = review.collect_review(
            self.ws, publish=False, run_id=opened["run_id"])
        reservation_path = review._collection_lock_path(self.ws)
        other_lease = {
            "schema": "taskplane.review-publication-reservation/v1",
            "run_id": "f" * 32, "owner_pid": 99999999,
            "owner_id": "another-runs-owner", "acquired_at": 1,
        }
        review.tp.atomic_write_json(
            reservation_path, other_lease, sort_keys=True)

        self.assertEqual(review.collect_review(
            self.ws, publish=False, run_id=opened["run_id"]), manifest)
        self.assertEqual(review.tp.load_json(
            reservation_path, what="test reservation"), other_lease)

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

        output = io.StringIO()
        args = SimpleNamespace(
            repository_action="status", workspace=self.ws,
            run_id=first["run_id"])
        with mock.patch.object(
                run_store.RunStore, "load",
                side_effect=FileNotFoundError("run manifest is unavailable")), \
                contextlib.redirect_stdout(output):
            status_code = cli.cmd_repository(args)
        status_manifest = json.loads(output.getvalue())
        self.assertEqual(status_code, 0)
        self.assertEqual(status_manifest,
                         review._load_state(
                             self.ws, first["run_id"])["manifest"])

        self._write_slot_results(run_id=first["run_id"])
        collected = review.collect_review(
            self.ws, publish=False, run_id=first["run_id"])
        self.assertEqual(collected["run_id"], status_manifest["run_id"])
        self.assertEqual(review._load_state(
            self.ws, second["run_id"])["status"], "ready")

    def test_manifest_counters_equal_the_final_canonical_bytes(self):
        out = self._start()
        self.assertEqual(out["manifest_bytes"],
                         len(review_evidence.canonical_bytes(out)))
        self.assertEqual(out["counters"]["emitted_bytes"],
                         out["manifest_bytes"])

    def test_full_catalog_directive_is_bounded_to_automatic_sweep_budget(self):
        catalog = lens.load_catalog()["lenses"]
        routing = {
            "lenses": [
                {
                    **row,
                    "tier": "deep", "verdict": "deep",
                    "mode": "subagent", "score": 100,
                    "evidence": ["forced full-catalog regression fixture"],
                }
                for row in catalog
            ],
            "context": {"signals": {}},
        }

        out = self._start(router=lambda: routing, task_type="implementation")

        self.assertEqual(out["status"], "ready")
        self.assertEqual(out["routing_counts"]["sweep"], 5)
        self.assertEqual(len(out["slots"]), 5)
        self.assertTrue(all(len(row["lens_ids"]) == 1 and
                            row["slot_id"].startswith("sweep.")
                            for row in out["slots"]))
        self.assertLessEqual(out["manifest_bytes"], review.MAX_MANIFEST_BYTES)
        self.assertEqual(out["manifest_bytes"],
                         len(review_evidence.canonical_bytes(out)))

    def test_aggregate_manifest_budget_remains_bounded(self):
        oversized = {"payload": "x" * review.MAX_MANIFEST_BYTES}

        with self.assertRaisesRegex(
                review.ReviewKernelError, "review manifest exceeds"):
            review._manifest(oversized)

    def test_architecture_floor_prevents_an_all_na_automatic_route(self):
        catalog = lens.load_catalog()["lenses"]
        routing = {"lenses": [
            {"id": row["id"], "name": row["name"],
             "tier": "n/a", "verdict": "n/a", "mode": "none",
             "score": 0, "negative_evidence": ["no matching signal"]}
            for row in catalog], "context": {"signals": {}}}
        started = self._start(router=lambda: routing)
        self.assertGreaterEqual(len(started["slots"]), 4)
        self.assertLessEqual(len(started["slots"]), 5)
        self.assertTrue(any(slot["lens_ids"] == ["architecture"]
                            for slot in started["slots"]))
        self._write_slot_results()
        out = review.collect_review(self.ws, publish=False)
        self.assertEqual(out["status"], "complete")
        self.assertEqual(out["canonical_revision"], 1)


def test_evaluate_selects_three_or_four_from_material_implementation_evidence():
    catalog = lens.load_catalog()
    files = ["src/service.py"]
    content = {"src/service.py": "def changed():\n    return 2\n"}
    mapped = lens.route(
        files, task_type="reliability", stage="build", breadth="routed",
        requirement_text="A reliable focused review with executable tests",
        content_by_file=content)
    inputs = {
        "target": {"fingerprint": "a" * 64, "head": "abc123"},
        "requirement": {"id": "R-0001", "text": "focused review"},
        "acceptance": ["the focused route evaluates the implementation"],
        "design_contract": {"fingerprint": "design-v1", "edges": ["a->b"]},
        "diff": {"files": files, "changed_symbols": ["changed"],
                 "artifact": {"fingerprint": "diff-v1"}},
        "impact": {"touched": ["src"], "total_impacted": 1,
                   "unknown": []},
        "test_evidence": {"summary": "passed", "selectors": ["focused"]},
        "unresolved_findings": [],
        "routing_content": content,
    }

    route, projected = review._focused_evaluate_route(
        mapped, catalog=catalog, **inputs)

    assert route["status"] == "ready"
    assert 3 <= len(route["selected"]) <= 4
    assert len(projected["lenses"]) == len(catalog["lenses"]) == 26
    assert all(row["disposition"] == "execute_light"
               for row in route["dispositions"]
               if row["lens"] in route["selected"])
    assert {row["id"] for row in projected["lenses"]
            if row["tier"] == "sweep"} == set(route["selected"])

    with tempfile.TemporaryDirectory(prefix="tp-focused-evaluate-") as ws:
        os.makedirs(os.path.join(ws, "src"))
        with open(os.path.join(ws, "src", "service.py"), "w",
                  encoding="utf-8") as stream:
            stream.write(content["src/service.py"])
        started = review.start_review(
            ws, target=inputs["target"],
            graph={"meta": {"scanned_head": "abc123",
                            "content_fingerprint": "graph-v1"},
                   "modules": {"src": {"files": files}}, "edges": []},
            impact=inputs["impact"], diff=inputs["diff"],
            runnability=inputs["test_evidence"],
            requirement=inputs["requirement"], acceptance=inputs["acceptance"],
            contracts=["resource:review.route-fingerprint"], stage="build",
            task_type="reliability", router=lambda: mapped,
            routing_content=content,
            design_contract=inputs["design_contract"],
            unresolved_findings=inputs["unresolved_findings"])
        state = review._load_state(ws, started["run_id"])
        stored = review_evidence.ArtifactStore(ws).read(
            state["routing_decision"])["focused_route"]
        assert stored["status"] == "ready"
        assert len(stored["dispositions"]) == 26
        assert 3 <= len(stored["selected"]) <= 4
        assert {lens_id for slot in started["slots"]
                for lens_id in slot["lens_ids"]} == set(stored["selected"])

    mutations = [
        {"diff": {**inputs["diff"],
                  "changed_symbols": ["changed", "changed_again"]}},
        {"diff": {**inputs["diff"],
                  "files": ["src/service.py", "tests/test_service.py"]}},
        {"impact": {**inputs["impact"], "total_impacted": 3}},
        {"test_evidence": {"summary": "passed", "selectors": ["focused", "radius"]}},
        {"unresolved_findings": [{"lens": "architecture",
                                  "fingerprint": "finding-v1"}]},
    ]
    for mutation in mutations:
        changed, _ = review._focused_evaluate_route(
            mapped, catalog=catalog, **{**inputs, **mutation})
        assert changed["route_fingerprint"] != route["route_fingerprint"]

    overflow, refused = review._focused_evaluate_route(
        mapped, catalog=catalog, **inputs,
        mandatory_lenses={"architecture", "code-quality", "testability",
                          "security", "product"})
    assert overflow["status"] == "expanded_approval_required"
    assert len(overflow["selected"]) == 5
    assert not [row for row in refused["lenses"] if row["tier"] == "sweep"]


def test_focused_evaluate_accepts_integer_depth_dependency_impact():
    graph = {
        "meta": {"scanned_head": "abc123"},
        "modules": {"service": {}, "consumer": {}, "api": {}},
        "edges": [
            {"from": "consumer", "to": "service", "kind": "imports"},
            {"from": "api", "to": "consumer", "kind": "imports"},
        ],
    }
    with mock.patch.object(depgraph, "load", return_value=graph):
        impact = depgraph.impact("/unused", ["service"])
    assert set(impact["impacted"]) == {1, 2}

    catalog = lens.load_catalog()
    files = ["src/service.py"]
    content = {"src/service.py": "def changed():\n    return 2\n"}
    mapped = lens.route(
        files, task_type="reliability", stage="build", breadth="routed",
        requirement_text="A reliable focused review with executable tests",
        content_by_file=content)
    inputs = {
        "target": {"fingerprint": "a" * 64, "head": "abc123"},
        "requirement": {"id": "R-0001", "text": "focused review"},
        "acceptance": ["the focused route evaluates the implementation"],
        "design_contract": {"fingerprint": "design-v1", "edges": ["a->b"]},
        "diff": {"files": files, "changed_symbols": ["changed"],
                 "artifact": {"fingerprint": "diff-v1"}},
        "test_evidence": {"summary": "passed", "selectors": ["focused"]},
        "unresolved_findings": [],
        "routing_content": content,
    }

    route, _ = review._focused_evaluate_route(
        mapped, catalog=catalog, impact=impact, **inputs)
    string_depth_impact = {
        **impact,
        "impacted": {str(depth): rows
                     for depth, rows in impact["impacted"].items()},
    }
    string_route, _ = review._focused_evaluate_route(
        mapped, catalog=catalog, impact=string_depth_impact, **inputs)

    assert route["status"] == "ready"
    assert route["route_fingerprint"] == string_route["route_fingerprint"]


def test_fix_reruns_only_invalidated_fingerprinted_lens_evidence():
    selected = ["architecture", "code-quality", "testability", "qa"]
    prior = {
        "schema": lens_route_policy.DECISION_SCHEMA,
        "status": "ready",
        "policy_version": lens_route_policy.POLICY_VERSION,
        "catalog_fingerprint": "catalog-v1",
        "selected": selected,
        "dispatchable_selected": selected,
        "lens_input_fingerprints": {
            lens_id: f"fp-{lens_id}" for lens_id in selected},
    }
    sealed_results = [
        {"lens": lens_id, "verdict": "pass", "blockers": 0,
         "sealed": True, "host_provenance": "verified",
         "result_fingerprint": f"result-{lens_id}"}
        for lens_id in selected
    ]

    unchanged = review_retry.fingerprinted_reuse_plan(
        prior, prior, sealed_results)
    assert unchanged["dispatch"] == []
    assert unchanged["reused"] == selected

    single_route = json.loads(json.dumps(prior))
    single_route["lens_input_fingerprints"]["testability"] = "changed-one"
    single = review_retry.fingerprinted_reuse_plan(
        prior, single_route, sealed_results)
    assert single["dispatch"] == ["testability"]
    assert single["invalidation"]["testability"] == "input_fingerprint_changed"

    multiple_route = json.loads(json.dumps(prior))
    multiple_route["lens_input_fingerprints"].update({
        "architecture": "changed-architecture", "qa": "changed-qa"})
    multiple = review_retry.fingerprinted_reuse_plan(
        prior, multiple_route, sealed_results)
    assert multiple["dispatch"] == ["architecture", "qa"]
    assert set(multiple["reused"]) == {"code-quality", "testability"}

    failed = json.loads(json.dumps(sealed_results))
    failed[0]["verdict"] = "fail"
    failed_plan = review_retry.fingerprinted_reuse_plan(prior, prior, failed)
    assert failed_plan["dispatch"] == ["architecture"]
    assert failed_plan["invalidation"]["architecture"] == "prior_result_not_passing"

    fixture = TestSelectiveReviewKernel()
    fixture.setUp()
    try:
        first = fixture._start(
            stage="build", task_type="reliability", design_contract={},
            unresolved_findings=[])
        fixture._write_slot_results(run_id=first["run_id"])
        review.collect_review(fixture.ws, publish=False, run_id=first["run_id"])
        second = fixture._start(
            stage="build", task_type="reliability", design_contract={},
            unresolved_findings=[], retry_source_run_id=first["run_id"],
            retry_lenses={"architecture"})
        assert second["slots"] == []
        second_state = review._load_state(fixture.ws, second["run_id"])
        assert set(second_state["reuse_plan"]["reused"]) == set(
            second_state["focused_route"]["selected"])
        assert review.collect_review(
            fixture.ws, publish=False, run_id=second["run_id"]
        )["status"] == "complete"
    finally:
        __import__("shutil").rmtree(fixture.ws, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
