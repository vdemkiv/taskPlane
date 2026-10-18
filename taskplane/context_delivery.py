"""Transport helpers for clients of the bounded context CLI.

Adapters must return every response body to their consumer before requesting the
next one. These helpers never consume context, approve work or hide tool output.
"""
from __future__ import annotations

import json
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar, Token
from functools import wraps
import hashlib
import os
from pathlib import Path
import sys
import time
from typing import Any, Callable, Iterator, ParamSpec, TypeVar


BYTE_CLASSES = ('payload', 'transport', 'index', 'ledger', 'source_fingerprints')
# Count actual primitive calls/items, never a single marker for an entire startup.
OPERATION_UNITS = {
    'read': 'one actual read call', 'stat': 'one metadata query',
    'hash': 'one digest computation', 'parse': 'one parser invocation',
    'write': 'one body publication', 'index_lookup': 'one index selection',
    'reconstruction': 'one reconstructed node', 'selection': 'one examined item',
    'sort': 'one item admitted to a sort',
    'traversal_node': 'one graph node visit', 'traversal_edge': 'one graph edge visit',
    'checkpoint_write': 'one checkpoint publication', 'directory_flush': 'one directory flush',
    'ledger_replay': 'one replayed page record',
    'source_read': 'one source read call', 'source_stat': 'one source metadata query',
    'source_hash': 'one source digest computation', 'source_parse': 'one source parser invocation',
    'source_write': 'one source publication',
}
CLI_COMPONENTS = ('entrypoint', 'controller-selection', 'controller-storage',
                  'source-audit', 'context-construction', 'context-delivery',
                  'completion-reconciliation', 'workspace-binding',
                  'native-observations', 'dependency-graph', 'atomic-persistence')
_CLI_METRICS: ContextVar[Any] = ContextVar('context_cli_metrics', default=None)
P = ParamSpec('P')
R = TypeVar('R')


def cli_metrics() -> Any:
    """Optional observation only. It cannot select a controller or authorize work."""
    return _CLI_METRICS.get()


