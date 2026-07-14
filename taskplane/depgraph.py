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
import hashlib
import sys as _sys
import json
import os
import re

import taskplane_lite as tp

GRAPH_FILE = "graph.json"
CODE_EXT = (".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".go",
            ".cs", ".java", ".rb")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".taskplane", ".tp-work",
             "venv", ".venv", "dist", "build", ".eval", ".em-review",
             ".security-review"}


def _path(ws: str) -> str:
    # graph.json lives in the external per-project store, not the repo.
    return os.path.join(tp.kb_root(ws), GRAPH_FILE)


def load(ws: str) -> dict:
    p = _path(ws)
    if not os.path.exists(p):
        return {"modules": {}, "edges": [], "files": {}, "recorded": []}
    with open(p) as f:
        return json.load(f)


def save(ws: str, g: dict) -> None:
    os.makedirs(tp.kb_root(ws), exist_ok=True)
    with open(_path(ws), "w") as f:
        json.dump(g, f, indent=1, sort_keys=True)


def summary(ws: str) -> dict:
    """Public read model for a view — module/edge counts without the caller
    needing to know graph.json's internal key names. (The dashboard consumes
    this instead of reaching into the raw file, so a schema change here can't
    silently zero the mission-control graph tab.)"""
    g = load(ws)
    return {"modules": len(g.get("modules") or {}),
            "edges": len(g.get("edges") or [])}


# ------------------------------------------------------------------ modules

# Directory names that mark a source root — the module is the FEATURE
# directly under them, so real structure (auth, payment, …) shows instead
# of the whole app collapsing into one node.
_SRC_ROOTS = ("src", "app", "lib", "packages", "pkg", "internal", "cmd")


def module_of(relpath: str) -> str:
    """Feature-level module id. If the path passes through a source root
    (src/, app/, …), the module is up to two segments AFTER the last such
    root: template/app/src/payment/stripe/x.ts -> payment/stripe;
    src/auth/session.py -> auth. Otherwise the first two path segments.
    A file at the repo root -> (root)."""
    d = os.path.dirname(relpath)
    if not d:
        return "(root)"
    parts = d.split("/")
    # last source-root marker in the path
    root_i = None
    for i, p in enumerate(parts):
        if p in _SRC_ROOTS:
            root_i = i
    if root_i is not None and root_i + 1 < len(parts):
        feat = parts[root_i + 1:root_i + 3]      # feature (+ subfeature)
        return "/".join(feat)
    if root_i is not None:                        # files directly in src/
        return parts[root_i]
    return "/".join(parts[:2])


# ------------------------------------------------------------------ scanners

def _py_imports(src: str, relpath: str, known_stems: dict) -> set:
    out = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    stdlib = getattr(_sys, "stdlib_module_names", frozenset())
    pkg_dir = os.path.dirname(relpath)
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
    return out


_JS_IMPORT = re.compile(
    r"""(?:import\s+(?:[^'"]*\s+from\s+)?|require\s*\(\s*|export\s+[^'"]*"""
    r"""from\s+)['"]([^'"]+)['"]""")


def _js_imports(src: str, relpath: str, file_index: set) -> set:
    out = set()
    for target in _JS_IMPORT.findall(src):
        if target.startswith("."):
            resolved = os.path.normpath(
                os.path.join(os.path.dirname(relpath), target))
            # find an actual file this resolves to
            for cand in (resolved, *(f"{resolved}{e}" for e in CODE_EXT),
                         *(f"{resolved}/index{e}" for e in CODE_EXT)):
                if cand in file_index:
                    out.add(module_of(cand))
                    break
        else:
            out.add("ext:" + target.split("/")[0])
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


def _ruby_imports(src: str, relpath: str, file_index: set) -> set:
    """require_relative resolved to files; bare require matched against
    repo lib paths first (Rails-style lib/foo/bar → lib/foo), else a gem.
    (Rails constant autoloading carries no import statements — those edges
    come from the model/controller dirs sharing modules, and can be added
    as recorded edges where they matter.)"""
    out = set()
    here = os.path.dirname(relpath)
    for target in _RB_REQ_REL.findall(src):
        cand = os.path.normpath(os.path.join(here, target)) + ".rb"
        if cand in file_index:
            out.add(module_of(cand))
    for target in _RB_REQ.findall(src):
        cand = os.path.join("lib", target) + ".rb"
        if cand in file_index:
            out.add(module_of(cand))
        elif (target + ".rb") in file_index:
            out.add(module_of(target + ".rb"))
        elif target not in _RB_STDLIB and not target.startswith("."):
            out.add("ext:" + target.split("/")[0])
    return out


