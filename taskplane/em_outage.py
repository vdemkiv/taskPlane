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

import base64
import binascii
import hashlib
import json
import os
import stat
from collections.abc import Mapping


OUTAGE_SCHEMA = "taskplane.em-producer-receipt-outage/v1"
RESOLUTION_SCHEMA = "taskplane.em-producer-receipt-outage-resolution/v1"
CONTROL_PLANE_SCHEMA = "taskplane.em-outage-control-plane/v1"
OUTPUT_SNAPSHOT_SCHEMA = "taskplane.em-output-snapshot/v1"
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
    """Capture bytes from one regular, non-symlink descriptor exactly once.

    Pathname currency ends at the no-follow ``open``.  From then on the one
    descriptor is the authority: both stability checks and all bytes come
    from that captured object.  Renaming a different object over the public
    path later cannot substitute or relabel the captured bytes.  Platforms
    without a no-follow open primitive fail closed because a pre-open
    ``lstat`` would merely introduce another pathname race.
    """

    requested = os.path.abspath(os.fspath(path))
    if not hasattr(os, "O_NOFOLLOW"):
        raise EmOutageError(
            "safe no-follow output capture is unavailable on this host")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
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
        stable = (
            (before.st_dev, before.st_ino, before.st_mode, before.st_size,
             before.st_mtime_ns, before.st_ctime_ns)
            == (after.st_dev, after.st_ino, after.st_mode, after.st_size,
                after.st_mtime_ns, after.st_ctime_ns)
            and sum(len(chunk) for chunk in chunks) == after.st_size
        )
        if not stable:
            raise EmOutageError(f"output changed while hashing: {path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


_SNAPSHOT_FIELDS = frozenset({"schema", "findings", "report", "fingerprint"})
_SNAPSHOT_ITEM_FIELDS = frozenset({
    "label", "sha256", "bytes", "content_base64",
})


def _snapshot_item(label: str, content: bytes) -> dict[str, object]:
    return {
        "label": label,
        "sha256": _sha256(content),
        "bytes": len(content),
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


def capture_output_snapshot(
        findings_path: str, report_path: str) -> dict[str, object]:
    """Capture the two logical EM outputs into one durable value.

    Each public name is opened exactly once.  The returned mapping contains
    immutable ``bytes`` encoded for the protected loop-state store plus a
    closed fingerprint.  Consumers validate and decode this value; they never
    re-open either public pathname.
    """

    material = {
        "schema": OUTPUT_SNAPSHOT_SCHEMA,
        "findings": _snapshot_item(
            "findings.json", read_regular_bytes(findings_path)),
        "report": _snapshot_item(
            "report.md", read_regular_bytes(report_path)),
    }
    material["fingerprint"] = _sha256(_canonical_bytes(material))
    return validate_output_snapshot(material)


def validate_output_snapshot(
        value: Mapping[str, object]) -> dict[str, object]:
    """Validate and return one closed, JSON-safe output snapshot."""

    if not isinstance(value, Mapping) or set(value) != _SNAPSHOT_FIELDS:
        raise EmOutageError("EM output snapshot fields are not closed")
    checked = dict(value)
    supplied = _closed_text(
        checked.pop("fingerprint", None), "output_snapshot.fingerprint")
    if checked.get("schema") != OUTPUT_SNAPSHOT_SCHEMA:
        raise EmOutageError("EM output snapshot schema is invalid")
    for field, label in (("findings", "findings.json"),
                         ("report", "report.md")):
        item = checked.get(field)
        if not isinstance(item, Mapping) or \
                set(item) != _SNAPSHOT_ITEM_FIELDS:
            raise EmOutageError(f"EM output snapshot {field} is invalid")
        item = dict(item)
        if item.get("label") != label:
            raise EmOutageError(
                f"EM output snapshot {field} label is invalid")
        try:
            content = base64.b64decode(
                str(item.get("content_base64") or ""), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise EmOutageError(
                f"EM output snapshot {field} bytes are invalid") from exc
        if isinstance(item.get("bytes"), bool) or \
                not isinstance(item.get("bytes"), int) or \
                item.get("bytes") != len(content) or \
                item.get("sha256") != _sha256(content):
            raise EmOutageError(
                f"EM output snapshot {field} identity is invalid")
        checked[field] = item
    expected = _sha256(_canonical_bytes(checked))
    if supplied != expected:
        raise EmOutageError("EM output snapshot fingerprint mismatch")
    checked["fingerprint"] = supplied
    return checked


def output_snapshot_bytes(value: Mapping[str, object]) -> dict[str, bytes]:
    """Decode exact bytes from a validated snapshot without path access."""

    snapshot = validate_output_snapshot(value)
    exact: dict[str, bytes] = {}
    for field in ("findings", "report"):
        item = snapshot.get(field)
        if not isinstance(item, Mapping):
            raise EmOutageError(f"EM output snapshot {field} is invalid")
        exact[field] = base64.b64decode(
            str(item.get("content_base64") or ""), validate=True)
    return exact


def output_snapshot_evidence(
        value: Mapping[str, object]) -> dict[str, object]:
    """Return the bounded snapshot identity carried into audit/sign-off."""

    snapshot = validate_output_snapshot(value)
    outputs: dict[str, object] = {}
    for field in ("findings", "report"):
        item = snapshot.get(field)
        if not isinstance(item, Mapping):
            raise EmOutageError(f"EM output snapshot {field} is invalid")
        outputs[field] = {
            key: item[key] for key in ("label", "sha256", "bytes")
        }
    return {
        "schema": OUTPUT_SNAPSHOT_SCHEMA,
        "fingerprint": snapshot["fingerprint"],
        "outputs": outputs,
    }


def output_hashes(findings_path: str | None = None,
                  report_path: str | None = None, *,
                  snapshot: Mapping[str, object] | None = None
                  ) -> dict[str, object]:
    if snapshot is None:
        if findings_path is None or report_path is None:
            raise EmOutageError("EM output paths are missing")
        snapshot = capture_output_snapshot(findings_path, report_path)
    elif findings_path is not None or report_path is not None:
        raise EmOutageError("EM output hashes require paths or one snapshot")
    exact = output_snapshot_bytes(snapshot)
    findings = exact["findings"]
    report = exact["report"]
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
    "output_snapshot_fingerprint", "accepted_drift", "fingerprint",
})


def outage_identity(*, repository: Mapping[str, object], store: str,
                    worktree: str, run_id: str, slot: str,
                    expected_worker: str,
                    output_contract_fingerprint: str,
                    producer_dispatch_fingerprint: str,
                    integration_revision: str,
                    outputs: Mapping[str, object],
                    output_snapshot_fingerprint: str,
                    review_kernel: Mapping[str, object],
                    task: str = "engineering-signoff",
                    accepted_drift: str = "D-0014") -> dict[str, object]:
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
        byte_count = output_value[field]
        if isinstance(byte_count, bool) or not isinstance(byte_count, int) or \
                byte_count <= 0:
            raise EmOutageError(f"outputs.{field} is invalid")
    kernel = dict(review_kernel)
    if not kernel:
        raise EmOutageError("ReviewKernel identity is missing")
    snapshot_fingerprint = _closed_text(
        output_snapshot_fingerprint, "output_snapshot_fingerprint")
    if len(snapshot_fingerprint) != 64 or any(
            character not in "0123456789abcdef"
            for character in snapshot_fingerprint):
        raise EmOutageError("output_snapshot_fingerprint is invalid")
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
        "output_snapshot_fingerprint": snapshot_fingerprint,
        "review_kernel_fingerprint": _sha256(_canonical_bytes(kernel)),
        "accepted_drift": _closed_text(accepted_drift, "accepted_drift"),
    }
    material["fingerprint"] = _sha256(_canonical_bytes(material))
    return validate_outage_identity(material)


