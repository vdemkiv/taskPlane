"""t8 (R-0005) — install truth: the onboarding failure that blocked real users.

Org/Team members CANNOT install plugins from GitHub; only org admins can
publish to the org marketplace, and file-upload is the other path. These
tests pin the honest install story so it cannot silently regress:

  - the README install section is an account-type decision tree with the
    member path FIRST (pinned by section order);
  - the member section contains ZERO dead-end instructions (no GitHub
    marketplace-add or install command a member cannot run) and states the
    restriction, the ask-your-admin path, the file-upload path, and the
    try-on-personal fallback;
  - the admin section carries the publish-to-org-marketplace steps
    (Organization settings > Plugins; file upload; GitHub sync requires a
    private/internal mirror) and the availability settings;
  - the personal and Codex sections keep the direct install commands;
  - no public copy (README + the three plugin manifests) affirmatively
    claims an org member can install from GitHub — the same forbidden-claim
    scan the CI docs leg runs repo-wide;
  - three per-host quickstarts (Claude Code / Cowork / Codex) exist, each
    ending in the "set up taskplane" prompt; the Codex quickstart never
    references workflow-only features; quickstart `tp <sub>` commands are
    smoke-checked against tp.py's real argparse subparsers;
  - `tp onboard` prints account-type install paths: the org-managed context
    (host markers) gets the matching path, otherwise the by-account-type
    triage; no context ever prints a member-inaccessible step;
  - marketplace.json / plugin.json / codex plugin.json copy stays aligned
    (names, versions, descriptions consistent);
  - README links are statically valid (URL format + existing relative
    targets + resolvable internal anchors) — no network, no flakes.

READ-ONLY toward taskplane/*.py and the docs: these tests inspect, they
never modify.

NOTE (R-0010 D2, WS-D): the facade/driver routing-determinism pins — tp-go's
description carries the internal-delivery-driver phrasing and NOT the
facade's 'implement X' trigger, while skills/taskplane/SKILL.md keeps it —
live in taskplane/tests/test_release_freshness.py
(TestFacadeDriverRoutingDeterminism), not here. R-0010's acceptance permits
either file ("test_onboarding_docs.py (or freshness test)"); they landed
next to the other skill-freshness checks so the whole skills/ gate reads in
one place, and both files run in the same declared command. Look there
before adding a duplicate here.
"""
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

ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(*rel):
    with open(os.path.join(ROOT, *rel), encoding="utf-8") as f:
        return f.read()


README = None  # populated in setUpModule so a missing README fails loudly


def setUpModule():
    global README
    README = _read("README.md")


# --------------------------------------------------------------------------
# Forbidden-claim scan — shared with the CI docs leg (same regexes).
# A claim is forbidden when it AFFIRMATIVELY says an org member can
# install/add a plugin from GitHub / a marketplace. Negated statements
# ("members cannot add …") do not match: `can\s+` never matches "can't" or
# "cannot", and "can not install" breaks the adjacency the regex requires.
FORBIDDEN_MEMBER_CLAIMS = [
    r"(?i)\bmembers?\s+can\s+(?:also\s+|simply\s+|just\s+)?"
    r"(?:install|add|sync)\b[^.\n]{0,80}(?:git\s?hub|marketplace|repository)",
    r"(?i)\bmembers?\s+(?:may|are\s+able\s+to)\s+"
    r"(?:install|add|sync)\b[^.\n]{0,80}(?:git\s?hub|marketplace|repository)",
    r"(?i)\bany\s+member\b[^.\n]{0,40}\binstall\b",
]

# Command literals a Team/Enterprise member cannot run — the member section
# must never contain them (zero dead-end instructions).
MEMBER_DEAD_ENDS = [
    "/plugin marketplace add",
    "/plugin install",
    "claude plugin marketplace add",
    "codex plugin marketplace add",
    "codex plugin add",
    "Add from a repository",
]

PUBLIC_COPY = [
    ("README.md",),
    (".claude-plugin", "marketplace.json"),
    (".claude-plugin", "plugin.json"),
    (".codex-plugin", "plugin.json"),
]


