"""Bounded transcript fixtures; these cannot certify fresh installed Claude recovery."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from html import escape
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


@pytest.mark.parametrize('replay', [False, True])
def test_preparation_watermark_refuses_preexisting_or_replayed_launch(tmp_path, monkeypatch, replay):
    parent, _, attempt, call, result, _, event = setup_native(tmp_path, monkeypatch)
    source = select(event)
    attempt["transcript_admission_offset"] = source["selected_size"]
    if replay:
        with parent.open('ab') as stream: stream.write(encoded(call) + encoded(result))
    assert observe(attempt, source)["status"] == "conflict"


def test_launch_appended_after_boundary_still_requires_post_preparation_timestamp(tmp_path, monkeypatch):
    parent, _, attempt, call, result, _, event = setup_native(tmp_path, monkeypatch)
    prefix = encoded({'sessionId':'parent', 'cwd':attempt['workspace'], 'type':'user'})
    parent.write_bytes(prefix)
    source = select(event)
    attempt['transcript_admission_offset'] = len(prefix)
    call['timestamp'] = (datetime.fromisoformat(attempt['prepared_at']) - timedelta(seconds=1)).isoformat()
    with parent.open('ab') as stream: stream.write(encoded(call) + encoded(result))
    answer = observe(attempt, source)
    assert answer['status'] == 'conflict'
    assert answer['reason'] == 'Native timestamps are missing, future or out of order'


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



def notification_record(attempt, result, body='The actual final answer', **extra):
    child = result['toolUseResult']['agentId']
    output = result['toolUseResult']['outputFile']
    content = (f'<task-notification>\n<task-id>{child}</task-id>\n'
               f'<tool-use-id>{attempt["call_id"]}</tool-use-id>\n<output-file>{output}</output-file>\n'
               '<status>completed</status>\n<summary>Agent finished</summary>\n'
               '<note>Each stop can notify again.</note>\n'
               f'<result>{escape(body, quote=False)}</result>\n<usage><tool_uses>2</tool_uses></usage>\n</task-notification>')
    return dict(type='user', sessionId='parent', cwd=attempt['workspace'], isSidechain=False,
                timestamp=datetime.now(timezone.utc).isoformat(),
                origin={'kind':'task-notification', 'producer':'session-task'}, promptSource='system',
                turnOrigin='task_notification', message={'role':'user', 'content': content}, **extra)


def attachment_notification_record(note):
    """Claude 2.1.289's observed mid-turn session-task delivery envelope."""
    return {key: note[key] for key in ('sessionId', 'cwd', 'isSidechain', 'timestamp')} | {
        'type': 'attachment', 'renderedRole': 'system',
        'uuid': 'd9b5aafa-bc18-423f-b3b3-942d70f8e1d7',
        'attachment': {
            'type': 'queued_command', 'commandMode': 'task-notification',
            'origin': deepcopy(note['origin']), 'prompt': note['message']['content'],
            'source_uuid': '0ab418a7-9368-408e-ab0e-e78ac771f2b7',
            'delivery_id': 'dfcc0737-40d2-49d0-aa6e-41bae3aa95a5',
            'timestamp': note['timestamp'],
        },
        # Display wrappers are not authoritative content, even when they contain XML.
        'rendered': [{'content': '<system-reminder>Display only</system-reminder>'}],
        'renderedInHumanTurn': [{'content': '<system-reminder>Display only</system-reminder>'}],
    }


def transcript_notification_record(note):
    """Exact transcript-only system shape observed in Claude 2.1.290, line 439."""
    note = deepcopy(note)
    note.pop('turnOrigin')
    return {**note, 'queueSkipAttachments': True, 'queueTranscriptOnly': True,
            'uuid': '8da9cff0-db55-499c-8d9e-78caa4ac4910',
            'promptId': 'c0db83a5-3beb-46c2-be6d-0bcf1851d102', 'permissionMode': 'auto',
            'userType': 'external', 'entrypoint': 'cli', 'version': '2.1.290'}


@pytest.mark.parametrize('defect', [None, 'origin', 'producer', 'origin-extra', 'origin-missing',
    'prompt-source', 'turn-origin', 'skip-missing', 'skip-false', 'skip-string', 'skip-int',
    'transcript-missing', 'transcript-false', 'transcript-string', 'transcript-int',
    'uuid-missing', 'uuid-invalid', 'prompt-id-missing', 'prompt-id-invalid',
    'role', 'type', 'content-list', 'rendered-only', 'batch', 'nested-notification',
    'child', 'call', 'output', 'session', 'workspace', 'sidechain', 'sidechain-missing',
    'future', 'before-launch', 'naive', 'duplicate', 'conflicting-delivery', 'changed-span'])
