"""Repo-declared graph exclusions (D-0017).

`SKIP_DIRS` is a fixed set covering the universal cases. It cannot cover the
repo-specific ones, and every real codebase has them: generated protobuf or
OpenAPI clients, sample apps, docs sites, test corpora. Left unexcluded they
become graph MODULES and route review lenses at code nobody wrote — this repo
minted `api`, `auth`, `components` and `src` out of its own test fixtures that
way, and adding a four-repo accuracy corpus took it from 16 modules to 28.

What is pinned here is the handful of properties whose failure would be
invisible:

  1. an exclusion actually removes the module (the point)
  2. it matches on SEGMENT boundaries — `corpus` must not eat `corpus-notes.md`
  3. a malformed file fails OPEN and is REPORTED, never silently narrowing
     the graph (a narrowed graph is a narrowed blast radius: fails toward
     LESS review)
  4. the narrowing is DISCLOSED in the payload, so an impact consumer can see
     the answer was scoped rather than trust a small one
  5. one parser, not two — the floors reader and the exclude reader are the
     same code, so the file format cannot fork

Every assertion here was observed FAILING before it was kept.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import depgraph  # noqa: E402
import path_roles  # noqa: E402


def _repo(root, git=False):
    """A tiny workspace with a real module and a 'generated' tree.

    `git=True` matters: `_scan_locked` has TWO enumeration branches — one
    driven by `git ls-files` and one by `os.walk` — and a real taskplane
    workspace is always a git repo, so the GIT branch is the production
    path. Mutation testing caught that this file originally exercised only
    the walk fallback: deleting the exclusion filter from the git branch
    left every test passing.
    """
    for rel, body in (
            ("src/app.py", "import src.util\n"),
            ("src/util.py", "x = 1\n"),
            ("generated/client/api.py", "import requests\n"),
            ("corpus-notes.md", "not a corpus\n")):
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
    if git:
        import subprocess
        for args in (["init", "-q"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "t"], ["add", "-A"],
                     ["commit", "-qm", "base"]):
            subprocess.run(["git", *args], cwd=root, capture_output=True)
    return root


class _Scanned(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("TASKPLANE_HOME")
        self.home = tempfile.mkdtemp(prefix="tp-excl-home-")
        os.environ["TASKPLANE_HOME"] = self.home
        self.ws = _repo(tempfile.mkdtemp(prefix="tp-excl-ws-"))

    def tearDown(self):
        if self._old is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self._old
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.ws, ignore_errors=True)

    def _yaml(self, text):
        with open(os.path.join(self.ws, "components.yaml"), "w",
                  encoding="utf-8") as f:
            f.write(text)

    def _scan(self):
        depgraph.scan(self.ws)
        return depgraph.load(self.ws) or {}


class TestExclusionRemovesTheModule(_Scanned):
    def test_without_a_declaration_the_generated_tree_is_a_module(self):
        mods = set(self._scan().get("modules") or {})
        self.assertIn("generated/client", mods,
                      "baseline: the tree IS a module until excluded")

    def test_declaring_it_removes_it(self):
        self._yaml("exclude:\n  - generated\n")
        mods = set(self._scan().get("modules") or {})
        self.assertNotIn("generated/client", mods)
        self.assertIn("src", mods, "excluding one tree must not drop the rest")


class TestBothEnumerationBranches(unittest.TestCase):
    """_scan_locked enumerates either via `git ls-files` or via `os.walk`,
    and a real workspace always takes the GIT branch. The filter must be on
    both — proven by running the same assertion against each."""

    def setUp(self):
        self._old = os.environ.get("TASKPLANE_HOME")
        self.home = tempfile.mkdtemp(prefix="tp-excl2-home-")
        os.environ["TASKPLANE_HOME"] = self.home
        self.made = []

    def tearDown(self):
        if self._old is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self._old
        shutil.rmtree(self.home, ignore_errors=True)
        for d in self.made:
            shutil.rmtree(d, ignore_errors=True)

    def _ws(self, git):
        ws = _repo(tempfile.mkdtemp(prefix="tp-excl2-ws-"), git=git)
        self.made.append(ws)
        with open(os.path.join(ws, "components.yaml"), "w",
                  encoding="utf-8") as f:
            f.write("exclude:\n  - generated\n")
        return ws

    def test_git_enumeration_honors_the_exclusion(self):
        ws = self._ws(git=True)
        self.assertIsNotNone(depgraph._git_candidates(ws),
                             "fixture must actually be a git work tree, or "
                             "this test silently re-covers the walk branch")
        depgraph.scan(ws)
        mods = set((depgraph.load(ws) or {}).get("modules") or {})
        self.assertNotIn("generated/client", mods)
        self.assertIn("src", mods)

    def test_walk_enumeration_honors_the_exclusion(self):
        ws = self._ws(git=False)
        self.assertIsNone(depgraph._git_candidates(ws))
        depgraph.scan(ws)
        mods = set((depgraph.load(ws) or {}).get("modules") or {})
        self.assertNotIn("generated/client", mods)
        self.assertIn("src", mods)


class TestSegmentBoundaries(_Scanned):
    """`corpus` must not also exclude `corpus-notes.md`. This is the same
    prefix-vs-segment distinction that made `.taskplane` wrongly match
    `.taskplane-kb/` elsewhere in this codebase."""

    def test_a_prefix_does_not_eat_a_longer_sibling(self):
        self.assertTrue(path_roles.is_excluded("corpus/x/y.py", ["corpus"]))
        self.assertFalse(path_roles.is_excluded("corpus-notes.md", ["corpus"]))

    def test_the_exact_path_itself_is_excluded(self):
        self.assertTrue(path_roles.is_excluded("a/b.py", ["a/b.py"]))

    def test_host_shaped_input_still_matches(self):
        self.assertTrue(path_roles.is_excluded(r"generated\client\api.py",
                                               ["generated"]))


class TestMalformedFailsOpenAndIsReported(_Scanned):
    """A narrowed graph is a narrowed blast radius. A file nobody can parse
    must therefore apply NO exclusions and say so — never apply some."""

    def test_unparseable_file_applies_nothing_and_reports(self):
        self._yaml("exclude:\n  - generated\nnonsense line\n")
        prefixes, err = depgraph.load_excludes(self.ws)
        self.assertEqual(prefixes, [])
        self.assertIsNotNone(err)
        self.assertIn("components.yaml ignored", err)
        self.assertIn("generated/client", set(self._scan().get("modules") or {}),
                      "a malformed file must not narrow the graph at all")

    def test_a_list_item_under_floors_is_still_a_hard_error(self):
        """Regression: when the parser was shared, accepting list items
        everywhere made a malformed floors file parse to {} silently,
        dropping the floors its author intended with no report."""
        with self.assertRaises(ValueError):
            path_roles.parse_components_yaml(
                "floors:\n  - candidate_min_files: 8\n")

    def test_missing_file_is_not_an_error(self):
        prefixes, err = depgraph.load_excludes(self.ws)
        self.assertEqual((prefixes, err), ([], None))


class TestNarrowingIsDisclosed(_Scanned):
    """The Go scanner already discloses partial coverage in the payload so
    impact consumers do not trust a near-empty blast radius. Scoping by
    declaration gets the same treatment."""

    def test_the_payload_names_what_was_excluded(self):
        self._yaml("exclude:\n  - generated\n")
        meta = (self._scan().get("meta") or {}).get("scanners") or {}
        self.assertIn("excluded", meta)
        self.assertEqual(meta["excluded"]["declared_in"], "components.yaml")
        self.assertEqual(meta["excluded"]["prefixes"], ["generated"])

    def test_a_parse_failure_is_disclosed_too(self):
        self._yaml("exclude:\n  - generated\nnonsense line\n")
        meta = (self._scan().get("meta") or {}).get("scanners") or {}
        self.assertIn("exclude_error", meta,
                      "'my exclusions did nothing' must be visible, not silent")
        self.assertNotIn("excluded", meta)


class TestOneParserNotTwo(unittest.TestCase):
    """The floors reader and the exclude reader must be the same code. Two
    readers of one file format is the defect shape this codebase already
    carries in RUNTIME_OWNED vs lens.LOOP_OWNED, which have drifted."""

    def test_decompose_delegates_to_path_roles(self):
        import decompose
        src = open(decompose.__file__, encoding="utf-8").read()
        self.assertIn("path_roles.parse_components_yaml", src)
        self.assertNotIn("unsupported components.yaml line", src,
                         "decompose must not carry its own copy of the "
                         "format's error text — that is the second parser")

    def test_both_sections_come_out_of_one_call(self):
        cfg = path_roles.parse_components_yaml(
            "floors:\n  cluster_min_files: 3\nexclude:\n  - vendor/gen\n")
        self.assertEqual(cfg["floors"], {"cluster_min_files": 3})
        self.assertEqual(cfg["exclude"], ["vendor/gen"])


if __name__ == "__main__":
    unittest.main()
