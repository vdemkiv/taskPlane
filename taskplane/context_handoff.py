"""Current-visit context delivery and receipts; the Controller still owns every gate."""
from __future__ import annotations

import base64
from copy import deepcopy
import json
import shlex
from pathlib import Path
from typing import Any
import uuid

from . import depgraph, primitives, workflow as w, workflow_evidence as evidence
from .context import Store, digest, encode, signed, PAGE_LIMIT
from .context_views import PHASE_BYTES, collection, view

CONTRACT = "bounded/v1"
SEMANTIC_CONTRACT = "bounded/v2"
EXCLUSIONS = ["predecessor_conversation", "predecessor_tool_history", "private_runtime",
              "secret_values", "executable_approval"]


def consumed_inputs(keys: list[str]) -> list[str] | dict[str, Any]:
    """Bound receipt metadata; the immutable receipt retains every returned root."""
    if len(keys) <= 64:
        return keys
    return {"schema": "taskplane.consumed-inputs/v1", "count": len(keys), "sha256": digest(keys)}


def binding(state: dict[str, Any]) -> dict[str, Any]:
    return {**{key: state[key] for key in ("workspace", "root", "run", "revision")},
            "visit": w.current(state)["id"], "scope_digest": primitives.content_fingerprint(state["scope"]),
            **({"task_generation": state["task_generation"]} if "task_generation" in state else {})}


def context_action(workspace: Path, run: str, task: str | None = None, *,
                   operation: str | None = None, key: str | None = None) -> str:
    """An executable logical command retaining exactly this consumer's selectors."""
    args = ["flow", "context", "--workspace", str(workspace.resolve()), "--run", run]
    if task is not None:
        args += ["--task", task]
    if operation is not None and key is not None:
        args += ["--" + operation, key]
    return shlex.join(args)


def text_body(raw: bytes) -> dict[str, Any]:
    try:
        return {"encoding": "utf-8", "text": raw.decode("utf-8")}
    except UnicodeError:
        return {"encoding": "base64", "text": base64.b64encode(raw).decode("ascii")}


def inputs(workspace: Path, state: dict[str, Any], task: str | None) -> tuple[list[dict[str, Any]], list[str], list[str], dict[str, Any]]:
    phase = w.current(state)["phase"]
    rows = evidence.context_tasks(state)
    accepted: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    semantic = state.get("context_contract") == SEMANTIC_CONTRACT
    artifact_ids: set[str] = set()
    for visit in state["visits"][:state["index"]]:
        packet = visit.get("packet")
        if visit.get("superseded") or visit["decision"] != "approved" or not packet:
            continue
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
            if semantic and relative in artifact_ids:
                continue
            artifact_ids.add(relative)
            raw = evidence.read(workspace, relative)
            try:
                document = json.loads(raw)
            except (ValueError, UnicodeError):
                document = None
            if isinstance(document, dict) and document.get("schema") == "taskplane.phase-output/v1":
                continue  # The exact normative body is already represented above.
            if relative == packet.get("context", {}).get("tasks_path"):
                continue  # Task definitions are selected separately; observations are not normative.
            # v2 keeps every normative output required. Supporting bytes remain
            # accessible through bound, digest-verified refs; authors can mark a
            # particular artifact required for specific downstream phases.
            declaration: dict[str, Any] = next((a for a in packet["output"].get("artifacts", [])
                                if a.get("path") == relative), {})
            required = (not semantic or declaration.get("kind") not in {"verification", "supporting", "raw-log", "source"}
                        or phase in declaration.get("required_for", []))
            items.append({"id": "artifact/" + relative, "kind": "accepted-artifact", "required": required,
                          "body": {"path": relative, **text_body(raw)}})
    # A workspace-global task file may belong to a different run. Initial tasks
    # are frozen by start; accepted packets provide their later definitions.
    selected = [row for row in rows if row.get("phase", phase) == phase]
    if task:
        selected = [row for row in selected if row["id"] == task]
        w.require(selected, "invalid_context", "Task is not part of this current phase.")
        for dependency in selected[0].get("dependencies", []):
            result = state.get("task_results", {}).get(dependency)
            if not result:
                continue  # Earlier-phase evidence is already supplied above.
            items.append({"id": "prerequisite/" + dependency, "kind": "prerequisite-result",
                          "body": {"task": dependency, "result": deepcopy(result)}})
            for relative in result.get("outputs", result.get("manifest", {})):
                items.append({"id": "prerequisite-artifact/" + dependency + "/" + relative,
                              "kind": "prerequisite-artifact",
                              "body": {"path": relative, **text_body(evidence.read(workspace, relative))}})
    criteria = sorted({c for row in selected for c in evidence.task_criteria(row)}) or state["scope"]["criteria"]
    paths = (sorted({p for row in selected for p in row.get("paths", [])}
                    & set(state["scope"]["paths"][phase])) if selected else state["scope"]["paths"][phase])
    items.insert(0, {"id": "requirements-and-scope", "kind": "requirements",
                    "body": {"goal": state.get("goal", ""), "criteria": criteria, "phase": phase, "paths": paths,
                             "tasks": [{k:v for k,v in row.items() if k not in evidence.TASK_OBSERVATIONS}
                                       for row in selected]}})
    declared_reads = ({p for row in selected for p in evidence.read_inputs(state, row)}
                      if selected else set(state["scope"].get("verification_inputs", [])))
    source_paths = sorted(set(paths) | declared_reads)
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
                      "priority": 0 if any("read_inputs" in row and relative in row["read_inputs"] for row in selected) else 1,
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
                "model_attention": "unobservable", "supporting_artifacts": "referenced" if semantic else "required"}
    return items, [row["id"] for row in selected], criteria, {"authority": authority, "coverage": coverage}


