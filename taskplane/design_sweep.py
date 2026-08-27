"""Read-only validation of the retained R-0013 Codex Design sweep audit.

Codex owns the native lifecycle.  This module only reads an exact raw Codex
JSONL audit, binds it to one source thread and Design turn, and validates the
observed starts/finals against the retained result and approved Design bytes.
It never accepts caller-constructed timing rows and never writes audit data.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, BinaryIO

if __package__:
    from .delivery_ports import content_fingerprint
else:  # pragma: no cover - compatibility with legacy direct imports
    from delivery_ports import content_fingerprint


DESIGN_SWEEP_SCHEMA = "taskplane.design-quick-lens-sweep/v1"
DESIGN_RESULT_SCHEMA = "taskplane.design-lens-result/v1"
EXPECTED_LENS_COUNT = 26
AGENT_PREFIX = "/root/r0013_design_lens_"
_HEX = frozenset("0123456789abcdef")
_RESULT_PATH = re.compile(r"design/lens-evidence/([a-z0-9-]+)\.json")
_SHA256 = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
_REQUIRED_SWEEP_REFUSALS = frozenset(
    {
        "missing catalog id",
        "duplicate id",
        "repeated generation",
        "serial-all timing",
        "full mode",
        "deep mode",
        "automatic sweep",
        "non-Design stage",
        "undispositioned result",
    }
)


class DesignSweepError(ValueError):
    """The retained broad Design signal violates its closed contract."""


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DesignSweepError(f"{field} is required")
    return value


def _fingerprint(value: Any, field: str) -> str:
    text = _required_text(value, field)
    if len(text) != 64 or any(character not in _HEX for character in text):
        raise DesignSweepError(
            f"{field} must be a lowercase SHA-256 fingerprint"
        )
    return text


def _finite_time(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DesignSweepError(f"{field} must be a finite timestamp")
    timestamp = float(value)
    if not math.isfinite(timestamp):
        raise DesignSweepError(f"{field} must be a finite timestamp")
    return timestamp


def _catalog_ids(catalog: Mapping[str, Any]) -> tuple[str, ...]:
    if not isinstance(catalog, Mapping):
        raise DesignSweepError("lens catalog must be a mapping")
    lenses = catalog.get("lenses")
    if isinstance(lenses, (str, bytes)) or not isinstance(lenses, Sequence):
        raise DesignSweepError("lens catalog lenses must be a collection")
    ids: list[str] = []
    for index, row in enumerate(lenses):
        if not isinstance(row, Mapping):
            raise DesignSweepError(f"catalog lens {index} must be a mapping")
        ids.append(_required_text(row.get("id"), f"catalog lens {index} id"))
    if len(ids) != EXPECTED_LENS_COUNT:
        raise DesignSweepError(
            f"Design sweep requires exactly {EXPECTED_LENS_COUNT} catalog lenses"
        )
    if len(ids) != len(set(ids)):
        raise DesignSweepError("lens catalog contains duplicate ids")
    return tuple(ids)


def _raw_json_evidence(
    value: Any, *, label: str
) -> tuple[str, Mapping[str, Any]]:
    if isinstance(value, Path):
        try:
            raw = value.read_bytes()
        except OSError as exc:
            raise DesignSweepError(f"{label} is unreadable: {exc}") from exc
    elif isinstance(value, bytes):
        raw = value
    else:
        raise DesignSweepError(
            f"{label} must be exact retained bytes or a retained Path"
        )
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise DesignSweepError(f"{label} is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise DesignSweepError(f"{label} must contain an object")
    return hashlib.sha256(raw).hexdigest(), parsed


def _disposition_rows(value: Any) -> dict[str, Mapping[str, Any]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise DesignSweepError("Design dispositions must be a collection")
    rows: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(value):
        if not isinstance(row, Mapping):
            raise DesignSweepError(f"Design disposition {index} must be a mapping")
        lens_id = _required_text(
            row.get("lens"), f"Design disposition {index} lens"
        )
        if lens_id in rows:
            raise DesignSweepError(f"duplicate Design disposition for {lens_id}")
        rows[lens_id] = row
    return rows


def _approved_sweep_semantics(
    sweep: Mapping[str, Any], completed_state: Mapping[str, Any]
) -> dict[str, Any]:
    stage = sweep.get("stage")
    if stage != "design":
        raise DesignSweepError("approved all-lens sweep is permitted only in Design")
    mode = sweep.get("mode")
    if mode != "quick":
        raise DesignSweepError("approved Design all-lens sweep must use quick mode")
    expected_count = sweep.get("expected_lens_count")
    if isinstance(expected_count, bool) or expected_count != EXPECTED_LENS_COUNT:
        raise DesignSweepError(
            f"approved Design sweep requires exactly {EXPECTED_LENS_COUNT} lenses"
        )
    refusals = sweep.get("refusals")
    if isinstance(refusals, (str, bytes)) or not isinstance(refusals, Sequence) or \
            set(refusals) != _REQUIRED_SWEEP_REFUSALS:
        raise DesignSweepError(
            "approved Design sweep semantic refusal inventory is incomplete"
        )
    automatic = sweep.get("automatic", False)
    if automatic is not False:
        raise DesignSweepError("automatic all-lens sweep is forbidden")
    result_count = completed_state.get("result_count")
    unique_count = completed_state.get("unique_lens_count")
    repeat_count = completed_state.get("repeat_count")
    if isinstance(result_count, bool) or result_count != EXPECTED_LENS_COUNT or \
            isinstance(unique_count, bool) or unique_count != EXPECTED_LENS_COUNT:
        raise DesignSweepError(
            "approved Design sweep completed counts must cover 26 unique lenses"
        )
    if isinstance(repeat_count, bool) or repeat_count != 0:
        raise DesignSweepError("repeated all-lens generation is forbidden")
    return {
        "stage": stage,
        "mode": mode,
        "automatic": automatic,
        "expected_lens_count": expected_count,
        "result_count": result_count,
        "unique_lens_count": unique_count,
        "repeat_count": repeat_count,
    }


def _line_source(value: bytes | Path) -> tuple[Iterator[bytes], BinaryIO | None]:
    if isinstance(value, Path):
        try:
            stream = value.open("rb")
        except OSError as exc:
            raise DesignSweepError(f"Codex audit source is unreadable: {exc}") from exc
        return iter(stream), stream
    if isinstance(value, bytes):
        return iter(value.splitlines(keepends=True)), None
    raise DesignSweepError(
        "Codex audit source must be exact raw bytes or a retained Path"
    )


def _entry(raw_line: bytes, line_number: int) -> Mapping[str, Any]:
    try:
        parsed = json.loads(raw_line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise DesignSweepError(
            f"Codex audit line {line_number} is not valid JSON"
        ) from exc
    if not isinstance(parsed, dict):
        raise DesignSweepError(
            f"Codex audit line {line_number} must contain an object"
        )
    return parsed


def _audit_timestamp(value: Any, field: str) -> float:
    text = _required_text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DesignSweepError(f"{field} is not an ISO timestamp") from exc
    timestamp = parsed.timestamp()
    if not math.isfinite(timestamp):
        raise DesignSweepError(f"{field} must be a finite timestamp")
    return timestamp


def _final_text(payload: Mapping[str, Any]) -> str | None:
    content = payload.get("content")
    if not isinstance(content, list):
        return None
    values = [
        row.get("text")
        for row in content
        if isinstance(row, Mapping) and row.get("type") == "input_text"
        and isinstance(row.get("text"), str)
    ]
    finals = [text for text in values if text.startswith("Message Type: FINAL_ANSWER\n")]
    if len(finals) > 1:
        raise DesignSweepError("one agent message contains duplicate final payloads")
    return finals[0] if finals else None


def _parse_final_claim(text: str, *, lens_id: str) -> tuple[str, str]:
    expected_path = f"design/lens-evidence/{lens_id}.json"
    paths = {match.group(0) for match in _RESULT_PATH.finditer(text)}
    if paths != {expected_path}:
        raise DesignSweepError(
            f"final result path mismatch for lens {lens_id}"
        )
    fingerprints = set(_SHA256.findall(text))
    if len(fingerprints) != 1:
        raise DesignSweepError(
            f"final result SHA is missing or ambiguous for lens {lens_id}"
        )
    return expected_path, next(iter(fingerprints))


def _parse_codex_audit(
    source: bytes | Path,
    *,
    expected_source_log_sha256: str,
    source_thread_id: str,
    design_turn_id: str,
    expected_agent_paths: set[str],
) -> tuple[
    str,
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
    dict[str, list[float]],
]:
    expected_log_sha = _fingerprint(
        expected_source_log_sha256, "expected_source_log_sha256"
    )
    thread_id = _required_text(source_thread_id, "source_thread_id")
    turn_id = _required_text(design_turn_id, "design_turn_id")
    hasher = hashlib.sha256()
    session_ids: set[str] = set()
    starts: dict[str, list[dict[str, Any]]] = {}
    finals: dict[str, list[dict[str, Any]]] = {}
    failed_starts: dict[str, list[float]] = {}
    foreign_generation_starts: list[str] = []
    foreign_generation_finals: list[str] = []
    lines, stream = _line_source(source)
    try:
        for line_number, raw_line in enumerate(lines, start=1):
            hasher.update(raw_line)
            if not raw_line.strip():
                raise DesignSweepError(
                    f"Codex audit line {line_number} is unexpectedly empty"
                )
            row = _entry(raw_line, line_number)
            row_type = row.get("type")
            payload = row.get("payload")
            if not isinstance(payload, Mapping):
                continue
            if row_type == "session_meta":
                session_id = payload.get("id") or payload.get("session_id")
                if isinstance(session_id, str) and session_id:
                    session_ids.add(session_id)
                continue

            if row_type == "event_msg" and payload.get("type") == "item_completed":
                item = payload.get("item")
                if not isinstance(item, Mapping) or \
                        item.get("type") != "SubAgentActivity":
                    continue
                agent_path = item.get("agent_path")
                if not isinstance(agent_path, str) or \
                        not agent_path.startswith(AGENT_PREFIX):
                    continue
                if payload.get("thread_id") != thread_id or \
                        payload.get("turn_id") != turn_id:
                    foreign_generation_starts.append(agent_path)
                    continue
                if agent_path not in expected_agent_paths:
                    raise DesignSweepError(
                        f"unknown Design lens native task {agent_path}"
                    )
                if item.get("kind") in {"failed", "errored"}:
                    failed_starts.setdefault(agent_path, []).append(
                        _finite_time(
                            payload.get("started_at_ms"),
                            f"failed start timestamp for {agent_path}",
                        ) / 1000.0
                    )
                    continue
                if item.get("kind") != "started":
                    continue
                starts.setdefault(agent_path, []).append(
                    {
                        "event_id": _required_text(
                            item.get("id"), f"start event id for {agent_path}"
                        ),
                        "agent_thread_id": _required_text(
                            item.get("agent_thread_id"),
                            f"agent thread id for {agent_path}",
                        ),
                        "started_at": _finite_time(
                            payload.get("started_at_ms"),
                            f"started_at_ms for {agent_path}",
                        ) / 1000.0,
                    }
                )
                continue

            if row_type != "response_item" or payload.get("type") != "agent_message":
                continue
            author = payload.get("author")
            if not isinstance(author, str) or not author.startswith(AGENT_PREFIX):
                continue
            metadata = payload.get("internal_chat_message_metadata_passthrough")
            text = _final_text(payload)
            if text is None:
                continue
            if not isinstance(metadata, Mapping) or metadata.get("turn_id") != turn_id:
                foreign_generation_finals.append(author)
                continue
            if author not in expected_agent_paths:
                raise DesignSweepError(f"unknown Design lens final author {author}")
            if payload.get("recipient") != "/root":
                raise DesignSweepError(
                    f"Design lens final has foreign recipient for {author}"
                )
            finals.setdefault(author, []).append(
                {
                    "message_id": _required_text(
                        payload.get("id"), f"final message id for {author}"
                    ),
                    "ended_at": _audit_timestamp(
                        row.get("timestamp"), f"final timestamp for {author}"
                    ),
                    "text": text,
                }
            )
    finally:
        if stream is not None:
            stream.close()

    source_log_sha = hasher.hexdigest()
    if source_log_sha != expected_log_sha:
        raise DesignSweepError("Codex audit source fingerprint mismatch")
    if session_ids != {thread_id}:
        raise DesignSweepError("Codex audit source thread identity mismatch")
    if foreign_generation_starts or foreign_generation_finals:
        raise DesignSweepError(
            "repeated or non-Design all-lens generation exists outside the "
            "approved Design turn"
        )
    for agent_path, failed_times in failed_starts.items():
        successful = starts.get(agent_path, [])
        if len(successful) == 1 and any(
            failed_at >= successful[0]["started_at"]
            for failed_at in failed_times
        ):
            raise DesignSweepError(
                f"failed native start for {agent_path} is not superseded "
                "before the successful start"
            )
    return source_log_sha, {
        key: rows[0]
        for key, rows in starts.items()
        if len(rows) == 1
    }, {
        key: rows[0]
        for key, rows in finals.items()
        if len(rows) == 1
    }, starts, finals, failed_starts


def _rows_overlap(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return max(float(left["started_at"]), float(right["started_at"])) < min(
        float(left["ended_at"]), float(right["ended_at"])
    )


def _overlap_batches(rows: list[dict[str, Any]]) -> list[str]:
    adjacent: list[set[int]] = [set() for _ in rows]
    for left in range(len(rows)):
        for right in range(left + 1, len(rows)):
            if _rows_overlap(rows[left], rows[right]):
                adjacent[left].add(right)
                adjacent[right].add(left)
    isolated = [rows[index]["lens_id"] for index, edges in enumerate(adjacent) if not edges]
    if isolated:
        raise DesignSweepError(
            "predominantly serial Design trace; rows without overlap: "
            + ", ".join(sorted(isolated))
        )

    components: list[list[int]] = []
    unseen = set(range(len(rows)))
    while unseen:
        seed = min(unseen)
        pending = [seed]
        component: list[int] = []
        unseen.remove(seed)
        while pending:
            current = pending.pop()
            component.append(current)
            for neighbor in sorted(adjacent[current]):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    pending.append(neighbor)
        components.append(sorted(component))
    batch_ids = [f"native-overlap-batch-{index:02d}" for index in range(len(components))]
    for batch_id, component in zip(batch_ids, components):
        for index in component:
            rows[index]["batch_id"] = batch_id
    return batch_ids


def validate_design_sweep(
    catalog: Mapping[str, Any],
    *,
    stage: str,
    source_content_fingerprint: str,
    result_evidence: Mapping[str, Any],
    approved_design_evidence: bytes | Path,
    codex_audit_evidence: bytes | Path,
    source_thread_id: str,
    design_turn_id: str,
    expected_catalog_fingerprint: str,
    expected_design_evidence_sha256: str,
    expected_source_log_sha256: str,
) -> dict[str, Any]:
    """Validate the one real quick, concurrent, Design-only all-lens sweep."""
    if stage != "design":
        raise DesignSweepError("all-lens sweep is permitted only in Design")
    catalog_ids = _catalog_ids(catalog)
    catalog_id_set = set(catalog_ids)
    catalog_fingerprint = content_fingerprint(dict(catalog))
    if _fingerprint(
        expected_catalog_fingerprint, "expected_catalog_fingerprint"
    ) != catalog_fingerprint:
        raise DesignSweepError("lens catalog fingerprint mismatch")
    source_fingerprint = _fingerprint(
        source_content_fingerprint, "source_content_fingerprint"
    )

    design_sha, design = _raw_json_evidence(
        approved_design_evidence, label="approved Design evidence"
    )
    if design_sha != _fingerprint(
        expected_design_evidence_sha256, "expected_design_evidence_sha256"
    ):
        raise DesignSweepError("approved Design evidence fingerprint mismatch")
    if design.get("schema") != "taskplane.design/v1":
        raise DesignSweepError("approved Design evidence schema is invalid")
    sweep = design.get("design_sweep")
    completed_state = (
        sweep.get("completed_state") if isinstance(sweep, Mapping) else None
    )
    if not isinstance(sweep, Mapping) or \
            not isinstance(completed_state, Mapping) or \
            completed_state.get("source_content_fingerprint") != source_fingerprint:
        raise DesignSweepError("approved Design sweep source binding mismatch")
    semantics = _approved_sweep_semantics(sweep, completed_state)
    if stage != semantics["stage"]:
        raise DesignSweepError(
            "runtime stage does not match the approved Design sweep stage"
        )
    dispositions = _disposition_rows(design.get("lens_evidence"))
    if set(dispositions) != catalog_id_set:
        raise DesignSweepError(
            "final Design dispositions must cover every catalog lens exactly once"
        )
    if not isinstance(result_evidence, Mapping) or \
            set(result_evidence) != catalog_id_set:
        raise DesignSweepError(
            "retained result evidence must cover every catalog lens exactly once"
        )

    agent_paths = {
        lens_id: f"{AGENT_PREFIX}{lens_id.replace('-', '_')}"
        for lens_id in catalog_ids
    }
    parsed = _parse_codex_audit(
        codex_audit_evidence,
        expected_source_log_sha256=expected_source_log_sha256,
        source_thread_id=source_thread_id,
        design_turn_id=design_turn_id,
        expected_agent_paths=set(agent_paths.values()),
    )
    source_log_sha, starts, finals, all_starts, all_finals, _failed_starts = parsed
    for agent_path in agent_paths.values():
        start_count = len(all_starts.get(agent_path, []))
        final_count = len(all_finals.get(agent_path, []))
        if start_count != 1:
            raise DesignSweepError(
                f"Design lens {agent_path} requires one successful native start; "
                f"observed {start_count}"
            )
        if final_count != 1:
            raise DesignSweepError(
                f"Design lens {agent_path} requires one final result; observed "
                f"{final_count}"
            )
    if set(starts) != set(agent_paths.values()) or \
            set(finals) != set(agent_paths.values()):
        raise DesignSweepError("Design audit does not cover all canonical lens tasks")

    rows: list[dict[str, Any]] = []
    seen_threads: set[str] = set()
    seen_start_events: set[str] = set()
    seen_final_messages: set[str] = set()
    for lens_id in catalog_ids:
        agent_path = agent_paths[lens_id]
        start = starts[agent_path]
        final = finals[agent_path]
        agent_thread_id = start["agent_thread_id"]
        if agent_thread_id in seen_threads:
            raise DesignSweepError("Design lens native thread identity is duplicated")
        seen_threads.add(agent_thread_id)
        if start["event_id"] in seen_start_events or \
                final["message_id"] in seen_final_messages:
            raise DesignSweepError(
                "Design lens lifecycle event identity is duplicated"
            )
        seen_start_events.add(start["event_id"])
        seen_final_messages.add(final["message_id"])
        if final["ended_at"] <= start["started_at"]:
            raise DesignSweepError(
                f"Design lens final precedes its start for {lens_id}"
            )
        result_path, claimed_sha = _parse_final_claim(
            final["text"], lens_id=lens_id
        )
        retained_sha, result = _raw_json_evidence(
            result_evidence[lens_id], label=f"retained result for {lens_id}"
        )
        if retained_sha != claimed_sha:
            raise DesignSweepError(
                f"final result SHA mismatch for lens {lens_id}"
            )
        if result.get("schema") != DESIGN_RESULT_SCHEMA or \
                result.get("lens") != lens_id:
            raise DesignSweepError(
                f"retained result identity is invalid for lens {lens_id}"
            )
        if result.get("content_fingerprint") != source_fingerprint:
            raise DesignSweepError(
                f"retained result source fingerprint mismatch for lens {lens_id}"
            )
        disposition = dispositions[lens_id]
        if disposition.get("source_evidence") != result_path or \
                disposition.get("source_evidence_sha256") != claimed_sha or \
                disposition.get("source_content_fingerprint") != source_fingerprint:
            raise DesignSweepError(
                f"final Design disposition evidence mismatch for lens {lens_id}"
            )
        if disposition.get("source_verdict") != result.get("verdict") or \
                disposition.get("source_blockers") != result.get("blockers"):
            raise DesignSweepError(
                f"final Design source result mismatch for lens {lens_id}"
            )
        rows.append(
            {
                "lens_id": lens_id,
                "native_task_name": agent_path.removeprefix("/root/"),
                "native_agent_id": agent_thread_id,
                "start_event_id": start["event_id"],
                "final_message_id": final["message_id"],
                "started_at": start["started_at"],
                "ended_at": final["ended_at"],
                "mode": "quick",
                "result_path": result_path,
                "result_fingerprint": claimed_sha,
                "disposition": _required_text(
                    disposition.get("disposition"),
                    f"final disposition for {lens_id}",
                ),
            }
        )

    concurrent_batches = _overlap_batches(rows)
    rows.sort(key=lambda row: str(row["lens_id"]))
    generation_id = content_fingerprint(
        {"source_thread_id": source_thread_id, "design_turn_id": design_turn_id}
    )
    projection: dict[str, Any] = {
        "schema": DESIGN_SWEEP_SCHEMA,
        "stage": semantics["stage"],
        "source_thread_id": source_thread_id,
        "design_turn_id": design_turn_id,
        "source_log_sha256": source_log_sha,
        "source_content_fingerprint": source_fingerprint,
        "catalog_fingerprint": catalog_fingerprint,
        "design_evidence_sha256": design_sha,
        "expected_lens_count": semantics["expected_lens_count"],
        "result_count": len(rows),
        "unique_lens_count": len({row["lens_id"] for row in rows}),
        "native_thread_count": len(seen_threads),
        "mode": semantics["mode"],
        "automatic": semantics["automatic"],
        "generation_id": generation_id,
        "generation_count": 1,
        "repeat_count": semantics["repeat_count"],
        "concurrent_batch_ids": concurrent_batches,
        "rows": rows,
        "status": "complete",
    }
    return {**projection, "fingerprint": content_fingerprint(projection)}
