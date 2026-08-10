#!/usr/bin/env python3
"""Build and validate the taskplane Claude plugin ZIP.

The sibling of scripts/package_openai.py, and it exists for the same reason:
until now the Claude package was assembled by hand for each release, so what
a user installed was whatever the person building it happened to select.
A hand-built archive cannot be diffed, cannot be rebuilt identically, and
cannot be checked by CI — the failure mode is a member quietly missing from
one release and present in the next, which is invisible until an installed
user hits a dead pointer.

Membership differs from the OpenAI archive in three ways, all of them host
facts rather than taste:

  * `.claude-plugin/plugin.json` AND `marketplace.json` — Claude reads the
    marketplace entry; Codex has no equivalent.
  * `workflows/**.js` — Dynamic Workflows are a Claude capability. The
    Task-dispatch path stays mandatory and byte-identical on both hosts, so
    omitting these on Codex removes an accelerator, never a gate.
  * every skill, including `tp-tag`. Claude Tag is a Claude surface; the
    OpenAI archive excludes it because there is nothing there to drive it.

Determinism: fixed timestamps, fixed permissions, sorted members. Building
the same tree twice produces byte-identical bytes and therefore the same
sha256, so a release archive can be verified against its source commit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import zipfile
from pathlib import Path

# Console codepages are not always UTF-8 (Windows defaults to cp1252, a C
# locale gives ASCII), and this script's own output carries arrows and em
# dashes. The text is ours and it is UTF-8; say so rather than dying in the
# middle of a report.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE_PATH = ROOT / ".claude-plugin" / "marketplace.json"
ARCHIVE_ROOT = "taskplane"
FIXED_ZIP_TIME = (2020, 1, 1, 0, 0, 0)

REQUIRED_FILES = (
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "PRIVACY.md",
    "SUPPORT.md",
    "TERMS.md",
    "hooks/hooks.json",
)

# A member here is one whose absence breaks the INSTALLED experience, not
# merely the build. Each is asserted after the archive is written, against
# the archive's own name list — never against the filesystem.
MUST_CONTAIN = (
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    "hooks/hooks.json",
    "taskplane/taskplane_lite.py",
    "taskplane/tp.py",
    "taskplane/lens.py",
    "taskplane/lens_signals.py",
    "lenses/catalog.json",
    "skills/taskplane/SKILL.md",
)


class PackageError(RuntimeError):
    """A package invariant failed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PackageError(message)


def add_tree(files: set, base: Path, predicate) -> None:
    require(base.is_dir(), f"required directory is missing: {base.name}")
    for path in base.rglob("*"):
        if path.is_file() and predicate(path):
            files.add(path)


def package_files() -> list:
    files: set = {MANIFEST_PATH, MARKETPLACE_PATH}
    for relative in REQUIRED_FILES:
        path = ROOT / relative
        require(path.is_file(), f"required file is missing: {relative}")
        files.add(path)

    add_tree(files, ROOT / "assets",
             lambda p: p.suffix.lower() in {".svg", ".png", ".jpg", ".jpeg",
                                            ".webp"})
    add_tree(files, ROOT / "skills", lambda p: True)
    add_tree(files, ROOT / "agents", lambda p: p.suffix == ".md")
    add_tree(files, ROOT / "discipline", lambda p: p.suffix == ".md")
    add_tree(files, ROOT / "docs", lambda p: p.suffix == ".md")
    add_tree(files, ROOT / "workflows", lambda p: p.suffix == ".js")
    add_tree(files, ROOT / "taskplane",
             lambda p: p.parent == ROOT / "taskplane" and p.suffix == ".py")
    add_tree(files, ROOT / "lenses",
             lambda p: p.suffix == ".md" or p.name == "catalog.json")

    for path in files:
        relative = path.relative_to(ROOT)
        require(not path.is_symlink(),
                f"symlinks are not allowed in the package: {relative}")
        mode = path.stat(follow_symlinks=False).st_mode
        require(stat.S_ISREG(mode),
                f"package member is not a regular file: {relative}")
    return sorted(files, key=lambda p: p.relative_to(ROOT).as_posix())


def write_zip(files: list, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    try:
        with zipfile.ZipFile(temporary, "w",
                             compression=zipfile.ZIP_DEFLATED,
                             compresslevel=9) as archive:
            for path in files:
                relative = path.relative_to(ROOT).as_posix()
                info = zipfile.ZipInfo(f"{ARCHIVE_ROOT}/{relative}",
                                       FIXED_ZIP_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, path.read_bytes(),
                                 compress_type=zipfile.ZIP_DEFLATED,
                                 compresslevel=9)
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()


def validate_archive(path: Path, version: str) -> tuple:
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        require(bad is None, f"corrupt archive member: {bad}")
        names = set(archive.namelist())
        for relative in MUST_CONTAIN:
            require(f"{ARCHIVE_ROOT}/{relative}" in names,
                    f"archive is missing a member the install needs: "
                    f"{relative}")
        for name in names:
            require(name.startswith(f"{ARCHIVE_ROOT}/"),
                    f"member escapes the archive root: {name}")
            require(".." not in name.split("/"),
                    f"member contains a parent traversal: {name}")
        manifest = json.loads(
            archive.read(f"{ARCHIVE_ROOT}/.claude-plugin/plugin.json"))
        require(manifest["version"] == version,
                f"packaged manifest says {manifest['version']}, "
                f"expected {version}")
        uncompressed = sum(i.file_size for i in archive.infolist())
    return len(names), uncompressed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the taskplane Claude plugin ZIP.")
    parser.add_argument("--output-dir", default=str(ROOT / "dist"))
    # Both artifacts are the same ZIP container; only the extension differs.
    # `.plugin` is what a Claude file-upload install expects, so it is built
    # and checksummed here rather than copied by hand after the fact —
    # a renamed copy has no provenance and no gate.
    parser.add_argument("--ext", choices=("zip", "plugin"), default="zip")
    args = parser.parse_args(argv)

    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        marketplace = json.loads(
            MARKETPLACE_PATH.read_text(encoding="utf-8"))
        version = manifest["version"]
        require(marketplace["version"] == version
                and marketplace["plugins"][0]["version"] == version,
                "manifest and marketplace versions disagree — the release "
                "is not single-sourced")
        files = package_files()
        name = (f"taskplane-{version}-claude.zip" if args.ext == "zip"
                else f"taskplane-{version}.plugin")
        output = Path(args.output_dir) / name
        write_zip(files, output)
        count, uncompressed = validate_archive(output, version)
    except PackageError as exc:
        print(f"package_claude: {exc}", file=sys.stderr)
        return 1

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    checksum = output.with_suffix(output.suffix + ".sha256")
    checksum.write_text(f"{digest}  {output.name}\n", encoding="utf-8")

    print(f"archive: {output}")
    print(f"files: {count}")
    print(f"compressed_bytes: {output.stat().st_size}")
    print(f"uncompressed_bytes: {uncompressed}")
    print(f"sha256: {digest}")
    print(f"checksum: {checksum}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
