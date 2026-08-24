import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loop  # noqa: E402
import taskplane_lite as tp  # noqa: E402
import lens  # noqa: E402
import depgraph  # noqa: E402
import evaluator_health  # noqa: E402
import review  # noqa: E402
import review_evidence  # noqa: E402
import storage as runtime_storage  # noqa: E402
import checkpoint  # noqa: E402
import build_c  # noqa: E402


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


TASK = {"id": "t1", "scope": ["src/todo/**"], "tests": "true",
        "criteria": ["complete() marks done"]}


class TestProgramOrder(unittest.TestCase):
    def _ledger(self):
        return {
            "schema": "taskplane.r0012-program-ledger/v1",
            "program_authority": {
                "schema": build_c.PROGRAM_LEDGER_SCHEMA,
                "consolidated_approval": {
                    "approved": True, "actor": "user",
                    "authority_receipt": "decision:0045"},
                "r0009": {"accepted": True,
                           "evidence_digest": "baseline:green"},
                "r0010": {"status": "active"},
                "r0011": {"exact_sha_green": False,
                           "signed_off_by": None},
            },
        }

    def test_program_order_opens_only_authorized_phase(self):
        authority = build_c.require_program_phase(self._ledger(), "r0010")
        self.assertEqual(authority["phase"], "r0010")
        self.assertEqual(authority["status"], "authorized")

        missing_approval = self._ledger()
        missing_approval["program_authority"]["consolidated_approval"] = {
            "approved": False, "actor": "", "authority_receipt": ""}
        with self.assertRaisesRegex(build_c.ProgramAuthorityError,
                                    "consolidated human approval"):
            build_c.require_program_phase(missing_approval, "r0010")

        missing_r0009 = self._ledger()
        missing_r0009["program_authority"]["r0009"]["accepted"] = False
        with self.assertRaisesRegex(build_c.ProgramAuthorityError,
                                    "R-0009 acceptance"):
            build_c.require_program_phase(missing_r0009, "r0010")

        with self.assertRaisesRegex(build_c.ProgramAuthorityError,
                                    "exact-SHA proof and human sign-off"):
            build_c.require_program_phase(self._ledger(), "r0011")

    def test_program_order_wiring_precedes_plan_execution(self):
        source = open(loop.__file__, encoding="utf-8").read()
        approve_start = source.index("def approve(")
        approve_end = source.index("\ndef retro(", approve_start)
        approve_body = source[approve_start:approve_end]
        projection = approve_body.index("build_c.project_define(")
        execute = approve_body.index('state["step"] = "execute"')
        self.assertLess(projection, execute)
        build_c_source = open(build_c.__file__, encoding="utf-8").read()
        self.assertIn("start_review(", build_c_source)
        self.assertNotIn("automatic_sweep_route", build_c_source)
        self.assertNotIn("lens.route", build_c_source)


class TestScopeAssignment(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = git_ws(self.tmp, [TASK])
        self.tasks = [
            {"id": "t-a", "scope": ["src/a/**"], "deps": [],
             "status": "pending"},
            {"id": "t-b", "scope": ["src/b/**"], "deps": [],
             "status": "pending"},
            {"id": "t-a-next", "scope": ["src/a/nested/**"], "deps": [],
             "status": "pending"},
        ]
        self.graph = {
            "modules": {
                "a": {"files": ["src/a/one.py"]},
                "a/nested": {"files": ["src/a/nested/two.py"]},
                "b": {"files": ["src/b/one.py"]},
            },
            "edges": [], "files": {},
            "meta": {"fingerprint": "graph-1"},
        }

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _assign(self, **overrides):
        created = []
        registered = []

        def create(ws, task_id, revision):
            path = os.path.join(self.tmp, task_id)
            created.append((task_id, revision, path))
            return path

        def register(ws, worker, task_id):
            registered.append((task_id, worker))
            return {"schema": "taskplane.managed-task-worktree/v1",
                    "task_id": task_id, "path": worker,
                    "branch_tip": "a" * 40}

        args = {
            "graph": self.graph,
            "create_worktree": create,
            "register_worktree": register,
            "revision": "a" * 40,
        }
        args.update(overrides)
        receipt = build_c.assign_scopes(
            self.ws, {"tasks": self.tasks}, **args)
        return receipt, created, registered

    def test_scope_assignment_runs_disjoint_scopes_concurrently_and_serializes_overlap(self):
        receipt, created, registered = self._assign()

        self.assertEqual([row["task_id"] for row in receipt["assignments"]],
                         ["t-a", "t-b"])
        self.assertEqual(receipt["serialized"], [{
            "task_id": "t-a-next", "blocked_by": "t-a",
            "reason": "scope_overlap",
        }])
        self.assertTrue(receipt["dispatch_set"]["concurrent"])
        self.assertEqual(receipt["dispatch_set"]["member_count"], 2)
        self.assertEqual(receipt["wait_invocation"]["operation"],
                         "wait_for_events")
        self.assertFalse(receipt["wait_policy"]["scheduled_polling"])
        self.assertEqual(receipt["wait_policy"]["timeout_seconds"], 1800)
        self.assertEqual([row[0] for row in created], ["t-a", "t-b"])
        self.assertEqual([row[0] for row in registered], ["t-a", "t-b"])

        encoded = json.dumps(receipt, sort_keys=True).lower()
        for forbidden in ("wave", "claim", "build_lease", "build-lease",
                          "slot_lease", "lens_state", "evaluate", "fix"):
            self.assertNotIn(forbidden, encoded)

    def test_scope_assignment_uses_real_repository_and_storage_edges(self):
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.ws,
            text=True).strip()

        receipt = build_c.assign_scopes(
            self.ws, {"tasks": [{
                "id": "t-live", "scope": ["src/a/**"], "deps": [],
                "status": "pending",
            }]}, graph=self.graph, revision=revision)

        assignment = receipt["assignments"][0]
        self.assertTrue(os.path.isdir(assignment["worktree"]))
        self.assertEqual(subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=assignment["worktree"],
            text=True).strip(), revision)
        persisted = runtime_storage.load_task_worktree_registration(
            self.ws, "t-live")
        self.assertEqual(persisted["path"], assignment["worktree"])
        self.assertEqual(persisted["branch_tip"], revision)

    def test_scope_assignment_fails_when_live_graph_or_registration_edge_is_severed(self):
        with unittest.mock.patch("build_c.depgraph.scope_modules",
                                 return_value=[]):
            with self.assertRaisesRegex(build_c.ScopeAssignmentError,
                                        "graph identity"):
                self._assign()

        def severed_registration(ws, worker, task_id):
            return {"schema": "taskplane.managed-task-worktree/v1",
                    "task_id": "wrong", "path": worker,
                    "branch_tip": "a" * 40}

        with self.assertRaisesRegex(build_c.ScopeAssignmentError,
                                    "registration identity"):
            self._assign(register_worktree=severed_registration)

    def test_scope_assignment_refuses_legacy_state_and_invalid_event_wait(self):
        with self.assertRaisesRegex(build_c.ScopeAssignmentError,
                                    "legacy BUILD state"):
            build_c.assign_scopes(
                self.ws, {"tasks": self.tasks, "wave": {"id": "old"}},
                graph=self.graph, create_worktree=lambda *args: "unused",
                register_worktree=lambda *args: {}, revision="a" * 40)

        with self.assertRaisesRegex(build_c.ScopeAssignmentError,
                                    "event wait"):
            self._assign(wait_policy_factory=lambda *_: {
                "schema": "taskplane.wait-policy/v1", "mode": "poll",
                "scheduled_polling": True, "timeout_seconds": 1,
            })


class TestIntegrationAuthorization(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = git_ws(self.tmp, [TASK])
        self.task_id = "t-live"
        self.scope = ["src/todo/a.py"]
        self.worker = runtime_storage.task_worktree_path(
            self.ws, self.task_id)
        os.makedirs(os.path.dirname(self.worker), exist_ok=True)
        subprocess.run([
            "git", "worktree", "add", "-q", "-b", "tp/t-live",
            self.worker, "HEAD",
        ], cwd=self.ws, check=True)
        with open(os.path.join(self.worker, "src", "todo", "a.py"), "a",
                  encoding="utf-8") as stream:
            stream.write("y=2\n")
        subprocess.run(["git", "add", "src/todo/a.py"], cwd=self.worker,
                       check=True)
        subprocess.run([
            "git", "-c", "user.email=e@e", "-c", "user.name=t",
            "commit", "-qm", "green task",
        ], cwd=self.worker, check=True)
        self.tip = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.worker,
            text=True).strip()
        runtime_storage.register_task_worktree(
            self.ws, self.worker, self.task_id)
        self.contract = {
            "task_id": "contract-live", "task": "integration fixture",
            "allowed_tools": ["Read"],
        }
        tp.activate(self.worker, dict(self.contract), snapshot=None)
        self.receipt = self._receipt()
        self._save_state(self.receipt)
        self.primary_before = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.ws,
            text=True).strip()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    @staticmethod
    def _digest(value):
        material = {key: item for key, item in value.items()
                    if key != "receipt_digest"}
        return hashlib.sha256(json.dumps(
            material, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")).hexdigest()

    def _receipt(self, **updates):
        active = tp.load_active(self.worker)
        receipt = {
            "schema": checkpoint.CHECKPOINT_RECEIPT_SCHEMA,
            "producer": "taskplane.checkpoint-engine/v1",
            "engine_fingerprint": "e" * 64,
            "active_contract_fingerprint": hashlib.sha256(json.dumps(
                active, sort_keys=True, separators=(",", ":"), default=str
            ).encode("utf-8")).hexdigest(),
            "identity": {
                "run_id": "legacy", "task_id": self.task_id,
                "checkpoint_id": "cp-live", "ac_ids": ["AC-live"],
            },
            "phase": "build",
            "ordered_phases": list(checkpoint.ORDERED_CHECKPOINT_PHASES),
            "completed_phases": ["focused_proof"],
            "command": {}, "environment_fingerprint": "f" * 64,
            "output": {}, "result": {"state": "succeeded", "exit_code": 0},
            "worktree_revision": self.tip,
            "declared_scope": list(self.scope),
            "predecessor_receipt_digests": [], "verdict": "green",
        }
        receipt.update(updates)
        receipt["receipt_digest"] = self._digest(receipt)
        return receipt

    def _save_state(self, receipt=None, *, task_updates=None, deps=None):
        task = {
            "id": self.task_id, "scope": list(self.scope),
            "deps": list(deps or []), "status": "running",
            "workspace": self.worker,
        }
        if receipt is not None:
            task["_submission"] = {
                "task": self.task_id, "outcome": "pass",
                "checkpoint_receipt": receipt,
            }
        task.update(task_updates or {})
        loop.save(self.ws, {"step": "execute", "tasks": [task]})

    def test_integration_authorization_merges_only_engine_green_exact_sha(self):
        predecessor_digest = "a" * 64
        receipt = self._receipt(
            predecessor_receipt_digests=[predecessor_digest])
        dependency = {
            "id": "dep", "status": "integrated",
            "integration_authorization": {
                "checkpoint_receipt_digest": predecessor_digest,
                "authorized_revision": "b" * 40,
            },
        }
        task = {
            "id": self.task_id, "scope": list(self.scope), "deps": ["dep"],
            "status": "running", "workspace": self.worker,
            "_submission": {"task": self.task_id, "outcome": "pass",
                            "checkpoint_receipt": receipt},
        }
        loop.save(self.ws, {"step": "execute", "tasks": [dependency, task]})
        authorized = build_c.integrate_on_green(self.ws, self.task_id)

        self.assertEqual(authorized["status"], "integrated")
        self.assertEqual(authorized["authorized_revision"], self.tip)
        self.assertEqual(authorized["checkpoint_receipt_digest"],
                         receipt["receipt_digest"])
        self.assertEqual(authorized["predecessor_receipt_digests"],
                         [predecessor_digest])
        self.assertEqual(authorized["merge_receipt"]["branch_tip"], self.tip)
        self.assertEqual(subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.ws,
            text=True).strip(), self.tip)

    def test_integration_authorization_rejects_missing_red_stale_mixed_and_branch_tip_only(self):
        mutations = []
        red = self._receipt(verdict="red")
        mutations.append((red, {}, "green checkpoint"))
        stale = self._receipt(worktree_revision=self.primary_before)
        mutations.append((stale, {}, "registered worktree tip"))
        mixed = self._receipt(identity={
            "run_id": "legacy", "task_id": "other",
            "checkpoint_id": "cp-live", "ac_ids": ["AC-live"],
        })
        mutations.append((mixed, {}, "task identity"))
        mismatched_scope = self._receipt(declared_scope=["src/other.py"])
        mutations.append((mismatched_scope, {}, "declared scope"))
        caller_authored = dict(self.receipt, caller_verdict="green")
        caller_authored["receipt_digest"] = self._digest(caller_authored)
        mutations.append((caller_authored, {}, "caller-authored"))
        mutations.append((None, {"target_commit": self.tip},
                          "engine checkpoint receipt"))

        for receipt, task_updates, message in mutations:
            with self.subTest(message=message):
                self._save_state(receipt, task_updates=task_updates)
                with self.assertRaisesRegex(
                        build_c.IntegrationAuthorizationError, message):
                    build_c.integrate_on_green(self.ws, self.task_id)
                self.assertEqual(subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=self.ws,
                    text=True).strip(), self.primary_before)

    def test_integration_authorization_requires_exact_green_predecessors(self):
        predecessor_digest = "a" * 64
        receipt = self._receipt(
            predecessor_receipt_digests=[predecessor_digest])
        dependency = {
            "id": "dep", "status": "failed",
            "integration_authorization": {
                "checkpoint_receipt_digest": predecessor_digest,
                "authorized_revision": "b" * 40,
            },
        }
        task = {
            "id": self.task_id, "scope": list(self.scope), "deps": ["dep"],
            "status": "running", "workspace": self.worker,
            "_submission": {"task": self.task_id, "outcome": "pass",
                            "checkpoint_receipt": receipt},
        }
        loop.save(self.ws, {"step": "execute", "tasks": [dependency, task]})
        with self.assertRaisesRegex(build_c.IntegrationAuthorizationError,
                                    "predecessor dep is not green"):
            build_c.integrate_on_green(self.ws, self.task_id)
        self.assertEqual(subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.ws,
            text=True).strip(), self.primary_before)

    def test_merge_on_green_severed_repository_edge_fails_closed(self):
        with unittest.mock.patch(
                "build_c.repository.RepositoryManager.merge_registered_task",
                side_effect=RuntimeError("severed integration edge")):
            with self.assertRaisesRegex(build_c.IntegrationAuthorizationError,
                                        "severed integration edge"):
                build_c.integrate_on_green(self.ws, self.task_id)
        self.assertEqual(subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.ws,
            text=True).strip(), self.primary_before)

