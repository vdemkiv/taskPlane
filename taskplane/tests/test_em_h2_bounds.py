"""R-0002 H2-C: review cost, transcript, and usage-truth bounds."""

from __future__ import annotations

import json
import inspect
import os
import time

import pytest

from taskplane import dispatch_telemetry, review, tp
from taskplane.delivery_ports import FakeClock


def _codex_usage_row(identity: str, *, input_tokens: int = 10,
                     cached_tokens: int = 4, output_tokens: int = 2) -> dict:
    return {"message": {"id": identity, "usage": {
        "input_tokens": input_tokens,
        "input_tokens_details": {"cached_tokens": cached_tokens},
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }}}


def _dispatch(dispatch_id: str) -> dict:
    return {
        "dispatch_id": dispatch_id, "thread_id": "thread-" + dispatch_id,
        "thread_type": "worker", "task_id": "task-" + dispatch_id,
        "dependencies": [], "shared_owner": None, "started_at": 2,
        "ended_at": 2, "wait_duration_seconds": 0,
        "correction_count": 0, "events": [],
    }


def _usage() -> dict:
    return {
        "input_tokens": 8, "cached_input_tokens": 2,
        "uncached_input_tokens": 6, "output_tokens": 2,
        "reasoning_tokens": 1, "total_tokens": 10,
    }


def _ledger() -> dict:
    return dispatch_telemetry.new_ledger(
        run_id="run", source_sha="a" * 40,
        design_fingerprint="design", plan_fingerprint="plan",
        started_at=1,
    )


def _screen(ledger: dict) -> dict:
    return dispatch_telemetry.screen_dispatch(
        ledger, FakeClock(wall_time=3), current_stage="build",
        outstanding_set_fingerprint="b" * 64,
        preserved_context_fingerprint="c" * 64,
    )


def test_h13_standalone_review_has_finite_default_token_ceiling(
        tmp_path, monkeypatch) -> None:
    budget = tp._standalone_review_budget(None)
    assert budget == {
        "max_cost_usd": 3.0,
        "max_cost_usd_mode": "advisory",
        "max_tokens": 25_000_000,
        "token_usage_required": True,
    }
    assert tp._standalone_review_budget(123)["max_tokens"] == 123
    with pytest.raises(ValueError, match="must be positive"):
        tp._standalone_review_budget(0)

    written = {}
    monkeypatch.setattr(tp.tp, "tp_dir", lambda _ws: str(tmp_path))
    monkeypatch.setattr(
        tp.tp, "load_json",
        lambda *_args, **_kwargs: {"budget": {"max_tokens": 321}})
    monkeypatch.setattr(
        tp.tp, "activate_review_contract_action",
        lambda *_args, **_kwargs: {"budget": {"max_actions": 40}})
    monkeypatch.setattr(
        tp.tp, "active_contract_path",
        lambda _ws, slot: str(tmp_path / f"{slot}.json"))
    monkeypatch.setattr(
        tp.tp, "atomic_write_json",
        lambda path, value, **_kwargs: written.update(
            {"path": path, "value": value}))
    producer = tp._activate_visible_review_bootstrap(
        str(tmp_path), action={}, expected={}, task_slot="review-slot")
    assert producer["budget"]["max_tokens"] == 321
    assert producer["budget"]["token_usage_required"] is True
    assert written["value"] == producer


def test_h27_screening_reuses_one_bounded_transcript_projection(
        tmp_path) -> None:
    transcript = tmp_path / "session.jsonl"
    first = json.dumps(_codex_usage_row("m1")) + "\n"
    transcript.write_text(first, encoding="utf-8")

    projection, checkpoint = dispatch_telemetry.project_transcript_usage(
        str(transcript), provider="codex")
    assert projection["status"] == "available"
    assert projection["bytes_read"] == len(first.encode())
    assert projection["byte_limit"] == 64 * 1024 * 1024
    assert projection["messages"] == 1
    assert checkpoint and checkpoint["offset"] == len(first.encode())

    second = json.dumps(_codex_usage_row("m2")) + "\n"
    with transcript.open("a", encoding="utf-8") as stream:
        stream.write(second)
    appended, next_checkpoint = dispatch_telemetry.project_transcript_usage(
        str(transcript), provider="codex", checkpoint=checkpoint)
    assert appended["status"] == "available"
    assert appended["bytes_read"] == len((first + second).encode())
    assert appended["messages"] == 2
    assert appended["usage"]["total_tokens"] == 24
    assert next_checkpoint and next_checkpoint["offset"] == \
        transcript.stat().st_size

    oversized = tmp_path / "oversized.jsonl"
    oversized.write_bytes(b"x" * 65)
    refused, refused_checkpoint = \
        dispatch_telemetry.project_transcript_usage(
            str(oversized), provider="codex", byte_limit=64)
    assert refused["status"] == "unavailable"
    assert "byte cap" in refused["reason"]
    assert refused_checkpoint is None


