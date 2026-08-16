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
from typing import Any, Iterable

import storage as runtime_storage
import taskplane_lite as tp


MAX_SCOPED_VIEW_BYTES = 16 * 1024
MAX_INLINE_REQUIREMENTS_BYTES = 4 * 1024
_KIND = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_SLOT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")


class ArtifactIntegrityError(ValueError):
    pass


class ProvenanceError(ValueError):
    pass


class RevisionError(ValueError):
    pass


def canonical_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def content_fingerprint(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


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
        if not supplied_path:
            relative = str(ref.get("relative_path") or "")
            supplied_path = os.path.join(self.workspace, *relative.split("/"))
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


def create_scoped_view(store: ArtifactStore, envelope_ref: dict, *,
                       slot_id: str, lens_ids, relevant_files=None,
                       evidence=None) -> dict:
    """Derive a bounded deterministic view; never re-derive shared facts."""
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
                      source: str | None = None) -> dict:
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


def collect_slot_results(store: ArtifactStore, lease_refs: Iterable[dict],
                         result_refs: Iterable[dict]) -> dict:
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
        for field in ("slot_id", "lens_ids", "target_fingerprint",
                      "context_fingerprint", "view_fingerprint",
                      "canonical_revision"):
            if row.get(field) != lease.get(field):
                raise ProvenanceError(f"result {field} does not match lease")
        if row.get("authored_by") != "lens-slot":
            raise ProvenanceError("result is not slot-authored")
        actual[lease_fp] = row
    missing = sorted(set(expected) - set(actual))
    if missing:
        raise ProvenanceError("missing slot results: " + ", ".join(missing))
    revisions = {row.get("canonical_revision") for row in leases}
    targets = {row.get("target_fingerprint") for row in leases}
    contexts = {row.get("context_fingerprint") for row in leases}
    if len(revisions) != 1 or len(targets) != 1 or len(contexts) != 1:
        raise ProvenanceError("slot results mix canonical identities")
    ordered = sorted(results, key=lambda row: row["slot_id"])
    return {
        "status": "complete",
        "slot_ids": [row["slot_id"] for row in ordered],
        "result_fingerprints": [row["result_fingerprint"] for row in ordered],
        "results": ordered,
        "target_fingerprint": next(iter(targets)),
        "context_fingerprint": next(iter(contexts)),
        "canonical_revision": next(iter(revisions)),
    }


def revision_identity(record: dict) -> dict:
    return {key: record.get(key) for key in (
        "target_fingerprint", "context_fingerprint", "findings_fingerprint",
        "canonical_revision")}


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
