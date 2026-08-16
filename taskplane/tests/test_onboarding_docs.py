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
from argparse import Namespace
from contextlib import redirect_stdout


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tp as cli  # noqa: E402


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

    def tearDown(self):
        self._tmp.cleanup()


class TestOnboardInstallTruth(_TmpRepo):
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

    def test_every_readme_link_is_statically_valid(self):
        bad = []
        for target in self.LINK.findall(README):
            if target.startswith(("http://", "https://")):
                if not re.match(r"https?://[\w.-]+(?::\d+)?(?:/\S*)?$", target):
                    bad.append((target, "malformed URL"))
                continue
            if target.startswith("#"):
                if target[1:] not in self._anchors(README):
                    bad.append((target, "internal anchor not found"))
                continue
            path, _, fragment = target.partition("#")
            full = os.path.join(ROOT, path)
            if not os.path.exists(full):
                bad.append((target, "relative target missing"))
            elif fragment and path.endswith(".md"):
                if fragment not in self._anchors(_read(path)):
                    bad.append((target, "anchor missing in target"))
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
