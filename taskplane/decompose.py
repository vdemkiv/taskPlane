"""Component decomposition — a `components` LAYER for graph.json (R-0003).

Hybrid derivation, per the approved Design Contract (contract:component-map):

  * directory convention — files grouped by sub-directory under their module,
  * import/reference cohesion — root-level loners join the one cluster they
    import (mutually-importing loners cluster together),
  * AST symbol clustering for oversized files — top-level def/class groups
    in a Python file >= BIG_FILE_LINES lines, clustered by shared name
    prefix (`render_*`, `db_*`, …) with reference cohesion (a residual
    symbol that only calls into one cluster joins it).

Floors (the approved design's constants — override via components.yaml):

  CANDIDATE_MIN_FILES  a module is a decomposition candidate only with
                       >= 8 code files, OR
  BIG_FILE_LINES       any single code file >= 600 physical lines
  CLUSTER_MIN_FILES    a file cluster earns a component with >= 2 files
  CLUSTER_MIN_SYMBOLS  an intra-file symbol cluster needs >= 4 top-level
  CLUSTER_MIN_LINES    symbols spanning >= 120 lines
  Everything below a floor folds into the residual `<module>::core`; a module
  below the candidate threshold IS its single `::core` component.

components.yaml schema (repo root; OPTIONAL). Only this flat mapping subset
is parsed — stdlib only, no YAML dependency; `#` comments and unknown keys
are ignored; a malformed file fails OPEN to the defaults:

    floors:
      candidate_min_files: 8
      big_file_lines: 600
      cluster_min_files: 2
      cluster_min_symbols: 4
      cluster_min_lines: 120

Component node shape (stored under graph.json's top-level `components` key,
sorted by id — contract:component-map):

    {id:          "<module>::<cluster>"   (never colliding with module ids
                                           or contract:/resource:/svc:/ext:
                                           boundary prefixes),
     module:      the owning depgraph module id,
     files:       sorted file span,
     symbols:     sorted top-level symbol-name span ([] for file clusters),
     fingerprint: sha256 over the sorted (file, hash, symbol-span) material,
     deps:        [{to, kind}] component-level edges (kind imports for module
                  /external targets, references for sibling components),
     lens_map:    {lens_id: {verdict, score, evidence}} computed by the
                  SHIPPED lens_signals.route_verdicts over the component's
                  file span; recomputed ONLY on fingerprint change}
    + derived_by: directory | cohesion | symbols | mixed | core
    + degraded:   true — ONLY when derivation for the module failed and it
                  was folded to a single ::core (fail-open marker)

Fail-open (load-bearing): ANY per-module derivation failure — bad AST,
unreadable file, a detector meltdown — degrades THAT module to a single
`::core` component with `degraded: true` and an error note in the stats;
`derive()` itself never raises, so a scan can never be crashed (or a gate
blocked) by decomposition.

Bounded reads: per-file MAX_FILE_BYTES / per-module MAX_MODULE_FILES caps in
the style of lens_signals.Ctx, with the same realpath containment. Stdlib
only. Deterministic: every collection is sorted before use or output.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re

CANDIDATE_MIN_FILES = 8    # module decomposes with >= this many code files…
BIG_FILE_LINES = 600       # …or any single code file >= this many lines
CLUSTER_MIN_FILES = 2      # file cluster floor
CLUSTER_MIN_SYMBOLS = 4    # intra-file symbol cluster floor (symbols)
CLUSTER_MIN_LINES = 120    # intra-file symbol cluster floor (line span)

# Bounded reads, Ctx-style (lens_signals bounds content scans the same way).
# The per-file cap is larger than lens_signals.MAX_FILE_BYTES because the
# AST pass needs the WHOLE file to be parseable; a file beyond the cap is
# never parsed (it folds to ::core) instead of being truncated into a
# spurious SyntaxError degrade.
MAX_FILE_BYTES = 1024 * 1024   # per-file read bound
MAX_MODULE_FILES = 400         # max code files considered per module

_FLOOR_KEYS = ("candidate_min_files", "big_file_lines", "cluster_min_files",
               "cluster_min_symbols", "cluster_min_lines")
_COMPONENTS_YAML = "components.yaml"

_STDLIB = getattr(__import__("sys"), "stdlib_module_names", frozenset())


class _DerivationError(Exception):
    """Internal: a per-module derivation failure (caught by derive())."""


# ---------------------------------------------------------------- bounded IO

def _read_text(workspace: str, rel: str) -> str | None:
    """Bounded, contained read (Ctx.read pattern): at most MAX_FILE_BYTES,
    utf-8 with replacement; a path escaping the real workspace -> None."""
    p = os.path.join(workspace, rel)
    root = os.path.realpath(workspace)
    real = os.path.realpath(p)
    if real != root and not real.startswith(root + os.sep):
        return None
    try:
        with open(real, "rb") as f:
            return f.read(MAX_FILE_BYTES).decode("utf-8", "replace")
    except OSError:
        return None


# ------------------------------------------------------------ components.yaml

def _parse_components_yaml(text: str) -> dict:
    """Parse the documented flat subset: a `floors:` mapping of int values.
    Anything unparseable raises ValueError (the caller fails open)."""
    floors: dict = {}
    section = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        top = re.match(r"^([A-Za-z_][\w-]*):\s*$", line)
        if top:
            section = top.group(1)
            continue
        kv = re.match(r"^\s+([A-Za-z_][\w-]*):\s*(-?\d+)\s*$", line)
        if kv:
            if section == "floors" and kv.group(1) in _FLOOR_KEYS:
                floors[kv.group(1)] = int(kv.group(2))
            continue
        raise ValueError(f"unsupported components.yaml line: {raw!r}")
    return floors


def load_floors(workspace: str) -> tuple[dict, str | None]:
    """(floors, error) — defaults overlaid with components.yaml when present.
    A malformed file fails OPEN to the defaults, with the error reported."""
    floors = {"candidate_min_files": CANDIDATE_MIN_FILES,
              "big_file_lines": BIG_FILE_LINES,
              "cluster_min_files": CLUSTER_MIN_FILES,
              "cluster_min_symbols": CLUSTER_MIN_SYMBOLS,
              "cluster_min_lines": CLUSTER_MIN_LINES}
    p = os.path.join(workspace, _COMPONENTS_YAML)
    if not os.path.exists(p):
        return floors, None
    try:
        text = _read_text(workspace, _COMPONENTS_YAML)
        if text is None:
            raise ValueError("components.yaml unreadable")
        floors.update(_parse_components_yaml(text))
        return floors, None
    except Exception as e:
        return floors, f"components.yaml ignored (defaults used): {e}"


def floors_hash(workspace: str) -> str:
    """Cache key for the active floor configuration — a components.yaml
    change invalidates every module-level derivation skip."""
    floors, _err = load_floors(workspace)
    return hashlib.sha256(json.dumps(floors, sort_keys=True,
                                     separators=(",", ":")).encode()
                          ).hexdigest()


# ----------------------------------------------------------------- utilities

def _sanitize(name: str) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-") or "part"
    if s == "core":              # `core` is reserved for the residual
        s = "core-x"
    return s


def _sym_prefix(name: str) -> str:
    s = name.lstrip("_") or "_"
    return _sanitize(s.split("_")[0] or "_")


def _count_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _module_of(rel: str):
    import depgraph
    return depgraph.module_of(rel)


def _repo_stems(graph: dict) -> dict:
    """stem/basename/dir -> module id, mirroring depgraph's scan resolution
    so alias targets land on the same module ids the graph uses."""
    stems: dict = {}
    for f in sorted(graph.get("files") or {}):
        stem = f.rsplit(".", 1)[0]
        stems[stem] = _module_of(f)
        stems.setdefault(os.path.basename(stem), _module_of(f))
        d = os.path.dirname(f)
        while d:
            stems.setdefault(d, _module_of(d + "/_"))
            d = os.path.dirname(d)
    return stems


def _module_stems(files: list) -> dict:
    """stem/basename -> file, for intra-module import resolution."""
    stems: dict = {}
    for f in files:
        stem = f.rsplit(".", 1)[0]
        stems[stem] = f
        stems.setdefault(os.path.basename(stem), f)
    return stems


# -------------------------------------------------- python import resolution

def _py_import_map(tree: ast.AST, rel: str, mod_stems: dict,
                   repo_stems: dict, module: str):
    """(intra, alias): intra = {local name -> intra-module file}, alias =
    {local name -> module-level target (module id or ext:pkg)}. Stdlib and
    own-module targets are dropped from alias (they are not deps)."""
    intra: dict = {}
    alias: dict = {}
    pkg_dir = os.path.dirname(rel)

    def resolve(dotted: str, local: str) -> None:
        n = (dotted or "").replace(".", "/")
        if not n:
            return
        hit_file = mod_stems.get(n) or mod_stems.get(n.split("/")[-1]
                                                     if "/" in n else n)
        if hit_file and (n in mod_stems or "/" not in n):
            intra[local] = hit_file
            return
        hit_mod = repo_stems.get(n) or repo_stems.get(n.split("/")[0])
        if hit_mod:
            if hit_mod != module:
                alias[local] = hit_mod
            return
        top = n.split("/")[0]
        if top and "/" not in n and top not in _STDLIB:
            alias[local] = f"ext:{top}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                local = a.asname or a.name.split(".")[0]
                resolve(a.name if a.asname else a.name.split(".")[0], local)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg_dir.split("/") if pkg_dir else []
                base = base[: len(base) - (node.level - 1)]
                stem = "/".join(base + [s for s in
                                        (node.module or "").split(".") if s])
            else:
                stem = (node.module or "").replace(".", "/")
            for a in node.names:
                local = a.asname or a.name
                child = f"{stem}/{a.name}" if stem else a.name
                if child in mod_stems:      # from pkg import file
                    intra[local] = mod_stems[child]
                elif stem in mod_stems:     # from <module file> import sym
                    intra[local] = mod_stems[stem]
                else:
                    resolve(stem or a.name, local)
    return intra, alias


def _file_refs(text: str | None, rel: str, mod_stems: dict,
               repo_stems: dict, module: str):
    """Per-file (intra-module file targets, module-level alias targets).
    Python via AST; JS-family via relative-import regex; other -> empty."""
    if text is None:
        raise _DerivationError(f"unreadable file: {rel}")
    if rel.endswith(".py"):
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            raise _DerivationError(f"bad AST in {rel}: {e}") from None
        intra, alias = _py_import_map(tree, rel, mod_stems, repo_stems,
                                      module)
        return set(intra.values()), set(alias.values())
    intra: set = set()
    if rel.endswith((".js", ".ts", ".tsx", ".jsx", ".mjs")):
        for target in re.findall(
                r"""(?:import\s+(?:[^'"]*\s+from\s+)?|require\s*\(\s*)"""
                r"""['"](\.[^'"]+)['"]""", text):
            resolved = os.path.normpath(
                os.path.join(os.path.dirname(rel), target))
            stem = resolved.rsplit(".", 1)[0] if "." in os.path.basename(
                resolved) else resolved
            if stem in mod_stems:
                intra.add(mod_stems[stem])
    return intra, set()


# -------------------------------------------------------------- fingerprints

def _fingerprint(members: list) -> str:
    """sha256 over the sorted (file, hash, symbol-span) material — the
    contract:component-map cache key."""
    material = sorted([f, h, span] for f, h, span in members)
    return hashlib.sha256(json.dumps(material, sort_keys=True,
                                     separators=(",", ":")).encode()
                          ).hexdigest()


def _module_fingerprint(files_hashes: list, fhash: str) -> str:
    material = {"files": sorted(files_hashes), "floors": fhash}
    return hashlib.sha256(json.dumps(material, sort_keys=True,
                                     separators=(",", ":")).encode()
                          ).hexdigest()


# ------------------------------------------------------------- module deriva

def _symbol_clusters(text: str, rel: str, floors: dict):
    """Cluster a big Python file's top-level symbols by name prefix, then
    pull each residual symbol that references exactly one passing cluster
    into it. Returns (clusters {name: [symbol nodes]}, residual [nodes]).
    Raises _DerivationError on a bad AST (fail-open handled by the caller)."""
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        raise _DerivationError(f"bad AST in {rel}: {e}") from None
    tops = {n.name: n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.ClassDef))}
    groups: dict = {}
    for name in sorted(tops):
        groups.setdefault(_sym_prefix(name), []).append(name)

    def span(names):
        return sum(tops[n].end_lineno - tops[n].lineno + 1 for n in names)

    passing = {p for p, names in groups.items()
               if len(names) >= floors["cluster_min_symbols"]
               and span(names) >= floors["cluster_min_lines"]}
    owner = {name: p for p, names in groups.items() for name in names}
    residual = [n for p, names in sorted(groups.items())
                if p not in passing for n in names]
    # reference cohesion: a residual symbol calling into exactly one passing
    # cluster joins it
    clusters = {p: list(groups[p]) for p in sorted(passing)}
    still = []
    for name in residual:
        refs = {owner[x.id] for x in ast.walk(tops[name])
                if isinstance(x, ast.Name) and x.id in tops
                and x.id != name}
        targets = sorted((refs & passing) - {owner[name]})
        if len(targets) == 1 and owner[name] not in passing:
            clusters[targets[0]].append(name)
        else:
            still.append(name)
    folded = sum(1 for p in groups if p not in passing)
    return ({p: sorted(clusters[p]) for p in clusters},
            sorted(still), tops, tree, folded)