def test_transcript_only_notification_requires_exact_native_provenance(tmp_path, monkeypatch, defect):
    parent, _, attempt, _, result, _, event = setup_native(tmp_path, monkeypatch)
    source = select(event)
    first = observe(attempt, source)
    body = observations.handback_redirect('child-1')
    note = transcript_notification_record(notification_record(attempt, result, body))
    if defect == 'origin': note['origin']['kind'] = 'user'
    if defect == 'producer': note['origin']['producer'] = 'other'
    if defect == 'origin-extra': note['origin']['claimed'] = True
    if defect == 'origin-missing': note.pop('origin')
    if defect == 'prompt-source': note['promptSource'] = 'user'
    if defect == 'turn-origin': note['turnOrigin'] = 'human'
    for prefix, key in [('skip', 'queueSkipAttachments'), ('transcript', 'queueTranscriptOnly')]:
        if defect == prefix + '-missing': note.pop(key)
        if defect == prefix + '-false': note[key] = False
        if defect == prefix + '-string': note[key] = 'true'
        if defect == prefix + '-int': note[key] = 1
    for prefix, key in [('uuid', 'uuid'), ('prompt-id', 'promptId')]:
        if defect == prefix + '-missing': note.pop(key)
        if defect == prefix + '-invalid': note[key] = 'native-looking-id'
    if defect == 'role': note['message']['role'] = 'assistant'
    if defect == 'type': note['type'] = 'queue-operation'
    if defect == 'content-list': note['message']['content'] = [note['message']['content']]
    if defect == 'rendered-only': note['rendered'] = note.pop('message')
    if defect == 'batch': note['message']['content'] += '\n' + note['message']['content']
    if defect == 'nested-notification': note['message']['content'] = note['message']['content'].replace('<result>', '<result><task-notification>')
    if defect == 'child': note['message']['content'] = note['message']['content'].replace('<task-id>child-1', '<task-id>other')
    if defect == 'call': note['message']['content'] = note['message']['content'].replace('<tool-use-id>call-1', '<tool-use-id>other')
    if defect == 'output': note['message']['content'] = note['message']['content'].replace('<output-file>/', '<output-file>/other/')
    if defect == 'session': note['sessionId'] = 'foreign'
    if defect == 'workspace': note['cwd'] += '-foreign'
    if defect == 'sidechain': note['isSidechain'] = True
    if defect == 'sidechain-missing': note.pop('isSidechain')
    if defect == 'future': note['timestamp'] = '2999-01-01T00:00:00Z'
    if defect == 'before-launch': note['timestamp'] = attempt['prepared_at']
    if defect == 'naive': note['timestamp'] = '2020-01-01T00:00:00'
    with parent.open('ab') as stream:
        stream.write(encoded(note))
        if defect == 'duplicate': stream.write(encoded(note))
        if defect == 'conflicting-delivery':
            stream.write(encoded(transcript_notification_record(notification_record(attempt, result, 'Other report'))))
    answer = observe(attempt, source, first['cursor'])
    if defect in {None, 'duplicate', 'changed-span'}:
        assert answer['status'] == 'matched' and answer['completion']['handback_redirect'] is True
        assert answer['completion']['body_digest'] == digest(body)
        assert answer['completion']['record_sha256'] == digest(note)
        assert len(next(iter(answer['cursor']['entries'].values()))['notification']) == 1
        assert body not in json.dumps(answer)
        if defect == 'changed-span':
            parent.write_bytes(parent.read_bytes().replace(b'Agent finished', b'Agent replaced'))
            assert observe(attempt, source, answer['cursor'])['status'] == 'conflict'
        else:
            assert observe(attempt, source, answer['cursor'])['completion'] == answer['completion']
    elif defect in {'child', 'output', 'session', 'workspace', 'sidechain', 'sidechain-missing',
                    'future', 'before-launch', 'naive', 'conflicting-delivery'}:
        assert answer['status'] == 'conflict' and 'completion' not in answer
    else:
        assert answer['status'] == 'matched' and 'completion' not in answer


def test_transcript_notification_cursor_upgrade_rescans_once_and_revalidates(tmp_path, monkeypatch):
    parent, _, attempt, _, result, _, event = setup_native(tmp_path, monkeypatch)
    source = select(event)
    note = transcript_notification_record(notification_record(attempt, result))
    with parent.open('ab') as stream: stream.write(encoded(note))
    observed = observe(attempt, source)
    cursor = deepcopy(observed['cursor'])
    cursor.pop('notification_version', None)
    for entry in cursor['entries'].values(): entry['notification'] = []
    old = observations._sealed(cursor)
    assert old['offset'] == parent.stat().st_size
    migrated = observe(attempt, source, old)
    assert migrated['status'] == 'matched' and migrated['completion']['record_sha256'] == digest(note)
    assert migrated['evidence_sha256'] == observed['evidence_sha256']
    assert migrated['cursor']['notification_version'] == 2
    scans = []
    original = observations._scan
    def scan(fd, path, start, end, budget, retain):
        scans.append((start, end))
        return original(fd, path, start, end, budget, retain)
    monkeypatch.setattr(observations, '_scan', scan)
    replay = observe(attempt, source, migrated['cursor'])
    assert replay['completion'] == migrated['completion']
    assert all(start == end for start, end in scans)
    parent.write_bytes(parent.read_bytes().replace(b'SECRET RESULT', b'EDITED RESULT'))
    assert observe(attempt, source, old)['status'] == 'conflict'


@pytest.mark.parametrize('variant', ['user', 'transcript-only', 'attachment'])
@pytest.mark.parametrize('escaped', [False, True])
def test_direct_notification_preserves_literal_and_escaped_report_text(tmp_path, monkeypatch, variant, escaped):
    parent, _, attempt, _, result, _, event = setup_native(tmp_path, monkeypatch)
    source = select(event)
    body = 'Report <XML> x <= 2 & literal &lt; "quotes" and <result>text</result>.\n'
    note = notification_record(attempt, result, body)
    if not escaped:
        note['message']['content'] = note['message']['content'].replace(escape(body, quote=False), body)
    if variant == 'transcript-only': note = transcript_notification_record(note)
    if variant == 'attachment': note = attachment_notification_record(note)
    with parent.open('ab') as stream: stream.write(encoded(note))
    answer = observe(attempt, source)
    assert answer['status'] == 'matched'
    assert answer['completion']['body_digest'] == digest(escape(body, quote=False) if escaped else body)
    assert not answer['completion'].get('handback_redirect')
    assert observe(attempt, source, answer['cursor'])['completion'] == answer['completion']


