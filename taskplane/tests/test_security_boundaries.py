"""Concrete security regressions from the 36-hour remediation intake."""
import importlib.util
import json
from pathlib import Path
import pytest
from taskplane import primitives

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('package_security', ROOT/'scripts/package_plugin.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


@pytest.mark.parametrize('depth', [1, 2])
def test_package_refuses_symlink_directory_before_read(tmp_path, monkeypatch, depth):
    root = tmp_path/'repo'; root.mkdir()
    outside = tmp_path/'private'; outside.mkdir(); (outside/'sentinel.txt').write_text('private')
    parent = root
    if depth == 2:
        parent = root/'skills'; parent.mkdir()
    link = parent/'linked'
    try: link.symlink_to(outside, target_is_directory=True)
    except OSError: pytest.skip('Native account cannot create symlinks')
    monkeypatch.setattr(package, 'ROOT', root)
    with pytest.raises(ValueError, match='symlink'):
        package.read_member(link/'sentinel.txt')


def test_package_refuses_version_mismatch_and_path_version(tmp_path, monkeypatch):
    monkeypatch.setattr(package, 'ROOT', tmp_path)
    for directory, version in [('.codex-plugin','1.0.0'),('.claude-plugin','1.0.1')]:
        (tmp_path/directory).mkdir(); (tmp_path/directory/'plugin.json').write_text(json.dumps({'version':version}))
    with pytest.raises(ValueError, match='disagree'): package.package('openai', tmp_path/'dist')
    (tmp_path/'.codex-plugin/plugin.json').write_text(json.dumps({'version':'../../escape'}))
    with pytest.raises(ValueError, match='semantic version'): package.package('openai', tmp_path/'dist')


@pytest.mark.parametrize('command,shell', [('echo injected', False), (['echo','injected'], True)])
def test_runtime_subprocess_cannot_enable_shell(tmp_path, command, shell):
    with pytest.raises(ValueError, match='argv'):
        primitives._run(command, tmp_path, shell=shell)


def test_all_ci_checkouts_drop_credentials():
    source = (ROOT/'.github/workflows/ci.yml').read_text().splitlines()
    checkouts = [i for i,line in enumerate(source) if 'uses: actions/checkout@' in line]
    assert checkouts
    assert all('persist-credentials: false' in source[i+1] for i in checkouts)
