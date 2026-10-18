"""Exercise extracted candidate CLI boundaries; these are not installed-host tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def run_cli(workspace, *args, root=ROOT):
    result = subprocess.run([sys.executable, str(root / "taskplane/tp.py"), *args],
                            cwd=workspace, capture_output=True, text=True,
                            encoding="utf-8", env={**os.environ, "CODEX_THREAD_ID": "e2e-root"})
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_both_extracted_packages_retain_cli_and_authority_boundaries(tmp_path):
    for host in ("openai", "claude"):
        result = subprocess.run([sys.executable, str(ROOT / f"scripts/package_{host}.py"),
                                 "--output-dir", str(tmp_path / "dist")],
                                capture_output=True, text=True, cwd=ROOT, encoding="utf-8")
        assert result.returncode == 0, result.stderr
        receipt = json.loads(result.stdout)
        install = tmp_path / host
        with zipfile.ZipFile(receipt["archive"]) as archive:
            names = archive.namelist()
            assert all(name not in names for name in ("taskplane/loop.py", "taskplane/taskplane_lite.py",
                "taskplane/stage_loop.py", "taskplane/enforcement.py", "agents/spec-phase-definitions.json"))
            assert not any(name.startswith("taskplane/tests/") for name in names)
            archive.extractall(install)
        for shipped in ('PRIVACY.md', 'skills/tp-engineering/SKILL.md',
                        'skills/tp-product/SKILL.md', 'skills/tp-design/SKILL.md',
                        'skills/tp-go/references/shared-flow.md'):
            assert (install / shipped).read_bytes() == (ROOT / shipped).read_bytes()
        workspace = tmp_path / (host + "-workspace")
        workspace.mkdir()
        for args in (["start", "--phase", "engineering", "--standalone"], ["finish"]):
            rejected = subprocess.run([sys.executable, str(install/"taskplane/tp.py"), "flow", *args, "--profile", "protected_host",
                                       "--workspace", str(workspace)], capture_output=True, text=True)
            assert rejected.returncode == 2
            assert json.loads(rejected.stdout)["reason"] in {"unsupported_authority", "state_unavailable"}
        assert not (workspace/".taskplane/flow-events.jsonl").exists()
        assert json.loads(run_cli(workspace, "version", "--verify", root=install))["ok"]
