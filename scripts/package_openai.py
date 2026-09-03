#!/usr/bin/env python3
"""Build and validate the taskplane skills-only OpenAI marketplace ZIP."""

from __future__ import annotations

import argparse
import ast
import base64
from collections.abc import Mapping, Sequence
import hashlib
import importlib.util
import io
import json
import math
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PurePosixPath

# Console codepages are not always UTF-8 (Windows defaults to cp1252, a C
# locale gives ASCII), and this script's own output carries arrows and em
# dashes. The text is ours and it is UTF-8; say so rather than dying in the
# middle of a report.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass



ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MANIFEST_PATH = ROOT / ".codex-plugin" / "plugin.json"
ARCHIVE_ROOT = "taskplane"
FIXED_ZIP_TIME = (2020, 1, 1, 0, 0, 0)
OPENAI_EXCLUDED_SKILLS = {"tp-tag"}
PACKAGE_TEMP_ROOT = "TASKPLANE_PACKAGE_TEMP_ROOT"

CATEGORIES = {
    "Productivity",
    "Creativity",
    "Developer Tools",
    "Business & Operations",
    "Data & Analytics",
    "Communication",
    "Education & Research",
    "Security",
    "Finance",
    "Healthcare",
    "Travel",
    "Entertainment",
    "Other",
}

REQUIRED_FILES = (
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "PRIVACY.md",
    "SUPPORT.md",
    "TERMS.md",
    "docs/authority-matrix.md",
    "docs/loop-design.md",
    "docs/state-spec.md",
    "hooks/hooks.json",
    "hooks/host-native.json",
    "hooks/host_native_runtime.py",
)

HOOK_FILES = (
    "hooks/hooks.json",
    "hooks/host-native.json",
    "hooks/host_native_runtime.py",
)

STAGE_RUNTIME_FILES = (
    "taskplane/taskplane_lite.py",
    "taskplane/loop.py",
    "taskplane/tp.py",
    "taskplane/stage_entities.py",
    "taskplane/stage_handoff.py",
    "taskplane/stage_migration.py",
    "taskplane/loop_status.py",
    "taskplane/dashboard.py",
    "taskplane/runtime_eval.py",
    "taskplane/release_evidence.py",
    "taskplane/settings.py",
    "taskplane/design_host_transport.py",
    "taskplane/collision_registry.json",
    "taskplane/build_quality.py",
    "taskplane/failure_routing.py",
    "taskplane/run_artifacts.py",
    "taskplane/run_store.py",
    "taskplane/owned_cleanup.py",
    "taskplane/dispatch_telemetry.py",
    "taskplane/wave_metrics.py",
    "taskplane/retro.py",
    "docs/cli-reference.md",
    "skills/taskplane/SKILL.md",
    "skills/taskplane/flow.json",
    "skills/tp-build/SKILL.md",
    "skills/tp-design/SKILL.md",
    "skills/tp-design/flow.json",
    "skills/tp-engineering/SKILL.md",
    "skills/tp-engineering/flow.json",
    "skills/tp-go/SKILL.md",
    "skills/tp-go/flow.json",
    "skills/tp-go/references/parallel.md",
    "skills/tp-go/references/retro.md",
    "skills/tp-product/SKILL.md",
    "skills/tp-product/flow.json",
    "skills/tp-status/SKILL.md",
    "skills/tp-status/flow.json",
)

RELEASE_SURFACE_FILES = (
    "taskplane/release_evidence.py",
    "lenses/references/prompt-injection-defense.md",
    "README.md",
    "CHANGELOG.md",
)

# Runtime policy authorities are data, not Python modules. Keep their
# install membership explicit so a future package-file predicate cannot
# silently strand settings loading or its conformance receipts.
CANONICAL_AUTHORITY_FILES = (
    "taskplane/operational-settings.json",
    "taskplane/settings_inventory.json",
)

RELEASE_COMPATIBILITY_RECEIPT_FIELDS = frozenset({
    "schema", "source_sha", "compatibility_policy_fingerprint", "cells",
    "producer", "status", "cryptographic_authenticity_claimed", "fingerprint",
})

RELEASE_COMPATIBILITY_CELL_FIELDS = frozenset({
    "plugin", "host", "candidate_sha", "test_name", "test_outcome",
    "artifact_sha256", "host_validator_sha256", "check_identity", "platform",
})

SUPPORTED_HOOK_ROOT_FIELDS = frozenset({"description", "hooks"})


