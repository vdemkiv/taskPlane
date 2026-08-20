"""Immutable review envelope, scoped views and result provenance (R-0005).

All large records are canonical JSON artifacts.  A reference carries the
semantic record fingerprint and the digest of the exact stored bytes; the
former binds cross-artifact identity while the latter detects mutation.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from typing import Any, Iterable, Iterator, TypeAlias, TypedDict

import storage as runtime_storage
import taskplane_lite as tp


MAX_SCOPED_VIEW_BYTES = 16 * 1024
MAX_INLINE_REQUIREMENTS_BYTES = 4 * 1024
_KIND = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_SLOT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
REVIEW_EVIDENCE_SECTIONS = frozenset({
    "diff", "impact", "graph_quality", "runnability", "requirements",
    "contracts", "change",
})
UNTRUSTED_REVIEW_SECTIONS: tuple[str, ...] = (
    "change", "diff", "requirements")
UNTRUSTED_DATA_BEGIN: str = "<taskplane-untrusted-review-data>"
UNTRUSTED_DATA_END: str = "</taskplane-untrusted-review-data>"

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class ReviewEvidenceReference(TypedDict, total=False):
    """Portable identity required to resolve one immutable review section."""

    schema: str
    section: str
    target_fingerprint: str
    context_fingerprint: str
    canonical_revision: int
    content_fingerprint: str
    digest: str
    bytes: int
    fingerprint: str
    artifact: dict[str, JsonValue]
    transport: str


class UntrustedReviewEvidenceFrame(TypedDict):
    """Fixed control/data boundary around PR-controlled evidence."""

    schema: str
    section: str
    interpretation: str
    begin: str
    content: JsonValue
    end: str
    flags: list[dict[str, JsonValue]]


class ArtifactIntegrityError(ValueError):
    pass


class ProvenanceError(ValueError):
    pass


class RevisionError(ValueError):
    pass


def _summary_repair_projection(result: dict) -> dict:
    """Remove only derived verdict/count fields from a producer result.

    The remaining projection includes findings, checked evidence, lease
    identity, and every producer-authored extension.  Equality of this
    projection is the mechanical proof that a summary repair did not rewrite
    review substance or provenance.
    """
    if not isinstance(result, dict):
        raise ProvenanceError("slot result must be an object")
    projected = copy.deepcopy(result)
    rows = projected.get("lens_results")
    if not isinstance(rows, list):
        raise ProvenanceError("slot result lens_results must be a list")
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ProvenanceError("slot result lens verdict is invalid")
        lens = str(row.get("lens") or "").strip()
        checked = row.get("checked_evidence")
        if not lens or lens in seen or not isinstance(checked, list):
            raise ProvenanceError("slot result lens verdict is invalid")
        seen.add(lens)
        row.pop("verdict", None)
        row.pop("blockers", None)
    if not isinstance(projected.get("findings"), list):
        raise ProvenanceError("finding schema must be a list")
    return projected


def assert_summary_only_repair(before: dict, after: dict) -> None:
    """Prove that two results differ only in derived lens summaries."""
    if _summary_repair_projection(before) != _summary_repair_projection(after):
        raise ProvenanceError("summary repair changes review substance or provenance")


def canonical_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def content_fingerprint(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def create_execution_binding(workspace: str, *, target: dict, run_id: str,
                             lens_ids, slot_id: str,
                             lease_fingerprint: str,
                             producer: str) -> dict:
    """Bind review evidence to its exact governed execution identity."""
    root = tp.review_execution_root_identity(workspace)
    target_row = {
        "fingerprint": str((target or {}).get("fingerprint") or "").strip(),
        "base": str((target or {}).get("merge_base") or
                    (target or {}).get("base") or "").strip(),
        "head": str((target or {}).get("head") or "").strip(),
    }
    identity = {
        "run_id": str(run_id or "").strip(),
        "lens_ids": _strings(lens_ids),
        "slot_id": str(slot_id or "").strip(),
        "lease_fingerprint": str(lease_fingerprint or "").strip(),
        "producer": str(producer or "").strip(),
    }
    if not target_row["fingerprint"] or not target_row["head"] or \
            any(not value for key, value in identity.items()
                if key != "lens_ids") or not identity["lens_ids"]:
        raise ProvenanceError("review execution binding is incomplete")
    material = {
        "schema": "taskplane.review-execution-binding/v1",
        "repository_id": root["repository_id"],
        "repository_kind": root["repository_kind"],
        "worktree_fingerprint": root["worktree_fingerprint"],
        "engine_fingerprint": root["engine_fingerprint"],
        "target": target_row,
        **identity,
    }
    return dict(material, binding_fingerprint=content_fingerprint(material))


def verify_execution_binding(workspace: str, binding: dict, *, target: dict,
                             run_id: str, lens_ids, slot_id: str,
                             lease_fingerprint: str,
                             producer: str) -> bool:
    """Recompute the complete execution binding; no partial match is valid."""
    try:
        expected = create_execution_binding(
            workspace, target=target, run_id=run_id, lens_ids=lens_ids,
            slot_id=slot_id, lease_fingerprint=lease_fingerprint,
            producer=producer)
    except Exception as exc:
        raise ProvenanceError(f"review execution binding is invalid: {exc}") \
            from None
    if not isinstance(binding, dict) or binding != expected:
        raise ProvenanceError("review execution binding does not match")
    return True


def require_approvable_collection(collected: dict) -> bool:
    """Prove exactly-once slot/result conservation for an approval candidate."""
    if not isinstance(collected, dict):
        raise ProvenanceError("review collection conservation is invalid")
    expected = list(collected.get("expected_slot_ids") or [])
    actual = list(collected.get("collected_slot_ids") or
                  collected.get("slot_ids") or [])
    results = list(collected.get("results") or [])
    result_slots = [str(row.get("slot_id") or "")
                    for row in results if isinstance(row, dict)]
    fingerprints = list(collected.get("result_fingerprints") or [])
    completeness = collected.get("completeness") or {}
    conserved = bool(expected) and len(set(expected)) == len(expected) and \
        len(set(actual)) == len(actual) and sorted(expected) == sorted(actual) and \
        sorted(result_slots) == sorted(actual) and \
        len(fingerprints) == len(actual) and \
        len(set(fingerprints)) == len(fingerprints) and \
        not list(collected.get("gaps") or []) and \
        completeness == {"expected": len(expected), "collected": len(actual),
                         "missing": 0, "complete": True}
    if not conserved:
        raise ProvenanceError("review collection conservation is incomplete")
    return True


def _strings(values) -> list[str]:
    return sorted({str(v).strip() for v in (values or []) if str(v).strip()})


class ArtifactStore:
    """Content-addressed, immutable artifact storage under the checkout."""

    def __init__(self, workspace: str, root: str | None = None):
        self.workspace = os.path.abspath(workspace)
        if root is None:
            locator = runtime_storage.load_workspace_locator(self.workspace)
            root = (os.path.join(locator["paths"]["artifacts"],
                                 "review-artifacts-v2")
                    if locator else os.path.join(
                        tp.tp_dir(self.workspace), "review-artifacts-v2"))
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)
        if os.path.islink(self.root):
            raise ArtifactIntegrityError("artifact store root is a symlink")
        self._root_real = os.path.realpath(self.root)

    @classmethod
    def from_reference(cls, workspace: str, reference: dict) -> "ArtifactStore":
        """Reopen a canonical store after its producer worktree was removed."""
        artifact = reference.get("artifact") \
            if isinstance(reference, dict) and isinstance(
                reference.get("artifact"), dict) else reference
        path = str((artifact or {}).get("path") or "")
        if not os.path.isabs(path):
            raise ArtifactIntegrityError(
                "retained artifact reference needs an absolute canonical path")
        real = os.path.realpath(path)
        root = os.path.dirname(os.path.dirname(real))
        if os.path.basename(root) != "review-artifacts-v2":
            raise ArtifactIntegrityError("retained artifact store shape is invalid")
        locator = runtime_storage.load_workspace_locator(workspace)
        authority_root = (os.path.realpath(locator["home"]) if locator else
                          os.path.realpath(workspace))
        if os.path.commonpath((authority_root, root)) != authority_root:
            raise ArtifactIntegrityError(
                "retained artifact lies outside canonical repository storage")
        return cls(workspace, root=root)

    def _validate_kind(self, kind: str) -> str:
        kind = str(kind or "")
        if not _KIND.fullmatch(kind):
            raise ArtifactIntegrityError("invalid artifact kind")
        return kind

    def _path(self, kind: str, fingerprint: str) -> str:
        kind = self._validate_kind(kind)
        if not re.fullmatch(r"[0-9a-f]{64}", str(fingerprint or "")):
            raise ArtifactIntegrityError("invalid artifact fingerprint")
        directory = os.path.join(self.root, kind)
        os.makedirs(directory, exist_ok=True)
        if os.path.islink(directory):
            raise ArtifactIntegrityError("artifact kind directory is a symlink")
        return os.path.join(directory, f"{fingerprint}.json")

    def put(self, kind: str, payload, *, fingerprint: str | None = None) -> dict:
        data = canonical_bytes(payload)
        digest = hashlib.sha256(data).hexdigest()
        identity = fingerprint or digest
        path = self._path(kind, identity)
        with tp.file_lock(path):
            if os.path.lexists(path):
                if os.path.islink(path):
                    raise ArtifactIntegrityError("artifact final path is a symlink")
                try:
                    with open(path, "rb") as existing:
                        prior = existing.read()
                except OSError as exc:
                    raise ArtifactIntegrityError(
                        f"cannot read existing artifact: {exc}")
                if prior != data:
                    raise ArtifactIntegrityError(
                        "content-address collision or altered immutable artifact")
                return self._reference(
                    kind, identity, digest, len(data), path)

            directory = os.path.dirname(path)
            fd, tmp = tempfile.mkstemp(
                prefix=f".{identity}.", suffix=".tmp", dir=directory)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                # The immutable name appears only after every byte is durable.
                # A crash before this replace leaves no partial final artifact;
                # a retry writes a fresh temp and recovers normally.
                os.replace(tmp, path)
            finally:
                try:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                except OSError:
                    pass
        return self._reference(kind, identity, digest, len(data), path)

    def _reference(self, kind, fingerprint, digest, size, path):
        return {
            "schema": "taskplane.artifact-reference/v1",
            "kind": kind,
            "fingerprint": fingerprint,
            "digest": digest,
            "bytes": int(size),
            "path": os.path.abspath(path),
            "relative_path": os.path.relpath(path, self.workspace).replace(os.sep, "/"),
            "transport": "artifact-reference",
        }

    def _validated_path(self, ref: dict) -> str:
        if not isinstance(ref, dict):
            raise ArtifactIntegrityError("artifact reference must be an object")
        kind = self._validate_kind(ref.get("kind"))
        expected = os.path.abspath(self._path(kind, ref.get("fingerprint")))
        supplied_path = ref.get("path")
        if not supplied_path and ref.get("relative_path"):
            relative = str(ref.get("relative_path") or "")
            supplied_path = os.path.join(self.workspace, *relative.split("/"))
        if not supplied_path:
            # Portable references deliberately carry no host-specific path.
            # Their kind/fingerprint tuple is the canonical local locator.
            supplied_path = expected
        supplied = os.path.abspath(str(supplied_path))
        if supplied != expected:
            raise ArtifactIntegrityError("artifact reference points outside canonical store path")
        if os.path.islink(supplied):
            raise ArtifactIntegrityError("artifact reference is a symlink")
        real = os.path.realpath(supplied)
        if os.path.commonpath([self._root_real, real]) != self._root_real:
            raise ArtifactIntegrityError("artifact reference points outside store")
        return supplied

    def verify(self, ref: dict) -> bool:
        path = self._validated_path(ref)
        try:
            with open(path, "rb") as stream:
                data = stream.read()
        except OSError as exc:
            raise ArtifactIntegrityError(f"artifact is missing: {exc}")
        digest = hashlib.sha256(data).hexdigest()
        if digest != ref.get("digest"):
            raise ArtifactIntegrityError("artifact digest mismatch")
        if len(data) != ref.get("bytes"):
            raise ArtifactIntegrityError("artifact byte length mismatch")
        return True


    def read(self, ref: dict):
        path = self._validated_path(ref)
        self.verify(ref)
        with open(path, "rb") as stream:
            try:
                return json.loads(stream.read().decode("utf-8"))
            except (UnicodeError, ValueError) as exc:
                raise ArtifactIntegrityError(f"artifact is not canonical JSON: {exc}")

    def references(self, kind: str) -> list[dict]:
        kind = self._validate_kind(kind)
        directory = os.path.join(self.root, kind)
        if not os.path.isdir(directory) or os.path.islink(directory):
            return []
        refs = []
        for name in sorted(os.listdir(directory)):
            if not re.fullmatch(r"[0-9a-f]{64}\.json", name):
                continue
            path = os.path.join(directory, name)
            if not os.path.isfile(path) or os.path.islink(path):
                continue
            with open(path, "rb") as stream:
                data = stream.read()
            refs.append(self._reference(
                kind, name[:-5], hashlib.sha256(data).hexdigest(),
                len(data), path))
        return refs


def retained_cleanup_evidence(primary_workspace: str, worker_workspace: str,
                              references: Iterable[dict], *,
                              lifecycle_released: bool) -> dict:
    """Prove evidence is canonical and independent of a removable worker."""
    worker = os.path.realpath(worker_workspace)
    verified = []
    reasons = []
    if not lifecycle_released:
        reasons.append("owning lifecycle has not released evidence retention")
    for reference in references or []:
        artifact = reference.get("artifact") \
            if isinstance(reference, dict) and isinstance(
                reference.get("artifact"), dict) else reference
        path = os.path.realpath(str((artifact or {}).get("path") or ""))
        try:
            if not path or os.path.commonpath((worker, path)) == worker:
                raise ArtifactIntegrityError(
                    "evidence remains inside the candidate worktree")
            store = ArtifactStore.from_reference(primary_workspace, reference)
            store.verify(artifact)
            verified.append({"path": path,
                             "fingerprint": artifact.get("fingerprint"),
                             "digest": artifact.get("digest")})
        except (ArtifactIntegrityError, OSError, ValueError) as exc:
            reasons.append(str(exc))
    return {
        "schema": "taskplane.worktree-evidence-retention/v1",
        "status": "released" if not reasons else "evidence-needed",
        "evidence_needed": bool(reasons), "reasons": reasons,
        "verified": verified,
    }


def retain_worktree_governance(primary_workspace: str, worker_workspace: str,
                               task_id: str) -> dict:
    """Make legacy worker evidence primary-owned before cleanup.

    Managed workers already write outside their checkout and are only
    inventoried. Legacy workers are copied byte-for-byte into ignored primary
    governance storage. No source byte is removed here.
    """
    primary = os.path.realpath(primary_workspace)
    worker = os.path.realpath(worker_workspace)
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", str(task_id)).strip("-.") \
        or "task"
    destination = os.path.join(tp.tp_dir(primary),
                               "retained-worktree-evidence", token)
    candidates = [
        runtime_storage.evaluation_root(worker),
        runtime_storage.review_public_root(worker),
        ArtifactStore(worker).root,
    ]
    retained = []

    def add_file(source: str, relative: str) -> None:
        if os.path.islink(source):
            raise ArtifactIntegrityError("worktree evidence contains a symlink")
        with open(source, "rb") as handle:
            data = handle.read()
        digest = hashlib.sha256(data).hexdigest()
        target = os.path.join(destination, *relative.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.exists(target):
            with open(target, "rb") as handle:
                if handle.read() != data:
                    raise ArtifactIntegrityError(
                        "retained evidence destination changed")
        else:
            fd, temporary = tempfile.mkstemp(
                prefix=".retain-", dir=os.path.dirname(target))
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        retained.append({"path": os.path.realpath(target), "digest": digest,
                         "bytes": len(data), "source": relative})

    seen = set()
    for root in candidates:
        real_root = os.path.realpath(root)
        if real_root in seen or not os.path.exists(real_root):
            continue
        seen.add(real_root)
        if os.path.commonpath((worker, real_root)) != worker:
            # Canonical managed storage survives directly; inventory files.
            for base, dirs, files in os.walk(real_root):
                dirs[:] = [name for name in dirs
                           if not os.path.islink(os.path.join(base, name))]
                for name in files:
                    path = os.path.join(base, name)
                    if os.path.islink(path):
                        continue
                    with open(path, "rb") as handle:
                        data = handle.read()
                    retained.append({
                        "path": os.path.realpath(path),
                        "digest": hashlib.sha256(data).hexdigest(),
                        "bytes": len(data), "source": "canonical"})
            continue
        label = os.path.basename(real_root) or "evidence"
        if os.path.isfile(real_root):
            add_file(real_root, label)
            continue
        for base, dirs, files in os.walk(real_root):
            dirs[:] = [name for name in dirs
                       if not os.path.islink(os.path.join(base, name))]
            for name in files:
                source = os.path.join(base, name)
                relative = os.path.join(
                    label, os.path.relpath(source, real_root)).replace(
                        os.sep, "/")
                add_file(source, relative)
    reasons = []
    verdict = runtime_storage.evaluation_path(worker)
    if not os.path.isfile(verdict) and not any(
            row.get("source", "").endswith("verdict.json") for row in retained):
        reasons.append("canonical evaluator verdict is unavailable")
    return {
        "schema": "taskplane.retained-worktree-governance/v1",
        "task_id": str(task_id),
        "status": "released" if not reasons else "evidence-needed",
        "evidence_needed": bool(reasons), "reasons": reasons,
        "records": sorted(retained, key=lambda row: row["path"]),
    }


def _target_fingerprint(target: dict) -> str:
    value = str((target or {}).get("fingerprint") or "").strip()
    if not value:
        raise ArtifactIntegrityError("envelope target fingerprint is required")
    return value


def create_envelope(store: ArtifactStore, *, target: dict, diff: dict,
                    impact: dict, graph_quality: dict, runnability: dict,
                    requirement: dict, acceptance, contracts,
                    change: dict | None = None) -> dict:
    """Write one immutable full envelope for a canonical target snapshot."""
    canonical_diff = copy.deepcopy(diff or {})
    diff_files = _strings(canonical_diff.get("files"))
    diff_symbols = _strings(canonical_diff.get("changed_symbols"))
    canonical_target = copy.deepcopy(target or {})
    if _strings(canonical_target.get("changed_files")) == diff_files:
        canonical_target.pop("changed_files", None)
    canonical_impact = copy.deepcopy(impact or {})
    canonical_graph_quality = copy.deepcopy(graph_quality or {})
    if canonical_graph_quality.get("impact") == canonical_impact:
        canonical_graph_quality.pop("impact", None)
    if _strings(canonical_graph_quality.get("changed_files")) == diff_files:
        canonical_graph_quality.pop("changed_files", None)
    if _strings(canonical_graph_quality.get("changed_symbols")) == diff_symbols:
        canonical_graph_quality.pop("changed_symbols", None)
    canonical_acceptance = copy.deepcopy(list(acceptance or []))
    canonical_requirement = copy.deepcopy(requirement or {})
    if canonical_requirement.get("acceptance") == canonical_acceptance:
        canonical_requirement.pop("acceptance", None)
    base = {
        "schema": "taskplane.review-envelope/v2",
        "target": canonical_target,
        "target_fingerprint": _target_fingerprint(target),
        "diff": canonical_diff,
        "impact": canonical_impact,
        "graph_quality": canonical_graph_quality,
        "runnability": copy.deepcopy(runnability or {}),
        "requirements": {
            "requirement": canonical_requirement,
            "acceptance": canonical_acceptance,
        },
        "contracts": _strings(contracts),
        "change": copy.deepcopy(change or {}),
        "derivations": {"diff": 1, "impact": 1, "graph_quality": 1,
                        "runnability": 1, "requirements": 1,
                        "contracts": 1},
    }
    context = content_fingerprint(base)
    payload = dict(base, context_fingerprint=context)
    return store.put("envelope", payload, fingerprint=context)


def _load_complete_envelope(store: ArtifactStore, envelope_ref: dict) -> dict:
    envelope = store.read(envelope_ref)
    if envelope.get("schema") != "taskplane.review-envelope/v2":
        raise ArtifactIntegrityError("not a review envelope")
    if envelope.get("context_fingerprint") != envelope_ref.get("fingerprint"):
        raise ArtifactIntegrityError("envelope context fingerprint mismatch")
    return envelope


def _envelope_section_reference(envelope_ref: dict, section: str,
                                value) -> dict:
    """Bind a compact JSON pointer to exact immutable envelope content."""
    return {
        "schema": "taskplane.envelope-section-reference/v1",
        "envelope_fingerprint": envelope_ref.get("fingerprint"),
        "envelope_digest": envelope_ref.get("digest"),
        "section": f"/{section}",
        "content_fingerprint": content_fingerprint(value),
        "bytes": len(canonical_bytes(value)),
        "transport": "envelope-reference",
    }


def read_envelope_section(store: ArtifactStore,
                          envelope_ref: dict[str, Any],
                          section_ref: dict[str, Any]) -> Any:
    """Resolve and verify a section reference without re-deriving facts."""
    if not isinstance(section_ref, dict) or section_ref.get("schema") != \
            "taskplane.envelope-section-reference/v1":
        raise ProvenanceError("invalid envelope section reference")
    if (section_ref.get("envelope_fingerprint")
            != envelope_ref.get("fingerprint")
            or section_ref.get("envelope_digest") != envelope_ref.get("digest")):
        raise ProvenanceError("section reference belongs to another envelope")
    pointer = str(section_ref.get("section") or "")
    if not re.fullmatch(r"/[a-z][a-z0-9_]*", pointer):
        raise ProvenanceError("invalid envelope section pointer")
    envelope = _load_complete_envelope(store, envelope_ref)
    name = pointer[1:]
    if name not in envelope:
        raise ProvenanceError("envelope section is missing")
    value = envelope[name]
    if (content_fingerprint(value) != section_ref.get("content_fingerprint")
            or len(canonical_bytes(value)) != section_ref.get("bytes")):
        raise ProvenanceError("envelope section provenance mismatch")
    return value


def _requirements_view(envelope_ref: dict, requirements: dict) -> dict:
    """Keep small requirements inline; reference large canonical records."""
    if len(canonical_bytes(requirements)) <= MAX_INLINE_REQUIREMENTS_BYTES:
        return copy.deepcopy(requirements)
    requirement = requirements.get("requirement") or {}
    identity = {key: copy.deepcopy(requirement[key])
                for key in ("id", "title", "status") if key in requirement}
    return {
        "reference": _envelope_section_reference(
            envelope_ref, "requirements", requirements),
        "requirement": identity,
        "acceptance_count": len(requirements.get("acceptance") or []),
    }


def _impact_view(envelope_ref: dict, impact: dict) -> dict:
    """Keep the blast radius complete without copying it into every slot."""
    if len(canonical_bytes(impact)) <= MAX_INLINE_REQUIREMENTS_BYTES:
        return copy.deepcopy(impact)
    touched = _strings(impact.get("touched"))
    summary = {
        "reference": _envelope_section_reference(
            envelope_ref, "impact", impact),
        "total_impacted": int(impact.get("total_impacted") or 0),
        "touched_count": len(touched),
        "unknown_count": len(impact.get("unknown") or []),
        "depth_limit": impact.get("depth_limit"),
        "truncated": bool(impact.get("truncated")),
    }
    if len(canonical_bytes(touched)) <= 2048:
        summary["touched"] = touched
    else:
        summary["touched_by_reference"] = True
    return summary


def _create_scoped_view_v2(store: ArtifactStore, envelope_ref: dict, *,
                           slot_id: str, lens_ids, relevant_files=None,
                           evidence=None) -> dict:
    """Derive the fitting legacy view while active v2 leases drain."""
    if not _SLOT.fullmatch(str(slot_id or "")):
        raise ProvenanceError("invalid slot id")
    envelope = _load_complete_envelope(store, envelope_ref)
    graph_quality = envelope.get("graph_quality") or {}
    if graph_quality.get("status") == "impact_incomplete" and not (
            graph_quality.get("review_fallback") or {}).get("mode") == \
            "immutable_diff":
        raise ProvenanceError("impact_incomplete creates zero scoped views")
    diff = envelope.get("diff") or {}
    wanted = set(_strings(relevant_files))
    files = _strings(diff.get("files"))
    if wanted:
        files = [path for path in files if path in wanted]
    symbols = _strings(diff.get("changed_symbols"))
    # The full canonical indexes remain available through the verified
    # envelope/diff artifact.  Copy small indexes into the prompt view for
    # convenience, but large reviews stay reference-first instead of paying
    # for the same file/symbol lists once per lens.
    diff_view = {
        "file_count": len(files),
        "changed_symbol_count": len(symbols),
    }
    if diff.get("artifact"):
        diff_view["artifact"] = copy.deepcopy(diff["artifact"])
    if len(canonical_bytes(files)) <= 4096 or not diff.get("artifact"):
        diff_view["files"] = files
    else:
        diff_view["files_by_reference"] = True
    if len(canonical_bytes(symbols)) <= 2048 or not diff.get("artifact"):
        diff_view["changed_symbols"] = symbols
    else:
        diff_view["changed_symbols_by_reference"] = True
    base = {
        "schema": "taskplane.scoped-review-view/v2",
        "context_fingerprint": envelope["context_fingerprint"],
        "envelope": copy.deepcopy(envelope_ref),
        "slot_id": slot_id,
        "lens_ids": _strings(lens_ids),
        "target": copy.deepcopy(envelope["target"]),
        "target_fingerprint": envelope["target_fingerprint"],
        "diff": diff_view,
        "impact": _impact_view(
            envelope_ref, envelope.get("impact") or {}),
        "graph_quality": copy.deepcopy(envelope.get("graph_quality") or {}),
        "runnability": copy.deepcopy(envelope.get("runnability") or {}),
        "requirements": _requirements_view(
            envelope_ref, envelope.get("requirements") or {}),
        "contracts": copy.deepcopy(envelope.get("contracts") or []),
        "change": copy.deepcopy(envelope.get("change") or {}),
        "evidence": copy.deepcopy(evidence or {}),
        "forbidden_derivations": ["git diff", "graph impact", "graph scan",
                                  "requirement lookup", "runnability probe"],
    }
    view_fp = content_fingerprint(base)
    payload = dict(base, view_fingerprint=view_fp)
    data = canonical_bytes(payload)
    if len(data) > MAX_SCOPED_VIEW_BYTES:
        raise ArtifactIntegrityError(
            f"scoped view exceeds {MAX_SCOPED_VIEW_BYTES} byte bound")
    return store.put("view", payload, fingerprint=view_fp)


def _evidence_reference(store: ArtifactStore, envelope: dict, *, section: str,
                        value, canonical_revision: int) -> dict:
    """Store one target/revision-bound governed copy of an overflow section."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", section):
        raise ProvenanceError("invalid evidence section")
    record = {
        "schema": "taskplane.review-evidence-section/v2",
        "section": section,
        "target_fingerprint": envelope["target_fingerprint"],
        "context_fingerprint": envelope["context_fingerprint"],
        "canonical_revision": int(canonical_revision),
        "content_fingerprint": content_fingerprint(value),
        # The immutable resolved path carries the same structural data-only
        # frame as inline producer evidence.  A consumer never receives raw
        # PR-controlled content at the control-text level.
        "content": frame_review_evidence(section, value),
    }
    artifact = store.put("review-section", record)
    return {
        "schema": "taskplane.review-evidence-reference/v2",
        "section": section,
        "target_fingerprint": envelope["target_fingerprint"],
        "context_fingerprint": envelope["context_fingerprint"],
        "canonical_revision": int(canonical_revision),
        "content_fingerprint": record["content_fingerprint"],
        "digest": artifact["digest"],
        "bytes": artifact["bytes"],
        "fingerprint": artifact["fingerprint"],
        "artifact": {
            key: artifact[key] for key in (
                "schema", "kind", "fingerprint", "digest", "bytes",
                "transport")
        },
        "transport": "artifact-reference",
    }