class TestBuildCCheckpointSpec(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = git_ws(self.tmp, [TASK])
        self.proof = "taskplane/tests/test_focused.py"
        proof = os.path.join(self.ws, self.proof)
        os.makedirs(os.path.dirname(proof), exist_ok=True)
        with open(proof, "w", encoding="utf-8") as stream:
            stream.write("def test_focused():\n    assert True\n")
        subprocess.run(["git", "add", self.proof], cwd=self.ws, check=True)
        subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                        "commit", "-qm", "focused proof"], cwd=self.ws,
                       check=True)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _spec(self, **updates):
        value = {
            "schema": checkpoint.CHECKPOINT_SCHEMA,
            "checkpoint_id": "cp-r0010-ac-1",
            "phase": "build",
            "ac_ids": ["AC-1"],
            "predecessor_checkpoint_ids": [],
            "worktree_revision": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=self.ws,
                text=True).strip(),
            "declared_scope": ["taskplane/checkpoint.py", "taskplane/tests/**"],
            "focused_proof": {
                "path": self.proof,
                "argv": ["python3", "-m", "pytest", "-q", self.proof],
            },
            "ratchet_baseline": {"cycle_count": 0},
        }
        value.update(updates)
        return value

    def test_build_c_checkpoint_preflight_binds_tracked_proof_and_phase_order(self):
        validated = checkpoint.validate_checkpoint_spec(self.ws, self._spec())
        self.assertEqual(validated["focused_proof"]["path"], self.proof)
        self.assertEqual(validated["ordered_phases"], [
            "compile_import", "focused_proof", "forbidden_state_counts",
            "ratchet_delta", "engineering_judgment",
        ])

        with open(os.path.join(self.ws, self.proof), "a",
                  encoding="utf-8") as stream:
            stream.write("# changed after the named revision\n")
        with self.assertRaisesRegex(checkpoint.CheckpointSpecError,
                                    "exact HEAD"):
            checkpoint.validate_checkpoint_spec(self.ws, self._spec())

    def test_build_c_checkpoint_refuses_missing_or_untracked_focused_proof(self):
        missing = self._spec(focused_proof={
            "path": "taskplane/tests/missing.py",
            "argv": ["python3", "-m", "pytest", "-q",
                     "taskplane/tests/missing.py"],
        })
        with self.assertRaisesRegex(checkpoint.CheckpointSpecError,
                                    "taskplane/tests/missing.py"):
            checkpoint.validate_checkpoint_spec(self.ws, missing)

        untracked_path = os.path.join(self.ws, "taskplane", "tests",
                                      "untracked.py")
        with open(untracked_path, "w", encoding="utf-8") as stream:
            stream.write("def test_untracked():\n    assert True\n")
        untracked = self._spec(
            declared_scope=["taskplane/checkpoint.py",
                            "taskplane/tests/untracked.py"],
            focused_proof={
                "path": "taskplane/tests/untracked.py",
                "argv": ["python3", "-m", "pytest", "-q",
                         "taskplane/tests/untracked.py"],
            })
        with self.assertRaisesRegex(checkpoint.CheckpointSpecError,
                                    "tracked regular file"):
            checkpoint.validate_checkpoint_spec(self.ws, untracked)


class TestSubmitCheckpointWiring(unittest.TestCase):
    def test_submit_checkpoint_edges_are_mutation_sensitive(self):
        source = open(loop.__file__, encoding="utf-8").read()
        start = source.index("def _run_submit_checkpoint(")
        end = source.index("\ndef submit(", start)
        body = source[start:end]
        preflight = body.index("checkpoint.validate_checkpoint_spec(")
        launch = body.index('governed_commands.execute(act_ws, "launch"')
        wait = body.index('governed_commands.execute(act_ws, "wait"')
        receipt = body.index("checkpoint.validate_and_mint(")
        self.assertLess(preflight, launch)
        self.assertLess(launch, wait)
        self.assertLess(wait, receipt)

        submit_start = source.index("def submit(")
        submit_end = source.index("\ndef _submission_staleness(", submit_start)
        submit_body = source[submit_start:submit_end]
        self.assertIn("_run_submit_checkpoint(", submit_body)
        self.assertIn('submission["checkpoint_receipt"] = checkpoint_receipt',
                      submit_body)


class TestClosedGapPlan(unittest.TestCase):
    def _tasks(self):
        return [
            {"id": f"t0{i + 2}-r0010-gap", "scope": [f"taskplane/g{i}.py"],
             "gap_category": category}
            for i, category in enumerate(checkpoint.CLOSED_GAP_CATEGORIES)
        ]

    def test_closed_gap_plan_accepts_each_of_the_six_categories_once(self):
        verdict = checkpoint.validate_closed_gap_plan(self._tasks())
        self.assertTrue(verdict["passed"], verdict)
        self.assertFalse(verdict["scope_decision_required"])
        self.assertEqual(verdict["categories"],
                         list(checkpoint.CLOSED_GAP_CATEGORIES))

    def test_closed_gap_plan_rejects_missing_duplicate_and_seventh_category(self):
        missing = self._tasks()
        missing[0].pop("gap_category")
        self.assertFalse(checkpoint.validate_closed_gap_plan(missing)["passed"])

        duplicate = self._tasks()
        duplicate[-1]["gap_category"] = duplicate[0]["gap_category"]
        duplicate_verdict = checkpoint.validate_closed_gap_plan(duplicate)
        self.assertFalse(duplicate_verdict["passed"])
        self.assertIn("duplicate", " ".join(duplicate_verdict["errors"]))

        seventh = self._tasks()
        seventh.append({"id": "t09-r0010-gap", "scope": ["taskplane/g6.py"],
                        "gap_category": "seventh-category"})
        seventh_verdict = checkpoint.validate_closed_gap_plan(seventh)
        self.assertFalse(seventh_verdict["passed"])
        self.assertTrue(seventh_verdict["scope_decision_required"])
        self.assertIn("seventh-category", " ".join(seventh_verdict["errors"]))


def submit_gate(ws, outcome="pass", task_id=None):
    submitted = loop.submit(ws, outcome, task_id=task_id)
    if "error" in submitted:
        return submitted
    return loop.gate(ws, outcome, task_id=task_id)


def write_verdict(ws):
    state = loop.load(ws)
    task = state["tasks"][state["current_task"]]
    act_ws = task.get("workspace") or ws
    routed = lens.route_git_diff(
        act_ws, base=state.get("baseline") or "HEAD",
        task_type=task.get("type"), breadth="routed")
    criteria = loop._criteria_for(ws, state, task)
    os.makedirs(os.path.join(act_ws, ".eval"), exist_ok=True)
    graph_dod = loop._task_graph_dod(ws, state, task)
    impact = graph_dod.get("impact") or {}
    direct = sorted({e.get("module")
                     for e in (impact.get("impacted") or {}).get(1, [])
                     if e.get("module") and
                     not str(e.get("module")).startswith("req:")})
    prod = depgraph.product_impact(
        ws, graph_dod.get("realized_modules") or [])
    own = task.get("req") or state.get("requirement_id")
    own = depgraph._req_node(own) if own else None
    affected = [r for r in prod.get("affected_requirements") or []
                if r != own]
    contracts = [c.get("id") if isinstance(c, dict) else c
                 for c in (task.get("contracts") or [])]
    with open(os.path.join(act_ws, ".eval", "verdict.json"), "w", encoding="utf-8") as f:
        json.dump({"schema": "taskplane.evaluator-output/v1",
                   "task": task["id"],
                   "requirement": task.get("req") or
                                  state.get("requirement_id") or "",
                   "verdict": "pass",
                   "criteria": [{"criterion": c, "status": "met",
                                  "evidence": "verified by test"}
                                for c in criteria],
                   "lenses": [{"lens": x["id"], "verdict": "pass",
                               "blockers": 0} for x in routed["lenses"]],
                   "graph": {
                       "dispositions": [
                           {"node": node, "status": "tested",
                            "evidence": "covered by declared task tests"}
                           for node in direct],
                       "requirements_checked": affected,
                       "contracts_checked": contracts,
                   },
                   "failures": []}, f)


def pass_eval(ws):
    write_kernel_results(ws)
    write_verdict(ws)
    return submit_gate(ws, "pass")


