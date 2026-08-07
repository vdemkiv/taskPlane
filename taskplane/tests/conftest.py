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


@pytest.fixture(autouse=True)
def _isolated_taskplane_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "tp-store"))
    yield
    # monkeypatch restores the prior value automatically on teardown.