_LENS_RELEVANT_SECTIONS = {
    "architecture": {"diff", "impact", "graph_quality", "requirements",
                     "contracts", "change"},
    "security": {"diff", "impact", "runnability", "requirements",
                 "contracts", "change"},
    "performance": {"diff", "impact", "graph_quality", "runnability",
                    "change"},
    "scalability": {"diff", "impact", "graph_quality", "runnability",
                    "change"},
    "privacy-compliance": {"diff", "requirements", "contracts", "change"},
    "data-safety": {"diff", "impact", "requirements", "contracts", "change"},
}


def _relevant_sections(lens_ids) -> set[str]:
    """Return deterministic evidence candidates useful to the routed lenses."""
    relevant = {"diff", "requirements", "change"}
    for lens_id in _strings(lens_ids):
        relevant.update(_LENS_RELEVANT_SECTIONS.get(lens_id, ()))
    return relevant


def _section_summary(section: str, value) -> dict:
    """Produce a small deterministic summary without inventing review facts."""
    summary = {
        "section": section,
        "content_bytes": len(canonical_bytes(value)),
        "content_fingerprint": content_fingerprint(value),
    }
    if isinstance(value, dict):
        summary["keys"] = sorted(str(key) for key in value)[:16]
        for key in ("status", "total_impacted", "truncated"):
            if key in value and isinstance(value[key], (str, int, bool, type(None))):
                summary[key] = value[key]
    elif isinstance(value, list):
        summary["item_count"] = len(value)
    return summary