_CSPROJ_PROJ = re.compile(r'ProjectReference\s+Include="([^"]+)"')
_CSPROJ_PKG = re.compile(r'PackageReference\s+Include="([^"]+)"')
_GEMFILE_GEM = re.compile(r"""^\s*gem\s+['"]([\w-]+)['"]""", re.M)


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

def scan(ws: str) -> dict:
    """Build/refresh the graph. Incremental: unchanged files (by content
    hash) keep their cached edges — a rescan after a small diff is cheap."""
    prev = load(ws)
    files, code_files = {}, []
    for root, dirs, names in os.walk(ws):
        # Sort in place so the walk order is deterministic — otherwise the
        # first-seen-wins basename/namespace maps below depend on filesystem
        # order, making a bare `import utils` resolve non-reproducibly when
        # two files share a basename.
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS
                         and not d.startswith(".tp-"))
        for n in sorted(names):
            rel = os.path.relpath(os.path.join(root, n), ws)
            if rel.startswith("knowledge/"):
                continue
            if n.endswith(CODE_EXT):
                code_files.append(rel)
            files[rel] = True

    # stem/dir → module map for python import resolution: covers
    # `import src.db.conn`, `from src.db import conn` (package dir), and
    # bare `import conn` (basename).
    known_stems = {}
    for f in code_files:
        stem = f.rsplit(".", 1)[0]
        known_stems[stem] = module_of(f)
        known_stems.setdefault(os.path.basename(stem), module_of(f))
        d = os.path.dirname(f)
        while d:
            # resolve dir stems through module_of so import targets land on
            # the SAME feature module a file does (consistent edge endpoints)
            known_stems.setdefault(d, module_of(d + "/_"))
            d = os.path.dirname(d)

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
                    ns_map.setdefault(ns, module_of(rel))
            else:
                for pkg in _java_declared(sources[rel]):
                    pkg_map.setdefault(pkg, module_of(rel))

    file_entries, edges = {}, set()
    prev_files = prev.get("files", {})
    for rel in code_files:
        full = os.path.join(ws, rel)
        cached = prev_files.get(rel)
        try:
            st = os.stat(full)
            size, mtime = st.st_size, int(st.st_mtime)
        except OSError:
            size = mtime = None
        # mtime+size short-circuit: an unchanged file keeps its cached hash,
        # imports AND edges WITHOUT being re-read or re-hashed. This is what
        # makes a rescan scale with the DIFF, not the whole tree — on a big
        # repo the em-gate true-up and retro no longer re-hash every file.
        if (cached and size is not None and cached.get("size") == size
                and cached.get("mtime") == mtime and "imports" in cached):
            imports = set(cached["imports"])
            mod = module_of(rel)
            imports.discard(mod)
            file_entries[rel] = {"hash": cached.get("hash", ""),
                                 "imports": sorted(imports),
                                 "size": size, "mtime": mtime}
            for target in imports:
                edges.add((mod, target, "imports"))
            continue
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                src = fh.read()
        except OSError:
            continue
        digest = hashlib.sha1(src.encode()).hexdigest()[:12]
        if cached and cached.get("hash") == digest:
            imports = set(cached["imports"])
        elif rel.endswith(".py"):
            imports = _py_imports(src, rel, known_stems)
        elif rel.endswith(".go"):
            imports = set()
            for block, single in re.findall(
                    r'import\s+\(([^)]*)\)|import\s+"([^"]+)"', src, re.S):
                for t in ([single] if single
                          else re.findall(r'"([^"]+)"', block)):
                    imports.add("ext:" + t.split("/")[-1])
        elif rel.endswith(".cs"):
            imports = _cs_imports(src, ns_map)
        elif rel.endswith(".java"):
            imports = _java_imports(src, pkg_map)
        elif rel.endswith(".rb"):
            imports = _ruby_imports(src, rel, set(files))
        else:
            imports = _js_imports(src, rel, set(files))
        mod = module_of(rel)
        imports.discard(mod)
        file_entries[rel] = {"hash": digest, "imports": sorted(imports),
                             "size": size, "mtime": mtime}
        for target in imports:
            edges.add((mod, target, "imports"))

    # manifests: .csproj project/package references, Gemfile gems
    for rel in files:
        if rel.endswith(".csproj"):
            with open(os.path.join(ws, rel), encoding="utf-8",
                      errors="replace") as fh:
                text = fh.read()
            mod = module_of(rel)
            for pref in _CSPROJ_PROJ.findall(text):
                tgt = os.path.normpath(os.path.join(
                    os.path.dirname(rel), pref.replace("\\", "/")))
                edges.add((mod, module_of(tgt), "project_ref"))
            for pkg in _CSPROJ_PKG.findall(text):
                edges.add((mod, "ext:" + pkg.split(".")[0], "imports"))
        elif os.path.basename(rel) == "Gemfile":
            with open(os.path.join(ws, rel), encoding="utf-8",
                      errors="replace") as fh:
                for gem in _GEMFILE_GEM.findall(fh.read()):
                    edges.add((module_of(rel), "ext:" + gem, "imports"))

    # infra: docker-compose services
    for rel in files:
        if re.search(r"docker-compose[^/]*\.ya?ml$", rel):
            with open(os.path.join(ws, rel), encoding="utf-8",
                      errors="replace") as fh:
                for svc in _compose_services(fh.read()):
                    sid = f"svc:{svc['name']}"
                    for dep in svc["depends_on"]:
                        edges.add((sid, f"svc:{dep}", "depends_on"))
                    edges.add((sid, module_of(rel), "defined_in"))

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
    resolvable.update(module_of(rel) for rel in files)
    edges = {(a, b, k) for (a, b, k) in edges
             if b.startswith(("ext:", "svc:")) or b in resolvable}

    modules = {}
    for rel in code_files:
        m = module_of(rel)
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

    g = {
        "modules": modules,
        "edges": sorted([{"from": a, "to": b, "kind": k}
                         for a, b, k in edges],
                        key=lambda e: (e["from"], e["to"])),
        "files": file_entries,
        "recorded": prev.get("recorded", []),
    }
    # merge agent-recorded edges (never dropped by rescans)
    g["edges"] += [e for e in g["recorded"]
                   if not any(x["from"] == e["from"] and x["to"] == e["to"]
                              and x["kind"] == e["kind"] for x in g["edges"])]
    save(ws, g)
    tp.trace(ws, "graph_scan", modules=len(modules), edges=len(g["edges"]),
             files=len(file_entries))
    return g


