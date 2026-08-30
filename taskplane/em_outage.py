"""Narrow final-EM producer-receipt outage authority.

The normal producer-observation path remains the only way to submit and gate
an engineering review.  This module describes the one exceptional envelope
that may be consumed by the authenticated, slot-less loop control plane when
the review bytes and every product/mechanical check are valid but the host
receipt is unavailable.

It deliberately has no loop-state or CLI dependencies.  ``loop.py`` owns the
state lock, lifecycle transition, and human attribution; this module owns
regular-file reads, the closed identity, deterministic fingerprints, and the
immutable resolution receipt.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping


OUTAGE_SCHEMA = "taskplane.em-producer-receipt-outage/v1"
RESOLUTION_SCHEMA = "taskplane.em-producer-receipt-outage-resolution/v1"
CONTROL_PLANE_SCHEMA = "taskplane.em-outage-control-plane/v1"
REASON_CODE = "producer_receipt_unavailable"
DOMAIN = "taskplane.final-em.producer-receipt-outage/v1"


class EmOutageError(ValueError):
    """The candidate cannot be represented as the narrow EM-only outage."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _closed_text(value: object, field: str, *, maximum: int = 4096) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum or "\x00" in text:
        raise EmOutageError(f"{field} is missing or invalid")
    return text


