"""J1 remains unverified; no simulated or skipped native journey is registered.

The available production route is loop._phase_bridge_prepare followed by real
SubagentStart/SubagentStop hooks through observe_phase_runtime_hook and
design_host_transport.observe_phase_hook. Taskplane-owned nonce receipts are
eligible; the older prepare_native_entry diagnostic is not this route.

The J1 flow starts with the selected package's normal
``onboard --install-codex-hooks`` producer. Its ignored launcher must resolve
that candidate. Before native preparation/dispatch, the orchestrator must
retain actual user-approved hook trust and both native and bridge receipts
bound to the current session/workspace. Installed files or exit zero without
a launcher are not hook execution evidence. No local supporting case below
creates trust, invokes a hook with a made-up event, or supplies host receipts.

Execution then requires an orchestrator-prepared current candidate phase
contract, real host dispatch under that slot, and terminal/output collection.
T19 received no such J1 workflow. Authentication is available, not a blocker.
Existing simulated lifecycle helpers cannot supply genuine host authority.
The two approved J1 selectors remain outstanding, not renamed or substituted.
"""

import copy
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from scripts import package_openai
from taskplane import (
    host_capabilities, loop, requirements, review_evidence, stage_entities,
    stage_handoff, stage_migration,
)


REGISTRY = "agents/spec-phase-definitions.json"
REGISTRY_MEMBER = f"{package_openai.ARCHIVE_ROOT}/{REGISTRY}"

# Exact R-0001 requirement.contracts source rows from the approved T19 packet
# (1751eb687b006784ec442f253c9593d0208a4e167d20669e9903e4800a7fe88e).
# These are requirement inputs, never inserted stage or handoff outputs.
R0001_CONTRACTS = [
    {"relation": "changes", "id": "contract:graph-decomposition"},
    {"relation": "changes", "id": "contract:slice-validation"},
    {"relation": "changes", "id": "contract:taskplane-source-touchpoint-coverage-v1"},
    {"relation": "changes", "id": "contract:taskplane-cross-task-seam-manifest-v1"},
    {"relation": "changes", "id": "contract:taskplane-realized-seam-conformance-v1"},
    {"relation": "changes", "id": "contract:taskplane-standalone-review-seams-v1"},
    {"relation": "changes", "id": "contract:taskplane.stage-handoff/v2"},
    {"relation": "changes", "id": "contract:taskplane.phase-progress-receipt/v1"},
    {"relation": "changes", "id": "contract:taskplane.phase-pickup-result/v1"},
    {"relation": "changes", "id": "contract:taskplane.phase-review-collection/v1"},
    {"relation": "changes", "id": "contract:taskplane.phase-host-dispatch/v1"},
    {"relation": "consumes", "id": "contract:taskplane.stage-authority-binding/v1"},
]


@pytest.fixture(scope="module")
def supporting_package_archive(tmp_path_factory):
    """Actual package producer output; this supplies no native J1 evidence."""
    archive = tmp_path_factory.mktemp("j1-package-support") / "taskplane.zip"
    with pytest.MonkeyPatch.context() as patch:
        # Sparse checkouts can omit this tracked producer input. Read its exact
        # committed bytes without restoring or changing active host hooks.
        hook_path = package_openai.ROOT / ".codex" / "hooks.json"
        if not hook_path.is_file():
            hook_text = subprocess.check_output(
                ["git", "show", "HEAD:.codex/hooks.json"],
                cwd=package_openai.ROOT, text=True, encoding="utf-8",
            )
            read_text = Path.read_text

            def read_source(path, *args, **kwargs):
                return hook_text if path == hook_path else read_text(path, *args, **kwargs)

            patch.setattr(Path, "read_text", read_source)
        manifest = package_openai.load_manifest()
        package_openai.write_zip(package_openai.package_files(manifest), archive)
        yield archive


@pytest.fixture(scope="module")
def supporting_candidate_package(supporting_package_archive, tmp_path_factory):
    """Extract the actual package producer's output, without a global install."""
    destination = tmp_path_factory.mktemp("j1-onboarding-package-support")
    with zipfile.ZipFile(supporting_package_archive) as archive:
        archive.extractall(destination)
    return destination / package_openai.ARCHIVE_ROOT