def write_kernel_results(ws):
    """Author canonical leased results through the observed hook protocol."""
    loop_state = loop.load(ws)
    task = loop_state["tasks"][loop_state["current_task"]]
    review_ws = (task.get("workspace") if loop_state.get("parallel") and
                 loop_state.get("step") == "evaluate" else None) or ws
    state = review._load_state(review_ws)
    store = review_evidence.ArtifactStore(review_ws)
    for index, slot in enumerate(state["slots"]):
        lease = store.read(slot["lease"])
        brief = store.read(slot["brief"])
        payload = {
            **lease,
            "schema": "taskplane.lens-slot-output/v2",
            "authored_by": "lens-slot",
            "lens_results": [
                {"lens": lens_id, "verdict": "pass", "blockers": 0,
                 "checked_evidence": [{
                     "file": "src/todo/a.py", "line": 1,
                     "claim": "declared happy-path fixture inspected"}]}
                for lens_id in lease["lens_ids"]
            ],
            "findings": [],
        }
        content = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        event = {
            "session_id": "loop-em-session",
            "agent_id": f"loop-em-child-{index}",
            "tool_name": "Write",
            "tool_input": {"file_path": slot["result_path"],
                           "content": content},
        }
        contract = {
            "task": brief["producer_contract"]["task"],
            "task_id": f"loop-em-contract-{index}",
            "read_only": True,
            "write_allow": [slot["result_path"]],
        }
        review.register_slot_producer(
            review_ws, event=event, contract=contract,
            task_slot=brief["producer_contract"]["task_slot"])
        review.record_slot_write_observation(
            review_ws, event=event, contract=contract,
            task_slot=brief["producer_contract"]["task_slot"])
        path = os.path.join(review_ws, slot["result_path"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(content)
    return review.collect_review(review_ws, publish=False,
                                 run_id=state["run_id"])


def pass_em(ws):
    canonical = write_kernel_results(ws)
    coverage = {x["id"]: "sweep" for x in lens.load_catalog()["lenses"]}
    os.makedirs(os.path.join(ws, ".em-review"), exist_ok=True)
    with open(os.path.join(ws, ".em-review", "report.md"), "w", encoding="utf-8") as f:
        f.write("# Engineering review\n\nAll required evidence passed.\n")
    state = loop.load(ws)
    changed = [f for f in loop._diff_files(
        ws, state.get("baseline") or "HEAD")
        if not f.startswith(lens.LOOP_OWNED)]
    impact = depgraph.impact(ws, changed)
    with open(os.path.join(ws, ".em-review", "findings.json"), "w", encoding="utf-8") as f:
        identity = {key: canonical[key] for key in (
            "target_fingerprint", "context_fingerprint",
            "findings_fingerprint", "canonical_revision")}
        json.dump({"meta": {**identity,
                            "lens_coverage": coverage, "impact": impact,
                            "tests": ["true"],
                            "gate": {"verdict": "recommend-pass"}},
                   "findings": []}, f)
    return submit_gate(ws, "pass")


class TestLoop(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_free_text_goal_starts_at_pm(self):
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "add complete()")
        self.assertEqual(loop.load(ws)["step"], "pm")

    def _setup_stateless_review_contract(self):
        self.ws = git_ws(self.tmp, [TASK])
        self.lease = {
            "schema": "taskplane.slot-lease/v1",
            "slot_id": "deep.security",
            "lens_ids": ["security"],
            "target_fingerprint": "1" * 64,
            "context_fingerprint": "2" * 64,
            "view_fingerprint": "3" * 64,
            "canonical_revision": 4,
            "lease_fingerprint": "4" * 64,
        }
        self.producer = {
            "task": "review lens slot deep.security lease " + "4" * 64,
            "task_slot": "review-" + "4" * 20,
            "read_only": True,
            "write_allow": [
                ".eval/kernel-v2/results/" + "4" * 64 + ".json"
            ],
        }
        self.bindings = {
            "run_id": "run-review-4",
            "task_id": "t1",
            "role_marker": "taskplane-role:tp-security",
            "worker_identity": "tp_lens_security_44444444",
            "action_id": "review-action-4",
            "lens_ids": ["security"],
            "target_fingerprint": "1" * 64,
            "lease_fingerprint": "4" * 64,
            "canonical_revision": 4,
        }

    def _issue(self):
        return tp.issue_review_contract_action(
            self.ws, lease=self.lease, producer_contract=self.producer,
            result_path=self.producer["write_allow"][0], now=100,
            ttl_seconds=60, **{
                key: self.bindings[key] for key in (
                    "run_id", "task_id", "role_marker", "worker_identity",
                    "action_id")
            })

    def _activate(self, action, **overrides):
        expected = dict(self.bindings)
        expected.update(overrides)
        with unittest.mock.patch.dict(
                os.environ,
                {"TASKPLANE_TASK": self.producer["task_slot"]}):
            return tp.activate_review_contract_action(
                self.ws, action, now=101, **expected)

    def _assert_fresh_worker_activates_without_hook_or_active_file(self):
        self._setup_stateless_review_contract()
        action = self._issue()
        active_path = tp.active_contract_path(
            self.ws, self.producer["task_slot"])
        self.assertFalse(os.path.exists(active_path))

        contract = self._activate(action)

        self.assertTrue(contract["read_only"])
        self.assertEqual(contract["write_allow"],
                         self.producer["write_allow"])
        self.assertEqual(tp.load_json(active_path)["bootstrap_action_id"],
                         self.bindings["action_id"])
        allowed, _ = tp.screen_tool(
            contract, "Write", {"file_path": self.producer["write_allow"][0]},
            self.ws)
        refused, _ = tp.screen_tool(
            contract, "Write", {"file_path": "taskplane/loop.py"}, self.ws)
        self.assertTrue(allowed)
        self.assertFalse(refused)

    def _assert_tamper_stale_identity_replay_and_write_broadening_fail_closed(self):
        self._setup_stateless_review_contract()
        action = self._issue()
        broad_producer = dict(self.producer, write_allow=["taskplane/loop.py"])
        with self.assertRaises(tp.StateError):
            tp.issue_review_contract_action(
                self.ws, lease=self.lease,
                producer_contract=broad_producer,
                result_path="taskplane/loop.py", now=100,
                ttl_seconds=60, **{
                    key: self.bindings[key] for key in (
                        "run_id", "task_id", "role_marker",
                        "worker_identity", "action_id")
                })
        cases = []
        altered = json.loads(json.dumps(action))
        altered["task_id"] = "other-task"
        cases.append((altered, {}, 101))
        forged = json.loads(json.dumps(action))
        forged["signature"] = "0" * 64
        cases.append((forged, {}, 101))
        unsigned = json.loads(json.dumps(action))
        unsigned.pop("signature")
        cases.append((unsigned, {}, 101))
        broadened = json.loads(json.dumps(action))
        broadened["producer_contract"]["write_allow"].append("taskplane/**")
        cases.append((broadened, {}, 101))
        malformed = json.loads(json.dumps(action))
        malformed["schema"] = "taskplane.review-contract-action/v0"
        cases.append((malformed, {}, 101))
        cases.append((action, {}, 161))
        cases.append((action, {"run_id": "another-run"}, 101))
        cases.append((action, {"task_id": "another-task"}, 101))
        cases.append((action, {"worker_identity": "another_worker"}, 101))
        cases.append((action, {"role_marker": "taskplane-role:tp-qa"}, 101))
        cases.append((action, {"lens_ids": ["qa"]}, 101))
        cases.append((action, {"target_fingerprint": "9" * 64}, 101))
        cases.append((action, {"lease_fingerprint": "8" * 64}, 101))
        cases.append((action, {"canonical_revision": 5}, 101))
        cases.append((action, {"action_id": "other-action"}, 101))

        for candidate, overrides, now in cases:
            with self.subTest(overrides=overrides, now=now,
                              schema=candidate.get("schema")):
                expected = dict(self.bindings)
                expected.update(overrides)
                with unittest.mock.patch.dict(
                        os.environ,
                        {"TASKPLANE_TASK": self.producer["task_slot"]}):
                    with self.assertRaises(tp.StateError):
                        tp.activate_review_contract_action(
                            self.ws, candidate, now=now, **expected)
        with unittest.mock.patch.dict(
                os.environ, {"TASKPLANE_TASK": "review-wrong-worker"}):
            with self.assertRaises(tp.StateError):
                tp.activate_review_contract_action(
                    self.ws, action, now=101, **self.bindings)

    def _assert_loop_binds_worker_action_to_each_immutable_review_slot(self):
        self._setup_stateless_review_contract()
        import review_evidence

        store = review_evidence.ArtifactStore(self.ws)
        lease_ref = store.put("lease", self.lease)
        brief_ref = store.put("lens-brief", {
            "schema": "taskplane.lens-brief/v2",
            "lease": lease_ref,
            "result_path": self.producer["write_allow"][0],
            "producer_contract": self.producer,
            "role": {
                "role_marker": self.bindings["role_marker"],
                "task_name": self.bindings["worker_identity"],
            },
        })
        manifest = {
            "schema": "taskplane.review-start-manifest/v2",
            "status": "ready", "run_id": self.bindings["run_id"],
            "target_fingerprint": self.bindings["target_fingerprint"],
            "slots": [{
                "slot_id": self.lease["slot_id"],
                "lens_ids": self.lease["lens_ids"],
                "lease": lease_ref, "brief": brief_ref,
                "result_path": self.producer["write_allow"][0],
            }],
        }

        bound = loop._bind_stateless_review_contract_actions(
            self.ws, manifest, task_id="t1", now=100)

        bootstrap = bound["slots"][0]["contract_bootstrap"]
        self.assertEqual(bootstrap["schema"],
                         "taskplane.review-contract-bootstrap/v1")
        self.assertEqual(bootstrap["environment"]["TASKPLANE_TASK"],
                         self.producer["task_slot"])
        self.assertEqual(bootstrap["action"]["lease_identity"]["lens_ids"],
                         ["security"])
        self.assertNotIn("conversation", json.dumps(bootstrap).lower())
        self.assertFalse(os.path.exists(tp.active_contract_path(
            self.ws, self.producer["task_slot"])))

    def _assert_managed_worktree_result_is_exact_and_fail_closed(self):
        """Parallel Evaluate binds only this worktree's leased result."""
        self._setup_stateless_review_contract()
        import review_evidence

        fingerprint = self.lease["lease_fingerprint"]
        run_root = os.path.join(self.tmp, "run")
        lenses_root = os.path.join(
            run_root, "lenses", "worktrees", "t1-worktree-key")
        paths = {
            "state": os.path.join(run_root, "state", "worktrees", "t1"),
            "graph": os.path.join(run_root, "graph", "worktrees", "t1"),
            "evidence": os.path.join(
                run_root, "evidence", "worktrees", "t1"),
            "lenses": lenses_root,
            "artifacts": os.path.join(
                run_root, "artifacts", "worktrees", "t1"),
        }
        locator = {"paths": paths}
        canonical = os.path.join(
            lenses_root, "results", f"{fingerprint}.json")
        self.producer["write_allow"] = [canonical]

        with unittest.mock.patch(
                "storage.load_workspace_locator", return_value=locator):
            action = self._issue()
            contract = self._activate(action)
            self.assertEqual(contract["write_allow"], [canonical])

            store = review_evidence.ArtifactStore(self.ws)
            lease_ref = store.put("lease", self.lease)
            brief_ref = store.put("lens-brief", {
                "schema": "taskplane.lens-brief/v2",
                "lease": lease_ref,
                "result_path": canonical,
                "producer_contract": self.producer,
                "role": {
                    "role_marker": self.bindings["role_marker"],
                    "task_name": self.bindings["worker_identity"],
                },
            })
            bound = loop._bind_stateless_review_contract_actions(
                self.ws, {
                    "schema": "taskplane.review-start-manifest/v2",
                    "status": "ready",
                    "run_id": self.bindings["run_id"],
                    "target_fingerprint":
                        self.bindings["target_fingerprint"],
                    "slots": [{
                        "slot_id": self.lease["slot_id"],
                        "lens_ids": self.lease["lens_ids"],
                        "lease": lease_ref,
                        "brief": brief_ref,
                        "result_path": canonical,
                    }],
                }, task_id=self.bindings["task_id"], now=100)
            self.assertEqual(
                bound["slots"][0]["contract_bootstrap"]["action"]
                     ["result_path"], canonical)

            invalid_paths = {
                "sibling": os.path.join(
                    run_root, "lenses", "worktrees", "sibling", "results",
                    f"{fingerprint}.json"),
                "forged_lease": os.path.join(
                    lenses_root, "results", f"{'9' * 64}.json"),
                "broadened": os.path.join(
                    lenses_root, "results", "nested",
                    f"{fingerprint}.json"),
                "shared_parent": os.path.join(
                    run_root, "lenses", "results",
                    f"{fingerprint}.json"),
            }
            for name, result_path in invalid_paths.items():
                with self.subTest(name=name):
                    producer = dict(
                        self.producer, write_allow=[result_path])
                    with self.assertRaises(tp.StateError):
                        tp.issue_review_contract_action(
                            self.ws, lease=self.lease,
                            producer_contract=producer,
                            result_path=result_path, now=100,
                            ttl_seconds=60, **{
                                key: self.bindings[key] for key in (
                                    "run_id", "task_id", "role_marker",
                                    "worker_identity", "action_id")
                            })

    def test_existing_spec_skips_pm(self):
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "g", spec_path="specs/spec.md")
        self.assertEqual(loop.load(ws)["step"], "plan")

    def _gate_evaluator_unavailable(self):
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "g", spec_path="specs/spec.md")
        loop.next_action(ws)
        loop.gate(ws, "pass")
        loop.approve(ws, "plan")
        loop.next_action(ws)
        with open(os.path.join(ws, "src", "todo", "a.py"), "a",
                  encoding="utf-8") as stream:
            stream.write("\ndef complete():\n    return True\n")
        with unittest.mock.patch(
                "runtime_eval.guide_loop",
                return_value={"status": "on_path", "recovered": False}):
            submit_gate(ws, "pass")
        write_verdict(ws)
        path = os.path.join(ws, ".eval", "verdict.json")
        with open(path, encoding="utf-8") as stream:
            verdict = json.load(stream)
        verdict["evaluation"] = {
            "status": "unavailable",
            "reason_code": "agent_timeout",
            "detail": "bounded evaluator attempt 7 timed out on host alpha",
        }
        verdict["verdict"] = "fail"
        verdict["lenses"] = []
        verdict["failures"] = [{
            "what": "independent evaluator did not return a judgment",
            "repro": "dispatch attempt 7 on host alpha",
            "where": "host:alpha/evaluator:independent",
        }]
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(verdict, stream)
        with unittest.mock.patch(
                "runtime_eval.guide_loop",
                return_value={"status": "on_path", "recovered": False}):
            result = submit_gate(ws, "unavailable")
        return ws, verdict, result

    def test_evaluator_unavailable_remains_non_judged_and_keeps_readiness_closed(self):
        ws, _, result = self._gate_evaluator_unavailable()
        self.assertNotIn("error", result)
        state = loop.load(ws)
        task = state["tasks"][0]
        self.assertEqual(task["status"], "unavailable")
        self.assertNotIn(task["status"], loop.SETTLED)
        self.assertEqual(task["fix_cycles"], 0)
        self.assertEqual(state["step"], "escalated")

    def test_unavailable_warning_preserves_exact_outage_identity_without_pass_verdict(self):
        ws, verdict, result = self._gate_evaluator_unavailable()
        state = loop.load(ws)
        warning = state["evaluation_warnings"][0]
        identity = warning["outage_identity"]
        self.assertEqual(result["warning"], warning)
        self.assertEqual(warning["verdict"], "non-judged")
        self.assertEqual(identity["task"], verdict["task"])
        self.assertEqual(identity["requirement"], verdict["requirement"])
        self.assertEqual(identity["evaluation"], verdict["evaluation"])
        self.assertEqual(identity["failures"], verdict["failures"])
        self.assertEqual(identity, evaluator_health.outage_identity(
            task=verdict["task"], requirement=verdict["requirement"],
            evaluation=verdict["evaluation"], failures=verdict["failures"]))
        self.assertNotEqual(state["tasks"][0]["status"], "passed")

    def test_evaluator_unavailable_retry_returns_to_evaluate_without_opening_fix(self):
        ws, verdict, _ = self._gate_evaluator_unavailable()

        result = loop.resolve(ws, "retry")

        self.assertEqual(result["step"], "evaluate")
        state = loop.load(ws)
        task = state["tasks"][0]
        self.assertEqual(state["step"], "evaluate")
        self.assertEqual(task["status"], "running")
        self.assertEqual(task["fix_cycles"], 0)
        self.assertEqual(task["evaluation"]["verdict"], "non-judged")
        self.assertEqual(
            task["evaluation"]["outage_identity"]["evaluation"],
            verdict["evaluation"])

    def test_human_can_accept_met_criteria_during_orchestration_outage(self):
        ws, _, _ = self._gate_evaluator_unavailable()
        state = loop.load(ws)
        task = state["tasks"][0]
        task["evaluation"]["reason_code"] = "orchestration_unavailable"
        task["evaluation"]["outage_identity"]["evaluation"][
            "reason_code"] = "orchestration_unavailable"
        verdict_path = os.path.join(ws, ".eval", "verdict.json")
        with open(verdict_path, encoding="utf-8") as stream:
            verdict = json.load(stream)
        verdict["evaluation"]["reason_code"] = "orchestration_unavailable"
        with open(verdict_path, "w", encoding="utf-8") as stream:
            json.dump(verdict, stream)
        loop.save(ws, state)

        result = loop.resolve(ws, "pass")

        self.assertNotIn("error", result)
        accepted = loop.load(ws)["tasks"][0]
        self.assertEqual(accepted["status"], "passed")
        self.assertEqual(accepted["human_resolution"]["decision"], "pass")

    def test_human_pass_refuses_unmet_criteria(self):
        ws, _, _ = self._gate_evaluator_unavailable()
        state = loop.load(ws)
        task = state["tasks"][0]
        task["evaluation"]["reason_code"] = "orchestration_unavailable"
        task["evaluation"]["outage_identity"]["evaluation"][
            "reason_code"] = "orchestration_unavailable"
        verdict_path = os.path.join(ws, ".eval", "verdict.json")
        with open(verdict_path, encoding="utf-8") as stream:
            verdict = json.load(stream)
        verdict["evaluation"]["reason_code"] = "orchestration_unavailable"
        verdict["criteria"][0]["status"] = "not-met"
        with open(verdict_path, "w", encoding="utf-8") as stream:
            json.dump(verdict, stream)
        loop.save(ws, state)

        result = loop.resolve(ws, "pass")

        self.assertIn("error", result)
        self.assertEqual(loop.load(ws)["tasks"][0]["status"], "unavailable")

    def test_next_activates_contract_gate_clears(self):
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "g", spec_path="specs/spec.md")
        act = loop.next_action(ws)
        self.assertEqual(act["role"], "tp-planner")
        self.assertTrue(act["contract"]["read_only"])
        self.assertIsNotNone(tp.load_active(ws))          # activated
        loop.gate(ws, "pass")
        self.assertIsNone(tp.load_active(ws))             # cleared

    def test_plan_gate_fails_closed_on_phantom_plan(self):
        """A planner CLAIMING a plan is nothing: if plan/tasks.json is
        missing or empty, the plan gate must refuse to advance — the exact
        hallucinated-completion failure the ungoverned control run showed."""
        ws = git_ws(self.tmp, [TASK])
        os.remove(os.path.join(ws, "plan", "tasks.json"))   # phantom plan
        loop.init(ws, "g", spec_path="specs/spec.md")       # → plan
        loop.next_action(ws)
        r = loop.gate(ws, "pass")
        self.assertIn("error", r)
        self.assertIn("plan/tasks.json", r["error"])
        self.assertEqual(loop.load(ws)["step"], "plan")     # did NOT advance
        # writing a real plan unblocks the same gate
        json.dump({"tasks": [TASK]},
                  open(os.path.join(ws, "plan", "tasks.json"), "w", encoding="utf-8"))
        loop.next_action(ws)
        r = loop.gate(ws, "pass")
        self.assertNotIn("error", r)
        self.assertEqual(loop.load(ws)["step"], "plan_approval")

    def test_plan_gate_rejects_ambiguous_test_lists_before_approval(self):
        bad_values = (["tests/test_cart.py"],
                      ["python3 -m pytest tests/ -q"])
        for tests in bad_values:
            with self.subTest(tests=tests):
                ws = git_ws(tempfile.mkdtemp(), [dict(TASK, tests=tests)])
                loop.init(ws, "g", spec_path="specs/spec.md")
                loop.next_action(ws)
                out = loop.gate(ws, "pass")
                self.assertIn("error", out)
                self.assertIn("one command string", str(out))
                self.assertIn("python3 -m pytest", str(out))
                self.assertEqual(loop.load(ws)["step"], "plan")

    def test_replan_preserves_history_and_requires_fresh_approval(self):
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "g", spec_path="specs/spec.md", checkpoints=[])
        loop.next_action(ws)
        self.assertEqual(loop.gate(ws, "pass")["step"], "execute")

        refused = loop.replan(ws, by="", reason="invalid test command")
        self.assertIn("--by", refused["error"])
        self.assertEqual(loop.load(ws)["step"], "execute")

        out = loop.replan(ws, by="user", reason="invalid test command")
        self.assertEqual(out["step"], "plan")
        state = loop.load(ws)
        self.assertIsNone(state["tasks"])
        self.assertIn("plan", state["checkpoints"])
        self.assertEqual(state["replan_history"][-1]["from_step"],
                         "execute")
        self.assertEqual(state["replan_history"][-1]["tasks"][0]["id"],
                         "t1")

        # The corrected plan is reloaded and cannot bypass fresh human
        # approval even though the original loop had no plan checkpoint.
        loop.next_action(ws)
        self.assertEqual(loop.gate(ws, "pass")["step"], "plan_approval")
        self.assertEqual(loop.approve(ws, by="user")["step"], "execute")

    def test_define_projection_plan_gate_names_every_task_without_explicit_criteria(self):
        missing = dict(TASK)
        missing["id"] = "missing-criteria"
        missing.pop("criteria")
        empty = dict(TASK, id="empty-criteria", criteria=["", "  "])
        ws = git_ws(self.tmp, [missing, empty])
        loop.init(ws, "g", spec_path="specs/spec.md")
        loop.next_action(ws)

        out = loop.gate(ws, "pass")

        self.assertIn("error", out)
        blockers = "\n".join(out["dor"]["blockers"])
        self.assertIn("task missing-criteria: explicit acceptance criteria",
                      blockers)
        self.assertIn("task empty-criteria: explicit acceptance criteria",
                      blockers)
        self.assertEqual(loop.load(ws)["step"], "plan")

    def test_define_projection_replan_reanchors_unchanged_passed_contract(self):
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "g", spec_path="specs/spec.md")
        loop.next_action(ws)
        self.assertEqual(loop.gate(ws, "pass")["step"], "plan_approval")
        state = loop.load(ws)
        state["tasks"][0].update({
            "status": "passed", "workspace": ws,
            "target_commit": tp.git_head(ws),
        })
        loop.save(ws, state)
        loop.replan(ws, by="user", reason="metadata-only correction")
        loop.next_action(ws)
        verified = {
            "target_commit": tp.git_head(ws),
            "evaluation_path": os.path.join(ws, ".eval", "verdict.json"),
            "evaluation_sha256": "a" * 64,
            "resolution": "independent-pass",
        }

        with unittest.mock.patch.object(
                loop, "_verify_reanchor_task_evidence",
                return_value=(verified, None)):
            out = loop.gate(ws, "pass")

        self.assertNotIn("error", out)
        self.assertEqual(out["reanchor"]["restored_count"], 1)
        self.assertEqual(out["reanchor"]["restored"][0]["task_id"], "t1")
        self.assertEqual(loop.load(ws)["tasks"][0]["status"], "passed")
        self.assertEqual(loop.load(ws)["step"], "plan_approval")

    def test_define_projection_reanchor_real_verifier_rejects_non_proof_sentinels(self):
        def verify(evidence):
            ws = git_ws(tempfile.mkdtemp(dir=self.tmp), [TASK])
            verdict_path = runtime_storage.evaluation_path(ws)
            os.makedirs(os.path.dirname(verdict_path), exist_ok=True)
            with open(verdict_path, "w", encoding="utf-8") as stream:
                json.dump({
                    "schema": "taskplane.evaluator-output/v1",
                    "task": "t1",
                    "requirement": "",
                    "verdict": "pass",
                    "criteria": [{
                        "criterion": TASK["criteria"][0],
                        "status": "met",
                        "evidence": evidence,
                    }],
                    "failures": [],
                }, stream)
            subprocess.run(
                ["git", "add", "-f", ".eval/verdict.json"], cwd=ws,
                check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "commit", "-qm", "record evaluator evidence"],
                cwd=ws, check=True, capture_output=True, text=True)
            task = dict(TASK)
            prior = dict(task, status="passed", workspace=ws,
                         target_commit=tp.git_head(ws))
            return loop._verify_reanchor_task_evidence(ws, task, prior)

        for sentinel in (False, 0):
            with self.subTest(sentinel=sentinel):
                evidence, error = verify(sentinel)
                self.assertIsNone(evidence)
                self.assertEqual(
                    error, "durable evaluator criterion is not proven met")

        evidence, error = verify("receipt: evaluator independently passed")
        self.assertIsNone(error)
        self.assertEqual(evidence["resolution"], "independent-pass")

    def test_define_projection_replan_changed_criteria_stays_pending(self):
        prior = dict(TASK, status="passed", workspace=self.tmp,
                     target_commit="a" * 40)
        current = dict(TASK, criteria=["a newly changed criterion"])
        state = {
            "tasks": [current],
            "replan_history": [{"by": "user", "reason": "metadata",
                                "ts": 1, "tasks": [prior]}],
        }

        with unittest.mock.patch.object(
                loop, "_verify_reanchor_task_evidence") as verify:
            receipt, errors = loop._reanchor_replanned_tasks(self.tmp, state)

        self.assertEqual(errors, [])
        verify.assert_not_called()
        self.assertEqual(state["tasks"][0]["status"], "pending")
        self.assertEqual(receipt["pending"][0]["reason"],
                         "immutable_contract_changed")

    def test_define_projection_replan_unverified_evidence_stays_pending(self):
        prior = dict(TASK, status="passed", workspace=self.tmp,
                     target_commit="a" * 40)
        state = {
            "tasks": [dict(TASK)],
            "replan_history": [{"by": "user", "reason": "metadata",
                                "ts": 1, "tasks": [prior]}],
        }

        with unittest.mock.patch.object(
                loop, "_verify_reanchor_task_evidence",
                return_value=(None, "durable verdict target is stale")):
            receipt, errors = loop._reanchor_replanned_tasks(self.tmp, state)

        self.assertEqual(errors, [])
        self.assertEqual(state["tasks"][0]["status"], "pending")
        self.assertEqual(receipt["pending"][0]["reason"],
                         "evidence_unverified")
        self.assertIn("stale", receipt["pending"][0]["detail"])

    def test_define_projection_replan_requires_dependency_closed_restore(self):
        prior_root = dict(TASK, id="root", criteria=["old root"],
                          status="passed", workspace=self.tmp,
                          target_commit="a" * 40)
        prior_child = dict(TASK, id="child", deps=["root"],
                           status="passed", workspace=self.tmp,
                           target_commit="b" * 40)
        current_root = dict(TASK, id="root", criteria=["changed root"])
        current_child = dict(TASK, id="child", deps=["root"])
        state = {
            "tasks": [current_root, current_child],
            "replan_history": [{"by": "user", "reason": "metadata",
                                "ts": 1,
                                "tasks": [prior_root, prior_child]}],
        }

        with unittest.mock.patch.object(
                loop, "_verify_reanchor_task_evidence",
                return_value=({"target_commit": "b" * 40,
                               "evaluation_sha256": "c" * 64}, None)):
            receipt, errors = loop._reanchor_replanned_tasks(self.tmp, state)

        self.assertEqual(errors, [])
        self.assertEqual([task["status"] for task in state["tasks"]],
                         ["pending", "pending"])
        pending = {row["task_id"]: row for row in receipt["pending"]}
        self.assertEqual(pending["root"]["reason"],
                         "immutable_contract_changed")
        self.assertEqual(pending["child"]["reason"],
                         "dependency_not_reanchored")
        self.assertEqual(pending["child"]["dependencies"], ["root"])

    def test_plan_checkpoint_then_execute(self):
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "g", spec_path="specs/spec.md")   # → plan
        loop.next_action(ws); loop.gate(ws, "pass")     # plan → plan_approval
        self.assertEqual(loop.load(ws)["step"], "plan_approval")
        act = loop.next_action(ws)
        self.assertTrue(act["paused"])                   # human gate
        loop.approve(ws)
        self.assertEqual(loop.load(ws)["step"], "execute")
        self.assertEqual(loop.load(ws)["tasks"][0]["id"], "t1")

    def test_happy_path_to_signoff(self):
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "g", spec_path="s", checkpoints=["em"])  # no plan gate
        loop.next_action(ws); loop.gate(ws, "pass")            # plan → execute
        self.assertEqual(loop.load(ws)["step"], "execute")
        loop.next_action(ws); submit_gate(ws, "pass")          # execute → evaluate
        loop.next_action(ws); pass_eval(ws)                     # evaluate → em
        self.assertEqual(loop.load(ws)["step"], "em")
        loop.next_action(ws); pass_em(ws)                       # em → signoff
        self.assertEqual(loop.load(ws)["step"], "signoff")
        approved = loop.approve(ws)                            # → retro
        self.assertEqual(loop.load(ws)["step"], "retro")
        self.assertIn("loop retro", approved["instruction"])
        loop.retro(ws)                                         # → done
        self.assertEqual(loop.load(ws)["step"], "done")

    def test_em_fail_routes_request_changes_without_signoff_evidence(self):
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "g", spec_path="s", checkpoints=["em"])
        state = loop.load(ws)
        state.update({
            "step": "em",
            "tasks": [dict(TASK, status="passed")],
            "current_task": 0,
            "baseline": tp.git_head(ws),
            "submission_required": True,
        })
        loop.save(ws, state)
        review_root = os.path.join(ws, ".em-review")
        os.makedirs(review_root, exist_ok=True)
        findings_path = os.path.join(review_root, "findings.json")
        report_path = os.path.join(review_root, "report.md")
        findings = {
            "meta": {"gate": {"verdict": "request-changes"}},
            "findings": [
                {"severity": "high", "title": f"blocker {index}"}
                for index in range(1, 5)
            ],
        }
        with open(findings_path, "w", encoding="utf-8") as stream:
            json.dump(findings, stream)
        with open(report_path, "w", encoding="utf-8") as stream:
            stream.write("# Engineering review\n\nRequest changes.\n")
        findings_before = open(findings_path, "rb").read()
        report_before = open(report_path, "rb").read()

        submitted = loop.submit(ws, "fail", note="four blocking findings")
        self.assertTrue(submitted["submitted"])
        evidence_fingerprint = submitted["submission"]["fingerprint"]
        out = loop.gate(ws, "fail")

        self.assertNotIn("error", out)
        self.assertEqual(out["step"], "escalated")
        state = loop.load(ws)
        self.assertEqual(state["step"], "escalated")
        self.assertNotIn("signoff_evidence", state)
        self.assertNotIn("signoff_dod", state)
        request_changes = state["engineering_review_request_changes"]
        submission_audit = request_changes["submission"]
        self.assertEqual(
            submission_audit["fingerprint"],
            evidence_fingerprint,
        )
        self.assertEqual(
            submission_audit["evidence_paths"],
            [findings_path, report_path],
        )
        self.assertEqual(open(findings_path, "rb").read(), findings_before)
        self.assertEqual(open(report_path, "rb").read(), report_before)
        self.assertTrue(loop.next_action(ws)["paused"])

    def test_signoff_is_bound_to_em_integration_not_later_shared_bytes(self):
        """A later commit/loop cannot make an approved EM revision fail DoD.

        This is the screenshot regression: sign-off used to re-read the
        mutable shared findings/design and current checkout, producing a
        mixed-revision list of scope, design, graph and schema failures.
        """
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "g", spec_path="s", checkpoints=["em"])
        loop.next_action(ws); loop.gate(ws, "pass")
        loop.next_action(ws); submit_gate(ws, "pass")
        loop.next_action(ws); pass_eval(ws)
        loop.next_action(ws); pass_em(ws)

        sealed = loop.load(ws)["signoff_evidence"]
        reviewed_revision = sealed["integration_revision"]
        self.assertTrue(sealed["dod"]["passed"])

        # Simulate subsequent local work and another loop replacing the
        # legacy shared review projection with incompatible bytes.
        open(os.path.join(ws, "README.md"), "w", encoding="utf-8").write(
            "later unrelated loop\n")
        subprocess.run(["git", "add", "README.md"], cwd=ws, check=True)
        subprocess.run(["git", "commit", "-qm", "later loop"], cwd=ws,
                       check=True)
        with open(os.path.join(ws, ".em-review", "findings.json"), "w",
                  encoding="utf-8") as stream:
            json.dump({"meta": {"gate": {"verdict": "invalid"}},
                       "findings": "wrong schema"}, stream)

        action = loop.next_action(ws)
        self.assertTrue(action["dod"]["passed"], action["dod"])
        self.assertEqual(loop.load(ws)["signoff_evidence"]
                         ["integration_revision"], reviewed_revision)
        approved = loop.approve(ws, by="human")
        self.assertEqual(approved["step"], "retro")

    def test_legacy_signoff_recovery_fails_closed_without_new_loop_advice(self):
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "g", spec_path="s", checkpoints=["em"])
        state = loop.load(ws)
        state.update({"step": "signoff", "baseline": tp.git_head(ws),
                      "tasks": [dict(TASK, status="passed")]})
        loop.save(ws, state)

        action = loop.next_action(ws)
        self.assertTrue(action["dod"]["legacy_recovery"])
        self.assertNotIn("new loop", str(action).lower())
        refused = loop.approve(ws, by="human")
        self.assertEqual(refused["recovery"], "same_loop_engineering_review")
        self.assertNotIn("re-anchor", str(refused).lower())
        self.assertNotIn("new loop", str(refused).lower())
        self.assertEqual(loop.load(ws)["step"], "signoff")

    def test_fail_autofix_then_escalate(self):
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "g", spec_path="s", checkpoints=["em"], max_fix_cycles=2)
        loop.next_action(ws); loop.gate(ws, "pass")   # plan → execute
        loop.next_action(ws); submit_gate(ws, "pass") # execute → evaluate
        loop.next_action(ws); submit_gate(ws, "fail") # evaluate FAIL → fix (1)
        self.assertEqual(loop.load(ws)["step"], "fix")
        loop.next_action(ws); submit_gate(ws, "pass") # fix → evaluate
        loop.next_action(ws); submit_gate(ws, "fail") # FAIL → fix (2)
        loop.next_action(ws); submit_gate(ws, "pass") # fix → evaluate
        loop.next_action(ws); submit_gate(ws, "fail") # cycle 3 > max → escalated
        self.assertEqual(loop.load(ws)["step"], "escalated")
        loop.resolve(ws, "skip")                       # last task → em
        self.assertEqual(loop.load(ws)["step"], "em")

    def test_multi_task_progression(self):
        t2 = dict(TASK, id="t2")
        ws = git_ws(self.tmp, [TASK, t2])
        loop.init(ws, "g", spec_path="s", checkpoints=["em"])
        loop.next_action(ws); loop.gate(ws, "pass")   # plan → execute t1
        loop.next_action(ws); submit_gate(ws, "pass") # execute → evaluate
        loop.next_action(ws); pass_eval(ws)            # evaluate t1 pass → execute t2
        self.assertEqual(loop.load(ws)["step"], "execute")
        self.assertEqual(loop.load(ws)["current_task"], 1)
        loop.next_action(ws); submit_gate(ws, "pass")
        loop.next_action(ws); pass_eval(ws)            # evaluate t2 pass → em
        self.assertEqual(loop.load(ws)["step"], "em")

    def test_governed_coding_contracts_enable_regression_gate(self):
        """Execute and repair may never silently omit regression evidence."""
        state = {"goal": "g", "current_task": 0, "tasks": [TASK]}
        for step in ("execute", "fix"):
            with self.subTest(step=step):
                contract = loop._step_contract(step, state)
                self.assertTrue(
                    contract["coding"]["dod"]["regression_gate"])

    def test_read_only_workflow_roles_can_call_the_governed_cli(self):
        """Codex exposes CLI calls through Bash even for read-only roles."""
        state = {"goal": "g", "current_task": 0, "tasks": [TASK]}
        for step in ("pm", "design", "plan"):
            with self.subTest(step=step):
                contract = loop._step_contract(step, state)
                self.assertTrue(contract["read_only"])
                self.assertIn("Bash", contract["allowed_tools"])
                ok, reason = loop.tp.screen_tool(
                    contract, "Bash",
                    {"command": "python3 taskplane/tp.py status"}, self.tmp)
                self.assertTrue(ok, reason)

    def test_step_contract_is_active_before_definition_of_ready(self):
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "g", spec_path="specs/spec.md")
        order = []
        original_activate = loop.tp.activate
        original_dor = loop.tp.dor_check

        def activate(*args, **kwargs):
            order.append("contract")
            return original_activate(*args, **kwargs)

        def dor(*args, **kwargs):
            order.append("dor")
            return original_dor(*args, **kwargs)

        with unittest.mock.patch.object(loop.tp, "activate", activate), \
                unittest.mock.patch.object(loop.tp, "dor_check", dor):
            loop.next_action(ws)
        self.assertEqual(order[:2], ["contract", "dor"])

    def test_task_dod_enables_regression_gate(self):
        """The submit/gate reconstruction keeps the same governed DoD."""
        with unittest.mock.patch.object(
                loop.tp, "dod_check", return_value=[]) as check:
            self.assertEqual(loop._task_dod_errors(
                self.tmp, {"baseline": "HEAD"}, TASK, "HEAD"), [])
        contract = check.call_args.args[0]
        self.assertTrue(contract["coding"]["dod"]["regression_gate"])
        self.assertEqual(check.call_args.kwargs["regression_files"], [])

    def test_explicit_task_criteria_are_not_replaced_by_release_acceptance(self):
        """Each early task proves its slice; the release proves the union."""
        task = dict(TASK, criteria=["scoped behavior is complete"], req="R-1")
        with unittest.mock.patch.object(
                loop.reqs, "get_requirement",
                return_value={"acceptance": ["the full release is complete"]}):
            self.assertEqual(loop._criteria_for(self.tmp, {}, task),
                             ["scoped behavior is complete"])

    def test_release_acceptance_requires_explicit_owners_across_tasks(self):
        rec = {"acceptance": ["first outcome", "second outcome"]}
        tasks = [
            {"id": "t1", "req": "R-1", "criteria": ["scoped one"],
             "acceptance_refs": ["first outcome"], "status": "passed"},
            {"id": "t2", "req": "R-1", "criteria": ["scoped two"],
             "acceptance_refs": ["second outcome"], "status": "pending"},
        ]
        lookup = lambda rid: rec if rid == "R-1" else None
        self.assertEqual(tp.requirement_coverage_errors(tasks, lookup), [])
        errors = tp.requirement_coverage_errors(
            tasks, lookup, require_passed=True)
        self.assertIn("second outcome", " ".join(errors))

    def test_release_acceptance_rejects_an_unowned_outcome(self):
        rec = {"acceptance": ["first outcome", "missed outcome"]}
        tasks = [{"id": "t1", "req": "R-1", "criteria": ["scoped"],
                  "acceptance_refs": ["first outcome"]}]
        errors = tp.requirement_coverage_errors(tasks, lambda _rid: rec)
        self.assertIn("missed outcome", " ".join(errors))

    def test_release_acceptance_rejects_a_missing_requirement(self):
        tasks = [{"id": "t1", "req": "R-missing", "criteria": ["scoped"]}]
        errors = tp.requirement_coverage_errors(tasks, lambda _rid: None)
        self.assertIn("R-missing does not exist", " ".join(errors))

    def test_escalate_retry_resets_cycles(self):
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "g", spec_path="s", checkpoints=["em"], max_fix_cycles=1)
        loop.next_action(ws); loop.gate(ws, "pass")   # → execute
        loop.next_action(ws); submit_gate(ws, "pass") # → evaluate
        loop.next_action(ws); submit_gate(ws, "fail") # → fix (1)
        loop.next_action(ws); submit_gate(ws, "pass") # → evaluate
        loop.next_action(ws); submit_gate(ws, "fail") # cycle2 > max1 → escalated
        loop.resolve(ws, "retry")
        self.assertEqual(loop.load(ws)["step"], "fix")
        self.assertEqual(loop.load(ws)["tasks"][0]["fix_cycles"], 0)


