#!/usr/bin/env python3
"""Build and validate the taskplane skills-only OpenAI marketplace ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import sys
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PurePosixPath

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
MANIFEST_PATH = ROOT / ".codex-plugin" / "plugin.json"
ARCHIVE_ROOT = "taskplane"
FIXED_ZIP_TIME = (2020, 1, 1, 0, 0, 0)
OPENAI_EXCLUDED_SKILLS = {"tp-tag"}

CATEGORIES = {
    "Productivity",
    "Creativity",
    "Developer Tools",
    "Business & Operations",
    "Data & Analytics",
    "Communication",
    "Education & Research",
    "Security",
    "Finance",
    "Healthcare",
    "Travel",
    "Entertainment",
    "Other",
}

REQUIRED_FILES = (
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "PRIVACY.md",
    "SUPPORT.md",
    "TERMS.md",
    "docs/authority-matrix.md",
    "docs/loop-design.md",
    "docs/state-spec.md",
)


class PackageError(RuntimeError):
    """A marketplace package invariant failed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PackageError(message)


def load_manifest() -> dict:
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackageError(f"cannot read {MANIFEST_PATH}: {exc}") from exc
    require(isinstance(manifest, dict), "plugin manifest must be a JSON object")
    return manifest


def valid_https_url(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > 1024:
        return False
    parsed = urllib.parse.urlsplit(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def relative_luminance(color: str) -> float:
    values = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]

    def channel(value: float) -> float:
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(value) for value in values)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first: str, second: str) -> float:
    high, low = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def svg_dimensions(path: Path) -> tuple[float, float]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise PackageError(f"invalid SVG {path.relative_to(ROOT)}: {exc}") from exc
    require(root.tag.rsplit("}", 1)[-1] == "svg", f"{path.relative_to(ROOT)} root must be <svg>")
    view_box = root.attrib.get("viewBox", "").replace(",", " ").split()
    if len(view_box) == 4:
        try:
            width, height = float(view_box[2]), float(view_box[3])
        except ValueError as exc:
            raise PackageError(f"{path.relative_to(ROOT)} has a non-numeric viewBox") from exc
    else:
        try:
            width = float(root.attrib["width"])
            height = float(root.attrib["height"])
        except (KeyError, ValueError) as exc:
            raise PackageError(f"{path.relative_to(ROOT)} needs numeric square dimensions") from exc
    require(math.isfinite(width) and math.isfinite(height), f"{path.relative_to(ROOT)} dimensions must be finite")
    return width, height


