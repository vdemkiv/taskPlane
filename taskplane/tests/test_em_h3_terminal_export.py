"""H-32 exact-candidate successor and immutable-history proofs."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from taskplane import delivery_ports, terminal_truth


ROOT = Path(__file__).resolve().parents[2]
EXPORT_ROOT = ROOT / "exports" / "terminal" / "r0013"
STALE_SHA = "106af4631ab5b5c041055b9b9b918d78a18ae50b"
ORIGINAL_SHA256 = "1e41748672f8d492823824b6e2103ac87484f2687389d80567f231ea4151c459"
GIT = "/usr/bin/git"


def _verifier(path: Path = EXPORT_ROOT / "verify.py"):
    spec = importlib.util.spec_from_file_location(
        f"_em_h3_terminal_export_{hash(path)}", path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repository: Path, *args: str) -> str:
    return subprocess.check_output(
        [GIT, *args], cwd=repository, text=True, encoding="utf-8"
    ).strip()


def _commit(repository: Path, message: str) -> str:
    subprocess.run([GIT, "add", "-A"], cwd=repository, check=True)
    subprocess.run(
        [
            GIT,
            "-c", "user.name=H3-D fixture",
            "-c", "user.email=h3-d@example.invalid",
            "commit", "-qm", message,
        ],
        cwd=repository,
        check=True,
    )
    return _git(repository, "rev-parse", "HEAD")


def _fixture_repository(tmp_path: Path):
    repository = tmp_path / "candidate"
    export_root = repository / "exports" / "terminal" / "r0013"
    tests_root = repository / "taskplane" / "tests"
    export_root.mkdir(parents=True)
    tests_root.mkdir(parents=True)
    for name in (
        "verify.py",
        "successor-template.json",
        f"{STALE_SHA}.json",
        f"{STALE_SHA}.tombstone.json",
    ):
        shutil.copyfile(EXPORT_ROOT / name, export_root / name)
    shutil.copyfile(Path(__file__), tests_root / Path(__file__).name)
    (repository / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    subprocess.run([GIT, "init", "-q", str(repository)], check=True)
    head = _commit(repository, "candidate")
    verifier = _verifier(export_root / "verify.py")
    return repository, export_root, verifier, head


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


def _passing_selector_runner(calls: list[tuple[str, ...]] | None = None):
    def run(snapshot, argv, environment):
        assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
        if calls is not None:
            calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, b"passed\n", b"")
    return run


def _prepare(tmp_path: Path, monkeypatch):
    repository, export_root, verifier, head = _fixture_repository(tmp_path)
    coordinator = terminal_truth.TerminalCoordinator(tmp_path / "authority")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        coordinator, "_run_selector", _passing_selector_runner(calls)
    )
    manifest = verifier.prepare_repository_candidate(
        template_path=export_root / "successor-template.json",
        repository=repository,
        surface_documents=_surface_documents(head),
        coordinator=coordinator,
    )
    return repository, export_root, verifier, coordinator, manifest, calls, head


def test_h32_historical_projection_and_separate_tombstone_are_exact(tmp_path):
    repository, export_root, verifier, _ = _fixture_repository(tmp_path)
    del repository
    original_path = export_root / f"{STALE_SHA}.json"
    tombstone_path = export_root / f"{STALE_SHA}.tombstone.json"
    template = verifier.load_template(export_root / "successor-template.json")
    tombstone = json.loads(tombstone_path.read_text(encoding="utf-8"))

    assert hashlib.sha256(original_path.read_bytes()).hexdigest() == ORIGINAL_SHA256
    assert json.loads(original_path.read_text(encoding="utf-8"))["schema"] == \
        terminal_truth.TERMINAL_PROJECTION_SCHEMA
    assert verifier.validate_tombstone(
        tombstone,
        expected_template=template,
        original_path=original_path,
        tombstone_path=tombstone_path,
    )["active"] is False

    with pytest.raises(verifier.TerminalExportError, match="misnamed"):
        verifier.validate_tombstone(
            tombstone,
            expected_template=template,
            original_path=original_path,
            tombstone_path=export_root / "tombstone.json",
        )
    altered = dict(tombstone, reason="caller-selected")
    with pytest.raises(verifier.TerminalExportError, match="schema or reason"):
        verifier.validate_tombstone(
            altered,
            expected_template=template,
            original_path=original_path,
            tombstone_path=tombstone_path,
        )


def test_h32_terminal_export_matches_current_candidate_sha(tmp_path, monkeypatch):
    repository, export_root, verifier, _, manifest, calls, head = _prepare(
        tmp_path, monkeypatch
    )
    verified = verifier.verify_candidate_manifest(
        template_path=export_root / "successor-template.json",
        manifest=manifest,
        expected_sha=head,
    )
    template = verifier.load_template(export_root / "successor-template.json")

    assert verified["candidate_sha"] == head
    assert [row["selector"] for row in verified["selectors"]] == \
        template["required_selectors"]
    assert len(calls) == len(template["required_selectors"])
    assert verified["evidence_state"] == verifier.PREPARED_EVIDENCE_STATE
    assert verified["status"] == "prepared-not-authoritative"
    assert _git(repository, "status", "--porcelain=v1", "--untracked-files=all") == ""

    with pytest.raises(verifier.TerminalExportError, match="stale or invalid"):
        verifier.verify_candidate_manifest(
            template_path=export_root / "successor-template.json",
            manifest=manifest,
            expected_sha=STALE_SHA,
        )


def test_h32_selector_receipts_reject_fabrication_replay_redigest_and_tamper(
    tmp_path, monkeypatch
):
    _, export_root, verifier, coordinator, manifest, _, head = _prepare(
        tmp_path, monkeypatch
    )
    template_path = export_root / "successor-template.json"
    with pytest.raises(verifier.TerminalExportError, match="live exact-candidate"):
        verifier.verify_candidate_manifest(
            template_path=template_path,
            manifest=dict(manifest),
            expected_sha=head,
        )
    with pytest.raises(TypeError, match="not serializable"):
        copy.deepcopy(manifest)

    foreign = terminal_truth.TerminalCoordinator(tmp_path / "foreign-authority")
    with pytest.raises(terminal_truth.TerminalTruthError, match="live exact-candidate"):
        foreign.validate_exact_candidate_export(
            manifest,
            expected_sha=head,
            expected_template_sha256=manifest["template_sha256"],
        )

    receipt = manifest._selector_receipts[0]
    assert receipt._path.stem == receipt["fingerprint"]
    assert json.loads(receipt._path.read_text(encoding="utf-8")) == receipt
    assert receipt["git_executable_sha256"] == manifest._snapshot.git_executable_sha256
    assert receipt["git_environment_fingerprint"] == \
        manifest._snapshot.environment_fingerprint
    original = dict(receipt)
    with pytest.raises(TypeError, match="immutable"):
        receipt["output_sha256"] = "f" * 64
    # Exercise a hostile caller that deliberately bypasses the public mapping
    # API; the coordinator-owned seal must still reject it.
    dict.__setitem__(receipt, "output_sha256", "f" * 64)
    unsigned = {key: value for key, value in receipt.items() if key != "fingerprint"}
    dict.__setitem__(receipt, "fingerprint", terminal_truth._digest(unsigned))
    with pytest.raises(verifier.TerminalExportError, match="coordinator seal"):
        verifier.verify_candidate_manifest(
            template_path=template_path,
            manifest=manifest,
            expected_sha=head,
        )
    dict.clear(receipt)
    dict.update(receipt, original)

    persisted = receipt._path.read_bytes()
    receipt._path.write_bytes(b"{}")
    with pytest.raises(verifier.TerminalExportError, match="tampered"):
        verifier.verify_candidate_manifest(
            template_path=template_path,
            manifest=manifest,
            expected_sha=head,
        )
    receipt._path.write_bytes(persisted)
    assert coordinator.validate_exact_candidate_export(
        manifest,
        expected_sha=head,
        expected_template_sha256=manifest["template_sha256"],
    )["candidate_sha"] == head


@pytest.mark.parametrize(
    "inventory_mutation", ("zero", "missing", "extra", "reordered", "wrong")
)
def test_h32_coordinated_candidate_selector_rewrite_cannot_redefine_inventory(
    tmp_path, monkeypatch, inventory_mutation
):
    _, export_root, verifier, _, manifest, _, head = _prepare(tmp_path, monkeypatch)
    rows = copy.deepcopy(manifest["selectors"])
    if inventory_mutation == "zero":
        rows = []
    elif inventory_mutation == "missing":
        rows = rows[:-1]
    elif inventory_mutation == "extra":
        rows.append(
            dict(
                rows[-1],
                selector="taskplane/tests/test_em_h3_terminal_export.py::extra",
            )
        )
    elif inventory_mutation == "reordered":
        rows[0], rows[1] = rows[1], rows[0]
    else:
        rows[0]["selector"] = \
            "taskplane/tests/test_em_h3_terminal_export.py::wrong"

    with pytest.raises(TypeError, match="immutable"):
        manifest["selectors"] = rows
    # Reproduce the complete reported attack: change the live object, recompute
    # its public digest, and replace the persisted bytes coherently.
    dict.__setitem__(manifest, "selectors", rows)
    unsigned = {key: value for key, value in manifest.items() if key != "fingerprint"}
    dict.__setitem__(manifest, "fingerprint", terminal_truth._digest(unsigned))
    manifest._path.write_bytes(terminal_truth._canonical_bytes(manifest))

    with pytest.raises(verifier.TerminalExportError, match="coordinator seal"):
        verifier.verify_candidate_manifest(
            template_path=export_root / "successor-template.json",
            manifest=manifest,
            expected_sha=head,
        )


def test_h32_candidate_check_rejects_dirty_untracked_and_head_movement(
    tmp_path, monkeypatch
):
    repository, export_root, verifier, head = _fixture_repository(tmp_path)
    coordinator = terminal_truth.TerminalCoordinator(tmp_path / "authority")
    monkeypatch.setattr(coordinator, "_run_selector", _passing_selector_runner())
    kwargs = {
        "template_path": export_root / "successor-template.json",
        "repository": repository,
        "surface_documents": _surface_documents(head),
        "coordinator": coordinator,
    }

    (repository / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    with pytest.raises(verifier.TerminalExportError, match="untracked"):
        verifier.prepare_repository_candidate(**kwargs)
    (repository / "untracked.txt").unlink()

    template_path = export_root / "successor-template.json"
    original = template_path.read_text(encoding="utf-8")
    template_path.write_text(original + "\n", encoding="utf-8")
    with pytest.raises(verifier.TerminalExportError, match="clean"):
        verifier.prepare_repository_candidate(**kwargs)
    template_path.write_text(original, encoding="utf-8")

    moved = False

    def move_head(snapshot, argv, environment):
        nonlocal moved
        if not moved:
            moved = True
            (repository / "movement.txt").write_text("moved\n", encoding="utf-8")
            _commit(repository, "move head")
        return subprocess.CompletedProcess(argv, 0, b"passed\n", b"")

    monkeypatch.setattr(coordinator, "_run_selector", move_head)
    with pytest.raises(verifier.TerminalExportError, match="changed after|moved"):
        verifier.prepare_repository_candidate(**kwargs)


def test_h32_candidate_check_rejects_symlink_and_external_evidence(
    tmp_path, monkeypatch
):
    repository, export_root, verifier, head = _fixture_repository(tmp_path)
    coordinator = terminal_truth.TerminalCoordinator(tmp_path / "authority")
    monkeypatch.setattr(coordinator, "_run_selector", _passing_selector_runner())
    external = tmp_path / "external-template.json"
    shutil.copyfile(export_root / "successor-template.json", external)

    with pytest.raises(verifier.TerminalExportError, match="inside the candidate"):
        verifier.prepare_repository_candidate(
            template_path=external,
            repository=repository,
            surface_documents=_surface_documents(head),
            coordinator=coordinator,
        )

    template_path = export_root / "successor-template.json"
    template_path.unlink()
    template_path.symlink_to(external)
    with pytest.raises(verifier.TerminalExportError, match="symlink"):
        verifier.prepare_repository_candidate(
            template_path=template_path,
            repository=repository,
            surface_documents=_surface_documents(head),
            coordinator=coordinator,
        )


def test_h32_successor_binds_all_surfaces_and_rejects_stale_surface(
    tmp_path, monkeypatch
):
    repository, export_root, verifier, head = _fixture_repository(tmp_path)
    coordinator = terminal_truth.TerminalCoordinator(tmp_path / "authority")
    monkeypatch.setattr(coordinator, "_run_selector", _passing_selector_runner())
    documents = _surface_documents(head)
    documents.pop("run_journal")
    with pytest.raises(verifier.TerminalExportError, match="all terminal surfaces"):
        verifier.prepare_repository_candidate(
            template_path=export_root / "successor-template.json",
            repository=repository,
            surface_documents=documents,
            coordinator=coordinator,
        )

    documents = _surface_documents(head)
    documents["public_report"]["identity"]["full_source_sha"] = STALE_SHA
    with pytest.raises(verifier.TerminalExportError, match="another SHA"):
        verifier.prepare_repository_candidate(
            template_path=export_root / "successor-template.json",
            repository=repository,
            surface_documents=documents,
            coordinator=coordinator,
        )


def test_h32_verifier_is_wired_to_terminal_coordinator_consumer(
    tmp_path, monkeypatch
):
    repository, export_root, verifier, head = _fixture_repository(tmp_path)
    coordinator = terminal_truth.TerminalCoordinator(tmp_path / "authority")

    def refuse(**kwargs):
        del kwargs
        raise terminal_truth.TerminalTruthError("consumer", "consumer reached")

    monkeypatch.setattr(coordinator, "compose_exact_candidate_export", refuse)
    with pytest.raises(verifier.TerminalExportError, match="consumer reached"):
        verifier.prepare_repository_candidate(
            template_path=export_root / "successor-template.json",
            repository=repository,
            surface_documents=_surface_documents(head),
            coordinator=coordinator,
        )