class PackageError(RuntimeError):
    """A marketplace package invariant failed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PackageError(message)


def approved_output_roots(environ: Mapping[str, str] | None = None) -> tuple[Path, ...]:
    env = os.environ if environ is None else environ
    configured = env.get(PACKAGE_TEMP_ROOT)
    temporary = Path(configured) if configured else Path(tempfile.gettempdir())
    try:
        if temporary.is_symlink():
            raise PackageError("approved temporary root must not be a symlink")
        resolved = temporary.resolve(strict=True)
    except OSError as exc:
        raise PackageError(f"approved temporary root is invalid: {exc}") from exc
    require(resolved.is_dir(), "approved temporary root must be a directory")
    roots = (ROOT.resolve(), Path("/tmp").resolve(), resolved)
    return tuple(dict.fromkeys(roots))


def require_approved_output(path: Path, roots: Sequence[Path]) -> None:
    resolved = path.resolve()
    require(
        any(resolved == root or resolved.is_relative_to(root) for root in roots),
        "output path must stay inside the repository or approved temporary root",
    )


def load_json_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackageError(f"cannot read {label}: {exc}") from exc
    require(isinstance(value, dict), f"{label} must be a JSON object")
    return value


def _validate_sealed_receipt(
    receipt: Mapping[str, object], fields: frozenset[str], label: str
) -> None:
    from taskplane.delivery_ports import content_fingerprint

    require(set(receipt) == fields, f"{label} fields are not closed")
    projection = {key: value for key, value in receipt.items()
                  if key != "fingerprint"}
    require(receipt.get("fingerprint") == content_fingerprint(projection),
            f"{label} fingerprint is invalid")


def _validate_release_compatibility_receipt(
    receipt: Mapping[str, object],
    *,
    policy: Mapping[str, object],
    expected_source_sha: str,
) -> dict:
    from taskplane.delivery_ports import content_fingerprint

    _validate_sealed_receipt(
        receipt, RELEASE_COMPATIBILITY_RECEIPT_FIELDS,
        "release compatibility receipt",
    )
    require(receipt.get("schema") ==
            "taskplane.release-compatibility-matrix/v2",
            "release compatibility receipt schema is invalid")
    require(receipt.get("source_sha") == expected_source_sha,
            "release compatibility receipt does not bind the source SHA")
    require(receipt.get("compatibility_policy_fingerprint") ==
            content_fingerprint(policy),
            "release compatibility receipt does not bind the policy")
    require(receipt.get("status") == "release-compatible",
            "release compatibility receipt is not release-compatible")
    require(receipt.get("cryptographic_authenticity_claimed") is False,
            "release compatibility receipt must not claim authenticity")
    producer = policy.get("release_observation_producer")
    require(isinstance(producer, Mapping),
            "compatibility policy must declare its observation producer")
    require(receipt.get("producer") == producer.get("entrypoint"),
            "release compatibility receipt does not name the package producer")

    release_matrix = policy.get("release_matrix")
    require(isinstance(release_matrix, list),
            "compatibility policy must declare a release_matrix")
    expected_pairs = {
        (row.get("plugin"), row.get("host"))
        for row in release_matrix if isinstance(row, Mapping)
    }
    window = policy.get("window")
    require(isinstance(window, Mapping),
            "compatibility policy window is invalid")
    current = window.get("current")
    last_released = window.get("last_released")
    required_pairs = {
        (current, current),
        (current, last_released),
        (last_released, current),
        (last_released, last_released),
    }
    require(expected_pairs == required_pairs,
            "release matrix must cover current and the last released generation")

    cells = receipt.get("cells")
    require(isinstance(cells, list),
            "release compatibility receipt cells must be a list")
    observed_pairs: set[tuple[object, object]] = set()
    for cell in cells:
        require(isinstance(cell, Mapping) and
                set(cell) == RELEASE_COMPATIBILITY_CELL_FIELDS,
                "release compatibility cell fields are not closed")
        require(cell.get("candidate_sha") == expected_source_sha,
                "release compatibility cell does not bind the source SHA")
        require(cell.get("test_name") == producer.get("test_name") and
                cell.get("test_outcome") == "passed",
                "release compatibility cell has no passing executable outcome")
        require(cell.get("platform") == producer.get("platform"),
                "release compatibility cell platform is invalid")
        expected_check = (
            str(producer.get("check_identity_prefix")) +
            f'{cell.get("plugin")}-on-{cell.get("host")}'
        )
        require(cell.get("check_identity") == expected_check,
                "release compatibility cell check identity is invalid")
        for field_name in ("artifact_sha256", "host_validator_sha256"):
            value = cell.get(field_name)
            require(isinstance(value, str) and
                    re.fullmatch(r"[0-9a-f]{64}", value) is not None,
                    f"release compatibility cell {field_name} is invalid")
        observed_pairs.add((cell.get("plugin"), cell.get("host")))
    require(observed_pairs == required_pairs,
            "release compatibility evidence must cover the last released generation")
    require(len(cells) == 4,
            "release compatibility receipt must contain four executable cells")
    return dict(receipt)


class GitHubApi:
    """Read authenticated release facts from the repository's control plane."""

    def __init__(self, token: str):
        require(bool(token.strip()),
                "GITHUB_TOKEN is required for live release authority")
        self._token = token.strip()

    @classmethod
    def from_environment(cls) -> "GitHubApi":
        return cls(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "")

    def get(self, path: str) -> object:
        require(path.startswith("/repos/"), "GitHub API path is outside repository scope")
        request = urllib.request.Request(
            "https://api.github.com" + path,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "taskplane-release-packager",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                value = json.load(response)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise PackageError(f"GitHub release-authority query failed: {exc}") from exc
        return value


def _release_authority(policy: Mapping[str, object]) -> Mapping[str, object]:
    authority = policy.get("release_authority")
    require(isinstance(authority, Mapping),
            "compatibility policy must declare release authority")
    require(authority.get("provider") == "github",
            "release authority provider must be GitHub")
    require(authority.get("repository") == "vdemkiv/taskPlane",
            "release authority repository is invalid")
    require(authority.get("protected_ref") == "refs/heads/main",
            "release authority protected ref is invalid")
    workflow = authority.get("workflow")
    require(isinstance(workflow, Mapping) and
            workflow.get("name") == "CI" and
            workflow.get("path") == ".github/workflows/ci.yml" and
            workflow.get("event") == "push",
            "release authority workflow identity is invalid")
    checks = authority.get("required_checks")
    require(isinstance(checks, list) and checks and
            all(isinstance(name, str) and name for name in checks) and
            len(checks) == len(set(checks)),
            "release authority check identities are invalid")
    decision = authority.get("publication_decision")
    require(isinstance(decision, Mapping) and
            decision.get("kind") == "github-verified-annotated-tag/v1" and
            decision.get("message_schema") ==
            "taskplane.openai-publication-approval/v1" and
            decision.get("decision") == "approve",
            "publication decision trust policy is invalid")
    fingerprints = decision.get("allowed_signer_key_fingerprints")
    require(isinstance(fingerprints, list) and
            all(isinstance(fingerprint, str) and (
                re.fullmatch(r"[0-9A-F]{40}|[0-9A-F]{64}", fingerprint) is not None or
                re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", fingerprint) is not None
            ) for fingerprint in fingerprints),
            "publication signing key fingerprint policy is invalid")
    require(bool(fingerprints),
            "publication signing key fingerprint allowlist is empty; "
            "a separately authorized release must add a reviewed key fingerprint")
    return authority


def _openpgp_length(data: bytes, offset: int) -> tuple[int, int]:
    require(offset < len(data), "publication signature packet is truncated")
    first = data[offset]
    if first < 192:
        return first, offset + 1
    if first < 224:
        require(offset + 1 < len(data),
                "publication signature packet is truncated")
        return ((first - 192) << 8) + data[offset + 1] + 192, offset + 2
    if first == 255:
        require(offset + 4 < len(data),
                "publication signature packet is truncated")
        return int.from_bytes(data[offset + 1:offset + 5], "big"), offset + 5
    raise PackageError("publication signature uses an unsupported partial packet")


def _ascii_armored_bytes(signature: str, begin: str, end: str) -> bytes:
    lines = signature.strip().splitlines()
    require(lines and lines[0] == begin and lines[-1] == end,
            "publication signature armor is invalid")
    body_started = False
    encoded: list[str] = []
    for line in lines[1:-1]:
        if not body_started:
            if not line:
                body_started = True
                continue
            if ":" in line:
                continue
            body_started = True
        if line.startswith("="):
            break
        if line:
            encoded.append(line.strip())
    require(bool(encoded), "publication signature armor has no body")
    try:
        return base64.b64decode("".join(encoded), validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise PackageError("publication signature armor is invalid") from exc


def _hashed_openpgp_issuer_fingerprint(signature: str) -> str:
    data = _ascii_armored_bytes(
        signature, "-----BEGIN PGP SIGNATURE-----",
        "-----END PGP SIGNATURE-----",
    )
    offset = 0
    signature_bodies: list[bytes] = []
    while offset < len(data):
        header = data[offset]
        offset += 1
        require(bool(header & 0x80),
                "publication signature packet header is invalid")
        if header & 0x40:
            packet_type = header & 0x3F
            packet_length, offset = _openpgp_length(data, offset)
        else:
            packet_type = (header >> 2) & 0x0F
            length_type = header & 0x03
            require(length_type != 3,
                    "publication signature packet has indeterminate length")
            length_size = (1, 2, 4)[length_type]
            require(offset + length_size <= len(data),
                    "publication signature packet is truncated")
            packet_length = int.from_bytes(
                data[offset:offset + length_size], "big"
            )
            offset += length_size
        packet_end = offset + packet_length
        require(packet_end <= len(data),
                "publication signature packet is truncated")
        if packet_type == 2:
            signature_bodies.append(data[offset:packet_end])
        offset = packet_end
    require(len(signature_bodies) == 1,
            "publication signature must contain one OpenPGP signature packet")
    body = signature_bodies[0]
    require(len(body) >= 8 and body[0] == 4,
            "publication signature must use OpenPGP v4")
    hashed_length = int.from_bytes(body[4:6], "big")
    hashed_end = 6 + hashed_length
    require(hashed_end <= len(body),
            "publication signature hashed area is truncated")
    fingerprints: list[str] = []
    offset = 6
    while offset < hashed_end:
        subpacket_length, content_offset = _openpgp_length(body, offset)
        subpacket_end = content_offset + subpacket_length
        require(subpacket_length >= 1 and subpacket_end <= hashed_end,
                "publication signature hashed subpacket is truncated")
        subpacket_type = body[content_offset] & 0x7F
        value = body[content_offset + 1:subpacket_end]
        if subpacket_type == 33:
            require(len(value) in {21, 33} and value[0] in {4, 5},
                    "publication signature issuer fingerprint is invalid")
            fingerprints.append(value[1:].hex().upper())
        offset = subpacket_end
    require(len(fingerprints) == 1,
            "publication signature has no unique hashed issuer fingerprint")
    return fingerprints[0]


def _ssh_signer_fingerprint(signature: str) -> str:
    data = _ascii_armored_bytes(
        signature, "-----BEGIN SSH SIGNATURE-----",
        "-----END SSH SIGNATURE-----",
    )
    require(data.startswith(b"SSHSIG") and len(data) >= 10,
            "publication SSH signature is invalid")
    offset = 6
    version = int.from_bytes(data[offset:offset + 4], "big")
    offset += 4
    require(version == 1 and offset + 4 <= len(data),
            "publication SSH signature version is invalid")
    key_length = int.from_bytes(data[offset:offset + 4], "big")
    offset += 4
    key_end = offset + key_length
    require(key_length > 0 and key_end <= len(data),
            "publication SSH signature key is invalid")
    digest = hashlib.sha256(data[offset:key_end]).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def _signer_key_fingerprint(signature: object) -> str:
    require(isinstance(signature, str) and signature,
            "GitHub verification has no publication signature")
    if signature.startswith("-----BEGIN PGP SIGNATURE-----"):
        return _hashed_openpgp_issuer_fingerprint(signature)
    if signature.startswith("-----BEGIN SSH SIGNATURE-----"):
        return _ssh_signer_fingerprint(signature)
    raise PackageError("publication signature format cannot bind a signing key fingerprint")


def _validate_publication_verification_payload(
    payload: object,
    *,
    tag_name: str,
    expected_source_sha: str,
    expected_message: str,
) -> None:
    require(isinstance(payload, str),
            "GitHub publication verification payload is invalid")
    header, separator, message = payload.partition("\n\n")
    require(separator == "\n\n" and message.rstrip("\n") == expected_message,
            "GitHub publication verification payload does not bind the decision")
    lines = header.splitlines()
    require(len(lines) == 4 and
            lines[0] == f"object {expected_source_sha}" and
            lines[1] == "type commit" and
            lines[2] == f"tag {tag_name}" and
            lines[3].startswith("tagger "),
            "GitHub publication verification payload does not bind the tag object")


def _github_ci_snapshot(
    github_api: object,
    *,
    authority: Mapping[str, object],
    proof: Mapping[str, object],
    expected_source_sha: str,
) -> dict:
    from taskplane.delivery_ports import content_fingerprint

    repository = str(authority["repository"])
    protected_ref = str(authority["protected_ref"])
    branch = protected_ref.removeprefix("refs/heads/")
    remote_ref = github_api.get(
        f"/repos/{repository}/git/ref/heads/{urllib.parse.quote(branch, safe='')}"
    )
    require(isinstance(remote_ref, Mapping) and
            remote_ref.get("ref") == protected_ref and
            isinstance(remote_ref.get("object"), Mapping) and
            remote_ref["object"].get("type") == "commit" and
            remote_ref["object"].get("sha") == expected_source_sha,
            "GitHub protected ref does not name the candidate SHA")

    workflow_id = str(proof.get("workflow_run_id") or "")
    run = github_api.get(f"/repos/{repository}/actions/runs/{workflow_id}")
    workflow_policy = authority["workflow"]
    require(isinstance(run, Mapping) and
            str(run.get("id")) == workflow_id and
            run.get("name") == workflow_policy["name"] and
            run.get("path") == workflow_policy["path"] and
            run.get("event") == workflow_policy["event"] and
            run.get("head_branch") == branch and
            run.get("head_sha") == expected_source_sha and
            run.get("status") == "completed" and
            run.get("conclusion") == "success",
            "GitHub workflow run identity or release-green conclusion is invalid")
    for repository_field in ("repository", "head_repository"):
        value = run.get(repository_field)
        require(isinstance(value, Mapping) and
                value.get("full_name") == repository,
                "GitHub workflow run belongs to another repository")

    response = github_api.get(
        f"/repos/{repository}/commits/{expected_source_sha}/check-runs"
    )
    require(isinstance(response, Mapping) and
            isinstance(response.get("check_runs"), list),
            "GitHub check-run response is invalid")
    by_name: dict[str, Mapping[str, object]] = {}
    for row in response["check_runs"]:
        if isinstance(row, Mapping) and row.get("name") in authority["required_checks"]:
            name = str(row["name"])
            require(name not in by_name,
                    "GitHub returned duplicate required check identities")
            by_name[name] = row
    require(set(by_name) == set(authority["required_checks"]),
            "GitHub is missing a required release check")
    checks: list[dict[str, str]] = []
    for name in authority["required_checks"]:
        row = by_name[name]
        app = row.get("app")
        details_url = row.get("details_url")
        parsed_details = urllib.parse.urlsplit(
            details_url if isinstance(details_url, str) else ""
        )
        expected_run_path = (
            f"/{repository}/actions/runs/{workflow_id}/job/"
        )
        require(row.get("head_sha") == expected_source_sha and
                row.get("status") == "completed" and
                row.get("conclusion") == "success" and
                isinstance(app, Mapping) and app.get("slug") == "github-actions" and
                parsed_details.scheme == "https" and
                parsed_details.netloc == "github.com" and
                re.fullmatch(re.escape(expected_run_path) + r"[1-9][0-9]*",
                             parsed_details.path) is not None and
                not parsed_details.query and not parsed_details.fragment,
                f"GitHub required check is not release-green: {name}")
        checks.append({
            "id": str(row.get("id")),
            "name": str(name),
            "conclusion": "success",
            "app": "github-actions",
            "details_url": details_url,
        })
    snapshot = {
        "schema": "taskplane.github-release-ci-snapshot/v1",
        "repository": repository,
        "protected_ref": protected_ref,
        "source_sha": expected_source_sha,
        "workflow": {
            "id": workflow_id,
            "name": workflow_policy["name"],
            "path": workflow_policy["path"],
            "event": workflow_policy["event"],
            "head_branch": branch,
            "conclusion": "success",
        },
        "checks": checks,
    }
    require(proof.get("provider") == "github" and
            proof.get("repository_id") == repository,
            "release-green proof repository identity is invalid")
    require(proof.get("protected_default_branch") == branch,
            "release-green proof protected branch identity is invalid")
    require(proof.get("pushed_sha") == expected_source_sha,
            "release-green proof does not bind the candidate SHA")
    require(proof.get("required_check_names") ==
            list(authority["required_checks"]),
            "release-green proof check identities are invalid")
    require(proof.get("check_run_ids") == [row["id"] for row in checks],
            "release-green proof check-run identities are invalid")
    require(proof.get("conclusions") ==
            {name: "success" for name in authority["required_checks"]},
            "release-green proof check conclusions are invalid")
    require(proof.get("platform_response_digest") ==
            content_fingerprint(snapshot),
            "release-green proof does not match the live GitHub response")
    return snapshot


def _publication_decision_message(
    *,
    authority: Mapping[str, object],
    source_sha: str,
    release_green_fingerprint: str,
) -> str:
    decision = authority["publication_decision"]
    return "\n".join((
        str(decision["message_schema"]),
        f'decision={decision["decision"]}',
        f'repository={authority["repository"]}',
        f"source_sha={source_sha}",
        f"release_green_fingerprint={release_green_fingerprint}",
    ))


def _verify_signed_publication_decision(
    github_api: object,
    *,
    authority: Mapping[str, object],
    release_green: Mapping[str, object],
    expected_source_sha: str,
) -> None:
    repository = str(authority["repository"])
    decision = authority["publication_decision"]
    tag_name = str(decision["tag_prefix"]) + expected_source_sha
    tag_ref = github_api.get(f"/repos/{repository}/git/ref/tags/{tag_name}")
    require(isinstance(tag_ref, Mapping) and
            tag_ref.get("ref") == "refs/tags/" + tag_name and
            isinstance(tag_ref.get("object"), Mapping) and
            tag_ref["object"].get("type") == "tag",
            "publication authority must be an annotated Git tag")
    tag_object_sha = tag_ref["object"].get("sha")
    require(isinstance(tag_object_sha, str) and
            re.fullmatch(r"[0-9a-f]{40}", tag_object_sha) is not None,
            "publication authority tag object identity is invalid")
    tag = github_api.get(f"/repos/{repository}/git/tags/{tag_object_sha}")
    expected_message = _publication_decision_message(
        authority=authority,
        source_sha=expected_source_sha,
        release_green_fingerprint=str(release_green["fingerprint"]),
    )
    require(isinstance(tag, Mapping) and tag.get("tag") == tag_name and
            tag.get("message") == expected_message and
            isinstance(tag.get("object"), Mapping) and
            tag["object"].get("type") == "commit" and
            tag["object"].get("sha") == expected_source_sha,
            "signed publication decision does not bind release-green and candidate")
    verification = tag.get("verification")
    require(isinstance(verification, Mapping) and
            verification.get("verified") is True and
            verification.get("reason") == "valid" and
            bool(verification.get("signature")) and
            bool(verification.get("payload")),
            "GitHub did not cryptographically verify the publication decision")
    _validate_publication_verification_payload(
        verification["payload"],
        tag_name=tag_name,
        expected_source_sha=expected_source_sha,
        expected_message=expected_message,
    )
    signer_fingerprint = _signer_key_fingerprint(verification["signature"])
    require(signer_fingerprint in decision["allowed_signer_key_fingerprints"],
            "signed publication decision signing key fingerprint is not allowlisted")


def _materialize_git_revision(revision: str, destination: Path) -> None:
    result = subprocess.run(
        ["git", "archive", "--format=tar", revision],
        cwd=ROOT, capture_output=True, check=False,
    )
    require(result.returncode == 0,
            f"cannot materialize release generation {revision}")
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            require(not path.is_absolute() and ".." not in path.parts and
                    (member.isfile() or member.isdir()),
                    "historical release archive contains an unsafe member")
            target = destination.joinpath(*path.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            require(source is not None,
                    "historical release archive member is unreadable")
            target.write_bytes(source.read())


def _load_packager(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    require(spec is not None and spec.loader is not None,
            "historical package validator is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def produce_release_compatibility_receipt(
    *,
    expected_source_sha: str,
    policy: Mapping[str, object] | None = None,
) -> dict:
    """Execute the current/last-released package matrix and seal its facts."""
    from taskplane.delivery_ports import content_fingerprint
    from taskplane.release_evidence import (
        ReleaseEvidenceError,
        validate_compatibility_policy,
    )

    try:
        checked_policy = validate_compatibility_policy(
            policy or load_json_object(
                ROOT / "design" / "compatibility.json", "compatibility policy"
            )
        )
    except ReleaseEvidenceError as exc:
        raise PackageError(f"compatibility policy is invalid: {exc}") from exc
    producer = checked_policy.get("release_observation_producer")
    require(isinstance(producer, Mapping),
            "compatibility policy has no production observation producer")
    require(git_is_clean(),
            "compatibility producer requires a clean exact source checkout")
    require(git_head() == expected_source_sha,
            "compatibility producer does not bind the checked-out candidate SHA")
    last_tag = str(producer.get("last_released_tag") or "")
    last_commit = str(producer.get("last_released_commit") or "")
    resolved = subprocess.run(
        ["git", "rev-parse", f"{last_tag}^{{}}"], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False,
    )
    require(resolved.returncode == 0 and resolved.stdout.strip() == last_commit,
            "last released compatibility tag does not resolve to the pinned commit")

    current = checked_policy["window"]["current"]
    last_released = checked_policy["window"]["last_released"]
    with tempfile.TemporaryDirectory(prefix="taskplane-release-matrix-") as temporary:
        scratch = Path(temporary)
        historical_root = scratch / "last-released"
        historical_root.mkdir()
        _materialize_git_revision(last_commit, historical_root)
        historical = _load_packager(
            historical_root / "scripts" / "package_openai.py",
            f"_taskplane_package_openai_{last_commit}",
        )
        archives = {
            current: scratch / f"taskplane-{current}.zip",
            last_released: scratch / f"taskplane-{last_released}.zip",
        }
        write_zip(package_files(load_manifest()), archives[current])
        historical.write_zip(
            historical.package_files(historical.load_manifest()),
            archives[last_released],
        )
        artifact_digests = {
            version: hashlib.sha256(path.read_bytes()).hexdigest()
            for version, path in archives.items()
        }
        validator_digests = {
            current: hashlib.sha256(
                (ROOT / "scripts" / "package_openai.py").read_bytes()
            ).hexdigest(),
            last_released: hashlib.sha256(
                (historical_root / "scripts" / "package_openai.py").read_bytes()
            ).hexdigest(),
        }
        cells: list[dict[str, object]] = []
        for row in checked_policy["release_matrix"]:
            plugin = row["plugin"]
            host = row["host"]
            try:
                if host == current:
                    validate_archive(
                        archives[plugin],
                        expected_version=plugin,
                        release_surface_root=(
                            ROOT if plugin == current else historical_root
                        ),
                        stage_runtime_files=(
                            STAGE_RUNTIME_FILES if plugin == current else
                            historical.STAGE_RUNTIME_FILES
                        ),
                        release_surface_files=(
                            RELEASE_SURFACE_FILES if plugin == current else ()
                        ),
                        canonical_authority_files=(
                            CANONICAL_AUTHORITY_FILES
                            if plugin == current else ()
                        ),
                    )
                else:
                    historical.validate_archive(archives[plugin])
            except Exception as exc:
                raise PackageError(
                    f"compatibility check failed for {plugin}-on-{host}: {exc}"
                ) from exc
            cells.append({
                "plugin": plugin,
                "host": host,
                "candidate_sha": expected_source_sha,
                "test_name": producer["test_name"],
                "test_outcome": "passed",
                "artifact_sha256": artifact_digests[plugin],
                "host_validator_sha256": validator_digests[host],
                "check_identity": (
                    str(producer["check_identity_prefix"]) +
                    f"{plugin}-on-{host}"
                ),
                "platform": producer["platform"],
            })
    receipt = {
        "schema": "taskplane.release-compatibility-matrix/v2",
        "source_sha": expected_source_sha,
        "compatibility_policy_fingerprint": content_fingerprint(checked_policy),
        "producer": producer["entrypoint"],
        "cells": cells,
        "status": "release-compatible",
        "cryptographic_authenticity_claimed": False,
    }
    receipt["fingerprint"] = content_fingerprint(receipt)
    return _validate_release_compatibility_receipt(
        receipt, policy=checked_policy, expected_source_sha=expected_source_sha
    )


def validate_release_package_authority(
    *,
    release_green: Mapping[str, object] | None,
    expected_source_sha: str,
    now: float,
    policy: Mapping[str, object] | None = None,
    github_api: object | None = None,
) -> dict:
    """Fail closed on live CI, executed compatibility, and signed approval."""
    from taskplane.delivery_ports import content_fingerprint
    from taskplane.release_evidence import (
        ReleaseEvidenceError,
        validate_compatibility_policy,
        validate_release_green,
    )

    require(isinstance(release_green, Mapping),
            "a release-green receipt is required for marketplace packaging")
    require(git_is_clean(),
            "marketplace packaging requires a clean exact source checkout")
    try:
        checked_policy = validate_compatibility_policy(
            policy or load_json_object(
                ROOT / "design" / "compatibility.json", "compatibility policy"
            )
        )
        checked_release = validate_release_green(release_green, now=now)
    except ReleaseEvidenceError as exc:
        raise PackageError(f"release-green authority is invalid: {exc}") from exc
    require(checked_release["source_sha"] == expected_source_sha,
            "release-green authority does not bind the source SHA")
    require(checked_release["compatibility_policy_fingerprint"] ==
            content_fingerprint(checked_policy),
            "release-green authority does not bind the compatibility policy")
    authority = _release_authority(checked_policy)
    proof = checked_release.get("platform_ci_proof")
    require(isinstance(proof, Mapping),
            "release-green authority has no platform CI proof")
    api = github_api or GitHubApi.from_environment()
    _github_ci_snapshot(
        api,
        authority=authority,
        proof=proof,
        expected_source_sha=expected_source_sha,
    )
    compatibility_receipt = produce_release_compatibility_receipt(
        expected_source_sha=expected_source_sha,
        policy=checked_policy,
    )
    checked_compatibility = _validate_release_compatibility_receipt(
        compatibility_receipt,
        policy=checked_policy,
        expected_source_sha=expected_source_sha,
    )
    require(checked_release["mixed_version_matrix_receipt"] ==
            checked_compatibility["fingerprint"],
            "release-green authority does not bind last-released compatibility evidence")
    _verify_signed_publication_decision(
        api,
        authority=authority,
        release_green=checked_release,
        expected_source_sha=expected_source_sha,
    )
    return checked_release


def git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False,
    )
    require(result.returncode == 0 and
            re.fullmatch(r"[0-9a-f]{40}", result.stdout.strip()) is not None,
            "cannot resolve the exact source SHA")
    return result.stdout.strip()


def git_is_clean() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=normal"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=False,
    )
    return result.returncode == 0 and not result.stdout.strip()


def release_runtime_constants() -> dict[str, str]:
    """Read release identity from the runtime without executing it."""
    path = ROOT / "taskplane" / "release_evidence.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    wanted = {
        "CURRENT_VERSION", "PREVIOUS_VERSION",
        "COMPATIBILITY_PREVIOUS_VERSION", "HISTORICAL_GRAPH_REVISION",
    }
    values: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id in wanted:
            value = ast.literal_eval(node.value)
            require(isinstance(value, str),
                    f"release runtime constant {target.id} must be a string")
            values[target.id] = value
    require(set(values) == wanted,
            "release runtime must declare the closed release identity")
    return values


def load_manifest() -> dict:
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackageError(f"cannot read {MANIFEST_PATH}: {exc}") from exc
    require(isinstance(manifest, dict), "plugin manifest must be a JSON object")
    return manifest


def validate_hook_manifest(value: object) -> dict:
    """Enforce the Codex hook parser's closed root schema.

    Host-native discovery metadata is intentionally packaged separately in
    ``hooks/host-native.json``.  Putting ``hostNative`` back at this root
    recreates the 2.17.12 installation failure before any hook can run.
    """
    require(isinstance(value, dict), "hook manifest root must be an object")
    require(set(value) <= SUPPORTED_HOOK_ROOT_FIELDS,
            "hook manifest contains root fields rejected by Codex")
    require(isinstance(value.get("hooks"), dict),
            "hook manifest must declare hooks as an object")
    return value


def load_hook_manifest() -> dict:
    path = ROOT / "hooks" / "hooks.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackageError(f"cannot read hook manifest: {exc}") from exc
    return validate_hook_manifest(value)


def valid_https_url(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > 1024:
        return False
    parsed = urllib.parse.urlsplit(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def relative_luminance(color: str) -> float:
    values = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]

    def channel(value: float) -> float:
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(value) for value in values)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first: str, second: str) -> float:
    high, low = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def svg_dimensions(path: Path) -> tuple[float, float]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise PackageError(f"invalid SVG {path.relative_to(ROOT)}: {exc}") from exc
    require(root.tag.rsplit("}", 1)[-1] == "svg", f"{path.relative_to(ROOT)} root must be <svg>")
    view_box = root.attrib.get("viewBox", "").replace(",", " ").split()
    if len(view_box) == 4:
        try:
            width, height = float(view_box[2]), float(view_box[3])
        except ValueError as exc:
            raise PackageError(f"{path.relative_to(ROOT)} has a non-numeric viewBox") from exc
    else:
        try:
            width = float(root.attrib["width"])
            height = float(root.attrib["height"])
        except (KeyError, ValueError) as exc:
            raise PackageError(f"{path.relative_to(ROOT)} needs numeric square dimensions") from exc
    require(math.isfinite(width) and math.isfinite(height), f"{path.relative_to(ROOT)} dimensions must be finite")
    return width, height


