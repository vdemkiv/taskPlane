"""Evidence bundle (P2, R-0012) — engine computes facts, agent owns judgment.

Phase 3 agents spent 41 percent of shell wall-clock rebuilding, one shell
call at a time, facts the engine already held: which criteria exist, which
lenses routed, what the diff touched, which graph nodes are impacted,
whether the suite passed. `tp loop evidence` hands all of that over in one
call.

The guardrail these tests exist to pin is the SPLIT. The engine may state
an obligation; it may never discharge one. So the load-bearing test here is
not that the bundle is complete — it is that a bundle taken straight from
the engine and submitted unchanged is REFUSED at the evaluate gate.
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
import graph_quality  # noqa: E402
import review  # noqa: E402
import review_evidence  # noqa: E402
import taskplane_lite as tp  # noqa: E402
import evaluation_output  # noqa: E402


TASK = {"id": "t1", "scope": ["src/todo/**"], "tests": "true",
        "criteria": ["complete() marks done"]}


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
    json.dump({"tasks": tasks}, open(os.path.join(ws, "plan", "tasks.json"), "w", encoding="utf-8"))
    return ws


def submit_gate(ws, outcome="pass", task_id=None):
    with mock.patch("runtime_eval.guide_loop",
                    return_value={"status": "on_path", "recovered": False}):
        submitted = loop.submit(ws, outcome, task_id=task_id)
    if "error" in submitted:
        return submitted
    return loop.gate(ws, outcome, task_id=task_id)


def author_leased_results(ws):
    """Produce the canonical per-slot evidence the Evaluate gate consumes.

    A filled free-form verdict is intentionally insufficient: every routed
    lens must come from its leased, hook-observed producer slot.
    """
    state = review._load_state(ws)
    store = review_evidence.ArtifactStore(ws)
    for index, slot in enumerate(state["slots"]):
        lease = store.read(slot["lease"])
        brief = store.read(slot["brief"])
        payload = {
            **lease,
            "schema": "taskplane.lens-slot-output/v2",
            "authored_by": "lens-slot",
            "lens_results": [
                {"lens": lens_id, "verdict": "pass", "blockers": 0,
                 "checked_evidence": [{"file": "src/todo/a.py", "line": 1,
                                       "claim": "reviewed source"}]}
                for lens_id in lease["lens_ids"]
            ],
            "findings": [],
        }
        if brief.get("language_references"):
            payload["references_applied"] = list(
                brief["language_references"])
        content = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        event = {"session_id": "evidence-bundle-lens",
                 "agent_id": f"evidence-bundle-child-{index}",
                 "tool_name": "Write",
                 "tool_input": {"file_path": slot["result_path"],
                                "content": content}}
        contract = {"task": brief["producer_contract"]["task"],
                    "task_id": "evidence-bundle-contract",
                    "read_only": True,
                    "write_allow": [slot["result_path"]]}
        review.register_slot_producer(
            ws, event=event, contract=contract,
            task_slot=brief["producer_contract"]["task_slot"])
        review.record_slot_write_observation(
            ws, event=event, contract=contract,
            task_slot=brief["producer_contract"]["task_slot"])
        path = os.path.join(ws, slot["result_path"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(content)


class _AtEvaluate(unittest.TestCase):
    """Drive a loop to the evaluate step with one real task."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.ws = git_ws(self.tmp, [TASK])
        loop.init(self.ws, "g", spec_path="specs/spec.md")
        loop.next_action(self.ws)
        loop.gate(self.ws, "pass")               # plan → plan_approval
        loop.approve(self.ws, "plan")
        loop.next_action(self.ws)                # execute
        open(os.path.join(self.ws, "src", "todo", "a.py"), "a",
             encoding="utf-8").write("\ndef complete():\n    return True\n")
        submit_gate(self.ws, "pass")             # execute → evaluate
        loop.next_action(self.ws)

    def bundle(self, **kw):
        return loop.evidence(self.ws, **kw)


