"""Start a review in ONE call, and hand every lens agent ONE copy of the context.

Two measured costs, one cause: the review's opening sequence and its fan-out
both re-derive things taskplane already holds.

  * The opening. A review ran onboard, init, new, target, graph scan, graph
    impact, lens route, lens dispatch and two dashboard renders before a
    single lens looked at the diff — about ten shell calls, at a measured
    ~11k effective tokens each, and every command AND its output stays in
    the conversation to be re-read on every later turn. `tp loop evidence`
    already proved the fix for the evaluate step in v2.6: return everything
    the step needs in one payload, with the judgement slots empty.

  * The fan-out. Four lens agents cost ~754k effective tokens, "each
    carrying its own copy of the diff and the blast-radius brief". The diff
    is identical for all of them. Writing it once and citing the path costs
    one file; embedding it N times costs N copies at output weight.

Neither changes what a review DECIDES. The briefs carry the same contract,
the same lens, the same read-only harness; they just stop restating a
document that is already on disk next to them.
"""
import hashlib
import json
import os
import re
import shlex
import tempfile
from typing import Callable, Iterable

import taskplane_lite as tp

CONTEXT_DIR = os.path.join(".em-review", "context")
DIFF_NAME = "diff.patch"
IMPACT_NAME = "impact.json"
BRIEF_NAME = "blast-radius.md"
MAX_MANIFEST_BYTES = 16 * 1024
MAX_ROUTING_FILES = 200
MAX_ROUTING_FILE_BYTES = 64 * 1024
KERNEL_STATE = os.path.join(".em-review", "kernel-v2", "active.json")
KERNEL_RUNS = os.path.join(".em-review", "kernel-v2", "runs")
RESULT_SCHEMA = "taskplane.lens-slot-output/v2"
RESULT_AUTHOR = "lens-slot"


class ReviewKernelError(RuntimeError):
    """A normal review cannot preserve the selective-kernel contract."""


def _portable_ref(ref: dict | None) -> dict | None:
    """Host-neutral artifact reference used at every agent boundary."""
    if not ref:
        return None
    return {key: ref[key] for key in (
        "schema", "kind", "fingerprint", "digest", "bytes",
        "relative_path", "transport") if key in ref}


def _manifest(value: dict) -> dict:
    """Enforce the normal-operation stdout contract before the CLI prints."""
    from review_evidence import canonical_bytes
    counters = value.get("counters") if isinstance(value.get("counters"), dict) else None
    prior_manifest_bytes = int(value.get("manifest_bytes") or 0)
    emitted_before = max(0, int((counters or {}).get("emitted_bytes", 0))
                         - prior_manifest_bytes)
    value["manifest_bytes"] = 0
    if counters is not None:
        counters["emitted_bytes"] = emitted_before
    # Both counters are part of the bytes being counted. Iterate to the tiny
    # integer-width fixed point instead of measuring and then mutating.
    for _ in range(8):
        size = len(canonical_bytes(value))
        if size > MAX_MANIFEST_BYTES:
            raise ReviewKernelError(
                f"review manifest exceeds {MAX_MANIFEST_BYTES} bytes ({size})")
        changed = value["manifest_bytes"] != size
        value["manifest_bytes"] = size
        if counters is not None:
            total = emitted_before + size
            changed = changed or counters.get("emitted_bytes") != total
            counters["emitted_bytes"] = total
        if not changed:
            break
    if len(canonical_bytes(value)) != value["manifest_bytes"]:
        raise ReviewKernelError("review manifest byte accounting did not converge")
    return value


def _index_path(ws: str) -> str:
    return os.path.join(ws, KERNEL_STATE)


def _state_path(ws: str, run_id: str) -> str:
    return os.path.join(ws, KERNEL_RUNS, run_id, "state.json")


def _load_index(ws: str) -> dict:
    row = tp.load_json(_index_path(ws), default=None,
                       what="review kernel run index")
    if not isinstance(row, dict) or row.get("schema") != \
            "taskplane.review-run-index/v2":
        return {"schema": "taskplane.review-run-index/v2", "runs": {}}
    runs = row.get("runs")
    if not isinstance(runs, dict):
        raise ReviewKernelError("review kernel run index is corrupt")
    return row


def _save_state(ws: str, state: dict) -> None:
    run_id = str(state.get("run_id") or "")
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ReviewKernelError("review state has invalid run-id")
    tp.atomic_write_json(_state_path(ws, run_id), state, sort_keys=True)
    # Separate run files remove the old active.json payload collision; the
    # index still needs a read-modify-write lock so two starts cannot erase
    # each other's entries.
    with tp.file_lock(_index_path(ws)):
        index = _load_index(ws)
        index["runs"][run_id] = {
            "state": os.path.relpath(_state_path(ws, run_id), ws).replace(
                os.sep, "/"),
            "status": state.get("status"), "stage": state.get("stage"),
            "target_fingerprint": (state.get("target") or {}).get(
                "fingerprint"),
        }
        index["latest"] = run_id
        tp.atomic_write_json(_index_path(ws), index, sort_keys=True)


def _load_state(ws: str, run_id: str | None = None) -> dict:
    index = _load_index(ws)
    if run_id is None:
        active = sorted(rid for rid, row in index["runs"].items()
                        if (row or {}).get("status") in {
                            "ready", "prepared", "staged", "publishing",
                            "committed"})
        if len(active) > 1:
            raise ReviewKernelError(
                "several review runs are active; provide an explicit run-id")
        run_id = active[0] if active else index.get("latest")
    if not run_id or run_id not in index["runs"]:
        raise ReviewKernelError("no matching review kernel run; run review start")
    state = tp.load_json(_state_path(ws, run_id), default=None,
                         what="review kernel run state")
    if not isinstance(state, dict):
        raise ReviewKernelError("no active review kernel run; run review start")
    return state


def _run_id(stage: str, target_fingerprint: str,
            context_fingerprint: str, revision: int) -> str:
    material = "\0".join((stage, target_fingerprint,
                           context_fingerprint, str(revision)))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def bounded_caller_expander(graph: dict) -> Callable:
    """Bind every review surface to one frozen symbol graph adapter."""
    import depgraph

    frozen = graph if isinstance(graph, dict) else {}

    def expand(*, snapshot, changed_symbols, bounds):
        # ``snapshot`` proves the caller supplied a pinned review target. The
        # canonical symbol index is the graph captured for that target, never
        # ambient repository state that may move between Review and Evaluate.
        del snapshot
        return depgraph.bounded_changed_symbol_callers(
            snapshot=frozen, changed_symbols=changed_symbols, bounds=bounds)

    return expand


def canonical_diff_patch(ws: str, base: str,
                         max_bytes: int = 400_000) -> tuple[int, str]:
    """One bounded patch including untracked files for every review surface."""
    import subprocess

    try:
        tracked = subprocess.run(
            ["git", "diff", base], cwd=ws, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120)
        if tracked.returncode:
            return tracked.returncode, ""
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"], cwd=ws,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120)
        if untracked.returncode:
            return untracked.returncode, ""
        parts = [tracked.stdout or ""]
        size = len(parts[0].encode("utf-8"))
        for rel in sorted(line for line in untracked.stdout.splitlines()
                          if line.strip()):
            addition = subprocess.run(
                ["git", "diff", "--no-index", "--", "/dev/null", rel],
                cwd=ws, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120)
            if addition.returncode not in {0, 1}:
                return addition.returncode, ""
            text = addition.stdout or ""
            size += len(text.encode("utf-8"))
            if size > max_bytes:
                return 0, ""
            parts.append(text)
        return 0, "".join(parts)
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def changed_symbols_from_patch(patch: str) -> list[str]:
    """Bounded language-neutral symbol hints from the one canonical diff."""
    patterns = (
        re.compile(r"^\+\s*(?:async\s+)?def\s+([A-Za-z_][\w]*)"),
        re.compile(r"^\+\s*class\s+([A-Za-z_][\w]*)"),
        re.compile(r"^\+\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_][\w]*)"),
        re.compile(r"^\+\s*(?:export\s+)?(?:async\s+)?(?:function|class)\s+"
                   r"([A-Za-z_$][\w$]*)"),
    )
    found = set()
    for line in str(patch or "").splitlines():
        if line.startswith("+++"):
            continue
        if line.startswith("@@") and "@@" in line[2:]:
            context = line.rsplit("@@", 1)[-1].strip()
            for pattern in patterns:
                match = pattern.match("+" + context)
                if match:
                    found.add(match.group(1))
                    break
            continue
        for pattern in patterns:
            match = pattern.match(line)
            if match:
                found.add(match.group(1))
                break
        if len(found) >= 128:
            break
    return sorted(found)


