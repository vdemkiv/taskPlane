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
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _atomic_json(path: str, obj) -> None:
    """tmp + os.replace — a reader never sees a torn index (v1.5.1)."""
    tmp = path + f".tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


def _save_index(ws: str, idx: dict) -> None:
    os.makedirs(kb_dir(ws), exist_ok=True)
    _atomic_json(_index_path(ws), idx)


import contextlib


@contextlib.contextmanager
def mutate(ws: str, root: str | None = None):
    """Serialize KB read-modify-write (v1.5.1) — mirrors loop.mutate.
    Parallel wave workers record decisions at gates; without a lock two
    writers read the same index, both mint id len+1, and the second save
    orphans the first's decision. Advisory flock on index.json.lock. `root`
    locks a specific store dir (v1.5.2: publish must lock BOTH the private
    and shared indexes, so it needs to name the root rather than default to
    kb_dir)."""
    d = root or kb_dir(ws)
    os.makedirs(d, exist_ok=True)
    # Shared never-silently-lock-free primitive (v2.3.1): the old inline flock
    # swallowed ImportError/OSError and proceeded UNLOCKED on exactly the
    # flock-less hosts (Windows / some FUSE) this plugin targets — the
    # lost-update the lock exists to prevent. tp.file_lock falls back to an
    # atomic mkdir lock instead of running lock-free.
    with tp.file_lock(os.path.join(d, "index.json")):
        yield


def _is_shared_store(ws: str) -> bool:
    """True when kb writes land in the committed in-repo store — decision ids
    there must be collision-free so concurrent teammate commits merge as pure
    additions (v1.5.2), not dense len+1 numbers two branches both mint."""
    try:
        return os.path.realpath(kb_dir(ws)).startswith(
            os.path.realpath(tp.repo_store_root(ws)))
    except OSError:
        return False


def _max_id(entries) -> int:
    """Highest numeric id prefix among index entries (0 when empty)."""
    top = 0
    for e in entries or []:
        m = re.match(r"(\d+)", str(e.get("id", "")))
        if m:
            top = max(top, int(m.group(1)))
    return top


def _next_seq(entries) -> int:
    """Deletion-safe seq: max existing numeric id prefix + 1, NOT
    len(entries)+1 — len+1 re-mints an existing id the moment any entry is
    compacted, archived or hand-removed from the index."""
    return _max_id(entries) + 1


def _mint_decision_seq(idx: dict) -> int:
    """Collision-safe decision id mint: a MONOTONIC counter stored in the
    index (advanced on every mint) combined with max-existing-id+1 for
    pre-counter indexes — never len+1. This guarantees an id is never reused
    even after entries are compacted/archived/hand-removed, including the
    highest ones. Deletion-safe minting is the prerequisite that makes the
    optional archival path (see archive()) safe: the hot index can shrink
    without any risk of an old id being re-minted."""
    try:
        counter = int((idx.get("id_counters") or {}).get("decisions", 0))
    except (TypeError, ValueError):
        counter = 0
    seq = max(counter, _max_id(idx.get("decisions"))) + 1
    idx.setdefault("id_counters", {})["decisions"] = seq
    return seq


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
    with mutate(ws):
        return _record_decision_locked(
            ws, title, context=context, decision=decision,
            rationale=rationale, alternatives=alternatives, tags=tags,
            context_files=context_files, links=links, status=status,
            date=date)


def _record_decision_locked(ws, title, *, context, decision, rationale,
                            alternatives, tags, context_files, links,
                            status, date) -> dict:
    idx = load_index(ws)
    seq = _mint_decision_seq(idx)
    slug = _slug(title)
    # In the shared in-repo store, mint a collision-free id (dense seq + a
    # content hash) so two teammates recording on different branches don't
    # both allocate the same dense number and conflict on merge. Private
    # external store keeps the clean dense id.
    if _is_shared_store(ws):
        did = _shared_id(seq, {"id": f"{seq:04d}", "title": title,
                               "date": date or _today()})
    else:
        did = f"{seq:04d}"
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
    with open(os.path.join(kb_dir(ws), entry["file"]), "w", encoding="utf-8") as f:
        f.write(body)
    tp.trace(ws, "decision_recorded", id=did, title=title, tags=entry["tags"])
    return entry


class SharedIndexCorrupt(Exception):
    """The shared index exists but does not parse — publishing must refuse
    rather than treat it as empty and erase team history (v1.5.1)."""


def _load_index_at(root: str, *, strict: bool):
    p = os.path.join(root, "index.json")
    if not os.path.exists(p):
        return {"decisions": [], "flows": []}
    try:
        with open(p, encoding="utf-8") as f:
            idx = json.load(f)
    except ValueError:
        if strict:
            raise SharedIndexCorrupt(p)
        idx = {"decisions": [], "flows": []}
    idx.setdefault("decisions", [])
    idx.setdefault("flows", [])
    return idx