class TestTheEngineNeverJudges(_AtEvaluate):
    def test_bundle_declares_the_complete_evaluator_contract(self):
        bundle = self.bundle()
        self.assertEqual(bundle["output_schema"],
                         evaluation_output.evaluator_output_schema())
        self.assertEqual(bundle["output_contract"]["output_schema"],
                         bundle["output_schema"])
        self.assertEqual(bundle["output_schema_id"],
                         evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID)
        self.assertEqual(bundle["max_attempts"], 2)

    def test_every_criterion_slot_comes_back_empty(self):
        b = self.bundle()
        self.assertTrue(b["criteria"], "the obligation must be stated")
        for row in b["criteria"]:
            self.assertEqual(row["status"], "")
            self.assertEqual(row["evidence"], "")

    def test_every_lens_slot_comes_back_empty(self):
        b = self.bundle()
        for row in b.get("lenses") or []:
            self.assertEqual(row["verdict"], "")
            self.assertIsNone(row["blockers"])

    def test_the_top_level_verdict_comes_back_empty(self):
        self.assertEqual(self.bundle()["verdict"], "")

    def test_graph_dispositions_come_back_empty(self):
        g = self.bundle().get("graph")
        if g:
            for row in g["dispositions"]:
                self.assertEqual(row["status"], "")
                self.assertEqual(row["evidence"], "")
            self.assertEqual(g["requirements_checked"], [])
            self.assertEqual(g["contracts_checked"], [])

    def test_an_unedited_bundle_is_refused_at_the_gate(self):
        """THE load-bearing test. If the engine's own output could pass the
        gate, the engine would be grading itself and the evaluate step would
        be decoration."""
        self.bundle(write=True)
        path = os.path.join(self.ws, ".eval", "verdict.json")
        self.assertTrue(os.path.exists(path))
        result = submit_gate(self.ws, "pass")
        self.assertIn("error", result,
                      "an unjudged bundle must never satisfy the gate")
        self.assertEqual(loop.load(self.ws)["step"], "evaluate",
                         "the loop must not advance on engine output alone")

    def test_the_refusal_names_the_unproven_criteria(self):
        self.bundle(write=True)
        result = submit_gate(self.ws, "pass")
        blob = json.dumps(result)
        self.assertIn("acceptance criterion", blob)


class TestTheBundleMatchesWhatTheGateDemands(_AtEvaluate):
    def test_the_lens_set_is_exactly_the_gate_s_expected_set(self):
        """A bundle that briefed a NARROWER lens set than the gate checks
        would quietly send evaluators into a refusal they cannot see coming
        — and a wider one would invent obligations. Both are drift; the
        bundle and the gate must derive from the same route."""
        state = loop.load(self.ws)
        task = state["tasks"][state["current_task"]]
        routing = loop.lens_router.route_git_diff(
            self.ws, base=state.get("baseline") or "HEAD",
            task_type=task.get("type"), stage=loop.EVALUATE_ROUTE_STAGE,
            breadth="routed")
        expected = {e["id"] for e in routing["lenses"]
                    if e.get("mode") != "none"}
        offered = {r["lens"] for r in self.bundle().get("lenses") or []}
        self.assertEqual(offered, expected)

    def test_the_criteria_are_exactly_the_gate_s_expected_criteria(self):
        state = loop.load(self.ws)
        task = state["tasks"][state["current_task"]]
        expected = loop._criteria_for(self.ws, state, task)
        offered = [r["criterion"] for r in self.bundle()["criteria"]]
        self.assertEqual(offered, expected)

    def test_a_filled_bundle_does_pass_the_gate(self):
        """The complement: once an agent actually discharges the obligation
        the bundle stated, nothing else is in the way."""
        bundle = self.bundle()
        b = bundle["verdict_template"]
        for row in b["criteria"]:
            row["status"] = "met"
            row["evidence"] = "covered by the task's tests"
        for row in b.get("lenses") or []:
            row["verdict"] = "pass"
            row["blockers"] = 0
        if b.get("graph"):
            for row in b["graph"]["dispositions"]:
                row["status"] = "tested"
                row["evidence"] = "covered by declared task tests"
            b["graph"]["requirements_checked"] = list(
                (bundle.get("graph") or {}).get("requirements_to_check") or [])
            b["graph"]["contracts_checked"] = list(
                (bundle.get("graph") or {}).get("contracts_to_verify") or [])
        b["verdict"] = "pass"
        author_leased_results(self.ws)
        os.makedirs(os.path.join(self.ws, ".eval"), exist_ok=True)
        with open(os.path.join(self.ws, ".eval", "verdict.json"), "w", encoding="utf-8") as f:
            json.dump(b, f)
        result = submit_gate(self.ws, "pass")
        self.assertNotIn("error", result)


