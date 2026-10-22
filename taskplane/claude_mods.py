"""Claude Mod projections and cooperative observed-UI decisions.

The ledger is delivery evidence, never workflow authority. Lock order is ledger
then controller; verification reads the atomic ledger without locking it again.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, cast

from . import primitives, storage, workflow as w, workflow_evidence as evidence
from . import workflow_host as host, workflow_local

SNAPSHOT = "taskplane.claude-mods-snapshot/v1"
REQUEST = "taskplane.claude-mods-request/v1"
RESPONSE = "taskplane.claude-mods-response/v1"
INTENT = "taskplane.claude-mods-intent/v1"
LEDGER = "taskplane.claude-mods-ledger/v1"
ENVELOPE = "taskplane.observed-mods-decision/v1"
OBSERVATION = "taskplane.mods-ui-observation/v1"
MAX_BYTES = 1024 * 1024
MAX_ENVELOPE = 16384
BINDING_KEYS = {"workspace", "root", "run", "revision", "visit", "checkpoint", "manifest_digest", "scope_digest"}
CONTROL_KEYS = {"plugin", "component", "element", "request_id", "surface", "session"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return primitives.content_fingerprint(value)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        w.require(key not in result, "invalid_evidence", "Duplicate JSON fields are refused.")
        result[key] = value
    return result


def _decode(raw: str, limit: int) -> dict[str, Any]:
    w.require(isinstance(raw, str) and 0 < len(raw.encode("utf-8")) <= limit,
              "invalid_evidence", "Mod JSON exceeds its size bound.")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    except (ValueError, TypeError, RecursionError):
        raise w.Refusal("invalid_evidence", "Mod JSON must be a finite, unambiguous object.") from None
    w.require(isinstance(value, dict), "invalid_evidence", "Mod JSON must be an object.")
    return cast(dict[str, Any], value)


def read_request(raw: str) -> dict[str, Any]:
    request = _decode(raw, MAX_ENVELOPE)
    operation = request.get("operation")
    fields = {"prepare": {"binding", "choice", "comment"},
              "record_ui": {"payload_digest", "control"}, "receipt": set()}
    w.require(request.get("schema") == REQUEST and isinstance(operation, str) and operation in fields,
              "invalid_evidence", "Unsupported Mod request schema or operation.")
    w.require(set(request) == {"schema", "operation", "intent_id"} | fields[cast(str, operation)],
              "invalid_evidence", "Mod request fields do not match its operation.")
    w.require(isinstance(request["intent_id"], str) and
              re.fullmatch(r"[a-f0-9]{32}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", request["intent_id"]),
              "invalid_evidence", "Use a fresh random UUID for the immutable attempt.")
    if operation == "prepare":
        binding = request["binding"]
        w.require(isinstance(binding, dict) and set(binding) == BINDING_KEYS
                  and type(binding["revision"]) is int and binding["revision"] >= 0
                  and all(isinstance(binding[k], str) and 0 < len(binding[k]) <= 4096
                          for k in BINDING_KEYS - {"revision"}),
                  "decision_binding", "Supply the complete displayed checkpoint binding.")
        w.require(isinstance(request["choice"], str) and request["choice"] in {"approved", "changes_requested"}
                  and isinstance(request["comment"], str), "invalid_evidence", "Invalid choice or exact comment.")
    return request


def ledger_path(workspace: Path, root: str) -> Path:
    key = hashlib.sha256(root.encode()).hexdigest()[:32]
    return storage.runtime_file(str(workspace), f"claude-mods-{key}.json")


def _read(workspace: Path, root: str) -> dict[str, Any]:
    path = ledger_path(workspace, root)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return {"schema": LEDGER, "workspace": str(workspace), "root": root, "intents": {}}
    with os.fdopen(fd, "rb") as stream:
        w.require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "state_unavailable", "Mod ledger must be a regular file.")
        raw = stream.read(MAX_BYTES + 1)
    try:
        value = _decode(raw.decode("utf-8"), MAX_BYTES)
    except UnicodeError:
        raise w.Refusal("state_unavailable", "Invalid ledger encoding.") from None
    w.require(set(value) == {"schema", "workspace", "root", "intents"}
              and value["schema"] == LEDGER and value["workspace"] == str(workspace)
              and value["root"] == root and isinstance(value["intents"], dict),
              "state_unavailable", "Mod ledger identity is invalid.")
    for key, record in value["intents"].items():
        w.require(isinstance(record, dict) and isinstance(record.get("intent"), dict)
                  and record["intent"].get("intent_id") == key
                  and set(record) in ({"intent", "presentation"}, {"intent", "presentation", "observation", "envelope"}),
                  "state_unavailable", "Mod ledger contains an invalid attempt.")
        intent = record["intent"]
        read_request(_json({k: intent.get(k) for k in ("intent_id", "binding", "choice", "comment")} |
                           {"schema": REQUEST, "operation": "prepare"}))
        w.require(set(intent) == {"schema", "intent_id", "binding", "choice", "comment", "payload_digest", "created_at"}
                  and intent.get("schema") == INTENT and intent.get("payload_digest") == _hash(_payload(intent))
                  and record.get("presentation") == _presentation(intent),
                  "state_unavailable", "Immutable Mod intent or presentation changed.")
        workflow_local.timestamp(intent.get("created_at"))
    return value


def _write(workspace: Path, root: str, value: dict[str, Any]) -> None:
    w.require(len(json.dumps(value, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()) <= MAX_BYTES, "state_unavailable",
              "Mod ledger is full; unresolved attempts are retained.")
    primitives.atomic_write_json(str(ledger_path(workspace, root)), value, indent=None, private=True)


def _payload(value: dict[str, Any]) -> dict[str, Any]:
    return {k: deepcopy(value[k]) for k in ("intent_id", "binding", "choice", "comment")}


def _presentation(intent: dict[str, Any]) -> dict[str, Any]:
    label = "Accept" if intent["choice"] == "approved" else "Request changes"
    text = (label + " checkpoint\n" + _json(intent["binding"]) + "\nComment:\n" + intent["comment"])
    w.require(len(text) <= 4096, "invalid_evidence", "Complete confirmation exceeds 4,096 characters; shorten the draft explicitly.")
    return {"schema": "taskplane.mods-presentation/v1", "binding": deepcopy(intent["binding"]),
            "choice": intent["choice"], "label": label, "comment": intent["comment"],
            "text": text, "digest": _hash(text), "at": intent["created_at"],
            "reference": "mods/" + intent["intent_id"]}


def _control(control: Any, intent: dict[str, Any], root: str) -> None:
    expected = "confirm-accept" if intent["choice"] == "approved" else "confirm-changes"
    w.require(isinstance(control, dict) and set(control) == CONTROL_KEYS
              and control["plugin"] == "taskplane" and control["component"] == "checkpoint"
              and control["element"] == expected and control["session"] == root
              and isinstance(control["surface"], str) and control["surface"] in {"terminal", "desktop"}
              and isinstance(control["request_id"], str) and 0 < len(control["request_id"]) <= 256,
              "decision_provenance", "Callback control or session does not match the frozen confirmation.")


def _envelope(record: dict[str, Any]) -> dict[str, Any]:
    observation = record["observation"]
    return {"schema": ENVELOPE, "intent_id": record["intent"]["intent_id"],
            "event_id": observation["event_id"], "binding": deepcopy(record["intent"]["binding"]),
            "observation_digest": _hash(observation), "presentation": deepcopy(record["presentation"])}


def _verified(record: dict[str, Any]) -> dict[str, Any]:
    intent, observation = record["intent"], record["observation"]
    return {"event_id": observation["event_id"], "human": True, "automatic": False,
            "choice": intent["choice"], "binding": deepcopy(intent["binding"]), "assurance": "observed",
            "provenance": {"source": {"kind": "mods_ui", "reference": observation["event_id"],
                "conversation": intent["binding"]["root"], "actor": "user", "automatic": False,
                "observed_at": observation["observed_at"], "assurance": "observed_ui"},
                "recorder": "mods_ui_callback", "excerpt": record["presentation"]["text"],
                "comment": intent["comment"], "presentation": deepcopy(record["presentation"]),
                "observation": deepcopy(observation), "observation_digest": _hash(observation),
                "recorded_at": observation["observed_at"], "chronology": "verified/v1"}}


class ModsLocalAdapter(host.LocalAdapter):
    """Explicit observed UI verifier; ordinary conversation schemas delegate."""

    def verify_decision_context(self, reference: str, expected: dict[str, Any],
                                prior_decisions: dict[str, Any]) -> dict[str, Any]:
        try:
            candidate = json.loads(reference)
        except (ValueError, TypeError):
            return super().verify_decision_context(reference, expected, prior_decisions)
        if not isinstance(candidate, dict) or candidate.get("schema") != ENVELOPE:
            return super().verify_decision_context(reference, expected, prior_decisions)
        value = _decode(reference, MAX_ENVELOPE)
        w.require(self.name == "claude" and self.profile == "native_workflow",
                  "unsupported_authority", "Observed Mod decisions require Claude native workflow.")
        record = _read(self.workspace, self.root)["intents"].get(value.get("intent_id"))
        w.require(record is not None and record.get("observation") and record.get("envelope") == value
                  and value == _envelope(record), "decision_provenance", "Saved Mod observation or envelope changed.")
        intent, observation = record["intent"], record["observation"]
        _control(observation.get("control"), intent, self.root)
        w.require(observation == {"schema": OBSERVATION, "event_id": "mods/" + intent["intent_id"],
            "intent": deepcopy(intent), "presentation": deepcopy(record["presentation"]),
            "control": deepcopy(observation["control"]), "observed_at": observation["observed_at"],
            "assurance": "observed_ui", "preview": False},
            "decision_provenance", "Observation does not preserve the complete frozen intent.")
        verified = _verified(record)
        prior = prior_decisions.get(verified["event_id"])
        w.require(intent["binding"] == (prior["binding"] if prior else expected),
                  "decision_binding", "Mod decision has a stale or foreign checkpoint binding.")
        w.require(intent["binding"]["workspace"] == str(self.workspace) and intent["binding"]["root"] == self.root,
                  "decision_binding", "Mod decision has a foreign owner.")
        db = evidence.object_file(self.workspace, ".taskplane/" + self.filename)
        run = db.get("runs", {}).get(intent["binding"]["run"], {})
        stage: dict[str, Any] = next((v for v in run.get("visits", []) if v["id"] == intent["binding"]["visit"]), {})
        timestamp = workflow_local.timestamp
        w.require(timestamp(run.get("started_at")) <= timestamp(stage.get("submitted_at"))
                  <= timestamp(intent["created_at"]) <= timestamp(observation["observed_at"]) <= timestamp(_now()),
                  "decision_chronology", "Confirmation must follow this checkpoint and precede recording.")
        # Consent is the exact observed control checked above, bound to this
        # immutable choice and checkpoint. Free-form comments are never parsed
        # as approval instructions. Ordinary conversation verification delegates.
        w.require(prior is None or prior == verified, "stale_checkpoint", "Conflicting Mod event replay.")
        return verified


def selected_adapter(controller: host.Controller) -> host.Controller:
    w.require(controller.adapter.name == "claude" and controller.adapter.profile == "native_workflow"
              and isinstance(controller.adapter, host.LocalAdapter),
              "unsupported_authority", "Mod decisions require an already selected Claude native workflow controller.")
    w.require(controller.principal == controller.root, "scope_violation", "Only the selected owning session can decide.")
    if isinstance(controller.adapter, ModsLocalAdapter):
        return controller
    # Preserve any installed adapter permission behavior as well as its bound state.
    # Only the standard local adapter is replaced; unknown extensions are refused.
    w.require(type(controller.adapter) is host.LocalAdapter, "unsupported_authority", "Unsupported adapter extension.")
    return host.Controller(controller.workspace, controller.root, ModsLocalAdapter("claude"), principal=controller.principal)


def _eligible(controller: host.Controller, binding: dict[str, Any], choice: str) -> dict[str, Any]:
    state = controller.report(binding["run"])
    stage = w.current(state)
    w.require(stage.get("packet") and binding == w.binding(state, stage["packet"]),
              "stale_checkpoint", "Displayed checkpoint binding changed; refresh before a new attempt.")
    w.require(not any(state.get(k) for k in ("finished", "retired", "superseded_by", "archived"))
              and stage["decision"] in {"awaiting_human_approval", "stale"},
              "stale_checkpoint", "Checkpoint is no longer awaiting a decision.")
    w.require(choice == "changes_requested" or not state.get("invalidation_pending")
              and stage["decision"] != "stale", "stale_checkpoint", "Evidence changed; refresh the checkpoint.")
    # Preparation is not consent, but it must retain installed runtime permissions.
    if choice == "approved":
        controller.adapter.before_action(state, "decide")
    return state


def _receipt(controller: host.Controller, record: dict[str, Any]) -> dict[str, Any] | None:
    if not record.get("observation"):
        return None
    state = controller.report(record["intent"]["binding"]["run"])
    decision = state.get("decisions", {}).get(record["observation"]["event_id"])
    if decision is None:
        return None
    w.require(decision == _verified(record) and record.get("envelope") == _envelope(record),
              "stale_checkpoint", "Authoritative decision conflicts with the saved attempt.")
    return {"decision": deepcopy(decision), "confirmed_revision": state["revision"], "confirmed_at": _now()}


def decide(controller: host.Controller, request: dict[str, Any], *, preview: bool = False, run_id: str | None = None) -> dict[str, Any]:
    request = read_request(_json(request))
    operation = request["operation"]
    w.require(not preview or operation == "receipt", "unsupported_authority", "Preview cannot prepare or record decisions.")
    controller = selected_adapter(controller)
    path = ledger_path(controller.workspace, controller.root)
    with primitives.file_lock(str(path)):
        ledger = _read(controller.workspace, controller.root)
        record = ledger["intents"].get(request["intent_id"])
        selected_run = request.get("binding", {}).get("run") if operation == "prepare" else (record or {}).get("intent", {}).get("binding", {}).get("run")
        w.require(run_id is None or selected_run == run_id, "decision_binding", "CLI run differs from the immutable attempt.")
        if operation == "prepare":
            payload = _payload(request)
            if record is not None:
                w.require(_payload(record["intent"]) == payload, "stale_checkpoint", "Attempt ID conflicts with different immutable content.")
            else:
                _eligible(controller, request["binding"], request["choice"])
                intent = {"schema": INTENT, **payload, "payload_digest": _hash(payload), "created_at": _now()}
                record = {"intent": intent, "presentation": _presentation(intent)}
                # Check final envelope size before saving even an unsubmitted intent.
                test_record = deepcopy(record)
                test_record["observation"] = {"event_id": "mods/" + intent["intent_id"]}
                w.require(len(_json(_envelope(test_record)).encode()) <= MAX_ENVELOPE,
                          "invalid_evidence", "Serialized confirmation envelope is too large.")
                ledger["intents"][request["intent_id"]] = record
                _write(controller.workspace, controller.root, ledger)
        w.require(record is not None, "state_unavailable", "No saved attempt exists for this ID.")
        assert record is not None
        if operation == "record_ui":
            intent = record["intent"]
            w.require(request["payload_digest"] == intent["payload_digest"],
                      "decision_binding", "Displayed confirmation digest changed.")
            _control(request["control"], intent, controller.root)
            if record.get("observation"):
                w.require(record["observation"]["control"] == request["control"],
                          "decision_provenance", "Retry must preserve the original observed control.")
            else:
                _eligible(controller, intent["binding"], intent["choice"])
                record["observation"] = {"schema": OBSERVATION, "event_id": "mods/" + intent["intent_id"],
                    "intent": deepcopy(intent), "presentation": deepcopy(record["presentation"]),
                    "control": deepcopy(request["control"]), "observed_at": _now(), "assurance": "observed_ui", "preview": False}
                record["envelope"] = _envelope(record)
                w.require(len(_json(record["envelope"]).encode()) <= MAX_ENVELOPE,
                          "invalid_evidence", "Serialized observation envelope is too large.")
                _write(controller.workspace, controller.root, ledger)
            receipt = _receipt(controller, record)
            if receipt is None:
                controller.apply("decide", intent["binding"]["run"], expected_revision=intent["binding"]["revision"],
                                 native_reference=_json(record["envelope"]))
        receipt = _receipt(controller, record)
        return {"schema": RESPONSE, "status": "recorded" if receipt else "prepared" if operation == "prepare" else "not_recorded",
                "intent": deepcopy(record["intent"]), "presentation": deepcopy(record["presentation"]), "receipt": receipt}


def _findings(workspace: Path, state: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the register's dispositions and attach only captured descriptive facts."""
    from .context import Store
    store = Store(workspace)
    result = deepcopy(rows)
    declarations = {}
    for entry in evidence._finding_packets(state):
        output = entry["packet"].get("output", {})
        for declaration in [*output.get("finding_references", []), *output.get("findings", [])]:
            if isinstance(declaration, dict) and isinstance(declaration.get("id"), str):
                declarations[evidence.finding_key(state, declaration)] = declaration
    for row in result:
        declaration = declarations.get((row["origin_run"], row["id"]), {})
        for key in ("title", "severity", "source", "tasks", "related_tasks", "lens", "lenses", "reviewer", "method"):
            if key in declaration:
                row[key] = deepcopy(declaration[key])
        row["historical"] = row.get("origin", {}).get("visit") != w.current(state)["id"]
        row["sources"] = []
        for reference in row.get("evidence_refs", []):
            source = {"reference": deepcopy(reference), "status": "unavailable"}
            try:
                captured = store.resolve(reference)
                if isinstance(captured, dict):
                    source["path"] = captured.get("path")
                    if captured.get("encoding") == "utf-8" and isinstance(captured.get("text"), str):
                        body = captured["text"].encode("utf-8")
                        source.update(status="available", text=body[:32768].decode("utf-8", errors="ignore"),
                                      truncated=len(body) > 32768)
            except (w.Refusal, OSError, ValueError, TypeError, KeyError):
                pass
            row["sources"].append(source)
        row["evidence_status"] = ("available" if row["sources"] and all(s["status"] == "available" for s in row["sources"])
                                  else "unavailable")
    return result


