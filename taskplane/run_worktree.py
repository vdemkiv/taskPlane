"""One native run per linked Git worktree; never copy runtime history."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Iterator

from . import primitives, workflow as w

GUIDANCE = ('New Taskplane runs require a fresh linked Git worktree. Use flow isolate '
            '--workspace <source> --destination <new-path>, select that worktree, and '
            'prepare only the new request inputs there. Do not copy .taskplane history.')


def git(workspace: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    try:
        result = subprocess.run(['git', '-C', str(workspace), *args], env=env,
                                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise w.Refusal('run_isolation', GUIDANCE) from exc
    w.require(result.returncode == 0, 'run_isolation', GUIDANCE)
    return result.stdout.strip()


def identity(workspace: Path) -> tuple[Path, Path]:
    top = Path(git(workspace, 'rev-parse', '--show-toplevel')).resolve()
    private = Path(git(workspace, 'rev-parse', '--absolute-git-dir')).resolve()
    common = Path(git(workspace, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
    w.require(workspace.resolve() == top and private != common, 'run_isolation', GUIDANCE)
    return top, private


def read_owner(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        w.require(not path.is_symlink(), 'run_isolation', 'Worktree ownership is symlinked.')
        return None
    w.require(not path.is_symlink() and path.is_file() and path.stat().st_size <= 4096,
              'run_isolation', 'Worktree ownership is invalid; preserve it for recovery.')
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise w.Refusal('run_isolation', 'Worktree ownership is unreadable; preserve it for recovery.') from exc
    w.require(isinstance(value, dict) and value.get('schema') == 'taskplane.run-worktree/v1',
              'run_isolation', 'Worktree ownership is invalid; preserve it for recovery.')
    assert isinstance(value, dict)
    return value


def fresh_state(workspace: Path, target: Path) -> None:
    """Inspect names only: never load another run's context to start a run."""
    store = workspace / '.taskplane'
    w.require(not store.is_symlink(), 'run_isolation', 'Runtime state must be local to the new worktree.')
    if not store.exists():
        return
    with os.scandir(store) as entries:
        for number, entry in enumerate(entries):
            w.require(number < 256, 'run_isolation', 'Runtime state is not fresh. ' + GUIDANCE)
            name = entry.name
            historical = (name.startswith(('workflow-', 'initialization-', 'context', 'dashboard', 'archive'))
                          or name in {'deliveries', 'reviews', 'snapshots'})
            # Only this root's interrupted empty initialization may be retried.
            own = name in {target.name, target.name + '.lock',
                           target.stem + '.initialized.json',
                           'initialization-' + target.stem.removeprefix('workflow-') + '.json'}
            w.require(not historical or own, 'run_isolation', 'Existing run history found. ' + GUIDANCE)


@contextmanager
def reserve(workspace: Path, root: str, request: dict[str, Any], target: Path) -> Iterator[dict[str, Any]]:
    top, private = identity(workspace)
    path = private / 'taskplane-run.json'
    with primitives.file_lock(str(path)):
        owner = read_owner(path)
        expected = {'schema': 'taskplane.run-worktree/v1', 'workspace': str(top), 'root': root,
                    'request': primitives.content_fingerprint(request)}
        w.require(owner is None or owner == expected, 'run_isolation',
                  'This worktree is already assigned to a run. ' + GUIDANCE)
        fresh_state(top, target)
        if owner is None:
            primitives.atomic_json(path, expected, strict_directory_sync=True)
        yield {'path': path, 'owner': expected}


def commit(reservation: dict[str, Any], run: str) -> None:
    primitives.atomic_json(reservation['path'], {**reservation['owner'], 'run': run},
                           strict_directory_sync=True)


def verify_resume(workspace: Path, root: str, state: dict[str, Any]) -> None:
    if not state.get('run_worktree'):
        return  # Existing runs predate isolation; resuming never creates a new run.
    top, private = identity(workspace)
    owner = read_owner(private / 'taskplane-run.json')
    if owner == state['run_worktree']:
        # The run commit is authoritative after an interrupted owner publication.
        commit({'path': private / 'taskplane-run.json', 'owner': owner}, state['run'])
        owner = {**owner, 'run': state['run']}
    w.require(owner is not None and owner.get('workspace') == str(top)
              and owner.get('root') == root and owner.get('run') == state['run'],
              'run_isolation', 'Run/worktree ownership changed; explicit recovery is required.')


def create(source: Path, destination: Path) -> dict[str, str]:
    """Create from committed HEAD only. Never stash, copy, delete, or reset source."""
    source = source.resolve()
    destination = destination.absolute()
    top = Path(git(source, 'rev-parse', '--show-toplevel')).resolve()
    w.require(not destination.exists() and not destination.is_symlink()
              and not destination.resolve().is_relative_to(top),
              'run_isolation', 'Choose a new worktree path outside the source checkout.')
    w.require(not git(source, 'ls-tree', '--name-only', 'HEAD', '--', '.taskplane'),
              'run_isolation', 'The baseline tracks .taskplane history; remove it from the baseline before isolation.')
    git(source, 'worktree', 'add', '--detach', '--', str(destination), 'HEAD')
    root, _ = identity(destination)
    w.require(not (root / '.taskplane').exists(), 'run_isolation', 'Created worktree has unexpected runtime state.')
    return {'status': 'created', 'workspace': str(root), 'baseline': git(root, 'rev-parse', 'HEAD'),
            'next_action': 'Select this worktree. Activate the requested route and prepare only current-run bootstrap inputs.'}
