"""Dependency-free language reference and implementation inventory."""
from __future__ import annotations

import copy
import hashlib
import os
from collections.abc import Sequence
from typing import Any


LANGUAGE_REFERENCES: dict[str, dict[str, Any]] = {
    "go": {
        "extensions": (".go",), "manifests": ("go.mod", "go.sum"),
        "references": {
            "code-quality": {"path": "lenses/references/go-code-quality.md"},
            "solution-design": {"path": "lenses/references/go-solution-design.md"},
            "architecture": {"path": "lenses/references/go-engineering.md", "section": "Architecture"},
            "backend": {"path": "lenses/references/go-engineering.md", "section": "Backend"},
            "sre": {"path": "lenses/references/go-engineering.md", "section": "SRE"},
            "security": {"path": "lenses/references/go-engineering.md", "section": "Security"},
            "qa": {"path": "lenses/references/go-engineering.md", "section": "QA"},
            "testability": {"path": "lenses/references/go-engineering.md", "section": "Testability"},
            "scalability": {"path": "lenses/references/go-engineering.md", "section": "Scalability"},
            "integrability": {"path": "lenses/references/go-engineering.md", "section": "Integrability"},
            "data-safety": {"path": "lenses/references/go-engineering.md", "section": "Data safety"},
        },
    },
    "python": {
        "extensions": (".py",),
        "manifests": ("pyproject.toml", "requirements.txt", "setup.py"),
        "references": {
            "code-quality": {"path": "lenses/references/python-code-quality.md"},
            "solution-design": {"path": "lenses/references/python-solution-design.md"},
            "scalability": {"path": "lenses/references/python-engineering.md", "section": "Scalability"},
            "qa": {"path": "lenses/references/python-engineering.md", "section": "QA"},
            "testability": {"path": "lenses/references/python-engineering.md", "section": "Testability"},
            "devops": {"path": "lenses/references/python-engineering.md", "section": "Packaging and DevOps"},
            "integrability": {"path": "lenses/references/python-engineering.md", "section": "Integrability"},
            "security": {"path": "lenses/references/python-engineering.md", "section": "Security"},
            "sre": {"path": "lenses/references/python-engineering.md", "section": "SRE"},
        },
    },
    "typescript": {
        "extensions": (".ts", ".tsx"), "manifests": ("tsconfig.json",),
        "references": {
            "code-quality": {"path": "lenses/references/typescript-code-quality.md"},
            "solution-design": {"path": "lenses/references/typescript-solution-design.md"},
            "integrability": {"path": "lenses/references/typescript-engineering.md", "section": "Integrability"},
            "devops": {"path": "lenses/references/typescript-engineering.md", "section": "DevOps"},
            "scalability": {"path": "lenses/references/typescript-engineering.md", "section": "Scalability"},
            "architecture": {"path": "lenses/references/typescript-engineering.md", "section": "Architecture"},
            "frontend": {"path": "lenses/references/typescript-engineering.md", "section": "Frontend async"},
            "security": {"path": "lenses/references/typescript-engineering.md", "section": "Security"},
        },
    },
}

IMPLEMENTATION_EXTENSIONS = {
    ".go": "go", ".py": "python", ".ts": "typescript",
    ".tsx": "typescript", ".rs": "rust", ".java": "java",
    ".kt": "kotlin", ".kts": "kotlin", ".c": "c", ".h": "c",
    ".cc": "cpp", ".cpp": "cpp", ".cs": "csharp", ".js": "javascript",
    ".jsx": "javascript", ".rb": "ruby", ".php": "php", ".swift": "swift",
}


def detected_languages(files: Sequence[object] | None) -> list[str]:
    """Return languages declared by file extensions or root/build manifests."""
    paths = [str(path).replace("\\", "/") for path in files or ()]
    found = []
    for language, spec in LANGUAGE_REFERENCES.items():
        extensions = tuple(str(item) for item in spec["extensions"])
        manifests = tuple(str(item) for item in spec["manifests"])
        if any(path.lower().endswith(extensions) or
               os.path.basename(path).lower() in manifests for path in paths):
            found.append(language)
    return found


def implementation_languages(files: Sequence[object] | None) -> list[str]:
    """Return every recognizable impacted implementation language."""
    found = set()
    for raw in files or ():
        extension = os.path.splitext(str(raw).replace("\\", "/").lower())[1]
        language = IMPLEMENTATION_EXTENSIONS.get(extension)
        if language:
            found.add(language)
    return sorted(found)


def _reference_record(
        language: str, lens_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    path = str(spec["path"]).replace("\\", "/")
    plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    absolute = os.path.realpath(os.path.join(plugin_root, *path.split("/")))
    if os.path.commonpath((os.path.realpath(plugin_root), absolute)) != \
            os.path.realpath(plugin_root) or not os.path.isfile(absolute):
        raise FileNotFoundError(f"language reference is missing or unsafe: {path}")
    with open(absolute, "rb") as stream:
        content = stream.read()
    section = str(spec.get("section") or "")
    if section and "## " + section not in content.decode(
            "utf-8", errors="replace").splitlines():
        raise ValueError(f"language reference section is missing: {path}#{section}")
    row = {
        "language": language, "lens": lens_id, "path": path,
        "content_sha256": hashlib.sha256(content).hexdigest(),
    }
    if section:
        row["section"] = section
    return row


def language_references(
        files: Sequence[object] | None, task_type: str | None = None,
        lens_ids: Sequence[object] | None = None) -> list[dict[str, Any]]:
    """Resolve scoped, content-bound references from repository paths."""
    wanted = ({"solution-design"} if task_type == "solution-design" else
              ({str(item) for item in lens_ids} if lens_ids is not None else
               {"code-quality"}))
    refs = []
    for language in detected_languages(files):
        references = LANGUAGE_REFERENCES[language]["references"]
        if not isinstance(references, dict):
            raise ValueError("language reference registry is invalid")
        for lens_id, reference in references.items():
            if lens_id in wanted:
                if not isinstance(reference, dict):
                    raise ValueError("language reference is invalid")
                refs.append(_reference_record(language, lens_id, reference))
    return sorted(refs, key=lambda row: (
        str(row["language"]), str(row["lens"]), str(row["path"]),
        str(row.get("section", ""))))


def language_quality_registry(
        files: Sequence[object] | None) -> list[dict[str, Any]]:
    """Resolve one content-bound code-quality reference per language."""
    languages = implementation_languages(files)
    if not languages:
        raise ValueError("impacted implementation language inventory is empty")
    unsupported = [name for name in languages if name not in LANGUAGE_REFERENCES]
    if unsupported:
        raise ValueError(
            "unsupported impacted implementation language: " +
            ", ".join(unsupported))
    rows = language_references(files, lens_ids=["code-quality"])
    by_language: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_language.setdefault(str(row["language"]), []).append(row)
    missing = [name for name in languages if not by_language.get(name)]
    duplicate = [name for name in languages if len(by_language.get(name, [])) > 1]
    if missing:
        raise ValueError("missing language quality reference: " + ", ".join(missing))
    if duplicate:
        raise ValueError(
            "duplicate language quality reference: " + ", ".join(duplicate))
    return [copy.deepcopy(by_language[name][0]) for name in languages]
