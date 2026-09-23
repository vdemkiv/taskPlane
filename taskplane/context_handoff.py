"""Current-visit context delivery and receipts; the Controller still owns every gate."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
import uuid

from . import depgraph, primitives, workflow as w, workflow_evidence as evidence
from .context import Store, digest, encode, signed
from .context_views import PHASE_BYTES, collection, view

CONTRACT = "bounded/v1"
EXCLUSIONS = ["predecessor_conversation", "predecessor_tool_history", "private_runtime",
              "secret_values", "executable_approval"]


def binding(state: dict[str, Any]) -> dict[str, Any]:
    return {**{key: state[key] for key in ("workspace", "root", "run", "revision")},
            "visit": w.current(state)["id"], "scope_digest": primitives.content_fingerprint(state["scope"])}


def text_body(raw: bytes) -> dict[str, Any]:
    try:
        return {"encoding": "utf-8", "text": raw.decode("utf-8")}
    except UnicodeError:
        return {"encoding": "base64", "text": base64.b64encode(raw).decode("ascii")}


def inputs(workspace: Path, state: dict[str, Any], task: str | None) -> tuple[list[dict[str, Any]], list[str], list[str], dict[str, Any]]:
    phase = w.current(state)["phase"]
    rows: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    for visit in state["visits"][:state["index"]]:
        packet = visit.get("packet")
        if visit.get("superseded") or visit["decision"] != "approved" or not packet:
            continue
        rows = packet.get("context", {}).get("tasks", rows)
        accepted.append({"visit": visit["id"], "phase": visit["phase"],
                         "checkpoint": packet["checkpoint"], "evidence_digest": digest(packet)})
        # Preserve the normative predecessor output and referenced artifact bodies,
        # without embedding executable decision envelopes or host transcripts.
        items.append({"id": "accepted/" + visit["id"], "kind": "accepted-output",
                      "body": {k:v for k,v in packet["output"].items() if k != "context_receipt"}})
        for number, check in enumerate(packet["output"].get("build_checks", [])):
            if isinstance(check, dict) and isinstance(check.get("reuse_ref"), dict):
                from .context_reuse import inherited
                items.append({"id": f"verification/{visit['id']}/{number}", "kind": "verification",
                              "body": inherited(workspace, check["reuse_ref"])})
        for relative in sorted(packet["manifest"]):
            raw = evidence.read(workspace, relative)
            try:
                document = json.loads(raw)
            except (ValueError, UnicodeError):
                document = None
            if isinstance(document, dict) and document.get("schema") == "taskplane.phase-output/v1":
                continue  # The exact normative body is already represented above.
            if relative == packet.get("context", {}).get("tasks_path"):
                continue  # Task definitions are selected separately; observations are not normative.
            items.append({"id": "artifact/" + relative, "kind": "accepted-artifact",
                          "body": {"path": relative, **text_body(raw)}})
    default_tasks = workspace / ".taskplane/tasks.json"
    if not rows and default_tasks.exists():
        rows = evidence.object_file(workspace, ".taskplane/tasks.json").get("tasks", [])
    selected = [row for row in rows if row.get("phase", phase) == phase]
    if task:
        selected = [row for row in selected if row["id"] == task]
        w.require(selected, "invalid_context", "Task is not part of this current phase.")
    criteria = sorted({c for row in selected for c in evidence.task_criteria(row)}) or state["scope"]["criteria"]
    paths = sorted({p for row in selected for p in row.get("paths", [])}) if selected else state["scope"]["paths"][phase]
    items.insert(0, {"id": "requirements-and-scope", "kind": "requirements",
                    "body": {"goal": state.get("goal", ""), "criteria": criteria, "phase": phase, "paths": paths,
                             "tasks": [{k:v for k,v in row.items() if k not in evidence.TASK_OBSERVATIONS}
                                       for row in selected]}})
    source_paths = sorted(set(paths) | set(state["scope"].get("verification_inputs", [])))
    for relative in source_paths:
        if relative.startswith(".taskplane/"):
            continue
        target = evidence.path(workspace, relative)
        if not target.exists():
            continue
        raw = evidence.read(workspace, relative)
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            value = None
        if isinstance(value, dict) and (value.get("schema") == "taskplane.phase-output/v1" or "tasks" in value):
            continue  # Avoid making an output/receipt depend on its own bytes.
        items.append({"id": "source/" + relative, "kind": "source", "required": False,
                      "body": {"path": relative, **text_body(raw)}})
    graph = depgraph.load(str(workspace))
    current_graph = bool(graph) and depgraph.source_inputs_current(str(workspace), graph)
    # Graph transport is semantic source structure. Scan timestamps and output
    # JSON digests must not make a receipt depend on its own serialization.
    graph_body = {key: graph.get(key, {}) for key in ("modules", "edges", "components", "recorded")}
    graph_body["files"] = {name: {k:v for k,v in row.items() if k not in {"mtime", "mtime_ns"}}
                           for name, row in graph.get("files", {}).items()}
    items.append({"id": "source-graph", "kind": "graph", "body": graph_body, "required": False})
    authority = {"binding": binding(state), "phase": phase, "decision": w.current(state)["decision"],
                 "criteria": criteria, "paths": paths, "accepted_inputs": accepted,
                 "context_is_authority": False}
    coverage = {"graph": "current" if current_graph else "unknown_or_stale",
                "request_text": "supplied" if state.get("goal") else "not_recorded",
                "untrusted_bodies": True, "host_conversation": "not_removed",
                "model_attention": "unobservable"}
    return items, [row["id"] for row in selected], criteria, {"authority": authority, "coverage": coverage}


class Session:
    def __init__(self, workspace: Path, state: dict[str, Any], task: str | None = None):
        w.require(state.get("run") and state.get("visits") and not state.get("superseded_by"),
                  "invalid_context", "Context requires the current bound run.")
        self.workspace, self.state = workspace.resolve(), state
        self.store = Store(self.workspace)
        self.binding = binding(state)
        self.phase = w.current(state)["phase"]
        self.items, task_ids, criteria, metadata = inputs(self.workspace, state, task)
        self.view = view(self.store, self.binding, self.phase, task_ids, criteria,
                         metadata["authority"], self.items, metadata["coverage"])
        self.view_ref = self.store.put("context-view", self.view)
        self.read_binding = {**self.binding, "source_key": self.view["source_key"]}
        self.required = [{"id": item["id"], "ref": self.store.put(item.get("kind", "input"), item["body"])}
                         for item in self.items if item.get("required", True)]
        self.handoff = signed({"schema": "taskplane.context-handoff/v1", "binding": self.binding,
                               "view_ref": self.view_ref,
                               "required_inputs": collection(self.store, self.required, "required-inputs", 0),
                               "accepted_inputs": collection(self.store, metadata["authority"]["accepted_inputs"], "accepted-inputs", 0),
                               "unknowns": metadata["coverage"], "exclusions": EXCLUSIONS})
        self.handoff_ref = self.store.put("context-handoff", self.handoff)
        self.refs = [self.store.put(item.get("kind", "input"), item["body"]) for item in self.items]
        self.refs += [self.view_ref, self.handoff_ref, self.view["authority_ref"]]
        for field in (self.view["references"], self.view["required_inputs"], self.handoff["accepted_inputs"]):
            if field.get("details"):
                self.refs.append(field["details"])
        self.store.register(self.read_binding, self.refs)
        self.required_trees = {item["ref"]["sha256"]: self.store.descendants(item["ref"])
                               for item in self.required}
        self.allowed: set[str] | None = None
        self.ledger_path = evidence.path(self.workspace,
            f".taskplane/context-v1/delivery/{self.handoff_ref['sha256']}.json")

    def descriptor(self) -> dict[str, Any]:
        return {"status": "ready", "contract": self.state.get("context_contract", "legacy"),
                "source_key": self.view["source_key"],
                "handoff_ref": self.handoff_ref, "view_ref": self.view_ref,
                "required_inputs": len(self.required), "phase": self.phase,
                "next_action": "flow context --consume " + self.handoff_ref["sha256"]}

    def ledger(self) -> dict[str, Any]:
        if not self.ledger_path.exists():
            return {"binding": self.binding, "seen": [], "pages": {}, "receipts": [], "returned_bytes": 0}
        w.require(self.ledger_path.stat().st_size <= 8 * 1024 * 1024,
                  "context_overflow", "Context delivery ledger is oversized.")
        value = evidence.object_file(self.workspace, str(self.ledger_path.relative_to(self.workspace)))
        w.require(value.get("binding") == self.binding, "invalid_context", "Foreign delivery receipt binding.")
        return value

    def _deliver(self, payload: dict[str, Any], seen: set[str],
                 page: tuple[str, int, int] | None = None, *, limit: int = 16384) -> dict[str, Any]:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with primitives.file_lock(str(self.ledger_path)):
            ledger = self.ledger()
            seen |= set(ledger["seen"])
            if page:
                key, number, total = page
                pages = sorted(set(ledger["pages"].get(key, [])) | {number})
                ledger["pages"][key] = pages
                if pages == list(range(total)):
                    seen.add(key)
            returned = sorted(key for key, nodes in self.required_trees.items() if nodes <= seen)
            returned = sorted(set(returned))
            receipt: dict[str, Any] = {"schema": "taskplane.context-delivery-receipt/v1",
                "binding": self.binding, "handoff_digest": self.handoff["digest"],
                "view_digest": self.view["digest"], "returned_refs": returned,
                "returned_bytes": ledger["returned_bytes"], "event_id": "returned/" + uuid.uuid4().hex,
                "status": "returned", "model_attention": "unobservable"}
            response: dict[str, Any] = {**payload, "remaining_required": len({x['ref']['sha256'] for x in self.required}) - len(returned)}
            # Account for the receipt envelope too; digest values have a fixed width.
            for _ in range(4):
                reference = self.store.put("delivery-receipt", receipt)
                response["context_receipt"] = {"receipt": reference, "consumed_inputs": returned}
                total_bytes = ledger["returned_bytes"] + len(encode(response))
                if total_bytes == receipt["returned_bytes"]:
                    break
                receipt["returned_bytes"] = total_bytes
            else:
                raise w.Refusal("invalid_context", "Receipt byte accounting did not converge.")
            # A rejected envelope was never returned. Do not let it add seen
            # bodies or issued receipts to the delivery ledger.
            w.require(len(encode(response)) < limit, "context_overflow", "Returned context exceeds its envelope budget.")
            ledger.update(seen=sorted(seen), returned_bytes=receipt["returned_bytes"])
            ledger["receipts"].append(reference["sha256"])
            w.require(len(ledger["receipts"]) <= 10000, "context_overflow", "Current delivery event limit reached.")
            primitives.atomic_json(self.ledger_path, ledger)
            self.store.register(self.read_binding, [reference])
            return response

    def consume(self, key: str) -> dict[str, Any]:
        w.require(key == self.handoff_ref["sha256"], "invalid_context", "Stale or foreign handoff.")
        seen = set()
        for item in self.items:
            if item["id"] in self.view["inline"]:
                seen |= self.store.descendants(self.store.put(item.get("kind", "input"), item["body"]))
        response = self._deliver({"schema": "taskplane.context-consumption/v1", "view": self.view}, seen,
                                 limit=PHASE_BYTES[self.phase])
        w.require(len(encode(response)) <= PHASE_BYTES[self.phase], "context_overflow", "Consumed view exceeds phase budget.")
        return response

    def read(self, key: str, page: int = 0, section: str | None = None) -> dict[str, Any]:
        if self.allowed is None:
            self.allowed = set()
            for ref in self.store.roots(self.read_binding):
                self.allowed |= self.store.descendants(ref)
        w.require(key in self.allowed, "invalid_context", "Reference was not supplied for this current binding.")
        result = self.store.page(key, page, section)
        response = self._deliver({"schema": "taskplane.context-read/v1", "page": result}, set(),
                                (key, page, result["pages"]) if section is None else None)
        w.require(len(encode(response)) <= 16384, "context_overflow", "Read envelope exceeds its page budget.")
        return response

    def validate(self, value: Any) -> None:
        w.require(isinstance(value, dict) and isinstance(value.get("receipt"), dict),
                  "invalid_context", "Phase output needs its returned context receipt.")
        receipt = self.store.resolve(value["receipt"])
        w.require(isinstance(receipt, dict) and receipt.get("schema") == "taskplane.context-delivery-receipt/v1"
                  and receipt.get("status") == "returned" and receipt.get("binding") == self.binding
                  and receipt.get("handoff_digest") == self.handoff["digest"]
                  and receipt.get("view_digest") == self.view["digest"]
                  and value["receipt"]["sha256"] in self.ledger()["receipts"],
                  "invalid_context", "Receipt was not returned for this current handoff.")
        required = sorted({item["ref"]["sha256"] for item in self.required})
        w.require(receipt.get("returned_refs") == required and value.get("consumed_inputs") == required,
                  "invalid_context", "Required inherited context has not been returned to the consumer.")


def consume_required(session: Session) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Programmatic consumer using the same public operations; retain every returned body."""
    responses = [session.consume(session.handoff_ref["sha256"])]
    for item in session.required:
        for key in sorted(session.store.descendants(item["ref"])):
            if key in session.ledger()["seen"]:
                continue
            first = session.read(key)
            responses.append(first)
            for page in range(1, first["page"]["pages"]):
                responses.append(session.read(key, page))
    return responses[-1]["context_receipt"], responses
