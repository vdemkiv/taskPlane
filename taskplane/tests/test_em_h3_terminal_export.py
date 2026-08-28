"""H-32 exact-candidate successor export and stale-SHA refusal proofs."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

from taskplane import terminal_truth


ROOT = Path(__file__).resolve().parents[2]
EXPORT_ROOT = ROOT / "exports" / "terminal" / "r0013"
STALE_SHA = "106af4631ab5b5c041055b9b9b918d78a18ae50b"


def _verifier():
    path = EXPORT_ROOT / "verify.py"
    spec = importlib.util.spec_from_file_location("_em_h3_terminal_export", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
    ).strip()


def _surface_documents(candidate_sha: str) -> dict[str, dict]:
    identity = {
        "full_source_sha": candidate_sha,
        "terminal_status": terminal_truth.TERMINAL_STATUS,
        "requirement_id": "R-0013",
        "design_fingerprint": "1" * 64,
        "plan_fingerprint": "2" * 64,
        "graph_fingerprint": "3" * 64,
        "native_usage_fingerprint": "4" * 64,
        "candidate_wiring_fingerprint": "5" * 64,
        "full_suite_fingerprint": "6" * 64,
        "predecessor_fingerprint": "0" * 64,
    }
    return {
        surface_id: terminal_truth.prepare_terminal_surface(
            surface_id,
            identity,
            {"surface": surface_id, "redacted": True},
        )
        for surface_id in terminal_truth.SURFACE_IDS
    }


def _selector_receipts(template: dict, candidate_sha: str) -> dict[str, dict]:
    return {
        selector: {
            "candidate_sha": candidate_sha,
            "outcome": "passed",
            "output_sha256": f"{index + 1:064x}",
        }
        for index, selector in enumerate(template["required_selectors"])
    }


def _prepared_candidate(verifier, template: dict, candidate_sha: str) -> dict:
    return verifier.prepare_candidate_manifest(
        template,
        candidate_sha=candidate_sha,
        surface_documents=_surface_documents(candidate_sha),
        selector_receipts=_selector_receipts(template, candidate_sha),
    )


def test_h32_terminal_export_matches_current_candidate_sha():
    verifier = _verifier()
    template = verifier.load_template(EXPORT_ROOT / "successor-template.json")
    head = _head()
    candidate = _prepared_candidate(verifier, template, head)
    documents = _surface_documents(head)
    receipts = _selector_receipts(template, head)

    assert verifier.verify_candidate_manifest(
        template,
        candidate,
        expected_sha=head,
        surface_documents=documents,
        selector_receipts=receipts,
    )["candidate_sha"] == head

    stale = copy.deepcopy(candidate)
    stale["candidate_sha"] = STALE_SHA
    with pytest.raises(verifier.TerminalExportError, match="stale SHA"):
        verifier.verify_candidate_manifest(
            template,
            stale,
            expected_sha=head,
            surface_documents=documents,
            selector_receipts=receipts,
        )

    tombstone = json.loads(
        (EXPORT_ROOT / f"{STALE_SHA}.json").read_text(encoding="utf-8")
    )
    assert verifier.validate_tombstone(
        tombstone, expected_template=template
    )["active"] is False
    assert tombstone["schema"] != terminal_truth.TERMINAL_PROJECTION_SCHEMA


def test_h32_successor_binds_all_terminal_surfaces():
    verifier = _verifier()
    template = verifier.load_template(EXPORT_ROOT / "successor-template.json")
    head = _head()
    assert tuple(template["surface_ids"]) == terminal_truth.SURFACE_IDS

    documents = _surface_documents(head)
    documents.pop("run_journal")
    with pytest.raises(verifier.TerminalExportError, match="all terminal surfaces"):
        verifier.prepare_candidate_manifest(
            template,
            candidate_sha=head,
            surface_documents=documents,
            selector_receipts=_selector_receipts(template, head),
        )

    documents = _surface_documents(head)
    documents["public_report"]["identity"]["full_source_sha"] = STALE_SHA
    with pytest.raises(verifier.TerminalExportError, match="stale SHA"):
        verifier.prepare_candidate_manifest(
            template,
            candidate_sha=head,
            surface_documents=documents,
            selector_receipts=_selector_receipts(template, head),
        )

    candidate = _prepared_candidate(verifier, template, head)
    candidate["surfaces"]["git_head"]["candidate_sha"] = STALE_SHA
    with pytest.raises(verifier.TerminalExportError, match="binding is stale"):
        verifier.verify_candidate_manifest(
            template,
            candidate,
            expected_sha=head,
            surface_documents=_surface_documents(head),
            selector_receipts=_selector_receipts(template, head),
        )


def test_h32_successor_binds_required_selectors():
    verifier = _verifier()
    template = verifier.load_template(EXPORT_ROOT / "successor-template.json")
    head = _head()
    receipts = _selector_receipts(template, head)
    receipts.pop(template["required_selectors"][0])
    with pytest.raises(verifier.TerminalExportError, match="all required selectors"):
        verifier.prepare_candidate_manifest(
            template,
            candidate_sha=head,
            surface_documents=_surface_documents(head),
            selector_receipts=receipts,
        )

    receipts = _selector_receipts(template, head)
    receipts[template["required_selectors"][0]]["outcome"] = "failed"
    with pytest.raises(verifier.TerminalExportError, match="did not pass"):
        verifier.prepare_candidate_manifest(
            template,
            candidate_sha=head,
            surface_documents=_surface_documents(head),
            selector_receipts=receipts,
        )


def test_h32_successor_does_not_invent_terminal_or_release_authority():
    verifier = _verifier()
    template = verifier.load_template(EXPORT_ROOT / "successor-template.json")
    head = _head()
    candidate = _prepared_candidate(verifier, template, head)
    documents = _surface_documents(head)
    receipts = _selector_receipts(template, head)

    assert candidate["status"] == "prepared-not-authoritative"
    assert candidate["evidence_state"] == {
        "terminal_authority": "not-minted",
        "full_suite": "not-recorded",
        "release": "not-granted",
        "main_mutation": "not-granted",
        "publication": "not-granted",
    }

    forged = copy.deepcopy(candidate)
    forged["evidence_state"]["full_suite"] = "passed"
    with pytest.raises(verifier.TerminalExportError, match="unavailable authority"):
        verifier.verify_candidate_manifest(
            template,
            forged,
            expected_sha=head,
            surface_documents=documents,
            selector_receipts=receipts,
        )

    forged = copy.deepcopy(candidate)
    forged["status"] = "complete"
    with pytest.raises(verifier.TerminalExportError, match="falsely claims"):
        verifier.verify_candidate_manifest(
            template,
            forged,
            expected_sha=head,
            surface_documents=documents,
            selector_receipts=receipts,
        )


def test_h32_repository_materialization_requires_clean_exact_head(tmp_path):
    verifier = _verifier()
    template = verifier.load_template(EXPORT_ROOT / "successor-template.json")
    repository = tmp_path / "candidate"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    tracked = repository / "candidate.txt"
    tracked.write_text("committed\n", encoding="utf-8")
    subprocess.run(["git", "add", "candidate.txt"], cwd=repository, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=H3-D fixture",
            "-c",
            "user.email=h3-d@example.invalid",
            "commit",
            "-qm",
            "candidate",
        ],
        cwd=repository,
        check=True,
    )
    head = verifier.clean_repository_head(repository)
    candidate = verifier.prepare_repository_candidate(
        template,
        repository=repository,
        surface_documents=_surface_documents(head),
        selector_receipts=_selector_receipts(template, head),
    )
    assert candidate["candidate_sha"] == head

    tracked.write_text("dirty\n", encoding="utf-8")
    with pytest.raises(verifier.TerminalExportError, match="clean and committed"):
        verifier.prepare_repository_candidate(
            template,
            repository=repository,
            surface_documents=_surface_documents(head),
            selector_receipts=_selector_receipts(template, head),
        )