def _text_values(value: JsonValue) -> Iterator[str]:
    """Yield strings for detection only; never copy them into safe flags."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key in sorted(value, key=str):
            yield from _text_values(value[key])
    elif isinstance(value, list):
        for item in value:
            yield from _text_values(item)


def _content_bound_delimiters(section: str, value: JsonValue) -> tuple[str, str]:
    """Create deterministic markers that PR content cannot pre-inject."""
    identity = hashlib.sha256(
        section.encode("utf-8") + b"\0" + canonical_bytes(value)
    ).hexdigest()[:24]
    return (
        f"<taskplane-untrusted-review-data:{identity}>",
        f"</taskplane-untrusted-review-data:{identity}>",
    )


_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a",
                       "5": "s", "7": "t"})
_INJECTION_PATTERNS = {
    "instruction_override": re.compile(
        r"\b(?:ignore|disregard|override|forget)\b.{0,96}"
        r"\b(?:previous|prior|system|developer|instructions?)\b"),
    "exfiltration": re.compile(
        r"\b(?:reveal|show|print|exfiltrate|send|leak)\b.{0,96}"
        r"\b(?:system|developer|secret|credentials?|tokens?|prompts?|instructions?)\b"),
    "role_override": re.compile(
        r"\b(?:you are now|act as|new role)\b.{0,96}"
        r"\b(?:system|developer|administrator|reviewer)\b"),
    "boundary_marker_injection": re.compile(
        r"taskplane[_\s-]*untrusted[_\s-]*review[_\s-]*data", re.I),
}


def _injection_flags(
        section: str, value: JsonValue) -> list[dict[str, JsonValue]]:
    """Detect attacks without ever reflecting attacker-controlled strings."""
    categories = set()
    match_count = 0
    for raw in _text_values(value):
        normalized = re.sub(r"\s+", " ", raw.lower().translate(_LEET))
        for category, pattern in _INJECTION_PATTERNS.items():
            count = len(pattern.findall(normalized))
            if count:
                categories.add(category)
                match_count += count
    if not categories:
        return []
    return [{
        "section": section,
        "categories": sorted(categories),
        "match_count": min(match_count, 999),
        "action": "obstructed-as-instruction; preserved-as-review-data",
    }]


def frame_review_evidence(section: str, value: JsonValue) -> JsonValue:
    """Structurally isolate PR-owned evidence from producer control text."""
    if section not in UNTRUSTED_REVIEW_SECTIONS:
        return copy.deepcopy(value)
    begin, end = _content_bound_delimiters(section, value)
    return {
        "schema": "taskplane.untrusted-review-data/v1",
        "section": section,
        "interpretation": "data-only",
        "begin": begin,
        "content": copy.deepcopy(value),
        "end": end,
        "flags": _injection_flags(section, value),
    }


def unframe_review_evidence(section: str, value: JsonValue) -> JsonValue:
    """Validate a producer frame and return its canonical evidence content."""
    if section not in UNTRUSTED_REVIEW_SECTIONS:
        return value
    if not isinstance(value, dict):
        raise ProvenanceError("untrusted review evidence is not framed")
    content = value.get("content")
    expected = frame_review_evidence(section, content)
    if value != expected:
        raise ProvenanceError("untrusted review evidence frame mismatch")
    return content


def untrusted_evidence_boundary(
        envelope: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Return bounded, source-free injection flags for PR-controlled data."""
    flags = []
    for section in UNTRUSTED_REVIEW_SECTIONS:
        flags.extend(_injection_flags(section, envelope.get(section)))
    return {
        "schema": "taskplane.untrusted-review-data-boundary/v1",
        "sections": list(UNTRUSTED_REVIEW_SECTIONS),
        "begin": UNTRUSTED_DATA_BEGIN,
        "end": UNTRUSTED_DATA_END,
        "interpretation": "data-only",
        "flags": flags,
    }


