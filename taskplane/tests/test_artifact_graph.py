"""Artifacts as first-class nodes, and the edges between them (D-0016, D-0015).

Two defects that only look like one:

  D-0016  CODE_EXT decided what EXISTED, not just what got parsed for
          imports. A repository whose product is not source code was
          invisible: this plugin's own skills, agents and lenses are markdown
          and declarative JSON, and the graph contained none of them — nor did
          decomposition, which is what a review walks to know where to look.
          The accuracy harness scored the plugin profile at 0% module recall
          for a repo where nothing was missing but the file extensions.

  D-0015  The graph modelled IMPORTS and nothing else. On a codebase whose
          components talk by NAMING each other — a skill dispatching an agent,
          an agent applying a lens, a script reading a routing catalog — it
          reported 4 internal edges for this repo. "What depends on this?" had
          no answer for over half the tree.

The load-bearing claim in D-0015 is that this is RESOLUTION, not pattern
matching. A path-shaped token becomes an edge only when it resolves to a file
that is really there; prose about a file that does not exist produces nothing.
`test_a_reference_to_a_file_that_does_not_exist_is_not_an_edge` is the one to
read first, and the one to keep if you keep only one.

The KIND (`calls` vs `uses`) is the single place a naming convention decides
anything, and it is deliberately confined to a label: both kinds live in
DEPENDENCY_EDGE_KINDS, so grading one wrong changes how a relationship reads
and never what a blast radius contains.

Every assertion here was observed FAILING before it was kept.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import depgraph  # noqa: E402


# A plugin whose product is markdown and declarative JSON, plus one code file
# that reads a data artifact and one that only TALKS about a missing one.
FILES = {
    "skills/review/SKILL.md":
        "---\nname: review\n---\n"
        "Dispatch `agents/reviewer.md`. Checks from `lenses/security.md`.\n"
        "See also `lenses/nonexistent.md` and `agents/ghost.md`.\n",
    "agents/reviewer.md": "---\nname: reviewer\n---\n"
                          "Apply the lens at `lenses/security.md`.\n",
    "lenses/security.md": "# security\nLook for authz gaps.\n",
    # names its own neighbour, so the same-module drop is not vacuous
    "lenses/_catalog.json":
        '{"lenses":[{"id":"security","doc":"lenses/security.md"}]}',
    ".claude-plugin/plugin.json": '{"name":"demo","version":"1.0.0"}',
    "engine/router.py": 'CATALOG = "lenses/_catalog.json"\n'
                        'OTHER = "engine/helper.py"\n',
    "engine/helper.py": "x = 1\n",
    "docs/design.md": "The reviewer lives in `agents/reviewer.md`.\n",
    "README.md": "Root-level docs.\n",
    "db/migrations/001.sql": "ALTER TABLE items ADD COLUMN price int;\n",
    "infra/main.tf": 'resource "aws_db_instance" "x" {}\n',
    ".github/workflows/ci.yml": "on: [push]\njobs:\n  t:\n    runs-on: x\n",
}


def _build(root, files=FILES):
    for rel, body in files.items():
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
    for args in (["init", "-q"], ["config", "user.email", "t@t"],
                 ["config", "user.name", "t"], ["add", "-A"],
                 ["commit", "-qm", "base"]):
        subprocess.run(["git", *args], cwd=root, capture_output=True)
    return root


class _Scanned(unittest.TestCase):
    FILES = FILES

    def setUp(self):
        self._old = os.environ.get("TASKPLANE_HOME")
        self.home = tempfile.mkdtemp(prefix="tp-art-home-")
        os.environ["TASKPLANE_HOME"] = self.home
        self.ws = _build(tempfile.mkdtemp(prefix="tp-art-ws-"), self.FILES)
        self.g = depgraph.scan(self.ws)
        self.mods = set(self.g.get("modules") or {})
        self.edges = {(e["from"], e["to"], e["kind"])
                      for e in (self.g.get("edges") or [])}
        self.pairs = {(a, b) for a, b, _k in self.edges}

    def tearDown(self):
        if self._old is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self._old
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.ws, ignore_errors=True)


class TestTheProductIsInTheGraph(_Scanned):
    """D-0016. None of these existed as nodes before: every one of them is a
    directory a reviewer must be able to ask about."""

    def test_markdown_and_declarative_artifacts_are_modules(self):
        for m in ("skills/review", "agents", "lenses", ".claude-plugin",
                  "db/migrations", "infra", ".github/workflows"):
            with self.subTest(module=m):
                self.assertIn(m, self.mods)

    def test_their_kind_is_module_not_something_second_class(self):
        """A separate kind would have to be taught to every consumer of
        _node_kind, and every one of them would have to decide whether it
        counts. These ARE modules."""
        for m in ("agents", "lenses", "infra"):
            self.assertEqual(self.g["modules"][m]["kind"], "module")

    def test_they_carry_file_counts_so_the_node_is_not_hollow(self):
        self.assertEqual(self.g["modules"]["lenses"]["files"], 2)
        self.assertEqual(self.g["modules"]["agents"]["files"], 1)

    def test_decomposition_can_see_inside_them(self):
        """`graph["files"]` is what decompose walks. A node with no files
        under it is a node the review still cannot look inside — which was
        the actual complaint behind D-0016."""
        self.assertIn("lenses/security.md", self.g["files"])
        self.assertEqual(self.g["files"]["lenses/security.md"]["imports"], [])
        self.assertTrue(self.g["files"]["lenses/security.md"]["artifact"])

    def test_a_root_level_artifact_does_not_mint_the_catch_all_module(self):
        """`README.md` has no directory to be named after; admitting it puts
        it in `(root)`, an id that describes nothing and would collect every
        unrelated top-level file in the repo."""
        self.assertNotIn("(root)", self.mods)
        self.assertNotIn("README.md", self.g["files"])

    def test_build_descriptors_are_deliberately_not_artifacts(self):
        """A pom.xml/pyproject.toml says how the code is ASSEMBLED; it is not
        a thing the code depends on. Admitting them minted a `main/resources`
        module out of a Java DI file in the accuracy corpus."""
        self.assertFalse(depgraph._is_artifact("a/pom.xml"))
        self.assertFalse(depgraph._is_artifact("a/pyproject.toml"))
        self.assertFalse(depgraph._is_artifact("a/Cargo.lock"))
        self.assertTrue(depgraph._is_artifact("a/x.md"))

    def test_the_payload_discloses_that_non_code_is_included(self):
        """A reviewer reading "2 files" needs to know whether that means two
        source files or two skills."""
        meta = ((self.g.get("meta") or {}).get("scanners") or {})
        self.assertEqual(meta["artifacts"]["extensions"],
                         list(depgraph.ARTIFACT_EXT))
        self.assertGreater(meta["artifacts"]["files"], 0)


class TestReferencesAreEdges(_Scanned):
    """D-0015. The relationships that were absent."""

    def test_an_artifact_naming_another_artifact_is_an_edge(self):
        self.assertIn(("skills/review", "lenses", "uses"), self.edges)
        self.assertIn(("agents", "lenses", "uses"), self.edges)

    def test_dispatch_is_a_call_not_a_read(self):
        self.assertIn(("skills/review", "agents", "calls"), self.edges)

    def test_a_document_merely_mentioning_an_agent_does_not_call_it(self):
        """The source is tested as well as the target. Without that the
        graph claimed `docs -calls-> agents`, which is not a thing that
        happens."""
        self.assertIn(("docs", "agents", "uses"), self.edges)
        self.assertNotIn(("docs", "agents", "calls"), self.edges)

    def test_code_reading_a_data_artifact_is_an_edge(self):
        """The data-read half: a router that opens a routing catalog DEPENDS
        on it, and no import statement says so."""
        self.assertIn(("engine", "lenses", "uses"), self.edges)

    def test_a_reference_to_a_file_that_does_not_exist_is_not_an_edge(self):
        """THE claim that separates resolution from pattern-matching. The
        skill names `lenses/nonexistent.md` and `agents/ghost.md` in exactly
        the same prose shape as the two real references above."""
        refs = self.g["files"]["skills/review/SKILL.md"]["refs"]
        self.assertIn("lenses/security.md", refs)
        self.assertNotIn("lenses/nonexistent.md", refs)
        self.assertNotIn("agents/ghost.md", refs)

    def test_a_code_files_reference_to_code_is_left_to_the_import_scanner(self):
        """`OTHER = "engine/helper.py"` is a string literal, not a
        dependency the graph should invent — the import scanners resolve
        code-to-code properly and a path in a literal is usually a fixture
        or a message."""
        self.assertEqual(self.g["files"]["engine/router.py"]["refs"],
                         ["lenses/_catalog.json"])

    def test_a_reference_inside_the_same_module_is_not_an_edge(self):
        """`lenses/security.md` and `lenses/_catalog.json` are one module;
        a file naming its own neighbour is not a relationship between
        components. The import scanners already drop these."""
        self.assertIn("lenses/security.md",
                      self.g["files"]["lenses/_catalog.json"]["refs"],
                      "fixture must actually contain a same-module "
                      "reference, or this assertion proves nothing")
        self.assertNotIn(("lenses", "lenses"), self.pairs)

    def test_both_kinds_answer_what_depends_on_this(self):
        """The kind is a LABEL. Grading one wrong must not be able to change
        a blast radius, which is only true while both are in the dependency
        family."""
        for kind in ("uses", "calls"):
            self.assertTrue(depgraph.is_dependency_edge({"kind": kind}))

    def test_the_blast_radius_now_reaches_the_artifacts(self):
        imp = depgraph.impact(self.ws, ["lenses/security.md"])
        self.assertEqual(imp["touched"], ["lenses"])
        reached = {row["module"]
                   for rows in (imp["impacted"] or {}).values()
                   for row in rows}
        self.assertIn("skills/review", reached)
        self.assertIn("agents", reached)


class TestReferenceResolverEdges(unittest.TestCase):
    """`_file_refs` is pure, so its edges are testable without a tree."""

    INDEX = {"lenses/security.md", "agents/reviewer.md", "engine/x.py",
             "README.md", "docs/sub/note.md"}

    def test_a_relative_reference_resolves_against_the_referring_file(self):
        self.assertEqual(
            depgraph._file_refs("see `../agents/reviewer.md`",
                                "docs/sub/note.md", self.INDEX, False),
            {"agents/reviewer.md"})

    def test_a_host_shaped_reference_still_resolves(self):
        self.assertEqual(
            depgraph._file_refs(r"see `agents\reviewer.md`", "skills/a.md",
                                self.INDEX, False),
            {"agents/reviewer.md"})

    def test_a_file_never_references_itself(self):
        self.assertEqual(
            depgraph._file_refs("this is `docs/sub/note.md`",
                                "docs/sub/note.md", self.INDEX, False),
            set())

    def test_a_root_target_is_skipped(self):
        self.assertEqual(
            depgraph._file_refs("see `README.md`", "docs/sub/note.md",
                                self.INDEX, False),
            set())

    def test_artifact_only_filters_code_targets(self):
        self.assertEqual(
            depgraph._file_refs("see `engine/x.py` and `lenses/security.md`",
                                "docs/sub/note.md", self.INDEX, True),
            {"lenses/security.md"})
        self.assertEqual(
            depgraph._file_refs("see `engine/x.py` and `lenses/security.md`",
                                "docs/sub/note.md", self.INDEX, False),
            {"engine/x.py", "lenses/security.md"})

    def test_prose_that_is_not_a_path_resolves_to_nothing(self):
        self.assertEqual(
            depgraph._file_refs(
                "Version 1.0.0 uses os.path.join and self.assertEqual, "
                "see http://example.com/a.md for lenses/missing.md",
                "docs/sub/note.md", self.INDEX, False),
            set())

    def test_the_kind_grader(self):
        self.assertEqual(
            depgraph._ref_kind("skills/a/SKILL.md", "agents/r.md"), "calls")
        self.assertEqual(
            depgraph._ref_kind("docs/a.md", "agents/r.md"), "uses")
        self.assertEqual(
            depgraph._ref_kind("skills/a/SKILL.md", "lenses/s.md"), "uses")


class TestIncrementalScanKeepsReferences(_Scanned):
    """The mtime+size cache is what keeps a rescan diff-sized. A cached
    entry that predates `refs` must be RECOMPUTED, not treated as "this file
    references nothing" — otherwise upgrading loses every artifact edge
    until each file happens to change."""

    def test_a_rescan_is_stable(self):
        again = depgraph.scan(self.ws)
        self.assertEqual(
            {(e["from"], e["to"], e["kind"]) for e in again["edges"]},
            self.edges)

    def test_a_pre_upgrade_cache_entry_is_recomputed(self):
        g = depgraph.load(self.ws)
        for row in g["files"].values():
            row.pop("refs", None)
        depgraph.save(self.ws, g)
        after = depgraph.scan(self.ws)
        self.assertIn("lenses/security.md",
                      after["files"]["skills/review/SKILL.md"]["refs"])
        self.assertIn(("skills/review", "agents", "calls"),
                      {(e["from"], e["to"], e["kind"])
                       for e in after["edges"]})


class TestACodeOnlyRepoIsUndisturbed(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("TASKPLANE_HOME")
        self.home = tempfile.mkdtemp(prefix="tp-art2-home-")
        os.environ["TASKPLANE_HOME"] = self.home
        self.ws = _build(tempfile.mkdtemp(prefix="tp-art2-ws-"), {
            "src/auth/session.py": "import src.db.conn\n",
            "src/db/conn.py": "x = 1\n",
        })

    def tearDown(self):
        if self._old is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self._old
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_no_new_nodes_edges_or_disclosure(self):
        g = depgraph.scan(self.ws)
        self.assertEqual(set(g["modules"]), {"auth", "db"})
        self.assertEqual({(e["from"], e["to"], e["kind"]) for e in g["edges"]},
                         {("auth", "db", "imports")})
        self.assertNotIn("artifacts",
                         (g.get("meta") or {}).get("scanners") or {})


class TestMavenDependencies(unittest.TestCase):
    """A pom's `<dependency>` list, attributed the way `.csproj` and Gemfile
    dependencies already are: to the module the manifest lives in. One rule,
    three manifest formats.

    The ROOT pom is skipped, for the same reason D-0007 skips a root
    package.json — it describes the BUILD, not a module in it, and cannot say
    which package uses what. The accuracy corpus asked for exactly that
    attribution (`com/acme/order -> ext:spring-core` from a root pom) and the
    honest answer is that no scanner can produce it without guessing; the
    corpus entry now says so.
    """

    def setUp(self):
        self._old = os.environ.get("TASKPLANE_HOME")
        self.home = tempfile.mkdtemp(prefix="tp-pom-home-")
        os.environ["TASKPLANE_HOME"] = self.home
        self.ws = _build(tempfile.mkdtemp(prefix="tp-pom-ws-"), {
            "pom.xml": "<project><artifactId>root</artifactId><dependencies>"
                       "<dependency><groupId>g</groupId>"
                       "<artifactId>root-only-dep</artifactId>"
                       "</dependency></dependencies></project>",
            "modules/order/pom.xml":
                "<project><artifactId>order</artifactId><dependencies>"
                "<dependency><groupId>org.springframework</groupId>"
                "<artifactId>spring-core</artifactId></dependency>"
                "<dependency><groupId>junit</groupId>"
                "<artifactId>junit</artifactId><scope>test</scope>"
                "</dependency></dependencies></project>",
            "modules/order/src/main/java/com/acme/order/O.java":
                "package com.acme.order;\npublic class O {}\n",
        })
        self.g = depgraph.scan(self.ws)
        self.edges = {(e["from"], e["to"], e["kind"])
                      for e in (self.g.get("edges") or [])}

    def tearDown(self):
        if self._old is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self._old
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_a_module_pom_declares_that_modules_dependencies(self):
        self.assertIn(("modules/order", "ext:spring-core", "imports"),
                      self.edges)

    def test_a_test_scoped_dependency_is_not_a_product_dependency(self):
        """`what depends on this?` is a question about the shipped thing."""
        self.assertNotIn("ext:junit", set(self.g["modules"]))

    def test_a_root_pom_is_not_attributed_to_anything(self):
        """It would have to PICK a package, which is invention."""
        self.assertNotIn("ext:root-only-dep", set(self.g["modules"]))
        self.assertNotIn("(root)", set(self.g["modules"]))


if __name__ == "__main__":
    unittest.main()