def validate_manifest(manifest: dict) -> None:
    name = manifest.get("name")
    version = manifest.get("version")
    description = manifest.get("description")
    require(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name) is not None,
            "manifest name must match OpenAI's package-name rules")
    require(name == ARCHIVE_ROOT, f"manifest name must remain {ARCHIVE_ROOT!r}")
    require(isinstance(version, str) and len(version) <= 64 and
            re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version) is not None,
            "manifest version must be semantic versioning")
    require(isinstance(description, str) and 0 < len(description) <= 1024,
            "manifest description must be 1-1024 characters")
    author = manifest.get("author")
    require(isinstance(author, dict) and isinstance(author.get("name"), str) and author["name"],
            "author.name is required")

    interface = manifest.get("interface")
    require(isinstance(interface, dict), "interface is required")
    limits = {
        "displayName": 30,
        "shortDescription": 30,
        "longDescription": 4000,
        "developerName": 80,
    }
    for field, limit in limits.items():
        value = interface.get(field)
        require(isinstance(value, str) and value.strip() == value and value,
                f"interface.{field} is required and cannot have outer whitespace")
        require(len(value) <= limit, f"interface.{field} exceeds the final submission limit of {limit}")
        if field != "longDescription":
            require("\n" not in value and "\r" not in value, f"interface.{field} must fit on one line")
    require(interface.get("developerName") == author.get("name"),
            "author.name and interface.developerName must match")
    require(interface.get("category") in CATEGORIES, "interface.category is not an OpenAI directory category")

    capabilities = interface.get("capabilities")
    require(isinstance(capabilities, list) and 0 < len(capabilities) <= 20,
            "interface.capabilities must contain 1-20 entries")
    for capability in capabilities:
        require(isinstance(capability, str) and capability and len(capability) <= 120 and "\n" not in capability,
                "each capability must be a non-empty single line of at most 120 characters")

    prompts = interface.get("defaultPrompt")
    require(isinstance(prompts, list) and 0 < len(prompts) <= 3,
            "interface.defaultPrompt must contain 1-3 prompts")
    normalized_prompts: set[str] = set()
    for prompt in prompts:
        require(isinstance(prompt, str) and prompt and len(prompt) <= 128 and "\n" not in prompt,
                "each starter prompt must be a non-empty single line of at most 128 characters")
        require("@" not in prompt, "starter prompts must not contain app @mentions")
        normalized = " ".join(unicodedata.normalize("NFKC", prompt).split()).casefold()
        require(normalized not in normalized_prompts, "starter prompts must be unique")
        normalized_prompts.add(normalized)

    for field in ("websiteURL", "privacyPolicyURL", "termsOfServiceURL"):
        require(valid_https_url(interface.get(field)), f"interface.{field} must be a valid public HTTPS URL")
    if "supportURL" in interface:
        require(valid_https_url(interface.get("supportURL")),
                "interface.supportURL must be a valid public HTTPS URL when provided")

    for field, background in (("brandColor", "#FFFFFF"), ("brandColorDark", "#212121")):
        color = interface.get(field)
        if color is None:
            continue
        require(isinstance(color, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", color) is not None,
                f"interface.{field} must be a six-digit hex color")
        require(contrast_ratio(color, background) >= 2.0,
                f"interface.{field} does not meet OpenAI's 2:1 contrast requirement")

    for field in ("logo", "composerIcon"):
        value = interface.get(field)
        require(isinstance(value, str) and value.startswith("./"), f"interface.{field} must start with ./")
        path = (ROOT / value[2:]).resolve()
        require(path.is_relative_to(ROOT) and path.is_file(), f"interface.{field} must reference a packaged file")
        require(path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".svg"},
                f"interface.{field} has an unsupported image type")
        require(path.stat().st_size <= 5 * 1024 * 1024, f"interface.{field} exceeds 5 MiB")
        if path.suffix.lower() == ".svg":
            width, height = svg_dimensions(path)
            require(width == height and width >= 48,
                    f"interface.{field} must be square and at least 48x48")

    require(manifest.get("skills") == "./skills/", "skills must point to ./skills/")
    require("apps" not in manifest and "mcpServers" not in manifest,
            "skills-only packages cannot declare apps or MCP servers")
    require("screenshots" not in interface, "skills-only packages cannot declare screenshots")