def _shared_id(seq: int, entry: dict) -> str:
    """Collision-free shared id: dense number for display order + a short
    content hash so two teammates pushing concurrently on different
    branches mint DIFFERENT ids and their index entries merge as pure
    additions (v1.5.1 — dense len+1 ids collided)."""
    import hashlib
    h = hashlib.sha1((entry.get("id", "") + entry.get("title", "")
                      + entry.get("date", "")).encode("utf-8")
                     ).hexdigest()[:8]
    return f"{seq:04d}-{h}"


def publish(ws: str, ids=None) -> dict:
    """Share push (v1.5.x) — like committing work to the team. Copies
    decisions AND flows from the PRIVATE external store into the SHARED
    in-repo store (<ws>/.taskplane-kb/knowledge), under collision-free
    shared ids. Idempotency is CONTENT-BASED: an entry already present in
    the shared index (matched by `published_from`) is never re-copied even
    if the private-side marker was lost mid-crash — retries converge.
    A stale private marker whose shared entry vanished (store rebuilt) is
    dropped and the entry re-pushed. Requirements and context docs are NOT
    pushed (see the returned `not_covered` field). Publishing is always a
    deliberate human ask; the caller then commits .taskplane-kb/."""
    src_kb = os.path.join(tp.external_store_root(ws), "knowledge")
    dst_kb = os.path.join(tp.repo_store_root(ws), "knowledge")
    # Lock BOTH stores across the whole read-modify-write: a concurrent gate
    # decision recording into the shared index (or a second publish) must not
    # interleave and orphan an entry. Fixed order (src before dst) avoids
    # deadlock. (v1.5.2)
    with mutate(ws, root=src_kb), mutate(ws, root=dst_kb):
        return _publish_locked(ws, src_kb, dst_kb, ids)


def _publish_locked(ws, src_kb, dst_kb, ids):
    try:
        dst_idx = _load_index_at(dst_kb, strict=True)
    except SharedIndexCorrupt as e:
        return {"error": "shared index is corrupt — refusing to push over "
                         "it (pushing would erase team history). Repair "
                         f"or restore {e} first.", "pushed": []}
    src_idx = _load_index_at(src_kb, strict=False)

    src_real = os.path.realpath(src_kb)
    pushed, already, malformed = [], [], []
    want = set(ids) if ids else None
    seen_ids = set()

    for kind, subdir in (("decisions", "decisions"), ("flows", "flows")):
        dst_by_origin = {d.get("published_from"): d
                        for d in dst_idx[kind] if d.get("published_from")}
        dst_ids = {d.get("id") for d in dst_idx[kind]}
        os.makedirs(os.path.join(dst_kb, subdir), exist_ok=True)
        for d in src_idx[kind]:
            seen_ids.add(d.get("id"))
            if want is not None and d.get("id") not in want:
                continue
            # content-based idempotency: shared side is the truth
            hit = dst_by_origin.get(d.get("id"))
            if hit is not None:
                if d.get("published_as") != hit["id"]:
                    d["published_as"] = hit["id"]     # repair lost marker
                already.append({"private": d["id"], "shared": hit["id"]})
                continue
            if d.get("published_as"):
                if d["published_as"] in dst_ids:
                    already.append({"private": d["id"],
                                    "shared": d["published_as"]})
                    continue
                d.pop("published_as", None)   # stale — store was rebuilt
            fpath = os.path.realpath(
                os.path.join(src_kb, d.get("file", "")))
            if not fpath.startswith(src_real + os.sep):
                malformed.append({"private": d.get("id"),
                                  "problem": "file path escapes the "
                                             "private store"})
                continue
            try:
                with open(fpath, encoding="utf-8") as f:
                    body = f.read()
            except OSError:
                malformed.append({"private": d.get("id"),
                                  "problem": "decision file missing"})
                continue
            new_id = _shared_id(_next_seq(dst_idx[kind]), d)
            slug = re.sub(r"^\d+-", "", os.path.basename(d["file"]))
            new_file = os.path.join(subdir, f"{new_id}-{slug}")
            with open(os.path.join(dst_kb, new_file), "w", encoding="utf-8") as f:
                f.write(body)
            shared = dict(d)
            shared.update({"id": new_id, "file": new_file,
                           "published_from": d["id"]})
            shared.pop("published_as", None)
            dst_idx[kind].append(shared)
            d["published_as"] = new_id
            pushed.append({"private": d["id"], "shared": new_id,
                           "title": d.get("title", ""), "kind": kind})

    unknown = sorted(want - seen_ids) if want else []
    if pushed or already:              # `already` may carry marker repairs
        _atomic_json(os.path.join(dst_kb, "index.json"), dst_idx)
        os.makedirs(src_kb, exist_ok=True)
        _atomic_json(os.path.join(src_kb, "index.json"), src_idx)
    if pushed:
        tp.trace(ws, "share_push", count=len(pushed),
                 ids=[p["private"] for p in pushed])
    return {"pushed": pushed, "already_published": already,
            "unknown_ids": unknown, "malformed": malformed,
            "shared_store": os.path.join(".taskplane-kb", "knowledge"),
            "not_covered": "requirements and context docs stay private — "
                           "publish covers decisions and flows",
            "next": "commit .taskplane-kb/ to make this visible to the "
                    "team" if pushed else "nothing new to push"}


