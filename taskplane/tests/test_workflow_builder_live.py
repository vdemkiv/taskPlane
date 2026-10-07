"""Synthetic inspector tests only: these do not certify WFB-LIVE."""
from copy import deepcopy
import importlib.util
import json
import shlex
from pathlib import Path
import subprocess
import sys

import pytest

from taskplane.context import Store, signed
from taskplane.context_handoff import Session
from taskplane.context_views import collection, shared_bodies, view

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/verify_workflow_builder_live.py"
spec = importlib.util.spec_from_file_location("workflow_builder_live", SCRIPT)
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def context_session(root, binding, task=None, *, large=False, shared=False, body=None):
    """Use actual runtime CAS trees, pages and bounded transport, no host claims."""
    session = object.__new__(Session)
    session.workspace, session.state, session.task = root, {"run": binding["run"]}, task
    session.binding, session.phase, session.store = binding, "engineering", Store(root)
    if body is None:
        body = {"request": "Review bound source", "text": "".join(f"Line {n}: evidence body αβγ\n" for n in range(1200 if large else 4))}
    session.items = [{"id": "source", "kind": "source", "body": body, "required": True}]
    if shared:
        session.items.append({"id": "finding", "kind": "finding-evidence", "body": body, "required": True})
    session.input_refs = {item["id"]: session.store.put(item["kind"], item["body"]) for item in session.items}
    session.view = view(session.store, binding, session.phase, [task] if task else [], [],
                        {"binding": binding, "accepted_inputs": []}, session.items, {}, _prepared_refs=session.input_refs)
    session.view_ref = session.store.put("context-view", session.view)
    session.required = [{"id": item["id"], "ref": session.input_refs[item["id"]]} for item in session.items]
    session.handoff = signed({"schema": "taskplane.context-handoff/v1", "binding": binding,
        "view_ref": session.view_ref, "required_inputs": collection(session.store, session.required, "required-inputs", 0),
        "accepted_inputs": collection(session.store, [], "accepted-inputs", 0), "unknowns": {}, "exclusions": []})
    session.handoff_ref = session.store.put("context-handoff", session.handoff)
    session.read_binding = {**binding, "source_key": session.view["source_key"]}
    session.original_trees = {item["ref"]["sha256"]: session.store.descendants(item["ref"]) for item in session.required}
    session.delivery_trees, session.required_trees = dict(session.original_trees), dict(session.original_trees)
    reuse = session.view.get("body_reuse", {}).get("details")
    if reuse:
        for group in shared_bodies(session.items, session.input_refs):
            for item in group["inputs"]:
                key = item["ref"]["sha256"]
                session.delivery_trees[key] = session.store.descendants(group["body_ref"])
                session.required_trees[key] = session.delivery_trees[key] | session.store.descendants(reuse)
    session.allowed = set().union(*session.required_trees.values())
    session.ledger_path = root / ".taskplane/context-v1/delivery" / (session.handoff_ref["sha256"] + ".json")
    return session


