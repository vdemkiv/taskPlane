"""R-0006 B3: graph degradation is producer-complete and gateable."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TASKPLANE = ROOT / "taskplane"
TP = TASKPLANE / "tp.py"
sys.path.insert(0, str(TASKPLANE))

import depgraph as dg  # noqa: E402
import tp as tp_cli  # noqa: E402


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


def _broken_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    module = ws / "app"
    module.mkdir(parents=True)
    (module / "good.py").write_text("VALUE = 1\n", encoding="utf-8")
    # This is large enough to make decomposition parse the module as well as
    # the base Python import scanner.  The one bad file therefore proves the
    # two producer sections are additive instead of one masking the other.
    broken = "".join(f"# line {i}\n" for i in range(620)) + "def broken(:\n"
    (module / "broken.py").write_text(broken, encoding="utf-8")
    _git("init", "-q", cwd=ws)
    _git("config", "user.email", "graph@example.test", cwd=ws)
    _git("config", "user.name", "Graph Fixture", cwd=ws)
    _git("add", "-A", cwd=ws)
    _git("commit", "-qm", "fixture", cwd=ws)
    return ws


def _quality(graph: dict) -> dict:
    quality = (graph.get("meta") or {}).get("graph_scan_quality") or {}
    assert quality.get("schema") == "taskplane.graph-scan-quality/v1"
    return quality


def _base_failure(quality: dict) -> dict:
    failures = quality["producers"]["base-scanner"]["failures"]
    assert len(failures) == 1
    return failures[0]


@pytest.mark.parametrize("decompose", [False, True])
def test_base_python_failure_is_structured_in_both_scan_modes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, decompose: bool):
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "store"))
    ws = _broken_workspace(tmp_path)

    graph = dg.scan(str(ws), decompose=decompose)
    quality = _quality(graph)
    failure = _base_failure(quality)

    assert quality["degraded"] is True
    assert quality["mode"] == ("components" if decompose else "modules")
    assert quality["affected_modules"] == ["app"]
    assert failure["file"] == "app/broken.py"
    assert failure["module"] == "app"
    assert failure["parser"] == "python-ast"
    assert failure["error_class"] == "SyntaxError"
    assert failure["reason"]
    assert "line 621" in failure["reason"]
    assert len(failure["file_fingerprint"]) == 64
    assert quality["recovery"] == "repair the named source/producer and rerun `tp graph scan --strict`"

    decomposition = quality["producers"]["decomposition"]
    if decompose:
        assert decomposition["status"] == "degraded"
        assert any(row["module"] == "app" and
                   row["file"] == "app/broken.py" and
                   row["parser"] == "python-ast"
                   for row in decomposition["failures"])
    else:
        assert decomposition == {"status": "not-requested", "failures": []}


def test_repeated_and_legacy_cached_scans_preserve_base_failures(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "store"))
    ws = _broken_workspace(tmp_path)
    first = dg.scan(str(ws))
    expected = _base_failure(_quality(first))

    second = dg.scan(str(ws))
    assert _base_failure(_quality(second)) == expected

    # Simulate a graph written by the previous scanner: same mtime/hash cache
    # row, but no proof that Python parsing happened and no failure record.
    legacy = dg.load(str(ws))
    legacy["files"]["app/broken.py"].pop("parse_checked", None)
    legacy["files"]["app/broken.py"].pop("parse_failure", None)
    legacy["meta"].pop("graph_scan_quality", None)
    dg.save(str(ws), legacy)
    repaired = dg.scan(str(ws))
    assert _base_failure(_quality(repaired)) == expected


def _run_graph(ws: Path, store: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["TASKPLANE_HOME"] = str(store)
    return subprocess.run(
        [sys.executable, str(TP), "graph", "--workspace", str(ws),
         "scan", *args], cwd=ws, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace")


@pytest.mark.parametrize("decompose", [False, True])
def test_cli_normal_and_strict_json_and_text_are_honest(
        tmp_path: Path, decompose: bool):
    ws = _broken_workspace(tmp_path)
    store = tmp_path / "store"
    dec = ["--decompose"] if decompose else []

    normal_json = _run_graph(ws, store, *dec, "--json")
    assert normal_json.returncode == 0, normal_json.stderr
    payload = json.loads(normal_json.stdout)
    assert payload["degraded"] is True
    assert payload["graph_quality"]["degraded"] is True
    assert payload["graph_quality"]["failures"][0]["file"] == "app/broken.py"
    assert "tp graph scan --strict" in payload["graph_quality"]["recovery"]

    normal_text = _run_graph(ws, store, *dec, "--text")
    assert normal_text.returncode == 0, normal_text.stderr
    assert "degraded=true" in normal_text.stdout
    assert "base-scanner" in normal_text.stdout
    assert "app/broken.py" in normal_text.stdout
    assert "app" in normal_text.stdout
    assert "SyntaxError" in normal_text.stdout
    assert "line 621" in normal_text.stdout
    assert "tp graph scan --strict" in normal_text.stdout

    strict_json = _run_graph(ws, store, *dec, "--strict", "--json")
    assert strict_json.returncode != 0
    assert json.loads(strict_json.stdout)["graph_quality"] == \
        payload["graph_quality"]

    strict_text = _run_graph(ws, store, *dec, "--strict", "--text")
    assert strict_text.returncode != 0
    assert "app/broken.py" in strict_text.stdout
    assert "degraded=true" in strict_text.stdout


def test_readiness_and_completion_consume_the_persisted_quality_record(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "store"))
    ws = _broken_workspace(tmp_path)
    dg.scan(str(ws))

    ready = dg.readiness(str(ws), [{
        "id": "t1", "scope": ["app/broken.py"], "new_modules": []}])
    assert ready["passed"] is False
    assert any("graph scan quality is degraded" in row for row in ready["errors"])
    assert "app/broken.py" in " ".join(ready["errors"])

    done = dg.completion(str(ws), ["app/broken.py"],
                         planned_modules=["app"])
    assert done["passed"] is False
    assert any("graph scan quality is degraded" in row for row in done["errors"])


@pytest.mark.parametrize("surface", ["loop-next", "loop-gate", "dod"])
def test_governed_loop_and_dod_cli_entries_refuse_degraded_graph(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str], surface: str):
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "store"))
    ws = _broken_workspace(tmp_path)
    dg.scan(str(ws))

    if surface.startswith("loop-"):
        code = tp_cli.cmd_loop(SimpleNamespace(
            workspace=str(ws), loop_action=surface.removeprefix("loop-")))
    else:
        code = tp_cli.cmd_dod(SimpleNamespace(workspace=str(ws)))

    output = capsys.readouterr().out
    assert code != 0
    assert "graph scan quality is degraded" in output
    assert "app/broken.py" in output


def test_standalone_review_continues_with_degraded_warning_and_floors(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]):
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "store"))
    ws = _broken_workspace(tmp_path)
    (ws / "app" / "good.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git("add", "-A", cwd=ws)
    _git("commit", "-qm", "review target", cwd=ws)
    dg.scan(str(ws))

    # Keep the test on the standalone Review route while exposing its sealed
    # slots immediately; the separate execution-choice tests own that human
    # preflight boundary.
    import review as review_kernel
    monkeypatch.setattr(review_kernel, "review_execution_preflight",
                        lambda **_kwargs: None)

    code = tp_cli.main([
        "review", "start", "HEAD", "--base", "HEAD~1",
        "--workspace", str(ws),
    ])
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert output["status"] == "ready"
    assert output["graph_degraded"] is True
    assert output["slots"]
    warning = output["preflight"]["graph_quality_warning"]
    assert warning["status"] == "degraded"
    assert warning["continuation"] == \
        "immutable_diff_with_architecture_security_floors"
    assert "app/broken.py" in warning["reason"]
    quality_path = ws / output["graph_quality"]["relative_path"]
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    assert quality["review_fallback"]["mode"] == "immutable_diff"
    assert quality["review_fallback"]["guardrails"] == [
        "architecture_floor", "security_floor"]
    assert "caller_coverage_incomplete" in quality["reasons"]
    assert quality["impact"]["unknown_reason"] == "graph_scan_degraded"
    scan_quality = quality["impact"]["graph_scan_quality"]
    assert scan_quality["degraded"] is True
    assert scan_quality["failures"][0]["file"] == \
        "app/broken.py"


def test_clean_scan_keeps_legacy_cli_shape_and_allows_strict(
        tmp_path: Path):
    ws = tmp_path / "clean"
    (ws / "app").mkdir(parents=True)
    (ws / "app" / "ok.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git("init", "-q", cwd=ws)
    _git("config", "user.email", "graph@example.test", cwd=ws)
    _git("config", "user.name", "Graph Fixture", cwd=ws)
    _git("add", "-A", cwd=ws)
    _git("commit", "-qm", "fixture", cwd=ws)

    result = _run_graph(ws, tmp_path / "store", "--strict")
    assert result.returncode == 0, result.stderr
    assert list(json.loads(result.stdout)) == [
        "modules", "edges", "files", "stored"]
