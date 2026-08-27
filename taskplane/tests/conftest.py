"""Isolate the external taskplane store during tests.

The knowledge base lives OUTSIDE the repo, under $TASKPLANE_HOME
(default ~/.taskplane). Point that at a throwaway temp dir so tests never
touch — or pollute — the developer's real ~/.taskplane.

Two layers:
  * a session-level default (belt) so a direct `python -m unittest` run is
    covered even without the fixture;
  * an autouse fixture (suspenders) that force-sets a fresh TASKPLANE_HOME for
    EVERY test and restores it afterward — so a test that pops or overwrites
    the var can never make a LATER test fall back to the real ~/.taskplane
    (the v0.9.6 bug: test_external_store teardowns popped the var, so every
    later test wrote into the developer's real store).
"""
import pytest

# Session-level belt lives in taskplane/tests/__init__.py so the plain
# `python -m unittest discover` runner (which never reads conftest.py) gets
# the same isolation. Import it here too — but DEFENSIVELY: when pytest is run
# as `cd taskplane && pytest tests/`, the repo root isn't on sys.path and
# `import taskplane.tests` raises ModuleNotFoundError while LOADING conftest,
# which aborts the whole run before a single test collects (v2.3.1 — this was
# the documented CI break). Add the repo root to sys.path first so the import
# resolves regardless of the working directory the runner was launched from.
import os as _os
import sys as _sys
_repo_root = _os.path.dirname(_os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))
if _repo_root not in _sys.path:
    _sys.path.insert(0, _repo_root)
from taskplane.tests import _SESSION_HOME  # noqa: F401,E402


def _workflow_command_value(value, *, property_value=False):
    """Escape one GitHub workflow-command field without changing pytest."""
    text = str(value).replace("%", "%25").replace("\r", "%0D").replace(
        "\n", "%0A")
    if property_value:
        text = text.replace(":", "%3A").replace(",", "%2C")
    return text


def pytest_runtest_logreport(report):
    """Expose the exact failed test through public check annotations.

    Raw Actions logs require authentication even for this public repository,
    while check annotations are public. Emitting the normal workflow command
    keeps the hosted Windows signal diagnosable without changing collection,
    ordering, fixtures, or outcomes.
    """
    if not report.failed or not _os.environ.get("GITHUB_ACTIONS"):
        return
    path, line, _domain = report.location
    nodeid = getattr(report, "nodeid", path)
    detail = getattr(report, "longreprtext", str(report.longrepr))
    detail = detail[-6000:]
    props = (
        f"file={_workflow_command_value(path, property_value=True)},"
        f"line={int(line) + 1},"
        f"title={_workflow_command_value(nodeid, property_value=True)}"
    )
    command = f"::error {props}::{_workflow_command_value(detail)}\n"
    # pytest's fd/sys capture can swallow a normal print from this hook until
    # after the runner's workflow-command parser has stopped observing it.
    # Write to the inherited runner descriptor so the annotation is parsed in
    # real time; the fallback preserves useful local output on exotic hosts.
    try:
        _os.write(1, command.encode("utf-8", errors="replace"))
    except OSError:
        print(command, end="", flush=True)


@pytest.fixture(autouse=True)
def _isolated_taskplane_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "tp-store"))
    yield
    # monkeypatch restores the prior value automatically on teardown.


# --------------------------------------------------------------------------
# t9 (R-0011 / E2): env-mutation guard.
#
# TASKPLANE_HOME had a bespoke belt-and-suspenders because a leak there was
# caught the hard way (v0.9.6: teardowns popped the var and later tests wrote
# into the developer's real ~/.taskplane). Nothing generalized that lesson:
# ANY other variable a test sets and forgets — TASKPLANE_AUDIT_EVERY,
# TASKPLANE_ROUTER_*, PATH, HOME, GIT_* — silently changes what every LATER
# test module sees, which is exactly the class of order-dependent flake that
# is worst to debug (green alone, red in the suite, or the reverse).
#
# This module-scoped autouse fixture snapshots os.environ before a test
# module's first test and requires byte-identity after its last one, naming
# the module and every offending key. Module scope, not function scope, on
# purpose: setUpClass/module-level fixtures legitimately set vars for the
# duration of a module, and the contract that matters is that nothing
# escapes the MODULE.
#
# Ordering note: pytest sets up broader scopes first, so this snapshot is
# taken BEFORE the function-scoped monkeypatch above ever runs and is
# compared AFTER its last restore — the fixture's own TASKPLANE_HOME churn
# is invisible to the guard, as it should be.
#
# Fix a failure by restoring what you set (addCleanup / monkeypatch /
# try-finally) — never by widening an allowlist here; there is none.
#
# _RUNNER_OWNED is not an allowlist for test code: PYTEST_CURRENT_TEST is
# written by pytest itself on every setup/call/teardown transition, so it is
# never byte-stable and never a signal about the module under guard.
_RUNNER_OWNED = ("PYTEST_CURRENT_TEST",)


def _env_snapshot():
    return {k: v for k, v in _os.environ.items() if k not in _RUNNER_OWNED}


@pytest.fixture(autouse=True, scope="module")
def _env_mutation_guard(request):
    before = _env_snapshot()
    yield
    after = _env_snapshot()
    if after == before:
        return
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(k for k in set(before) & set(after)
                     if before[k] != after[k])
    detail = []
    if added:
        detail.append("SET and not unset: "
                      + ", ".join(f"{k}={after[k]!r}" for k in added))
    if removed:
        detail.append("DELETED and not restored: "
                      + ", ".join(f"{k}(was {before[k]!r})" for k in removed))
    if changed:
        detail.append("OVERWRITTEN and not restored: "
                      + ", ".join(f"{k}: {before[k]!r} -> {after[k]!r}"
                                  for k in changed))
    raise AssertionError(
        f"env leak from test module {request.node.name}: os.environ is not "
        "byte-identical after the module ran — "
        + "; ".join(detail)
        + ". Restore every variable you set (addCleanup/monkeypatch/"
          "try-finally); a leaked variable changes what every LATER test "
          "module sees.")