def changed_content_from_patch(patch: str) -> dict[str, str]:
    """Bounded changed-hunk content from the one canonical unified diff.

    Applicability markers must describe this change, not unrelated words
    elsewhere in a large touched file. Added, removed, and nearby unchanged
    hunk lines count: deleting an auth check still summons the security lens,
    while a deny-to-allow edit retains its enclosing authorization context.
    """
    current = None
    rows: dict[str, list[str]] = {}
    sizes: dict[str, int] = {}
    in_hunk = False
    for line in str(patch or "").splitlines():
        if line.startswith("diff --git "):
            try:
                parts = shlex.split(line)
            except ValueError:
                parts = line.split()
            current = None
            if len(parts) >= 4:
                candidate = parts[3]
                candidate = (candidate[2:] if candidate.startswith("b/")
                             else candidate)
                if candidate in rows or len(rows) < MAX_ROUTING_FILES:
                    current = candidate
                    rows.setdefault(current, [])
                    sizes.setdefault(current, 0)
            in_hunk = False
            continue
        if line.startswith("@@"):
            in_hunk = current is not None
            continue
        if not current or not in_hunk or not line.startswith(("+", "-", " ")) \
                or line.startswith(("+++", "---")):
            continue
        text = line[1:]
        encoded = (text + "\n").encode("utf-8")
        remaining = MAX_ROUTING_FILE_BYTES - sizes[current]
        if remaining <= 0:
            continue
        if len(encoded) > remaining:
            text = text.encode("utf-8")[:max(0, remaining - 1)] \
                .decode("utf-8", "ignore")
            encoded = (text + "\n").encode("utf-8") if text else b""
        if encoded:
            rows[current].append(text)
            sizes[current] += len(encoded)
    return {path: "\n".join(lines) + ("\n" if lines else "")
            for path, lines in sorted(rows.items())}


def _routing_decision(routing: dict, catalog: dict) -> dict:
    """Validate one complete catalog mapping and preserve its evidence."""
    expected = [str(row.get("id")) for row in catalog.get("lenses") or []]
    rows = routing.get("lenses") or []
    if len(rows) != len(expected) or {str(x.get("id")) for x in rows} != set(expected):
        raise ReviewKernelError("mapper did not disposition the complete lens catalog")
    decision = {}
    for row in rows:
        lens_id = str(row.get("id"))
        verdict = str(row.get("tier") or row.get("verdict") or "")
        if verdict == "deep (forced)":
            verdict = "deep"
        if verdict not in {"deep", "light", "n/a"}:
            raise ReviewKernelError(f"mapper returned invalid verdict for {lens_id}")
        evidence_key = "negative_evidence" if verdict == "n/a" else "evidence"
        evidence = list(row.get(evidence_key) or row.get("reasons") or [])
        if verdict == "n/a" and not evidence:
            raise ReviewKernelError(f"mapper returned unevidenced n/a for {lens_id}")
        decision[lens_id] = {
            "verdict": verdict, "score": row.get("score"),
            evidence_key: evidence,
        }
        if row.get("floor"):
            decision[lens_id]["floor"] = row["floor"]
    return decision


def _slot_plan(store, envelope_ref: dict, routing: dict,
               decision: dict, *, base: str, runnability: dict,
               stage: str) -> tuple[list, list]:
    """Allocate exact deep slots plus at most one bounded light sweep."""
    import lens as lensmod
    import review_evidence as evidence

    full = lensmod.dispatch_briefs(routing, base=base, runnability=runnability)
    deep = [lid for lid, row in sorted(decision.items())
            if row["verdict"] == "deep"]
    light = [lid for lid, row in sorted(decision.items())
             if row["verdict"] == "light"]
    entries = [(f"deep.{lid}", [lid]) for lid in deep]
    if light:
        entries.append(("light-sweep", light))
    revision = evidence.next_revision(store)
    full_briefs = {row["id"]: row for row in full.get("deep") or []}
    if full.get("sweep"):
        full_briefs["light-sweep"] = full["sweep"]
    internal, manifest = [], []
    for slot_id, lens_ids in entries:
        view_ref = evidence.create_scoped_view(
            store, envelope_ref, slot_id=slot_id, lens_ids=lens_ids,
            evidence={lid: decision[lid] for lid in lens_ids})
        lease_ref = evidence.create_slot_lease(
            store, envelope_ref, view_ref, slot_id=slot_id,
            lens_ids=lens_ids, canonical_revision=revision)
        source = full_briefs.get(
            "light-sweep" if slot_id == "light-sweep" else lens_ids[0]) or {}
        result_path = os.path.join(
            ".eval" if stage == "build" else ".em-review",
            "kernel-v2", "results",
            f"{lease_ref['fingerprint']}.json").replace(os.sep, "/")
        producer_contract = {
            "task": f"review lens slot {slot_id} lease {lease_ref['fingerprint']}",
            "task_slot": f"review-{lease_ref['fingerprint'][:20]}",
            "read_only": True, "write_allow": [result_path],
        }
        result_schema = {
            "schema": RESULT_SCHEMA,
            "authored_by": RESULT_AUTHOR,
            "required": ["schema", "lease_fingerprint", "slot_id",
                         "lens_ids", "target_fingerprint",
                         "context_fingerprint", "view_fingerprint",
                         "canonical_revision", "authored_by",
                         "lens_results", "findings"],
            "lens_result": {
                "type": "object",
                "required": ["lens", "verdict", "blockers"],
                "verdict": ["pass", "fail"],
                "blockers": {"type": "integer", "minimum": 0}},
            "findings": {"type": "array", "items": "finding"},
            "finding": {"type": "object",
                        "required": ["lens", "severity", "class", "file",
                                     "line", "title", "scenario", "fix"]},
            "codex_completion_receipt": {
                "required_lines": ["taskplane-result-path:<result_path>",
                                   "taskplane-result-sha256:<sha256>"]},
        }
        brief = {
            "schema": "taskplane.lens-brief/v2", "slot_id": slot_id,
            "lens_ids": lens_ids, "target_fingerprint":
                store.read(envelope_ref)["target_fingerprint"],
            "context_fingerprint": envelope_ref["fingerprint"],
            "view": _portable_ref(view_ref), "lease": _portable_ref(lease_ref),
            "canonical_revision": revision, "result_path": result_path,
            "authored_by": RESULT_AUTHOR, "result_schema": result_schema,
            "producer_contract": producer_contract,
            # Compatibility alias, deliberately identical to the canonical
            # producer contract.  Two different task_slot values in one
            # brief make a correct host activation impossible.
            "contract": dict(producer_contract),
            "prompt": ("Read the scoped view by reference. Do not run git diff, "
                       "graph impact/scan, requirement lookup, or a runnability "
                       "probe. Activate producer_contract under its exact "
                       "task_slot, then use the host Write tool to author the "
                       "declared result_schema at result_path. Copy every "
                       "identity field exactly; authored_by is lens-slot."),
            # Concrete model ids are host-adapter transport, not canonical
            # review evidence: Claude's cheap default is `haiku`, while Codex
            # inherits.  Persist the portable capability request only.
            "role": {key: source.get(key) for key in
                     ("agent", "model_tier", "reasoning_effort",
                      "task_name", "role_marker") if source.get(key) is not None},
        }
        brief_ref = store.put("lens-brief", brief)
        row = {"slot_id": slot_id, "lens_ids": lens_ids,
               "view": view_ref, "lease": lease_ref,
               "brief": brief_ref, "result_path": result_path,
               "producer_contract": producer_contract}
        internal.append(row)
        manifest.append({"slot_id": slot_id, "lens_ids": lens_ids,
                         "brief": _portable_ref(brief_ref),
                         "view": _portable_ref(view_ref),
                         "lease": _portable_ref(lease_ref),
                         "result_path": result_path})
    expanded = {lid for row in internal for lid in row["lens_ids"]}
    if expanded != set(deep) | set(light) or len(entries) > len(deep) + 1:
        raise ReviewKernelError("dispatch slots do not equal deep plus light mapping")
    return internal, manifest


