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
import os
import tempfile

import pytest

_SESSION_HOME = tempfile.mkdtemp(prefix="tp-store-test-")
os.environ.setdefault("TASKPLANE_HOME", _SESSION_HOME)


@pytest.fixture(autouse=True)
def _isolated_taskplane_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "tp-store"))
    yield
    # monkeypatch restores the prior value automatically on teardown.
