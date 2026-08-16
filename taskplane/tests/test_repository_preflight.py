"""Repository acquisition is automatic, resumable, and pre-contract."""
import os
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import preflight  # noqa: E402
import repository  # noqa: E402
import run_store  # noqa: E402
import storage  # noqa: E402
import taskplane_lite  # noqa: E402
import tp as cli  # noqa: E402


PR = "https://github.com/Example/Project/pull/7"
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


def _tools(*, git=True, gh=True, authenticated=True):
    return {
        "git": {"present": git, "version": "git 2", "path": "git"},
        "gh": {"present": gh, "version": "gh 2", "path": "gh",
               "authenticated": authenticated},
    }


def _checkout(path):
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return path


def _review_checkout(path):
    _checkout(path)
    subprocess.run(["git", "config", "user.email", "t@example.com"],
                   cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=path,
                   check=True)
    subprocess.run(["git", "remote", "add", "origin",
                    "https://github.com/Example/Project.git"], cwd=path,
                   check=True)
    with open(os.path.join(path, "a.py"), "w", encoding="utf-8") as handle:
        handle.write("def value():\n    return 1\n")
    subprocess.run(["git", "add", "a.py"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=path,
                   check=True)
    base = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()
    with open(os.path.join(path, "a.py"), "w", encoding="utf-8") as handle:
        handle.write("def value():\n    return 2\n")
    subprocess.run(["git", "commit", "-qam", "head"], cwd=path,
                   check=True)
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()
    return base, head


class _Acquirer:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def acquire_pr(self, identity, target):
        self.calls.append((identity, target))
        if self.error:
            raise self.error
        return self.result

    def acquire_repository(self, identity, target):
        self.calls.append((identity, target))
        if self.error:
            raise self.error
        return self.result


class TestInteractivePreflight(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="tp-preflight-home-")
        self.ws = tempfile.mkdtemp(prefix="tp-preflight-ws-")

    def engine(self, tools, acquirer=None):
        return preflight.RepositoryPreflight(
            home=self.home, tools_provider=lambda: tools,
            acquirer=acquirer or _Acquirer())

    def test_missing_gh_prompts_and_never_activates_contract(self):
        acquirer = _Acquirer()
        out = self.engine(_tools(gh=False), acquirer).prepare(
            PR, workspace=self.ws, host={"kind": "codex"}, run_id="r1")
        self.assertEqual(out["status"], "needs_user")
        self.assertEqual(out["action"]["kind"], "install_gh")
        self.assertEqual(acquirer.calls, [])
        persisted = run_store.RunStore(home=self.home).load("r1")
        self.assertEqual(persisted["contract"]["status"], "inactive")

    def test_missing_authentication_prompts_instead_of_failing(self):
        out = self.engine(_tools(authenticated=False)).prepare(
            PR, workspace=self.ws, host={"kind": "codex"}, run_id="r2")
        self.assertEqual(out["status"], "needs_user")
        self.assertEqual(out["action"]["kind"], "authenticate_gh")
        self.assertIn("gh", out["action"]["command_argv"])

    def test_checkout_or_network_problem_is_a_retry_prompt(self):
        error = repository.RepositoryAcquisitionError(
            "network", "origin is temporarily unreachable")
        out = self.engine(_tools(), _Acquirer(error=error)).prepare(
            PR, workspace=self.ws, host={"kind": "codex"}, run_id="r3")
        self.assertEqual(out["status"], "needs_user")
        self.assertEqual(out["action"]["kind"], "retry_acquisition")
        self.assertIn("unreachable", out["action"]["detail"])

    def test_successful_pr_preflight_owns_checkout_and_stays_precontract(self):
        checkout = _checkout(os.path.join(
            self.home, "checkouts", "project", "worktrees", "pr-7-aaaa"))
        result = repository.AcquisitionResult(
            checkout=checkout, base_ref="origin/main", base="b" * 40,
            head="a" * 40, merge_base="c" * 40,
            changed_files=("src/a.py",), metadata={"title": "Example"})
        out = self.engine(_tools(), _Acquirer(result=result)).prepare(
            PR, workspace=self.ws, host={"kind": "codex"}, run_id="r4")
        self.assertEqual(out["status"], "ready")
        self.assertEqual(out["checkout"], checkout)
        persisted = run_store.RunStore(home=self.home).load("r4")
        self.assertEqual(persisted["repository"]["checkout"], checkout)
        self.assertEqual(persisted["contract"]["status"], "inactive")
        self.assertNotIn(".em-review", checkout)

    def test_user_authorization_is_persisted_and_resumable(self):
        engine = self.engine(_tools(authenticated=False))
        blocked = engine.prepare(PR, workspace=self.ws,
                                 host={"kind": "codex"}, run_id="r5")
        resumed = engine.authorize(
            "r5", action_id=blocked["action"]["action_id"],
            response="approve", approved_by="user in Codex chat")
        self.assertEqual(resumed["status"], "authorized")
        self.assertEqual(resumed["next"], "execute_action_then_retry")
        self.assertEqual(resumed["command_argv"][:2], ["gh", "auth"])
        persisted = run_store.RunStore(home=self.home).load("r5")
        self.assertEqual(persisted["preflight"]["status"], "authorized")
        self.assertEqual(persisted["preflight"]["authorization"]["by"],
                         "user in Codex chat")

    def test_approved_action_executes_once_and_resumes_the_same_run(self):
        calls = []
        states = iter((_tools(authenticated=False), _tools()))
        checkout = _checkout(os.path.join(
            self.home, "checkouts", "p", "worktrees", "pr-7-aaaa"))
        result = repository.AcquisitionResult(
            checkout=checkout, base_ref="origin/main", base="b" * 40,
            head="a" * 40, merge_base="c" * 40,
            changed_files=("src/a.py",), metadata={"title": "Example"})
        engine = preflight.RepositoryPreflight(
            home=self.home, tools_provider=lambda: next(states),
            acquirer=_Acquirer(result=result),
            action_runner=lambda argv: calls.append(list(argv)) or {
                "returncode": 0, "output": "authenticated"})
        blocked = engine.prepare(
            PR, workspace=self.ws, host={"kind": "codex"}, run_id="r6")
        out = engine.resume(
            "r6", action_id=blocked["action"]["action_id"],
            response="approve", approved_by="user in Codex chat")
        self.assertEqual(out["status"], "ready")
        self.assertEqual(out["run_id"], "r6")
        self.assertEqual(calls, [["gh", "auth", "login", "--web"]])

    def test_retry_rechecks_state_without_replaying_interactive_auth(self):
        error = repository.RepositoryAcquisitionError(
            "authentication", "credentials were refreshed elsewhere")
        engine = self.engine(_tools(), _Acquirer(error=error))
        blocked = engine.prepare(
            PR, workspace=self.ws, host={"kind": "codex"}, run_id="r6-retry")
        authorized = engine.authorize(
            "r6-retry", action_id=blocked["action"]["action_id"],
            response="retry", approved_by="user in Codex chat")
        self.assertEqual(authorized["command_argv"], [])
        self.assertEqual(authorized["command_argv_sequence"], [])

    def test_failed_approved_action_returns_to_user_instead_of_crashing(self):
        engine = preflight.RepositoryPreflight(
            home=self.home,
            tools_provider=lambda: _tools(authenticated=False),
            acquirer=_Acquirer(),
            action_runner=lambda _argv: {"returncode": 1,
                                         "output": "login cancelled"})
        blocked = engine.prepare(
            PR, workspace=self.ws, host={"kind": "codex"}, run_id="r7")
        out = engine.resume(
            "r7", action_id=blocked["action"]["action_id"],
            response="approve", approved_by="user in Codex chat")
        self.assertEqual(out["status"], "needs_user")
        self.assertEqual(out["action"]["kind"], "authenticate_gh")
        self.assertIn("cancelled", out["action"]["detail"])
        persisted = run_store.RunStore(home=self.home).load("r7")
        self.assertEqual(persisted["contract"]["status"], "inactive")

    def test_storage_permission_is_an_approval_prompt_not_a_traceback(self):
        checkout = os.path.join(self.home, "checkouts", "p", "worktrees",
                                "pr-7-aaaa")
        result = repository.AcquisitionResult(
            checkout=checkout, base_ref="origin/main", base="b" * 40,
            head="a" * 40, merge_base="c" * 40,
            changed_files=("src/a.py",), metadata={"title": "Example"})
        with mock.patch.object(
                storage, "write_workspace_locator",
                side_effect=PermissionError("approval required")):
            out = self.engine(_tools(), _Acquirer(result=result)).prepare(
                PR, workspace=self.ws, host={"kind": "codex"}, run_id="r8")
        self.assertEqual(out["status"], "needs_user")
        self.assertEqual(out["action"]["kind"], "authorize_storage_root")
        persisted = run_store.RunStore(home=self.home).load("r8")
        self.assertEqual(persisted["contract"]["status"], "inactive")

    def test_plain_repository_url_is_acquired_not_treated_as_local_ref(self):
        checkout = _checkout(os.path.join(
            self.home, "checkouts", "project", "worktrees", "repo-aaaa"))
        result = repository.AcquisitionResult(
            checkout=checkout, base_ref="", base="a" * 40,
            head="a" * 40, merge_base="a" * 40, changed_files=(),
            metadata={"url": "https://github.com/Example/Project"})
        acquirer = _Acquirer(result=result)
        out = self.engine(_tools(gh=False), acquirer).prepare(
            "https://github.com/Example/Project", workspace=self.ws,
            host={"kind": "codex"}, run_id="repo-url")
        self.assertEqual(out["status"], "ready")
        self.assertEqual(out["checkout"], checkout)
        self.assertEqual(acquirer.calls[0][1]["kind"], "repository")

    def test_repository_authentication_is_a_user_action(self):
        error = repository.RepositoryAcquisitionError(
            "authentication", "private repository requires credentials")
        out = self.engine(
            _tools(gh=False), _Acquirer(error=error)).prepare(
                "https://github.com/Example/Private", workspace=self.ws,
                host={"kind": "codex"}, run_id="repo-auth")
        self.assertEqual(out["status"], "needs_user")
        self.assertEqual(out["action"]["kind"],
                         "authenticate_repository")
        self.assertIn("credentials", out["action"]["detail"])


class TestLocalRepositoryPreflight(unittest.TestCase):
    def test_local_committed_repository_is_ready_without_gh(self):
        home = tempfile.mkdtemp(prefix="tp-preflight-home-")
        ws = tempfile.mkdtemp(prefix="tp-local-repo-")
        subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"],
                       cwd=ws, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=ws,
                       check=True)
        with open(os.path.join(ws, "a.py"), "w", encoding="utf-8") as handle:
            handle.write("x = 1\n")
        subprocess.run(["git", "add", "a.py"], cwd=ws, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=ws,
                       check=True)
        engine = preflight.RepositoryPreflight(
            home=home, tools_provider=lambda: _tools(gh=False),
            acquirer=_Acquirer())
        out = engine.prepare("", workspace=ws, host={"kind": "codex"},
                             run_id="local-1")
        self.assertEqual(out["status"], "ready")
        self.assertEqual(out["checkout"], os.path.realpath(ws))

    def test_explicit_local_path_is_prepared_instead_of_current_workspace(self):
        home = tempfile.mkdtemp(prefix="tp-preflight-home-")
        current = tempfile.mkdtemp(prefix="tp-current-workspace-")
        target = tempfile.mkdtemp(prefix="tp-explicit-repo-")
        subprocess.run(["git", "init", "-q"], cwd=target, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"],
                       cwd=target, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=target,
                       check=True)
        with open(os.path.join(target, "a.py"), "w", encoding="utf-8") as h:
            h.write("x = 1\n")
        subprocess.run(["git", "add", "a.py"], cwd=target, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=target,
                       check=True)
        out = preflight.RepositoryPreflight(
            home=home, tools_provider=lambda: _tools(gh=False),
            acquirer=_Acquirer()).prepare(
                target, workspace=current, host={"kind": "codex"},
                run_id="local-path")
        self.assertEqual(out["status"], "ready")
        self.assertEqual(out["checkout"], os.path.realpath(target))

    def test_explicit_initialize_approval_creates_baseline_and_resumes(self):
        home = tempfile.mkdtemp(prefix="tp-preflight-home-")
        target = tempfile.mkdtemp(prefix="tp-new-repo-")
        with open(os.path.join(target, "README.md"), "w",
                  encoding="utf-8") as handle:
            handle.write("# New\n")
        engine = preflight.RepositoryPreflight(
            home=home, tools_provider=lambda: _tools(gh=False),
            acquirer=_Acquirer())
        blocked = engine.prepare(
            target, workspace=tempfile.mkdtemp(), host={"kind": "codex"},
            run_id="initialize-local")
        self.assertEqual(blocked["status"], "needs_user")
        self.assertEqual(blocked["action"]["kind"],
                         "initialize_or_commit_git")
        resumed = engine.resume(
            "initialize-local", action_id=blocked["action"]["action_id"],
            response="initialize", approved_by="user in chat")
        self.assertEqual(resumed["status"], "ready")
        self.assertEqual(subprocess.check_output(
            ["git", "rev-list", "--count", "HEAD"], cwd=target,
            text=True).strip(), "1")