def resolve_evidence_reference(
        store: ArtifactStore, reference: ReviewEvidenceReference, *,
                               target_fingerprint: str,
                               canonical_revision: int,
                               allowed_sections: Iterable[str],
                               context_fingerprint: str | None = None
                               ) -> JsonValue:
    """Resolve a v2 overflow reference, rejecting every identity mismatch."""
    if not isinstance(reference, dict) or reference.get("schema") != \
            "taskplane.review-evidence-reference/v2":
        raise ProvenanceError("invalid review evidence reference")
    section = str(reference.get("section") or "")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", section):
        raise ProvenanceError("invalid evidence section")
    allowed = {str(value) for value in allowed_sections}
    if section not in allowed:
        raise ProvenanceError("evidence section is unauthorized")
    if reference.get("target_fingerprint") != target_fingerprint:
        raise ProvenanceError("evidence reference belongs to another target")
    if context_fingerprint is not None and reference.get(
            "context_fingerprint") != context_fingerprint:
        raise ProvenanceError("evidence reference belongs to another context")
    if int(reference.get("canonical_revision", -1)) != int(canonical_revision):
        raise ProvenanceError("evidence reference canonical revision is stale")
    artifact = reference.get("artifact")
    if not isinstance(artifact, dict):
        raise ProvenanceError("evidence reference artifact is missing")
    for key in ("fingerprint", "digest", "bytes"):
        if reference.get(key) != artifact.get(key):
            raise ProvenanceError(f"evidence reference {key} mismatch")
    record = store.read(artifact)
    expected = {
        "schema": "taskplane.review-evidence-section/v2",
        "section": section,
        "target_fingerprint": target_fingerprint,
        "context_fingerprint": (context_fingerprint if context_fingerprint
                                is not None else reference.get(
                                    "context_fingerprint")),
        "canonical_revision": int(canonical_revision),
        "content_fingerprint": reference.get("content_fingerprint"),
    }
    for key, value in expected.items():
        if record.get(key) != value:
            raise ProvenanceError(f"evidence record {key} mismatch")
    content = record.get("content")
    raw_content = unframe_review_evidence(section, content)
    if content_fingerprint(raw_content) != record.get("content_fingerprint"):
        raise ProvenanceError("evidence content fingerprint mismatch")
    return content


