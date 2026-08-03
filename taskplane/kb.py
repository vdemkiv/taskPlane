"""Knowledge base — durable, retrievable decisions & flows.

Distinct from the trace (`.taskplane/trace.jsonl` = every event, audit). The KB
(`knowledge/`) is the *why*: curated decision records (ADRs) and larger flows,
written at the loop's high-signal gate points and **retrieved by files/tags at
step start** so an agent begins with the handful of prior decisions that touch
its work — instead of re-deriving history. Lower tokens, and consistency (a
settled call isn't re-litigated).

Storage (stdlib only):
  knowledge/decisions/NNNN-slug.md   human-readable ADR
  knowledge/index.json               machine index (source of truth for search)
  knowledge/flows/*.md               larger playbooks (retrieved the same way)
"""

from __future__ import annotations

import datetime
import json
import os
import re

import taskplane_lite as tp


def kb_dir(ws: str) -> str:
    # The knowledge base lives in the EXTERNAL per-project store, not in the
    # repo — so decisions/index never get committed & pushed with the code.
    return tp.kb_root(ws)


def _index_path(ws: str) -> str:
    return os.path.join(kb_dir(ws), "index.json")


def load_index(ws: str) -> dict:
    p = _index_path(ws)
    if not os.path.exists(p):
        return {"decisions": [], "flows": []}
    with open(p) as f:
        return json.load(f)


def _save_index(ws: str, idx: dict) -> None:
    os.makedirs(kb_dir(ws), exist_ok=True)
    with open(_index_path(ws), "w") as f:
        json.dump(idx, f, indent=2)


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48] or "decision"


def _today() -> str:
    return datetime.date.today().isoformat()


# --------------------------------------------------------------- record

def record_decision(ws: str, title: str, *, context: str = "",
                    decision: str = "", rationale: str = "",
                    alternatives: str = "", tags=None, context_files=None,
                    links=None, status: str = "accepted",
                    date: str | None = None) -> dict:
    """Write a new ADR + index entry. Returns the entry."""
    idx = load_index(ws)
    n = len(idx["decisions"]) + 1
    did = f"{n:04d}"
    slug = _slug(title)
    entry = {
        "id": did,
        "title": title,
        "status": status,
        "date": date or _today(),
        "tags": list(tags or []),
        "context_files": list(context_files or []),
        "links": dict(links or {}),
        "file": f"decisions/{did}-{slug}.md",
    }
    idx["decisions"].append(entry)
    _save_index(ws, idx)

    os.makedirs(os.path.join(kb_dir(ws), "decisions"), exist_ok=True)
    body = f"""# {did} · {title}

- status: {entry['status']}
- date: {entry['date']}
- tags: {', '.join(entry['tags']) or '—'}
- context_files: {', '.join(entry['context_files']) or '—'}
- links: {json.dumps(entry['links']) if entry['links'] else '—'}

## Context
{context or '—'}

## Decision
{decision or '—'}

## Rationale
{rationale or '—'}

## Alternatives considered
{alternatives or '—'}
"""
    with open(os.path.join(kb_dir(ws), entry["file"]), "w") as f:
        f.write(body)
    tp.trace(ws, "decision_recorded", id=did, title=title, tags=entry["tags"])
    return entry


