"""Typed startup fences. Caller owns the controller lock and observed principal."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import base64
import shlex
import time
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, cast

from . import workflow as w
from .context import digest, encode

CONTROL_OPERATIONS = {'startup-status', 'stop', 'omit-optional'}
CONTROL_LIMIT = 4096
REVISION_SCHEMA = 'taskplane.requirements-revision/v1'
STARTUP_LIMITS = {'mandatory_unique_bytes': 131072, 'response_bytes': 32768,
                  'responses': 8, 'processing_ns': 60_000_000_000}


@dataclass(frozen=True)
class StartupInvocation:
    command: str
    argv: tuple[str, ...]
    operation: str
    workspace: str
    run: str
    grant_id: str
    request: dict[str, Any]
    revision: int | None


class StartupInvocationTransport:
    """Separate typed Claude bridge; the v1 claim/context contract stays intact.

    This reuses only the bounded locator, JSON, clock and rewrite primitives.
    It never represents a stop, status or probe as a claim/context invocation.
    Issuance and single-use consumption require the controller's existing lock.
    """
    SCHEMA = 'taskplane.claude-startup-invocation/v1'

    @staticmethod
    def request(value: str) -> dict[str, Any]:
        from . import primitives
        try:
            w.require(isinstance(value, str) and 0 < len(value) <= 5462,
                      'invalid_evidence', 'Startup request encoding exceeds its bound.')
            raw = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
            request = json.loads(raw)
            w.require(isinstance(request, dict) and len(raw) <= CONTROL_LIMIT
                      and primitives.canonical_bytes(request) == raw
                      and base64.urlsafe_b64encode(raw).decode().rstrip('=') == value,
                      'invalid_evidence', 'Startup request must be canonical bounded JSON/base64url.')
            return cast(dict[str, Any], request)
        except (ValueError, UnicodeError, RecursionError):
            raise w.Refusal('invalid_evidence', 'Startup request encoding is invalid.') from None

    @staticmethod
    def _words(words: Any, command: str, runtime: tuple[str, str]) -> StartupInvocation:
        from . import claude_worker_invocation as old
        w.require(isinstance(words, (tuple, list)) and len(words) in {14, 16}
                  and all(old._text(word, old.MAX_COMMAND_BYTES) and not old._UNSAFE.search(word) for word in words)
                  and len(shlex.join(words).encode()) <= old.MAX_COMMAND_BYTES
                  and tuple(words[:4]) == (*runtime, 'flow', 'worker'),
                  'scope_violation', 'Startup invocation requires the exact foreground runtime and typed worker command.')
        order = ('--operation', '--run', '--grant', '--workspace', '--startup-request', '--expected-revision')
        values: dict[str, str] = {}
        for key, value in zip(words[4::2], words[5::2]):
            w.require(key in order and key not in values and not value.startswith('-'),
                      'scope_violation', 'Unknown, duplicate or malformed startup invocation option.')
            values[key] = value
        w.require(set(order[:-1]) <= values.keys() and values['--operation'] in CONTROL_OPERATIONS | {'source-probe'}
                  and old._path(values['--workspace'])
                  and all(old._ID.fullmatch(values[k]) for k in ('--run', '--grant')),
                  'scope_violation', 'Startup invocation needs an exact operation, workspace, run and grant.')
        revision = values.get('--expected-revision')
        w.require(revision is None or (len(revision) <= 9 and revision.isascii() and revision.isdecimal()
                  and str(int(revision)) == revision),
                  'scope_violation', 'Startup revision must be a bounded canonical integer.')
        request = StartupInvocationTransport.request(values['--startup-request'])
        operation = values['--operation']
        if operation == 'source-probe':
            w.require(set(request) == {'path'} and isinstance(request['path'], str) and 0 < len(request['path']) <= 512,
                      'invalid_evidence', 'Source probe requires one bounded source path.')
        else:
            w.require(set(request) <= {'schema', 'request_id', 'request_reference', 'run', 'grant', 'attempt', 'reason', 'inputs'}
                      and request.get('schema') == 'taskplane.startup-control/v1'
                      and request.get('run') == values['--run'] and request.get('grant') == values['--grant'],
                      'invalid_evidence', 'Startup control request differs from its command binding.')
        argv = (*runtime, 'flow', 'worker', *(item for key in order if key in values for item in (key, values[key])))
        return StartupInvocation(command, argv, operation, values['--workspace'], values['--run'],
                                 values['--grant'], request, int(revision) if revision is not None else None)

    @staticmethod
    def parse_command(command: str) -> StartupInvocation:
        from . import claude_worker_invocation as old
        w.require(old._text(command, old.MAX_COMMAND_BYTES) and not old._UNSAFE.search(command),
                  'scope_violation', 'Startup invocation contains unsupported shell syntax or exceeds its bound.')
        try:
            words = shlex.split(command)
        except ValueError:
            raise w.Refusal('scope_violation', 'Startup invocation quoting is invalid.') from None
        return StartupInvocationTransport._words(words, command, old._runtime(None, None))

    @staticmethod
    def parse_cli(argv: Any) -> tuple[StartupInvocation, str]:
        from . import claude_worker_invocation as old
        w.require(isinstance(argv, (tuple, list)) and len(argv) in {15, 17}
                  and all(old._text(word, old.MAX_COMMAND_BYTES) for word in argv)
                  and argv[-2] == '--invocation-ref' and argv.count('--invocation-ref') == 1,
                  'unsupported_authority', 'Startup invocation requires one final automatic reference.')
        reference = argv[-1]
        old.decode_reference(reference)
        runtime = old._runtime(None, None)
        words = (runtime[0], *argv[:-2])
        parsed = StartupInvocationTransport._words(words, shlex.join(words), runtime)
        w.require(parsed.argv == words, 'scope_violation', 'Startup invocation differs from the canonical hook rewrite.')
        return parsed, reference

    @staticmethod
    def _bind(command: StartupInvocation, binding: Any) -> None:
        from . import claude_worker_invocation as old, primitives
        w.require(isinstance(binding, dict) and set(binding) == old.BINDING_FIELDS | {'operation', 'request_sha256'},
                  'state_unavailable', 'Startup invocation binding schema is invalid.')
        root = binding['actor'] == binding['root']
        w.require(binding['kind'] == ('root-startup-control' if root else 'worker-startup')
                  and binding['active'] is True and binding['automatic'] is True
                  and all(old._text(binding[k], 512 if k == 'call_id' else 200)
                          for k in ('root', 'actor', 'run', 'visit', 'call_id'))
                  and old._path(binding['workspace'])
                  and all(old._sha(binding[k]) for k in ('binding_sha256', 'workspace_contract_sha256',
                                                       'runtime_sha256', 'task_sha256', 'request_sha256'))
                  and all(old._text(binding[k]) and old._ID.fullmatch(binding[k]) for k in ('task_id', 'grant_id'))
                  and type(binding['revision']) is int and binding['revision'] >= 0
                  and type(binding['task_generation']) is int and binding['task_generation'] >= 0
                  and type(binding['attempt']) is int and binding['attempt'] >= 1
                  and type(binding['claimed']) is bool,
                  'state_unavailable', 'Startup invocation binding is incomplete or inactive.')
        w.require((root and binding['launch_call_id'] is None and binding['launch_evidence_sha256'] is None)
                  or (not root and old._text(binding['launch_call_id'], 512) and old._sha(binding['launch_evidence_sha256'])),
                  'unsupported_authority', 'Worker startup requires an exact observed launch proof.')
        w.require((command.operation == 'source-probe' and not root and binding['claimed'] is True
                   and old._sha(binding['inputs_sha256']))
                  or (command.operation in CONTROL_OPERATIONS and binding['inputs_sha256'] is None),
                  'unsupported_authority', 'Startup controls and source probes have distinct authority requirements.')
        w.require(command.workspace == binding['workspace'] and command.run == binding['run']
                  and command.grant_id == binding['grant_id'] and command.operation == binding['operation']
                  and primitives.content_fingerprint(command.request) == binding['request_sha256']
                  and (command.revision is None or command.revision == binding['revision'])
                  and (not root or command.operation == 'startup-status' or command.revision == binding['revision']),
                  'stale_checkpoint', 'Startup invocation differs from the current actor, grant, operation or revision.')

    @staticmethod
    def validate_record(record: Any) -> None:
        from . import claude_worker_invocation as old, primitives
        w.require(isinstance(record, dict) and set(record) == old._RECORD_FIELDS
                  and record['schema'] == StartupInvocationTransport.SCHEMA
                  and isinstance(record['state'], str) and record['state'] in {'pending', 'consumed'}
                  and all(old._sha(record[k]) for k in old._RECORD_FIELDS if k.endswith('sha256') or k == 'fingerprint')
                  and record['fingerprint'] == primitives.manifest_record(record)['fingerprint'],
                  'state_unavailable', 'Startup invocation record is invalid or corrupt.')
        argv = record['argv']
        w.require(isinstance(argv, list) and len(argv) in {14, 16}
                  and all(old._text(word, old.MAX_COMMAND_BYTES) for word in argv)
                  and old._path(argv[0]) and old._path(argv[1]),
                  'state_unavailable', 'Stored startup invocation argv is invalid.')
        command = StartupInvocationTransport._words(argv, shlex.join(argv), tuple(argv[:2]))
        StartupInvocationTransport._bind(command, record['binding'])
        w.require(old.decode_reference(record['reference']) == record['binding']['root']
                  and list(command.argv) == argv and primitives.content_fingerprint(argv) == record['argv_sha256']
                  and old._digest(shlex.join((*argv, '--invocation-ref', record['reference']))) == record['rewritten_command_sha256'],
                  'state_unavailable', 'Stored startup invocation differs from its command or locator.')
        issued, expires, consumed = record['issued'], record['expires'], record['consumed']
        w.require(old._valid_clock(issued) and old._valid_clock(expires) and issued['boot_id'] == expires['boot_id']
                  and all(expires[k] - issued[k] == old.TTL_NS for k in ('utc_ns', 'monotonic_ns'))
                  and ((record['state'] == 'pending' and consumed is None)
                       or (record['state'] == 'consumed' and old._valid_clock(consumed)
                           and consumed['boot_id'] == issued['boot_id']
                           and all(issued[k] <= consumed[k] < expires[k] for k in ('utc_ns', 'monotonic_ns')))),
                  'state_unavailable', 'Startup invocation lifetime or consumption state is invalid.')

    @staticmethod
    def _pending(record: dict[str, Any], command: StartupInvocation, reference: str,
                 binding: dict[str, Any]) -> dict[str, Any]:
        from . import claude_worker_invocation as old
        StartupInvocationTransport.validate_record(record)
        w.require(isinstance(command, StartupInvocation)
                  and command == StartupInvocationTransport.parse_command(command.command),
                  'scope_violation', 'Startup invocation was not parsed against the executing runtime.')
        StartupInvocationTransport._bind(command, binding)
        w.require(record['reference'] == reference and record['binding'] == binding and list(command.argv) == record['argv']
                  and record['state'] == 'pending', 'scope_violation', 'Startup invocation is consumed or its exact binding changed.')
        w.require(binding['runtime_sha256'] == old.runtime_digest(), 'stale_checkpoint', 'Startup invocation runtime changed.')
        return old._current_clock(record)

    @staticmethod
    def issue_record(command: StartupInvocation, tool_input: dict[str, Any], binding: dict[str, Any], *,
                     existing: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        from . import claude_worker_invocation as old, primitives
        w.require(isinstance(command, StartupInvocation)
                  and command == StartupInvocationTransport.parse_command(command.command),
                  'scope_violation', 'Startup invocation was not parsed against the executing runtime.')
        StartupInvocationTransport._bind(command, binding)
        w.require(binding['runtime_sha256'] == old.runtime_digest(), 'stale_checkpoint', 'Startup invocation runtime changed.')
        if existing is not None:
            StartupInvocationTransport._pending(existing, command, existing.get('reference', ''), binding)
        reference = existing['reference'] if existing is not None else old._reference(binding['root'])
        old.decode_reference(reference)
        w.require(isinstance(tool_input, dict) and tool_input.get('command') == command.command
                  and ('run_in_background' not in tool_input or tool_input['run_in_background'] is False)
                  and len(primitives.canonical_bytes(tool_input)) <= old.MAX_INPUT_BYTES,
                  'scope_violation', 'Startup invocation requires the exact bounded foreground Bash input.')
        rewritten = deepcopy(tool_input)
        rewritten['command'] = shlex.join((*command.argv, '--invocation-ref', reference))
        w.require(len(primitives.canonical_bytes(rewritten)) <= old.MAX_INPUT_BYTES,
                  'scope_violation', 'Rewritten startup invocation exceeds its bound.')
        if existing is not None:
            w.require(existing['original_input_sha256'] == primitives.content_fingerprint(tool_input)
                      and existing['original_command_sha256'] == old._digest(command.command)
                      and existing['rewritten_input_sha256'] == primitives.content_fingerprint(rewritten),
                      'scope_violation', 'Conflicting duplicate startup invocation admission.')
            return deepcopy(existing), rewritten
        issued = old._clock()
        w.require(old._valid_clock(issued), 'state_unavailable', 'Invocation clock is invalid.')
        record = primitives.manifest_record({'schema': StartupInvocationTransport.SCHEMA, 'reference': reference,
            'binding': deepcopy(binding), 'argv': list(command.argv), 'argv_sha256': primitives.content_fingerprint(list(command.argv)),
            'original_input_sha256': primitives.content_fingerprint(tool_input),
            'rewritten_input_sha256': primitives.content_fingerprint(rewritten),
            'original_command_sha256': old._digest(command.command), 'rewritten_command_sha256': old._digest(rewritten['command']),
            'issued': issued, 'expires': {**issued, **{k: issued[k] + old.TTL_NS for k in ('utc_ns', 'monotonic_ns')}},
            'state': 'pending', 'consumed': None})
        StartupInvocationTransport.validate_record(record)
        return record, rewritten

    @staticmethod
    def consume_record(record: dict[str, Any], command: StartupInvocation, reference: str,
                       binding: dict[str, Any], *, persist: Any, dispatch: Any) -> Any:
        from . import claude_worker_invocation as old, primitives
        w.require(callable(persist) and callable(dispatch), 'state_unavailable', 'Startup invocation requires locked durable dispatch.')
        now = StartupInvocationTransport._pending(record, command, reference, binding)
        consumed = primitives.manifest_record({**record, 'state': 'consumed', 'consumed': now})
        record.clear()
        record.update(consumed)
        try:
            persist(deepcopy(consumed))
        except (OSError, primitives.StateError):
            raise w.Refusal('state_unavailable', 'Startup invocation consumption could not be durably saved.') from None
        old._current_clock(record)
        return dispatch()

    @staticmethod
    def post_input_matches(record: dict[str, Any], tool_input: Any) -> bool:
        from . import claude_worker_invocation as old, primitives
        StartupInvocationTransport.validate_record(record)
        try:
            return (isinstance(tool_input, dict) and len(primitives.canonical_bytes(tool_input)) <= old.MAX_INPUT_BYTES
                    and primitives.content_fingerprint(tool_input) in {record['original_input_sha256'], record['rewritten_input_sha256']})
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return False


def invocation_transport(record: dict[str, Any]) -> Any:
    from . import claude_worker_invocation as legacy
    return StartupInvocationTransport if record.get('schema') == StartupInvocationTransport.SCHEMA else legacy


def revision_preview(workspace: Path, state: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    """Compute a complete conservative closure from accepted interfaces and graph.

    This function is read-only. Missing mappings or graph coverage refuse a
    selective change instead of treating an unobserved dependency as absent.
    """
    from . import workflow_evidence as e, depgraph, worker_runtime as workers
    fields = {'schema', 'request_id', 'request_reference', 'run', 'expected_revision',
              'expected_generation', 'scope_digest', 'requirements_digest', 'plan_digest',
              'changes', 'affected_artifacts', 'scope_amendment', 'reason', 'return_phase'}
    w.require(isinstance(request, dict) and set(request) == fields and len(encode(request)) <= 262144
              and request['schema'] == REVISION_SCHEMA and request['return_phase'] == 'product'
              and all(isinstance(request[k], str) and 0 < len(request[k].strip()) <= limit
                      for k, limit in [('request_id', 128), ('request_reference', 512), ('reason', 2048)]),
              'invalid_evidence', 'A bounded typed revision needs the actual human request, exact changes and Product return.')
    w.require(request['run'] == state['run'] and type(request['expected_revision']) is int
              and type(request['expected_generation']) is int and request['expected_revision'] == state['revision']
              and request['expected_generation'] == state.get('storage_generation', 0)
              and request['scope_digest'] == digest(state['scope']),
              'stale_checkpoint', 'Revision run, generation, scope or state changed.')
    w.require(w.current(state)['phase'] == 'build' and w.current(state)['decision'] in
              {'not_requested', 'changes_requested', 'rejected'} and not state.get('finished')
              and not state.get('cancellation') and not state.get('active_requirements_revision'),
              'approval_required', 'Begin a revision only from an active unsealed Build without another revision.')
    plan = w.accepted_plan(state)
    product = next((v for v in reversed(state['visits'][:state['index']])
                    if v['phase'] == 'product' and v['decision'] == 'approved'
                    and not v['superseded'] and v.get('packet')), None)
    w.require(product and request['requirements_digest'] == digest(product['packet']['output'])
              and request['plan_digest'] == digest(plan['packet']),
              'stale_checkpoint', 'The accepted Product or Plan differs from this revision request.')
    assert product is not None
    original = {row['id']: row for row in product['packet']['output']['acceptance_criteria']}
    changes = request['changes']
    w.require(isinstance(changes, dict) and changes and set(changes) <= set(original)
              and all(isinstance(row, dict) and row.get('id') == key and e.substantive(row.get('statement'))
                      and row != original[key] for key, row in changes.items()),
              'invalid_evidence', 'Changes must name existing criteria with exact replacement requirements.')
    new_scope = deepcopy(request['scope_amendment'] or state['scope'])
    e.valid_scope(workspace, new_scope)
    w.require(new_scope['criteria'] == state['scope']['criteria'], 'scope_violation',
              'A selective revision must preserve the full criterion register.')
    historical = {p for visit in state['visits'] if visit.get('packet')
                  for p in visit['packet']['manifest']}
    w.require(all(not set(new_scope['paths'][phase]) & historical for phase in ('product', 'design', 'plan')),
              'scope_violation', 'Specify fresh Product, Design and Plan output paths; sealed history cannot be overwritten.')
    definitions = {row['id']: row for row in plan['packet']['output']['task_dag']}
    w.require(definitions and all('read_inputs' in row for row in definitions.values()),
              'invalid_evidence', 'Selective closure is unknown: every accepted task needs explicit read interfaces.')
    coverage = plan['packet']['output']['acceptance_coverage']
    w.require(set(coverage) == set(original) and all(set(v) <= set(definitions) and v for v in coverage.values()),
              'invalid_evidence', 'Selective closure is unknown: accepted criterion/task coverage is incomplete.')
    strategy = plan['packet']['output'].get('verification_strategy')
    w.require(isinstance(strategy, dict) and strategy.get('schema') == e.VERIFICATION_STRATEGY
              and isinstance(strategy.get('checks'), list) and strategy['checks'],
              'invalid_evidence', 'Selective closure is unknown: accepted check definitions are required.')
    checks = strategy['checks']
    w.require(all(set(row.get('criteria', [])) <= set(original) and row.get('task') in definitions
                  for row in checks) and {c for row in checks for c in row['criteria']} == set(original),
              'invalid_evidence', 'Selective closure is unknown: checks must cover every criterion and exact task.')
    allowed = {p for paths in state['scope']['paths'].values() for p in paths}
    allowed.update(p for row in definitions.values() for p in row['read_inputs'])
    artifacts = request['affected_artifacts']
    w.require(isinstance(artifacts, dict) and set(artifacts) <= allowed
              and artifacts == e.manifest(workspace, list(artifacts)),
              'stale_checkpoint', 'Affected artifacts must be current exact accepted-scope references.')
    graph = depgraph.load(str(workspace))
    quality = depgraph.scan_quality(graph)
    w.require(graph.get('meta', {}).get('graph_scan_quality') and not quality['degraded']
              and quality.get('mode') == 'components'
              and graph.get('meta', {}).get('source_coverage', {}).get('complete') is True
              and graph.get('meta', {}).get('module_confidence') != 'low'
              and depgraph.source_inputs_current(str(workspace), graph),
              'invalid_evidence', 'Selective closure is unknown: a complete current source graph is required.')
    module_ids = depgraph.declared_module_ids(graph)
    def module(path: str) -> str:
        return path if path in graph['modules'] else depgraph.module_of(path, module_ids)
    reads = {key: set(row['read_inputs']) for key, row in definitions.items()}
    writes = {key: set(row['paths']) for key, row in definitions.items()}
    # The scanner intentionally omits Taskplane's evidence store. Exact declared
    # data artifacts still participate in task/read/write closure; they are not
    # source modules. Never extend this exception to executable or declared
    # implementation/test paths, or to undeclared files under the same folder.
    intent = state['scope'].get('implementation_intent', {})
    source_paths = set(intent.get('implementation_paths', [])) | set(intent.get('test_paths', []))
    data_artifacts = {p for paths in writes.values() for p in paths
                      if Path(p).parts[0] == '.taskplane'
                      and Path(p).suffix in {'.json', '.jsonl', '.md', '.txt', '.log'}
                      and p not in source_paths}
    affected = {key for criterion in changes for key in coverage[criterion]}
    files = set(artifacts)
    modules: set[str] = set()
    # Fixed point includes downstream task dependencies, read interfaces,
    # shared writers, reverse source edges and the accepted integration checks.
    while True:
        before = (frozenset(affected), frozenset(files), frozenset(modules))
        files.update(p for key in affected for p in writes[key])
        modules.update(module(p) for p in files if module(p) in graph['modules'])
        for edge in graph['edges']:
            if edge['to'] in modules:
                modules.add(edge['from'])
        for key, row in definitions.items():
            if (set(row['dependencies']) & affected or (reads[key] | writes[key]) & files
                    or any(module(p) in modules for p in reads[key] | writes[key])):
                affected.add(key)
        for check in checks:
            if set(check['criteria']) & set(changes) or set(check.get('source_inputs', []) +
                    check.get('test_inputs', [])) & files or check['task'] in affected:
                affected.add(check['task'])
        if before == (frozenset(affected), frozenset(files), frozenset(modules)):
            break
    unknown = [p for p in files if e.path(workspace, p).exists()
               and module(p) not in graph['modules'] and p not in data_artifacts]
    w.require(not unknown, 'invalid_evidence', 'Selective closure is unknown for existing paths: ' + ', '.join(sorted(unknown)[:10]))
    artifact_manifest = e.manifest(workspace, sorted(files & data_artifacts), allow_missing=True)
    affected_criteria = {c for key in affected for c in e.task_criteria(definitions[key])}
    renewed = sorted(set(changes))
    result = {'schema': 'taskplane.requirements-revision-preview/v1', 'request_id': request['request_id'],
        'request_digest': digest(request), 'binding': {'run': state['run'], 'revision': state['revision'],
            'storage_generation': state.get('storage_generation', 0)},
        'criteria': renewed, 'affected_criteria': sorted(affected_criteria), 'tasks': sorted(affected),
        'files': sorted(files), 'modules': sorted(modules), 'graph_digest': digest(graph),
        'artifact_manifest': artifact_manifest,
        'checks': sorted(check['id'] for check in checks if check['task'] in affected
                         or set(check['criteria']) & set(changes)),
        'affected_grants': sorted(key for key, row in workers.records(state).items() if row['task_id'] in affected),
        'retained_results': sorted(key for key in state.get('task_results', {}) if key not in affected),
        'approvals_requiring_renewal': ['product', 'design', 'plan'], 'scope': new_scope,
        'requirements': [deepcopy(changes.get(key, value)) for key, value in original.items()],
        'status': 'preview', 'approvals_created': 0}
    return {**result, 'preview_digest': digest(result)}


def clock_identity() -> str | None:
    """An actual boot identity, never a clock-offset guess or caller assertion."""
    try:
        if sys.platform.startswith('linux'):
            raw = Path('/proc/sys/kernel/random/boot_id').read_bytes()
        elif sys.platform == 'darwin':
            raw = subprocess.check_output(['/usr/sbin/sysctl', '-n', 'kern.boottime'],
                                          timeout=2, stderr=subprocess.DEVNULL)
        else:
            return None
        return digest({'platform': sys.platform, 'boot': raw.decode().strip()}) if 0 < len(raw) < 4096 else None
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        return None


def startup_command(event: dict[str, Any], state: dict[str, Any], principal: str) -> dict[str, str] | None:
    from .workflow_local import runtime_words
    words = runtime_words(event)
    if len(words) < 4 or words[2] != 'flow' or Path(words[1]).resolve() != Path(__file__).with_name('tp.py').resolve():
        return None
    def flag(name: str) -> str | None:
        if words.count(name) != 1:
            return None
        index = words.index(name) + 1
        return words[index] if index < len(words) else None
    if flag('--run') != state['run']:
        return None
    kind = 'context' if words[3] == 'context' else flag('--operation') if words[3] == 'worker' else None
    if kind not in {'prepare', 'claim', 'context', 'source-probe'}:
        return None
    if principal == state['root']:
        return {'kind': 'prepare', 'task': flag('--task') or ''} if kind == 'prepare' else None
    from .worker_runtime import find
    row = find(state, principal)
    if not row or (kind == 'context' and flag('--task') != row['task_id']) or (
            kind != 'context' and flag('--grant') != row['grant_id']):
        return None
    return {'kind': kind, 'task': row['task_id'], 'grant': row['grant_id']}


def native_output_digest(event: dict[str, Any]) -> str | None:
    value = event.get('tool_response')
    if isinstance(value, dict) and 'schema' not in value:
        value = value.get('output', value.get('stdout'))
    if isinstance(value, str) and len(value.encode()) <= 65536:
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return digest(value) if isinstance(value, dict) else None


def native_output_bytes(event: dict[str, Any]) -> int | None:
    value = event.get('tool_response')
    if isinstance(value, dict) and 'schema' not in value:
        if value.get('exit_code') not in (None, 0) or value.get('isError') is True:
            return None
        value = value.get('output', value.get('stdout'))
    return len(value.encode()) if isinstance(value, str) and len(value.encode()) <= 65536 else None


def new_account(row: dict[str, Any], preflight: dict[str, Any]) -> dict[str, Any]:
    w.require(type(preflight.get('required_body_bytes')) is int
              and preflight['required_body_bytes'] >= 0,
              'invalid_context', 'Startup requires a complete input preflight.')
    return {'schema': 'taskplane.startup-account/v3', 'contract': 'execution_only',
            'binding': {k: deepcopy(row[k]) for k in ('run', 'grant_id', 'attempt', 'binding', 'expected_runtime')},
            'input_binding': {'requirements_epochs': deepcopy(row.get('requirements_epochs', {})),
                              'task_epoch': row.get('task_epoch', 0),
                              'source_digest': digest(row.get('input_manifest', {}))},
            'stage': 'prepared', 'operations': {}, 'execution': 'incomplete'}


def account(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get('startup_account')
    w.require(isinstance(value, dict) and value.get('schema') in {
                  'taskplane.startup-account/v1', 'taskplane.startup-account/v2', 'taskplane.startup-account/v3'}
              and (not row.get('startup_account_schema') or value.get('schema') == row['startup_account_schema'])
              and value.get('binding') == {k: row[k] for k in
                  ('run', 'grant_id', 'attempt', 'binding', 'expected_runtime')},
              'worker_readiness', 'Startup account is missing or has a foreign binding.')
    assert isinstance(value, dict)
    if value['schema'] == 'taskplane.startup-account/v3':
        w.require(value.get('contract') == 'execution_only'
                  and value.get('input_binding') == {
                      'requirements_epochs': row.get('requirements_epochs', {}),
                      'task_epoch': row.get('task_epoch', 0),
                      'source_digest': digest(row.get('input_manifest', {}))}
                  and set(value) == {'schema', 'contract', 'binding', 'input_binding',
                                     'stage', 'operations', 'execution'}
                  and value.get('stage') in {'prepared', 'claimed', 'delivering', 'brief_ready',
                                            'source_probe_pending', 'ready', 'stop_requested', 'failed'}
                  and isinstance(value.get('operations'), dict),
                  'worker_readiness', 'Execution-only startup contract or input binding changed.')
        operations = value['operations']
        for key, op in operations.items():
            w.require(isinstance(key, str) and isinstance(op, dict)
                      and op.get('status') in {'reserved', 'emission_pending', 'completed'},
                      'worker_readiness', 'Invalid startup operation record.')
            if op['status'] == 'completed':
                proof = op.get('execution', {})
                w.require(isinstance(proof, dict) and isinstance(proof.get('native_reference'), str)
                          and bool(proof['native_reference']) and type(op.get('emitted_bytes')) is int
                          and proof == {
                              'schema': 'taskplane.startup-execution/v1', 'operation': key,
                              'run': row['run'], 'grant': row['grant_id'], 'attempt': row['attempt'],
                              'binding': value['binding'], 'request_digest': op.get('request_digest'),
                              'response_digest': op.get('response_digest'), 'response_bytes': op['emitted_bytes'],
                              'native_reference': proof['native_reference'], 'runtime': row['expected_runtime'],
                              'automatic': True, 'post_hook_committed': True},
                          'worker_readiness', 'Completed startup operation lacks its exact execution proof.')
        complete = bool(operations) and all(op['status'] == 'completed' for op in operations.values())
        w.require(value.get('execution') == ('complete' if complete else 'incomplete')
                  and (value['stage'] != 'ready' or complete and any(
                      op.get('kind') == 'source-probe' and op.get('control_epoch') == row.get('control_epoch', 0)
                      for op in operations.values())),
                  'worker_readiness', 'Startup readiness lacks complete operations and its source probe.')
        return cast(dict[str, Any], value)
    w.require(value.get('limits') == STARTUP_LIMITS,
              'worker_readiness', 'Legacy startup account limits changed.')
    if value['schema'] == 'taskplane.startup-account/v2':
        from . import host_capabilities as caps
        policy = value.get('timing_policy', {})
        w.require(isinstance(policy, dict) and policy.get('mode') in {'advisory', 'required'}
                  and digest(policy) == value.get('timing_policy_sha256')
                  and policy.get('requirements_epochs') == row.get('requirements_epochs', {})
                  and policy.get('task_epoch') == row.get('task_epoch', 0)
                  and policy.get('source') == row.get('input_manifest', {}),
                  'worker_readiness', 'Startup timing mode is missing or its immutable binding changed.')
        w.require(policy['mode'] != 'required' or caps.qualified_startup_timing(
                  policy.get('capability'), policy['host'], row['expected_runtime']),
                  'worker_readiness', 'Required timing lacks its pinned full native qualification.')
    return cast(dict[str, Any], value)


def timing_required(value: dict[str, Any]) -> bool:
    return bool(value['schema'] == 'taskplane.startup-account/v1'
                or value['schema'] == 'taskplane.startup-account/v2' and value['timing_policy']['mode'] == 'required')


def reserve_response(row: dict[str, Any], operation_id: str, kind: str,
                     request_digest: str, maximum_bytes: int = 32768) -> dict[str, Any]:
    """Caller durably writes this reservation before producing any response body."""
    allow_work(row)
    value = account(row)
    w.require(isinstance(operation_id, str) and 0 < len(operation_id) <= 512
              and kind in {'prepare', 'claim', 'context', 'source-probe'}
              and isinstance(request_digest, str) and len(request_digest) == 64
              and type(maximum_bytes) is int and 0 < maximum_bytes <= STARTUP_LIMITS['response_bytes'],
              'invalid_evidence', 'Invalid startup operation reservation.')
    previous = value['operations'].get(operation_id)
    if previous:
        w.require(previous['kind'] == kind and previous['request_digest'] == request_digest
                  and previous['maximum_bytes'] == maximum_bytes, 'invalid_evidence',
                  'Conflicting startup operation identity.')
        return cast(dict[str, Any], previous)  # Exact host call replay; a new native call spends a new slot.
    w.require(value['stage'] not in {'ready', 'stop_requested', 'exhausted', 'failed'}
              and (not timing_required(value) or not value['operations'] or
                   type(value['processing_ns']) is int and value['processing_ns'] < STARTUP_LIMITS['processing_ns']),
              'worker_readiness', 'Startup is stopped, complete or exhausted.')
    w.require(all(op['status'] == 'completed' for op in value['operations'].values()),
              'worker_readiness', 'Previous startup operation has unknown output or timing; reconcile its native evidence.')
    legacy = value['schema'] != 'taskplane.startup-account/v3'
    if legacy:
        w.require(value['responses_reserved'] < STARTUP_LIMITS['responses'], 'context_overflow',
                  'All eight startup response slots are spent; a retry cannot reset this attempt.')
    if kind == 'source-probe':
        w.require(value['stage'] == 'brief_ready' and row.get('context_receipt'),
                  'invalid_context', 'First source probe needs complete current mandatory context.')
        value['stage'] = 'source_probe_pending'
    operation = {'kind': kind, 'request_digest': request_digest, 'maximum_bytes': maximum_bytes,
                 'status': 'reserved', 'control_epoch': row.get('control_epoch', 0)}
    if legacy:
        operation.update(reserved_monotonic_ns=time.monotonic_ns(), intervals=[])
    value['operations'][operation_id] = operation
    if legacy:
        value['responses_reserved'] += 1
        value['measurement'] = 'incomplete'
    else:
        value['execution'] = 'incomplete'
    return operation


def emitted_response(row: dict[str, Any], operation_id: str, result: dict[str, Any]) -> None:
    """Retain emission intent; only matching native completion resolves uncertainty."""
    value = account(row)
    operation = value['operations'][operation_id]
    size = len(encode(result)) + 1  # Public CLI newline is transport too.
    w.require(size <= operation['maximum_bytes'], 'context_overflow', 'Startup response exceeds its reserved envelope.')
    fingerprint = digest(result)
    if operation.get('response_digest'):
        w.require(operation['response_digest'] == fingerprint, 'invalid_evidence', 'Conflicting startup response replay.')
        return
    operation.update(response_digest=fingerprint, emitted_bytes=size, status='emission_pending')
    if value['schema'] != 'taskplane.startup-account/v3':
        value['responses_emitted'] += 1
        value['response_bytes'] += size


def interval_total(intervals: list[dict[str, Any]]) -> int:
    """Union genuine intervals from one host clock; nested work counts once."""
    w.require(isinstance(intervals, list) and 0 < len(intervals) <= 512, 'invalid_evidence',
              'Startup timing requires bounded actual intervals.')
    domains = set()
    spans = []
    for item in intervals:
        w.require(isinstance(item, dict) and set(item) == {'clock', 'start_ns', 'end_ns', 'source'}
                  and isinstance(item['clock'], str) and bool(item['clock'])
                  and isinstance(item['source'], str) and bool(item['source'])
                  and type(item['start_ns']) is int and type(item['end_ns']) is int
                  and 0 <= item['start_ns'] <= item['end_ns'],
                  'invalid_evidence', 'Missing or conflicting startup clock interval.')
        domains.add(item['clock'])
        spans.append((item['start_ns'], item['end_ns']))
    w.require(len(domains) == 1, 'invalid_evidence', 'Startup intervals use different host clocks.')
    total, right = 0, -1
    for start, end in sorted(spans):
        total += max(0, end - max(start, right))
        right = max(right, end)
    return total


def _complete_legacy_response(row: dict[str, Any], operation_id: str, response_digest: str,
                      intervals: list[dict[str, Any]], *, native_reference: str,
                      automatic: bool, runtime: dict[str, Any]) -> None:
    """Internal hook API, never a worker-supplied timing/authority envelope."""
    value = account(row)
    operation = value['operations'][operation_id]
    w.require(automatic is True and runtime == row['expected_runtime']
              and isinstance(native_reference, str) and bool(native_reference)
              and operation.get('response_digest') == response_digest,
              'unsupported_authority', 'Startup completion needs matching native output, runtime and automatic hooks.')
    w.require(isinstance(intervals, list) and len(intervals) <= 64, 'invalid_evidence',
              'A startup operation may retain at most 64 timing intervals.')
    elapsed = interval_total(intervals)
    proof = {'intervals': deepcopy(intervals), 'native_reference': native_reference,
             'response_digest': response_digest, 'runtime': deepcopy(runtime), 'automatic': True}
    if operation['status'] == 'completed':
        w.require(operation.get('proof') == proof, 'invalid_evidence', 'Conflicting native startup completion.')
        return
    # Global union also protects against overlapping observed hook/CLI intervals.
    prior_completed = [op for key, op in value['operations'].items()
                       if key != operation_id and op['status'] == 'completed']
    total = interval_total([span for op in prior_completed for span in op['proof']['intervals']] + intervals)
    operation.update(status='completed', proof=proof, processing_ns=elapsed)
    completed = prior_completed + [operation]
    value['processing_ns'] = total
    value['measurement'] = 'complete' if len(completed) == len(value['operations']) else 'incomplete'
    if value['processing_ns'] > STARTUP_LIMITS['processing_ns']:
        value['stage'] = 'exhausted'
        value['overshoot_ns'] = value['processing_ns'] - STARTUP_LIMITS['processing_ns']
    elif row.get('stop_requested') or operation['control_epoch'] != row.get('control_epoch', 0):
        value['stage'] = 'stop_requested'
    elif operation['kind'] == 'source-probe':
        value['stage'] = 'ready'
        row['state'] = 'running'
    elif row.get('context_receipt'):
        value['stage'] = 'brief_ready'
    elif operation['kind'] == 'claim':
        value['stage'] = 'claimed'
    elif operation['kind'] == 'context':
        value['stage'] = 'delivering'


def complete_response(row: dict[str, Any], operation_id: str, response_digest: str,
                      intervals: list[dict[str, Any]], *, native_reference: str,
                      automatic: bool, runtime: dict[str, Any],
                      execution: dict[str, Any] | None = None,
                      timing: dict[str, Any] | None = None) -> None:
    """Reconcile internal native observations; no public proof-dictionary API.

    v1 keeps its original mandatory timing semantics. v2 requires independently
    committed execution evidence even when timing is advisory. Work on a copy so
    a foreign/conflicting observation cannot partially mutate an account.
    """
    original = account(row)
    if original['schema'] == 'taskplane.startup-account/v1':
        _complete_legacy_response(row, operation_id, response_digest, intervals,
            native_reference=native_reference, automatic=automatic, runtime=runtime)
        return
    value = deepcopy(original)
    operation = value['operations'].get(operation_id)
    w.require(isinstance(operation, dict) and automatic is True and runtime == row['expected_runtime']
              and isinstance(native_reference, str) and bool(native_reference)
              and operation.get('response_digest') == response_digest,
              'unsupported_authority', 'Startup completion needs matching native output, runtime and automatic hooks.')
    expected = {'schema': 'taskplane.startup-execution/v1', 'operation': operation_id,
                'run': row['run'], 'grant': row['grant_id'], 'attempt': row['attempt'],
                'binding': value['binding'], 'request_digest': operation['request_digest'],
                'response_digest': response_digest, 'response_bytes': operation.get('emitted_bytes'),
                'native_reference': native_reference, 'runtime': runtime,
                'automatic': True, 'post_hook_committed': True}
    w.require(execution == expected and type(operation.get('emitted_bytes')) is int,
              'unsupported_authority', 'Startup execution needs matching durable automatic hook completion and actual output bytes.')
    if operation.get('execution'):
        w.require(operation['execution'] == expected, 'invalid_evidence', 'Conflicting native startup execution.')
        if value['schema'] == 'taskplane.startup-account/v3':
            return

    if value['schema'] == 'taskplane.startup-account/v3':
        operation.update(execution=deepcopy(expected), status='completed')
        complete = all(op.get('execution') and op['status'] == 'completed'
                       for op in value['operations'].values())
        value['execution'] = 'complete' if complete else 'incomplete'
        if row.get('stop_requested') or operation['control_epoch'] != row.get('control_epoch', 0):
            value['stage'] = 'stop_requested'
        elif complete:
            if operation['kind'] == 'source-probe':
                value['stage'] = 'ready'
            elif row.get('context_receipt'):
                value['stage'] = 'brief_ready'
            elif operation['kind'] == 'claim':
                value['stage'] = 'claimed'
            elif operation['kind'] == 'context':
                value['stage'] = 'delivering'
        row['startup_account'] = value
        if value['stage'] == 'ready' and row['state'] in {'bootstrapping', 'running'}:
            row['state'] = 'running'
        return

    # Invalid/partial timing is telemetry in advisory mode, never an execution
    # substitute. Retain bounded diagnostics; do not save arbitrary caller blobs.
    valid_intervals: list[dict[str, Any]] = []
    diagnostic = 'No full native timing observation.'
    observed = None
    try:
        w.require(isinstance(intervals, list) and len(intervals) <= 64,
                  'invalid_evidence', 'Timing interval count exceeds 64.')
        observed = interval_total(intervals)
        valid_intervals = deepcopy(intervals)
        diagnostic = 'Available intervals are partial; full native timing is unknown.'
    except (w.Refusal, TypeError, ValueError):
        diagnostic = 'Timing is absent, malformed or uses conflicting clocks.'
    policy = value['timing_policy']
    cap = policy['capability']
    qualified = timing_required(value)
    from . import host_capabilities as caps
    available = caps.startup_timing_capability(policy['host'], runtime)
    full = (qualified and available == cap and caps.qualified_startup_timing(available, policy['host'], runtime)
            and observed is not None and isinstance(timing, dict)
            and timing.get('capability_sha256') == digest(cap)
            and timing.get('operation') == operation_id
            and timing.get('native_reference') == native_reference
            and timing.get('coverage') == 'complete' and timing.get('gaps') == []
            and timing.get('boundary') == cap['boundary']
            and timing.get('provider') == cap['provider']
            and all(span['clock'] == cap['clock'] for span in valid_intervals))
    record = {'coverage': 'complete' if full else 'unknown',
              'full_processing_ns': observed if full else None,
              'observed_processing_ns': observed, 'intervals': valid_intervals,
              'reason': 'Qualified full native boundary.' if full else diagnostic,
              'capability_sha256': digest(cap)}
    if operation.get('timing'):
        w.require(operation['timing'] == record, 'invalid_evidence', 'Conflicting native startup timing replay.')
        return
    operation.update(execution=deepcopy(expected), timing=record,
                     status='completed' if not qualified or full else 'execution_complete')
    operations = list(value['operations'].values())
    value['execution'] = 'complete' if all(op.get('execution') for op in operations) else 'incomplete'
    try:
        value['observed_processing_ns'] = interval_total([
            span for op in operations for span in op.get('timing', {}).get('intervals', [])])
    except w.Refusal:
        value['observed_processing_ns'] = None
    full_complete = (value['observed_processing_ns'] is not None
                     and all(op.get('timing', {}).get('coverage') == 'complete' for op in operations))
    value['processing_ns'] = value['observed_processing_ns'] if full_complete else None
    value['measurement'] = 'complete' if full_complete else 'incomplete' if qualified else 'deferred'
    if row.get('stop_requested') or operation['control_epoch'] != row.get('control_epoch', 0):
        value['stage'] = 'stop_requested'
    elif qualified and value['processing_ns'] is not None and value['processing_ns'] > STARTUP_LIMITS['processing_ns']:
        value['stage'] = 'exhausted'
        value['overshoot_ns'] = value['processing_ns'] - STARTUP_LIMITS['processing_ns']
    elif (not qualified or full_complete) and all(op['status'] == 'completed' for op in operations):
        if operation['kind'] == 'source-probe':
            value['stage'] = 'ready'
        elif row.get('context_receipt'):
            value['stage'] = 'brief_ready'
        elif operation['kind'] == 'claim':
            value['stage'] = 'claimed'
        elif operation['kind'] == 'context':
            value['stage'] = 'delivering'
    row['startup_account'] = value
    if value['stage'] == 'ready' and row['state'] in {'bootstrapping', 'running'}:
        row['state'] = 'running'


def startup_status(row: dict[str, Any]) -> dict[str, Any]:
    """Small public projection; unknown full timing stays null, never zero."""
    if not row.get('startup_account'):
        return {'execution': 'unknown', 'timing_mode': 'legacy', 'full_processing_ns': None}
    try:
        value = account(row)
    except w.Refusal as exc:
        return {'execution': 'invalid', 'reason': exc.detail[:256]}
    if value['schema'] == 'taskplane.startup-account/v3':
        return {'contract': 'execution_only', 'stage': value['stage'], 'execution': value['execution'],
                'pending_operations': [key for key, op in value['operations'].items()
                                       if op.get('status') != 'completed'],
                'stop_requested': bool(row.get('stop_requested'))}
    policy = value.get('timing_policy', {})
    return {'stage': value['stage'], 'execution': value.get('execution', 'legacy'),
            'timing_mode': policy.get('mode', 'required'), 'measurement': value['measurement'],
            'full_processing_ns': value['processing_ns'],
            'observed_processing_ns': value.get('observed_processing_ns'),
            'observed_boundary': 'Partial automatic hook entry intervals; final post-hook work, output and teardown excluded.',
            'full_timing_gaps': [] if value['measurement'] == 'complete' else ['Full native boundary unavailable or incomplete'],
            'timing_reason': policy.get('capability', {}).get('reason', 'Legacy or qualified timing contract.'),
            'responses_reserved': value['responses_reserved'], 'responses_emitted': value['responses_emitted']}


def control(state: dict[str, Any], row: dict[str, Any], principal: str,
            operation: str, request: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    """No context receipt, cancellation claim or guessed child identity is accepted."""
    from . import worker_runtime as workers
    from .context_handoff import binding
    w.require(state.get('context_contract') == 'bounded/v3' and operation in CONTROL_OPERATIONS,
              'unsupported_authority', 'The current run has no v3 startup control capability.')
    w.require(principal == state['root'] or (principal == row.get('worker_id')
              and not row.get('identity_conflict')), 'scope_violation',
              'Startup control belongs only to the original root or observed worker.')
    w.require(row.get('binding') == binding(state) and row.get('run') == state['run']
              and row.get('root') == state['root'] and row.get('expected_runtime') == runtime,
              'unsupported_authority', 'Startup control requires the exact current grant and loaded runtime.')
    w.require(isinstance(request, dict) and set(request) <= {
        'schema', 'request_id', 'request_reference', 'run', 'grant', 'attempt', 'reason', 'inputs'}
        and request.get('schema') == 'taskplane.startup-control/v1'
        and request.get('run') == state['run'] and request.get('grant') == row['grant_id']
        and type(request.get('attempt')) is int and request['attempt'] == row['attempt']
        and all(isinstance(request.get(k), str) and 0 < len(request[k].strip()) <= 512
                for k in ('request_id', 'request_reference'))
        and isinstance(request.get('reason', ''), str) and len(request.get('reason', '')) <= 1024
        and len(encode(request)) <= CONTROL_LIMIT, 'invalid_evidence', 'Invalid bounded startup control request.')
    payload_digest = digest({'operation': operation, 'principal': principal, 'request': request})
    history = row.get('startup_controls', {})
    prior = history.get(request['request_id'])
    if prior:
        w.require(prior['digest'] == payload_digest, 'invalid_evidence', 'Conflicting startup control request ID.')
        return deepcopy(prior['response'])
    w.require(len(history) < 128, 'state_unavailable', 'Startup control history is full.')
    if operation == 'omit-optional':
        inputs = request.get('inputs')
        optional = set(row.get('startup_supporting', []))
        w.require(isinstance(inputs, list) and inputs and all(isinstance(x, str) for x in inputs)
                  and set(inputs) <= optional, 'invalid_context',
                  'Only frozen supporting input IDs may be omitted; mandatory inputs require a reviewed revision.')
    else:
        w.require('inputs' not in request, 'invalid_evidence', 'Input omission requires omit-optional.')
    if operation == 'stop':
        w.require(row['state'] in workers.LIVE, 'scope_violation', 'This worker attempt is already terminal.')
        if not row.get('stop_requested'):
            row['control_epoch'] = row.get('control_epoch', 0) + 1
            row['stop_requested'] = {'request_id': request['request_id'], 'principal': principal,
                                     'reason': request.get('reason', ''), 'at': workers.now()}
            row['state'] = 'cancel_requested'
            if row.get('startup_account'):
                row['startup_account']['stage'] = 'stop_requested'
    elif operation == 'omit-optional':
        row['omitted_supporting'] = sorted(set(row.get('omitted_supporting', [])) | set(request['inputs']))
    response = {'schema': 'taskplane.startup-control-result/v1', 'operation': operation,
                'request_id': request['request_id'], 'run': state['run'], 'grant': row['grant_id'],
                'attempt': row['attempt'], 'parent': state['root'], 'state': row['state'],
                'control_epoch': row.get('control_epoch', 0),
                'remaining_required': row.get('context_delivery', {}).get('remaining_required', 'unknown'),
                'startup_stage': row.get('startup_account', {}).get('stage', 'unmeasured'),
                'startup': startup_status(row),
                'budget': {k: row.get('startup_account', {}).get(k, 'unknown') for k in
                           ('responses_reserved', 'responses_emitted', 'processing_ns', 'measurement')},
                'omitted_supporting': len(row.get('omitted_supporting', [])),
                'cancellation': 'not_observed', 'process_exit': 'unknown',
                'reservation_released': False, 'result_accepted': False,
                'permissions': {'implementation': False, 'phase_transition': False}}
    if row.get('startup_account', {}).get('schema') == 'taskplane.startup-account/v3':
        response.pop('budget')
    w.require(len(encode(response)) <= CONTROL_LIMIT, 'context_overflow', 'Startup control response exceeds 4 KiB.')
    row.setdefault('startup_controls', {})[request['request_id']] = {'digest': payload_digest, 'response': deepcopy(response)}
    row['startup_control_responses'] = row.get('startup_control_responses', 0) + 1
    return response


def allow_work(row: dict[str, Any]) -> None:
    w.require(not row.get('stop_requested'), 'scope_violation',
              'This attempt is stopped; later context or work reservations are refused.')
    if row.get('startup_account'):
        value = account(row)
        w.require(value['stage'] not in {'stop_requested', 'exhausted', 'failed'},
                  'worker_readiness', 'Startup failed or exhausted its budget; no further work is admitted.')


def runtime_artifacts(workspace: Path, request: dict[str, Any], runtime: dict[str, Any],
                      *, verification_schema: str, checks: set[str]) -> None:
    """Verify the executing bytes against the exact reviewed package and checks."""
    import hashlib
    import zipfile
    from . import workflow_evidence as evidence
    w.require(request['runtime_digest'] == digest(runtime) and runtime.get('member_sha256')
              and all(isinstance(value, str) and len(value) == 64 for value in runtime['member_sha256'].values()),
              'unsupported_authority', 'The executing runtime does not match the complete tested candidate.')
    package = request['package']
    w.require(isinstance(package, dict) and set(package) == {'path', 'sha256'},
              'invalid_evidence', 'Upgrade requires the reviewed source package path and hash.')
    archive_path = evidence.path(workspace, package['path'])
    w.require(archive_path.is_file() and archive_path.stat().st_size <= 134217728,
              'invalid_evidence', 'Upgrade package is missing or exceeds 128 MiB.')
    descriptor = os.open(archive_path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        import stat
        before = os.fstat(stream.fileno())
        w.require(stat.S_ISREG(before.st_mode) and before.st_size <= 134217728,
                  'invalid_evidence', 'Upgrade package must be a bounded regular file.')
        archive_hash = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1048576), b''):
            archive_hash.update(chunk)
        w.require(archive_hash.hexdigest() == package['sha256'], 'stale_checkpoint', 'Upgrade package bytes changed.')
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            members = archive.infolist()
            names = {i.filename for i in members}
            w.require(len(members) <= 20000 and len(names) == len(members)
                      and sum(i.file_size for i in members) <= 134217728,
                      'invalid_evidence', 'Upgrade package member inventory is ambiguous or oversized.')
            for name, expected in runtime['member_sha256'].items():
                w.require(name in names and hashlib.sha256(archive.read(name)).hexdigest() == expected,
                          'stale_checkpoint', f'Loaded runtime differs from package member {name}.')
        after = os.fstat(stream.fileno())
        def stamp(s: os.stat_result) -> tuple[int, int, int, int, int]:
            return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        w.require(stamp(before) == stamp(after) == stamp(archive_path.stat()),
                  'stale_checkpoint', 'Upgrade package changed while it was being validated.')
    verification = request['verification']
    w.require(isinstance(verification, dict) and set(verification) == {'path', 'sha256'},
              'invalid_evidence', 'Upgrade requires hash-pinned compatibility and rollback evidence.')
    raw = evidence.read(workspace, verification['path'])
    w.require(hashlib.sha256(raw).hexdigest() == verification['sha256'], 'stale_checkpoint', 'Upgrade verification changed.')
    checked = json.loads(raw)
    w.require(isinstance(checked, dict) and checked.get('schema') == verification_schema
              and checked.get('runtime_digest') == request['runtime_digest']
              and isinstance(checked.get('checks'), dict) and set(checked['checks']) == checks,
              'invalid_evidence', 'Compatibility evidence must cover every required upgrade and rollback behavior.')
    for name, check in checked['checks'].items():
        w.require(isinstance(check, dict) and type(check.get('exit')) is int and check['exit'] == 0
                  and isinstance(check.get('command'), list) and check['command']
                  and isinstance(check.get('evidence'), dict), 'invalid_evidence', f'Missing passing upgrade check: {name}.')
        pin = check['evidence']
        w.require(hashlib.sha256(evidence.read(workspace, pin.get('path', ''))).hexdigest() == pin.get('sha256'),
                  'stale_checkpoint', f'Upgrade check bytes changed: {name}.')


def upgrade_preview(workspace: Path, state: dict[str, Any], request: dict[str, Any],
                    runtime: dict[str, Any]) -> dict[str, Any]:
    """Read-only, source-bound migration proposal; no checkpoint is approved here."""
    from . import worker_runtime as workers
    from .context_handoff import selection_preview
    fields = {'schema', 'request_id', 'request_reference', 'run', 'expected_revision', 'expected_generation',
              'from_contract', 'to_contract', 'scope_digest', 'accepted_checkpoints', 'runtime_digest',
              'package', 'verification', 'selection'}
    w.require(isinstance(request, dict) and set(request) == fields
              and request.get('schema') == 'taskplane.context-upgrade-request/v1'
              and len(encode(request)) <= 262144
              and all(isinstance(request.get(k), str) and 0 < len(request[k].strip()) <= 512
                      for k in ('request_id', 'request_reference')),
              'invalid_evidence', 'Context upgrade requires the exact bounded request and actual authorization reference.')
    w.require(request['run'] == state['run'] and type(request['expected_revision']) is int
              and request['expected_revision'] == state['revision']
              and type(request['expected_generation']) is int
              and request['expected_generation'] == state.get('task_generation', 0),
              'stale_checkpoint', 'Context upgrade revision or task generation changed.')
    w.require(state.get('context_contract') == request['from_contract'] == 'bounded/v2'
              and request['to_contract'] == 'bounded/v3', 'unsupported_authority',
              'Only an explicit v2 to v3 same-run context upgrade is supported.')
    stage = w.current(state)
    w.require(stage['phase'] == 'build' and stage['decision'] == 'not_requested' and not stage.get('packet')
              and not any(state.get(k) for k in ('finished', 'retired', 'cancellation', 'invalidation_pending', 'revision_intent')),
              'scope_violation', 'Context upgrade needs an active unsealed Build boundary.')
    w.accepted_plan(state)
    checkpoints = {v['phase']: {'visit': v['id'], 'checkpoint': v['packet']['checkpoint'],
                               'packet_digest': digest(v['packet'])}
        for v in state['visits'][:state['index']] if v.get('decision') == 'approved'
        and not v.get('superseded') and v.get('packet') and v['phase'] in {'product', 'design', 'plan'}}
    w.require(set(checkpoints) == {'product', 'design', 'plan'} and request['accepted_checkpoints'] == checkpoints
              and request['scope_digest'] == digest(state['scope']), 'stale_checkpoint',
              'Accepted Product, Design, Plan or scope differs from the reviewed upgrade.')
    w.require(workers.joined(state) and not any(r.get('state') == 'running'
              for r in state.get('observed_handles', {}).values()), 'scope_violation',
              'Context upgrade cannot pass live workers, unresolved reservations or command handles.')
    runtime_artifacts(workspace, request, runtime,
        verification_schema='taskplane.context-upgrade-verification/v1',
        checks={'v2_unchanged', 'v3_old_reader_refusal', 'migration_retry', 'authorization_refusals', 'quiescent_rollback'})
    selected = selection_preview(workspace, state, request['selection'])
    preview = {'schema': 'taskplane.context-upgrade-preview/v1', 'run': state['run'],
        'request_id': request['request_id'], 'request_digest': digest(request),
        'revision': state['revision'], 'from_contract': 'bounded/v2', 'to_contract': 'bounded/v3',
        'scope_digest': digest(state['scope']), 'accepted_checkpoints': checkpoints,
        'runtime_digest': digest(runtime), 'package_sha256': request['package']['sha256'],
        'verification_sha256': request['verification']['sha256'], 'selection': selected,
        'invalidation': {'delivery_receipts': 'all prior bindings', 'parent_readiness': True,
                         'prior_task_results': sorted(state.get('task_results', {})),
                         'checks': 'runtime-dependent checks require fresh applicability verification'},
        'approvals_created': 0, 'process_census': 'observed handles only; OS census unavailable',
        'assurance': 'cooperative observed source and root authorization; not protected-host attestation'}
    return {**preview, 'preview_digest': digest(preview)}


def require_runtime(state: dict[str, Any]) -> None:
    """A migrated contract must keep the reviewed enforcing runtime for writes."""
    pin = state.get('context_runtime_digest')
    if pin is not None:
        from . import host_capabilities
        w.require(isinstance(pin, str) and pin == digest(host_capabilities.runtime_identity()),
                  'unsupported_authority', 'This runtime differs from the enforcing runtime pinned to the run. '
                  'Inspect the run or use an explicitly verified compatible rollback. '
                  'Worker controls still require their exact recorded runtime and native binding.')


def validate_control_history(state: dict[str, Any]) -> None:
    """Reject corrupt revision/migration records before they can affect a grant."""
    def checksum(value: Any) -> bool:
        return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)
    pin = state.get('context_runtime_digest')
    w.require(pin is None or checksum(pin) and state.get('context_contract') == 'bounded/v3',
              'state_unavailable', 'Invalid enforcing context runtime pin.')
    for name, kind in [('context_upgrades', 'upgrade'), ('context_rollbacks', 'rollback')]:
        events = state.get(name, {})
        w.require(isinstance(events, dict) and len(events) <= 256,
                  'state_unavailable', 'Invalid context transition history.')
        for key, event in events.items():
            w.require(isinstance(event, dict) and event.get('schema') == f'taskplane.context-{kind}-event/v1'
                      and checksum(event.get('request_digest')) and checksum(event.get('preview_digest'))
                      and isinstance(event.get('result'), dict) and event['result'].get('request_id') == key
                      and event['result'].get('run') == state['run'] and event['result'].get('status') == 'committed'
                      and event['result'].get('context_contract') == 'bounded/v3'
                      and event['result'].get('approvals_created') == 0 and pin is not None
                      and type(event.get('prior_revision')) is int and event['prior_revision'] >= 0
                      and type(event['result'].get('revision')) is int
                      and event['result']['revision'] == event['prior_revision'] + 1,
                      'state_unavailable', 'Invalid committed context transition.')
    events = state.get('requirements_revisions', {})
    w.require(isinstance(events, dict) and len(events) <= 256,
              'state_unavailable', 'Invalid requirements revision history.')
    pending = []
    for key, event in events.items():
        w.require(isinstance(event, dict) and event.get('schema') == 'taskplane.requirements-revision-event/v1'
                  and isinstance(event.get('request'), dict) and event['request'].get('request_id') == key
                  and event['request'].get('run') == state['run']
                  and event.get('request_digest') == digest(event['request'])
                  and isinstance(event.get('preview'), dict) and isinstance(event.get('result'), dict)
                  and type(event.get('opened')) is bool and isinstance(event.get('history'), list)
                  and event.get('status') in {'pending', 'review', 'aborted', 'completed'},
                  'state_unavailable', 'Invalid requirements revision event.')
        preview = event['preview']; result = event['result']
        w.require(preview.get('request_digest') == event['request_digest']
                  and preview.get('preview_digest') == digest({k: v for k, v in preview.items() if k != 'preview_digest'})
                  and isinstance(preview.get('binding'), dict) and preview['binding'].get('run') == state['run']
                  and isinstance(preview.get('tasks'), list) and isinstance(preview.get('criteria'), list)
                  and result.get('run') == state['run'] and result.get('request_id') == key
                  and result.get('status') == event['status'] and result.get('approvals_created') == 0
                  and (event['status'] not in {'review', 'completed'} or event['opened']),
                  'state_unavailable', 'Requirements revision proof or status is inconsistent.')
        if event['status'] != 'completed':
            pending.append(key)
    active = state.get('active_requirements_revision')
    w.require((not pending and active is None) or (pending == [active] and state.get('dispatch_paused') is True),
              'state_unavailable', 'Pending requirements revision must retain its dispatch fence.')


def rollback_preview(workspace: Path, state: dict[str, Any], request: dict[str, Any],
                     runtime: dict[str, Any]) -> dict[str, Any]:
    """Rebind a quiescent v3 run to tested v3-capable bytes without rewinding it."""
    from . import worker_runtime as workers
    from .context_handoff import selection_preview
    fields = {'schema', 'request_id', 'request_reference', 'run', 'expected_revision', 'expected_generation',
              'from_contract', 'to_contract', 'scope_digest', 'accepted_checkpoints', 'prior_runtime_digest',
              'runtime_digest', 'package', 'verification', 'reason'}
    w.require(isinstance(request, dict) and set(request) == fields and len(encode(request)) <= 262144
              and request.get('schema') == 'taskplane.context-rollback-request/v1'
              and all(isinstance(request.get(k), str) and 0 < len(request[k].strip()) <= limit for k, limit in
                      [('request_id', 128), ('request_reference', 512), ('reason', 2048)]),
              'invalid_evidence', 'Compatible rollback requires an exact bounded request and actual reason.')
    w.require(request['run'] == state['run'] and type(request['expected_revision']) is int
              and request['expected_revision'] == state['revision'] and type(request['expected_generation']) is int
              and request['expected_generation'] == state.get('task_generation', 0)
              and request['scope_digest'] == digest(state['scope'])
              and request['prior_runtime_digest'] == state.get('context_runtime_digest')
              and isinstance(request['prior_runtime_digest'], str),
              'stale_checkpoint', 'Rollback run, revision, generation, scope or enforcing runtime changed.')
    w.require(state.get('context_contract') == request['from_contract'] == request['to_contract'] == 'bounded/v3',
              'unsupported_authority', 'Rollback must preserve bounded/v3 enforcement; incapable runtimes remain read-only.')
    stage = w.current(state)
    w.require(stage['phase'] == 'build' and stage['decision'] == 'not_requested' and not stage.get('packet')
              and not any(state.get(k) for k in ('finished', 'retired', 'cancellation', 'invalidation_pending',
                                               'active_requirements_revision', 'revision_intent')),
              'scope_violation', 'Compatible rollback requires an active unsealed Build boundary.')
    w.accepted_plan(state)
    checkpoints = {v['phase']: {'visit': v['id'], 'checkpoint': v['packet']['checkpoint'],
                               'packet_digest': digest(v['packet'])}
        for v in state['visits'][:state['index']] if v.get('decision') == 'approved'
        and not v.get('superseded') and v.get('packet') and v['phase'] in {'product', 'design', 'plan'}}
    w.require(set(checkpoints) == {'product', 'design', 'plan'} and request['accepted_checkpoints'] == checkpoints,
              'stale_checkpoint', 'Accepted checkpoints changed before compatible rollback.')
    w.require(workers.joined(state) and not any(row.get('state') == 'running'
              for row in state.get('observed_handles', {}).values()), 'scope_violation',
              'Compatible rollback cannot pass live workers, unresolved reservations or command handles.')
    runtime_artifacts(workspace, request, runtime,
        verification_schema='taskplane.context-rollback-verification/v1',
        checks={'compatible_contract', 'after_state_reader', 'obligations_preserved', 'old_reader_refusal', 'crash_retry'})
    selection = deepcopy(state.get('context_selection'))
    w.require(isinstance(selection, dict), 'invalid_context', 'Rollback needs the current complete selection.')
    assert isinstance(selection, dict)
    selection['task_generation'] = state.get('task_generation', 0) + 1
    selected = selection_preview(workspace, state, selection)
    preview = {'schema': 'taskplane.context-rollback-preview/v1', 'run': state['run'],
        'request_id': request['request_id'], 'request_digest': digest(request),
        'revision': state['revision'], 'from_contract': 'bounded/v3', 'to_contract': 'bounded/v3',
        'scope_digest': digest(state['scope']), 'accepted_checkpoints': checkpoints,
        'prior_runtime_digest': state['context_runtime_digest'], 'runtime_digest': digest(runtime),
        'package_sha256': request['package']['sha256'], 'verification_sha256': request['verification']['sha256'],
        'selection': selected, 'approvals_created': 0,
        'invalidation': {'delivery_receipts': 'all prior bindings', 'parent_readiness': True,
                         'prior_task_results': sorted(state.get('task_results', {})),
                         'checks': 'runtime-dependent checks require fresh applicability verification'},
        'history': 'Preserve all current decisions, events, worker records and obligations; no snapshot restore.',
        'assurance': 'cooperative observed source and root authorization; not protected-host attestation'}
    return {**preview, 'preview_digest': digest(preview)}
