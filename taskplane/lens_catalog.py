"""Dependency-neutral access to the shipped lens catalog.

Reading output identities must not import routing or native execution.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

_CATALOG_CACHE: dict[str, Any] | None = None


def load_catalog(root: str | None = None) -> dict[str, Any]:
    global _CATALOG_CACHE
    if _CATALOG_CACHE is not None and root is None:
        return _CATALOG_CACHE
    plugin_root = Path(root) if root else Path(__file__).resolve().parent.parent
    with (plugin_root / "lenses" / "catalog.json").open(encoding="utf-8") as stream:
        catalog = cast(dict[str, Any], json.load(stream))
    if root is None:
        _CATALOG_CACHE = catalog
    return catalog
