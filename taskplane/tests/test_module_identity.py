"""Declared module identity (D-0007).

The accuracy harness found the defect this file guards: on a workspace
monorepo the scanner produced the right SHAPE — four modules, two edges — and
named none of them the way the repo does. It said `ui`, `core`, `svc/gateway`,
`svc/billing`; every manifest, every import statement and every human says
`@acme/ui`, `@acme/core`, `acme/gateway`, `acme/billing`. Module recall and
precision were both 0% with nothing actually missing.

That is worse than it sounds. An id exists to be cross-referenced. `graph
impact` answering "touches ui" is not something a reviewer can carry back to
the codebase, a contract scope, or a lens route.

THE RULE, and the tests below pin its EDGES as hard as its centre: adopt a
declared name only when that name is what other code IMPORTS the module by.

  package.json `name`  IS the specifier  (`import "@acme/core"`)      ADOPT
  go.mod `module`      IS the import path (`import "acme/billing"`)   ADOPT
  pyproject `name`     is a DISTRIBUTION name; you import the package
                       directory, not the dist                            —
  pom.xml artifactId   is a build coordinate; Java imports by package      —

The three refusals matter more than the two adoptions. A rule that swallowed
every manifest would rename `services/pricing` to `pricing` and the repo root
to `shopfront`, and every one of those ids would match nothing.

The second half of the file is about AGREEMENT. A declared id is only worth
having if everything that turns a path into a module id resolves it the same
way — otherwise routing computes `packages/ui`, the graph contains `@acme/ui`,
and the blast radius comes back empty while looking perfectly healthy.

Every assertion here was observed FAILING before it was kept: by running it
against the pre-fix scanner, or by mutating the fix and confirming it fails.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import depgraph  # noqa: E402


# A workspace monorepo in miniature: two npm workspace members (one importing
# the other), two Go modules (one importing the other), and — deliberately —
# a Python service and a Maven root whose manifests must NOT be adopted.
FILES = {
    "package.json": '{"name":"shopfront","private":true,'
                    '"workspaces":["packages/*"]}',
    "packages/ui/package.json": '{"name":"@acme/ui","version":"1.0.0"}',
    "packages/ui/index.ts": 'import { fmt } from "@acme/core";\n'
                            'import React from "react";\n',
    "packages/ui/deep/widget.ts": 'export const w = 1;\n',
    "packages/core/package.json": '{"name":"@acme/core"}',
    "packages/core/index.ts": 'export const fmt = (x) => x;\n',
    "svc/billing/go.mod": "module acme/billing\n\ngo 1.22\n",
    "svc/billing/billing.go": "package billing\n\nfunc Run(){}\n",
    "svc/gateway/go.mod": "module acme/gateway\n\ngo 1.22\n",
    "svc/gateway/main.go": 'package main\n\nimport (\n\t"acme/billing"\n'
                           '\t"github.com/gin-gonic/gin"\n)\n',
    "services/pricing/pyproject.toml": '[project]\nname = "pricing"\n',
    "services/pricing/app.py": "x = 1\n",
    "pom.xml": "<project><artifactId>bigapp</artifactId></project>",
}


def _build(root, files=FILES):
    for rel, body in files.items():
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
    # A real taskplane workspace is a git work tree, so the git enumeration
    # branch of _scan_locked is the production path (test_graph_exclusions
    # learned this the hard way: a walk-only fixture let a missing filter
    # pass unnoticed on the branch that actually runs).
    for args in (["init", "-q"], ["config", "user.email", "t@t"],
                 ["config", "user.name", "t"], ["add", "-A"],
                 ["commit", "-qm", "base"]):
        subprocess.run(["git", *args], cwd=root, capture_output=True)
    return root


class _Scanned(unittest.TestCase):
    FILES = FILES

    def setUp(self):
        self._old = os.environ.get("TASKPLANE_HOME")
        self.home = tempfile.mkdtemp(prefix="tp-mid-home-")
        os.environ["TASKPLANE_HOME"] = self.home
        self.ws = _build(tempfile.mkdtemp(prefix="tp-mid-ws-"), self.FILES)
        self.g = depgraph.scan(self.ws)
        self.mods = set(self.g.get("modules") or {})
        self.edges = {(e["from"], e["to"], e["kind"])
                      for e in (self.g.get("edges") or [])}

    def tearDown(self):
        if self._old is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self._old
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.ws, ignore_errors=True)


class TestTheRepoNamesItsOwnModules(_Scanned):
    def test_npm_workspace_members_use_their_declared_names(self):
        self.assertIn("@acme/ui", self.mods)
        self.assertIn("@acme/core", self.mods)
        self.assertNotIn("ui", self.mods, "the invented id must be GONE, not "
                                          "carried alongside the real one")
        self.assertNotIn("core", self.mods)

    def test_go_modules_use_their_declared_import_paths(self):
        self.assertIn("acme/gateway", self.mods)
        self.assertIn("acme/billing", self.mods)
        self.assertNotIn("svc/gateway", self.mods)
        self.assertNotIn("svc/billing", self.mods)

    def test_the_declaration_is_the_module_BOUNDARY(self):
        """A nested directory inside a declared module belongs to it: the
        published unit is what a blast radius should be drawn around, and
        `packages/ui/deep/widget.ts` is shipped as part of `@acme/ui`."""
        self.assertEqual(
            depgraph.module_of("packages/ui/deep/very/widget.ts",
                               depgraph.declared_module_ids(self.g)),
            "@acme/ui")
        self.assertNotIn("deep", self.mods)


class TestTheRefusalsAreTheRule(_Scanned):
    """Three manifests here declare a name that is NOT how anything imports
    the code. Adopting any of them renames a real directory to an id that
    matches nothing — which is the exact failure being fixed, in reverse."""

    def test_the_root_manifest_describes_the_repo_not_a_module(self):
        self.assertNotIn("shopfront", self.mods)
        self.assertNotIn("bigapp", self.mods)

    def test_a_python_distribution_name_is_not_an_import_name(self):
        """`pip install pricing` then `import app` — the dist name is not
        the specifier, so services/pricing keeps its path-derived id."""
        self.assertIn("services/pricing", self.mods)
        self.assertNotIn("pricing", self.mods)

    def test_a_directory_with_no_manifest_is_untouched(self):
        self.assertEqual(depgraph.module_of("src/auth/session.py"), "auth")
        self.assertEqual(
            depgraph.module_of("src/auth/session.py",
                               depgraph.declared_module_ids(self.g)),
            "auth", "a declared map must not perturb paths outside it")


class TestImportsResolveToTheDeclaredModule(_Scanned):
    """Identity without resolution is half the fix: the edge has to land on
    the declared node, or the graph gains a correct id with nothing pointing
    at it."""

    def test_a_workspace_import_is_internal_not_a_scope_named_external(self):
        self.assertIn(("@acme/ui", "@acme/core", "imports"), self.edges)
        self.assertNotIn("ext:@acme", self.mods,
                         "`@acme/core` used to become an external node named "
                         "after the SCOPE — neither the package nor a real "
                         "third-party dependency")

    def test_a_declared_go_import_is_an_intra_repo_edge(self):
        self.assertIn(("acme/gateway", "acme/billing", "imports"), self.edges)

    def test_an_undeclared_import_is_still_external(self):
        """The complement. Nothing here may fabricate an internal edge for
        a path this repo does not declare."""
        self.assertIn("ext:gin", self.mods)
        self.assertIn("ext:react", self.mods)

    def test_the_go_disclosure_stops_claiming_external_only(self):
        """meta.scanners.go said `external-only` — true before, a lie the
        moment the scanner emits an internal Go edge. An impact consumer
        reads this to decide how much to trust a small blast radius."""
        go = ((self.g.get("meta") or {}).get("scanners") or {}).get("go") or {}
        self.assertEqual(go.get("coverage"), "declared-modules")
        self.assertIn("DECLARES", go.get("limitation", ""))


class TestTheMapIsPublished(_Scanned):
    """Everything that turns a changed FILE into a module id must resolve it
    the way the scan did. Publishing the map is what makes that possible;
    these pin that the consumers actually use it."""

    def test_the_payload_carries_the_map(self):
        ids = depgraph.declared_module_ids(self.g)
        self.assertEqual(ids.get("packages/ui"), "@acme/ui")
        self.assertEqual(ids.get("svc/gateway"), "acme/gateway")
        self.assertNotIn("", ids, "the repo root is never a declared module")

    def test_impact_of_a_file_path_lands_on_the_declared_module(self):
        imp = depgraph.impact(self.ws, ["packages/core/index.ts"])
        self.assertEqual(imp["touched"], ["@acme/core"])
        reached = {row["module"]
                   for rows in (imp["impacted"] or {}).values()
                   for row in rows}
        self.assertIn("@acme/ui", reached,
                      "the dependent must be reachable — a path resolved to "
                      "`core` would have found an empty blast radius while "
                      "looking perfectly healthy")

    def test_completion_agrees_with_the_graph(self):
        c = depgraph.completion(self.ws, ["packages/ui/index.ts"],
                                planned_modules=["@acme/ui"])
        self.assertEqual(c["realized_modules"], ["@acme/ui"])
        self.assertEqual(c["unrealized_modules"], [])

    def test_a_scope_glob_resolves_through_the_workspace_helper(self):
        self.assertEqual(depgraph.scope_modules(self.ws, ["packages/ui/**"]),
                         ["@acme/ui"])
        self.assertEqual(depgraph.modules_for_scope(["packages/ui/**"]),
                         ["ui"], "the raw helper is unchanged — the ws-taking "
                                 "one is what call sites must use")

    def test_lens_routing_sees_the_dependent(self):
        """The hub signal counts dependents of the modules a change touches.
        Resolving `packages/core/index.ts` to `core` finds a node the graph
        does not contain, and the signal silently reads 0."""
        import lens
        self.assertGreaterEqual(
            lens.hub_signal(self.ws, ["packages/core/index.ts"]), 1)


class TestManifestReaderEdges(unittest.TestCase):
    """`manifest_modules` is pure and injectable, so its edges are testable
    without a filesystem."""

    def _read(self, table):
        return lambda rel: table.get(rel)

    def test_malformed_json_keeps_the_path_derived_id(self):
        """Fail OPEN. A manifest nobody can parse must leave the module
        named as it was, never crash the scan or mint a blank id."""
        table = {"packages/ui/package.json": "{not json"}
        self.assertEqual(depgraph.manifest_modules(list(table), self._read(table)),
                         {})

    def test_a_missing_or_blank_name_is_not_an_id(self):
        for body in ('{"version":"1.0.0"}', '{"name":"   "}', '{"name":42}'):
            table = {"packages/ui/package.json": body}
            with self.subTest(body=body):
                self.assertEqual(
                    depgraph.manifest_modules(list(table), self._read(table)),
                    {})

    def test_a_reserved_prefix_is_refused(self):
        """`ext:`/`svc:`/`req:` are node-KIND namespaces. A declared name
        that collides would make an internal module read as external to
        every consumer of _node_kind — refuse it, keep the path id."""
        table = {"packages/ui/package.json": '{"name":"ext:evil"}',
                 "svc/a/go.mod": "module svc:thing\n"}
        self.assertEqual(depgraph.manifest_modules(list(table),
                                                   self._read(table)), {})

    def test_declared_ids_are_slash_shaped_on_every_host(self):
        table = {"packages/ui/package.json": '{"name":"acme\\\\ui"}'}
        self.assertEqual(
            depgraph.manifest_modules(list(table), self._read(table)),
            {"packages/ui": "acme/ui"})

    def test_a_host_shaped_lookup_path_still_resolves(self):
        ids = {"packages/ui": "@acme/ui"}
        self.assertEqual(depgraph.module_of(r"packages\ui\index.ts", ids),
                         "@acme/ui")

    def test_the_nearest_declaration_wins(self):
        ids = {"packages": "@acme/all", "packages/ui": "@acme/ui"}
        self.assertEqual(depgraph.module_of("packages/ui/a/b.ts", ids),
                         "@acme/ui")
        self.assertEqual(depgraph.module_of("packages/other/b.ts", ids),
                         "@acme/all")

    def test_specifier_matching_is_on_segment_boundaries(self):
        ids = {"acme/billing"}
        self.assertEqual(depgraph._declared_target("acme/billing/internal/db",
                                                   ids), "acme/billing")
        self.assertIsNone(depgraph._declared_target("acme/billing-legacy", ids),
                          )
        self.assertIsNone(depgraph._declared_target("acme", ids))


class TestNoDisturbanceWithoutManifests(unittest.TestCase):
    """A repo that declares nothing must scan byte-identically to before."""

    def setUp(self):
        self._old = os.environ.get("TASKPLANE_HOME")
        self.home = tempfile.mkdtemp(prefix="tp-mid2-home-")
        os.environ["TASKPLANE_HOME"] = self.home
        self.ws = _build(tempfile.mkdtemp(prefix="tp-mid2-ws-"), {
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

    def test_path_derived_ids_and_no_meta_key(self):
        g = depgraph.scan(self.ws)
        self.assertEqual(set(g["modules"]), {"auth", "db"})
        self.assertNotIn("module_ids", g.get("meta") or {},
                         "a repo with no declarations gains no new payload")
        self.assertEqual(depgraph.declared_module_ids(g), {})


if __name__ == "__main__":
    unittest.main()