def start_review(ws: str, *, target: dict, graph: dict, impact: dict,
                 diff: dict, runnability: dict | None = None,
                 requirement: dict | None = None,
                 acceptance: Iterable | None = None,
                 contracts: Iterable | None = None, stage: str = "review",
                 task_type: str | None = None, base: str = "HEAD",
                 caller_expander: Callable | None = None,
                 router: Callable | None = None,
                 routing_content: dict | None = None) -> dict:
    """Run the normal Review/Evaluate/final-EM evidence kernel once.

    The absolute order is target -> graph quality/one expansion -> complete
    impact -> one mapping -> envelope -> exact dispatch.  Any uncertainty
    returns a compact zero-dispatch manifest.
    """
    import graph_quality
    import lens as lensmod
    import review_evidence as evidence
    import runnability as run_probe
    import yield_meter

    # Runnability is briefing evidence only.  Keeping its one-shot collection
    # inside the review producer means the loop/gates never consult it and a
    # broken or unavailable command can never become an enforcement input.
    if runnability is None:
        runnability = run_probe.evidence_record(run_probe.probe_once(ws))

    store = evidence.ArtifactStore(ws)
    files = sorted({str(x) for x in diff.get("files") or []})
    symbols = sorted({str(x) for x in diff.get("changed_symbols") or []})
    # Empty changed-symbol input is not evidence of complete caller coverage.
    # A strong module graph needs no symbol expansion; a sparse one must fail
    # closed because there is no bounded seed to expand.
    source_change = any(os.path.splitext(path)[1].lower() in {
        ".py", ".js", ".jsx", ".mjs", ".ts", ".tsx", ".go",
        ".cs", ".java", ".rb"} for path in files)
    bounded_expander = caller_expander if symbols or not source_change else None
    quality = graph_quality.assess(
        graph, target_head=str(target.get("head") or ""),
        changed_files=files, changed_symbols=symbols, impact=impact,
        caller_expander=bounded_expander, snapshot={
            "target_fingerprint": target.get("fingerprint"),
            "target_head": target.get("head")},
    )
    if source_change and not symbols:
        coverage = quality["changed_symbol_caller_coverage"]
        coverage["ratio"] = None
        coverage["status"] = "incomplete"
        quality["sufficient"] = False
        quality["status"] = "impact_incomplete"
        quality["reasons"] = sorted(set(
            list(quality.get("reasons") or []) + ["symbol_extraction_incomplete"]))
        quality.pop("fingerprint", None)
        quality["fingerprint"] = graph_quality.fingerprint(quality)
    quality_ref = store.put("graph-quality", quality,
                            fingerprint=quality["fingerprint"])
    observation = yield_meter.observation_bundle(
        ws, "review start", ["target", "contract", "graph-quality",
                             "impact", "runnability", "requirements"])
    counters = {
        "top_level_cli_count": 1, "emitted_bytes": 0,
        "repeated_derivation_bytes": 0, "dispatched_agent_count": 0,
        "prompt_view_bytes": 0, "artifact_render_bytes": 0,
        "duplicate_artifact_bytes": 0, "duplicate_artifact_count": 0,
        "envelope_count": 0, "view_count": 0,
        "diff_derivation_count": 1, "impact_derivation_count": 1,
        "caller_expansion_count": int((quality.get("expansion") or {}).get("count", 0)),
        "observation_actions": observation["actions"],
        "effective_tokens": None,
    }
    if quality.get("status") != "complete":
        run_id = _run_id(stage, str(target.get("fingerprint") or ""),
                         quality["fingerprint"], 0)
        manifest = _manifest({
            "schema": "taskplane.review-start-manifest/v2",
            "status": "impact_incomplete", "stage": stage,
            "run_id": run_id,
            "target_fingerprint": target.get("fingerprint"),
            "graph_quality": _portable_ref(quality_ref),
            "routing_mode": "selective", "slots": [], "briefs": [],
            "agents": [], "counters": counters,
        })
        _save_state(ws, {"schema": "taskplane.review-run-state/v2",
                         "run_id": run_id, "status": "impact_incomplete",
                         "stage": stage, "target": target,
                         "quality": quality_ref, "manifest": manifest})
        return manifest

    route_fn = router or (lambda: lensmod.route(
        files, task_type=task_type, breadth="routed", stage=stage,
        workspace=ws, requirement_text=(requirement or {}).get("text"),
        content_by_file=routing_content))
    try:
        routing = route_fn()
        if (routing.get("context") or {}).get("status") == "mapper_unavailable":
            raise ReviewKernelError("mapper_unavailable")
        catalog = lensmod.load_catalog()
        decision = _routing_decision(routing, catalog)
    except Exception as exc:
        run_id = _run_id(stage, str(target.get("fingerprint") or ""),
                         quality["fingerprint"], 0)
        manifest = _manifest({
            "schema": "taskplane.review-start-manifest/v2",
            "status": "mapper_unavailable", "stage": stage,
            "run_id": run_id,
            "target_fingerprint": target.get("fingerprint"),
            "graph_quality": _portable_ref(quality_ref),
            "routing_mode": "selective", "slots": [], "briefs": [],
            "agents": [], "reason": f"{exc.__class__.__name__}: {exc}",
            "counters": counters,
        })
        _save_state(ws, {"schema": "taskplane.review-run-state/v2",
                         "run_id": run_id, "status": "mapper_unavailable",
                         "stage": stage, "target": target,
                         "quality": quality_ref, "manifest": manifest})
        return manifest

    decision_ref = store.put("routing-decision", {
        "schema": "taskplane.routing-decision/v2", "stage": stage,
        "routing_mode": "selective", "dispositions": decision})
    routing_input_ref = store.put("routing-input", {
        "schema": "taskplane.routing-input/v2", "target": target,
        "diff": diff, "impact": quality.get("impact") or impact,
        "graph_quality": _portable_ref(quality_ref),
        "runnability": runnability, "requirement": requirement or {},
        "acceptance": list(acceptance or []),
        "contracts": sorted({str(x) for x in contracts or []}),
        "change": {"type": task_type, "stage": stage}})
    envelope_ref = evidence.create_envelope(
        store, target=target, diff=diff,
        impact=quality.get("impact") or impact, graph_quality=quality,
        runnability=runnability, requirement=requirement or {},
        acceptance=acceptance or [], contracts=contracts or [],
        change={"type": task_type, "stage": stage,
                "routing_input": _portable_ref(routing_input_ref),
                "routing_decision": _portable_ref(decision_ref)})
    internal_slots, slots = _slot_plan(
        store, envelope_ref, routing, decision, base=base,
        runnability=runnability, stage=stage)
    counters.update({
        "dispatched_agent_count": len(slots), "envelope_count": 1,
        "view_count": len(slots),
        "prompt_view_bytes": sum(row["view"]["bytes"] for row in slots),
    })
    counts = {tier: sum(1 for row in decision.values()
                        if row["verdict"] == tier)
              for tier in ("deep", "light", "n/a")}
    revision = (internal_slots[0]["lease"] and
                store.read(internal_slots[0]["lease"])["canonical_revision"]
                if internal_slots else evidence.next_revision(store))
    run_id = _run_id(stage, str(target.get("fingerprint") or ""),
                     envelope_ref["fingerprint"], revision)
    for slot in internal_slots:
        slot["run_id"] = run_id
    manifest = _manifest({
        "schema": "taskplane.review-start-manifest/v2", "status": "ready",
        "stage": stage, "run_id": run_id, "routing_mode": "selective",
        "target_fingerprint": target.get("fingerprint"),
        "context_fingerprint": envelope_ref["fingerprint"],
        "graph_quality": _portable_ref(quality_ref),
        "routing_input": _portable_ref(routing_input_ref),
        "routing_decision": _portable_ref(decision_ref),
        "envelope": _portable_ref(envelope_ref), "routing_counts": counts,
        "slots": slots, "counters": counters,
    })
    _save_state(ws, {
        "schema": "taskplane.review-run-state/v2", "status": "ready",
        "run_id": run_id,
        "target": target, "stage": stage, "routing": routing,
        "routing_decision": decision_ref, "envelope": envelope_ref,
        "quality": quality_ref, "slots": internal_slots,
        "manifest": manifest, "counters": counters,
    })
    tp.trace(ws, "review_kernel_started", stage=stage,
             run_id=run_id,
             target_head=target.get("head"),
             target_fingerprint=target.get("fingerprint"),
             context_fingerprint=envelope_ref["fingerprint"],
             graph_quality_status=quality.get("status"),
             routing_mode="selective", routing_complete=True,
             dispositions_complete=len(decision) == len(
                 catalog.get("lenses") or []),
             routing_counts=counts,
             slots=[row["slot_id"] for row in slots])
    return manifest


