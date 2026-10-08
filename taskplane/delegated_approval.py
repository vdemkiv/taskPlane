"""Explicit cooperative observations carried by Codex delegation, not host attestation.

The root records an original user response and its presented policy together.
This does not authenticate the account or turn arbitrary tool output into a user
message. Existing direct/native transcript contracts remain separate.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import re
from typing import Any

from . import workflow as w
from .primitives import content_fingerprint

POLICY_SCHEMA = "taskplane.approval-policy/v2"
SCHEMA = "taskplane.delegated-user-observation/v1"
DECISION_SCHEMA = "taskplane.observed-decision/v3"
CHECKPOINT_SCHEMA = "taskplane.delegated-checkpoint-observation/v1"


def _receipt(state: dict[str, Any], relay: Any, schema: str, fields: set[str]) -> None:
    """Shared transport/integrity checks; cooperative provenance is not authentication."""
    w.require(isinstance(relay, dict) and set(relay) == fields and relay.get("schema") == schema
              and relay.get("transport") == "codex_delegation"
              and relay.get("assurance") == "relayed_observation"
              and relay.get("host_attested") is False
              and relay.get("independent_source_verification") == "unavailable"
              and isinstance(relay.get("reference"), str) and 0 < len(relay["reference"]) <= 512
              and isinstance(relay.get("source_thread"), str) and 0 < len(relay["source_thread"]) <= 512
              and relay["source_thread"] != state["root"] and relay.get("receiver_thread") == state["root"],
              "invalid_evidence", "Delegation needs an explicit cooperative source and receiving root.")
    w.require(relay["digest"] == content_fingerprint({k: v for k, v in relay.items() if k != "digest"}),
              "invalid_evidence", "Delegated observation receipt changed.")


def verify(state: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    """Bind one explicit relayed policy consent to the current submitted checkpoint."""
    from .workflow_local import timestamp

    relay, source, context = (request.get(key) for key in ("relay", "source", "choice_context"))
    fields = {"schema", "transport", "reference", "source_thread", "receiver_thread",
              "recorded_at", "human", "presentation", "binding", "proposal", "assurance",
              "host_attested", "independent_source_verification", "digest"}
    _receipt(state, relay, SCHEMA, fields)
    assert isinstance(relay, dict)
    w.require(isinstance(source, dict) and source.get("kind") == "delegated_user_observation"
              and source.get("actor") == "user" and source.get("automatic") is False
              and isinstance(relay["source_thread"], str) and 0 < len(relay["source_thread"]) <= 512
              and source.get("conversation") == relay["source_thread"] != state["root"]
              and relay["receiver_thread"] == state["root"]
              and request.get("recorder") == "root_orchestrator"
              and request.get("event_id") == source.get("reference"),
              "invalid_evidence", "Delegation must preserve the original user source and receiving root.")
    w.require(isinstance(context, dict) and context.get("schema") == "taskplane.policy-choice/v1",
              "invalid_evidence", "Delegated consent requires its complete presented policy question.")
    assert isinstance(source, dict) and isinstance(context, dict)
    w.require(isinstance(context.get("source"), dict), "invalid_evidence",
              "Delegated policy requires the original presentation source.")
    human, shown = relay["human"], relay["presentation"]
    message_fields = {"conversation", "role", "reference", "observed_at", "text"}
    w.require(all(isinstance(row, dict) and set(row) == message_fields for row in (human, shown)),
              "invalid_evidence", "Retain separate original human and presentation observations.")
    w.require(human == {"conversation": source.get("conversation"), "role": "user",
                       "reference": source.get("reference"), "observed_at": source.get("observed_at"),
                       "text": request.get("excerpt")}
              and shown == {"conversation": source.get("conversation"), "role": "assistant",
                            "reference": context.get("source", {}).get("reference"),
                            "observed_at": context.get("source", {}).get("observed_at"),
                            "text": context.get("question")}
              and isinstance(shown.get("text"), str) and 0 < len(shown["text"]) <= 16384
              and isinstance(context.get("instructions"), str) and bool(context["instructions"])
              and context["instructions"] in shown["text"],
              "invalid_evidence", "Delegated policy changed its original question, response or metadata.")
    stage = w.current(state)
    w.require(stage.get("decision") == "awaiting_human_approval" and stage.get("packet"),
              "approval_required", "Delegated consent requires a submitted checkpoint.")
    w.require(relay["binding"] == w.binding(state, stage["packet"]),
              "stale_checkpoint", "Delegated consent names a stale or foreign checkpoint or scope.")
    proposal = {key: request.get(key) for key in ("binding", "mode", "allowed_phases", "stop_phases", "conditions")}
    w.require(relay["proposal"] == proposal == context.get("proposal"),
              "invalid_evidence", "Delegated policy differs from the presented bounded proposal.")
    w.require(timestamp(stage.get("submitted_at")) <= timestamp(shown.get("observed_at"))
              < timestamp(human.get("observed_at")) <= timestamp(relay["recorded_at"])
              <= datetime.now(timezone.utc),
              "invalid_evidence", "Delegated observation chronology is invalid.")
    return deepcopy(relay)


def verify_checkpoint(state: dict[str, Any], request: dict[str, Any], expected: dict[str, Any],
                      prior: dict[str, Any]) -> dict[str, Any]:
    """Normalize one verified-owner observation without inventing typed human text.

    The trusted root records the parent's current complete read and owner observation.
    This validates consistency and checkpoint authority, not account authentication.
    Reaction times unavailable from the source stay unavailable; observation time is
    distinct and its precision is retained. A consumed source event cannot be reused.
    """
    from .workflow_local import timestamp
    from .workflow_approval import conversational_choice

    w.require(set(request) == {"schema", "event_id", "recorder", "choice", "binding", "relay"}
              and request.get("schema") == DECISION_SCHEMA and request.get("recorder") == "root_orchestrator",
              "invalid_evidence", "Use the explicit delegated checkpoint contract.")
    relay = request.get("relay")
    fields = {"schema", "transport", "reference", "source_thread", "receiver_thread", "recorded_at",
              "assurance", "host_attested", "independent_source_verification", "binding", "question",
              "owner", "observation", "digest"}
    _receipt(state, relay, CHECKPOINT_SCHEMA, fields)
    assert isinstance(relay, dict)
    stage = w.current(state)
    w.require(state.get("profile") == "native_workflow" and stage.get("decision") == "awaiting_human_approval"
              and stage.get("packet"), "approval_required", "A delegated decision needs a pending native checkpoint.")
    w.require(request.get("binding") == relay["binding"] == expected == w.binding(state, stage["packet"]),
              "stale_checkpoint", "Observation has a stale or foreign checkpoint binding.")
    question, owner, observation = (relay.get(k) for k in ("question", "owner", "observation"))
    w.require(isinstance(question, dict) and set(question) == {"message_id", "channel", "text", "sent_at", "phase", "binding"}
              and question["phase"] == stage["phase"] and question["binding"] == expected
              and question["channel"] == "chatgpt" and isinstance(question["message_id"], str)
              and 0 < len(question["message_id"]) <= 512 and isinstance(question["text"], str)
              and 0 < len(question["text"]) <= 16384,
              "invalid_evidence", "Preserve the exact original question and its phase/checkpoint mapping.")
    assert isinstance(question, dict)
    target = {"evaluate": "(?:evaluate|evaluation)", "retro": "(?:retro|retrospective)"}.get(stage["phase"], stage["phase"])
    # Reaction semantics require a direct phase-acceptance question, not approval
    # words somewhere in a generic message, a quoted example or a negative request.
    clause = r"(?:^|[.?!]\s+|\n)(?:do you|will you) (?:accept|approve) (?:this|the) " + target + r"\b[^?\n]*\?"
    match = re.search(clause, question["text"], re.IGNORECASE)
    w.require(match is not None and not re.search(r"\b(?:not|never|if|unless|hypothetical|example|withdraw|cancel)\b", match.group(0), re.IGNORECASE),
              "invalid_evidence", "The reaction must answer a direct unambiguous acceptance question for this phase.")
    w.require(isinstance(owner, dict) and set(owner) == {"user_id", "verified", "reference", "source_thread"}
              and owner["verified"] is True and owner["source_thread"] == relay["source_thread"]
              and isinstance(owner["user_id"], str) and 0 < len(owner["user_id"]) <= 512
              and isinstance(owner["reference"], str) and 0 < len(owner["reference"]) <= 512,
              "invalid_evidence", "The original owner needs a separately recorded verification observation.")
    assert isinstance(owner, dict)
    w.require(isinstance(observation, dict) and set(observation) == {"kind", "method", "observed_at", "precision", "current", "complete_message", "raw"}
              and observation["kind"] in {"owner_reaction", "owner_text"}
              and observation["method"] == "user_message.read_messages"
              and observation["current"] is True and observation["complete_message"] is True
              and observation["precision"] in {"second", "minute"}
              and isinstance(observation["raw"], dict),
              "invalid_evidence", "Generic emoji/tool payloads are not owner observations.")
    assert isinstance(observation, dict)
    raw = observation["raw"]
    message = raw.get("message")
    w.require(isinstance(message, dict) and "deleted_at" in message and message["deleted_at"] is None
              and message.get("channel") == question["channel"] and isinstance(message.get("content"), dict),
              "invalid_evidence", "The complete source message must still exist on the original channel.")
    assert isinstance(message, dict)
    observed_at, recorded_at = timestamp(observation["observed_at"]), timestamp(relay["recorded_at"])
    w.require(timestamp(stage.get("submitted_at")) <= timestamp(question["sent_at"]) < observed_at
              <= recorded_at <= datetime.now(timezone.utc),
              "invalid_evidence", "Original question/observation chronology is invalid.")
    if observation["kind"] == "owner_reaction":
        w.require(message.get("message_id") == question["message_id"]
                  and message.get("content", {}).get("text") == question["text"]
                  and message.get("sent_at") == question["sent_at"]
                  and isinstance(message.get("reactions"), dict)
                  and message["reactions"].get(owner["user_id"]) == "👍",
                  "invalid_evidence", "Only the verified owner's current thumbs-up on the exact question can approve it.")
        choice: str | None = "approved"
        excerpt: str | None = None
        event_source = {"kind": "owner_reaction", "channel": question["channel"], "message_id": question["message_id"],
                        "owner": owner["user_id"], "reaction": "👍"}
        normalization = {"schema": "taskplane.owner-reaction-normalization/v1", "raw_reaction": "👍",
                         "derived_choice": choice, "typed_text": None, "reaction_at": None,
                         "basis": "verified owner reaction to the exact phase acceptance question"}
    else:
        w.require(message.get("author_id") == owner["user_id"]
                  and message.get("reply_to_id") == question["message_id"]
                  and isinstance(message.get("message_id"), str) and bool(message["message_id"])
                  and timestamp(question["sent_at"]) < timestamp(message.get("sent_at")) <= observed_at,
                  "invalid_evidence", "Owner text must directly reply to the original question.")
        excerpt = message["content"].get("text")
        w.require(isinstance(excerpt, str) and 0 < len(excerpt) <= 4096,
                  "invalid_evidence", "Preserve the original bounded owner response.")
        choice = conversational_choice(excerpt)
        w.require(choice is not None, "invalid_evidence", "Owner response does not express an unambiguous decision.")
        event_source = {"kind": "owner_text", "channel": message["channel"], "message_id": message["message_id"], "owner": owner["user_id"]}
        normalization = {"schema": "taskplane.owner-text-normalization/v1", "derived_choice": choice, "typed_text": excerpt}
    event_id = "delegated:" + content_fingerprint(event_source)
    w.require(request["choice"] == choice and request["event_id"] == event_id,
              "invalid_evidence", "Derived decision or source event identity does not match the preserved observation.")
    w.require(event_id not in prior and not any(d.get("provenance", {}).get("source_event") == event_source for d in prior.values()),
              "stale_checkpoint", "This original owner observation has already been consumed.")
    return {"event_id": event_id, "human": True, "automatic": False, "choice": choice,
            "binding": deepcopy(expected), "assurance": "observed", "provenance": {
                "schema": DECISION_SCHEMA, "source": {"kind": "delegated_user_observation", "actor": "user",
                    "automatic": False, "reference": message["message_id"], "channel": message["channel"],
                    "observed_at": observation["observed_at"], "owner_id": owner["user_id"]},
                "source_event": event_source, "source_assurance": "relayed_observation", "host_attested": False,
                "excerpt": excerpt, "normalization": normalization, "relay": deepcopy(relay),
                "recorded_at": relay["recorded_at"], "chronology": "observation-order/v1"}}