def _create_scoped_view_v3(store: ArtifactStore, envelope_ref: dict, *,
                           slot_id: str, lens_ids, relevant_files,
                           canonical_revision: int,
                           routing_fingerprint: str,
                           producer: str) -> dict:
    """Project a deterministic bounded identity spine plus verified overflow."""
    if not _SLOT.fullmatch(str(slot_id or "")):
        raise ProvenanceError("invalid slot id")
    if not str(routing_fingerprint or "").strip():
        raise ProvenanceError("routing fingerprint is required")
    if not str(producer or "").strip():
        raise ProvenanceError("producer identity is required")
    revision = int(canonical_revision)
    if revision < 1:
        raise ProvenanceError("canonical revision must be positive")
    envelope = _load_complete_envelope(store, envelope_ref)
    graph_quality = envelope.get("graph_quality") or {}
    if graph_quality.get("status") == "impact_incomplete" and not (
            graph_quality.get("review_fallback") or {}).get("mode") == \
            "immutable_diff":
        raise ProvenanceError("impact_incomplete creates zero scoped views")

    sections = ("diff", "impact", "graph_quality", "runnability",
                "requirements", "contracts", "change")
    lenses = _strings(lens_ids)
    relevant_sections = _relevant_sections(lenses)
    summaries = [_section_summary(section, envelope.get(section))
                 for section in sections if section in relevant_sections]

    wanted = _strings(relevant_files)
    changed = set(_strings((envelope.get("diff") or {}).get("files")))
    relevant = [path for path in wanted if path in changed]
    spine = {
        "schema": "taskplane.scoped-review-view/v3",
        "target_fingerprint": envelope["target_fingerprint"],
        "context_fingerprint": envelope["context_fingerprint"],
        "envelope_fingerprint": envelope_ref["fingerprint"],
        "envelope_digest": envelope_ref["digest"],
        "canonical_revision": revision,
        "routing_fingerprint": str(routing_fingerprint).strip(),
        "slot_id": slot_id,
        "lens_ids": lenses,
        "producer": str(producer).strip(),
        "lease_identity": {
            "slot_id": slot_id,
            "canonical_revision": revision,
            "producer": str(producer).strip(),
        },
        "relevance": {
            "files": relevant,
            "file_count": len(relevant),
            "changed_file_count": len(changed),
        },
        "relevant_summaries": summaries,
        "untrusted_evidence_boundary": untrusted_evidence_boundary(envelope),
        "forbidden_derivations": ["git diff", "graph impact", "graph scan",
                                  "requirement lookup", "runnability probe"],
    }
    ordered = sorted(sections, key=lambda section: (
        0 if section in relevant_sections else 1, section,
        content_fingerprint(envelope.get(section))))
    inline_sections = {}
    manifest = []
    for section in ordered:
        value = envelope.get(section)
        reference = _evidence_reference(
            store, envelope, section=section, value=value,
            canonical_revision=revision)
        manifest.append({
            "section": section,
            "reason": "canonical content exceeds bounded producer view",
            "content_bytes": len(canonical_bytes(value)),
            "content_fingerprint": content_fingerprint(value),
            "reference": reference,
        })

    def shaped_payload(current_inline, current_manifest):
        omissions = [{
            "section": row["section"],
            "reason": "referenced outside the bounded producer view",
            "bytes": row["content_bytes"],
            "digest": row["reference"]["digest"],
        } for row in current_manifest]
        base = dict(
            spine, inline_sections=current_inline,
            reference_manifest=current_manifest,
            reference_manifest_fingerprint=content_fingerprint(current_manifest),
            omissions=omissions)
        integrity = content_fingerprint(base)
        return dict(base, integrity={"algorithm": "sha256",
                                     "fingerprint": integrity},
                    view_fingerprint=integrity)

    # Start from the fully bounded reference representation, then spend only
    # verified remaining bytes on exact content in relevance order.  Testing
    # the complete final shape reserves all manifest/omission bytes up front.
    for section in ordered:
        row = next(item for item in manifest if item["section"] == section)
        candidate_manifest = [item for item in manifest if item is not row]
        candidate_inline = dict(inline_sections)
        candidate_inline[section] = frame_review_evidence(
            section, envelope.get(section))
        if len(canonical_bytes(shaped_payload(
                candidate_inline, candidate_manifest))) <= MAX_SCOPED_VIEW_BYTES:
            inline_sections = candidate_inline
            manifest = candidate_manifest

    payload = shaped_payload(inline_sections, manifest)
    base = {key: value for key, value in payload.items()
            if key not in ("integrity", "view_fingerprint")}
    integrity = content_fingerprint(base)
    # shaped_payload and this explicit calculation intentionally agree; keep
    # the final binding adjacent to the boundary check for auditability.
    payload["integrity"] = {"algorithm": "sha256", "fingerprint": integrity}
    payload["view_fingerprint"] = integrity
    size = len(canonical_bytes(payload))
    if size > MAX_SCOPED_VIEW_BYTES:
        raise ArtifactIntegrityError(
            "mandatory scoped view spine exceeds "
            f"{MAX_SCOPED_VIEW_BYTES} byte bound ({size} bytes)")
    return store.put("view", payload, fingerprint=integrity)


