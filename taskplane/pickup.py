"""Stateless shelf front door for one approved Design Contract element."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
from collections.abc import Mapping, MutableSequence
from dataclasses import dataclass

import build_c
import design_contract


_SHA = re.compile(r"[0-9a-f]{40,64}\Z")
_OPERATOR_SOURCE_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_PATH_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_RECEIPT_SCHEMA_V1 = "taskplane.pickup-receipt/v1"
_RECEIPT_SCHEMA_V2 = "taskplane.pickup-receipt/v2"
_RECEIPT_PRODUCER = "taskplane.pickup/v1"
_RECEIPT_FIELDS_V1 = frozenset({
    "schema", "producer", "authorized_source_sha", "design_fingerprint",
    "approval_digest", "engine_receipt_digest", "element_id",
    "micro_plan_fingerprint", "ordinal", "criterion_id",
    "predecessor_receipt_digest", "assigned_revision", "declared_scope",
    "checkpoint_receipt", "checkpoint_receipt_digest", "merge_receipt",
    "merge_outcome", "repository_tree_fingerprint", "terminal_status",
    "receipt_digest",
})
_RECEIPT_FIELDS_V2 = _RECEIPT_FIELDS_V1 | {"operator_trust"}
_OPERATOR_TRUST_SCHEMA = "taskplane.pickup-operator-trust/v1"
_OPERATOR_TRUST_FIELDS = frozenset({
    "schema", "authority_mode", "flag_name", "flag_value",
    "cryptographic_authenticity_claimed",
})
_OPERATOR_TRUST_MODE = "attributed-operator-trust"
_OPERATOR_TRUST_FLAG = "--trust-source"


class PickupRefusal(RuntimeError):
    """Pickup refused at a named pre-execution trust boundary."""


@dataclass(frozen=True, slots=True)
class _OperatorTrust:
    authority_mode: str
    flag_name: str
    flag_value: str
    cryptographic_authenticity_claimed: bool


def _parse_operator_trust(
        raw: str | Mapping[str, object] | None, *, boundary: str,
        expected_source_sha: str, required: bool) -> _OperatorTrust | None:
    if boundary not in {"cli", "receipt"} or not isinstance(required, bool):
        raise PickupRefusal("operator-trust: boundary is invalid")
    if boundary == "cli":
        if raw is None:
            if required:
                raise PickupRefusal(
                    "operator-trust: --trust-source is required"
                )
            return None
        if not isinstance(raw, str):
            raise PickupRefusal(
                "operator-trust: --trust-source is malformed"
            )
        flag_value = raw
    else:
        flag_value_raw = raw.get("flag_value") \
            if isinstance(raw, Mapping) else None
        if not isinstance(raw, Mapping) or \
                set(raw) != _OPERATOR_TRUST_FIELDS or \
                raw.get("schema") != _OPERATOR_TRUST_SCHEMA or \
                raw.get("authority_mode") != _OPERATOR_TRUST_MODE or \
                raw.get("flag_name") != _OPERATOR_TRUST_FLAG or \
                raw.get("cryptographic_authenticity_claimed") is not False or \
                not isinstance(flag_value_raw, str):
            raise PickupRefusal(
                "receipt-lineage: operator trust fields are invalid"
            )
        flag_value = flag_value_raw
    if not _OPERATOR_SOURCE_SHA.fullmatch(expected_source_sha):
        raise PickupRefusal(
            "operator-trust: shelf source SHA is malformed"
        )
    if not _OPERATOR_SOURCE_SHA.fullmatch(flag_value):
        raise PickupRefusal(
            "operator-trust: --trust-source is malformed"
        )
    if flag_value != expected_source_sha:
        raise PickupRefusal(
            "operator-trust: --trust-source does not match shelf source SHA"
        )
    return _OperatorTrust(
        authority_mode=_OPERATOR_TRUST_MODE,
        flag_name=_OPERATOR_TRUST_FLAG,
        flag_value=flag_value,
        cryptographic_authenticity_claimed=False,
    )


def _serialize_operator_trust(
        value: _OperatorTrust) -> dict[str, object]:
    return {
        "schema": _OPERATOR_TRUST_SCHEMA,
        "authority_mode": value.authority_mode,
        "flag_name": value.flag_name,
        "flag_value": value.flag_value,
        "cryptographic_authenticity_claimed":
            value.cryptographic_authenticity_claimed,
    }


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _receipt_digest(receipt: Mapping[str, object]) -> str:
    return _digest({
        name: value for name, value in receipt.items()
        if name != "receipt_digest"
    })


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


def _normalized_element(authority: Mapping[str, object]) -> dict:
    element = authority.get("element")
    if not isinstance(element, dict) or set(element) != {
            "id", "scope", "acceptance"}:
        raise PickupRefusal("micro-plan: selected element is invalid")
    element_id = str(element.get("id") or "").strip()
    scope = element.get("scope")
    acceptance = element.get("acceptance")
    if not _PATH_ID.fullmatch(element_id) or \
            not isinstance(scope, list) or not scope or \
            not all(isinstance(item, str) and item for item in scope) or \
            not isinstance(acceptance, list) or not acceptance:
        raise PickupRefusal("micro-plan: ordered criteria are invalid")
    normalized: list[dict] = []
    criterion_ids: set[str] = set()
    for criterion in acceptance:
        if not isinstance(criterion, dict) or set(criterion) != {
                "id", "proof"}:
            raise PickupRefusal("micro-plan: criterion fields are invalid")
        criterion_id = str(criterion.get("id") or "").strip()
        proof = criterion.get("proof")
        if not _PATH_ID.fullmatch(criterion_id) or \
                criterion_id in criterion_ids or \
                not isinstance(proof, dict) or set(proof) != {"path", "argv"} or \
                not isinstance(proof.get("path"), str) or \
                not str(proof["path"]).strip() or \
                not isinstance(proof.get("argv"), list) or \
                not proof["argv"] or not all(
                    isinstance(item, str) and item for item in proof["argv"]):
            raise PickupRefusal("micro-plan: focused proof is invalid")
        criterion_ids.add(criterion_id)
        normalized.append({
            "id": criterion_id,
            "proof": {
                "path": str(proof["path"]), "argv": list(proof["argv"]),
            },
        })
    return {
        "id": element_id, "scope": list(scope), "acceptance": normalized,
    }


def _micro_plan(authority: Mapping[str, object], ordinal: int) -> dict:
    element = _normalized_element(authority)
    acceptance = element["acceptance"]
    if ordinal < 1 or ordinal > len(acceptance):
        raise PickupRefusal("micro-plan: criterion ordinal is invalid")
    criterion = acceptance[ordinal - 1]
    material = {
        "element_id": element["id"], "scope": list(element["scope"]),
        "criterion": dict(criterion),
    }
    return {**material, "fingerprint": _digest(material)}


def _resume_authority(path: str) -> dict:
    """Load closed authority bytes whose authenticity a receipt will bind."""
    try:
        with open(path, encoding="utf-8") as source:
            value = json.load(source)
    except (OSError, ValueError) as exc:
        raise PickupRefusal(
            "approved-design: repository authority is invalid"
        ) from exc
    if not isinstance(value, dict) or set(value) != {
            "schema", "design", "approval", "engine_receipt"} or \
            value.get("schema") != design_contract.PICKUP_CONTRACT_SCHEMA:
        raise PickupRefusal("approved-design: repository authority is invalid")
    design = value.get("design")
    if not isinstance(design, dict) or set(design) != {
            "schema", "source_sha", "element"} or \
            design.get("schema") != design_contract.PICKUP_DESIGN_SCHEMA or \
            not _SHA.fullmatch(str(design.get("source_sha") or "")):
        raise PickupRefusal("approved-design: repository authority is invalid")
    fingerprint = _digest(design)
    approval = value.get("approval")
    engine_receipt = value.get("engine_receipt")
    if not isinstance(approval, dict) or set(approval) != {
            "schema", "actor", "design_fingerprint", "key_id", "signature"} or \
            approval.get("schema") != design_contract.PICKUP_APPROVAL_SCHEMA or \
            not str(approval.get("actor") or "").startswith("human:") or \
            approval.get("design_fingerprint") != fingerprint or \
            not _DIGEST.fullmatch(str(approval.get("key_id") or "")) or \
            not _DIGEST.fullmatch(str(approval.get("signature") or "")) or \
            not isinstance(engine_receipt, dict) or set(engine_receipt) != {
                "schema", "producer", "source_sha", "design_fingerprint",
                "key_id", "signature"} or \
            engine_receipt.get("schema") != \
                design_contract.PICKUP_ENGINE_RECEIPT_SCHEMA or \
            engine_receipt.get("producer") != \
                "taskplane.design-approval-engine/v1" or \
            engine_receipt.get("source_sha") != design["source_sha"] or \
            engine_receipt.get("design_fingerprint") != fingerprint or \
            engine_receipt.get("key_id") != approval["key_id"] or \
            not _DIGEST.fullmatch(str(engine_receipt.get("signature") or "")):
        raise PickupRefusal("approved-design: repository authority is invalid")
    authority = {
        "source_sha": design["source_sha"],
        "design_fingerprint": fingerprint,
        "element": design["element"],
        "approval": approval,
        "engine_receipt": engine_receipt,
    }
    _normalized_element(authority)
    return authority


def _receipt_directory(checkout: str,
                       authority: Mapping[str, object]) -> Path:
    element = _normalized_element(authority)
    return Path(checkout).joinpath(
        "exports", "pickup", str(authority["source_sha"]),
        str(authority["design_fingerprint"]), element["id"],
    )


def _receipt_relative_path(checkout: str, path: Path) -> str:
    return path.resolve().relative_to(Path(checkout).resolve()).as_posix()


def _changed_paths(checkout: str, older: str, newer: str) -> set[str]:
    _git(checkout, "merge-base", "--is-ancestor", older, newer)
    return set(filter(None, _git(
        checkout, "diff", "--name-only", older, newer, "--"
    ).splitlines()))


def _validate_receipt(checkout: str, authority: Mapping[str, object],
                      path: Path, receipt: object) -> dict[str, object]:
    if not isinstance(receipt, dict):
        raise PickupRefusal("receipt-lineage: receipt fields are invalid")
    schema = receipt.get("schema")
    if schema == _RECEIPT_SCHEMA_V1:
        if set(receipt) != _RECEIPT_FIELDS_V1:
            raise PickupRefusal("receipt-lineage: receipt fields are invalid")
    elif schema == _RECEIPT_SCHEMA_V2:
        if set(receipt) != _RECEIPT_FIELDS_V2:
            raise PickupRefusal("receipt-lineage: receipt fields are invalid")
        _parse_operator_trust(
            receipt.get("operator_trust"), boundary="receipt",
            expected_source_sha=str(authority["source_sha"]), required=True,
        )
    else:
        raise PickupRefusal("receipt-lineage: receipt schema is invalid")
    element = _normalized_element(authority)
    ordinal = receipt.get("ordinal")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or \
            ordinal < 1 or ordinal > len(element["acceptance"]):
        raise PickupRefusal("receipt-lineage: receipt ordinal is invalid")
    criterion_id = element["acceptance"][ordinal - 1]["id"]
    expected_digest = _receipt_digest(receipt)
    expected_name = \
        f"{ordinal}-{criterion_id}-{expected_digest}.json"
    if path.name != expected_name or \
            receipt.get("receipt_digest") != expected_digest:
        raise PickupRefusal("receipt-lineage: receipt digest/path mismatch")
    if receipt.get("producer") != _RECEIPT_PRODUCER or \
            receipt.get("authorized_source_sha") != authority["source_sha"] or \
            receipt.get("design_fingerprint") != \
                authority["design_fingerprint"] or \
            receipt.get("approval_digest") != _digest(authority["approval"]) or \
            receipt.get("engine_receipt_digest") != \
                _digest(authority["engine_receipt"]) or \
            receipt.get("element_id") != element["id"] or \
            receipt.get("criterion_id") != criterion_id or \
            receipt.get("terminal_status") != "green" or \
            receipt.get("merge_outcome") != "integrated":
        raise PickupRefusal("receipt-lineage: receipt identity is invalid")
    micro_plan = _micro_plan(authority, ordinal)
    assigned_revision = receipt.get("assigned_revision")
    checkpoint_receipt = receipt.get("checkpoint_receipt")
    checkpoint_digest = receipt.get("checkpoint_receipt_digest")
    if receipt.get("micro_plan_fingerprint") != micro_plan["fingerprint"] or \
            receipt.get("declared_scope") != micro_plan["scope"] or \
            not isinstance(assigned_revision, str) or \
            not _SHA.fullmatch(assigned_revision) or \
            not isinstance(checkpoint_receipt, Mapping) or \
            checkpoint_receipt.get("receipt_digest") != checkpoint_digest:
        raise PickupRefusal("receipt-lineage: receipt evidence is mixed")
    try:
        checked_checkpoint, checked_merge = build_c.validate_pickup_evidence(
            checkpoint_receipt, receipt.get("merge_receipt"),
            micro_plan=micro_plan, revision=assigned_revision,
        )
    except build_c.IntegrationAuthorizationError as exc:
        raise PickupRefusal(
            f"receipt-lineage: {exc}"
        ) from exc
    if checked_checkpoint["receipt_digest"] != checkpoint_digest or \
            checked_merge != receipt["merge_receipt"]:
        raise PickupRefusal("receipt-lineage: receipt evidence is mixed")
    tree = _git(checkout, "rev-parse", f"{assigned_revision}^{{tree}}")
    if receipt.get("repository_tree_fingerprint") != tree:
        raise PickupRefusal("receipt-lineage: repository tree is invalid")
    return {**receipt, "_path": _receipt_relative_path(checkout, path)}


def _load_receipts(checkout: str,
                   authority: Mapping[str, object]) -> list[dict[str, object]]:
    directory = _receipt_directory(checkout, authority)
    if not directory.exists():
        return []
    try:
        if directory.is_symlink() or not directory.is_dir() or \
                directory.resolve() != directory.absolute():
            raise PickupRefusal(
                "receipt-lineage: receipt directory is invalid")
        paths = sorted(directory.iterdir())
    except OSError as exc:
        raise PickupRefusal(
            "receipt-lineage: receipts could not be read"
        ) from exc
    receipts: list[dict[str, object]] = []
    for path in paths:
        try:
            if path.is_symlink() or not path.is_file():
                raise PickupRefusal(
                    "receipt-lineage: receipt path is invalid")
            _git(checkout, "ls-files", "--error-unmatch", "--",
                 _receipt_relative_path(checkout, path))
            with path.open(encoding="utf-8") as source:
                value = json.load(source)
        except PickupRefusal:
            raise
        except (OSError, ValueError) as exc:
            raise PickupRefusal(
                "receipt-lineage: receipt is invalid"
            ) from exc
        receipts.append(_validate_receipt(checkout, authority, path, value))
    receipts.sort(key=lambda value: int(value["ordinal"]))
    ordinals = [int(value["ordinal"]) for value in receipts]
    if ordinals != list(range(1, len(receipts) + 1)):
        raise PickupRefusal(
            "receipt-lineage: collision, fork, or gap detected")
    predecessor: str | None = None
    operator_lineage_started = False
    for receipt in receipts:
        if receipt["schema"] == _RECEIPT_SCHEMA_V2:
            operator_lineage_started = True
        elif operator_lineage_started:
            raise PickupRefusal(
                "receipt-lineage: v1 receipt follows v2 operator lineage"
            )
        if receipt.get("predecessor_receipt_digest") != predecessor:
            raise PickupRefusal(
                "receipt-lineage: predecessor chain is invalid")
        predecessor = str(receipt["receipt_digest"])
    return receipts


def _verify_receipt_history(checkout: str, authority_rel: str,
                            authority: Mapping[str, object],
                            receipts: list[dict[str, object]]) -> None:
    if not receipts:
        _verify_source_lineage(
            checkout, authority_rel, str(authority["source_sha"])
        )
        return
    first_revision = str(receipts[0]["assigned_revision"])
    if _changed_paths(
            checkout, str(authority["source_sha"]), first_revision
    ) != {authority_rel}:
        raise PickupRefusal(
            "source-sha: first pickup revision is not shelf authority only")
    for previous, current in zip(receipts, receipts[1:]):
        if _changed_paths(
                checkout, str(previous["assigned_revision"]),
                str(current["assigned_revision"])
        ) != {str(previous["_path"])}:
            raise PickupRefusal(
                "source-sha: receipt history contains unrelated changes")
    head = _git(checkout, "rev-parse", "HEAD")
    if _changed_paths(
            checkout, str(receipts[-1]["assigned_revision"]), head
    ) != {str(receipts[-1]["_path"])}:
        raise PickupRefusal(
            "source-sha: checkout history is not explained by receipts")


def _write_receipt(checkout: str, authority: Mapping[str, object],
                   micro_plan: Mapping[str, object], ordinal: int,
                   predecessor: str | None, result: Mapping[str, object],
                   operator_trust: _OperatorTrust | None) \
        -> dict[str, object]:
    checkpoint_receipt = result.get("checkpoint")
    integration = result.get("integration")
    merge_receipt = (integration.get("merge_receipt")
                     if isinstance(integration, Mapping) else None)
    revision = (integration.get("authorized_revision")
                if isinstance(integration, Mapping) else None)
    if not isinstance(revision, str):
        raise PickupRefusal("receipt-lineage: integration revision is missing")
    try:
        checked_checkpoint, checked_merge = build_c.validate_pickup_evidence(
            checkpoint_receipt, merge_receipt, micro_plan=micro_plan,
            revision=revision,
        )
    except build_c.IntegrationAuthorizationError as exc:
        raise PickupRefusal(f"receipt-lineage: {exc}") from exc
    receipt = {
        "schema": (_RECEIPT_SCHEMA_V2 if operator_trust is not None
                   else _RECEIPT_SCHEMA_V1),
        "producer": _RECEIPT_PRODUCER,
        "authorized_source_sha": authority["source_sha"],
        "design_fingerprint": authority["design_fingerprint"],
        "approval_digest": _digest(authority["approval"]),
        "engine_receipt_digest": _digest(authority["engine_receipt"]),
        "element_id": micro_plan["element_id"],
        "micro_plan_fingerprint": micro_plan["fingerprint"],
        "ordinal": ordinal,
        "criterion_id": micro_plan["criterion"]["id"],
        "predecessor_receipt_digest": predecessor,
        "assigned_revision": revision,
        "declared_scope": list(micro_plan["scope"]),
        "checkpoint_receipt": checked_checkpoint,
        "checkpoint_receipt_digest": checked_checkpoint["receipt_digest"],
        "merge_receipt": checked_merge,
        "merge_outcome": "integrated",
        "repository_tree_fingerprint": _git(
            checkout, "rev-parse", f"{revision}^{{tree}}"
        ),
        "terminal_status": "green",
    }
    if operator_trust is not None:
        receipt["operator_trust"] = _serialize_operator_trust(operator_trust)
    receipt["receipt_digest"] = _receipt_digest(receipt)
    directory = _receipt_directory(checkout, authority)
    root = Path(checkout).resolve()
    for parent in reversed(directory.parents):
        if parent == root:
            break
        if parent.exists() and parent.is_symlink():
            raise PickupRefusal("receipt-lineage: receipt path is symlinked")
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if directory.resolve() != directory.absolute() or \
                os.path.commonpath((str(root), str(directory.resolve()))) != \
                str(root):
            raise PickupRefusal("receipt-lineage: receipt path escapes checkout")
        path = directory / (
            f"{ordinal}-{micro_plan['criterion']['id']}-"
            f"{receipt['receipt_digest']}.json"
        )
        payload = json.dumps(
            receipt, indent=2, sort_keys=True, ensure_ascii=True
        ) + "\n"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=directory
        )
        temporary = Path(temporary_name)
        try:
            os.chmod(temporary, 0o644)
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                descriptor = -1
                target.write(payload)
                target.flush()
                os.fsync(target.fileno())
            os.link(temporary, path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
    except FileExistsError as exc:
        raise PickupRefusal(
            "receipt-lineage: receipt collision refused"
        ) from exc
    except PickupRefusal:
        raise
    except OSError as exc:
        raise PickupRefusal("receipt-lineage: receipt write failed") from exc
    return receipt


def run(checkout: str, design_path: str, *, trust_source: str | None = None,
        trace: MutableSequence[str] | None = None) -> dict[str, object]:
    """Execute one approved shelf criterion without orchestration state."""
    cli_entry = time.monotonic()
    events = trace if trace is not None else []
    checkpoint_started_after_seconds: float | None = None

    def emit(event: str) -> None:
        nonlocal checkpoint_started_after_seconds
        if event == "pickup.checkpoint.started" and \
                checkpoint_started_after_seconds is None:
            checkpoint_started_after_seconds = time.monotonic() - cli_entry
        events.append(event)

    root = os.path.realpath(checkout)
    authority_path, authority_rel = _authority_path(root, design_path)
    _verify_clean(root)
    authority_key = Path(root).joinpath(
        ".taskplane", "review-contract-authority.json"
    )
    resume_authority = trust_source is not None or not authority_key.is_file()
    if resume_authority:
        authority = _resume_authority(authority_path)
    else:
        try:
            authority = design_contract.load_approved_contract_for_pickup(
                root, authority_path
            )
        except design_contract.PickupAuthorityError as exc:
            boundary = ("engine-receipt" if "engine receipt" in str(exc).lower()
                        else "approved-design")
            raise PickupRefusal(f"{boundary}: {exc}") from exc
    operator_trust = _parse_operator_trust(
        trust_source, boundary="cli",
        expected_source_sha=str(authority["source_sha"]),
        required=resume_authority,
    )
    events.append("pickup.preflight.authority")
    receipts = _load_receipts(root, authority)
    if operator_trust is None and receipts and \
            receipts[-1]["schema"] == _RECEIPT_SCHEMA_V2:
        raise PickupRefusal(
            "receipt-lineage: v1 receipt follows v2 operator lineage"
        )
    _verify_receipt_history(root, authority_rel, authority, receipts)
    events.append("pickup.preflight.checkout")
    if receipts:
        events.append("pickup.receipt.lineage")
    element = _normalized_element(authority)
    next_ordinal = len(receipts) + 1
    if next_ordinal > len(element["acceptance"]):
        events.append("pickup.storage.audit")
        return {
            "schema": "taskplane.pickup-result/v1", "status": "complete",
            "receipt": {
                name: value for name, value in receipts[-1].items()
                if name != "_path"
            },
            "trace": list(events),
            "storage_audit": {
                "run": 0, "track": 0, "claim": 0, "lease": 0, "wave": 0,
                "equivalent": 0,
            },
        }
    micro_plan = _micro_plan(authority, next_ordinal)
    events.append("pickup.micro_plan.ready")
    if operator_trust is not None:
        events.append("pickup.operator_trust.accepted")
    build_c_entry = getattr(build_c, "run_pickup", None)
    if not callable(build_c_entry):
        raise PickupRefusal(
            "pickup-build-c: BUILD-C entry is unavailable"
        )
    try:
        result = build_c_entry(root, micro_plan, emit=emit)
    except (build_c.ScopeAssignmentError,
            build_c.IntegrationAuthorizationError) as exc:
        raise PickupRefusal(f"pickup-build-c: {exc}") from exc
    if checkpoint_started_after_seconds is None:
        raise PickupRefusal(
            "pickup-build-c: checkpoint start was not observed"
        )
    receipt = _write_receipt(
        root, authority, micro_plan, next_ordinal,
        str(receipts[-1]["receipt_digest"]) if receipts else None, result,
        operator_trust,
    )
    events.append("pickup.storage.audit")
    return {
        "schema": "taskplane.pickup-result/v1", "status": "integrated",
        **result, "receipt": receipt, "trace": list(events),
        "timing": {
            "pickup.cold_start.seconds": checkpoint_started_after_seconds,
        },
        "storage_audit": {
            "run": 0, "track": 0, "claim": 0, "lease": 0, "wave": 0,
            "equivalent": 0,
        },
    }
