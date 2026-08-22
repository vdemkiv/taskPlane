"""R-0006 B4: public engine failures have one actionable CLI envelope."""
from __future__ import annotations

import contextlib
import io
from pathlib import Path
import shlex
import sys

import pytest


TASKPLANE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TASKPLANE))

import storage  # noqa: E402
import taskplane_lite as tpl  # noqa: E402
import tp as cli  # noqa: E402


REQUIRED_PUBLIC_ERRORS = frozenset({
    tpl.StateError,
    storage.StorageIdentityError,
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


def test_public_registry_is_the_only_known_error_source():
    assert isinstance(cli.PUBLIC_ENGINE_ERROR_REGISTRY, dict)
    assert frozenset(cli.PUBLIC_ENGINE_ERROR_REGISTRY) == cli.KNOWN_ENGINE_ERRORS
    assert REQUIRED_PUBLIC_ERRORS <= cli.KNOWN_ENGINE_ERRORS
    for error_class, envelope in cli.PUBLIC_ENGINE_ERROR_REGISTRY.items():
        assert isinstance(error_class, type)
        assert issubclass(error_class, BaseException)
        assert set(envelope) == {
            "headline", "recovery", "exit_code", "debug_cause",
        }
        assert envelope["headline"].strip()
        assert envelope["recovery"].strip()
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
    assert "recovery: " in stderr
    recovery = stderr.split("recovery: ", 1)[1].splitlines()[0]
    tokens = shlex.split(recovery)
    assert tokens and tokens[0].endswith("tp.py")
    assert Path(tokens[0]).samefile(TASKPLANE / "tp.py")
    assert Path(tokens[0]).stat().st_mode & 0o111
    assert tokens[1] == "onboard"
    assert "--workspace" in tokens
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