def _receipt_path(ws: str, lease_fingerprint: str) -> str:
    return os.path.join(ws, ".em-review", "kernel-v2", "provenance",
                        lease_fingerprint + ".json")


def _producer_assignment_path(ws: str, lease_fingerprint: str) -> str:
    return os.path.join(ws, ".em-review", "kernel-v2", "producers",
                        lease_fingerprint + ".json")


def _child_observation_path(ws: str, event: dict) -> str:
    identity = _hook_child_identity(event)
    digest = hashlib.sha256(json.dumps(
        identity, separators=(",", ":")).encode("utf-8")).hexdigest()
    return os.path.join(ws, ".em-review", "kernel-v2", "children",
                        digest + ".json")


def _observe_hook_child(ws: str, event: dict) -> dict:
    host, session, child_id = _hook_child_identity(event)
    observed = {
        "schema": "taskplane.hook-child-observation/v1",
        "producer_host": host, "producer_session": session,
        "producer_child_id": child_id, "host_event": "SubagentStart",
    }
    path = _child_observation_path(ws, event)
    with tp.file_lock(path):
        prior = tp.load_json(path, default=None, what="hook child observation")
        if prior is None:
            tp.atomic_write_json(path, observed, sort_keys=True)
            return observed
        if not isinstance(prior, dict) or any(
                prior.get(key) != value for key, value in observed.items()):
            raise ReviewKernelError("hook child observation is contradictory")
        return prior


def _hook_child_identity(event: dict) -> tuple[str, str, str]:
    child_id = str(event.get("agent_id") or "").strip()
    session = str(event.get("session_id") or event.get("turn_id") or "").strip()
    if not child_id or not session:
        raise ReviewKernelError(
            "leased result producer has no hook-observed child identity")
    return ("claude" if event.get("session_id") else "codex",
            session, child_id)


def register_slot_producer(ws: str, *, event: dict, contract: dict,
                           task_slot: str | None = None,
                           _observe_lifecycle: bool = True) -> dict | None:
    """Bind a leased slot to the exact child observed at SubagentStart."""
    if _observe_lifecycle:
        _observe_hook_child(ws, event)
    candidates = []
    for run_id in sorted(_load_index(ws)["runs"]):
        state = tp.load_json(_state_path(ws, run_id), default=None,
                             what="review kernel run state")
        if not isinstance(state, dict) or state.get("status") != "ready":
            continue
        for slot in state.get("slots") or []:
            expected = slot["producer_contract"]
            if contract.get("task") == expected["task"] and \
                    list(contract.get("write_allow") or []) == \
                    expected["write_allow"] and \
                    str(task_slot or "") == expected["task_slot"]:
                candidates.append((state, slot))
    if not candidates:
        return None
    if len(candidates) != 1 or not contract.get("read_only"):
        raise ReviewKernelError("leased slot producer dispatch is ambiguous")
    state, slot = candidates[0]
    store = __import__("review_evidence").ArtifactStore(ws)
    lease = store.read(slot["lease"])
    host, session, child_id = _hook_child_identity(event)
    assignment = {
        "schema": "taskplane.slot-producer-assignment/v1",
        "run_id": state["run_id"],
        "lease_fingerprint": lease["lease_fingerprint"],
        "slot_id": lease["slot_id"], "result_path": slot["result_path"],
        "contract_task": slot["producer_contract"]["task"],
        "contract_task_slot": slot["producer_contract"]["task_slot"],
        "producer_host": host, "producer_session": session,
        "producer_child_id": child_id, "host_event": "SubagentStart",
    }
    path = _producer_assignment_path(ws, lease["lease_fingerprint"])
    child_path = _child_observation_path(ws, event)
    binding_lock = os.path.join(ws, ".em-review", "kernel-v2",
                                "producer-binding.json")
    with tp.file_lock(binding_lock):
        child = tp.load_json(child_path, default=None,
                             what="hook child observation")
        if not isinstance(child, dict) or child.get("schema") != \
                "taskplane.hook-child-observation/v1":
            raise ReviewKernelError(
                "leased result child was not observed at SubagentStart")
        bound = child.get("lease_fingerprint")
        if bound not in (None, lease["lease_fingerprint"]):
            raise ReviewKernelError(
                "dispatched child is already bound to another leased slot")
        prior = tp.load_json(path, default=None,
                             what="slot producer assignment")
        if prior is not None and prior != assignment:
            raise ReviewKernelError(
                "leased result slot is already bound to another dispatched child")
        tp.atomic_write_json(path, assignment, sort_keys=True)
        child = dict(child, lease_fingerprint=lease["lease_fingerprint"],
                     run_id=state["run_id"], slot_id=lease["slot_id"])
        tp.atomic_write_json(child_path, child, sort_keys=True)
    return assignment


def _result_bytes_from_write_event(tool_name: str, tool_input: dict,
                                   result_path: str) -> bytes:
    if tool_name == "Write":
        content = tool_input.get("content")
        if not isinstance(content, str):
            raise ReviewKernelError(
                "leased result Write must expose exact content bytes")
        return content.encode("utf-8")
    if tool_name == "apply_patch":
        patch = str(tool_input.get("command") or "")
        lines = patch.splitlines()
        marker = "*** Add File: "
        starts = [(index, line[len(marker):].strip())
                  for index, line in enumerate(lines)
                  if line.startswith(marker)]
        if len(starts) != 1 or tp.norm(starts[0][1]) != tp.norm(result_path):
            raise ReviewKernelError(
                "leased result patch must add exactly its result path")
        content = []
        for line in lines[starts[0][0] + 1:]:
            if line == "*** End Patch":
                break
            if line.startswith("*** ") or not line.startswith("+"):
                raise ReviewKernelError(
                    "leased result patch does not expose exact add-file bytes")
            content.append(line[1:])
        return ("\n".join(content) + "\n").encode("utf-8")
    raise ReviewKernelError(
        "leased result must use Write or an exact add-file apply_patch")