def _derive_module(workspace: str, module: str, files: list, hashes: dict,
                   floors: dict, repo_stems: dict):
    """Derive the module's raw components (no lens maps yet). Returns
    (components, floor_folded). Raises _DerivationError on any failure."""
    files = sorted(files)[:MAX_MODULE_FILES]
    texts = {f: _read_text(workspace, f) for f in files}
    for f, t in texts.items():
        if t is None:
            raise _DerivationError(f"unreadable file: {f}")
    lines = {f: _count_lines(t) for f, t in texts.items()}
    sizes = {f: len(t.encode("utf-8", "replace")) for f, t in texts.items()}

    def core_only():
        members = [(f, hashes.get(f, ""), "") for f in files]
        return [{"id": f"{module}::core", "module": module,
                 "files": list(files), "symbols": [],
                 "_members": members, "deps": [], "derived_by": "core"}], 0

    candidate = (len(files) >= floors["candidate_min_files"]
                 or any(n >= floors["big_file_lines"]
                        for n in lines.values()))
    if not candidate:
        return core_only()

    mod_stems = _module_stems(files)
    # a Python file at/over the big-file floor gets SYMBOL clustering (a file
    # truncated by the read bound is never parsed — it stays a plain member)
    big = [f for f in files
           if f.endswith(".py") and lines[f] >= floors["big_file_lines"]
           and sizes[f] < MAX_FILE_BYTES]
    pool = [f for f in files if f not in big]

    # ---- file clusters: sub-directory convention + import cohesion
    common = os.path.commonpath([os.path.dirname(f) or "." for f in files]) \
        if files else "."
    clusters: dict = {}          # key -> {"files": set, "name": str}
    singleton: dict = {}         # file -> key (root-level loners)
    for f in pool:
        d = os.path.dirname(f) or "."
        sub = os.path.relpath(d, common) if d != common else "."
        if sub != "." and not sub.startswith(".."):
            key = "dir:" + sub.split("/")[0]
            clusters.setdefault(key, {"files": set(),
                                      "name": _sanitize(sub.split("/")[0]),
                                      "how": "directory"})
            clusters[key]["files"].add(f)
        else:
            key = "one:" + f
            clusters[key] = {"files": {f},
                             "name": _sanitize(
                                 os.path.basename(f).rsplit(".", 1)[0]),
                             "how": "cohesion"}
            singleton[f] = key

    intra_imports: dict = {}
    alias_targets: dict = {}
    for f in files:
        intra, alias = _file_refs(texts[f], f, mod_stems, repo_stems, module)
        intra_imports[f] = intra - {f}
        alias_targets[f] = alias

    # cohesion pass 1: mutually-importing loners cluster together
    for f in sorted(singleton):
        for g_ in sorted(intra_imports[f]):
            if (g_ in singleton and f in intra_imports.get(g_, ())
                    and singleton[f] != singleton[g_]):
                keep, drop = sorted((singleton[f], singleton[g_]))
                clusters[keep]["files"] |= clusters[drop]["files"]
                for x in clusters[drop]["files"]:
                    if x in singleton:
                        singleton[x] = keep
                del clusters[drop]
    # cohesion pass 2: a loner whose intra-module imports all land in exactly
    # one other cluster joins it
    file_cluster = {f: k for k, c in clusters.items() for f in c["files"]}
    for f in sorted(singleton):
        k = singleton.get(f)
        if k not in clusters or len(clusters[k]["files"]) != 1:
            continue
        targets = {file_cluster[g_] for g_ in intra_imports[f]
                   if g_ in file_cluster and file_cluster[g_] != k}
        if len(targets) == 1:
            tgt = targets.pop()
            clusters[tgt]["files"].add(f)
            del clusters[k]
            file_cluster[f] = tgt

    floor_folded = 0
    comp: dict = {}              # name -> component draft
    residual_files: set = set()

    def draft(name, how):
        return comp.setdefault(name, {"files": set(), "symbols": set(),
                                      "members": [], "how": {how}})

    for k in sorted(clusters):
        c = clusters[k]
        if len(c["files"]) >= floors["cluster_min_files"]:
            d = draft(c["name"], c["how"])
            d["files"] |= c["files"]
            d["members"] += [(f, hashes.get(f, ""), "")
                             for f in sorted(c["files"])]
        else:
            floor_folded += 1
            residual_files |= c["files"]

    # ---- intra-file symbol clusters for the big files
    sym_comp: dict = {}          # (file, symbol) -> component name
    residual_syms: dict = {}     # file -> [symbol names]
    file_tops: dict = {}
    for f in big:
        sclusters, still, tops, _tree, folded = _symbol_clusters(
            texts[f], f, floors)
        floor_folded += folded
        file_tops[f] = tops
        residual_syms[f] = still
        for pname, names in sorted(sclusters.items()):
            d = draft(pname, "symbols")
            d["files"].add(f)
            d["symbols"] |= set(names)
            d["members"] += [
                (f, hashes.get(f, ""),
                 f"{n}:{tops[n].lineno}-{tops[n].end_lineno}")
                for n in names]
            for n in names:
                sym_comp[(f, n)] = pname

    # ---- residual -> <module>::core
    core_files = residual_files | {f for f in big if residual_syms.get(f)}
    if core_files or residual_syms:
        d = draft("core", "core")
        d["files"] |= core_files
        d["members"] += [(f, hashes.get(f, ""), "")
                         for f in sorted(residual_files)]
        for f in big:
            tops = file_tops[f]
            d["symbols"] |= set(residual_syms[f])
            d["members"] += [
                (f, hashes.get(f, ""),
                 f"{n}:{tops[n].lineno}-{tops[n].end_lineno}")
                for n in residual_syms[f]]
            for n in residual_syms[f]:
                sym_comp[(f, n)] = "core"
    if not comp:                 # nothing earned a node -> single core
        return core_only()

    # ---- component-level deps
    comp_of_file = {}
    for name, d in comp.items():
        for f in d["files"]:
            if f not in big:
                comp_of_file[f] = name

    def comp_id(name):
        return f"{module}::{name}"

    deps: dict = {name: set() for name in comp}
    for name, d in sorted(comp.items()):
        for f in sorted(d["files"]):
            if f in big:
                continue
            # module-level imports (from the scanned graph material)
            for tgt in sorted(alias_targets.get(f, ())):
                deps[name].add((tgt, "imports"))
            # intra-module file imports crossing into a sibling component
            for g_ in sorted(intra_imports.get(f, ())):
                other = comp_of_file.get(g_)
                if other and other != name:
                    deps[name].add((comp_id(other), "references"))
    # symbol-level deps for the big files
    for f in big:
        tops = file_tops[f]
        try:
            tree = ast.parse(texts[f])
        except SyntaxError as e:     # pragma: no cover — parsed above
            raise _DerivationError(f"bad AST in {f}: {e}") from None
        intra_map, alias_map = _py_import_map(tree, f, mod_stems,
                                              repo_stems, module)
        for n, node in sorted(tops.items()):
            own = sym_comp.get((f, n))
            if own is None:
                continue
            names = {x.id for x in ast.walk(node)
                     if isinstance(x, ast.Name)}
            for ref in sorted(names & set(tops)):
                other = sym_comp.get((f, ref))
                if other and other != own:
                    deps[own].add((comp_id(other), "references"))
            for local in sorted(names & set(alias_map)):
                deps[own].add((alias_map[local], "imports"))
            for local in sorted(names & set(intra_map)):
                other = comp_of_file.get(intra_map[local])
                if other and other != own:
                    deps[own].add((comp_id(other), "references"))

    how_label = {("core",): "core", ("directory",): "directory",
                 ("cohesion",): "cohesion", ("symbols",): "symbols"}
    out = []
    for name in sorted(comp):
        d = comp[name]
        cid = comp_id(name)
        label = ("core" if name == "core" else
                 how_label.get(tuple(sorted(d["how"])), "mixed"))
        out.append({
            "id": cid, "module": module,
            "files": sorted(d["files"]),
            "symbols": sorted(d["symbols"]),
            "_members": sorted(d["members"]),
            "deps": [{"to": t, "kind": k}
                     for t, k in sorted(deps[name]) if t != cid],
            "derived_by": label,
        })
    return out, floor_folded