class Fixture:
    """Construct internally consistent native-shaped data in an isolated tmpdir."""
    def __init__(self, root):
        self.root = root
        self.serial = 0
        self.definition = {"schema": "taskplane.workflow-blueprint/v1", "id": "test-review", "version": "0.1.0"}
        self.runtime_root = root / "runtime"
        module = self.file("runtime/taskplane/tp.py", b"# fixture runtime\n")
        hooks = self.file("runtime/hooks/hooks.json", b"{}")
        interpreter = self.file("runtime/python", b"fixture interpreter")
        self.runtime = {"schema": "taskplane.workflow-runtime/v1", "runtime_root": str(self.runtime_root),
            "compatible": True, "blockers": [], "modules": {"taskplane/tp.py": {
                "path": module["path"], "sha256": module["sha256"], "loaded_code_sha256": "a" * 64}},
            "native_member_sha256": {"hooks/hooks.json": hooks["sha256"]}, "interpreter": interpreter}
        self.runtime["digest"] = live.digest(self.runtime)
        authored = self.pair("workflow save --definition test-review", {"status": "created", "definition_digest": live.digest(self.definition)})
        self.index = {"schema": live.SCHEMA, "status": "observed", "definition": self.file("definition.json", self.definition),
                      "authoring_transcript": self.transcript("author.jsonl", authored), "invocations": []}
        self.states, self.transcripts = [], []
        for number in (1, 2):
            self.invocation(number)
        self.save()

    def file(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = value if isinstance(value, bytes) else encoded(value)
        path.write_bytes(raw)
        return {"path": str(path), "sha256": live.digest(raw)}

    def transcript(self, name, rows):
        return self.file(name, b"".join(encoded(row) + b"\n" for row in rows))

    def pair(self, command, output, *, name="Bash", root="session", worker=None, call=None, stamp="2026-10-06T03:00:00Z", **extras):
        self.serial += 1
        call = call or "tool-" + str(self.serial)
        common = {"sessionId": root, "cwd": str(self.root), "timestamp": stamp}
        if worker:
            common.update(agentId=worker, isSidechain=True)
        args = {"command": command} if name == "Bash" else command
        result = {"type": "tool_result", "tool_use_id": call, "content": json.dumps(output)}
        return [{**common, "type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": call, "name": name, "input": args}]}},
            {**common, "type": "user", "message": {"role": "user", "content": [result]}, **extras}]

    def tty(self, number, flag):
        return self.transcript(f"{number}-{flag[2:]}.jsonl", self.pair(
            {"cmd": f"claude --plugin-dir {self.runtime_root} {flag} session", "tty": True}, {"session_id": number}, name="exec_command"))

    def invocation(self, number):
        run, visit = f"run-{number}", f"visit-{number}"
        workspace = str(self.root)
        source = self.file(f"source-{number}.py", f"source {number}".encode())
        prefix = f"reports/{number}"
        source_name = f"source-{number}.py"
        bindings = {"schema": "taskplane.workflow-bindings/v1", "workspace": workspace, "status": "complete", "unresolved_inputs": [],
            "values": {"request": f"Review case {number}", "source_files": [source_name], "output_prefix": prefix, "invocation_label": str(number)},
            "source_manifest": {source_name: {"kind": "working", "sha256": source["sha256"]}}}
        tasks = [{"id": lens, "execution": "native_required", "review_lens": lens, "read_inputs": [source_name], "criteria": [],
                  "paths": [f"{prefix}/{lens}.md"]} for lens in ("security", "code-quality")]
        folder = f".taskplane/bootstrap/workflow-{number}"
        contents = {"definition.json": self.definition, "bindings.json": bindings, "capabilities.json": {"runtime": self.runtime}, "tasks.json": tasks}
        members = {name: self.file(f"{folder}/{name}", value)["sha256"] for name, value in contents.items()}
        compilation = {"schema": "taskplane.workflow-compilation/v1", "package_path": folder,
            "definition_digest": live.digest(self.definition), "binding_digest": live.digest(bindings), "runtime_digest": self.runtime["digest"],
            "compiler_digest": "b" * 64, "capability_digest": "c" * 64, "members": members,
            "task_patterns": {"engineering": tasks}, "entry_phase": "engineering"}
        compilation["digest"] = live.digest(compilation)
        workflow_binding = {key: compilation[key] for key in ("package_path", "definition_digest", "runtime_digest", "compiler_digest", "capability_digest")}
        workflow_binding["package_digest"] = compilation["digest"]
        state = {"run": run, "root": "session", "workspace": workspace, "revision": 4, "index": 0, "finished": True,
            "scope": {"workflow_binding": workflow_binding}, "workers": {}, "task_results": {}, "decisions": {},
            "visits": [{"id": visit, "decision": "approved", "packet": {"checkpoint": f"checkpoint-{number}"}}]}
        rows = self.pair(f"python {self.runtime_root}/taskplane/tp.py workflow compile --out {folder}", {"status": "created", "package_digest": compilation["digest"]})
        rows += self.pair(f"flow start --scope {folder}/scope.json", {"run": run})
        worker_transcripts = []
        for index, task in enumerate(tasks):
            identity, grant = f"worker-{number}-{index}", f"grant-{number}-{index}"
            output = self.file(task["paths"][0], b"Synthetic review result")
            manifest = {task["paths"][0]: output["sha256"]}
            bound = {"root": "session", "workspace": workspace, "run": run, "visit": visit, "revision": 1, "task_generation": 1}
            consumer = {"worker_id": identity, "grant_id": grant, "attempt": 1, "task_id": task["id"], "task_generation": 1}
            context = context_session(self.root, {**bound, "consumer": consumer}, task["id"])
            response = context.drain(context.handoff_ref["sha256"])
            receipt = response["context_receipt"]
            native = {"type": "user", "sessionId": "session", "cwd": workspace,
                "origin": {"kind": "task-notification", "producer": "session-task"}, "promptSource": "system", "turnOrigin": "task_notification",
                "message": {"role": "user", "content": f"Task {identity} completed"}}
            child = self.pair(f"flow worker --operation claim --run {run} --grant {grant}", {"grant_id": grant, "task_id": task["id"]}, worker=identity)
            child += self.pair(f"flow context --workspace {workspace} --run {run} --task {task['id']} --drain {context.handoff_ref['sha256']}", response, worker=identity)
            hook_call = f"hook-{number}-{index}"
            child += self.pair("cat source.py", "source", worker=identity, call=hook_call)
            worker_transcripts.append({"worker_id": identity, "transcript": self.transcript(f"child-{number}-{index}.jsonl", child)})
            row = {"worker_id": identity, "grant_id": grant, "task_id": task["id"], "host": "claude", "state": "accepted",
                "claimed_at": "2026-10-06T03:00:00Z", "terminal_status": "completed", "ended_at": "2026-10-06T03:01:00Z",
                "workspace": workspace, "root": "session", "run": run, "binding": bound, "attempt": 1, "task_generation": 1,
                "input_manifest": {source_name: source["sha256"]}, "dependency_results": {}, "task_digest": live.digest(task),
                "context_receipt": receipt, "context_delivery": {"remaining_required": 0}, "call_id": f"launch-{number}-{index}",
                "expected_runtime": {"root": str(self.runtime_root), "member_sha256": self.runtime["native_member_sha256"]},
                "hook_readiness": {"root": str(self.runtime_root), "member_sha256": self.runtime["native_member_sha256"], "matched_call": hook_call},
                "stop_observation": {"event_id": "stop-" + identity, "observed_at": "2026-10-06T03:01:00Z"},
                "handback": {"status": "delivered", "stop_event": "stop-" + identity, "notification": {"record_sha256": live.digest(native)}}}
            result = {"grant": grant, "worker_id": identity, "task_digest": row["task_digest"], "reviewer": "session",
                "accepted_at": "2026-10-06T03:02:00Z", "input_manifest": row["input_manifest"], "dependency_results": {},
                "manifest": manifest, "outputs": task["paths"]}
            state["workers"][grant], state["task_results"][task["id"]] = row, result
            rows += self.pair({"prompt": f"Taskplane grant: {grant}"}, "launched", name="Agent", call=row["call_id"], toolUseResult={"agentId": identity})
            rows.append(native)
            rows += self.pair(f"flow worker --operation accept-result --run {run} --task {task['id']} --grant {grant}", result)
        event = f"human-{number}"
        state["visits"][0]["packet"].update(execution_evidence="native-results/v1",
            manifest={path:sha for result in state["task_results"].values() for path,sha in result["manifest"].items()},
            output={"run": run, "visit": visit, "lens_coverage": [{"status": "native_verified", "task_id": task,
                    "reviewer": result["worker_id"], "grant": result["grant"]} for task,result in state["task_results"].items()]})
        rows += self.pair(f"flow submit --run {run}", {"checkpoint": f"checkpoint-{number}"})
        state["decisions"][event] = {"choice": "approved", "human": True, "binding": {
            "root": "session", "run": run, "workspace": workspace, "visit": visit, "checkpoint": f"checkpoint-{number}"},
            "provenance": {"source": {"actor": "user", "conversation": "session", "reference": event}, "excerpt": "Approve this review"}}
        rows.append({"type": "user", "sessionId": "session", "uuid": event, "message": {"role": "user", "content": "Approve this review"}})
        rows += self.pair(f"flow decide --run {run}", {"choice": "approved"})
        rows += self.pair(f"flow finish --run {run}", {"finished": True})
        snapshot = {"dashboard": workspace + "/.taskplane/dashboard.html", "workflow": deepcopy(state)}
        html = f"<html>session {run} {visit} revision 4</html>".encode()
        render = self.pair({"code": "await tab.getState()"}, f"Snapshot: session {run} {visit} revision 4", name="mcp__cua_repl.js")
        item = {"workspace": workspace, "root": "session", "run": run,
            "compilation": self.file(f"{folder}/compilation.json", compilation), "worker_transcripts": worker_transcripts,
            "launch": self.tty(number, "--session-id"), "dashboard": {
                "snapshot": self.file(f"snapshot-{number}.json", snapshot), "html": self.file(f"snapshot-{number}.html", html),
                "render": self.transcript(f"render-{number}.jsonl", render), "screenshot": self.file(f"screen-{number}.png", b"\x89PNG\r\n\x1a\n" + b"fixture" * 20)}}
        if number == 1:
            before = deepcopy(state)
            before.update(finished=False, revision=2, decisions={})
            before["visits"][0]["decision"] = "working"
            stopped = self.pair({"session_id": number, "chars": "\x03"}, {"exit_code": 130}, name="write_stdin")
            item["interruption"] = {"before": self.file("before.json", before), "interrupt": self.transcript("interrupt.jsonl", stopped), "resume": self.tty(1, "--resume")}
            rows += self.pair(f"workflow check --run {run}", {"status": "valid"}, stamp="2026-10-06T03:03:00Z")
            rows += self.pair(f"flow report --run {run}", {"run": run}, stamp="2026-10-06T03:03:00Z")
        self.states.append(state)
        self.transcripts.append(rows)
        self.index["invocations"].append(item)

    def save(self):
        for n, item in enumerate(self.index["invocations"]):
            item["controller"] = self.file(f"controller-{n}.json", self.states[n])
            item["root_transcript"] = self.transcript(f"root-{n}.jsonl", self.transcripts[n])
        self.path = self.root / "evidence.json"
        self.file("evidence.json", self.index)
        return self.path


