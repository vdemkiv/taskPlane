"""Release checks derived from executable or package truth.

Release validation should fail when an artifact is stale, missing, or cannot
be installed. It should not fail because prose was wrapped differently or a
skill omitted a release-number mention.
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))


def _read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


class TestReleaseWindow(unittest.TestCase):
    ROW = re.compile(r"^\| \*\*(v\d+\.\d+\.\d+)\*\* \|", re.M)

    def test_readme_keeps_exactly_three_current_changelog_rows(self):
        current = "v" + json.loads(
            _read(".codex-plugin/plugin.json"))["version"]
        readme = _read("README.md")
        section = readme.split("## What's new", 1)[1].split("## Install", 1)[0]
        rows = self.ROW.findall(section)
        changelog_rows = set(self.ROW.findall(_read("CHANGELOG.md")))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], current)
        self.assertTrue(set(rows) <= changelog_rows)


class TestGeneratedCliReference(unittest.TestCase):
    REFERENCE = "docs/cli-reference.md"
    TPPY = os.path.join(ROOT, "taskplane", "tp.py")

    def _generate(self) -> str:
        result = subprocess.run(
            [sys.executable, self.TPPY, "help", "--md"],
            capture_output=True,
            text=True,
            cwd=ROOT,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.strip())
        return result.stdout

    def test_committed_reference_matches_live_parser(self):
        generated = self._generate()
        self.assertEqual(_read(self.REFERENCE), generated)
        self.assertNotIn(ROOT, generated)

    def test_generator_rejects_undocumented_flags(self):
        import tp as cli

        parser = argparse.ArgumentParser(prog="fake")
        sub = parser.add_subparsers(dest="cmd", required=True)
        command = sub.add_parser("one", help="documented command")
        command.add_argument("--bare")
        with self.assertRaises(cli.CliReferenceError) as raised:
            cli.cli_reference_markdown(parser)
        self.assertIn("--bare", str(raised.exception))

    def test_generator_owns_the_closed_stage_request_contract(self):
        import tp as cli

        generated = self._generate()
        self.assertIn("### Closed stage-command request", generated)
        self.assertIn("`taskplane.stage-command/v1`", generated)
        self.assertIn("| `terminalize-and-start` |", generated)
        self.assertIn("`predecessor_stage_id`", generated)
        self.assertIn("`successor_stage`", generated)
        self.assertIn(
            "tp.py stage terminalize-and-start --request request.json",
            generated,
        )
        example = cli._CLI_STAGE_SUCCESSOR_EXAMPLE
        self.assertEqual(example["outcome"], "done")
        self.assertNotIn("reason_code", example)
        self.assertNotIn("reason", example)
        self.assertEqual(
            example["declared_scope"],
            {
                "scope_paths": ["taskplane/**"],
                "out_of_scope_paths": [],
            },
        )


class TestReferencedDocumentation(unittest.TestCase):
    REFERENCE = re.compile(
        r"`((?:\.\./[a-z0-9-]+/)?references/[a-z0-9-]+\.md)`"
    )

    def _skill_pointers(self):
        pointers = []
        for skill_file in sorted(glob.glob(os.path.join(ROOT, "skills", "*", "SKILL.md"))):
            skill_dir = os.path.dirname(skill_file)
            skill = os.path.basename(skill_dir)
            for pointer in sorted(set(self.REFERENCE.findall(_read(os.path.relpath(skill_file, ROOT))))):
                target = os.path.normpath(os.path.join(skill_dir, pointer))
                pointers.append((skill, pointer, os.path.relpath(target, ROOT)))
        return pointers

    @staticmethod
    def _packager():
        path = os.path.join(ROOT, "scripts", "package_openai.py")
        spec = importlib.util.spec_from_file_location("_package_openai_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_skill_document_and_reference_links_resolve(self):
        dead = []
        for skill_file in glob.glob(os.path.join(ROOT, "skills", "*", "SKILL.md")):
            text = _read(os.path.relpath(skill_file, ROOT))
            for rel in set(re.findall(r"`(docs/[a-z0-9-]+\.md)`", text)):
                if not os.path.isfile(os.path.join(ROOT, rel)):
                    dead.append(f"{skill_file} -> {rel}")
        for _skill, pointer, target in self._skill_pointers():
            if not os.path.isfile(os.path.join(ROOT, target)):
                dead.append(f"{pointer} -> {target}")
        self.assertEqual(dead, [])

    def test_references_are_members_of_the_openai_package(self):
        packager = self._packager()
        packaged = {
            os.path.relpath(str(path), str(packager.ROOT)).replace(os.sep, "/")
            for path in packager.package_files(packager.load_manifest())
        }
        excluded = set(getattr(packager, "OPENAI_EXCLUDED_SKILLS", ()))
        missing = [
            target
            for skill, _pointer, target in self._skill_pointers()
            if skill not in excluded and target.replace(os.sep, "/") not in packaged
        ]
        self.assertEqual(missing, [])

    def test_built_archive_contains_public_docs_and_excludes_claude_workflows(self):
        packager = self._packager()
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "taskplane-openai.zip"
            packager.write_zip(
                packager.package_files(packager.load_manifest()), archive_path
            )
            packager.validate_archive(archive_path)
            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())
        expected_docs = {
            "taskplane/" + os.path.relpath(path, ROOT).replace(os.sep, "/")
            for path in glob.glob(os.path.join(ROOT, "docs", "*.md"))
        }
        self.assertTrue(expected_docs <= names)
        self.assertFalse(any(name.startswith("taskplane/workflows/") for name in names))


class TestInstalledHookPackaging(unittest.TestCase):
    HOOK_MEMBERS = {
        "taskplane/hooks/hooks.json",
        "taskplane/hooks/host-native.json",
        "taskplane/hooks/host_native_runtime.py",
    }

    @staticmethod
    def _load_packager(name: str):
        path = os.path.join(ROOT, "scripts", name)
        spec = importlib.util.spec_from_file_location(
            f"_release_{name.replace('.', '_')}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_openai_archive_has_parser_safe_install_complete_hooks(self):
        packager = self._load_packager("package_openai.py")
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "taskplane-openai.zip"
            packager.write_zip(
                packager.package_files(packager.load_manifest()), archive_path)
            packager.validate_archive(archive_path)
            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())
                hooks = json.loads(archive.read(
                    "taskplane/hooks/hooks.json"))
        self.assertTrue(self.HOOK_MEMBERS <= names)
        self.assertTrue(set(hooks) <= {"description", "hooks"})
        self.assertNotIn("hostNative", hooks)

    def test_claude_archive_has_install_complete_hooks(self):
        packager = self._load_packager("package_claude.py")
        manifest = json.loads(_read(".claude-plugin/plugin.json"))
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "taskplane-claude.zip"
            packager.write_zip(packager.package_files(), archive_path)
            packager.validate_archive(archive_path, manifest["version"])
            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())
        self.assertTrue(self.HOOK_MEMBERS <= names)


class TestSkillIdentity(unittest.TestCase):
    @staticmethod
    def _frontmatter_name(text: str) -> str | None:
        frontmatter = re.search(r"\A---\r?\n(.*?)\r?\n---", text, re.S)
        if not frontmatter:
            return None
        name = re.search(r"^name:\s*(.+?)\s*$", frontmatter.group(1), re.M)
        return name.group(1).strip().strip("\"'") if name else None

    def test_manifest_names_match_skill_directory(self):
        for skill_file in sorted(glob.glob(os.path.join(ROOT, "skills", "*", "SKILL.md"))):
            slug = os.path.basename(os.path.dirname(skill_file))
            text = _read(os.path.relpath(skill_file, ROOT))
            self.assertEqual(self._frontmatter_name(text), slug)
            for interface in glob.glob(os.path.join(os.path.dirname(skill_file), "agents", "*.y*ml")):
                body = _read(os.path.relpath(interface, ROOT))
                for value in re.findall(r"^\s*display_name:\s*(.+?)\s*$", body, re.M):
                    self.assertEqual(value.strip().strip("\"'"), slug)


if __name__ == "__main__":
    unittest.main()