def record_edge(ws: str, src: str, dst: str, kind: str = "runtime",
                note: str = "") -> dict:
    """An agent-observed dependency static analysis can't see (HTTP call,
    queue, cron, deploy relationship). Survives rescans."""
    g = load(ws)
    e = {"from": src, "to": dst, "kind": kind, "note": note,
         "recorded": True}
    g["recorded"].append(e)
    g["edges"].append(e)
    for x in (src, dst):
        g["modules"].setdefault(
            x, {"kind": "infra" if x.startswith("svc:") else
                ("external" if x.startswith("ext:") else "module"),
                "files": 0})
    save(ws, g)
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

def modules_for_scope(scope_globs) -> list:
    """Map scope globs/paths to graph modules (glob prefix → module)."""
    mods = set()
    for g in scope_globs or []:
        prefix = g.split("*", 1)[0].rstrip("/")
        if not prefix:
            continue
        mods.add(module_of(prefix) if "." in os.path.basename(prefix)
                 else module_of(prefix + "/_"))
    return sorted(mods)


def _req_node(rid: str) -> str:
    return rid if rid.startswith("req:") else f"req:{rid}"


def link_requirement(ws: str, rid: str, files, kind: str = "realizes",
                     replace: bool = True) -> dict:
    """Maintain the req→module edges for one requirement. `files` may be
    real paths or scope globs. replace=True refreshes that requirement's
    edges of this kind (the true-up), so the product side never goes stale."""
    node = _req_node(rid)
    mods = sorted(set(modules_for_scope(files)))
    g = load(ws)
    if replace:
        drop = lambda e: e["from"] == node and e["kind"] == kind
        g["recorded"] = [e for e in g["recorded"] if not drop(e)]
        g["edges"] = [e for e in g["edges"] if not drop(e)]
    for m in mods:
        e = {"from": node, "to": m, "kind": kind, "note": "", "recorded": True}
        g["recorded"].append(e)
        g["edges"].append(e)
        g["modules"].setdefault(m, {"kind": "module", "files": 0})
    g["modules"].setdefault(node, {"kind": "requirement", "files": 0})
    save(ws, g)
    tp.trace(ws, "graph_req_link", requirement=node, kind=kind, modules=mods)
    return {"requirement": node, "kind": kind, "modules": mods}


def link_requirement_dep(ws: str, rid: str, depends_on: str,
                         note: str = "") -> dict:
    """Product dependency: req:rid depends on req:depends_on."""
    return record_edge(ws, _req_node(rid), _req_node(depends_on),
                       kind="depends", note=note)