class TestReviewCliPreflightBoundary(unittest.TestCase):
    def test_remote_pr_pause_is_printed_before_contract_or_graph_work(self):
        home = tempfile.mkdtemp(prefix="tp-cli-preflight-home-")
        ws = tempfile.mkdtemp(prefix="tp-cli-preflight-ws-")
        old = os.environ.get("TASKPLANE_HOME")
        os.environ["TASKPLANE_HOME"] = home
        action = {
            "schema": "taskplane.user-action/v1", "action_id": "action-1",
            "kind": "authenticate_gh", "prompt": "Sign in", "detail": "x",
            "command_argv": ["gh", "auth", "login", "--web"],
            "choices": ["approve", "cancel"]}
        output = io.StringIO()
        try:
            with mock.patch.object(
                    preflight.RepositoryPreflight, "prepare",
                    return_value={"schema": "taskplane.preflight/v1",
                                  "run_id": "run-1",
                                  "status": "needs_user",
                                  "action": action, "revision": 2}), \
                    contextlib.redirect_stdout(output):
                rc = cli.main(["review", "start", PR,
                               "--workspace", ws])
            self.assertEqual(rc, 2)
            self.assertEqual(__import__("json").loads(
                output.getvalue())["action"], action)
            self.assertFalse((taskplane_lite.load_active(ws) or {}).get(
                "task_id"))
            self.assertFalse(os.path.exists(os.path.join(ws, ".em-review")))
        finally:
            if old is None:
                os.environ.pop("TASKPLANE_HOME", None)
            else:
                os.environ["TASKPLANE_HOME"] = old

    def test_plain_repository_url_uses_preflight_before_review(self):
        ws = tempfile.mkdtemp(prefix="tp-cli-repository-url-")
        expected = {"schema": "taskplane.preflight/v1",
                    "run_id": "repository-review", "status": "needs_user",
                    "action": {"kind": "retry_acquisition"}}
        output = io.StringIO()
        with mock.patch.object(
                preflight.RepositoryPreflight, "prepare",
                return_value=expected) as prepare, \
                contextlib.redirect_stdout(output):
            rc = cli.main(["review", "start",
                           "https://github.com/Example/Project",
                           "--workspace", ws])
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(output.getvalue()), expected)
        self.assertEqual(prepare.call_args.args[0],
                         "https://github.com/Example/Project")

    def test_generic_repository_surface_uses_the_same_preflight_kernel(self):
        ws = tempfile.mkdtemp(prefix="tp-repository-cli-")
        expected = {"schema": "taskplane.preflight/v1", "run_id": "run-2",
                    "status": "needs_user", "action": {"kind": "install_gh"}}
        output = io.StringIO()
        with mock.patch.object(
                preflight.RepositoryPreflight, "prepare",
                return_value=expected) as prepare, \
                contextlib.redirect_stdout(output):
            rc = cli.main(["repository", "prepare", PR,
                           "--workspace", ws])
        self.assertEqual(rc, 2)
        self.assertEqual(__import__("json").loads(output.getvalue()), expected)
        self.assertEqual(prepare.call_args.kwargs["workspace"], ws)

    def test_ready_kernel_activates_only_after_repository_and_route_ready(self):
        home = tempfile.mkdtemp(prefix="tp-ready-review-home-")
        checkout = os.path.join(home, "checkouts", "p", "worktrees", "pr")
        base, head = _review_checkout(checkout)
        acquired = repository.AcquisitionResult(
            checkout=checkout, base_ref=base, base=base, head=head,
            merge_base=base, changed_files=("a.py",), metadata={})
        engine = preflight.RepositoryPreflight(
            home=home, tools_provider=lambda: _tools(),
            acquirer=_Acquirer(result=acquired))
        prepared = engine.prepare(
            PR, workspace=tempfile.mkdtemp(), host={"kind": "codex"},
            run_id="review-ready")
        old = os.environ.get("TASKPLANE_HOME")
        os.environ["TASKPLANE_HOME"] = home
        output = io.StringIO()
        ready = {"schema": "taskplane.review-start-manifest/v2",
                 "status": "ready", "run_id": "kernel-ready",
                 "briefs": [], "routing_decision": {},
                 "counters": {"top_level_cli_calls": 1}}
        try:
            with mock.patch.object(
                    preflight.RepositoryPreflight, "prepare",
                    return_value=prepared), \
                    mock.patch("target.resolve_pr_head",
                               return_value={"ok": False}), \
                    mock.patch("review.start_review", return_value=ready), \
                    mock.patch("review._load_state", return_value={}), \
                    mock.patch("review._save_state"), \
                    mock.patch.object(cli, "_review_visuals",
                                      return_value=({}, [])), \
                    mock.patch.object(cli, "_seed_owed", return_value=[]), \
                    contextlib.redirect_stdout(output):
                rc = cli.main(["review", "start", PR,
                               "--workspace", tempfile.mkdtemp()])
            self.assertEqual(rc, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["contract"]["status"], "active")
            manifest = run_store.RunStore(home=home).load("review-ready")
            self.assertEqual(manifest["status"], "governed")
            self.assertEqual(manifest["review"]["status"], "ready")
            self.assertTrue(taskplane_lite.load_active(checkout))
            self.assertFalse(os.path.exists(os.path.join(
                checkout, ".em-review")))
        finally:
            if old is None:
                os.environ.pop("TASKPLANE_HOME", None)
            else:
                os.environ["TASKPLANE_HOME"] = old

    def test_sparse_kernel_leaves_repository_run_and_contract_inactive(self):
        home = tempfile.mkdtemp(prefix="tp-sparse-review-home-")
        checkout = os.path.join(home, "checkouts", "p", "worktrees", "pr")
        base, head = _review_checkout(checkout)
        acquired = repository.AcquisitionResult(
            checkout=checkout, base_ref=base, base=base, head=head,
            merge_base=base, changed_files=("a.py",), metadata={})
        prepared = preflight.RepositoryPreflight(
            home=home, tools_provider=lambda: _tools(),
            acquirer=_Acquirer(result=acquired)).prepare(
                PR, workspace=tempfile.mkdtemp(), host={"kind": "codex"},
                run_id="review-sparse")
        old = os.environ.get("TASKPLANE_HOME")
        os.environ["TASKPLANE_HOME"] = home
        sparse = {"schema": "taskplane.review-start-manifest/v2",
                  "status": "impact_incomplete", "run_id": "kernel-sparse",
                  "briefs": [], "counters": {"top_level_cli_calls": 1}}
        try:
            with mock.patch.object(
                    preflight.RepositoryPreflight, "prepare",
                    return_value=prepared), \
                    mock.patch("target.resolve_pr_head",
                               return_value={"ok": False}), \
                    mock.patch("review.start_review", return_value=sparse), \
                    contextlib.redirect_stdout(io.StringIO()):
                rc = cli.main(["review", "start", PR,
                               "--workspace", tempfile.mkdtemp()])
            self.assertEqual(rc, 1)
            manifest = run_store.RunStore(home=home).load("review-sparse")
            self.assertEqual(manifest["status"], "review_blocked")
            self.assertEqual(manifest["contract"]["status"], "inactive")
            self.assertFalse(taskplane_lite.load_active(checkout))
        finally:
            if old is None:
                os.environ.pop("TASKPLANE_HOME", None)
            else:
                os.environ["TASKPLANE_HOME"] = old


