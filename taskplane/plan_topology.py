"""Plan topology, atomic ready-set admission, and execution-DAG metrics.

The pair map produced here is the scheduler input, rather than explanatory
metadata.  In particular, a declared test file is a build artifact: when it
does not exist in the admitted source and another task owns that path, the
producer is an implicit predecessor of the consumer even if edit scopes are
otherwise disjoint.
"""

from __future__ import annotations

from copy import deepcopy
import contextlib
import fnmatch
import json
import math
import os
from pathlib import Path
import re
import shlex
import threading
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

try:
    from .delivery_ports import (
        Clock,
        DeliveryPortError,
        EventWaiter,
        IRREVERSIBLE_TOOLS,
        TaskDispatchCapability,
        TaskDispatchCapabilityFactory,
        canonical_json,
        content_fingerprint,
    )
except ImportError:  # pragma: no cover - direct module loading
    from delivery_ports import (  # type: ignore
        Clock,
        DeliveryPortError,
        EventWaiter,
        IRREVERSIBLE_TOOLS,
        TaskDispatchCapability,
        TaskDispatchCapabilityFactory,
        canonical_json,
        content_fingerprint,
    )


TOPOLOGY_SCHEMA = "taskplane.plan-topology/v1"
ADMISSION_SCHEMA = "taskplane.dispatch-admission/v1"
EXECUTION_DAG_SCHEMA = "taskplane.execution-dag/v1"
EVENT_KINDS = frozenset({
    "progress", "complete", "attention", "failed", "cancelled",
    "partial-host",
})
TERMINAL_EVENT_KINDS = frozenset({
    "complete", "attention", "failed", "cancelled", "partial-host",
})
COMPLETE_STATUSES = frozenset({"complete", "done", "pass", "passed"})
TERMINAL_STATUSES = COMPLETE_STATUSES | frozenset({
    "attention", "failed", "cancelled", "skipped",
})
DEFAULT_EVENT_QUEUE_CAP = 256
MAX_EVENT_BYTES = 64 * 1024
HOST_CAPABILITY_SCHEMA = "taskplane.scheduler-host-capability/v1"
EXECUTION_DAG_HEAD_SCHEMA = "taskplane.execution-dag-head/v1"


class PlanTopologyError(RuntimeError):
    """The Plan or a scheduler transition is structurally unsafe."""


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlanTopologyError(f"{label} must be numeric")
    try:
        normalized = float(value)
    except (OverflowError, ValueError) as exc:
        raise PlanTopologyError(f"{label} must be finite") from exc
    if not math.isfinite(normalized):
        raise PlanTopologyError(f"{label} must be finite")
    return normalized


_ADMISSION_LOCK = threading.RLock()
_DAG_STORE_LOCK = threading.RLock()


class ClosedTaskDispatchCapabilityFactory:
    """Production default-deny capability producer.

    Unlike the recorded hermetic port, this factory retains no capability
    inventory.  Its only output is the closed, exact-bound worker authority.
    """

    _LIST_FIELDS = (
        "allowed_tools", "read_paths", "write_paths", "allowed_git_refs",
        "allowed_network_endpoints", "credential_handles",
    )
    _REQUIRED = {
        "run_id", "source_sha", "design_fingerprint", "plan_fingerprint",
        "task_id", "stage", "reservation_fingerprint",
        "predecessor_fingerprint",
    }

    @staticmethod
    def _digest(value: object, label: str, *, sha: bool = False) -> str:
        text = str(value or "")
        lengths = {40, 64} if sha else {64}
        if len(text) not in lengths or not re.fullmatch(r"[0-9a-f]+", text):
            raise DeliveryPortError(f"invalid dispatch capability {label}")
        return text

    def create(self, **bindings: Any) -> TaskDispatchCapability:
        missing = self._REQUIRED.difference(bindings)
        if missing:
            raise DeliveryPortError(
                f"missing dispatch capability bindings: {sorted(missing)}")
        unknown = set(bindings).difference(
            self._REQUIRED | set(self._LIST_FIELDS))
        if unknown:
            raise DeliveryPortError(
                f"unknown dispatch capability bindings: {sorted(unknown)}")
        projection = {
            "run_id": str(bindings["run_id"] or "").strip(),
            "source_sha": self._digest(
                bindings["source_sha"], "source_sha", sha=True),
            "design_fingerprint": self._digest(
                bindings["design_fingerprint"], "design_fingerprint"),
            "plan_fingerprint": self._digest(
                bindings["plan_fingerprint"], "plan_fingerprint"),
            "task_id": str(bindings["task_id"] or "").strip(),
            "stage": str(bindings["stage"] or "").strip(),
            "reservation_fingerprint": self._digest(
                bindings["reservation_fingerprint"],
                "reservation_fingerprint"),
            "predecessor_fingerprint": bindings["predecessor_fingerprint"],
        }
        if not projection["run_id"] or not projection["task_id"] or \
                not projection["stage"]:
            raise DeliveryPortError(
                "dispatch capability identity cannot be empty")
        predecessor = projection["predecessor_fingerprint"]
        if predecessor is not None:
            projection["predecessor_fingerprint"] = self._digest(
                predecessor, "predecessor_fingerprint")
        for field in self._LIST_FIELDS:
            raw = bindings.get(field, ())
            if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
                raise DeliveryPortError(
                    f"dispatch capability {field} must be a sequence")
            values = tuple(sorted({str(value) for value in raw
                                   if str(value)}))
            if "*" in values:
                raise DeliveryPortError(
                    f"wildcard authority is forbidden: {field}")
            projection[field] = values
        forbidden = IRREVERSIBLE_TOOLS.intersection(
            projection["allowed_tools"])
        if forbidden:
            raise DeliveryPortError(
                f"workers cannot receive irreversible tools: "
                f"{sorted(forbidden)}")
        if any("release" in handle.lower()
               for handle in projection["credential_handles"]):
            raise DeliveryPortError(
                "workers cannot receive release credentials")
        projection.update(
            schema="taskplane.task-dispatch-capability/v1",
            release_credentials_available=False,
            irreversible_actions_allowed=False,
            cryptographic_authenticity_claimed=False,
        )
        projection["capability_id"] = content_fingerprint(projection)
        return TaskDispatchCapability(projection)


