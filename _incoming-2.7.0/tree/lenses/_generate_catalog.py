import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# Routing rows live in _catalog_data.json (lenses 2.0). They used to be a
# literal list here; at 26 lenses that list was long enough that edits were
# error-prone and diffs unreadable. This file now only RENDERS — the data is
# validated below, so a malformed row still fails loudly at generation time.
L = [(r["group"], r["id"], r["name"], r["charter"], r["boundary"],
      r["looks_for"], r["globs"], r["task_types"], r["baseline"],
      r["deep_globs"], r["checks"], r.get("untested_trigger", False))
     for r in json.load(open(os.path.join(HERE, "_catalog_data.json")))]

_VALID_TASK_TYPES = {
    "api", "auth", "backend", "data", "deploy", "design-system", "devops",
    "distributed", "docs", "feature", "frontend", "greenfield", "infra",
    "infrastructure", "integration", "migration", "mobile", "prototype", "qa",
    "reliability", "screens", "solution-design", "system-design", "ui",
}
for _r in L:
    _bad = sorted(set(_r[7]) - _VALID_TASK_TYPES)
    assert not _bad, f"{_r[1]}: task types not in the vocabulary: {_bad}"
    assert _r[3] and _r[5], f"{_r[1]}: charter and looks_for are required"


cat = {
  "deep_threshold_files": 8,
  "code_extensions": [".py",".js",".ts",".tsx",".jsx",".vue",".svelte",".go",
                      ".rs",".java",".rb",".php",".c",".cpp",".cs",".sql",".sh",
                      ".kt",".swift",".m",".mm",".dart",".scala"],
  "lenses": []
}
for g,i,n,charter,boundary,lf,globs,tt,base,deep,checks,untested in L:
    e = {"id":i,"name":n,"group":g,"charter":charter,"boundary":boundary,
         "looks_for":lf}
    if globs: e["globs"]=globs
    if tt: e["task_types"]=tt
    if base: e["baseline"]=base
    if deep: e["deep_globs"]=deep
    if checks: e["checks"]=checks
    if untested: e["untested_trigger"]=True
    cat["lenses"].append(e)

# ---- stage profiles (v3 Phase 1, contract:stage-profiles) ----
# The candidate lens set per loop stage. PURE DATA: adding a lens to a
# profile is a one-line change here (regenerate) with no router code change.
# `review` is ALWAYS the full catalog so a final review can never be
# profile-narrowed; an unknown stage falls open to the full catalog in the
# router (more coverage, never less). Membership per design/design.md §3.2.
_ALL_IDS = [e["id"] for e in cat["lenses"]]
cat["stage_profiles"] = {
    "design": ["solution-design", "architecture", "tradeoffs", "scalability",
               "security", "data-safety", "services-selection", "cost-finops"],
    "build": ["code-quality", "testability", "backend", "frontend",
              "security"],
    "review": _ALL_IDS,
}
for _stage, _ids in cat["stage_profiles"].items():
    _unknown = sorted(set(_ids) - set(_ALL_IDS))
    assert not _unknown, \
        f"stage_profiles[{_stage!r}]: unknown lens ids {_unknown}"
    assert len(_ids) == len(set(_ids)), \
        f"stage_profiles[{_stage!r}]: duplicate lens ids"

import os
_out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog.json")
with open(_out, "w") as _f:
    json.dump(cat, _f, indent=2)
print(len(cat["lenses"]),"lenses across",len({e['group'] for e in cat['lenses']}),"groups")
from collections import defaultdict
g=defaultdict(list)
for e in cat["lenses"]: g[e["group"]].append(e["id"])
for k,v in g.items(): print(f"  {k}: {', '.join(v)}")
