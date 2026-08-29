"""Exact-candidate integration proof for the complete R-0002 H3 surface.

H3-C entered this join under an attributed retention exception.  This file
therefore proves the implemented privacy paths and consumes that exception;
it never upgrades H-23 or H-25 to independently-green evidence.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "taskplane"))

from taskplane import dashboard, terminal_truth  # noqa: E402
import depgraph  # noqa: E402
from taskplane import taskplane_lite as tp  # noqa: E402
from taskplane.command_runtime import (  # noqa: E402
    COMMAND_RETENTION_SECONDS,
    CommandRuntime,
)
from taskplane.delivery_ports import TrustedGitInspector  # noqa: E402
from taskplane.host_native import HostSurfaceSnapshot  # noqa: E402


H3C_CANDIDATE = "9b85aa9b578e5210eb31d5f9e6faff916a694d93"
DISPOSITION_PATH = "design/backlog/r0002-h3c-retention-exceptions.md"
DISPOSITION_SHA256 = \
    "bd40d659569919abe09b797cf7df66c81f7e1e73ad425efc48dc417f52d550a5"
TERMINAL_TEMPLATE = "exports/terminal/r0013/successor-template.json"
TERMINAL_VERIFIER = "exports/terminal/r0013/verify.py"

# Bind the integration proof to its production producers, leaf evidence, and
# the attributed exception.  A green leaf from another revision cannot be
# mixed with this join candidate.
EXACT_CANDIDATE_INPUTS = (
    "design/contract.json",
    "plan/tasks.json",
    DISPOSITION_PATH,
    TERMINAL_TEMPLATE,
    TERMINAL_VERIFIER,
    "taskplane/dashboard.py",
    "taskplane/host_native.py",
    "taskplane/depgraph.py",
    "taskplane/command_runtime.py",
    "taskplane/taskplane_lite.py",
    "taskplane/terminal_truth.py",
    "taskplane/tests/test_em_h3_dashboard.py",
    "taskplane/tests/test_em_h3_privacy.py",
    "taskplane/tests/test_em_h3_terminal_export.py",
    "taskplane/tests/test_em_hx_graph.py",
    "taskplane/tests/test_em_h3_integration.py",
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _git(repository: Path, *args: str) -> str:
    return subprocess.check_output(
        ["/usr/bin/git", *args], cwd=repository, text=True,
        encoding="utf-8",
    ).strip()


def _repository(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["/usr/bin/git", "init", "-q"], cwd=path, check=True)
    (path / "README.md").write_text("H3 integration\n", encoding="utf-8")
    subprocess.run(["/usr/bin/git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(
        [
            "/usr/bin/git", "-c", "user.name=H3 integration",
            "-c", "user.email=h3@example.invalid", "commit", "-qm",
            "candidate",
        ],
        cwd=path,
        check=True,
    )
    return path


def _load_verifier():
    path = ROOT / TERMINAL_VERIFIER
    spec = importlib.util.spec_from_file_location(
        f"_em_h3_integration_verifier_{hash(path)}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def _dashboard_snapshot(candidate_sha: str) -> HostSurfaceSnapshot:
    finding = {
        "id": "H3-C-retention-exception",
        "title": "Accepted retention limitations",
        "severity": "accepted-exception",
    }
    values = {
        name: {
            "status": "accepted-exception" if name == "findings" else "ready",
            "provenance": (
                f"{DISPOSITION_PATH}@{H3C_CANDIDATE}"
                if name == "findings" else f"git:{candidate_sha}"
            ),
            "summary": f"Exact-candidate {name} evidence",
            "items": [finding] if name == "findings" else [],
        }
        for name in dashboard.HOST_DASHBOARD_COMPONENTS
    }
    return HostSurfaceSnapshot.create(
        workflow_id="R-0002",
        run_id="H3-I",
        target="taskplane",
        revision=candidate_sha,
        sequence=3,
        stage="high-gate",
        state="accepted-exception",
        values=values,
        evidence=(f"git:{candidate_sha}",
                  f"sha256:{DISPOSITION_SHA256}"),
        safe_actions=("inspect evidence", "export review"),
    )


def _dashboard_errors(
        projection: dict, markup: str, candidate_sha: str) -> list[str]:
    errors: list[str] = []
    expected_components = list(dashboard.HOST_DASHBOARD_COMPONENTS)
    if projection.get("identity", {}).get("revision") != candidate_sha:
        errors.append("dashboard is not bound to the exact candidate")
    evidence = projection.get("evidence") or []
    for expected in (f"git:{candidate_sha}",
                     f"sha256:{DISPOSITION_SHA256}"):
        if expected not in evidence or expected not in markup:
            errors.append(f"dashboard evidence is unavailable: {expected}")
    if [row.get("id") for row in projection.get("components", [])] != \
            expected_components:
        errors.append("dashboard component inventory is incomplete")
    accessibility = projection.get("presentation", {}).get(
        "accessibility", {})
    for key in (
        "semantic_labels", "alt_text", "keyboard_navigation",
        "visible_focus", "reduced_motion", "status_not_color_only",
    ):
        if accessibility.get(key) is not True:
            errors.append(f"dashboard accessibility edge is disabled: {key}")
    if accessibility.get("contrast") != "WCAG-AA":
        errors.append("dashboard does not claim the reviewed contrast floor")
    actions = projection.get("safe_actions") or []
    presented = (
        projection.get("presentation", {}).get("primary_actions", []) +
        projection.get("presentation", {}).get("detail_actions", [])
    )
    if presented != actions:
        errors.append("dashboard action inventory drifted from canonical truth")
    for action in actions:
        if f'data-prompt="{action}"' not in markup:
            errors.append(f"dashboard action has no delivery behavior: {action}")
    for required in (
        'data-detail-trigger="true"',
        'aria-controls="tp-fullscreen-detail"',
        'data-delivery-scope="shared"',
        "Delivery failed — retry or reply in chat:",
        "event.preventDefault()",
    ):
        if required not in markup:
            errors.append(f"dashboard interaction is not truthful: {required}")
    return errors


def _graph_errors(proof: dict, renderer: str) -> list[str]:
    errors = list(proof.get("errors") or [])
    expected = {
        "status": "complete",
        "complete": True,
        "truncated": False,
        "node_count": 14,
        "edge_count": 24,
        "current_design_edge_count": 23,
    }
    for key, value in expected.items():
        if proof.get(key) != value:
            errors.append(f"architecture proof {key} is not {value!r}")
    activation = "ev.key==='Enter'||ev.key===' '||ev.key==='Spacebar'"
    if renderer.count(activation) != 2:
        errors.append("module and component graph nodes are not keyboard activated")
    if renderer.count("role:'button'") != 2 or \
            renderer.count("tabindex:'0'") != 2:
        errors.append("graph nodes are not exposed as focusable buttons")
    for required in (
        "showTip(n,n.x,n.y)", "show(c.x,c.y)",
        "else if(ev.key==='Escape')tip.style.display='none'",
    ):
        if required not in renderer:
            errors.append(f"graph keyboard behavior is incomplete: {required}")
    return errors


def _retention_disposition_errors(raw: bytes | None) -> list[str]:
    if raw is None:
        return ["H3-C retention disposition is unavailable"]
    errors: list[str] = []
    if _sha256_bytes(raw) != DISPOSITION_SHA256:
        errors.append("H3-C retention disposition bytes are not approved")
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        return errors + ["H3-C retention disposition is not UTF-8"]
    semantic_text = " ".join(text.split())
    required = (
        "Status: accepted delivery exception",
        "Accepted by: Volodymyr Demkiv",
        "Authority: user instruction, “bypass and proceed”",
        f"Candidate: `{H3C_CANDIDATE}`",
        "H-23 and H-25 must not be represented as independently green.",
        "route every authority append through the bounded trace sink",
        "migrate or expire the active pre-upgrade trace",
        "abandoned nonterminal command handles",
    )
    for item in required:
        if item not in semantic_text:
            errors.append(f"H3-C disposition omits approved fact: {item}")
    if len(re.findall(r"(?m)^- ", text)) != 3:
        errors.append("H3-C disposition does not enumerate exactly three gaps")
    return errors


def _shared_metadata_errors(meta: dict, durable: str, private: tuple[str, ...]) \
        -> list[str]:
    errors: list[str] = []
    if meta.get("schema") != "taskplane.store-meta/v2" or \
            meta.get("shared") is not True:
        errors.append("shared metadata schema is not minimized")
    if set(meta) != {
        "schema", "shared", "workspace_key", "repository_fingerprint",
    }:
        errors.append("shared metadata contains an unreviewed field")
    for value in private:
        if value and value in durable:
            errors.append(f"shared metadata leaks private material: {value}")
    return errors


def _terminal_errors(verified: dict, candidate_sha: str,
                     selectors: list[str], calls: list[tuple[str, ...]]) \
        -> list[str]:
    errors: list[str] = []
    if verified.get("candidate_sha") != candidate_sha:
        errors.append("terminal export is stale")
    if verified.get("status") != "prepared-not-authoritative":
        errors.append("terminal export invents terminal authority")
    if verified.get("evidence_state") != {
        "terminal_authority": "not-minted",
        "full_suite": "not-recorded",
        "release": "not-granted",
        "main_mutation": "not-granted",
        "publication": "not-granted",
    }:
        errors.append("terminal export evidence state is not fail-closed")
    if [row.get("selector") for row in verified.get("selectors", [])] != selectors:
        errors.append("terminal selector evidence is mixed or incomplete")
    observed = [call[-1] for call in calls]
    if observed != selectors:
        errors.append("terminal selectors did not execute in canonical order")
    return errors


def test_ac4_human_privacy_and_terminal_truth(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Join every H3 producer against one clean, immutable candidate."""
    inspector = TrustedGitInspector()
    before = inspector.snapshot(ROOT, evidence_paths=EXACT_CANDIDATE_INPUTS)
    candidate_sha = before.head_sha
    assert re.fullmatch(r"[0-9a-f]{40,64}", candidate_sha)
    assert _git(ROOT, "status", "--porcelain=v1", "--untracked-files=all") == ""
    for relative, expected in before.evidence_sha256.items():
        live = (ROOT / relative).read_bytes()
        assert _sha256_bytes(live) == expected

    disposition = (ROOT / DISPOSITION_PATH).read_bytes()
    assert _retention_disposition_errors(disposition) == []

    canonical = _dashboard_snapshot(candidate_sha)
    projection = dashboard.native_dashboard_projection(
        canonical, host="codex")
    markup = dashboard.render_native_dashboard_surface(
        projection, viewport_px=390, text_scale_percent=200,
        reduced_motion=True)
    assert _dashboard_errors(projection, markup, candidate_sha) == []

    graph = depgraph.architecture_map_proof(str(ROOT))
    assert _graph_errors(graph, depgraph._HTML) == []

    # Exercise the production shared-store writer.  This is evidence for its
    # implemented minimization path, not a claim that the three dispositioned
    # H3-C retention gaps disappeared.
    private_workspace = _repository(
        tmp_path / "Alice-Laptop" / "private-repository")
    _git(
        private_workspace, "remote", "add", "origin",
        "https://alice:credential@example.com/team/project.git",
    )
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "taskplane-home"))
    monkeypatch.setenv("TASKPLANE_STORE", "repo")
    meta = tp.write_store_meta(str(private_workspace))
    meta_path = Path(tp.store_meta_path(str(private_workspace)))
    durable_meta = meta_path.read_text(encoding="utf-8")
    private_values = (
        str(private_workspace), str(private_workspace.resolve()),
        "Alice-Laptop", "alice", "credential", "example.com",
    )
    assert _shared_metadata_errors(
        meta, durable_meta, private_values) == []

    # Prove the bounded terminal-handle lifecycle that H3-C did implement,
    # while leaving its accepted nonterminal-handle gap explicitly open.
    clock = [100.0]
    runtime = CommandRuntime(
        str(tmp_path / "commands"), workspace="repository",
        authorization="H3-I", clock=lambda: clock[0])
    handle = runtime.create(
        command_fingerprint="private-output", binding={"pid": 42})
    runtime.append_output(
        handle,
        "alice.private@example.com /Users/alice/private token=" + "x" * 64,
    )
    runtime.transition(handle, "succeeded")
    clock[0] += COMMAND_RETENTION_SECONDS + 1
    purged = runtime.enforce_retention()
    assert handle in purged["removed"]
    assert not (Path(runtime.root) / handle).exists()

    verifier = _load_verifier()
    template = verifier.load_template(ROOT / TERMINAL_TEMPLATE)
    calls: list[tuple[str, ...]] = []
    coordinator = terminal_truth.TerminalCoordinator(
        tmp_path / "terminal-authority")

    def pass_selector(snapshot, argv, environment):
        assert snapshot.head_sha == candidate_sha
        assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, b"passed\n", b"")

    monkeypatch.setattr(coordinator, "_run_selector", pass_selector)
    manifest = verifier.prepare_repository_candidate(
        template_path=ROOT / TERMINAL_TEMPLATE,
        repository=ROOT,
        surface_documents=_surface_documents(candidate_sha),
        coordinator=coordinator,
    )
    verified = verifier.verify_candidate_manifest(
        template_path=ROOT / TERMINAL_TEMPLATE,
        manifest=manifest,
        expected_sha=candidate_sha,
    )
    selectors = template["required_selectors"]
    assert _terminal_errors(verified, candidate_sha, selectors, calls) == []

    unchanged = inspector.assert_unchanged(before)
    assert unchanged.head_sha == candidate_sha
    assert unchanged.tree_sha == before.tree_sha


