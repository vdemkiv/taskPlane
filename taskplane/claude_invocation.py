"""One-use Claude hook-to-CLI observations; never host permission or attestation.

The native PreToolUse hook supplies updatedInput only after ordinary admission
and structured child launch verification. The opaque reference addresses that
exact admission under its controller lock. Environment and command text alone
cannot issue or resolve a principal. Host permissions still govern execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import secrets
import shlex
from typing import Any, TYPE_CHECKING

from . import primitives, runtime_command, workflow as w

if TYPE_CHECKING:
    from .workflow_host import Controller

FLAG = '--claude-invocation'
TTL_SECONDS = 120
MAX_ARGV_BYTES = 16384
MAX_ARGUMENTS = 64
_REFERENCE = re.compile(r'v1\.([0-9a-f]{2,800})\.([0-9a-f]{64})\.([0-9a-f]{64})\Z')


def _option(words: list[str], option: str) -> str | None:
    values = [words[index + 1] for index, word in enumerate(words[:-1]) if word == option]
    values.extend(word[len(option) + 1:] for word in words if word.startswith(option + '='))
    return values[0] if len(values) == 1 else None


def completion_matches(row: dict[str, Any], identity: dict[str, Any]) -> bool:
    """Accept only original or exactly issued input, including all other fields."""
    return all(row.get(k) == v if k != 'input_digest' else v in {
        row.get('input_digest'), row.get('claude_invocation', {}).get('updated_digest')}
        for k, v in identity.items())


def issue(controller: Controller, event: dict[str, Any]) -> dict[str, Any] | None:
    from . import host_capabilities, worker_runtime as workers, workflow_local
    from .context_handoff import binding
    words = workflow_local.runtime_words(event)
    if (controller.adapter.name != 'claude' or controller.adapter.profile != 'native_workflow'
            or controller.principal == controller.root or event.get('hook_event_name') != 'PreToolUse'
            or event.get('tool_name') != 'Bash' or len(words) < 4
            or words[2] != 'flow' or words[3] not in {'context', 'worker'}):
        return None
    cwd = runtime_command.execution_directory(event, controller.workspace)
    w.require(len(words) <= MAX_ARGUMENTS and sum(len(word.encode()) for word in words) <= MAX_ARGV_BYTES
              and runtime_command.installed(words, cwd, absolute=True) and FLAG not in words
              and not any(word.startswith(FLAG + '=') for word in words),
              'unsupported_authority', 'Claude invocation needs the exact absolute installed launcher and a fresh automatic hook.')
    call = controller._call(event)
    w.require(call is not None, 'unsupported_authority', 'Claude invocation needs an identified automatic tool call.')
    assert call is not None
    key, identity = call
    target = controller._path()
    with primitives.file_lock(str(target)):
        db = controller._read(target)
        row = db.get('admissions', {}).get(key)
        state = db['runs'].get(db['active'])
        worker = workers.find(state, controller.principal) if state else None
        w.require(row and state and worker and completion_matches(row, identity)
                  and row['state'] == 'admitted' and row.get('automatic') is True
                  and event.get('taskplane_automatic_hook') is True
                  and row['binding'] == binding(state) and row['run'] == state['run']
                  and row['runtime'] == host_capabilities.runtime_identity()
                  and worker.get('identity_observation', {}).get('status') == 'matched'
                  and not worker.get('identity_conflict') and worker.get('host') == 'claude'
                  and worker.get('state') in {'bootstrapping', 'running'},
                  'unsupported_authority', 'Claude invocation requires a current admitted observed child.')
        assert row and state and worker
        workers.current(state, worker)
        w.require(_option(words, '--run') == state['run'] and (
            words[3] == 'worker' and _option(words, '--operation') == 'claim'
            and _option(words, '--grant') == worker['grant_id']
            or words[3] == 'context' and _option(words, '--task') == worker['task_id']),
            'scope_violation', 'Claude invocation must name its exact current run and own grant or task.')
        w.require('claude_invocation' not in row, 'unsupported_authority', 'Claude invocation reference was already issued; do not replay this call.')
        token = 'v1.' + controller.root.encode().hex() + '.' + key + '.' + secrets.token_hex(32)
        updated = {**event['tool_input'], 'command': shlex.join([*words, FLAG, token])}
        row['claude_invocation'] = {
            'reference_sha256': hashlib.sha256(token.encode()).hexdigest(),
            'argv': [str(runtime_command.interpreter(words[0], cwd)), str(Path(words[1]).resolve()), *words[2:]],
            'cwd': str(cwd), 'grant': worker['grant_id'], 'issued_at': datetime.now(timezone.utc).isoformat(),
            'updated_digest': primitives.content_fingerprint(updated), 'consumed': False,
        }
        controller._write(target, db)
    return updated


def consume(workspace: Path, argv: list[str]) -> dict[str, Any] | None:
    from . import host_capabilities, worker_runtime as workers, workflow_host
    from .context_handoff import binding
    if FLAG not in argv:
        return None
    w.require(argv.count(FLAG) == 1 and len(argv) >= 6 and argv[-2] == FLAG,
              'unsupported_authority', 'Malformed Claude invocation reference.')
    token = argv[-1]
    matched = _REFERENCE.fullmatch(token)
    w.require(matched is not None, 'unsupported_authority', 'Invalid Claude invocation reference.')
    assert matched is not None
    try:
        root = bytes.fromhex(matched[1]).decode('utf-8')
    except (ValueError, UnicodeError):
        raise w.Refusal('unsupported_authority', 'Invalid Claude invocation root.') from None
    controller = workflow_host.Controller(workspace, root, workflow_host.installed_adapter('claude'))
    target = controller._path()
    with primitives.file_lock(str(target)):
        db = controller._read(target)
        row = db.get('admissions', {}).get(matched[2], {})
        proof = row.get('claude_invocation', {})
        state = db['runs'].get(db['active'])
        worker = workers.find(state, str(row.get('principal', ''))) if state else None
        w.require(proof and proof.get('reference_sha256') == hashlib.sha256(token.encode()).hexdigest()
                  and proof.get('consumed') is False and row.get('state') == 'admitted'
                  and row.get('automatic') is True and row.get('root') == root
                  and row.get('principal') != root and row.get('workspace') == str(workspace)
                  and row.get('runtime') == host_capabilities.runtime_identity()
                  and proof.get('argv') == argv[:-2] and proof.get('cwd') == str(Path.cwd().resolve())
                  and state and row.get('run') == state['run'] and row.get('binding') == binding(state)
                  and worker and proof.get('grant') == worker['grant_id'] and worker.get('host') == 'claude'
                  and worker.get('state') in {'bootstrapping', 'running'}
                  and worker.get('identity_observation', {}).get('status') == 'matched'
                  and not worker.get('identity_conflict'),
                  'unsupported_authority', 'Claude invocation is missing, used, closed, foreign or stale; obtain a fresh automatic hook.')
        assert state and worker
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(proof['issued_at'])).total_seconds()
        w.require(0 <= age <= TTL_SECONDS, 'unsupported_authority', 'Claude invocation expired; obtain a fresh automatic hook.')
        workers.current(state, worker)
        proof['consumed'] = True
        controller._write(target, db)
        return {'root': root, 'principal': row['principal'], 'workspace': str(workspace),
                'run': row['run'], 'grant': proof['grant'], 'binding': row['binding']}
