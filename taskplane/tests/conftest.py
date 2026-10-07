"""Every test gets isolated host data and local observations."""
import os
import json
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "taskplane"))


def pytest_addoption(parser):
    parser.addoption("--taskplane-archive-dir", default=None,
                     help="Verify these existing build archives instead of fresh temporary packages")
    parser.addoption("--taskplane-suite", choices=['all', 'portability', 'core', 'native', 'packages', 'capacity'], default='all')
    parser.addoption("--taskplane-host", choices=['codex', 'claude'])
    parser.addoption("--taskplane-collection-report", default=None)
    parser.addoption("--taskplane-shard-index", type=int, default=1)
    parser.addoption("--taskplane-shard-count", type=int, default=1)


def pytest_collection_modifyitems(config, items):
    from scripts.ci_local import shard_for, suite_for
    suite = config.getoption('--taskplane-suite')
    host = config.getoption('--taskplane-host')
    shard_index = config.getoption('--taskplane-shard-index')
    shard_count = config.getoption('--taskplane-shard-count')
    if not 1 <= shard_index <= shard_count:
        raise pytest.UsageError('Require 1 <= --taskplane-shard-index <= --taskplane-shard-count')
    if host and suite not in ('packages', 'capacity'):
        raise pytest.UsageError('--taskplane-host requires the packages or capacity suite')
    selected, deselected, report = [], [], []
    for item in items:
        category = suite_for(item.nodeid, capacity=item.get_closest_marker('taskplane_capacity') is not None)
        param = getattr(item, 'callspec', None)
        item_host = param.params.get('host') if param else None
        if item_host == 'openai': item_host = 'codex'
        shard = shard_for(item.nodeid, shard_count)
        keep = ((suite == 'all' or suite == category) and (not host or item_host == host)
                and shard == shard_index)
        (selected if keep else deselected).append(item)
        item.user_properties.append(('taskplane_suite', category))
        report.append({'nodeid': item.nodeid, 'suite': category, 'host': item_host,
                       'shard': shard, 'selected': keep})
    if config.getoption('--taskplane-collection-report'):
        target = Path(config.getoption('--taskplane-collection-report'))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2), encoding='utf-8')
    items[:] = selected
    if deselected: config.hook.pytest_deselected(items=deselected)


@pytest.fixture(autouse=True)
def isolated_hosts(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(("CODEX_", "CLAUDE_", "TASKPLANE_")) and key != "TASKPLANE_BROWSER_EXECUTABLE":
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
