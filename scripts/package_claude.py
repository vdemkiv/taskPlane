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
import ast
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
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
    "hooks/host-native.json",
    "hooks/host_native_runtime.py",
)

# A member here is one whose absence breaks the INSTALLED experience, not
# merely the build. Each is asserted after the archive is written, against
# the archive's own name list — never against the filesystem.
MUST_CONTAIN = (
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    "hooks/hooks.json",
    "hooks/host-native.json",
    "hooks/host_native_runtime.py",
    "taskplane/taskplane_lite.py",
    "taskplane/loop.py",
    "taskplane/tp.py",
    "taskplane/lens.py",
    "taskplane/lens_signals.py",
    "taskplane/stage_entities.py",
    "taskplane/stage_handoff.py",
    "taskplane/stage_migration.py",
    "taskplane/loop_status.py",
    "taskplane/dashboard.py",
    "taskplane/runtime_eval.py",
    "taskplane/release_evidence.py",
    "lenses/catalog.json",
    "docs/cli-reference.md",
    "skills/taskplane/SKILL.md",
    "skills/taskplane/flow.json",
    "skills/tp-build/SKILL.md",
    "skills/tp-design/SKILL.md",
    "skills/tp-design/flow.json",
    "skills/tp-engineering/SKILL.md",
    "skills/tp-engineering/flow.json",
    "skills/tp-go/SKILL.md",
    "skills/tp-go/flow.json",
    "skills/tp-go/references/parallel.md",
    "skills/tp-go/references/retro.md",
    "skills/tp-product/SKILL.md",
    "skills/tp-product/flow.json",
    "skills/tp-status/SKILL.md",
    "skills/tp-status/flow.json",
    "docs/assets/taskplane-cowork-flow.gif",
    "lenses/references/prompt-injection-defense.md",
)

RELEASE_SURFACE_FILES = (
    "taskplane/release_evidence.py",
    "lenses/references/prompt-injection-defense.md",
    "README.md",
    "CHANGELOG.md",
)

SUPPORTED_HOOK_ROOT_FIELDS = frozenset({"description", "hooks"})


class PackageError(RuntimeError):
    """A package invariant failed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PackageError(message)


def release_runtime_constants() -> dict[str, str]:
    """Read release identity from the runtime without executing it."""
    path = ROOT / "taskplane" / "release_evidence.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    wanted = {
        "CURRENT_VERSION", "PREVIOUS_VERSION",
        "COMPATIBILITY_PREVIOUS_VERSION", "HISTORICAL_GRAPH_REVISION",
    }
    values: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id in wanted:
            value = ast.literal_eval(node.value)
            require(isinstance(value, str),
                    f"release runtime constant {target.id} must be a string")
            values[target.id] = value
    require(set(values) == wanted,
            "release runtime must declare the closed release identity")
    return values


def validate_hook_manifest(value: object) -> dict:
    """Reject the 2.17.12 hook shape on every marketplace artifact."""
    require(isinstance(value, dict), "hook manifest root must be an object")
    require(set(value) <= SUPPORTED_HOOK_ROOT_FIELDS,
            "hook manifest contains root fields rejected by Codex")
    require(isinstance(value.get("hooks"), dict),
            "hook manifest must declare hooks as an object")
    return value


def load_hook_manifest() -> dict:
    path = ROOT / "hooks" / "hooks.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackageError(f"cannot read hook manifest: {exc}") from exc
    return validate_hook_manifest(value)


def add_tree(files: set, base: Path, predicate) -> None:
    require(base.is_dir(), f"required directory is missing: {base.name}")
    for path in base.rglob("*"):
        if path.is_file() and predicate(path):
            files.add(path)


def package_files() -> list:
    load_hook_manifest()
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
    add_tree(files, ROOT / "docs",
             lambda p: p.suffix == ".md" or
             p.relative_to(ROOT / "docs").as_posix() ==
             "assets/taskplane-cowork-flow.gif")
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
        release = release_runtime_constants()
        require(version == release["CURRENT_VERSION"],
                "Claude manifest version must match release runtime version")
        require(release["PREVIOUS_VERSION"] == "2.17.20",
                "forward repair must preserve v2.17.20 as the previous release")
        require(release["COMPATIBILITY_PREVIOUS_VERSION"] == "2.17.23",
                "forward repair compatibility N-1 must be v2.17.23")
        require(release["HISTORICAL_GRAPH_REVISION"] ==
                "2757822ede49177fc52de8c173302286364d6206",
                "forward repair must preserve historical graph revision 2757822e")
        marketplace = json.loads(
            archive.read(f"{ARCHIVE_ROOT}/.claude-plugin/marketplace.json"))
        require(marketplace.get("version") == version and
                marketplace.get("plugins", [{}])[0].get("version") == version,
                "packaged marketplace and Claude manifest versions disagree")
        for required in RELEASE_SURFACE_FILES:
            member = f"{ARCHIVE_ROOT}/{required}"
            require(member in names,
                    f"archive is missing forward-release surface {required}")
            require(archive.read(member) == (ROOT / required).read_bytes(),
                    f"archive has stale forward-release bytes for {required}")
        require(manifest.get("hostNative") == "../hooks/host-native.json",
                "Claude manifest must retain supported host-native metadata")
        try:
            hook_manifest = json.loads(
                archive.read(f"{ARCHIVE_ROOT}/hooks/hooks.json"))
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise PackageError(
                "archive contains an unreadable hook manifest") from exc
        validate_hook_manifest(hook_manifest)
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
    # D-0010: a release artifact must name the tree it came from, and a
    # dirty tree cannot. Local test builds stay possible and stay marked.
    parser.add_argument("--allow-dirty", action="store_true",
                        help="package over uncommitted edits; the provenance "
                             "record is stamped verified_source: false and "
                             "the archive must not be released")
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
        require(manifest.get("hostNative") == "../hooks/host-native.json",
                "Claude manifest must retain supported host-native metadata")
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

    import release_provenance as prov
    try:
        prov_path = prov.write(ROOT, output, digest,
                               allow_dirty=args.allow_dirty)
    except prov.ProvenanceError as exc:
        output.unlink(missing_ok=True)
        checksum.unlink(missing_ok=True)
        print(f"package_claude: {exc}", file=sys.stderr)
        return 1

    print(f"archive: {output}")
    print(f"files: {count}")
    print(f"compressed_bytes: {output.stat().st_size}")
    print(f"uncompressed_bytes: {uncompressed}")
    print(f"sha256: {digest}")
    print(f"checksum: {checksum}")
    print(f"provenance: {prov_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