def create_scoped_view(store: ArtifactStore, envelope_ref: dict, *,
                       slot_id: str, lens_ids, relevant_files=None,
                       evidence=None, canonical_revision: int | None = None,
                       routing_fingerprint: str | None = None,
                       producer: str | None = None) -> dict:
    """Derive a bounded view; v3 is selected by its explicit identity spine."""
    v3_values = (canonical_revision, routing_fingerprint, producer)
    if any(value is not None for value in v3_values):
        if not all(value is not None for value in v3_values):
            raise ProvenanceError("v3 scoped view identity is incomplete")
        if evidence:
            raise ProvenanceError("v3 evidence must use governed references")
        return _create_scoped_view_v3(
            store, envelope_ref, slot_id=slot_id, lens_ids=lens_ids,
            relevant_files=relevant_files,
            canonical_revision=canonical_revision,
            routing_fingerprint=routing_fingerprint, producer=producer)
    return _create_scoped_view_v2(
        store, envelope_ref, slot_id=slot_id, lens_ids=lens_ids,
        relevant_files=relevant_files, evidence=evidence)


def next_revision(store: ArtifactStore) -> int:
    current = _read_current(store)
    return int(current.get("canonical_revision", 0)) + 1 if current else 1


def create_slot_lease(store: ArtifactStore, envelope_ref: dict, view_ref: dict,
                      *, slot_id: str, lens_ids,
                      canonical_revision: int | None = None) -> dict:
    envelope = _load_complete_envelope(store, envelope_ref)
    view = store.read(view_ref)
    cited_envelope = view.get("envelope") or {}
    if view.get("schema") != "taskplane.scoped-review-view/v2" or \
            view.get("view_fingerprint") != view_ref.get("fingerprint"):
        raise ProvenanceError("slot lease scoped view is invalid")
    if view.get("context_fingerprint") != envelope["context_fingerprint"] or \
            view.get("target_fingerprint") != envelope["target_fingerprint"] or \
            cited_envelope.get("fingerprint") != envelope_ref.get("fingerprint"):
        raise ProvenanceError("slot lease view belongs to another envelope")
    lenses = _strings(lens_ids)
    if view.get("slot_id") != slot_id or view.get("lens_ids") != lenses:
        raise ProvenanceError("slot lease does not match scoped view")
    expected_revision = next_revision(store)
    revision = expected_revision if canonical_revision is None \
        else int(canonical_revision)
    if revision != expected_revision:
        raise RevisionError("slot lease canonical revision is stale or skipped")
    base = {
        "schema": "taskplane.slot-lease/v1",
        "slot_id": slot_id,
        "lens_ids": lenses,
        "target_fingerprint": envelope["target_fingerprint"],
        "context_fingerprint": envelope["context_fingerprint"],
        "view_fingerprint": view["view_fingerprint"],
        "canonical_revision": revision,
    }
    lease_fp = content_fingerprint(base)
    return store.put("lease", dict(base, lease_fingerprint=lease_fp),
                     fingerprint=lease_fp)