def parse_frontmatter(path: Path) -> tuple[str, str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n(.*)\Z", text, re.DOTALL)
    require(match is not None, f"{path.relative_to(ROOT)} needs closed YAML front matter")
    frontmatter, body = match.groups()
    name_match = re.search(r"^name:\s*(.+?)\s*$", frontmatter, re.MULTILINE)
    description_match = re.search(r"^description:\s*(.+?)\s*$", frontmatter, re.MULTILINE)
    require(name_match is not None, f"{path.relative_to(ROOT)} is missing name")
    require(description_match is not None, f"{path.relative_to(ROOT)} is missing description")
    name = name_match.group(1).strip().strip("'\"")
    raw_description = description_match.group(1).strip()
    if raw_description.startswith('"') and raw_description.endswith('"'):
        try:
            description = json.loads(raw_description)
        except json.JSONDecodeError as exc:
            raise PackageError(f"{path.relative_to(ROOT)} has malformed quoted description") from exc
    else:
        description = raw_description.strip("'")
    return name, description, body


def validate_skills(manifest: dict) -> list[Path]:
    skill_dirs = sorted(path for path in (ROOT / "skills").iterdir() if path.is_dir())
    require(skill_dirs, "skills/ must contain at least one skill")
    names: set[str] = set()
    for skill_dir in skill_dirs:
        require(not skill_dir.name.startswith("."), f"hidden skill directory is not allowed: {skill_dir.name}")
        skill_file = skill_dir / "SKILL.md"
        require(skill_file.is_file(), f"{skill_dir.relative_to(ROOT)} is missing SKILL.md")
        name, description, body = parse_frontmatter(skill_file)
        require(name and name not in names, f"duplicate or empty skill name: {name!r}")
        require(len(f"{manifest['name']}:{name}") <= 64, f"skill identity is too long: {name}")
        require(0 < len(description) <= 1024, f"skill description must be 1-1024 characters: {name}")
        require(body.strip(), f"skill body is empty: {name}")
        names.add(name)
    return skill_dirs


def add_tree(files: set[Path], base: Path, predicate) -> None:
    require(base.is_dir(), f"required directory is missing: {base.relative_to(ROOT)}")
    for path in base.rglob("*"):
        if not path.is_file() or not predicate(path):
            continue
        files.add(path)


def package_files(manifest: dict) -> list[Path]:
    files: set[Path] = {MANIFEST_PATH}
    for relative in REQUIRED_FILES:
        path = ROOT / relative
        require(path.is_file(), f"required file is missing: {relative}")
        files.add(path)

    add_tree(files, ROOT / "assets", lambda path: path.suffix.lower() in {".svg", ".png", ".jpg", ".jpeg", ".webp"})
    add_tree(
        files,
        ROOT / "skills",
        lambda path: path.relative_to(ROOT / "skills").parts[0]
        not in OPENAI_EXCLUDED_SKILLS,
    )
    add_tree(files, ROOT / "hooks", lambda path: path.name == "hooks.json")
    add_tree(files, ROOT / "agents", lambda path: path.suffix == ".md")
    add_tree(files, ROOT / "discipline", lambda path: path.suffix == ".md")
    # Ship the public documentation as a complete set. Skills and the stdlib
    # runtime cite docs/* directly; a package that validates only repository
    # existence can still strand an installed user with dead pointers.
    add_tree(files, ROOT / "docs", lambda path: path.suffix == ".md")
    add_tree(files, ROOT / "taskplane", lambda path: path.parent == ROOT / "taskplane" and path.suffix == ".py")
    add_tree(files, ROOT / "lenses", lambda path: path.suffix == ".md" or path.name == "catalog.json")

    for path in files:
        relative = path.relative_to(ROOT)
        require(not path.is_symlink(), f"symlinks are not allowed in the package: {relative}")
        require(path.is_file(), f"package member is not a regular file: {relative}")
        mode = path.stat(follow_symlinks=False).st_mode
        require(stat.S_ISREG(mode), f"package member is not a regular file: {relative}")
        require(path.stat().st_size <= 100 * 1024 * 1024, f"package member exceeds 100 MiB: {relative}")

    declared_assets = {
        (ROOT / manifest["interface"][field][2:]).resolve()
        for field in ("logo", "composerIcon")
    }
    require(declared_assets.issubset({path.resolve() for path in files}), "declared brand assets are not packaged")
    return sorted(files, key=lambda path: path.relative_to(ROOT).as_posix())


def write_zip(files: list[Path], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in files:
                relative = path.relative_to(ROOT).as_posix()
                info = zipfile.ZipInfo(f"{ARCHIVE_ROOT}/{relative}", FIXED_ZIP_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()


def validate_archive(path: Path) -> tuple[int, int]:
    require(path.is_file() and zipfile.is_zipfile(path), "output is not a readable ZIP")
    require(path.stat().st_size <= 100 * 1024 * 1024, "compressed ZIP exceeds 100 MB")
    normalized: set[str] = set()
    uncompressed = 0
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        require(0 < len(members) <= 5000, "ZIP must contain 1-5000 entries")
        roots: set[str] = set()
        names = {member.filename for member in members}
        for member in members:
            name = member.filename
            require(name and name == name.strip(), f"unsafe archive path: {name!r}")
            require("\\" not in name and not name.startswith("/"), f"unsafe archive path: {name}")
            pure = PurePosixPath(name)
            require(".." not in pure.parts and "" not in pure.parts, f"unsafe archive path: {name}")
            require(len(pure.parts) <= 20, f"archive path is deeper than 20 segments: {name}")
            require(not member.is_dir(), f"directory entries are unnecessary in the upload: {name}")
            require(member.flag_bits & 0x1 == 0, f"encrypted archive member is not allowed: {name}")
            key = unicodedata.normalize("NFC", name).casefold()
            require(key not in normalized, f"archive path normalization collision: {name}")
            normalized.add(key)
            roots.add(pure.parts[0])
            require(member.file_size <= 100 * 1024 * 1024, f"archive member exceeds 100 MiB: {name}")
            uncompressed += member.file_size
        require(roots == {ARCHIVE_ROOT}, "ZIP must have exactly one top-level taskplane/ directory")
        require(f"{ARCHIVE_ROOT}/.codex-plugin/plugin.json" in names, "ZIP is missing the Codex manifest")
        require(any(re.fullmatch(rf"{ARCHIVE_ROOT}/skills/[^/]+/SKILL\.md", name) for name in names),
                "ZIP has no valid skills/<skill>/SKILL.md")
        require(not any(name.endswith("/.app.json") or name.endswith("/.mcp.json") for name in names),
                "skills-only ZIP must not contain app or MCP configuration")
        require(not any(f"{ARCHIVE_ROOT}/.claude-plugin/" in name for name in names),
                "OpenAI upload must not contain the Claude manifest")
        require(not any(f"{ARCHIVE_ROOT}/skills/{skill}/" in name
                        for skill in OPENAI_EXCLUDED_SKILLS for name in names),
                "OpenAI upload must not contain host-specific Claude Tag skills")
        for required in ("README.md", "CHANGELOG.md"):
            require(f"{ARCHIVE_ROOT}/{required}" in names,
                    f"ZIP is missing {required}")
        doc_ref = re.compile(r"(?<![A-Za-z0-9_./-])(docs/[A-Za-z0-9_./-]+\.md)")
        referenced_docs: set[str] = set()
        source_prefixes = (f"{ARCHIVE_ROOT}/skills/",
                           f"{ARCHIVE_ROOT}/taskplane/",
                           f"{ARCHIVE_ROOT}/agents/",
                           f"{ARCHIVE_ROOT}/discipline/")
        for member in members:
            is_source = member.filename == f"{ARCHIVE_ROOT}/README.md" or \
                member.filename.startswith(source_prefixes)
            if not is_source or not member.filename.endswith((".md", ".py")):
                continue
            try:
                body = archive.read(member).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise PackageError(
                    f"referencing source is not UTF-8: {member.filename}") from exc
            referenced_docs.update(doc_ref.findall(body))
        missing_docs = sorted(
            rel for rel in referenced_docs
            if f"{ARCHIVE_ROOT}/{rel}" not in names)
        require(not missing_docs,
                "ZIP has dead docs references from shipped skills/runtime: "
                + ", ".join(missing_docs))
    require(uncompressed <= 512 * 1024 * 1024, "extracted ZIP exceeds 512 MiB")
    return len(members), uncompressed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    # D-0010 — same rule as the Claude archive; see scripts/release_provenance.py
    parser.add_argument("--allow-dirty", action="store_true",
                        help="package over uncommitted edits; the provenance "
                             "record is stamped verified_source: false and "
                             "the archive must not be released")
    args = parser.parse_args()

    try:
        manifest = load_manifest()
        validate_manifest(manifest)
        validate_skills(manifest)
        files = package_files(manifest)
        output_dir = args.output_dir.resolve()
        temporary_root = Path("/tmp").resolve()
        require(output_dir == ROOT / "dist" or output_dir.is_relative_to(ROOT) or output_dir.is_relative_to(temporary_root),
                "output directory must stay inside the repository or /tmp")
        output = output_dir / f"{manifest['name']}-{manifest['version']}-openai.zip"
        write_zip(files, output)
        member_count, uncompressed = validate_archive(output)
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        checksum = output.with_suffix(output.suffix + ".sha256")
        checksum.write_text(f"{digest}  {output.name}\n", encoding="utf-8")
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import release_provenance as prov
        try:
            prov_path = prov.write(ROOT, output, digest,
                                   allow_dirty=args.allow_dirty)
        except prov.ProvenanceError as exc:
            output.unlink(missing_ok=True)
            checksum.unlink(missing_ok=True)
            raise PackageError(str(exc)) from exc
    except (OSError, PackageError) as exc:
        print(f"OpenAI package validation failed: {exc}", file=sys.stderr)
        return 1

    print(f"OpenAI package ready: {output}")
    print(f"version: {manifest['version']} (unchanged)")
    print(f"files: {member_count}")
    print(f"compressed_bytes: {output.stat().st_size}")
    print(f"uncompressed_bytes: {uncompressed}")
    print(f"sha256: {digest}")
    print(f"checksum: {checksum}")
    print(f"provenance: {prov_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
