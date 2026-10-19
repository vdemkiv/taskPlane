"""Exact runtime command identity and workspace selection shared by CLI/hooks."""
from __future__ import annotations

from pathlib import Path
import os
import shutil
import sys
from typing import Any


def workspace_selector(words: list[str]) -> str | None:
    """Accept one exact selector; never let argparse abbreviate or choose the last."""
    selected = None
    seen = False
    for index, word in enumerate(words):
        option = word.split('=', 1)[0]
        if option.startswith('--') and option != '--workspace' and '--workspace'.startswith(option):
            raise ValueError('Use the exact --workspace option; abbreviations are refused.')
        if option != '--workspace':
            continue
        if seen:
            raise ValueError('Supply --workspace exactly once; duplicate selectors are refused.')
        seen = True
        selected = (word.split('=', 1)[1] if '=' in word else
                    words[index + 1] if index + 1 < len(words) else '')
        if not selected or selected.startswith('-'):
            raise ValueError('--workspace requires one explicit path.')
    return selected


def execution_directory(event: dict[str, Any], fallback: Path) -> Path:
    """Resolve the shell's declared workdir, independently of the selected project."""
    cwd = Path(event.get('cwd') or fallback).resolve()
    args = event.get('tool_input', {})
    if not isinstance(args, dict):
        raise ValueError('Command input must be an object.')
    value = args.get('workdir') if (event.get('tool_name') or event.get('tool')) == 'exec_command' else None
    if value is None:
        return cwd
    if not isinstance(value, str) or not value or '\0' in value:
        raise ValueError('Command workdir must be an explicit path.')
    return (cwd / value).resolve()


def reserved_directory_escape(words: list[str], event: dict[str, Any]) -> Path | None:
    """Recognize only a literal root-shell return from its reserved store.

    This selects a containing project for hook inspection, never a new store or
    a general command exception. The caller must still verify the root actor.
    """
    if (event.get('hook_event_name') not in {'PreToolUse', 'PostToolUse'}
            or (event.get('tool_name') or event.get('tool')) != 'Bash'
            or any(event.get(key) for key in ('parent_session_id', 'agent_id', 'subagent_id'))
            or len(words) != 2 or words[0] != 'cd'):
        return None
    args = event.get('tool_input', {})
    if (not isinstance(args, dict) or set(args) - {'command', 'description', 'timeout'}
            or 'description' in args and not isinstance(args['description'], str)
            or 'timeout' in args and (type(args['timeout']) is not int or not 0 < args['timeout'] <= 600000)):
        return None
    supplied = event.get('cwd')
    if not isinstance(supplied, str) or not Path(supplied).is_absolute():
        return None
    cwd = Path(supplied)
    if '.taskplane' not in cwd.parts or cwd != cwd.resolve():
        return None
    containing = Path(*cwd.parts[:cwd.parts.index('.taskplane')])
    # An absolute exact spelling excludes cd options, traversal and shell
    # expansion. command_words has already refused operators and substitutions.
    return containing if words[1] == str(containing) else None


def validate_workdir(words: list[str], event: dict[str, Any], fallback: Path) -> Path:
    cwd = execution_directory(event, fallback)
    if (len(words) >= 3 and Path(words[1]).name == 'tp.py' and not diagnostic(words)
            and cwd != Path(event.get('cwd') or fallback).resolve()):
        selected = workspace_selector(words[2:])
        if not Path(words[1]).is_absolute() or selected is None or not Path(selected).is_absolute():
            raise ValueError('A differing command workdir requires an absolute installed launcher '
                             'and one explicit absolute --workspace matching the selected project.')
    return cwd


def resolve_selection(value: str | None, event: dict[str, Any], fallback: Path) -> Path:
    from . import workspace_binding
    cwd = execution_directory(event, fallback)
    selected = str(cwd / value) if value is not None and not Path(value).is_absolute() else value
    return workspace_binding.resolve_workspace(selected, event={**event, 'cwd': str(cwd)})


def mutation_targets(event: dict[str, Any]) -> list[str]:
    """Literal host targets only; this does not authorize opaque command effects."""
    from . import workflow_local
    args = event.get('tool_input', {})
    if not isinstance(args, dict):
        return []
    tool = event.get('tool_name') or event.get('tool')
    if tool in {'Write', 'Edit', 'write_file', 'edit_file'}:
        target = args.get('file_path') or args.get('path')
        return [target] if isinstance(target, str) else []
    if tool == 'apply_patch':
        patch = args.get('command', args.get('input', args.get('patch', '')))
        return [line.split(': ', 1)[1] for line in patch.splitlines()
                if line.startswith(('*** Add File: ', '*** Update File: ', '*** Delete File: ', '*** Move to: '))] if isinstance(patch, str) else []
    if tool in {'Bash', 'exec_command'}:
        return workflow_local.shell_write_paths(event) or []
    return []