def _section(text, heading, stop_level=None):
    """Return the body of a markdown section: from its heading line to the
    next heading of the same-or-higher level. Lines inside ``` code fences
    are never treated as headings (a `# comment` in a shell block is code)."""
    lines = text.splitlines(keepends=True)
    start = None
    level = None
    fenced = False
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        m = re.match(r"(#{1,6})\s+(.*)", ln)
        if m and heading in m.group(2):
            start, level = i, len(m.group(1))
            break
    if start is None:
        raise AssertionError(f"README section not found: {heading!r}")
    stop = stop_level or level
    fenced = False
    for j in range(start + 1, len(lines)):
        if lines[j].lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        m = re.match(r"(#{1,6})\s+", lines[j])
        if m and len(m.group(1)) <= stop:
            return "".join(lines[start:j])
    return "".join(lines[start:])


class TestForbiddenMemberClaims(unittest.TestCase):
    def test_no_public_copy_claims_members_install_from_github(self):
        for rel in PUBLIC_COPY:
            text = _read(*rel)
            for pat in FORBIDDEN_MEMBER_CLAIMS:
                hits = re.findall(pat, text)
                self.assertEqual(
                    hits, [],
                    f"{'/'.join(rel)} affirmatively claims an org member can "
                    f"install from GitHub (pattern {pat!r}): {hits}")

    def test_the_scan_would_catch_the_bad_claim(self):
        # self-test: the regexes are not vacuous
        bad = "Team members can install taskplane from GitHub in one step."
        self.assertTrue(
            any(re.search(p, bad) for p in FORBIDDEN_MEMBER_CLAIMS))
        ok = "Org members cannot add taskplane from GitHub themselves."
        self.assertFalse(
            any(re.search(p, ok) for p in FORBIDDEN_MEMBER_CLAIMS))


class TestInstallDecisionTree(unittest.TestCase):
    MEMBER = "Team or Enterprise account (not an org admin)"
    ADMIN = "I'm an org admin"
    PERSONAL = "Personal, Pro, or Max account"
    CODEX = "Codex (OpenAI)"

    def test_member_path_first_pinned_by_section_order(self):
        install = README.index("## Install")
        member = README.index(self.MEMBER)
        admin = README.index("### " + self.ADMIN)
        personal = README.index(self.PERSONAL)
        codex = README.index(self.CODEX)
        self.assertTrue(install < member < admin < personal < codex,
                        "install decision tree must be ordered member -> "
                        "admin -> personal -> codex (member path FIRST)")

    def test_member_section_zero_dead_end_instructions(self):
        body = _section(README, self.MEMBER)
        for cmd in MEMBER_DEAD_ENDS:
            self.assertNotIn(
                cmd, body,
                f"member section contains an instruction a Team/Enterprise "
                f"member cannot run: {cmd!r}")

    def test_member_section_states_restriction_and_real_paths(self):
        body = _section(README, self.MEMBER)
        low = body.lower()
        self.assertIn("cannot", low, "the member restriction must be stated "
                      "plainly — no marketing gloss")
        self.assertIn("github", low)
        self.assertIn("admin", low, "ask-your-admin path missing")
        self.assertIn("upload", low, "file-upload path missing")
        self.assertIn("catalog", low, "the org plugin catalog is the "
                      "member's real install source")
        self.assertIn("personal", low, "try-on-personal fallback missing")

    def test_admin_section_publish_steps(self):
        # normalize markdown hard-wraps before matching multiword phrases
        body = " ".join(_section(README, self.ADMIN).split())
        self.assertIn("Organization settings", body)
        self.assertIn("Plugins", body)
        low = body.lower()
        self.assertIn("upload", low)
        # honest GitHub-sync constraint from the org docs: the synced
        # marketplace repository must be private or internal
        self.assertIn("private or internal", low)
        for availability in ("Available for install", "Required"):
            self.assertIn(availability, body)

    def test_personal_section_keeps_direct_commands(self):
        body = _section(README, self.PERSONAL)
        self.assertIn("/plugin marketplace add vdemkiv/taskPlane", body)
        self.assertIn("/plugin install taskplane@taskplane-marketplace", body)

    def test_codex_section_keeps_package_path(self):
        body = _section(README, self.CODEX)
        self.assertIn("codex plugin marketplace add vdemkiv/taskPlane", body)
        self.assertIn("codex plugin add taskplane", body)

    def test_docs_never_instruct_weakening(self):
        install = _section(README, "Install", stop_level=2)
        for phrase in ("disable the hook", "disable hooks",
                       "skip the gate", "bypass the contract",
                       "turn off the screen"):
            self.assertNotIn(phrase, install.lower())