class TestManagedMirrorAcquisition(unittest.TestCase):
    def test_failed_clone_leaves_no_partial_canonical_mirror(self):
        home = tempfile.mkdtemp(prefix="tp-mirror-atomic-")
        manager = repository.RepositoryManager(home=home)
        identity = storage.identity_from_remote(
            "https://github.com/Example/Project.git")
        layout = storage.resolve_layout(
            identity, home=home, run_id="acquisition")

        def fail_after_partial(argv, **_kwargs):
            candidate = argv[-1]
            os.makedirs(candidate, exist_ok=True)
            raise repository.RepositoryAcquisitionError(
                "network", "connection interrupted")

        with mock.patch.object(manager, "_run", side_effect=fail_after_partial):
            with self.assertRaises(repository.RepositoryAcquisitionError):
                manager._ensure_mirror(identity, layout)
        self.assertFalse(os.path.exists(layout.mirror_path))
        leftovers = [name for name in os.listdir(layout.checkout_root)
                     if name.startswith(".mirror-acquire-")]
        self.assertEqual(leftovers, [])


class TestRepositoryFlowDocumentation(unittest.TestCase):
    def _read(self, relative):
        with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
            return handle.read()

    def test_every_code_or_repository_flow_uses_the_precondition(self):
        for skill in ("taskplane", "tp-go", "tp-build", "tp-design",
                      "tp-product", "tp-engineering", "tp-northstar",
                      "tp-tag"):
            text = self._read(f"skills/{skill}/SKILL.md")
            self.assertIn("repository prepare", text, skill)
        self.assertIn("repository status", self._read(
            "skills/tp-status/SKILL.md"))

    def test_active_instructions_do_not_clone_source_into_review_artifacts(self):
        active = "\n".join(self._read(path) for path in (
            "skills/taskplane/SKILL.md", "skills/tp-go/SKILL.md",
            "skills/tp-engineering/SKILL.md",
            "skills/tp-engineering/references/em-session.md"))
        self.assertNotIn("Clone into `.em-review", active)
        self.assertNotIn("git clone --depth 1 <url> <scratch-dir>", active)

    def test_storage_document_names_all_hybrid_roots(self):
        text = self._read("docs/storage-and-repositories.md")
        for root in ("repositories/<repository-key>.json",
                     "checkouts/<repository-key>/mirror.git",
                     "projects/<repository-key>/knowledge/",
                     "runs/<run-id>/", "cache/graphs/<repository-key>"):
            self.assertIn(root, text)


if __name__ == "__main__":
    unittest.main()