@pytest.fixture
def evidence(tmp_path):
    return Fixture(tmp_path)


def failed(evidence, message):
    result = live.verify(evidence.save())
    assert result["status"] == "fail", result
    assert message in json.dumps(result), result


@pytest.fixture
def relay_evidence(evidence, monkeypatch):
    """Synthetic native-shaped relay; these files certify only the inspector."""
    monkeypatch.setattr(live.Path, "home", classmethod(lambda cls: evidence.root))
    state, item = evidence.states[0], evidence.index["invocations"][0]
    origin = "11111111-2222-3333-4444-555555555555"
    stage = state["visits"][0]
    stage["packet"]["checkpoint"] = "a" * 32
    state["started_at"] = "2026-10-06T02:59:00Z"
    stage.update(packet_revision=1, submitted_at="2026-10-06T03:00:00Z",
                 checkpoint_scope_digest=live.digest(state["scope"]))
    binding = live.checkpoint_identity(state)
    bound = {**{k:v for k,v in binding.items() if k != "packet_revision"}, "revision": 1}
    rows = evidence.transcripts[0]
    rows[:] = [row for row in rows if not (row.get("type") == "user" and row.get("uuid") == "human-1")]
    cli = f"python3 {evidence.runtime_root}/taskplane/tp.py flow"
    for action in ("submit", "present"):
        rows += evidence.pair(f"{cli} {action} --run run-1 --workspace {shlex.quote(state['workspace'])}", {
            "schema": "taskplane.command-summary/v1", "action": action, "run": "run-1",
            "errors": {"blocking": False}, "binding": {"pending_checkpoint": bound}})
    snapshot_state = deepcopy(state)
    snapshot_state.update(finished=False, decisions={}, revision=1)
    snapshot_state["visits"][0]["decision"] = "awaiting_human_approval"
    native_model = {"workflow": snapshot_state, "snapshot": {
        **{k:bound[k] for k in ("root", "run", "workspace", "visit", "revision")},
        "historical": False, "generated_at": "2026-10-06T03:00:20Z",
        "presentation_target": str(evidence.root / ".taskplane/dashboard.html")}}
    html = evidence.file(".taskplane/snapshot-aabb-1234.html", b"<html>native fixture</html>")
    snapshot = evidence.file(".taskplane/snapshot-aabb-1234.json", native_model)
    for ref in (html, snapshot):
        ref.update(offset=0, bytes=Path(ref["path"]).stat().st_size)
    native = {"schema": "taskplane.harness/v1", **{k:state[k] for k in ("root", "run", "workspace")},
        "presentation": {"checkpoint": binding, "binding": {"run": "run-1", "visit": "visit-1", "revision": 1},
            "outcome": "linked", "artifact": html["path"], "digest": html["sha256"], "model_digest": snapshot["sha256"]}}
    parent_rows = [
        {"type": "session_meta", "timestamp": "2026-10-06T02:00:00Z", "payload": {"id": origin, "session_id": origin, "source": "vscode"}},
        {"type": "response_item", "timestamp": "2026-10-06T02:58:00Z", "payload": {"type": "function_call",
            "call_id": "launch", "name": "exec_command", "arguments": json.dumps({
                "cmd": "/usr/local/bin/claude --session-id session", "tty": True, "workdir": state["workspace"]})}},
        {"type": "response_item", "timestamp": "2026-10-06T02:58:01Z", "payload": {"type": "function_call_output",
            "call_id": "launch", "output": json.dumps({"session_id": 123})}},
        {"type": "response_item", "timestamp": "2026-10-06T03:01:00Z", "payload": {"type": "message", "id": "shown",
            "role": "assistant", "content": [{"type": "output_text", "text": (
                f"Review checkpoint {bound['checkpoint']}: [A checkpoint]({html['path']}) "
                f"[A dashboard]({evidence.root}/.taskplane/dashboard.html)")}]},
            "metadata": {"retained_source": {"complete": True, "id": {"message_id": "shown", "role": "assistant"}}}},
        {"type": "response_item", "timestamp": "2026-10-06T03:02:00Z", "payload": {"type": "message", "id": "human",
            "role": "user", "content": [{"type": "input_text", "text": "approve\n"}],
            "internal_chat_message_metadata_passthrough": {"content_item_kinds": ["user.text"]}},
            "metadata": {"retained_source": {"complete": True, "id": {"message_id": "human", "role": "user"}}}},
    ]
    parent_path = f".codex/sessions/2026/10/06/rollout-2026-10-06T02-00-00-{origin}.jsonl"
    parent_bytes = [encoded(row) + b" " * 512 + b"\n" for row in parent_rows]
    ref = evidence.file(parent_path, b"".join(parent_bytes))
    refs, offset = [], 0
    for raw in parent_bytes:
        refs.append({"path": ref["path"], "offset": offset, "bytes": len(raw), "sha256": live.digest(raw)})
        offset += len(raw)
    source_ref = lambda r: f"{r['path']}#offset={r['offset']}&bytes={r['bytes']}"
    original_relay = {"schema": "taskplane.original-source-relay/v1", "session_meta": refs[0], "human": refs[4],
        "presentation": refs[3], "launch": {"segments": refs[1:3]}, "launch_flag": "--session-id", "html": html, "snapshot": snapshot}
    envelope = {"schema": "taskplane.observed-decision/v2", "event_id": "relay-human", "choice": "approved",
        "binding": bound, "recorder": "root_orchestrator", "excerpt": "approve\n", "source": {
            "kind": "conversation", "actor": "user", "automatic": False, "conversation": origin,
            "reference": source_ref(refs[4]), "observed_at": parent_rows[4]["timestamp"]},
        "presentation": {"checkpoint": bound["checkpoint"], "reference": source_ref(refs[3]), "at": parent_rows[3]["timestamp"]},
        "relay": original_relay}
    item["approval_relay"] = {"schema": "taskplane.approval-relay/v1", "origin_conversation": origin,
        "binding": binding, "session_meta": refs[0], "human": refs[4], "presentation": refs[3],
        "launch": {"segments": refs[1:3]}, "launch_flag": "--session-id", "html": html, "snapshot": snapshot,
        "native_presentation": evidence.file(".taskplane/harness-relay.json", native), "decision": envelope}
    state["decisions"] = {"relay-human": {"event_id": "relay-human", "human": True, "automatic": False,
        "choice": "approved", "binding": bound, "provenance": {"source": deepcopy(envelope["source"]),
        "excerpt": envelope["excerpt"], "recorder": "root_orchestrator", "presentation": deepcopy(envelope["presentation"]),
        "recorded_at": "2026-10-06T03:03:00Z", "schema": envelope["schema"], "relay": deepcopy(original_relay),
        "checkpoint_explicit": False, "chronology": "verified/v1"}}}
    rows += evidence.pair(f"{cli} decide --run run-1 --workspace {shlex.quote(state['workspace'])} --decision-json " + shlex.quote(json.dumps(envelope)), {
        "schema": "taskplane.command-summary/v1", "action": "decide", "run": "run-1", "status": "approved",
        "errors": {"blocking": False}, "binding": bound}, stamp="2026-10-06T03:03:00Z")
    evidence.save()
    return evidence