@pytest.mark.parametrize('defect', [None, 'row-type', 'attachment-type', 'attachment-object',
    'mode', 'origin', 'producer', 'origin-extra', 'origin-missing', 'role', 'role-missing',
    'source-missing', 'source-invalid', 'source-type', 'delivery-missing', 'delivery-invalid',
    'delivery-type', 'timestamp-missing', 'timestamp-mismatch', 'timestamp-invalid',
    'timestamp-naive', 'timestamp-future', 'timestamp-before-launch', 'plain-text',
    'prompt-missing', 'prompt-type', 'rendered-only', 'user-text', 'enqueue', 'remove',
    'child', 'call', 'workspace', 'session', 'sidechain', 'sidechain-missing', 'output',
    'body-change', 'id-change', 'duplicate', 'conflicting-delivery'])
def test_midturn_attachment_requires_native_delivery_provenance(tmp_path, monkeypatch, defect):
    parent, _, attempt, _, result, _, event = setup_native(tmp_path, monkeypatch)
    source = select(event)
    first = observe(attempt, source)
    final_answer = 'The actual final answer: x <= 2 & literal &lt; and "quotes".'
    note = attachment_notification_record(notification_record(attempt, result, final_answer))
    attachment = note['attachment']
    if defect == 'row-type': note['type'] = 'system'
    if defect == 'attachment-type': attachment['type'] = 'task-notification'
    if defect == 'attachment-object': note['attachment'] = attachment['prompt']
    if defect == 'mode': attachment['commandMode'] = 'prompt'
    if defect == 'origin': attachment['origin']['kind'] = 'user'
    if defect == 'producer': attachment['origin']['producer'] = 'other'
    if defect == 'origin-extra': attachment['origin']['other'] = 'unknown'
    if defect == 'origin-missing': attachment.pop('origin')
    if defect == 'role': note['renderedRole'] = 'user'
    if defect == 'role-missing': note.pop('renderedRole')
    for prefix, field in [('source', 'source_uuid'), ('delivery', 'delivery_id')]:
        if defect == prefix + '-missing': attachment.pop(field)
        if defect == prefix + '-invalid': attachment[field] = 'native-looking-id'
        if defect == prefix + '-type': attachment[field] = {'id': attachment[field]}
    if defect == 'timestamp-missing': attachment.pop('timestamp')
    if defect == 'timestamp-mismatch': attachment['timestamp'] = result['timestamp']
    if defect == 'timestamp-invalid': note['timestamp'] = attachment['timestamp'] = 'not a date'
    if defect == 'timestamp-naive': note['timestamp'] = attachment['timestamp'] = '2026-01-01T00:00:00'
    if defect == 'timestamp-future':
        note['timestamp'] = attachment['timestamp'] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    if defect == 'timestamp-before-launch': note['timestamp'] = attachment['timestamp'] = attempt['prepared_at']
    if defect == 'plain-text': attachment['prompt'] = final_answer
    if defect == 'prompt-missing': attachment.pop('prompt')
    if defect == 'prompt-type': attachment['prompt'] = [{'type': 'text', 'text': attachment['prompt']}]
    if defect == 'rendered-only':
        note['rendered'] = note['renderedInHumanTurn'] = [{'content': attachment.pop('prompt')}]
    if defect == 'user-text': note = dict(note, type='user', message={'role': 'user', 'content': attachment['prompt']})
    if defect in {'enqueue', 'remove'}:
        note = {'type': 'queue-operation', 'operation': defect, 'sessionId': note['sessionId'],
                'timestamp': note['timestamp'], 'content': attachment['prompt']}
        if defect == 'remove': note.update(reason='absorbed_mid_turn', commandUuid=attachment['source_uuid'],
                                           deliveryId=attachment['delivery_id'])
    if defect == 'child': attachment['prompt'] = attachment['prompt'].replace('child-1', 'child-2')
    if defect == 'call': attachment['prompt'] = attachment['prompt'].replace('call-1', 'call-2')
    if defect == 'workspace': note['cwd'] += '-foreign'
    if defect == 'session': note['sessionId'] = 'foreign'
    if defect == 'sidechain': note['isSidechain'] = True
    if defect == 'sidechain-missing': note.pop('isSidechain')
    if defect == 'output': attachment['prompt'] = attachment['prompt'].replace('/arbitrary/output', '/different/output')
    with parent.open('ab') as stream:
        stream.write(encoded(note))
        if defect == 'duplicate': stream.write(encoded(note))
        if defect == 'conflicting-delivery':
            other = deepcopy(note)
            other['attachment']['delivery_id'] = '1fcc0737-40d2-49d0-aa6e-41bae3aa95a5'
            stream.write(encoded(other))
    answer = observe(attempt, source, first['cursor'])
    if defect in {None, 'duplicate', 'body-change', 'id-change'}:
        assert answer['status'] == 'matched'
        assert answer['completion']['body_digest'] == digest(escape(final_answer, quote=False))
        assert answer['completion']['record_sha256'] == digest(note)
        assert 'The actual final answer' not in json.dumps(answer)
        assert observe(attempt, source, answer['cursor'])['completion'] == answer['completion']
        if defect == 'body-change':
            parent.write_bytes(parent.read_bytes().replace(b'The actual final answer', b'The edited final answer'))
        if defect == 'id-change':
            parent.write_bytes(parent.read_bytes().replace(b'dfcc0737-', b'1fcc0737-'))
        if defect in {'body-change', 'id-change'}:
            assert observe(attempt, source, answer['cursor'])['status'] == 'conflict'
    else:
        assert 'completion' not in answer