def test_h27_projection_resets_on_replacement_without_reusing_old_totals(
        tmp_path) -> None:
    transcript = tmp_path / "replace.jsonl"
    transcript.write_text(
        json.dumps(_codex_usage_row("old")) + "\n", encoding="utf-8")
    first, checkpoint = dispatch_telemetry.project_transcript_usage(
        str(transcript), provider="codex")
    assert first["messages"] == 1

    replacement = tmp_path / "replacement.tmp"
    replacement.write_text(
        json.dumps(_codex_usage_row("new", input_tokens=20,
                                    cached_tokens=5)) + "\n",
        encoding="utf-8")
    os.replace(replacement, transcript)
    current, _ = dispatch_telemetry.project_transcript_usage(
        str(transcript), provider="codex", checkpoint=checkpoint)
    assert current["status"] == "available"
    assert current["messages"] == 1
    assert current["usage"]["total_tokens"] == 22


def test_h27_checkpoint_binds_complete_consumed_prefix_on_inode_rewrite(
        tmp_path) -> None:
    transcript = tmp_path / "same-inode.jsonl"
    prefix = json.dumps({"padding": "x" * 5000}) + "\n"
    old = json.dumps(_codex_usage_row(
        "old", input_tokens=10, cached_tokens=1, output_tokens=2)) + "\n"
    transcript.write_text(prefix + old, encoding="utf-8")
    inode = transcript.stat().st_ino
    first, checkpoint = dispatch_telemetry.project_transcript_usage(
        str(transcript), provider="codex")
    assert first["usage"]["total_tokens"] == 12

    new = json.dumps(_codex_usage_row(
        "new", input_tokens=40, cached_tokens=5, output_tokens=3)) + "\n"
    with transcript.open("r+b") as stream:
        stream.truncate(0)
        stream.write((prefix + new).encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
    assert transcript.stat().st_ino == inode

    current, _ = dispatch_telemetry.project_transcript_usage(
        str(transcript), provider="codex", checkpoint=checkpoint)
    assert current["status"] == "available"
    assert current["messages"] == 1
    assert current["usage"]["total_tokens"] == 43


def test_h27_empty_checkpoint_recomputes_same_inode_regrowth(
        tmp_path) -> None:
    transcript = tmp_path / "empty.jsonl"
    transcript.write_bytes(b"")
    inode = transcript.stat().st_ino
    empty, checkpoint = dispatch_telemetry.project_transcript_usage(
        str(transcript), provider="codex")
    assert empty["status"] == "unavailable"
    assert checkpoint and checkpoint["offset"] == 0

    transcript.write_text(
        json.dumps(_codex_usage_row("new", input_tokens=30,
                                    cached_tokens=4)) + "\n",
        encoding="utf-8")
    assert transcript.stat().st_ino == inode
    current, _ = dispatch_telemetry.project_transcript_usage(
        str(transcript), provider="codex", checkpoint=checkpoint)
    assert current["status"] == "available"
    assert current["messages"] == 1
    assert current["usage"]["total_tokens"] == 32


def test_h28_review_scans_only_selected_session_with_byte_cap(
        tmp_path, monkeypatch) -> None:
    codex_root = tmp_path / "codex"
    sessions = codex_root / "sessions" / "2026" / "08" / "28"
    sessions.mkdir(parents=True)
    action_id = "action-1"
    run_id = "run-1"
    receipt_id = "approval-1"
    session_id = "01a0483d-ba00-7000-8000-000000000001"
    (codex_root / "session_index.jsonl").write_text(json.dumps({
        "id": session_id, "updated_at": "2026-08-28T12:00:00Z",
    }) + "\n", encoding="utf-8")
    prompt = review._review_action_prompt(run_id, action_id, "dynamic")
    selected = sessions / (
        "rollout-2026-08-28T08-00-00-" + session_id + ".jsonl")
    selected.write_text(json.dumps({
        "type": "response_item", "payload": {
            "type": "message", "id": receipt_id, "role": "user",
            "content": [{"type": "input_text", "text": prompt}],
        },
    }) + "\n", encoding="utf-8")
    unrelated = sessions / "rollout-newer-unrelated.jsonl"
    unrelated.write_text("not-json\n", encoding="utf-8")
    now = time.time_ns()
    os.utime(selected, ns=(now - 10, now - 10))
    os.utime(unrelated, ns=(now, now))
    monkeypatch.setattr(
        review, "_canonical_host_root",
        lambda host: str(codex_root if host == "codex" else
                         tmp_path / "absent"))

    original = review._host_review_records
    reads = []

    def counted(path: str) -> list[dict]:
        reads.append(path)
        return original(path)

    monkeypatch.setattr(review, "_host_review_records", counted)
    receipt = review._host_review_action_receipt(
        run_id=run_id, action_id=action_id, response="dynamic",
        receipt_ref=f"codex:{session_id}:{receipt_id}")
    assert receipt.receipt_id == receipt_id
    assert reads == [str(selected)]

    monkeypatch.setattr(review, "MAX_HOST_TRANSCRIPT_BYTES", 256)
    bounded = sessions / "rollout-bounded-session.jsonl"
    row = json.dumps({"tail": True}).encode() + b"\n"
    bounded.write_bytes((b"old\n" * 300) + row)
    assert review._host_review_records(str(bounded))[-1] == {"tail": True}


def test_h28_exact_session_lookup_never_walks_unmatched_history(
        tmp_path, monkeypatch) -> None:
    codex_root = tmp_path / "codex"
    session_id = "01a0483d-ba00-7000-8000-000000000001"
    selected = (codex_root / "sessions" / "2026" / "08" / "28" /
                ("rollout-2026-08-28T08-00-00-" + session_id + ".jsonl"))
    selected.parent.mkdir(parents=True)
    selected.write_text("{}\n", encoding="utf-8")
    for index in range(100):
        unrelated = codex_root / "sessions" / "2020" / f"{index:02d}"
        unrelated.mkdir(parents=True)
        (unrelated / f"rollout-unrelated-{index}.jsonl").write_text(
            "{}\n", encoding="utf-8")
    (codex_root / "session_index.jsonl").write_text(json.dumps({
        "id": session_id, "updated_at": "2026-08-28T12:00:00Z",
    }) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        review, "_canonical_host_root",
        lambda host: str(codex_root if host == "codex" else
                         tmp_path / "absent"))
    monkeypatch.setattr(
        review.glob, "iglob",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("historical transcript walk is forbidden")))

    assert review._host_review_transcripts(
        f"codex:{session_id}:receipt") == [("codex", str(selected))]