class TestUnavailableModelEvaluationDoesNotOpenAProductFix(_AtEvaluate):
    """Host/model availability is not an implementation defect."""

    def _write_unavailable(self, *, not_met=False):
        verdict = self.bundle()["verdict_template"]
        verdict["evaluation"] = {
            "status": "unavailable",
            "reason_code": "agent_timeout",
            "detail": "one bounded native model attempt did not complete",
        }
        verdict["verdict"] = "fail"
        for index, row in enumerate(verdict["criteria"]):
            row["status"] = "not-met" if not_met and index == 0 else "met"
            row["evidence"] = "mechanical evidence remains available"
        verdict["lenses"] = []
        verdict["failures"] = [{
            "what": "native model evaluation was unavailable",
            "repro": "one bounded dispatch attempt",
            "where": "host:model-evaluation",
        }]
        path = os.path.join(self.ws, ".eval", "verdict.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(verdict, stream)

    def test_unavailable_advances_with_warning_without_a_fix_cycle(self):
        self._write_unavailable()
        result = submit_gate(self.ws, "unavailable")
        self.assertNotIn("error", result)
        state = loop.load(self.ws)
        self.assertEqual(state["step"], "em")
        self.assertEqual(state["tasks"][0]["status"], "passed")
        self.assertEqual(state["tasks"][0]["fix_cycles"], 0)
        self.assertEqual(state["tasks"][0]["evaluation"]["status"],
                         "unavailable")

    def test_product_failure_cannot_be_disguised_as_unavailable(self):
        self._write_unavailable(not_met=True)
        result = submit_gate(self.ws, "unavailable")
        self.assertIn("error", result)
        self.assertEqual(loop.load(self.ws)["step"], "evaluate")
        self.assertEqual(loop.load(self.ws)["tasks"][0]["fix_cycles"], 0)

    def test_fail_submission_is_refused_for_pure_host_unavailability(self):
        self._write_unavailable()
        submitted = loop.submit(self.ws, "fail")
        self.assertNotIn("error", submitted)
        result = loop.gate(self.ws, "fail")
        self.assertIn("gate unavailable", result["error"])
        self.assertEqual(loop.load(self.ws)["step"], "evaluate")
        self.assertEqual(loop.load(self.ws)["tasks"][0]["fix_cycles"], 0)

    def test_unavailable_requires_green_mechanical_suite_evidence(self):
        self._write_unavailable()
        with loop.mutate(self.ws) as state:
            state.get("_suite_evidence", {}).pop("t1", None)
        result = submit_gate(self.ws, "unavailable")
        self.assertIn("green mechanical suite", json.dumps(result))
        self.assertEqual(loop.load(self.ws)["step"], "evaluate")


class TestWriteIsNonDestructive(_AtEvaluate):
    def test_an_existing_verdict_is_never_overwritten(self):
        path = os.path.join(self.ws, ".eval", "verdict.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"authored": "by the agent"}, f)
        out = self.bundle(write=True)
        self.assertFalse(out["written"])
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"authored": "by the agent"})

    def test_without_write_nothing_is_written(self):
        self.bundle()
        self.assertFalse(os.path.exists(
            os.path.join(self.ws, ".eval", "verdict.json")))


