"""Dependency graph — persistent component/module/infra map in the KB.

Why: analysing a rich codebase (or one large PR) burns tokens re-deriving
"what depends on what" every single time. So taskplane stores the graph
alongside the knowledge base (`knowledge/graph.json`) and keeps it current
**deterministically** — static scanners, zero LLM cost — with agent-recorded
edges for what static analysis can't see (runtime calls, queues, infra).

  - scan(ws)            build/refresh the graph (incremental by file hash)
  - impact(ws, files)   change → impacted modules (reverse-dependency BFS,
                        with depth), the review's blast radius
  - record_edge(ws,...) agent-observed edge (kind: runtime/queue/deploys/…)
  - render_context(...) token-lean injection for loop steps
  - to_html(ws, ...)    self-contained interactive visualization

Nodes are MODULES (directory-level, e.g. `src/auth`) plus INFRA components
(docker-compose services) and EXTERNAL packages. Pure stdlib.
"""

from __future__ import annotations

import ast
import base64
import contextlib
import contextvars
import copy
import gzip
import hashlib
import subprocess
import sys as _sys
import json
import posixpath
import os
import re
import time

import graph_decomposition
import glob_match
import graph_primitives
import storage as runtime_storage
import taskplane_lite as tp

GRAPH_FILE = "graph.json"
CODE_EXT = (".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".go",
            ".cs", ".java", ".rb")
# ------------------------------------------------------------------ artifacts
#
# D-0016. CODE_EXT decided what EXISTS, not just what gets parsed for imports —
# so a repository whose product is not source code was invisible. taskplane's
# own skills, agents and lenses are markdown and declarative JSON; the graph
# contained none of them, and neither did decomposition, which is what the
# review depends on to know where to look. The accuracy harness scored the
# plugin profile at 0% module recall for a repo where nothing was missing
# except the file extensions.
#
# These files ARE the product. They carry no import statements, so they get a
# node and a file count and are never handed to an import parser.
#
# The list is deliberately short and excludes BUILD DESCRIPTORS (.toml, .xml,
# .cfg, .ini, .lock). A pom.xml or a pyproject.toml describes how the code is
# assembled — it is not a thing the code depends on, and admitting them minted
# a `main/resources` module out of a Java DI file in the corpus. Where a
# manifest matters it is already read for module IDENTITY (see
# `manifest_modules`), which is a different job.
ARTIFACT_EXT = (".md", ".json", ".yml", ".yaml", ".sql", ".tf")
# Non-git fallback skip list (in a git work tree the scan enumerates via
# `git ls-files`, which honors .gitignore). Covers vendored and build trees
# (vendor/ for Go, target/ for Rust/Java) so third-party code never becomes
# graph modules and pollutes blast radius.
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".taskplane", ".tp-work",
             "venv", ".venv", "dist", "build", "target", "vendor",
             ".tox", ".mypy_cache", ".pytest_cache", ".eval", ".em-review",
             ".security-review"}


def _path(ws: str) -> str:
    locator = runtime_storage.load_workspace_locator(ws)
    if locator:
        return os.path.join(locator["paths"]["graph"], GRAPH_FILE)
    # Legacy/non-run graph lives in the external per-project knowledge store.
    return os.path.join(tp.kb_root(ws), GRAPH_FILE)


def _empty() -> dict:
    return {"modules": {}, "edges": [], "files": {}, "recorded": [],
            "meta": {}}


# Corruption blocks gates (fail-closed) WITH this remedy. It must steer the
# operator to inspect/restore, never to delete-and-rescan: a re-scan only
# rebuilds SCANNED edges — agent-recorded manual edges (runtime/queue/deploy
# relationships, req: links) live only in this file's "recorded" section and
# would be silently lost, shrinking every future review's blast radius.
_CORRUPT_REMEDY = (
    "inspect or restore graph.json in the knowledge store (from a backup/"
    "snapshot, or git if the store is versioned). Do NOT delete it and "
    "re-scan: a re-scan only rebuilds scanned edges — recorded manual edges "
    "live in this file's 'recorded' section and would be lost")


# Per-process read memo: graph.json is parsed by many consumers per command
# (impact, product_impact, summary, hub_signal, design context …); each parse
# of a multi-MB file is pure waste when nothing changed. The memo is validated
# against the file's stat signature on EVERY load, so there is no cross-process
# cache: another process's atomic save (os.replace → new inode/mtime) is
# always picked up. save() refreshes the entry.
_GRAPH_CACHE: dict[str, tuple] = {}
# Active batched mutations: abs graph path -> the in-flight graph dict.
# See batch().
_BATCH: dict[str, dict] = {}
_SCANNER_CACHE_VERSION: dict[bool, str] = {}
_STRICT_GRAPH_QUALITY = contextvars.ContextVar(
    "taskplane_strict_graph_quality", default=False)

GRAPH_SCAN_QUALITY_SCHEMA = "taskplane.graph-scan-quality/v1"
GRAPH_SCAN_RECOVERY = (
    "repair the named source/producer and rerun `tp graph scan --strict`")
ARCHITECTURE_MAP_SCHEMA = "taskplane.architecture-map-proof/v1"
ARCHITECTURE_MAX_BYTES = 1024 * 1024
ARCHITECTURE_MAX_NODES = 512
ARCHITECTURE_MAX_EDGES = 2048
DESIGN_ARCHITECTURE_SCHEMA = "taskplane.design-architecture-map/v1"
ARCHITECTURE_AUTHORITY_FLOOR_SCHEMA = \
    "taskplane.architecture-authority-floor/v1"
CURRENT_GRAPH_AUTHORITY_FLOOR_SCHEMA = \
    "taskplane.current-graph-authority-floor/v1"
SEMANTIC_ENDPOINT_REGISTRY_SCHEMA = \
    "taskplane.semantic-endpoint-registry/v1"
_ARCHITECTURE_MAP_KEYS = frozenset({
    "schema", "decision_record", "scanner_input", "scanner_rule", "nodes",
    "required_properties", "required_singleton_sccs", "semantic_edges",
    "content_fingerprint",
})
_ARCHITECTURE_NODE_KEYS = frozenset({"id", "kind", "path_globs"})
_ARCHITECTURE_NODE_KINDS = frozenset({
    "external-host", "existing", "new", "producer", "test", "file",
    "contract", "resource",
})
_ARCHITECTURE_REQUIRED_PROPERTIES = frozenset({
    "native-authority, design-sweep, and terminal-truth owners are singleton SCCs",
    "no new owner imports or invokes a host transport or transition adapter",
    "governance adapters reach Codex only through contract:delivery.codex-native-dispatch",
    "all eight surface producers reach the coordinator through contract:delivery.exact-sha-terminal-truth",
    "tests observe every node and every declared production edge",
})
_SEMANTIC_EDGE_KEYS = frozenset({"from", "to", "kind", "reason"})
_SEMANTIC_EDGE_KINDS = frozenset({
    "blocks", "bound-by", "calls", "catalog-input", "changes",
    "completion-attention", "consumed-by", "consumes", "coordinated-by",
    "depends", "depends_on", "evidence", "handoff", "imports", "intent",
    "observed-by", "produces", "projects", "provides", "requires",
    "transported-by", "uses", "validated-by", "verified-by",
})
_GRAPH_NODE_ID = re.compile(r"^[A-Za-z0-9._/-]+(?::[A-Za-z0-9._/-]+)*$")

# These floors are engine-owned copies of the two separately approved graph
# authorities consumed by the R-0002 review.  A digest carried by
# design/contract.json proves only internal consistency: a caller could
# otherwise delete every row and recompute it.  The decision/requirement keyed
# floors pin both accepted sets independently, including row content and
# ordering, while the set digests make identity drift explicit in diagnostics.
_ARCHITECTURE_AUTHORITY_FLOORS = {
    "D-R0013-native-adapter-quarantine": {
        "schema": ARCHITECTURE_AUTHORITY_FLOOR_SCHEMA,
        "content_fingerprint":
            "2ce2f31148d4078d64f62de89b8eff9a902693b68395773f53b5371623030ebc",
        "node_count": 14,
        "node_set_fingerprint":
            "3d98e052e20e872af075cb337589fc51c10dc2fb4f8609342a1eb41a40310280",
        "semantic_edge_count": 24,
        "semantic_edge_set_fingerprint":
            "605ea7d0927748f945477d32048a1e641d7b0a1441992ac1fd0e4b36c6d6325b",
        "singleton_sccs": frozenset({
            "component:native-authority-validator",
            "component:design-sweep-validator",
            "component:terminal-truth-coordinator",
        }),
    },
}
_CURRENT_GRAPH_AUTHORITY_FLOORS = {
    "R-0002": {
        "schema": CURRENT_GRAPH_AUTHORITY_FLOOR_SCHEMA,
        "edge_count": 23,
        "edge_fingerprint":
            "d79577ead44054407fbc767fb86a40c5f61da79f84811dfa8d93328f8c5b3d4c",
        "edge_set_fingerprint":
            "09d2b45bc0196ed898120235a7c949b76f4cc81e479b86b7366d856b9b3d5748",
    },
}

# Versioned, closed semantic boundary vocabulary for the two authorities
# above.  Prefix syntax is not registration: every ext:/contract:/resource:/
# svc:/req:/component:/surface: endpoint must appear here before it can enter
# a production graph.
_SEMANTIC_ENDPOINT_REGISTRY = frozenset({
    "component:design-sweep-validator",
    "component:native-authority-validator",
    "component:r0013-contract-tests",
    "component:taskplane-governance-adapters",
    "component:terminal-truth-coordinator",
    "contract:ci.reproducible-python-quality",
    "contract:dashboard.accessible-truthful-actions",
    "contract:delivery.acceptance-wave-ceiling",
    "contract:delivery.bounded-stage-handoff",
    "contract:delivery.codex-native-dispatch",
    "contract:delivery.event-driven-wait",
    "contract:delivery.exact-sha-terminal-truth",
    "contract:delivery.execution-zero-lens",
    "contract:delivery.production-wiring",
    "contract:design.codex-native-capability-inventory",
    "contract:design.quick-concurrent-all-lens-sweep",
    "contract:docs.generated-truth",
    "contract:i18n.locale-and-grapheme",
    "contract:privacy.retention-and-disclosure",
    "contract:quality.review-remediation",
    "contract:release.compatibility-and-authority",
    "contract:review.high-closure-gate",
    "contract:runtime.durable-state-and-authority",
    "contract:runtime.scoped-dependency-binding",
    "ext:codex-native-orchestration",
    "resource:exports.exact-sha-terminal-truth",
    "resource:review.exact-candidate-evidence",
    "resource:review.finding-traceability",
    "surface:exports-terminal-evidence",
    "surface:git-head",
    "surface:governed-progress",
    "surface:public-report",
    "surface:release-evidence",
    "surface:repository-verification-report",
    "surface:run-journal",
    "surface:tasks-and-gates",
})
_SEMANTIC_ENDPOINT_REGISTRY_FINGERPRINT = \
    "3756cfb3f83c1d7ac5d024c7bd4672b7e61dd0e3226818327aa286b6c8ba5053"


class GraphQualityDegraded(RuntimeError):
    """A strict graph consumer refused the persisted producer record."""


