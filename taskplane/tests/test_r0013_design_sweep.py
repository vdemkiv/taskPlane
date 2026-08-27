from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

import pytest

from taskplane.delivery_ports import content_fingerprint
from taskplane.design_sweep import (
    DESIGN_SWEEP_SCHEMA,
    DesignSweepError,
    validate_design_sweep,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_THREAD = "01a00000-0000-7000-8000-000000000001"
FIXTURE_TURN = "01a00000-0000-7000-8000-000000000002"
CANONICAL_AUDIT = Path(
    os.environ.get(
        "TASKPLANE_R0013_CODEX_AUDIT",
        "/Users/vdemkiv/.codex/sessions/2026/08/24/"
        "rollout-2026-08-24T19-27-08-"
        "01a03619-63a9-7743-90f9-1b6b1945b6ac.jsonl",
    )
)
CANONICAL_THREAD = "01a03619-63a9-7743-90f9-1b6b1945b6ac"
CANONICAL_TURN = "01a0409b-8903-78f3-a096-3fbd794d6ab3"
CANONICAL_AUDIT_SHA256 = \
    "467edb61e99beac0432fbda6fc0e5028c6703304ab72b86029ff58f843d60ef7"


def _sources():
    catalog = json.loads((ROOT / "lenses/catalog.json").read_text(encoding="utf-8"))
    design_raw = (ROOT / "design/contract.json").read_bytes()
    contract = json.loads(design_raw)
    source_fingerprint = contract["design_sweep"]["completed_state"][
        "source_content_fingerprint"
    ]
    results = {
        row["id"]: (ROOT / f"design/lens-evidence/{row['id']}.json").read_bytes()
        for row in catalog["lenses"]
    }
    return catalog, design_raw, contract, results, source_fingerprint


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _fixture_entries(intervals):
    catalog, _design, _contract, results, _source = _sources()
    entries = [
        {
            "timestamp": _iso(1.0),
            "ordinal": 0,
            "type": "session_meta",
            "payload": {"id": FIXTURE_THREAD, "session_id": FIXTURE_THREAD},
        }
    ]
    for index, row in enumerate(catalog["lenses"]):
        lens_id = row["id"]
        agent_path = f"/root/r0013_design_lens_{lens_id.replace('-', '_')}"
        started_at, ended_at = intervals[index]
        entries.append(
            {
                "timestamp": _iso(started_at),
                "ordinal": index * 2 + 1,
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "thread_id": FIXTURE_THREAD,
                    "turn_id": FIXTURE_TURN,
                    "item": {
                        "type": "SubAgentActivity",
                        "id": f"fixture-start-{index:02d}",
                        "kind": "started",
                        "agent_thread_id": (
                            f"01a00000-0000-7000-8000-{index + 100:012d}"
                        ),
                        "agent_path": agent_path,
                    },
                    "started_at_ms": int(started_at * 1000),
                    "completed_at_ms": int(started_at * 1000),
                },
            }
        )
        result_sha = hashlib.sha256(results[lens_id]).hexdigest()
        entries.append(
            {
                "timestamp": _iso(ended_at),
                "ordinal": index * 2 + 2,
                "type": "response_item",
                "payload": {
                    "type": "agent_message",
                    "id": f"fixture-final-{index:02d}",
                    "author": agent_path,
                    "recipient": "/root",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Message Type: FINAL_ANSWER\n"
                                "Task name: /root\n"
                                f"Sender: {agent_path}\nPayload:\n"
                                f"taskplane-result-path:design/lens-evidence/{lens_id}.json\n"
                                f"taskplane-result-sha256:{result_sha}"
                            ),
                        }
                    ],
                    "internal_chat_message_metadata_passthrough": {
                        "turn_id": FIXTURE_TURN,
                        "create_time": ended_at,
                    },
                },
            }
        )
    return entries


def _raw_log(entries) -> bytes:
    ordered = sorted(
        entries,
        key=lambda row: (row["timestamp"], 0 if row["type"] == "session_meta" else 1),
    )
    for ordinal, row in enumerate(ordered):
        row["ordinal"] = ordinal
    return b"".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8")
        + b"\n"
        for row in ordered
    )


