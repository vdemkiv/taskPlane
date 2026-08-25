"""Canonical repository/run storage for the hybrid taskPlane layout."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import run_store  # noqa: E402
import review_evidence  # noqa: E402
import review  # noqa: E402
import depgraph  # noqa: E402
import design_contract  # noqa: E402
import evaluation_output  # noqa: E402
import storage  # noqa: E402
import taskplane_lite  # noqa: E402
import storage_migration  # noqa: E402


def _git(ws, *args):
    return subprocess.run(["git", *args], cwd=ws, capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          check=False)


class TestRepositoryIdentity(unittest.TestCase):
    def test_hosted_remote_forms_share_one_identity(self):
        expected = "github.com/ibrahim-3d/orchestrator-supaconductor"
        values = (
            "https://github.com/Ibrahim-3d/orchestrator-supaconductor.git",
            "git@github.com:Ibrahim-3d/orchestrator-supaconductor.git",
            "ssh://git@github.com/Ibrahim-3d/orchestrator-supaconductor.git",
        )
        self.assertEqual(
            {storage.identity_from_remote(value).repo_id for value in values},
            {expected})

    def test_clone_paths_do_not_change_hosted_repository_identity(self):
        remote = "https://github.com/Example/Project.git"
        roots = [tempfile.mkdtemp(prefix="tp-repo-a-"),
                 tempfile.mkdtemp(prefix="tp-repo-b-")]
        for root in roots:
            _git(root, "init", "-q")
            _git(root, "remote", "add", "origin", remote)
        identities = [storage.resolve_repository_identity(root)
                      for root in roots]
        self.assertEqual(identities[0].repo_id, identities[1].repo_id)
        self.assertNotEqual(identities[0].workspace, identities[1].workspace)

    def test_local_identity_is_stable_across_git_initialization(self):
        root = tempfile.mkdtemp(prefix="tp-local-identity-")
        before = storage.resolve_repository_identity(root)

        self.assertEqual(_git(root, "init", "-q").returncode, 0)
        after = storage.resolve_repository_identity(root)

        self.assertEqual(after.repo_id, before.repo_id)
        self.assertEqual(after.key, before.key)


class TestStorageLayout(unittest.TestCase):
    def test_code_runs_artifacts_and_knowledge_have_distinct_roots(self):
        home = tempfile.mkdtemp(prefix="tp-hybrid-home-")
        identity = storage.identity_from_remote(
            "https://github.com/Example/Project.git")
        layout = storage.resolve_layout(identity, home=home,
                                        run_id="run-123")
        roots = {layout.checkout_root, layout.run_root,
                 layout.artifact_root, layout.knowledge_root}
        self.assertEqual(len(roots), 4)
        self.assertTrue(layout.checkout_root.startswith(
            os.path.join(home, "checkouts")))
        self.assertTrue(layout.run_root.startswith(os.path.join(home, "runs")))
        self.assertTrue(layout.artifact_root.startswith(layout.run_root))
        self.assertTrue(layout.knowledge_root.startswith(
            os.path.join(home, "projects")))
        self.assertNotIn(".em-review", layout.checkout_root)

    def test_graph_cache_is_bound_to_repo_head_and_scanner(self):
        home = tempfile.mkdtemp(prefix="tp-hybrid-home-")
        identity = storage.identity_from_remote(
            "https://github.com/Example/Project.git")
        layout = storage.resolve_layout(identity, home=home,
                                        run_id="run-123")
        a = layout.graph_cache_path("a" * 40, "scanner-v1")
        b = layout.graph_cache_path("b" * 40, "scanner-v1")
        c = layout.graph_cache_path("a" * 40, "scanner-v2")
        self.assertEqual(len({a, b, c}), 3)

    def test_run_identity_cannot_escape_the_external_store(self):
        identity = storage.identity_from_remote(
            "https://github.com/Example/Project.git")
        for unsafe in ("../escape", "/tmp/escape", "..", ""):
            with self.subTest(unsafe=unsafe), self.assertRaises(
                    storage.StorageIdentityError):
                storage.resolve_layout(identity, home=tempfile.mkdtemp(),
                                       run_id=unsafe)

    def test_managed_graph_cache_reuses_same_repo_head_across_runs(self):
        home = tempfile.mkdtemp(prefix="tp-graph-cache-home-")
        checkout = tempfile.mkdtemp(prefix="tp-graph-cache-repo-")
        _git(checkout, "init", "-q")
        _git(checkout, "config", "user.email", "t@example.com")
        _git(checkout, "config", "user.name", "T")
        with open(os.path.join(checkout, "a.py"), "w",
                  encoding="utf-8") as handle:
            handle.write("def a():\n    return 1\n")
        _git(checkout, "add", "a.py")
        _git(checkout, "commit", "-qm", "base")
        identity = storage.resolve_repository_identity(checkout)
        first_layout = storage.resolve_layout(
            identity, home=home, run_id="run-one")
        storage.write_workspace_locator(
            checkout, identity=identity, layout=first_layout,
            run_id="run-one")
        first = depgraph.scan(checkout)
        second_layout = storage.resolve_layout(
            identity, home=home, run_id="run-two")
        storage.write_workspace_locator(
            checkout, identity=identity, layout=second_layout,
            run_id="run-two")
        original = depgraph._scan_locked
        try:
            depgraph._scan_locked = lambda *_a, **_k: (_ for _ in ()).throw(
                AssertionError("scanner reran instead of restoring cache"))
            second = depgraph.scan(checkout)
        finally:
            depgraph._scan_locked = original
        self.assertEqual(second, first)
        self.assertTrue(os.path.isfile(os.path.join(
            second_layout.graph_root, "graph.json")))

    def test_checkout_locator_points_to_run_without_owning_artifacts(self):
        home = tempfile.mkdtemp(prefix="tp-hybrid-home-")
        checkout = tempfile.mkdtemp(prefix="tp-managed-checkout-")
        _git(checkout, "init", "-q")
        identity = storage.identity_from_remote(
            "https://github.com/Example/Project.git", workspace=checkout)
        layout = storage.resolve_layout(identity, home=home, run_id="run-123")
        path = storage.write_workspace_locator(
            checkout, identity=identity, layout=layout, run_id="run-123")
        self.assertEqual(path, storage._locator_path(checkout))
        locator = storage.load_workspace_locator(checkout)
        self.assertEqual(locator["run_id"], "run-123")
        self.assertEqual(locator["paths"]["artifacts"], layout.artifact_root)
        self.assertFalse(locator["paths"]["artifacts"].startswith(checkout))
        self.assertFalse(os.path.exists(os.path.join(
            checkout, ".taskplane", "workspace.json")))
        artifact_store = review_evidence.ArtifactStore(checkout)
        self.assertEqual(artifact_store.root, os.path.join(
            layout.artifact_root, "review-artifacts-v2"))

    def test_locator_is_rejected_after_checkout_or_home_identity_changes(self):
        home = tempfile.mkdtemp(prefix="tp-hybrid-home-")
        checkout = tempfile.mkdtemp(prefix="tp-managed-checkout-")
        _git(checkout, "init", "-q")
        identity = storage.identity_from_remote(
            "https://github.com/Example/Project.git", workspace=checkout)
        layout = storage.resolve_layout(identity, home=home, run_id="run-123")
        storage.write_workspace_locator(
            checkout, identity=identity, layout=layout, run_id="run-123")
        moved = tempfile.mkdtemp(prefix="tp-other-checkout-")
        _git(moved, "init", "-q")
        source = storage._locator_path(checkout)
        target = storage._locator_path(moved)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(source, encoding="utf-8") as src, \
                open(target, "w", encoding="utf-8") as dst:
            dst.write(src.read())
        with self.assertRaises(storage.StorageIdentityError):
            storage.load_workspace_locator(moved)

    def test_review_state_and_results_resolve_outside_the_checkout(self):
        home = tempfile.mkdtemp(prefix="tp-hybrid-home-")
        checkout = tempfile.mkdtemp(prefix="tp-managed-checkout-")
        _git(checkout, "init", "-q")
        identity = storage.identity_from_remote(
            "https://github.com/Example/Project.git", workspace=checkout)
        layout = storage.resolve_layout(identity, home=home, run_id="run-123")
        storage.write_workspace_locator(
            checkout, identity=identity, layout=layout, run_id="run-123")
        run_id = "a" * 32
        review._save_state(checkout, {
            "schema": "taskplane.review-run-state/v2",
            "run_id": run_id, "status": "ready", "stage": "review",
            "target": {"fingerprint": "target"}})
        self.assertTrue(review._state_path(checkout, run_id).startswith(
            layout.state_root))
        self.assertFalse(os.path.exists(os.path.join(
            checkout, ".em-review", "kernel-v2")))
        result = review._result_path(checkout, "review", "b" * 64)
        self.assertTrue(os.path.isabs(result))
        self.assertTrue(result.startswith(layout.lens_root))
        self.assertEqual(depgraph._path(checkout), os.path.join(
            layout.graph_root, "graph.json"))
        self.assertEqual(taskplane_lite.project_key(checkout), identity.key)
        self.assertEqual(taskplane_lite.kb_root(checkout),
                         layout.knowledge_root)
        self.assertEqual(taskplane_lite.tp_dir(checkout), os.path.join(
            layout.state_root, "control"))
        contract = evaluation_output.create_evaluator_contract(
            workspace=checkout, task="t1", slot="t1",
            capability_snapshot={"capabilities": {
                "native_structured_output": {
                    "status": "unsupported", "source": "test"}}})
        self.assertEqual(contract["result_path"], os.path.join(
            layout.evidence_root, "evaluation", "verdict.json"))
        self.assertEqual(contract["write_allow"], [contract["result_path"]])
        self.assertEqual(storage.review_public_path(
            checkout, "findings.json"), os.path.join(
                layout.artifact_root, "public", "findings.json"))
        self.assertEqual(storage.dashboard_path(checkout), os.path.join(
            layout.artifact_root, "mission-control", "dashboard.html"))
        self.assertEqual(storage.dependency_graph_visual_path(checkout),
                         os.path.join(layout.artifact_root,
                                      "dependency-graph.html"))

    def test_external_evidence_participates_in_workspace_fingerprint(self):
        home = tempfile.mkdtemp(prefix="tp-evidence-fingerprint-home-")
        checkout = tempfile.mkdtemp(prefix="tp-evidence-fingerprint-repo-")
        _git(checkout, "init", "-q")
        _git(checkout, "config", "user.email", "t@example.com")
        _git(checkout, "config", "user.name", "T")
        with open(os.path.join(checkout, "a.py"), "w",
                  encoding="utf-8") as handle:
            handle.write("x = 1\n")
        _git(checkout, "add", "a.py")
        _git(checkout, "commit", "-qm", "base")
        identity = storage.resolve_repository_identity(checkout)
        layout = storage.resolve_layout(identity, home=home, run_id="run-123")
        storage.write_workspace_locator(
            checkout, identity=identity, layout=layout, run_id="run-123")
        evidence = storage.evaluation_path(checkout)
        os.makedirs(os.path.dirname(evidence), exist_ok=True)
        with open(evidence, "w", encoding="utf-8") as handle:
            handle.write('{"verdict":"pass"}\n')
        head = _git(checkout, "rev-parse", "HEAD").stdout.strip()
        before = taskplane_lite.workspace_fingerprint(
            checkout, head, extra_paths=[evidence])
        with open(evidence, "w", encoding="utf-8") as handle:
            handle.write('{"verdict":"fail"}\n')
        after = taskplane_lite.workspace_fingerprint(
            checkout, head, extra_paths=[evidence])
        self.assertNotEqual(before, after)
        self.assertFalse(os.path.exists(os.path.join(checkout, ".eval")))

    def test_managed_parallel_worktree_gets_isolated_run_roots(self):
        home = tempfile.mkdtemp(prefix="tp-worker-layout-home-")
        checkout = tempfile.mkdtemp(prefix="tp-worker-layout-repo-")
        _git(checkout, "init", "-q")
        _git(checkout, "config", "user.email", "t@example.com")
        _git(checkout, "config", "user.name", "T")
        with open(os.path.join(checkout, "a.py"), "w",
                  encoding="utf-8") as handle:
            handle.write("x = 1\n")
        _git(checkout, "add", "a.py")
        _git(checkout, "commit", "-qm", "base")
        identity = storage.resolve_repository_identity(checkout)
        layout = storage.resolve_layout(identity, home=home, run_id="run-123")
        storage.write_workspace_locator(
            checkout, identity=identity, layout=layout, run_id="run-123")
        worker = storage.task_worktree_path(checkout, "t1")
        os.makedirs(os.path.dirname(worker), exist_ok=True)
        result = _git(checkout, "worktree", "add", "--detach", worker,
                      "HEAD")
        self.assertEqual(result.returncode, 0, result.stderr)
        storage.bind_worker_locator(checkout, worker, "t1")
        child = storage.load_workspace_locator(worker)
        parent = storage.load_workspace_locator(checkout)
        self.assertEqual(child["primary_checkout"], os.path.realpath(checkout))
        self.assertNotEqual(child["paths"]["state"],
                            parent["paths"]["state"])
        self.assertTrue(child["paths"]["state"].startswith(
            parent["paths"]["state"] + os.sep))
        self.assertEqual(design_contract._primary_workspace(worker),
                         os.path.realpath(checkout))
        self.assertFalse(worker.startswith(os.path.realpath(checkout) + os.sep))


class TestRunLocalWorkspaceLocatorReconstruction(unittest.TestCase):
    """A durable registration can restore only its exact run-local worker."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="tp-reconstruct-home-")
        self.primary = tempfile.mkdtemp(prefix="tp-reconstruct-primary-")
        _git(self.primary, "init", "-q")
        _git(self.primary, "config", "user.email", "t@example.com")
        _git(self.primary, "config", "user.name", "T")
        _git(self.primary, "remote", "add", "origin",
             "https://github.com/Example/Project.git")
        with open(os.path.join(self.primary, "a.py"), "w",
                  encoding="utf-8") as handle:
            handle.write("value = 1\n")
        _git(self.primary, "add", "a.py")
        self.assertEqual(_git(self.primary, "commit", "-qm", "base").returncode,
                         0)
        self.identity = storage.resolve_repository_identity(self.primary)
        self.run_id = "run-123"
        self.task_id = "t1"
        self.layout = storage.resolve_layout(
            self.identity, home=self.home, run_id=self.run_id)
        storage.write_workspace_locator(
            self.primary, identity=self.identity, layout=self.layout,
            run_id=self.run_id)
        self.worker = storage.task_worktree_path(self.primary, self.task_id)
        os.makedirs(os.path.dirname(self.worker), exist_ok=True)
        result = _git(self.primary, "worktree", "add", "-b", "tp/t1",
                      self.worker, "HEAD")
        self.assertEqual(result.returncode, 0, result.stderr)
        storage.bind_worker_locator(self.primary, self.worker, self.task_id)
        with open(os.path.join(self.worker, "a.py"), "w",
                  encoding="utf-8") as handle:
            handle.write("value = 2\n")
        _git(self.worker, "add", "a.py")
        self.assertEqual(_git(self.worker, "commit", "-qm", "target").returncode,
                         0)
        self.target = _git(self.worker, "rev-parse", "HEAD").stdout.strip()
        storage.refresh_task_worktree_tip(self.primary, self.task_id)
        self.registration_path = storage.task_worktree_registration_path(
            self.primary, self.task_id)
        self.worker_locator_path = storage._locator_path(self.worker)
        self.primary_locator_path = storage._locator_path(self.primary)

    def _remove_locators(self):
        os.unlink(self.worker_locator_path)
        os.unlink(self.primary_locator_path)

    def _recover(self, **overrides):
        values = {
            "primary_checkout": self.primary,
            "worker_checkout": self.worker,
            "task_id": self.task_id,
            "target_commit": self.target,
            "home": self.home,
        }
        values.update(overrides)
        return storage.reconstruct_worker_locator(**values)

    def test_registration_reconstructs_locator_and_scan_is_run_local(self):
        primary_graph = os.path.join(self.layout.graph_root, "graph.json")
        shared_graph = os.path.join(
            self.home, "projects", self.identity.key, "knowledge",
            "graph.json")
        for path, marker in ((primary_graph, "primary"),
                             (shared_graph, "shared")):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"meta": {"marker": marker}}, handle,
                          sort_keys=True)
        with open(primary_graph, "rb") as handle:
            primary_before = handle.read()
        with open(shared_graph, "rb") as handle:
            shared_before = handle.read()
        self._remove_locators()

        locator_path = self._recover()
        graph = depgraph.scan(self.worker)

        locator = storage.load_workspace_locator(self.worker)
        token = storage._worktree_token(self.task_id)
        self.assertEqual(locator_path, self.worker_locator_path)
        self.assertEqual(locator["run_id"], self.run_id)
        self.assertEqual(locator["paths"]["graph"], os.path.join(
            self.layout.graph_root, "worktrees", token))
        self.assertEqual(graph["meta"]["scanned_head"], self.target)
        self.assertTrue(os.path.isfile(os.path.join(
            locator["paths"]["graph"], "graph.json")))
        with open(primary_graph, "rb") as handle:
            self.assertEqual(handle.read(), primary_before)
        with open(shared_graph, "rb") as handle:
            self.assertEqual(handle.read(), shared_before)
        self.assertIsNone(storage.load_workspace_locator(self.primary))

    def test_missing_ambiguous_or_mismatched_registration_fails_closed(self):
        self._remove_locators()
        with open(self.registration_path, encoding="utf-8") as handle:
            original = json.load(handle)

        cases = {
            "run": {"run_id": "sibling-run"},
            "repository": {"repository_key": "foreign-repository"},
            "path": {"path": self.primary},
            "branch": {"branch_ref": "refs/heads/tp/other"},
            "task": {"task_id": "t2"},
            "target": {"branch_tip": "f" * 40},
            "linked": {"linked": False},
        }
        for label, change in cases.items():
            with self.subTest(label=label):
                value = dict(original)
                value.update(change)
                with open(self.registration_path, "w", encoding="utf-8") as h:
                    json.dump(value, h)
                with self.assertRaises(storage.StorageIdentityError):
                    self._recover()
                self.assertFalse(os.path.exists(self.worker_locator_path))

        with open(self.registration_path, "w", encoding="utf-8") as handle:
            json.dump(original, handle)
        sibling = os.path.join(
            self.home, "runs", "sibling-run", "state",
            "worktree-registrations", os.path.basename(self.registration_path))
        os.makedirs(os.path.dirname(sibling), exist_ok=True)
        with open(sibling, "w", encoding="utf-8") as handle:
            json.dump(original, handle)
        with self.assertRaises(storage.StorageIdentityError):
            self._recover()

    def test_head_and_unsafe_symlink_inputs_fail_closed(self):
        self._remove_locators()
        with open(os.path.join(self.worker, "a.py"), "a",
                  encoding="utf-8") as handle:
            handle.write("value = 3\n")
        _git(self.worker, "add", "a.py")
        self.assertEqual(_git(self.worker, "commit", "-qm", "moved").returncode,
                         0)
        with self.assertRaises(storage.StorageIdentityError):
            self._recover()
        alias_root = tempfile.mkdtemp(prefix="tp-worker-alias-")
        alias = os.path.join(alias_root, "worker")
        os.symlink(self.worker, alias)
        with self.assertRaises(storage.StorageIdentityError):
            self._recover(worker_checkout=alias)


