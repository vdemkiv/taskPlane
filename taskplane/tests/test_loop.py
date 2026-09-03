import json
import hashlib
import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import unittest.mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loop  # noqa: E402
import taskplane_lite as tp  # noqa: E402
import lens  # noqa: E402
import depgraph  # noqa: E402
import evaluator_health  # noqa: E402
import evaluation_output  # noqa: E402
import evaluate_child_evidence  # noqa: E402
import review  # noqa: E402
import review_evidence  # noqa: E402
import runnability  # noqa: E402
import storage as runtime_storage  # noqa: E402
import checkpoint  # noqa: E402
import build_c  # noqa: E402
from tests import run_lr10_parallel as lr10_runner  # noqa: E402
from tests.root_session_fixture import open_delivery_root  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_loop_test_runtime(monkeypatch):
    """Give each loop journey a stable host identity and isolated store."""
    from taskplane import evaluate_child_evidence as packaged_evidence

    monkeypatch.delenv("TASKPLANE_NO_SUITE_CACHE", raising=False)
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "test-loop-session")
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    original_load_locator = runtime_storage.load_workspace_locator
    original_write_locator = runtime_storage.write_workspace_locator
    locator_cache = {}

    def load_locator_once(checkout):
        root = os.path.realpath(checkout)
        if root in locator_cache:
            if not os.path.exists(locator_cache[root]):
                return None
            locator_cache.pop(root)
        value = original_load_locator(checkout)
        if value is None:
            try:
                locator_cache[root] = runtime_storage._locator_path(checkout)
            except runtime_storage.StorageIdentityError:
                pass
        return dict(value) if isinstance(value, dict) else value

    def write_locator_and_invalidate(checkout, **kwargs):
        result = original_write_locator(checkout, **kwargs)
        locator_cache.pop(os.path.realpath(checkout), None)
        return result

    monkeypatch.setattr(
        runtime_storage, "load_workspace_locator", load_locator_once)
    monkeypatch.setattr(
        runtime_storage, "write_workspace_locator", write_locator_and_invalidate)
    original_next_action = loop.next_action
    original_wave = loop.wave

    def with_open_root(fn, ws, *args, **kwargs):
        state = loop.load(ws) or {}
        root = state.get("root_hygiene")
        if state.get("step") == "execute" or (
                isinstance(root, dict)
                and root.get("status") in {"prepared", "open"}):
            kwargs.setdefault(
                "root_observation_authority", open_delivery_root(ws))
        return fn(ws, *args, **kwargs)

    def next_with_open_root(ws, *args, **kwargs):
        return with_open_root(original_next_action, ws, *args, **kwargs)

    def wave_with_open_root(ws, *args, **kwargs):
        return with_open_root(original_wave, ws, *args, **kwargs)

    next_with_open_root.__wrapped__ = getattr(
        original_next_action, "__wrapped__", original_next_action)
    wave_with_open_root.__wrapped__ = getattr(
        original_wave, "__wrapped__", original_wave)
    monkeypatch.setattr(loop, "next_action", next_with_open_root)
    monkeypatch.setattr(loop, "wave", wave_with_open_root)

    def quality_probe(_root, languages, **_kwargs):
        commands = (
            ("lint", "ruff", ["python3", "-m", "ruff", "check"]),
            ("format", "ruff", ["python3", "-m", "ruff", "format", "--check"]),
            ("strict-typing", "mypy", ["python3", "-m", "mypy", "--strict"]),
            ("security-static", "bandit",
             ["python3", "-m", "bandit", "-r", "src"]),
        )
        return [{
            "language": language, "fingerprint": "9" * 64,
            "checks": [{"id": check_id, "tool": tool, "argv": argv,
                        "tool_version": "test-version",
                        "verdict": runnability.RUNS}
                       for check_id, tool, argv in commands],
        } for language in languages]

    def governed_receipt(_workspace, authorization, handle, *,
                         assignment_binding, argv):
        assert authorization == "test-authority"
        payload = json.loads(handle.removeprefix("test:"))
        assert payload["argv"] == argv
        assert payload["task_id"] == assignment_binding["task_id"]
        return {
            "identity": {"run_id": payload["run_id"],
                         "task_id": payload["task_id"]},
            "source_sha": assignment_binding["candidate_sha"],
            "target_sha": assignment_binding["candidate_sha"],
            "plan_fingerprint": assignment_binding["plan_fingerprint"],
            "runtime_argv": argv, "state": "succeeded", "exit_code": 0,
            "receipt_digest": hashlib.sha256(handle.encode()).hexdigest(),
        }

    monkeypatch.setattr(
        runnability, "probe_language_quality_toolchains", quality_probe)
    monkeypatch.setattr(
        packaged_evidence.runnability,
        "probe_language_quality_toolchains", quality_probe)
    monkeypatch.setattr(
        evaluate_child_evidence.governed_commands,
        "governed_command_execution_evidence", governed_receipt)
    monkeypatch.setattr(
        packaged_evidence.governed_commands,
        "governed_command_execution_evidence", governed_receipt)


def _install_test_launcher(workspace):
    root = os.path.join(workspace, ".taskplane")
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "codex-hook.py"), "w",
              encoding="utf-8") as handle:
        handle.write("# stable repository-family test launcher\n")


def git_ws(tmp, tasks):
    import requirements as reqs

    ws = os.path.join(tmp, "ws")
    os.makedirs(os.path.join(ws, "plan"))
    os.makedirs(os.path.join(ws, "src", "todo"))
    os.makedirs(os.path.join(ws, "tests"))
    open(os.path.join(ws, "src", "todo", "a.py"), "w", encoding="utf-8").write("x=1\n")
    open(os.path.join(ws, "tests", "test_current_contract.py"), "w",
         encoding="utf-8").write(
             "from src.todo.a import complete\n\n"
             "def test_complete_marks_done():\n"
             "    assert complete() is True\n")
    subprocess.run(["git", "init", "-q"], cwd=ws)
    subprocess.run(["git", "config", "user.email", "e@e"], cwd=ws)
    subprocess.run(["git", "config", "user.name", "t"], cwd=ws)
    subprocess.run(["git", "add", "-A"], cwd=ws)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=ws)
    _install_test_launcher(ws)
    planned = json.loads(json.dumps(tasks))
    marked = [row for row in planned if row.get("req") == "R-TEST"]
    if marked:
        requirement = reqs.record_requirement(
            ws, "current complete contract", functional=["complete marks done"],
            acceptance=["complete() marks done"],
            context_files=["src/todo/**", "tests/test_current_contract.py"])
        for row in marked:
            row["req"] = requirement["id"]
    json.dump({"tasks": planned}, open(
        os.path.join(ws, "plan", "tasks.json"), "w", encoding="utf-8"))
    return ws


TASK_SELECTOR = "tests/test_current_contract.py::test_complete_marks_done"
TASK = {
    "id": "t1", "req": "R-TEST",
    "scope": ["src/todo/**", "tests/test_current_contract.py"],
    "tests": f"python3 -m pytest -q {TASK_SELECTOR}",
    "criteria": ["complete() marks done"],
    "evaluation_evidence_edges": [{
        "producer": "src/todo/a.py",
        "consumer": "tests/test_current_contract.py",
        "selector": TASK_SELECTOR,
        "freshness_inputs": ["candidate_sha", "source_tree"],
        "severed_edge": {
            "mutation": "remove complete from src.todo.a",
            "selector": TASK_SELECTOR,
        },
    }],
    "changed_interfaces": [], "classified_failures": [],
}


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
        for forbidden in ("wave", "build_lease", "build-lease",
                          "slot_lease", "lens_state", "evaluate", "fix"):
            self.assertNotIn(forbidden, encoded)
        for retired in ("reservation_fingerprint", "capability",
                        "event_contract", "scheduler_revision",
                        "execution_dag_head"):
            self.assertNotIn(retired, receipt)
            self.assertTrue(all(
                retired not in row for row in receipt["assignments"]))

    def test_scope_assignment_preserves_plan_order_without_host_scheduler(self):
        ws = os.path.dirname(os.path.abspath(build_c.__file__))
        ws = os.path.dirname(ws)
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ws,
            text=True, encoding="utf-8", errors="replace").strip()
        tasks = [
            {"id": "a-center", "scope": ["taskplane"], "deps": [],
             "status": "pending"},
            {"id": "b-left", "scope": ["taskplane/brief_projection.py"],
             "deps": [], "status": "pending"},
            {"id": "c-right", "scope": ["taskplane/plan_topology.py"],
             "deps": [], "status": "pending"},
        ]
        graph = {
            "modules": {
                "center": {"files": ["taskplane/loop.py"]},
                "left": {"files": ["taskplane/brief_projection.py"]},
                "right": {"files": ["taskplane/plan_topology.py"]},
            },
            "edges": [], "files": {}, "meta": {},
        }
        modules = {
            "taskplane": ["center"],
            "taskplane/brief_projection.py": ["left"],
            "taskplane/plan_topology.py": ["right"],
        }
        scope_modules = unittest.mock.patch(
            "build_c.depgraph.scope_modules",
            side_effect=lambda _ws, scope: modules[scope[0]])
        scope_modules.start()
        self.addCleanup(scope_modules.stop)
        receipt = build_c.assign_scopes(
            ws, {"tasks": tasks}, graph=graph,
            revision=revision,
            create_worktree=lambda _ws, task_id, _revision:
                os.path.join(self.tmp, task_id),
            register_worktree=lambda _ws, worker, task_id: {
                "schema": "taskplane.managed-task-worktree/v1",
                "task_id": task_id, "path": worker,
                "branch_tip": revision,
            },
            wait_policy_factory=lambda _name, count: {
                "schema": "taskplane.wait-policy/v1", "mode": "event",
                "scheduled_polling": False, "timeout_seconds": 1800,
                "reissue_after": ["completion", "attention"],
                "outstanding_count": count,
            },
            wait_invocation_factory=lambda _policy, members: {
                "schema": "taskplane.event-wait-invocation/v1",
                "operation": "wait_for_events", "scheduled": False,
                "reissue": False, "outstanding_members": members,
            })

        self.assertEqual(receipt["dispatch_set"]["members"],
                         ["a-center"])
        self.assertEqual([row["task_id"] for row in receipt["assignments"]],
                         ["a-center"])
        self.assertEqual(
            [row["task_id"] for row in receipt["serialized"]],
            ["b-left", "c-right"])

    def test_scope_assignment_uses_real_repository_and_storage_edges(self):
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.ws,
            text=True, encoding="utf-8", errors="replace").strip()

        tasks = [{
            "id": "t-live", "scope": ["src/a/**"], "deps": [],
            "status": "pending",
        }]
        receipt = build_c.assign_scopes(
            self.ws, {"tasks": tasks}, graph=self.graph,
            revision=revision)

        assignment = receipt["assignments"][0]
        self.assertTrue(os.path.isdir(assignment["worktree"]))
        self.assertEqual(subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=assignment["worktree"],
            text=True, encoding="utf-8", errors="replace").strip(), revision)
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
            text=True, encoding="utf-8", errors="replace").strip()
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
            text=True, encoding="utf-8", errors="replace").strip()

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
            text=True, encoding="utf-8", errors="replace").strip(), self.tip)

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
                    text=True, encoding="utf-8",
                    errors="replace").strip(), self.primary_before)

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
            text=True, encoding="utf-8", errors="replace").strip(),
            self.primary_before)

    def test_merge_on_green_severed_repository_edge_fails_closed(self):
        with unittest.mock.patch(
                "build_c.repository.RepositoryManager.merge_registered_task",
                side_effect=RuntimeError("severed integration edge")):
            with self.assertRaisesRegex(build_c.IntegrationAuthorizationError,
                                        "severed integration edge"):
                build_c.integrate_on_green(self.ws, self.task_id)
        self.assertEqual(subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.ws,
            text=True, encoding="utf-8", errors="replace").strip(),
            self.primary_before)

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
                text=True, encoding="utf-8", errors="replace").strip(),
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
    submit = getattr(loop.submit, "__wrapped__", loop.submit)
    gate = getattr(loop.gate, "__wrapped__", loop.gate)
    state = loop.load(ws) or {}
    task = loop._current_task(state)
    if outcome == "pass" and state.get("step") == "execute" and \
            not state.get("parallel") and \
            isinstance((task or {}).get("evaluation_evidence_edges"), list):
        producer = task["evaluation_evidence_edges"][0]["producer"]
        target = os.path.join(ws, producer)
        with open(target, encoding="utf-8") as stream:
            source = stream.read()
        if "def complete(" not in source:
            with open(target, "a", encoding="utf-8") as stream:
                stream.write("\ndef complete():\n    return True\n")
    if outcome == "pass" and state.get("step") == "evaluate":
        collect_zero_test_kernel(ws)
        with unittest.mock.patch.object(
                loop, "_collect_zero_lens_evaluate_before_guidance",
                return_value={"fingerprint": "a" * 64}), \
                unittest.mock.patch.object(
                    loop, "_producer_observation_errors", return_value=[]), \
                unittest.mock.patch.object(
                    loop.runtime_eval, "guide_loop",
                    return_value={"status": "on_path", "recovered": False}):
            submitted = submit(ws, outcome, task_id=task_id)
            if "error" in submitted:
                return submitted
            return gate(ws, outcome, task_id=task_id)
    if outcome == "pass" and state.get("step") == "em":
        with unittest.mock.patch.object(
                loop.runtime_eval, "guide_loop",
                return_value={"status": "on_path", "recovered": False}):
            submitted = submit(ws, outcome, task_id=task_id)
    else:
        submitted = submit(ws, outcome, task_id=task_id)
    if "error" in submitted:
        return submitted
    return gate(ws, outcome, task_id=task_id)