def _validate_log(
    audit,
    *,
    stage="design",
    source_thread=FIXTURE_THREAD,
    design_turn=FIXTURE_TURN,
    expected_audit_sha=None,
    design_raw=None,
    results=None,
):
    catalog, retained_design, _contract, retained_results, source = _sources()
    active_design = design_raw or retained_design
    active_results = results or retained_results
    raw_sha = (
        hashlib.sha256(audit).hexdigest() if isinstance(audit, bytes) else None
    )
    return validate_design_sweep(
        catalog,
        stage=stage,
        source_content_fingerprint=source,
        result_evidence=active_results,
        approved_design_evidence=active_design,
        codex_audit_evidence=audit,
        source_thread_id=source_thread,
        design_turn_id=design_turn,
        expected_catalog_fingerprint=content_fingerprint(catalog),
        expected_design_evidence_sha256=hashlib.sha256(active_design).hexdigest(),
        expected_source_log_sha256=expected_audit_sha or raw_sha,
    )


def _overlapping_fixture():
    return _raw_log(
        _fixture_entries(
            [(100.0 + index, 200.0 + index) for index in range(26)]
        )
    )


def test_exactly_one_quick_result_for_all_26_catalog_lenses():
    receipt = _validate_log(_overlapping_fixture())

    assert receipt["schema"] == DESIGN_SWEEP_SCHEMA
    assert receipt["stage"] == "design"
    assert receipt["mode"] == "quick"
    assert receipt["result_count"] == 26
    assert receipt["unique_lens_count"] == 26
    assert receipt["native_thread_count"] == 26
    assert receipt["generation_count"] == 1
    assert receipt["repeat_count"] == 0
    assert receipt["automatic"] is False
    assert receipt["status"] == "complete"


def test_independent_lens_tasks_overlap_in_native_batches():
    receipt = _validate_log(_overlapping_fixture())

    assert receipt["concurrent_batch_ids"] == ["native-overlap-batch-00"]
    assert {row["batch_id"] for row in receipt["rows"]} == {
        "native-overlap-batch-00"
    }