class TestRunStore(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="tp-run-store-")
        self.identity = storage.identity_from_remote(
            "https://github.com/Example/Project.git")
        self.store = run_store.RunStore(home=self.home)

    def test_manifest_owns_repository_checkout_host_and_artifacts(self):
        manifest = self.store.create(
            self.identity, run_id="run-123", checkout="/tmp/project",
            host={"kind": "codex", "session_id": "thread-1"},
            target={"kind": "pr", "number": 7, "head": "a" * 40})
        self.assertEqual(manifest["schema"], "taskplane.run/v3")
        self.assertEqual(manifest["repository"]["repo_id"],
                         self.identity.repo_id)
        self.assertEqual(manifest["preflight"]["status"], "pending")
        self.assertEqual(manifest["revision"], 1)
        self.assertTrue(os.path.isfile(
            os.path.join(self.home, "runs", "run-123", "manifest.json")))

    def test_commit_is_revision_checked_and_atomic(self):
        self.store.create(self.identity, run_id="run-123",
                          checkout="/tmp/project", host={}, target={})
        updated = self.store.commit(
            "run-123", expected_revision=1,
            changes={"preflight": {"status": "ready"}})
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["preflight"]["status"], "ready")
        with self.assertRaises(run_store.RevisionConflict):
            self.store.commit("run-123", expected_revision=1,
                              changes={"status": "wrong"})
        with open(os.path.join(self.home, "runs", "run-123",
                               "manifest.json"), encoding="utf-8") as f:
            persisted = json.load(f)
        self.assertEqual(persisted, updated)