def read_regular_bytes(path: str) -> bytes:
    """Read one exact regular, non-symlink file without following a swap.

    ``O_NOFOLLOW`` closes the final-component symlink race where supported.
    The descriptor and path are then compared after the complete read, so a
    replacement during hashing is refused rather than silently fingerprinted.
    """

    requested = os.path.abspath(os.fspath(path))
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(requested, flags)
    except OSError as exc:
        raise EmOutageError(f"output is not a readable regular file: {path}") \
            from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise EmOutageError(f"output is not a regular file: {path}")
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        try:
            path_after = os.lstat(requested)
        except OSError as exc:
            raise EmOutageError(f"output changed while hashing: {path}") \
                from exc
        stable = (
            stat.S_ISREG(path_after.st_mode)
            and not stat.S_ISLNK(path_after.st_mode)
            and (before.st_dev, before.st_ino, before.st_mode,
                 before.st_size, before.st_mtime_ns)
            == (after.st_dev, after.st_ino, after.st_mode,
                after.st_size, after.st_mtime_ns)
            == (path_after.st_dev, path_after.st_ino, path_after.st_mode,
                path_after.st_size, path_after.st_mtime_ns)
        )
        if not stable:
            raise EmOutageError(f"output changed while hashing: {path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def output_hashes(findings_path: str, report_path: str) -> dict:
    findings = read_regular_bytes(findings_path)
    report = read_regular_bytes(report_path)
    if not findings or not report:
        raise EmOutageError("EM outputs must be non-empty")
    return {
        "findings_sha256": _sha256(findings),
        "findings_bytes": len(findings),
        "report_sha256": _sha256(report),
        "report_bytes": len(report),
    }


_IDENTITY_FIELDS = frozenset({
    "schema", "domain", "reason_code", "repository", "store",
    "worktree", "stage", "task", "run_id", "slot", "expected_worker",
    "output_contract_fingerprint", "producer_dispatch_fingerprint",
    "integration_revision", "outputs", "review_kernel_fingerprint",
    "accepted_drift", "fingerprint",
})


def outage_identity(*, repository: Mapping[str, object], store: str,
                    worktree: str, run_id: str, slot: str,
                    expected_worker: str,
                    output_contract_fingerprint: str,
                    producer_dispatch_fingerprint: str,
                    integration_revision: str,
                    outputs: Mapping[str, object],
                    review_kernel: Mapping[str, object],
                    task: str = "engineering-signoff",
                    accepted_drift: str = "D-0014") -> dict:
    """Build the engine-derived, domain-separated outage identity."""

    repository_value = {
        str(key): str(value) for key, value in sorted(repository.items())
        if value not in (None, "")
    }
    if not repository_value:
        raise EmOutageError("repository identity is missing")
    output_value = dict(outputs)
    required_outputs = {
        "findings_sha256", "findings_bytes", "report_sha256", "report_bytes"
    }
    if set(output_value) != required_outputs:
        raise EmOutageError("output hash identity is not closed")
    for field in ("findings_sha256", "report_sha256"):
        value = _closed_text(output_value[field], f"outputs.{field}")
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise EmOutageError(f"outputs.{field} is invalid")
    for field in ("findings_bytes", "report_bytes"):
        value = output_value[field]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise EmOutageError(f"outputs.{field} is invalid")
    kernel = dict(review_kernel)
    if not kernel:
        raise EmOutageError("ReviewKernel identity is missing")
    material = {
        "schema": OUTAGE_SCHEMA,
        "domain": DOMAIN,
        "reason_code": REASON_CODE,
        "repository": repository_value,
        "store": _closed_text(store, "store"),
        "worktree": _closed_text(worktree, "worktree"),
        "stage": "em",
        "task": _closed_text(task, "task"),
        "run_id": _closed_text(run_id, "run_id"),
        "slot": _closed_text(slot, "slot"),
        "expected_worker": _closed_text(expected_worker, "expected_worker"),
        "output_contract_fingerprint": _closed_text(
            output_contract_fingerprint, "output_contract_fingerprint"),
        "producer_dispatch_fingerprint": _closed_text(
            producer_dispatch_fingerprint, "producer_dispatch_fingerprint"),
        "integration_revision": _closed_text(
            integration_revision, "integration_revision"),
        "outputs": output_value,
        "review_kernel_fingerprint": _sha256(_canonical_bytes(kernel)),
        "accepted_drift": _closed_text(accepted_drift, "accepted_drift"),
    }
    material["fingerprint"] = _sha256(_canonical_bytes(material))
    return validate_outage_identity(material)


def validate_outage_identity(value: Mapping[str, object]) -> dict:
    if not isinstance(value, Mapping) or set(value) != _IDENTITY_FIELDS:
        raise EmOutageError("EM outage identity fields are not closed")
    checked = dict(value)
    supplied = _closed_text(checked.pop("fingerprint", None), "fingerprint")
    if checked.get("schema") != OUTAGE_SCHEMA or checked.get("domain") != DOMAIN:
        raise EmOutageError("EM outage identity schema/domain is invalid")
    if checked.get("reason_code") != REASON_CODE or checked.get("stage") != "em":
        raise EmOutageError("EM outage identity reason/stage is invalid")
    expected = _sha256(_canonical_bytes(checked))
    if supplied != expected:
        raise EmOutageError("EM outage identity fingerprint mismatch")
    checked["fingerprint"] = supplied
    return checked


def resolution_receipt(identity: Mapping[str, object], *, actor: str,
                       control_plane: Mapping[str, object]) -> dict:
    """Mint the immutable one-use audit value persisted with sign-off."""

    outage = validate_outage_identity(identity)
    authority = dict(control_plane)
    if authority.get("schema") != CONTROL_PLANE_SCHEMA:
        raise EmOutageError("control-plane identity is invalid")
    material = {
        "schema": RESOLUTION_SCHEMA,
        "outage_fingerprint": outage["fingerprint"],
        "reason_code": REASON_CODE,
        "actor": _closed_text(actor, "actor", maximum=512),
        "control_plane": authority,
        "integration_revision": outage["integration_revision"],
        "outputs": dict(outage["outputs"]),
        "output_contract_fingerprint": outage[
            "output_contract_fingerprint"],
        "producer_dispatch_fingerprint": outage[
            "producer_dispatch_fingerprint"],
        "review_kernel_fingerprint": outage["review_kernel_fingerprint"],
        "accepted_drift": outage["accepted_drift"],
        "consumed": True,
    }
    material["fingerprint"] = _sha256(_canonical_bytes(material))
    return material
