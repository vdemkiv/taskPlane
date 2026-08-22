"""Dependency-free graph identities, edge semantics, and context records.

This module is the single lower-layer contract shared by the graph scanner,
component decomposition, and lens routing.  It intentionally knows nothing
about graph persistence, scanning, decomposition, or lens execution.
"""
from __future__ import annotations

import json
import posixpath
import re


# Directory names that mark source layout rather than component identity.
_SRC_ROOTS = ("src", "app", "lib", "packages", "pkg", "internal", "cmd")
_GO_MODULE_LINE = re.compile(r"^\s*module\s+(\S+)", re.M)
_ID_PREFIXES = ("ext:", "svc:", "req:", "contract:", "resource:")

# Reserved manifest-map key for the repository's own Go module path.  The
# leading NUL cannot collide with a real repository directory.
ROOT_MODULE_KEY = "\x00root_module"

# Edges whose direction means "from NEEDS to".  Structural and annotation
# edges deliberately do not answer dependent/blast-radius questions.
DEPENDENCY_EDGE_KINDS = frozenset({
    "imports", "depends_on", "consumes", "depends", "calls", "uses",
})

_FIXTURE_SEGMENTS = frozenset({"fixtures", "testdata", "goldens"})
_GRAPH_LOADER = None
_LENS_ROUTER = None


def register_graph_loader(loader) -> None:
    """Register the scanner-owned read boundary used by lens projection."""
    global _GRAPH_LOADER
    _GRAPH_LOADER = loader


def load_graph(workspace: str) -> dict:
    """Read through the registered persistence boundary.

    Persistence remains owned by depgraph; this lower layer only holds the
    injected callable, which keeps lens routing from importing the scanner.
    """
    if _GRAPH_LOADER is None:
        raise RuntimeError("graph loader is not registered")
    return _GRAPH_LOADER(workspace)


def register_lens_router(router) -> None:
    """Register the detector-owned component lens-map boundary."""
    global _LENS_ROUTER
    _LENS_ROUTER = router


def lens_router_registered() -> bool:
    """Whether the composition root has activated the detector boundary."""
    return _LENS_ROUTER is not None


def route_verdicts(*args, **kwargs):
    """Invoke the registered lens router without importing its owner."""
    if _LENS_ROUTER is None:
        raise RuntimeError("lens router is not registered")
    return _LENS_ROUTER(*args, **kwargs)


def is_fixture_module(module: str) -> bool:
    """Whether a module boundary itself is fixture-classed."""
    value = str(module or "").replace("\\", "/").strip("/")
    if not value or value == "(root)":
        return False
    return (any(part.lower() in _FIXTURE_SEGMENTS
                for part in value.split("/") if part)
            or value.lower().endswith(".golden"))


def manifest_modules(files, read) -> dict:
    """Return ``{directory: declared import identity}`` for owned manifests.

    Only package.json names and go.mod module paths are import identities.
    Root package manifests describe the repository, while a root go.mod path
    is retained solely as an import prefix under ``ROOT_MODULE_KEY``.
    """
    out: dict = {}
    for rel in sorted(files or ()):
        rel = str(rel).replace("\\", "/")
        base = posixpath.basename(rel)
        if base not in ("package.json", "go.mod"):
            continue
        directory = posixpath.dirname(rel)
        if not directory:
            if base == "go.mod":
                match = _GO_MODULE_LINE.search(read(rel) or "")
                if match and match.group(1) and "/" in match.group(1):
                    out[ROOT_MODULE_KEY] = match.group(1).strip()
            continue
        text = read(rel)
        if not text:
            continue
        declared = None
        if base == "package.json":
            try:
                data = json.loads(text)
            except (ValueError, TypeError):
                continue
            if isinstance(data, dict) and isinstance(data.get("name"), str):
                declared = data["name"].strip()
        else:
            match = _GO_MODULE_LINE.search(text)
            declared = match.group(1).strip() if match else None
        if not declared or declared.startswith(_ID_PREFIXES):
            continue
        out[directory] = declared.replace("\\", "/").strip("/")
    return {key: value for key, value in out.items() if value}


def declared_module_ids(graph: dict | None) -> dict:
    """Return the manifest identity map persisted by the last graph scan."""
    return ((graph or {}).get("meta") or {}).get("module_ids") or {}


def root_module(declared_ids) -> "str | None":
    """Return the repository Go module prefix when held in map form."""
    if isinstance(declared_ids, dict):
        return declared_ids.get(ROOT_MODULE_KEY) or None
    return None


