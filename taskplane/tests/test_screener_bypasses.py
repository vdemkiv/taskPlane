"""The four screener bypasses from the whole-codebase review (D-0001..D-0004).

Every command below was VERIFIED ALLOWED by execution before the fix, under
the contract shown. They are not hypothetical shapes: each one was run
through `screen_tool` and returned `(True, 'within contract')`.

The controls matter as much as the exploits. A deny screen is easy to make
strict and useless; these pin that the ordinary forms an agent needs — an
in-scope write, `tar cf`, `curl` without `-o`, `python3 -c "print(1)"` —
still pass. Two of the fixes were narrowed BECAUSE a control failed:

  * adding `(` and `)` to the command-separator regex shredded
    `python3 -c "open(1)"` into three fragments and broke eval-body
    screening. Grouping is stripped at the TOKEN level instead, where shlex
    already knows which parens are quoted.
  * the sacred-path message kept its `out_of_scope` token because the
    sign-off DoD matches on it.

Every assertion here was observed FAILING against the pre-fix code.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import taskplane_lite as tpl  # noqa: E402


RO = tpl.build_contract("review", read_only=True,
                        write_allow=[".em-review/**"], tools=["Bash"])
BUILD = tpl.build_contract("build", scope=["src/**"])
UNSCOPED = tpl.build_contract("build")


def _allowed(contract, command):
    ok, _reason = tpl.screen_tool(contract, "Bash", {"command": command}, None)
    return ok


class TestD0001ShellGroupingHidesTheProgram(unittest.TestCase):
    """`_CMD_SEP_RE` split only on ;&| and newline, so a grouped command
    tokenized to `(rm` — a program name in no write table and no deny head.
    One paren defeated every screen, including read-only."""

    EXPLOITS = [
        (RO, "(rm -rf src/main.py)"),
        (RO, "{ rm -rf src/main.py; }"),
        (RO, "( rm -rf src/main.py )"),
        (RO, "((rm -rf src/main.py))"),
        (RO, "if true; then rm -rf src/main.py; fi"),
        (RO, "while true; do rm -rf src/main.py; done"),
        (RO, "for f in a; do rm -rf src/main.py; done"),
        (RO, "time rm -rf src/main.py"),
        # grouping also defeated the DENY list, not just the write screen
        (RO, "(git push)"),
        (BUILD, "(git push)"),
        (BUILD, "{ git push; }"),
    ]

    def test_every_grouped_form_is_refused(self):
        for contract, cmd in self.EXPLOITS:
            with self.subTest(cmd=cmd):
                self.assertFalse(_allowed(contract, cmd),
                                 f"LOOSENED: {cmd!r} is allowed again")

    def test_the_program_is_recovered_from_the_group(self):
        self.assertEqual(tpl._strip_keywords(tpl._shsplit("(rm -rf x)")),
                         ["rm", "-rf", "x"])
        self.assertEqual(tpl._strip_keywords(["then", "rm", "x"]),
                         ["rm", "x"])

    def test_a_paren_inside_a_quoted_argument_is_not_grouping(self):
        """The regression that made this a token-level fix: splitting the
        raw string on parens shredded an ordinary interpreter one-liner."""
        self.assertEqual(tpl._strip_keywords(["python3", "-c", "open(1)"]),
                         ["python3", "-c", "open(1)"])
        opaque = tpl._analyze("eval 'python3 -c \"open(1)\"'")[1]
        self.assertIsNotNone(opaque, "eval-body screening must still work")
        self.assertEqual(opaque[0], "interpreter")


class TestD0002UnscopedContractCannotGovernItself(unittest.TestCase):
    """`tp new GOAL` with no --scope is a supported form, and with
    scope_paths empty the scope test was skipped entirely — so an agent
    could overwrite its OWN contract file and every later screen would then
    approve everything."""

    def test_the_control_plane_is_never_writable(self):
        for path in (".taskplane/active_contract.json",
                     ".taskplane/active/t1.json",
                     "nested/.taskplane/active_contract.json"):
            with self.subTest(path=path):
                ok, _ = tpl.screen_tool(UNSCOPED, "Write",
                                        {"file_path": path, "content": "x"},
                                        None)
                self.assertFalse(ok)

    def test_escapes_are_refused_without_a_declared_scope(self):
        for path in ("../outside.py", "/etc/passwd"):
            with self.subTest(path=path):
                ok, _ = tpl.screen_tool(UNSCOPED, "Write",
                                        {"file_path": path, "content": "x"},
                                        None)
                self.assertFalse(ok)

    def test_an_unscoped_contract_still_works_inside_the_workspace(self):
        """The complement — 'no scope' means the workspace, not nothing."""
        ok, _ = tpl.screen_tool(UNSCOPED, "Write",
                                {"file_path": "src/ok.py", "content": "x"},
                                None)
        self.assertTrue(ok)

    def test_the_sacred_family_is_not_defeatable_by_a_handwritten_list(self):
        """`sacred = any(... for g in _SACRED_OUT_OF_SCOPE if g in oos)` made
        the "cannot be overridden" guarantee hold only for contracts built
        from the DEFAULT list."""
        self.assertIsNotNone(tpl.scope_violation(
            "secrets/k.pem",
            {"scope_paths": ["secrets/k.pem"],
             "out_of_scope_paths": ["secrets/*"], "plan_minted": True}))

    def test_the_message_keeps_the_searchable_token(self):
        """The sign-off DoD matches on `out_of_scope`; a clearer message is
        not worth silently changing what callers grep for."""
        msg = tpl.scope_violation(".taskplane/active_contract.json",
                                  {"scope_paths": [], "out_of_scope_paths": []})
        self.assertIn("out_of_scope", msg)


class TestD0003WorkspaceRootIsAPath(unittest.TestCase):
    """`norm()` returned "" for the workspace root and `screen_command`'s
    `if p:` read that as "nothing to check" — so `rm -rf .` passed a scoped
    contract while `rm -rf ..` was correctly refused."""

    def test_deleting_the_workspace_root_is_refused(self):
        for cmd in ("rm -rf .", "rm -rf ./", "rm -rf .//"):
            with self.subTest(cmd=cmd):
                self.assertFalse(_allowed(BUILD, cmd))

    def test_the_neighbours_still_behave(self):
        self.assertFalse(_allowed(BUILD, "rm -rf .."))
        self.assertFalse(_allowed(BUILD, "rm -rf other"))
        self.assertTrue(_allowed(BUILD, "rm -rf src/x.py"))

    def test_root_has_a_distinct_id_and_empty_means_empty(self):
        self.assertEqual(tpl.norm("."), ".")
        self.assertEqual(tpl.norm(""), "")


class TestD0004ArgvParsingGaps(unittest.TestCase):
    def test_a_value_taking_shell_option_does_not_steal_the_c_body(self):
        """`bash -o errexit -c '…'` — the first non-dash arg is the option
        VALUE, so the real command was never analysed at all."""
        self.assertFalse(_allowed(RO, "bash -o errexit -c 'rm -rf src/main.py'"))
        self.assertFalse(_allowed(BUILD, "bash -o errexit -c 'git push'"))
        self.assertEqual(
            tpl._shell_c_body(["-o", "errexit", "-c", "rm -rf x"]),
            "rm -rf x")

    def test_clustered_c_still_resolves(self):
        self.assertFalse(_allowed(RO, "bash -lc 'echo x > src/main.py'"))
        self.assertEqual(tpl._shell_c_body(["-lc", "rm x"]), "rm x")

    def test_downloaders_are_write_programs(self):
        for cmd in ("curl -o src/main.py http://x/y",
                    "curl --output src/main.py http://x/y",
                    "curl -osrc/main.py http://x/y",
                    "wget -O src/main.py http://x/y",
                    "wget --output-document=src/main.py http://x/y"):
            with self.subTest(cmd=cmd):
                self.assertFalse(_allowed(RO, cmd))

    def test_a_downloader_without_an_output_flag_is_not_a_write(self):
        self.assertTrue(_allowed(BUILD, "curl https://example.com"))
        self.assertTrue(_allowed(BUILD, "curl -o src/new.py http://x/y"))

    def test_tar_dashless_mode_word_is_an_extract(self):
        for cmd in ("tar xf payload.tar", "tar xzf payload.tar",
                    "tar xvf payload.tar"):
            with self.subTest(cmd=cmd):
                self.assertFalse(_allowed(BUILD, cmd))
        self.assertTrue(tpl._tar_extracts(["xf", "a.tar"]))
        self.assertTrue(tpl._tar_extracts(["-xzf", "a.tar"]))

    def test_tar_create_and_list_are_not_extracts(self):
        self.assertFalse(tpl._tar_extracts(["cf", "a.tar", "src"]))
        self.assertFalse(tpl._tar_extracts(["tf", "a.tar"]))
        self.assertTrue(_allowed(BUILD, "tar cf out.tar src"))
        self.assertTrue(_allowed(BUILD, "tar tf out.tar"))

    def test_an_env_assignment_prefix_does_not_defeat_the_deny_list(self):
        self.assertFalse(_allowed(BUILD, "FOO=1 git push"))
        self.assertFalse(_allowed(BUILD, "A=1 B=2 git push"))
        self.assertFalse(_allowed(RO, "FOO=1 rm -rf src/main.py"))

    def test_runaway_nesting_fails_closed(self):
        """`_analyze` returned ([], None) at depth>6, which every caller
        reads as "no mutation found" — while its twin in `_deny_segments`
        already failed CLOSED at the same depth."""
        deep = "sh -c '" * 8 + "rm -rf src/main.py" + "'" * 8
        self.assertFalse(_allowed(RO, deep))
        _targets, opaque = tpl._analyze("rm x", _depth=7)
        self.assertIsNotNone(opaque)
        self.assertEqual(opaque[0], "destructive")


class TestNoLooseningFromTheseFixes(unittest.TestCase):
    """A deny screen is easy to make strict and useless. These are the
    ordinary forms an agent needs, and they must all still pass."""

    ALLOWED = [
        (BUILD, "echo x > src/main.py"),
        (BUILD, "sed -i s/a/b/ src/main.py"),
        (BUILD, "python3 -m pytest -q"),
        (BUILD, 'python3 -c "print(1)"'),
        (BUILD, "find src -name '*.py'"),
        (BUILD, "git status"),
        (BUILD, "git diff --stat"),
        (RO, "echo hello"),
        (RO, "ls -la"),
        (RO, "cat src/main.py"),
        (RO, "grep -rn pattern src"),
    ]

    def test_ordinary_commands_still_pass(self):
        for contract, cmd in self.ALLOWED:
            with self.subTest(cmd=cmd):
                self.assertTrue(_allowed(contract, cmd),
                                f"OVER-TIGHTENED: {cmd!r} is now refused")


if __name__ == "__main__":
    unittest.main()
