"""Exercise isolation with real Git worktrees and the native controller."""
import json
from pathlib import Path
import subprocess

import pytest

from taskplane import run_worktree as isolation, workflow as w, workflow_host as host
from taskplane.tests.test_workflow_evidence import prepare


def git(path, *args):
    return subprocess.run(['git', '-C', str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    git(root, 'init')
    git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '--allow-empty', '-m', 'baseline')
    return root


def start(workspace, root='root', reference='request/one'):
    initial, _, _ = prepare(workspace)
    (workspace / '.taskplane/dashboard.html').unlink()  # Fixture's synthetic old dashboard is not a bootstrap input.
    c = host.Controller(workspace, root, host.installed_adapter('codex'))
    request = {'scope': initial['scope'], 'request_reference': reference}
    return c, request


def test_create_never_copies_dirty_source_or_old_context(repository):
    old = repository / '.taskplane'
    old.mkdir()
    (old / 'history.json').write_text('old context')
    (repository / 'uncommitted.txt').write_text('keep this')
    before = git(repository, 'status', '--porcelain')
    result = isolation.create(repository, repository.parent / 'new-run')
    workspace = Path(result['workspace'])
    assert not (workspace / '.taskplane').exists()
    assert not (workspace / 'uncommitted.txt').exists()
    assert (old / 'history.json').read_text() == 'old context'
    assert git(repository, 'status', '--porcelain') == before
    assert git(workspace, 'rev-parse', 'HEAD') == git(repository, 'rev-parse', 'HEAD')


def test_main_checkout_refused_before_workflow_created(repository):
    c, request = start(repository)
    with pytest.raises(w.Refusal, match='fresh linked Git worktree'):
        c.start(request)
    assert not list((repository / '.taskplane').glob('workflow-*.json'))


def test_one_run_resume_and_other_root_refused(repository):
    workspace = Path(isolation.create(repository, repository.parent / 'run')['workspace'])
    c, request = start(workspace)
    first = c.start(request)
    before = c._path().read_bytes()
    assert c.start(request)['run'] == first['run']
    assert c._path().read_bytes() == before
    other = host.Controller(workspace, 'other-root', host.installed_adapter('codex'))
    with pytest.raises(w.Refusal, match='already assigned'):
        other.start(request)
    assert not other.adapter.state_exists()
    with pytest.raises(w.Refusal, match='fresh linked Git worktree'):
        c.start({**request, 'replace_run': first['run'], 'expected_revision': first['revision']})
    assert c._path().read_bytes() == before


def test_completed_worktree_cannot_start_another_run(repository):
    workspace = Path(isolation.create(repository, repository.parent / 'run')['workspace'])
    c, request = start(workspace)
    c.start(request)
    # Retired/finished controller shape, without pretending its phases passed.
    db = json.loads(c._path().read_text())
    db['active'] = None
    c._write(c._path(), db)
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal, match='fresh linked Git worktree'):
        c.start({**request, 'request_reference': 'request/two'})
    assert c._path().read_bytes() == before


@pytest.mark.parametrize('history', ['workflow-foreign.json', 'context', 'deliveries', 'dashboard.html'])
def test_copied_runtime_history_refused(repository, history):
    workspace = Path(isolation.create(repository, repository.parent / 'run')['workspace'])
    c, request = start(workspace)
    store = workspace / '.taskplane'
    store.mkdir(exist_ok=True)
    (store / history).write_text('must not be imported')
    with pytest.raises(w.Refusal, match='Existing run history'):
        c.start(request)
    assert (store / history).read_text() == 'must not be imported'
    assert not c.adapter.state_exists()


def test_second_worktree_has_only_its_run(repository):
    a = Path(isolation.create(repository, repository.parent / 'one')['workspace'])
    b = Path(isolation.create(repository, repository.parent / 'two')['workspace'])
    ca, ra = start(a); cb, rb = start(b)
    sa = ca.start(ra); sb = cb.start(rb)
    assert sa['run'] != sb['run']
    assert list(json.loads(cb._path().read_text())['runs']) == [sb['run']]
    assert sa['run'] not in cb._path().read_text()


