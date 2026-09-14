"""Behavioral onboarding truth and static README link validation."""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from argparse import Namespace
from contextlib import redirect_stdout


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tp as cli  # noqa: E402
import dashboard  # noqa: E402
import host_capabilities  # noqa: E402
import pytest  # noqa: E402


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(*rel: str) -> str:
    with open(os.path.join(ROOT, *rel), encoding="utf-8") as f:
        return f.read()


README = _read("README.md")
MEMBER_DEAD_ENDS = (
    "/plugin marketplace add",
    "/plugin install",
    "claude plugin marketplace add",
    "codex plugin marketplace add",
    "codex plugin add",
    "Add from a repository",
)
FORBIDDEN_MEMBER_CLAIMS = (
    r"(?i)\bmembers?\s+can\s+(?:also\s+|simply\s+|just\s+)?"
    r"(?:install|add|sync)\b[^.\n]{0,80}(?:git\s?hub|marketplace|repository)",
    r"(?i)\bmembers?\s+(?:may|are\s+able\s+to)\s+"
    r"(?:install|add|sync)\b[^.\n]{0,80}(?:git\s?hub|marketplace|repository)",
    r"(?i)\bany\s+member\b[^.\n]{0,40}\binstall\b",
)


class TestPublicInstallClaims(unittest.TestCase):
    def test_public_copy_never_claims_org_members_can_install_from_github(self):
        files = [
            "README.md",
            ".claude-plugin/marketplace.json",
            ".claude-plugin/plugin.json",
            ".codex-plugin/plugin.json",
        ]
        failures = []
        for rel in files:
            text = _read(rel)
            for pattern in FORBIDDEN_MEMBER_CLAIMS:
                if re.search(pattern, text):
                    failures.append((rel, pattern))
        self.assertEqual(failures, [])

    def test_claim_scanner_is_not_vacuous(self):
        bad = "Team members can install taskplane from GitHub in one step."
        good = "Org members cannot add taskplane from GitHub themselves."
        self.assertTrue(any(re.search(pattern, bad) for pattern in FORBIDDEN_MEMBER_CLAIMS))
        self.assertFalse(any(re.search(pattern, good) for pattern in FORBIDDEN_MEMBER_CLAIMS))


