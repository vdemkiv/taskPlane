"""Language references reach the worker without widening lens routing."""
import hashlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lens  # noqa: E402


class TestLanguageReferenceDelivery(unittest.TestCase):
    @staticmethod
    def _plugin_root():
        return os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))

    def test_go_review_brief_names_and_instructs_the_reference(self):
        routing = lens.route(["src/service.go"], only=["code-quality"])
        row = next(x for x in routing["lenses"]
                   if x["id"] == "code-quality")
        refs = row["language_references"]
        self.assertEqual([r["path"] for r in refs],
                         ["lenses/references/go-code-quality.md"])
        dispatch = lens.dispatch_briefs(routing)
        briefs = list(dispatch["deep"])
        if dispatch.get("sweep"):
            briefs.append(dispatch["sweep"])
        brief = next(b for b in briefs
                     if b.get("id") == "code-quality"
                     or "code-quality" in b.get("ids", []))
        self.assertEqual(brief["language_references"], refs)
        self.assertIn("read and apply", brief["prompt"].lower())
        self.assertIn(refs[0]["path"], brief["prompt"])

    def test_reference_resolution_is_language_general(self):
        cases = {
            "src/a.go": "go-code-quality.md",
            "src/a.py": "python-code-quality.md",
            "src/a.ts": "typescript-code-quality.md",
        }
        for path, suffix in cases.items():
            with self.subTest(path=path):
                refs = lens.language_references([path])
                self.assertEqual(len(refs), 1)
                self.assertTrue(refs[0]["path"].endswith(suffix))
                absolute = os.path.join(
                    self._plugin_root(), *refs[0]["path"].split("/"))
                with open(absolute, "rb") as stream:
                    self.assertEqual(
                        refs[0]["content_sha256"],
                        hashlib.sha256(stream.read()).hexdigest())
        self.assertEqual(lens.language_references(["README.md"]), [])

    def test_generic_go_scope_primes_go_instead_of_python(self):
        with tempfile.TemporaryDirectory() as ws:
            open(os.path.join(ws, "go.mod"), "w", encoding="utf-8").close()
            routing = lens.prime_scope(["src/**"], workspace=ws)
        self.assertIn("go.mod", routing["context"]["files"])
        refs = routing["context"]["language_references"]
        self.assertEqual({r["language"] for r in refs}, {"go"})
        self.assertNotIn("python", {r["language"] for r in refs})

    def test_mixed_workspace_primes_each_detected_language(self):
        with tempfile.TemporaryDirectory() as ws:
            for child, manifest in (("go", "go.mod"),
                                    ("py", "pyproject.toml"),
                                    ("web", "tsconfig.json")):
                root = os.path.join(ws, "services", child)
                os.makedirs(root)
                open(os.path.join(root, manifest), "w",
                     encoding="utf-8").close()
            routing = lens.prime_scope(["services/**"], workspace=ws)
        files = routing["context"]["files"]
        self.assertTrue(any(path.endswith("/x.go") for path in files))
        self.assertTrue(any(path.endswith("/x.py") for path in files))
        self.assertTrue(any(path.endswith("/x.ts") for path in files))
        self.assertEqual(
            {row["language"] for row in
             routing["context"]["language_references"]},
            {"go", "python", "typescript"})

    def test_go_design_uses_the_design_reference(self):
        routing = lens.route(["go.mod"], task_type="solution-design",
                             only=["solution-design"])
        refs = routing["context"]["language_references"]
        self.assertEqual(len(refs), 1)
        self.assertEqual(
            {key: refs[0][key] for key in ("language", "lens", "path")},
            {"language": "go", "lens": "solution-design",
             "path": "lenses/references/go-solution-design.md"})
        self.assertRegex(refs[0]["content_sha256"], r"^[0-9a-f]{64}$")
        row = next(x for x in routing["lenses"]
                   if x["id"] == "solution-design")
        self.assertEqual(row["language_references"], refs)

    def test_owning_lenses_receive_only_their_language_sections(self):
        refs = lens.language_references(
            ["service.go", "worker.py", "ui.tsx"], lens_ids={
                "architecture", "backend", "frontend", "testability"})
        actual = {(row["language"], row["lens"], row.get("section"))
                  for row in refs}
        self.assertEqual(actual, {
            ("go", "architecture", "Architecture"),
            ("go", "backend", "Backend"),
            ("go", "testability", "Testability"),
            ("python", "testability", "Testability"),
            ("typescript", "architecture", "Architecture"),
            ("typescript", "frontend", "Frontend async"),
        })
        self.assertTrue(all(row["content_sha256"] for row in refs))

    def test_reference_versions_and_verified_gate_fixes_are_present(self):
        root = self._plugin_root()
        bodies = {}
        for language in ("go", "python", "typescript"):
            path = os.path.join(root, "lenses", "references",
                                f"{language}-code-quality.md")
            bodies[language] = open(path, encoding="utf-8").read()
            self.assertIn("Target language version:", bodies[language])
        self.assertIn("grep -rnP", bodies["python"])
        self.assertIn("parserOptions.projectService", bodies["typescript"])
        self.assertIn("Go 1.26", bodies["go"])
        sources = open(os.path.join(root, "lenses", "references",
                                    "SOURCES.md"), encoding="utf-8").read()
        self.assertIn("e67851cfcca008592c7c4965b8220c7cb37e2f1c", sources)
        self.assertIn("go.dev", sources)
        self.assertIn("docs.python.org", sources)
        self.assertIn("typescriptlang.org", sources)


if __name__ == "__main__":
    unittest.main()