# Statuses closed enough to leave the hot index (superseded-by-* matches by
# prefix). Everything else — notably `accepted` decisions, which are LIVE
# constraints retrieved into briefs — stays hot.
_ARCHIVABLE_STATUSES = ("rejected", "withdrawn", "done")


def archive(ws: str, ids=None) -> dict:
    """OPTIONAL compaction (v2.3.0): move closed decisions (superseded-by-*/
    rejected/withdrawn/done, or an explicit `ids` list) from the hot
    index.json into index-archive.json in the same store — so list/retrieve
    and the SessionStart context hook stop paying full-parse cost for
    history, without the index growing forever.

    Id-safe by construction: minting is a monotonic counter (see
    _mint_decision_seq), advanced here over every id leaving the index, so
    an archived id is NEVER re-minted. ADR .md files are never touched
    (append-only store) — only index entries move, and archived entries stay
    readable in index-archive.json. Crash-safe ordering: the archive file is
    written BEFORE the shrunk index, so a crash in between duplicates an
    entry (harmless — dedup on next archive) rather than dropping one.
    A corrupt existing archive refuses (fail-closed) instead of being
    overwritten."""
    with mutate(ws):
        idx = load_index(ws)
        want = set(ids) if ids else None
        keep, moved = [], []
        for d in idx.get("decisions", []):
            status = str(d.get("status", ""))
            eligible = (d.get("id") in want) if want is not None else (
                status.startswith("superseded")
                or status in _ARCHIVABLE_STATUSES)
            (moved if eligible else keep).append(d)
        if not moved:
            return {"archived": [], "remaining": len(keep),
                    "archive": "index-archive.json"}
        arch_p = os.path.join(kb_dir(ws), "index-archive.json")
        arch = {"decisions": [], "flows": []}
        if os.path.exists(arch_p):
            try:
                with open(arch_p, encoding="utf-8") as f:
                    arch = json.load(f)
            except ValueError:
                return {"error": "index-archive.json is corrupt — repair or "
                                 f"restore it before archiving: {arch_p}",
                        "archived": [], "remaining": len(idx["decisions"])}
        arch.setdefault("decisions", [])
        already = {d.get("id") for d in arch["decisions"]}
        arch["decisions"].extend(d for d in moved
                                 if d.get("id") not in already)
        # advance the mint counter over EVERY id in the pre-archive index so
        # the ids leaving the hot index can never be re-minted
        counters = idx.setdefault("id_counters", {})
        try:
            cur = int(counters.get("decisions", 0))
        except (TypeError, ValueError):
            cur = 0
        counters["decisions"] = max(cur, _max_id(idx.get("decisions")))
        idx["decisions"] = keep
        _atomic_json(arch_p, arch)          # archive first …
        _save_index(ws, idx)                # … then the shrunk hot index
    out_ids = [d.get("id") for d in moved]
    tp.trace(ws, "kb_archived", count=len(moved), ids=out_ids)
    return {"archived": out_ids, "remaining": len(keep),
            "archive": "index-archive.json"}


def supersede(ws: str, old_id: str, by_id: str) -> None:
    with mutate(ws):
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
    with mutate(ws):
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


# Per-process lint memo: path -> ((mtime_ns, size), [violations]). lint()
# runs at every DoD check AND inside signoff, over a store that only grows —
# re-reading every historical record twice per command is pure waste. The
# memo is validated per file by stat on EVERY call, so strictness is intact:
# every record that would have been linted still contributes its violations
# (from the memo when byte-identical, re-scanned the moment mtime/size
# moves), and new files are always scanned. No cross-process cache.
_LINT_CACHE: dict[str, tuple] = {}


def _lint_file(p: str, rel: str) -> list:
    """All violations for ONE file (same checks as always)."""
    out = []
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return out
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
    if p.endswith(".json"):
        try:
            data = json.loads(text)
        except ValueError:
            out.append({"file": rel, "problem": "invalid JSON"})
            return out
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
                    st = os.stat(p)
                    sig = (st.st_mtime_ns, st.st_size)
                except OSError:
                    continue
                hit = _LINT_CACHE.get(p)
                if hit is not None and hit[0] == sig:
                    out.extend(hit[1])
                    continue
                violations = _lint_file(p, rel)
                _LINT_CACHE[p] = (sig, violations)
                out.extend(violations)
    return out