def test_missing_duplicate_serial_full_deep_or_non_design_sweep_is_refused():
    intervals = [(100.0 + index, 200.0 + index) for index in range(26)]
    entries = _fixture_entries(intervals)
    product_final = next(
        row
        for row in entries
        if row["type"] == "response_item"
        and row["payload"]["author"].endswith("_product")
    )
    with pytest.raises(DesignSweepError, match="requires one final result; observed 0"):
        _validate_log(_raw_log([row for row in entries if row is not product_final]))

    duplicated = deepcopy(entries)
    duplicated.append(deepcopy(product_final))
    with pytest.raises(DesignSweepError, match="requires one final result; observed 2"):
        _validate_log(_raw_log(duplicated))

    serial = _raw_log(
        _fixture_entries(
            [(100.0 + index * 10, 101.0 + index * 10) for index in range(26)]
        )
    )
    with pytest.raises(DesignSweepError, match="predominantly serial"):
        _validate_log(serial)

    _catalog, _design, retained_contract, _results, _source = _sources()
    for mode in ("full", "deep"):
        contract = deepcopy(retained_contract)
        contract["design_sweep"]["mode"] = mode
        changed = json.dumps(
            contract, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        with pytest.raises(DesignSweepError, match="must use quick mode"):
            _validate_log(_overlapping_fixture(), design_raw=changed)

    automatic = deepcopy(retained_contract)
    automatic["design_sweep"]["automatic"] = True
    automatic_bytes = json.dumps(
        automatic, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    with pytest.raises(DesignSweepError, match="automatic all-lens sweep"):
        _validate_log(_overlapping_fixture(), design_raw=automatic_bytes)

    repeated = deepcopy(retained_contract)
    repeated["design_sweep"]["completed_state"]["repeat_count"] = 1
    repeated_bytes = json.dumps(
        repeated, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    with pytest.raises(DesignSweepError, match="repeated all-lens generation"):
        _validate_log(_overlapping_fixture(), design_raw=repeated_bytes)

    non_design = deepcopy(retained_contract)
    non_design["design_sweep"]["stage"] = "evaluate"
    non_design_bytes = json.dumps(
        non_design, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    with pytest.raises(DesignSweepError, match="permitted only in Design"):
        _validate_log(_overlapping_fixture(), design_raw=non_design_bytes)
    with pytest.raises(DesignSweepError, match="permitted only in Design"):
        _validate_log(_overlapping_fixture(), stage="evaluate")


@pytest.mark.skipif(not CANONICAL_AUDIT.is_file(), reason="canonical Codex audit unavailable")
def test_canonical_retained_codex_audit_proves_the_design_sweep():
    receipt = _validate_log(
        CANONICAL_AUDIT,
        source_thread=CANONICAL_THREAD,
        design_turn=CANONICAL_TURN,
        expected_audit_sha=CANONICAL_AUDIT_SHA256,
    )

    assert receipt["source_thread_id"] == CANONICAL_THREAD
    assert receipt["design_turn_id"] == CANONICAL_TURN
    assert receipt["source_log_sha256"] == CANONICAL_AUDIT_SHA256
    assert receipt["result_count"] == 26
    assert receipt["native_thread_count"] == 26
    assert receipt["concurrent_batch_ids"] == ["native-overlap-batch-00"]


def test_foreign_source_thread_or_design_turn_is_refused():
    audit = _overlapping_fixture()
    with pytest.raises(DesignSweepError, match="source thread identity mismatch"):
        _validate_log(
            audit,
            source_thread="01afffff-ffff-7fff-8fff-ffffffffffff",
        )
    with pytest.raises(DesignSweepError, match="outside the approved Design turn"):
        _validate_log(
            audit,
            design_turn="01afffff-ffff-7fff-8fff-fffffffffffe",
        )


def test_caller_mappings_cannot_replace_raw_audit_or_result_bytes():
    audit = _overlapping_fixture()
    with pytest.raises(DesignSweepError, match="exact raw bytes"):
        _validate_log(
            {"invented": "row"}, expected_audit_sha="0" * 64
        )

    _catalog, _design, _contract, results, _source = _sources()
    invented = {
        lens_id: json.loads(raw)
        for lens_id, raw in results.items()
    }
    with pytest.raises(DesignSweepError, match="exact retained bytes"):
        _validate_log(audit, results=invented)


def test_predominantly_serial_intervals_are_refused():
    audit = _raw_log(
        _fixture_entries(
            [(100.0 + index * 10, 101.0 + index * 10) for index in range(26)]
        )
    )
    with pytest.raises(DesignSweepError, match="predominantly serial"):
        _validate_log(audit)


def test_missing_or_duplicate_final_is_refused():
    entries = _fixture_entries(
        [(100.0 + index, 200.0 + index) for index in range(26)]
    )
    final = next(
        row
        for row in entries
        if row["type"] == "response_item"
        and row["payload"]["author"].endswith("_product")
    )
    missing = [row for row in entries if row is not final]
    with pytest.raises(DesignSweepError, match="requires one final result; observed 0"):
        _validate_log(_raw_log(missing))

    duplicate = deepcopy(entries)
    duplicate.append(deepcopy(final))
    with pytest.raises(DesignSweepError, match="requires one final result; observed 2"):
        _validate_log(_raw_log(duplicate))


def test_duplicate_successful_start_is_refused_but_failed_attempt_is_ignored():
    entries = _fixture_entries(
        [(100.0 + index, 200.0 + index) for index in range(26)]
    )
    start = next(
        row
        for row in entries
        if row["type"] == "event_msg"
        and row["payload"]["item"].get("agent_path", "").endswith("_product")
    )
    duplicate = deepcopy(entries)
    duplicate.append(deepcopy(start))
    with pytest.raises(
        DesignSweepError, match="requires one successful native start; observed 2"
    ):
        _validate_log(_raw_log(duplicate))

    failed_attempt = deepcopy(entries)
    failed = deepcopy(start)
    failed["payload"]["item"]["kind"] = "failed"
    failed["payload"]["item"]["id"] = "superseded-failed-attempt"
    failed["timestamp"] = _iso(
        start["payload"]["started_at_ms"] / 1000.0 - 1.0
    )
    failed["payload"]["started_at_ms"] -= 1000
    failed["payload"]["completed_at_ms"] -= 1000
    failed_attempt.append(failed)
    assert _validate_log(_raw_log(failed_attempt))["status"] == "complete"

    late_failed_attempt = deepcopy(entries)
    late = deepcopy(start)
    late["payload"]["item"]["kind"] = "failed"
    late["payload"]["item"]["id"] = "late-failed-attempt"
    late["timestamp"] = _iso(
        start["payload"]["started_at_ms"] / 1000.0 + 1.0
    )
    late["payload"]["started_at_ms"] += 1000
    late["payload"]["completed_at_ms"] += 1000
    late_failed_attempt.append(late)
    with pytest.raises(DesignSweepError, match="not superseded before"):
        _validate_log(_raw_log(late_failed_attempt))


def test_second_complete_generation_in_another_turn_is_refused():
    entries = _fixture_entries(
        [(100.0 + index, 200.0 + index) for index in range(26)]
    )
    second_turn = "01a00000-0000-7000-8000-000000000003"
    second_generation = deepcopy(
        [row for row in entries if row["type"] != "session_meta"]
    )
    for row in second_generation:
        if row["type"] == "event_msg":
            payload = row["payload"]
            payload["turn_id"] = second_turn
            payload["started_at_ms"] += 1_000_000
            payload["completed_at_ms"] += 1_000_000
            payload["item"]["id"] += "-second-generation"
            payload["item"]["agent_thread_id"] += "-second-generation"
            row["timestamp"] = _iso(payload["started_at_ms"] / 1000.0)
        else:
            payload = row["payload"]
            payload["id"] += "-second-generation"
            metadata = payload["internal_chat_message_metadata_passthrough"]
            metadata["turn_id"] = second_turn
            metadata["create_time"] += 1000.0
            ended_at = datetime.fromisoformat(
                row["timestamp"].replace("Z", "+00:00")
            ).timestamp() + 1000.0
            row["timestamp"] = _iso(ended_at)
    combined = _raw_log(entries + second_generation)

    with pytest.raises(
        DesignSweepError,
        match="repeated or non-Design all-lens generation.*approved Design turn",
    ):
        _validate_log(combined)


@pytest.mark.parametrize("field", ["path", "sha"])
def test_wrong_final_result_path_or_sha_is_refused(field):
    entries = _fixture_entries(
        [(100.0 + index, 200.0 + index) for index in range(26)]
    )
    final = next(
        row
        for row in entries
        if row["type"] == "response_item"
        and row["payload"]["author"].endswith("_product")
    )
    text = final["payload"]["content"][0]["text"]
    if field == "path":
        text = text.replace("product.json", "security.json")
        message = "final result path mismatch"
    else:
        text = text.replace(
            hashlib.sha256(
                (ROOT / "design/lens-evidence/product.json").read_bytes()
            ).hexdigest(),
            "0" * 64,
        )
        message = "final result SHA mismatch"
    final["payload"]["content"][0]["text"] = text
    with pytest.raises(DesignSweepError, match=message):
        _validate_log(_raw_log(entries))


def test_catalog_design_and_audit_fingerprints_are_mandatory_and_exact():
    catalog, design, _contract, results, source = _sources()
    audit = _overlapping_fixture()
    with pytest.raises(TypeError, match="expected_catalog_fingerprint"):
        validate_design_sweep(
            catalog,
            stage="design",
            source_content_fingerprint=source,
            result_evidence=results,
            approved_design_evidence=design,
            codex_audit_evidence=audit,
            source_thread_id=FIXTURE_THREAD,
            design_turn_id=FIXTURE_TURN,
            expected_design_evidence_sha256=hashlib.sha256(design).hexdigest(),
            expected_source_log_sha256=hashlib.sha256(audit).hexdigest(),
        )
    with pytest.raises(DesignSweepError, match="audit source fingerprint mismatch"):
        _validate_log(audit, expected_audit_sha="0" * 64)


def test_every_design_result_has_one_disposition():
    _catalog, _design, contract, _results, _source = _sources()
    contract["lens_evidence"] = contract["lens_evidence"][:-1]
    design = json.dumps(
        contract, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    with pytest.raises(DesignSweepError, match="cover every catalog lens"):
        _validate_log(_overlapping_fixture(), design_raw=design)