def test_h28_missing_or_oversized_session_index_is_structured_unavailable(
        tmp_path, monkeypatch) -> None:
    codex_root = tmp_path / "codex"
    codex_root.mkdir()
    session_id = "01a0483d-ba00-7000-8000-000000000001"
    monkeypatch.setattr(review, "_canonical_host_root", lambda _host: str(
        codex_root))
    with pytest.raises(review.HostTranscriptUnavailable) as missing:
        review._host_review_transcripts(
            f"codex:{session_id}:receipt")
    assert missing.value.detail["status"] == "unavailable"
    assert missing.value.detail["reason"] == "host session index is missing"

    (codex_root / "session_index.jsonl").write_bytes(
        b"x" * (review.MAX_HOST_SESSION_INDEX_BYTES + 1))
    with pytest.raises(review.HostTranscriptUnavailable) as oversized:
        review._host_review_transcripts(
            f"codex:{session_id}:receipt")
    assert "byte cap" in oversized.value.detail["reason"]


def test_h33_missing_host_usage_is_explicit_and_never_claims_enforcement(
        ) -> None:
    ledger = _ledger()
    first = _screen(ledger)
    assert first["dispatch_allowed"] is True
    assert first["budget"]["budget_claim"] is False
    assert first["budget"]["measurement_status"] == "unavailable"
    assert first["usage_capability"] == {
        "schema": "taskplane.host-usage-capability/v1",
        "status": "unavailable", "budget_claim": False,
        "enforcement": "not-enforced", "observed_tokens": None,
        "reason": "no host token totals have been observed",
    }

    dispatch_telemetry.bind_dispatch(ledger, _dispatch("active"))
    missing = _screen(ledger)
    assert missing["dispatch_allowed"] is False
    assert missing["usage_capability"]["budget_claim"] is False
    assert missing["usage_capability"]["observed_tokens"] is None

    dispatch_telemetry.observe_usage(
        ledger, dispatch_id="active", usage=_usage(),
        source_fingerprint="d" * 64)
    observed = _screen(ledger)
    assert observed["dispatch_allowed"] is True
    assert observed["budget"]["budget_claim"] is True
    assert observed["usage_capability"]["budget_claim"] is True
    assert observed["usage_capability"]["enforcement"] == "host-observed"
    assert observed["usage_capability"]["observed_tokens"] == 10


def test_h1e_process_tree_cleanup_keeps_both_absolute_deadlines() -> None:
    parameters = inspect.signature(
        review._terminate_validation_sandbox_process_tree).parameters
    assert "shared_deadline" in parameters
    assert "operation_deadline" in parameters