def test_lost_owner_commit_recovers_same_run(repository, monkeypatch):
    workspace = Path(isolation.create(repository, repository.parent / 'run')['workspace'])
    c, request = start(workspace)
    real = isolation.commit
    monkeypatch.setattr(isolation, 'commit', lambda *_: (_ for _ in ()).throw(OSError('interrupted')))
    with pytest.raises(OSError, match='interrupted'):
        c.start(request)
    run = c.report()['run']
    monkeypatch.setattr(isolation, 'commit', real)
    assert c.start(request)['run'] == run


def test_nested_destination_and_existing_path_refused(repository):
    for target in [repository / 'child', repository]:
        with pytest.raises(w.Refusal, match='outside the source'):
            isolation.create(repository, target)


def test_cli_refuses_before_graph_or_context(repository, monkeypatch, capsys):
    from taskplane import flow
    from taskplane import depgraph
    monkeypatch.setattr(depgraph, 'scan', lambda *a, **k: pytest.fail('graph scanned before isolation check'))
    assert flow.main(['start', '--workspace', str(repository), '--request-reference', 'new']) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['reason'] == 'run_isolation'
    assert not list((repository / '.taskplane').glob('workflow-*.json'))


def test_deleted_runtime_does_not_release_worktree(repository):
    import shutil
    workspace = Path(isolation.create(repository, repository.parent / 'run')['workspace'])
    c, request = start(workspace)
    c.start(request)
    shutil.rmtree(workspace / '.taskplane')
    c, request = start(workspace)
    with pytest.raises(w.Refusal, match='already assigned'):
        c.start(request)


def test_preexisting_run_remains_resumable_without_migration(repository):
    workspace = Path(isolation.create(repository, repository.parent / 'run')['workspace'])
    c, request = start(workspace)
    first = c.start(request)
    db = json.loads(c._path().read_text())
    del db['runs'][first['run']]['run_worktree']  # Existing pre-isolation record shape.
    c._write(c._path(), db)
    before = c._path().read_bytes()
    assert c.start(request)['run'] == first['run']
    assert c._path().read_bytes() == before


def test_concurrent_chats_cannot_share_one_worktree(repository):
    from concurrent.futures import ThreadPoolExecutor
    workspace = Path(isolation.create(repository, repository.parent / 'run')['workspace'])
    c, request = start(workspace)
    other = host.Controller(workspace, 'other', host.installed_adapter('codex'))
    def attempt(controller):
        try:
            return controller.start(request)['run']
        except w.Refusal as exc:
            assert exc.reason == 'run_isolation'
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [c, other]))
    assert sum(value is not None for value in results) == 1


def test_tracked_runtime_baseline_is_refused(repository):
    (repository / '.taskplane').mkdir()
    (repository / '.taskplane/old.json').write_text('history')
    git(repository, 'add', '.taskplane')
    git(repository, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '-m', 'bad baseline')
    destination = repository.parent / 'run'
    with pytest.raises(w.Refusal, match='tracks .taskplane'):
        isolation.create(repository, destination)
    assert not destination.exists()


def test_cli_creates_then_starts_isolated_run(repository, capsys, monkeypatch):
    from taskplane import flow
    destination = repository.parent / 'cli-run'
    assert flow.main(['isolate', '--workspace', str(repository), '--destination', str(destination)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['workspace'] == str(destination)
    c, request = start(destination)
    scope = destination / '.taskplane/bootstrap/scope.json'
    scope.parent.mkdir()
    scope.write_text(json.dumps(request['scope']))
    monkeypatch.setenv('CODEX_THREAD_ID', 'root')
    assert flow.main(['start', '--workspace', str(destination), '--scope', '.taskplane/bootstrap/scope.json',
                      '--request-reference', request['request_reference']], governor=c) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['workflow']['run_worktree']['workspace'] == str(destination)
    assert not (repository / '.taskplane').exists()


def test_invalid_request_does_not_claim_worktree(repository):
    workspace = Path(isolation.create(repository, repository.parent / 'run')['workspace'])
    c, request = start(workspace)
    with pytest.raises(w.Refusal, match='scope'):
        c.start({})
    _, private = isolation.identity(workspace)
    assert not (private / 'taskplane-run.json').exists()
    assert c.start(request)['run_worktree']['workspace'] == str(workspace)