def record_slot_write_observation(ws: str, *, event: dict, contract: dict,
                                  task_slot: str | None = None) -> dict:
    """Record the trusted hook's approval of one leased result-path write.

    The result still carries a human-readable ``authored_by`` field, but that
    field has no authority. Collection requires this separate hook receipt,
    bound to the active contract, slot, result path, and host session/turn.
    """
    tool_name = str(event.get("tool_name") or event.get("tool") or "")
    tool_input = event.get("tool_input") or {}
    paths = tp.write_paths(tool_name, tool_input)
    if len(paths) != 1:
        raise ReviewKernelError("leased result must use one screenable host write")
    raw = paths[0]
    absolute = os.path.realpath(raw if os.path.isabs(raw) else os.path.join(ws, raw))
    index = _load_index(ws)
    match = None
    for run_id in sorted(index["runs"]):
        state = tp.load_json(_state_path(ws, run_id), default=None,
                             what="review kernel run state")
        if not isinstance(state, dict) or state.get("status") not in {
                "ready", "prepared"}:
            continue
        for slot in state.get("slots") or []:
            wanted = os.path.realpath(os.path.join(ws, slot["result_path"]))
            if absolute == wanted:
                if match is not None:
                    raise ReviewKernelError("leased result path is not unique")
                match = (state, slot)
    if match is None:
        raise ReviewKernelError("write is not a leased review result path")
    state, slot = match
    expected = slot["producer_contract"]
    if not contract.get("read_only") or contract.get("task") != expected["task"]:
        raise ReviewKernelError("leased result write lacks its producer contract")
    if list(contract.get("write_allow") or []) != expected["write_allow"]:
        raise ReviewKernelError("leased result producer write allowance mismatches")
    if str(task_slot or "") != expected["task_slot"]:
        raise ReviewKernelError("leased result write uses the wrong contract slot")
    producer_host, producer_session, producer_child_id = \
        _hook_child_identity(event)
    lease = __import__("review_evidence").ArtifactStore(ws).read(slot["lease"])
    result_bytes = _result_bytes_from_write_event(
        tool_name, tool_input, slot["result_path"])
    assignment = tp.load_json(
        _producer_assignment_path(ws, lease["lease_fingerprint"]),
        default=None, what="slot producer assignment")
    assignment_expected = {
        "run_id": state["run_id"],
        "lease_fingerprint": lease["lease_fingerprint"],
        "slot_id": lease["slot_id"], "result_path": slot["result_path"],
        "contract_task": expected["task"],
        "contract_task_slot": expected["task_slot"],
        "producer_host": producer_host, "producer_session": producer_session,
        "producer_child_id": producer_child_id,
    }
    if not isinstance(assignment, dict):
        # Real host order is SubagentStart under the parent contract, then
        # child activation, then Write. Bind the already-observed child now.
        assignment = register_slot_producer(
            ws, event=event, contract=contract, task_slot=task_slot,
            _observe_lifecycle=False)
    if not isinstance(assignment, dict) or assignment.get("schema") != \
            "taskplane.slot-producer-assignment/v1" or any(
                assignment.get(key) != value
                for key, value in assignment_expected.items()):
        raise ReviewKernelError(
            "leased result write is not from its dispatched child")
    assignment_fingerprint = hashlib.sha256(json.dumps(
        assignment, sort_keys=True, separators=(",", ":"))
        .encode("utf-8")).hexdigest()
    receipt = {
        "schema": "taskplane.slot-write-observation/v3",
        "run_id": state["run_id"],
        "lease_fingerprint": lease["lease_fingerprint"],
        "slot_id": lease["slot_id"], "result_path": slot["result_path"],
        "contract_task": expected["task"],
        "contract_task_slot": expected["task_slot"],
        "producer_session": producer_session,
        "producer_host": producer_host,
        "producer_child_id": producer_child_id,
        "producer_assignment_fingerprint": assignment_fingerprint,
        "result_sha256": hashlib.sha256(result_bytes).hexdigest(),
        "result_bytes": len(result_bytes),
        "host_event": "PreToolUse", "tool": tool_name,
    }
    path = _receipt_path(ws, lease["lease_fingerprint"])
    prior = tp.load_json(path, default=None, what="slot write observation")
    if prior is not None and prior != receipt:
        raise ReviewKernelError("leased result already observed from another producer")
    tp.atomic_write_json(path, receipt, sort_keys=True)
    return receipt