class TestDegradationIsLoud(_AtEvaluate):
    def test_post_kernel_mapper_failure_cannot_replace_canonical_decision(self):
        """Once persisted, the one governed decision is the only authority."""
        with mock.patch.object(loop.lens_router, "route_git_diff",
                               side_effect=RuntimeError("catalog gone")):
            b = self.bundle()
        self.assertNotIn("lenses_error", b)
        self.assertEqual(b["review_kernel"]["status"], "ready")
        self.assertTrue(b["lenses"])

    def test_an_unknown_task_id_is_refused(self):
        self.assertIn("error", self.bundle(task_id="nope"))

    def test_no_loop_is_refused(self):
        bare = tempfile.mkdtemp()
        self.assertIn("error", loop.evidence(bare))

    def test_bundle_consumes_live_kernel_and_never_reroutes(self):
        kernel = review._load_state(self.ws)
        store = review_evidence.ArtifactStore(self.ws)
        decision = store.read(kernel["routing_decision"])["dispositions"]
        expected = {lens_id for lens_id, row in decision.items()
                    if row["verdict"] != "n/a"}
        with mock.patch.object(loop.lens_router, "route_git_diff",
                               side_effect=AssertionError("must not remap")):
            bundle = self.bundle()
        self.assertEqual(bundle["review_kernel"]["run_id"], kernel["run_id"])
        self.assertEqual({row["lens"] for row in bundle["lenses"]}, expected)

    def test_impact_incomplete_kernel_produces_zero_lens_obligations(self):
        second = os.path.join(self.tmp, "impact-incomplete")
        os.makedirs(second)
        ws = git_ws(second, [TASK])
        loop.init(ws, "g", spec_path="specs/spec.md")
        loop.next_action(ws); loop.gate(ws, "pass")
        loop.approve(ws, "plan"); loop.next_action(ws)
        with open(os.path.join(ws, "src", "todo", "a.py"), "w",
                  encoding="utf-8") as stream:
            stream.write("x=2\n")
        submit_gate(ws, "pass")
        real_assess = graph_quality.assess

        def low_confidence(*args, **kwargs):
            record = real_assess(*args, **kwargs)
            record["status"] = "impact_incomplete"
            record["module_confidence"] = "low"
            record["reasons"] = ["fixture forces incomplete graph evidence"]
            coverage = record["changed_symbol_caller_coverage"]
            coverage["status"] = "incomplete"
            coverage["ratio"] = None
            return record

        with mock.patch.object(graph_quality, "assess",
                               side_effect=low_confidence):
            action = loop.next_action(ws)
        self.assertEqual(action["review_kernel"]["status"],
                         "impact_incomplete")
        with mock.patch.object(loop.lens_router, "route_git_diff",
                               side_effect=AssertionError("must not remap")):
            bundle = loop.evidence(ws)
        self.assertEqual(bundle["review_kernel"]["status"],
                         "impact_incomplete")
        self.assertEqual(bundle["lenses"], [])
        self.assertEqual(bundle["lenses_not_applicable"], [])
        self.assertIn("impact_incomplete", bundle["lenses_error"])