class TestQuickstarts(unittest.TestCase):
    HOSTS = ("Quickstart: Claude Code",
             "Quickstart: Cowork / Claude Desktop",
             "Quickstart: Codex")

    def test_quickstart_present_per_host_ending_in_setup_prompt(self):
        for h in self.HOSTS:
            body = _section(README, h)
            self.assertIn("set up taskplane", body,
                          f"{h} must end with the 'set up taskplane' prompt")

    def test_codex_quickstart_no_workflow_only_features(self):
        body = _section(README, "Quickstart: Codex")
        for term in ("workflow", "review-wave", "show_widget",
                     "Dynamic Workflow"):
            self.assertNotIn(term.lower(), body.lower(),
                             f"Codex quickstart references workflow-only "
                             f"feature {term!r}")

    def test_quickstart_tp_commands_exist_in_cli_parser(self):
        src = _read("taskplane", "tp.py")
        real = set(re.findall(r"add_parser\(\s*\"([a-z-]+)\"", src))
        self.assertIn("onboard", real)  # parser-scan sanity
        for h in self.HOSTS:
            body = _section(README, h)
            for sub_ in re.findall(r"\btp(?:\.py)?\s+([a-z][a-z-]*)\b", body):
                self.assertIn(
                    sub_, real,
                    f"{h} references `tp {sub_}` which is not a real "
                    "tp.py subcommand")


class _TmpRepo(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = self._tmp.name
        subprocess.run(["git", "init", "-q"], cwd=self.ws, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-q", "--allow-empty", "-m", "x"],
                       cwd=self.ws, check=True)
        with open(os.path.join(self.ws, "app.py"), "w", encoding="utf-8") as f:
            f.write("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=self.ws, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-q", "-m", "files"],
                       cwd=self.ws, check=True)

    def tearDown(self):
        self._tmp.cleanup()


