"""Build/Fix uses ordinary test DoD, without a separate quality receipt."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from taskplane import loop


def test_build_quality_command_is_removed():
    script = Path(__file__).resolve().parents[1] / "tp.py"
    result = subprocess.run([sys.executable, str(script), "loop", "--help"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
    assert result.returncode == 0, result.stderr
    assert "build-quality" not in result.stdout
    refused = subprocess.run([sys.executable, str(script), "loop", "build-quality"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
    assert refused.returncode != 0
    assert "invalid choice" in refused.stderr


@pytest.mark.parametrize("stage", ["execute", "fix"])
@pytest.mark.parametrize("exit_code", [0, 1], ids=["passing-tests", "failing-tests"])
def test_build_fix_gate_uses_real_test_result_without_quality_receipt(tmp_path, monkeypatch, stage, exit_code):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    (workspace / "owned.py").write_text("VALUE = 1\n")
    (workspace / "check.py").write_text(f"raise SystemExit({exit_code})\n")
    subprocess.run(["git", "add", "owned.py", "check.py"], cwd=workspace, check=True)
    subprocess.run(["git", "-c", "user.name=Taskplane", "-c", "user.email=taskplane@example.invalid",
                    "commit", "-qm", "base"], cwd=workspace, check=True)
    snapshot = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=workspace, text=True, encoding="utf-8", errors="replace").strip()
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "disabled")
    monkeypatch.setattr(loop.tp, "external_store_root", lambda _ws: str(tmp_path / "store"))
    state = {"run_id": "run-r0002", "goal": "scoped test", "step": stage, "current_task": 0,
             "max_fix_cycles": 3, "checkpoints": [],
             "baseline": snapshot, "tasks": [{"id": "QUALITY", "scope": ["owned.py", "check.py"],
                 "tests": "PYTHONDONTWRITEBYTECODE=1 python3 check.py",
                 "status": "pending", "test_contract": {"changed_producers": ["owned.py"]}}]}
    loop.save(str(workspace), state)
    root = Path(loop.tp.tp_dir(str(workspace)))
    root.mkdir(parents=True, exist_ok=True)
    (root / "snapshot").write_text(snapshot)
    result = loop.gate(str(workspace), "pass")
    if exit_code:
        assert "Definition of Done failed" in result.get("error", ""), result
        assert any("tests_pass" in error for error in result["dod"]["errors"])
        assert loop.load(str(workspace))["step"] == stage
    else:
        assert not result.get("error"), result
        assert result["step"] == "evaluate"
    assert "build_quality_receipt" not in loop.load(str(workspace))["tasks"][0]
    assert "authoritative-ci" not in json.dumps(result)


def test_receipt_removal_preserves_task_submission_authority(tmp_path):
    task = {"id": "QUALITY", "test_contract": {"changed_producers": ["owned.py"]}}
    assert loop._task_submission_authority_required(task)
    assert loop._task_submission_authority_error(str(tmp_path), {}, "execute", task)
