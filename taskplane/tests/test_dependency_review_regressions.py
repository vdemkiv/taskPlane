"""Dependency findings from the whole-repository source review."""
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from taskplane import depgraph, runnability


@pytest.mark.parametrize("source,relative", [
    ("from services import billing as payments\n", "web/handler.py"),
    ("from .. import billing\n", "services/checkout/client.py"),
])
def test_python_submodule_imports_reach_the_dependency_graph(tmp_path, source, relative):
    billing = tmp_path / "services/billing/__init__.py"
    billing.parent.mkdir(parents=True)
    billing.write_text("def calculate():\n    return 1\n")
    importer = tmp_path / relative
    importer.parent.mkdir(parents=True)
    importer.write_text(source)
    graph = depgraph.scan(str(tmp_path))
    edges = {(row["from"], row["to"]) for row in graph["edges"]}
    assert (depgraph.module_of(relative), "services/billing") in edges


@pytest.mark.parametrize("name", ["calculate", "*"])
def test_python_symbol_and_star_imports_keep_module_semantics(name):
    imports, failure = depgraph._py_imports_checked(
        f"from services.billing import {name}\n", "web/handler.py",
        {"services/billing": "services/billing"})
    assert failure is None
    assert imports == {"services/billing"}


@pytest.mark.skipif(os.name == "nt", reason="uses a POSIX executable fixture")
def test_repair_at_same_path_refreshes_once_and_reuses_unchanged_probes(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    interpreter = bindir / "python3"
    interpreter.write_text("#!/bin/sh\nexit 1\n")
    interpreter.chmod(0o700)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setattr(runnability, "_cache_path", lambda _: str(tmp_path / "cache.json"))
    monkeypatch.setattr(runnability, "enabled", lambda **_: True)
    with patch.object(runnability, "probe", wraps=runnability.probe) as probe:
        failed = runnability.probe_once(str(root))
        assert failed["checks"][0]["verdict"] == runnability.BROKEN
        assert runnability.probe_once(str(root))["cached"]
        before = runnability.fingerprint(str(root))
        interpreter.write_text("#!/bin/sh\nexit 0\n")
        assert runnability.fingerprint(str(root)) != before
        repaired = runnability.probe_once(str(root))
        assert repaired["checks"][0]["verdict"] == runnability.RUNS
        for _ in range(6):
            assert runnability.probe_once(str(root))["cached"]
        assert probe.call_count == 2
