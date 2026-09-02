"""R-0002 H2-C: review cost, transcript, and usage-truth bounds."""

from __future__ import annotations

import datetime
import inspect
import json
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


def _codex_rollout_path(root, session_id):
    first, second, *_ = session_id.split("-")
    instant = datetime.datetime.fromtimestamp(int(first + second, 16) / 1000)
    return (root / "sessions" / instant.strftime("%Y") /
            instant.strftime("%m") / instant.strftime("%d") /
            (f"rollout-{instant.strftime('%Y-%m-%dT%H-%M-%S')}-"
             f"{session_id}.jsonl"))


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


def test_h28_review_scans_only_selected_session_with_byte_cap(
        tmp_path, monkeypatch) -> None:
    codex_root = tmp_path / "codex"
    action_id = "action-1"
    run_id = "run-1"
    receipt_id = "approval-1"
    session_id = "01a0483d-ba00-7000-8000-000000000001"
    codex_root.mkdir()
    (codex_root / "session_index.jsonl").write_text(json.dumps({
        "id": session_id, "updated_at": "2026-08-28T12:00:00Z",
    }) + "\n", encoding="utf-8")
    prompt = review._review_action_prompt(run_id, action_id, "dynamic")
    selected = _codex_rollout_path(codex_root, session_id)
    selected.parent.mkdir(parents=True)
    selected.write_text(json.dumps({
        "type": "response_item", "payload": {
            "type": "message", "id": receipt_id, "role": "user",
            "content": [{"type": "input_text", "text": prompt}],
        },
    }) + "\n", encoding="utf-8")
    unrelated = selected.parent / "rollout-newer-unrelated.jsonl"
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
    bounded = selected.parent / "rollout-bounded-session.jsonl"
    row = json.dumps({"tail": True}).encode() + b"\n"
    bounded.write_bytes((b"old\n" * 300) + row)
    assert review._host_review_records(str(bounded))[-1] == {"tail": True}


def test_h28_exact_session_lookup_never_walks_unmatched_history(
        tmp_path, monkeypatch) -> None:
    codex_root = tmp_path / "codex"
    session_id = "01a0483d-ba00-7000-8000-000000000001"
    selected = _codex_rollout_path(codex_root, session_id)
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


def test_h28_session_index_rejects_symlink_hardlink_and_path_replacement(
        tmp_path, monkeypatch) -> None:
    root = tmp_path / "codex"
    root.mkdir()
    source = tmp_path / "external.jsonl"
    source.write_text("{}\n", encoding="utf-8")
    symlink = root / "session_index.jsonl"
    symlink.symlink_to(source)
    with pytest.raises(review.HostTranscriptUnavailable, match="canonical"):
        review._host_session_index(str(symlink), root=str(root))

    symlink.unlink()
    os.link(source, symlink)
    with pytest.raises(review.HostTranscriptUnavailable, match="unique"):
        review._host_session_index(str(symlink), root=str(root))

    symlink.unlink()
    symlink.write_text("{}\n", encoding="utf-8")
    replacement = root / "replacement.jsonl"
    replacement.write_text("{}\n", encoding="utf-8")
    original_read = review.os.read
    replaced = False

    def replace_during_read(descriptor: int, amount: int) -> bytes:
        nonlocal replaced
        chunk = original_read(descriptor, amount)
        if not replaced:
            replaced = True
            os.replace(replacement, symlink)
        return chunk

    monkeypatch.setattr(review.os, "read", replace_during_read)
    with pytest.raises(review.HostTranscriptUnavailable, match="changed"):
        review._host_session_index(str(symlink), root=str(root))


def test_h28_session_index_rejects_same_length_concurrent_mutation(
        tmp_path, monkeypatch) -> None:
    root = tmp_path / "codex"
    root.mkdir()
    index = root / "session_index.jsonl"
    index.write_bytes(b"{\"a\":1}\n")
    original_read = review.os.read
    mutated = False

    def mutate_during_read(descriptor: int, amount: int) -> bytes:
        nonlocal mutated
        chunk = original_read(descriptor, amount)
        if not mutated:
            mutated = True
            with index.open("r+b") as stream:
                stream.write(b"{\"b\":2}\n")
                stream.flush()
                os.fsync(stream.fileno())
        return chunk

    monkeypatch.setattr(review.os, "read", mutate_during_read)
    with pytest.raises(review.HostTranscriptUnavailable, match="changed"):
        review._host_session_index(str(index), root=str(root))


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
