"""Bounded transcript fixtures; these cannot certify fresh installed Claude recovery."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

import pytest

from taskplane import claude_worker_observations as observations
from taskplane.context import digest

pytestmark = pytest.mark.skipif(not observations.supported_reader(), reason="Requires native no-follow reads")


def encoded(row):
    return (json.dumps(row, separators=(",", ":")) + "\n").encode()


def setup_native(tmp_path, monkeypatch):
    home = tmp_path / "native-home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    parent = home / ".claude" / "projects" / "original-project" / "parent.jsonl"
    parent.parent.mkdir(parents=True)
    children = parent.with_suffix("") / "subagents"
    children.mkdir(parents=True)
    workspace = str(tmp_path / "different-worktree")
    stamp = lambda seconds: (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    arguments = {"description": "fixture", "prompt": "SECRET LAUNCH PROMPT", "run_in_background": True}
    attempt = {"call_id": "call-1", "workspace": workspace, "dispatch_digest": digest(arguments),
               "prepared_at": stamp(60), "grant_id": "grant-1", "attempt": 1, "run": "run-1"}
    call = {"sessionId": "parent", "cwd": workspace, "timestamp": stamp(40),
            "message": {"content": [{"type": "tool_use", "id": "call-1", "name": "Agent", "input": arguments}]}}
    result = {"sessionId": "parent", "cwd": workspace, "timestamp": stamp(30),
              "message": {"content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "SECRET RESULT"}]},
              "toolUseResult": {"agentId": "child-1", "isAsync": True, "status": "async_launched",
                                "outputFile": "/arbitrary/output/cannot/select/source"}}
    header = {"sessionId": "parent", "agentId": "child-1", "cwd": workspace,
              "isSidechain": True, "timestamp": stamp(35), "message": {"content": "SECRET HEADER PROMPT"}}
    parent.write_bytes(encoded(call) + encoded(result))
    (children / "agent-child-1.jsonl").write_bytes(encoded(header))
    event = {"hook_event_name": "PreToolUse", "session_id": "parent", "tool_use_id": "root-hook",
             "transcript_path": str(parent)}
    return parent, children, attempt, call, result, header, event


def select(event, **kwargs):
    answer = observations.select_source("parent", event, automatic=True, **kwargs)
    assert answer["status"] == "selected", answer
    return answer["source"]


def observe(attempt, source, cursor=None, **kwargs):
    return observations.observe("parent", attempt, {}, source=source, cursor=cursor, **kwargs)


def test_root_source_uses_original_project_with_worktree_cwd(tmp_path, monkeypatch):
    parent, children, attempt, call, result, header, event = setup_native(tmp_path, monkeypatch)
    source = select(event)
    answer = observe(attempt, source)
    assert answer["status"] == "matched"
    assert answer["worker_id"] == "child-1"
    assert answer["record_sha256"] == {"call": digest(call), "result": digest(result), "header": digest(header)}
    assert len(answer["references"]) == 2
    assert {ref["source"] for ref in answer["references"]} == {str(parent), str(children / "agent-child-1.jsonl")}
    payload = json.dumps(answer)
    assert "SECRET" not in payload and "arbitrary/output" not in payload
    assert len(payload) < 15000
    # Native metadata provenance is separate from admitted execution identity.
    changed = deepcopy(attempt); changed["workspace"] += "-foreign"
    assert observe(changed, source)["status"] == "conflict"
    assert observations.correlate_records("parent", attempt, [call, result], [header])["evidence_sha256"] == answer["evidence_sha256"]


def test_unpinned_worker_cannot_select_or_override_source(tmp_path, monkeypatch):
    parent, _, attempt, _, _, _, event = setup_native(tmp_path, monkeypatch)
    assert observations.observe("parent", attempt, event)["status"] == "not_yet_available"
    assert observations.select_source("parent", event)["status"] == "conflict"
    worker_event = {**event, "agent_id": "child-1"}
    assert observations.select_source("parent", worker_event, automatic=True)["status"] == "conflict"
    source = select(event)
    worker_event["transcript_path"] = "/some/child/transcript"
    assert observations.observe("parent", attempt, worker_event, source=source)["status"] == "matched"
    refused = observations.observe("parent", attempt, {**event, "transcript_path": "/foreign/parent.jsonl"}, source=source)
    assert refused["status"] == "conflict"
    assert observe(attempt, source, refused["cursor"])["status"] == "conflict"
    other = parent.parent.parent / "other-project" / "parent.jsonl"
    other.parent.mkdir(); other.write_bytes(parent.read_bytes())
    assert observations.select_source("parent", {**event, "transcript_path": str(other)}, automatic=True, previous=source)["status"] == "conflict"


@pytest.mark.parametrize("defect", ["outside", "relative", "traversal", "double_slash", "wrong_parent", "foreign_session", "sidechain"])
def test_source_selection_refuses_untrusted_paths_and_lineage(tmp_path, monkeypatch, defect):
    parent, _, _, call, result, _, event = setup_native(tmp_path, monkeypatch)
    if defect == "outside": event["transcript_path"] = str(tmp_path / "parent.jsonl")
    if defect == "relative": event["transcript_path"] = "parent.jsonl"
    if defect == "traversal": event["transcript_path"] = str(parent.parent) + "/../original-project/parent.jsonl"
    if defect == "double_slash": event["transcript_path"] = str(parent.parent) + "//parent.jsonl"
    if defect == "wrong_parent": event["transcript_path"] = str(parent.with_name("foreign.jsonl"))
    if defect == "foreign_session": call["sessionId"] = "foreign"; parent.write_bytes(encoded(call) + encoded(result))
    if defect == "sidechain": call["isSidechain"] = True; parent.write_bytes(encoded(call) + encoded(result))
    assert observations.select_source("parent", event, automatic=True)["status"] == "conflict"


@pytest.mark.parametrize("defect", ["leaf_symlink", "ancestor_symlink", "fifo", "directory"])
def test_selection_never_follows_unsafe_native_paths(tmp_path, monkeypatch, defect):
    parent, _, _, _, _, _, event = setup_native(tmp_path, monkeypatch)
    if defect == "ancestor_symlink":
        project = parent.parent
        saved = project.with_name("saved-project"); project.rename(saved); project.symlink_to(saved, target_is_directory=True)
    else:
        saved = parent.with_name("saved.jsonl"); parent.rename(saved)
        if defect == "leaf_symlink": parent.symlink_to(saved)
        if defect == "fifo": os.mkfifo(parent)
        if defect == "directory": parent.mkdir()
    assert observations.select_source("parent", event, automatic=True)["status"] == "conflict"


def test_launch_older_than_four_mib_survives_restart_and_append(tmp_path, monkeypatch):
    parent, _, attempt, call, result, _, event = setup_native(tmp_path, monkeypatch)
    filler = encoded({"type": "progress", "data": "x" * 8192})
    parent.write_bytes(encoded(call) + encoded(result) + filler * 600)
    source = select(event)
    budget = observations.ReadBudget()
    first = observe(attempt, source, budget=budget)
    assert first["status"] == "not_yet_available"
    assert 0 < first["cursor"]["offset"] < parent.stat().st_size
    assert first["cursor"]["entries"] and budget.bytes_read <= observations.MAX_BYTES
    # JSON roundtrip simulates a different hook process loading controller state.
    second = observe(attempt, source, json.loads(json.dumps(first["cursor"])))
    assert second["status"] == "matched"
    proof = second["proof"]; evidence = second["evidence_sha256"]
    with parent.open("ab") as stream: stream.write(filler * 650)
    third = observe(attempt, source, second["cursor"])
    assert third["status"] == "not_yet_available" and third["proof"] == proof
    fourth = observe(attempt, source, third["cursor"])
    assert fourth["status"] == "matched" and fourth["evidence_sha256"] == evidence
    assert fourth["proof"] == proof


def test_delayed_result_and_partial_header_resume_exact_newline(tmp_path, monkeypatch):
    parent, children, attempt, call, result, header, event = setup_native(tmp_path, monkeypatch)
    parent.write_bytes(encoded(call))
    child = children / "agent-child-1.jsonl"; child.unlink()
    source = select(event)
    first = observe(attempt, source)
    assert first["status"] == "not_yet_available"
    partial = encoded(result)[:-8]
    with parent.open("ab") as stream: stream.write(partial)
    second = observe(attempt, source, first["cursor"])
    assert second["cursor"]["offset"] == len(encoded(call))
    with parent.open("ab") as stream: stream.write(encoded(result)[-8:])
    child.write_bytes(encoded(header)[:-1])
    third = observe(attempt, source, second["cursor"])
    assert third["status"] == "not_yet_available"
    with child.open("ab") as stream: stream.write(b"\n")
    fourth = observe(attempt, source, third["cursor"])
    assert fourth["status"] == "matched"


def test_competing_record_after_budget_boundary_prevents_premature_match(tmp_path, monkeypatch):
    parent, _, attempt, call, result, _, event = setup_native(tmp_path, monkeypatch)
    competing = deepcopy(result); competing["toolUseResult"]["agentId"] = "other-child"
    parent.write_bytes(encoded(call) + encoded(result) + encoded({"progress": "x" * 8192}) * 550 + encoded(competing))
    source = select(event)
    first = observe(attempt, source)
    assert first["status"] == "not_yet_available"
    second = observe(attempt, source, first["cursor"])
    assert second["status"] == "conflict"
    assert observe(attempt, source, second["cursor"])["status"] == "conflict"


@pytest.mark.parametrize("defect", ["call", "result", "header", "replacement", "truncation", "child_symlink", "project_replacement", "cursor", "projection"])
def test_retained_evidence_changes_are_sticky(tmp_path, monkeypatch, defect):
    parent, children, attempt, call, result, header, event = setup_native(tmp_path, monkeypatch)
    source = select(event)
    first = observe(attempt, source)
    assert first["status"] == "matched"
    cursor = deepcopy(first["cursor"])
    if defect == "call":
        call["message"]["content"][0]["input"]["prompt"] = "CHANGED SECRET TEXT"; parent.write_bytes(encoded(call) + encoded(result))
    if defect == "result":
        result["toolUseResult"]["agentId"] = "child-2"; parent.write_bytes(encoded(call) + encoded(result))
    if defect == "header":
        header["cwd"] += "x"; (children / "agent-child-1.jsonl").write_bytes(encoded(header))
    if defect == "replacement":
        moved = parent.with_suffix(".old"); parent.rename(moved); parent.write_bytes(moved.read_bytes())
    if defect == "truncation": parent.write_bytes(encoded(call))
    if defect == "child_symlink":
        child = children / "agent-child-1.jsonl"; child.unlink(); child.symlink_to(parent)
    if defect == "project_replacement":
        old = parent.parent.with_name("old-project"); parent.parent.rename(old); parent.parent.mkdir()
        parent.write_bytes((old / parent.name).read_bytes())
    if defect == "cursor": cursor["offset"] += 1
    if defect == "projection":
        next(iter(cursor["entries"].values()))["result"][0]["projection"]["child"] = "child-2"
        # Even a recomputed local checksum cannot replace a retained projection's
        # native span. Checksums are integrity checks, never authentication.
        cursor = observations._sealed(cursor)
    second = observe(attempt, source, cursor)
    assert second["status"] == "conflict", second
    assert observe(attempt, source, second["cursor"])["status"] == "conflict"


def test_missing_original_source_retains_proof_but_cannot_refresh_match(tmp_path, monkeypatch):
    parent, _, attempt, _, _, _, event = setup_native(tmp_path, monkeypatch)
    source = select(event); first = observe(attempt, source)
    saved = parent.with_suffix(".saved"); parent.rename(saved)
    second = observe(attempt, source, first["cursor"])
    assert second["status"] == "not_yet_available" and second["proof"] == first["proof"]
    saved.rename(parent)
    assert observe(attempt, source, second["cursor"])["status"] == "matched"


def test_identical_duplicates_are_idempotent_and_appended_conflict_is_sticky(tmp_path, monkeypatch):
    parent, _, attempt, call, result, _, event = setup_native(tmp_path, monkeypatch)
    source = select(event); first = observe(attempt, source)
    with parent.open("ab") as stream: stream.write(encoded(call) + encoded(result))
    second = observe(attempt, source, first["cursor"])
    assert second["status"] == "matched" and first["evidence_sha256"] == second["evidence_sha256"]
    conflicting = deepcopy(call); conflicting["message"]["content"][0]["input"]["prompt"] = "other"
    with parent.open("ab") as stream: stream.write(encoded(conflicting))
    third = observe(attempt, source, second["cursor"])
    assert third["status"] == "conflict"
    assert all(len(value) <= 2 for key, value in next(iter(third["cursor"]["entries"].values())).items() if key in {"call", "result", "header"})


def test_record_limit_resumes_and_oversized_record_refuses(tmp_path, monkeypatch):
    parent, _, attempt, call, result, _, event = setup_native(tmp_path, monkeypatch)
    parent.write_bytes(encoded(call) + encoded({"progress": 1}) * observations.MAX_RECORDS + encoded(result))
    source = select(event)
    first = observe(attempt, source)
    assert first["status"] == "not_yet_available" and first["budget"]["records"] == observations.MAX_RECORDS
    assert observe(attempt, source, first["cursor"])["status"] == "matched"
    with parent.open("ab") as stream: stream.write(b"x" * (observations.MAX_LINE_BYTES + 1))
    assert observe(attempt, source, first["cursor"])["status"] == "conflict"


def test_admission_watermark_refuses_preexisting_matching_launch(tmp_path, monkeypatch):
    _, _, attempt, _, _, _, event = setup_native(tmp_path, monkeypatch)
    source = select(event)
    attempt["transcript_admission_offset"] = source["selected_size"]
    assert observe(attempt, source)["status"] == "conflict"


def test_shared_budget_limits_all_attempts_and_source_selection(tmp_path, monkeypatch):
    _, _, attempt, _, _, _, event = setup_native(tmp_path, monkeypatch)
    budget = observations.ReadBudget(max_bytes=2048, max_records=12)
    source = select(event, budget=budget)
    first = observe(attempt, source, budget=budget)
    second = observe(attempt, source, first["cursor"], budget=budget)
    assert second["status"] == "not_yet_available"
    assert budget.bytes_read <= 2048 and budget.records_read <= 12
    assert second["budget"] == budget.report()


def test_many_candidates_share_scan_and_keep_separate_evidence(tmp_path, monkeypatch):
    parent, children, attempt, call, result, header, event = setup_native(tmp_path, monkeypatch)
    second = {**attempt, "call_id": "call-2", "grant_id": "grant-2"}
    call2 = deepcopy(call); call2["message"]["content"][0]["id"] = "call-2"
    result2 = deepcopy(result); result2["message"]["content"][0]["tool_use_id"] = "call-2"; result2["toolUseResult"]["agentId"] = "child-2"
    header2 = {**header, "agentId": "child-2"}
    parent.write_bytes(encoded(call2) + encoded(call) + encoded(result) + encoded(result2))
    (children / "agent-child-2.jsonl").write_bytes(encoded(header2))
    source = select(event)
    answer = observations.observe_many("parent", [attempt, second], {}, source=source)
    assert [row["worker_id"] for row in answer["observations"]] == ["child-1", "child-2"]
    # Parent payload is scanned once, plus exactly the selected session and two headers.
    expected = parent.stat().st_size + source["session_reference"]["bytes"] + len(encoded(header)) + len(encoded(header2))
    assert answer["budget"]["bytes"] == expected
    # Querying only one registered candidate must still notice the other's conflict.
    changed = deepcopy(result2); changed["toolUseResult"]["agentId"] = "other-child"
    with parent.open("ab") as stream: stream.write(encoded(changed))
    one = observations.observe_many("parent", [attempt], {}, source=source, cursor=answer["cursor"])
    assert one["observations"][0]["status"] == "matched"
    two = observations.observe_many("parent", [second], {}, source=source, cursor=one["cursor"])
    assert two["observations"][0]["status"] == "conflict"


def test_invalid_source_checksum_and_no_follow_capability_refuse(tmp_path, monkeypatch):
    _, _, attempt, _, _, _, event = setup_native(tmp_path, monkeypatch)
    source = select(event); source["selected_size"] += 1
    assert observe(attempt, source)["status"] == "conflict"
    monkeypatch.setattr(observations, "supported_reader", lambda: False)
    assert observe(attempt, source)["status"] == "unsupported"
    assert observations.select_source("parent", event, automatic=True)["status"] == "unsupported"


@pytest.mark.parametrize("defect", ["entries", "missing_role", "reference", "missing_projection"])
def test_malformed_serialized_cursor_refuses_without_crashing(tmp_path, monkeypatch, defect):
    _, _, attempt, _, _, _, event = setup_native(tmp_path, monkeypatch)
    source = select(event); first = observe(attempt, source)
    cursor = deepcopy(first["cursor"])
    entry = next(iter(cursor["entries"].values()))
    if defect == "entries": cursor["entries"] = []
    if defect == "missing_role": entry.pop("header")
    if defect == "reference": entry["call"][0]["reference"]["bytes"] = "invalid"
    if defect == "missing_projection": entry["call"][0].pop("projection")
    result = observe(attempt, source, observations._sealed(cursor))
    assert result["status"] == "conflict"
    assert observe(attempt, source, result["cursor"])["status"] == "conflict"


def test_child_cannot_be_read_from_a_replaced_project_during_scan(tmp_path, monkeypatch):
    parent, _, attempt, _, _, header, event = setup_native(tmp_path, monkeypatch)
    source = select(event)
    original = observations._open_child
    def replace_before_child(path, project_identity):
        old = parent.parent.with_name("moved-project")
        parent.parent.rename(old)
        new_child = parent.with_suffix("") / "subagents" / "agent-child-1.jsonl"
        new_child.parent.mkdir(parents=True)
        new_child.write_bytes(encoded(header))
        return original(path, project_identity)
    monkeypatch.setattr(observations, "_open_child", replace_before_child)
    assert observe(attempt, source)["status"] == "conflict"


def test_changed_source_reference_cannot_expand_parent_reads(tmp_path, monkeypatch):
    _, _, attempt, _, _, _, event = setup_native(tmp_path, monkeypatch)
    source = select(event)
    source["session_reference"]["source"] = "/foreign.jsonl"
    assert observe(attempt, observations._sealed(source))["status"] == "conflict"


def test_repeated_root_selection_preserves_descriptor_and_refreshes_admission_watermark(tmp_path, monkeypatch):
    parent, _, _, _, _, _, event = setup_native(tmp_path, monkeypatch)
    source = select(event)
    with parent.open("ab") as stream: stream.write(encoded({"progress": 1}))
    selected = observations.select_source("parent", event, automatic=True, previous=source)
    assert selected["source"] == source
    assert selected["watermark"] == parent.stat().st_size > source["selected_size"]


def test_pending_candidates_rotate_when_revalidation_budget_is_shared(tmp_path, monkeypatch):
    parent, children, attempt, call, result, header, event = setup_native(tmp_path, monkeypatch)
    attempts = []
    rows = []
    for index in range(3):
        key = str(index)
        attempts.append({**attempt, "call_id": "call-" + key, "grant_id": "grant-" + key})
        invoked = deepcopy(call); invoked["message"]["content"][0]["id"] = "call-" + key
        returned = deepcopy(result); returned["message"]["content"][0]["tool_use_id"] = "call-" + key
        returned["toolUseResult"]["agentId"] = "child-" + key
        (children / ("agent-child-" + key + ".jsonl")).write_bytes(encoded({**header, "agentId": "child-" + key}))
        rows.extend([invoked, returned])
    parent.write_bytes(b"".join(encoded(row) for row in rows))
    source = select(event)
    answer = observations.observe_many("parent", attempts, {}, source=source)
    assert all(row["status"] == "matched" for row in answer["observations"])
    entries = list(answer["cursor"]["entries"].values())
    proof_bytes = max(sum(value["reference"]["bytes"] for role in ("call", "result", "header") for value in entry[role]) for entry in entries)
    allowance = source["session_reference"]["bytes"] + proof_bytes
    seen = set()
    for _ in range(3):
        budget = observations.ReadBudget(max_bytes=allowance)
        answer = observations.observe_many("parent", attempts, {}, source=source, cursor=answer["cursor"], budget=budget)
        assert budget.bytes_read <= allowance
        seen.update(row["worker_id"] for row in answer["observations"] if row["status"] == "matched")
    assert seen == {"child-0", "child-1", "child-2"}


@pytest.mark.parametrize("defect", ["tool", "arguments", "result_parent", "result_workspace", "root_child", "error",
    "header_parent", "header_workspace", "header_sidechain", "header_child", "future", "naive", "backwards"])
def test_incremental_parser_preserves_exact_correlation_refusals(tmp_path, monkeypatch, defect):
    parent, children, attempt, call, result, header, event = setup_native(tmp_path, monkeypatch)
    if defect == "tool": call["message"]["content"][0]["name"] = "Bash"
    if defect == "arguments": call["message"]["content"][0]["input"]["prompt"] = "changed"
    if defect == "result_parent": result["sessionId"] = "foreign"
    if defect == "result_workspace": result["cwd"] += "-foreign"
    if defect == "root_child": result["toolUseResult"]["agentId"] = "parent"
    if defect == "error": result["message"]["content"][0]["is_error"] = True
    if defect == "header_parent": header["sessionId"] = "foreign"
    if defect == "header_workspace": header["cwd"] += "-foreign"
    if defect == "header_sidechain": header["isSidechain"] = False
    if defect == "header_child": header["agentId"] = "foreign"
    if defect == "future": result["timestamp"] = "2999-01-01T00:00:00+00:00"
    if defect == "naive": call["timestamp"] = "2020-01-01T00:00:00"
    if defect == "backwards": result["timestamp"] = attempt["prepared_at"]
    parent.write_bytes(encoded(call) + encoded(result))
    (children / "agent-child-1.jsonl").write_bytes(encoded(header))
    source = select(event)
    answer = observe(attempt, source)
    assert answer["status"] == "conflict", defect
    assert observe(attempt, source, answer["cursor"])["status"] == "conflict"


def test_learning_exact_worker_identity_does_not_discard_cursor_or_proof(tmp_path, monkeypatch):
    _, _, attempt, _, _, _, event = setup_native(tmp_path, monkeypatch)
    source = select(event); first = observe(attempt, source)
    attempt["worker_id"] = "child-1"
    second = observe(attempt, source, first["cursor"])
    assert second["status"] == "matched" and second["proof"] == first["proof"]
    assert len(second["cursor"]["entries"]) == 1
    attempt["worker_id"] = "other-child"
    assert observe(attempt, source, second["cursor"])["status"] == "conflict"