@pytest.mark.parametrize('defect', [None, 'user', 'origin', 'producer', 'prompt', 'turn', 'child',
                                  'call', 'workspace', 'session', 'output', 'status', 'body-change', 'duplicate', 'timestamp'])
def test_native_notification_requires_exact_provenance_and_launch(tmp_path, monkeypatch, defect):
    parent, _, attempt, call, result, _, event = setup_native(tmp_path, monkeypatch)
    source = select(event)
    first = observe(attempt, source)
    note = notification_record(attempt, result)
    if defect == 'user': note.pop('origin')
    if defect == 'origin': note['origin']['kind'] = 'user'
    if defect == 'producer': note['origin']['producer'] = 'other'
    if defect == 'prompt': note['promptSource'] = 'user'
    if defect == 'turn': note['turnOrigin'] = 'user'
    if defect == 'child': note['message']['content'] = note['message']['content'].replace('child-1', 'child-2')
    if defect == 'call': note['message']['content'] = note['message']['content'].replace('call-1', 'call-2')
    if defect == 'workspace': note['cwd'] += '-foreign'
    if defect == 'session': note['sessionId'] = 'foreign'
    if defect == 'timestamp': note['timestamp'] = 'not a date'
    if defect == 'output': note['message']['content'] = note['message']['content'].replace('/arbitrary/output', '/different/output')
    if defect == 'status': note['message']['content'] = note['message']['content'].replace('<status>completed', '<status>failed')
    with parent.open('ab') as stream: stream.write(encoded(note))
    if defect == 'duplicate':
        changed = notification_record(attempt, result, 'Conflicting final answer')
        with parent.open('ab') as stream: stream.write(encoded(changed))
    answer = observe(attempt, source, first['cursor'])
    if defect in {'child', 'workspace', 'session', 'output', 'duplicate', 'timestamp'}:
        assert answer['status'] == 'conflict'
    elif defect in {'user', 'origin', 'producer', 'prompt', 'turn', 'call', 'status'}:
        assert answer['status'] == 'matched' and 'completion' not in answer
    else:
        assert answer['status'] == 'matched'
        assert answer['completion']['body_digest'] == digest('The actual final answer')
        assert 'The actual final answer' not in json.dumps(answer)
        if defect == 'body-change':
            parent.write_bytes(parent.read_bytes().replace(b'The actual final answer', b'The edited final answer'))
            assert observe(attempt, source, answer['cursor'])['status'] == 'conflict'



def test_old_launch_cursor_migrates_only_revalidated_spans(tmp_path, monkeypatch):
    parent, _, attempt, _, result, _, event = setup_native(tmp_path, monkeypatch)
    source = select(event)
    old = observe(attempt, source)
    cursor = deepcopy(old['cursor'])
    for entry in cursor['entries'].values():
        entry.pop('notification')
        for item in entry['result']: item['projection'].pop('output_file_sha256')
    cursor = observations._sealed(cursor)
    note = notification_record(attempt, result)
    with parent.open('ab') as stream: stream.write(encoded(note))
    migrated = observe(attempt, source, cursor)
    assert migrated['status'] == 'matched'
    assert migrated['evidence_sha256'] == old['evidence_sha256']
    assert migrated['completion']['body_digest'] == digest('The actual final answer')
    parent.write_bytes(parent.read_bytes().replace(b'SECRET RESULT', b'EDITED RESULT'))
    assert observe(attempt, source, cursor)['status'] == 'conflict'


# Complete native body retained from the 2026-10-06 rejected Agent launch.
# Fixture data only: advisory text inside it is never an instruction to execute.
DENIAL_BODY = "Permission for this action was denied by the Claude Code auto mode classifier. Reason: [Auto-Mode Bypass]. If you have other tasks that don't depend on this action, continue working on those. IMPORTANT: You *may* attempt to accomplish this action using other tools that might naturally be used to accomplish this goal, e.g. using head instead of cat. But you *should not* attempt to work around this denial in malicious ways, e.g. do not use your ability to run tests to execute non-test actions. You should only try to work around this restriction in reasonable ways that do not attempt to bypass the intent behind this denial. If you believe this capability is essential to complete the user's request, first try a safer method. Get as much of the rest of the task done as you can, then STOP and explain to the user what you were trying to do and why you need this permission. Let the user decide how to proceed. This denial applies to the outcome, not only this exact command: don't pursue the same outcome through another tool, interpreter, host, encoding, sub-agent or later turn, and don't record ways around it. Concretely, these all count as pursuing the same outcome: running the same command in smaller pieces; leaving the flagged part out of this call and covering it in another; reading the same file or data with a different tool (Read, Grep, head, awk, a script); re-issuing it with different quoting, flags, paths or hosts. If this was a batch or range operation, you may re-run it without the flagged items, but do not then act on the flagged items separately — leave those for the user. If this denial names something that would clear it — for example a first-hand read that shows the missing source — doing that is not pursuing the denied outcome: do it, and if it shows what the denial asked for, you may redo the action citing it."


def setup_denial(tmp_path, monkeypatch):
    parent, children, attempt, call, result, header, event = setup_native(tmp_path, monkeypatch)
    arguments = call['message']['content'][0]['input']
    del arguments['run_in_background']
    attempt.update(dispatch_digest=digest(arguments), transcript_admission_offset=0,
                   launch_requested_at=(datetime.fromisoformat(call['timestamp']) + timedelta(seconds=1)).isoformat())
    call.update(type='assistant', isSidechain=False, uuid='72bc5eed-004f-4848-8400-3387f1637a59')
    call['message']['role'] = 'assistant'
    result.update(type='user', isSidechain=False, session_id='parent',
                  parentUuid=call['uuid'], sourceToolAssistantUUID=call['uuid'],
                  toolDenialKind='automode-blocked', toolUseResult='Error: ' + DENIAL_BODY)
    result['message']['role'] = 'user'
    result['message']['content'][0].update(is_error=True, content=DENIAL_BODY)
    parent.write_bytes(encoded(call) + encoded(result))
    return parent, children, attempt, call, result, header, event