def test_ac4_dashboard_action_and_evidence_mutations_fail_closed() -> None:
    candidate_sha = "a" * 40
    projection = dashboard.native_dashboard_projection(
        _dashboard_snapshot(candidate_sha), host="codex")
    markup = dashboard.render_native_dashboard_surface(projection)

    no_evidence = copy.deepcopy(projection)
    no_evidence["evidence"] = []
    errors = _dashboard_errors(
        no_evidence,
        dashboard.render_native_dashboard_surface(no_evidence),
        candidate_sha,
    )
    assert any("evidence is unavailable" in error for error in errors)

    severed = markup.replace(
        'data-prompt="inspect evidence"', 'data-severed="inspect evidence"', 1)
    errors = _dashboard_errors(projection, severed, candidate_sha)
    assert any("action has no delivery behavior" in error for error in errors)

    misleading = markup.replace(
        "Delivery failed — retry or reply in chat:", "Delivered", 1)
    errors = _dashboard_errors(projection, misleading, candidate_sha)
    assert any("interaction is not truthful" in error for error in errors)


def test_ac4_graph_keyboard_mutation_fails_closed() -> None:
    activation = "ev.key==='Enter'||ev.key===' '||ev.key==='Spacebar'"
    pointer_only = depgraph._HTML.replace(activation, "false", 1)
    errors = _graph_errors(
        depgraph.architecture_map_proof(str(ROOT)), pointer_only)
    assert any("not keyboard activated" in error for error in errors)


