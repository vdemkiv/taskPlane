"""Build reproducible source packages for Codex and Claude."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def package_input(path: Path) -> bool:
    """Generated host declarations and local tooling are never shipped."""
    relative = path.relative_to(ROOT)
    return (not set(relative.parts) & {"node_modules", "__pycache__", ".cache", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
            and relative.parts[:2] not in {(".claude-plugin", "types"), (".codex-plugin", "types")}
            and not path.name.endswith((".d.ts", ".d.mts", ".d.cts", ".pyc", ".pyo", ".tsbuildinfo")))


def read_member(path: Path) -> bytes:
    """Reject indirect symlinks before any package input is read."""
    root = ROOT.resolve()
    relative = path.relative_to(root)
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"Package member cannot follow a symlink: {path}")
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"Package member escapes source root: {path}")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(f"Package member must be regular: {path}")
        return stream.read()


def host_member(name: str, data: bytes, host: str | None) -> bytes:
    """Apply the single declared host transformation to source or committed bytes."""
    if host == "openai" and name == "hooks/hooks.json":
        hooks = json.loads(data)
        if not isinstance(hooks, dict):
            raise ValueError("Hook registration must be an object")
        if "modules" in hooks:
            del hooks["modules"]
            return (json.dumps(hooks, indent=2) + "\n").encode("utf-8")
    return data


def source_identity(members: dict[str, bytes], host: str | None = None) -> dict:
    """Compare archived bytes with a commit, including declared host transforms."""
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True)
    revision = commit.stdout.strip() if commit.returncode == 0 else ""
    tree = subprocess.run(["git", "ls-tree", "-r", "-z", revision], cwd=ROOT,
                          capture_output=True) if revision else None
    blobs = {}
    if tree is not None and tree.returncode == 0:
        for entry in tree.stdout.split(b"\0"):
            if entry:
                metadata, name = entry.split(b"\t", 1)
                mode, kind, oid = metadata.split()
                if kind == b"blob" and mode in {b"100644", b"100755"}:
                    blobs[name.decode("utf-8", errors="surrogateescape")] = oid.decode()
    comparable = bool(revision and tree is not None and tree.returncode == 0)
    differences = []
    transforms = []
    for name, data in members.items():
        if comparable and host == "openai" and name == "hooks/hooks.json" and name in blobs:
            committed = subprocess.run(["git", "cat-file", "blob", blobs[name]], cwd=ROOT,
                                       capture_output=True)
            if committed.returncode == 0:
                try:
                    expected = host_member(name, committed.stdout, host)
                except (ValueError, TypeError):
                    expected = None
                if expected is not None and expected != committed.stdout:
                    transforms.append({"path": name, "operation": "remove-claude-mod-registration"})
                    if expected != data:
                        differences.append(name)
                    continue
        blob = b"blob " + str(len(data)).encode() + b"\0" + data
        digest = hashlib.sha256(blob) if len(revision) == 64 else hashlib.sha1(blob, usedforsecurity=False)
        if blobs.get(name) != digest.hexdigest():
            differences.append(name)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                           capture_output=True, text=True)
    return {"source_commit": revision or None,
            "source_dirty": bool(dirty.stdout.strip()) if dirty.returncode == 0 else None,
            "matches_source_commit": not differences if comparable else None,
            "source_member_differences": sorted(differences) if comparable else None,
            "source_transforms": transforms}


def package(host: str, output_dir: Path, extension: str | None = None) -> dict:
    if host not in {"openai", "claude"}:
        raise ValueError("unknown plugin host")
    manifest_dir = ".codex-plugin" if host == "openai" else ".claude-plugin"
    manifest = json.loads(read_member(ROOT / manifest_dir / "plugin.json"))
    version = manifest["version"]
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
        raise ValueError("Package version must be a safe semantic version")
    for other in (".codex-plugin", ".claude-plugin"):
        target_manifest = ROOT / other / "plugin.json"
        if target_manifest.exists() and json.loads(read_member(target_manifest)).get("version") != version:
            raise ValueError("Codex and Claude package versions disagree")
    suffix = extension or ("zip" if host == "openai" else "plugin")
    if suffix not in {"zip", "plugin"}:
        raise ValueError("package extension must be zip or plugin")
    files = {ROOT / name for name in ("README.md", "CHANGELOG.md", "LICENSE", "PRIVACY.md", "TERMS.md")}
    for directory in (manifest_dir, "hooks", "assets", "skills", "agents"):
        if (ROOT / directory).is_symlink():
            raise ValueError(f"Package directory cannot be a symlink: {directory}")
        files.update(p for p in (ROOT / directory).rglob("*")
                     if p.is_file() and package_input(p))
    files.update((ROOT / "taskplane").glob("*.py"))
    files.update(p for p in (ROOT / "lenses").rglob("*") if p.suffix == ".md" or p.name == "catalog.json")
    files.update(ROOT / "docs" / name for name in ("cli-reference.md", "lens-catalog.md", "onboarding.md"))
    files.update(ROOT / "docs/assets" / name for name in
                 ("taskplane-cowork-flow.gif", "taskplane-flow-source.html"))
    if host == "claude":
        # Keep the runtime and reproducible checks explicit; never sweep tooling
        # directories or generated host declarations into the plugin.
        files.update(ROOT / "mods" / name for name in
                     ("taskplane.tsx", "taskplane.test.ts", "tsconfig.json",
                      "package.json", "package-lock.json"))
        files.add(ROOT / "docs/claude-mods.md")
    for path in files:
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Package member must be a regular file: {path}")
    members = {path.relative_to(ROOT).as_posix(): read_member(path) for path in sorted(files)}
    members = {name: host_member(name, data, host) for name, data in members.items()}
    identity = source_identity(members, host)
    output_dir.mkdir(parents=True, exist_ok=True)
    name = f"taskplane-{version}-openai.zip" if host == "openai" else f"taskplane-{version}.{suffix}"
    target = output_dir / name
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative, data in members.items():
            info = zipfile.ZipInfo(relative, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100644 << 16)
            archive.writestr(info, data)
    result = {"host": host, "version": version, "archive": str(target.resolve()),
              "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
              **identity, "members": len(members),
              "release_readiness": {"source_identity": "matched" if identity["matches_source_commit"] else "unverified_or_changed",
                                    "platform_ci": "not_observed", "installed_runtime": "not_observed",
                                    "publish_ready": False},
              "member_sha256": {name: hashlib.sha256(data).hexdigest()
                                for name, data in members.items()}}
    target.with_suffix(target.suffix + ".json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main(host: str) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--ext", choices=["zip", "plugin"])
    args = parser.parse_args()
    print(json.dumps(package(host, args.output_dir, args.ext), indent=2))
    return 0