class _TmpRepo(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = self._tmp.name
        subprocess.run(["git", "init", "-q"], cwd=self.ws, check=True)
        subprocess.run(
            [
                "git", "-c", "user.email=t@t", "-c", "user.name=t",
                "commit", "-q", "--allow-empty", "-m", "x",
            ],
            cwd=self.ws,
            check=True,
        )
        with open(os.path.join(self.ws, "app.py"), "w", encoding="utf-8") as f:
            f.write("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=self.ws, check=True)
        subprocess.run(
            [
                "git", "-c", "user.email=t@t", "-c", "user.name=t",
                "commit", "-q", "-m", "files",
            ],
            cwd=self.ws,
            check=True,
        )

class TestOnboardInstallTruth(_TmpRepo):
    def test_native_session_remains_ready_without_optional_launcher(self):
        env = {"CODEX_HOME": os.path.join(self.ws, "codex-home"),
               "CODEX_THREAD_ID": "reinstall-session",
               "TASKPLANE_HOME": os.path.join(self.ws, "state-home"),
               "TASKPLANE_MANAGED_HOOK_POLICY": "supported"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
                cli, "_install_context", return_value="personal"):
            self.assertTrue(cli._install_codex_hooks(self.ws)["ok"])
            os.makedirs(os.path.join(cli.tp.kb_root(self.ws), "context"))
            host_capabilities.record_runtime_hook_receipt(
                cli.tp.store_home(self.ws), hook_path="native",
                engine_fingerprint=cli.tp._entry_engine_fingerprint(), event={
                    "session_id": env["CODEX_THREAD_ID"],
                    "hook_event_name": "PreToolUse", "tool_use_id": "before-removal",
                    "cwd": self.ws})
            os.unlink(os.path.join(self.ws, ".taskplane", "codex-hook.py"))
            report = cli._onboard_report(self.ws)
            self.assertTrue(report["ready"], report)
            self.assertEqual(report["next_action"], "ready")
            restored = cli._initialize_entry(self.ws)
            self.assertTrue(restored["ready"], restored)
            self.assertEqual(restored["initialization"]["repairs"], [])
            self.assertFalse(os.path.exists(os.path.join(self.ws, ".taskplane", "codex-hook.py")))
            # Configuration alone must not bless a different host session.
            os.environ["CODEX_THREAD_ID"] = "new-session-without-hook"
            fresh = cli._onboard_report(self.ws)
            self.assertFalse(fresh["ready"])
            self.assertEqual(fresh["next_action"], "tp_init")

    def test_install_context_org_managed_via_host_marker(self):
        with tempfile.NamedTemporaryFile(suffix=".json") as marker:
            old = cli._MANAGED_SETTINGS_PATHS
            cli._MANAGED_SETTINGS_PATHS = (marker.name,)
            try:
                self.assertEqual(cli._install_context(), "org-managed")
            finally:
                cli._MANAGED_SETTINGS_PATHS = old

    def test_install_context_personal_via_plugin_path(self):
        old = cli._MANAGED_SETTINGS_PATHS
        cli._MANAGED_SETTINGS_PATHS = ()
        try:
            self.assertEqual(
                cli._install_context(
                    plugin_path="/home/u/.claude/plugins/marketplaces/x/tp.py"
                ),
                "personal",
            )
        finally:
            cli._MANAGED_SETTINGS_PATHS = old

    def test_install_context_undetectable_defaults_to_triage(self):
        old = cli._MANAGED_SETTINGS_PATHS
        cli._MANAGED_SETTINGS_PATHS = ()
        try:
            self.assertEqual(
                cli._install_context(plugin_path="/opt/somewhere/tp.py"),
                "unknown",
            )
        finally:
            cli._MANAGED_SETTINGS_PATHS = old

    def test_org_managed_output_never_prints_member_inaccessible_steps(self):
        text = "\n".join(cli._install_paths_lines("org-managed")).lower()
        for command in MEMBER_DEAD_ENDS:
            self.assertNotIn(command.lower(), text)
        self.assertIn("catalog", text)
        self.assertIn("admin", text)

    def test_unknown_context_prints_account_type_triage(self):
        lines = cli._install_paths_lines("unknown")
        text = "\n".join(lines).lower()
        for account_type in ("member", "admin", "personal"):
            self.assertIn(account_type, text)
        member_lines = [line for line in lines if "member" in line.lower()]
        self.assertTrue(member_lines)
        self.assertTrue(all("cannot" in line.lower() for line in member_lines))

    def test_codex_host_gets_codex_install_path(self):
        import unittest.mock as mock

        with mock.patch.dict(os.environ, {"CODEX_HOME": "/tmp/x"}):
            text = "\n".join(cli._install_paths_lines("unknown")).lower()
        self.assertIn("codex", text)
        self.assertNotIn("organization settings", text)

    def test_non_codex_host_keeps_account_triage(self):
        import unittest.mock as mock

        env = {
            key: value
            for key, value in os.environ.items()
            if key not in ("CODEX_HOME", "CODEX_THREAD_ID")
        }
        with mock.patch.dict(os.environ, env, clear=True):
            lines = cli._install_paths_lines("unknown")
        self.assertIn("member", "\n".join(lines).lower())

    def test_onboard_report_carries_install_paths(self):
        report = cli._onboard_report(self.ws)
        self.assertIn(report["install"]["context"], ("org-managed", "personal", "unknown"))
        self.assertTrue(report["install"]["paths"])
        self.assertEqual(report["phase_configuration"]["status"], "ready")
        self.assertEqual(report["phase_configuration"]["phases"],
            ["product", "design", "plan", "build", "evaluate", "engineering", "retro"])
        self.assertEqual(report["settings"]["effective_digest"],
                         report["phase_configuration"]["settings_digest"])

    def test_onboarding_refuses_broken_phase_links_before_dispatch(self):
        from unittest.mock import patch
        from taskplane import loop
        with patch.object(loop, "_phase_bridge_registry", side_effect=ValueError("changed skill content")):
            report = cli._onboard_report(self.ws)
        self.assertFalse(report["ready"])
        self.assertEqual(report["next_action"], "repair_phase_configuration")
        self.assertEqual(report["phase_configuration"]["error"], "changed skill content")

    def test_human_and_json_commands_expose_install_guidance(self):
        old = cli._MANAGED_SETTINGS_PATHS
        cli._MANAGED_SETTINGS_PATHS = ()
        human = io.StringIO()
        try:
            with redirect_stdout(human):
                rc = cli.cmd_onboard(Namespace(workspace=self.ws, json=False, out=None))
        finally:
            cli._MANAGED_SETTINGS_PATHS = old
        self.assertEqual(rc, 0)
        for account_type in ("member", "admin", "personal"):
            self.assertIn(account_type, human.getvalue().lower())

        machine = io.StringIO()
        with redirect_stdout(machine):
            rc = cli.cmd_onboard(Namespace(workspace=self.ws, json=True, out=None))
        self.assertEqual(rc, 0)
        self.assertIn("install", json.loads(machine.getvalue()))


class TestReadmeLinkHygiene(unittest.TestCase):
    LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")

    @staticmethod
    def _anchors(markdown: str) -> set[str]:
        anchors = set()
        for match in re.finditer(r"^#{1,6}\s+(.*)$", markdown, re.M):
            slug = re.sub(r"[`*_~]", "", match.group(1).strip().lower())
            slug = re.sub(r"[^\w\s-]", "", slug)
            anchors.add(re.sub(r"\s+", "-", slug.strip()))
        return anchors

    def test_setup_and_harness_links_resolve(self):
        for source in ("README.md", "docs/onboarding.md", "docs/configuration.md",
                       "skills/tp-go/references/setup.md"):
            with self.subTest(source=source):
                text = _read(source)
                for target in self.LINK.findall(text):
                    if target.startswith(("http://", "https://")):
                        self.assertRegex(target, r"https?://[\w.-]+(?::\d+)?(?:/\S*)?$")
                        continue
                    path, _, fragment = target.partition("#")
                    relative = os.path.normpath(os.path.join(os.path.dirname(source), path)) if path else source
                    self.assertTrue(os.path.isfile(os.path.join(ROOT, relative)), (source, target))
                    if fragment and relative.endswith(".md"):
                        self.assertIn(fragment, self._anchors(_read(relative)), (source, target))


@pytest.mark.parametrize("action", [
    "install_codex_hooks", "install_or_enable_hooks", "start_new_session",
    "check_hook_identity", "contact_administrator", "review_repository_trust",
    "recover_run_binding", "archive_run", "repair_phase_configuration",
    "resume_run", "unknown-future-action", "ready", None,
])
def test_incomplete_onboarding_never_offers_start(action):
    report = {"ready": False, "next_action": action, "checks": [{
        "id": "blocked", "label": "Setup <check>", "ok": False,
        "detail": "missing", "hint": "Needs <attention>",
    }]}
    rendered = dashboard.render_onboarding(report)
    assert "Ready to go" not in rendered
    assert "ready for governed work" not in dashboard.headline_onboarding(report)
    assert "Continue setup" in rendered
    assert "Needs &lt;attention&gt;" in rendered


def test_ready_onboarding_preserves_original_request_and_checks():
    report = {"ready": True, "next_action": "ready", "checks": [{
        "id": "hook", "label": "Hook", "ok": True}]}
    rendered = dashboard.render_onboarding(report)
    assert "Ready to go" in rendered
    assert "Continue my original TaskPlane request" in rendered
    report["checks"][0]["ok"] = False
    assert "Ready to go" not in dashboard.render_onboarding(report)


if __name__ == "__main__":
    unittest.main()