def hook_workspace(event: dict[str, Any], fallback: Path) -> Path:
    """Resolve actual execution/target ancestry before unselected fallback."""
    from . import workspace_binding, workflow as w, workflow_local
    incidental = Path(event.get('cwd') or fallback).resolve()
    execution = execution_directory(event, fallback)
    w.require('.taskplane' not in incidental.parts and '.taskplane' not in execution.parts,
              'scope_violation', 'Tool execution is inside reserved Taskplane state. Return to the containing project with the supported literal cd command.')
    words = workflow_local.runtime_words(event)
    explicit = workspace_selector(words[2:]) if installed(words, execution) else None
    roots: set[Path] = set()
    def containing(path: Path) -> Path | None:
        for number, parent in enumerate([path, *path.parents]):
            w.require(number < 64, 'scope_violation', 'Workspace target ancestry exceeds its safe bound.')
            if (parent / '.taskplane').exists():
                return parent
        return None
    selected = (resolve_selection(explicit, event, fallback)
                if explicit is not None or os.environ.get('TASKPLANE_WORKSPACE') else
                workspace_binding.resolve_workspace(containing(execution) or execution,
                                                    event={**event, 'cwd': str(execution)}))
    for location in (incidental, execution):
        found = containing(location)
        if found:
            roots.add(found)
    if os.environ.get('TASKPLANE_WORKSPACE') or explicit is not None:
        roots.add(selected)
    targets = mutation_targets(event)
    for target in targets:
        path = Path(target)
        found = containing((execution / path).absolute().parent)
        if found:
            roots.add(found)
    # A normal read may inspect another directory without granting mutation.
    if (event.get('tool_name') or event.get('tool')) in workflow_local.READ_TOOLS or workflow_local.readonly_command(event):
        return selected
    w.require(len(roots) <= 1, 'scope_violation',
              'Execution and targets select conflicting governed workspaces; use one native task workspace.')
    chosen = next(iter(roots)) if roots else selected
    chosen = workspace_binding.resolve_workspace(chosen, event=event)
    for target in targets if roots else []:
        path = Path(target)
        workspace_binding.relative_path(chosen, str(path if path.is_absolute() else execution / path))
    return chosen


def _windows_current_directory(command: str) -> bool:
    import ctypes
    kernel = getattr(ctypes, 'WinDLL')('kernel32', use_last_error=True)
    needed = kernel.NeedCurrentDirectoryForExePathW
    needed.argtypes = [ctypes.c_wchar_p]
    needed.restype = ctypes.c_int
    return bool(needed(command))


def interpreter(command: str, cwd: Path) -> Path | None:
    """Resolve executable paths and every PATH entry in the execution directory."""
    if os.path.dirname(command):
        found = shutil.which(str(cwd / command))
        return Path(found).resolve() if found else None
    search = [cwd / entry for entry in os.get_exec_path()]
    if os.name == 'nt' and _windows_current_directory(command):
        search.insert(0, cwd)
    # Passing a bare name to Windows which() can prepend this hook process's
    # cwd. Resolve absolute candidates so only the command's cwd participates.
    for directory in search:
        found = shutil.which(str(directory / command))
        if found:
            return Path(found).resolve()
    return None


def installed(words: list[str], cwd: Path, *, absolute: bool = False) -> bool:
    return (len(words) >= 3
            and interpreter(words[0], cwd) == Path(sys.executable).resolve()
            and (not absolute or Path(words[1]).is_absolute())
            and (cwd / words[1]).resolve() == Path(__file__).with_name('tp.py').resolve())


def diagnostic(words: list[str]) -> bool:
    return words[2:] in (['version'], ['version', '--verify'], ['help'], ['help', '--md'],
                          ['--help'], ['flow', '--help'])


def collision(words: list[str], cwd: Path) -> str | None:
    """Identify a competing launcher without reading or executing that launcher."""
    if (len(words) < 3 or Path(words[1]).name != 'tp.py'
            or words[2] not in {'flow', 'workspace', 'graph', 'review', 'dashboard', 'help', 'version', '--help'}):
        return None
    executing = Path(__file__).with_name('tp.py').resolve()
    requested = (cwd / words[1]).resolve()
    if requested == executing:
        if interpreter(words[0], cwd) == Path(sys.executable).resolve():
            return None
        return (f'Taskplane interpreter mismatch in execution directory {cwd}. '
                f'Executing hook interpreter: {Path(sys.executable).resolve()}. '
                f'Requested interpreter: {interpreter(words[0], cwd) or "unavailable"}. '
                'Use the absolute verified interpreter with the exact installed launcher.')
    return (f'Taskplane runtime mismatch. Executing hook runtime: {executing}. '
            f'Requested runtime: {requested}. Select one Taskplane installation in the host plugin '
            'settings, align its skills and hooks, then reload the session and use that exact launcher. '
            'The foreign launcher remains refused; no configuration was changed.')