def _codex_session_receipt(ws: str, store, slot: dict, lease: dict,
                           raw_result: bytes) -> dict | None:
    """Recover host provenance from Codex's native, read-only task store.

    Repo hooks remain the preferred immediate receipt.  Codex also persists a
    host-authored child record outside the model's writable checkout.  A child
    that names the exact leased path and digest in its final answer therefore
    gives collection an equivalent byte-bound receipt when a hook transport is
    unavailable.  Parent thread + hashed task name + model/effort + result
    bytes are all matched; a prose claim or merely existing child is not.
    """
    if tp.host() != "codex":
        return None
    parent_thread = str(os.environ.get("CODEX_THREAD_ID") or "").strip()
    if not parent_thread:
        return None
    brief = store.read(slot["brief"])
    role = brief.get("role") or {}
    task_name = str(role.get("task_name") or "").strip()
    if not task_name:
        return None
    expected_path = tp.norm(slot["result_path"])
    expected_digest = hashlib.sha256(raw_result).hexdigest()
    home = os.path.realpath(os.environ.get("CODEX_HOME") or
                            os.path.join(os.path.expanduser("~"), ".codex"))
    sessions = os.path.join(home, "sessions")
    paths = []
    for directory, _dirs, names in os.walk(sessions):
        paths.extend(os.path.join(directory, name) for name in names
                     if name.startswith("rollout-") and name.endswith(".jsonl"))
    observed = None
    for path in sorted(paths, reverse=True)[:512]:
        spawn = None
        child_id = None
        model = None
        effort = None
        final_messages = []
        try:
            with open(path, encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    if len(line) > 2 * 1024 * 1024:
                        continue
                    try:
                        event = json.loads(line)
                    except (TypeError, ValueError):
                        continue
                    payload = event.get("payload") or {}
                    if event.get("type") == "session_meta" and spawn is None:
                        source = payload.get("source") or {}
                        candidate = (((source.get("subagent") or {})
                                      .get("thread_spawn"))
                                     if isinstance(source, dict) else None)
                        if isinstance(candidate, dict):
                            spawn = candidate
                            child_id = str(payload.get("id") or "")
                    elif event.get("type") == "turn_context" and model is None:
                        model = payload.get("model")
                        effort = (payload.get("effort") or
                                  payload.get("reasoning_effort"))
                    elif event.get("type") == "event_msg" and \
                            payload.get("type") == "task_complete":
                        final_messages.append(str(
                            payload.get("last_agent_message") or ""))
        except OSError:
            continue
        if not spawn or spawn.get("parent_thread_id") != parent_thread or \
                os.path.basename(str(spawn.get("agent_path") or "")) != task_name:
            continue
        if role.get("model") not in (None, model) or \
                role.get("reasoning_effort") not in (None, effort):
            continue
        path_line = "taskplane-result-path:" + expected_path
        digest_line = "taskplane-result-sha256:" + expected_digest
        if not any(path_line in {part.strip() for part in message.splitlines()}
                   and digest_line in {part.strip() for part in message.splitlines()}
                   for message in final_messages):
            continue
        observed = {"child_id": child_id, "model": model, "effort": effort}
        break
    if not observed or not observed["child_id"]:
        return None
    expected = slot["producer_contract"]
    assignment = {
        "schema": "taskplane.slot-producer-assignment/v1",
        "run_id": slot.get("run_id"),
        "lease_fingerprint": lease["lease_fingerprint"],
        "slot_id": lease["slot_id"], "result_path": slot["result_path"],
        "contract_task": expected["task"],
        "contract_task_slot": expected["task_slot"],
        "producer_host": "codex", "producer_session": parent_thread,
        "producer_child_id": observed["child_id"],
    }
    assignment_path = _producer_assignment_path(
        ws, lease["lease_fingerprint"])
    prior_assignment = tp.load_json(
        assignment_path, default=None, what="slot producer assignment")
    if prior_assignment is not None and prior_assignment != assignment:
        return None
    if prior_assignment is None:
        tp.atomic_write_json(assignment_path, assignment, sort_keys=True)
    assignment_fingerprint = hashlib.sha256(json.dumps(
        assignment, sort_keys=True, separators=(",", ":"))
        .encode("utf-8")).hexdigest()
    receipt = {
        "schema": "taskplane.slot-write-observation/v3",
        **{key: assignment[key] for key in (
            "run_id", "lease_fingerprint", "slot_id", "result_path",
            "contract_task", "contract_task_slot", "producer_session",
            "producer_host", "producer_child_id")},
        "producer_assignment_fingerprint": assignment_fingerprint,
        "result_sha256": expected_digest, "result_bytes": len(raw_result),
        "host_event": "CodexTaskComplete",
        "tool": "native-session-result-receipt",
    }
    receipt_path = _receipt_path(ws, lease["lease_fingerprint"])
    prior_receipt = tp.load_json(
        receipt_path, default=None, what="slot write observation")
    if prior_receipt is not None and prior_receipt != receipt:
        return None
    if prior_receipt is None:
        tp.atomic_write_json(receipt_path, receipt, sort_keys=True)
    return receipt


def _validate_finding(row: dict, lens_ids: list[str]) -> dict:
    required = ("severity", "class", "file", "line", "title", "scenario", "fix")
    if not isinstance(row, dict) or any(key not in row for key in required):
        raise __import__("review_evidence").ProvenanceError(
            "finding schema is missing required fields")
    if row.get("severity") not in {
            "blocker", "major", "minor", "question", "praise",
            "high", "med", "low", "info"}:
        raise __import__("review_evidence").ProvenanceError(
            "finding schema has invalid severity")
    if row.get("class") not in {"regression", "pre-existing", "observation"}:
        raise __import__("review_evidence").ProvenanceError(
            "finding schema has invalid class")
    if isinstance(row.get("line"), bool) or not isinstance(row.get("line"), int) \
            or row["line"] < 1:
        raise __import__("review_evidence").ProvenanceError(
            "finding schema has invalid line")
    for key in ("file", "title", "scenario", "fix"):
        if not isinstance(row.get(key), str) or not row[key].strip():
            raise __import__("review_evidence").ProvenanceError(
                f"finding schema has invalid {key}")
    row = dict(row)
    if row.get("lens") is None:
        if len(lens_ids) != 1:
            raise __import__("review_evidence").ProvenanceError(
                "finding schema must identify its lens in a multi-lens slot")
        row["lens"] = lens_ids[0]
    if row.get("lens") not in lens_ids:
        raise __import__("review_evidence").ProvenanceError(
            "finding schema cites a lens outside its slot")
    return row


def blocking_findings_by_lens(findings: Iterable[dict]) -> dict[str, int]:
    """Derive gate authority through the canonical class-aware policy.

    Lens producers use multiple severity vocabularies.  The loop policy is
    authoritative: every regression blocks regardless of severity, while
    pre-existing findings and observations remain visible but non-blocking.
    Late binding preserves that single policy seam without duplicating it.
    """
    import loop as loop_engine

    counts: dict[str, int] = {}
    for finding in findings or []:
        if not isinstance(finding, dict) or not loop_engine.finding_blocks(finding):
            continue
        lens_id = str(finding.get("lens") or "").strip()
        if lens_id:
            counts[lens_id] = counts.get(lens_id, 0) + 1
    return counts


def _read_slot_output(ws: str, store, slot: dict) -> tuple[dict, list[dict]]:
    import review_evidence as evidence
    path = os.path.join(ws, slot["result_path"])
    try:
        with open(path, "rb") as stream:
            raw_result = stream.read()
    except OSError:
        raw_result = b""
    row = tp.load_json(path, default=None, what="leased lens result")
    if not isinstance(row, dict):
        raise evidence.ProvenanceError("missing slot result: " + slot["slot_id"])
    lease = store.read(slot["lease"])
    for field in ("lease_fingerprint", "slot_id", "lens_ids",
                  "target_fingerprint", "context_fingerprint",
                  "view_fingerprint", "canonical_revision"):
        if row.get(field) != lease.get(field):
            raise evidence.ProvenanceError(
                f"slot result {field} does not match lease")
    if row.get("schema") != RESULT_SCHEMA or row.get("authored_by") != RESULT_AUTHOR:
        raise evidence.ProvenanceError("slot result violates canonical result schema")
    lens_rows = row.get("lens_results")
    if not isinstance(lens_rows, list):
        raise evidence.ProvenanceError("slot result lens_results must be a list")
    by_lens = {}
    for verdict in lens_rows:
        if not isinstance(verdict, dict) or set(("lens", "verdict", "blockers")) \
                - set(verdict):
            raise evidence.ProvenanceError("slot result lens verdict schema is invalid")
        lens_id = str(verdict.get("lens") or "")
        blockers = verdict.get("blockers")
        if lens_id in by_lens or lens_id not in lease["lens_ids"] or \
                verdict.get("verdict") not in {"pass", "fail"} or \
                isinstance(blockers, bool) or not isinstance(blockers, int) or \
                blockers < 0:
            raise evidence.ProvenanceError("slot result lens verdict is invalid")
        by_lens[lens_id] = {"lens": lens_id,
                            "verdict": verdict["verdict"],
                            "blockers": blockers}
    if set(by_lens) != set(lease["lens_ids"]):
        raise evidence.ProvenanceError("slot result does not cover its leased lenses")
    findings = row.get("findings")
    if not isinstance(findings, list):
        raise evidence.ProvenanceError("finding schema must be a list")
    findings = [_validate_finding(item, lease["lens_ids"]) for item in findings]
    blocking = blocking_findings_by_lens(findings)
    for lens_id in lease["lens_ids"]:
        expected_blockers = blocking.get(lens_id, 0)
        expected_verdict = "fail" if expected_blockers else "pass"
        summary = by_lens[lens_id]
        if summary["blockers"] != expected_blockers or \
                summary["verdict"] != expected_verdict:
            raise evidence.ProvenanceError(
                "blocking finding contradicts lens verdict summary: " + lens_id)
    receipt = tp.load_json(_receipt_path(ws, lease["lease_fingerprint"]),
                           default=None, what="slot write observation")
    if not isinstance(receipt, dict):
        receipt = _codex_session_receipt(ws, store, slot, lease, raw_result)
    expected_receipt = {
        "run_id": slot.get("run_id"),
        "lease_fingerprint": lease["lease_fingerprint"],
        "slot_id": lease["slot_id"], "result_path": slot["result_path"],
        "contract_task": slot["producer_contract"]["task"],
        "contract_task_slot": slot["producer_contract"]["task_slot"],
    }
    if not isinstance(receipt, dict) or receipt.get("schema") != \
            "taskplane.slot-write-observation/v3" or any(
                receipt.get(key) != value for key, value in expected_receipt.items()):
        raise evidence.ProvenanceError(
            "slot result has no matching hook-observed or Codex-session "
            "producer receipt")
    if not str(receipt.get("producer_session") or ""):
        raise evidence.ProvenanceError("slot result producer session is missing")
    if receipt.get("producer_host") not in {"claude", "codex"}:
        raise evidence.ProvenanceError("slot result producer host is missing")
    if not str(receipt.get("producer_child_id") or "") or not str(
            receipt.get("producer_assignment_fingerprint") or ""):
        raise evidence.ProvenanceError("slot result producer child is missing")
    if receipt.get("result_sha256") != hashlib.sha256(raw_result).hexdigest() \
            or receipt.get("result_bytes") != len(raw_result):
        raise evidence.ProvenanceError(
            "slot result does not match exact observed bytes")
    assignment = tp.load_json(
        _producer_assignment_path(ws, lease["lease_fingerprint"]),
        default=None, what="slot producer assignment")
    if not isinstance(assignment, dict):
        raise evidence.ProvenanceError(
            "slot result producer assignment is missing")
    assignment_fingerprint = hashlib.sha256(json.dumps(
        assignment, sort_keys=True, separators=(",", ":"))
        .encode("utf-8")).hexdigest()
    if assignment_fingerprint != receipt["producer_assignment_fingerprint"] or \
            any(receipt.get(key) != assignment.get(key) for key in (
                "run_id", "lease_fingerprint", "slot_id", "result_path",
                "contract_task", "contract_task_slot", "producer_host",
                "producer_session", "producer_child_id")):
        raise evidence.ProvenanceError(
            "slot result receipt does not match its dispatched child")
    ref = evidence.write_slot_result(
        store, slot["lease"], authored_slot=row["slot_id"],
        lens_ids=row["lens_ids"], findings=findings,
        authored_by=row["authored_by"])
    return ref, [by_lens[lid] for lid in sorted(by_lens)]


def _revision_record(store, envelope_ref: dict, collected: dict) -> tuple[dict, dict | None]:
    import copy
    import review_evidence as evidence
    envelope = store.read(envelope_ref)
    prior = evidence._read_current(store)
    revision_number = int((prior or {}).get("canonical_revision", 0)) + 1
    if collected.get("canonical_revision") != revision_number:
        raise evidence.RevisionError("slot results cite a stale or future revision")
    if collected.get("target_fingerprint") != envelope["target_fingerprint"] or \
            collected.get("context_fingerprint") != envelope["context_fingerprint"]:
        raise evidence.RevisionError("slot results contradict envelope identity")
    material = {
        "result_fingerprints": collected.get("result_fingerprints") or [],
        "findings": [finding for result in (collected.get("results") or [])
                     for finding in (result.get("findings") or [])],
    }
    record = {
        "schema": "taskplane.findings-revision/v1",
        "target_fingerprint": envelope["target_fingerprint"],
        "context_fingerprint": envelope["context_fingerprint"],
        "findings_fingerprint": evidence.content_fingerprint(material),
        "canonical_revision": revision_number,
        "result_fingerprints": list(material["result_fingerprints"]),
        "findings": copy.deepcopy(material["findings"]),
        "supersedes_revision": revision_number - 1 if revision_number > 1 else None,
    }
    return dict(record, artifact=store.put("findings-revision", record)), prior


def _preflight_projections(store, revision: dict, refs: list[dict]) -> None:
    import review_evidence as evidence
    expected = evidence.revision_identity(revision)
    seen = set()
    for ref in refs:
        payload = store.read(ref)
        kind = payload.get("kind")
        if kind in seen or payload.get("identity") != expected:
            raise evidence.RevisionError("projection set is stale or contradictory")
        seen.add(kind)
    if seen != {"findings", "report", "dashboard", "gate"}:
        raise evidence.RevisionError("projection set is incomplete")


def _collection_lock_path(ws: str) -> str:
    return os.path.join(ws, ".em-review", "kernel-v2",
                        "revision-reservation.json")


def _assert_collection_reservation(ws: str, run_id: str) -> None:
    """Only one prepared canonical revision may own publication authority."""
    for candidate in sorted(_load_index(ws)["runs"]):
        if candidate == run_id:
            continue
        row = tp.load_json(_state_path(ws, candidate), default=None,
                           what="review kernel run state")
        if isinstance(row, dict) and row.get("status") in {
                "prepared", "staged", "publishing", "committed"}:
            raise __import__("review_evidence").RevisionError(
                "another canonical revision owns the publication reservation")


def _atomic_write_bytes(path: str, data: bytes) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            if os.path.exists(temporary):
                os.unlink(temporary)
        except OSError:
            pass


def _publication_transaction(ws: str, run_id: str) -> dict:
    root = os.path.join(ws, ".em-review", "kernel-v2", "publications", run_id)
    return {
        "root": root,
        "prior_findings": os.path.join(root, "prior-findings.json"),
        "prior_report": os.path.join(root, "prior-report.md"),
    }


def _snapshot_publication(ws: str, state: dict) -> dict:
    """Persist the exact prior aliases before any authoritative mutation."""
    transaction = _publication_transaction(ws, state["run_id"])
    os.makedirs(transaction["root"], exist_ok=True)
    findings = os.path.join(ws, ".em-review", "findings.json")
    report = os.path.join(ws, ".em-review", "report.md")
    prior = {}
    for name, source, backup in (
            ("findings", findings, transaction["prior_findings"]),
            ("report", report, transaction["prior_report"])):
        exists = os.path.isfile(source)
        prior[name] = exists
        if exists:
            with open(source, "rb") as stream:
                _atomic_write_bytes(backup, stream.read())
    return {"schema": "taskplane.review-publication-transaction/v1",
            "prior": prior}


def _restore_pointer(store, evidence, identity: dict,
                     prior: dict | None) -> None:
    """Compare-and-restore a pointer advanced by this transaction only."""
    path = evidence._current_path(store)
    with tp.file_lock(path):
        current = evidence._read_current_file(store)
        if current == prior:
            return
        if current != identity:
            raise evidence.RevisionError(
                "cannot recover publication after concurrent pointer change")
        if prior is None:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        else:
            _atomic_write_bytes(path, evidence.canonical_bytes(prior))


def _restore_publication(ws: str, state: dict, store, evidence) -> None:
    """Roll back aliases and pointer from a durable publication snapshot."""
    transaction = state.get("publication_transaction") or {}
    prior_files = transaction.get("prior") or {}
    paths = _publication_transaction(ws, state["run_id"])
    aliases = {
        "findings": os.path.join(ws, ".em-review", "findings.json"),
        "report": os.path.join(ws, ".em-review", "report.md"),
    }
    backups = {"findings": paths["prior_findings"],
               "report": paths["prior_report"]}
    for name in ("findings", "report"):
        target = aliases[name]
        if prior_files.get(name):
            try:
                with open(backups[name], "rb") as stream:
                    prior_bytes = stream.read()
            except OSError as exc:
                raise evidence.RevisionError(
                    f"publication recovery snapshot is missing: {exc}") from None
            _atomic_write_bytes(target, prior_bytes)
        else:
            try:
                os.unlink(target)
            except FileNotFoundError:
                pass
    _restore_pointer(store, evidence,
                     evidence.revision_identity(state["revision"]),
                     state.get("prior_identity"))


def _resume_collection(ws: str, state: dict, store) -> dict:
    """Prepare immutable projections, then publish as one recoverable unit."""
    import review_evidence as evidence

    revision = state["revision"]
    identity = evidence.revision_identity(revision)
    prior = state.get("prior_identity")
    if state.get("status") in {"publishing", "committed"}:
        # A persisted in-flight phase means a prior process stopped between
        # pointer/alias writes. Restore the durable prior snapshot before
        # trying the idempotent staged transaction again.
        _restore_publication(ws, state, store, evidence)
        state = dict(state, status="staged")
        _save_state(ws, state)
    current = evidence._read_current(store)
    if current != prior:
        raise evidence.RevisionError(
            "canonical revision changed while collection was prepared")
    if state.get("status") == "prepared":
        body = state["publication_body"]
        markdown = state["report_markdown"]
        report_ref = store.put(
            "report-body", {"identity": identity, "markdown": markdown})
        projections = [
            evidence.create_projection(
                store, revision, kind="findings", body=revision["artifact"]),
            evidence.create_projection(
                store, revision, kind="report", body=report_ref),
            evidence.create_projection(
                store, revision, kind="dashboard",
                body={"source": revision["artifact"]}),
            evidence.create_projection(
                store, revision, kind="gate", body={"ready": True}),
        ]
        _preflight_projections(store, revision, projections)
        transaction = _snapshot_publication(ws, state)
        counters = dict(state.get("counters") or {})
        counters["artifact_render_bytes"] = (
            len(json.dumps(body, indent=1, sort_keys=True).encode("utf-8"))
            + len(markdown.encode("utf-8")))
        staged_manifest = dict(
            state["manifest"], counters=counters,
            report=_portable_ref(report_ref),
            projections=[_portable_ref(ref) for ref in projections])
        state = dict(
            state, status="staged", projections=projections,
            report_ref=report_ref, publication_transaction=transaction,
            manifest=_manifest(staged_manifest), counters=counters)
        _save_state(ws, state)
    if state.get("status") == "staged":
        state = dict(state, status="publishing")
        _save_state(ws, state)
        findings_path = os.path.join(ws, ".em-review", "findings.json")
        report_path = os.path.join(ws, ".em-review", "report.md")
        try:
            evidence._advance_current(store, identity, expected_current=prior)
            state = dict(state, status="committed")
            _save_state(ws, state)
            tp.atomic_write_json(
                findings_path, state["publication_body"], sort_keys=True)
            _atomic_write_bytes(
                report_path, state["report_markdown"].encode("utf-8"))
            published = None
            if state.get("publish_requested"):
                import views
                published = views.publish_report(ws)
                if not published:
                    raise ReviewKernelError(
                        "review artifact publication failed")
            manifest = dict(state["manifest"])
            manifest["published"] = (
                {"root": tp.to_posix(published["root"]),
                 "withheld": published.get("withheld") or []}
                if published else None)
            manifest = _manifest(manifest)
            state = dict(state, status="complete", manifest=manifest,
                         counters=manifest["counters"])
            _save_state(ws, state)
        except BaseException:
            _restore_publication(ws, state, store, evidence)
            state = dict(state, status="staged")
            _save_state(ws, state)
            raise
    if state.get("status") != "complete":
        raise ReviewKernelError(
            f"review collection cannot resume from {state.get('status')}")
    tp.trace(ws, "review_kernel_collected", stage=state.get("stage"),
             **identity)
    return state["manifest"]


def collect_review(ws: str, *, result_refs: Iterable[dict] | None = None,
                   publish: bool = True, run_id: str | None = None) -> dict:
    """Validate slot-authored results and publish one canonical revision."""
    import review_evidence as evidence

    selected = _load_state(ws, run_id)
    with tp.file_lock(_collection_lock_path(ws)):
        state = _load_state(ws, selected["run_id"])
        store = evidence.ArtifactStore(ws)
        if state.get("status") == "complete":
            if evidence._read_current(store) != evidence.revision_identity(
                    state.get("revision") or {}):
                raise evidence.RevisionError(
                    "completed review no longer matches canonical current revision")
            return state["manifest"]
        _assert_collection_reservation(ws, state["run_id"])
        if state.get("status") in {
                "prepared", "staged", "publishing", "committed"}:
            return _resume_collection(ws, state, store)
        if state.get("status") != "ready":
            raise ReviewKernelError(
                f"review cannot collect from {state.get('status')}")
        if list(result_refs or []):
            raise evidence.ProvenanceError(
                "direct result references cannot establish hook-observed authorship")
        refs, lens_results = [], []
        for slot in state.get("slots") or []:
            ref, rows = _read_slot_output(ws, store, slot)
            refs.append(ref)
            lens_results.extend(rows)
        leases = [row["lease"] for row in state.get("slots") or []]
        if leases:
            collected = evidence.collect_slot_results(store, leases, refs)
        else:
            envelope = store.read(state["envelope"])
            prior = evidence._read_current(store)
            collected = {
                "status": "complete", "slot_ids": [],
                "result_fingerprints": [], "results": [],
                "target_fingerprint": envelope["target_fingerprint"],
                "context_fingerprint": envelope["context_fingerprint"],
                "canonical_revision": int(
                    (prior or {}).get("canonical_revision", 0)) + 1,
            }
        revision, prior = _revision_record(
            store, state["envelope"], collected)
        decision = store.read(state["routing_decision"])["dispositions"]
        identity = evidence.revision_identity(revision)
        body = {"meta": {**identity, "lens_coverage": decision,
                         "target": identity["target_fingerprint"]},
                "findings": revision["findings"]}
        lines = ["# Engineering review", "",
                 f"Canonical revision: {identity['canonical_revision']}",
                 f"Context: `{identity['context_fingerprint']}`", "",
                 f"Findings: {len(revision['findings'])}", ""]
        markdown = "\n".join(lines)
        counters = dict(state.get("counters") or {})
        counters["top_level_cli_count"] = int(
            counters.get("top_level_cli_count", 1)) + 1
        counters["artifact_render_bytes"] = (
            len(evidence.canonical_bytes(body)) + len(markdown.encode("utf-8")))
        manifest = _manifest({
            "schema": "taskplane.review-collect-manifest/v2",
            "status": "complete", "run_id": state["run_id"], **identity,
            "findings": _portable_ref(revision["artifact"]),
            "report": None, "projections": [],
            "published": None, "counters": counters,
        })
        prepared = dict(
            state, status="prepared", revision=revision,
            projections=[], manifest=manifest,
            counters=manifest["counters"], lens_results=lens_results,
            prior_identity=prior, publication_body=body,
            report_markdown=markdown, publish_requested=bool(publish))
        # This durable reservation precedes every authoritative projection.
        _save_state(ws, prepared)
        return _resume_collection(ws, prepared, store)


def context_dir(ws: str) -> str:
    return os.path.join(ws, CONTEXT_DIR)


def _record(ws: str, paths: dict, status: str) -> None:
    """Record WHAT this review put on disk, as the engine saw it.

    An evaluation rubric asserts an exact substring match of a context path
    inside a dispatched brief; the comparand is therefore the LITERAL string
    `write_context` returned and `context_note` embeds, never a path rebuilt
    from the module constants — a rebuild is equal today and free to drift
    tomorrow, and the assertion silently becomes unprovable rather than
    failing. Each digest is read BACK off the disk for the same reason: the
    fact being recorded is "these bytes are there for the lens agents", and
    only a re-read can attest to that.

    `status` is what keeps a refusal from being read as a write. Rubric
    items score on row existence and ordering, so an empty `paths` list is
    not enough on its own: a session whose workspace refused the directory
    stored NOTHING and must say so in a field, not by omission.
      written — at least one file landed;
      refused — the workspace would not take the context directory;
      empty   — the directory is there and no file landed.
    """
    sha = {}
    for rel in paths.values():
        try:
            with open(os.path.join(ws, rel), "rb") as f:
                sha[rel] = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            sha[rel] = None
    tp.trace(ws, "review_context_written", status=status,
             paths=list(paths.values()), sha256=sha)


def write_context(ws: str, *, diff: str = "", impact: dict | None = None,
                  blast_radius: str = "") -> dict:
    """Write the shared review context ONCE. Returns the paths written, or
    an empty dict if the workspace will not take them — in which case the
    caller keeps embedding, because a missing file must degrade to the old
    behaviour rather than to a brief with no context at all."""
    d = context_dir(ws)
    out = {}
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        _record(ws, out, "refused")
        return out
    for name, body in ((DIFF_NAME, diff),
                       (BRIEF_NAME, blast_radius),
                       (IMPACT_NAME, json.dumps(impact, indent=2,
                                                sort_keys=True)
                        if impact else "")):
        if not body:
            continue
        p = os.path.join(d, name)
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write(body)
            # These paths cross the host boundary inside immutable briefs.
            # Keep filesystem construction host-native, but emit portable
            # POSIX references so Claude/Codex payload bytes match on Windows.
            out[name] = tp.to_posix(os.path.join(CONTEXT_DIR, name))
        except OSError:
            continue
    _record(ws, out, "written" if out else "empty")
    return out


def context_note(paths: dict) -> str:
    """What a brief says INSTEAD of carrying the payload.

    Deliberately explicit that the files are already there: an agent told
    only "the diff is available" will re-derive it with `git diff`, which is
    the cost this exists to remove."""
    if not paths:
        return ""
    lines = ["\nSHARED REVIEW CONTEXT — already on disk, read it, do NOT "
             "re-derive it:"]
    if DIFF_NAME in paths:
        lines.append(f"  {paths[DIFF_NAME]}  — the full diff under review "
                     f"(do not run `git diff` again)")
    if BRIEF_NAME in paths:
        lines.append(f"  {paths[BRIEF_NAME]}  — blast radius from the "
                     f"dependency graph (do not re-run `graph impact`)")
    if IMPACT_NAME in paths:
        lines.append(f"  {paths[IMPACT_NAME]}  — the impact payload as JSON")
    lines.append("  Every lens agent in this wave reads the SAME files. "
                 "They were written once, before dispatch.")
    return "\n".join(lines) + "\n"