def write_verdict(ws):
    state = loop.load(ws)
    task = state["tasks"][state["current_task"]]
    act_ws = (task.get("workspace") if state.get("parallel") and
              state.get("step") == "evaluate" else None) or ws
    binding = loop.review_kernel_binding(state, "evaluate", task)
    kernel = (review._load_state(
        str(binding.get("workspace") or act_ws), binding["run_id"])
        if binding else None)
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
    verdict = {
        "schema": evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID,
        "task": task["id"],
        "requirement": task.get("req") or state.get("requirement_id") or "",
        "verdict": "pass",
        "evaluation": {"status": "complete", "reason_code": "none",
                       "detail": "durable evidence consumed"},
        "criteria": [{"criterion": c, "status": "met",
                      "evidence": "verified by test"} for c in criteria],
        "graph": {
            "dispositions": [
                {"node": node, "status": "tested",
                 "evidence": "covered by declared task tests"}
                for node in direct],
            "requirements_checked": affected,
            "contracts_checked": contracts,
        },
        "failures": [],
    }
    route = state.get("evaluate_child_evidence")
    if route:
        assignments = route["assignments"]
        results = _evaluate_evidence_results(assignments, route["run_id"])
        for assignment in assignments:
            kind = assignment["producer_kind"]
            loop.observe_evaluate_evidence_child_start(
                artifact_root=route["artifact_root"], assignment=assignment,
                dispatch_id="intent-" + kind,
                native_task_name="test-" + kind)
            loop.complete_evaluate_evidence_child(
                workspace=route["workspace"],
                artifact_root=route["artifact_root"], run_id=route["run_id"],
                assignment=assignment, result=results[kind], work_units=2)
        verdict = evaluation_output.attach_child_evidence(
            verdict, run_id=route["run_id"],
            evaluator_attempt_id=route["evaluator_attempt_id"],
            expected_binding=route["binding"])
    with open(os.path.join(act_ws, ".eval", "verdict.json"), "w",
              encoding="utf-8") as f:
        json.dump(verdict, f)


def _evidence_execution(assignment, run_id, argv, label):
    return {"authorization": "test-authority", "handle": "test:" + json.dumps(
        {"argv": argv, "run_id": run_id,
         "task_id": assignment["binding"]["task_id"], "label": label},
        sort_keys=True, separators=(",", ":"))}


def _evaluate_evidence_results(assignments, run_id):
    language = next(row for row in assignments if row["producer_kind"] ==
                    evaluate_child_evidence.LANGUAGE_PRODUCER)
    design = next(row for row in assignments if row["producer_kind"] ==
                  evaluate_child_evidence.TEST_DESIGN_PRODUCER)
    quality = {
        "schema": evaluate_child_evidence.LANGUAGE_RESULT_SCHEMA,
        "producer_kind": evaluate_child_evidence.LANGUAGE_PRODUCER,
        "reuse_key_digest": language["reuse_key_digest"],
        "language_coverage": [{
            "language": item["language"],
            "reference_id": item["reference"]["path"],
            "reference_sha256": item["reference"]["content_sha256"],
            "toolchain_fingerprint": item["toolchain_fingerprint"],
            "inspected_files": item["implementation_files"],
            "command_receipts": [
                _evidence_execution(
                    language, run_id, command["argv"],
                    "quality:" + command["id"])
                for command in item["required_commands"]],
            "findings": [],
        } for item in language["language_obligations"]],
    }
    obligations = design["test_obligations"]
    test_design = {
        "schema": evaluate_child_evidence.TEST_DESIGN_RESULT_SCHEMA,
        "producer_kind": evaluate_child_evidence.TEST_DESIGN_PRODUCER,
        "reuse_key_digest": design["reuse_key_digest"],
        "current_value": [{
            **test, "classification": "protects-current-contract",
            "execution": _evidence_execution(
                design, run_id,
                ["python3", "-m", "pytest", "-q", test["selector"]],
                "current:" + test["selector"]),
        } for test in obligations["tests"]],
        "producer_consumers": [{
            "producer": edge["producer"], "consumer": edge["consumer"],
            "selector": edge["selector"],
            "execution": _evidence_execution(
                design, run_id,
                ["python3", "-m", "pytest", "-q", edge["selector"]],
                "edge:" + edge["producer"]),
            "severed_edge_execution": _evidence_execution(
                design, run_id,
                ["python3", "-m", "pytest", "-q",
                 edge["severed_edge"]["selector"]],
                "severed:" + edge["producer"]),
        } for edge in obligations["producer_consumer_edges"]],
        "same_slice_fixtures": [{
            "producer": row["producer"], "path": row["fixture"]["path"],
            "slice": row["slice"],
        } for row in obligations["changed_interfaces"]],
        "failure_classifications": [{
            "id": row["id"], "classification": row["classification"],
            "reason": "classified before repair", "owner": "product-code",
            "cluster": "test-fixture",
        } for row in obligations["failures"]],
    }
    return {
        evaluate_child_evidence.LANGUAGE_PRODUCER: quality,
        evaluate_child_evidence.TEST_DESIGN_PRODUCER: test_design,
    }


def collect_zero_test_kernel(ws):
    state = loop.load(ws)
    task = state["tasks"][state["current_task"]]
    binding = loop.review_kernel_binding(state, "evaluate", task)
    if not binding:
        return
    kernel_ws = str(binding.get("workspace") or ws)
    kernel = review._load_state(kernel_ws, binding["run_id"])
    if kernel.get("status") != "ready" or \
            kernel.get("zero_lens_evaluation") is not True:
        return
    with open(runtime_storage.evaluation_path(kernel_ws),
              encoding="utf-8") as stream:
        verdict = json.load(stream)
    route = state.get("evaluate_child_evidence") or {}
    empty = review.collect_expected_set(
        run_id=binding["run_id"], task_id=task["id"], stage="Evaluate",
        expected_lenses=[], collected_lenses=[], result=verdict,
        result_validator=lambda value: evaluation_output.validate_evaluator_value(
            value, expected_lenses=[],
            expected_evidence_binding=route.get("binding") or {}),
        producer_observation_fingerprint="a" * 64)
    review.collect_review(
        kernel_ws, publish=False, run_id=binding["run_id"],
        empty_lens_collection=empty)


def pass_eval(ws):
    write_kernel_results(ws)
    write_verdict(ws)
    return submit_gate(ws, "pass")


def commit_integration(ws):
    subprocess.run(["git", "add", "-A"], cwd=ws, check=True)
    subprocess.run(
        ["git", "-c", "user.email=e@e", "-c", "user.name=t", "commit",
         "-qm", "integrate candidate"], cwd=ws, check=True)