def validate_manifest(manifest: dict) -> None:
    name = manifest.get("name")
    version = manifest.get("version")
    description = manifest.get("description")
    require(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name) is not None,
            "manifest name must match OpenAI's package-name rules")
    require(name == ARCHIVE_ROOT, f"manifest name must remain {ARCHIVE_ROOT!r}")
    require(isinstance(version, str) and len(version) <= 64 and
            re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version) is not None,
            "manifest version must be semantic versioning")
    release = release_runtime_constants()
    require(version == release["CURRENT_VERSION"],
            "Codex manifest version must match release_evidence.CURRENT_VERSION")
    require(release["PREVIOUS_VERSION"] == "2.17.20",
            "forward repair must preserve v2.17.20 as the last released generation")
    require(release["COMPATIBILITY_PREVIOUS_VERSION"] == "2.18.0",
            "forward repair compatibility N-1 must be v2.18.0")
    require(release["HISTORICAL_GRAPH_REVISION"] ==
            "2757822ede49177fc52de8c173302286364d6206",
            "forward repair must preserve historical graph revision 2757822e")
    require(isinstance(description, str) and 0 < len(description) <= 1024,
            "manifest description must be 1-1024 characters")
    author = manifest.get("author")
    require(isinstance(author, dict) and isinstance(author.get("name"), str) and author["name"],
            "author.name is required")

    interface = manifest.get("interface")
    require(isinstance(interface, dict), "interface is required")
    limits = {
        "displayName": 30,
        "shortDescription": 30,
        "longDescription": 4000,
        "developerName": 80,
    }
    for field, limit in limits.items():
        value = interface.get(field)
        require(isinstance(value, str) and value.strip() == value and value,
                f"interface.{field} is required and cannot have outer whitespace")
        require(len(value) <= limit, f"interface.{field} exceeds the final submission limit of {limit}")
        if field != "longDescription":
            require("\n" not in value and "\r" not in value, f"interface.{field} must fit on one line")
    require(interface.get("developerName") == author.get("name"),
            "author.name and interface.developerName must match")
    require(interface.get("category") in CATEGORIES, "interface.category is not an OpenAI directory category")

    capabilities = interface.get("capabilities")
    require(isinstance(capabilities, list) and 0 < len(capabilities) <= 20,
            "interface.capabilities must contain 1-20 entries")
    for capability in capabilities:
        require(isinstance(capability, str) and capability and len(capability) <= 120 and "\n" not in capability,
                "each capability must be a non-empty single line of at most 120 characters")

    prompts = interface.get("defaultPrompt")
    require(isinstance(prompts, list) and 0 < len(prompts) <= 3,
            "interface.defaultPrompt must contain 1-3 prompts")
    normalized_prompts: set[str] = set()
    for prompt in prompts:
        require(isinstance(prompt, str) and prompt and len(prompt) <= 128 and "\n" not in prompt,
                "each starter prompt must be a non-empty single line of at most 128 characters")
        require("@" not in prompt, "starter prompts must not contain app @mentions")
        normalized = " ".join(unicodedata.normalize("NFKC", prompt).split()).casefold()
        require(normalized not in normalized_prompts, "starter prompts must be unique")
        normalized_prompts.add(normalized)

    for field in ("websiteURL", "privacyPolicyURL", "termsOfServiceURL"):
        require(valid_https_url(interface.get(field)), f"interface.{field} must be a valid public HTTPS URL")
    if "supportURL" in interface:
        require(valid_https_url(interface.get("supportURL")),
                "interface.supportURL must be a valid public HTTPS URL when provided")

    for field, background in (("brandColor", "#FFFFFF"), ("brandColorDark", "#212121")):
        color = interface.get(field)
        if color is None:
            continue
        require(isinstance(color, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", color) is not None,
                f"interface.{field} must be a six-digit hex color")
        require(contrast_ratio(color, background) >= 2.0,
                f"interface.{field} does not meet OpenAI's 2:1 contrast requirement")

    for field in ("logo", "composerIcon"):
        value = interface.get(field)
        require(isinstance(value, str) and value.startswith("./"), f"interface.{field} must start with ./")
        path = (ROOT / value[2:]).resolve()
        require(path.is_relative_to(ROOT) and path.is_file(), f"interface.{field} must reference a packaged file")
        require(path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".svg"},
                f"interface.{field} has an unsupported image type")
        require(path.stat().st_size <= 5 * 1024 * 1024, f"interface.{field} exceeds 5 MiB")
        if path.suffix.lower() == ".svg":
            width, height = svg_dimensions(path)
            require(width == height and width >= 48,
                    f"interface.{field} must be square and at least 48x48")

    require(manifest.get("skills") == "./skills/", "skills must point to ./skills/")
    require("apps" not in manifest and "mcpServers" not in manifest,
            "skills-only packages cannot declare apps or MCP servers")
    require("hostNative" not in manifest and
            "hostNativeRuntime" not in manifest,
            "Codex manifest cannot declare unsupported host-native fields")
    require("screenshots" not in interface, "skills-only packages cannot declare screenshots")