def _fingerprinted_scan_quality(record: dict) -> dict:
    """Bind graph-scan quality to canonical material, excluding itself."""
    material = copy.deepcopy(record)
    material.pop("fingerprint", None)
    digest = hashlib.sha256(json.dumps(
        material, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode("utf-8")).hexdigest()
    material["fingerprint"] = digest
    return material


def scan_quality(graph: dict) -> dict:
    """Return the canonical producer-complete graph scan quality record."""
    raw = ((graph or {}).get("meta") or {}).get("graph_scan_quality")
    if isinstance(raw, dict) and raw.get("schema") == \
            GRAPH_SCAN_QUALITY_SCHEMA:
        return _fingerprinted_scan_quality(raw)
    return _fingerprinted_scan_quality({
        "schema": GRAPH_SCAN_QUALITY_SCHEMA,
        "degraded": False,
        "mode": "modules",
        "scanned_revision": str(((graph or {}).get("meta") or {}).get(
            "scanned_head") or ""),
        "affected_modules": [],
        "failures": [],
        "producers": {
            "base-scanner": {"status": "complete", "failures": []},
            "decomposition": {"status": "not-requested", "failures": []},
            "architecture-map": {"status": "not-requested", "failures": []},
        },
        "recovery": GRAPH_SCAN_RECOVERY,
    })


def quality_errors(graph: dict) -> list[str]:
    """Human-actionable errors for every strict consumer of one record."""
    quality = scan_quality(graph)
    if not quality.get("degraded"):
        return []
    details = []
    for row in quality.get("failures") or []:
        details.append(
            f"{row.get('producer', 'unknown')} {row.get('module', '?')} "
            f"{row.get('file', '?')} {row.get('error_class', 'error')}: "
            f"{row.get('reason', 'unknown reason')}")
    suffix = "; ".join(details) or "producer reported degradation"
    return [f"graph scan quality is degraded: {suffix} — "
            f"{quality.get('recovery') or GRAPH_SCAN_RECOVERY}"]


def require_quality(graph: dict) -> None:
    errors = quality_errors(graph)
    if errors:
        raise GraphQualityDegraded(errors[0])


@contextlib.contextmanager
def strict_quality():
    """Make nested graph scans fail after persisting their quality record."""
    token = _STRICT_GRAPH_QUALITY.set(True)
    try:
        yield
    finally:
        _STRICT_GRAPH_QUALITY.reset(token)


def _stat_sig(p: str):
    st = os.stat(p)
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def scanner_cache_version(*, decompose: bool = False) -> str:
    """Content identity of this scanner plus its requested graph layer."""
    cached = _SCANNER_CACHE_VERSION.get(bool(decompose))
    if cached is None:
        try:
            sources = [__file__, graph_primitives.__file__]
            if decompose:
                sources.extend((
                    graph_decomposition.__file__,
                    os.path.join(os.path.dirname(__file__),
                                 "lens_signals.py"),
                ))
            digest = hashlib.sha256()
            for source in sources:
                with open(source, "rb") as handle:
                    digest.update(handle.read())
            cached = digest.hexdigest()[:16]
        except OSError:
            cached = "unavailable"
        _SCANNER_CACHE_VERSION[bool(decompose)] = cached
    return f"{cached}-{'components' if decompose else 'modules'}"


def _managed_cache_path(ws: str, *, decompose: bool) -> tuple[str, str] | None:
    locator = runtime_storage.load_workspace_locator(ws)
    if not locator:
        return None
    head = tp.git_head(ws)
    if not head:
        return None
    path = os.path.join(
        locator["home"], "cache", "graphs", locator["repository_key"],
        head, f"{scanner_cache_version(decompose=decompose)}.json")
    return path, head


def _restore_managed_cache(ws: str, *, decompose: bool) -> dict | None:
    located = _managed_cache_path(ws, decompose=decompose)
    if not located or os.path.exists(_path(ws)):
        return None
    path, head = located
    try:
        value = tp.load_json(path, default=None,
                             what="managed dependency graph cache")
    except tp.StateError:
        return None
    if not isinstance(value, dict) or value.get("schema") != \
            "taskplane.graph-cache/v1" or value.get("head") != head or \
            value.get("scanner_version") != scanner_cache_version(
                decompose=decompose) or not isinstance(value.get("graph"),
                                                       dict) or \
            value.get("components_fingerprint") != \
            _components_file_fingerprint(ws) or \
            value.get("design_fingerprint") != _design_file_fingerprint(ws):
        return None
    graph = value["graph"]
    save(ws, graph)
    return graph


def _write_managed_cache(ws: str, graph: dict, *, decompose: bool) -> None:
    located = _managed_cache_path(ws, decompose=decompose)
    if not located:
        return
    path, head = located
    tp.atomic_write_json(path, {
        "schema": "taskplane.graph-cache/v1", "head": head,
        "scanner_version": scanner_cache_version(decompose=decompose),
        "components_fingerprint": _components_file_fingerprint(ws),
        "design_fingerprint": _design_file_fingerprint(ws),
        "graph": graph,
    }, indent=1, sort_keys=True)


def load(ws: str) -> dict:
    """Read the graph. Missing file → a legitimate empty default (a project
    that was never scanned has no graph). Corrupt file → StateError with a
    remedy, NEVER a silent empty rebuild: an empty graph would weaken graph
    DoR/DoD gating and silently shrink every review's blast radius.

    Callers must treat the returned dict as READ-ONLY — it may be a shared
    per-process memo. Mutate only via scan/record_edge/link_requirement or
    inside a batch() block."""
    p = os.path.abspath(_path(ws))
    if p in _BATCH:                       # a batch sees its own mutations
        return _BATCH[p]
    try:
        sig = _stat_sig(p)
    except OSError:
        return _empty()
    hit = _GRAPH_CACHE.get(p)
    if hit is not None and hit[0] == sig:
        return hit[1]
    try:
        g = tp.load_json(p, default=None,
                         what="dependency graph (graph.json)")
    except tp.StateError:
        raise tp.StateError(p, "corrupt dependency graph (graph.json)",
                            _CORRUPT_REMEDY) from None
    if g is None:
        return _empty()
    if not isinstance(g, dict):
        raise tp.StateError(
            p, "corrupt dependency graph (not a JSON object)",
            _CORRUPT_REMEDY)
    g.setdefault("modules", {})
    g.setdefault("edges", [])
    g.setdefault("files", {})
    g.setdefault("meta", {})
    g.setdefault("recorded", [])
    _GRAPH_CACHE[p] = (sig, g)
    return g


# Lens projection reads graph state through the persistence owner's callable,
# keeping lens_signals below depgraph without copying path/store semantics.
graph_primitives.register_graph_loader(lambda workspace: load(workspace))


def save(ws: str, g: dict) -> None:
    """Atomic write (tmp + os.replace, same pattern as loop.save) — a
    concurrent reader never sees a torn graph.json mid-write."""
    p = os.path.abspath(_path(ws))
    tp.atomic_write_json(p, g, indent=1, sort_keys=True)
    try:
        _GRAPH_CACHE[p] = (_stat_sig(p), g)
    except OSError:
        _GRAPH_CACHE.pop(p, None)


@contextlib.contextmanager
def batch(ws: str):
    """One locked load → N in-memory mutations → ONE atomic flush.

    Callers that record many edges in one command (e.g. the plan gate's
    per-requirement/per-contract annotation) wrap the calls in
    ``with depgraph.batch(ws):`` — record_edge/link_requirement/scan detect
    the active batch and mutate the shared in-memory graph instead of doing
    a full load→save cycle each. The flush is stamped, atomic and performed
    under the same graph.json lock, so nothing is cached across processes.
    On an exception nothing is flushed and the read memo is dropped (the
    in-memory graph may hold partial mutations)."""
    p = os.path.abspath(_path(ws))
    if p in _BATCH:                        # nested: the outer batch flushes
        yield _BATCH[p]
        return
    with tp.file_lock(p):
        _GRAPH_CACHE.pop(p, None)          # re-read under the lock
        g = load(ws)
        _BATCH[p] = g
        try:
            yield g
        except BaseException:
            _GRAPH_CACHE.pop(p, None)      # partial mutations — not truth
            raise
        finally:
            _BATCH.pop(p, None)
        _stamp_meta(ws, g)
        save(ws, g)


def _stamp_meta(ws: str, g: dict, *, scanned: bool = False) -> dict:
    """Bind graph evidence to its exact file/edge material.

    Recorded contract/runtime edges can change between source scans, so their
    writers must advance the same content fingerprint used by review evidence.
    ``scanned_at`` remains the time of the deterministic source scan; an edge
    recording does not pretend the code tree was rescanned.
    """
    graph_material = {
        "files": {p: row.get("hash", "")
                  for p, row in (g.get("files") or {}).items()},
        "edges": sorted((e["from"], e["to"], e["kind"],
                         e.get("source"), e.get("confidence"))
                        for e in (g.get("edges") or [])),
        "architecture_map": str((((g.get("meta") or {}).get(
            "architecture_map") or {}).get("fingerprint") or "")),
    }
    meta = dict(g.get("meta") or {})
    meta.update({
        "schema": 2,
        "updated_at": int(time.time()),
        "content_fingerprint": hashlib.sha256(
            json.dumps(graph_material, sort_keys=True,
                       separators=(",", ":")).encode()).hexdigest(),
        "source_counts": {
            source: sum(1 for e in (g.get("edges") or [])
                        if e.get("source") == source)
            for source in sorted({e.get("source", "unknown")
                                  for e in (g.get("edges") or [])})
        },
    })
    if scanned:
        meta["scanned_at"] = int(time.time())
        meta["scanned_head"] = tp.git_head(ws)
    g["meta"] = meta
    return g


def summary(ws: str) -> dict:
    """Public read model for a view — module/edge counts without the caller
    needing to know graph.json's internal key names. (The dashboard consumes
    this instead of reaching into the raw file, so a schema change here can't
    silently zero the mission-control graph tab.)"""
    g = load(ws)
    return {"modules": len(g.get("modules") or {}),
            "edges": len(g.get("edges") or [])}


# ------------------------------------------------ shared graph primitives
# Stable depgraph API, one lower-layer implementation.  Aliases preserve
# every existing caller and monkeypatch target while scanner, decomposition,
# and lens routing consume the same identity/edge/context contract.
_SRC_ROOTS = graph_primitives._SRC_ROOTS
_GO_MODULE_LINE = graph_primitives._GO_MODULE_LINE
_ID_PREFIXES = graph_primitives._ID_PREFIXES
ROOT_MODULE_KEY = graph_primitives.ROOT_MODULE_KEY
DEPENDENCY_EDGE_KINDS = graph_primitives.DEPENDENCY_EDGE_KINDS
manifest_modules = graph_primitives.manifest_modules
declared_module_ids = graph_primitives.declared_module_ids
_strip_root_module = graph_primitives._strip_root_module
root_module = graph_primitives.root_module
strip_root_prefix = graph_primitives.strip_root_prefix
_declared_target = graph_primitives._declared_target
module_of = graph_primitives.module_of
_node_kind = graph_primitives.node_kind
_is_boundary = graph_primitives.is_boundary
is_dependency_edge = graph_primitives.is_dependency_edge

# ------------------------------------------------------------------ scanners

def _bounded_parse_reason(exc: BaseException) -> str:
    """One-line, bounded producer reason suitable for JSON and terminals."""
    if isinstance(exc, SyntaxError):
        reason = str(exc.msg or "invalid syntax")
        if exc.lineno is not None:
            reason += f" at line {exc.lineno}"
            if exc.offset is not None:
                reason += f", column {exc.offset}"
    else:
        reason = str(exc) or exc.__class__.__name__
    return " ".join(reason.split())[:240]


def _py_imports_checked(src: str, relpath: str,
                        known_stems: dict) -> tuple[set, dict | None]:
    out = set()
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return out, {
            "file": relpath,
            "parser": "python-ast",
            "error_class": exc.__class__.__name__,
            "reason": _bounded_parse_reason(exc),
            "file_fingerprint": hashlib.sha256(src.encode()).hexdigest(),
        }
    stdlib = getattr(_sys, "stdlib_module_names", frozenset())
    pkg_dir = posixpath.dirname(relpath)
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import → resolve against file's dir
                base = pkg_dir.split("/")
                base = base[: len(base) - (node.level - 1)]
                names = ["/".join(base + (node.module or "").split("."))]
            else:
                names = [node.module or ""]
        for n in names:
            n = n.replace(".", "/")
            hit = known_stems.get(n) or known_stems.get(n.split("/")[0])
            if hit:
                out.add(hit)
            elif "/" not in n and n and n not in stdlib:
                out.add(f"ext:{n}")
    return out, None


def _py_imports(src: str, relpath: str, known_stems: dict) -> set:
    """Backward-compatible import-only view of the checked scanner."""
    return _py_imports_checked(src, relpath, known_stems)[0]


_JS_IMPORT = re.compile(
    r"""(?:import\s+(?:[^'"]*\s+from\s+)?|require\s*\(\s*|export\s+[^'"]*"""
    r"""from\s+)['"]([^'"]+)['"]""")


def _js_imports(src: str, relpath: str, file_index: set,
                manifests: dict | None = None,
                declared_ids=None, root_mod: "str | None" = None) -> set:
    out = set()
    for target in _JS_IMPORT.findall(src):
        if target.startswith("."):
            # LOGICAL path arithmetic over '/'-shaped repo paths, so
            # posixpath, not os.path: on Windows os.path.join/normpath
            # would emit backslashes that never match the '/'-keyed
            # file_index, silently dropping every relative JS import.
            resolved = posixpath.normpath(
                posixpath.join(posixpath.dirname(relpath), target))
            # find an actual file this resolves to
            for cand in (resolved, *(f"{resolved}{e}" for e in CODE_EXT),
                         *(f"{resolved}/index{e}" for e in CODE_EXT)):
                if cand in file_index:
                    out.add(module_of(cand, manifests))
                    break
        else:
            # A bare specifier is not automatically third-party: in a
            # workspace it is how one member imports another. `@acme/core`
            # used to become `ext:@acme` — an external node named after a
            # SCOPE, which is neither the package nor a real dependency.
            inside = _declared_target(target, declared_ids)
            if not inside:
                # Intra-repo: strip the repo's own module path and bucket the
                # remainder the same way a file path is bucketed. The root
                # is passed in — recovering it from `declared_ids` is what
                # silently failed in v2.10.0 (see root_module).
                rel_in = strip_root_prefix(
                    target, root_mod if root_mod is not None
                    else root_module(declared_ids))
                if rel_in:
                    inside = module_of(rel_in + "/_", manifests)
            out.add(inside if inside else "ext:" + target.split("/")[0])
    return out


_CS_NS = re.compile(r"^\s*namespace\s+([\w.]+)", re.M)
_CS_USING = re.compile(r"^\s*(?:global\s+)?using\s+(?:static\s+)?"
                       r"([\w.]+)\s*;", re.M)


def _cs_declared(src: str) -> list:
    """Namespaces a C# file declares (block-scoped or file-scoped)."""
    return _CS_NS.findall(src)


def _cs_imports(src: str, ns_map: dict) -> set:
    """`using` directives resolved against declared namespaces; System.*
    is the BCL (skipped); everything else unresolved is a package dep."""
    out = set()
    for u in _CS_USING.findall(src):
        hit = None
        parts = u.split(".")
        for i in range(len(parts), 0, -1):        # longest prefix wins
            hit = ns_map.get(".".join(parts[:i]))
            if hit:
                break
        if hit:
            out.add(hit)
        elif not u.startswith(("System", "global")):
            out.add("ext:" + ".".join(parts[:2]))
    return out


_JAVA_PKG = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.M)
_JAVA_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+?)"
                          r"(?:\.\*)?\s*;", re.M)


def _java_declared(src: str) -> list:
    return _JAVA_PKG.findall(src)


def _java_imports(src: str, pkg_map: dict) -> set:
    """imports resolved against declared packages (an import of a CLASS
    resolves via its package prefix); java./javax. are the JDK (skipped)."""
    out = set()
    for imp in _JAVA_IMPORT.findall(src):
        parts = imp.split(".")
        hit = None
        for i in range(len(parts), 0, -1):
            hit = pkg_map.get(".".join(parts[:i]))
            if hit:
                break
        if hit:
            out.add(hit)
        elif not imp.startswith(("java.", "javax.", "jakarta.annotation")):
            out.add("ext:" + ".".join(parts[:3 if parts[0] in
                                             ("org", "com", "io", "net")
                                             else 1]))
    return out


_RB_REQ_REL = re.compile(r"""require_relative\s+['"]([^'"]+)['"]""")
_RB_REQ = re.compile(r"""(?<!_)require\s+['"]([^'"]+)['"]""")
_RB_STDLIB = {"json", "yaml", "set", "time", "date", "uri", "net/http",
              "logger", "csv", "fileutils", "pathname", "securerandom",
              "digest", "base64", "open3", "socket", "erb", "openssl"}


def _ruby_imports(src: str, relpath: str, file_index: set,
                  manifests: dict | None = None) -> set:
    """require_relative resolved to files; bare require matched against
    repo lib paths first (Rails-style lib/foo/bar → lib/foo), else a gem.
    (Rails constant autoloading carries no import statements — those edges
    come from the model/controller dirs sharing modules, and can be added
    as recorded edges where they matter.)"""
    out = set()
    here = posixpath.dirname(relpath)
    for target in _RB_REQ_REL.findall(src):
        cand = posixpath.normpath(posixpath.join(here, target)) + ".rb"
        if cand in file_index:
            out.add(module_of(cand, manifests))
    for target in _RB_REQ.findall(src):
        cand = posixpath.join("lib", target) + ".rb"
        if cand in file_index:
            out.add(module_of(cand, manifests))
        elif (target + ".rb") in file_index:
            out.add(module_of(target + ".rb", manifests))
        elif target not in _RB_STDLIB and not target.startswith("."):
            out.add("ext:" + target.split("/")[0])
    return out


_CSPROJ_PROJ = re.compile(r'ProjectReference\s+Include="([^"]+)"')
_CSPROJ_PKG = re.compile(r'PackageReference\s+Include="([^"]+)"')
_GEMFILE_GEM = re.compile(r"""^\s*gem\s+['"]([\w-]+)['"]""", re.M)
# Maven/Gradle POM dependencies. Same shape as .csproj PackageReference and
# Gemfile gems, and attributed the same way: to the module the manifest
# itself lives in. A pom in `modules/order/` is that module's dependency
# list; a pom at the REPO ROOT describes the whole build and cannot say
# which package uses what, so it is skipped for exactly the reason D-0007
# skips a root package.json — a root manifest is about the repository, not
# a module in it, and spreading its dependencies over every package would
# be invention rather than resolution.
_POM_DEP = re.compile(
    r"<dependency>(.*?)</dependency>", re.S | re.I)
_POM_ARTIFACT = re.compile(r"<artifactId>\s*([^<\s]+)\s*</artifactId>", re.I)
_POM_SCOPE = re.compile(r"<scope>\s*([^<\s]+)\s*</scope>", re.I)


def _compose_services(src: str) -> list:
    """Very small docker-compose reader: service names + depends_on."""
    services, cur, in_services, in_dep = [], None, False, False
    for line in src.splitlines():
        if re.match(r"^services\s*:", line):
            in_services = True
            continue
        if in_services and re.match(r"^\S", line):     # left the block
            in_services = False
        if not in_services:
            continue
        m = re.match(r"^  (\w[\w.-]*)\s*:\s*$", line)
        if m:
            cur = m.group(1)
            services.append({"name": cur, "depends_on": []})
            in_dep = False
            continue
        if cur and re.match(r"^\s{4}depends_on\s*:", line):
            in_dep = True
            continue
        if cur and in_dep:
            d = re.match(r"^\s+-\s*(\w[\w.-]*)", line)
            if d:
                services[-1]["depends_on"].append(d.group(1))
            elif not re.match(r"^\s{6}", line):
                in_dep = False
    return services


# ------------------------------------------------------------------ scan

def _git_candidates(ws: str) -> list | None:
    """Candidate files honoring .gitignore: tracked + untracked-unignored,
    minus deleted-but-tracked. None when `ws` is not a git work tree (or git
    is unusable) — the caller falls back to the os.walk. Enumerating via git
    keeps vendored/build trees (vendor/, target/, generated output …) out of
    the graph on any repo with a sane .gitignore, and turns the per-gate
    full-tree walk into one git call."""
    try:
        r = subprocess.run(
            ["git", "-C", ws, "ls-files", "-z", "--cached", "--others",
             "--exclude-standard"],
            capture_output=True, timeout=60)
        if r.returncode != 0:
            return None
        names = {n for n in r.stdout.decode("utf-8", "replace").split("\0")
                 if n}
        d = subprocess.run(["git", "-C", ws, "ls-files", "-z", "--deleted"],
                           capture_output=True, timeout=60)
        if d.returncode == 0:
            names -= {n for n in d.stdout.decode("utf-8", "replace")
                      .split("\0") if n}
        return sorted(names)
    except Exception:
        return None


_GO_LIMITATION = (
    "internal Go imports are not resolved to modules — the Go scanner "
    "records external (ext:) edges only, so intra-repo Go dependencies are "
    "absent and impact()/hub signals under-count for Go code. Record "
    "intra-repo Go edges explicitly (record_edge / `tp graph edge`).")
# D-0007 narrowed the gap without closing it: an import path that a go.mod in
# this repo DECLARES now resolves to that module. What is still missing is
# every import whose module path is not declared here — so the disclosure must
# say which of the two situations the caller is in, not keep claiming
# external-only coverage while emitting internal Go edges.
_GO_LIMITATION_DECLARED = (
    "Go imports are resolved to internal modules only where a go.mod in this "
    "repo DECLARES the import path (see meta.module_ids). An import path this "
    "repo does not declare still lands as ext:<last-segment>, so intra-repo "
    "Go dependencies outside the declared module set are absent and "
    "impact()/hub signals under-count for them. Record those explicitly "
    "(record_edge / `tp graph edge`).")