if __name__ == "__main__":
    unittest.main()


class TestLoopLensAndRequirementWiring(unittest.TestCase):
    """Step 1 wiring: prime at EXECUTE, route at EVALUATE/EM, refinement
    gate at plan approval, tasks anchored to R-ids."""

    def _ws(self, scope="src/auth/**", high_cost=False, with_req=True):
        import requirements as reqs
        ws = tempfile.mkdtemp()
        os.makedirs(os.path.join(ws, "plan"))
        os.makedirs(os.path.join(ws, "src", "auth"))
        with open(os.path.join(ws, "src", "auth", "a.py"), "w", encoding="utf-8") as f:
            f.write("x=1\n")
        for c in (["init", "-q"], ["add", "-A"]):
            subprocess.run(["git", *c], cwd=ws)
        subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                        "commit", "-qm", "i"], cwd=ws)
        task = {"id": "t1", "scope": [scope], "tests": "true"}
        if high_cost:
            task["high_cost"] = True
        if with_req:
            r = reqs.record_requirement(
                ws, "login", functional=["user logs in"],
                acceptance=["valid creds -> session"],
                context_files=[scope])
            task["req"] = r["id"]
        with open(os.path.join(ws, "plan", "tasks.json"), "w", encoding="utf-8") as f:
            json.dump({"tasks": [task]}, f)
        loop.init(ws, "auth work", spec_path="s", checkpoints=["plan"])
        loop.next_action(ws)
        loop.gate(ws, "pass")          # plan -> plan_approval
        return ws

    def test_execute_is_primed_and_anchored(self):
        ws = self._ws()
        out = loop.approve(ws)
        self.assertIn("refinement", out)          # forecast shown at the gate
        act = loop.next_action(ws)                # execute step
        self.assertEqual(act["step"], "execute")
        primed = {x["id"] for x in act["lenses"]}
        self.assertIn("security", primed)         # auth scope -> security
        self.assertEqual(act["requirement"]["id"], "R-0001")
        self.assertTrue(act["requirement"]["acceptance"])

    def test_plan_brief_exposes_canonical_contract_ids_structurally(self):
        import requirements as reqs
        ws = tempfile.mkdtemp()
        for c in (["init", "-q"], ["add", "-A"]):
            subprocess.run(["git", *c], cwd=ws)
        subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                        "commit", "--allow-empty", "-qm", "i"], cwd=ws)
        r = reqs.record_requirement(
            ws, "change API", functional=["change it"],
            nfr={"security": "fail closed", "architecture": "stay local"},
            acceptance=["changed behavior is tested"],
            contracts=[{"relation": "changes",
                        "id": "contract:pricing.checkout.total"}],
            context_files=["pricing/checkout.py"])
        loop.init(ws, "change API", spec_path="specs/spec.md",
                  requirement_id=r["id"])
        action = loop.next_action(ws)
        self.assertEqual(action["step"], "plan")
        self.assertEqual(
            action["requirement"]["contracts"],
            [{"relation": "changes",
              "id": "contract:pricing.checkout.total"}])
        self.assertNotIn("changes:contract:", json.dumps(
            action["requirement"]["contracts"]))

    def test_pm_gate_scores_dor_and_links_planned_graph_once(self):
        import requirements as reqs
        ws = tempfile.mkdtemp()
        os.makedirs(os.path.join(ws, "src", "auth"))
        os.makedirs(os.path.join(ws, "specs"))
        with open(os.path.join(ws, "src", "auth", "a.py"), "w",
                  encoding="utf-8") as f:
            f.write("def authorize():\n    return True\n")
        with open(os.path.join(ws, "specs", "spec.md"), "w",
                  encoding="utf-8") as f:
            f.write("# Auth requirement\n")
        for command in (["init", "-q"], ["add", "-A"]):
            subprocess.run(["git", *command], cwd=ws, check=True)
        subprocess.run(
            ["git", "-c", "user.email=e@e", "-c", "user.name=t",
             "commit", "-qm", "initial"], cwd=ws, check=True)
        depgraph.scan(ws)
        rec = reqs.record_requirement(
            ws, "authorize users", functional=["authorize a user"],
            nfr={"security": "deny by default",
                 "architecture": "preserve the auth boundary"},
            acceptance=["unauthorized users receive 403"],
            context_files=["src/auth/a.py"])
        loop.init(ws, "authorize users", requirement_id=rec["id"])
        loop.next_action(ws)

        out = loop.gate(ws, "pass")

        self.assertEqual(out["step"], "plan")
        state = loop.load(ws)
        self.assertEqual(state["requirement_refinement"]["functional"], 1.0)
        graph = depgraph.load(ws)
        planned = [edge for edge in graph["edges"]
                   if edge.get("from") == "req:" + rec["id"]
                   and edge.get("kind") == "planned"]
        self.assertEqual(len(planned), 1)
        self.assertEqual(planned[0]["to"], "auth")

    def test_pm_gate_blocks_missing_security_and_architecture_nfrs(self):
        import requirements as reqs
        ws = tempfile.mkdtemp()
        os.makedirs(os.path.join(ws, "specs"))
        with open(os.path.join(ws, "specs", "spec.md"), "w",
                  encoding="utf-8") as f:
            f.write("# Thin requirement\n")
        rec = reqs.record_requirement(
            ws, "thin", functional=["change behavior"],
            acceptance=["behavior is verified"],
            context_files=["src/feature.py"])
        loop.init(ws, "thin", requirement_id=rec["id"])
        loop.next_action(ws)

        out = loop.gate(ws, "pass")

        self.assertIn("Definition of Ready", out["error"])
        details = " ".join(out["dor"]["errors"])
        self.assertIn("security", details)
        self.assertIn("architecture", details)
        self.assertEqual(loop.load(ws)["step"], "pm")

    def test_evaluate_routes_on_real_diff(self):
        """R-0006 row 1: EVALUATE routes the real diff with stage='build'
        (route v2) — not the legacy stage-less route it pinned pre-design."""
        ws = self._ws()
        loop.approve(ws)
        loop.next_action(ws)
        # the "build": touch an auth file, uncommitted
        with open(os.path.join(ws, "src", "auth", "b.py"), "w", encoding="utf-8") as f:
            f.write("def authorize():\n    return True\n")
        submit_gate(ws, "pass")                   # execute -> evaluate
        act = loop.next_action(ws)
        self.assertEqual(act["step"], "evaluate")
        # v2 build-stage signature: full-catalog coverage honesty (every
        # lens appears, the narrowed-away ones as mode "none") — the
        # legacy routed path returned only the summoned subset.
        catalog_ids = {l["id"] for l in lens.load_catalog()["lenses"]}
        self.assertEqual({x["id"] for x in act["lenses"]}, catalog_ids)
        self.assertTrue([x for x in act["lenses"] if x["mode"] == "none"])
        # security is floored on an auth diff: routed, never n/a...
        sec = next(x for x in act["lenses"] if x["id"] == "security")
        self.assertNotEqual(sec["mode"], "none")
        self.assertIn(sec["tier"], ("light", "deep"))
        # ...and carries the v2 engine keys the legacy path never emitted
        self.assertIn("verdict", sec)
        self.assertIn("score", sec)
        # the brief IS route v2 on this diff: same mode as the direct
        # build-stage derivation the validator single-sources
        state = loop.load(ws)
        direct = lens.route_git_diff(
            ws, base=state.get("baseline") or "HEAD",
            task_type=None, stage=loop.EVALUATE_ROUTE_STAGE,
            breadth="routed")
        dsec = next(x for x in direct["lenses"] if x["id"] == "security")
        self.assertEqual(sec["mode"], dsec["mode"])
        self.assertEqual(
            {x["id"] for x in act["lenses"] if x["mode"] != "none"},
            {x["id"] for x in direct["lenses"] if x["mode"] != "none"})

    def test_high_cost_unrefined_blocks_until_force(self):
        import requirements as reqs
        ws = self._ws(with_req=False, high_cost=True)
        # anchor to a thin (unrefined) requirement
        r = reqs.record_requirement(ws, "vague", context_files=["src/auth/**"])
        st = loop.load(ws)
        st["tasks"][0]["req"] = r["id"]
        st["tasks"][0]["high_cost"] = True
        loop.save(ws, st)
        out = loop.approve(ws)
        self.assertIn("error", out)                # hard-blocked
        out2 = loop.approve(ws, force=True)
        self.assertEqual(out2["step"], "execute")  # human override

    def test_no_requirement_still_flows(self):
        ws = self._ws(with_req=False)
        out = loop.approve(ws)
        self.assertEqual(out["step"], "execute")
        act = loop.next_action(ws)
        self.assertIsNone(act["requirement"])      # unanchored is allowed


