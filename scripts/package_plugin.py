"""Build reproducible source packages for Codex and Claude."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def package(host: str, output_dir: Path, extension: str | None = None) -> dict:
    if host not in {"openai", "claude"}:
        raise ValueError("unknown plugin host")
    manifest_dir = ".codex-plugin" if host == "openai" else ".claude-plugin"
    manifest = json.loads((ROOT / manifest_dir / "plugin.json").read_text())
    version = manifest["version"]
    suffix = extension or ("zip" if host == "openai" else "plugin")
    if suffix not in {"zip", "plugin"}:
        raise ValueError("package extension must be zip or plugin")
    files = {ROOT / name for name in ("README.md", "LICENSE", "PRIVACY.md", "TERMS.md")}
    for directory in (manifest_dir, "hooks", "assets", "skills", "agents"):
        files.update(p for p in (ROOT / directory).rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts)
    files.update((ROOT / "taskplane").glob("*.py"))
    files.update(p for p in (ROOT / "lenses").rglob("*") if p.suffix == ".md" or p.name == "catalog.json")
    files.update(ROOT / "docs" / name for name in ("cli-reference.md", "lens-catalog.md"))
    for path in files:
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Package member must be a regular file: {path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    name = f"taskplane-{version}-openai.zip" if host == "openai" else f"taskplane-{version}.{suffix}"
    target = output_dir / name
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            relative = path.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo(relative, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100644 << 16)
            archive.writestr(info, path.read_bytes())
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                           capture_output=True, text=True)
    result = {"host": host, "version": version, "archive": str(target.resolve()),
              "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
              "source_commit": commit.stdout.strip(), "source_dirty": bool(dirty.stdout.strip()),
              "members": len(files)}
    target.with_suffix(target.suffix + ".json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main(host: str) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--ext", choices=["zip", "plugin"])
    args = parser.parse_args()
    print(json.dumps(package(host, args.output_dir, args.ext), indent=2))
    return 0