def snapshot(workspace: Path, run_id: str | None, controller: host.Controller, *, generation: int = 0,
             preview: bool = False) -> dict[str, Any]:
    from . import flow
    w.require(type(generation) is int and generation >= 0, "invalid_evidence", "Invalid selection generation.")
    w.require(workspace.resolve() == controller.workspace, "decision_binding", "Workspace differs from selected controller.")
    status = "ready"
    authority_started = _now()
    try:
        # 2.31.7 advisory reports retain unavailable authority as diagnostics.
        # A Mod must fail refresh instead of presenting that as fresh empty state.
        authority = controller.report(run_id)
        w.require(authority.get("workflow_available") is True,
                  "state_unavailable", "Workflow authority is unavailable.")
        try:
            report = flow.report(controller.workspace, authority.get("run", run_id), governor=controller)
        except (OSError, ValueError, TypeError, KeyError, primitives.StateError):
            # Core authority was read successfully; optional telemetry stays unknown.
            report = {"workflow": authority, "tokens": None, "token_coverage": None,
                      "usage_measurement": {"status": "unavailable"}} if authority.get("visits") else None
        if authority.get("visits"):
            w.require(bool((report or {}).get("workflow", {}).get("visits"))
                      and (report or {}).get("workflow", {}).get("run") == authority["run"],
                      "state_unavailable", "Authoritative Mod state became unavailable during refresh.")
    except w.Refusal as exc:
        if exc.reason != "state_unavailable" or not run_id:
            raise
        # Distinguish an absent selection from broken authority/storage.
        # A second successful authority read validates the store before declaring
        # a missing requested run; corruption must fail refresh normally.
        current_state = controller.report()
        if current_state.get("run") == run_id:
            raise
        report, status = None, "selection_unavailable"
    if not report and status == "ready":
        status = "no_run"
    state = (report or {}).get("workflow", {})
    stage = w.current(state) if state.get("visits") else {}
    packet = stage.get("packet")
    summary = evidence.outcome_summary(state) if stage else {}
    historical = bool((report or {}).get("historical") or any(state.get(k) for k in ("finished", "retired", "superseded_by", "archived")))
    supported = (type(controller.adapter) in {host.LocalAdapter, ModsLocalAdapter}
                 and controller.adapter.name == "claude" and controller.adapter.profile == "native_workflow")
    reasons = []
    if not supported: reasons.append("unsupported_host")
    if controller.principal != controller.root: reasons.append("foreign_owner")
    if preview: reasons.append("preview")
    if historical: reasons.append("historical")
    if not packet or stage.get("decision") != "awaiting_human_approval": reasons.append("no_pending_checkpoint")
    if state.get("invalidation_pending"): reasons.append("evidence_changed")
    result = {"schema": SNAPSHOT, "status": status,
        "selection": {"workspace": str(controller.workspace), "root": state.get("root", controller.root),
                      "run": state.get("run", run_id), "generation": generation},
        "identity": w.binding(state, packet) if packet else None,
        "phase": stage.get("phase"), "work": stage.get("work"), "decision": stage.get("decision"),
        "revision": state.get("revision"), "packet_revision": stage.get("packet_revision"),
        "historical": historical, "superseded_by": state.get("superseded_by"),
        "evidence": {"status": "stale" if state.get("invalidation_pending") or stage.get("decision") == "stale"
                     else "validated" if packet else "not_submitted" if stage else "unavailable",
                     "drift": state.get("evidence_drift"), "invalidation_pending": bool(state.get("invalidation_pending"))},
        "findings": _findings(controller.workspace, state, summary.get("findings", [])) if stage else [], "current_checks": summary.get("current_checks", []),
        "unresolved_count": len(summary["unresolved_findings"]) if summary else None,
        "workers": state.get("worker_status"),
        "run_usage": {"tokens": (report or {}).get("tokens"), "coverage": (report or {}).get("token_coverage"),
                      "measurement": (report or {}).get("usage_measurement")},
        "context_usage": None, "confirmed_at": (report or {}).get("snapshot", {}).get("captured_at") or authority_started,
        "capabilities": {"snapshot": True, "evidence": bool(stage), "intent": not reasons,
                         "direct_decision": not reasons, "native_confirmation": supported, "preview": preview, "reasons": reasons},
        "fallback": {"dashboard": str(controller.workspace / ".taskplane/dashboard.html"), "decision": "conversation"}}
    result["snapshot_id"] = _hash(result)
    return result