class TestParallelExecution(unittest.TestCase):
    """Waves: deps + scope-disjointness pick the wave; every worker gets its
    OWN contract in its OWN worktree — the harness is per agent."""

    def _ws(self):
        ws = tempfile.mkdtemp()
        os.makedirs(os.path.join(ws, "plan"))
        for d in ("src/a", "src/b", "src/c"):
            os.makedirs(os.path.join(ws, d))
            with open(os.path.join(ws, d, "m.py"), "w", encoding="utf-8") as f:
                f.write("x=1\n")
        subprocess.run(["git", "init", "-q"], cwd=ws)
        subprocess.run(["git", "add", "-A"], cwd=ws)
        subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                        "commit", "-qm", "i"], cwd=ws)
        tasks = [
            {"id": "t1", "scope": ["src/a/**"], "tests": "true"},
            {"id": "t2", "scope": ["src/b/**"], "tests": "true"},
            {"id": "t3", "scope": ["src/a/**", "src/c/**"], "tests": "true"},
            {"id": "t4", "scope": ["src/c/**"], "tests": "true",
             "deps": ["t1"]},
        ]
        with open(os.path.join(ws, "plan", "tasks.json"), "w", encoding="utf-8") as f:
            json.dump({"tasks": tasks}, f)
        loop.init(ws, "parallel goal", spec_path="s", checkpoints=["plan"],
                  parallel=True)
        loop.next_action(ws); loop.gate(ws, "pass")   # plan → approval
        loop.approve(ws)                               # → execute
        return ws

    def test_wave_respects_deps_and_scope_disjointness(self):
        ws = self._ws()
        w = loop.wave(ws)
        ids = [e["task"]["id"] for e in w["wave"]]
        held = {h["task"]: h["reason"] for h in w["held"]}
        self.assertEqual(ids, ["t1", "t2"])       # disjoint, dep-free
        self.assertIn("t3", held)                 # overlaps t1 (src/a)
        self.assertIn("t4", held)                 # dep t1 not passed yet
        self.assertTrue(all(e["lenses"] is not None for e in w["wave"]))

    def test_claim_activates_contract_in_worker_worktree(self):
        import taskplane_lite as tpl
        ws = self._ws()
        agent_ws = os.path.join(ws, ".tp-work", "t1")
        subprocess.run(["git", "worktree", "add", "-q", agent_ws, "-b",
                        "tp/t1"], cwd=ws)
        out = loop.claim(ws, "t1", agent_ws)
        self.assertEqual(out["claimed"], "t1")
        # the WORKER's workspace is governed…
        c = tpl.load_active(agent_ws)
        self.assertEqual(c["coding"]["scope_paths"], ["src/a/**"])
        # …and the hook blocks it outside its own task scope:
        allow, _ = tpl.screen_tool(
            c, "Write", {"file_path": os.path.join(agent_ws, "src/b/x.py")},
            agent_ws)
        self.assertFalse(allow)
        allow2, _ = tpl.screen_tool(
            c, "Write", {"file_path": os.path.join(agent_ws, "src/a/x.py")},
            agent_ws)
        self.assertTrue(allow2)
        # the MAIN workspace is not governed by this worker's contract
        self.assertIsNone(tpl.load_active(ws))

    def test_parallel_execute_gate_validates_claimed_task_worktree(self):
        """EXECUTE DoD must import and test the claimed branch's bytes."""
        tests = (
            "python3 -c \"import sys; sys.path.insert(0, 'src/a'); "
            "import taskplane_lite; assert "
            "taskplane_lite.WORKTREE_ONLY_EXECUTE_DOD\""
        )
        ws = self._ws()
        state = loop.load(ws)
        state["tasks"][0]["tests"] = tests
        loop.save(ws, state)

        agent_ws = os.path.join(ws, ".tp-work", "t1")
        subprocess.run(
            ["git", "worktree", "add", "-q", agent_ws, "-b",
             "tp/execute-dod-worktree-binding"],
            cwd=ws, check=True,
        )
        loop.claim(ws, "t1", agent_ws)
        module = os.path.join(agent_ws, "src", "a", "taskplane_lite.py")
        os.makedirs(os.path.dirname(module), exist_ok=True)
        shutil.copyfile(tp.__file__, module)
        with open(module, "a", encoding="utf-8") as stream:
            stream.write("WORKTREE_ONLY_EXECUTE_DOD = True\n")
        subprocess.run(["git", "add", "-A"], cwd=agent_ws, check=True)
        subprocess.run(
            ["git", "-c", "user.email=e@e", "-c", "user.name=t",
             "commit", "-qm", "worker-only engine bytes"],
            cwd=agent_ws, check=True,
        )

        submitted = loop.submit(ws, "pass", task_id="t1")
        self.assertTrue(submitted.get("submitted"), submitted)
        gated = loop.gate(ws, "pass", task_id="t1")

        self.assertNotIn("error", gated)
        self.assertTrue(gated["built"])
        self.assertEqual(loop.load(ws)["tasks"][0]["status"], "built")

    def test_evaluator_evidence_binds_claimed_worktree_from_either_checkout(self):
        """The evaluator may launch through the primary bridge or in the
        claimed worker, but both paths must cite and describe worker bytes."""
        ws = self._ws()
        agent_ws = os.path.join(ws, ".tp-work", "t1")
        subprocess.run(["git", "worktree", "add", "-q", agent_ws, "-b",
                        "tp/t1-evidence"], cwd=ws, check=True)
        loop.claim(ws, "t1", agent_ws)
        changed = os.path.join(agent_ws, "src", "a", "evidence.py")
        with open(changed, "w", encoding="utf-8") as stream:
            stream.write("worker_only = True\n")

        state = loop.load(ws)
        state["current_task"] = 0
        state["step"] = "evaluate"
        state["graph_governance"] = False
        env = {key: value for key, value in os.environ.items()
               if key != "TASKPLANE_TASK"}
        state["_suite_evidence"] = {"t1": {
            "schema": "taskplane.suite-evidence/v1",
            "command": "true",
            "key": tp._suite_cache_key(agent_ws, "true", env),
            "returncode": 0,
            "tail": "",
            "duration_s": 0.01,
            "source": "execute-gate",
        }}
        loop.save(ws, state)

        cli = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "tp.py")

        def evidence(cwd, write=False):
            command = [sys.executable, cli, "loop", "evidence",
                       "--task", "t1"]
            if write:
                command.append("--write")
            result = subprocess.run(
                command, cwd=cwd, check=True, text=True,
                encoding="utf-8", errors="replace",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return json.loads(result.stdout)

        through_primary = evidence(ws, write=True)
        from_worker = evidence(agent_ws)
        for bundle in (through_primary, from_worker):
            self.assertNotIn("error", bundle)
            self.assertTrue(bundle["suite"]["cited"])
            self.assertEqual(bundle["suite"]["source"], "execute-gate")
            self.assertIn("src/a/evidence.py", bundle["diff"]["files"])
        self.assertTrue(os.path.exists(os.path.join(
            agent_ws, ".eval", "verdict.json")))
        self.assertFalse(os.path.exists(os.path.join(
            ws, ".eval", "verdict.json")))

    def test_parallel_gates_flow_to_evaluate_then_next_wave(self):
        ws = self._ws()
        for tid in ("t1", "t2"):
            agent_ws = os.path.join(ws, ".tp-work", tid)
            subprocess.run(["git", "worktree", "add", "-q", agent_ws, "-b",
                            f"tp/{tid}"], cwd=ws)
            loop.claim(ws, tid, agent_ws)
            depgraph.scan(agent_ws)
        out = submit_gate(ws, "pass", task_id="t1")
        self.assertEqual(out["still_running"], ["t2"])
        submit_gate(ws, "pass", task_id="t2")
        # both built → next surfaces evaluate for the first built task
        act = loop.next_action(ws)
        self.assertEqual(act["step"], "evaluate")
        self.assertEqual(act["task"]["id"], "t1")
        pass_eval(ws)                                  # t1 passed
        act2 = loop.next_action(ws)                   # evaluate t2
        self.assertEqual(act2["task"]["id"], "t2")
        pass_eval(ws)                                  # t2 passed
        # t1 passed unlocks t4, but t3/t4 overlap on src/c → serialized:
        # t3 (first in plan order) dispatches, t4 holds for the next wave.
        w = loop.wave(ws)
        self.assertEqual({e["task"]["id"] for e in w["wave"]}, {"t3"})
        held = {h["task"]: h["reason"] for h in w["held"]}
        self.assertIn("t4", held)
        self.assertIn("overlaps", held["t4"])

    def test_all_passed_reaches_em(self):
        ws = self._ws()
        agent_ws = os.path.join(ws, ".tp-work", "t1")
        subprocess.run(["git", "worktree", "add", "-q", agent_ws, "-b",
                        "tp/t1-final-evaluate"], cwd=ws, check=True)
        loop.claim(ws, "t1", agent_ws)
        depgraph.scan(agent_ws)
        st = loop.load(ws)
        for t in st["tasks"]:
            t["status"] = "passed"
        st["tasks"][0]["status"] = "built"     # last one still to evaluate
        loop.save(ws, st)
        act = loop.next_action(ws)
        self.assertEqual(act["step"], "evaluate")
        out = pass_eval(ws)
        self.assertEqual(out["step"], "em")

    def test_gate_requires_task_id_in_parallel_execute(self):
        ws = self._ws()
        self.assertIn("error", loop.gate(ws, "pass"))


class TestParallelEvaluateWorktreeGraphBinding(unittest.TestCase):
    """Parallel Evaluate must judge task bytes with that task's graph."""

    def _park_at_evaluate(self):
        ws = TestParallelExecution._ws(TestParallelExecution())
        worker = os.path.join(ws, ".tp-work", "t1")
        subprocess.run(
            ["git", "worktree", "add", "-q", worker, "-b",
             "tp/graph-bound-evaluate"], cwd=ws, check=True)
        loop.claim(ws, "t1", worker)
        changed = os.path.join(worker, "src", "a", "m.py")
        with open(changed, "w", encoding="utf-8") as stream:
            stream.write("x=2\n")
        subprocess.run(["git", "add", "-A"], cwd=worker, check=True)
        subprocess.run(
            ["git", "-c", "user.email=e@e", "-c", "user.name=t",
             "commit", "-qm", "worker graph target"],
            cwd=worker, check=True)
        state = loop.load(ws)
        state["current_task"] = 0
        state["step"] = "evaluate"
        state["graph_governance"] = True
        loop.save(ws, state)
        return ws, worker

    @staticmethod
    def _graph(head, fingerprint, *, module="a"):
        return {
            "modules": {module: {"kind": "module", "files": 1}},
            "edges": [],
            "files": {"src/a/m.py": {
                "module": module, "hash": fingerprint[:16]}},
            "recorded": [],
            "meta": {
                "schema": 2,
                "scanned_head": head,
                "content_fingerprint": fingerprint,
                "source_counts": {"scanner": 0},
            },
        }

    def test_kernel_uses_only_exact_task_graph_and_leaves_primary_untouched(self):
        ws, worker = self._park_at_evaluate()
        canonical_worker = os.path.realpath(worker)
        primary_graph = self._graph(tp.git_head(ws), "1" * 64,
                                    module="primary-only")
        task_graph = self._graph(tp.git_head(worker), "2" * 64)
        primary_before = json.dumps(primary_graph, sort_keys=True)
        reads = []

        def load_graph(workspace):
            reads.append(workspace)
            if workspace == canonical_worker:
                return task_graph
            if workspace == os.path.realpath(ws):
                return primary_graph
            self.fail(f"unexpected graph workspace: {workspace}")

        with unittest.mock.patch.object(
                depgraph, "load", side_effect=load_graph), \
                unittest.mock.patch.object(depgraph, "scan") as scan_graph:
            action = getattr(loop.next_action, "__wrapped__", loop.next_action)(ws)

        self.assertEqual(action["review_kernel"]["status"], "ready")
        self.assertEqual((action["impact"]["graph"] or {})[
            "content_fingerprint"], "2" * 64)
        self.assertEqual(set(reads), {canonical_worker})
        scan_graph.assert_not_called()
        self.assertEqual(json.dumps(primary_graph, sort_keys=True),
                         primary_before)

    def test_validated_workspace_is_only_downstream_evidence_authority(self):
        ws, worker = self._park_at_evaluate()
        canonical_worker = os.path.realpath(worker)
        task_graph = self._graph(tp.git_head(worker), "5" * 64)
        alias_root = tempfile.mkdtemp()
        alias_parent = os.path.join(alias_root, "tasks")
        os.symlink(os.path.dirname(canonical_worker), alias_parent)
        alias = os.path.join(alias_parent, os.path.basename(canonical_worker))
        graph_reads = []
        diff_reads = []
        review_reads = []
        original_diff = loop._diff_files
        original_kernel = loop._review_kernel

        def validate_workspace(_ws, _state, task):
            task["workspace"] = alias
            return canonical_worker, None

        def load_graph(workspace):
            graph_reads.append(workspace)
            if workspace != canonical_worker:
                self.fail(f"noncanonical graph workspace: {workspace}")
            return task_graph

        def diff_files(workspace, base):
            diff_reads.append(workspace)
            return original_diff(workspace, base)

        def review_kernel(primary, diff_ws, **kwargs):
            review_reads.append(diff_ws)
            return original_kernel(primary, diff_ws, **kwargs)

        with unittest.mock.patch.object(
                loop, "_parallel_evaluate_workspace",
                side_effect=validate_workspace), \
                unittest.mock.patch.object(
                    depgraph, "load", side_effect=load_graph), \
                unittest.mock.patch.object(
                    loop, "_diff_files", side_effect=diff_files), \
                unittest.mock.patch.object(
                    loop, "_review_kernel", side_effect=review_kernel):
            action = getattr(
                loop.next_action, "__wrapped__", loop.next_action)(ws)

        self.assertEqual(action["review_kernel"]["status"], "ready")
        self.assertTrue(graph_reads)
        self.assertTrue(diff_reads)
        self.assertTrue(review_reads)
        self.assertEqual(set(graph_reads), {canonical_worker})
        self.assertEqual(set(diff_reads), {canonical_worker})
        self.assertEqual(set(review_reads), {canonical_worker})

    def test_missing_or_revision_mismatched_task_graph_never_falls_back(self):
        cases = (
            ("missing", {"modules": {}, "edges": [], "files": {},
                         "recorded": [], "meta": {}}),
            ("revision-mismatched",
             self._graph("f" * 40, "3" * 64)),
        )
        for label, task_graph in cases:
            with self.subTest(label=label):
                ws, worker = self._park_at_evaluate()
                primary_graph = self._graph(tp.git_head(worker), "4" * 64)
                reads = []

                def load_graph(workspace):
                    resolved = os.path.realpath(workspace)
                    reads.append(resolved)
                    if resolved == os.path.realpath(worker):
                        return task_graph
                    if resolved == os.path.realpath(ws):
                        return primary_graph
                    self.fail(f"unexpected graph workspace: {workspace}")

                with unittest.mock.patch.object(
                        depgraph, "load", side_effect=load_graph), \
                        unittest.mock.patch.object(depgraph, "scan") as scan_graph:
                    action = getattr(
                        loop.next_action, "__wrapped__", loop.next_action)(ws)

                self.assertEqual(action["review_kernel"]["status"],
                                 "impact_incomplete")
                self.assertEqual(action["review_kernel"]["slots"], [])
                self.assertEqual(set(reads), {os.path.realpath(worker)})
                scan_graph.assert_not_called()

    def test_primary_checkout_is_not_an_unambiguous_task_graph_workspace(self):
        ws, _ = self._park_at_evaluate()
        state = loop.load(ws)
        state["tasks"][0]["workspace"] = ws
        loop.save(ws, state)

        with unittest.mock.patch.object(depgraph, "load") as load_graph:
            action = getattr(loop.next_action, "__wrapped__", loop.next_action)(ws)

        self.assertIn("error", action)
        self.assertIn("task worktree", action["error"])
        load_graph.assert_not_called()

    def test_workspace_resolver_uses_precise_mapping_annotations(self):
        import collections.abc
        import inspect
        import typing

        signature = inspect.signature(loop._parallel_evaluate_workspace)
        for parameter in ("state", "task"):
            annotation = typing.get_type_hints(
                loop._parallel_evaluate_workspace)[parameter]
            self.assertIs(typing.get_origin(annotation),
                          collections.abc.Mapping)
            self.assertNotEqual(signature.parameters[parameter].annotation,
                                dict)

    def test_workspace_resolver_rejects_noncanonical_and_symlink_paths(self):
        cases = ("foreign", "mismatched", "symlink", "parent-symlink")
        for case in cases:
            with self.subTest(case=case):
                ws, worker = self._park_at_evaluate()
                if case == "foreign":
                    candidate = tempfile.mkdtemp()
                elif case == "mismatched":
                    candidate = os.path.join(ws, ".tp-work", "t2")
                    os.makedirs(candidate)
                elif case == "symlink":
                    candidate = os.path.join(ws, ".tp-work", "t1-alias")
                    os.symlink(worker, candidate)
                else:
                    alias_root = tempfile.mkdtemp()
                    alias_parent = os.path.join(alias_root, "tasks")
                    os.symlink(os.path.dirname(os.path.realpath(worker)),
                               alias_parent)
                    candidate = os.path.join(
                        alias_parent, os.path.basename(worker))
                state = loop.load(ws)
                task = state["tasks"][0]
                task["workspace"] = candidate

                resolved, error = loop._parallel_evaluate_workspace(
                    ws, state, task)

                self.assertIsNone(resolved)
                self.assertIn("canonical managed task worktree", error)


class TestManagedWorktreeGraphPublication(unittest.TestCase):
    """Evaluate restores a missing locator from one exact run registration."""

    def setUp(self):
        self.ws = TestParallelExecution._ws(TestParallelExecution())
        initial_state = loop.load(self.ws)
        subprocess.run(
            ["git", "remote", "add", "origin",
             "https://github.com/Example/Loop-Recovery.git"], cwd=self.ws,
            check=True)
        self.home = tempfile.mkdtemp(prefix="tp-loop-reconstruct-home-")
        self.identity = runtime_storage.resolve_repository_identity(self.ws)
        self.run_id = "run-loop-123"
        self.layout = runtime_storage.resolve_layout(
            self.identity, home=self.home, run_id=self.run_id)
        runtime_storage.write_workspace_locator(
            self.ws, identity=self.identity, layout=self.layout,
            run_id=self.run_id)
        loop.save(self.ws, initial_state)
        self.worker = runtime_storage.task_worktree_path(self.ws, "t1")
        os.makedirs(os.path.dirname(self.worker), exist_ok=True)
        result = subprocess.run(
            ["git", "worktree", "add", "-q", "-b", "tp/t1-recovery",
             self.worker, "HEAD"], cwd=self.ws, capture_output=True,
            text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        claimed = loop.claim(self.ws, "t1", self.worker)
        self.assertNotIn("error", claimed)
        changed = os.path.join(self.worker, "src", "a", "m.py")
        with open(changed, "w", encoding="utf-8") as stream:
            stream.write("x=2\n")
        subprocess.run(["git", "add", "-A"], cwd=self.worker, check=True)
        subprocess.run(
            ["git", "-c", "user.email=e@e", "-c", "user.name=t",
             "commit", "-qm", "managed worker target"], cwd=self.worker,
            check=True)
        self.target = tp.git_head(self.worker)
        submitted = loop.submit(self.ws, "pass", task_id="t1")
        self.assertNotIn("error", submitted)
        gated = loop.gate(self.ws, "pass", task_id="t1")
        self.assertNotIn("error", gated)
        state = loop.load(self.ws)
        self.assertEqual(state["tasks"][0]["target_commit"], self.target)
        state["current_task"] = 0
        state["step"] = "evaluate"
        state["graph_governance"] = True
        loop.save(self.ws, state)
        self.worker_locator = runtime_storage._locator_path(self.worker)
        self.primary_locator = runtime_storage._locator_path(self.ws)
        os.unlink(self.worker_locator)
        os.unlink(self.primary_locator)
        with unittest.mock.patch.dict(
                os.environ, {"TASKPLANE_HOME": self.home}):
            loop.save(self.ws, state)

    def test_evaluate_reconstructs_then_reads_exact_run_local_graph(self):
        primary_graph = os.path.join(self.layout.graph_root, "graph.json")
        os.makedirs(os.path.dirname(primary_graph), exist_ok=True)
        primary_value = {
            "modules": {"primary-only": {"kind": "module", "files": 1}},
            "edges": [], "files": {}, "recorded": [],
            "meta": {"scanned_head": tp.git_head(self.ws),
                     "content_fingerprint": "1" * 64},
        }
        with open(primary_graph, "w", encoding="utf-8") as handle:
            json.dump(primary_value, handle, sort_keys=True)
        with open(primary_graph, "rb") as handle:
            primary_before = handle.read()
        with unittest.mock.patch.dict(
                os.environ, {"TASKPLANE_HOME": self.home}):
            state = loop.load(self.ws)
            resolved, error = loop._parallel_evaluate_workspace(
                self.ws, state, state["tasks"][0])
            graph = depgraph.scan(resolved)
            action = getattr(loop.next_action, "__wrapped__",
                             loop.next_action)(self.ws)

        self.assertIsNone(error)
        self.assertEqual(resolved, os.path.realpath(self.worker))
        self.assertEqual(graph["meta"]["scanned_head"], self.target)
        locator = runtime_storage.load_workspace_locator(self.worker)
        self.assertTrue(os.path.isfile(os.path.join(
            locator["paths"]["graph"], "graph.json")))
        self.assertNotIn("error", action, action)
        self.assertEqual(action["review_kernel"]["status"], "ready")
        self.assertEqual(action["impact"]["graph"]["scanned_head"],
                         self.target)
        with open(primary_graph, "rb") as handle:
            self.assertEqual(handle.read(), primary_before)

    def test_evaluate_requires_independent_target_commit(self):
        with unittest.mock.patch.dict(
                os.environ, {"TASKPLANE_HOME": self.home}):
            state = loop.load(self.ws)
            state["tasks"][0].pop("target_commit")
            resolved, error = loop._parallel_evaluate_workspace(
                self.ws, state, state["tasks"][0])
        self.assertIsNone(resolved)
        self.assertIn("target commit", error)
        self.assertFalse(os.path.exists(self.worker_locator))


class TestParallelCommitDiscipline(unittest.TestCase):
    def test_gate_refuses_uncommitted_worktree_then_accepts(self):
        ws = TestParallelExecution._ws(TestParallelExecution())
        agent_ws = os.path.join(ws, ".tp-work", "t1")
        subprocess.run(["git", "worktree", "add", "-q", agent_ws, "-b",
                        "tp/t1"], cwd=ws)
        loop.claim(ws, "t1", agent_ws)
        with open(os.path.join(agent_ws, "src", "a", "new.py"), "w", encoding="utf-8") as f:
            f.write("y=2\n")
        loop.submit(ws, "pass", task_id="t1")
        out = loop.gate(ws, "pass", task_id="t1")
        self.assertIn("error", out)                    # fail closed
        self.assertIn("uncommitted", out["error"])
        subprocess.run(["git", "add", "-A"], cwd=agent_ws)
        subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                        "commit", "-qm", "t1"], cwd=agent_ws)
        loop.submit(ws, "pass", task_id="t1")
        out2 = loop.gate(ws, "pass", task_id="t1")
        self.assertTrue(out2.get("built"))             # now accepted

    def test_em_survives_removed_worktrees(self):
        ws = TestParallelExecution._ws(TestParallelExecution())
        st = loop.load(ws)
        for t in st["tasks"]:
            t["status"] = "passed"
            t["workspace"] = os.path.join(ws, ".tp-work", "gone")  # removed
        st["step"] = "em"
        loop.save(ws, st)
        act = loop.next_action(ws)                     # must not crash
        self.assertEqual(act["step"], "em")