def scan(ws: str, decompose: bool = False, *, strict: bool = False) -> dict:
    """Build/refresh the graph. Incremental: unchanged files (by content
    hash) keep their cached edges — a rescan after a small diff is cheap.
    The read-modify-write is serialized under the graph.json lock so a
    concurrent record_edge/link_requirement is never lost.

    decompose=True additionally derives the `components` LAYER (R-0003,
    contract:component-map) via taskplane/decompose.py. The layer is
    ADDITIVE: without the flag the scan is byte-identical to the legacy
    behavior (decompose.py is not even imported), and a graph that never
    decomposed carries no `components` key at all."""
    p = os.path.abspath(_path(ws))
    if p in _BATCH:                        # inside batch(): lock already held
        graph = _scan_locked(ws, into=_BATCH[p], decompose=decompose)
        if strict or _STRICT_GRAPH_QUALITY.get():
            require_quality(graph)
        return graph
    restored = _restore_managed_cache(ws, decompose=decompose)
    if restored is not None:
        if strict or _STRICT_GRAPH_QUALITY.get():
            require_quality(restored)
        return restored
    with tp.file_lock(p):
        graph = _scan_locked(ws, decompose=decompose)
    _write_managed_cache(ws, graph, decompose=decompose)
    if strict or _STRICT_GRAPH_QUALITY.get():
        require_quality(graph)
    return graph


def _scan_volatile_stripped(g: dict) -> str:
    """Canonical JSON of a graph minus the volatile meta timestamps — the
    only fields that move on a content-identical rescan."""
    meta = {k: v for k, v in (g.get("meta") or {}).items()
            if k not in ("updated_at", "scanned_at")}
    stable = {k: v for k, v in g.items() if k != "meta"}
    stable["meta"] = meta
    return json.dumps(stable, sort_keys=True, default=str)


def load_excludes(ws: str) -> tuple[list, str | None]:
    """(prefixes, error) — repo-declared trees that are NOT product code.

    SKIP_DIRS covers the universal cases (node_modules, vendor, build output).
    It cannot cover the repo-specific ones, and every real codebase has them:
    generated protobuf/OpenAPI clients, sample apps, docs sites, test corpora.
    Left unexcluded they become graph MODULES and route review lenses at code
    nobody wrote — this repo already mints `api`, `auth`, `components` and
    `src` out of its own test fixtures that way.

    Fails OPEN with a report, like `decompose.load_floors`: a malformed file
    must never silently narrow the graph, because a narrowed graph is a
    narrowed blast radius, and that fails toward LESS review.
    """
    import path_roles
    path = os.path.join(ws, "components.yaml")
    if not os.path.exists(path):
        return [], None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        return path_roles.parse_components_yaml(text)["exclude"], None
    except (OSError, ValueError) as exc:
        return [], f"components.yaml ignored (no exclusions applied): {exc}"


def _components_file_fingerprint(ws: str) -> str:
    """Content identity for every cache consumer of ``components.yaml``."""
    path = os.path.join(ws, "components.yaml")
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as stream:
            while True:
                block = stream.read(64 * 1024)
                if not block:
                    return digest.hexdigest()
                digest.update(block)
    except OSError:
        return ""


def _design_file_fingerprint(ws: str) -> str:
    """Content identity for the Design authority consumed by graph scans."""
    path = os.path.join(ws, "design", "contract.json")
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as stream:
            while True:
                block = stream.read(64 * 1024)
                if not block:
                    return digest.hexdigest()
                digest.update(block)
    except OSError:
        return ""


def _safe_architecture_glob(pattern: str) -> bool:
    value = str(pattern or "").replace("\\", "/")
    parts = [part for part in value.split("/") if part]
    return bool(value and not value.startswith(("/", "./"))
                and ".." not in parts and not os.path.isabs(value))


def _canonical_json_fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode("utf-8")).hexdigest()


def _read_design_architecture(ws: str) -> dict:
    """Read both immutable R-0013 and current Design graph authorities.

    ``architecture_decomposition.semantic_edges`` is the accepted R-0013
    authority. ``graph.proposed_edges`` remains the current Design authority;
    neither is allowed to replace the other accidentally.
    """
    path = os.path.join(ws, "design", "contract.json")
    try:
        with open(path, "rb") as stream:
            raw = stream.read(ARCHITECTURE_MAX_BYTES + 1)
    except FileNotFoundError:
        return {"configured": False, "nodes": [], "semantic_edges": [],
                "design_edges": [], "singleton_sccs": [], "errors": []}
    except OSError as exc:
        return {"configured": True, "nodes": [], "semantic_edges": [],
                "design_edges": [], "singleton_sccs": [],
                "errors": [f"design/contract.json cannot be read: {exc}"]}
    if len(raw) > ARCHITECTURE_MAX_BYTES:
        return {"configured": True, "nodes": [], "semantic_edges": [],
                "design_edges": [], "singleton_sccs": [], "errors": [
                    "design/contract.json exceeds architecture proof bound "
                    f"{ARCHITECTURE_MAX_BYTES} bytes"]}
    try:
        contract = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"configured": True, "nodes": [], "semantic_edges": [],
                "design_edges": [], "singleton_sccs": [], "errors": [
                    "design/contract.json is not valid UTF-8 JSON: "
                    f"{type(exc).__name__}"]}
    if not isinstance(contract, dict):
        return {"configured": True, "nodes": [], "semantic_edges": [],
                "design_edges": [], "singleton_sccs": [],
                "errors": ["design/contract.json root must be an object"]}
    architecture = contract.get("architecture_decomposition")
    errors = []
    if not isinstance(architecture, dict):
        return {"configured": True, "nodes": [], "semantic_edges": [],
                "design_edges": [], "singleton_sccs": [], "errors": [
                    "accepted design is missing architecture_decomposition"]}
    unknown = sorted(set(architecture) - _ARCHITECTURE_MAP_KEYS)
    if unknown:
        errors.append("architecture_decomposition has unknown sections: "
                      + ", ".join(unknown))
    if architecture.get("schema") != DESIGN_ARCHITECTURE_SCHEMA:
        errors.append("architecture_decomposition has unknown schema: "
                      + str(architecture.get("schema") or "missing"))
    decision_record = str(architecture.get("decision_record") or "").strip()
    authority_floor = _ARCHITECTURE_AUTHORITY_FLOORS.get(decision_record)
    if authority_floor is None:
        errors.append("architecture_decomposition decision_record has no "
                      "accepted authority floor: "
                      + (decision_record or "missing"))
    fingerprint = str(architecture.get("content_fingerprint") or "")
    material = {key: architecture[key] for key in sorted(architecture)
                if key != "content_fingerprint"}
    expected_fingerprint = _canonical_json_fingerprint(material)
    if fingerprint != expected_fingerprint:
        errors.append("architecture_decomposition content_fingerprint does "
                      "not bind the complete accepted map")
    if authority_floor is not None and fingerprint != \
            authority_floor["content_fingerprint"]:
        errors.append("architecture_decomposition does not match the immutable "
                      f"authority floor for {decision_record}")
    if architecture.get("scanner_input") != \
            "design/contract.json#/architecture_decomposition":
        errors.append("architecture_decomposition scanner_input is missing "
                      "or points elsewhere")
    for field in ("decision_record", "scanner_rule"):
        if not isinstance(architecture.get(field), str) or not \
                architecture[field].strip():
            errors.append(f"architecture_decomposition {field} is required")
    nodes = architecture.get("nodes")
    if not isinstance(nodes, list):
        errors.append("architecture_decomposition nodes must be a list")
        nodes = []
    if authority_floor is not None:
        node_set = sorted(
            nodes, key=lambda row: str(row.get("id") or "")
            if isinstance(row, dict) else "")
        if len(nodes) != authority_floor["node_count"] or \
                _canonical_json_fingerprint(node_set) != \
                authority_floor["node_set_fingerprint"]:
            errors.append("architecture_decomposition nodes do not match the "
                          "immutable 14-node id/kind/path-glob authority")
    semantic_edges = architecture.get("semantic_edges")
    if not isinstance(semantic_edges, list):
        errors.append("architecture_decomposition semantic_edges must be a list")
        semantic_edges = []
    if authority_floor is not None:
        edge_set = sorted(semantic_edges, key=lambda row: (
            str(row.get("from") or ""), str(row.get("to") or ""),
            str(row.get("kind") or ""), str(row.get("reason") or ""))
            if isinstance(row, dict) else ("", "", "", ""))
        if len(semantic_edges) != authority_floor["semantic_edge_count"] or \
                _canonical_json_fingerprint(edge_set) != \
                authority_floor["semantic_edge_set_fingerprint"]:
            errors.append("architecture_decomposition semantic_edges do not "
                          "match the immutable 24-edge authority")
    singleton_sccs = architecture.get("required_singleton_sccs")
    if not isinstance(singleton_sccs, list):
        errors.append("architecture_decomposition required_singleton_sccs "
                      "must be a list")
        singleton_sccs = []
    singleton_values = [item.strip() for item in singleton_sccs
                        if isinstance(item, str) and item.strip()]
    if len(singleton_values) != len(singleton_sccs):
        errors.append("architecture_decomposition required_singleton_sccs "
                      "must contain only non-empty strings")
    if authority_floor is not None and (len(singleton_values) != 3 or
            set(singleton_values) != authority_floor["singleton_sccs"]):
        errors.append("architecture_decomposition required_singleton_sccs "
                      "do not match the immutable three-singleton authority")
    properties = architecture.get("required_properties")
    if not isinstance(properties, list) or not properties or not all(
            isinstance(item, str) and item.strip() for item in properties):
        errors.append("architecture_decomposition required_properties must "
                      "be a non-empty string list")
    graph = contract.get("graph")
    design_edges = graph.get("proposed_edges") if isinstance(graph, dict) \
        else None
    if not isinstance(design_edges, list):
        errors.append("current design graph.proposed_edges must be a list")
        design_edges = []
    requirement = str(contract.get("requirement") or "").strip()
    graph_floor = _CURRENT_GRAPH_AUTHORITY_FLOORS.get(requirement)
    if graph_floor is None:
        errors.append("current design requirement has no approved graph "
                      "authority floor: " + (requirement or "missing"))
    elif not design_edges:
        errors.append("current design graph.proposed_edges must be non-empty")
    elif len(design_edges) != graph_floor["edge_count"] or \
            _canonical_json_fingerprint(design_edges) != \
            graph_floor["edge_fingerprint"] or \
            _canonical_json_fingerprint(sorted(design_edges, key=lambda row: (
                str(row.get("from") or ""), str(row.get("to") or ""),
                str(row.get("kind") or ""), str(row.get("reason") or ""))
                if isinstance(row, dict) else ("", "", "", ""))) != \
            graph_floor["edge_set_fingerprint"]:
        errors.append("current design graph.proposed_edges do not match the "
                      f"approved authority for {requirement}")
    proposed_modules = graph.get("proposed_modules") if isinstance(
        graph, dict) else None
    if not isinstance(proposed_modules, list) or not all(
            isinstance(item, str) and item.strip() for item in proposed_modules):
        errors.append("current design graph.proposed_modules must be a list")
        proposed_modules = []
    contract_ids = [str(row.get("id") or "").strip()
                    for row in (contract.get("contracts") or [])
                    if isinstance(row, dict) and row.get("id")]
    return {"configured": True, "nodes": nodes,
            "semantic_edges": semantic_edges, "design_edges": design_edges,
            "singleton_sccs": singleton_sccs, "errors": errors,
            "decision_record": decision_record, "requirement": requirement,
            "authority_floor": authority_floor,
            "graph_authority_floor": graph_floor,
            "required_properties": properties or [],
            "proposed_modules": proposed_modules,
            "contract_ids": contract_ids}


def _python_file_import_edges(ws: str,
                              files: set[str]) -> tuple[set, list[str]]:
    """Observe exact Python file imports inside the declared file universe."""
    aliases: dict[str, set[str]] = {}
    module_for: dict[str, str] = {}
    for path in sorted(files):
        if not path.endswith(".py"):
            continue
        module = path[:-3].replace("/", ".")
        if module.endswith(".__init__"):
            module = module[:-9]
        module_for[path] = module
        for alias in {module, module.rsplit(".", 1)[-1]}:
            aliases.setdefault(alias, set()).add(path)

    observed: set[tuple[str, str]] = set()
    errors = []
    for source in sorted(files):
        module = module_for.get(source)
        if not module:
            continue
        full = os.path.join(ws, *source.split("/"))
        try:
            if os.path.getsize(full) > 2 * 1024 * 1024:
                errors.append(f"owner source exceeds 2097152 bytes: {source}")
                continue
            with open(full, encoding="utf-8", errors="replace") as stream:
                tree = ast.parse(stream.read(), filename=source)
        except (OSError, SyntaxError) as exc:
            errors.append(
                f"owner source cannot be inspected: {source}: "
                f"{type(exc).__name__}")
            continue
        candidates = set()
        package = module.rsplit(".", 1)[0] if "." in module else ""
        for item in ast.walk(tree):
            if isinstance(item, ast.Import):
                candidates.update(alias.name for alias in item.names)
            elif isinstance(item, ast.ImportFrom):
                base = item.module or ""
                if item.level:
                    parts = package.split(".") if package else []
                    keep = max(0, len(parts) - (item.level - 1))
                    prefix = ".".join(parts[:keep])
                    base = ".".join(part for part in (prefix, base) if part)
                if base:
                    candidates.add(base)
                for alias in item.names:
                    if alias.name != "*":
                        candidates.add(".".join(
                            part for part in (base, alias.name) if part))
        for candidate in sorted(candidates):
            targets = aliases.get(candidate) or set()
            if len(targets) > 1:
                errors.append(
                    f"ambiguous owner import {candidate!r} from {source}")
                continue
            if targets:
                target = next(iter(targets))
                if target != source:
                    observed.add((source, target))
    return observed, errors


def _semantic_edges(rows, *, label: str, architecture_ids: set[str],
                    known_files: set[str],
                    endpoint_registry: frozenset[str]) -> \
        tuple[list[dict], list[str]]:
    """Validate one semantic authority without inventing missing endpoints."""
    edges, errors = [], []
    seen = set()

    def endpoint_exists(node: str) -> bool:
        if ":" in node:
            return node in endpoint_registry
        if node in architecture_ids:
            return True
        normalized = node.replace("\\", "/").strip("/")
        return bool(normalized and any(
            path == normalized or path.startswith(normalized + "/")
            for path in known_files))

    for index, row in enumerate(rows or []):
        if not isinstance(row, dict):
            errors.append(f"{label}[{index}] must be an object")
            continue
        unknown = sorted(set(row) - _SEMANTIC_EDGE_KEYS)
        if unknown:
            errors.append(f"{label}[{index}] has unknown fields: "
                          + ", ".join(unknown))
        source, target, kind = (str(row.get(key) or "").strip()
                                for key in ("from", "to", "kind"))
        reason = str(row.get("reason") or "").strip()
        if not source or not target or not reason:
            errors.append(f"{label}[{index}] requires from, to, kind, reason")
            continue
        if not _GRAPH_NODE_ID.fullmatch(source) or not \
                _GRAPH_NODE_ID.fullmatch(target):
            errors.append(f"{label}[{index}] has unsafe node identity")
        if kind not in _SEMANTIC_EDGE_KINDS:
            errors.append(f"{label}[{index}] has unknown semantic kind: {kind}")
        key = (source, target, kind)
        if key in seen:
            errors.append(f"{label} has duplicate edge: "
                          f"{source} -> {target}:{kind}")
        seen.add(key)
        for endpoint in (source, target):
            if ":" in endpoint and endpoint not in endpoint_registry:
                errors.append(f"{label}[{index}] names unregistered semantic "
                              f"endpoint: {endpoint}")
                continue
            if not endpoint_exists(endpoint):
                errors.append(f"{label}[{index}] names unknown endpoint: "
                              f"{endpoint}")
        edges.append({"from": source, "to": target, "kind": kind,
                      "reason": reason})
    return edges, errors


def _disk_glob_hits(ws: str, pattern: str, *, limit: int = 32) -> list[str]:
    """Bounded existence check used to distinguish missing from ignored."""
    hits = []
    for root, dirs, names in os.walk(ws):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS
                         and not d.startswith(".tp-"))
        for name in sorted(names):
            rel = os.path.relpath(os.path.join(root, name), ws).replace(
                os.sep, "/")
            if glob_match.path_matches(rel, pattern):
                hits.append(rel)
                if len(hits) >= limit:
                    return hits
    return hits