def _supporting_package_onboard(package, workspace, *, install=False, launcher=False):
    # No inherited adapter assertion may supply trust for this isolated test.
    # Keep the task slot and existing native identity; the fallback merely
    # selects the Codex report branch when tests run outside Codex.
    environment = {key: value for key, value in os.environ.items()
                   if key not in host_capabilities._ENV_OBSERVATIONS}
    environment.setdefault("CODEX_THREAD_ID", "supporting-onboarding-no-host-receipt")
    engine = (workspace / ".taskplane" / "codex-hook.py" if launcher else
              package / "taskplane" / "tp.py")
    command = [sys.executable, str(engine), "onboard", "--json"]
    if install:
        command.append("--install-codex-hooks")
    result = subprocess.run(command, cwd=workspace, env=environment,
                            text=True, encoding="utf-8", capture_output=True,
                            timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_supporting_j1_onboarding_detects_missing_launcher(
    supporting_candidate_package, tmp_path, record_property
):
    from taskplane.tests.test_stage_cross_host import _real_pristine_run

    workspace, _, _ = _real_pristine_run(tmp_path)
    launcher = workspace / ".taskplane" / "codex-hook.py"
    assert not launcher.exists()
    report = _supporting_package_onboard(supporting_candidate_package, workspace)
    assert report["codex_hooks"]["status"] == "missing"
    assert report["codex_hooks"]["ok"] is False
    assert report["host_capabilities"]["ready"] is False
    assert not launcher.exists()  # The read-only report cannot onboard implicitly.
    record_property("evidence_mode", "supporting-package-onboarding-no-host-execution")


def test_supporting_j1_onboarding_produces_ignored_candidate_launcher(
    supporting_candidate_package, tmp_path, record_property
):
    from taskplane.tests.test_stage_cross_host import _real_pristine_run

    workspace, _, _ = _real_pristine_run(tmp_path)
    report = _supporting_package_onboard(
        supporting_candidate_package, workspace, install=True,
    )
    launcher = workspace / ".taskplane" / "codex-hook.py"
    intended_engine = supporting_candidate_package / "taskplane" / "tp.py"
    assert launcher.is_file()
    assert report["codex_hooks"]["status"] == "ready"
    assert Path(report["codex_hooks"]["resolved_engine"]).resolve() == intended_engine.resolve()
    ignored = subprocess.run(["git", "check-ignore", "--", ".taskplane/codex-hook.py"],
                             cwd=workspace, text=True, encoding="utf-8", errors="replace", capture_output=True, check=True)
    assert ignored.stdout.strip() == ".taskplane/codex-hook.py"
    # Execute the real generated launcher through a non-hook command; its
    # selected engine reports its own resolver result without a fake event.
    through_launcher = _supporting_package_onboard(
        supporting_candidate_package, workspace, launcher=True,
    )
    assert Path(through_launcher["codex_hooks"]["resolved_engine"]).resolve() == intended_engine.resolve()
    record_property("evidence_mode", "supporting-package-onboarding-no-host-execution")


def test_supporting_j1_onboarding_install_does_not_supply_trust_or_host_receipts(
    supporting_candidate_package, tmp_path, record_property
):
    from taskplane.tests.test_stage_cross_host import _real_pristine_run

    workspace, _, _ = _real_pristine_run(tmp_path)
    report = _supporting_package_onboard(
        supporting_candidate_package, workspace, install=True,
    )
    assert report["codex_hooks"]["status"] == "ready"
    capabilities = report["host_capabilities"]
    assert capabilities["trust"]["status"] == "unknown"
    assert capabilities["loaded_session"]["status"] == "unknown"
    assert capabilities["ready"] is False
    assert not (Path(os.environ["TASKPLANE_HOME"]) / "host-receipts").exists()
    # Stop at the missing authentic-host prerequisite. Root's real J1 flow
    # must retain user trust and matching native/bridge receipts before it
    # prepares or dispatches; this local test does not manufacture that pass.
    record_property("evidence_mode", "supporting-package-onboarding-no-host-execution")
    record_property("native_dispatch", "not-performed-missing-authentic-host-prerequisites")


def _rewrite_supporting_archive(source, target, *, registry=None, version=None):
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w") as changed:
        for member in original.infolist():
            if member.filename == REGISTRY_MEMBER:
                continue
            payload = original.read(member)
            if version and member.filename.endswith("/.codex-plugin/plugin.json"):
                manifest = json.loads(payload)
                manifest["version"] = version
                payload = json.dumps(manifest).encode()
            changed.writestr(member, payload)
        if registry is not None:
            changed.writestr(REGISTRY_MEMBER, registry)


def test_supporting_package_includes_exact_phase_registry(supporting_package_archive):
    with zipfile.ZipFile(supporting_package_archive) as archive:
        assert REGISTRY_MEMBER in archive.namelist()
        assert archive.read(REGISTRY_MEMBER) == (package_openai.ROOT / REGISTRY).read_bytes()
    package_openai.validate_archive(supporting_package_archive)


@pytest.mark.parametrize("case", ["missing", "changed", "corrupt"])
def test_supporting_package_rejects_invalid_phase_registry(
    supporting_package_archive, tmp_path, case
):
    registry = (package_openai.ROOT / REGISTRY).read_bytes()
    replacement = {"missing": None, "changed": registry + b"\n", "corrupt": b"{broken"}[case]
    archive = tmp_path / f"{case}.zip"
    _rewrite_supporting_archive(supporting_package_archive, archive, registry=replacement)
    with pytest.raises(package_openai.PackageError, match=r"agents/spec-phase-definitions\.json"):
        package_openai.validate_archive(archive)


def test_supporting_package_preserves_explicit_historical_surface_override(
    supporting_package_archive, tmp_path
):
    """Synthetic older declared surface exercises the existing override API."""
    archive = tmp_path / "historical-surface.zip"
    _rewrite_supporting_archive(supporting_package_archive, archive, version="2.18.0")
    package_openai.validate_archive(
        archive,
        expected_version="2.18.0",
        release_surface_root=package_openai.ROOT,
        stage_runtime_files=tuple(
            member for member in package_openai.STAGE_RUNTIME_FILES if member != REGISTRY
        ),
        release_surface_files=(),
        canonical_authority_files=(),
    )


def _supporting_pristine_phase_run(tmp_path, monkeypatch, *, contracts=None):
    """Public producers with simulated source/authority; no stage or host event seed."""
    from taskplane.tests.test_r0001_phase_agents_spec import _registry
    from taskplane.tests.test_stage_cross_host import (
        _real_pristine_run, _record_bootstrap_requirement,
    )

    workspace, store, initial = _real_pristine_run(tmp_path)
    ws = str(workspace)
    requirement = (
        _record_bootstrap_requirement(workspace) if contracts is None else
        requirements.record_requirement(
            ws, "supporting R-0001 contract identity compatibility",
            functional=["preserve exact source contract identities and relations"],
            acceptance=["normal initialization prepares the first phase"],
            contracts=contracts,
        )
    )
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "pristine-session")
    initialized = loop.init(
        ws, "supporting first-dispatch regression", requirement_id=requirement["id"],
        by="human:simulated",
    )
    assert "error" not in initialized, initialized
    authority = initialized["_stage_native_root_authority"]
    artifacts = review_evidence.ArtifactStore(ws)
    configuration = {
        "definition_source": REGISTRY,
        "definition_set_fingerprint": _registry().definition_set_fingerprint,
        "knowledge_reference": artifacts.put("phase-knowledge", {"facts": []}),
        "candidate_fingerprint": review_evidence.content_fingerprint(
            {"revision": authority["target_revision"], "evidence_mode": "simulated"}
        ),
        "target_revision": authority["target_revision"],
        "host_kind": "simulated", "host_version": "supporting-local-test",
        "output_paths": {"product": {"requirement": "specs/requirement.json"}},
    }

    def authorize(current):
        assert current["run_id"] == authority["run_id"]
        assert loop._stage_native_init_authority(
            ws, requirement["id"], "human:simulated"
        ) == authority

    stage_migration.change_phase_routing(
        store, initial["run_id"], owner="agent-runtime", configuration=configuration,
        expected_previous=None, expected_revision=initial["revision"],
        operation_id="supporting-enable-phase-runtime", validate_authority=authorize,
    )
    before = store.load(initial["run_id"])
    assert before["schema"] == "taskplane.run/v3"
    assert not before.get("stage_heads")
    assert not before.get("stage_operations")
    return ws, store, initial["run_id"], requirement


