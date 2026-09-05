"""The initial repository-only Requirement → Design boundary.

This adapter reads only explicitly selected, committed inputs. It neither
reconstructs predecessor runtime nor grants initial human authorization.
"""
from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable, Mapping
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING or __package__:
    from . import phase_handoff
else:  # pragma: no cover - direct CLI module loading
    import phase_handoff


def project_entry(material: Mapping[str, object], *, outcome: str,
                  durable_progress: Mapping[str, object],
                  receipt_evidence: object = None) -> dict[str, Any]:
    """Project only an explicitly authorized, new Requirement completion."""
    if outcome != "done" or dict(durable_progress) != {
            "phase": "requirement", "state": "terminal", "outcome": "done"}:
        raise ValueError("Requirement export requires terminal done progress")
    if receipt_evidence is not None:
        raise ValueError("Requirement export refuses receipt evidence")
    if (material.get("design") is not None or material.get("plan") is not None or
            material.get("tasks") != [] or material.get("progress_receipts") != [] or
            material.get("lineage") != {"predecessor_handoff_fingerprint": None,
                                        "predecessor_receipt_head": None}):
        raise ValueError("Requirement export cannot carry predecessor phase state")
    obligations = material.get("obligations")
    if not isinstance(obligations, list) or not obligations or any(
            not isinstance(row, Mapping) or not isinstance(row.get("id"), str)
            for row in obligations):
        raise ValueError("Requirement export obligations are invalid")
    # Requirement has no progress-receipt phase. Its obligations are inputs to
    # Design, not claims that Design or implementation has already succeeded.
    return phase_handoff.create_phase_handoff(
        **copy.deepcopy(dict(material)),
        producer={"phase": "requirement", "outcome": "done"},
        successor={"phase": "design", "mode": "next-phase"},
        progress={"completed": [], "remaining": [row["id"] for row in obligations]})


def _selected_json(root: str, reference: dict[str, Any]) -> dict[str, Any]:
    phase_handoff.validate_repository_artifact_reference(root, reference)
    if reference["media_type"] != "application/json":
        raise ValueError("Requirement entry requires selected JSON artifacts")
    _, raw = phase_handoff._safe_regular_file(
        root, reference["destination"], code="artifact-integrity")
    # The reference validator verifies worktree bytes and tracking; separately
    # pin the same bytes to HEAD rather than treating the index as a commit.
    committed = phase_handoff._git(
        root, "rev-parse", "HEAD:" + reference["destination"], code="artifact-integrity")
    observed = phase_handoff._git(
        root, "hash-object", "--", reference["destination"], code="artifact-integrity")
    if committed != observed:
        raise ValueError("selected Requirement entry artifact is not committed")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Requirement entry JSON artifact must be an object")
    return value


def _complete_requirement(value: dict[str, Any]) -> None:
    for field in ("id", "title"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ValueError("selected full requirement needs " + field)
    for field in ("functional", "acceptance", "open_questions", "depends_on", "context_files"):
        items = value.get(field)
        if not isinstance(items, list) or any(
                not isinstance(item, str) or not item.strip() for item in items):
            raise ValueError("selected full requirement needs explicit " + field + " list")
    if not isinstance(value.get("contracts"), list) or not isinstance(value.get("nfr"), dict):
        raise ValueError("selected full requirement needs contracts and nfr")
    if any(not isinstance(key, str) or not isinstance(item, str) or not item.strip()
           for key, item in value["nfr"].items()):
        raise ValueError("selected requirement NFR statements must be nonempty text")


def validate_entry(repository_root: str, handoff: dict[str, Any], *,
                   product_dor: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
    """Apply incumbent Product and Design-entry checks before any publication."""
    root = phase_handoff._repository_root(repository_root)
    head = phase_handoff._git(root, "rev-parse", "HEAD")
    tree = phase_handoff._git(root, "rev-parse", "HEAD^{tree}")
    if (handoff["repository"]["id"] != phase_handoff.repository_identity(root) or
            handoff["source"] != {"commit": head, "tree": tree}):
        raise ValueError("Requirement entry repository/source differs from current committed checkout")
    if phase_handoff._git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("Requirement entry requires a clean committed checkout")
    initial = handoff["authority_receipts"][0]
    if initial["source_commit"] != head or initial["source_tree"] != tree:
        raise ValueError("initial authorization is not for the exact Requirement entry source")
    selected = handoff["selected_artifacts"]
    refs = {row["kind"]: row for row in selected}
    if len(refs) != len(selected) or not {"requirement", "graph"} <= refs.keys():
        raise ValueError("Requirement entry needs exactly one requirement and baseline graph")
    if (refs["requirement"] != handoff["requirement"]["artifact"] or
            refs["requirement"]["digest"] != handoff["requirement"]["fingerprint"]):
        raise ValueError("Requirement entry identity is not the exact selected requirement")
    requirement = _selected_json(root, refs["requirement"])
    _complete_requirement(requirement)
    rid = handoff["requirement"]["id"]
    if (requirement["id"] != rid or requirement["acceptance"] !=
            [row["criterion"] for row in handoff["acceptance"]] or
            sorted(requirement["contracts"], key=phase_handoff.canonical_bytes) !=
            sorted(handoff["contracts"], key=phase_handoff.canonical_bytes)):
        raise ValueError("Requirement entry acceptance/contracts differ from selected requirement")
    if {item for row in handoff["obligations"] for item in row["acceptance"]} != {
            row["id"] for row in handoff["acceptance"]}:
        raise ValueError("Requirement entry obligations omit acceptance criteria")
    dependencies = requirement["depends_on"]
    if set(refs) != {"requirement", "graph", *("requirement-" + item for item in dependencies)}:
        raise ValueError("Requirement entry has missing dependency or unrelated selected artifacts")
    for dependency in dependencies:
        record = _selected_json(root, refs["requirement-" + dependency])
        _complete_requirement(record)
        if record["id"] != dependency:
            raise ValueError("selected requirement dependency does not exist: " + dependency)
    product = product_dor(requirement)
    if not product["passed"]:
        raise ValueError("Product Definition of Ready failed: " + "; ".join(product["errors"]))
    graph = _selected_json(root, refs["graph"])
    if (not isinstance(graph.get("modules"), dict) or not isinstance(graph.get("edges"), list) or
            not isinstance(graph.get("meta"), dict)):
        raise ValueError("Requirement entry requires a complete baseline graph snapshot")
    meta = graph["meta"]
    if not meta.get("content_fingerprint"):
        raise ValueError("baseline dependency graph is missing — run graph scan")
    scanned = meta.get("scanned_head")
    if scanned and scanned != head:
        if not isinstance(scanned, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", scanned):
            raise ValueError("baseline dependency graph is stale for the current HEAD")
        phase_handoff._git(root, "merge-base", "--is-ancestor", scanned, head, code="source-stale")
        changed = set(phase_handoff._git(
            root, "diff", "--name-only", scanned, head, "--", code="source-stale").splitlines())
        if not changed <= {row["destination"] for row in selected}:
            raise ValueError("baseline dependency graph is stale for the current HEAD")
    if (phase_handoff._git(root, "rev-parse", "HEAD") != head or
            phase_handoff._git(root, "status", "--porcelain=v1", "--untracked-files=all")):
        raise ValueError("Requirement entry source changed during validation")