def write_slot_result(store: ArtifactStore, lease_ref: dict, *,
                      authored_slot: str, lens_ids, findings,
                      authored_by: str = "lens-slot",
                      references_applied=None, notes=None,
                      source: str | None = None, lens_results=None,
                      repair_audit: dict | None = None) -> dict:
    lease = store.read(lease_ref)
    if lease.get("schema") != "taskplane.slot-lease/v1":
        raise ProvenanceError("result lease is invalid")
    if authored_by != "lens-slot":
        raise ProvenanceError("result was not authored by its leased lens slot")
    if authored_slot != lease.get("slot_id"):
        raise ProvenanceError("result authored by wrong slot")
    lenses = _strings(lens_ids)
    if lenses != lease.get("lens_ids"):
        raise ProvenanceError("result lens ids do not match slot lease")
    base = {
        "schema": "taskplane.slot-result/v1",
        "lease_fingerprint": lease["lease_fingerprint"],
        "slot_id": authored_slot,
        "lens_ids": lenses,
        "target_fingerprint": lease["target_fingerprint"],
        "context_fingerprint": lease["context_fingerprint"],
        "view_fingerprint": lease["view_fingerprint"],
        "canonical_revision": lease["canonical_revision"],
        "authored_by": authored_by,
        "findings": copy.deepcopy(list(findings or [])),
    }
    if lens_results is not None:
        if not isinstance(lens_results, list):
            raise ProvenanceError("slot result lens_results must be a list")
        base["lens_results"] = copy.deepcopy(lens_results)
    if repair_audit is not None:
        if not isinstance(repair_audit, dict) or \
                repair_audit.get("equivalence") != "proven":
            raise ProvenanceError("slot result repair audit is invalid")
        base["repair_audit"] = copy.deepcopy(repair_audit)
    if lease.get("execution_binding") is not None:
        base["execution_binding"] = copy.deepcopy(lease["execution_binding"])
    if source:
        # This is supplied by ReviewKernel from the sealed lease, never by
        # the lens payload.  It is therefore immutable producer provenance,
        # not a self-asserted path from the model.
        base["source"] = str(source)
    if notes:
        base["notes"] = copy.deepcopy(list(notes))
    if references_applied:
        base["references_applied"] = copy.deepcopy(
            list(references_applied))
    result_fp = content_fingerprint(base)
    return store.put("slot-result", dict(base, result_fingerprint=result_fp),
                     fingerprint=result_fp)


def collect_partial_slot_results(store: ArtifactStore,
                                 lease_refs: Iterable[dict],
                                 result_refs: Iterable[dict], *,
                                 gaps: Iterable[dict] = ()) -> dict:
    """Collect every valid result while making missing evidence explicit.

    A producer failure must not erase its valid siblings.  This function is
    deliberately identity-strict for the results it accepts, but represents
    absent/invalid slots as bounded gap records instead of pretending the
    review is complete.  Callers may persist the returned collection as an
    immutable provisional revision and retry only the named gaps.
    """
    lease_refs = list(lease_refs)
    result_refs = list(result_refs)
    leases = [store.read(ref) for ref in lease_refs]
    results = [store.read(ref) for ref in result_refs]
    expected = {row.get("lease_fingerprint"): row for row in leases}
    if len(expected) != len(leases):
        raise ProvenanceError("duplicate slot lease")
    if len({ref.get("fingerprint") for ref in result_refs}) != len(results):
        raise ProvenanceError("duplicate or copied slot result")
    actual = {}
    for row in results:
        lease_fp = row.get("lease_fingerprint")
        if lease_fp not in expected:
            raise ProvenanceError("result cites an unexpected lease")
        if lease_fp in actual:
            raise ProvenanceError("copied result cannot satisfy two slots")
        lease = expected[lease_fp]
        identity_fields = [
            "slot_id", "lens_ids", "target_fingerprint",
            "context_fingerprint", "view_fingerprint", "canonical_revision",
        ]
        identity_fields.extend(field for field in (
            "reference_manifest_fingerprint", "routing_fingerprint",
            "producer", "execution_binding") if field in lease)
        for field in identity_fields:
            if row.get(field) != lease.get(field):
                raise ProvenanceError(f"result {field} does not match lease")
        if row.get("authored_by") != "lens-slot":
            raise ProvenanceError("result is not slot-authored")
        actual[lease_fp] = row
    revisions = {row.get("canonical_revision") for row in leases}
    targets = {row.get("target_fingerprint") for row in leases}
    contexts = {row.get("context_fingerprint") for row in leases}
    if len(revisions) != 1 or len(targets) != 1 or len(contexts) != 1:
        raise ProvenanceError("slot results mix canonical identities")
    expected_by_slot = {str(row.get("slot_id") or ""): row for row in leases}
    if "" in expected_by_slot or len(expected_by_slot) != len(leases):
        raise ProvenanceError("duplicate or missing slot identity")
    normalized_gaps = []
    gap_slots = set()
    for raw in gaps:
        row = raw if isinstance(raw, dict) else {}
        slot_id = str(row.get("slot_id") or "").strip()
        if slot_id not in expected_by_slot:
            raise ProvenanceError("gap cites an unexpected slot")
        if slot_id in gap_slots:
            raise ProvenanceError("duplicate gap for slot: " + slot_id)
        reason = str(row.get("reason") or "").strip()
        if not reason:
            raise ProvenanceError("slot gap requires a reason")
        normalized = {"slot_id": slot_id, "reason": reason}
        for key in ("producer_task", "result_path"):
            if str(row.get(key) or "").strip():
                normalized[key] = str(row[key]).strip()
        normalized_gaps.append(normalized)
        gap_slots.add(slot_id)
    actual_slots = {str(row.get("slot_id") or "") for row in actual.values()}
    if actual_slots & gap_slots:
        raise ProvenanceError("slot cannot be both collected and incomplete")
    missing_slots = set(expected_by_slot) - actual_slots
    if missing_slots != gap_slots:
        unnamed = sorted(missing_slots - gap_slots)
        extra = sorted(gap_slots - missing_slots)
        detail = ", ".join(unnamed or extra)
        raise ProvenanceError("slot gaps do not match missing results: " + detail)
    ordered = sorted(results, key=lambda row: row["slot_id"])
    normalized_gaps.sort(key=lambda row: row["slot_id"])
    complete = not normalized_gaps
    return {
        "schema": "taskplane.partial-slot-collection/v1",
        "status": "complete" if complete else "incomplete",
        "expected_slot_ids": sorted(expected_by_slot),
        "collected_slot_ids": [row["slot_id"] for row in ordered],
        "slot_ids": [row["slot_id"] for row in ordered],
        "result_fingerprints": [row["result_fingerprint"] for row in ordered],
        "results": ordered,
        "gaps": normalized_gaps,
        "completeness": {
            "expected": len(leases), "collected": len(ordered),
            "missing": len(normalized_gaps), "complete": complete,
        },
        "target_fingerprint": next(iter(targets)),
        "context_fingerprint": next(iter(contexts)),
        "canonical_revision": next(iter(revisions)),
    }