def _supporting_prepared_contract_boundaries(tmp_path, monkeypatch, contracts):
    ws, store, run_id, requirement = _supporting_pristine_phase_run(
        tmp_path, monkeypatch, contracts=contracts,
    )
    assert requirement["contracts"] == contracts
    first = loop.next_action(ws)
    assert "error" not in first, first
    assert first["phase_runtime"]["status"] == "pending"
    manifest = store.load(run_id)
    root_id = loop.load(ws)["_stage_run_binding"]["root_stage_id"]
    root = store.read_stage_object(run_id, manifest["stage_heads"][root_id]["object"])
    artifacts = review_evidence.ArtifactStore(ws)
    handoff = stage_handoff.read_manifest(
        artifacts, root["input_manifest_ref"],
        expected_authority_revision=root["authority"]["authority_revision"],
        expected_authority_fingerprint=root["authority"]["authority_fingerprint"],
    )
    return ws, store, run_id, first, manifest, root, artifacts, handoff


def test_supporting_r0001_contracts_reach_root_handoff_stage_and_preparation(
    tmp_path, monkeypatch, record_property
):
    """Actual producers with simulated source/authority; no native J1 claim."""
    ws, store, run_id, first, manifest, root, artifacts, handoff = (
        _supporting_prepared_contract_boundaries(tmp_path, monkeypatch, R0001_CONTRACTS)
    )
    assert root["contracts"] == sorted(row["id"] for row in R0001_CONTRACTS)
    assert handoff["contracts"] == {
        "provided": [],
        "consumed": [R0001_CONTRACTS[-1]["id"]],
        "changed": sorted(row["id"] for row in R0001_CONTRACTS[:-1]),
    }
    root_input = artifacts.read(root["selected_artifacts"][0])
    assert root_input["requirement"]["contracts"] == R0001_CONTRACTS
    assert loop.next_action(ws)["phase_runtime"]["reference"] == first["phase_runtime"]["reference"]
    assert store.load(run_id) == manifest
    record_property("evidence_mode", "supporting-production-init-next-with-simulated-source-and-authority")
    record_property("native_host_execution", "not-performed")