def strip_root_prefix(spec: str, root: "str | None") -> "str | None":
    """Map ``<root>/pkg/x`` to the repository-relative ``pkg/x``."""
    if not root:
        return None
    spec = str(spec or "").replace("\\", "/").strip("/")
    if spec == root:
        return None
    if spec.startswith(root + "/"):
        return spec[len(root) + 1:] or None
    return None


def _strip_root_module(spec: str, declared_ids) -> "str | None":
    return strip_root_prefix(spec, root_module(declared_ids))


def _declared_target(spec: str, declared_ids) -> "str | None":
    """Return the longest declared module matching an import specifier."""
    if not declared_ids:
        return None
    spec = str(spec or "").replace("\\", "/").strip("/")
    while spec:
        if spec in declared_ids and spec != ROOT_MODULE_KEY:
            return spec
        if "/" not in spec:
            return None
        spec = spec.rsplit("/", 1)[0]
    return None


def module_of(relpath: str, manifests: dict | None = None) -> str:
    """Return the stable module identity owning a repository-relative path."""
    relpath = str(relpath or "").replace("\\", "/")
    directory = posixpath.dirname(relpath)
    if manifests:
        probe = directory
        while probe:
            hit = manifests.get(probe)
            if hit:
                return hit
            parent = posixpath.dirname(probe)
            if parent == probe:
                break
            probe = parent
    if not directory:
        return "(root)"
    parts = directory.split("/")
    for marker in (("src", "main", "java"), ("src", "test", "java")):
        for index in range(0, len(parts) - len(marker) + 1):
            if tuple(parts[index:index + len(marker)]) == marker:
                package = parts[index + len(marker):]
                if package:
                    return "/".join(package[-3:])
    kept = [part for part in parts if part not in _SRC_ROOTS]
    return "/".join(kept[:2]) if kept else parts[-1]


def node_kind(node: str) -> str:
    """Return the public graph-node family for an identifier."""
    if node.startswith("req:"):
        return "requirement"
    if node.startswith("contract:"):
        return "contract"
    if node.startswith("resource:"):
        return "resource"
    if node.startswith("svc:"):
        return "infra"
    if node.startswith("ext:"):
        return "external"
    return "module"


def is_boundary(node: str) -> bool:
    return node.startswith(("contract:", "resource:", "svc:", "ext:"))


def is_dependency_edge(edge: dict) -> bool:
    """Whether ``edge`` expresses the dependency direction from NEEDS to."""
    try:
        return edge.get("kind") in DEPENDENCY_EDGE_KINDS
    except AttributeError:
        return False


def graph_payload(graph: dict, modules,
                  *, fixture_module_predicate=None) -> dict:
    """Project raw graph data into the shared lens/component context record.

    ``hub_dependents`` intentionally counts every incoming edge for backward
    compatibility. ``module_dependents`` is stricter: only dependency edges,
    excluding self-dependence and fixture-class witnesses.  The result stays
    an ordinary dict so all existing JSON and caller payloads remain stable.
    """
    selected = sorted({str(module) for module in modules or () if module})
    selected_set = set(selected)
    incoming: dict[str, set] = {}
    dependency_incoming: dict[str, set] = {}
    contracts: set[str] = set()
    fixture_module_predicate = (
        fixture_module_predicate or (lambda _value: False))

    for edge in (graph or {}).get("edges") or []:
        if not isinstance(edge, dict):
            continue
        source, target = edge.get("from"), edge.get("to")
        if not source or not target:
            continue
        incoming.setdefault(target, set()).add(source)
        if is_dependency_edge(edge):
            dependency_incoming.setdefault(target, set()).add(source)
        for left, right in ((source, target), (target, source)):
            if left in selected_set and str(right).startswith("contract:"):
                contracts.add(str(right))

    return {
        "hub_dependents": max(
            (len(incoming.get(module, ())) for module in selected), default=0),
        "boundary_contracts": sorted(contracts),
        "modules": selected,
        "module_ids": declared_module_ids(graph),
        "module_dependents": {
            module: len([
                dependent
                for dependent in dependency_incoming.get(module, ())
                if dependent != module
                and not fixture_module_predicate(str(dependent))
            ])
            for module in selected
        },
    }


# Compatibility aliases retained for depgraph's historically internal names.
_node_kind = node_kind
_is_boundary = is_boundary
