"""J0 fresh-install admission, with native completion deliberately fail-closed.

These tests invoke candidate bytes in a fresh clone and empty child home. They
do not import the simulated phase fixtures, replay host hooks, or author missing
producer artifacts. A failed admission is a blocking J0 result, not a successful
negative proof. The separately named byte check proves loading only.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest


SOURCE = Path(__file__).resolve().parents[2]
GOAL = (
    "Add one short J0 smoke-check note to the project documentation and complete "
    "its governed requirement through the declared local terminal outcome; "
    "do not publish, tag, push, or modify any installation."
)
NEGATIVE_CASES = (
    "s0_incumbent_lifecycle", "s1_incumbent_run_store",
    "s2_incumbent_dispatch_collection", "s3_phase_registry",
    "s3_agent_runtime", "s4_runtime_pickup_dispatch",
    "s5_runtime_evaluator_boundary", "s6_runtime_retro_telemetry",
    "manual_repair", "fabricated_output", "degraded_host",
    "missing_telemetry_seal",
)


@dataclass(frozen=True)
class FreshInstallation:
    checkout: Path
    environment: dict[str, str]
    candidate: str
    isolation_id: str

    def run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.checkout / "taskplane/tp.py"), *arguments],
            cwd=self.checkout, env=self.environment, capture_output=True,
            text=True, timeout=30,
        )


@pytest.fixture
def fresh_installation(tmp_path: Path) -> FreshInstallation:
    # This dictionary belongs only to explicitly isolated child processes.
    # The governed builder's TASKPLANE_TASK and environment are never changed.
    child_home = tmp_path / "empty-home"
    child_home.mkdir()
    assert not list(child_home.iterdir())
    environment = {
        "PATH": os.defpath,
        "HOME": str(child_home),
        "CODEX_HOME": str(child_home / ".codex"),
        "TASKPLANE_HOME": str(child_home / ".taskplane"),
        "XDG_CONFIG_HOME": str(child_home / ".config"),
        "XDG_CACHE_HOME": str(child_home / ".cache"),
        "TMPDIR": str(tmp_path),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    candidate = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=SOURCE, text=True,
    ).strip()
    checkout = tmp_path / "candidate"
    subprocess.run(
        ["git", "clone", "--quiet", "--no-local", "--no-hardlinks",
         str(SOURCE), str(checkout)],
        env=environment, check=True, capture_output=True, text=True, timeout=30,
    )
    actual = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=checkout,
        env=environment, text=True,
    ).strip()
    assert actual == candidate
    assert not (checkout / ".taskplane").exists()
    return FreshInstallation(checkout, environment, candidate, uuid.uuid4().hex)


def _public_admission(installation: FreshInstallation) -> dict:
    """Use ordinary setup/launch once; no repair, advisory bypass or relaunch."""
    rows = {}
    for name, arguments in (
        ("project_init", ("init",)),
        ("onboarding", ("onboard", "--json")),
        ("loop_init", ("loop", "init", GOAL)),
    ):
        result = installation.run(*arguments)
        try:
            output = json.loads(result.stdout)
        except json.JSONDecodeError:
            output = {"unparsed_output": result.stdout[-2000:]}
        rows[name] = {"returncode": result.returncode, "output": output,
                      "stderr": result.stderr[-2000:]}
    if rows["loop_init"]["returncode"] == 0:
        result = installation.run("loop", "next")
        rows["loop_next"] = {
            "returncode": result.returncode, "output": json.loads(result.stdout),
        }
    return rows


def _require_native_admission(installation, rows, record_property):
    onboarding = rows["onboarding"]["output"]
    report = {
        "candidate": installation.candidate,
        "isolation_id": installation.isolation_id,
        "isolation_id_is_native_identity": False,
        "host_capabilities": onboarding.get("host_capabilities"),
        "loop_init": rows["loop_init"],
        "operator_repair_performed": False,
        "native_completion_claimed": False,
    }
    record_property("j0_admission", json.dumps(report, sort_keys=True))
    assert rows["project_init"]["returncode"] == 0, rows["project_init"]
    assert rows["onboarding"]["returncode"] == 0, rows["onboarding"]
    assert onboarding.get("ready") is True and rows["loop_init"]["returncode"] == 0, (
        "J0 BLOCKED at fresh public host admission; no native identity, exclusive "
        "real-host lease, terminal, collected output or telemetry seal may be "
        "inferred from this setup. " + json.dumps(report, sort_keys=True)
    )


def test_fresh_clone_empty_home_completes_one_small_requirement_to_declared_outcome(
    fresh_installation, record_property,
):
    rows = _public_admission(fresh_installation)
    _require_native_admission(fresh_installation, rows, record_property)
    # Admission cannot substitute for the missing real host journey driver.
    # No action payload, hook event or successful judgment is manufactured here.
    pytest.fail(
        "J0 BLOCKED: the public launcher must supply the exclusive real-host "
        "lease and actual producer dispatch/collection/finalization chain; "
        "successful setup alone is not the declared terminal outcome."
    )


@pytest.mark.parametrize("case", NEGATIVE_CASES, ids=NEGATIVE_CASES)
def test_mode_bound_shared_boundary_severance_fails_same_journey_not_silently_skipped(
    fresh_installation, record_property, case,
):
    record_property("j0_severance_case", case)
    # A baseline admission failure cannot masquerade as one of twelve distinct
    # severance proofs. Each case independently stays red before that boundary.
    rows = _public_admission(fresh_installation)
    _require_native_admission(fresh_installation, rows, record_property)
    pytest.fail(
        f"J0 severance {case} is unverified: a green real-host baseline and "
        "mode-bound public fault boundary are required before this case can "
        "claim a targeted failure. No severance was executed."
    )


def test_loaded_bytes_match_candidate_repository(fresh_installation, record_property):
    installation = fresh_installation
    script = (
        "import hashlib, json; from pathlib import Path; "
        "from taskplane import tp, loop, agent_runtime, design_host_transport; "
        "print(json.dumps({m.__name__: {'path': str(Path(m.__file__).resolve()), "
        "'sha256': hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()} "
        "for m in (tp, loop, agent_runtime, design_host_transport)}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=installation.checkout,
        env=installation.environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    loaded = json.loads(result.stdout)
    for row in loaded.values():
        relative = Path(row["path"]).relative_to(installation.checkout)
        expected = subprocess.check_output(
            ["git", "show", f"{installation.candidate}:{relative.as_posix()}"],
            cwd=SOURCE,
        )
        assert row["sha256"] == hashlib.sha256(expected).hexdigest()
    record_property("candidate_loaded_bytes", json.dumps({
        "candidate": installation.candidate, "loaded": loaded,
        "evidence_mode": "local-byte-verification-only",
        "native_completion_claimed": False,
    }, sort_keys=True))