class TestSerialClaimRefusal(unittest.TestCase):
    """A1 (R-0007): `claim` fails closed on a serial loop — a direct claim
    used to form a wave whose submits deadlock (decision 0011)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _trace(self, ws):
        path = os.path.join(ws, ".taskplane", "trace.jsonl")
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f]

    def test_two_serial_claims_both_refused_with_remedy_and_trace(self):
        # decision 0011 replayed as the criterion pins it: TWO claims (a
        # would-be wave) under serial mode, BOTH refused fail-closed
        task2 = dict(TASK, id="t2")
        ws = git_ws(self.tmp, [TASK, task2])
        loop.init(ws, "g", spec_path="s", checkpoints=["em"])  # serial
        loop.next_action(ws); loop.gate(ws, "pass")            # plan → execute
        for tid in ("t1", "t2"):
            out = loop.claim(ws, tid, os.path.join(ws, ".tp-work", tid))
            self.assertIn("error", out)
            # the remedy is NAMED: re-init parallel, or the serial path
            self.assertIn("--parallel", out["error"])
            self.assertIn("loop next", out["error"])
        # traced with the named reason, once per refused claim
        blocked = [e for e in self._trace(ws)
                   if e.get("event") == "loop_claim_blocked"]
        self.assertEqual([e.get("task") for e in blocked], ["t1", "t2"])
        self.assertEqual({e.get("reason") for e in blocked}, {"serial_mode"})
        # fail closed BEFORE any claim side effect: statuses still pending,
        # no contract slot activated, no worktree created
        for t in loop.load(ws)["tasks"]:
            self.assertEqual(t.get("status", "pending"), "pending")
            self.assertIsNone(t.get("workspace"))
        self.assertFalse(os.path.isdir(os.path.join(ws, ".tp-work")))

    def test_claim_still_works_on_parallel_loop(self):
        # the non-refusal path: the same claim call on a --parallel loop
        ws = TestParallelExecution._ws(TestParallelExecution())
        agent_ws = os.path.join(ws, ".tp-work", "t1")
        subprocess.run(["git", "worktree", "add", "-q", agent_ws, "-b",
                        "tp/t1"], cwd=ws)
        out = loop.claim(ws, "t1", agent_ws)
        self.assertEqual(out.get("claimed"), "t1")
        self.assertEqual(loop.load(ws)["tasks"][0]["status"], "running")


class TestPlanOrderingGate(unittest.TestCase):
    """B2 (R-0008): brief-shape tasks (taskplane/lens.py, lens_signals.py,
    tp.py) must be transitive dependency ancestors of every golden-brief
    regen task (taskplane/tests/fixtures/briefs/**) — enforced mechanically
    at BOTH plan transitions (the plan GATE and plan_approval approve), not
    by planner memory: a loop initialized without the 'plan' checkpoint
    goes plan→execute at the gate and must be refused THERE."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    SHAPE = {"id": "s1", "scope": ["taskplane/lens.py"], "tests": "true",
             "criteria": ["shape"]}
    GOLD = {"id": "g1", "scope": ["taskplane/tests/fixtures/briefs/**"],
            "tests": "true", "criteria": ["golden"],
            "new_modules": ["taskplane/tests"]}

    def _plan_ws(self, tasks, checkpoints=("plan",)):
        ws = git_ws(self.tmp, tasks)
        # the surfaces must exist so the plan's scope maps to real files
        os.makedirs(os.path.join(ws, "taskplane", "tests", "fixtures",
                                 "briefs"), exist_ok=True)
        for f in ("lens.py",):
            open(os.path.join(ws, "taskplane", f), "w", encoding="utf-8").write("x=1\n")
        subprocess.run(["git", "add", "-A"], cwd=ws)
        subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                        "commit", "-qm", "surfaces"], cwd=ws)
        loop.init(ws, "g", spec_path="s", checkpoints=list(checkpoints))
        loop.next_action(ws)
        return ws

    def _trace_events(self, ws, event):
        with open(os.path.join(ws, ".taskplane", "trace.jsonl"), encoding="utf-8") as f:
            return [json.loads(line) for line in f
                    if f'"{event}"' in line]

    def test_violating_plan_is_refused_at_the_gate_naming_both_tasks(self):
        ws = self._plan_ws([self.SHAPE, dict(self.GOLD)])      # no dep
        out = loop.gate(ws, "pass")
        self.assertIn("error", out)
        self.assertIn("plan ordering", out["error"])
        self.assertIn("s1", out["error"])                # offender named
        self.assertIn("g1", out["error"])                # offender named
        self.assertEqual(loop.load(ws)["step"], "plan")  # held at plan
        blocked = self._trace_events(ws, "loop_gate_blocked")
        self.assertTrue(blocked)
        self.assertEqual(blocked[-1].get("reason"), "ordering")

    def test_no_plan_checkpoint_loop_cannot_bypass_the_rule(self):
        # the reproduced bypass: `loop init --checkpoints em` has no
        # plan_approval step — the gate transitions plan→execute directly
        # and used to skip the ordering rule entirely
        ws = self._plan_ws([self.SHAPE, dict(self.GOLD)],
                           checkpoints=("em",))
        out = loop.gate(ws, "pass")
        self.assertIn("error", out)
        self.assertIn("plan ordering", out["error"])
        self.assertIn("s1", out["error"])
        self.assertIn("g1", out["error"])
        st = loop.load(ws)
        self.assertEqual(st["step"], "plan")             # NOT execute
        blocked = self._trace_events(ws, "loop_gate_blocked")
        self.assertEqual(blocked[-1].get("reason"), "ordering")

    def test_violating_plan_is_refused_at_approve_too(self):
        # belt and suspenders: the plan_approval transition keeps its own
        # enforcement (a hand-edited state cannot sneak past approve)
        ws = self._plan_ws([self.SHAPE, dict(self.GOLD, deps=["s1"])])
        loop.gate(ws, "pass")                            # ordered → approval
        self.assertEqual(loop.load(ws)["step"], "plan_approval")
        st = loop.load(ws)
        st["tasks"][1]["deps"] = []                      # de-order in state
        loop.save(ws, st)
        out = loop.approve(ws, by="human")
        self.assertIn("error", out)
        self.assertIn("plan ordering", out["error"])
        self.assertIn("s1", out["error"])
        self.assertIn("g1", out["error"])
        self.assertEqual(loop.load(ws)["step"], "plan_approval")  # held
        blocked = self._trace_events(ws, "loop_approve_blocked")
        self.assertEqual(blocked[-1].get("reason"), "ordering")

    def test_declared_dependency_passes_the_gate(self):
        ws = self._plan_ws([self.SHAPE, dict(self.GOLD, deps=["s1"])])
        loop.gate(ws, "pass")                            # plan → approval
        self.assertEqual(loop.load(ws)["step"], "plan_approval")
        out = loop.approve(ws, by="human")
        self.assertNotIn("error", out)
        self.assertEqual(loop.load(ws)["step"], "execute")

    def test_transitive_dependency_satisfies_the_rule(self):
        mid = {"id": "m1", "scope": ["src/todo/**"], "tests": "true",
               "criteria": ["mid"], "deps": ["s1"]}
        errs = tp.plan_ordering_errors(
            [self.SHAPE, mid, dict(self.GOLD, deps=["m1"])])
        self.assertEqual(errs, [])

    def test_same_task_touching_both_surfaces_is_ordered(self):
        both = {"id": "b1", "tests": "true", "criteria": ["b"],
                "scope": ["taskplane/tp.py",
                          "taskplane/tests/fixtures/briefs/**"]}
        self.assertEqual(tp.plan_ordering_errors([both]), [])

    def test_catch_all_scopes_do_not_synthesize_an_unsatisfiable_cycle(self):
        """EM (v3 phase 3): _scope_touches matches stems in BOTH directions,
        so a catch-all scope landed in the shape set AND the golden set at
        once. Two such tasks then demanded that each depend on the other —
        a cycle no plan can satisfy, dead-ending an already-planned loop at
        the human approval gate with no --force path.

        A task in both sets carries both halves itself and is self-ordered,
        which is what the single-task case already recognised."""
        for scope in (["**"], ["taskplane/**"], ["*"]):
            plan = [{"id": "t1", "scope": scope, "tests": "true",
                     "criteria": ["a"], "deps": []},
                    {"id": "t2", "scope": scope, "tests": "true",
                     "criteria": ["b"], "deps": []}]
            self.assertEqual(tp.plan_ordering_errors(plan), [], scope)

    def test_a_both_task_alongside_a_narrow_golden_task_is_not_paired(self):
        both = {"id": "b1", "scope": ["taskplane/**"], "tests": "true",
                "criteria": ["b"], "deps": []}
        self.assertEqual(tp.plan_ordering_errors([both, self.GOLD]), [])

    def test_the_real_phase2_gap_is_still_caught_after_the_fix(self):
        """The regression this gate exists for — t6 (brief shape) parallel
        to t7 (golden regen), two DISJOINT scopes — must still refuse."""
        errs = tp.plan_ordering_errors([self.SHAPE, self.GOLD])
        self.assertEqual(len(errs), 1)
        self.assertIn("s1", errs[0])
        self.assertIn("g1", errs[0])

    def test_the_refusal_names_scope_narrowing_as_a_remedy(self):
        """The old text named only 'add the dep or re-plan' — which for a
        catch-all pair was the one remedy that could not work."""
        errs = tp.plan_ordering_errors([self.SHAPE, self.GOLD])
        self.assertIn("narrow the scopes", errs[0])
        self.assertIn("deps", errs[0])

    def test_the_refusal_says_why_there_is_no_force(self):
        errs = tp.plan_ordering_errors([self.SHAPE, self.GOLD])
        self.assertIn("no --force", errs[0])
        self.assertIn("OLD brief shape", errs[0])

    def test_violation_detected_transitively_not_just_directly(self):
        gold = dict(self.GOLD, deps=["u1"])              # dep, but not on s1
        unrelated = {"id": "u1", "scope": ["src/todo/**"], "tests": "true",
                     "criteria": ["u"]}
        errs = tp.plan_ordering_errors([self.SHAPE, unrelated, gold])
        self.assertEqual(len(errs), 1)
        self.assertIn("g1", errs[0]); self.assertIn("s1", errs[0])

    def test_phase3_plan_shape_passes(self):
        # the shipped Phase 3 plan (ids/deps/scopes) — the rule governs the
        # phase's own plan and must accept it
        plan = [
            {"id": "t1", "deps": [], "scope": [
                "taskplane/loop.py", "taskplane/taskplane_lite.py",
                "taskplane/audit.py", "taskplane/tests/test_loop.py",
                "taskplane/tests/test_dor_dod.py",
                "taskplane/tests/test_audit_sweep.py"]},
            {"id": "t2", "deps": ["t1", "t3"], "scope": [
                "taskplane/loop.py", "taskplane/tp.py",
                "taskplane/taskplane_lite.py",
                "taskplane/tests/test_loop.py"]},
            {"id": "t3", "deps": [], "scope": [
                "taskplane/tp.py", "taskplane/decompose.py",
                "taskplane/tests/test_stage_waves.py",
                "taskplane/tests/test_codex_compat.py",
                "taskplane/tests/test_decompose.py"]},
            {"id": "t4", "deps": [], "scope": [
                "taskplane/requirements.py",
                "taskplane/tests/test_requirements.py",
                "taskplane/tests/fixtures/calibration/**"]},
            {"id": "t5", "deps": ["t3"], "scope": [
                "taskplane/decompose.py", "taskplane/lens.py",
                "taskplane/lens_signals.py", "taskplane/depgraph.py",
                "taskplane/tests/test_decompose.py",
                "taskplane/tests/test_lens_route_v2.py",
                "taskplane/tests/test_lens_signals_fixtures.py",
                "taskplane/tests/test_dashboard_v2.py",
                "taskplane/tests/fixtures/decompose/**",
                "taskplane/tests/fixtures/detectors/**"]},
            {"id": "t6", "deps": ["t2"], "scope": [
                "taskplane/taskplane_lite.py",
                "taskplane/tests/test_governance_invariants.py"]},
            {"id": "t7", "deps": [], "scope": [
                "skills/taskplane/SKILL.md", "skills/tp-go/SKILL.md",
                "references/harness-rules.md",
                "taskplane/tests/test_release_freshness.py"]},
            {"id": "t8", "deps": ["t2", "t7"], "scope": [
                "taskplane/tp.py", "docs/cli-reference.md",
                ".github/workflows/ci.yml",
                "taskplane/tests/test_release_freshness.py"]},
            {"id": "t9", "deps": ["t4", "t6", "t8"], "scope": [
                "taskplane/loop.py",
                ".github/workflows/ci.yml", "taskplane/tests/conftest.py",
                "taskplane/tests/test_runner_isolation.py",
                "taskplane/tests/test_*.py"]},
            {"id": "t10", "deps": ["t2", "t5", "t8", "t9"], "scope": [
                "taskplane/tp.py", "taskplane/tests/test_stage_waves.py",
                "taskplane/tests/test_codex_compat.py",
                "taskplane/tests/fixtures/briefs/**"]},
        ]
        self.assertEqual(tp.plan_ordering_errors(plan), [])


def _trace_events(ws, event=None):
    with open(os.path.join(ws, ".taskplane", "trace.jsonl"), encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return [r for r in rows if event is None or r.get("event") == event]


_VOLATILE = {"ts", "time", "now", "duration", "elapsed", "seconds"}


def _scrub(obj):
    """Wall-clock stamps are the only legitimate run-to-run difference when
    the same workspace bytes are gated twice; everything else must match."""
    if isinstance(obj, dict):
        # Progress is an observational projection over the audit stream, not
        # gate state. Replaying the same gate necessarily samples a different
        # elapsed value; the dashboard delivery digests then differ only
        # because that sampled projection is rendered into its payload. The
        # progress/delivery contracts have their own exact tests, while this
        # differential proves the engine-skew precheck changes no workflow
        # outcome.
        if obj.get("schema") == "taskplane.status-progress/v1":
            return "<live-progress>"
        if obj.get("schema") == "taskplane.dashboard-delivery/v1":
            return "<dashboard-delivery>"
        return {k: ("<t>" if k.endswith("_at") or k in _VOLATILE
                    else _scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub(x) for x in obj]
    return obj


class TestEngineSkewRefusal(unittest.TestCase):
    """A4 (R-0007, decision 0018): the evaluate gate refuses evidence that a
    DIFFERENT engine build produced.

    Recorded scenario replayed here (Phase 2 retro lesson 2 — the t7
    topology): a parallel-wave worker builds and evaluates inside its own
    worktree whose checkout of taskplane/ is AHEAD of the primary's; the
    orchestrator then gates in the primary. `_evaluation_errors` therefore
    ran under one build and judged evidence produced under another, so the
    verdict depended on WHICH process ran rather than on the evidence.

    STATED DESIGN LIMIT: the comparison is between the PRODUCING process and
    the VALIDATING process — producer-vs-validator skew, the t7 topology.
    It is not an authenticity check on the evidence file itself: a
    hand-authored .eval/verdict.json claiming some third engine is NOT what
    this detects. That case stays covered by the existing guards — the
    submission staleness re-attest binds verdict.json's bytes to the
    submission fingerprint, and the DoD/_evaluation_errors walk validates
    its content. Escalation path if it ever must be closed: stamp the
    engine fingerprint into .eval/verdict.json itself.
    """

    SURFACE = {"loop", "loop_status", "taskplane_lite", "audit", "lens", "lens_signals",
               "design_contract", "depgraph", "decompose", "requirements",
               "runtime_eval"}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _wave_ws(self):
        """A one-task parallel wave parked at EVALUATE with the worker's
        evidence written in its worktree — the t7 topology, minus the second
        engine (which the stamp stands in for)."""
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "g", spec_path="s", checkpoints=["plan"], parallel=True)
        loop.next_action(ws)
        loop.gate(ws, "pass")                       # plan → plan_approval
        loop.approve(ws)                            # → execute (wave)
        agent_ws = os.path.join(ws, ".tp-work", "t1")
        subprocess.run(["git", "worktree", "add", "-q", agent_ws, "-b",
                        "tp/t1"], cwd=ws)
        loop.claim(ws, "t1", agent_ws)
        # t00 made the claimed worktree graph authoritative and removed the
        # stale-primary fallback. This fixture must establish the same
        # target-bound graph precondition as every real parallel evaluation.
        depgraph.scan(agent_ws)
        submit_gate(ws, "pass", task_id="t1")       # built
        loop.next_action(ws)                        # → evaluate
        write_kernel_results(ws)
        write_verdict(ws)
        return ws

    def _restamp(self, ws, fingerprint):
        """Stand in for the second engine: rewrite ONLY the submission's
        engine stamp, leaving the evidence and its fingerprint untouched."""
        st = loop.load(ws)
        if fingerprint is None:
            st["_submission"].pop("engine_fingerprint", None)
        else:
            st["_submission"]["engine_fingerprint"] = fingerprint
        loop.save(ws, st)

    def _real_engine_wave_ws(self):
        """Build the merge-and-resubmit topology with real engine bytes."""
        ws = git_ws(self.tmp, [TASK])
        engine_root = os.path.join(ws, "taskplane")
        os.makedirs(engine_root)
        source_root = os.path.dirname(os.path.abspath(loop.__file__))
        for name in tp.VALIDATOR_SURFACE:
            shutil.copy(os.path.join(source_root, name + ".py"), engine_root)
        subprocess.run(["git", "add", "-A"], cwd=ws, check=True)
        subprocess.run(["git", "commit", "-qm", "engine baseline"],
                       cwd=ws, check=True)

        loop.init(ws, "g", spec_path="s", checkpoints=["plan"],
                  parallel=True)
        loop.next_action(ws)
        loop.gate(ws, "pass")
        loop.approve(ws)
        agent_ws = os.path.join(ws, ".tp-work", "t1")
        subprocess.run(["git", "worktree", "add", "-q", agent_ws, "-b",
                        "tp/t1"], cwd=ws, check=True)
        loop.claim(ws, "t1", agent_ws)
        with open(os.path.join(agent_ws, "src", "todo", "a.py"), "w",
                  encoding="utf-8") as handle:
            handle.write("x=2\n")
        subprocess.run(["git", "add", "-A"], cwd=agent_ws, check=True)
        subprocess.run(["git", "commit", "-qm", "task change"],
                       cwd=agent_ws, check=True)
        depgraph.scan(agent_ws)
        submit_gate(ws, "pass", task_id="t1")
        loop.next_action(ws)
        write_kernel_results(ws)
        review.collect_review(agent_ws, publish=False)
        write_verdict(ws)
        return ws, agent_ws

    def test_fingerprint_is_the_validator_surface_bytes_not_its_paths(self):
        fp = tp.engine_fingerprint()
        self.assertEqual(fp, tp.engine_fingerprint())        # deterministic
        self.assertRegex(fp, r"^[0-9a-f]{64}$")
        self.assertEqual(set(tp.VALIDATOR_SURFACE), self.SURFACE)
        here = os.path.dirname(os.path.dirname(os.path.abspath(loop.__file__)))
        here = os.path.join(here, "taskplane")
        copy = os.path.join(self.tmp, "engine-copy")
        os.makedirs(copy)
        for name in tp.VALIDATOR_SURFACE:
            shutil.copy(os.path.join(here, name + ".py"), copy)
        fake = {n: types.SimpleNamespace(
            __file__=os.path.join(copy, n + ".py"))
            for n in tp.VALIDATOR_SURFACE}
        with unittest.mock.patch.dict(sys.modules, fake):
            # same bytes at a different path (worktree vs primary checkout)
            self.assertEqual(tp.engine_fingerprint(), fp)
            for name in sorted(tp.VALIDATOR_SURFACE):
                path = os.path.join(copy, name + ".py")
                original = open(path, "rb").read()
                with open(path, "ab") as f:
                    f.write(b"\n# newer engine\n")
                self.assertNotEqual(tp.engine_fingerprint(), fp,
                                    f"{name} is not in the fingerprint")
                open(path, "wb").write(original)
            self.assertEqual(tp.engine_fingerprint(), fp)

    def test_recorded_t7_skew_is_refused_then_gates_through_after_merge(self):
        ws = self._wave_ws()
        loop.submit(ws, "pass")
        evidence = json.loads(json.dumps(loop.load(ws)["_submission"]))
        self.assertEqual(evidence["engine_fingerprint"],
                         tp.engine_fingerprint())
        # the worktree engine is ahead of the primary validator
        self._restamp(ws, "f" * 64)
        before = json.dumps(loop.load(ws), sort_keys=True)
        out = loop.gate(ws, "pass")
        self.assertIn("error", out)
        self.assertIn("different engine build", out["error"])
        self.assertIn("git merge tp/t1", out["error"])
        self.assertIn("loop submit", out["error"])          # named remedy
        # `reason` distinguishes the running-engine mismatch from the
        # workspace-engine one the A4 repair added (R-0013): the payload has
        # to say WHICH pair diverged, or the message quotes two hashes with
        # no way to tell what was compared.
        self.assertEqual(out["engine_skew"],
                         {"submitted": "f" * 64,
                          "validator": tp.engine_fingerprint(),
                          "reason": "engine_skew"})
        # no transition, no state change at all — the task stays evaluable
        self.assertEqual(json.dumps(loop.load(ws), sort_keys=True), before)
        self.assertEqual(loop.load(ws)["step"], "evaluate")
        blocked = _trace_events(ws, "loop_gate_blocked")[-1]
        self.assertEqual(blocked["reason"], "engine_skew")
        self.assertEqual(blocked["submitted"], "f" * 64)
        self.assertEqual(blocked["validator"], tp.engine_fingerprint())
        # "merge tp/t1 into the primary": one engine now owns production and
        # validation. The evidence is IDENTICAL — a re-evaluation is never
        # stranded by the refusal.
        self._restamp(ws, tp.engine_fingerprint())
        self.assertEqual(loop.load(ws)["_submission"], evidence)
        out2 = loop.gate(ws, "pass")
        self.assertNotIn("error", out2)
        self.assertEqual(loop.load(ws)["step"], "em")

    def test_absent_stamp_is_refused_and_a_resubmit_restamps(self):
        """ABSENT = REFUSE (fail-closed), with the in-flight case handled:
        a submission recorded by a pre-A4 engine carries no stamp, and the
        remedy that clears it is the same `loop submit` — so submit's
        idempotence key includes engine_fingerprint, otherwise the unstamped
        record would be kept and the loop stranded."""
        ws = self._wave_ws()
        loop.submit(ws, "pass")
        self._restamp(ws, None)                 # pre-A4 in-flight submission
        out = loop.gate(ws, "pass")
        self.assertIn("error", out)
        self.assertIn("different engine build", out["error"])
        self.assertIn("no engine fingerprint", out["error"])
        self.assertIn("git merge tp/t1", out["error"])
        self.assertIsNone(out["engine_skew"]["submitted"])
        self.assertEqual(loop.load(ws)["step"], "evaluate")
        self.assertEqual(_trace_events(ws, "loop_gate_blocked")[-1]["reason"],
                         "engine_skew")
        again = loop.submit(ws, "pass")
        self.assertEqual(again["submission"]["engine_fingerprint"],
                         tp.engine_fingerprint())
        self.assertNotIn("error", loop.gate(ws, "pass"))
        self.assertEqual(loop.load(ws)["step"], "em")

    def test_merge_and_byte_identical_reevidence_replaces_worker_engine_stamp(
            self):
        """The documented merge+resubmit remedy works before cleanup.

        The task worktree branches before a primary-only engine fix.  Its
        unmerged submission must retain the worktree's older producer stamp
        and be refused.  Once the exact task target is merged, regenerating
        byte-identical canonical evidence and resubmitting must replace the
        cached submission metadata with the primary validator's engine even
        while the clean, older worktree still exists.
        """
        ws, agent_ws = self._real_engine_wave_ws()
        verdict_path = os.path.join(agent_ws, ".eval", "verdict.json")
        original_verdict = open(verdict_path, "rb").read()
        worker_engine = tp.workspace_engine_fingerprint(agent_ws)

        with open(os.path.join(ws, "taskplane", "loop.py"), "a",
                  encoding="utf-8") as handle:
            handle.write("\n# primary validator fix\n")
        subprocess.run(["git", "add", "taskplane/loop.py"], cwd=ws,
                       check=True)
        subprocess.run(["git", "commit", "-qm", "primary engine fix"],
                       cwd=ws, check=True)
        primary_engine = tp.workspace_engine_fingerprint(ws)
        self.assertNotEqual(worker_engine, primary_engine)

        on_path = {"schema": "taskplane.runtime-guidance/v1",
                   "status": "on_path", "step": "evaluate"}
        with unittest.mock.patch.object(loop.runtime_eval, "guide_loop",
                                        return_value=on_path), \
                unittest.mock.patch.object(loop.time, "time",
                                            return_value=100):
            first_result = loop.submit(ws, "pass")
        self.assertNotIn("error", first_result, first_result)
        first = first_result["submission"]
        self.assertEqual(first["evidence_engine_fingerprint"], worker_engine)
        self.assertEqual(first["submitted_at"], 100)
        refused = loop.gate(ws, "pass")
        self.assertEqual(
            refused["engine_skew"]["reason"], "engine_skew_workspace")
        self.assertEqual(loop.load(ws)["step"], "evaluate")

        subprocess.run(["git", "merge", "--no-ff", "-m", "merge task",
                        "tp/t1"], cwd=ws, check=True)
        target = loop.load(ws)["tasks"][0]["target_commit"]
        self.assertEqual(tp.git_head(agent_ws), target)
        self.assertEqual(subprocess.run(
            ["git", "merge-base", "--is-ancestor", target, "HEAD"], cwd=ws,
            check=False).returncode, 0)

        os.unlink(verdict_path)
        token = loop._EVIDENCE_STATE_WORKSPACE.set(ws)
        try:
            self.assertTrue(loop.evidence(agent_ws, write=True)["written"])
        finally:
            loop._EVIDENCE_STATE_WORKSPACE.reset(token)
        write_verdict(ws)
        self.assertEqual(open(verdict_path, "rb").read(), original_verdict)

        with unittest.mock.patch.object(loop.runtime_eval, "guide_loop",
                                        return_value=on_path), \
                unittest.mock.patch.object(loop.time, "time",
                                            return_value=200):
            second = loop.submit(ws, "pass")["submission"]
        self.assertEqual(second["fingerprint"], first["fingerprint"])
        self.assertEqual(
            second["evidence_engine_fingerprint"], primary_engine)
        self.assertEqual(second["submitted_at"], 200)
        self.assertNotEqual(second, first)
        # This regression owns submission identity and the engine-skew
        # pre-check. The synthetic repository does not carry the full host
        # producer-receipt fixture needed by the independent evaluation walk.
        with unittest.mock.patch.object(loop, "_evaluation_errors",
                                        return_value=[]):
            self.assertNotIn("error", loop.gate(ws, "pass"))
        self.assertEqual(loop.load(ws)["step"], "em")

    def test_no_submission_record_is_not_this_guard_s_business(self):
        """The stamp governs a submission RECORD. A loop with no submission
        at all is the submission_required gate's refusal (already enforced
        above this pre-check) — this guard must not invent a second, weaker
        one, and legacy loops without the flag stay resumable."""
        self.assertIsNone(tp.engine_skew_refusal(self.tmp, None))
        self.assertIsNone(tp.engine_skew_refusal(self.tmp, {}))

    def test_the_comparison_runs_before_the_evaluation_walk(self):
        """Pure PRE-check: it can only refuse more, never validate less."""
        import inspect
        src = inspect.getsource(loop.gate)
        self.assertLess(src.index("engine_skew_refusal"),
                        src.index("_evaluation_errors("))

    def test_equal_fingerprint_gate_is_byte_identical_to_the_pre_a4_flow(self):
        """NON-SKEW DIFFERENTIAL: gate the SAME workspace bytes twice — once
        with the pre-check removed entirely (the pre-A4 flow), once with it
        live — and require identical results, identical post-state and an
        identical trace. Wall-clock stamps are the only scrubbed difference.
        """
        ws = self._wave_ws()
        loop.submit(ws, "pass")
        # the gate reads/writes the workspace AND the per-user state dir
        backup = os.path.join(self.tmp, "backup")
        state_backup = os.path.join(self.tmp, "backup-state")
        shutil.copytree(ws, backup, symlinks=True)
        shutil.copytree(loop.state_dir(ws), state_backup, symlinks=True)
        real = tp.engine_skew_refusal
        tp.engine_skew_refusal = lambda *a, **kw: None       # today's engine
        try:
            today_out = loop.gate(ws, "pass")
            today_state = loop.load(ws)
            today_trace = _trace_events(ws)
        finally:
            tp.engine_skew_refusal = real
        state = loop.state_dir(ws)
        shutil.rmtree(ws)
        shutil.rmtree(state)
        shutil.copytree(backup, ws, symlinks=True)
        shutil.copytree(state_backup, state, symlinks=True)
        a4_out = loop.gate(ws, "pass")
        a4_state = loop.load(ws)
        a4_trace = _trace_events(ws)
        self.assertNotIn("error", a4_out)
        self.assertEqual(a4_state["step"], "em")             # not vacuous:
        self.assertGreater(len(a4_trace), 2)                 # a real gate ran
        self.assertGreater(len(_scrub(a4_out)), 1)
        self.assertEqual(_scrub(today_out), _scrub(a4_out))
        self.assertEqual(_scrub(today_state), _scrub(a4_state))
        self.assertEqual(_scrub(today_trace), _scrub(a4_trace))
        self.assertEqual([r for r in a4_trace
                          if r.get("reason") == "engine_skew"], [])


class TestStatelessReviewContractBootstrap(unittest.TestCase):
    """Focused selector for the stateless signed-action regression."""

    setUp = TestLoop.setUp
    _setup_stateless_review_contract = \
        TestLoop._setup_stateless_review_contract
    _issue = TestLoop._issue
    _activate = TestLoop._activate
    test_fresh_worker_activates_without_hook_or_active_file = \
        TestLoop._assert_fresh_worker_activates_without_hook_or_active_file
    test_tamper_stale_identity_replay_and_write_broadening_fail_closed = \
        TestLoop._assert_tamper_stale_identity_replay_and_write_broadening_fail_closed
    test_loop_binds_worker_action_to_each_immutable_review_slot = \
        TestLoop._assert_loop_binds_worker_action_to_each_immutable_review_slot
    test_managed_worktree_result_is_exact_and_fail_closed = \
        TestLoop._assert_managed_worktree_result_is_exact_and_fail_closed


class TestReviewBridge(unittest.TestCase):
    def test_review_bridge_checkout_bound_main_reloads_target_runtime(self):
        checkout = tempfile.mkdtemp()
        package = os.path.join(checkout, "taskplane")
        os.makedirs(package)
        with open(os.path.join(package, "taskplane_lite.py"), "w",
                  encoding="utf-8") as stream:
            stream.write("ORIGIN = 'target-checkout'\n")

        missing = object()
        prior_lite = sys.modules.get("taskplane_lite", missing)
        prior_package = sys.modules.get("taskplane", missing)
        prior_popen = subprocess.Popen
        prior_path = list(sys.path)
        prior_argv = list(sys.argv)
        try:
            tp._checkout_bound_main(checkout, [
                "-c", "import taskplane_lite; "
                "assert taskplane_lite.ORIGIN == 'target-checkout'",
            ])
        finally:
            subprocess.Popen = prior_popen
            sys.path[:] = prior_path
            sys.argv[:] = prior_argv
            for name, prior in (("taskplane_lite", prior_lite),
                                ("taskplane", prior_package)):
                if prior is missing:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = prior
            shutil.rmtree(checkout)

    def test_review_bridge_execute_gate_uses_safe_argv(self):
        completed = subprocess.CompletedProcess(
            ["python3", "-m", "pytest", "-q"], 0, "", "")
        with unittest.mock.patch(
                "subprocess.run", return_value=completed) as invoked:
            with loop._claimed_execute_suite_binding():
                result = tp.run_suite_command(
                    ".", "python3 -m pytest -q")
        self.assertEqual(result.returncode, 0)
        argv = invoked.call_args.args[0]
        self.assertEqual(argv, ["python3", "-m", "pytest", "-q"])
        self.assertFalse(invoked.call_args.kwargs["shell"])

    def test_review_bridge_execute_gate_rejects_shell_operators(self):
        with unittest.mock.patch("subprocess.run") as invoked:
            with loop._claimed_execute_suite_binding():
                result = tp.run_suite_command(
                    ".", "python3 -m pytest && touch escaped")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("shell operators", result.stderr)
        invoked.assert_not_called()