@pytest.mark.parametrize('parser', ['pure', 'incremental'])
def test_real_shaped_launch_denial_regression(tmp_path, monkeypatch, parser):
    import hashlib
    parent, _, attempt, call, result, header, event = setup_denial(tmp_path, monkeypatch)
    assert len(DENIAL_BODY.encode()) == 1856
    assert hashlib.sha256(DENIAL_BODY.encode()).hexdigest() == 'e293470ef2aa8a4727c32ce61e79fc464558df203254586b47c215bd0d63a07d'
    assert 'run_in_background' not in call['message']['content'][0]['input']
    answer = (observations.correlate_records('parent', attempt, [call, result], [header])
              if parser == 'pure' else observe(attempt, select(event)))
    assert answer['status'] == 'launch_denied', answer
    assert answer['denial_kind'] == 'automode-blocked'
    assert answer['denial_code'] == 'auto-mode-bypass'
    assert answer['denied_at'] == result['timestamp']
    assert answer['record_sha256'] == {'call': digest(call), 'result': digest(result)}
    assert 'worker_id' not in answer and 'started_at' not in answer and 'proof' not in answer
    if parser == 'incremental':
        assert answer['denial_proof']['schema'] == 'taskplane.claude-launch-denial-proof/v1'
        assert answer['denial_proof']['watermark'] == parent.stat().st_size
    else:
        assert 'denial_proof' not in answer


DENIAL_MUTATIONS = [
    'tool', 'input', 'input_mapping', 'call_id', 'result_id', 'call_parent', 'result_parent',
    'call_cwd', 'result_cwd', 'call_alias', 'result_alias', 'missing_result_alias', 'null_call_alias',
    'call_sidechain', 'result_sidechain', 'missing_call_sidechain', 'numeric_sidechain',
    'call_type', 'result_type', 'call_role', 'result_role', 'uuid', 'missing_uuid',
    'source_uuid', 'parent_uuid', 'missing_link', 'denial_kind', 'body', 'body_case',
    'body_substring', 'body_list', 'wrapper', 'structured_child', 'generic_error',
    'error_integer', 'error_string', 'missing_error', 'called_naive', 'returned_future',
    'returned_before_call', 'prepared_after_call', 'launch_before_preparation', 'launch_after_denial',
    'admitted_naive', 'admitted_after_denial', 'worker', 'claim', 'start', 'context', 'result',
    'lifecycle', 'terminal', 'launch_proof', 'completion_conflict',
]


@pytest.mark.parametrize('parser', ['pure', 'incremental'])
@pytest.mark.parametrize('defect', DENIAL_MUTATIONS)
def test_denial_requires_exact_native_shape_and_empty_attempt(tmp_path, monkeypatch, parser, defect):
    parent, _, attempt, call, result, header, event = setup_denial(tmp_path, monkeypatch)
    # Pin a separate session prefix so refusal tests exercise the candidate,
    # not a changed source-selection reference.
    session = {'sessionId': 'parent', 'isSidechain': False}
    parent.write_bytes(encoded(session) + encoded(call) + encoded(result))
    source = select(event)
    use, reply = call['message']['content'][0], result['message']['content'][0]
    if defect == 'tool': use['name'] = 'Bash'
    if defect == 'input': use['input']['prompt'] += ' altered'
    if defect == 'input_mapping': use['input'] = 'not a mapping'
    if defect == 'call_id': use['id'] = 'foreign'
    if defect == 'result_id': reply['tool_use_id'] = 'foreign'
    if defect == 'call_parent': call['sessionId'] = 'foreign'
    if defect == 'result_parent': result['sessionId'] = 'foreign'
    if defect == 'call_cwd': call['cwd'] += '-foreign'
    if defect == 'result_cwd': result['cwd'] += '-foreign'
    if defect == 'call_alias': call['session_id'] = 'foreign'
    if defect == 'result_alias': result['session_id'] = 'foreign'
    if defect == 'missing_result_alias': del result['session_id']
    if defect == 'null_call_alias': call['session_id'] = None
    if defect == 'call_sidechain': call['isSidechain'] = True
    if defect == 'result_sidechain': result['isSidechain'] = True
    if defect == 'missing_call_sidechain': del call['isSidechain']
    if defect == 'numeric_sidechain': result['isSidechain'] = 0
    if defect == 'call_type': call['type'] = 'user'
    if defect == 'result_type': result['type'] = 'assistant'
    if defect == 'call_role': call['message']['role'] = 'user'
    if defect == 'result_role': result['message']['role'] = 'assistant'
    if defect == 'uuid': call['uuid'] = 'not-a-uuid'
    if defect == 'missing_uuid': del call['uuid']
    if defect == 'source_uuid': result['sourceToolAssistantUUID'] = 'foreign'
    if defect == 'parent_uuid': result['parentUuid'] = 'foreign'
    if defect == 'missing_link': del result['sourceToolAssistantUUID']
    if defect == 'denial_kind': result['toolDenialKind'] = 'AUTOMODE-BLOCKED'
    if defect == 'body': reply['content'] += ' '
    if defect == 'body_case': reply['content'] = DENIAL_BODY.lower()
    if defect == 'body_substring': reply['content'] = DENIAL_BODY[:115]
    if defect == 'body_list': reply['content'] = [{'type': 'text', 'text': DENIAL_BODY}]
    if defect in {'body', 'body_case', 'body_substring'}: result['toolUseResult'] = 'Error: ' + reply['content']
    if defect == 'wrapper': result['toolUseResult'] = DENIAL_BODY
    if defect == 'structured_child': result['toolUseResult'] = {'agentId': 'child-1', 'isAsync': True, 'status': 'async_launched'}
    if defect == 'generic_error':
        reply['content'] = 'An unrelated failure'
        result['toolUseResult'] = 'Error: An unrelated failure'
    if defect == 'error_integer': reply['is_error'] = 1
    if defect == 'error_string': reply['is_error'] = 'true'
    if defect == 'missing_error': del reply['is_error']
    if defect == 'called_naive': call['timestamp'] = '2026-01-01T00:00:00'
    if defect == 'returned_future': result['timestamp'] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    if defect == 'returned_before_call': result['timestamp'] = attempt['prepared_at']
    if defect == 'prepared_after_call': attempt['prepared_at'] = result['timestamp']
    if defect == 'launch_before_preparation': attempt['launch_requested_at'] = '2000-01-01T00:00:00Z'
    if defect == 'launch_after_denial': attempt['launch_requested_at'] = datetime.now(timezone.utc).isoformat()
    if defect == 'admitted_naive': attempt['admitted_at'] = '2026-01-01T00:00:00'
    if defect == 'admitted_after_denial': attempt['admitted_at'] = datetime.now(timezone.utc).isoformat()
    field = {'worker': 'worker_id', 'claim': 'claimed_at', 'start': 'started_at', 'context': 'context_receipt',
             'result': 'result_ref', 'lifecycle': 'events', 'terminal': 'terminal_status',
             'launch_proof': 'launch_proof_ref', 'completion_conflict': 'completion_conflict'}.get(defect)
    if field: attempt[field] = 'contradictory evidence'
    parent.write_bytes(encoded(session) + encoded(call) + encoded(result))
    answer = (observations.correlate_records('parent', attempt, [call, result], [header])
              if parser == 'pure' else observe(attempt, source))
    assert answer['status'] != 'launch_denied', (defect, answer)
    assert 'denial_proof' not in answer


