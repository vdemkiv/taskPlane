"""Every test gets isolated host data and local observations."""
import os
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "taskplane"))


@pytest.fixture(autouse=True)
def isolated_hosts(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(("CODEX_", "CLAUDE_", "TASKPLANE_")) and key != "TASKPLANE_BROWSER_EXECUTABLE":
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
