"""Exercise the shipped advisory CLI through Retro and the installed packages."""
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile
from taskplane import flow
from contextlib import redirect_stdout
import io

ROOT = Path(__file__).resolve().parents[2]


def run_cli(workspace, *args, root=ROOT, governor=None):
    if governor is not None:
        assert args[0] == "flow"
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = flow.main(list(args[1:]), governor=governor)
        assert code == 0, stream.getvalue()
        return stream.getvalue()
    result = subprocess.run([sys.executable, str(root / "taskplane/tp.py"), *args],
                            cwd=workspace, capture_output=True, text=True,
                            encoding="utf-8", env={**os.environ, "CODEX_THREAD_ID": "e2e-root"})
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_product_runs_through_retro_with_graph_lenses_and_dashboard(tmp_path):
    workspace = tmp_path / "product"
    workspace.mkdir()
    (workspace / "labels.py").write_text('def normalize(value):\n    return " ".join(value.split()).lower()\n')
    product_test = workspace / "test_labels.py"
    product_test.write_text('from labels import normalize\nassert normalize("  Hello   WORLD ") == "hello world"\nassert normalize("") == ""\n')
    subprocess.run([sys.executable, str(product_test)], cwd=workspace, check=True)
    # Preserve graph/product integration while ensuring an unverified public
    # installation cannot turn old observation milestones into acceptance.
    rejected = subprocess.run([sys.executable, str(ROOT/"taskplane/tp.py"), "flow", "start", "--profile", "protected_host",
                               "--workspace", str(workspace)], capture_output=True, text=True)
    assert rejected.returncode == 2 and json.loads(rejected.stdout)["reason"] == "unsupported_authority"
    flow.append(workspace, {"kind": "start", "run": "legacy", "session": "e2e-root",
                            "phase": "product", "goal": "Historical label work"})
    graph = json.loads(run_cli(workspace, "graph", "--workspace", str(workspace), "scan", "--decompose"))
    assert graph["files"] == 2 and graph["components"] >= 1
    lenses = json.loads(run_cli(workspace, "lens", "--workspace", str(workspace), "--files", "labels.py"))
    assert any(row["verdict"] != "n/a" for row in lenses.values())
    (workspace / "tasks.json").write_text(json.dumps({"tasks": [
        {"id": "T1", "title": "Normalize labels", "dependencies": [], "status": "done",
         "paths": ["labels.py"], "verification": "Two product assertions passed"}]}))
    run_cli(workspace, "flow", "attach", "--workspace", str(workspace), "--tasks", "tasks.json")
    for phase in ("design", "plan", "build", "evaluate", "engineering", "retro"):
        rejected = subprocess.run([sys.executable, str(ROOT/"taskplane/tp.py"), "flow", "progress",
                                   "--workspace", str(workspace), "--phase", phase],
                                  env={**os.environ, "CODEX_THREAD_ID": "e2e-root"}, capture_output=True, text=True)
        assert rejected.returncode == 2
    report = json.loads(run_cli(workspace, "flow", "report", "--full", "--workspace", str(workspace)))
    assert report["phase"] == "product" and report["status"] == "legacy_unverified"
    assert len(report["milestones"]) == 0
    page = (workspace / ".taskplane/dashboard.html").read_text()
    assert all(label in page for label in ("Task decomposition", "Dependency graph", "Lens reviews", "Tokens", "Retro"))
    assert (workspace / ".taskplane/.gitignore").read_text() == "*\n"
    # Ordinary CLI discovery exposes only the supported product.
    help_text = run_cli(workspace, "--help")
    assert all(command not in help_text for command in ("loop", "contracts", "budget", "pickup", "stage"))


def test_both_packages_install_without_legacy_runtime_and_run_the_flow(tmp_path):
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
