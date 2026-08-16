"""Generated/static documentation contracts with a code-derived oracle.

This deliberately avoids asserting prose, source-code spellings, or old
release narratives. Those checks created churn without proving a user
workflow. Only surfaces that can be compared mechanically to a source of
truth remain here.
"""
from __future__ import annotations

import importlib.util
import os
import re
import tempfile
import unittest


ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(*rel: str) -> str:
    with open(os.path.join(ROOT, *rel), encoding="utf-8") as f:
        return f.read()


class TestGeneratedDocumentation(unittest.TestCase):
    def test_every_taskplane_env_var_read_by_code_is_documented(self):
        code = ""
        source_dir = os.path.join(ROOT, "taskplane")
        for name in sorted(os.listdir(source_dir)):
            if name.endswith(".py"):
                code += _read("taskplane", name)
        code += _read("hooks", "hooks.json")
        tokens = set(re.findall(r"TASKPLANE_[A-Z_]*[A-Z]", code))
        documented = _read("docs", "configuration.md")
        self.assertEqual(sorted(t for t in tokens if t not in documented), [])

    def test_lens_catalog_is_generated_from_catalog(self):
        spec = importlib.util.spec_from_file_location(
            "gen_lens_catalog",
            os.path.join(ROOT, "scripts", "gen_lens_catalog.py"),
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            module.OUT = os.path.join(tmp, "lens-catalog.md")
            module.main()
            with open(module.OUT, encoding="utf-8") as f:
                generated = f.read()
        self.assertEqual(generated, _read("docs", "lens-catalog.md"))

    def test_submission_worksheet_uses_version_placeholder(self):
        document = _read("docs", "openai-submission.md")
        self.assertFalse(re.findall(r"\d+\.\d+\.\d+-openai\.zip", document))
        self.assertIn("<version>", document)


if __name__ == "__main__":
    unittest.main()