@pytest.mark.parametrize('parser', ['pure', 'incremental'])
@pytest.mark.parametrize('variant', ['Task', 'call_alias', 'duplicates', 'historical_denial'])
def test_denial_supported_variants_and_revalidation(tmp_path, monkeypatch, parser, variant):
    parent, _, attempt, call, result, header, event = setup_denial(tmp_path, monkeypatch)
    if variant == 'Task': call['message']['content'][0]['name'] = 'Task'
    if variant == 'call_alias': call['session_id'] = 'parent'
    if variant == 'historical_denial':
        attempt.update(terminal_status='launch_denied', revoked_at=result['timestamp'], ended_at=result['timestamp'])
    records = [call, result] * (2 if variant == 'duplicates' else 1)
    parent.write_bytes(b''.join(encoded(row) for row in records))
    answer = (observations.correlate_records('parent', attempt, records, [header])
              if parser == 'pure' else observe(attempt, select(event)))
    assert answer['status'] == 'launch_denied'
    assert DENIAL_BODY not in json.dumps(answer) and 'SECRET' not in json.dumps(answer)
    if parser == 'incremental':
        second = observe(attempt, answer['denial_proof']['source'], json.loads(json.dumps(answer['cursor'])))
        assert second['denial_proof'] == answer['denial_proof']


@pytest.mark.parametrize('parser', ['pure', 'incremental'])
@pytest.mark.parametrize('defect', ['call', 'result', 'same_row_item', 'notification', 'reverse', 'ordinary_user', 'ordinary_assistant'])
def test_denial_rejects_competing_records_and_text_only_claims(tmp_path, monkeypatch, parser, defect):
    parent, _, attempt, call, result, header, event = setup_denial(tmp_path, monkeypatch)
    records = [call, result]
    if defect in {'call', 'result'}:
        other = deepcopy(call if defect == 'call' else result)
        other['extra'] = 'competing native frame'
        records.append(other)
    if defect == 'same_row_item':
        other = deepcopy(result['message']['content'][0]); other['extra'] = 'distinct item'
        result['message']['content'].append(other)
    if defect == 'notification':
        records.append(notification_record(attempt, {'toolUseResult': {'agentId': 'child-1', 'outputFile': '/native'}}))
    if defect == 'reverse': records.reverse()
    if defect.startswith('ordinary_'):
        result.pop('toolDenialKind')
        result['message'] = {'role': defect.removeprefix('ordinary_'), 'content': DENIAL_BODY}
        result['type'] = result['message']['role']
    parent.write_bytes(b''.join(encoded(row) for row in records))
    answer = (observations.correlate_records('parent', attempt, records, [header])
              if parser == 'pure' else observe(attempt, select(event)))
    assert answer['status'] != 'launch_denied', answer


def legacy_denial_cursor(attempt, source):
    answer = observe(attempt, source)
    assert answer['status'] == 'launch_denied'
    cursor = deepcopy(answer['cursor'])
    entry = next(iter(cursor['entries'].values()))
    for role in ('call', 'result'):
        for key in observations._PROJECTION_ADDITIONS[role] - {'agent_type', 'output_file_sha256'}:
            entry[role][0]['projection'].pop(key)
    entry['conflict'] = observations.LEGACY_LAUNCH_CONFLICT
    return observations._sealed(cursor)


def reobserve_denial(attempt, source, cursor, **kwargs):
    entry = cursor['entries'][digest(observations._binding('parent', attempt))]
    expected = {f'expected_{role}_sha256': entry[role][0]['reference']['sha256'] for role in ('call', 'result')}
    return observations.reobserve_legacy_denial('parent', attempt, source=source, cursor=cursor,
                                               **{**expected, **kwargs})