def verify_relay(evidence):
    item = evidence.index["invocations"][0]
    return live.verify_approval_relay(live.Reader(evidence.root), item, evidence.states[0], live.Transcript(evidence.transcripts[0]))


def test_relay_original_human_approval_and_aggregate(relay_evidence):
    assert verify_relay(relay_evidence)["recorded"] is True
    assert live.verify(relay_evidence.save())["status"] == "pass"


def test_relay_pending_does_not_require_finish_or_second_invocation(relay_evidence):
    e = relay_evidence
    e.states[0].update(finished=False, decisions={})
    e.states[0]["visits"][0]["decision"] = "awaiting_human_approval"
    e.index["invocations"] = e.index["invocations"][:1]
    assert verify_relay(e)["recorded"] is False
    assert live.verify(e.save())["status"] == "fail"


@pytest.mark.parametrize("key", ["run", "root", "workspace", "visit", "checkpoint", "scope_digest", "manifest_digest", "packet_revision"])
def test_relay_binding_is_exact(relay_evidence, key):
    relay_evidence.index["invocations"][0]["approval_relay"]["binding"][key] = "foreign"
    with pytest.raises(live.EvidenceError, match="stale relay checkpoint"):
        verify_relay(relay_evidence)


@pytest.mark.parametrize("field,value", [("excerpt", "approve"), ("event_id", "other"), ("choice", "rejected")])
def test_relay_decision_cannot_change_human_origin(relay_evidence, field, value):
    relay_evidence.index["invocations"][0]["approval_relay"]["decision"][field] = value
    with pytest.raises(live.EvidenceError):
        verify_relay(relay_evidence)


@pytest.mark.parametrize("field,value", [("conversation", "session"), ("reference", "made-up"),
    ("observed_at", "2026-10-06T03:02:01Z"), ("actor", "assistant"), ("automatic", True)])
def test_relay_source_cannot_be_rewritten(relay_evidence, field, value):
    relay_evidence.index["invocations"][0]["approval_relay"]["decision"]["source"][field] = value
    with pytest.raises(live.EvidenceError, match="original human"):
        verify_relay(relay_evidence)


def mutate_native_frame(evidence, key, mutate):
    """Keep original byte offset and update selected frame hash after corruption."""
    ref = evidence.index["invocations"][0]["approval_relay"][key]
    path = Path(ref["path"])
    raw = path.read_bytes()
    row = json.loads(raw[ref["offset"]:ref["offset"] + ref["bytes"]])
    mutate(row)
    replacement = encoded(row)
    assert len(replacement) < ref["bytes"]
    replacement = replacement + b" " * (ref["bytes"] - len(replacement) - 1) + b"\n"
    path.write_bytes(raw[:ref["offset"]] + replacement + raw[ref["offset"] + ref["bytes"]:])
    ref["sha256"] = live.digest(replacement)


@pytest.mark.parametrize("role", ["tool", "system", "agent", "assistant"])
def test_relay_nonhuman_role_rejected(relay_evidence, role):
    mutate_native_frame(relay_evidence, "human", lambda r: r["payload"].update(role=role))
    with pytest.raises(live.EvidenceError, match="native user message"):
        verify_relay(relay_evidence)


def test_relay_automation_rejected(relay_evidence):
    mutate_native_frame(relay_evidence, "human", lambda r: r["payload"]["internal_chat_message_metadata_passthrough"].update(content_item_kinds=["auto"]))
    with pytest.raises(live.EvidenceError, match="automation or tool claim"):
        verify_relay(relay_evidence)


def test_relay_original_session_identity_rejected(relay_evidence):
    mutate_native_frame(relay_evidence, "session_meta", lambda r: r["payload"].update(id="other"))
    with pytest.raises(live.EvidenceError, match="parent session identity"):
        verify_relay(relay_evidence)


def test_relay_copied_native_log_rejected(relay_evidence):
    relay = relay_evidence.index["invocations"][0]["approval_relay"]
    copied = relay_evidence.file("copied.jsonl", Path(relay["human"]["path"]).read_bytes())
    for key in ("session_meta", "human", "presentation"):
        relay[key]["path"] = copied["path"]
    with pytest.raises(live.EvidenceError, match="not a copied log"):
        verify_relay(relay_evidence)


@pytest.mark.parametrize("key", ["human", "presentation", "native_presentation", "html", "snapshot"])
def test_relay_tampered_reference_rejected(relay_evidence, key):
    relay_evidence.index["invocations"][0]["approval_relay"][key]["sha256"] = "0" * 64
    with pytest.raises(live.EvidenceError, match="Stale evidence SHA-256"):
        verify_relay(relay_evidence)


def test_relay_unrelated_launch_rejected(relay_evidence):
    mutate_native_frame(relay_evidence, "presentation", lambda r: r["payload"]["content"][0].update(text="An unrelated review"))
    with pytest.raises(live.EvidenceError, match="exact immutable checkpoint"):
        verify_relay(relay_evidence)


def test_relay_native_command_must_return_checkpoint(relay_evidence):
    rows = relay_evidence.transcripts[0]
    for row in rows:
        for block in row.get("message", {}).get("content", []) if isinstance(row.get("message", {}).get("content"), list) else []:
            if block.get("type") == "tool_result" and "pending_checkpoint" in block.get("content", ""):
                block["content"] = "The assistant says this was approved"
    with pytest.raises(live.EvidenceError, match="actual target submit result"):
        verify_relay(relay_evidence)


@pytest.mark.parametrize("replacement", ["echo {quoted}", "python3 tp.py flow wait --run run-1 --note {quoted}", "cat {quoted}"])
def test_relay_quoted_decision_is_not_execution(relay_evidence, replacement):
    for row in relay_evidence.transcripts[0]:
        for block in row.get("message", {}).get("content", []) if isinstance(row.get("message", {}).get("content"), list) else []:
            command = block.get("input", {}).get("command", "")
            if "--decision-json" in command:
                block["input"]["command"] = replacement.format(quoted=shlex.quote(command))
    with pytest.raises(live.EvidenceError, match="actual post-approval"):
        verify_relay(relay_evidence)


def test_relay_stale_human_before_presentation(relay_evidence):
    mutate_native_frame(relay_evidence, "human", lambda r: r.update(timestamp="2026-10-06T02:30:00Z"))
    with pytest.raises(live.EvidenceError, match="precedes its actual checkpoint"):
        verify_relay(relay_evidence)


def test_relay_current_scope_drift(relay_evidence):
    relay_evidence.states[0]["scope"]["unexpected"] = True
    with pytest.raises(live.EvidenceError, match="scope drifted"):
        verify_relay(relay_evidence)


def test_relay_launch_of_different_session(relay_evidence):
    relay = relay_evidence.index["invocations"][0]["approval_relay"]
    # Reuse the single-frame mutation helper on the selected launch call.
    relay["launch_call"] = relay["launch"]["segments"][0]
    mutate_native_frame(relay_evidence, "launch_call", lambda r: r["payload"].update(
        arguments=json.dumps({"cmd": "/usr/local/bin/claude --session-id other", "tty": True, "workdir": str(relay_evidence.root)})))
    with pytest.raises(live.EvidenceError, match="unrelated native session"):
        verify_relay(relay_evidence)