def product_impact(ws: str, changed_files) -> dict:
    """The product side of blast radius: which requirements' planned or
    realized surface a change touches, plus requirements that DEPEND on
    those (one hop up the product graph). Zero tokens, like impact()."""
    g = load(ws)
    items = list(changed_files or [])
    # accept file paths OR already-resolved module names
    mods = {module_of(f) for f in items} | set(items)
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


# ------------------------------------------------------------------ impact

def impact(ws: str, changed_files, max_depth: int = 3) -> dict:
    """Blast radius of a change: the modules touched, then everything that
    depends on them (reverse edges), by depth. This is what a reviewer needs
    BEFORE reading any code — and it costs zero tokens."""
    g = load(ws)
    rev = {}
    for e in g["edges"]:
        rev.setdefault(e["to"], []).append((e["from"], e["kind"]))

    touched = sorted({module_of(f) for f in (changed_files or [])})
    seen = {m: 0 for m in touched}
    frontier, by_depth = list(touched), {}
    depth = 0
    while frontier and depth < max_depth:
        depth += 1
        nxt = []
        for m in frontier:
            for dep, kind in rev.get(m, []):
                if dep not in seen:
                    seen[dep] = depth
                    by_depth.setdefault(depth, []).append(
                        {"module": dep, "via": m, "kind": kind})
                    nxt.append(dep)
        frontier = nxt
    return {
        "touched": touched,
        "impacted": by_depth,
        "total_impacted": sum(len(v) for v in by_depth.values()),
        "unknown": [m for m in touched if m not in g["modules"]],
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
 .edge{stroke:#c9c9c4;stroke-width:1.2;fill:none}
 .edge.rec{stroke-dasharray:4 3}
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
function details(n){
 const outs=edges.filter(e=>e.from===n.id).map(e=>`→ ${e.to} (${e.kind})`);
 const ins=edges.filter(e=>e.to===n.id).map(e=>`← ${e.from} (${e.kind})`);
 return {head:`${n.id} · ${n.kind}${n.files?` · ${n.files} file(s)`:''}`+
   (CHANGED.has(n.id)?' · changed':'')+
   (IMPACT[n.id]?` · impacted d${IMPACT[n.id]}`:''),
  edges:outs.concat(ins)};}
function showTip(n,x,y){const d=details(n);tip.style.display='block';
 tip.style.left=(x+16)+'px';tip.style.top=(y+8)+'px';
 tip.innerHTML=`<b>${n.id}</b> · ${n.kind}${n.files?` · ${n.files} file(s)`:''}`+
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
 c.addEventListener('keydown',ev=>{if(ev.key==='Escape')tip.style.display='none';});
 svg.appendChild(c);
 const t=el('text',{class:'lbl',x:n.x+r(n)+4,y:n.y+4});
 t.textContent=n.id;svg.appendChild(t);}
</script></body></html>"""


def to_html(ws: str, changed_files=None, title: str | None = None,
            out: str | None = None) -> str:
    """Self-contained interactive dependency map; changed/impacted modules
    highlighted so a reviewer sees the blast radius before reading code."""
    g = load(ws)
    imp = impact(ws, changed_files or [])
    impacted = {e["module"]: d for d, es in imp["impacted"].items()
                for e in es}
    rows = ["<table><tr><th>module</th><th>status</th><th>via</th>"
            "<th>kind</th></tr>"]
    for m in imp["touched"]:
        rows.append(f"<tr><td>{m}</td><td class=chg>changed</td>"
                    "<td>—</td><td>—</td></tr>")
    for d in sorted(imp["impacted"]):
        for e in imp["impacted"][d]:
            rows.append(f"<tr><td>{e['module']}</td>"
                        f"<td class=imp>impacted (depth {d})</td>"
                        f"<td>{e['via']}</td><td>{e['kind']}</td></tr>")
    table = "\n".join(rows) + "</table>" if imp["touched"] else \
        "<p style='margin:6px 20px'>no change set given — structural view.</p>"

    data = {"modules": g["modules"], "edges": g["edges"],
            "changed": imp["touched"], "impacted": impacted}
    sub = (f"{len(g['modules'])} components · {len(g['edges'])} edges · "
           f"{imp['total_impacted']} impacted by this change"
           if imp["touched"] else
           f"{len(g['modules'])} components · {len(g['edges'])} edges")
    html = (_HTML.replace("__TITLE__", title or os.path.basename(ws))
            .replace("__SUB__", sub)
            .replace("__TABLE__", table)
            .replace("__DATA__", json.dumps(data)))
    out = out or os.path.join(ws, ".taskplane", "depgraph.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        f.write(html)
    return out