class Session:
    def __init__(self, workspace: Path, state: dict[str, Any], task: str | None = None, *,
                 consumer: dict[str, Any] | None = None, snapshot: dict[str, Any] | None = None):
        w.require(state.get("run") and state.get("visits") and not state.get("superseded_by"),
                  "invalid_context", "Context requires the current bound run.")
        self.workspace, self.state, self.task = workspace.resolve(), state, task
        self.store = Store(self.workspace)
        self.binding = binding(state)
        self.phase = w.current(state)["phase"]
        if snapshot is None:
            self.items, task_ids, criteria, metadata = inputs(self.workspace, state, task)
        else:
            w.require(snapshot.get("binding") == self.binding and snapshot.get("task") == task,
                      "invalid_context", "Frozen task inputs belong to another grant revision.")
            self.items, task_ids, criteria, metadata = deepcopy(snapshot["inputs"])
        self.frozen = {"binding": deepcopy(self.binding), "task": task,
                       "inputs": deepcopy([self.items, task_ids, criteria, metadata])}
        if consumer is not None:
            w.require(set(consumer) == {"worker_id", "grant_id", "attempt", "task_id", "task_generation"}
                      and consumer["task_id"] == task and bool(consumer["worker_id"])
                      and consumer["task_generation"] == state.get("task_generation", 0),
                      "invalid_context", "Worker context needs its bound consumer identity.")
            self.binding = {**self.binding, "consumer": deepcopy(consumer)}
            metadata["authority"] = {**metadata["authority"], "binding": self.binding}
        self.input_refs = {item["id"]: self.store.put(item.get("kind", "input"), item["body"])
                           for item in self.items}
        self.view = view(self.store, self.binding, self.phase, task_ids, criteria,
                         metadata["authority"], self.items, metadata["coverage"],
                         _prepared_refs=self.input_refs)
        self.view_ref = self.store.put("context-view", self.view)
        self.read_binding = {**self.binding, "source_key": self.view["source_key"]}
        self.required = [{"id": item["id"], "ref": self.input_refs[item["id"]]}
                         for item in self.items if item.get("required", True)]
        self.handoff = signed({"schema": "taskplane.context-handoff/v1", "binding": self.binding,
                               "view_ref": self.view_ref,
                               "required_inputs": collection(self.store, self.required, "required-inputs", 0),
                               "accepted_inputs": collection(self.store, metadata["authority"]["accepted_inputs"], "accepted-inputs", 0),
                               "unknowns": metadata["coverage"], "exclusions": EXCLUSIONS})
        self.handoff_ref = self.store.put("context-handoff", self.handoff)
        self.refs = list(self.input_refs.values())
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
                "required_inputs": len(self.required), "phase": self.phase, "preflight": self.preflight(),
                "next_action": context_action(self.workspace, self.state["run"], self.task,
                    operation="drain" if self.task and any(row["id"] == self.task and "read_inputs" in row
                        for row in evidence.context_tasks(self.state)) else "consume", key=self.handoff_ref["sha256"])}

    def preflight(self) -> dict[str, Any]:
        required = {item["ref"]["sha256"]: item for item in self.required}
        nodes = set().union(*self.required_trees.values()) if self.required_trees else set()
        bodies = {self.input_refs[item["id"]]["sha256"]: item["body"] for item in self.items}
        sizes = sorted(({"id": item["id"], "bytes": len(encode(bodies[key]))} for key, item in required.items()),
                       key=lambda item: (-item["bytes"], item["id"]))
        return {"required_roots": len(required), "required_body_bytes": sum(item["bytes"] for item in sizes),
                "required_pages": sum(self.store.page(key)["pages"] for key in nodes),
                "supporting_roots": sum(not item.get("required", True) for item in self.items),
                "largest_required": sizes[:5], "basis": "Unique required bodies; page envelopes add transport cost."}

    def ledger(self) -> dict[str, Any]:
        if not self.ledger_path.exists():
            return {"binding": self.binding, "seen": [], "pages": {}, "receipts": [], "returned_bytes": 0}
        w.require(self.ledger_path.stat().st_size <= 8 * 1024 * 1024,
                  "context_overflow", "Context delivery ledger is oversized.")
        value = evidence.object_file(self.workspace, str(self.ledger_path.relative_to(self.workspace)))
        w.require(value.get("binding") == self.binding, "invalid_context", "Foreign delivery receipt binding.")
        return value

    def _preview(self, payload: dict[str, Any], ledger: dict[str, Any], seen: set[str],
                 updates: list[tuple[str, int, int]], *, event_id: str,
                 persist: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
        from copy import deepcopy
        ledger = deepcopy(ledger)
        seen = seen | set(ledger["seen"])
        for key, number, total in updates:
            pages = sorted(set(ledger["pages"].get(key, [])) | {number})
            ledger["pages"][key] = pages
            if pages == list(range(total)):
                seen.add(key)
        returned = sorted(key for key, nodes in self.required_trees.items() if nodes <= seen)
        receipt: dict[str, Any] = {"schema": "taskplane.context-delivery-receipt/v1",
            "binding": self.binding, "handoff_digest": self.handoff["digest"],
            "view_digest": self.view["digest"], "returned_refs": returned,
            "returned_bytes": ledger["returned_bytes"], "event_id": event_id,
            "status": "returned", "model_attention": "unobservable"}
        response: dict[str, Any] = {**payload, "remaining_required": len(self.required_trees) - len(returned)}
        if payload.get("schema") == "taskplane.context-drain/v1":
            response["done"] = response["remaining_required"] == 0
            response["next_action"] = None
        if response["remaining_required"]:
            response["next_action"] = context_action(self.workspace, self.state["run"], self.task,
                operation="drain" if "done" in response else "read-required", key=self.handoff_ref["sha256"])
        for _ in range(4):
            reference = (self.store.put if persist else self.store.reference)("delivery-receipt", receipt)
            response["context_receipt"] = {"receipt": reference, "consumed_inputs": consumed_inputs(returned)}
            total_bytes = ledger["returned_bytes"] + len(encode(response))
            if total_bytes == receipt["returned_bytes"]:
                break
            receipt["returned_bytes"] = total_bytes
        else:
            raise w.Refusal("invalid_context", "Receipt byte accounting did not converge.")
        ledger.update(seen=sorted(seen), returned_bytes=receipt["returned_bytes"])
        ledger["receipts"].append(reference["sha256"])
        return response, ledger

    def _deliver(self, payload: dict[str, Any], seen: set[str],
                 page: tuple[str, int, int] | None = None, *, limit: int = 16384,
                 page_updates: list[tuple[str, int, int]] | None = None) -> dict[str, Any]:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with primitives.file_lock(str(self.ledger_path)):
            response, ledger = self._preview(payload, self.ledger(), seen,
                ([page] if page else []) + (page_updates or []), event_id="returned/" + uuid.uuid4().hex,
                persist=True)
            # Preview may create immutable objects, but only a returned response
            # commits delivery. A refusal cannot grant a receipt or advance pages.
            w.require(len(encode(response)) < limit, "context_overflow", "Returned context exceeds its envelope budget.")
            w.require(len(ledger["receipts"]) <= 10000, "context_overflow", "Current delivery event limit reached.")
            primitives.atomic_json(self.ledger_path, ledger)
            self.store.register(self.read_binding, [response["context_receipt"]["receipt"]])
            return response

    def consume(self, key: str) -> dict[str, Any]:
        w.require(key == self.handoff_ref["sha256"], "invalid_context", "Stale or foreign handoff.")
        seen = set()
        for item in self.items:
            if item["id"] in self.view["inline"]:
                ref = self.input_refs[item["id"]]
                seen |= self.required_trees[ref["sha256"]] if ref["sha256"] in self.required_trees else self.store.descendants(ref)
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

    def drain(self, key: str) -> dict[str, Any]:
        """Bound the combined bodies and receipt, not each page independently."""
        return self.read_required(key, _drain=True)

    def read_required(self, key: str, *, _drain: bool = False) -> dict[str, Any]:
        """Return a bounded prefix of missing required pages, never just their IDs."""
        w.require(key == self.handoff_ref["sha256"], "invalid_context", "Stale or foreign handoff.")
        ledger = self.ledger()
        missing = sorted(set().union(*self.required_trees.values()) - set(ledger["seen"]))
        first_pages = {sha: self.store.page(sha) for sha in missing}
        # Return large nodes while the completed-root receipt is still small.
        # Otherwise a late large node can be stranded behind hundreds of roots.
        missing.sort(key=lambda sha: (-len(encode(first_pages[sha])), sha))
        pages: list[dict[str, Any]] = []
        updates: list[tuple[str, int, int]] = []
        limit = 32768 if _drain else PAGE_LIMIT
        payload = {"schema": "taskplane.context-drain/v1" if _drain else "taskplane.context-read-batch/v1",
                   "handoff_ref": self.handoff_ref, "pages": pages}
        # Size the receipt for precisely the candidate pages, not every eventual
        # root. Hash/event values have fixed widths; byte counts are converged by
        # the same serializer as delivery. This never updates the read ledger.
        full = False
        for sha in missing:
            first = first_pages[sha]
            for number in range(first["pages"]):
                if number in ledger["pages"].get(sha, []):
                    continue
                page = first if number == 0 else self.store.page(sha, number)
                candidate, _ = self._preview({**payload, "pages": [*pages, page]}, ledger, set(),
                    [*updates, (sha, number, page["pages"])], event_id="returned/" + "0" * 32)
                if len(pages) == 64 or len(encode(candidate)) >= limit:
                    full = True
                    break
                pages.append(page)
                updates.append((sha, number, page["pages"]))
            if full:
                break
        w.require(not missing or pages, "context_overflow", "No required page fits the batch response budget.")
        return self._deliver(payload, set(), page_updates=updates, limit=limit)

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
        # Exact legacy lists remain valid, including receipts issued before this
        # compact representation. Neither form can replace the bound ledger proof.
        w.require(receipt.get("returned_refs") == required
                  and value.get("consumed_inputs") in (required, consumed_inputs(required)),
                  "invalid_context", "Required inherited context has not been returned to the consumer.")


def consume_required(session: Session) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Programmatic consumer using the same public operations; retain every returned body."""
    responses = [session.consume(session.handoff_ref["sha256"])]
    while responses[-1]["remaining_required"]:
        responses.append(session.read_required(session.handoff_ref["sha256"]))
    return responses[-1]["context_receipt"], responses