def test_supporting_cli_import_prepares_before_any_phase_observation(
    tmp_path, monkeypatch, record_property
):
    """Actual CLI module loading and producers; simulated source/authority only."""
    cli_loop = importlib.import_module("loop")
    assert not cli_loop.__package__
    # Exercise the same flat import as tp.py and the actual native preparation
    # script; the runtime's nonce owner is still imported as a package.
    monkeypatch.setattr(sys.modules[__name__], "loop", cli_loop)
    ws, store, run_id, first, manifest, root, _, _ = (
        _supporting_prepared_contract_boundaries(tmp_path, monkeypatch, R0001_CONTRACTS)
    )
    assert root["contracts"] == sorted(row["id"] for row in R0001_CONTRACTS)
    assert first["phase_runtime"]["package"] == []
    assert first["phase_runtime"]["native_identity_claimed"] is False
    pending = cli_loop.next_action(ws)
    assert pending["phase_runtime"]["reference"] == first["phase_runtime"]["reference"]
    assert "task_name" not in pending
    assert store.load(run_id) == manifest
    record_property("evidence_mode", "supporting-production-cli-import-init-next-with-simulated-source-and-authority")
    record_property("native_host_execution", "not-performed")


@pytest.mark.parametrize("contract_id", [
    pytest.param("contract:a--", id="legacy-trailing-hyphens"),
    pytest.param("contract:" + "a" * 128, id="legacy-128-character-body"),
    pytest.param("contract:namespace." + "a" * 114 + "/v12",
                 id="namespaced-versioned-128-character-body"),
])
def test_supporting_contract_legacy_spelling_and_body_bounds(
    tmp_path, monkeypatch, contract_id
):
    """Supporting production preparation accepts both grammars at the old bound."""
    contracts = [{"relation": "provides", "id": contract_id}]
    *_, root, artifacts, handoff = _supporting_prepared_contract_boundaries(
        tmp_path, monkeypatch, contracts,
    )
    assert root["contracts"] == [contract_id]
    assert handoff["contracts"] == {"provided": [contract_id], "consumed": [], "changed": []}
    assert artifacts.read(root["selected_artifacts"][0])["requirement"]["contracts"] == contracts