def test_relay_source_from_unrelated_parent(relay_evidence):
    relay = relay_evidence.index["invocations"][0]["approval_relay"]
    relay["human"]["path"] = relay_evidence.file("foreign.jsonl", b"{}\n")["path"]
    with pytest.raises(live.EvidenceError, match="one original parent session"):
        verify_relay(relay_evidence)


def test_relay_recorded_source_must_match_original(relay_evidence):
    relay_evidence.states[0]["decisions"]["relay-human"]["provenance"]["excerpt"] = "approve"
    with pytest.raises(live.EvidenceError, match="Recorded decision does not preserve"):
        verify_relay(relay_evidence)


def test_relay_recorded_result_cannot_be_assistant_claim(relay_evidence):
    rows = relay_evidence.transcripts[0]
    rows[-1]["message"]["content"][0]["content"] = json.dumps({"claimed_result": {
        "schema": "taskplane.command-summary/v1", "action": "decide", "run": "run-1", "status": "approved"}})
    with pytest.raises(live.EvidenceError, match="actual post-approval"):
        verify_relay(relay_evidence)


def sync_relay_record(evidence):
    """Update synthetic recorded evidence after an intentionally valid variant."""
    envelope = evidence.index["invocations"][0]["approval_relay"]["decision"]
    provenance = evidence.states[0]["decisions"][envelope["event_id"]]["provenance"]
    provenance.update(source=deepcopy(envelope["source"]), schema=envelope["schema"], relay=deepcopy(envelope.get("relay")))
    for row in evidence.transcripts[0]:
        blocks = row.get("message", {}).get("content", [])
        for block in blocks if isinstance(blocks, list) else []:
            command = block.get("input", {}).get("command", "")
            if "--decision-json" in command:
                block["input"]["command"] = command.split("--decision-json", 1)[0] + "--decision-json " + shlex.quote(json.dumps(envelope))


def mutate_launch_frame(evidence, number, mutate):
    relay = evidence.index["invocations"][0]["approval_relay"]
    relay["launch_frame"] = relay["launch"]["segments"][number]
    mutate_native_frame(evidence, "launch_frame", mutate)
    del relay["launch_frame"]