# ------------------------------------------------------------------ lens map

def _graph_payload(graph: dict, module: str) -> dict:
    edges = graph.get("edges") or []
    dependents = {e.get("from") for e in edges
                  if e.get("to") == module and e.get("from") != module}
    contracts = set()
    for e in edges:
        a, b = e.get("from"), e.get("to")
        for x, y in ((a, b), (b, a)):
            if x == module and str(y).startswith("contract:"):
                contracts.add(y)
    return {"hub_dependents": len(dependents),
            "boundary_contracts": sorted(contracts),
            "modules": [module]}


def _lens_map(workspace: str, graph: dict, c: dict) -> dict:
    import lens_signals
    vmap = lens_signals.route_verdicts(
        workspace, c["files"], graph=_graph_payload(graph, c["module"]))
    # contract:component-map pins {lens_id: {verdict, score, evidence}}
    return {lid: {"verdict": v["verdict"], "score": v["score"],
                  "evidence": v["evidence"]}
            for lid, v in sorted(vmap.items())}


# -------------------------------------------------------------------- derive

def derive(workspace: str, graph: dict, prev: dict | None = None):
    """Derive the component layer for `graph`. NEVER raises.

    Cache, two levels (prev = the previously persisted graph):
      * module level — a module whose (file, hash) material and floors hash
        are unchanged is reused wholesale (skip re-derivation entirely);
      * component level — a derived component whose fingerprint matches its
        previous incarnation reuses the cached lens_map (zero recompute).

    Returns (components, stats) with stats
      {components, recomputed, cache_hits, floor_folded, modules_skipped,
       degraded: [module...], floors_hash, error: str|None}.
    """
    stats = {"components": 0, "recomputed": 0, "cache_hits": 0,
             "floor_folded": 0, "modules_skipped": 0, "degraded": [],
             "floors_hash": "", "error": None}
    errors: list = []
    try:
        floors, ferr = load_floors(workspace)
        if ferr:
            errors.append(ferr)
        fhash = hashlib.sha256(json.dumps(floors, sort_keys=True,
                                          separators=(",", ":")).encode()
                               ).hexdigest()
        stats["floors_hash"] = fhash

        by_module: dict = {}
        for rel in sorted(graph.get("files") or {}):
            by_module.setdefault(_module_of(rel), []).append(rel)
        hashes = {rel: (row or {}).get("hash", "")
                  for rel, row in (graph.get("files") or {}).items()}

        prev = prev or {}
        prev_files = prev.get("files") or {}
        prev_comps: dict = {}
        for c in prev.get("components") or []:
            prev_comps.setdefault(c.get("module"), []).append(c)
        prev_fhash = ((prev.get("meta") or {}).get("decompose") or {}) \
            .get("floors")
        prev_by_module: dict = {}
        for rel in prev_files:
            prev_by_module.setdefault(_module_of(rel), []).append(rel)

        repo_stems = _repo_stems(graph)
        out: list = []
        for module in sorted(by_module):
            files = sorted(by_module[module])
            material = sorted([f, hashes.get(f, "")] for f in files)
            prev_material = sorted(
                [f, (prev_files.get(f) or {}).get("hash", "")]
                for f in sorted(prev_by_module.get(module) or []))
            # Phase 2 EM fix (MED): the cached lens_map bakes in the graph
            # flags (_graph_payload: hub_dependents, boundary_contracts)
            # that score graph-signal lenses. Keying the cache ONLY on file
            # content let a stale map survive graph drift (module becomes a
            # hub -> cached map still narrows) — a NARROWING failure against
            # the ladder's every-rung-only-widens guarantee. Both cache
            # levels now also require the module's graph signature.
            gsig = hashlib.sha256(json.dumps(
                _graph_payload(graph, module), sort_keys=True,
                separators=(",", ":")).encode()).hexdigest()[:16]
            prev_gsig_ok = all(c.get("graph_sig") == gsig
                               for c in prev_comps.get(module) or [])
            if (module in prev_comps and prev_fhash == fhash
                    and prev_gsig_ok
                    and material == prev_material):
                reused = json.loads(json.dumps(prev_comps[module]))
                out.extend(reused)
                stats["modules_skipped"] += 1
                stats["cache_hits"] += len(reused)
                continue
            try:
                comps, folded = _derive_module(workspace, module, files,
                                               hashes, floors, repo_stems)
                stats["floor_folded"] += folded
            except Exception as e:
                # fail-open: the module degrades to a single ::core with a
                # degraded marker — a broken file never breaks the scan
                errors.append(f"{module}: {e}")
                stats["degraded"].append(module)
                comps = [{"id": f"{module}::core", "module": module,
                          "files": files, "symbols": [],
                          "_members": [(f, hashes.get(f, ""), "")
                                       for f in files],
                          "deps": [], "derived_by": "core",
                          "degraded": True}]
            prev_by_id = {c.get("id"): c
                          for c in prev_comps.get(module) or []}
            for c in comps:
                c["fingerprint"] = _fingerprint(c.pop("_members"))
                c["graph_sig"] = gsig
                old = prev_by_id.get(c["id"])
                if old is not None and \
                        old.get("fingerprint") == c["fingerprint"] and \
                        old.get("graph_sig") == gsig and \
                        isinstance(old.get("lens_map"), dict) and \
                        old.get("lens_map"):
                    c["lens_map"] = json.loads(json.dumps(old["lens_map"]))
                    stats["cache_hits"] += 1
                    continue
                try:
                    c["lens_map"] = _lens_map(workspace, graph, c)
                    stats["recomputed"] += 1
                except Exception as e:
                    errors.append(f"{module}: lens map failed: {e}")
                    c["lens_map"] = {}
                    c["degraded"] = True
                    if module not in stats["degraded"]:
                        stats["degraded"].append(module)
            out.extend(comps)

        out.sort(key=lambda c: c["id"])
        stats["components"] = len(out)
        stats["error"] = "; ".join(errors) if errors else None
        return out, stats
    except Exception as e:       # absolute fail-open: never crash a scan
        stats["error"] = "; ".join(errors + [f"decompose failed: {e}"])
        return [], stats