def validate_scheduler_host_capability(
        receipt: object, *, run_id: str, source_sha: str,
        plan_fingerprint: str, clock: Clock) -> dict[str, int]:
    """Validate the sole production authority for scheduler capacity."""
    fields = {
        "schema", "run_id", "source_sha", "plan_fingerprint",
        "configured_host_concurrency", "max_in_flight", "issued_at",
        "expires_at", "cryptographic_authenticity_claimed", "fingerprint",
    }
    if not isinstance(receipt, Mapping) or set(receipt) != fields or \
            receipt.get("schema") != HOST_CAPABILITY_SCHEMA:
        raise PlanTopologyError(
            "scheduler host capability receipt is missing or malformed")
    material = {key: receipt[key] for key in fields if key != "fingerprint"}
    if receipt.get("fingerprint") != content_fingerprint(material):
        raise PlanTopologyError(
            "scheduler host capability receipt fingerprint is invalid")
    if receipt.get("cryptographic_authenticity_claimed") is not False:
        raise PlanTopologyError(
            "scheduler host capability cannot claim cryptographic authenticity")
    if (receipt.get("run_id"), receipt.get("source_sha"),
            receipt.get("plan_fingerprint")) != (
                run_id, source_sha, plan_fingerprint):
        raise PlanTopologyError(
            "scheduler host capability receipt has cross-run bindings")
    concurrency = receipt.get("configured_host_concurrency")
    max_in_flight = receipt.get("max_in_flight")
    issued_at = receipt.get("issued_at")
    expires_at = receipt.get("expires_at")
    if any(isinstance(value, bool) for value in (
            concurrency, max_in_flight, issued_at, expires_at)) or \
            not isinstance(concurrency, int) or \
            not isinstance(max_in_flight, int) or \
            not isinstance(issued_at, (int, float)) or \
            not isinstance(expires_at, (int, float)) or \
            not math.isfinite(issued_at) or \
            not math.isfinite(expires_at) or \
            concurrency < 0 or max_in_flight < 0 or \
            max_in_flight > concurrency or expires_at <= issued_at:
        raise PlanTopologyError(
            "scheduler host capability receipt is malformed")
    now = float(clock.wall_time())
    if now < float(issued_at) or now >= float(expires_at):
        raise PlanTopologyError(
            "scheduler host capability receipt is stale")
    return {
        "configured_host_concurrency": concurrency,
        "max_in_flight": max_in_flight,
    }