class TestTheSuiteIsCitedNotRerun(_AtEvaluate):
    @staticmethod
    def _transport_shim(root):
        package = os.path.join(root, "taskplane")
        os.makedirs(package)
        with open(os.path.join(package, "__init__.py"), "w",
                  encoding="utf-8") as stream:
            stream.write("from pathlib import Path\n"
                         "__path__ = [str(Path.cwd() / 'taskplane')]\n")

    def test_native_executor_and_evaluator_task_ids_share_suite_identity(self):
        base = dict(os.environ)
        producer = {**base, "CODEX_THREAD_ID": "executor-thread",
                    "TASKPLANE_TASK": "execute-t1"}
        consumer = {**base, "CODEX_THREAD_ID": "evaluator-thread",
                    "TASKPLANE_TASK": "evaluate-t1"}
        self.assertEqual(
            tp._suite_cache_key(self.ws, TASK["tests"], producer),
            tp._suite_cache_key(self.ws, TASK["tests"], consumer))
        changed = {**producer, "TASKPLANE_AUDIT_EVERY": "different"}
        self.assertNotEqual(
            tp._suite_cache_key(self.ws, TASK["tests"], producer),
            tp._suite_cache_key(self.ws, TASK["tests"], changed))

    def test_transport_only_pythonpath_is_not_suite_behavior_identity(self):
        shim = tempfile.mkdtemp()
        self._transport_shim(shim)
        native = {"LANG": "C.UTF-8"}
        transported = {**native, "PYTHONPATH": shim}
        self.assertEqual(
            tp._suite_cache_key(self.ws, TASK["tests"], native),
            tp._suite_cache_key(self.ws, TASK["tests"], transported))
        with open(os.path.join(shim, "behavior.py"), "w",
                  encoding="utf-8") as stream:
            stream.write("VALUE = 'changes imports'\n")
        self.assertNotEqual(
            tp._suite_cache_key(self.ws, TASK["tests"], native),
            tp._suite_cache_key(self.ws, TASK["tests"], transported))

    def test_native_evaluator_cites_gate_record_across_transport_shim(self):
        shim = tempfile.mkdtemp()
        self._transport_shim(shim)
        state = loop.load(self.ws)
        record = state["_suite_evidence"][TASK["id"]]
        producer_env = {**os.environ, "PYTHONPATH": shim,
                        "CODEX_THREAD_ID": "execute-thread",
                        "TASKPLANE_TASK": "execute-t1"}
        record["key"] = tp._suite_cache_key(
            self.ws, TASK["tests"], producer_env)
        record["returncode"] = 0
        record["source"] = "execute-gate"
        loop.save(self.ws, state)
        original = tp._run

        def refuse_suite(command, *args, **kwargs):
            if kwargs.get("shell"):
                raise AssertionError("native evaluator must never rerun suite")
            return original(command, *args, **kwargs)

        consumer = {**os.environ, "CODEX_THREAD_ID": "evaluate-thread",
                    "TASKPLANE_TASK": "evaluate-t1"}
        consumer.pop("PYTHONPATH", None)
        with mock.patch.dict(os.environ, consumer, clear=True), \
                mock.patch.object(tp, "_run", side_effect=refuse_suite):
            bundle = self.bundle()
        self.assertTrue(bundle["suite"]["cited"])
        self.assertEqual(bundle["suite"]["source"], "execute-gate")
        self.assertEqual(bundle["suite"]["returncode"], 0)

    def test_native_no_record_runner_binds_imports_to_checkout(self):
        checkout = tempfile.mkdtemp()
        package = os.path.join(checkout, "taskplane", "tests")
        os.makedirs(package)
        with open(os.path.join(package, "__init__.py"), "w",
                  encoding="utf-8") as stream:
            stream.write("LOCAL = True\n")
        with open(os.path.join(package, "test_native.py"), "w",
                  encoding="utf-8") as stream:
            stream.write(
                "import taskplane.tests\n"
                "def test_checkout_package_wins():\n"
                "    assert taskplane.tests.LOCAL is True\n")
        proc = tp.run_suite_command(
            checkout,
            "python -m pytest -q -p no:cacheprovider "
            "taskplane/tests/test_native.py",
            env=dict(os.environ))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_checkout_binding_propagates_to_nested_python_processes(self):
        checkout = tempfile.mkdtemp()
        package = os.path.join(checkout, "taskplane", "tests")
        os.makedirs(package)
        with open(os.path.join(package, "__init__.py"), "w",
                  encoding="utf-8") as stream:
            stream.write("LOCAL = True\n")
        with open(os.path.join(package, "test_nested.py"), "w",
                  encoding="utf-8") as stream:
            stream.write(
                "import subprocess,sys\n"
                "def test_nested_checkout_wins():\n"
                "    child = subprocess.run([sys.executable, '-c', "
                "'import taskplane.tests; assert taskplane.tests.LOCAL'], "
                "capture_output=True, text=True)\n"
                "    assert child.returncode == 0, child.stderr\n")
        polluted = tempfile.mkdtemp()
        os.makedirs(os.path.join(polluted, "taskplane"))
        with open(os.path.join(polluted, "taskplane", "__init__.py"), "w",
                  encoding="utf-8") as stream:
            stream.write("REGULAR_INSTALLED_PACKAGE = True\n")
        proc = tp.run_suite_command(
            checkout,
            "python -m pytest -q -p no:cacheprovider "
            "taskplane/tests/test_nested.py",
            env={**os.environ, "PYTHONPATH": polluted})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_no_record_bundle_executes_checkout_bound_python_fallback(self):
        state = loop.load(self.ws)
        state.pop("_suite_evidence", None)
        state["tasks"][0]["tests"] = (
            "python -c \"import os,taskplane; "
            "assert taskplane.__path__ == "
            "[os.path.join(os.getcwd(),'taskplane')]\"")
        loop.save(self.ws, state)
        with mock.patch.object(tp, "suite_cache_lookup", return_value=None):
            bundle = self.bundle()
        self.assertEqual(bundle["suite"]["returncode"], 0, bundle["suite"])
        self.assertFalse(bundle["suite"]["cited"])

    def test_the_bundle_cites_the_run_the_execute_gate_already_paid_for(self):
        """The wave economics in one assertion: the execute gate ran this
        task's tests, so the evaluator's bundle must cite that run rather
        than buy a second identical one."""
        b = self.bundle()
        self.assertTrue(b["suite"]["cited"])
        self.assertEqual(b["suite"]["returncode"], 0)
        self.assertIsNotNone(b["suite"].get("seconds_saved"))

    def test_green_declared_suite_does_not_launch_a_second_regression_suite(self):
        """A task gate pays once: green suite + cheap coverage guard only."""
        contract = tp.build_contract(
            "EXECUTE: t1", scope=TASK["scope"], test_command=TASK["tests"],
            plan_minted=True, regression_gate=True)
        regression = mock.Mock()
        regression.dod_errors.return_value = []
        hit = {
            "key": "same-content", "command": TASK["tests"],
            "returncode": 0, "tail": "green", "duration_s": 1.0,
            "produced_in": self.ws,
        }
        with mock.patch.object(tp, "suite_cache_lookup", return_value=hit), \
                mock.patch.dict(sys.modules, {"regression": regression}):
            errors = tp.dod_check(
                contract, self.ws, tp.snapshot_ref(self.ws),
                regression_files=["src/todo/a.py"], suite_evidence={})

        self.assertEqual(errors, [])
        self.assertIsNone(regression.dod_errors.call_args.args[1])

    def test_the_kill_switch_forces_the_bundle_to_execute(self):
        with mock.patch.dict(os.environ,
                             {"TASKPLANE_NO_SUITE_CACHE": "1"}, clear=False):
            b = self.bundle()
        self.assertFalse(b["suite"]["cited"])
        self.assertIn("seconds", b["suite"])

    def test_the_bundle_is_traced(self):
        self.bundle()
        path = os.path.join(tp.tp_dir(self.ws), "trace.jsonl")
        with open(path, encoding="utf-8") as f:
            events = [json.loads(x) for x in f if x.strip()]
        rows = [e for e in events if e.get("event") == "evidence_bundle"]
        self.assertTrue(rows)
        self.assertEqual(rows[-1]["task"], "t1")


if __name__ == "__main__":
    unittest.main()
