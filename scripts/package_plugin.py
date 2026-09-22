"""Build reproducible source packages for Codex and Claude."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def source_identity(members: dict[str, bytes]) -> dict:
    """Compare the exact archived bytes with one captured commit, not its label."""
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
    for name, data in members.items():
        blob = b"blob " + str(len(data)).encode() + b"\0" + data
        digest = hashlib.sha256(blob) if len(revision) == 64 else hashlib.sha1(blob)
        if blobs.get(name) != digest.hexdigest():
            differences.append(name)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                           capture_output=True, text=True)
    return {"source_commit": revision or None,
            "source_dirty": bool(dirty.stdout.strip()) if dirty.returncode == 0 else None,
            "matches_source_commit": not differences if comparable else None,
            "source_member_differences": sorted(differences) if comparable else None}


def package(host: str, output_dir: Path, extension: str | None = None) -> dict:
    if host not in {"openai", "claude"}:
        raise ValueError("unknown plugin host")
    manifest_dir = ".codex-plugin" if host == "openai" else ".claude-plugin"
    manifest = json.loads((ROOT / manifest_dir / "plugin.json").read_text())
    version = manifest["version"]
    suffix = extension or ("zip" if host == "openai" else "plugin")
    if suffix not in {"zip", "plugin"}:
        raise ValueError("package extension must be zip or plugin")
    files = {ROOT / name for name in ("README.md", "CHANGELOG.md", "LICENSE", "PRIVACY.md", "TERMS.md")}
    for directory in (manifest_dir, "hooks", "assets", "skills", "agents"):
        files.update(p for p in (ROOT / directory).rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts)
    files.update((ROOT / "taskplane").glob("*.py"))
    files.update(p for p in (ROOT / "lenses").rglob("*") if p.suffix == ".md" or p.name == "catalog.json")
    files.update(ROOT / "docs" / name for name in ("cli-reference.md", "lens-catalog.md", "onboarding.md"))
    files.update(ROOT / "docs/assets" / name for name in
                 ("taskplane-cowork-flow.gif", "taskplane-flow-source.html"))
    for path in files:
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Package member must be a regular file: {path}")
    members = {path.relative_to(ROOT).as_posix(): path.read_bytes() for path in sorted(files)}
    identity = source_identity(members)
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
