"""The stage lifecycle has a distinct, bounded CLI surface."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from taskplane import tp as cli


def _request_file(tmp_path: Path, value: dict[str, object]) -> Path:
    path = tmp_path / "stage-request.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("action", "runtime_command"),
    [
        ("start", "start"),
        ("resume", "resume"),
        ("terminalize", "terminalize"),
        ("terminalize-and-start", "terminalize-and-start"),
        ("split", "split"),
        ("history", "history"),
        ("reuse", "reuse"),
    ],
)
def test_stage_mutation_commands_forward_one_json_request(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
        action: str, runtime_command: str) -> None:
    import loop

    request = {
        "schema": "taskplane.stage-command/v1",
        "run_id": "run-r0004",
        "operation_id": "operation-001",
        "expected_revision": 4,
    }
    request_path = _request_file(tmp_path, request)
    observed: list[object] = []

    def stage_command(workspace: str, command: str,
                      payload: dict[str, object]) -> dict[str, object]:
        observed.extend([workspace, command, payload])
        return {
            "schema": "taskplane.stage-command-result/v1",
            "command": command,
            "receipt": {"operation_id": payload["operation_id"]},
        }

    monkeypatch.setattr(loop, "stage_command", stage_command, raising=False)

    assert cli.main([
        "stage", "--workspace", str(tmp_path), action,
        "--request", str(request_path),
    ]) == 0

    assert observed == [str(tmp_path), runtime_command, request]
    assert json.loads(capsys.readouterr().out) == {
        "schema": "taskplane.stage-command-result/v1",
        "command": runtime_command,
        "receipt": {"operation_id": "operation-001"},
    }


def test_stage_request_can_be_read_from_stdin(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    import loop

    request = {"run_id": "run-r0004", "operation_id": "resume-001"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    monkeypatch.setattr(
        loop, "stage_command",
        lambda workspace, command, payload: {
            "workspace": workspace, "command": command, "request": payload,
        }, raising=False)

    assert cli.main([
        "stage", "--workspace", str(tmp_path), "resume", "--request", "-",
    ]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "workspace": str(tmp_path), "command": "resume", "request": request,
    }


def test_terminalize_and_start_is_exposed_in_generated_help() -> None:
    result = subprocess.run(
        [sys.executable, str(Path(cli.__file__)), "help", "--md"],
        text=True, capture_output=True, check=False,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stderr
    generated = result.stdout

    assert "`tp.py stage terminalize-and-start`" in generated
    assert "`taskplane.stage-command/v1`" in generated
    assert "| `terminalize-and-start` |" in generated
    assert "`predecessor_stage_id`" in generated
    assert "`successor_stage`" in generated


def test_generated_stage_request_fields_match_runtime_validator() -> None:
    import loop

    documented = {
        command: frozenset(fields)
        for command, fields in cli._CLI_STAGE_REQUEST_FIELDS.items()
    }
    assert documented == loop._STAGE_REQUEST_FIELDS


def test_stage_history_uses_the_same_bounded_request_boundary(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    import loop

    request = {"run_id": "run-r0004", "cursor": "page-2", "limit": 17}
    request_path = _request_file(tmp_path, request)
    calls: list[tuple[str, str, dict[str, object]]] = []

    def stage_command(workspace: str, command: str,
                      payload: dict[str, object]) -> dict[str, object]:
        calls.append((workspace, command, payload))
        return {
            "schema": "taskplane.stage-history-page/v1",
            "stages": [], "cursor": payload["cursor"], "next_cursor": None,
        }

    monkeypatch.setattr(loop, "stage_command", stage_command, raising=False)

    assert cli.main([
        "stage", "--workspace", str(tmp_path), "history",
        "--request", str(request_path),
    ]) == 0
    assert calls == [(str(tmp_path), "history", request)]
    assert json.loads(capsys.readouterr().out)["stages"] == []


def test_stage_request_must_be_a_json_object(
        tmp_path: Path, capsys) -> None:
    request_path = tmp_path / "not-an-object.json"
    request_path.write_text("[]", encoding="utf-8")

    assert cli.main([
        "stage", "--workspace", str(tmp_path), "history",
        "--request", str(request_path),
    ]) == 1

    assert json.loads(capsys.readouterr().out) == {
        "error": "stage request must be a JSON object",
    }


def test_stage_runtime_refusal_is_nonzero_and_machine_readable(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    import loop

    request_path = _request_file(tmp_path, {"run_id": "legacy-run"})
    monkeypatch.setattr(
        loop, "stage_command",
        lambda *_args, **_kwargs: {
            "error": "stage-native writes are disabled for an unmigrated run",
            "fallback": "legacy-read-only",
        }, raising=False)

    assert cli.main([
        "stage", "--workspace", str(tmp_path), "start",
        "--request", str(request_path),
    ]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "error": "stage-native writes are disabled for an unmigrated run",
        "fallback": "legacy-read-only",
    }


def test_importing_cli_does_not_eagerly_import_stage_entities() -> None:
    taskplane_dir = Path(cli.__file__).resolve().parent
    script = (
        "import json,sys; "
        f"sys.path.insert(0, {str(taskplane_dir)!r}); "
        "import tp; "
        "print(json.dumps('stage_entities' in sys.modules))"
    )
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, "-c", script], text=True, capture_output=True,
        encoding="utf-8", errors="replace", env=env, check=False)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) is False
