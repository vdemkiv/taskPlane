"""Stateless shelf front door for one approved Design Contract element."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from collections.abc import MutableSequence

import build_c
import design_contract


_SHA = re.compile(r"[0-9a-f]{40,64}\Z")


class PickupRefusal(RuntimeError):
    """Pickup refused at a named pre-execution trust boundary."""


def _git(checkout: str, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=checkout, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8",
        errors="replace", check=False,
    )
    if completed.returncode:
        raise PickupRefusal(
            "checkout-clean: repository identity could not be verified"
        )
    return (completed.stdout or "").strip()


def _authority_path(checkout: str, relative_path: str) -> tuple[str, str]:
    if not relative_path or os.path.isabs(relative_path):
        raise PickupRefusal("checkout-clean: authority path must be relative")
    root = Path(checkout).resolve()
    candidate = root.joinpath(relative_path)
    try:
        mode = candidate.lstat().st_mode
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise PickupRefusal("checkout-clean: authority file is missing") from exc
    if not stat.S_ISREG(mode) or resolved != candidate.absolute() or \
            os.path.commonpath((str(root), str(resolved))) != str(root):
        raise PickupRefusal(
            "checkout-clean: authority must be a repository regular file"
        )
    rel = resolved.relative_to(root).as_posix()
    _git(str(root), "ls-files", "--error-unmatch", "--", rel)
    return str(resolved), rel


def _verify_clean(checkout: str) -> None:
    dirty = _git(checkout, "status", "--porcelain=v1", "--untracked-files=all")
    if dirty:
        raise PickupRefusal("checkout-clean: checkout has dirty or untracked bytes")


def _verify_source_lineage(checkout: str, authority_rel: str,
                           source_sha: str) -> None:
    head = _git(checkout, "rev-parse", "HEAD")
    if not _SHA.fullmatch(source_sha):
        raise PickupRefusal("source-sha: authority source SHA is invalid")
    if head == source_sha:
        return
    _git(checkout, "merge-base", "--is-ancestor", source_sha, head)
    changed = set(filter(None, _git(
        checkout, "diff", "--name-only", source_sha, head, "--"
    ).splitlines()))
    if changed != {authority_rel}:
        raise PickupRefusal(
            "source-sha: checkout contains changes beyond shelf authority"
        )


def _micro_plan(authority: dict) -> dict:
    element = authority.get("element")
    if not isinstance(element, dict) or set(element) != {
            "id", "scope", "acceptance"}:
        raise PickupRefusal("micro-plan: selected element is invalid")
    element_id = str(element.get("id") or "").strip()
    scope = element.get("scope")
    acceptance = element.get("acceptance")
    if not element_id or not isinstance(scope, list) or not scope or \
            not all(isinstance(item, str) and item for item in scope) or \
            not isinstance(acceptance, list) or len(acceptance) != 1:
        raise PickupRefusal("micro-plan: exactly one bounded criterion is required")
    criterion = acceptance[0]
    if not isinstance(criterion, dict) or set(criterion) != {"id", "proof"}:
        raise PickupRefusal("micro-plan: criterion fields are invalid")
    proof = criterion.get("proof")
    if not isinstance(proof, dict) or set(proof) != {"path", "argv"}:
        raise PickupRefusal("micro-plan: focused proof is invalid")
    material = {
        "element_id": element_id, "scope": list(scope),
        "criterion": dict(criterion),
    }
    fingerprint = hashlib.sha256(json.dumps(
        material, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    return {**material, "fingerprint": fingerprint}


def run(checkout: str, design_path: str, *,
        trace: MutableSequence[str] | None = None) -> dict:
    """Execute one approved shelf criterion without orchestration state."""
    events = trace if trace is not None else []
    root = os.path.realpath(checkout)
    authority_path, authority_rel = _authority_path(root, design_path)
    _verify_clean(root)
    try:
        authority = design_contract.load_approved_contract_for_pickup(
            root, authority_path
        )
    except design_contract.PickupAuthorityError as exc:
        boundary = ("engine-receipt" if "engine receipt" in str(exc).lower()
                    else "approved-design")
        raise PickupRefusal(f"{boundary}: {exc}") from exc
    events.append("pickup.preflight.authority")
    _verify_source_lineage(root, authority_rel, authority["source_sha"])
    events.append("pickup.preflight.checkout")
    micro_plan = _micro_plan(authority)
    events.append("pickup.micro_plan.ready")
    try:
        result = build_c.run_pickup(root, micro_plan, emit=events.append)
    except (build_c.ScopeAssignmentError,
            build_c.IntegrationAuthorizationError) as exc:
        raise PickupRefusal(f"pickup-build-c: {exc}") from exc
    events.append("pickup.storage.audit")
    return {
        "schema": "taskplane.pickup-result/v1", "status": "integrated",
        **result, "trace": list(events),
        "storage_audit": {
            "run": 0, "track": 0, "claim": 0, "lease": 0, "wave": 0,
            "equivalent": 0,
        },
    }