class TestLegacyStorageMigration(unittest.TestCase):
    def test_clean_scratch_clone_is_registered_without_being_moved(self):
        home = tempfile.mkdtemp(prefix="tp-migration-home-")
        ws = tempfile.mkdtemp(prefix="tp-migration-ws-")
        clone = os.path.join(ws, ".em-review", "scratch", "project")
        os.makedirs(clone)
        _git(clone, "init", "-q")
        _git(clone, "config", "user.email", "t@example.com")
        _git(clone, "config", "user.name", "T")
        with open(os.path.join(clone, "a.py"), "w", encoding="utf-8") as f:
            f.write("x = 1\n")
        _git(clone, "add", "a.py")
        _git(clone, "commit", "-qm", "base")
        _git(clone, "remote", "add", "origin",
             "https://github.com/Example/Project.git")
        report = storage_migration.migrate_legacy_checkouts(ws, home=home)
        self.assertEqual(report["adopted"], 1)
        self.assertEqual(report["review_required"], 0)
        self.assertTrue(os.path.isdir(clone), "migration must not move source")
        row = report["checkouts"][0]
        self.assertEqual(row["status"], "registered_legacy_alias")
        identity = storage.resolve_repository_identity(clone)
        record = os.path.join(home, "repositories", f"{identity.key}.json")
        with open(record, encoding="utf-8") as handle:
            persisted = json.load(handle)
        self.assertEqual(persisted["checkouts"][0]["path"],
                         os.path.realpath(clone))

    def test_dirty_scratch_clone_is_reported_and_not_registered(self):
        home = tempfile.mkdtemp(prefix="tp-migration-home-")
        ws = tempfile.mkdtemp(prefix="tp-migration-ws-")
        clone = os.path.join(ws, ".em-review", "scratch", "dirty")
        os.makedirs(clone)
        _git(clone, "init", "-q")
        _git(clone, "remote", "add", "origin",
             "https://github.com/Example/Dirty.git")
        with open(os.path.join(clone, "untracked.txt"), "w",
                  encoding="utf-8") as handle:
            handle.write("user data\n")
        report = storage_migration.migrate_legacy_checkouts(ws, home=home)
        self.assertEqual(report["adopted"], 0)
        self.assertEqual(report["review_required"], 1)
        self.assertEqual(report["checkouts"][0]["status"],
                         "dirty_user_checkout")


if __name__ == "__main__":
    unittest.main()
