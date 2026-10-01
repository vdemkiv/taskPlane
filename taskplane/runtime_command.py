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


def interpreter(command: str, cwd: Path) -> Path | None:
    """Resolve executable paths and every PATH entry in the execution directory."""
    if os.path.dirname(command):
        found = shutil.which(str(cwd / command))
    else:
        search = os.pathsep.join(str(cwd / entry) for entry in os.get_exec_path())
        found = shutil.which(command, path=search)
    return Path(found).resolve() if found else None


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