@pytest.fixture(scope="module")
def supporting_contract_reader_inputs(tmp_path_factory):
    """Produce once in an isolated store; consumer cases mutate detached copies."""
    tmp_path = tmp_path_factory.mktemp("j1-contract-reader-support")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TASKPLANE_HOME", str(tmp_path / "tp-store"))
        *_, root, artifacts, handoff = _supporting_prepared_contract_boundaries(
            tmp_path, patch, R0001_CONTRACTS,
        )
    return root, artifacts, handoff


@pytest.mark.parametrize("owner", ["handoff", "stage"])
@pytest.mark.parametrize("ids,reason", [
    pytest.param(["contract:"], "invalid entry", id="empty-body"),
    pytest.param(["contract:1bad"], "invalid entry", id="leading-digit"),
    pytest.param(["contract:Upper"], "invalid entry", id="uppercase"),
    pytest.param(["contract:a_b"], "invalid entry", id="underscore"),
    pytest.param(["contract:.a/v1"], "invalid entry", id="empty-namespace"),
    pytest.param(["contract:a..b/v1"], "invalid entry", id="empty-segment"),
    pytest.param(["contract:a./v1"], "invalid entry", id="trailing-dot"),
    pytest.param(["contract:a.b"], "invalid entry", id="missing-version"),
    pytest.param(["contract:a/v1"], "invalid entry", id="missing-namespace"),
    pytest.param(["contract:a.b/v0"], "invalid entry", id="zero-version"),
    pytest.param(["contract:a.b/v01"], "invalid entry", id="leading-zero-version"),
    pytest.param(["contract:a.b/v-1"], "invalid entry", id="negative-version"),
    pytest.param(["contract:a.b/v"], "invalid entry", id="empty-version"),
    pytest.param(["contract:a.b/v1/extra"], "invalid entry", id="extra-path"),
    pytest.param(["contract:a.b/v1?query"], "invalid entry", id="query-suffix"),
    pytest.param(["contract:a.b\\v1"], "invalid entry", id="backslash"),
    pytest.param(["contract:a.b/v1\nextra"], "invalid entry", id="embedded-newline"),
    pytest.param(["contract:a b/v1"], "invalid entry", id="embedded-space"),
    pytest.param(["contract:" + "a" * 129], "invalid entry", id="legacy-over-bound"),
    pytest.param(["contract:namespace." + "a" * 115 + "/v12"],
                 "invalid entry", id="namespaced-versioned-over-bound"),
    pytest.param([R0001_CONTRACTS[0]["id"]] * 2, "duplicate entries", id="legacy-duplicate"),
    pytest.param([R0001_CONTRACTS[-1]["id"]] * 2, "duplicate entries", id="namespaced-duplicate"),
])
def test_supporting_contract_readers_reject_malformed_ids_and_duplicates(
    supporting_contract_reader_inputs, record_property, owner, ids, reason
):
    """Consumer-unit corruption of actual producer output; no journey proof."""
    root, artifacts, handoff = supporting_contract_reader_inputs
    if owner == "handoff":
        broken = copy.deepcopy(handoff)
        broken["contracts"]["consumed"] = ids
        broken["fingerprint"] = stage_handoff.manifest_fingerprint(broken)
        with pytest.raises(stage_handoff.HandoffValidationError, match=reason):
            stage_handoff.validate_manifest(artifacts, broken)
    else:
        broken = copy.deepcopy(root)
        broken["contracts"] = ids
        broken["fingerprint"] = stage_entities.stage_fingerprint(broken)
        with pytest.raises(stage_entities.StageValidationError, match=reason):
            stage_entities.validate_stage(broken)
    record_property("evidence_mode", "consumer-unit-corruption-of-production-output")