def test_legacy_denial_requires_explicit_reverified_migration(tmp_path, monkeypatch):
    _, _, attempt, call, result, _, event = setup_denial(tmp_path, monkeypatch)
    source = select(event)
    cursor = legacy_denial_cursor(attempt, source)
    unrelated = deepcopy(next(iter(cursor['entries'].values())))
    unrelated['binding']['grant_id'] = 'other-grant'
    unrelated['conflict'] = 'Unrelated sticky conflict'
    cursor['entries'][digest(unrelated['binding'])] = unrelated
    cursor = observations._sealed(cursor)
    original = deepcopy(cursor)
    assert observe(attempt, source, cursor)['status'] == 'conflict'
    repaired = reobserve_denial(attempt, source, cursor)
    assert repaired['status'] == 'launch_denied', repaired
    assert cursor == original
    assert repaired['cursor']['entries'][digest(unrelated['binding'])] == unrelated
    proof = repaired['denial_proof']
    assert observations._valid_seal(proof, 'taskplane.claude-launch-denial-proof/v1')
    assert set(proof['records']) == {'call', 'result'}
    assert proof['source'] == source and proof['binding'] == observations._binding('parent', attempt)
    assert proof['admission_timestamps'] == {'launch_requested_at': attempt['launch_requested_at']}
    assert proof['records']['call'][0]['projection']['record_sha256'] == digest(call)
    assert proof['records']['result'][0]['projection']['record_sha256'] == digest(result)
    assert 'proof' not in repaired and 'launch_proof_ref' not in attempt


@pytest.mark.parametrize('defect', ['old_field', 'new_field', 'new_type', 'unknown_field', 'span_hash',
                                  'global_conflict', 'other_reason', 'binding', 'expected_call', 'expected_result',
                                  'header', 'notification', 'peer_handback', 'launch_proof', 'seal', 'source_seal'])
def test_legacy_denial_recovery_cannot_clear_other_or_forged_evidence(tmp_path, monkeypatch, defect):
    _, _, attempt, _, _, _, event = setup_denial(tmp_path, monkeypatch)
    source = select(event); cursor = legacy_denial_cursor(attempt, source)
    entry = next(iter(cursor['entries'].values()))
    projection = entry['result'][0]['projection']
    if defect == 'old_field': projection['timestamp'] = attempt['prepared_at']
    if defect == 'new_field': projection['denial_kind'] = 'forged'
    if defect == 'new_type': projection['error_wrapper_exact'] = 1
    if defect == 'unknown_field': projection['invented_provenance'] = True
    if defect == 'span_hash': entry['result'][0]['reference']['sha256'] = '0' * 64
    if defect == 'global_conflict': cursor['conflict'] = 'Independent source contradiction'
    if defect == 'other_reason': entry['conflict'] = 'Changed source span'
    if defect == 'binding': entry['binding']['workspace'] += '-foreign'
    if defect in {'header', 'notification', 'peer_handback'}: entry[defect] = deepcopy(entry['result'])
    if defect == 'launch_proof': attempt['launch_proof_ref'] = {'sha256': '0' * 64}
    cursor = observations._sealed(cursor)
    if defect == 'seal': cursor['sha256'] = '0' * 64
    if defect == 'source_seal': source = {**source, 'sha256': '0' * 64}
    original = deepcopy(cursor)
    kwargs = {f'{defect}_sha256': '0' * 64} if defect.startswith('expected_') else {}
    answer = reobserve_denial(attempt, source, cursor, **kwargs)
    assert answer['status'] == 'conflict', answer
    assert cursor == original and answer['cursor'] == original
    assert 'denial_proof' not in answer


def test_old_nonconflicting_projection_adds_only_reverified_denial_fields(tmp_path, monkeypatch):
    _, _, attempt, _, _, _, event = setup_denial(tmp_path, monkeypatch)
    source = select(event); cursor = legacy_denial_cursor(attempt, source)
    entry = next(iter(cursor['entries'].values())); entry['conflict'] = None
    cursor = observations._sealed(cursor)
    original = deepcopy(cursor)
    answer = observe(attempt, source, cursor)
    assert answer['status'] == 'launch_denied'
    assert cursor == original
    entry = next(iter(answer['cursor']['entries'].values()))
    assert entry['result'][0]['projection']['error_wrapper_exact'] is True


@pytest.mark.parametrize('defect', ['lost', 'replace', 'leaf_symlink', 'project_symlink', 'truncate', 'call', 'result'])
@pytest.mark.parametrize('legacy', [False, True])
def test_denial_source_must_stay_pinned_and_readable(tmp_path, monkeypatch, defect, legacy):
    parent, _, attempt, call, result, _, event = setup_denial(tmp_path, monkeypatch)
    source = select(event)
    cursor = legacy_denial_cursor(attempt, source) if legacy else observe(attempt, source)['cursor']
    original = deepcopy(cursor)
    if defect in {'lost', 'replace', 'leaf_symlink'}:
        saved = parent.with_name('saved.jsonl'); parent.rename(saved)
        if defect == 'replace': parent.write_bytes(saved.read_bytes())
        if defect == 'leaf_symlink': parent.symlink_to(saved)
    if defect == 'project_symlink':
        project = parent.parent; saved = project.with_name('saved-project')
        project.rename(saved); project.symlink_to(saved, target_is_directory=True)
    if defect == 'truncate': parent.write_bytes(encoded(call))
    if defect == 'call':
        call['uuid'] = '72bc5eed-004f-4848-8400-3387f1637a50'
        parent.write_bytes(encoded(call) + encoded(result))
    if defect == 'result':
        result['parentUuid'] = '72bc5eed-004f-4848-8400-3387f1637a50'
        parent.write_bytes(encoded(call) + encoded(result))
    answer = reobserve_denial(attempt, source, cursor) if legacy else observe(attempt, source, cursor)
    assert answer['status'] == ('not_yet_available' if defect == 'lost' else 'conflict')
    assert 'denial_proof' not in answer and cursor == original
    if defect == 'lost':
        saved.rename(parent)
        recovered = reobserve_denial(attempt, source, answer['cursor']) if legacy else observe(attempt, source, answer['cursor'])
        assert recovered['status'] == 'launch_denied'
    elif not legacy:
        assert observe(attempt, source, answer['cursor'])['status'] == 'conflict'