def write_kernel_results(ws):
    """Author canonical leased results through the observed hook protocol."""
    loop_state = loop.load(ws)
    task = loop_state["tasks"][loop_state["current_task"]]
    review_ws = (task.get("workspace") if loop_state.get("parallel") and
                 loop_state.get("step") == "evaluate" else None) or ws
    state = review._load_state(review_ws)
    if state.get("zero_lens_evaluation") is True:
        assert state.get("slots") == []
        return
    manifest = loop._bind_stateless_review_contract_actions(
        review_ws, state["manifest"], task_id=task["id"])
    wait_invocation = manifest["wait_invocation"]
    if wait_invocation != loop.event_wait_invocation(
            manifest["slots"][0]["wait_policy"],
            [slot["slot_id"] for slot in manifest["slots"]]):
        raise AssertionError("review fixture did not consume the shared wait policy")
    store = review_evidence.ArtifactStore(review_ws)
    for slot in manifest["slots"]:
        lease = store.read(slot["lease"])
        brief = store.read(slot["brief"])
        bootstrap = slot["contract_bootstrap"]
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
            **({"references_applied": list(brief["language_references"])}
               if brief.get("language_references") else {}),
        }
        content = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        event = {
            "session_id": f"loop-review-{state['run_id']}",
            "agent_id": "loop-review-child-" +
                        lease["lease_fingerprint"][:16],
            "tool_name": "Write",
            "tool_input": {"file_path": slot["result_path"],
                           "content": content},
        }
        with unittest.mock.patch.dict(
                os.environ, bootstrap["environment"]):
            contract = tp.activate_review_contract_action(
                review_ws, bootstrap["action"], **bootstrap["expected"])
        review.register_slot_producer(
            review_ws, event=event, contract=contract,
            task_slot=bootstrap["task_slot"])
        review.record_slot_write_observation(
            review_ws, event=event, contract=contract,
            task_slot=bootstrap["task_slot"])
        path = os.path.join(review_ws, slot["result_path"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(content)
    return loop.collect_review_bridge(
        review_ws, publish=False,
        run_id=manifest["collection"]["run_id"])


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
        self.wait_policy = loop.event_wait_policy(
            "review-contract-bootstrap", 1)
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
            "wait_policy": self.wait_policy,
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
        _, evidence_runtime, _ = loop._review_runtime_modules()

        with unittest.mock.patch(
                "storage.load_workspace_locator", return_value=locator), \
                unittest.mock.patch.object(
                    evidence_runtime.runtime_storage,
                    "load_workspace_locator", return_value=locator):
            action = self._issue()
            contract = self._activate(action)
            self.assertEqual(contract["write_allow"], [canonical])

            store = evidence_runtime.ArtifactStore(self.ws)
            lease_ref = store.put("lease", self.lease)
            brief_ref = store.put("lens-brief", {
                "schema": "taskplane.lens-brief/v2",
                "lease": lease_ref,
                "result_path": canonical,
                "producer_contract": self.producer,
                "wait_policy": self.wait_policy,
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
        evidence = {
            "attempt": 7,
            "host": "alpha",
            "result": "agent_timeout",
        }
        current = loop.load(ws)
        task = current["tasks"][current["current_task"]]
        act_ws = task.get("workspace") or ws
        verdict["failures"] = [{
            "schema": loop.failure_routing.FAILURE_RECORD_SCHEMA_ID,
            "id": "evaluator-outage-attempt-7",
            "source": "independent-evaluator",
            "stage": "evaluate",
            "repro": "dispatch attempt 7 on host alpha",
            "evidence": evidence,
            "evidence_digest": loop.failure_routing.evidence_digest(evidence),
            "class": "environment",
            "reason": "the bounded independent evaluator timed out",
            "owner": "host:alpha",
            "cluster": "evaluator-availability",
            "route": "environment-recovery",
            "candidate": loop._failure_candidate_identity(act_ws, task),
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

    def test_worker_dispatch_identity_is_stable_once_then_rotates(self):
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "g", spec_path="specs/spec.md")
        state = loop.load(ws)

        first_ref, first_sequence = loop._reserve_worker_dispatch_ref(
            ws, state, stage="evaluate", task="t1", worker_workspace=ws)
        second_ref, second_sequence = loop._reserve_worker_dispatch_ref(
            ws, state, stage="evaluate", task="t1", worker_workspace=ws)

        self.assertEqual((first_ref, first_sequence), ("t1", 1))
        self.assertEqual((second_ref, second_sequence),
                         ("t1-attempt-2", 2))
        self.assertNotEqual(
            tp.dispatch_task_name("step", "tp-evaluator", first_ref),
            tp.dispatch_task_name("step", "tp-evaluator", second_ref))
        self.assertEqual(
            loop.load(ws)["worker_dispatch_sequences"]["evaluate:t1"], 2)

    def test_unavailable_retry_emits_fresh_exact_worker_identity(self):
        ws, _, _ = self._gate_evaluator_unavailable()
        self.assertEqual(loop.resolve(ws, "retry")["step"], "evaluate")

        action = loop.next_action(ws)

        self.assertEqual(
            loop.load(ws)["worker_dispatch_sequences"]["evaluate:t1"], 2)
        self.assertEqual(
            action["task_name"],
            action["contract_bootstrap"]["worker_identity"])
        self.assertEqual(
            action["task_name"],
            tp.dispatch_task_name(
                "step", "tp-evaluator", "t1-attempt-2"))
        lifecycle = tp.load_json(tp.active_contract_path(
            ws, action["contract_bootstrap"]["task_slot"]))[
                "worker_lifecycle"]
        self.assertEqual(lifecycle["expected_task_name"], action["task_name"])

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
        self.assertEqual(
            accepted["reanchor_authority"]["schema"],
            loop._REANCHOR_AUTHORITY_REF_SCHEMA)
        self.assertTrue(os.path.isfile(runtime_storage.evaluation_path(
            ws, "reanchor-authority.json")))

    def test_human_can_accept_exact_producer_receipt_outage_once(self):
        ws, _, _ = self._gate_evaluator_unavailable()
        state = loop.load(ws)
        task = state["tasks"][0]
        task["evaluation"]["reason_code"] = "producer_receipt_unavailable"
        task["evaluation"]["outage_identity"]["evaluation"][
            "reason_code"] = "producer_receipt_unavailable"
        verdict_path = os.path.join(ws, ".eval", "verdict.json")
        with open(verdict_path, encoding="utf-8") as stream:
            verdict = json.load(stream)
        verdict["evaluation"]["reason_code"] = \
            "producer_receipt_unavailable"
        task["evaluation"]["outage_identity"] = \
            evaluator_health.outage_identity(
                task=verdict["task"], requirement=verdict["requirement"],
                evaluation=verdict["evaluation"],
                failures=verdict["failures"])
        with open(verdict_path, "w", encoding="utf-8") as stream:
            json.dump(verdict, stream)
        loop.save(ws, state)
        fingerprint = task["evaluation"]["outage_identity"]["fingerprint"]

        refused = loop.resolve(
            ws, "pass", by="human:vdemkiv",
            accept_producer_receipt_outage=True,
            outage_fingerprint="0" * 64)
        self.assertIn("error", refused)
        accepted = loop.resolve(
            ws, "pass", by="human:vdemkiv",
            accept_producer_receipt_outage=True,
            outage_fingerprint=fingerprint)

        self.assertNotIn("error", accepted)
        task = loop.load(ws)["tasks"][0]
        self.assertEqual(task["status"], "passed")
        self.assertEqual(task["human_resolution"]["actor"],
                         "human:vdemkiv")
        self.assertEqual(task["human_resolution"]["outage_fingerprint"],
                         fingerprint)
        self.assertEqual(
            task["reanchor_authority"]["schema"],
            loop._REANCHOR_AUTHORITY_REF_SCHEMA)
        receipt = tp.load_json(runtime_storage.evaluation_path(
            ws, "reanchor-authority.json"))
        self.assertEqual(
            receipt["disposition"],
            "human-resolved-producer-receipt-outage")
        self.assertEqual(
            receipt["outage_identity"]["fingerprint"], fingerprint)

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

    def test_next_prepares_child_contract_without_binding_orchestrator(self):
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "g", spec_path="specs/spec.md")
        act = loop.next_action(ws)
        self.assertEqual(act["role"], "tp-planner")
        self.assertTrue(act["contract"]["read_only"])
        slot = act["contract_bootstrap"]["task_slot"]
        self.assertEqual(tp.list_task_slots(ws), [slot])
        self.assertIsNone(tp.load_active(ws))  # root/orchestrator is unbound
        child = tp.load_json(tp.active_contract_path(ws, slot))
        self.assertTrue(child["worker_scoped"])
        self.assertEqual(child["worker_lifecycle"]["status"], "pending")
        loop.gate(ws, "pass")
        self.assertEqual(tp.list_task_slots(ws), [])      # released by gate

    def test_plan_gate_fails_closed_on_phantom_plan(self):
        """A planner CLAIMING a plan is nothing: if plan/tasks.json is
        missing or empty, the plan gate must refuse to advance — the exact
        hallucinated-completion failure the ungoverned control run showed."""
        ws = git_ws(self.tmp, [TASK])
        with open(os.path.join(ws, "plan", "tasks.json"),
                  encoding="utf-8") as stream:
            real_plan = json.load(stream)
        os.remove(os.path.join(ws, "plan", "tasks.json"))   # phantom plan
        loop.init(ws, "g", spec_path="specs/spec.md")       # → plan
        loop.next_action(ws)
        r = loop.gate(ws, "pass")
        self.assertIn("error", r)
        self.assertIn("plan/tasks.json", r["error"])
        self.assertEqual(loop.load(ws)["step"], "plan")     # did NOT advance
        # writing a real plan unblocks the same gate
        json.dump(real_plan,
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
                    "schema": evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID,
                    "task": "t1",
                    "requirement": TASK["req"],
                    "verdict": "pass",
                    "criteria": [{
                        "criterion": TASK["criteria"][0],
                        "status": "met",
                        "evidence": evidence,
                    }],
                    "graph": {"dispositions": [],
                              "requirements_checked": [],
                              "contracts_checked": []},
                    "failures": [],
                }, stream)
            subprocess.run(
                ["git", "add", "-f", ".eval/verdict.json"], cwd=ws,
                check=True, capture_output=True, text=True,
                encoding="utf-8", errors="replace")
            subprocess.run(
                ["git", "commit", "-qm", "record evaluator evidence"],
                cwd=ws, check=True, capture_output=True, text=True,
                encoding="utf-8", errors="replace")
            task = dict(TASK)
            prior = dict(
                task, status="passed", workspace=ws,
                target_commit=tp.git_head(ws))
            return loop._verify_reanchor_task_evidence(ws, task, prior)

        for sentinel in (
                False, 0, None, "", [], {}, [False], [0],
                {"passed": False}, {"proof": {"digest": ""}},
                [None, []], {"line": 12}):
            with self.subTest(sentinel=sentinel):
                evidence, error = verify(sentinel)
                self.assertIsNone(evidence)
                self.assertEqual(
                    error, "durable evaluator criterion description is missing")

        for prose in ("ok", "src/todo/a.py:12",
                      "tests passed; implementation looks correct"):
            with self.subTest(prose=prose):
                self.assertFalse(loop._verified_criterion_evidence(prose))
                evidence, error = verify(prose)
                self.assertIsNone(evidence)
                self.assertEqual(
                    error,
                    "engine-authored reanchor authority receipt is missing")

    def _authoritative_reanchor_case(self):
        ws, _, _ = self._gate_evaluator_unavailable()
        subprocess.run(["git", "add", "src/todo/a.py"], cwd=ws,
                       check=True, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
        subprocess.run(["git", "commit", "-qm", "bind evaluated source"],
                       cwd=ws, check=True, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
        state = loop.load(ws)
        task_state = state["tasks"][0]
        task_state["evaluation"]["reason_code"] = \
            "orchestration_unavailable"
        task_state["evaluation"]["outage_identity"]["evaluation"][
            "reason_code"] = "orchestration_unavailable"
        verdict_path = runtime_storage.evaluation_path(ws)
        with open(verdict_path, encoding="utf-8") as stream:
            verdict = json.load(stream)
        verdict["evaluation"]["reason_code"] = \
            "orchestration_unavailable"
        task_state["evaluation"]["outage_identity"] = \
            evaluator_health.outage_identity(
                task=verdict["task"], requirement=verdict["requirement"],
                evaluation=verdict["evaluation"],
                failures=verdict["failures"])
        with open(verdict_path, "w", encoding="utf-8") as stream:
            json.dump(verdict, stream)
        loop.save(ws, state)
        self.assertNotIn("error", loop.resolve(ws, "pass"))
        state = loop.load(ws)
        prior = dict(state["tasks"][0])
        task = dict(prior)
        return ws, task, prior, verdict_path

    def test_define_projection_reanchor_authority_fails_closed_on_tamper_missing_and_mixed_revision(self):
        ws, task, prior, verdict_path = self._authoritative_reanchor_case()
        receipt_path = runtime_storage.evaluation_path(
            ws, "reanchor-authority.json")

        evidence, error = loop._verify_reanchor_task_evidence(
            ws, task, prior)
        self.assertIsNone(error)
        self.assertEqual(
            evidence["resolution"],
            "human-resolved-orchestration-outage")
        self.assertTrue(loop._verified_criterion_evidence(
            evidence["criterion_proof"]))

        missing_path = receipt_path + ".missing"
        os.replace(receipt_path, missing_path)
        try:
            evidence, error = loop._verify_reanchor_task_evidence(
                ws, task, prior)
            self.assertIsNone(evidence)
            self.assertIn("authority is unavailable", error)
        finally:
            os.replace(missing_path, receipt_path)

        receipt_bytes = open(receipt_path, "rb").read()
        with open(receipt_path, "ab") as stream:
            stream.write(b"\n")
        evidence, error = loop._verify_reanchor_task_evidence(
            ws, task, prior)
        self.assertIsNone(evidence)
        self.assertEqual(
            error, "engine-authored reanchor authority bytes changed")
        with open(receipt_path, "wb") as stream:
            stream.write(receipt_bytes)

        verdict_bytes = open(verdict_path, "rb").read()
        with open(verdict_path, encoding="utf-8") as stream:
            verdict = json.load(stream)
        verdict["caller_authored_copy"] = True
        with open(verdict_path, "w", encoding="utf-8") as stream:
            json.dump(verdict, stream, sort_keys=True)
        evidence, error = loop._verify_reanchor_task_evidence(
            ws, task, prior)
        self.assertIsNone(evidence)
        self.assertEqual(
            error,
            "engine-authored reanchor authority does not match exact pass")
        with open(verdict_path, "wb") as stream:
            stream.write(verdict_bytes)

        with open(os.path.join(ws, "src", "todo", "a.py"), "a",
                  encoding="utf-8") as stream:
            stream.write("# later revision\n")
        subprocess.run(
            ["git", "add", "src/todo/a.py"], cwd=ws,
            check=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        subprocess.run(
            ["git", "commit", "-qm", "later mixed revision"], cwd=ws,
            check=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        mixed_prior = dict(prior, target_commit=tp.git_head(ws))
        evidence, error = loop._verify_reanchor_task_evidence(
            ws, task, mixed_prior)
        self.assertIsNone(evidence)
        self.assertEqual(
            error,
            "engine-authored reanchor authority does not match exact pass")

    def test_define_projection_reanchor_ancestry_timeout_fails_closed(self):
        ws, task, prior, _ = self._authoritative_reanchor_case()
        real_run = subprocess.run
        observed_timeouts = []

        def time_out_ancestry(argv, **kwargs):
            if argv[:3] == ["git", "merge-base", "--is-ancestor"]:
                observed_timeouts.append(kwargs.get("timeout"))
                raise subprocess.TimeoutExpired(
                    argv, kwargs.get("timeout"))
            return real_run(argv, **kwargs)

        with unittest.mock.patch("subprocess.run",
                                 side_effect=time_out_ancestry):
            evidence, error = loop._verify_reanchor_task_evidence(
                ws, task, prior)

        self.assertIsNone(evidence)
        self.assertEqual(
            error, "passed source ancestry verification timed out")
        self.assertEqual(observed_timeouts,
                         [loop._REANCHOR_ANCESTRY_TIMEOUT_SECONDS])

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
        loop.next_action(ws); evaluated = pass_eval(ws)        # evaluate → em
        self.assertNotIn("error", evaluated, evaluated)
        self.assertEqual(loop.load(ws)["step"], "em")
        commit_integration(ws)
        em_action = loop.next_action(ws)
        em_slot = em_action["contract_bootstrap"]["task_slot"]
        self.assertEqual(tp.list_task_slots(ws), [em_slot])
        pass_em(ws)                                             # em → signoff
        self.assertEqual(tp.list_task_slots(ws), [])
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
        commit_integration(ws)
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

    def test_product_and_plan_actions_expose_focused_quick_routes(self):
        product_ws = git_ws(self.tmp, [TASK])
        loop.init(product_ws, "secure account onboarding")
        product = loop.next_action(product_ws)
        self.assertEqual(product["step"], "pm")
        self.assertEqual(product["focused_route"]["stage"], "product")
        self.assertEqual(len(product["focused_route"]["dispositions"]), 26)
        self.assertTrue(all(row["tier"] == "sweep" for row in
                            product["lenses"] if row["mode"] != "none"))

        # Use a distinct root because each stage action owns an active worker
        # contract until the host reports its terminal lifecycle event.
        plan_root = os.path.join(self.tmp, "plan-root")
        os.makedirs(plan_root)
        plan_ws = git_ws(plan_root, [TASK])
        loop.init(plan_ws, "focused plan", spec_path="specs/spec.md")
        plan = loop.next_action(plan_ws)
        self.assertEqual(plan["step"], "plan")
        self.assertEqual(plan["focused_route"]["stage"], "plan")
        self.assertIn(len(plan["focused_route"]["selected"]), {3, 4})
        self.assertEqual(len(plan["lenses"]), 26)
        self.assertTrue(all(row["focused_disposition"] in {
            "execute_light", "covered_by", "not_applicable"
        } for row in plan["lenses"]))
        self.assertEqual(
            (next(row for row in plan["lenses"]
                  if row["id"] == "architecture"))["tier"], "sweep")

    def test_focused_stage_runtime_loader_failure_is_mapper_unavailable(self):
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "secure account onboarding")

        with unittest.mock.patch.object(
                loop, "_review_runtime_modules",
                side_effect=RuntimeError("checkout review bundle unavailable")):
            action = loop.next_action(ws)

        self.assertEqual(action["step"], "pm")
        self.assertIn("focused stage routing failed closed", action["error"])
        self.assertEqual(action["focused_route"], {
            "status": "mapper_unavailable", "slots": []})

    def test_fix_gate_runs_suite_in_claimed_task_namespace(self):
        """A repair that changes Taskplane validates with repaired bytes."""
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "g", spec_path="s", checkpoints=["em"])
        state = loop.load(ws)
        state.update({
            "step": "fix",
            "parallel": True,
            "submission_required": False,
            "current_task": 0,
            "tasks": [dict(TASK, status="built", workspace=ws)],
        })
        loop.save(ws, state)
        lifecycle = []

        @contextlib.contextmanager
        def claimed_binding():
            lifecycle.append("enter")
            try:
                yield
            finally:
                lifecycle.append("exit")

        def blocked_dod(*_args, **_kwargs):
            self.assertEqual(lifecycle, ["enter"])
            return ["bounded stop after namespace assertion"]

        with unittest.mock.patch.object(
                loop, "_claimed_execute_suite_binding",
                new=claimed_binding), unittest.mock.patch.object(
                    loop, "_task_dod_errors", side_effect=blocked_dod):
            result = loop.gate(ws, "pass", task_id="t1")

        self.assertEqual(lifecycle, ["enter", "exit"])
        self.assertIn("Definition of Done failed", result["error"])

    def test_read_only_workflow_roles_can_call_the_governed_cli(self):
        """H1 blocks governed CLI calls through Bash for read-only roles."""
        state = {"goal": "g", "current_task": 0, "tasks": [TASK]}
        for step in ("pm", "design", "plan"):
            with self.subTest(step=step):
                contract = loop._step_contract(step, state)
                self.assertTrue(contract["read_only"])
                self.assertIn("Bash", contract["allowed_tools"])
                ok, reason = loop.tp.screen_tool(
                    contract, "Bash",
                    {"command": "python3 taskplane/tp.py status"}, self.tmp)
                self.assertFalse(ok)
                self.assertIn("every shell command tool is blocked", reason)

    def test_worker_contract_activates_only_after_definition_of_ready(self):
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
        self.assertEqual(order[:2], ["dor", "contract"])
        self.assertIsNone(tp.load_active(ws))
        self.assertEqual(len(tp.list_task_slots(ws)), 1)

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
        os.makedirs(os.path.join(ws, "tests"))
        with open(os.path.join(ws, "src", "auth", "a.py"), "w", encoding="utf-8") as f:
            f.write("x=1\n")
        with open(os.path.join(ws, "tests", "test_auth.py"), "w",
                  encoding="utf-8") as f:
            f.write("from src.auth.b import authorize\n\n"
                    "def test_authorize():\n"
                    "    assert authorize() is True\n")
        for c in (["init", "-q"], ["add", "-A"]):
            subprocess.run(["git", *c], cwd=ws)
        subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                        "commit", "-qm", "i"], cwd=ws)
        selector = "tests/test_auth.py::test_authorize"
        task = {"id": "t1", "scope": [scope, "tests/test_auth.py"],
                "tests": f"python3 -m pytest -q {selector}",
                "criteria": (["valid creds -> session"] if with_req else
                             ["the scoped behavior is complete"]),
                "evaluation_evidence_edges": [{
                    "producer": "src/auth/b.py",
                    "consumer": "tests/test_auth.py", "selector": selector,
                    "freshness_inputs": ["candidate_sha", "source_tree"],
                    "severed_edge": {
                        "mutation": "remove authorize",
                        "selector": selector,
                    },
                }],
                "changed_interfaces": [], "classified_failures": []}
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
        for command in (["init", "-q"], ["add", "-A"]):
            subprocess.run(["git", *command], cwd=ws, check=True)
        subprocess.run(
            ["git", "-c", "user.email=e@e", "-c", "user.name=t",
             "commit", "-qm", "initial"], cwd=ws, check=True)
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

    def test_evaluate_seals_real_diff_without_lens_routes(self):
        ws = self._ws()
        loop.approve(ws)
        loop.next_action(ws)
        # the "build": touch an auth file, uncommitted
        with open(os.path.join(ws, "src", "auth", "b.py"), "w", encoding="utf-8") as f:
            f.write("def authorize():\n    return True\n")
        submit_gate(ws, "pass")                   # execute -> evaluate
        act = loop.next_action(ws)
        self.assertEqual(act["step"], "evaluate")
        self.assertNotIn("lenses", act)
        self.assertNotIn("language_references", act)
        self.assertEqual(act["review_kernel"]["slots"], [])
        self.assertEqual(act["review_kernel"]["expected_lenses"], [])
        self.assertEqual(
            act["review_kernel"]["lens_execution_policy"], "none")
        self.assertNotIn("focused_route", act["review_kernel"])

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
        import requirements as reqs

        ws = tempfile.mkdtemp()
        os.makedirs(os.path.join(ws, "plan"))
        os.makedirs(os.path.join(ws, "tests"))
        for d in ("src/a", "src/b", "src/c"):
            os.makedirs(os.path.join(ws, d))
            with open(os.path.join(ws, d, "m.py"), "w", encoding="utf-8") as f:
                f.write("x=1\n")
        for task_id, modules in {
                "t1": ("a",), "t2": ("b",),
                "t3": ("a", "c"), "t4": ("c",)}.items():
            import_rows = "\n".join(
                f"from src.{module}.m import x as {module}_x"
                for module in modules)
            checks = " and ".join(f"{module}_x >= 1" for module in modules)
            with open(os.path.join(ws, "tests", f"test_{task_id}.py"), "w",
                      encoding="utf-8") as f:
                f.write(f"{import_rows}\n\ndef test_{task_id}():\n"
                        f"    assert {checks}\n")
        subprocess.run(["git", "init", "-q"], cwd=ws)
        subprocess.run(["git", "add", "-A"], cwd=ws)
        subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                        "commit", "-qm", "i"], cwd=ws)
        _install_test_launcher(ws)
        requirement = reqs.record_requirement(
            ws, "parallel task completion",
            functional=["complete each planned task"],
            acceptance=[
                "task t1 is complete", "task t2 is complete",
                "task t3 is complete", "task t4 is complete"],
            context_files=["src/**", "tests/**"])

        def planned_task(task_id, modules, *, deps=None):
            selector = f"tests/test_{task_id}.py::test_{task_id}"
            return {
                "id": task_id, "req": requirement["id"],
                "scope": [*(f"src/{module}/**" for module in modules),
                          f"tests/test_{task_id}.py"],
                "tests": f"python3 -m pytest -q {selector}",
                "criteria": [f"task {task_id} is complete"],
                "deps": list(deps or []),
                "evaluation_evidence_edges": [{
                    "producer": f"src/{module}/m.py",
                    "consumer": f"tests/test_{task_id}.py",
                    "selector": selector,
                    "freshness_inputs": ["candidate_sha", "source_tree"],
                    "severed_edge": {
                        "mutation": f"remove src/{module}/m.py",
                        "selector": selector,
                    },
                } for module in modules],
                "changed_interfaces": [], "classified_failures": [],
            }

        tasks = [
            planned_task("t1", ("a",)),
            planned_task("t2", ("b",)),
            planned_task("t3", ("a", "c")),
            planned_task("t4", ("c",), deps=("t1",)),
        ]
        with open(os.path.join(ws, "plan", "tasks.json"), "w", encoding="utf-8") as f:
            json.dump({"tasks": tasks}, f)
        loop.init(ws, "parallel goal", spec_path="s", checkpoints=["plan"],
                  parallel=True)
        loop.next_action(ws); loop.gate(ws, "pass")   # plan → approval
        loop.approve(ws)                               # → execute
        open_delivery_root(ws)
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
        # The slot-less worktree/orchestrator remains unbound; the native
        # child receives the exact slot through contract_bootstrap.
        self.assertIsNone(tpl.load_active(agent_ws))
        slot = out["contract_bootstrap"]["task_slot"]
        c = tpl.load_json(tpl.active_contract_path(agent_ws, slot))
        self.assertEqual(
            c["coding"]["scope_paths"],
            ["src/a/**", "tests/test_t1.py"])
        # Once the child hook selects that contract, it blocks writes outside
        # the task scope and admits the declared path.
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

    def test_fresh_claim_quarantines_older_unbound_pending_duplicate(self):
        ws = self._ws()
        agent_ws = os.path.join(ws, ".tp-work", "t1")
        subprocess.run(["git", "worktree", "add", "-q", agent_ws, "-b",
                        "tp/t1-stale-pending"], cwd=ws, check=True)
        stale = tp.prepare_worker_contract(
            agent_ws,
            tp.build_contract("EXECUTE: t1", scope=["src/a/**"],
                              test_command="true", plan_minted=True,
                              regression_gate=True),
            stage="execute", task="t1",
            task_name="tp_step_executor_t1_stale000",
            role_marker="taskplane-role:tp-executor", now=1)
        tp.activate(agent_ws, stale, snapshot=tp.git_head(agent_ws),
                    task_slot_override=stale["task_slot"])

        claimed = loop.claim(ws, "t1", agent_ws)

        fresh_slot = claimed["contract_bootstrap"]["task_slot"]
        self.assertNotEqual(fresh_slot, stale["task_slot"])
        self.assertEqual(tp.list_task_slots(agent_ws), [fresh_slot])
        quarantine = os.path.join(
            agent_ws, ".taskplane", "quarantine", "contracts")
        archived = [json.load(open(os.path.join(quarantine, name),
                                   encoding="utf-8"))
                    for name in os.listdir(quarantine)]
        self.assertEqual(len(archived), 1)
        terminal = archived[0]["worker_lifecycle"]["terminal"]
        self.assertEqual(terminal["authority"], "orphan-recovery")
        self.assertEqual(terminal["outcome"], "interruption")
        self.assertEqual(terminal["submission_status"],
                         "superseded_pending_claim")

    def test_parallel_execute_gate_validates_claimed_task_worktree(self):
        """EXECUTE DoD must import and test the claimed branch's bytes."""
        tests = (
            "python3 -c \"import sys; sys.path.insert(0, 'src/a'); "
            "import worker_probe; assert "
            "worker_probe.WORKTREE_ONLY_EXECUTE_DOD\""
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
        module = os.path.join(agent_ws, "src", "a", "worker_probe.py")
        os.makedirs(os.path.dirname(module), exist_ok=True)
        with open(module, "w", encoding="utf-8") as stream:
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
        tests = str(state["tasks"][0]["tests"])
        state["_suite_evidence"] = {"t1": {
            "schema": "taskplane.suite-evidence/v1",
            "command": tests,
            "key": tp._suite_cache_key(agent_ws, tests, env),
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
            module = os.path.join(
                agent_ws, "src", {"t1": "a", "t2": "b"}[tid], "m.py")
            with open(module, "w", encoding="utf-8") as stream:
                stream.write("x=2\n")
            subprocess.run(["git", "add", "-A"], cwd=agent_ws, check=True)
            subprocess.run(
                ["git", "-c", "user.email=e@e", "-c", "user.name=t",
                 "commit", "-qm", f"complete {tid}"],
                cwd=agent_ws, check=True)
            depgraph.scan(agent_ws)
        out = submit_gate(ws, "pass", task_id="t1")
        self.assertEqual(out["still_running"], ["t2"])
        submit_gate(ws, "pass", task_id="t2")
        # both built → next surfaces evaluate for the first built task
        act = loop.next_action(ws)
        self.assertEqual(act["step"], "evaluate")
        self.assertEqual(act["task"]["id"], "t1")
        first = pass_eval(ws)                          # t1 passed
        self.assertNotIn("error", first, first)
        act2 = loop.next_action(ws)                   # evaluate t2
        self.assertEqual(act2["task"]["id"], "t2")
        pass_eval(ws)                                  # t2 passed
        # t1 passed unlocks t4, but t3/t4 overlap on src/c → serialized:
        # t3 (first in plan order) dispatches, t4 holds for the next wave.
        w = loop.wave(ws)
        self.assertEqual({e["task"]["id"] for e in w["wave"]}, {"t3"})
        held = {h["task"]: h for h in w["held"]}
        self.assertIn("t4", held)
        self.assertEqual(held["t4"]["shared_owner"], "scope:src/c/**")
        self.assertEqual(
            held["t4"]["reason"], "serialized by scope:src/c/**")

    def test_all_passed_reaches_em(self):
        ws = self._ws()
        agent_ws = os.path.join(ws, ".tp-work", "t1")
        subprocess.run(["git", "worktree", "add", "-q", agent_ws, "-b",
                        "tp/t1-final-evaluate"], cwd=ws, check=True)
        loop.claim(ws, "t1", agent_ws)
        with open(os.path.join(agent_ws, "src", "a", "m.py"), "w",
                  encoding="utf-8") as stream:
            stream.write("x=2\n")
        subprocess.run(["git", "add", "-A"], cwd=agent_ws, check=True)
        subprocess.run(
            ["git", "-c", "user.email=e@e", "-c", "user.name=t",
             "commit", "-qm", "complete final task"],
            cwd=agent_ws, check=True)
        depgraph.scan(agent_ws)
        open_delivery_root(ws)
        st = loop.load(ws)
        for t in st["tasks"]:
            t["status"] = "passed"
        st["tasks"][0]["status"] = "built"     # last one still to evaluate
        loop.save(ws, st)
        act = loop.next_action(ws)
        self.assertNotIn("error", act, act)
        self.assertEqual(act["step"], "evaluate")
        out = pass_eval(ws)
        self.assertEqual(out["step"], "em", out)

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
        open_delivery_root(ws)
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
        authority = open_delivery_root(ws)
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
            action = getattr(loop.next_action, "__wrapped__", loop.next_action)(
                ws, root_observation_authority=authority)

        self.assertNotIn("error", action, action)
        self.assertEqual(action["review_kernel"]["status"], "ready")
        self.assertEqual((action["impact"]["graph"] or {})[
            "content_fingerprint"], "2" * 64)
        self.assertEqual(set(reads), {canonical_worker})
        scan_graph.assert_called_once_with(canonical_worker)
        self.assertEqual(json.dumps(primary_graph, sort_keys=True),
                         primary_before)

    def test_validated_workspace_is_only_downstream_evidence_authority(self):
        ws, worker = self._park_at_evaluate()
        authority = open_delivery_root(ws)
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
                loop.next_action, "__wrapped__", loop.next_action)(
                    ws, root_observation_authority=authority)

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
                scan_graph.assert_called_once_with(os.path.realpath(worker))

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
        expected = tp._audit_minimized("serial_mode")
        self.assertEqual([e.get("reason") for e in blocked],
                         [expected, expected])
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


def _trace_events(ws, event=None):
    with open(os.path.join(ws, ".taskplane", "trace.jsonl"), encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return [r for r in rows if event is None or r.get("event") == event]


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

    SURFACE = {"loop", "loop_status", "taskplane_lite", "audit",
               "audit_projection", "lens", "lens_signals", "design_contract",
               "depgraph", "decompose", "requirements", "runtime_eval"}

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
        with open(os.path.join(agent_ws, "src", "todo", "a.py"), "a",
                  encoding="utf-8") as stream:
            stream.write("\ndef complete():\n    return True\n")
        subprocess.run(["git", "add", "-A"], cwd=agent_ws, check=True)
        subprocess.run(
            ["git", "-c", "user.email=e@e", "-c", "user.name=t",
             "commit", "-qm", "complete task"],
            cwd=agent_ws, check=True)
        # t00 made the claimed worktree graph authoritative and removed the
        # stale-primary fallback. This fixture must establish the same
        # target-bound graph precondition as every real parallel evaluation.
        depgraph.scan(agent_ws)
        built = submit_gate(ws, "pass", task_id="t1")
        self.assertNotIn("error", built, built)
        action = loop.next_action(ws)                # → evaluate
        self.assertNotIn("error", action, action)
        write_kernel_results(ws)
        write_verdict(ws)
        collect_zero_test_kernel(ws)
        return ws

    def _skew_submit(self, ws):
        with unittest.mock.patch.object(
                loop, "_collect_zero_lens_evaluate_before_guidance",
                return_value={"fingerprint": "a" * 64}), \
                unittest.mock.patch(
                    "runtime_eval.guide_loop",
                    return_value={"status": "on_path", "recovered": False}):
            return loop.submit(ws, "pass")

    def _skew_gate(self, ws):
        with unittest.mock.patch.object(
                loop, "_producer_observation_errors", return_value=[]):
            return loop.gate(ws, "pass")

    def _restamp(self, ws, fingerprint):
        """Stand in for the second engine: rewrite ONLY the submission's
        engine stamp, leaving the evidence and its fingerprint untouched."""
        st = loop.load(ws)
        if fingerprint is None:
            st["_submission"].pop("engine_fingerprint", None)
        else:
            st["_submission"]["engine_fingerprint"] = fingerprint
        loop.save(ws, st)

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
        self._skew_submit(ws)
        evidence = json.loads(json.dumps(loop.load(ws)["_submission"]))
        self.assertEqual(evidence["engine_fingerprint"],
                         tp.engine_fingerprint())
        # the worktree engine is ahead of the primary validator
        self._restamp(ws, "f" * 64)
        before = json.dumps(loop.load(ws), sort_keys=True)
        out = self._skew_gate(ws)
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
        self.assertEqual(blocked["reason"],
                         tp._audit_minimized("engine_skew"))
        self.assertEqual(blocked["submitted"], "f" * 64)
        self.assertEqual(blocked["validator"],
                         tp._audit_pseudonym(tp.engine_fingerprint()))
        # "merge tp/t1 into the primary": one engine now owns production and
        # validation. The evidence is IDENTICAL — a re-evaluation is never
        # stranded by the refusal.
        self._restamp(ws, tp.engine_fingerprint())
        self.assertEqual(loop.load(ws)["_submission"], evidence)
        out2 = self._skew_gate(ws)
        self.assertNotIn("error", out2)
        self.assertEqual(loop.load(ws)["step"], "em")

    def test_absent_stamp_is_refused_and_a_resubmit_restamps(self):
        """ABSENT = REFUSE (fail-closed), with the in-flight case handled:
        a submission recorded by a pre-A4 engine carries no stamp, and the
        remedy that clears it is the same `loop submit` — so submit's
        idempotence key includes engine_fingerprint, otherwise the unstamped
        record would be kept and the loop stranded."""
        ws = self._wave_ws()
        self._skew_submit(ws)
        self._restamp(ws, None)                 # pre-A4 in-flight submission
        out = self._skew_gate(ws)
        self.assertIn("error", out)
        self.assertIn("different engine build", out["error"])
        self.assertIn("no engine fingerprint", out["error"])
        self.assertIn("git merge tp/t1", out["error"])
        self.assertIsNone(out["engine_skew"]["submitted"])
        self.assertEqual(loop.load(ws)["step"], "evaluate")
        self.assertEqual(
            _trace_events(ws, "loop_gate_blocked")[-1]["reason"],
            tp._audit_minimized("engine_skew"))
        again = self._skew_submit(ws)
        self.assertEqual(again["submission"]["engine_fingerprint"],
                         tp.engine_fingerprint())
        self.assertNotIn("error", self._skew_gate(ws))
        self.assertEqual(loop.load(ws)["step"], "em")

    def test_no_submission_record_is_not_this_guard_s_business(self):
        """The stamp governs a submission RECORD. A loop with no submission
        at all is the submission_required gate's refusal (already enforced
        above this pre-check) — this guard must not invent a second, weaker
        one, and legacy loops without the flag stay resumable."""
        self.assertIsNone(tp.engine_skew_refusal(self.tmp, None))
        self.assertIsNone(tp.engine_skew_refusal(self.tmp, {}))

    def test_engine_skew_refuses_before_evaluation_validation(self):
        ws = self._wave_ws()
        self._skew_submit(ws)
        self._restamp(ws, "f" * 64)

        with unittest.mock.patch.object(
                loop, "_evaluation_errors",
                side_effect=AssertionError(
                    "evaluation validation ran after engine-skew refusal"),
                ) as validate_evaluation:
            out = self._skew_gate(ws)

        self.assertIn("different engine build", out["error"])
        self.assertEqual(loop.load(ws)["step"], "evaluate")
        validate_evaluation.assert_not_called()


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


class TestTaskSuiteTimeoutAuthority(unittest.TestCase):
    @staticmethod
    def _task(timeout=None):
        task = dict(TASK)
        if timeout is not None:
            task["verification_runner"] = {
                "gate_timeout": {"aggregate_seconds": timeout}}
        return task

    def test_nested_task_timeout_reaches_claimed_execute_runner(self):
        task = self._task(1800)
        contract = loop._step_contract(
            "execute", {"current_task": 0, "tasks": [task]})
        contract["coding"]["dod"]["require_clean_scope_diff"] = False
        completed = subprocess.CompletedProcess(
            ["python3", "-m", "pytest"], 0, "", "")
        with loop._claimed_execute_suite_binding(), \
                unittest.mock.patch.object(
                    subprocess, "run", return_value=completed) as invoked:
            self.assertEqual(tp.dod_check(contract, tempfile.mkdtemp(), None), [])
        self.assertTrue(any(
            call.kwargs.get("timeout") == 1800
            for call in invoked.call_args_list), invoked.call_args_list)

    def test_invalid_task_timeout_blocks_plan_without_suite_launch(self):
        invalid = [True, 0, 14401, {"invalid": "shape"}]
        for value in invalid:
            with self.subTest(value=value), \
                    unittest.mock.patch.object(
                        loop, "_plan_delivery_mode_from_file"), \
                    unittest.mock.patch.object(
                        loop.tp, "run_suite_command") as runner:
                task = self._task()
                if isinstance(value, dict):
                    task["verification_runner"] = value
                else:
                    task["verification_runner"] = {
                        "gate_timeout": {"aggregate_seconds": value}}
                errors = loop._plan_dor_errors(
                    tempfile.mkdtemp(), {"tasks": [task]})
                self.assertTrue(any(
                    "verification_runner.gate_timeout.aggregate_seconds" in e
                    for e in errors), errors)
                runner.assert_not_called()

    def test_final_signoff_preserves_each_task_timeout(self):
        task = self._task(1800)
        task["status"] = "passed"
        captured = []

        def check(contract, *_args, **_kwargs):
            captured.append(contract)
            return []

        with unittest.mock.patch.object(
                loop.tp, "requirement_coverage_errors", return_value=[]), \
                unittest.mock.patch.object(
                    loop.tp, "changed_files", return_value=[]), \
                unittest.mock.patch.object(
                    loop.tp, "dod_check", side_effect=check), \
                unittest.mock.patch.object(
                    loop, "_engineering_review_errors", return_value=[]), \
                unittest.mock.patch.object(loop.kb, "lint", return_value=[]):
            loop._compute_signoff_dod(
                tempfile.mkdtemp(), {"tasks": [task], "baseline": "HEAD"})
        self.assertEqual(
            captured[0]["coding"]["dod"]["test_timeout_seconds"], 1800)

    def test_invalid_contract_timeout_skips_suite_launch(self):
        contract = tp.build_contract(
            "test", test_command="true", scope=["taskplane/**"])
        contract["coding"]["dod"]["require_clean_scope_diff"] = False
        contract["coding"]["dod"]["test_timeout_seconds"] = True
        with unittest.mock.patch.object(
                tp, "run_suite_command") as runner, \
                unittest.mock.patch.object(tp, "suite_cache_lookup") as cache:
            errors = tp.dod_check(contract, tempfile.mkdtemp(), None)
        self.assertEqual(
            errors,
            ["coding.dod.test_timeout_seconds must be a real integer from 1 "
             "to 3600"])
        cache.assert_not_called()
        runner.assert_not_called()


class TestSupersededPendingWorkerRecovery(unittest.TestCase):
    def _active(self, workspace, *, stage="execute", task="t1", now=10,
                name=None):
        name = name or f"tp_step_executor_{task}_{now:08d}"
        contract = tp.prepare_worker_contract(
            workspace,
            tp.build_contract(f"{stage.upper()}: {task}", read_only=True,
                              write_allow=[".eval/**"]),
            stage=stage, task=task, task_name=name,
            role_marker="taskplane-role:tp-executor", now=now)
        tp.activate(workspace, contract, snapshot=f"head-{now}",
                    task_slot_override=contract["task_slot"])
        return contract

    def test_session_sweep_keeps_unique_newest_and_quarantines_older(self):
        workspace = tempfile.mkdtemp()
        older = self._active(workspace, now=10)
        newest = self._active(workspace, now=20)
        released = tp.sweep_completed_worker_contracts(
            workspace, loop_state={
                "step": "execute", "parallel": True,
                "tasks": [{"id": "t1", "status": "running"}]}, now=30)
        self.assertEqual([row["slot"] for row in released],
                         [older["task_slot"]])
        self.assertEqual(tp.list_task_slots(workspace),
                         [newest["task_slot"]])
        archived = json.load(open(released[0]["quarantine"],
                                  encoding="utf-8"))
        terminal = archived["worker_lifecycle"]["terminal"]
        self.assertEqual(terminal["authority"], "orphan-recovery")
        self.assertEqual(terminal["outcome"], "interruption")
        self.assertEqual(terminal["submission_status"],
                         "superseded_pending_claim")

    def test_active_owned_duplicate_fails_closed_without_release(self):
        workspace = tempfile.mkdtemp()
        older = self._active(workspace, now=10, name="worker-old")
        newest = self._active(workspace, now=20, name="worker-new")
        tp.bind_worker_contract_event(workspace, {
            "session_id": "session", "agent_id": "agent",
            "task_name": "worker-old"}, now=11)
        with self.assertRaisesRegex(tp.StateError, "unbound pending"):
            tp.release_superseded_pending_worker_contracts(
                workspace, stage="execute", task="t1",
                keep_slot=newest["task_slot"], now=30)
        self.assertEqual(set(tp.list_task_slots(workspace)),
                         {older["task_slot"], newest["task_slot"]})

    def test_equal_prepared_at_fails_closed_without_release(self):
        workspace = tempfile.mkdtemp()
        first = self._active(workspace, now=10, name="worker-one")
        second = self._active(workspace, now=10, name="worker-two")
        with self.assertRaisesRegex(tp.StateError, "unique newest"):
            tp.release_superseded_pending_worker_contracts(
                workspace, stage="execute", task="t1")
        self.assertEqual(set(tp.list_task_slots(workspace)),
                         {first["task_slot"], second["task_slot"]})

    def test_unrelated_worker_slots_are_preserved(self):
        workspace = tempfile.mkdtemp()
        older = self._active(workspace, task="t1", now=10)
        newest = self._active(workspace, task="t1", now=20)
        unrelated = self._active(workspace, task="t2", now=5)
        released = tp.release_superseded_pending_worker_contracts(
            workspace, stage="execute", task="t1",
            keep_slot=newest["task_slot"], now=30)
        self.assertEqual([row["slot"] for row in released],
                         [older["task_slot"]])
        self.assertEqual(set(tp.list_task_slots(workspace)),
                         {newest["task_slot"], unrelated["task_slot"]})


class TestReviewBridge(unittest.TestCase):
    def _review_bridge_graph_workspace(self):
        root = tempfile.mkdtemp()
        workspace = os.path.join(root, "workspace")
        os.makedirs(os.path.join(workspace, "src", "todo"))
        subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
        subprocess.run(["git", "config", "user.email", "e@e"],
                       cwd=workspace, check=True)
        subprocess.run(["git", "config", "user.name", "t"],
                       cwd=workspace, check=True)
        source = os.path.join(workspace, "src", "todo", "a.py")
        with open(source, "w", encoding="utf-8") as stream:
            stream.write("def pending():\n    return False\n")
        subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"],
                       cwd=workspace, check=True)
        baseline = tp.git_head(workspace)
        with open(source, "w", encoding="utf-8") as stream:
            stream.write("def complete():\n    return True\n")
        subprocess.run(["git", "add", "src/todo/a.py"],
                       cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-qm", "implementation"],
                       cwd=workspace, check=True)
        depgraph.scan(workspace)
        graph = depgraph.load(workspace)
        task = dict(TASK, req="R-graph-order")
        impact = depgraph.impact(
            workspace, ["src/todo/a.py"],
            policy=depgraph.impact_policy(task))
        requirement = {
            "id": "R-graph-order",
            "text": "complete() marks done",
            "acceptance": [{"id": "AC-1",
                            "criterion": "complete() marks done"}],
            "review_policy": {"depth": "quick-only"},
        }
        return workspace, baseline, graph, impact, task, requirement

    def test_review_bridge_graph_failure_dispatches_no_lenses(self):
        workspace, baseline, graph, impact, task, requirement = \
            self._review_bridge_graph_workspace()
        stale = json.loads(json.dumps(graph))
        stale.setdefault("meta", {})["scanned_head"] = "0" * 40
        _, _, review_kernel = loop._review_runtime_modules()

        with unittest.mock.patch.object(
                review_kernel, "start_review",
                wraps=review_kernel.start_review) as started, \
                self.assertRaises(loop._ReviewGraphQualityError) as raised:
            loop._review_kernel(
                workspace, workspace, base=baseline, step="evaluate",
                task=task, graph=stale, impact=impact,
                requirement=requirement)

        started.assert_not_called()
        self.assertEqual(raised.exception.quality["status"],
                         "impact_incomplete")
        self.assertIn("stale_graph", raised.exception.quality["reasons"])

    def test_zero_lens_evaluate_seals_current_graph_without_route_or_wait(self):
        workspace, baseline, graph, impact, task, requirement = \
            self._review_bridge_graph_workspace()
        _, evidence_kernel, review_kernel = loop._review_runtime_modules()

        manifest, routing = loop._review_kernel(
            workspace, workspace, base=baseline, step="evaluate", task=task,
            graph=graph, impact=impact, requirement=requirement)
        bound = loop._bind_stateless_review_contract_actions(
            workspace, manifest, task_id=task["id"])
        sealed = review_kernel._load_state(workspace, manifest["run_id"])
        quality = evidence_kernel.ArtifactStore(workspace).read(
            sealed["quality"])

        self.assertEqual(quality["status"], "complete")
        self.assertEqual(quality["scanned_head"], tp.git_head(workspace))
        self.assertEqual(routing["lenses"], [])
        self.assertEqual(bound["slots"], [])
        self.assertNotIn("wait_invocation", bound)
        self.assertEqual(sealed["expected_lenses"], [])
        self.assertEqual(sealed["lens_execution_policy"], "none")
        self.assertNotIn("routing_decision", sealed)

        immutable = json.loads(json.dumps({
            "quality": sealed["quality"],
            "evaluation_input": sealed["evaluation_input"],
            "slots": sealed["slots"],
        }))
        with open(os.path.join(workspace, "src", "todo", "a.py"), "a",
                  encoding="utf-8") as stream:
            stream.write("# later graph revision\n")
        subprocess.run(["git", "add", "src/todo/a.py"],
                       cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-qm", "later graph revision"],
                       cwd=workspace, check=True)
        depgraph.scan(workspace)
        reloaded = review_kernel._load_state(workspace, manifest["run_id"])
        self.assertEqual({
            "quality": reloaded["quality"],
            "evaluation_input": reloaded["evaluation_input"],
            "slots": reloaded["slots"],
        }, immutable)

    def test_review_bridge_checkout_bound_main_reloads_target_runtime(self):
        canonical = sys.modules.get("taskplane_lite")
        canonical_storage = sys.modules.get("storage")
        launcher = types.SimpleNamespace(
            __file__="/launcher/v2.17.16/taskplane_lite.py",
            review_execution_root_identity=lambda *_: (_ for _ in ()).throw(
                AssertionError("launcher runtime was used")))
        workspace = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)

        with unittest.mock.patch.object(loop, "tp", launcher), \
                unittest.mock.patch.object(
                    loop, "_REVIEW_RUNTIME_BUNDLE", None):
            runtime, evidence, review_kernel = \
                loop._review_runtime_modules()
            binding = evidence.create_execution_binding(
                workspace,
                target={"fingerprint": "target-v2.17.17", "head": "abc123"},
                run_id="target-run", lens_ids=["architecture"],
                slot_id="sweep.architecture",
                lease_fingerprint="a" * 64,
                producer="target-producer")

        self.assertEqual(binding["schema"],
                         "taskplane.review-execution-binding/v1")
        self.assertIs(evidence.tp, runtime)
        self.assertIs(evidence.runtime_storage,
                      review_kernel.runtime_storage)
        self.assertIs(review_kernel.tp, runtime)
        self.assertIs(review_kernel.review_evidence_runtime, evidence)
        self.assertIs(review_kernel.terminal_truth_runtime,
                      loop.terminal_truth)
        target_import = runtime.__dict__["__builtins__"]["__import__"]
        self.assertIs(target_import("storage"), evidence.runtime_storage)
        self.assertEqual(os.path.realpath(runtime.__file__), os.path.realpath(
            os.path.join(os.path.dirname(loop.__file__),
                         "taskplane_lite.py")))
        self.assertEqual(
            os.path.realpath(evidence.runtime_storage.__file__),
            os.path.realpath(os.path.join(
                os.path.dirname(loop.__file__), "storage.py")))
        self.assertIs(sys.modules.get("taskplane_lite"), canonical)
        self.assertIs(sys.modules.get("storage"), canonical_storage)
        for private in (runtime, evidence, review_kernel,
                        evidence.runtime_storage):
            self.assertIsNot(sys.modules.get(private.__name__), private)

    def test_review_bridge_private_loader_rejects_external_symlink(self):
        root = tempfile.mkdtemp()
        outside = tempfile.mkdtemp()
        payload = os.path.join(outside, "payload.py")
        with open(payload, "w", encoding="utf-8") as stream:
            stream.write("external_payload_executed = True\n")
        os.symlink(payload, os.path.join(root, "review.py"))

        with self.assertRaisesRegex(RuntimeError, "escapes checkout"):
            loop._verified_review_module_source(root, "review")

    def test_review_bridge_private_loader_rejects_replacement_race(self):
        root = tempfile.mkdtemp()
        candidate = os.path.join(root, "review.py")
        replacement = os.path.join(root, "replacement.py")
        with open(candidate, "w", encoding="utf-8") as stream:
            stream.write("trusted = True\n")
        with open(replacement, "w", encoding="utf-8") as stream:
            stream.write("replacement_executed = True\n")
        real_open = os.open
        real_replace = os.replace

        def replace_before_open(path, flags):
            if os.path.realpath(path) == os.path.realpath(candidate):
                real_replace(replacement, candidate)
            return real_open(path, flags)

        with unittest.mock.patch.object(
                loop.os, "open", side_effect=replace_before_open), \
                self.assertRaisesRegex(RuntimeError, "changed while pinned"):
            loop._verified_review_module_source(root, "review")

    def test_review_bridge_private_loader_executes_verified_checkout_bytes(self):
        root = tempfile.mkdtemp()
        candidate = os.path.join(root, "verified.py")
        with open(candidate, "w", encoding="utf-8") as stream:
            stream.write("checkout_proof = 'verified-bytes'\n")

        loader = loop._CheckoutReviewModuleBundle(root)
        module = loader.load("verified")

        self.assertEqual(module.checkout_proof, "verified-bytes")
        self.assertEqual(loader.sources["verified"]["path"], candidate)
        self.assertIsNot(sys.modules.get(module.__name__), module)

    def test_review_bridge_graph_policy_mutation_reloads_complete_bundle(self):
        root = tempfile.mkdtemp()
        modules = {
            "storage": "POLICY = 'storage'\n",
            "taskplane_lite": "POLICY = 'runtime'\n",
            "review_evidence": (
                "import storage as runtime_storage\n"
                "import taskplane_lite as tp\n"),
            "review": (
                "import storage as runtime_storage\n"
                "import taskplane_lite as tp\n"
                "import review_evidence as review_evidence_runtime\n"),
            "graph_quality": "POLICY = 'graph-v1'\n",
        }
        for name, source in modules.items():
            with open(os.path.join(root, name + ".py"), "w",
                      encoding="utf-8") as stream:
                stream.write(source)
        with open(os.path.join(root, "loop.py"), "w",
                  encoding="utf-8") as stream:
            stream.write("# checkout root identity\n")

        with unittest.mock.patch.object(
                loop, "__file__", os.path.join(root, "loop.py")), \
                unittest.mock.patch.object(
                    loop, "_REVIEW_RUNTIME_BUNDLE", None):
            loop._review_runtime_modules()
            first_loader = loop._REVIEW_RUNTIME_BUNDLE["loader"]
            first_policy = first_loader.load("graph_quality")
            replacement = os.path.join(root, "graph_quality.next")
            with open(replacement, "w", encoding="utf-8") as stream:
                stream.write("POLICY = 'graph-v2'\n")
            os.replace(replacement, os.path.join(root, "graph_quality.py"))

            loop._review_runtime_modules()
            second_loader = loop._REVIEW_RUNTIME_BUNDLE["loader"]
            second_policy = second_loader.load("graph_quality")

        self.assertIn("graph_quality", loop._REVIEW_REQUIRED_MODULES)
        self.assertIsNot(first_loader, second_loader)
        self.assertEqual(first_policy.POLICY, "graph-v1")
        self.assertEqual(second_policy.POLICY, "graph-v2")

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

    def test_review_bridge_execute_gate_accepts_safe_hosted_checks_argv(self):
        completed = subprocess.CompletedProcess(
            ["gh", "pr", "checks", "1", "--watch", "--fail-fast"],
            0, "", "")
        with unittest.mock.patch(
                "subprocess.run", return_value=completed) as invoked:
            with loop._claimed_execute_suite_binding():
                result = tp.run_suite_command(
                    ".", "gh pr checks 1 --watch --fail-fast")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            invoked.call_args.args[0],
            ["gh", "pr", "checks", "1", "--watch", "--fail-fast"])
        self.assertFalse(invoked.call_args.kwargs["shell"])

    def test_review_bridge_execute_gate_rejects_shell_operators(self):
        with unittest.mock.patch("subprocess.run") as invoked:
            with loop._claimed_execute_suite_binding():
                result = tp.run_suite_command(
                    ".", "python3 -m pytest && touch escaped")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("shell operators", result.stderr)
        invoked.assert_not_called()


class TestLR10ParallelRunner:
    @staticmethod
    def _roots(tmp_path, shards):
        parent = tmp_path / "runner"
        parent.mkdir()
        roots = {}
        for index, name in enumerate(shards, 1):
            child = parent / f"{index:02d}-{name}"
            child.mkdir()
            roots[name] = child
        return parent, roots

    def test_timeout_terminates_then_kills_and_collects_later_shards(
            self, tmp_path, monkeypatch):
        events = []

        class TimedOutProcess:
            returncode = None
            calls = 0

            def communicate(self, timeout):
                self.calls += 1
                events.append(("communicate-slow", timeout))
                if self.calls == 1:
                    raise subprocess.TimeoutExpired(
                        ["slow"], timeout, output="partial-out",
                        stderr="partial-err")
                if self.calls == 2:
                    raise subprocess.TimeoutExpired(
                        ["slow"], timeout, output="partial-out-after-term",
                        stderr="partial-err-after-term")
                self.returncode = -9
                return ("partial-out-after-term-complete\n",
                        "partial-err-after-term-complete\n")

            def terminate(self):
                events.append(("terminate-slow", None))

            def kill(self):
                events.append(("kill-slow", None))

        class ReadyProcess:
            returncode = 0

            def communicate(self, timeout):
                events.append(("communicate-ready", timeout))
                return "ready-out\n", "ready-err\n"

        def popen(argv, **_kwargs):
            name = argv[-1]
            events.append((f"start-{name}", None))
            return TimedOutProcess() if name == "slow.py" else ReadyProcess()

        shards = {"slow": ("slow.py",), "ready": ("ready.py",)}
        monkeypatch.setattr(
            lr10_runner, "_create_temp_roots",
            lambda value: self._roots(tmp_path, value))
        _, results = lr10_runner.run_shards(
            shards, popen_factory=popen, clock=lambda: 100.0)

        assert [result.status for result in results] == ["timeout", "passed"]
        assert results[0].stdout == "partial-out-after-term-complete\n"
        assert results[0].stderr == "partial-err-after-term-complete\n"
        assert results[1].stdout == "ready-out\n"
        assert events.index(("start-ready.py", None)) < next(
            index for index, event in enumerate(events)
            if event[0].startswith("communicate"))
        assert ("terminate-slow", None) in events
        assert ("kill-slow", None) in events
        assert events[2] == ("communicate-slow", 1500.0)

    def test_popen_failure_does_not_abandon_started_or_later_shards(
            self, tmp_path, monkeypatch):
        events = []

        class Process:
            returncode = 0

            def __init__(self, name):
                self.name = name

            def communicate(self, timeout):
                events.append((f"collect-{self.name}", timeout))
                return f"{self.name}-out", f"{self.name}-err"

        def popen(argv, **_kwargs):
            name = argv[-1]
            events.append((f"start-{name}", None))
            if name == "second.py":
                raise OSError("synthetic startup failure")
            return Process(name)

        shards = {
            "first": ("first.py",),
            "second": ("second.py",),
            "third": ("third.py",),
        }
        monkeypatch.setattr(
            lr10_runner, "_create_temp_roots",
            lambda value: self._roots(tmp_path, value))
        _, results = lr10_runner.run_shards(
            shards, popen_factory=popen, clock=lambda: 100.0)

        assert [result.status for result in results] == [
            "passed", "startup-error", "passed"]
        assert "synthetic startup failure" in results[1].stderr
        assert [event[0] for event in events[:3]] == [
            "start-first.py", "start-second.py", "start-third.py"]
        assert "collect-first.py" in [event[0] for event in events]
        assert "collect-third.py" in [event[0] for event in events]

    def test_nonzero_shard_still_collects_every_result(
            self, tmp_path, monkeypatch):
        collected = []

        class Process:
            def __init__(self, name, returncode):
                self.name = name
                self.returncode = returncode

            def communicate(self, timeout):
                collected.append(self.name)
                return self.name, ""

        processes = iter([Process("failed", 2), Process("passed", 0)])
        shards = {"failed": ("failed.py",), "passed": ("passed.py",)}
        monkeypatch.setattr(
            lr10_runner, "_create_temp_roots",
            lambda value: self._roots(tmp_path, value))
        _, results = lr10_runner.run_shards(
            shards, popen_factory=lambda *_args, **_kwargs: next(processes),
            clock=lambda: 100.0)

        assert collected == ["failed", "passed"]
        assert [result.status for result in results] == ["failed", "passed"]

    def test_output_order_is_deterministic_and_omits_temp_paths(self, capsys):
        shards = {"zeta": ("z.py",), "alpha": ("a.py", "b.py")}
        results = [
            lr10_runner.ShardResult(
                "01-zeta", "zeta", ("z.py",), "passed", 0,
                "z-out\n", "z-err\n", 1.25),
            lr10_runner.ShardResult(
                "02-alpha", "alpha", ("a.py", "b.py"), "passed", 0,
                "a-out\n", "a-err\n", 2.5),
        ]

        lr10_runner._render_shard_map(shards)
        assert lr10_runner._render_results(results) == 0
        output = capsys.readouterr().out

        assert ("1 Taskplane Fix worker/native agent; "
                "5 internal parallel pytest subprocess shards") in output
        assert output.index("01-zeta") < output.index("02-alpha")
        assert output.index("z.py") < output.index("a.py") < output.index("b.py")
        assert "lr10-runner-" not in output
        assert "temp=" not in output

    def test_temp_roots_are_validated_direct_non_symlink_children(
            self, tmp_path):
        parent = tmp_path / "parent"
        child = parent / "01-valid"
        outside = tmp_path / "outside"
        parent.mkdir()
        child.mkdir()
        outside.mkdir()

        assert lr10_runner._validate_child_root(parent, child) == child.resolve()
        with pytest.raises(RuntimeError, match="direct child"):
            lr10_runner._validate_child_root(parent, outside)

        link = parent / "02-link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("directory symlinks are unavailable")
        with pytest.raises(RuntimeError, match="must not be a symlink"):
            lr10_runner._validate_child_root(parent, link)

    def test_streams_stay_separate_and_duration_and_deadlines_are_recorded(
            self, tmp_path, monkeypatch):
        invocations = []
        moments = iter([10.0, 10.0, 11.0, 12.5])

        class Process:
            returncode = 0

            def communicate(self, timeout):
                assert timeout == 1499.0
                return "stdout-only", "stderr-only"

        def popen(argv, **kwargs):
            invocations.append((argv, kwargs))
            return Process()

        shards = {"only": ("only.py",)}
        monkeypatch.setattr(
            lr10_runner, "_create_temp_roots",
            lambda value: self._roots(tmp_path, value))
        _, results = lr10_runner.run_shards(
            shards, popen_factory=popen, clock=lambda: next(moments))

        result = results[0]
        assert result.stdout == "stdout-only"
        assert result.stderr == "stderr-only"
        assert result.duration_seconds == 2.5
        kwargs = invocations[0][1]
        assert kwargs["stdout"] is subprocess.PIPE
        assert kwargs["stderr"] is subprocess.PIPE
        assert kwargs["shell"] is False
        assert "TASKPLANE_TASK" not in kwargs["env"]
        assert lr10_runner.SHARD_TIMEOUT_SECONDS == 1500
        assert lr10_runner.AGGREGATE_TIMEOUT_SECONDS == 1800
        assert lr10_runner.CLEANUP_MARGIN_SECONDS == 300