def architecture_map_proof(ws: str, *, known_files=None,
                           max_nodes: int = ARCHITECTURE_MAX_NODES,
                           max_edges: int = ARCHITECTURE_MAX_EDGES) -> dict:
    """Validate the accepted Design map without partial or substitute proof."""
    parsed = _read_design_architecture(ws)
    nodes = list(parsed["nodes"])
    architecture_edge_rows = list(parsed["semantic_edges"])
    design_edge_rows = list(parsed["design_edges"])
    errors = list(parsed["errors"])
    if _canonical_json_fingerprint(sorted(_SEMANTIC_ENDPOINT_REGISTRY)) != \
            _SEMANTIC_ENDPOINT_REGISTRY_FINGERPRINT:
        errors.append("semantic endpoint registry fingerprint is invalid")
    try:
        node_limit = max(0, int(max_nodes))
        edge_limit = max(0, int(max_edges))
    except (TypeError, ValueError):
        node_limit, edge_limit = 0, 0
        errors.append("architecture bounds must be non-negative integers")
    total_edges = len(architecture_edge_rows) + len(design_edge_rows)
    truncated = len(nodes) > node_limit or total_edges > edge_limit
    if len(nodes) > node_limit:
        errors.append(
            f"owner node count {len(nodes)} exceeds bound {node_limit}")
    if total_edges > edge_limit:
        errors.append(
            f"semantic edge count {total_edges} exceeds bound {edge_limit}")

    available = set(known_files) if known_files is not None else set()
    if known_files is None:
        for root, dirs, names in os.walk(ws):
            dirs[:] = sorted(d for d in dirs if d != ".git")
            for name in names:
                available.add(os.path.relpath(
                    os.path.join(root, name), ws).replace(os.sep, "/"))
    node_ids, node_details, node_files = [], [], {}
    seen_ids = set()
    for index, row in enumerate(nodes[:node_limit]):
        if not isinstance(row, dict):
            errors.append(f"architecture node[{index}] must be an object")
            continue
        unknown = sorted(set(row) - _ARCHITECTURE_NODE_KEYS)
        if unknown:
            errors.append(f"architecture node[{index}] has unknown fields: "
                          + ", ".join(unknown))
        node_id = str(row.get("id") or "").strip()
        kind = str(row.get("kind") or "").strip()
        patterns = row.get("path_globs")
        if not node_id or node_id in seen_ids:
            errors.append(f"architecture node[{index}] has missing or "
                          f"duplicate id: {node_id or 'missing'}")
            continue
        if not _GRAPH_NODE_ID.fullmatch(node_id):
            errors.append(f"architecture node[{index}] has unsafe id: {node_id}")
        if ":" in node_id and node_id not in _SEMANTIC_ENDPOINT_REGISTRY:
            errors.append(f"architecture node[{index}] names unregistered "
                          f"semantic endpoint: {node_id}")
        seen_ids.add(node_id)
        node_ids.append(node_id)
        if kind not in _ARCHITECTURE_NODE_KINDS:
            errors.append(f"architecture node {node_id} has unknown kind: {kind}")
        if not isinstance(patterns, list) or not all(
                isinstance(item, str) and item for item in patterns):
            errors.append(f"architecture node {node_id} path_globs must be a list")
            patterns = []
        boundary_kind = kind in {"external-host", "contract", "resource"}
        if boundary_kind and patterns:
            errors.append(f"boundary node {node_id} cannot declare path globs")
        if not boundary_kind and not patterns:
            errors.append(f"architecture node {node_id} has no path globs")
        prefix_for_kind = {"external-host": "ext:", "contract": "contract:",
                           "resource": "resource:"}.get(kind)
        if prefix_for_kind and not node_id.startswith(prefix_for_kind):
            errors.append(f"architecture node {node_id} kind {kind} requires "
                          f"a {prefix_for_kind} identity")
        matches = set()
        for pattern in patterns:
            normalized = pattern.replace("\\", "/")
            if not _safe_architecture_glob(normalized):
                errors.append(f"architecture node {node_id} has unsafe glob: "
                              f"{pattern}")
                continue
            hits = {path for path in available
                    if glob_match.path_matches(path, normalized)}
            root_real = os.path.realpath(ws)
            for hit in sorted(hits):
                hit_real = os.path.realpath(os.path.join(ws, *hit.split("/")))
                if not (hit_real.startswith(root_real + os.sep)
                        and os.path.isfile(hit_real)):
                    errors.append(f"architecture node {node_id} glob resolves "
                                  f"outside candidate files: {hit}")
            if not hits:
                disk_hits = _disk_glob_hits(ws, normalized)
                if disk_hits:
                    errors.append(f"architecture node {node_id} glob is "
                                  f"ignored or excluded: {normalized}")
                else:
                    errors.append(f"architecture node {node_id} glob has no "
                                  f"candidate files: {normalized}")
            matches.update(hits)
        node_files[node_id] = sorted(matches)
        node_details.append({"id": node_id, "kind": kind,
                             "path_globs": list(patterns),
                             "matched_files": sorted(matches)})

    properties = {str(item) for item in parsed.get("required_properties") or []}
    missing_properties = sorted(_ARCHITECTURE_REQUIRED_PROPERTIES - properties)
    unknown_properties = sorted(properties - _ARCHITECTURE_REQUIRED_PROPERTIES)
    if parsed["configured"] and missing_properties:
        errors.append("architecture_decomposition is missing required "
                      "properties: " + "; ".join(missing_properties))
    if parsed["configured"] and unknown_properties:
        errors.append("architecture_decomposition has unknown required "
                      "properties: " + "; ".join(unknown_properties))

    architecture_ids = set(node_ids)
    bounded_architecture_edges, edge_errors = _semantic_edges(
        architecture_edge_rows[:edge_limit],
        label="architecture_decomposition.semantic_edges",
        architecture_ids=architecture_ids, known_files=available,
        endpoint_registry=_SEMANTIC_ENDPOINT_REGISTRY)
    errors.extend(edge_errors)
    remaining = max(0, edge_limit - len(bounded_architecture_edges))
    bounded_design_edges, design_edge_errors = _semantic_edges(
        design_edge_rows[:remaining], label="graph.proposed_edges",
        architecture_ids=(architecture_ids
                          | set(parsed.get("proposed_modules") or [])
                          | set(parsed.get("contract_ids") or [])),
        known_files=available,
        endpoint_registry=_SEMANTIC_ENDPOINT_REGISTRY)
    errors.extend(design_edge_errors)

    file_owners: dict[str, set[str]] = {}
    for node_id, matched in node_files.items():
        for path in matched:
            file_owners.setdefault(path, set()).add(node_id)
    file_imports, import_errors = _python_file_import_edges(
        ws, set(file_owners))
    errors.extend(import_errors)
    architecture_imports = set()
    for source_file, target_file in file_imports:
        for source in file_owners.get(source_file, ()):
            for target in file_owners.get(target_file, ()):
                if source != target:
                    architecture_imports.add((source, target))

    new_owners = {row["id"] for row in node_details
                  if row["kind"] == "new"}
    forbidden_targets = {
        "component:taskplane-governance-adapters",
        "ext:codex-native-orchestration",
    }
    forbidden_imports = sorted(
        (source, target) for source, target in architecture_imports
        if source in new_owners and target in forbidden_targets)
    if forbidden_imports:
        errors.append("new owners depend on host transport or transition "
                      "adapters: " + ", ".join(
                          f"{source} -> {target}"
                          for source, target in forbidden_imports))

    accepted_edge_keys = {(row["from"], row["to"], row["kind"])
                          for row in bounded_architecture_edges}
    required_edge_keys = {
        ("taskplane", "contract:delivery.codex-native-dispatch", "intent"),
        ("contract:delivery.codex-native-dispatch",
         "ext:codex-native-orchestration", "transported-by"),
        ("taskplane", "contract:delivery.exact-sha-terminal-truth", "changes"),
        ("contract:delivery.exact-sha-terminal-truth",
         "taskplane/terminal_truth.py", "coordinated-by"),
    }
    missing_required_edges = sorted(required_edge_keys - accepted_edge_keys)
    if missing_required_edges:
        errors.append("architecture_decomposition semantic authority omits "
                      "required edges: " + ", ".join(
                          f"{source} -> {target}:{kind}"
                          for source, target, kind in missing_required_edges))

    singleton_sccs = [str(item or "").strip()
                      for item in parsed["singleton_sccs"]]
    if len(singleton_sccs) != len(set(singleton_sccs)):
        errors.append("required_singleton_sccs must be unique")
    unknown_singletons = sorted(set(singleton_sccs) - architecture_ids)
    if unknown_singletons:
        errors.append("required_singleton_sccs names unknown nodes: "
                      + ", ".join(unknown_singletons))

    sccs, cyclic = [], []
    if not truncated and not unknown_singletons:
        try:
            sccs = graph_primitives.strongly_connected_components(
                node_ids, architecture_imports)
            self_edges = {source for source, target in architecture_imports
                          if source == target}
            cyclic = [component for component in sccs
                      if len(component) > 1 or component[0] in self_edges]
            memberships = {member: component for component in sccs
                           for member in component}
            non_singletons = [node for node in singleton_sccs
                              if len(memberships.get(node, [])) != 1
                              or node in self_edges]
            if non_singletons:
                errors.append("required singleton SCCs are cyclic: "
                              + ", ".join(sorted(non_singletons)))
        except ValueError as exc:
            errors.append(str(exc))

    configured = bool(parsed["configured"])
    if configured and not nodes:
        errors.append("architecture_decomposition declares no nodes")
    if configured and not architecture_edge_rows:
        errors.append("architecture_decomposition declares no semantic edges")
    complete = bool(configured and not errors and not truncated)
    import_rows = [{"from": source, "to": target, "kind": "imports"}
                   for source, target in sorted(architecture_imports)]
    proof = {
        "schema": ARCHITECTURE_MAP_SCHEMA,
        "configured": configured,
        "status": ("complete" if complete else
                   "incomplete" if configured or errors else "not-requested"),
        "complete": complete,
        "truncated": truncated,
        "node_count": len(nodes),
        "edge_count": len(architecture_edge_rows),
        "current_design_edge_count": len(design_edge_rows),
        "node_bound": node_limit,
        "edge_bound": edge_limit,
        "declared_nodes": sorted(node_ids),
        "node_details": sorted(node_details, key=lambda row: row["id"]),
        "declared_edges": bounded_architecture_edges,
        "observed_edges": (bounded_architecture_edges if complete else []),
        "current_design_edges": bounded_design_edges,
        "architecture_import_edges": import_rows,
        "required_singleton_sccs": sorted(singleton_sccs),
        "sccs": sccs,
        "cyclic_sccs": cyclic,
        "errors": errors,
        "source": "design/contract.json#/architecture_decomposition",
        "source_fingerprint": _design_file_fingerprint(ws),
        "accepted_authority": {
            "schema": ARCHITECTURE_AUTHORITY_FLOOR_SCHEMA,
            "decision_record": str(parsed.get("decision_record") or ""),
            "content_fingerprint": str(
                (parsed.get("authority_floor") or {}).get(
                    "content_fingerprint") or ""),
        },
        "current_design_authority": {
            "schema": CURRENT_GRAPH_AUTHORITY_FLOOR_SCHEMA,
            "requirement": str(parsed.get("requirement") or ""),
            "edge_fingerprint": str(
                (parsed.get("graph_authority_floor") or {}).get(
                    "edge_fingerprint") or ""),
        },
        "semantic_endpoint_registry": {
            "schema": SEMANTIC_ENDPOINT_REGISTRY_SCHEMA,
            "count": len(_SEMANTIC_ENDPOINT_REGISTRY),
            "fingerprint": _SEMANTIC_ENDPOINT_REGISTRY_FINGERPRINT,
        },
    }
    material = dict(proof)
    proof["fingerprint"] = hashlib.sha256(json.dumps(
        material, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode("utf-8")).hexdigest()
    return proof


# ------------------------------------------------------- reference resolution
#
# D-0015. The graph modelled IMPORTS and nothing else, so on a repo whose
# components talk to each other by NAMING each other — a skill dispatching an
# agent, an agent applying a lens, a module reading a schema or a routing
# catalog — it reported 11 edges for a tree with well over a hundred real
# relationships. "What depends on this?" had no answer for the half of the
# codebase that is not source code.
#
# This is RESOLUTION, not pattern-matching. The regex below is a cheap sieve
# for path-SHAPED tokens; it decides nothing. A token becomes an edge only
# when it resolves to a file that actually exists in this tree — the same
# contract `_js_imports` already honours for relative specifiers. A reference
# to a file that is not there produces nothing, so the scanner cannot invent a
# dependency out of prose.
#
# Two deliberate restrictions:
#   * a CODE file's reference only counts when the target is an ARTIFACT.
#     Code-to-code dependency is the import scanners' job and they resolve it
#     properly; a path in a string literal is usually a fixture or a message.
#   * a target at the repo ROOT is skipped, for the reason in `_is_artifact`:
#     it would land in `(root)`, an id that describes nothing.
# The backslash is in the class on purpose: an artifact authored on Windows
# writes `agents\reviewer.md`, and a token that stopped at the separator would
# resolve a DIFFERENT set of edges on the two hosts. Candidates are normalized
# to '/' before lookup, so both spellings reach the same file or neither does.
_REF_TOKEN = re.compile(
    r"[A-Za-z0-9_@.][A-Za-z0-9_./@+\\-]{0,180}\.[A-Za-z0-9]{1,6}")

# Artifact directories whose contents are DISPATCHED rather than read, and
# the wider set of directories whose artifacts DO the dispatching. A skill
# naming an agent is a control transfer (`calls`); a design note naming the
# same agent is a data read (`uses`), which is why the SOURCE is tested too —
# without it the graph claimed `docs -calls-> agents`.
#
# Both kinds are in DEPENDENCY_EDGE_KINDS, so a mis-graded one never changes
# a blast radius, only how the relationship reads. That is exactly why a
# naming convention is acceptable HERE and nowhere that decides whether an
# edge exists at all — every edge below is resolved against a real file.
DISPATCH_DIRS = frozenset({"agents", "agent", "commands", "command"})
EXECUTABLE_DIRS = DISPATCH_DIRS | frozenset({
    "skills", "skill", "hooks", "workflows", "workflow"})


def _ref_kind(source: str, target: str) -> str:
    def _segs(p):
        return set(posixpath.dirname(str(p)).split("/"))
    return ("calls" if (_segs(target) & DISPATCH_DIRS)
            and (_segs(source) & EXECUTABLE_DIRS) else "uses")


def _file_refs(src: str, relpath: str, file_index, artifact_only: bool) -> set:
    """Repo files this file NAMES, each verified to exist in the tree."""
    out = set()
    here = posixpath.dirname(relpath)
    for tok in set(_REF_TOKEN.findall(src or "")):
        cands = [tok.replace("\\", "/").lstrip("./")]
        if here:
            cands.append(posixpath.normpath(posixpath.join(here, tok)))
        for cand in cands:
            if (cand == relpath or cand not in file_index
                    or not posixpath.dirname(cand)):
                continue
            if artifact_only and not _is_artifact(cand):
                continue
            out.add(cand)
            break
    return out


def _is_artifact(relpath: str) -> bool:
    """A non-code file that is itself product surface (D-0016).

    A file at the REPO ROOT is excluded. There is no directory to name it
    after, so it would land in the catch-all `(root)` module — an id that
    describes nothing and routes lenses at a pile of unrelated top-level
    config. Root-level CODE still mints `(root)` exactly as before: this is
    a rule about which NEW files are admitted, not a change to old ids.
    """
    rel = str(relpath or "").replace("\\", "/")
    return bool(posixpath.dirname(rel)) and rel.endswith(ARTIFACT_EXT)


def _graph_scan_quality(base_failures: list[dict], dstats: dict | None,
                        architecture: dict | None, *, decompose: bool,
                        scanned_revision: str) -> dict:
    """Combine producer reports without letting decomposition mask base AST."""
    base = [copy.deepcopy(row) for row in base_failures]
    for row in base:
        row["producer"] = "base-scanner"
    decomposition = [copy.deepcopy(row)
                     for row in ((dstats or {}).get("failures") or [])]
    for row in decomposition:
        row["producer"] = "decomposition"
    if decompose and (dstats or {}).get("error") and not decomposition:
        decomposition.append({
            "producer": "decomposition",
            "file": "",
            "module": "(graph)",
            "parser": "decomposition",
            "error_class": "DecompositionError",
            "reason": " ".join(str(dstats["error"]).split())[:240],
            "file_fingerprint": "",
        })
    architecture_failures = []
    if architecture and architecture.get("status") == "incomplete":
        architecture_failures = [{
            "producer": "architecture-map",
            "file": "components.yaml",
            "module": "(architecture-map)",
            "parser": "owner-graph",
            "error_class": "ArchitectureMapIncomplete",
            "reason": str(reason)[:480],
            "file_fingerprint": str(
                architecture.get("source_fingerprint") or ""),
        } for reason in (architecture.get("errors") or [
            "architecture map proof is incomplete"])]
    key = lambda row: (str(row.get("producer") or ""),
                       str(row.get("module") or ""),
                       str(row.get("file") or ""),
                       str(row.get("reason") or ""))
    base.sort(key=key)
    decomposition.sort(key=key)
    architecture_failures.sort(key=key)
    failures = sorted(base + decomposition + architecture_failures, key=key)
    return _fingerprinted_scan_quality({
        "schema": GRAPH_SCAN_QUALITY_SCHEMA,
        "degraded": bool(failures),
        "mode": "components" if decompose else "modules",
        "scanned_revision": str(scanned_revision or ""),
        "affected_modules": sorted({str(row.get("module") or "")
                                    for row in failures
                                    if str(row.get("module") or "")}),
        "failures": failures,
        "producers": {
            "base-scanner": {
                "status": "degraded" if base else "complete",
                "failures": base,
            },
            "decomposition": {
                "status": ("degraded" if decomposition else "complete")
                if decompose else "not-requested",
                "failures": decomposition,
            },
            "architecture-map": {
                "status": ((architecture or {}).get("status")
                           or "not-requested"),
                "failures": architecture_failures,
            },
        },
        "recovery": GRAPH_SCAN_RECOVERY,
    })


def _scan_locked(ws: str, into: dict | None = None,
                 decompose: bool = False) -> dict:
    prev = load(ws)
    files, code_files, artifact_files = {}, [], []
    excludes, exclude_err = load_excludes(ws)
    import path_roles as _pr
    listed = _git_candidates(ws)
    if listed is not None:
        # Git work tree: .gitignore is authoritative. Still drop the
        # loop-owned/runtime dirs and any COMMITTED vendored tree.
        for rel in listed:
            parts = rel.split("/")
            if any(seg in SKIP_DIRS or seg.startswith(".tp-")
                   for seg in parts[:-1]):
                continue
            if rel.startswith("knowledge/"):
                continue
            if _pr.is_excluded(rel, excludes):
                continue
            if rel.endswith(CODE_EXT):
                code_files.append(rel)
            elif _is_artifact(rel):
                artifact_files.append(rel)
            files[rel] = True
    else:
        for root, dirs, names in os.walk(ws):
            # Sort in place so the walk order is deterministic — otherwise
            # the first-seen-wins basename/namespace maps below depend on
            # filesystem order, making a bare `import utils` resolve
            # non-reproducibly when two files share a basename.
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS
                             and not d.startswith(".tp-"))
            for n in sorted(names):
                # os.walk yields host separators; git ls-files (the other
                # enumeration path) yields '/'. Normalize here so the two
                # produce identical ids and every downstream '/'-shaped
                # glob keeps matching on Windows.
                rel = os.path.relpath(os.path.join(root, n), ws).replace(
                    os.sep, "/")
                if rel.startswith("knowledge/"):
                    continue
                if _pr.is_excluded(rel, excludes):
                    continue
                if n.endswith(CODE_EXT):
                    code_files.append(rel)
                elif _is_artifact(rel):
                    artifact_files.append(rel)
                files[rel] = True

    # D-0007: what the repo CALLS its own modules, before anything is named.
    # Every id minted below this line goes through `_mod`, so the scan cannot
    # end up with one call site using the declared id and another the guess.
    def _read_text(rel):
        try:
            with open(os.path.join(ws, rel), encoding="utf-8",
                      errors="replace") as fh:
                return fh.read()
        except OSError:
            return None

    manifests = manifest_modules(files, _read_text)
    # The root module path is a PREFIX, never a module id. Leaving it in the
    # membership set would let `_declared_target` match it by walking an
    # import path up to the repo root and return the whole repository as one
    # module — the collapse manifest_modules explicitly refuses to cause.
    root_mod = manifests.get(ROOT_MODULE_KEY)
    declared_ids = {v for k, v in manifests.items() if k != ROOT_MODULE_KEY}

    def _mod(rel):
        return module_of(rel, manifests)

    # stem/dir → module map for python import resolution: covers
    # `import src.db.conn`, `from src.db import conn` (package dir), and
    # bare `import conn` (basename).
    known_stems = {}
    for f in code_files:
        stem = f.rsplit(".", 1)[0]
        known_stems[stem] = _mod(f)
        known_stems.setdefault(posixpath.basename(stem), _mod(f))
        d = posixpath.dirname(f)
        while d:
            # resolve dir stems through module_of so import targets land on
            # the SAME feature module a file does (consistent edge endpoints)
            known_stems.setdefault(d, _mod(d + "/_"))
            d = posixpath.dirname(d)

    # declaration maps (C# namespaces / Java packages → module), first pass
    ns_map, pkg_map, sources = {}, {}, {}
    for rel in code_files:
        if rel.endswith((".cs", ".java")):
            try:
                with open(os.path.join(ws, rel), encoding="utf-8",
                          errors="replace") as fh:
                    sources[rel] = fh.read()
            except OSError:
                continue
            if rel.endswith(".cs"):
                for ns in _cs_declared(sources[rel]):
                    ns_map.setdefault(ns, _mod(rel))
            else:
                for pkg in _java_declared(sources[rel]):
                    pkg_map.setdefault(pkg, _mod(rel))

    file_entries, edges = {}, set()
    base_failures: list[dict] = []
    ref_rows: list = []          # (file, module, [resolved target files])
    prev_files = prev.get("files", {})
    for rel in code_files:
        full = os.path.join(ws, rel)
        cached = prev_files.get(rel)
        mod = _mod(rel)
        try:
            st = os.stat(full)
            size, mtime = st.st_size, int(st.st_mtime)
            mtime_ns = st.st_mtime_ns
        except OSError:
            size = mtime = mtime_ns = None
        # Nanosecond-mtime+size short-circuit: an unchanged file keeps its
        # cached hash,
        # imports AND edges WITHOUT being re-read or re-hashed. This is what
        # makes a rescan scale with the DIFF, not the whole tree — on a big
        # repo the em-gate true-up and retro no longer re-hash every file.
        if (cached and size is not None and cached.get("size") == size
                and cached.get("mtime_ns") == mtime_ns
                and "imports" in cached
                and "refs" in cached
                and (not rel.endswith(".py")
                     or cached.get("parse_checked") is True)
                and not (rel.endswith(".py") and
                         isinstance(cached.get("parse_failure"), dict))):
            imports = set(cached["imports"])
            imports.discard(mod)
            refs = list(cached["refs"])
            file_entries[rel] = {"hash": cached.get("hash", ""),
                                 "imports": sorted(imports), "refs": refs,
                                 "size": size, "mtime": mtime,
                                 "mtime_ns": mtime_ns}
            if rel.endswith(".py"):
                file_entries[rel]["parse_checked"] = True
                failure = cached.get("parse_failure")
                if isinstance(failure, dict):
                    failure = copy.deepcopy(failure)
                    failure["module"] = mod
                    failure["producer"] = "base-scanner"
                    file_entries[rel]["parse_failure"] = failure
                    base_failures.append(failure)
            for target in imports:
                edges.add((mod, target, "imports"))
            ref_rows.append((rel, mod, refs))
            continue
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                src = fh.read()
        except OSError:
            continue
        digest = hashlib.sha1(src.encode()).hexdigest()[:12]
        parse_failure = None
        if (cached and cached.get("hash") == digest and "refs" in cached
                and (not rel.endswith(".py")
                     or cached.get("parse_checked") is True)):
            imports = set(cached["imports"])
            if rel.endswith(".py") and isinstance(
                    cached.get("parse_failure"), dict):
                parse_failure = copy.deepcopy(cached["parse_failure"])
        elif rel.endswith(".py"):
            imports, parse_failure = _py_imports_checked(
                src, rel, known_stems)
        elif rel.endswith(".go"):
            # PARTIAL COVERAGE (mirrors the Ruby autoloading note). A Go
            # import path is resolvable to an internal module exactly when
            # the repo DECLARES that path in a go.mod — which D-0007 now
            # reads. `import "acme/billing"` in a repo carrying
            # svc/billing/go.mod (`module acme/billing`) is an intra-repo
            # edge and is emitted as one. Everything else still lands as
            # ext:<last-segment>: with no declaration there is nothing to
            # match against, so do NOT fabricate an edge. The residual gap
            # stays disclosed in meta.scanners.go.limitation, and the rest
            # can be recorded with record_edge / `tp graph edge`.
            #
            # v2.11.0 closes the case that matters most: a repo with ONE
            # root go.mod — the ordinary Go layout. An import under that
            # module path is intra-repo by construction. v2.10.0 taught
            # manifest_modules to READ the root module path and then wired
            # the prefix-stripping into the JS resolver ONLY, so this branch
            # still answered ext: for every internal import and `graph
            # impact` reported 2 modules with no call structure on a
            # 256-module repo. It now uses the same resolution the JS path
            # uses, so there is ONE rule for "this import is ours".
            imports = set()
            for block, single in re.findall(
                    r'import\s+\(([^)]*)\)|import\s+"([^"]+)"', src, re.S):
                for t in ([single] if single
                          else re.findall(r'"([^"]+)"', block)):
                    inside = _declared_target(t, declared_ids)
                    if not inside:
                        rel_in = strip_root_prefix(t, root_mod)
                        if rel_in:
                            inside = module_of(rel_in + "/_", manifests)
                    imports.add(inside if inside
                                else "ext:" + t.split("/")[-1])
        elif rel.endswith(".cs"):
            imports = _cs_imports(src, ns_map)
        elif rel.endswith(".java"):
            imports = _java_imports(src, pkg_map)
        elif rel.endswith(".rb"):
            imports = _ruby_imports(src, rel, set(files), manifests)
        else:
            imports = _js_imports(src, rel, set(files), manifests,
                                  declared_ids, root_mod)
        imports.discard(mod)
        refs = sorted(_file_refs(src, rel, files, artifact_only=True))
        file_entries[rel] = {"hash": digest, "imports": sorted(imports),
                             "refs": refs, "size": size, "mtime": mtime,
                             "mtime_ns": mtime_ns}
        if rel.endswith(".py"):
            file_entries[rel]["parse_checked"] = True
            if parse_failure is not None:
                parse_failure = copy.deepcopy(parse_failure)
                parse_failure["module"] = mod
                parse_failure["producer"] = "base-scanner"
                file_entries[rel]["parse_failure"] = parse_failure
                base_failures.append(parse_failure)
        for target in imports:
            edges.add((mod, target, "imports"))
        ref_rows.append((rel, mod, refs))

    # D-0016: artifacts enter the graph as FILES too, not just as a node.
    # `graph["files"]` is what decomposition walks, so a node with no files
    # under it is a node the review still cannot look inside. They carry no
    # imports — the empty list is the honest answer, not a placeholder — and
    # they go through the same mtime+size cache so a rescan stays diff-sized.
    for rel in artifact_files:
        full = os.path.join(ws, rel)
        cached = prev_files.get(rel)
        try:
            st = os.stat(full)
            size, mtime = st.st_size, int(st.st_mtime)
            mtime_ns = st.st_mtime_ns
        except OSError:
            size = mtime = mtime_ns = None
        if (cached and size is not None and cached.get("size") == size
                and cached.get("mtime_ns") == mtime_ns
                and "refs" in cached):
            refs = list(cached["refs"])
            file_entries[rel] = {"hash": cached.get("hash", ""),
                                 "imports": [], "refs": refs,
                                 "size": size, "mtime": mtime,
                                 "mtime_ns": mtime_ns,
                                 "artifact": True}
            ref_rows.append((rel, _mod(rel), refs))
            continue
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                src = fh.read()
        except OSError:
            continue
        refs = sorted(_file_refs(src, rel, files, artifact_only=False))
        file_entries[rel] = {
            "hash": hashlib.sha1(src.encode()).hexdigest()[:12],
            "imports": [], "refs": refs, "size": size, "mtime": mtime,
            "mtime_ns": mtime_ns, "artifact": True}
        ref_rows.append((rel, _mod(rel), refs))

    # manifests: .csproj project/package references, Gemfile gems
    for rel in files:
        if rel.endswith(".csproj"):
            with open(os.path.join(ws, rel), encoding="utf-8",
                      errors="replace") as fh:
                text = fh.read()
            mod = _mod(rel)
            for pref in _CSPROJ_PROJ.findall(text):
                tgt = posixpath.normpath(posixpath.join(
                    posixpath.dirname(rel), pref.replace("\\", "/")))
                edges.add((mod, _mod(tgt), "project_ref"))
            for pkg in _CSPROJ_PKG.findall(text):
                edges.add((mod, "ext:" + pkg.split(".")[0], "imports"))
        elif posixpath.basename(rel) == "pom.xml" and posixpath.dirname(rel):
            with open(os.path.join(ws, rel), encoding="utf-8",
                      errors="replace") as fh:
                text = fh.read()
            mod = _mod(rel)
            for block in _POM_DEP.findall(text):
                art = _POM_ARTIFACT.search(block)
                if not art:
                    continue
                scope = _POM_SCOPE.search(block)
                if scope and scope.group(1).lower() in ("test", "provided",
                                                        "system"):
                    continue      # not a runtime dependency of the product
                edges.add((mod, "ext:" + art.group(1), "imports"))
        elif posixpath.basename(rel) == "Gemfile":
            with open(os.path.join(ws, rel), encoding="utf-8",
                      errors="replace") as fh:
                for gem in _GEMFILE_GEM.findall(fh.read()):
                    edges.add((_mod(rel), "ext:" + gem, "imports"))

    # infra: docker-compose services
    for rel in files:
        if re.search(r"docker-compose[^/]*\.ya?ml$", rel):
            with open(os.path.join(ws, rel), encoding="utf-8",
                      errors="replace") as fh:
                for svc in _compose_services(fh.read()):
                    sid = f"svc:{svc['name']}"
                    for dep in svc["depends_on"]:
                        edges.add((sid, f"svc:{dep}", "depends_on"))
                    edges.add((sid, _mod(rel), "defined_in"))

    # D-0015: the resolved references become dependency edges. A reference
    # inside the SAME module is not a relationship between components — it is
    # a file naming its neighbour — so self-edges are dropped, exactly as the
    # import scanners do.
    for _rel, mod, refs in ref_rows:
        for target in refs:
            tmod = _mod(target)
            if tmod and tmod != mod:
                edges.add((mod, tmod, _ref_kind(_rel, target)))

    # Stale-edge filter: a deleted module must not survive as an edge target
    # via some UNCHANGED importer's cached import list (the mtime+size cache
    # above keeps imports without re-reading the file). Keep an edge iff its
    # target still resolves in the CURRENT tree. The resolvable universe is
    # known_stems.values() — every module a current file, package dir, or
    # ancestor dir resolves to — plus the module of every walked file (compose
    # files, manifests). NOT just leaf modules-with-code-files: a legitimate
    # parent-package import (`import src`) targets a dir-level module that
    # owns no files directly, and filtering to leaf modules would drop it.
    resolvable = set(known_stems.values())
    resolvable.update(_mod(rel) for rel in files)
    edges = {(a, b, k) for (a, b, k) in edges
             if b.startswith(("ext:", "svc:")) or b in resolvable}

    # H-31: the accepted Design decomposition is a production graph input,
    # not inert documentation. The proof is exact and fail-closed: no bounded
    # prefix, unknown path/identity/edge, or declared SCC drift can pass.
    architecture = architecture_map_proof(
        ws, known_files=set(files))

    modules = {}
    for rel in code_files + artifact_files:
        m = _mod(rel)
        modules.setdefault(m, {"kind": "module", "files": 0})
        modules[m]["files"] += 1
    for a, b, _k in edges:
        for x in (a, b):
            if x.startswith("ext:"):
                modules.setdefault(x, {"kind": "external", "files": 0})
            elif x.startswith("svc:"):
                modules.setdefault(x, {"kind": "infra", "files": 0})
            else:
                modules.setdefault(x, {"kind": "module", "files": 0})

    # Scanner-coverage disclosure lives IN the graph payload so any impact
    # consumer (readiness/impact return meta; dashboards read it) can see
    # when coverage is partial rather than trusting a near-empty blast
    # radius. It never blocks DoR — honesty, not a new gate.
    scanners_meta = {}
    if any(rel.endswith(".go") for rel in code_files):
        # A ROOT go.mod declares the repo's module path but is deliberately
        # not a manifest entry (it would collapse the repo into one node —
        # see ROOT_MODULE_KEY), so the `dirname in manifests` test called
        # the ordinary single-module Go repo "external-only" even while its
        # imports resolved. Either form of declaration counts.
        go_declared = bool(root_mod) or any(
            posixpath.basename(rel) == "go.mod"
            and posixpath.dirname(rel) in manifests
            for rel in files)
        scanners_meta["go"] = (
            {"coverage": "declared-modules",
             "limitation": _GO_LIMITATION_DECLARED} if go_declared else
            {"coverage": "external-only", "limitation": _GO_LIMITATION})
    # Narrowing the graph is disclosed IN the payload, same as the Go
    # scanner's partial coverage: an impact consumer must be able to see
    # that the blast radius was scoped by declaration rather than trust a
    # small answer. A malformed components.yaml is reported here too, so
    # "my exclusions did nothing" is visible instead of silent.
    # A graph that now contains markdown and declarative JSON should SAY so:
    # a reviewer reading a module list needs to know whether "12 files" means
    # twelve source files or eight source files and four skills.
    if artifact_files:
        scanners_meta["artifacts"] = {"extensions": list(ARTIFACT_EXT),
                                      "files": len(artifact_files)}
    if excludes:
        scanners_meta["excluded"] = {"declared_in": "components.yaml",
                                     "prefixes": sorted(excludes)}
    if exclude_err:
        scanners_meta["exclude_error"] = exclude_err
    meta: dict = {"scanners": scanners_meta} if scanners_meta else {}
    # D-0007: PUBLISH the map, do not just use it. Anything that turns a
    # changed FILE into a module id — impact, completion, lens routing —
    # must resolve it the way the scan did, or it looks up `packages/ui` in
    # a graph that only knows `@acme/ui` and reports an empty blast radius.
    if manifests:
        meta["module_ids"] = dict(sorted(manifests.items()))
    if architecture.get("status") != "not-requested":
        meta["architecture_map"] = architecture
    g = {
        "modules": modules,
        "edges": sorted([{"from": a, "to": b, "kind": k}
                         for a, b, k in edges],
                        key=lambda e: (e["from"], e["to"])),
        "files": file_entries,
        "recorded": prev.get("recorded", []),
        "meta": meta,
    }
    if architecture.get("complete"):
        for node in architecture["node_details"]:
            kind = node["kind"]
            public_kind = {
                "external-host": "external", "contract": "contract",
                "resource": "resource", "producer": "surface",
            }.get(kind, "component")
            g["modules"][node["id"]] = {
                "kind": public_kind, "files": len(node["matched_files"]),
                "paths": node["matched_files"],
                "declared_by": architecture["source"],
            }
        g["edges"].extend({
            "from": edge["from"], "to": edge["to"],
            "kind": edge["kind"],
            "reason": edge["reason"],
            "source": ("design/contract.json#/architecture_decomposition/"
                       "semantic_edges"),
            "confidence": "high", "declared": True,
        } for edge in architecture["declared_edges"])
        g["edges"].extend({
            "from": edge["from"], "to": edge["to"],
            "kind": edge["kind"], "reason": edge["reason"],
            "source": "design/contract.json#/graph/proposed_edges",
            "confidence": "high", "declared": True,
        } for edge in architecture["current_design_edges"])
        g["edges"].extend({
            "from": edge["from"], "to": edge["to"],
            "kind": edge["kind"],
            "source": ("design/contract.json#/architecture_decomposition/"
                       "observed-imports"),
            "confidence": "high", "observed": True,
        } for edge in architecture["architecture_import_edges"])
    # merge agent-recorded edges (never dropped by rescans)
    g["edges"] += [e for e in g["recorded"]
                   if not any(x["from"] == e["from"] and x["to"] == e["to"]
                              and x["kind"] == e["kind"] for x in g["edges"])]
    # Every edge states its provenance.  High graph priority is only safe when
    # a reviewer can distinguish deterministic scanner output from a human- or
    # agent-recorded runtime relationship.
    for e in g["edges"]:
        recorded = bool(e.get("recorded"))
        e.setdefault("source", "recorded" if recorded else "scanner")
        e.setdefault("confidence", "medium" if recorded else "high")
        for node in (e["from"], e["to"]):
            g["modules"].setdefault(
                node, {"kind": _node_kind(node), "files": 0})
    # v2.0.0: unify ext:X with an INTERNAL module named X. An import the
    # resolver could not map to a file (e.g. `from core import hub` where
    # core/ is a package dir) used to become a dangling ext: node - and
    # every consumer (impact, hub signal, blast radius) undercounted the
    # real dependents of that internal module.
    internal = {m for m, meta in g["modules"].items()
                if not m.startswith(("ext:", "svc:", "req:"))}
    for e in g["edges"]:
        for side in ("from", "to"):
            v = e[side]
            if v.startswith("ext:") and v[4:] in internal:
                e[side] = v[4:]
    referenced = {e["from"] for e in g["edges"]}
    referenced |= {e["to"] for e in g["edges"]}
    for m in [m for m in g["modules"]
              if m.startswith("ext:") and m[4:] in internal
              and m not in referenced]:
        del g["modules"][m]
    # dedupe edges that collapsed onto an existing internal edge
    seen, uniq = set(), []
    for e in g["edges"]:
        k = (e["from"], e["to"], e["kind"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    g["edges"] = uniq
    # ---- component layer (R-0003, contract:component-map). ADDITIVE only:
    # --decompose derives/refreshes it; a plain scan CARRIES an existing
    # layer forward (like `recorded`) and a never-decomposed graph gets no
    # `components` key — that path stays byte-identical to the legacy scan.
    dstats = None
    if decompose:
        try:
            comps, dstats = graph_decomposition.derive(ws, g, prev)
            g["components"] = comps
            g["meta"]["decompose"] = {"floors": dstats.get("floors_hash", "")}
        except Exception as e:   # fail-open: never crash the scan
            dstats = {"components": 0, "recomputed": 0, "cache_hits": 0,
                      "floor_folded": 0, "error": f"decompose failed: {e}"}
            if "components" in prev:
                g["components"] = copy.deepcopy(prev["components"])
                pd = (prev.get("meta") or {}).get("decompose")
                if pd is not None:
                    g["meta"]["decompose"] = copy.deepcopy(pd)
    elif "components" in prev:
        g["components"] = copy.deepcopy(prev["components"])
        pd = (prev.get("meta") or {}).get("decompose")
        if pd is not None:
            g["meta"]["decompose"] = copy.deepcopy(pd)
    g["meta"]["graph_scan_quality"] = _graph_scan_quality(
        base_failures, dstats, architecture, decompose=decompose,
        scanned_revision=tp.git_head(ws) or "")
    if into is not None:
        # Active batch: replace the batched graph's contents in place so the
        # batch's single flush persists this scan (identity preserved).
        into.clear()
        into.update(g)
        g = into
    _stamp_meta(ws, g, scanned=True)
    if dstats is not None:
        payload = {k: dstats.get(k, 0) for k in
                   ("components", "recomputed", "cache_hits", "floor_folded")}
        if dstats.get("error"):
            payload["error"] = dstats["error"]
        tp.trace(ws, "graph_decompose", **payload)
    if into is None:
        # --decompose no-change rescan is a NO-OP: when nothing but the
        # volatile meta timestamps moved, skip the write so graph.json stays
        # byte-identical (the fingerprint-cache acceptance criterion).
        if (decompose and os.path.exists(os.path.abspath(_path(ws)))
                and _scan_volatile_stripped(g)
                == _scan_volatile_stripped(prev)):
            tp.trace(ws, "graph_scan", modules=len(modules),
                     edges=len(g["edges"]), files=len(file_entries))
            return prev
        save(ws, g)
    tp.trace(ws, "graph_scan", modules=len(modules), edges=len(g["edges"]),
             files=len(file_entries))
    return g


@contextlib.contextmanager
def _mutation(ws: str):
    """One graph read-modify-write: honor an active batch() (mutate its
    in-memory graph, defer the flush) or lock + load + stamp + atomic save.
    Serializing under the graph.json lock is what stops a concurrent
    scan() + record_edge() pair from silently losing the recorded edge."""
    p = os.path.abspath(_path(ws))
    if p in _BATCH:
        yield _BATCH[p]
        return
    with tp.file_lock(p):
        _GRAPH_CACHE.pop(p, None)          # re-read under the lock
        g = load(ws)
        try:
            yield g
        except BaseException:
            _GRAPH_CACHE.pop(p, None)      # partial mutations — not truth
            raise
        _stamp_meta(ws, g)
        save(ws, g)


def record_edge(ws: str, src: str, dst: str, kind: str = "runtime",
                note: str = "", confidence: str = "medium") -> dict:
    """An agent-observed dependency static analysis can't see (HTTP call,
    queue, cron, deploy relationship). Survives rescans."""
    if confidence not in ("high", "medium", "low"):
        raise ValueError("confidence must be high, medium, or low")
    e = {"from": src, "to": dst, "kind": kind, "note": note,
         "recorded": True, "source": "recorded", "confidence": confidence}
    def same(x):
        return (x.get("from"), x.get("to"), x.get("kind")) == (src, dst, kind)
    with _mutation(ws) as g:
        g["recorded"] = [x for x in g.get("recorded", []) if not same(x)] + [e]
        g["edges"] = [x for x in g.get("edges", []) if not same(x)] + [e]
        for x in (src, dst):
            g["modules"].setdefault(x, {"kind": _node_kind(x), "files": 0})
    tp.trace(ws, "graph_edge_recorded", src=src, dst=dst, kind=kind)
    return e


# ----------------------------------------------------------- product layer
# The graph carries BOTH sides of the system: engineering nodes (modules,
# svc:/ext: infra) and product nodes (req:R-XXXX). Edges:
#   req:R -[planned]->  module   what the plan intends to touch (plan gate)
#   req:R -[realizes]-> module   what actually realizes it (trued-up at EM)
#   req:R -[depends]->  req:R'   product dependency between requirements
# Because impact() walks reverse edges generically, requirements appear in a
# change's blast radius automatically — and contracts/evaluation query the
# product side without any extra machinery.

def modules_for_scope(scope_globs, manifests: dict | None = None) -> list:
    """Map scope globs/paths to graph modules (glob prefix → module).

    Pass `manifests` (declared_module_ids(graph)) wherever the result is
    compared against graph ids: without it a scope of `packages/ui/**` in a
    workspace resolves to `ui`, which the graph does not contain.
    """
    mods = set()
    for g in scope_globs or []:
        prefix = g.split("*", 1)[0].rstrip("/")
        if not prefix:
            continue
        mods.add(module_of(prefix, manifests)
                 if "." in posixpath.basename(prefix)
                 else module_of(prefix + "/_", manifests))
    return sorted(mods)


def scope_modules(ws: str, scope_globs) -> list:
    """`modules_for_scope` with the workspace's DECLARED ids applied.

    Prefer this at every call site that has a workspace. The `manifests`
    argument is easy to forget, and forgetting it is silent: the scope
    resolves to a path-derived id the graph does not contain, so the blast
    radius comes back empty and the gate reads that as "nothing impacted".
    """
    return modules_for_scope(scope_globs, declared_module_ids(load(ws)))


def req_node(rid: str) -> str:
    """Public requirement-node id (L15, v2.2.1)."""
    return _req_node(rid)


def _req_node(rid: str) -> str:
    return rid if rid.startswith("req:") else f"req:{rid}"


def link_requirement(ws: str, rid: str, files, kind: str = "realizes",
                     replace: bool = True) -> dict:
    """Maintain the req→module edges for one requirement. `files` may be
    real paths or scope globs. replace=True refreshes that requirement's
    edges of this kind (the true-up), so the product side never goes stale."""
    node = _req_node(rid)
    with _mutation(ws) as g:
        mods = sorted(set(modules_for_scope(files, declared_module_ids(g))))
        if replace:
            drop = lambda e: e["from"] == node and e["kind"] == kind
            g["recorded"] = [e for e in g["recorded"] if not drop(e)]
            g["edges"] = [e for e in g["edges"] if not drop(e)]
        for m in mods:
            e = {"from": node, "to": m, "kind": kind, "note": "",
                 "recorded": True, "source": "requirement",
                 "confidence": "high"}
            g["recorded"].append(e)
            g["edges"].append(e)
            g["modules"].setdefault(m, {"kind": "module", "files": 0})
        g["modules"].setdefault(node, {"kind": "requirement", "files": 0})
    tp.trace(ws, "graph_req_link", requirement=node, kind=kind, modules=mods)
    return {"requirement": node, "kind": kind, "modules": mods}


def link_requirement_dep(ws: str, rid: str, depends_on: str,
                         note: str = "") -> dict:
    """Product dependency: req:rid depends on req:depends_on."""
    return record_edge(ws, _req_node(rid), _req_node(depends_on),
                       kind="depends", note=note, confidence="high")


def product_impact(ws: str, changed_files) -> dict:
    """The product side of blast radius: which requirements' planned or
    realized surface a change touches, plus requirements that DEPEND on
    those (one hop up the product graph). Zero tokens, like impact()."""
    g = load(ws)
    items = list(changed_files or [])
    # accept file paths OR already-resolved module names
    mods = {module_of(f, declared_module_ids(g)) for f in items} | set(items)
    direct = sorted({e["from"] for e in g["edges"]
                     if e["from"].startswith("req:")
                     and e["kind"] in ("planned", "realizes")
                     and e["to"] in mods})
    rev = {}
    for e in g["edges"]:
        if (e["kind"] == "depends" and e["from"].startswith("req:")
                and e["to"].startswith("req:")):
            rev.setdefault(e["to"], []).append(e["from"])
    upstream = sorted({r for d in direct for r in rev.get(d, [])}
                      - set(direct))
    return {"affected_requirements": direct,
            "dependent_requirements": upstream,
            "modules": sorted(mods)}


# -------------------------------------------------------- governance policy

_DISTRIBUTED_TYPES = {"distributed", "system-design", "service", "migration"}


def contract_ids(source) -> list:
    """Canonical contract-id extraction (M6, v2.2.1). Accepts a task/record
    dict (reads its `contracts`) or a raw contracts list; entries may be
    plain ids or {id: ...} rows. One implementation, everywhere."""
    rows = source.get("contracts") if isinstance(source, dict) else source
    out = []
    for row in rows or []:
        cid = row.get("id") if isinstance(row, dict) else row
        cid = str(cid or "").strip()
        if cid:
            out.append(cid)
    return out


def normalize_policy(policy: dict | None) -> dict:
    """Coerce a policy's depths to safe ints and its boundary to a known
    mode (M2, v2.2.1) — consumers trust this output instead of re-coercing."""
    p = dict(policy or {})
    for key, default, minimum in (("local_depth", 3, 1),
                                  ("contract_depth", 1, 0),
                                  ("requirement_depth", 1, 0)):
        try:
            p[key] = max(minimum, int(p.get(key, default)))
        except (TypeError, ValueError):
            p[key] = default
    if p.get("boundary_mode") not in ("contract-only", "stop", "expand"):
        p["boundary_mode"] = "contract-only"
    return p


def impact_policy(task: dict | None = None) -> dict:
    """Resolve the task's typed dependency-depth policy.

    A numeric hop count alone cannot express a distributed boundary.  The
    defaults walk implementation dependencies inside the current entity, but
    cross an entity only through an explicit contract/resource node.
    """
    task = task or {}
    supplied = dict(task.get("impact_policy") or {})
    distributed = task.get("type") in _DISTRIBUTED_TYPES
    base = {
        "local_depth": 2 if distributed else 3,
        "boundary_mode": "contract-only",
        "contract_depth": 1,
        "requirement_depth": 2 if task.get("high_cost") else 1,
    }
    base.update(supplied)
    return normalize_policy(base)


def aggregate_impact_policy(tasks) -> dict:
    """One fail-closed review radius for a multi-task final review."""
    policies = [impact_policy(t) for t in (tasks or [])]
    if not policies:
        return impact_policy({})
    boundary_rank = {"stop": 0, "contract-only": 1, "expand": 2}
    boundary = max(
        (p.get("boundary_mode", "contract-only") for p in policies),
        key=lambda value: boundary_rank.get(value, 1))
    def number(policy, key, default, minimum):
        try:
            return max(minimum, int(policy.get(key, default)))
        except (TypeError, ValueError):
            return default

    return {
        "local_depth": max(number(p, "local_depth", 3, 1)
                           for p in policies),
        "boundary_mode": boundary,
        "contract_depth": max(number(p, "contract_depth", 1, 0)
                              for p in policies),
        "requirement_depth": max(number(p, "requirement_depth", 1, 0)
                                 for p in policies),
    }


def readiness(ws: str, tasks) -> dict:
    """Graph Definition of Ready for a plan.

    Refreshes the deterministic graph and returns per-task policies, unknown
    surfaces, and fail-closed blockers. A new local module must be explicitly
    declared by the planner; a distributed task without a named, recorded
    contract is not implementation-ready.
    """
    errors, warnings, rows = [], [], []
    try:
        g = scan(ws)
    except Exception as exc:
        return {"passed": False, "errors": [f"graph scan failed: {exc}"],
                "warnings": [], "tasks": [], "graph": {}}
    errors.extend(quality_errors(g))
    for task in tasks or []:
        tid = task.get("id", "?")
        supplied = dict(task.get("impact_policy") or {})
        policy = impact_policy(task)
        if supplied.get("boundary_mode") not in (None, "contract-only",
                                                 "stop", "expand"):
            errors.append(f"task {tid}: invalid graph boundary_mode")
        for _k in ("local_depth", "contract_depth", "requirement_depth"):
            if _k in supplied:
                try:
                    int(supplied[_k])
                except (TypeError, ValueError):
                    errors.append(
                        f"task {tid}: invalid dependency depth policy")
                    break
        mods = modules_for_scope(task.get("scope") or [],
                                 declared_module_ids(g))
        unknown = sorted(m for m in mods if m not in g.get("modules", {}))
        declared_new = set(task.get("new_modules") or [])
        undeclared_unknown = sorted(set(unknown) - declared_new)
        distributed = task.get("type") in _DISTRIBUTED_TYPES
        contracts = list(task.get("contracts") or [])
        task_contract_ids = contract_ids(task)
        if distributed and not contracts:
            errors.append(f"task {tid}: distributed/system work must declare "
                          "its API, event, data, trust, or runtime contracts")
        invalid_contracts = sorted(c for c in task_contract_ids
                                   if not c.startswith(("contract:",
                                                        "resource:")))
        if invalid_contracts:
            errors.append(f"task {tid}: contract ids need contract: or "
                          "resource: prefixes: " + ", ".join(invalid_contracts))
        missing_contracts = sorted(c for c in task_contract_ids
                                   if c not in g.get("modules", {}))
        if missing_contracts:
            errors.append(f"task {tid}: contracts are not recorded in the "
                          "dependency graph: " + ", ".join(missing_contracts))
        if undeclared_unknown:
            # Name the exact remedy field: without it a planner can only
            # discover `new_modules` by reading source.
            errors.append(
                f"task {tid}: new/unknown graph modules were not declared: "
                + ", ".join(undeclared_unknown)
                + " — declare them in the task's \"new_modules\" field in "
                  "plan/tasks.json (e.g. \"new_modules\": "
                + json.dumps(undeclared_unknown) + ")")
        if declared_new - set(unknown):
            warnings.append(f"task {tid}: declared new_modules already exist: "
                            + ", ".join(sorted(declared_new - set(unknown))))
        imp = impact(ws, mods, policy=policy) if mods else None
        rows.append({"task": tid, "modules": mods, "unknown": unknown,
                     "declared_new_modules": sorted(declared_new),
                     "contracts": contracts, "policy": policy,
                     "impact": imp})
    return {"passed": not errors, "errors": errors, "warnings": warnings,
            "tasks": rows, "graph": dict(g.get("meta") or {})}


def completion(ws: str, changed_files, planned_modules=None,
               policy: dict | None = None) -> dict:
    """Graph Definition of Done read model for one realized change."""
    graph = load(ws)
    files = list(changed_files or [])
    actual = sorted({module_of(f, declared_module_ids(graph)) for f in files})
    planned = sorted(set(planned_modules or []))
    imp = impact(ws, files, policy=policy)
    contract_files = sorted(f for f in files if re.search(
        r"(^|/)(openapi|asyncapi|schemas?|contracts?)(/|\.)|"
        r"\.(proto|avsc)$", f, re.I))
    errors = quality_errors(graph)
    if imp.get("unknown"):
        errors.append("graph contains unknown realized modules: "
                      + ", ".join(imp["unknown"]))
    unexpected = sorted(set(actual) - set(planned)) if planned else []
    if unexpected:
        errors.append("realized dependency surface exceeds the approved plan: "
                      + ", ".join(unexpected))
    return {
        "passed": not errors,
        "errors": errors,
        "planned_modules": planned,
        "realized_modules": actual,
        "unexpected_modules": unexpected,
        "unrealized_modules": sorted(set(planned) - set(actual)),
        "contract_files": contract_files,
        "impact": imp,
    }


# ------------------------------------------------------------------ impact

def bounded_changed_symbol_callers(*, snapshot: dict, changed_symbols,
                                   bounds: dict, clock=None) -> dict:
    """Walk a canonical symbol index from callee to callers, once, bounded.

    The snapshot schema is deliberately language-neutral: ``symbol_edges``
    rows name ``caller`` and ``callee`` and may cite a boundary ``contract``.
    Language adapters build that frozen index; this function never reads the
    ambient checkout and therefore cannot drift to a different target while a
    review is running.
    """
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    supplied = dict(bounds or {})

    def limit(name, default):
        try:
            return max(1, int(supplied.get(name, default)))
        except (TypeError, ValueError):
            return default

    max_symbols = limit("max_symbols", 128)
    max_hops = limit("max_hops", 6)
    max_edges = limit("max_edges", 512)
    timeout_seconds = limit("timeout_seconds", 10)
    symbols = sorted({str(s).strip() for s in (changed_symbols or [])
                      if str(s).strip()})
    unresolved = symbols[max_symbols:]
    symbols = symbols[:max_symbols]
    reverse: dict[str, list] = {}
    for raw in snapshot.get("symbol_edges") or []:
        if not isinstance(raw, dict):
            continue
        caller = str(raw.get("caller") or "").strip()
        callee = str(raw.get("callee") or "").strip()
        if not caller or not callee:
            continue
        row = {"caller": caller, "callee": callee}
        contract = str(raw.get("contract") or "").strip()
        if contract:
            row["contract"] = contract
        reverse.setdefault(callee, []).append(row)
    for rows in reverse.values():
        rows.sort(key=lambda row: (row["caller"], row.get("contract", "")))

    monotonic = clock or time.monotonic
    deadline = monotonic() + timeout_seconds
    frontier = list(symbols)
    seen = set(symbols)
    callers, contracts = set(), set()
    examined = 0
    truncated = bool(unresolved)
    timed_out = False
    for _hop in range(1, max_hops + 1):
        current_frontier = sorted(frontier)
        next_frontier = []
        stopped = False
        for callee_index, callee in enumerate(current_frontier):
            rows = reverse.get(callee, [])
            for row_index, row in enumerate(rows):
                if monotonic() >= deadline:
                    timed_out = truncated = True
                    stopped = True
                    break
                examined += 1
                caller = row["caller"]
                callers.add(caller)
                if row.get("contract"):
                    contracts.add(row["contract"])
                if caller not in seen:
                    seen.add(caller)
                    next_frontier.append(caller)
                if examined >= max_edges:
                    # Reaching the numeric bound is complete only when this
                    # was the final reachable edge.  Account for later rows,
                    # later changed symbols, and the next caller frontier.
                    truncated = truncated or (
                        row_index + 1 < len(rows)
                        or any(reverse.get(node)
                               for node in current_frontier[callee_index + 1:])
                        or any(reverse.get(node) for node in next_frontier)
                    )
                    stopped = True
                    break
            if stopped:
                break
        if stopped:
            break
        frontier = sorted(set(next_frontier))
        if not frontier:
            break
    else:
        # ``frontier`` is the next, not the just-processed, hop here.
        if any(reverse.get(node) for node in frontier):
            truncated = True
            unresolved.extend(frontier)
    unresolved.extend(snapshot.get("unresolved_symbols") or [])
    unresolved = sorted({str(v).strip() for v in unresolved if str(v).strip()})
    complete = not truncated and not timed_out and not unresolved
    return {
        "schema": "taskplane.changed-symbol-callers/v1",
        "adapter": "canonical-symbol-index",
        "callers": sorted(callers),
        "contracts": sorted(contracts),
        "unresolved": unresolved,
        "complete": complete,
        "truncated": truncated,
        "timed_out": timed_out,
        "edges_examined": examined,
        "bounds": {"max_symbols": max_symbols, "max_hops": max_hops,
                   "max_edges": max_edges,
                   "timeout_seconds": timeout_seconds},
    }


def impact(ws: str, changed_files, max_depth: int = 3,
           policy: dict | None = None) -> dict:
    """Blast radius of a change: the modules touched, then everything that
    depends on them (reverse edges), by depth. This is what a reviewer needs
    BEFORE reading any code — and it costs zero tokens."""
    g = load(ws)
    policy = dict(policy or {})
    if policy.get("local_depth") is not None:
        try:
            max_depth = max(1, int(policy["local_depth"]))
        except (TypeError, ValueError):
            pass
    resolved_policy = {
        "local_depth": max_depth,
        "boundary_mode": "contract-only",
        "contract_depth": 1,
        "requirement_depth": 1,
    }
    resolved_policy.update(policy)
    try:
        contract_depth = max(0, int(resolved_policy["contract_depth"]))
    except (TypeError, ValueError):
        contract_depth = 1
    try:
        requirement_depth = max(0, int(resolved_policy["requirement_depth"]))
    except (TypeError, ValueError):
        requirement_depth = 1
    boundary_mode = resolved_policy.get("boundary_mode", "contract-only")

    rev = {}
    for e in g["edges"]:
        rev.setdefault(e["to"], []).append((e["from"], e["kind"]))

    # v2.0.0: accept BOTH file paths and module ids - the plan gate and
    # the execute brief pass modules_for_scope() output (module names),
    # which module_of() used to collapse to "(root)", silently zeroing
    # their blast radius.
    _ids = declared_module_ids(g)
    touched = sorted({f if f in g["modules"] else module_of(f, _ids)
                      for f in (changed_files or [])})
    seen = {m: 0 for m in touched}
    # frontier state carries the number of explicit contract/resource and
    # requirement boundaries crossed.  This keeps distributed-system impact
    # at the contract between entities instead of inventing a deep service
    # implementation graph that the repository cannot prove.
    frontier = [(m, 0, 0) for m in touched]
    by_depth, policy_blocked = {}, []
    depth = 0
    while frontier and depth < max_depth:
        depth += 1
        nxt = []
        for m, boundary_hops, requirement_hops in frontier:
            for dep, kind in rev.get(m, []):
                next_boundary = boundary_hops
                next_requirement = requirement_hops
                boundary_pair = _is_boundary(m) or _is_boundary(dep)
                if boundary_pair:
                    allowed_contract = (m.startswith(("contract:", "resource:"))
                                        or dep.startswith(("contract:",
                                                           "resource:")))
                    if (boundary_mode == "stop"
                            or (boundary_mode == "contract-only"
                                and not allowed_contract)):
                        policy_blocked.append({"module": dep, "via": m,
                                               "kind": kind,
                                               "reason": "boundary-policy"})
                        continue
                    next_boundary += 1
                    if next_boundary > contract_depth:
                        policy_blocked.append({"module": dep, "via": m,
                                               "kind": kind,
                                               "reason": "contract-depth"})
                        continue
                if m.startswith("req:") or dep.startswith("req:"):
                    next_requirement += 1
                    if next_requirement > requirement_depth:
                        policy_blocked.append({"module": dep, "via": m,
                                               "kind": kind,
                                               "reason": "requirement-depth"})
                        continue
                if dep not in seen:
                    seen[dep] = depth
                    by_depth.setdefault(depth, []).append(
                        {"module": dep, "via": m, "kind": kind})
                    nxt.append((dep, next_boundary, next_requirement))
        frontier = nxt
    depth_truncated = any(
        dep not in seen for m, _bh, _rh in frontier
        for dep, _kind in rev.get(m, []))
    # A named boundary/requirement policy stop is an intentional radius
    # limit, not evidence that traversal ran out of budget.  Keep the legacy
    # aggregate flag for callers that display every stopped path, while
    # exposing the uncertainty-bearing condition separately so graph-quality
    # can fail closed only on genuinely unexplored depth.
    truncated = bool(policy_blocked) or depth_truncated
    return {
        "touched": touched,
        "impacted": by_depth,
        "total_impacted": sum(len(v) for v in by_depth.values()),
        "unknown": [m for m in touched if m not in g["modules"]],
        "depth_limit": max_depth,
        "truncated": truncated,
        "depth_truncated": depth_truncated,
        "policy": resolved_policy,
        "policy_blocked": policy_blocked,
        "boundary_nodes": sorted(m for m in seen if _is_boundary(m)),
        "graph": dict(g.get("meta") or {}),
    }


def render_context(imp: dict) -> str:
    """Token-lean impact summary injected at review steps."""
    if not imp["touched"]:
        return ""
    lines = [f"Change blast radius (dependency graph, no re-derivation "
             f"needed): touches {', '.join(imp['touched'])}."]
    for depth in sorted(imp["impacted"]):
        entries = imp["impacted"][depth]
        lines.append(
            f"  depth {depth}: " + "; ".join(
                f"{e['module']} ({e['kind']} ← {e['via']})"
                for e in entries[:8])
            + (f" …+{len(entries)-8}" if len(entries) > 8 else ""))
    if imp["unknown"]:
        lines.append("  (new modules, not in graph yet: "
                     + ", ".join(imp["unknown"]) + " — rescan after merge)")
    if imp.get("truncated"):
        lines.append(f"  traversal stopped at depth {imp.get('depth_limit')} "
                     "with additional dependents beyond the review radius")
    policy = imp.get("policy") or {}
    if policy:
        lines.append("  policy: local depth "
                     f"{policy.get('local_depth', imp.get('depth_limit'))}; "
                     f"boundary {policy.get('boundary_mode', 'contract-only')}; "
                     f"contract depth {policy.get('contract_depth', 1)}")
    if imp.get("affected_requirements"):
        lines.append(
            "  PRODUCT impact — this change touches the realized surface of: "
            + ", ".join(imp["affected_requirements"])
            + ". Re-check those requirements' acceptance criteria.")
    if imp.get("dependent_requirements"):
        lines.append(
            "  requirements depending on the affected ones: "
            + ", ".join(imp["dependent_requirements"]))
    return "\n".join(lines)


# ------------------------------------------------------------------ html

def _esc(s) -> str:
    """HTML-escape a repo-derived value (module id, dir name) before it goes
    into the impact table — directory names are attacker-influenced."""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Dependency graph — __TITLE__</title>
<style>
 body{margin:0;font:13px/1.45 -apple-system,'Segoe UI',sans-serif;
      background:#fcfcfb;color:#1a1a18}
 header{padding:14px 20px 6px}h1{font-size:16px;margin:0 0 2px}
 .sub{color:#6b6b66;font-size:12px}
 .legend{display:flex;gap:14px;padding:6px 20px;font-size:12px;color:#44443f}
 .legend span{display:flex;align-items:center;gap:5px}
 .dot{width:10px;height:10px;border-radius:50%;display:inline-block}
 #wrap{position:relative}svg{display:block;width:100%;height:66vh}
 .lbl{font-size:11px;fill:#44443f;pointer-events:none}
 .lbl.comp{font-size:9px;fill:#6b6b66}
 .edge{stroke:#c9c9c4;stroke-width:1.2;fill:none}
 .edge.rec{stroke-dasharray:4 3}
 .edge.comp{stroke:#a9d4bb;stroke-width:1}
 #tip{position:absolute;background:#fff;border:1px solid #dcdcd7;
      border-radius:6px;padding:8px 10px;font-size:12px;display:none;
      box-shadow:0 2px 8px rgba(0,0,0,.08);max-width:320px;pointer-events:none}
 table{border-collapse:collapse;margin:10px 20px 30px;font-size:12px}
 td,th{border:1px solid #e3e3de;padding:4px 10px;text-align:left}
 th{background:#f3f3ef;font-weight:600}
 .imp{color:#b3261e;font-weight:600}.chg{color:#8c3d00;font-weight:600}
</style></head><body>
<header><h1>Dependency graph — __TITLE__</h1>
<div class="sub">__SUB__</div></header>
<div class="legend">
 <span><i class="dot" style="background:#2a78d6"></i>module</span>
 <span><i class="dot" style="background:#3aa76d"></i>component</span>
 <span><i class="dot" style="background:#4a3aa7"></i>infra&nbsp;(svc:)</span>
 <span><i class="dot" style="background:#eda100"></i>external</span>
 <span><i class="dot" style="background:#e34948"></i>changed</span>
 <span><i class="dot" style="background:#eb6834"></i>impacted (depth 1–3)</span>
 <span>⤍ dashed = agent-recorded edge</span>
</div>
<div id="wrap"><svg id="g"></svg><div id="tip"></div></div>
<h1 style="padding:0 20px;font-size:14px">Impact table</h1>
__TABLE__
<script>
const G=__DATA__;
const W=document.getElementById('g').clientWidth||1200,H=innerHeight*.66;
const S=Math.min(W,H);
const nodes=Object.entries(G.modules).map(([id,m],i)=>({id,...m,
  x:W/2+(S/3)*Math.cos(2*Math.PI*i/Object.keys(G.modules).length),
  y:H/2+(S/3)*Math.sin(2*Math.PI*i/Object.keys(G.modules).length),vx:0,vy:0}));
const byId=Object.fromEntries(nodes.map(n=>[n.id,n]));
const edges=G.edges.filter(e=>byId[e.from]&&byId[e.to]);
const CHANGED=new Set(G.changed||[]),IMPACT=G.impacted||{};
function color(n){if(CHANGED.has(n.id))return'#e34948';
 if(IMPACT[n.id])return'#eb6834';
 return n.kind==='infra'?'#4a3aa7':n.kind==='external'?'#eda100':'#2a78d6';}
function r(n){return Math.max(7,Math.min(16,5+Math.sqrt(n.files||1)*2));}
// tiny force sim
for(let it=0;it<260;it++){
 for(const e of edges){const a=byId[e.from],b=byId[e.to];
  let dx=b.x-a.x,dy=b.y-a.y,d=Math.hypot(dx,dy)||1,f=(d-120)*.008;
  a.vx+=f*dx/d;a.vy+=f*dy/d;b.vx-=f*dx/d;b.vy-=f*dy/d;}
 for(let i=0;i<nodes.length;i++)for(let j=i+1;j<nodes.length;j++){
  const a=nodes[i],b=nodes[j];let dx=b.x-a.x,dy=b.y-a.y,
  d2=dx*dx+dy*dy||1,f=1800/d2;const d=Math.sqrt(d2);
  a.vx-=f*dx/d;a.vy-=f*dy/d;b.vx+=f*dx/d;b.vy+=f*dy/d;}
 for(const n of nodes){n.vx+=(W/2-n.x)*.002;n.vy+=(H/2-n.y)*.002;
  n.x+=n.vx*.72;n.y+=n.vy*.72;n.vx*=.62;n.vy*=.62;
  n.x=Math.max(30,Math.min(W-30,n.x));n.y=Math.max(26,Math.min(H-26,n.y));}}
const svg=document.getElementById('g'),NS='http://www.w3.org/2000/svg';
svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
function el(t,a){const e=document.createElementNS(NS,t);
 for(const k in a)e.setAttribute(k,a[k]);return e;}
svg.appendChild(el('defs',{})).innerHTML=
 '<marker id="ar" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" '+
 'markerHeight="6" orient="auto"><path d="M0 0L8 4L0 8z" fill="#c9c9c4"/></marker>';
for(const e of edges){const a=byId[e.from],b=byId[e.to],
 dx=b.x-a.x,dy=b.y-a.y,d=Math.hypot(dx,dy)||1,
 x2=b.x-dx/d*(r(b)+3),y2=b.y-dy/d*(r(b)+3);
 const p=el('path',{class:'edge'+(e.recorded?' rec':''),
  d:`M${a.x} ${a.y}L${x2} ${y2}`,'marker-end':'url(#ar)'});
 svg.appendChild(p);}
const tip=document.getElementById('tip');
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function details(n){
 const outs=edges.filter(e=>e.from===n.id).map(e=>`→ ${esc(e.to)} (${esc(e.kind)})`);
 const ins=edges.filter(e=>e.to===n.id).map(e=>`← ${esc(e.from)} (${esc(e.kind)})`);
 return {head:`${esc(n.id)} · ${esc(n.kind)}${n.files?` · ${n.files} file(s)`:''}`+
   (CHANGED.has(n.id)?' · changed':'')+
   (IMPACT[n.id]?` · impacted d${IMPACT[n.id]}`:''),
  edges:outs.concat(ins)};}
function showTip(n,x,y){const d=details(n);tip.style.display='block';
 tip.style.left=(x+16)+'px';tip.style.top=(y+8)+'px';
 tip.innerHTML=`<b>${esc(n.id)}</b> · ${esc(n.kind)}${n.files?` · ${n.files} file(s)`:''}`+
  (CHANGED.has(n.id)?' · <b class=chg>changed</b>':'')+
  (IMPACT[n.id]?` · <b class=imp>impacted d${IMPACT[n.id]}</b>`:'')+
  `<br>${d.edges.slice(0,9).join('<br>')||'no edges'}`;}
for(const n of nodes){
 const d=details(n);
 // Keyboard/screen-reader/touch reachable: focusable, labelled, and the
 // details open on focus and click too — not hover-only (which excludes
 // keyboard and touch users entirely).
 const c=el('circle',{cx:n.x,cy:n.y,r:r(n),fill:color(n),
  stroke:'#fcfcfb','stroke-width':2,cursor:'pointer',tabindex:'0',
  role:'button','aria-label':d.head+'. '+
   (d.edges.length?d.edges.length+' edges: '+d.edges.slice(0,9).join('; '):
    'no edges')});
 c.addEventListener('mousemove',ev=>showTip(n,ev.offsetX,ev.offsetY));
 c.addEventListener('mouseleave',()=>tip.style.display='none');
 c.addEventListener('focus',()=>showTip(n,n.x,n.y));
 c.addEventListener('blur',()=>tip.style.display='none');
 c.addEventListener('click',()=>showTip(n,n.x,n.y));
 c.addEventListener('keydown',ev=>{
  if(ev.key==='Enter'||ev.key===' '||ev.key==='Spacebar'){
   ev.preventDefault();showTip(n,n.x,n.y);
  }else if(ev.key==='Escape')tip.style.display='none';
 });
 svg.appendChild(c);
 const t=el('text',{class:'lbl',x:n.x+r(n)+4,y:n.y+4});
 t.textContent=n.id;svg.appendChild(t);}
// component LAYER (R-0003, contract:component-map): each component is a
// small node ringed around its owning module (its module grouping), with a
// distinct visual class and its component-level edges. An undecomposed
// graph carries no G.components, so this whole block renders nothing.
const comps=G.components||[];
const byComp={},byMod={};
// E3: the ring gap is NOT a fixed offset any more — every component carries
// `ring`, the count-scaled gap computed host-side
// (depgraph.component_ring_gap), so labels on a many-component module stop
// overlapping. Legacy data without `ring` falls back to the base constant.
const COMP_RING_BASE=__RING_BASE__;
const ringOf=c=>(typeof c.ring==='number'&&isFinite(c.ring))?c.ring
 :COMP_RING_BASE;
for(const c of comps){(byMod[c.module]=byMod[c.module]||[]).push(c);}
for(const mid in byMod){const m=byId[mid];if(!m)continue;
 byMod[mid].forEach((c,i)=>{const a=2*Math.PI*i/byMod[mid].length,
  rad=r(m)+ringOf(c);
  c.x=m.x+rad*Math.cos(a);c.y=m.y+rad*Math.sin(a);
  byComp[c.id]=c;});}
for(const c of comps){if(!byComp[c.id])continue;
 for(const d of (c.deps||[])){const t2=byComp[d.to]||byId[d.to];
  if(!t2)continue;
  svg.appendChild(el('path',{class:'edge comp',
   d:`M${c.x} ${c.y}L${t2.x} ${t2.y}`}));}}
for(const c of comps){if(!byComp[c.id])continue;
 const label=`${esc(c.id)} · component of ${esc(c.module)} · `+
  `${c.files||0} file(s)${c.symbols?` · ${c.symbols} symbol(s)`:''}`;
 const cc=el('circle',{class:'compnode',cx:c.x,cy:c.y,r:5,fill:'#3aa76d',
  stroke:'#fcfcfb','stroke-width':1.5,cursor:'pointer',tabindex:'0',
  role:'button','aria-label':label});
 const show=(x,y)=>{tip.style.display='block';tip.style.left=(x+16)+'px';
  tip.style.top=(y+8)+'px';tip.innerHTML=`<b>${esc(c.id)}</b> · component`+
   `<br>${(c.deps||[]).slice(0,9).map(d=>`→ ${esc(d.to)} (${esc(d.kind)})`)
    .join('<br>')||'no component edges'}`;};
 cc.addEventListener('mousemove',ev=>show(ev.offsetX,ev.offsetY));
 cc.addEventListener('mouseleave',()=>tip.style.display='none');
 cc.addEventListener('focus',()=>show(c.x,c.y));
 cc.addEventListener('blur',()=>tip.style.display='none');
 cc.addEventListener('click',()=>show(c.x,c.y));
 // E3 a11y: same keyboard escape hatch module nodes have — a keyboard user
 // who opened this tooltip can dismiss it without a pointer.
 cc.addEventListener('keydown',ev=>{
  if(ev.key==='Enter'||ev.key===' '||ev.key==='Spacebar'){
   ev.preventDefault();show(c.x,c.y);
  }else if(ev.key==='Escape')tip.style.display='none';
 });
 svg.appendChild(cc);
 const tl=el('text',{class:'lbl comp',x:c.x+7,y:c.y+3});
 tl.textContent=c.id.split('::')[1]||c.id;svg.appendChild(tl);}
</script></body></html>"""


# E3 (R-0011): component ring geometry. The gap between a module node's
# edge and its ring of component nodes used to be a fixed 24px, so a module
# with many components crowded them onto the same short arc and their labels
# overlapped. The gap is now a monotonically increasing function of the
# module's component COUNT: each extra component buys COMPONENT_RING_STEP
# more radius, which grows the ring's circumference (and therefore the arc
# between neighbouring labels) linearly with the count. Computed here, in
# Python, and carried per component in the embedded data — the renderer
# stays a static self-contained page with no host-side layout engine.
COMPONENT_RING_BASE = 24    # gap for a single-component module (px)
COMPONENT_RING_STEP = 4     # extra gap per additional component (px)


def component_ring_gap(count: int) -> int:
    """Ring gap in px for a module holding `count` components. Monotonically
    increasing in `count`; never below COMPONENT_RING_BASE."""
    try:
        n = int(count)
    except (TypeError, ValueError):
        n = 1
    return COMPONENT_RING_BASE + COMPONENT_RING_STEP * max(0, n - 1)


def focus_graph(g: dict, imp: dict, depth: int) -> "tuple[dict, dict, str]":
    """The blast-radius NEIGHBOURHOOD, not the whole map.

    The full view embeds every module and every edge in the repo. On a
    monorepo that is 620 KB of JSON — a fine file, and a page no inline
    widget can carry, which is precisely where the reader keeps asking
    for the graph to appear. Nearly all of that weight is edges between
    modules the change never reaches.

    So: keep the changed modules, keep what depends on them out to
    `depth`, and keep only the edges whose BOTH endpoints survive. The
    result is the same engine-built map, cropped — not a redrawn
    substitute — and it returns a note naming exactly what was dropped,
    because a view that silently omits half the graph reads as
    'that's all there is'.
    """
    keep = set(imp.get("touched") or [])
    kept_impacted: dict = {}
    for d, es in (imp.get("impacted") or {}).items():
        if int(d) <= depth:
            kept_impacted[d] = es
            keep |= {e["module"] for e in es}
    mods = {k: v for k, v in (g.get("modules") or {}).items() if k in keep}
    edges = [e for e in (g.get("edges") or [])
             if e.get("from") in keep and e.get("to") in keep]
    note = (f"focused to depth {depth}: "
            f"{len(mods)}/{len(g.get('modules') or {})} modules · "
            f"{len(edges)}/{len(g.get('edges') or [])} edges shown")
    sub_g = {**g, "modules": mods, "edges": edges}
    sub_i = {**imp, "impacted": kept_impacted,
             "total_impacted": sum(len(v) for v in kept_impacted.values())}
    return sub_g, sub_i, note


def as_fragment(page: str) -> str:
    """The same standalone page, embeddable inline in a chat widget.

    The graph is a whole HTML document — DOCTYPE, <head>, and a stylesheet
    that styles `body`, `table`, `h1`. Pasted into a host page it would
    both fail to parse as a fragment and repaint the surrounding chat. So
    it travels inside an `srcdoc` iframe: the document is carried BYTE FOR
    BYTE, the browser gives it its own document and its own styles, and
    nothing leaks either way.

    That byte-identity is the point. The recurring failure this guards
    against is an assistant substituting a hand-drawn chart for the
    product's map; a wrapper that re-authored the page to fit inline
    would be the same substitution wearing the engine's name. Only the
    five characters that cannot survive an HTML attribute are escaped.
    """
    raw = page.encode("utf-8")
    packed = base64.b64encode(gzip.compress(raw, 9)).decode("ascii")
    fid = "tpg-" + hashlib.sha256(raw).hexdigest()[:10]
    return (
        '<div style="padding:.5rem 0">'
        f'<iframe id="{fid}" title="dependency graph" sandbox="allow-scripts" '
        'style="width:100%;height:620px;border:1px solid var(--border);'
        'border-radius:8px;background:#fcfcfb"></iframe>'
        f'<noscript>the dependency graph needs scripts to unpack '
        f'({len(raw)} bytes)</noscript>'
        '<script>(async function(){try{'
        f'var b=atob("{packed}");var u=new Uint8Array(b.length);'
        'for(var i=0;i<b.length;i++)u[i]=b.charCodeAt(i);'
        'var t=await new Response(new Blob([u]).stream()'
        '.pipeThrough(new DecompressionStream("gzip"))).text();'
        f'document.getElementById("{fid}").srcdoc=t;'
        '}catch(e){'
        f'document.getElementById("{fid}").outerHTML='
        '"<p style=\\"font-family:monospace;font-size:12px\\">graph could not '
        'be unpacked in this view: "+e+"</p>";}})();</script></div>')


def to_html(ws: str, changed_files=None, title: str | None = None,
            out: str | None = None, focus: int | None = None,
            fragment: bool = False) -> str:
    """Self-contained interactive dependency map; changed/impacted modules
    highlighted so a reviewer sees the blast radius before reading code.

    `focus=N` crops the map to the changed set plus everything within N
    dependency hops of it — the same graph, small enough to render inline.
    """
    g = load(ws)
    imp = impact(ws, changed_files or [])
    focus_note = ""
    if focus and imp.get("touched"):
        g, imp, focus_note = focus_graph(g, imp, int(focus))
    impacted = {e["module"]: d for d, es in imp["impacted"].items()
                for e in es}
    rows = ["<table><tr><th>module</th><th>status</th><th>via</th>"
            "<th>kind</th></tr>"]
    for m in imp["touched"]:
        rows.append(f"<tr><td>{_esc(m)}</td><td class=chg>changed</td>"
                    "<td>—</td><td>—</td></tr>")
    for d in sorted(imp["impacted"]):
        for e in imp["impacted"][d]:
            rows.append(f"<tr><td>{_esc(e['module'])}</td>"
                        f"<td class=imp>impacted (depth {d})</td>"
                        f"<td>{_esc(e['via'])}</td><td>{_esc(e['kind'])}</td>"
                        "</tr>")
    table = "\n".join(rows) + "</table>" if imp["touched"] else \
        "<p style='margin:6px 20px'>no change set given — structural view.</p>"

    data = {"modules": g["modules"], "edges": g["edges"],
            "changed": imp["touched"], "impacted": impacted}
    sub = (f"{len(g['modules'])} components · {len(g['edges'])} edges · "
           f"{imp['total_impacted']} impacted by this change"
           if imp["touched"] else
           f"{len(g['modules'])} components · {len(g['edges'])} edges")
    comps = g.get("components")
    if isinstance(comps, list) and comps:
        # R-0003 component layer (ADDITIVE): rendered as small nodes ringed
        # around their module, with their component-level edges. Without the
        # layer, `data` carries no `components` key and the page renders as
        # before.
        per_module: dict = {}
        for c in comps:
            if isinstance(c, dict):
                per_module[c.get("module")] = \
                    per_module.get(c.get("module"), 0) + 1
        data["components"] = [
            {"id": c.get("id"), "module": c.get("module"),
             "files": len(c.get("files") or []),
             "symbols": len(c.get("symbols") or []),
             "ring": component_ring_gap(per_module.get(c.get("module"), 1)),
             "deps": [{"to": d.get("to"), "kind": d.get("kind")}
                      for d in (c.get("deps") or [])]}
            for c in comps if isinstance(c, dict)]
        sub += f" · {len(comps)} decomposed component node(s)"
    if focus_note:
        sub += " · " + focus_note
    # json.dumps does not neutralize a `</script>` occurring inside a
    # repo-supplied module id — it would close the inline <script> early and
    # let the remainder execute as markup. Escape `<` (and U+2028/9) so the
    # embedded JSON can never break out of the script element.
    safe_data = (json.dumps(data).replace("<", "\\u003c")
                 .replace(" ", "\\u2028").replace(" ", "\\u2029"))
    html = (_HTML.replace("__TITLE__", _esc(title or os.path.basename(ws)))
            .replace("__RING_BASE__", str(COMPONENT_RING_BASE))
            .replace("__SUB__", _esc(sub))
            .replace("__TABLE__", table)
            .replace("__DATA__", safe_data))
    if fragment:
        html = as_fragment(html)
    if out is None:
        import storage as runtime_storage
        out = runtime_storage.dependency_graph_visual_path(ws)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    return out