@pytest.mark.parametrize('legacy', [False, True])
def test_denial_waits_for_complete_watermark_and_finds_late_competitor(tmp_path, monkeypatch, legacy):
    parent, _, attempt, call, result, _, event = setup_denial(tmp_path, monkeypatch)
    source = select(event)
    cursor = legacy_denial_cursor(attempt, source) if legacy else None
    filler = encoded({'type': 'progress', 'data': 'x' * 8192})
    competitor = deepcopy(result); competitor['toolUseResult'] = {'agentId': 'late-child', 'isAsync': True, 'status': 'async_launched'}
    with parent.open('ab') as stream: stream.write(filler * 600 + encoded(competitor))
    first = reobserve_denial(attempt, source, cursor) if legacy else observe(attempt, source)
    assert first['status'] == 'not_yet_available'
    assert first['cursor']['offset'] < first['cursor']['watermark']
    assert 'denial_proof' not in first and first['budget']['bytes'] <= observations.MAX_BYTES
    resumed = json.loads(json.dumps(first['cursor']))
    second = reobserve_denial(attempt, source, resumed) if legacy else observe(attempt, source, resumed)
    assert second['status'] == 'conflict' and 'denial_proof' not in second


def test_legacy_denial_bounded_progress_is_private_and_resumes(tmp_path, monkeypatch):
    parent, _, attempt, _, _, _, event = setup_denial(tmp_path, monkeypatch)
    source = select(event); original = legacy_denial_cursor(attempt, source)
    with parent.open('ab') as stream: stream.write(encoded({'type': 'progress', 'data': 'x' * 8192}) * 600)
    first = reobserve_denial(attempt, source, original)
    assert first['status'] == 'not_yet_available'
    assert first['cursor']['legacy_denial_reobservation']['conflict'] == observations.LEGACY_LAUNCH_CONFLICT
    assert observe(attempt, source, first['cursor'])['status'] == 'conflict'
    refused = reobserve_denial(attempt, source, first['cursor'], expected_result_sha256='0' * 64)
    assert refused['status'] == 'conflict'
    second = reobserve_denial(attempt, source, json.loads(json.dumps(first['cursor'])))
    assert second['status'] == 'launch_denied'
    assert 'legacy_denial_reobservation' not in second['cursor']
    assert second['denial_proof']['watermark'] == parent.stat().st_size
    assert next(iter(original['entries'].values()))['conflict'] == observations.LEGACY_LAUNCH_CONFLICT


def test_denial_partial_line_and_budget_exhaustion_preserve_unknown(tmp_path, monkeypatch):
    parent, _, attempt, call, result, _, event = setup_denial(tmp_path, monkeypatch)
    parent.write_bytes(encoded(call) + encoded(result)[:-1])
    source = select(event)
    first = observe(attempt, source)
    assert first['status'] == 'not_yet_available' and 'denial_proof' not in first
    with parent.open('ab') as stream: stream.write(b'\n')
    second = observe(attempt, source, first['cursor'])
    assert second['status'] == 'launch_denied'
    exhausted = observe(attempt, source, second['cursor'], budget=observations.ReadBudget(max_bytes=0))
    assert exhausted['status'] == 'not_yet_available' and 'denial_proof' not in exhausted
    legacy = legacy_denial_cursor(attempt, source)
    exhausted = reobserve_denial(attempt, source, legacy, budget=observations.ReadBudget(max_records=1))
    assert exhausted['status'] == 'not_yet_available' and exhausted['cursor'] == legacy


def test_denial_admission_offsets_and_late_completion_remain_blocked(tmp_path, monkeypatch):
    parent, _, attempt, _, result, _, event = setup_denial(tmp_path, monkeypatch)
    source = select(event)
    replay = deepcopy(attempt); replay['transcript_admission_offset'] = 1
    assert observe(replay, source)['status'] == 'conflict'
    answer = observe(attempt, source)
    notification = notification_record(attempt, {'toolUseResult': {'agentId': 'child-1', 'outputFile': '/native'}})
    with parent.open('ab') as stream: stream.write(encoded(notification))
    late = observe(attempt, source, answer['cursor'])
    assert late['status'] == 'conflict' and 'denial_proof' not in late
    assert observe(attempt, source, late['cursor'])['status'] == 'conflict'


def test_pure_denial_explicit_lifecycle_and_observation_time_refuse(tmp_path, monkeypatch):
    _, _, attempt, call, result, header, _ = setup_denial(tmp_path, monkeypatch)
    header['call_id'] = attempt['call_id']
    assert observations.correlate_records('parent', attempt, [call, result], [header])['status'] == 'conflict'
    for stamp in ('2026-01-01T00:00:00', call['timestamp']):
        assert observations.correlate_records('parent', attempt, [call, result], [], observed_at=stamp)['status'] == 'conflict'