def collect_slot_results(store: ArtifactStore, lease_refs: Iterable[dict],
                         result_refs: Iterable[dict]) -> dict:
    """Compatibility strict collection: every leased slot must be present."""
    lease_refs = list(lease_refs)
    result_refs = list(result_refs)
    leases = [store.read(ref) for ref in lease_refs]
    present = {store.read(ref).get("lease_fingerprint") for ref in result_refs}
    missing = sorted(str(row.get("lease_fingerprint") or "")
                     for row in leases
                     if row.get("lease_fingerprint") not in present)
    if missing:
        raise ProvenanceError("missing slot results: " + ", ".join(missing))
    return collect_partial_slot_results(
        store, lease_refs, result_refs, gaps=[])


def revision_identity(record: dict) -> dict:
    return {key: record.get(key) for key in (
        "target_fingerprint", "context_fingerprint", "findings_fingerprint",
        "canonical_revision")}


def sealed_current_revision(store: ArtifactStore, revision: dict) -> dict:
    """Return revision substance only after the canonical pointer seals it.

    ``sealed`` is deliberately derived here.  A caller-supplied flag is not
    evidence that collection committed this exact artifact as current.
    """
    if not isinstance(revision, dict) or not isinstance(
            revision.get("artifact"), dict):
        raise RevisionError("current canonical revision artifact is missing")
    expected = revision_identity(revision)
    if _read_current(store) != expected:
        raise RevisionError("revision is not the current canonical revision")
    canonical = store.read(revision["artifact"])
    if not isinstance(canonical, dict) or revision_identity(canonical) != expected:
        raise RevisionError("current canonical revision artifact contradicts pointer")
    return dict(copy.deepcopy(canonical), sealed=True)


def _current_path(store: ArtifactStore) -> str:
    path = os.path.join(store.root, "revisions", "current.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _read_current_file(store: ArtifactStore) -> dict | None:
    path = _current_path(store)
    try:
        with open(path, encoding="utf-8") as stream:
            row = json.load(stream)
    except FileNotFoundError:
        return None
    except ValueError as exc:
        raise RevisionError(
            f"corrupt canonical revision state: {exc}") from None
    except OSError as exc:
        raise RevisionError(
            f"unreadable canonical revision state: {exc}") from None
    required = ("target_fingerprint", "context_fingerprint",
                "findings_fingerprint", "canonical_revision")
    revision = row.get("canonical_revision") if isinstance(row, dict) else None
    if not isinstance(row, dict) or isinstance(revision, bool) or \
            not isinstance(revision, int) or revision < 1 or \
            any(not isinstance(row.get(key), str) or not row.get(key)
                for key in required[:-1]):
        raise RevisionError("corrupt canonical revision state: invalid identity")
    return row


def _read_current(store: ArtifactStore) -> dict | None:
    return _read_current_file(store)


def _advance_current(store: ArtifactStore, record: dict, *,
                     expected_current: dict | None) -> None:
    path = _current_path(store)
    with tp.file_lock(path):
        prior = _read_current_file(store)
        if prior != expected_current:
            raise RevisionError("canonical revision changed concurrently")
        want = int((prior or {}).get("canonical_revision", 0)) + 1
        if int(record.get("canonical_revision", 0)) != want:
            raise RevisionError(
                "canonical revision is stale, skipped, or rolled back")
        directory = os.path.dirname(path)
        fd, tmp = tempfile.mkstemp(
            prefix=".current.", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(canonical_bytes(record))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
        finally:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                pass


def commit_revision(store: ArtifactStore, envelope_ref: dict,
                    collected: dict) -> dict:
    envelope = _load_complete_envelope(store, envelope_ref)
    prior = _read_current(store)
    revision = int((prior or {}).get("canonical_revision", 0)) + 1
    if collected.get("canonical_revision") != revision:
        raise RevisionError("slot results cite a stale or future revision")
    if collected.get("target_fingerprint") != envelope["target_fingerprint"] or \
            collected.get("context_fingerprint") != envelope["context_fingerprint"]:
        raise RevisionError("slot results contradict envelope identity")
    findings_material = {
        "result_fingerprints": collected.get("result_fingerprints") or [],
        "findings": [f for row in (collected.get("results") or [])
                     for f in (row.get("findings") or [])],
    }
    findings_fp = content_fingerprint(findings_material)
    record = {
        "schema": "taskplane.findings-revision/v1",
        "target_fingerprint": envelope["target_fingerprint"],
        "context_fingerprint": envelope["context_fingerprint"],
        "findings_fingerprint": findings_fp,
        "canonical_revision": revision,
        "result_fingerprints": list(findings_material["result_fingerprints"]),
        "findings": copy.deepcopy(findings_material["findings"]),
        "supersedes_revision": revision - 1 if revision > 1 else None,
    }
    ref = store.put("findings-revision", record)
    committed = dict(record, artifact=ref)
    _advance_current(store, revision_identity(committed),
                     expected_current=prior)
    return committed


def create_projection(store: ArtifactStore, revision: dict, *, kind: str,
                      body) -> dict:
    if kind not in {"findings", "report", "dashboard", "gate"}:
        raise RevisionError("unknown canonical projection kind")
    payload = {
        "schema": "taskplane.review-projection/v1",
        "kind": kind,
        "identity": revision_identity(revision),
        "body": copy.deepcopy(body),
    }
    return store.put(f"projection-{kind}", payload)


def verify_projection_set(store: ArtifactStore, revision: dict,
                          projection_refs: Iterable[dict]) -> bool:
    expected = revision_identity(revision)
    current = _read_current(store)
    if current != expected:
        raise RevisionError("canonical revision is not the current identity")
    seen = set()
    for ref in projection_refs:
        payload = store.read(ref)
        kind = payload.get("kind")
        if kind in seen:
            raise RevisionError("duplicate canonical projection")
        seen.add(kind)
        if payload.get("identity") != expected:
            raise RevisionError("projection identity is stale or contradictory")
    return True


# Call-site vocabulary used by the design contract.
build_envelope = create_envelope
scoped_view = create_scoped_view
collect_results = collect_slot_results
validate_projections = verify_projection_set
