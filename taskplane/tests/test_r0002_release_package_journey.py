"""Installed-package journey for the 2.18.9 marketplace candidate.

The journey executes extracted archives from an isolated directory.  It
checks public behavior and schemas, not byte identity; the sole digest
comparison is the packager's explicit reproducibility property.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
VERSION = "2.18.10"


def _script_module(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(
        f"taskplane_release_test_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _extract(archive: Path, target: Path) -> Path:
    with zipfile.ZipFile(archive) as package:
        package.extractall(target)
    root = target / "taskplane"
    assert root.is_dir()
    return root


def _run_package_entry_point(kind: str, output_dir: Path) -> Path:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / f"package_{kind}.py"),
         "--output-dir", str(output_dir), "--allow-dirty"],
        cwd=ROOT, text=True, encoding="utf-8", capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    archive = output_dir / f"taskplane-{VERSION}-{kind}.zip"
    assert archive.is_file()
    return archive


def _replace_packaged_settings(archive: Path, replacement: dict) -> None:
    member = "taskplane/taskplane/operational-settings.json"
    temporary = archive.with_suffix(".rewritten.zip")
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(
            temporary, "w") as target:
        for info in source.infolist():
            payload = source.read(info)
            if info.filename == member:
                payload = (json.dumps(replacement, sort_keys=True) + "\n").encode()
            target.writestr(info, payload)
    os.replace(temporary, archive)


def _packaged_hook_manifest(archive: Path) -> dict:
    with zipfile.ZipFile(archive) as package:
        return json.loads(package.read("taskplane/hooks/hooks.json"))


def _replace_packaged_hook_manifest(archive: Path, replacement: dict) -> None:
    member = "taskplane/hooks/hooks.json"
    temporary = archive.with_suffix(".rewritten.zip")
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(
            temporary, "w") as target:
        for info in source.infolist():
            payload = source.read(info)
            if info.filename == member:
                payload = (json.dumps(replacement, indent=2) + "\n").encode()
            target.writestr(info, payload)
    os.replace(temporary, archive)


def _expected_installed_hook_manifest(kind: str) -> dict:
    claude = json.loads((
        ROOT / "hooks" / "hooks.json"
    ).read_text(encoding="utf-8"))
    if kind == "claude":
        return claude
    assert kind == "openai"
    codex = json.loads((
        ROOT / ".codex" / "hooks.json"
    ).read_text(encoding="utf-8"))
    expected = json.loads(json.dumps(claude))
    expected["hooks"]["SessionStart"] = codex["hooks"]["SessionStart"]
    return expected


def _assert_installed_session_start_wiring(kind: str, manifest: dict) -> None:
    assert manifest == _expected_installed_hook_manifest(kind)
    host = "codex" if kind == "openai" else "claude"
    hook_path = "bridge" if kind == "openai" else "native"
    commands = [
        hook[field]
        for entry in manifest["hooks"]["SessionStart"]
        for hook in entry["hooks"]
        for field in ("command", "commandWindows")
    ]
    assert any("host-native-check" in command for command in commands)
    for command in commands:
        if "host-native-check" not in command:
            continue
        assert f"--host {host}" in command
        assert f"TASKPLANE_HOOK_PATH={hook_path}" in command or (
            f'TASKPLANE_HOOK_PATH={hook_path}"' in command)


def test_openai_and_claude_package_entry_points_accept_canonical_v2_operational_settings(
        tmp_path):
    canonical = json.loads((
        ROOT / "taskplane" / "operational-settings.json"
    ).read_text(encoding="utf-8"))
    assert canonical["schema"] == "taskplane.operational-settings/v2"

    for kind in ("openai", "claude"):
        archive = _run_package_entry_point(kind, tmp_path / kind)
        with zipfile.ZipFile(archive) as package:
            packaged = json.loads(package.read(
                "taskplane/taskplane/operational-settings.json"))
        assert packaged == canonical


def test_openai_and_claude_package_entry_points_reject_foreign_or_invalid_operational_settings_authority(
        tmp_path):
    canonical = json.loads((
        ROOT / "taskplane" / "operational-settings.json"
    ).read_text(encoding="utf-8"))
    foreign = {**canonical, "schema": "foreign.operational-settings/v2"}
    malformed = dict(canonical)
    malformed.pop("workflow")
    invalid_binding = json.loads(json.dumps(canonical))
    invalid_binding["workflow"]["root_session"]["seed_budget_tokens"] = 39_999

    openai = _script_module("package_openai")
    claude = _script_module("package_claude")
    for kind, module in (("openai", openai), ("claude", claude)):
        canonical_archive = _run_package_entry_point(
            kind, tmp_path / f"canonical-{kind}")
        for name, replacement in (
                ("foreign", foreign), ("malformed", malformed),
                ("invalid-binding", invalid_binding)):
            archive = tmp_path / f"{name}-{canonical_archive.name}"
            archive.write_bytes(canonical_archive.read_bytes())
            _replace_packaged_settings(archive, replacement)
            with pytest.raises(module.PackageError, match="canonical authority"):
                if kind == "openai":
                    module.validate_archive(archive, expected_version=VERSION)
                else:
                    module.validate_archive(archive, VERSION)


def test_installed_openai_archive_has_codex_session_start_host_path_and_claude_archive_retains_claude_wiring(
        tmp_path):
    for kind in ("openai", "claude"):
        archive = _run_package_entry_point(kind, tmp_path / kind)
        _assert_installed_session_start_wiring(
            kind, _packaged_hook_manifest(archive))


def test_installed_archive_session_start_wiring_rejects_wrong_host_or_hook_path_for_either_host(
        tmp_path):
    for kind in ("openai", "claude"):
        canonical = _run_package_entry_point(
            kind, tmp_path / f"canonical-{kind}")
        _assert_installed_session_start_wiring(
            kind, _packaged_hook_manifest(canonical))
        expected_host = "codex" if kind == "openai" else "claude"
        wrong_host = "claude" if kind == "openai" else "codex"
        expected_path = "bridge" if kind == "openai" else "native"
        wrong_path = "native" if kind == "openai" else "bridge"
        for mutation, old, new in (
                ("wrong-host", f"--host {expected_host}",
                 f"--host {wrong_host}"),
                ("wrong-hook-path", f"TASKPLANE_HOOK_PATH={expected_path}",
                 f"TASKPLANE_HOOK_PATH={wrong_path}")):
            archive = tmp_path / f"{mutation}-{canonical.name}"
            archive.write_bytes(canonical.read_bytes())
            manifest = _packaged_hook_manifest(archive)
            for entry in manifest["hooks"]["SessionStart"]:
                for hook in entry["hooks"]:
                    for field in ("command", "commandWindows"):
                        hook[field] = hook[field].replace(old, new)
            _replace_packaged_hook_manifest(archive, manifest)
            with pytest.raises(AssertionError):
                _assert_installed_session_start_wiring(
                    kind, _packaged_hook_manifest(archive))


def test_installed_openai_direct_launcher_plan_approval_prepares_canonical_typed_root_seed_and_non_null_receipt(
        tmp_path):
    archive = _run_package_entry_point("openai", tmp_path / "package")
    package_root = _extract(archive, tmp_path / "extracted")
    setup = r'''
import json
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
workspace = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "taskplane"))
import loop

loop.save(str(workspace), json.load(sys.stdin))
'''

    def approve_scope(name, scope):
        case = tmp_path / name
        workspace = case / "workspace"
        workspace.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
        (workspace / "README.md").write_text(
            "installed plan approval\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "README.md"], cwd=workspace, check=True)
        subprocess.run([
            "git", "-c", "user.name=Taskplane", "-c",
            "user.email=taskplane@example.invalid", "commit", "-qm", "base",
        ], cwd=workspace, check=True)
        baseline = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=workspace, text=True,
            encoding="utf-8").strip()
        task = {
            "id": "P14-part-a-canary", "wave": "W2A",
            "scope": [scope], "tests": "true",
            "criteria": ["installed Plan approval prepares its root seed"],
            "status": "pending", "deps": [],
        }
        (workspace / "plan").mkdir()
        (workspace / "plan" / "tasks.json").write_text(
            json.dumps({"tasks": [task]}), encoding="utf-8")
        state = {
            "run_id": "run-" + name, "baseline": baseline,
            "design_fingerprint": "b" * 64, "step": "plan_approval",
            "tasks": [task], "current_task": 0,
            "goal": "exercise installed Plan approval root preparation",
            "parallel": True, "max_fix_cycles": 1,
            "checkpoints": ["plan"], "design_required": False,
        }
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "TASKPLANE_HOME": str(case / "private-store"),
            "TASKPLANE_STAGE_NATIVE": "disabled",
            "TASKPLANE_CONSOLIDATED_FLOW": "0",
        }
        prepared = subprocess.run(
            [sys.executable, "-I", "-c", setup, str(package_root),
             str(workspace)],
            cwd=case, text=True, encoding="utf-8", input=json.dumps(state),
            capture_output=True, env=environment)
        assert prepared.returncode == 0, prepared.stdout + prepared.stderr
        approval = subprocess.run([
            sys.executable, str(package_root / "taskplane/tp.py"),
            "loop", "--workspace", str(workspace), "approve",
            "--advisory", "--by", "human:package-journey",
        ], cwd=case, text=True, encoding="utf-8", capture_output=True,
           env=environment)
        return case, workspace, environment, approval

    safe_scope = "build/taskplane-2.18.10/canary/**"
    case, workspace, environment, approval = approve_scope(
        "installed-plan-approval", safe_scope)
    assert approval.returncode == 0, approval.stdout + approval.stderr
    assert json.loads(approval.stdout)["step"] == "execute"

    inspect = r'''
import json
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
workspace = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "taskplane"))
import loop
from taskplane import root_seed, settings

state = loop.load(str(workspace))
prepared = state["root_hygiene"]
receipt = prepared["prepare_receipt"]
seed = root_seed.load_root_seed(str(workspace), prepared["seed_ref"])
configured = settings.load_settings(environment={})
root_seed.verify_prepare_receipt(
    seed, receipt, settings=configured,
    expected_seed_ref=prepared["seed_ref"])
print(json.dumps({
    "step": state["step"], "settings_digest": state["settings_digest"],
    "root_hygiene": prepared, "seed": seed,
}, sort_keys=True))
'''
    inspected = subprocess.run(
        [sys.executable, "-I", "-c", inspect, str(package_root),
         str(workspace)],
        cwd=case, text=True, encoding="utf-8", capture_output=True,
        env=environment)
    assert inspected.returncode == 0, inspected.stdout + inspected.stderr
    observed = json.loads(inspected.stdout)
    prepared_root = observed["root_hygiene"]
    receipt = prepared_root["prepare_receipt"]
    assert observed["step"] == "execute"
    assert prepared_root["status"] == "prepared"
    assert prepared_root["seed_ref"] == "waves/W2A/root-seed.json"
    assert receipt is not None
    assert receipt["status"] == "prepared"
    assert receipt["seed_fingerprint"] == prepared_root["seed_fingerprint"]
    assert observed["seed"]["seed_fingerprint"] == \
        prepared_root["seed_fingerprint"]
    assert observed["seed"]["pickups"][0]["write_scopes"] == [safe_scope]
    assert receipt["binding"]["settings_fingerprint"] == \
        observed["settings_digest"]

    unsafe_scopes = {
        "absolute": "/private/output/**",
        "traversal": "build/../outside/**",
        "native-separator": r"build\private\**",
        "control": "build/unsafe\u001f/**",
        "ambiguous": "build/[unterminated/**",
    }
    for name, unsafe_scope in unsafe_scopes.items():
        _, unsafe_workspace, _, refused = approve_scope(
            "unsafe-" + name, unsafe_scope)
        assert refused.returncode == 1, refused.stdout + refused.stderr
        refusal = json.loads(refused.stdout)
        assert "seed pickup write scope" in refusal["error"]
        assert refusal["step"] == "plan_approval"
        assert not (
            unsafe_workspace / "waves" / "W2A" / "root-seed.json"
        ).exists()


def _run_installed_semantics(package_root: Path, case: Path) -> dict:
    program = r'''
import hashlib
import json
import os
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
case = Path(sys.argv[2]).resolve()
# ``-I`` already excludes the working directory and PYTHONPATH.  Prepend the
# extracted install without removing the interpreter's own standard library.
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "taskplane"))

from taskplane import build_quality, failure_routing, run_artifacts, settings
from taskplane import design_host_transport

configured = settings.load_settings(environment={})
assert configured.digest == settings.settings_digest(configured)
assert configured.lenses.counts["design"] == 16
assert configured.lenses.counts["build"] == 0
assert configured.tests.backend == "ci"
assert configured.build.shards == 1
assert configured.receipt["precedence"] == ["defaults", "file"]

overridden = settings.load_settings(
    overlay={"stages": {"design": {"reasoning": "medium"}},
             "build": {"shards": 2}}, environment={})
assert overridden.stages["design"].reasoning == "medium"
assert overridden.build.shards == 2
assert overridden.receipt["precedence"] == ["defaults", "file", "overlay"]
for unsafe in ({"tests": {"backend": "local"}}, {"unknown": True}):
    try:
        settings.load_settings(overlay=unsafe, environment={})
    except settings.SettingsError:
        pass
    else:
        raise AssertionError("unsafe or unknown settings override was admitted")

candidate = {"id": "installed-candidate", "fingerprint": "a" * 64}
evidence = {"schema": "taskplane.failure-evidence/v1",
            "command": "pytest exact-selector", "returncode": 1,
            "stderr": "contract mismatch"}
failure = {
    "schema": failure_routing.FAILURE_RECORD_SCHEMA_ID,
    "id": "F-installed", "source": "pytest:exact-selector",
    "stage": "build", "repro": "pytest exact-selector",
    "evidence": evidence,
    "evidence_digest": failure_routing.evidence_digest(evidence),
    "class": "test", "reason": "the assertion tests a retired contract",
    "owner": "test-design", "cluster": "release-package",
    "route": "test-correction", "candidate": candidate,
}
routing = failure_routing.route_failure_records([failure])
assert routing["next"] == "test-correction"
assert routing["product_fix_allowed"] is False
broken = dict(failure)
broken["route"] = "fix"
try:
    failure_routing.validate_failure_record(broken)
except failure_routing.FailureRoutingError:
    pass
else:
    raise AssertionError("contradictory failure route was admitted")

progression = None
for layer, execution in (
    ("static", "local"), ("exact-selector", "local"),
    ("changed-radius", "ci"), ("proportional-suite", "ci"),
    ("authoritative-ci", "ci"),
):
    progression = build_quality.advance_progression(
        candidate["fingerprint"], layer, execution=execution,
        prior=progression)
assert progression["completed"] == list(build_quality.VALIDATION_LAYERS)
assert progression["authoritative"] is True
assert progression["matrix_runs"] == 1
try:
    build_quality.advance_progression(
        candidate["fingerprint"], "authoritative-ci", execution="ci",
        prior=progression)
except build_quality.BuildQualityError:
    pass
else:
    raise AssertionError("a second authoritative matrix was admitted")

artifact_root = case / "run" / "artifacts"
artifact_root.parent.mkdir(parents=True)
binding = run_artifacts.create_binding(
    repository_id="installed-repository", run_id="installed-run",
    stage_id="build", stage_instance_id="build-installed-1",
    candidate=candidate, settings_digest=configured.digest,
    source_fingerprint="b" * 64)
run_artifacts.create_manifest(artifact_root, binding=binding)
for artifact_class in (
    "dashboard", "dependency-graphs", "telemetry", "validation",
    "cleanup", "retro",
):
    run_artifacts.publish_artifact(
        artifact_root, artifact_class,
        {"schema": "taskplane.installed-package-evidence/v1",
         "class": artifact_class},
        metadata={"producer": "installed-package-journey"})
run_artifacts.append_activity(
    artifact_root, event_type="terminal", agent_attempt_id="attempt-1",
    worker_id="worker-1", task_id="RELEASE-PACKAGE",
    lens="zero-lens-build", details={"outcome": "success"})
verified = run_artifacts.verify_manifest(
    artifact_root, expected_binding=binding)
assert set(verified["class_counts"]) == set(run_artifacts.ARTIFACT_CLASSES)
assert all(value == 1 for value in verified["class_counts"].values())
assert verified["zero_unindexed_files"] is True

hook = json.loads((root / "hooks/hooks.json").read_text(encoding="utf-8"))
assert {"PreToolUse", "SessionStart", "Stop", "SubagentStart", "SubagentStop"} \
    <= set(hook["hooks"])
skills = []
for skill_file in sorted((root / "skills").glob("*/SKILL.md")):
    skills.append(skill_file.parent.name)
    flow = skill_file.parent / "flow.json"
    assert flow.is_file(), f"missing flow for {skill_file.parent.name}"
    flow_value = json.loads(flow.read_text(encoding="utf-8"))
    assert flow_value["settings"] == {
        "source": "taskplane/operational-settings.json",
        "loader": "taskplane.settings.load_settings",
        "binding": "settings_digest",
    }
assert {"taskplane", "tp-design", "tp-build", "tp-engineering", "tp-go"} \
    <= set(skills)
role = design_host_transport.portable_role_reference("tp-lens")
assert role["path"] == "agents/tp-lens.md"

print(json.dumps({
    "version": "2.18.9",
    "settings_digest": configured.digest,
    "routing": routing["next"],
    "validation_layers": progression["completed"],
    "artifact_classes": sorted(verified["class_counts"]),
    "skills": skills,
}, sort_keys=True))
'''
    result = subprocess.run(
        [sys.executable, "-I", "-c", program, str(package_root), str(case)],
        cwd=case, text=True, encoding="utf-8", capture_output=True,
        env={"PATH": os.environ.get("PATH", "")},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def _run_minimal_installed_loop(package_root: Path, case: Path) -> None:
    workspace = case / "workspace"
    workspace.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    spec = workspace / "spec.md"
    spec.write_text("# Installed journey\n\nPreserve package behavior.\n",
                    encoding="utf-8")
    (workspace / "README.md").write_text("installed package\n", encoding="utf-8")
    (workspace / "app.py").write_text("VALUE = 'installed'\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md", "spec.md", "app.py"],
                   cwd=workspace, check=True)
    subprocess.run([
        "git", "-c", "user.name=Taskplane", "-c",
        "user.email=taskplane@example.invalid", "commit", "-qm", "base",
    ], cwd=workspace, check=True)
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "TASKPLANE_HOME": str(case / "private-store"),
    }
    cli = package_root / "taskplane/tp.py"
    requirement_result = subprocess.run([
        sys.executable, str(cli), "req", "--workspace", str(workspace),
        "new", "Installed package remains governable",
        "--functional", "Initialize Design through the installed runtime",
        "--acceptance", "A fresh installed Design run emits its governed brief",
        "--nfr", "security=Keep private run evidence outside the checkout",
        "--nfr", "architecture=Use the canonical settings and artifact boundaries",
        "--files", "app.py",
    ], cwd=case, text=True, encoding="utf-8", capture_output=True,
       env=environment)
    assert requirement_result.returncode == 0, (
        requirement_result.stdout + requirement_result.stderr)
    requirement_id = json.loads(requirement_result.stdout)["recorded"]
    initialized = subprocess.run([
        sys.executable, str(cli), "loop", "--workspace", str(workspace),
        "init", "--spec", str(spec), "--req", requirement_id,
        "--design", "--parallel",
        "installed package journey",
    ], cwd=case, text=True, encoding="utf-8", capture_output=True,
       env=environment)
    assert initialized.returncode == 0, initialized.stdout + initialized.stderr
    state = json.loads(initialized.stdout)
    assert state["initialized"] is True
    assert state["step"] == "design"

    next_action = subprocess.run([
        sys.executable, str(cli), "loop", "--workspace", str(workspace),
        "next",
    ], cwd=case, text=True, encoding="utf-8", capture_output=True,
       env=environment)
    assert next_action.returncode == 0, next_action.stdout + next_action.stderr
    action = json.loads(next_action.stdout)
    assert action["schema"] == "taskplane.loop-next-delta/v1"
    assert action["status"] == "ready"
    assert action["step"] == "design"
    assert action["current_action"]["step"] == "design"


def test_extracted_marketplace_packages_execute_the_governed_journey(tmp_path):
    openai = _script_module("package_openai")
    claude = _script_module("package_claude")
    provenance = _script_module("release_provenance")

    manifest = openai.load_manifest()
    openai_archive = tmp_path / f"taskplane-{VERSION}-openai.zip"
    openai_repeat = tmp_path / "reproducibility" / openai_archive.name
    openai_files = openai.package_files(manifest)
    openai.write_zip(openai_files, openai_archive)
    openai.validate_archive(openai_archive, expected_version=VERSION)
    openai.write_zip(openai_files, openai_repeat)
    # One explicit reproducibility digest; archive identity is not treated as
    # evidence that behavior or release authority is correct.
    assert _sha256(openai_archive) == _sha256(openai_repeat)

    claude_archive = tmp_path / f"taskplane-{VERSION}-claude.zip"
    claude.write_zip(claude.package_files(), claude_archive)
    claude.validate_archive(claude_archive, VERSION)

    for kind, archive in (("openai", openai_archive),
                          ("claude", claude_archive)):
        with zipfile.ZipFile(archive) as package:
            names = set(package.namelist())
        assert "taskplane/taskplane/test_portfolio.json" not in names
        assert "taskplane/taskplane/operational-settings.json" in names
        record_path = provenance.write(
            ROOT, archive, _sha256(archive), allow_dirty=True, kind=kind)
        record = provenance.validate(
            json.loads(record_path.read_text(encoding="utf-8")),
            expected_source_sha=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
                encoding="utf-8").strip(),
            require_release_inputs=True,
        )
        assert record["archive"] == archive.name
        assert record["sha256"] == _sha256(archive)
        assert record["verified_source"] is (not record["dirty"])

    for kind, archive in (("openai", openai_archive),
                          ("claude", claude_archive)):
        case = tmp_path / f"installed-{kind}"
        case.mkdir()
        package_root = _extract(archive, case / "extracted")
        semantic = _run_installed_semantics(package_root, case)
        assert semantic["version"] == VERSION
        assert semantic["routing"] == "test-correction"
        assert semantic["artifact_classes"] == sorted((
            "dashboard", "dependency-graphs", "telemetry", "agent-activity",
            "validation", "cleanup", "retro",
        ))
        version = subprocess.run(
            [sys.executable, str(package_root / "taskplane/tp.py"), "version"],
            cwd=case, text=True, encoding="utf-8", capture_output=True,
            env={"PATH": os.environ.get("PATH", "")},
        )
        assert version.returncode == 0, version.stdout + version.stderr
        assert version.stdout.strip() == VERSION
        _run_minimal_installed_loop(package_root, case / "governed-loop")