def parse_frontmatter(path: Path) -> tuple[str, str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n(.*)\Z", text, re.DOTALL)
    require(match is not None, f"{path.relative_to(ROOT)} needs closed YAML front matter")
    frontmatter, body = match.groups()
    name_match = re.search(r"^name:\s*(.+?)\s*$", frontmatter, re.MULTILINE)
    description_match = re.search(r"^description:\s*(.+?)\s*$", frontmatter, re.MULTILINE)
    require(name_match is not None, f"{path.relative_to(ROOT)} is missing name")
    require(description_match is not None, f"{path.relative_to(ROOT)} is missing description")
    name = name_match.group(1).strip().strip("'\"")
    raw_description = description_match.group(1).strip()
    if raw_description.startswith('"') and raw_description.endswith('"'):
        try:
            description = json.loads(raw_description)
        except json.JSONDecodeError as exc:
            raise PackageError(f"{path.relative_to(ROOT)} has malformed quoted description") from exc
    else:
        description = raw_description.strip("'")
    return name, description, body


def validate_skills(manifest: dict) -> list[Path]:
    skill_dirs = sorted(path for path in (ROOT / "skills").iterdir() if path.is_dir())
    require(skill_dirs, "skills/ must contain at least one skill")
    names: set[str] = set()
    for skill_dir in skill_dirs:
        require(not skill_dir.name.startswith("."), f"hidden skill directory is not allowed: {skill_dir.name}")
        skill_file = skill_dir / "SKILL.md"
        require(skill_file.is_file(), f"{skill_dir.relative_to(ROOT)} is missing SKILL.md")
        name, description, body = parse_frontmatter(skill_file)
        require(name and name not in names, f"duplicate or empty skill name: {name!r}")
        require(len(f"{manifest['name']}:{name}") <= 64, f"skill identity is too long: {name}")
        require(0 < len(description) <= 1024, f"skill description must be 1-1024 characters: {name}")
        require(body.strip(), f"skill body is empty: {name}")
        names.add(name)
    return skill_dirs


def add_tree(files: set[Path], base: Path, predicate) -> None:
    require(base.is_dir(), f"required directory is missing: {base.relative_to(ROOT)}")
    for path in base.rglob("*"):
        if not path.is_file() or not predicate(path):
            continue
        files.add(path)


def expected_skill_files(root: Path) -> tuple[str, ...]:
    """Return every installable skill member for archive validation.

    Skills are executable host inputs.  Checking only for one ``SKILL.md``
    allowed a valid-looking archive to omit the dynamically selected Design
    role or one of its references.  Derive the closed expectation from the
    source generation being packaged, while retaining the intentional Codex
    exclusion for the Claude-only tag surface.
    """
    base = root / "skills"
    require(base.is_dir(), "release source is missing skills/")
    return tuple(sorted(
        path.relative_to(root).as_posix()
        for path in base.rglob("*")
        if path.is_file()
        and path.relative_to(base).parts[0] not in OPENAI_EXCLUDED_SKILLS
    ))


def package_files(manifest: dict) -> list[Path]:
    validate_manifest(manifest)
    load_hook_manifest()
    files: set[Path] = {MANIFEST_PATH}
    for relative in REQUIRED_FILES:
        path = ROOT / relative
        require(path.is_file(), f"required file is missing: {relative}")
        files.add(path)
    for relative in STAGE_RUNTIME_FILES:
        path = ROOT / relative
        require(path.is_file(), f"stage runtime member is missing: {relative}")
        files.add(path)
    for relative in CANONICAL_AUTHORITY_FILES:
        path = ROOT / relative
        require(path.is_file(), f"canonical authority is missing: {relative}")
        files.add(path)

    add_tree(files, ROOT / "assets", lambda path: path.suffix.lower() in {".svg", ".png", ".jpg", ".jpeg", ".webp"})
    add_tree(
        files,
        ROOT / "skills",
        lambda path: path.relative_to(ROOT / "skills").parts[0]
        not in OPENAI_EXCLUDED_SKILLS,
    )
    add_tree(files, ROOT / "agents", lambda path: path.suffix == ".md")
    add_tree(files, ROOT / "discipline", lambda path: path.suffix == ".md")
    # Ship the public documentation as a complete set. Skills and the stdlib
    # runtime cite docs/* directly; a package that validates only repository
    # existence can still strand an installed user with dead pointers.
    add_tree(
        files,
        ROOT / "docs",
        lambda path: path.suffix == ".md" or
        path.relative_to(ROOT / "docs").as_posix() ==
        "assets/taskplane-cowork-flow.gif",
    )
    add_tree(files, ROOT / "taskplane", lambda path: path.parent == ROOT / "taskplane" and path.suffix == ".py")
    add_tree(files, ROOT / "lenses", lambda path: path.suffix == ".md" or path.name == "catalog.json")

    for path in files:
        relative = path.relative_to(ROOT)
        require(not path.is_symlink(), f"symlinks are not allowed in the package: {relative}")
        require(path.is_file(), f"package member is not a regular file: {relative}")
        mode = path.stat(follow_symlinks=False).st_mode
        require(stat.S_ISREG(mode), f"package member is not a regular file: {relative}")
        require(path.stat().st_size <= 100 * 1024 * 1024, f"package member exceeds 100 MiB: {relative}")

    declared_assets = {
        (ROOT / manifest["interface"][field][2:]).resolve()
        for field in ("logo", "composerIcon")
    }
    require(declared_assets.issubset({path.resolve() for path in files}), "declared brand assets are not packaged")
    return sorted(files, key=lambda path: path.relative_to(ROOT).as_posix())


def write_zip(files: list[Path], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in files:
                relative = path.relative_to(ROOT).as_posix()
                info = zipfile.ZipInfo(f"{ARCHIVE_ROOT}/{relative}", FIXED_ZIP_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()


def validate_archive(
    path: Path,
    *,
    expected_version: str | None = None,
    release_surface_root: Path | None = None,
    stage_runtime_files: tuple[str, ...] = STAGE_RUNTIME_FILES,
    release_surface_files: tuple[str, ...] = RELEASE_SURFACE_FILES,
    canonical_authority_files: tuple[str, ...] = CANONICAL_AUTHORITY_FILES,
) -> tuple[int, int]:
    require(path.is_file() and zipfile.is_zipfile(path), "output is not a readable ZIP")
    require(path.stat().st_size <= 100 * 1024 * 1024, "compressed ZIP exceeds 100 MB")
    normalized: set[str] = set()
    uncompressed = 0
    expected_surface_root = release_surface_root or ROOT
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        require(0 < len(members) <= 5000, "ZIP must contain 1-5000 entries")
        roots: set[str] = set()
        names = {member.filename for member in members}
        for member in members:
            name = member.filename
            require(name and name == name.strip(), f"unsafe archive path: {name!r}")
            require("\\" not in name and not name.startswith("/"), f"unsafe archive path: {name}")
            pure = PurePosixPath(name)
            require(".." not in pure.parts and "" not in pure.parts, f"unsafe archive path: {name}")
            require(len(pure.parts) <= 20, f"archive path is deeper than 20 segments: {name}")
            require(not member.is_dir(), f"directory entries are unnecessary in the upload: {name}")
            require(member.flag_bits & 0x1 == 0, f"encrypted archive member is not allowed: {name}")
            key = unicodedata.normalize("NFC", name).casefold()
            require(key not in normalized, f"archive path normalization collision: {name}")
            normalized.add(key)
            roots.add(pure.parts[0])
            require(member.file_size <= 100 * 1024 * 1024, f"archive member exceeds 100 MiB: {name}")
            uncompressed += member.file_size
        require(roots == {ARCHIVE_ROOT}, "ZIP must have exactly one top-level taskplane/ directory")
        require(f"{ARCHIVE_ROOT}/.codex-plugin/plugin.json" in names, "ZIP is missing the Codex manifest")
        try:
            packaged_manifest = json.loads(
                archive.read(f"{ARCHIVE_ROOT}/.codex-plugin/plugin.json"))
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise PackageError(
                "ZIP contains an unreadable Codex manifest") from exc
        require(isinstance(packaged_manifest, dict),
                "ZIP Codex manifest root must be an object")
        require("hostNative" not in packaged_manifest and
                "hostNativeRuntime" not in packaged_manifest,
                "ZIP Codex manifest contains unsupported host-native fields")
        require(any(re.fullmatch(rf"{ARCHIVE_ROOT}/skills/[^/]+/SKILL\.md", name) for name in names),
                "ZIP has no valid skills/<skill>/SKILL.md")
        require(not any(name.endswith("/.app.json") or name.endswith("/.mcp.json") for name in names),
                "skills-only ZIP must not contain app or MCP configuration")
        require(not any(f"{ARCHIVE_ROOT}/.claude-plugin/" in name for name in names),
                "OpenAI upload must not contain the Claude manifest")
        require(not any(f"{ARCHIVE_ROOT}/skills/{skill}/" in name
                        for skill in OPENAI_EXCLUDED_SKILLS for name in names),
                "OpenAI upload must not contain host-specific Claude Tag skills")
        for required in ("README.md", "CHANGELOG.md"):
            require(f"{ARCHIVE_ROOT}/{required}" in names,
                    f"ZIP is missing {required}")
        for required in HOOK_FILES:
            require(f"{ARCHIVE_ROOT}/{required}" in names,
                    f"ZIP is missing installed hook runtime member {required}")
        for required in stage_runtime_files:
            require(f"{ARCHIVE_ROOT}/{required}" in names,
                    f"ZIP is missing stage runtime member {required}")
        for required in expected_skill_files(expected_surface_root):
            require(f"{ARCHIVE_ROOT}/{required}" in names,
                    f"ZIP is missing installable skill member {required}")
        package_version = (
            expected_version or release_runtime_constants()["CURRENT_VERSION"]
        )
        require(packaged_manifest.get("version") == package_version,
                "ZIP Codex manifest does not match release runtime version")
        for required in canonical_authority_files:
            member = f"{ARCHIVE_ROOT}/{required}"
            require(member in names,
                    f"ZIP is missing canonical authority {required}")
            try:
                authority = json.loads(archive.read(member))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise PackageError(
                    f"ZIP canonical authority is unreadable: {required}") from exc
            require(isinstance(authority, Mapping),
                    f"ZIP canonical authority is not an object: {required}")
            is_operational_settings = required.endswith(
                "operational-settings.json")
            expected_schema = (
                "taskplane.operational-settings/v2"
                if is_operational_settings
                else "taskplane.operational-settings-inventory/v1"
            )
            require(authority.get("schema") == expected_schema,
                    f"ZIP canonical authority has an invalid schema: {required}")
            if is_operational_settings:
                expected_authority = load_json_object(
                    expected_surface_root / required,
                    "canonical operational settings authority",
                )
                require(
                    authority == expected_authority,
                    f"ZIP canonical authority does not match source: {required}",
                )
        for required in release_surface_files:
            member = f"{ARCHIVE_ROOT}/{required}"
            require(member in names,
                    f"ZIP is missing forward-release surface {required}")
            try:
                body = archive.read(member).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise PackageError(
                    f"ZIP forward-release surface is not UTF-8: {required}") from exc
            require(bool(body.strip()),
                    f"ZIP forward-release surface is empty: {required}")
            if required == "taskplane/release_evidence.py":
                try:
                    tree = ast.parse(body, filename=member)
                except SyntaxError as exc:
                    raise PackageError(
                        "ZIP release runtime is not valid Python") from exc
                assignments = {
                    node.targets[0].id: ast.literal_eval(node.value)
                    for node in tree.body
                    if isinstance(node, ast.Assign)
                    and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "CURRENT_VERSION"
                }
                require(assignments.get("CURRENT_VERSION") == package_version,
                        "ZIP release runtime and manifest versions disagree")
        try:
            hook_manifest = json.loads(
                archive.read(f"{ARCHIVE_ROOT}/hooks/hooks.json"))
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise PackageError("ZIP contains an unreadable hooks/hooks.json") from exc
        validate_hook_manifest(hook_manifest)
        require(
            f"{ARCHIVE_ROOT}/docs/assets/taskplane-cowork-flow.gif" in names,
            "ZIP is missing the README flow-guide GIF",
        )
        doc_ref = re.compile(r"(?<![A-Za-z0-9_./-])(docs/[A-Za-z0-9_./-]+\.md)")
        referenced_docs: set[str] = set()
        source_prefixes = (f"{ARCHIVE_ROOT}/skills/",
                           f"{ARCHIVE_ROOT}/taskplane/",
                           f"{ARCHIVE_ROOT}/agents/",
                           f"{ARCHIVE_ROOT}/discipline/")
        for member in members:
            is_source = member.filename == f"{ARCHIVE_ROOT}/README.md" or \
                member.filename.startswith(source_prefixes)
            if not is_source or not member.filename.endswith((".md", ".py")):
                continue
            try:
                body = archive.read(member).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise PackageError(
                    f"referencing source is not UTF-8: {member.filename}") from exc
            referenced_docs.update(doc_ref.findall(body))
        missing_docs = sorted(
            rel for rel in referenced_docs
            if f"{ARCHIVE_ROOT}/{rel}" not in names)
        require(not missing_docs,
                "ZIP has dead docs references from shipped skills/runtime: "
                + ", ".join(missing_docs))
    require(uncompressed <= 512 * 1024 * 1024, "extracted ZIP exceeds 512 MiB")
    return len(members), uncompressed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--release-green-receipt", type=Path,
                        help="optionally validate release authority for this exact source SHA")
    parser.add_argument("--write-compatibility-receipt", type=Path,
                        help="execute and write the current/last-released matrix")
    # D-0010 — same rule as the Claude archive; see scripts/release_provenance.py
    parser.add_argument("--allow-dirty", action="store_true",
                        help="package over uncommitted edits; the provenance "
                             "record is stamped verified_source: false and "
                             "the archive must not be released")
    args = parser.parse_args()

    try:
        if args.write_compatibility_receipt is not None:
            receipt_path = args.write_compatibility_receipt.resolve()
            require_approved_output(receipt_path, approved_output_roots())
            receipt = produce_release_compatibility_receipt(
                expected_source_sha=git_head(),
            )
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            receipt_path.write_text(
                json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            print(f"Release compatibility receipt ready: {receipt_path}")
            return 0
        if args.release_green_receipt is not None:
            validate_release_package_authority(
                release_green=load_json_object(args.release_green_receipt,
                                               "release-green receipt"),
                expected_source_sha=git_head(),
                now=time.time(),
            )
        manifest = load_manifest()
        validate_manifest(manifest)
        validate_skills(manifest)
        files = package_files(manifest)
        output_dir = args.output_dir.resolve()
        require_approved_output(output_dir, approved_output_roots())
        output = output_dir / f"{manifest['name']}-{manifest['version']}-openai.zip"
        write_zip(files, output)
        member_count, uncompressed = validate_archive(output)
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        checksum = output.with_suffix(output.suffix + ".sha256")
        checksum.write_text(f"{digest}  {output.name}\n", encoding="utf-8")
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import release_provenance as prov
        try:
            prov_path = prov.write(ROOT, output, digest,
                                   allow_dirty=args.allow_dirty, kind="openai")
        except prov.ProvenanceError as exc:
            output.unlink(missing_ok=True)
            checksum.unlink(missing_ok=True)
            raise PackageError(str(exc)) from exc
    except (OSError, PackageError) as exc:
        print(f"OpenAI package validation failed: {exc}", file=sys.stderr)
        return 1

    print(f"OpenAI package ready: {output}")
    print(f"version: {manifest['version']} (unchanged)")
    print(f"files: {member_count}")
    print(f"compressed_bytes: {output.stat().st_size}")
    print(f"uncompressed_bytes: {uncompressed}")
    print(f"sha256: {digest}")
    print(f"checksum: {checksum}")
    print(f"provenance: {prov_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