def test_supporting_pristine_init_next_prepares_once_and_picks_up_pending(
    tmp_path, monkeypatch, record_property
):
    """Actual init/next and preparation; no native launch/terminal is simulated."""
    ws, store, run_id, _ = _supporting_pristine_phase_run(tmp_path, monkeypatch)
    first = loop.next_action(ws)
    assert "error" not in first, first
    assert first["phase_runtime"]["status"] == "pending"
    manifest = store.load(run_id)
    root_id = loop.load(ws)["_stage_run_binding"]["root_stage_id"]
    assert manifest["active_stage_projection"]["active_stage_ids"] == [root_id]
    assert sorted(row["operation"] for row in manifest["stage_operations"].values()) == [
        "resume_stage", "start_stage",
    ]
    preparations = [row for row in stage_migration.phase_records(manifest).values()
                    if row["operation"] == "phase_prepare"]
    assert len(preparations) == 1
    assert first["phase_runtime"]["reference"] == preparations[0]["result"]["reference"]

    pending = loop.next_action(ws)
    assert pending["phase_runtime"]["reference"] == first["phase_runtime"]["reference"]
    assert pending["phase_runtime"]["operation_id"] == first["phase_runtime"]["operation_id"]
    assert pending["phase_runtime"]["status"] == "pending"
    assert "task_name" not in pending  # One dispatch request, no second launch request.
    assert loop.next_action(ws)["phase_runtime"] == pending["phase_runtime"]
    # Pickup keeps precedence over a new requirement attachment.
    assert loop.next_action(ws, rid="R-9999")["phase_runtime"] == pending["phase_runtime"]
    assert store.load(run_id) == manifest
    record_property("evidence_mode", "supporting-production-init-next-with-simulated-source-and-authority")
    record_property("native_host_execution", "not-performed")


@pytest.mark.parametrize("change", ["authorization_session", "requirement"])
def test_supporting_pristine_next_rejects_changed_authority_before_effects(
    tmp_path, monkeypatch, record_property, change
):
    ws, store, run_id, requirement = _supporting_pristine_phase_run(tmp_path, monkeypatch)
    before = store.load(run_id)
    if change == "authorization_session":
        # Changing the recorded authorization is not the same as replacing
        # the process/session that consumes its unchanged durable scope.
        state = loop.load(ws)
        state["_stage_native_root_authority"]["session_id"] = "foreign-authorization"
        loop.save(ws, state)
    else:
        requirements.amend_requirement(ws, requirement["id"],
            acceptance=["changed supporting requirement must refuse bootstrap"])
    refused = loop.next_action(ws)
    assert "stage-native root bootstrap failed closed" in refused["error"]
    assert ("authority is invalid" if change == "authorization_session" else
            "requirement changed") in refused["error"]
    assert "task_name" not in refused
    assert "phase_runtime" not in refused
    assert store.load(run_id) == before
    record_property("evidence_mode", "supporting-production-init-next-with-simulated-source-and-authority")


def test_supporting_pristine_next_recovers_committed_root_before_pending_lookup(
    tmp_path, monkeypatch, record_property
):
    """Sever the singleton binding write after the actual lifecycle commit."""
    ws, store, run_id, _ = _supporting_pristine_phase_run(tmp_path, monkeypatch)
    persist = loop._persist_stage_run_binding

    def interrupted(*args, **kwargs):
        raise OSError("supporting interruption after root commit")

    with monkeypatch.context() as patch:
        patch.setattr(loop, "_persist_stage_run_binding", interrupted)
        refused = loop.next_action(ws)
    assert "supporting interruption after root commit" in refused["error"]
    committed = store.load(run_id)
    assert len(committed["stage_operations"]) == 1
    assert not any(row["operation"] == "phase_prepare"
                   for row in stage_migration.phase_records(committed).values())
    assert loop._persist_stage_run_binding is persist

    recovered = loop.next_action(ws)
    assert "error" not in recovered, recovered
    assert recovered["phase_runtime"]["status"] == "pending"
    manifest = store.load(run_id)
    assert {key: manifest["stage_operations"][key]
            for key in committed["stage_operations"]} == committed["stage_operations"]
    assert sorted(row["operation"] for row in manifest["stage_operations"].values()) == [
        "resume_stage", "start_stage",
    ]
    assert manifest["stage_heads"] == committed["stage_heads"]
    pending = loop.next_action(ws)
    assert pending["phase_runtime"]["reference"] == recovered["phase_runtime"]["reference"]
    assert "task_name" not in pending
    assert store.load(run_id) == manifest
    record_property("evidence_mode", "supporting-production-init-next-with-simulated-source-and-authority")