def validate_outage_identity(
        value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _IDENTITY_FIELDS:
        raise EmOutageError("EM outage identity fields are not closed")
    checked = dict(value)
    supplied = _closed_text(checked.pop("fingerprint", None), "fingerprint")
    if checked.get("schema") != OUTAGE_SCHEMA or checked.get("domain") != DOMAIN:
        raise EmOutageError("EM outage identity schema/domain is invalid")
    if checked.get("reason_code") != REASON_CODE or checked.get("stage") != "em":
        raise EmOutageError("EM outage identity reason/stage is invalid")
    snapshot_fingerprint = _closed_text(
        checked.get("output_snapshot_fingerprint"),
        "output_snapshot_fingerprint")
    if len(snapshot_fingerprint) != 64 or any(
            character not in "0123456789abcdef"
            for character in snapshot_fingerprint):
        raise EmOutageError("output_snapshot_fingerprint is invalid")
    expected = _sha256(_canonical_bytes(checked))
    if supplied != expected:
        raise EmOutageError("EM outage identity fingerprint mismatch")
    checked["fingerprint"] = supplied
    return checked


def resolution_receipt(identity: Mapping[str, object], *, actor: str,
                       control_plane: Mapping[str, object]
                       ) -> dict[str, object]:
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
        "output_snapshot_fingerprint": outage[
            "output_snapshot_fingerprint"],
        "accepted_drift": outage["accepted_drift"],
        "consumed": True,
    }
    material["fingerprint"] = _sha256(_canonical_bytes(material))
    return material
