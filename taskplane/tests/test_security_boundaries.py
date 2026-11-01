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


@pytest.fixture
def git_review_fixture():
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory(prefix='taskplane-git-base-') as directory:
        root = Path(directory).resolve()
        repo = root / 'repo'; repo.mkdir()
        def git(*args):
            return subprocess.run(['git', *args], cwd=repo, check=True,
                                  capture_output=True, text=True, timeout=10)
        git('init')
        git('config', 'user.name', 'Fixture')
        git('config', 'user.email', 'fixture@example.invalid')
        (repo / 'app.py').write_text('before\n')
        git('add', 'app.py'); git('commit', '-m', 'fixture')
        git('branch', 'review-base')
        (repo / 'app.py').write_text('after\n')
        sentinel = root / 'outside-review.txt'; sentinel.write_bytes(b'preserve me\n')
        yield repo, sentinel


@pytest.mark.parametrize('entry', ['changed', 'review', 'impact', 'html'])
def test_option_shaped_git_base_refuses_without_outside_write(git_review_fixture, entry):
    from taskplane import tp
    repo, sentinel = git_review_fixture
    base = '--output=' + str(sentinel)
    before = sentinel.read_bytes()
    if entry == 'changed':
        try:
            with pytest.raises(ValueError, match='base'):
                tp._changed(str(repo), base)
        finally:
            assert sentinel.read_bytes() == before
    else:
        command = (['review', 'start', '--scope', 'diff'] if entry == 'review'
                   else ['graph', '--workspace', str(repo), entry])
        if entry == 'review': command += ['--workspace', str(repo)]
        result = tp.main([*command, '--base=' + base])
        assert sentinel.read_bytes() == before
        assert result == 1


def test_option_shaped_base_refused_before_enumeration_or_patch(git_review_fixture, monkeypatch):
    from taskplane import tp
    repo, sentinel = git_review_fixture
    def unexpected(*args, **kwargs):
        pytest.fail('Option-shaped base reached Git or source enumeration')
    monkeypatch.setattr(tp, '_git', unexpected)
    monkeypatch.setattr(tp, '_changed', unexpected)
    assert tp.main(['review', 'start', '--workspace', str(repo),
                    '--base=--output=' + str(sentinel)]) == 1
    assert sentinel.read_bytes() == b'preserve me\n'


@pytest.mark.parametrize('base', ['HEAD', 'review-base', 'HEAD~0'])
def test_normal_git_refs_preserve_enumeration_and_patch(git_review_fixture, base):
    from taskplane import tp
    repo, sentinel = git_review_fixture
    assert tp._changed(str(repo), base) == ['app.py']
    assert tp.main(['review', 'start', '--workspace', str(repo), '--base', base]) == 0
    source = json.loads((repo / '.taskplane/review-source.json').read_text())
    assert source['files'] == ['app.py']
    assert '-before' in source['patch'] and '+after' in source['patch']
    assert sentinel.read_bytes() == b'preserve me\n'