@pytest.mark.parametrize("mutation", ("missing", "tampered"))
def test_ac4_retention_bypass_record_mutation_fails_closed(
        mutation: str) -> None:
    approved = (ROOT / DISPOSITION_PATH).read_bytes()
    raw = None if mutation == "missing" else approved.replace(
        H3C_CANDIDATE.encode(), b"0" * 40)
    errors = _retention_disposition_errors(raw)
    assert errors
    assert any("unavailable" in error or "not approved" in error
               for error in errors)


def test_ac4_shared_metadata_leak_mutation_fails_closed() -> None:
    private_path = "/Users/alice/private-repository"
    leaked = {
        "schema": "taskplane.store-meta/v2",
        "shared": True,
        "workspace_key": "workspace:fixture",
        "repository_fingerprint": "f" * 64,
        "workspace": private_path,
    }
    errors = _shared_metadata_errors(
        leaked, json.dumps(leaked), (private_path, "alice"))
    assert any("unreviewed field" in error for error in errors)
    assert any("leaks private material" in error for error in errors)


@pytest.mark.parametrize("mutation", ("stale", "mixed"))
def test_ac4_terminal_stale_or_mixed_evidence_fails_closed(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        mutation: str) -> None:
    candidate_sha = TrustedGitInspector().snapshot(ROOT).head_sha
    verifier = _load_verifier()
    coordinator = terminal_truth.TerminalCoordinator(
        tmp_path / "terminal-authority")
    monkeypatch.setattr(
        coordinator,
        "_run_selector",
        lambda _snapshot, argv, _environment:
        subprocess.CompletedProcess(argv, 0, b"passed\n", b""),
    )
    documents = _surface_documents(candidate_sha)
    if mutation == "stale":
        documents["public_report"]["identity"]["full_source_sha"] = "0" * 40
        message = "another SHA"
    else:
        documents.pop("run_journal")
        message = "all terminal surfaces"
    with pytest.raises(verifier.TerminalExportError, match=message):
        verifier.prepare_repository_candidate(
            template_path=ROOT / TERMINAL_TEMPLATE,
            repository=ROOT,
            surface_documents=documents,
            coordinator=coordinator,
        )
