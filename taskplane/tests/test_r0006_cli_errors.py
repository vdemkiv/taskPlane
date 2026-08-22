"""R-0006 B4: public engine failures have one actionable CLI envelope."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest


TASKPLANE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TASKPLANE))

import storage  # noqa: E402
import run_store  # noqa: E402
import taskplane_lite as tpl  # noqa: E402
import tp as cli  # noqa: E402


REQUIRED_PUBLIC_ERRORS = frozenset({
    tpl.StateError,
    storage.StorageIdentityError,
    run_store.TaskplaneCompatibilityError,
})


def _error(error_class: type[BaseException]) -> BaseException:
    if error_class is tpl.StateError:
        return error_class(
            "/tmp/taskplane-state.json", "governed state is corrupt",
            "restore the state file from version control")
    return error_class("fixture refusal")


def _raising_summary(error: BaseException):
    def command(_args):
        raise error

    return command


def _capture_main(argv):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        return cli.main(argv), stdout.getvalue(), stderr.getvalue()


def _rendered_recovery_commands(stderr: str, envelope: dict) \
        -> list[list[str]]:
    value = envelope["recovery"]
    expected = (value,) if isinstance(value, str) else tuple(value)
    commands = []
    for index in range(1, len(expected) + 1):
        suffix = "" if len(expected) == 1 else f" {index}/{len(expected)}"
        prefix = f"recovery{suffix}: "
        assert prefix in stderr
        commands.append(shlex.split(
            stderr.split(prefix, 1)[1].splitlines()[0]))
    return commands


def test_public_registry_is_the_only_known_error_source():
    assert isinstance(cli.PUBLIC_ENGINE_ERROR_REGISTRY, dict)
    assert frozenset(cli.PUBLIC_ENGINE_ERROR_REGISTRY) == cli.KNOWN_ENGINE_ERRORS
    assert REQUIRED_PUBLIC_ERRORS == cli.KNOWN_ENGINE_ERRORS
    for error_class, envelope in cli.PUBLIC_ENGINE_ERROR_REGISTRY.items():
        assert isinstance(error_class, type)
        assert issubclass(error_class, BaseException)
        assert set(envelope) == {
            "headline", "recovery", "action_label", "exit_code",
            "debug_cause",
        }
        assert envelope["headline"].strip()
        recovery = envelope["recovery"]
        commands = (recovery,) if isinstance(recovery, str) else tuple(recovery)
        assert commands and all(str(command).strip() for command in commands)
        assert envelope["action_label"] == "recovery"
        assert envelope["exit_code"] in {1, 2}
        assert envelope["debug_cause"] == "reraise"


@pytest.mark.parametrize(
    "error_class", tuple(getattr(cli, "PUBLIC_ENGINE_ERROR_REGISTRY", {})),
)
def test_every_registered_error_is_concise_actionable_and_traceback_free(
        error_class, monkeypatch, tmp_path):
    envelope = cli.PUBLIC_ENGINE_ERROR_REGISTRY[error_class]
    monkeypatch.setattr(cli, "cmd_summary", _raising_summary(_error(error_class)))
    monkeypatch.delenv("TASKPLANE_DEBUG", raising=False)

    rc, stdout, stderr = _capture_main(
        ["summary", "--workspace", str(tmp_path)])

    assert rc == envelope["exit_code"]
    assert stdout == ""
    assert f"taskplane: {envelope['headline']}" in stderr
    assert "fixture refusal" in stderr or "governed state is corrupt" in stderr
    commands = _rendered_recovery_commands(stderr, envelope)
    assert commands and all(commands)
    if error_class is run_store.TaskplaneCompatibilityError:
        assert commands == [
            ["codex", "plugin", "marketplace", "add", "vdemkiv/taskPlane"],
            ["codex", "plugin", "add", "taskplane"],
        ]
    else:
        tokens = commands[0]
        assert tokens[0].endswith("tp.py")
        assert Path(tokens[0]).samefile(TASKPLANE / "tp.py")
        assert Path(tokens[0]).stat().st_mode & 0o111
        assert "--workspace" in tokens
        if error_class is storage.StorageIdentityError:
            assert tokens[1:3] == ["repository", "prepare"]
            assert tokens[3] == str(tmp_path)
        else:
            assert tokens[1] == "onboard"
            assert "--json" in tokens
    assert "Traceback (most recent call last)" not in stderr


@pytest.mark.parametrize(
    "error_class", tuple(getattr(cli, "PUBLIC_ENGINE_ERROR_REGISTRY", {})),
)
def test_debug_reraises_every_registered_error(
        error_class, monkeypatch, tmp_path):
    error = _error(error_class)
    monkeypatch.setattr(cli, "cmd_summary", _raising_summary(error))
    monkeypatch.setenv("TASKPLANE_DEBUG", "1")

    with pytest.raises(error_class) as caught:
        cli.main(["summary", "--workspace", str(tmp_path)])

    assert caught.value is error
    assert caught.value.__traceback__ is not None


@pytest.mark.parametrize("debug", [False, True], ids=["normal", "debug"])
def test_startup_compatibility_refusal_uses_registered_preparser_envelope(
        debug, monkeypatch, tmp_path):
    error = run_store.TaskplaneCompatibilityError(
        "required stage dependency cannot load")

    def refuse():
        raise error

    monkeypatch.setattr(
        cli.repository_run_store, "ensure_stage_compatibility", refuse)
    if debug:
        monkeypatch.setenv("TASKPLANE_DEBUG", "1")
        with pytest.raises(run_store.TaskplaneCompatibilityError) as caught:
            cli.main(["summary", "--workspace", str(tmp_path)])
        assert caught.value is error
        assert caught.value.__traceback__ is not None
    else:
        monkeypatch.delenv("TASKPLANE_DEBUG", raising=False)
        rc, stdout, stderr = _capture_main(
            ["summary", "--workspace", str(tmp_path)])
        assert rc == 2
        assert stdout == ""
        assert stderr.count("TaskplaneCompatibilityError") == 1
        assert "taskplane: engine compatibility check failed" in stderr
        assert _rendered_recovery_commands(
            stderr,
            cli.PUBLIC_ENGINE_ERROR_REGISTRY[
                run_store.TaskplaneCompatibilityError]) == [
                    ["codex", "plugin", "marketplace", "add",
                     "vdemkiv/taskPlane"],
                    ["codex", "plugin", "add", "taskplane"],
                ]
        assert "Traceback" not in stderr
    assert list(tmp_path.iterdir()) == []


def test_compatibility_recovery_sequence_is_codex_parser_valid():
    codex = shutil.which("codex")
    if codex is None:
        pytest.skip("Codex CLI is not installed on this runner")
    recovery = cli.PUBLIC_ENGINE_ERROR_REGISTRY[
        run_store.TaskplaneCompatibilityError]["recovery"]
    commands = [shlex.split(command) for command in recovery]

    assert commands == [
        ["codex", "plugin", "marketplace", "add", "vdemkiv/taskPlane"],
        ["codex", "plugin", "add", "taskplane"],
    ]
    for command in commands:
        parsed = subprocess.run(
            [codex, *command[1:], "--help"], text=True, encoding="utf-8",
            errors="replace", capture_output=True, check=False)
        assert parsed.returncode == 0, parsed.stderr
        assert "Usage:" in parsed.stdout


def _git(*args, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), text=True, encoding="utf-8",
        errors="replace", capture_output=True, check=True)


@pytest.mark.parametrize(
    "locator_defect", ["invalid-schema", "mismatched-checkout"])
def test_storage_identity_recovery_executes_and_rebinds_invalid_locator(
        locator_defect, tmp_path):
    workspace = tmp_path / "repository"
    workspace.mkdir()
    _git("init", "-q", cwd=workspace)
    _git("config", "user.name", "taskPlane test", cwd=workspace)
    _git("config", "user.email", "taskplane@example.invalid", cwd=workspace)
    (workspace / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git("add", "README.md", cwd=workspace)
    _git("commit", "-q", "-m", "fixture", cwd=workspace)

    state_home = tmp_path / "taskplane-home"
    identity = storage.resolve_repository_identity(str(workspace))
    layout = storage.resolve_layout(
        identity, home=str(state_home), run_id="invalid-locator")
    locator_path = Path(storage.write_workspace_locator(
        str(workspace), identity=identity, layout=layout,
        run_id="invalid-locator"))
    locator_value = json.loads(locator_path.read_text(encoding="utf-8"))
    if locator_defect == "invalid-schema":
        locator_value["schema"] = "invalid"
    else:
        locator_value["checkout"] = str(tmp_path / "another-checkout")
    locator_path.write_text(
        json.dumps(locator_value) + "\n", encoding="utf-8")

    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "TASKPLANE_HOME": str(state_home),
    }
    env.pop("TASKPLANE_DEBUG", None)
    command = [
        sys.executable, str(TASKPLANE / "tp.py"), "onboard",
        "--workspace", str(workspace), "--json",
    ]
    refused = subprocess.run(
        command, cwd=str(workspace), env=env, text=True, encoding="utf-8",
        errors="replace", capture_output=True, check=False)

    assert refused.returncode == 1
    assert refused.stdout == ""
    assert "StorageIdentityError" in refused.stderr
    assert "Traceback" not in refused.stderr
    recovery = refused.stderr.split("recovery: ", 1)[1].splitlines()[0]
    recovery_argv = shlex.split(recovery)
    assert recovery_argv[1:3] == ["repository", "prepare"]
    assert recovery_argv[3:] == [
        str(workspace), "--workspace", str(workspace)]

    repaired = subprocess.run(
        recovery_argv, cwd=str(workspace), env=env, text=True,
        encoding="utf-8", errors="replace", capture_output=True,
        check=False)

    assert repaired.returncode == 0, repaired.stderr
    assert json.loads(repaired.stdout)["status"] == "ready"
    locator = storage.load_workspace_locator(str(workspace))
    assert locator is not None
    assert locator["schema"] == "taskplane.workspace/v1"
    assert Path(locator["home"]).samefile(state_home)

    retried = subprocess.run(
        command, cwd=str(workspace), env=env, text=True, encoding="utf-8",
        errors="replace", capture_output=True, check=False)
    assert retried.returncode == 0, retried.stderr
    assert "StorageIdentityError" not in retried.stderr


def test_unregistered_error_keeps_exit_70_and_traceback(
        monkeypatch, tmp_path):
    monkeypatch.setattr(
        cli, "cmd_summary", _raising_summary(ValueError("unexpected defect")))
    monkeypatch.delenv("TASKPLANE_DEBUG", raising=False)

    rc, stdout, stderr = _capture_main(
        ["summary", "--workspace", str(tmp_path)])

    assert rc == 70
    assert stdout == ""
    assert "ValueError: unexpected defect" in stderr
    assert "Traceback (most recent call last)" in stderr
