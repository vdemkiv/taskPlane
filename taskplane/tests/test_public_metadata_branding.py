from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PUBLIC_ROOTS = (
    ROOT / "README.md",
    ROOT / "CHANGELOG.md",
    ROOT / ".codex-plugin",
    ROOT / ".claude-plugin",
    ROOT / "agents",
    ROOT / "skills",
    ROOT / "docs",
    ROOT / "lenses",
    ROOT / "discipline",
    ROOT / "hooks",
)
TEXT_SUFFIXES = {".json", ".md", ".yaml", ".yml"}
FORBIDDEN_PRODUCT_REFERENCES = (
    "conductor",
    "supaconductor",
    "superconductor",
)


def _public_text_files():
    for root in PUBLIC_ROOTS:
        if root.is_file():
            yield root
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
                yield path


def test_public_metadata_describes_taskplane_without_competitor_references():
    violations = []
    for path in _public_text_files():
        body = path.read_text(encoding="utf-8").casefold()
        matches = [
            token for token in FORBIDDEN_PRODUCT_REFERENCES if token in body
        ]
        if matches:
            violations.append(
                f"{path.relative_to(ROOT)}: {', '.join(matches)}")
    assert not violations, (
        "public Taskplane metadata must describe Taskplane alone; exact external "
        "namespaces belong only in internal collision enforcement and tests:\n" +
        "\n".join(violations)
    )