class ExecutionDagRevisionStore:
    """Immutable execution-DAG revisions below one exact managed run root."""

    _REVISION = re.compile(r"(?P<ordinal>[0-9]+)-(?P<fingerprint>[0-9a-f]{64})[.]json\Z")

    def __init__(self, managed_run_root: str | os.PathLike[str]) -> None:
        supplied = Path(managed_run_root)
        if supplied.is_symlink():
            raise PlanTopologyError("managed run root cannot be a symlink")
        supplied.mkdir(parents=True, exist_ok=True)
        self.managed_run_root = supplied.resolve(strict=True)
        self.root = self.managed_run_root / "execution-dag"
        self.revisions = self.root / "revisions"
        self.revisions.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or self.revisions.is_symlink():
            raise PlanTopologyError("execution DAG store cannot be a symlink")
        self.head_path = self.root / "HEAD"

    @staticmethod
    def _validated_dag(dag: object) -> tuple[dict[str, Any], bytes]:
        if not isinstance(dag, Mapping) or \
                dag.get("schema") != EXECUTION_DAG_SCHEMA:
            raise PlanTopologyError("execution DAG revision is invalid")
        value = deepcopy(dict(dag))
        fingerprint = str(value.pop("fingerprint", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint) or \
                content_fingerprint(value) != fingerprint:
            raise PlanTopologyError("execution DAG fingerprint is invalid")
        value["fingerprint"] = fingerprint
        return value, canonical_json(value)

    def _revision_rows(self) -> dict[int, list[Path]]:
        rows: dict[int, list[Path]] = {}
        for path in self.revisions.iterdir():
            match = self._REVISION.fullmatch(path.name)
            if match:
                rows.setdefault(int(match.group("ordinal")), []).append(path)
        if any(len(paths) != 1 for paths in rows.values()):
            raise PlanTopologyError("execution DAG revision fork detected")
        return rows

    def _read_head_unlocked(self) -> dict[str, Any] | None:
        rows = self._revision_rows()
        if not self.head_path.exists():
            return None
        try:
            head = json.loads(self.head_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PlanTopologyError(
                f"execution DAG head is malformed: {exc}") from exc
        fields = {"schema", "ordinal", "fingerprint", "revision_path",
                  "predecessor_fingerprint"}
        if not isinstance(head, dict) or set(head) != fields or \
                head.get("schema") != EXECUTION_DAG_HEAD_SCHEMA or \
                isinstance(head.get("ordinal"), bool) or \
                not isinstance(head.get("ordinal"), int) or \
                int(head["ordinal"]) < 0:
            raise PlanTopologyError("execution DAG head is malformed")
        ordinal = int(head["ordinal"])
        expected_name = f"{ordinal}-{head.get('fingerprint')}.json"
        expected_relative = f"execution-dag/revisions/{expected_name}"
        if head.get("revision_path") != expected_relative or \
                ordinal not in rows or rows[ordinal][0].name != expected_name:
            raise PlanTopologyError(
                "execution DAG head does not name its immutable revision")
        prior_fingerprint = None
        for value in range(ordinal + 1):
            if value not in rows:
                raise PlanTopologyError("execution DAG revision gap detected")
            match = self._REVISION.fullmatch(rows[value][0].name)
            try:
                revision_dag, revision_bytes = self._validated_dag(json.loads(
                    rows[value][0].read_text(encoding="utf-8")))
            except (OSError, ValueError) as exc:
                raise PlanTopologyError(
                    f"execution DAG immutable revision is malformed: {exc}") \
                    from exc
            if match is None or match.group("fingerprint") != \
                    revision_dag["fingerprint"] or \
                    revision_bytes != rows[value][0].read_bytes():
                raise PlanTopologyError(
                    "execution DAG immutable revision bytes changed")
            if value == ordinal and head.get(
                    "predecessor_fingerprint") != prior_fingerprint:
                raise PlanTopologyError(
                    "execution DAG head lineage is invalid")
            prior_fingerprint = revision_dag["fingerprint"]
        dag, encoded = self._validated_dag(json.loads(
            rows[ordinal][0].read_text(encoding="utf-8")))
        if encoded != rows[ordinal][0].read_bytes() or \
                dag["fingerprint"] != head["fingerprint"]:
            raise PlanTopologyError(
                "execution DAG immutable revision bytes changed")
        return head

    def read_head(self) -> dict[str, Any]:
        with _DAG_STORE_LOCK:
            head = self._read_head_unlocked()
            if head is None:
                raise PlanTopologyError("execution DAG head is unavailable")
            return dict(head)

    def read_dag(self) -> dict[str, Any]:
        """Read and revalidate the exact immutable revision named by HEAD."""
        with _DAG_STORE_LOCK:
            head = self._read_head_unlocked()
            if head is None:
                raise PlanTopologyError("execution DAG head is unavailable")
            path = self.managed_run_root / str(head["revision_path"])
            dag, encoded = self._validated_dag(json.loads(
                path.read_text(encoding="utf-8")))
            if encoded != path.read_bytes():
                raise PlanTopologyError(
                    "execution DAG immutable revision bytes changed")
            return dag

    @staticmethod
    def _write_once(path: Path, encoded: bytes) -> None:
        try:
            descriptor = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if path.read_bytes() != encoded:
                raise PlanTopologyError(
                    "execution DAG immutable revision collision")
            return
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)

    def _write_head(self, head: Mapping[str, Any]) -> None:
        temporary = self.head_path.with_name(
            f"HEAD.tmp-{os.getpid()}-{threading.get_ident()}")
        encoded = canonical_json(dict(head))
        with temporary.open("wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.head_path)
        directory = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def persist(self, dag: object, *, expected_head: str | None) -> dict[str, Any]:
        value, encoded = self._validated_dag(dag)
        fingerprint = value["fingerprint"]
        with _DAG_STORE_LOCK:
            current = self._read_head_unlocked()
            if current is not None and current["fingerprint"] == fingerprint:
                return dict(current)
            actual = current["fingerprint"] if current is not None else None
            if actual != expected_head:
                raise PlanTopologyError("execution DAG head CAS mismatch")
            ordinal = int(current["ordinal"]) + 1 if current else 0
            rows = self._revision_rows()
            existing = rows.get(ordinal, [])
            revision = self.revisions / f"{ordinal}-{fingerprint}.json"
            if existing and existing[0] != revision:
                raise PlanTopologyError("execution DAG revision fork detected")
            self._write_once(revision, encoded)
            # Re-read under the same namespace lock immediately before the
            # atomic replace.  An exact orphan is crash recovery; a different
            # head is a CAS conflict and can never be overwritten.
            observed = self._read_head_unlocked()
            observed_fingerprint = (observed["fingerprint"]
                                    if observed is not None else None)
            if observed_fingerprint not in {expected_head, fingerprint}:
                raise PlanTopologyError("execution DAG head CAS mismatch")
            predecessor = (current["fingerprint"] if current else None)
            head = {
                "schema": EXECUTION_DAG_HEAD_SCHEMA,
                "ordinal": ordinal,
                "fingerprint": fingerprint,
                "revision_path":
                    f"execution-dag/revisions/{revision.name}",
                "predecessor_fingerprint": predecessor,
            }
            self._write_head(head)
            return dict(head)


def _path(value: object) -> str:
    return str(value or "").strip().replace("\\", "/").removeprefix("./")


def _task_rows(tasks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = [dict(task) for task in tasks]
    ids = [str(task.get("id") or "") for task in rows]
    if any(not task_id for task_id in ids) or len(set(ids)) != len(ids):
        raise PlanTopologyError("task ids must be non-empty and unique")
    known = set(ids)
    for row, task_id in zip(rows, ids):
        row["id"] = task_id
        deps = [str(dep) for dep in row.get("deps") or ()]
        unknown = set(deps).difference(known)
        if unknown:
            raise PlanTopologyError(
                f"task {task_id} has unknown dependencies: {sorted(unknown)}"
            )
        if task_id in deps:
            raise PlanTopologyError(f"task {task_id} depends on itself")
        row["deps"] = sorted(set(deps))
        row["scope"] = sorted({_path(item) for item in row.get("scope") or () if _path(item)})
    return rows


def _scope_matches(scope: str, artifact: str) -> bool:
    if not scope or not artifact:
        return False
    if scope == artifact:
        return True
    if any(mark in scope for mark in "*?["):
        return fnmatch.fnmatchcase(artifact, scope)
    return artifact.startswith(scope.rstrip("/") + "/")


def _scope_overlap(left: Sequence[str], right: Sequence[str]) -> str | None:
    candidates: list[str] = []
    for left_path in left:
        for right_path in right:
            if _scope_matches(left_path, right_path):
                candidates.append(right_path)
            elif _scope_matches(right_path, left_path):
                candidates.append(left_path)
    return min(candidates) if candidates else None


def _declared_test_files(command: object) -> tuple[str, ...]:
    if not isinstance(command, str) or not command.strip():
        return ()
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise PlanTopologyError(f"malformed task test command: {exc}") from exc
    files = set()
    for token in tokens:
        candidate = _path(token.split("::", 1)[0])
        if candidate.endswith(".py") and "/" in candidate:
            files.add(candidate)
    return tuple(sorted(files))


def _repository_inventory(
    test_files: Iterable[str], repository_files: Iterable[str] | None,
) -> set[str]:
    if repository_files is not None:
        return {_path(item) for item in repository_files}
    return {item for item in test_files if Path(item).is_file()}


def _descendants(dependencies: Mapping[str, Sequence[str]]) -> dict[str, set[str]]:
    descendants = {task_id: set() for task_id in dependencies}
    visited: set[str] = set()

    # First validate with a conventional dependency walk.  The second pass
    # below computes transitive descendants without depending on input order.
    def validate(task_id: str, stack: set[str]) -> None:
        if task_id in stack:
            raise PlanTopologyError("task dependency graph contains a cycle")
        if task_id in visited:
            return
        stack.add(task_id)
        for dependency in dependencies[task_id]:
            validate(dependency, stack)
        stack.remove(task_id)
        visited.add(task_id)

    visited.clear()
    for task_id in dependencies:
        validate(task_id, set())
    for task_id in dependencies:
        pending = list(dependencies[task_id])
        while pending:
            dependency = pending.pop()
            descendants[dependency].add(task_id)
            pending.extend(dependencies[dependency])
    return descendants


def classify_plan(
    tasks: Sequence[Mapping[str, Any]], *,
    repository_files: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Return an exhaustive pair map and dependency closure for ``tasks``.

    Missing test artifacts are classified against the admitted repository
    inventory, not the executor's later working tree.  A uniquely scoped
    producer becomes an implicit predecessor.  Ambiguous ownership fails
    closed; a missing unowned test is reported and keeps its consumer held.
    """
    rows = _task_rows(tasks)
    by_id = {row["id"]: row for row in rows}
    all_test_files = {
        path for row in rows for path in _declared_test_files(row.get("tests"))
    }
    present = _repository_inventory(all_test_files, repository_files)
    dependencies = {row["id"]: set(row["deps"]) for row in rows}
    test_edges: dict[tuple[str, str], str] = {}
    missing_by_consumer: dict[str, list[str]] = {row["id"]: [] for row in rows}

    for consumer in rows:
        for artifact in _declared_test_files(consumer.get("tests")):
            if artifact in present:
                continue
            owners = sorted(
                row["id"] for row in rows
                if row["id"] != consumer["id"]
                and any(_scope_matches(scope, artifact) for scope in row["scope"])
            )
            if len(owners) > 1:
                raise PlanTopologyError(
                    f"missing test artifact has ambiguous owners: {artifact}: {owners}"
                )
            if owners:
                owner = owners[0]
                dependencies[consumer["id"]].add(owner)
                test_edges[(owner, consumer["id"])] = artifact
            elif not any(_scope_matches(scope, artifact) for scope in consumer["scope"]):
                missing_by_consumer[consumer["id"]].append(artifact)

    effective = {task_id: sorted(values) for task_id, values in dependencies.items()}
    descendants = _descendants(effective)
    pairs: list[dict[str, Any]] = []
    ids = sorted(by_id)
    for index, left_id in enumerate(ids):
        for right_id in ids[index + 1:]:
            shared_owner: str | None = None
            artifact = test_edges.get((left_id, right_id)) or test_edges.get((right_id, left_id))
            if artifact:
                shared_owner = f"test-artifact:{artifact}"
            elif right_id in descendants[left_id]:
                shared_owner = f"dependency:{left_id}"
            elif left_id in descendants[right_id]:
                shared_owner = f"dependency:{right_id}"
            else:
                overlap = _scope_overlap(by_id[left_id]["scope"], by_id[right_id]["scope"])
                if overlap:
                    shared_owner = f"scope:{overlap}"
            pairs.append({
                "left": left_id,
                "right": right_id,
                "disposition": "serialized" if shared_owner else "parallel",
                "shared_owner": shared_owner,
            })

    material = {
        "schema": TOPOLOGY_SCHEMA,
        "task_ids": ids,
        "pairs": pairs,
        "effective_dependencies": effective,
        "missing_test_assets": {
            task_id: sorted(paths) for task_id, paths in missing_by_consumer.items() if paths
        },
        "test_artifact_edges": [
            {"producer": producer, "consumer": consumer, "artifact": artifact}
            for (producer, consumer), artifact in sorted(test_edges.items())
        ],
    }
    material["fingerprint"] = content_fingerprint(material)
    return material


def _initial_execution_dag(tasks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = _task_rows(tasks)
    nodes = [
        {"id": f"g0:{row['id']}", "task_id": row["id"], "generation": 0}
        for row in sorted(rows, key=lambda row: row["id"])
    ]
    edges = [
        [f"g0:{dep}", f"g0:{row['id']}", "dependency"]
        for row in sorted(rows, key=lambda row: row["id"])
        for dep in row["deps"]
    ]
    material = {
        "schema": EXECUTION_DAG_SCHEMA,
        "generations": [{"generation": 0, "at": None, "task_ids": sorted(row["id"] for row in rows)}],
        "nodes": nodes,
        "edges": edges,
    }
    material["fingerprint"] = content_fingerprint(material)
    return material


def new_scheduler_state(
    tasks: Sequence[Mapping[str, Any]], *, run_id: str, source_sha: str,
    design_fingerprint: str, plan_fingerprint: str, stage: str,
    repository_files: Iterable[str] | None = None,
    statuses: Mapping[str, str] | None = None, sessions_admitted: int = 0,
) -> dict[str, Any]:
    rows = _task_rows(tasks)
    topology = classify_plan(rows, repository_files=repository_files)
    supplied_statuses = dict(statuses or {})
    unknown = set(supplied_statuses).difference(row["id"] for row in rows)
    if unknown:
        raise PlanTopologyError(f"statuses name unknown tasks: {sorted(unknown)}")
    state = {
        "schema": "taskplane.scheduler-state/v1",
        "run_id": str(run_id),
        "source_sha": str(source_sha),
        "design_fingerprint": str(design_fingerprint),
        "plan_fingerprint": str(plan_fingerprint),
        "stage": str(stage),
        "tasks": rows,
        "repository_files": sorted({_path(item) for item in repository_files or ()}),
        "topology": topology,
        "statuses": {
            row["id"]: supplied_statuses.get(row["id"], "ready") for row in rows
        },
        "in_flight": {},
        "reservations": [],
        "sessions_admitted": int(sessions_admitted),
        "revision": 0,
        "events": [],
        "event_queue_cap": DEFAULT_EVENT_QUEUE_CAP,
        "task_times": {},
        "scheduler_caused_idle_seconds": 0,
        "execution_dag": _initial_execution_dag(rows),
        "evidence_head": None,
    }
    return state


def _pair_lookup(topology: Mapping[str, Any]) -> dict[frozenset[str], Mapping[str, Any]]:
    return {
        frozenset((str(row["left"]), str(row["right"]))): row
        for row in topology.get("pairs") or ()
    }


def _ready_projection(state: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    topology = state["topology"]
    statuses = state["statuses"]
    missing = topology.get("missing_test_assets") or {}
    ready: list[str] = []
    held: list[str] = []
    for task_id in topology["task_ids"]:
        status = statuses.get(task_id)
        if status in TERMINAL_STATUSES or status == "in_flight":
            continue
        dependencies = topology["effective_dependencies"][task_id]
        if not missing.get(task_id) and all(
            statuses.get(dependency) in COMPLETE_STATUSES for dependency in dependencies
        ):
            ready.append(task_id)
        else:
            held.append(task_id)
    return ready, held


def _maximal_disjoint(
    ready: Sequence[str], topology: Mapping[str, Any], capacity: int,
) -> list[str]:
    lookup = _pair_lookup(topology)
    limit = min(capacity, len(ready))
    best: tuple[str, ...] = ()

    def search(selected: tuple[str, ...], candidates: tuple[str, ...]) -> bool:
        nonlocal best
        if len(selected) > len(best) or (
            len(selected) == len(best) and selected < best
        ):
            best = selected
        if len(best) == limit:
            return True
        if len(selected) + len(candidates) <= len(best):
            return False
        for index, candidate in enumerate(candidates):
            compatible = tuple(
                member for member in candidates[index + 1:]
                if lookup[frozenset((candidate, member))]["disposition"]
                == "parallel"
            )
            if search((*selected, candidate), compatible):
                return True
        return False

    search((), tuple(sorted(ready)))
    return list(best)


def _empty_admission(status: str, *, held: Sequence[str], ready: Sequence[str], reason: str) -> dict[str, Any]:
    return {
        "schema": ADMISSION_SCHEMA,
        "status": status,
        "reason": reason,
        "reservation_fingerprint": None,
        "dispatch_set": None,
        "assignments": [],
        "overflow_ready": list(ready),
        "held": list(held),
    }


def _admit_ready_batch_locked(
    state: MutableMapping[str, Any], host_capability: Mapping[str, Any],
    budget: Mapping[str, Any], evidence_store: Any, clock: Clock, *,
    capability_factory: TaskDispatchCapabilityFactory,
) -> dict[str, Any]:
    ready, held = _ready_projection(state)
    if not ready:
        return _empty_admission("idle", held=held, ready=(), reason="no dependency-ready work")
    if "configured_host_concurrency" not in host_capability:
        return _empty_admission(
            "stop_for_human_scope_review", held=held, ready=ready,
            reason="host concurrency capability is absent",
        )
    host_concurrency = int(host_capability["configured_host_concurrency"])
    max_in_flight = int(budget.get("max_in_flight", host_concurrency))
    session_limit = int(budget.get("session_limit", 60))
    if host_concurrency < 0 or max_in_flight <= 0 or session_limit <= 0:
        raise PlanTopologyError("dispatch capacities must be positive (host may be zero)")
    sessions_remaining = session_limit - int(state.get("sessions_admitted") or 0)
    if sessions_remaining <= 0:
        return _empty_admission(
            "stop_for_human_scope_review", held=held, ready=ready,
            reason="session budget exhausted",
        )
    current_in_flight = len(state.get("in_flight") or {})
    capacity = min(
        host_concurrency - current_in_flight,
        max_in_flight - current_in_flight,
        sessions_remaining,
    )
    if capacity <= 0:
        return _empty_admission(
            "waiting_for_event", held=held, ready=ready,
            reason="in-flight capacity exhausted",
        )

    selected = _maximal_disjoint(ready, state["topology"], capacity)
    if not selected:
        return _empty_admission(
            "idle", held=held, ready=ready,
            reason="no pairwise-disjoint ready task",
        )
    revision = int(state.get("revision") or 0)
    reservation_material = {
        "schema": "taskplane.dispatch-reservation/v1",
        "run_id": state["run_id"],
        "source_sha": state["source_sha"],
        "design_fingerprint": state["design_fingerprint"],
        "plan_fingerprint": state["plan_fingerprint"],
        "stage": state["stage"],
        "scheduler_revision": revision,
        "topology_fingerprint": state["topology"]["fingerprint"],
        "members": selected,
    }
    reservation_fingerprint = content_fingerprint(reservation_material)
    task_by_id = {row["id"]: row for row in state["tasks"]}
    predecessor = (
        state["reservations"][-1]["reservation_fingerprint"]
        if state.get("reservations") else None
    )
    assignments = []
    for task_id in selected:
        task = task_by_id[task_id]
        scope = tuple(task.get("scope") or ())
        capability = capability_factory.create(
            run_id=state["run_id"],
            source_sha=state["source_sha"],
            design_fingerprint=state["design_fingerprint"],
            plan_fingerprint=state["plan_fingerprint"],
            task_id=task_id,
            stage=state["stage"],
            reservation_fingerprint=reservation_fingerprint,
            predecessor_fingerprint=predecessor,
            allowed_tools=tuple(task.get("allowed_tools") or ("read", "test")),
            read_paths=tuple(task.get("read_paths") or scope),
            write_paths=tuple(task.get("write_paths") or scope),
            allowed_git_refs=tuple(task.get("allowed_git_refs") or ()),
            allowed_network_endpoints=tuple(task.get("allowed_network_endpoints") or ()),
            credential_handles=tuple(task.get("credential_handles") or ()),
        )
        assignments.append({
            "task_id": task_id,
            "reservation_fingerprint": reservation_fingerprint,
            "capability": capability.projection,
            "assignment_mode": "direct",
            "event_contract": {
                "schema": "taskplane.worker-event-contract/v1",
                "wait_mode": "event",
                "timeout_seconds": 1800,
                "scheduled_polling": False,
                "required_for_long_worker": ["progress", "terminal"]
                if task.get("long_worker") else ["terminal"],
            },
        })
    dispatch_set = {
        "schema": "taskplane.direct-assignment-set/v1",
        "concurrent": True,
        "members": selected,
        "member_count": len(selected),
    }
    receipt_payload = {
        **reservation_material,
        "reservation_fingerprint": reservation_fingerprint,
        "dispatch_set": dispatch_set,
        "assignments": assignments,
        "reserved_at": float(clock.wall_time()),
    }
    evidence_fingerprint = None
    if evidence_store is not None:
        prepared = evidence_store.prepare(
            "telemetry", f"dispatch-{reservation_fingerprint}", receipt_payload,
            expected_head=state.get("evidence_head"),
        )
        committed = evidence_store.commit(prepared)
        evidence_receipt = json.loads(committed)
        evidence_fingerprint = evidence_receipt["fingerprint"]

    reservation = {
        **receipt_payload,
        "evidence_fingerprint": evidence_fingerprint,
    }
    for task_id in selected:
        state["statuses"][task_id] = "in_flight"
        state["in_flight"][task_id] = reservation_fingerprint
        state["task_times"].setdefault(task_id, {})["start"] = float(clock.wall_time())
    state["reservations"].append(reservation)
    state["sessions_admitted"] = int(state.get("sessions_admitted") or 0) + len(selected)
    state["revision"] = revision + 1
    if evidence_fingerprint:
        state["evidence_head"] = evidence_fingerprint
    state["scheduler_caused_idle_seconds"] = 0
    selected_set = set(selected)
    return {
        "schema": ADMISSION_SCHEMA,
        "status": "admitted",
        "reason": "atomic pairwise-disjoint ready-set reservation",
        "reservation_fingerprint": reservation_fingerprint,
        "dispatch_set": dispatch_set,
        "assignments": assignments,
        "overflow_ready": [task_id for task_id in ready if task_id not in selected_set],
        "held": held,
        "evidence_fingerprint": evidence_fingerprint,
    }


def admit_ready_batch(
    state: MutableMapping[str, Any], host_capability: Mapping[str, Any],
    budget: Mapping[str, Any], evidence_store: Any, clock: Clock, *,
    capability_factory: TaskDispatchCapabilityFactory,
) -> dict[str, Any]:
    """Atomically reserve and directly assign the maximal ready tranche."""
    with _ADMISSION_LOCK:
        return _admit_ready_batch_locked(
            state, host_capability, budget, evidence_store, clock,
            capability_factory=capability_factory,
        )


def _event_bytes(event: Mapping[str, Any]) -> bytes:
    try:
        return canonical_json(dict(event))
    except (TypeError, ValueError) as exc:
        raise PlanTopologyError(f"worker event is not canonical JSON: {exc}") from exc


def _contiguous_task_events(
    events: Sequence[Mapping[str, Any]], task_id: str,
) -> list[Mapping[str, Any]]:
    ordered = sorted(
        (row for row in events if str(row["task_id"]) == task_id),
        key=lambda row: (int(row["sequence"]), str(row["event_id"])),
    )
    contiguous: list[Mapping[str, Any]] = []
    expected = 1
    for row in ordered:
        if int(row["sequence"]) != expected:
            break
        contiguous.append(row)
        expected += 1
    return contiguous


def record_worker_event(
    state: MutableMapping[str, Any], event: Mapping[str, Any], *,
    host_capability: Mapping[str, Any] | None = None,
    budget: Mapping[str, Any] | None = None,
    evidence_store: Any = None,
    clock: Clock | None = None,
    capability_factory: TaskDispatchCapabilityFactory | None = None,
) -> dict[str, Any]:
    """Idempotently record one bounded event and admit on terminal wake."""
    with _ADMISSION_LOCK:
        raw = _event_bytes(event)
        if len(raw) > MAX_EVENT_BYTES:
            raise PlanTopologyError("worker event exceeds 64 KiB")
        required = {"event_id", "task_id", "sequence", "kind", "at"}
        missing = required.difference(event)
        if missing:
            raise PlanTopologyError(f"worker event missing fields: {sorted(missing)}")
        event_id = str(event["event_id"])
        task_id = str(event["task_id"])
        kind = str(event["kind"])
        if not event_id or task_id not in state["statuses"]:
            raise PlanTopologyError("worker event identity is invalid")
        if kind not in EVENT_KINDS:
            raise PlanTopologyError(f"unknown worker event kind: {kind}")
        if int(event["sequence"]) < 0:
            raise PlanTopologyError("worker event sequence cannot be negative")
        _finite_number(event["at"], "worker event at")
        task = next(row for row in state["tasks"] if row["id"] == task_id)
        existing = next(
            (row for row in state["events"] if row["event_id"] == event_id), None
        )
        normalized = dict(event)
        if existing is not None:
            if existing != normalized:
                raise PlanTopologyError("worker event id collision")
            return {
                "schema": "taskplane.worker-event-result/v1",
                "status": "duplicate",
                "terminal": state["statuses"].get(task_id) in TERMINAL_STATUSES,
            }
        existing_sequence = next((
            row for row in state["events"]
            if str(row["task_id"]) == task_id
            and int(row["sequence"]) == int(event["sequence"])
        ), None)
        if existing_sequence is not None:
            existing_semantics = {
                key: value for key, value in existing_sequence.items()
                if key != "event_id"
            }
            event_semantics = {
                key: value for key, value in normalized.items()
                if key != "event_id"
            }
            if existing_semantics != event_semantics:
                raise PlanTopologyError("worker event task sequence collision")
            return {
                "schema": "taskplane.worker-event-result/v1",
                "status": "duplicate",
                "terminal": state["statuses"].get(task_id) in TERMINAL_STATUSES,
            }
        if len(state["events"]) >= int(state.get("event_queue_cap") or DEFAULT_EVENT_QUEUE_CAP):
            raise PlanTopologyError("worker event queue cap reached")
        contiguous = _contiguous_task_events(
            [*state["events"], normalized], task_id,
        )
        terminal_event = next(
            (row for row in contiguous if row["kind"] in TERMINAL_EVENT_KINDS),
            None,
        )
        if task.get("long_worker") and terminal_event is not None and \
                terminal_event["kind"] == "complete" and not any(
                    row["kind"] == "progress"
                    for row in contiguous
                    if int(row["sequence"]) < int(terminal_event["sequence"])
                ):
            raise PlanTopologyError(
                "long worker terminal event requires prior progress event"
            )
        state["events"].append(normalized)
        state["events"].sort(key=lambda row: (str(row["task_id"]), int(row["sequence"]), str(row["event_id"])))
        admission = None
        terminal = terminal_event is not None
        if terminal and state["statuses"].get(task_id) not in TERMINAL_STATUSES:
            status = {
                "complete": "complete",
                "attention": "attention",
                "partial-host": "attention",
                "failed": "failed",
                "cancelled": "cancelled",
            }[str(terminal_event["kind"])]
            state["statuses"][task_id] = status
            state["in_flight"].pop(task_id, None)
            state["task_times"].setdefault(task_id, {})["terminal"] = float(
                terminal_event["at"]
            )
            if all(value is not None for value in (
                host_capability, budget, clock, capability_factory,
            )):
                admission = _admit_ready_batch_locked(
                    state, host_capability or {}, budget or {}, evidence_store,
                    clock, capability_factory=capability_factory,
                )
        return {
            "schema": "taskplane.worker-event-result/v1",
            "status": "recorded",
            "terminal": terminal,
            "attention": terminal_event is not None
            and terminal_event["kind"] == "partial-host",
            "admission": admission,
        }


def wait_for_worker_events(
    state: MutableMapping[str, Any], waiter: EventWaiter, *, clock: Clock,
    host_capability: Mapping[str, Any] | None = None,
    budget: Mapping[str, Any] | None = None,
    evidence_store: Any = None,
    capability_factory: TaskDispatchCapabilityFactory | None = None,
) -> dict[str, Any]:
    """Perform the incumbent one-shot 1800-second event wait."""
    outstanding = sorted(state.get("in_flight") or {})
    policy = {"mode": "event", "timeout_seconds": 1800, "scheduled_polling": False}
    events = waiter.wait(policy, outstanding)
    results = [
        record_worker_event(
            state, event,
            host_capability=host_capability, budget=budget,
            evidence_store=evidence_store, clock=clock,
            capability_factory=capability_factory,
        )
        for event in events
    ]
    return {
        "schema": "taskplane.worker-event-wake/v1",
        "event_count": len(results),
        "terminal": any(result["terminal"] for result in results),
        "results": results,
    }


def append_replan_generation(
    execution_dag: Mapping[str, Any], tasks: Sequence[Mapping[str, Any]], clock: Clock,
) -> dict[str, Any]:
    """Append one immutable generation and explicit supersession edges."""
    if execution_dag.get("schema") != EXECUTION_DAG_SCHEMA:
        raise PlanTopologyError("invalid execution DAG schema")
    result = deepcopy(dict(execution_dag))
    rows = _task_rows(tasks)
    generation = len(result["generations"])
    prefix = f"g{generation}"
    previous = generation - 1
    result["generations"].append({
        "generation": generation,
        "at": float(clock.wall_time()),
        "task_ids": sorted(row["id"] for row in rows),
    })
    existing_previous = {
        str(node["task_id"]) for node in result["nodes"]
        if int(node["generation"]) == previous
    }
    for row in sorted(rows, key=lambda item: item["id"]):
        result["nodes"].append({
            "id": f"{prefix}:{row['id']}",
            "task_id": row["id"],
            "generation": generation,
        })
        for dependency in row["deps"]:
            result["edges"].append([
                f"{prefix}:{dependency}", f"{prefix}:{row['id']}", "dependency"
            ])
        if row["id"] in existing_previous:
            result["edges"].append([
                f"g{previous}:{row['id']}", f"{prefix}:{row['id']}", "supersession"
            ])
    result["fingerprint"] = content_fingerprint({
        key: value for key, value in result.items() if key != "fingerprint"
    })
    return result


def execution_metrics(state: Mapping[str, Any]) -> dict[str, Any]:
    """Compute the Retro parallelism and duration-weighted critical path."""
    scheduler_idle = _finite_number(
        state.get("scheduler_caused_idle_seconds") or 0,
        "scheduler-caused idle time",
    )
    times = {}
    for task_id, values in (state.get("task_times") or {}).items():
        if values.get("start") is None or values.get("terminal") is None:
            continue
        times[task_id] = {
            "start": _finite_number(
                values["start"], f"task {task_id} start time",
            ),
            "terminal": _finite_number(
                values["terminal"], f"task {task_id} terminal time",
            ),
        }
    if not times:
        return {
            "schema": "taskplane.execution-metrics/v1",
            "active_worker_seconds": 0,
            "delivery_wall_seconds": 0,
            "parallelism_factor": 0,
            "longest_serial_chain": {"tasks": [], "seconds": 0},
            "scheduler_caused_idle_seconds": scheduler_idle,
        }
    durations = {
        task_id: max(0.0, values["terminal"] - values["start"])
        for task_id, values in times.items()
    }
    durations = {
        task_id: _finite_number(value, f"task {task_id} active time")
        for task_id, value in durations.items()
    }
    starts = [values["start"] for values in times.values()]
    terminals = [values["terminal"] for values in times.values()]
    wall = _finite_number(
        max(terminals) - min(starts), "delivery wall time",
    )
    active = _finite_number(sum(durations.values()), "active worker time")
    dependencies = state["topology"]["effective_dependencies"]
    cache: dict[str, tuple[float, list[str]]] = {}

    def critical(task_id: str) -> tuple[float, list[str]]:
        if task_id in cache:
            return cache[task_id]
        candidates = [
            critical(dependency) for dependency in dependencies.get(task_id, ())
            if dependency in durations
        ]
        predecessor_seconds, predecessor_tasks = max(
            candidates, key=lambda item: (item[0], [-ord(ch) for ch in "/".join(item[1])]),
            default=(0.0, []),
        )
        cache[task_id] = (
            predecessor_seconds + durations[task_id],
            [*predecessor_tasks, task_id],
        )
        return cache[task_id]

    longest_seconds, longest_tasks = max(
        (critical(task_id) for task_id in sorted(durations)),
        key=lambda item: (item[0], [-ord(ch) for ch in "/".join(item[1])]),
    )
    longest_seconds = _finite_number(
        longest_seconds, "longest serial chain time",
    )
    parallelism = _finite_number(
        active / wall if wall else 0, "parallelism factor",
    )
    return {
        "schema": "taskplane.execution-metrics/v1",
        "active_worker_seconds": active,
        "delivery_wall_seconds": wall,
        "parallelism_factor": parallelism,
        "longest_serial_chain": {"tasks": longest_tasks, "seconds": longest_seconds},
        "scheduler_caused_idle_seconds": scheduler_idle,
    }
