"""Full production CLI measurement, with a separate auxiliary Session fixture.

The default path invokes the actual tp.py flow context command and refuses to
claim complete measurement without production controller instrumentation. The
--operation build/drain fixture remains explicitly auxiliary Session evidence.
Warm means immutable objects already exist, never receipt reuse. These are
synthetic disposable fixtures, not installed-host proof or approval observations.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))

def _load_runtime():
    # Direct invocation establishes isolation before importing candidate code.
    global Metrics, Store, bytes_read, count, digest, encode, measured, Session, binding, check_drain
    sys.dont_write_bytecode = True
    from taskplane.context import Metrics, Store, bytes_read, count, digest, encode, measured
    from taskplane.context_handoff import Session, binding
    from taskplane.context_delivery import check_drain


def _rss():
    from taskplane.context_delivery import peak_resident_bytes
    return peak_resident_bytes()


def _read(path):
    count('source_stat')
    count('source_read')
    raw = path.read_bytes()
    bytes_read('source_fingerprints', len(raw))
    count('source_hash')
    hashlib.sha256(raw).hexdigest()
    count('source_parse')
    return json.loads(raw)


def _write(path, value):
    from taskplane import primitives
    count('source_write')
    primitives.atomic_json(path, value)


def fixture(workspace, nodes, shape='primary'):
    """Fixed 1024-byte JSON payloads; no native host evidence is fabricated."""
    _load_runtime()
    state = {'workspace': str(workspace), 'root': 'context-benchmark-fixture',
             'run': 'context-benchmark', 'revision': 0, 'task_generation': 0,
             'scope': {'criteria': ['SCA-01'], 'paths': {'build': []}},
             'context_contract': 'bounded/v2', 'index': 0,
             'visits': [{'id': 'build-fixture', 'phase': 'build', 'decision': 'not_requested'}]}
    payloads = [f'{i:08d}:' + 'x' * (1022 - 9) for i in range(nodes)]
    if shape == 'multipage':
        payloads = [list(range(10000 + i, 10300 + i)) for i in range(nodes)]
    elif shape == 'high-edge':
        # Canonical indexed lists reuse small leaves across many required roots.
        payloads = [[f'shared-{j:04d}:' + 'z' * 500 for j in range(32)] + [text]
                    for text in payloads]
    items = [{'id': f'body/{i:06d}', 'kind': 'benchmark-body', 'body': body}
             for i, body in enumerate(payloads)]
    if shape == 'shared':
        items += [{'id': f'role/{i:06d}', 'kind': 'benchmark-role', 'body': body}
                  for i, body in enumerate(payloads)]
    meta = {'authority': {'binding': binding(state), 'phase': 'build', 'decision': 'not_requested',
                         'criteria': ['SCA-01'], 'paths': [], 'accepted_inputs': [],
                         'context_is_authority': False},
            'coverage': {'model_attention': 'unobservable', 'fixture': True}}
    return state, {'binding': binding(state), 'task': 'BENCH', 'inputs': [items, ['BENCH'], ['SCA-01'], meta]}


def execute(operation, workspace, sample_id, nodes=64, shape='primary'):
    _load_runtime()
    metrics = Metrics()
    started = time.perf_counter()
    with measured(metrics):
        count('process_startup')
        if operation == 'noop':
            result = {'noop': True, 'bytecode_disabled': sys.dont_write_bytecode,
                      'runtime_environment': {key: os.environ[key] for key in (
                          'HOME', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'XDG_CONFIG_HOME',
                          'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME', 'TMPDIR')}}
        elif operation == 'build':
            state, snapshot = fixture(workspace, nodes, shape)
            session = Session(workspace, state, 'BENCH', snapshot=snapshot)
            _write(workspace/'fixture.json', {'state': state, 'snapshot': session.frozen})
            result = {'index': session.page_index, 'coverage_expected': sorted({r['ref']['sha256'] for r in session.required}),
                      'prepared_ref': session.prepared_ref}
        else:
            data = _read(workspace/'fixture.json')
            consumer = {'worker_id': sample_id, 'grant_id': sample_id, 'attempt': 1,
                        'task_id': 'BENCH', 'task_generation': 0}
            session = Session(workspace, data['state'], 'BENCH', snapshot=data['snapshot'], consumer=consumer)
            if operation == 'drain':
                response = session.drain(session.handoff_ref['sha256'])
                check_drain(response)
                result = {'response': response}
                if response['done']:
                    _write(workspace/(sample_id + '-receipt.json'), response['context_receipt'])
                    result['coverage_observed'] = session.store.resolve(response['context_receipt']['receipt'])['returned_refs']
            elif operation == 'validate':
                receipt = _read(workspace/(sample_id + '-receipt.json'))
                session.validate(receipt)
                result = {'coverage_observed': session.store.resolve(receipt['receipt'])['returned_refs']}
            else:
                raise ValueError(operation)
        # Returned transport bytes are explicit, separate from actual file reads.
        response_bytes = len(encode(result.get('response', result)))
        metrics.byte_classes['transport'] += response_bytes
        result['measurement'] = {**metrics.report(), 'elapsed_seconds': time.perf_counter() - started,
                                 'peak_rss_bytes': _rss(), 'transport_bytes': response_bytes}
    return result


def isolated_environment(root):
    root = external_path(root)
    root.mkdir(parents=True, exist_ok=True)
    allowed = {'PATH', 'SystemRoot', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT',
               'LANG', 'LC_ALL', 'TZ'}
    env = {k: v for k, v in os.environ.items() if k in allowed}
    directories = {'HOME': 'home', 'USERPROFILE': 'home', 'CODEX_HOME': 'codex',
                   'CLAUDE_CONFIG_DIR': 'claude', 'XDG_CONFIG_HOME': 'xdg/config',
                   'XDG_CACHE_HOME': 'xdg/cache', 'XDG_DATA_HOME': 'xdg/data',
                   'XDG_STATE_HOME': 'xdg/state', 'XDG_RUNTIME_DIR': 'xdg/runtime',
                   'APPDATA': 'appdata', 'LOCALAPPDATA': 'localappdata',
                   'TMPDIR': 'tmp', 'TMP': 'tmp', 'TEMP': 'tmp',
                   'RUFF_CACHE_DIR': 'cache/ruff', 'MYPY_CACHE_DIR': 'cache/mypy'}
    for key, relative in directories.items():
        target = root/relative
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        env[key] = str(target)
    env.update(TASKPLANE_CONTEXT_ISOLATION=str(root), PYTHONDONTWRITEBYTECODE='1',
               PYTHONNOUSERSITE='1', CODEX_THREAD_ID='context-benchmark-fixture',
               PLUGIN_ROOT=str(ROOT), CLAUDE_PLUGIN_ROOT=str(ROOT))
    return env


def session_sample(workspace, *, nodes, mode, repetition, shape='primary', evidence_root=None):
    """Auxiliary Session-only evidence; never satisfies the full CLI gate."""
    workspace = external_path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    evidence_root = external_path(evidence_root or workspace/'benchmark-evidence')
    evidence_root.mkdir(parents=True, exist_ok=True)
    sample_id = f'{shape}-{nodes}-{mode}-{repetition}'
    env = isolated_environment(workspace/'isolated-hosts')
    _load_runtime()
    executions, measured_results = [], []

    def run(operation):
        command = [sys.executable, str(Path(__file__).resolve()), '--operation', operation,
                   '--workspace', str(workspace), '--sample-id', sample_id,
                   '--nodes', str(nodes), '--shape', shape]
        started = time.perf_counter()
        proc = subprocess.run(command, cwd=workspace, env=env, capture_output=True, timeout=180)
        elapsed = time.perf_counter() - started
        stdout_path = evidence_root/f'{sample_id}-{len(executions):04d}-{operation}.stdout'
        stderr_path = stdout_path.with_suffix('.stderr')
        stdout_path.write_bytes(proc.stdout)
        stderr_path.write_bytes(proc.stderr)
        record = {'command': command, 'argv': command, 'cwd': str(workspace),
                  'stdout_base64': base64.b64encode(proc.stdout).decode('ascii'),
                  'stderr_base64': base64.b64encode(proc.stderr).decode('ascii'),
                  'exit_code': proc.returncode, 'elapsed_seconds': elapsed,
                  'stdout_path': str(stdout_path), 'stdout_sha256': hashlib.sha256(proc.stdout).hexdigest(),
                  'stdout_bytes': len(proc.stdout), 'stderr_path': str(stderr_path),
                  'stderr': proc.stderr.decode('utf-8', 'replace')}
        executions.append(record)
        if proc.returncode:
            raise RuntimeError(json.dumps({'failed_execution': record, 'raw_executions': executions}))
        value = json.loads(proc.stdout)
        if operation != 'noop':
            measured_results.append((operation, elapsed, value['measurement']))
        return value, elapsed

    # Matched import/process baseline. It is retained, not silently subtracted
    # from nonpositive or resolution-dominated measurements into a passing zero.
    _, baseline = run('noop')
    built, _ = run('build')
    responses = []
    for _ in range(10000):
        value, _ = run('drain')
        responses.append(value['response'])
        if value['response']['done']:
            break
    else:
        raise RuntimeError('Drain did not complete; raw evidence retained at ' + str(evidence_root))
    # Completion already performs the authoritative graph/segment reconciliation.
    # A later seal validation is a separate operation, tested independently.
    validated = value
    units, classes = Counter(), Counter()
    for _, _, row in measured_results:
        units.update(row['operation_classes'])
        classes.update(row['byte_classes'])
    total = sum(elapsed for _, elapsed, _ in measured_results)
    index = built['index']
    record = {'sample_id': sample_id, 'measurement_class': 'real', 'shape': shape,
              'nodes': index['nodes'], 'requested_nodes': nodes, 'edges': index['edges'], 'pages': index['pages'],
              'invocations': len(responses), 'processes': len(measured_results),
              'payload_bytes': 1024 if shape in {'primary', 'shared'} else None,
              'mode': mode, 'repetition': repetition, 'operations': sum(units.values()),
              'operation_classes': dict(units), 'bytes_read': sum(classes.values()),
              'unique_body_bytes': index['unique_body_bytes'],
              'transport_bytes': sum(row['transport_bytes'] for _, _, row in measured_results),
              'max_response_pages': max(len(r['pages']) for r in responses),
              'max_response_bytes': max(len(encode(r)) for r in responses),
              'max_index_rebuilds_per_invocation': max(r['index_rebuilds'] for _, _, r in measured_results),
              'byte_classes': dict(classes), 'coverage_expected': built['coverage_expected'],
              'coverage_observed': validated['coverage_observed'], 'elapsed_seconds': total,
              'startup_seconds': baseline * len(measured_results),
              'clock_resolution_seconds': time.get_clock_info('perf_counter').resolution,
              'peak_rss_bytes': (None if any(r['peak_rss_bytes'] is None for _, _, r in measured_results)
                                 else max(r['peak_rss_bytes'] for _, _, r in measured_results)),
              'raw_executions': executions, 'design_scenario_ids': ['SCA01-drain-grid', 'SCA01-cost'],
              'measurement_scope': 'fresh fixture CLI using production Session; full Controller CLI is a separate integration obligation'}
    (evidence_root/(sample_id + '.json')).write_text(json.dumps(record, indent=2), encoding='utf-8')
    return record


def session_benchmark(root):
    samples = []
    for nodes in (64, 256, 1024):
        warm_workspace = None
        for repetition in range(5):
            workspace = root/f'primary-{nodes}-cold-{repetition}'
            samples.append(session_sample(workspace, nodes=nodes, mode='cold', repetition=repetition))
            warm_workspace = workspace
        for repetition in range(5):
            samples.append(session_sample(warm_workspace, nodes=nodes, mode='warm', repetition=repetition))
    summary = []
    for mode in ('cold', 'warm'):
        previous = None
        for nodes in (64, 256, 1024):
            rows = [r for r in samples if r['nodes'] == nodes and r['mode'] == mode]
            adjusted = [r['elapsed_seconds'] - r['startup_seconds'] for r in rows]
            maximum = max(adjusted)
            summary.append({'nodes': nodes, 'mode': mode,
                'p50_seconds': statistics.median(r['elapsed_seconds'] for r in rows),
                'p95_seconds': max(r['elapsed_seconds'] for r in rows),
                'p95_adjusted_seconds': maximum,
                'adjusted_growth': None if previous is None or previous <= 0 else maximum / previous,
                'timing_conclusive': all(a > 10*r['clock_resolution_seconds'] for a, r in zip(adjusted, rows))})
            previous = maximum
    return {'schema': 'taskplane.context-benchmark/v2', 'samples': samples, 'timing': summary,
            'native_model_tokens': 'not measured', 'model_attention': 'unobservable',
            'full_controller_cli': 'requires assembled candidate integration measurement'}


CONTROLLER_DEPENDENCIES = ('taskplane/flow.py', 'taskplane/workflow.py',
    'taskplane/workflow_host.py', 'taskplane/workflow_local.py',
    'taskplane/workflow_evidence.py', 'taskplane/worker_runtime.py',
    'taskplane/worker_results.py', 'taskplane/context_reuse.py')


def external_path(path):
    path = Path(path).resolve()
    if path == ROOT or path.is_relative_to(ROOT):
        raise ValueError('Benchmark stores, evidence and caches must be outside candidate source')
    return path


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def _save(path, value):
    path = external_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(_json(value) + b'\n')


def prepare_controller_fixture(workspace, payload_nodes, shape):
    """Synthetic disposable state through real production storage/validation.

    No trusted HostAdapter is injected, no accepted decision is created, and no
    Session.inputs snapshot is substituted. The public CLI will select and audit
    this actual cooperative controller. Refused host authority is never bypassed.
    """
    from taskplane import workflow as w, workflow_host as h, primitives
    workspace = external_path(workspace)
    isolation = external_path(os.environ['TASKPLANE_CONTEXT_ISOLATION'])
    if (workspace/'.taskplane').exists():
        raise ValueError('Controller fixture requires a fresh disposable workspace')
    if not Path(os.environ['CODEX_HOME']).resolve().is_relative_to(isolation):
        raise ValueError('Fixture requires isolated host stores')
    scope = {'criteria': ['SCA-01'], 'paths': {p: [p + '.json'] for p in w.PHASES}}
    tasks = {'tasks': [{'id': 'BENCH', 'phase': 'product', 'owner': 'context-benchmark-fixture',
                        'dependencies': [], 'paths': ['product.json'], 'criteria': ['SCA-01'],
                        'verification': 'Synthetic context workload; no approval or native proof.',
                        'read_inputs': []}]}
    _save(workspace/'tasks.json', tasks)
    controller = h.Controller(workspace, 'context-benchmark-fixture', h.installed_adapter('codex'))
    state = controller.start({'scope': scope, 'entry': 'product', 'standalone': False,
        'tasks': 'tasks.json', 'goal': 'Synthetic disposable context measurement.',
        'request_reference': 'fixture/context-benchmark'})
    findings = []
    for i in range(payload_nodes):
        row = {'origin_run': 'synthetic-context-fixture', 'id': f'F{i:06d}',
               'owner': 'fixture', 'criteria': ['SCA-01'], 'disposition': 'open',
               'verification': 'unverified', 'required_evidence': [], 'evidence_refs': [],
               'payload': ''}
        row['payload'] = 'x' * (1024 - len(_json(row)))
        if len(_json(row)) != 1024:
            raise ValueError('Primary payload is not exactly 1024 bytes')
        if shape == 'multipage':
            from taskplane.context import Store
            # An actual required list-valued node has five transport pages;
            # nesting the same list in a small finding dict would have just one.
            row['evidence_refs'] = [Store(workspace).put('finding-evidence',
                                                       [f'{j:04d}' for j in range(300)])]
        elif shape == 'high-edge':
            row['payload'] = ['shared-edge-' + str(j) + 'z' * 500 for j in range(32)]
        if shape == 'shared':
            from taskplane.context import Store
            row['evidence_refs'] = [Store(workspace).put('finding-evidence',
                                                       ['shared-' + 'z' * 1015] * 32)]
        findings.append(row)
    # This fixture is synthetic authority in isolated stores, never a historical
    # import. Use the real transaction and database validators, without stubs.
    target = controller._path()
    with primitives.file_lock(str(target)):
        database = controller._read(target)
        database['runs'][state['run']]['inherited_findings'] = {'findings': findings}
        w.validate_state(database['runs'][state['run']])
        controller._write(target, database)
    return {'run': state['run'], 'task': 'BENCH', 'payload_nodes': payload_nodes,
            'authority': 'synthetic-disposable-fixture', 'decisions_created': 0}


def graph_oracle(workspace, manifest_ref):
    """Read/digest actual canonical bodies, without trusting PageIndex counts."""
    objects = external_path(workspace)/'.taskplane/context-v1/objects'
    cache = {}
    def node(ref):
        key = ref['sha256']
        if (not isinstance(key, str) or re.fullmatch('[a-f0-9]{64}', key) is None
                or type(ref.get('bytes')) is not int or ref['bytes'] < 0):
            raise ValueError('Oracle reference is malformed')
        raw = (objects/(key + '.json')).read_bytes()
        value = json.loads(raw)
        if (hashlib.sha256(raw).hexdigest() != key or len(raw) != ref['bytes']
                or value['schema'] != 'taskplane.context-object/v1'
                or value['kind'] != ref['kind'] or value['source_key'] != ref['source_key']):
            raise ValueError('Oracle canonical reference mismatch')
        cache[key] = (value, len(raw))
        return value
    manifest = node(manifest_ref)['data']
    if manifest.get('schema') != 'taskplane.context-required-manifest/v1':
        raise ValueError('Oracle requires the authoritative required manifest')
    pending = [ref for row in manifest['required'] for ref in [row['body'], *row['provenance']]]
    seen, references, edges, pages, size = set(), {}, 0, [], 0
    incoming = Counter()
    while pending:
        ref = pending.pop()
        key = ref['sha256']
        if key in references and references[key] != ref:
            raise ValueError('Oracle conflicting reference')
        references[key] = ref
        if key in seen:
            continue
        if len(seen) >= 20000 or size > 128 * 1024 * 1024:
            raise ValueError('Oracle graph exceeds supported bounds')
        value = node(ref)
        seen.add(key)
        size += cache[key][1]
        data, form = value['data'], value['form']
        children = ([] if form == 'value' else [r[1] for r in data] if form == 'dict'
                    else [r[1] if isinstance(r, list) else r for r in data] if form == 'entries'
                    else list(data))
        edges += len(children)
        incoming.update(child['sha256'] for child in children)
        pending.extend(children)
        pages.extend([key, page] for page in range(max(1, (len(data) + 63)//64) if isinstance(data, list) else 1))
    return {'nodes': len(seen), 'edges': edges, 'pages': len(pages),
            'shared_edges': sum(max(0, amount - 1) for amount in incoming.values()),
            'unique_body_bytes': size, 'expected_pages': sorted(pages),
            'coverage_expected': sorted({r['root']['sha256'] for r in manifest['required']})}


def _process(command, *, cwd, env, evidence_root, ordinal, purpose):
    """Retain exact channel bytes on success, failure, timeout and spawn error."""
    started = time.perf_counter()
    error = None
    try:
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=180)
        stdout, stderr, code = result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired as exc:
        stdout, stderr, code, error = exc.stdout or b'', exc.stderr or b'', None, 'timeout'
    except OSError as exc:
        stdout, stderr, code, error = b'', b'', None, repr(exc)
    record = {'argv': command, 'command': command, 'cwd': str(cwd), 'exit_code': code,
              'stdout_base64': base64.b64encode(stdout).decode('ascii'),
              'stderr_base64': base64.b64encode(stderr).decode('ascii'),
              'elapsed_seconds': time.perf_counter() - started, 'purpose': purpose,
              'environment': env, 'error': error}
    _save(evidence_root/f'{ordinal:05d}-{purpose}.json', record)
    return record, stdout


def sample(workspace, *, nodes, mode, repetition, shape='primary', evidence_root=None,
           warm_objects=None):
    """One full fresh-public-CLI consumer, including construction and completion."""
    workspace = external_path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    evidence_root = external_path(evidence_root or workspace/'benchmark-evidence')
    evidence_root.mkdir(parents=True, exist_ok=True)
    env = isolated_environment(evidence_root/'isolation')
    sample_id = f'{shape}-{nodes}-{mode}-{repetition}'
    row = {'sample_id': sample_id, 'measurement_scope': 'full-controller-cli',
           'measurement_class': 'real', 'authority': 'synthetic-disposable-fixture',
           'shape': shape, 'mode': mode, 'repetition': repetition, 'target_nodes': nodes,
           'execution_status': 'unknown', 'raw_executions': [], 'errors': []}
    measured_rows, responses = [], []
    def invoke(command, purpose, measured_command=False):
        ordinal = len(row['raw_executions'])
        metrics_path = evidence_root/'isolation'/f'metrics-{ordinal:05d}.json'
        child_env = {**env, 'TASKPLANE_CONTEXT_METRICS': str(metrics_path)}
        execution, stdout = _process(command, cwd=workspace, env=child_env,
            evidence_root=evidence_root, ordinal=ordinal, purpose=purpose)
        row['raw_executions'].append(execution)
        if execution['exit_code'] != 0:
            raise RuntimeError('Candidate command failed; exact output is retained')
        if measured_command:
            if not metrics_path.is_file():
                raise RuntimeError('Full Controller instrumentation sidecar is missing')
            raw_metrics = metrics_path.read_bytes()
            report = json.loads(raw_metrics)
            execution['metrics_base64'] = base64.b64encode(raw_metrics).decode('ascii')
            if (report.get('schema') != 'taskplane.context-cli-metrics/v1'
                    or report.get('coverage') != 'complete' or report.get('argv') != command
                    or report.get('cwd') != str(workspace) or report.get('exit_code') != 0
                    or report.get('stdout_sha256') != hashlib.sha256(stdout).hexdigest()):
                raise RuntimeError('Full Controller measurement is missing, incomplete or foreign')
            expected_classes = {'payload', 'transport', 'index', 'ledger', 'source_fingerprints'}
            if (set(report['byte_classes']) != expected_classes
                    or any(type(v) is not int or v < 0 for v in [*report['byte_classes'].values(), *report['operation_classes'].values()])
                    or report['bytes_read'] != sum(report['byte_classes'].values())
                    or report['operations'] != sum(report['operation_classes'].values())
                    or report['byte_classes']['transport'] != len(stdout)):
                raise ValueError('Invalid declared primitive counts or exact transport bytes')
            measured_rows.append((execution, report))
        return json.loads(stdout), execution
    try:
        missing = [name for name in CONTROLLER_DEPENDENCIES if not (ROOT/name).is_file()]
        if missing:
            row['missing_dependencies'] = missing
            raise RuntimeError('Candidate dependencies are absent; no candidate subprocess was started')
        prepared, _ = invoke([sys.executable, str(Path(__file__).resolve()), '--operation', 'controller-fixture',
            '--workspace', str(workspace), '--nodes', str(nodes - 1), '--shape', shape], 'fixture')
        if mode == 'warm':
            if warm_objects is None:
                raise ValueError('Warm sample requires an actual previous immutable-object store')
            shutil.copytree(external_path(warm_objects), workspace/'.taskplane/context-v1/objects', dirs_exist_ok=True)
        _, baseline = invoke([sys.executable, str(ROOT/'taskplane/tp.py'), 'version'], 'startup-baseline')
        command = [sys.executable, str(ROOT/'taskplane/tp.py'), 'flow', 'context',
                   '--workspace', str(workspace), '--run', prepared['run'], '--task', prepared['task']]
        construction, _ = invoke(command, 'construction', True)
        oracle = graph_oracle(workspace, construction['measurement_graph']['manifest'])
        row['oracle'] = oracle
        if shape == 'primary' and oracle['nodes'] != nodes:
            raise ValueError('Actual canonical graph size differs from the required primary N')
        if ((shape == 'multipage' and oracle['pages'] <= oracle['nodes'])
                or (shape == 'shared' and oracle['shared_edges'] <= 0)
                or (shape == 'high-edge' and oracle['edges'] <= oracle['nodes'])):
            raise ValueError('Actual canonical graph does not establish the requested variant')
        key = construction['handoff_ref']['sha256']
        for _ in range(10000):
            response, execution = invoke([*command, '--drain', key], 'drain', True)
            if (not isinstance(response.get('pages'), list) or type(response.get('remaining_required')) is not int
                    or response['remaining_required'] < 0 or len(response['pages']) > 64
                    or len(base64.b64decode(execution['stdout_base64'])) > 32768):
                raise ValueError('Invalid full CLI drain response')
            responses.append(response)
            if response.get('done') is True and response['remaining_required'] == 0:
                break
            if not response['pages']:
                raise ValueError('Incomplete drain made no progress')
        else:
            raise ValueError('Full CLI drain exceeded invocation limit')
        actual_pages = [[p['sha256'], p['page']] for r in responses for p in r['pages']]
        if sorted(actual_pages) != oracle['expected_pages']:
            raise ValueError('Missing, duplicated or foreign actual delivered pages')
        receipt = responses[-1]['context_receipt']['receipt']
        raw = (workspace/'.taskplane/context-v1/objects'/(receipt['sha256'] + '.json')).read_bytes()
        if hashlib.sha256(raw).hexdigest() != receipt['sha256']:
            raise ValueError('Final receipt digest mismatch')
        observed = json.loads(raw)['data']['returned_refs']
        if observed != oracle['coverage_expected']:
            raise ValueError('Independent final required-root coverage mismatch')
        units, classes = Counter(), Counter()
        for _, report in measured_rows:
            units.update(report['operation_classes'])
            classes.update(report['byte_classes'])
        row.update({k: oracle[k] for k in ('nodes', 'edges', 'pages', 'unique_body_bytes', 'coverage_expected')})
        row.update(invocations=len(responses), processes=len(measured_rows),
            payload_bytes=1024 if shape == 'primary' else 0,
            operations=sum(units.values()), operation_classes=dict(units), byte_classes=dict(classes),
            bytes_read=sum(classes.values()), transport_bytes=classes['transport'], coverage_observed=observed,
            elapsed_seconds=sum(e['elapsed_seconds'] for e, _ in measured_rows),
            startup_seconds=baseline['elapsed_seconds'] * len(measured_rows),
            clock_resolution_seconds=time.get_clock_info('perf_counter').resolution,
            peak_rss_bytes=min(r['peak_rss_bytes'] or 0 for _, r in measured_rows),
            max_response_pages=max(len(r['pages']) for r in responses),
            max_response_bytes=max(len(base64.b64decode(e['stdout_base64'])) for e, _ in measured_rows if e['purpose'] == 'drain'),
            max_index_rebuilds_per_invocation=max(r['index_rebuilds'] for _, r in measured_rows))
        row['peak_rss_bytes'] = (max(r['peak_rss_bytes'] for _, r in measured_rows)
                                 if row['peak_rss_bytes'] > 0 else None)
        row['timing_conclusive'] = row['elapsed_seconds'] - row['startup_seconds'] > 10 * row['clock_resolution_seconds']
        if not row['timing_conclusive'] or row['peak_rss_bytes'] is None:
            raise RuntimeError('RSS unavailable or startup-adjusted measurement inconclusive')
        if (row['operations'] > 12 * (row['nodes'] + row['edges'] + row['pages']) + 32 * row['invocations']
                or row['bytes_read'] > 3 * row['unique_body_bytes'] + row['transport_bytes'] + 262144 * row['invocations']
                or row['max_index_rebuilds_per_invocation'] > 1):
            row['execution_status'] = 'fail'
            raise ValueError('Accepted operation, I/O or index-rebuild bound exceeded')
        row['execution_status'] = 'pass'
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        row['errors'].append(f'{type(exc).__name__}: {exc}')
    _save(evidence_root/(sample_id + '.json'), row)
    return row


def benchmark(root):
    root = external_path(root)
    samples = []
    for nodes in (64, 256, 1024):
        seed = None
        for mode in ('cold', 'warm'):
            for repetition in range(5):
                workspace = root/f'primary-{nodes}-{mode}-{repetition}'
                result = sample(workspace, nodes=nodes, mode=mode, repetition=repetition, warm_objects=seed)
                samples.append(result)
                if mode == 'cold' and result['execution_status'] == 'pass':
                    seed = workspace/'.taskplane/context-v1/objects'
    for shape in ('shared', 'high-edge', 'multipage'):
        samples.append(sample(root/shape, nodes=16, mode='cold', repetition=0, shape=shape))
    timing = []
    for mode in ('cold', 'warm'):
        previous = None
        for nodes in (64, 256, 1024):
            rows = [r for r in samples if r.get('nodes') == nodes and r['mode'] == mode
                    and r['shape'] == 'primary' and r['execution_status'] == 'pass']
            if len(rows) != 5:
                continue
            adjusted = [r['elapsed_seconds'] - r['startup_seconds'] for r in rows]
            p95 = max(adjusted)
            timing.append({'nodes': nodes, 'mode': mode, 'p95_seconds': max(r['elapsed_seconds'] for r in rows),
                           'p50_seconds': statistics.median(r['elapsed_seconds'] for r in rows),
                           'p95_adjusted_seconds': p95, 'timing_conclusive': all(r['timing_conclusive'] for r in rows),
                           'adjusted_growth': None if previous is None else p95 / previous})
            previous = p95
    growth_pass = len(timing) == 6 and all(r['adjusted_growth'] is None or r['adjusted_growth'] <= 6 for r in timing)
    return {'schema': 'taskplane.context-benchmark/v3', 'samples': samples, 'timing': timing,
            'execution_status': 'pass' if growth_pass and all(r['execution_status'] == 'pass' for r in samples) else 'unknown',
            'native_model_tokens': 'not measured', 'model_attention': 'unobservable',
            'authority': 'synthetic-disposable-fixture', 'evidence_root': str(root)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--operation', choices=('noop', 'build', 'drain', 'validate', 'controller-fixture'))
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--sample-id', default='benchmark')
    parser.add_argument('--nodes', type=int, default=64)
    parser.add_argument('--shape', choices=('primary', 'shared', 'high-edge', 'multipage'), default='primary')
    args = parser.parse_args()
    root = (external_path(args.workspace) if args.workspace else
            Path(tempfile.mkdtemp(prefix='taskplane-context-benchmark-', dir=external_path(tempfile.gettempdir()))))
    # Preserve an enclosing isolated environment for nested fixture commands.
    isolation = os.environ.get('TASKPLANE_CONTEXT_ISOLATION') or str(root/'isolated-hosts')
    environment = isolated_environment(Path(isolation))
    os.environ.clear()
    os.environ.update(environment)
    root.mkdir(parents=True, exist_ok=True)
    if args.operation:
        if args.workspace is None:
            parser.error('--workspace is required')
        value = (prepare_controller_fixture(root, args.nodes, args.shape) if args.operation == 'controller-fixture'
                 else execute(args.operation, root, args.sample_id, args.nodes, args.shape))
    else:
        value = benchmark(root)
        _save(root/'benchmark.json', value)
    print(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False))
    return 0 if value.get('execution_status', 'pass') == 'pass' else 2


if __name__ == '__main__':
    raise SystemExit(main())