def observed(component: str, *, stage: str | None = None) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Register an instrumented boundary; measure executions, including failures.

    Registration says the seam exists, not that it executed. The recorded stages
    and primitive counters distinguish those cases. No native authority is added.
    """
    sink = cli_metrics()
    if sink is not None:
        sink.covered(component)
    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        @wraps(function)
        def call(*args: P.args, **kwargs: P.kwargs) -> R:
            current = cli_metrics()
            if current is None:
                return function(*args, **kwargs)
            current.covered(component)
            with current.stage(stage or component):
                return function(*args, **kwargs)
        return call
    return decorate


def operation(name: str, function: Callable[..., R], *args: Any, category: str | None = None, **kwargs: Any) -> R:
    """Count an actual primitive invocation and its returned source bytes."""
    from .context import count, bytes_read
    count(name)
    value = function(*args, **kwargs)
    if category is not None:
        if not isinstance(value, (str, bytes)):
            raise TypeError('Measured byte reads must return text or bytes')
        raw = value.encode('utf-8') if isinstance(value, str) else value
        bytes_read(category, len(raw))
    return value


def peak_resident_bytes() -> int | None:
    """Actual process peak RSS; unsupported measurement stays unknown."""
    try:
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            class ProcessMemory(ctypes.Structure):
                _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                    *[(name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
                      'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                      'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]]
            loader = getattr(ctypes, 'WinDLL')
            kernel = loader('kernel32', use_last_error=True)
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            query = loader('psapi', use_last_error=True).GetProcessMemoryInfo
            query.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemory), wintypes.DWORD]
            query.restype = wintypes.BOOL
            memory = ProcessMemory()
            setattr(memory, 'cb', ctypes.sizeof(memory))
            if not query(kernel.GetCurrentProcess(), ctypes.byref(memory), ctypes.sizeof(memory)):
                return None
            value = int(getattr(memory, 'PeakWorkingSetSize'))
        else:
            import resource
            raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            value = int(raw if sys.platform == 'darwin' else raw * 1024)
        return value if value > 0 else None
    except (ImportError, AttributeError, OSError, ValueError):
        return None


class ContextCliMetrics:
    """Early-entry measurement sink for the production tp.py integration.

    tp.py must install this before importing flow/controller modules. Producers
    record actual operations at their I/O/algorithm boundaries and attest which
    components are fully instrumented. Missing wiring remains unknown. Wall time
    around the real public subprocess additionally includes interpreter startup.
    """

    def __init__(self) -> None:
        self.units: Counter[str] = Counter()
        self.byte_classes: Counter[str] = Counter(dict.fromkeys(BYTE_CLASSES, 0))
        self.index_rebuilds = 0
        self.components: set[str] = set()
        self.stages: list[dict[str, Any]] = []
        self.started = time.perf_counter()
        self._token: Token[Any] | None = None

    def count(self, name: str, amount: int = 1) -> None:
        if name not in OPERATION_UNITS or type(amount) is not int or amount < 0:
            raise ValueError('Undeclared or invalid context measurement unit: ' + str(name))
        self.units[name] += amount

    def read(self, category: str, amount: int) -> None:
        if category not in BYTE_CLASSES or type(amount) is not int or amount < 0:
            raise ValueError('Invalid context byte class/count')
        self.byte_classes[category] += amount

    def reclassify(self, source: str, target: str, amount: int) -> None:
        if (source not in BYTE_CLASSES or target not in BYTE_CLASSES
                or type(amount) is not int or not 0 <= amount <= self.byte_classes[source]):
            raise ValueError('Invalid verified byte reclassification')
        self.byte_classes[source] -= amount
        self.byte_classes[target] += amount

    def covered(self, component: str) -> None:
        if component not in CLI_COMPONENTS:
            raise ValueError('Unknown context instrumentation component')
        self.components.add(component)

    @contextmanager
    def stage(self, name: str) -> Iterator[ContextCliMetrics]:
        started = time.perf_counter()
        before = sum(self.units.values())
        try:
            yield self
        finally:
            self.stages.append({'name': name, 'elapsed_seconds': time.perf_counter() - started,
                                'operations': sum(self.units.values()) - before})

    def install(self) -> ContextCliMetrics:
        if cli_metrics() is not None:
            raise ValueError('Context CLI measurement already installed')
        self._token = _CLI_METRICS.set(self)
        return self

    def report(self) -> dict[str, Any]:
        return {'operations': sum(self.units.values()), 'operation_classes': dict(self.units),
                'byte_classes': dict(self.byte_classes), 'bytes_read': sum(self.byte_classes.values()),
                'index_rebuilds': self.index_rebuilds}

    def read_json(self, path: Path, category: str = 'source_fingerprints') -> Any:
        """Instrument the actual read/parse; callers still enforce their path policy."""
        raw = path.read_bytes()
        self.count('read')
        self.read(category, len(raw))
        self.count('parse')
        return json.loads(raw)

    def finish(self, target: Path, *, argv: list[str], cwd: Path,
               stdout: bytes, exit_code: int) -> dict[str, Any]:
        """Publish an external sidecar after emission, including refused commands.

        The caller supplies exact emitted bytes, including the terminal newline.
        Sidecar publication is measurement overhead, outside product I/O totals.
        """
        rss = peak_resident_bytes()
        self.read('transport', len(stdout))
        missing = sorted(set(CLI_COMPONENTS) - self.components)
        startup = [s for s in self.stages if s['name'] == 'startup']
        complete = not missing and bool(startup) and sum(s['operations'] for s in startup) > 0
        result = {**self.report(), 'schema': 'taskplane.context-cli-metrics/v1',
                  'argv': list(argv), 'cwd': str(Path(cwd).resolve()), 'exit_code': exit_code,
                  'stdout_sha256': hashlib.sha256(stdout).hexdigest(),
                  'elapsed_seconds': time.perf_counter() - self.started,
                  'clock_resolution_seconds': time.get_clock_info('perf_counter').resolution,
                  'peak_rss_bytes': rss, 'stages': self.stages,
                  'components': sorted(self.components), 'missing_components': missing,
                  'coverage': 'complete' if complete else 'unknown',
                  'unit_definitions': OPERATION_UNITS}
        target = Path(target).resolve()
        isolation = Path(os.environ.get('TASKPLANE_CONTEXT_ISOLATION', '')).resolve()
        candidate = Path(__file__).resolve().parents[1]
        if (not os.environ.get('TASKPLANE_CONTEXT_ISOLATION') or not target.is_relative_to(isolation)
                or target.is_relative_to(candidate)):
            raise ValueError('Measurement sidecar must be inside external benchmark isolation')
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, sort_keys=True, allow_nan=False)
        if self._token is not None:
            _CLI_METRICS.reset(self._token)
            self._token = None
        return result


def begin_cli_measurement() -> ContextCliMetrics | None:
    """Earliest production entry point calls this before other candidate imports.

    No flag changes controller selection, source audits, receipts or decisions.
    The target is supplied only by the isolated measurement harness; normal CLI
    execution neither creates files nor installs a measurement sink.
    """
    target = os.environ.get('TASKPLANE_CONTEXT_METRICS')
    if target is None:
        return None
    isolation = os.environ.get('TASKPLANE_CONTEXT_ISOLATION')
    candidate = Path(__file__).resolve().parents[1]
    path = Path(target).resolve()
    if (not isolation or not path.is_relative_to(Path(isolation).resolve())
            or path.is_relative_to(candidate)):
        raise ValueError('Context measurement requires an external isolated sidecar')
    return ContextCliMetrics().install()


class CommandOutput:
    """Accumulate subprocess/tool chunks and parse only a terminal response.

    A running handle is not JSON completion, including when a chunk happens to
    contain valid JSON. Keep this object across host waits; use a long host wait
    within the user's progress-update interval rather than short model polls.
    """

    def __init__(self, limit_bytes: int = 65536):
        if type(limit_bytes) is not int or not 1024 <= limit_bytes <= 1048576:
            raise ValueError("Invalid combined output budget")
        self.limit_bytes = limit_bytes
        self.parts: list[str] = []
        self.size = 0
        self.done = False

    def feed(self, output: str, *, exit_code: int | None = None) -> dict[str, Any] | None:
        if self.done:
            raise ValueError("Command already terminated")
        self.size += len(output.encode("utf-8"))
        if self.size > self.limit_bytes:
            self.done = True
            raise ValueError("Combined command output exceeds budget; do not discard and reread context")
        self.parts.append(output)
        if exit_code is None:
            return None
        self.done = True
        if exit_code != 0:
            raise ValueError(f"Context command failed with exit code {exit_code}")
        result = json.loads("".join(self.parts))
        if not isinstance(result, dict):
            raise ValueError("Context command must return an object")
        return result