class TestOnboardInstallTruth(_TmpRepo):
    def test_install_context_org_managed_via_host_marker(self):
        with tempfile.NamedTemporaryFile(suffix=".json") as f:
            old = cli._MANAGED_SETTINGS_PATHS
            cli._MANAGED_SETTINGS_PATHS = (f.name,)
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
                    plugin_path="/home/u/.claude/plugins/marketplaces/x/tp.py"),
                "personal")
        finally:
            cli._MANAGED_SETTINGS_PATHS = old

    def test_install_context_undetectable_defaults_to_triage(self):
        old = cli._MANAGED_SETTINGS_PATHS
        cli._MANAGED_SETTINGS_PATHS = ()
        try:
            self.assertEqual(
                cli._install_context(plugin_path="/opt/somewhere/tp.py"),
                "unknown")
        finally:
            cli._MANAGED_SETTINGS_PATHS = old

    def test_org_managed_lines_never_print_member_inaccessible_steps(self):
        text = "\n".join(cli._install_paths_lines("org-managed"))
        low = text.lower()
        for cmd in MEMBER_DEAD_ENDS:
            self.assertNotIn(cmd.lower(), low,
                             f"org-managed onboarding prints a step a "
                             f"member cannot run: {cmd!r}")
        self.assertIn("catalog", low)
        self.assertIn("admin", low)

    def test_unknown_context_prints_by_account_type_triage(self):
        lines = cli._install_paths_lines("unknown")
        text = "\n".join(lines).lower()
        for word in ("member", "admin", "personal"):
            self.assertIn(word, text,
                          f"triage must mention the {word} path")
        member_lines = [l for l in lines if "member" in l.lower()]
        self.assertTrue(member_lines)
        for l in member_lines:
            self.assertIn("cannot", l.lower(),
                          "the member line must state the restriction")
            for cmd in MEMBER_DEAD_ENDS:
                self.assertNotIn(cmd.lower(), l.lower())

    def test_codex_host_gets_codex_install_path_not_claude_paths(self):
        # codex-compat review fix: on a Codex host the Claude org-admin /
        # marketplace universe is wrong — guidance must name the Codex
        # plugin tooling and explicitly disclaim the Claude paths.
        import unittest.mock as mock
        with mock.patch.dict(os.environ, {"CODEX_HOME": "/tmp/x"}):
            text = "\n".join(cli._install_paths_lines("unknown")).lower()
        self.assertIn("codex", text)
        self.assertNotIn("organization settings", text)
        for cmd in MEMBER_DEAD_ENDS:
            self.assertNotIn(cmd.lower(), text)

    def test_non_codex_host_unaffected_by_codex_branch(self):
        import unittest.mock as mock
        env = {k: v for k, v in os.environ.items()
               if k not in ("CODEX_HOME", "CODEX_THREAD_ID")}
        with mock.patch.dict(os.environ, env, clear=True):
            lines = cli._install_paths_lines("unknown")
        self.assertIn("member", "\n".join(lines).lower())

    def test_personal_context_lines_mention_update_path(self):
        text = "\n".join(cli._install_paths_lines("personal")).lower()
        self.assertIn("personal", text)

    def test_onboard_report_carries_install_paths(self):
        r = cli._onboard_report(self.ws)
        self.assertIn("install", r)
        self.assertIn(r["install"]["context"],
                      ("org-managed", "personal", "unknown"))
        self.assertTrue(r["install"]["paths"])

    def test_cmd_onboard_prints_account_type_paths(self):
        old = cli._MANAGED_SETTINGS_PATHS
        cli._MANAGED_SETTINGS_PATHS = ()  # force the triage context
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = cli.cmd_onboard(
                    Namespace(workspace=self.ws, json=False, out=None))
        finally:
            cli._MANAGED_SETTINGS_PATHS = old
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        low = out.lower()
        self.assertIn("install", low)
        for word in ("member", "admin", "personal"):
            self.assertIn(word, low,
                          "onboard output must mention the account-type "
                          f"install paths (missing: {word})")

    def test_cmd_onboard_json_report_includes_install(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.cmd_onboard(
                Namespace(workspace=self.ws, json=True, out=None))
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertIn("install", data)


class TestManifestCopyAlignment(unittest.TestCase):
    def setUp(self):
        self.mp = json.loads(_read(".claude-plugin", "marketplace.json"))
        self.pj = json.loads(_read(".claude-plugin", "plugin.json"))
        self.cj = json.loads(_read(".codex-plugin", "plugin.json"))

    def test_names_consistent(self):
        self.assertEqual(self.pj["name"], "taskplane")
        self.assertEqual(self.cj["name"], "taskplane")
        self.assertEqual(self.mp["plugins"][0]["name"], "taskplane")
        self.assertEqual(self.mp["name"], "taskplane-marketplace")
        # the README install commands use exactly these names
        self.assertIn("taskplane@taskplane-marketplace", README)

    def test_versions_agree_across_all_manifests(self):
        versions = {self.pj["version"], self.cj["version"],
                    self.mp["version"], self.mp["plugins"][0]["version"]}
        self.assertEqual(len(versions), 1, f"version drift: {versions}")

    def test_descriptions_aligned(self):
        # claude plugin + marketplace entry carry the SAME description
        self.assertEqual(self.pj["description"],
                         self.mp["plugins"][0]["description"])
        # all three share the one product identity phrase
        for d in (self.pj["description"], self.cj["description"],
                  self.mp["description"]):
            self.assertIn("control plane", d)


class TestReadmeLinkHygiene(unittest.TestCase):
    """Static link truth — format + existence, NO network (non-flaky)."""

    LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")

    @staticmethod
    def _anchors(md_text):
        out = set()
        for m in re.finditer(r"^#{1,6}\s+(.*)$", md_text, re.M):
            slug = m.group(1).strip().lower()
            slug = re.sub(r"[`*_~]", "", slug)
            slug = re.sub(r"[^\w\s-]", "", slug)
            slug = re.sub(r"\s+", "-", slug.strip())
            out.add(slug)
        return out

    def test_every_readme_link_is_statically_valid(self):
        bad = []
        for target in self.LINK.findall(README):
            if target.startswith(("http://", "https://")):
                if not re.match(r"https?://[\w.-]+(?::\d+)?(?:/\S*)?$",
                                target):
                    bad.append((target, "malformed URL"))
            elif target.startswith("#"):
                if target[1:] not in self._anchors(README):
                    bad.append((target, "internal anchor not found"))
            else:
                path = target.split("#", 1)[0]
                frag = target.split("#", 1)[1] if "#" in target else None
                full = os.path.join(ROOT, path)
                if not os.path.exists(full):
                    bad.append((target, "relative target missing"))
                elif frag and path.endswith(".md"):
                    with open(full, encoding="utf-8") as f:
                        if frag not in self._anchors(f.read()):
                            bad.append((target, "anchor missing in target"))
        self.assertEqual(bad, [], f"README link problems: {bad}")


class TestWhatsNewTable(unittest.TestCase):
    """R-0005 row 5 — the README what's-new table stays at EXACTLY 3 data
    rows (newest release on top); CHANGELOG.md remains the stated
    authoritative full history."""

    VERSION = re.compile(r"v(\d+)\.(\d+)\.(\d+)")

    def _rows(self):
        body = _section(README, "What's new")
        rows = [ln for ln in body.splitlines()
                if ln.startswith("|") and not ln.startswith("| ---")]
        self.assertTrue(rows, "what's-new table missing")
        header, data = rows[0], rows[1:]
        self.assertIn("Version", header)
        return data

    def test_whats_new_exactly_three_rows(self):
        data = self._rows()
        self.assertEqual(
            len(data), 3,
            "the README what's-new table is pinned at EXACTLY 3 data rows "
            f"(CHANGELOG.md holds the full history); found {len(data)}")

    def test_rows_newest_first_each_named_with_a_version(self):
        parsed = []
        for row in self._rows():
            m = self.VERSION.search(row)
            self.assertIsNotNone(m, f"what's-new row without a version: "
                                    f"{row[:60]}...")
            parsed.append(tuple(int(g) for g in m.groups()))
        self.assertEqual(parsed, sorted(parsed, reverse=True),
                         "what's-new rows must be newest-first "
                         f"(got {parsed})")

    def test_changelog_stays_authoritative(self):
        body = _section(README, "What's new")
        self.assertIn("CHANGELOG.md", body)
        self.assertIn("authoritative", body)


class TestFeatureDocsDrift(unittest.TestCase):
    """R-0005 row 3 — every user-facing flag/command/artifact added in
    v2.4.0+Phase 2 appears in the docs tree, and the feature doc cannot
    drift from the code: env vars it names must be read by the code, CLI
    flags it names must exist in tp.py's argparse surface, `tp <sub>`
    commands must be real subparsers, and workflow files it names must
    exist. Programmatic greps — no network, no flakes."""

    DOC = ("docs", "routing-and-flows.md")

    @classmethod
    def setUpClass(cls):
        cls.doc = _read(*cls.DOC)
        cls.tp_src = _read("taskplane", "tp.py")
        code = ""
        tp_dir = os.path.join(ROOT, "taskplane")
        for name in sorted(os.listdir(tp_dir)):
            if name.endswith(".py"):
                code += _read("taskplane", name)
        code += _read("hooks", "hooks.json")
        cls.code = code

    def test_readme_points_at_the_feature_doc(self):
        self.assertIn("docs/routing-and-flows.md", README,
                      "README must point readers at the feature doc")
        self.assertIn("docs/configuration.md", README)

    def test_every_env_var_named_in_doc_is_read_by_code(self):
        named = set(re.findall(r"TASKPLANE_[A-Z_]*[A-Z]", self.doc))
        self.assertTrue(named, "the feature doc must name its env vars")
        in_code = set(re.findall(r"TASKPLANE_[A-Z_]*[A-Z]", self.code))
        ghosts = sorted(named - in_code)
        self.assertEqual(ghosts, [],
                         "docs/routing-and-flows.md documents env vars the "
                         f"code never reads: {ghosts}")

    def test_every_cli_flag_named_in_doc_exists_in_argparse(self):
        flags = set(re.findall(r"--[a-z][a-z-]*", self.doc))
        self.assertIn("--decompose", flags)   # scan sanity
        self.assertIn("--emit", flags)
        missing = sorted(f for f in flags
                         if f'"{f}"' not in self.tp_src)
        self.assertEqual(missing, [],
                         "docs/routing-and-flows.md documents CLI flags "
                         f"tp.py's argparse does not define: {missing}")

    def test_every_tp_command_named_in_doc_is_a_real_subparser(self):
        real = set(re.findall(r"add_parser\(\s*\"([a-z-]+)\"", self.tp_src))
        used = set(re.findall(r"\btp(?:\.py)?\s+([a-z][a-z-]*)\b", self.doc))
        self.assertTrue(used, "the feature doc must cite tp commands")
        ghosts = sorted(used - real)
        self.assertEqual(ghosts, [],
                         "docs/routing-and-flows.md cites tp subcommands "
                         f"that do not exist: {ghosts}")

    def test_every_workflow_file_named_in_doc_exists(self):
        named = set(re.findall(r"workflows/([a-z-]+\.js)", self.doc))
        self.assertGreaterEqual(len(named), 4,
                                "the doc must name all four workflow files")
        for f in sorted(named):
            self.assertTrue(
                os.path.exists(os.path.join(ROOT, "workflows", f)),
                f"docs/routing-and-flows.md names missing workflow "
                f"file workflows/{f}")

    def test_v3_surface_appears_in_docs_tree_with_dogfood_examples(self):
        # every user-facing flag/command/artifact from v2.4.0+Phase 2
        for needle in (
                "--decompose",            # R-0003 CLI surface
                "components.yaml",        # R-0003 floors override
                "component_attribution",  # R-0003 attribution artifact
                "fail-open",              # R-0003 widening ladder
                "--emit",                 # R-0002/R-0004 dispatch surface
                "review-wave", "execute-wave", "evaluate-wave", "fix-wave",
                "TASKPLANE_WORKFLOWS",    # kill-switch
                "`0`", "`false`", "`no`", "`off`",  # all four spellings
                "TASKPLANE_AUDIT_EVERY",  # audit cadence
                "router regression",      # audit auto-filing
                "n/a",                    # evidence-backed skips
                "cap", "floors",          # budget guardrails
                "fixture-path discount",  # D-0002
                'stage="build"',          # R-0006 evaluate routing
                "Dogfood example",
        ):
            self.assertIn(needle, self.doc,
                          f"feature doc missing v3 surface: {needle!r}")
        # one honest dogfood example per feature section (>= 5 features)
        self.assertGreaterEqual(self.doc.count("Dogfood example"), 5)

    def test_stage_wave_emitters_documented_exactly(self):
        for cmd in ("tp lens dispatch --emit",
                    "tp loop wave --emit",
                    "tp loop next --emit",
                    "tp graph scan --decompose"):
            self.assertIn(cmd, self.doc,
                          f"feature doc must cite the exact CLI surface: "
                          f"{cmd!r}")

    def test_feature_doc_never_instructs_weakening(self):
        low = self.doc.lower()
        for phrase in ("disable the hook", "disable hooks",
                       "skip the gate", "bypass the contract",
                       "turn off the screen", "weaken"):
            self.assertNotIn(phrase, low,
                             f"feature doc instructs weakening: {phrase!r}")
        # the kill-switch is documented as forcing the TASK path, never as
        # removing a gate
        self.assertIn("no gate is reachable only via workflows",
                      " ".join(self.doc.split()))

    def test_generated_lens_catalog_not_hand_edited_here(self):
        # this suite's docs work never touches the generated catalog; the
        # freshness gate itself lives in test_v230_docs.py + CI
        self.assertIn("generated", _read("docs", "lens-catalog.md").lower())


if __name__ == "__main__":
    unittest.main()