def publish(ws: str, ids=None) -> dict:
    """Share push (v1.5.0) — like committing work to the team. Copies
    decisions from the PRIVATE external store into the SHARED in-repo store
    (<ws>/.taskplane-kb/knowledge), re-numbered into the shared index. Each
    pushed decision is marked `published_as` in the private index, so a
    second push is idempotent; the shared copy records `published_from`.
    `ids`: push only these private ids; None pushes everything unpublished.
    The caller (a human, or a driver acting on a human's ask) then commits
    .taskplane-kb/ — publishing is deliberate, never automatic."""
    src_kb = os.path.join(tp.external_store_root(ws), "knowledge")
    dst_kb = os.path.join(tp.repo_store_root(ws), "knowledge")

    def _load(root):
        try:
            with open(os.path.join(root, "index.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"decisions": [], "flows": []}

    src_idx, dst_idx = _load(src_kb), _load(dst_kb)
    os.makedirs(os.path.join(dst_kb, "decisions"), exist_ok=True)
    pushed, already = [], []
    want = set(ids) if ids else None
    for d in src_idx["decisions"]:
        if want is not None and d["id"] not in want:
            continue
        if d.get("published_as"):
            already.append({"private": d["id"],
                            "shared": d["published_as"]})
            continue
        new_id = f"{len(dst_idx['decisions']) + 1:04d}"
        base = os.path.basename(d["file"])          # NNNN-slug.md
        new_file = os.path.join("decisions", new_id + base[4:])
        try:
            with open(os.path.join(src_kb, d["file"])) as f:
                body = f.read()
        except OSError:
            continue                                 # index entry w/o file
        with open(os.path.join(dst_kb, new_file), "w") as f:
            f.write(body)
        shared = dict(d)
        shared.update({"id": new_id, "file": new_file,
                       "published_from": d["id"]})
        shared.pop("published_as", None)
        dst_idx["decisions"].append(shared)
        d["published_as"] = new_id
        pushed.append({"private": d["id"], "shared": new_id,
                       "title": d["title"]})
    if pushed:
        with open(os.path.join(dst_kb, "index.json"), "w") as f:
            json.dump(dst_idx, f, indent=2)
        os.makedirs(src_kb, exist_ok=True)
        with open(os.path.join(src_kb, "index.json"), "w") as f:
            json.dump(src_idx, f, indent=2)
        tp.trace(ws, "share_push", count=len(pushed),
                 ids=[p["private"] for p in pushed])
    return {"pushed": pushed, "already_published": already,
            "shared_store": os.path.join(".taskplane-kb", "knowledge"),
            "next": "commit .taskplane-kb/ to make this visible to the "
                    "team" if pushed else "nothing new to push"}


def supersede(ws: str, old_id: str, by_id: str) -> None:
    idx = load_index(ws)
    for d in idx["decisions"]:
        if d["id"] == old_id:
            d["status"] = f"superseded-by-{by_id}"
    _save_index(ws, idx)


# --------------------------------------------------------------- retrieve

def get_decision(ws: str, did: str) -> dict | None:
    for d in load_index(ws)["decisions"]:
        if d["id"] == did:
            return d
    return None


def set_status(ws: str, did: str, status: str) -> dict | None:
    """Lifecycle transition (e.g. proposed -> accepted). Append-only: files
    are never deleted; only the index status moves."""
    idx = load_index(ws)
    hit = None
    for d in idx["decisions"]:
        if d["id"] == did:
            d["status"] = status
            hit = d
    _save_index(ws, idx)
    return hit


def governing(ws: str, scope_globs) -> list:
    """Decision-registry context extension (R-0002): ACCEPTED decisions whose
    linked modules overlap the given scope are ALWAYS in force for that work —
    returned unconditionally, not relevance-ranked. Constraints travel with
    the contract."""
    scope = list(scope_globs or [])
    if not scope:
        return []
    out = []
    for d in load_index(ws)["decisions"]:
        if d.get("status") != "accepted":
            continue
        mods = (d.get("links") or {}).get("modules") or []
        if mods and _path_overlap(scope, list(mods)):
            out.append(d)
    return out


CURRENT_STATE_CAP = 6000     # chars of inventory injected into a brief


def current_state(ws: str):
    """Current-state grounding (R-0004): the as-built inventory —
    context/current-state.md in the external store. Returned to every brief
    so design work is judged as a DELTA against what exists, never in a
    vacuum. None when the file is missing or still the unfilled scaffold
    (only headings/placeholder parentheticals — no real content lines)."""
    p = os.path.join(kb_dir(ws), "context", "current-state.md")
    try:
        text = open(p, encoding="utf-8").read()
    except OSError:
        return None
    filled = False
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("- **") and s.rstrip().endswith(":**"):
            continue                       # empty scaffold bullet
        if s.startswith("(") and s.endswith(")"):
            continue                       # placeholder hint
        if s.startswith(">"):
            continue                       # scaffold explainer quote
        filled = True
        break
    if not filled:
        return None
    if len(text) > CURRENT_STATE_CAP:
        text = text[:CURRENT_STATE_CAP] + "\n… (truncated — read " \
            "context/current-state.md in the knowledge store for the rest)"
    return {"path": os.path.join("context", "current-state.md"),
            "text": text}


def _stem(glob: str) -> str:
    """The fixed directory prefix of a glob, before the first wildcard."""
    cut = len(glob)
    for ch in "*?[":
        i = glob.find(ch)
        if i != -1:
            cut = min(cut, i)
    stem = glob[:cut]
    return stem.rsplit("/", 1)[0] + "/" if "/" in stem else ""


def _path_overlap(a_globs, b_globs) -> int:
    hits = 0
    a = [_stem(g) for g in a_globs]
    b = [_stem(g) for g in b_globs]
    for x in a:
        for y in b:
            if x and y and (x.startswith(y) or y.startswith(x)):
                hits += 1
                break
    return hits


def retrieve(ws: str, *, files=None, tags=None, limit: int = 5,
             include_superseded: bool = False) -> list:
    """Return the most relevant decisions for the given files/tags, ranked."""
    idx = load_index(ws)
    files = list(files or [])
    tags = set(tags or [])
    scored = []
    for d in idx["decisions"]:
        if not include_superseded and d["status"].startswith("superseded"):
            continue
        score = _path_overlap(files, d.get("context_files", []))
        score += 2 * len(tags & set(d.get("tags", [])))
        if score > 0:
            scored.append((score, d))
    scored.sort(key=lambda s: (s[0], s[1]["id"]), reverse=True)
    return [d for _, d in scored[:limit]]


def render_context(decisions: list) -> str:
    """Compact payload injected into a step's context (token-lean)."""
    if not decisions:
        return ""
    lines = ["Prior decisions relevant to this work (from the knowledge base):"]
    for d in decisions:
        lines.append(f"  [{d['id']}] {d['title']} ({d['status']}, {d['date']})"
                     + (f" — tags: {', '.join(d['tags'])}" if d.get("tags") else ""))
    lines.append("Honor these unless you have a concrete reason to supersede one.")
    return "\n".join(lines)


def list_decisions(ws: str) -> list:
    return load_index(ws)["decisions"]


def counts(ws: str) -> dict:
    """Public read model for a view — decision/requirement/open-debt counts
    from the committed index, without the caller hard-coding index.json's
    key names. The dashboard consumes this instead of reading the file."""
    idx = load_index(ws)
    return {
        "decisions": len(idx.get("decisions") or []),
        "requirements": len(idx.get("requirements") or []),
        "debt_open": len([x for x in (idx.get("debt") or [])
                          if x.get("status") == "open"]),
    }


# ------------------------------------------------------------------ lint

# Committed store = decision data only (docs/state-spec.md). These markers
# indicate instructions-to-a-model leaking into org data — fail closed.
PROMPT_MARKERS = ("you are ", "you're an ", "act as ", "your task is to",
                  "follow these instructions", "system prompt", "<system",
                  "## evaluator prompt", "respond with", "do not reveal")
# The committed store SHIPS in the public repo. Commercialization/pricing
# strategy is not decision data for a shipped plugin — it's private business
# strategy that must not travel with an Apache-2.0 clone. Flag it so a
# board/pricing record can't slip into the pushed tree unnoticed.
SENSITIVE_MARKERS = ("price per", "per-seat", "per seat", "per governed-agent",
                     "acv ", "arr ", "$/yr", "/yr", "k/yr", "paid sku",
                     "monetize", "monetise", "commercialization",
                     "commercialisation", "go-to-market", "pricing tier")
_MAX_FIELD = 4000   # decision fields are dense facts, not essays


def lint(ws: str) -> list:
    """Scan the committed decision store for prompt data. Returns
    violations [{file, problem}]; empty list = clean."""
    out = []
    # Scan the external knowledge store (where decisions now live) plus any
    # in-repo plan/specs. The lint still matters even though the KB no longer
    # ships: it keeps prompt data and pricing/commercial strategy out of a
    # store that may later be exported or shared with a team.
    roots = [tp.kb_root(ws), os.path.join(ws, "plan"), os.path.join(ws, "specs")]
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fn in files:
                if not fn.endswith((".md", ".json")):
                    continue
                p = os.path.join(dirpath, fn)
                rel = os.path.relpath(p, root)
                try:
                    with open(p, encoding="utf-8", errors="replace") as f:
                        text = f.read()
                except OSError:
                    continue
                low = text.lower()
                for m in PROMPT_MARKERS:
                    if m in low:
                        out.append({"file": rel,
                                    "problem": f"prompt marker {m!r} — "
                                    "committed store holds decision data "
                                    "only (docs/state-spec.md)"})
                        break
                for m in SENSITIVE_MARKERS:
                    if m in low:
                        out.append({"file": rel,
                                    "problem": f"commercial/pricing marker "
                                    f"{m!r} — the committed store ships "
                                    "publicly; keep pricing & commercialization "
                                    "strategy out of the repo"})
                        break
                if fn.endswith(".json"):
                    try:
                        data = json.loads(text)
                    except ValueError:
                        out.append({"file": rel, "problem": "invalid JSON"})
                        continue
                    def big(v, key=""):
                        if isinstance(v, str) and len(v) > _MAX_FIELD:
                            out.append({"file": rel, "problem":
                                        f"field {key or '(root)'} exceeds "
                                        f"{_MAX_FIELD} chars — distill to a "
                                        "decision, don't dump text"})
                        elif isinstance(v, dict):
                            for k, x in v.items():
                                big(x, k)
                        elif isinstance(v, list):
                            for x in v:
                                big(x, key)
                    big(data)
    return out