def test_original_source_relay_is_read_only(relay_evidence):
    e = relay_evidence
    before = {p: p.read_bytes() for p in e.root.rglob("*") if p.is_file()}
    original_state = deepcopy(e.states)
    result = verify_relay(e)
    assert result["recorded"] is True and result["historical_only"] is False
    assert e.states == original_state
    assert before == {p: p.read_bytes() for p in e.root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("recorded", [False, True])
def test_remapped_v1_is_historical_inspection_only(relay_evidence, recorded):
    e = relay_evidence
    relay = e.index["invocations"][0]["approval_relay"]
    envelope = relay["decision"]
    envelope.update(schema="taskplane.observed-decision/v1")
    del envelope["relay"]
    envelope["source"]["conversation"] = e.states[0]["root"]
    sync_relay_record(e)
    if recorded:
        with pytest.raises(live.EvidenceError, match="Historical v1 remapped relay"):
            verify_relay(e)
        assert live.verify(e.save())["status"] == "fail"
    else:
        e.states[0].update(finished=False, decisions={})
        e.states[0]["visits"][0]["decision"] = "awaiting_human_approval"
        result = verify_relay(e)
        assert result["recorded"] is False and result["historical_only"] is True


@pytest.mark.parametrize("terminator", [";", ""])
def test_v2_single_json_exec_wrapper_and_native_result_blocks(relay_evidence, terminator):
    e = relay_evidence
    args = {"cmd": "/usr/local/bin/claude --session-id session", "workdir": str(e.root), "tty": True}
    def wrapper(row):
        payload = row["payload"]
        payload.pop("arguments")
        payload.update(type="custom_tool_call", name="functions.exec", input="text(await tools.exec_command(" + json.dumps(args) + "))" + terminator)
    mutate_launch_frame(e, 0, wrapper)
    mutate_launch_frame(e, 1, lambda row: row["payload"].update(type="custom_tool_call_output", output=[
        {"type": "input_text", "text": "Script completed\nWall time: 1s"},
        {"type": "input_text", "text": json.dumps({"session_id": 123, "output": "native prompt"})}]))
    sync_relay_record(e)
    assert verify_relay(e)["recorded"] is True


def test_v2_versioned_executable_resume_and_literal_prompt(relay_evidence):
    e = relay_evidence
    relay = e.index["invocations"][0]["approval_relay"]
    relay["launch_flag"] = relay["decision"]["relay"]["launch_flag"] = "--resume"
    mutate_launch_frame(e, 0, lambda row: row["payload"].update(arguments=json.dumps({
        "cmd": "/Users/test/.local/share/claude/versions/2.1.290 --plugin-dir /candidate --resume session "
            + shlex.quote("Use $taskplane.\nContinue this exact session."), "workdir": str(e.root), "tty": True})))
    sync_relay_record(e)
    assert verify_relay(e)["recorded"] is True


@pytest.mark.parametrize("command", [
    "echo /usr/local/bin/claude --session-id session",
    "/usr/local/bin/claude --session-id session; echo claimed",
    "/usr/local/bin/claude --session-id session --resume session",
    "/usr/local/bin/claude --session-id session --session-id session",
    "/usr/local/bin/claude --session-id other 'session'",
    "/usr/local/bin/claude --session-id session --dangerously-skip-permissions",
    "claude --session-id session",
])
def test_v2_launch_requires_exact_supported_command(relay_evidence, command):
    e = relay_evidence
    mutate_launch_frame(e, 0, lambda row: row["payload"].update(arguments=json.dumps({
        "cmd": command, "tty": True, "workdir": str(e.root)})))
    with pytest.raises(live.EvidenceError):
        verify_relay(e)


def test_v2_launch_wrong_workdir_rejected(relay_evidence):
    mutate_launch_frame(relay_evidence, 0, lambda row: row["payload"].update(arguments=json.dumps({
        "cmd": "/usr/local/bin/claude --session-id session", "tty": True, "workdir": "/foreign"})))
    with pytest.raises(live.EvidenceError, match="exact native target workspace"):
        verify_relay(relay_evidence)


@pytest.mark.parametrize("output", [{"claimed": {"session_id": 123}}, {"exit_code": 0},
    {"session_id": 123, "exit_code": 1}, {"session_id": True}, {"session_id": 123, "error": "denied"}])
def test_v2_launch_requires_running_native_result(relay_evidence, output):
    mutate_launch_frame(relay_evidence, 1, lambda row: row["payload"].update(output=json.dumps(output)))
    with pytest.raises(live.EvidenceError, match="actual running native session"):
        verify_relay(relay_evidence)


def test_v2_presentation_requires_complete_retained_assistant_frame(relay_evidence):
    mutate_native_frame(relay_evidence, "presentation", lambda row: row["metadata"]["retained_source"].update(complete=False))
    with pytest.raises(live.EvidenceError, match="original human frame"):
        verify_relay(relay_evidence)


@pytest.mark.parametrize("metadata", [{"source": {"subagent": "worker"}}, {"source": "automation"},
    {"source": None}, {"subagent": "worker"}, {"automation_id": "timer"}, {"isSidechain": True}])
def test_v2_original_session_cannot_be_subagent_or_automation(relay_evidence, metadata):
    mutate_native_frame(relay_evidence, "session_meta", lambda row: row["payload"].update(metadata))
    with pytest.raises(live.EvidenceError, match="native human Codex session"):
        verify_relay(relay_evidence)


@pytest.mark.parametrize("missing", ["checkpoint", "immutable_link"])
def test_v2_presentation_names_exact_immutable_checkpoint(relay_evidence, missing):
    e = relay_evidence
    relay = e.index["invocations"][0]["approval_relay"]
    text = ("Review " + (relay["binding"]["checkpoint"] if missing != "checkpoint" else "another checkpoint")
            + " [Review](" + (relay["html"]["path"] if missing != "immutable_link" else str(e.root / ".taskplane/dashboard.html")) + ")")
    mutate_native_frame(e, "presentation", lambda row: row["payload"]["content"][0].update(text=text))
    with pytest.raises(live.EvidenceError, match="exact immutable checkpoint"):
        verify_relay(e)


@pytest.mark.parametrize("key", ["html", "snapshot"])
def test_v2_snapshot_requires_whole_file(relay_evidence, key):
    relay_evidence.index["invocations"][0]["approval_relay"][key]["bytes"] -= 1
    with pytest.raises(live.EvidenceError, match="complete immutable native snapshot"):
        verify_relay(relay_evidence)


@pytest.mark.parametrize("field,value", [("schema", "taskplane.observed-decision/v1"), ("relay", {}),
    ("checkpoint_explicit", True), ("chronology", "claimed")])
def test_v2_recorded_provenance_is_exact(relay_evidence, field, value):
    relay_evidence.states[0]["decisions"]["relay-human"]["provenance"][field] = value
    with pytest.raises(live.EvidenceError, match="Recorded decision does not preserve"):
        verify_relay(relay_evidence)


def test_v2_envelope_cannot_substitute_relay_references(relay_evidence):
    relay = relay_evidence.index["invocations"][0]["approval_relay"]
    relay["decision"]["relay"] = deepcopy(relay["decision"]["relay"])
    relay["decision"]["relay"]["human"]["sha256"] = "0" * 64
    with pytest.raises(live.EvidenceError, match="differs from the verified original"):
        verify_relay(relay_evidence)


def test_v2_relay_cannot_bypass_presentation_with_explicit_flag(relay_evidence):
    relay_evidence.index["invocations"][0]["approval_relay"]["decision"]["checkpoint_explicit"] = True
    with pytest.raises(live.EvidenceError, match="actual preceding presentation"):
        verify_relay(relay_evidence)


def test_complete_synthetic_contract_only(evidence):
    before = {p: p.read_bytes() for p in evidence.root.rglob("*") if p.is_file()}
    result = live.verify(evidence.path)
    assert result["status"] == "pass", result
    assert "no host attestation" in result["assurance"]
    assert before == {p: p.read_bytes() for p in evidence.root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("status", ["not_run", "unknown", "fail", True])
def test_honest_nonpassing_status(evidence, status):
    evidence.index["status"] = status
    failed(evidence, "Actual host evidence is")


def test_success_booleans_and_prose_are_not_evidence(tmp_path):
    p = tmp_path / "assertions.json"
    p.write_text(json.dumps({"schema": live.SCHEMA, "status": "observed", "invocations": [{"passed": True, "hooks": "worked"}] * 2}))
    assert live.verify(p)["status"] == "fail"


@pytest.mark.parametrize("field,value,reason", [
    ("claimed_at", None, "Missing claimed"), ("terminal_status", "running", "Missing claimed"),
    ("state", "result_pending", "Missing claimed"), ("run", "foreign", "Foreign worker"),
    ("completion_conflict", True, "Missing claimed"),
    ("hook_readiness", {"matched_call": "made-up"}, "Automatic hook pair"),
    ("context_delivery", {"remaining_required": 1}, "partial, stale or foreign"),
    ("handback", {"status": "delivered", "notification": {"record_sha256": "0" * 64}}, "actual native completion"),
])
def test_worker_evidence_cannot_be_asserted(evidence, field, value, reason):
    evidence.states[0]["workers"]["grant-1-0"][field] = value
    failed(evidence, reason)


def test_context_receipt_tampering(evidence):
    row = evidence.states[0]["workers"]["grant-1-0"]
    path = evidence.root / ".taskplane/context-v1/objects" / (row["context_receipt"]["receipt"]["sha256"] + ".json")
    path.write_bytes(path.read_bytes() + b" ")
    failed(evidence, "Stale evidence")


def change_worker_context(evidence, transform):
    entry = evidence.index["invocations"][0]["worker_transcripts"][0]
    rows = [json.loads(line) for line in Path(entry["transcript"]["path"]).read_text().splitlines()]
    transform(rows)
    entry["transcript"] = evidence.transcript("child-1-0.jsonl", rows)


def test_valid_final_receipt_without_delivered_bodies_refuses(evidence):
    def omit(rows):
        result = rows[3]["message"]["content"][0]
        body = json.loads(result["content"])
        body["pages"] = []
        result["content"] = json.dumps(body)
    change_worker_context(evidence, omit)
    failed(evidence, "Required context body pages were omitted")


@pytest.mark.parametrize("replacement", ["<persisted-output>Output too large. Preview: {}…</persisted-output>",
                                        '{"done":true,"remaining_required":0', "Output truncated: {}"])
def test_persisted_or_truncated_context_is_not_delivery(evidence, replacement):
    def hide(rows):
        rows[3]["message"]["content"][0]["content"] = replacement
    change_worker_context(evidence, hide)
    failed(evidence, "incomplete, filtered or persisted")


@pytest.mark.parametrize("field,value", [("data", "replaced body"), ("kind", "foreign"), ("pages", 2),
                                         ("total", 999), ("next_page", 1), ("untrusted_data", False)])
def test_altered_context_page_refuses(evidence, field, value):
    def alter(rows):
        result = rows[3]["message"]["content"][0]
        body = json.loads(result["content"])
        body["pages"][0][field] = value
        result["content"] = json.dumps(body)
    change_worker_context(evidence, alter)
    failed(evidence, "Omitted or altered context body page")


@pytest.mark.parametrize("suffix", [" | head -c 2000", " > /dev/null", " &"])
def test_filtered_context_command_refuses(evidence, suffix):
    def filter_command(rows):
        rows[2]["message"]["content"][0]["input"]["command"] += suffix
    change_worker_context(evidence, filter_command)
    failed(evidence, "unfiltered foreground")


def test_foreign_worker_context_frame_refuses(evidence):
    change_worker_context(evidence, lambda rows: rows[3].update(agentId="another-worker"))
    failed(evidence, "Foreign native context consumer")


def context_trace(root, *, mode="small", shared=False, task="review", body=None):
    binding = {"workspace": str(root), "root": "session", "run": "context-run", "revision": 1,
               "visit": "context-visit", "task_generation": 1}
    fixture = object.__new__(Fixture)
    fixture.root, fixture.serial = root, 0
    session = context_session(root, binding, task, large=True, shared=shared, body=body)
    prefix = f"flow context --workspace {root} --run context-run" + (f" --task {task}" if task else "")
    key, rows, responses = session.handoff_ref["sha256"], [], []
    def emit(command, result):
        rows.extend(fixture.pair(prefix + " " + command, result))
        responses.append(result)
    if mode == "consume":
        emit("--consume " + key, session.consume(key))
    if mode == "read":
        for sha in sorted(set().union(*session.required_trees.values())):
            for page in range(session.store.page(sha)["pages"]):
                emit(f"--read {sha} --page {page}", session.read(sha, page))
    else:
        while not responses or responses[-1]["remaining_required"]:
            emit("--read-required " + key, session.read_required(key))
    emit("--drain " + key, session.drain(key))
    return binding, session, rows, responses


@pytest.mark.parametrize("mode,shared,task", [("small", False, "review"), ("small", True, "review"),
                                           ("read", False, "review"), ("consume", True, None)])
def test_complete_small_batches_pages_and_root_consumption_pass(tmp_path, mode, shared, task):
    binding, session, rows, responses = context_trace(tmp_path, mode=mode, shared=shared, task=task)
    assert responses[-1]["pages"] == [] and responses[-1]["done"] is True
    if mode == "small":
        assert len(responses) >= 3
        assert all(len(encoded(response)) < 16 * 1024 for response in responses)
    result = live.verify_context_delivery(live.Reader(tmp_path), tmp_path, live.Transcript(rows), binding,
                                          responses[-1]["context_receipt"], task)
    assert result["required_roots"] == len(session.required_trees)


def test_omitted_early_batch_cannot_be_replaced_by_empty_terminal_drain(tmp_path):
    binding, _, rows, responses = context_trace(tmp_path)
    del rows[:2]
    with pytest.raises(live.EvidenceError, match="Required context body pages were omitted"):
        live.verify_context_delivery(live.Reader(tmp_path), tmp_path, live.Transcript(rows), binding,
                                     responses[-1]["context_receipt"], "review")


def test_persisted_early_batch_refuses_despite_valid_final_receipt(tmp_path):
    binding, _, rows, responses = context_trace(tmp_path)
    rows[1]["message"]["content"][0]["content"] = "<persisted-output>Full output saved. Preview: {}…</persisted-output>"
    with pytest.raises(live.EvidenceError, match="incomplete, filtered or persisted"):
        live.verify_context_delivery(live.Reader(tmp_path), tmp_path, live.Transcript(rows), binding,
                                     responses[-1]["context_receipt"], "review")


def test_all_cursors_required_for_multi_page_canonical_node(tmp_path):
    binding, _, rows, responses = context_trace(tmp_path, mode="read", body=list(range(100)))
    assert [response["page"]["page"] for response in responses[:-1]] == [0, 1]
    live.verify_context_delivery(live.Reader(tmp_path), tmp_path, live.Transcript(rows), binding,
                                 responses[-1]["context_receipt"], "review")
    with pytest.raises(live.EvidenceError, match="Required context body pages were omitted"):
        live.verify_context_delivery(live.Reader(tmp_path), tmp_path, live.Transcript(rows[2:]), binding,
                                     responses[-1]["context_receipt"], "review")


def test_foreign_handoff_and_foreign_cas_page_refuse(tmp_path):
    binding, session, rows, responses = context_trace(tmp_path)
    result = rows[-1]["message"]["content"][0]
    final = json.loads(result["content"])
    final["handoff_ref"] = session.store.put("context-handoff", {**session.handoff, "binding": {**binding, "root": "foreign"}})
    result["content"] = json.dumps(final)
    with pytest.raises(live.EvidenceError, match="Foreign or altered context handoff"):
        live.verify_context_delivery(live.Reader(tmp_path), tmp_path, live.Transcript(rows), binding,
                                     responses[-1]["context_receipt"], "review")
    result["content"] = json.dumps(responses[-1])
    first = rows[1]["message"]["content"][0]
    body = json.loads(first["content"])
    foreign = session.store.put("foreign-input", "unrelated context")
    body["pages"][0] = session.store.page(foreign["sha256"])
    first["content"] = json.dumps(body)
    with pytest.raises(live.EvidenceError, match="Foreign context body page"):
        live.verify_context_delivery(live.Reader(tmp_path), tmp_path, live.Transcript(rows), binding,
                                     responses[-1]["context_receipt"], "review")


def test_cas_body_bytes_are_verified_even_with_valid_native_page(tmp_path):
    binding, session, rows, responses = context_trace(tmp_path)
    key = responses[0]["pages"][0]["sha256"]
    path = session.store.path(key)
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(live.EvidenceError, match="Stale evidence"):
        live.verify_context_delivery(live.Reader(tmp_path), tmp_path, live.Transcript(rows), binding,
                                     responses[-1]["context_receipt"], "review")


@pytest.mark.parametrize("file", ["source-1.py", "runtime/taskplane/tp.py", "reports/1/security.md", ".taskplane/bootstrap/workflow-1/bindings.json"])
def test_stale_files_refuse(evidence, file):
    path = evidence.root / file
    path.write_bytes(path.read_bytes() + b"changed")
    failed(evidence, "Stale evidence")


def test_prose_completion_cannot_replace_notification(evidence):
    rows = evidence.transcripts[0]
    evidence.transcripts[0] = [row for row in rows if row.get("origin", {}).get("kind") != "task-notification"]
    evidence.transcripts[0].append({"type": "assistant", "message": {"content": "All workers completed and accepted."}})
    failed(evidence, "actual native completion")


def test_copied_authority_refuses(evidence):
    evidence.states[1]["decisions"] = deepcopy(evidence.states[0]["decisions"])
    failed(evidence, "Accepted checkpoint was not observed")


def test_forged_user_provenance_refuses(evidence):
    evidence.states[0]["decisions"]["human-1"]["provenance"]["source"]["reference"] = "invented"
    failed(evidence, "no matching native user")


def test_dashboard_generation_is_not_rendering(evidence):
    evidence.index["invocations"][0]["dashboard"]["render"] = evidence.transcript("fake-render.jsonl", [{"rendered": True}])
    failed(evidence, "No actual rendered")


def test_interruption_request_is_not_exit(evidence):
    evidence.index["invocations"][0]["interruption"]["interrupt"] = evidence.transcript("not-stopped.jsonl", evidence.pair(
        {"session_id": 1, "chars": "\x03"}, {"session_id": 1}, name="write_stdin"))
    failed(evidence, "no observed terminal exit")


def test_resume_cannot_replace_original_package(evidence):
    path = evidence.root / "before.json"
    before = json.loads(path.read_text())
    before["scope"]["workflow_binding"]["package_digest"] = "0" * 64
    evidence.index["invocations"][0]["interruption"]["before"] = evidence.file("before.json", before)
    failed(evidence, "replaced the original")


def test_missing_resume_remains_not_run(evidence):
    del evidence.index["invocations"][0]["interruption"]
    failed(evidence, "interruption/resume is not_run")


def test_only_label_changes_do_not_count_as_new_inputs(evidence):
    # Test the real aggregate by updating the second package's bound values and
    # all affected hashes, not by relying on an earlier stale-file refusal.
    item = evidence.index["invocations"][1]
    compilation = json.loads(Path(item["compilation"]["path"]).read_text())
    binding_path = evidence.root / compilation["package_path"] / "bindings.json"
    bindings = json.loads(binding_path.read_text())
    first = json.loads((evidence.root / ".taskplane/bootstrap/workflow-1/bindings.json").read_text())
    bindings["values"].update(request=first["values"]["request"], source_files=first["values"]["source_files"])
    ref = evidence.file(str(binding_path), bindings)
    compilation["members"]["bindings.json"] = ref["sha256"]
    compilation["binding_digest"] = live.digest(bindings)
    compilation["digest"] = live.digest({k:v for k,v in compilation.items() if k != "digest"})
    for row in evidence.transcripts[1]:
        for block in row.get("message", {}).get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_result" and 'package_digest' in block.get("content", ""):
                block["content"] = json.dumps({"status": "created", "package_digest": compilation["digest"]})
    evidence.states[1]["scope"]["workflow_binding"]["package_digest"] = compilation["digest"]
    item["compilation"] = evidence.file(item["compilation"]["path"], compilation)
    failed(evidence, "Only labels/output prefixes differ")


def test_bounded_native_slices_and_codex_frames(tmp_path):
    call = {"type": "response_item", "payload": {"type": "function_call", "call_id": "x", "name": "functions.exec",
            "arguments": 'text(await tools.exec_command({cmd:"workflow save --definition test-review"}));'}}
    result = {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "x", "output": '{"exit_code":0,"output":"ok"}'}}
    prefix, selected = b"unselected old history\n", encoded(call) + b"\n" + encoded(result) + b"\n"
    path = tmp_path / "native.jsonl"
    path.write_bytes(prefix + selected + b"unselected later history\n")
    ref = {"path": str(path), "offset": len(prefix), "bytes": len(selected), "sha256": live.digest(selected)}
    transcript = live.Reader(tmp_path).transcript({"segments": [ref]})
    assert len(transcript.command(["workflow save", "test-review"])) == 1


@pytest.fixture
def native_custom_exec_frames(tmp_path):
    session = "578f2c77-b891-4799-9c64-c26d5b1627ff"
    call_id = "call_9b4be4d1bd404076a8ddc61454693421"
    command = (
        'text(await tools.exec_command({cmd:"/Users/example/.local/share/claude/versions/2.1.290 '
        '--ax-screen-reader --plugin-dir /Users/example/cc-plugins/taskplane-2.33.0 '
        f'--session-id {session} -- "+sq(load("wfbLiveAPrompt")),'
        f'workdir:{json.dumps(str(tmp_path))},tty:true,yield_time_ms:1000,max_output_tokens:2000}}));'
    )
    call = {"type": "response_item", "payload": {
        "type": "custom_tool_call", "call_id": call_id, "name": "exec", "input": command}}
    result = {"type": "response_item", "payload": {
        "type": "custom_tool_call_output", "call_id": call_id, "output": [{"type": "text", "text": json.dumps({
            "chunk_id": "native-launch", "wall_time_seconds": 1.001, "session_id": 19159,
            "original_token_count": 0, "output": ""})}]}}
    return session, call, result


def test_native_custom_exec_command_and_tty_launch(tmp_path, native_custom_exec_frames):
    session, call, result = native_custom_exec_frames
    raw = encoded(call) + b"\n" + encoded(result) + b"\n"
    path = tmp_path / "native-custom.jsonl"
    path.write_bytes(raw)
    ref = {"path": str(path), "sha256": live.digest(raw)}
    reader = live.Reader(tmp_path)
    transcript = reader.transcript(ref)
    matches = transcript.command(["--session-id", session])
    assert len(matches) == 1
    assert matches[0][0] == call["payload"]["call_id"]
    assert any(value.get("session_id") == 19159 for value in transcript.values(matches))
    assert live.launch(reader, ref, session, "--session-id")[0] == call["payload"]["call_id"]


@pytest.mark.parametrize("wrapper", ["nested", "assistant_text", "tool_output"])
def test_quoted_custom_exec_frames_are_not_native_calls(tmp_path, native_custom_exec_frames, wrapper):
    session, call, result = native_custom_exec_frames
    quoted = json.dumps([call, result])
    if wrapper == "nested":
        rows = [{"nested": [call, result]}]
    elif wrapper == "assistant_text":
        rows = [{"type": "assistant", "message": {"content": [{"type": "text", "text": quoted}]}}]
    else:
        rows = [{"type": "response_item", "payload": {
            "type": "custom_tool_call_output", "call_id": "outer", "output": [{"type": "text", "text": quoted}]}}]
    raw = b"".join(encoded(row) + b"\n" for row in rows)
    path = tmp_path / "quoted-custom.jsonl"
    path.write_bytes(raw)
    ref = {"path": str(path), "sha256": live.digest(raw)}
    reader = live.Reader(tmp_path)
    assert reader.transcript(ref).command(["--session-id", session]) == []
    with pytest.raises(live.EvidenceError, match="Missing actual TTY tool call/result"):
        live.launch(reader, ref, session, "--session-id")


def test_duplicate_json_and_missing_file_cli_are_json_failures(tmp_path):
    path = tmp_path / "evidence.json"
    path.write_text('{"schema":"x","schema":"y"}')
    assert "Duplicate JSON key" in json.dumps(live.verify(path))
    result = subprocess.run([sys.executable, str(SCRIPT), "--evidence", str(tmp_path / "missing.json")], capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads(result.stdout)["status"] == "fail"
    assert not result.stderr


def test_symlink_evidence_refuses(evidence):
    path = evidence.root / "alias.json"
    path.symlink_to(evidence.root / "definition.json")
    evidence.index["definition"]["path"] = str(path)
    failed(evidence, "Symlink evidence")


def test_native_task_digest_cannot_be_relabelled(evidence):
    evidence.states[0]["workers"]["grant-1-0"]["task_digest"] = "f" * 64
    evidence.states[0]["task_results"]["security"]["task_digest"] = "f" * 64
    failed(evidence, "pinned native task")


def test_saved_definition_must_be_identical(evidence):
    evidence.index["definition"] = evidence.file("different-definition.json", {**evidence.definition, "version": "0.2.0"})
    failed(evidence, "Saved definition changed")


def test_slice_cannot_promote_nested_object_to_native_record(tmp_path):
    body = b'{"type":"assistant","message":{"content":[]}}'
    path = tmp_path / "nested.jsonl"
    path.write_bytes(b'{"nested":' + body + b'}\n')
    with pytest.raises(live.EvidenceError, match="starts inside"):
        live.Reader(tmp_path).transcript({"path": str(path), "offset": 10, "bytes": len(body), "sha256": live.digest(body)})


@pytest.mark.parametrize("value", [[], None, True, 123, "claimed success"])
def test_malformed_index_always_returns_json_result(tmp_path, value):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(value))
    assert live.verify(path)["status"] == "fail"
