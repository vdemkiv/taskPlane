"""Focused cooperative-source fixtures. These do not authenticate live accounts."""
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

import pytest

from taskplane import delegated_approval as relay_api, workflow as w
from taskplane.primitives import content_fingerprint
from taskplane.tests.test_workflow_local import setup, submit, decision, decide


def digest(request):
    relay = request['relay']
    relay['digest'] = content_fingerprint({k: v for k, v in relay.items() if k != 'digest'})


def observation(state, kind='owner_reaction'):
    native = decision(state)
    bound = deepcopy(native['binding'])
    phase = w.current(state)['phase']
    question = {'message_id': 'fixture-question', 'channel': 'chatgpt',
                'text': f'Do you accept this {phase} with the recorded limits?',
                'sent_at': native['presentation']['at'], 'phase': phase, 'binding': deepcopy(bound)}
    message = {'message_id': question['message_id'], 'channel': 'chatgpt', 'author_id': 'fixture-assistant',
               'content': {'text': question['text']}, 'sent_at': question['sent_at'], 'deleted_at': None,
               'reactions': {'fixture-owner': '👍'}, 'reply_to_id': None}
    if kind == 'owner_text':
        message.update(message_id='fixture-answer', author_id='fixture-owner', content={'text': 'Approved'},
                       sent_at=native['source']['observed_at'], reactions={}, reply_to_id=question['message_id'])
    source = {'kind': kind, 'channel': 'chatgpt', 'message_id': message['message_id'], 'owner': 'fixture-owner'}
    if kind == 'owner_reaction':
        source['reaction'] = '👍'
    request = {'schema': relay_api.DECISION_SCHEMA, 'event_id': 'delegated:' + content_fingerprint(source),
               'recorder': 'root_orchestrator', 'choice': 'approved', 'binding': bound}
    request['relay'] = {'schema': relay_api.CHECKPOINT_SCHEMA, 'transport': 'codex_delegation',
        'reference': 'fixture:explicit-parent-observation', 'source_thread': 'fixture-parent',
        'receiver_thread': state['root'], 'assurance': 'relayed_observation', 'host_attested': False,
        'independent_source_verification': 'unavailable', 'binding': deepcopy(bound), 'question': question,
        'owner': {'user_id': 'fixture-owner', 'verified': True, 'reference': 'fixture-owner-verification', 'source_thread': 'fixture-parent'},
        'observation': {'kind': kind, 'method': 'user_message.read_messages', 'current': True,
            'complete_message': True, 'precision': 'second', 'observed_at': datetime.now(timezone.utc).isoformat(),
            'raw': {'message': message, 'before': [], 'after': [], 'partial': True}},
        'recorded_at': datetime.now(timezone.utc).isoformat()}
    digest(request)
    return request


@pytest.mark.parametrize('kind', ['owner_reaction', 'owner_text'])
def test_owner_observation_applies_exact_checkpoint_and_preserves_source(tmp_path, kind):
    c, state = setup(tmp_path)
    state = submit(c, state)
    request = observation(state, kind)
    result = decide(c, state, request)
    event = result['decisions'][request['event_id']]
    assert w.current(result)['decision'] == 'approved'
    assert event['human'] and not event['automatic']
    assert event['provenance']['relay'] == request['relay']
    assert event['provenance']['host_attested'] is False
    assert event['provenance']['source_assurance'] == 'relayed_observation'
    if kind == 'owner_reaction':
        assert event['provenance']['excerpt'] is None
        assert event['provenance']['normalization']['typed_text'] is None
        assert event['provenance']['normalization']['raw_reaction'] == '👍'
    else:
        assert event['provenance']['excerpt'] == 'Approved'
    original = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        decide(c, result, request)
    assert c._path().read_bytes() == original
    advanced = c.apply('advance', result['run'], expected_revision=result['revision'], phase='design')
    with pytest.raises(w.Refusal):
        decide(c, submit(c, advanced), request)


@pytest.mark.parametrize('bad', ['owner', 'owner_unverified', 'owner_source', 'question', 'target', 'phase',
    'scope', 'checkpoint', 'manifest', 'revision', 'receiver', 'source_thread', 'generic_tool', 'generic_emoji',
    'deleted', 'missing_deleted', 'old_observation', 'incomplete_message', 'before_submission', 'future',
    'host_claim', 'choice', 'event', 'digest', 'plain_emoji_question', 'negative_question', 'conditional_question'])
def test_wrong_or_stale_observations_are_rejected_without_mutation(tmp_path, bad):
    c, state = setup(tmp_path)
    state = submit(c, state)
    request = observation(state)
    relay = request['relay']; obs = relay['observation']; raw = obs['raw']['message']
    if bad == 'owner': relay['owner']['user_id'] = 'someone-else'
    elif bad == 'owner_unverified': relay['owner']['verified'] = False
    elif bad == 'owner_source': relay['owner']['source_thread'] = 'unrelated'
    elif bad == 'question': relay['question']['text'] += ' Changed question.'
    elif bad == 'target': raw['message_id'] = 'another-question'
    elif bad == 'phase': relay['question']['phase'] = 'retro'
    elif bad in {'scope', 'checkpoint', 'manifest', 'revision'}:
        field = {'scope': 'scope_digest', 'manifest': 'manifest_digest'}.get(bad, bad)
        request['binding'][field] = 'wrong'
    elif bad == 'receiver': relay['receiver_thread'] = 'wrong'
    elif bad == 'source_thread': relay['source_thread'] = state['root']
    elif bad == 'generic_tool': relay['transport'] = 'tool_result'
    elif bad == 'generic_emoji': obs['method'] = 'emoji_tool'
    elif bad == 'deleted': raw['deleted_at'] = obs['observed_at']
    elif bad == 'missing_deleted': del raw['deleted_at']
    elif bad == 'old_observation': obs['current'] = False
    elif bad == 'incomplete_message': obs['complete_message'] = False
    elif bad == 'before_submission': relay['question']['sent_at'] = state['started_at']
    elif bad == 'future': relay['recorded_at'] = '2100-01-01T00:00:00Z'
    elif bad == 'host_claim': relay['host_attested'] = True
    elif bad == 'choice': request['choice'] = 'rejected'
    elif bad == 'event': request['event_id'] = 'arbitrary-event'
    elif bad in {'plain_emoji_question', 'negative_question', 'conditional_question'}:
        text = {'plain_emoji_question': 'Like this screenshot?', 'negative_question': 'Do you not accept this product?',
                'conditional_question': 'Do you accept this product if the unrun checks pass?'}[bad]
        relay['question']['text'] = raw['content']['text'] = text
    digest(request)
    if bad == 'digest': relay['digest'] = '0' * 64
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        decide(c, state, request)
    assert c._path().read_bytes() == before


def test_raw_thumb_is_not_added_to_generic_text_parser():
    from taskplane.workflow_approval import conversational_choice
    assert conversational_choice('👍') is None


# LOCAL-SEC-01's declared module owns this matrix. Native files below are
# synthetic, isolated observations passed through the real readers/controller;
# they are neither authenticated human evidence nor live-host certification.
TEXT_TRANSPORTS = (
    "observed-v1", "observed-v2", "observed-v3-direct",
    "observed-v3-adjacent", "native-capture",
)
CONSENT_TRANSPORTS = (*TEXT_TRANSPORTS, "observed-v3-reaction")
TARGETS = ("correct", "omitted", "wrong", "multiple")
FOREIGN_CHECKPOINT = "f" * 32


def _source_bytes(paths):
    return {str(path): Path(path).read_bytes() for path in paths}


def consent_authority(controller, run):
    """Compare exact serialized authority, independent of capture diagnostics."""
    state = json.loads(controller._path().read_text())["runs"][run]
    keys = ("workspace", "root", "run", "revision", "scope", "scope_index",
            "visits", "decisions", "approval_policy", "status", "active_visit")
    return json.dumps({key: state.get(key) for key in keys}, sort_keys=True,
                      separators=(",", ":")).encode()


@contextmanager
def consent_calls():
    """Observe actual calls without replacing the validator or its result."""
    from taskplane.workflow_approval import validate_checkpoint_consent
    calls = []
    previous = sys.getprofile()
    def trace(frame, event, arg):
        if event == "call" and frame.f_code is validate_checkpoint_consent.__code__:
            calls.append(deepcopy({key: frame.f_locals[key] for key in
                                  ("question", "answer", "phase", "binding", "reaction")}))
        if previous is not None:
            previous(frame, event, arg)
    sys.setprofile(trace)
    try:
        yield calls
    finally:
        sys.setprofile(previous)


def consent_transport_case(tmp_path, monkeypatch, transport, question, answer="Approved"):
    """Build real transport proof, then dispatch through Controller.apply.

    v1's observed presentation.text is the complete original question. v2 and
    native capture must obtain that text from the pinned original source rows,
    not trust an added caller field. The controller integration must pass it to
    validate_checkpoint_consent after proof and retain it in provenance.
    """
    source_paths = []
    capture = None
    if transport == "observed-v2":
        from taskplane.tests.test_workflow_delivery import relay_fixture
        if not (hasattr(os, "O_DIRECTORY") and hasattr(os, "O_NOFOLLOW")
                and os.open in os.supports_dir_fd):
            pytest.skip("Accepting original-source relay evidence requires secure descriptor-relative reads")
        controller, state, value, rows, rewrite = relay_fixture(tmp_path, monkeypatch)
    elif transport == "native-capture":
        from taskplane import claude_worker_observations as native
        from taskplane.tests.test_native_approval_capture import fixture
        if not native.supported_reader():
            pytest.skip("Native no-follow reads unavailable")
        controller, state, path, event, shown, human, write = fixture(tmp_path, monkeypatch)
    else:
        controller, state = setup(tmp_path)
        state = submit(controller, state)
    binding = w.binding(state, w.current(state)["packet"])
    assert binding["checkpoint"] != FOREIGN_CHECKPOINT
    if transport == "observed-v2":
        from taskplane import workflow_local as local
        snapshot = local.Harness(controller.workspace, controller.root).read()["presentation"]["artifact"]
        question = question.format(**binding, snapshot=snapshot)
    else:
        question = question.format(**binding)
    answer = answer.format(**binding)

    if transport == "observed-v1":
        value = decision(state, text=answer, event="fixture-v1-answer")
        value["choice"] = "approved"
        value["presentation"]["text"] = question
    elif transport == "observed-v2":
        question = f"Current checkpoint {binding['checkpoint']}.\n{question}\n[Snapshot]({snapshot})"
        rows[3]["payload"]["content"][0]["text"] = question
        rows[4]["payload"]["content"][0]["text"] = answer
        value["excerpt"] = answer
        rewrite()
        source_paths.append(Path(value["relay"]["human"]["path"]))
    elif transport == "native-capture":
        from taskplane import flow, native_approval, workflow_local as local
        question = f"Current checkpoint {binding['checkpoint']}.\n{question}"
        shown["message"]["content"] = question
        human["message"]["content"] = answer
        event["prompt"] = answer
        # Select the real source via the owning-root hook while the reply is
        # absent. This cannot approve; it establishes the native file identity.
        write([shown])
        flow.hook(event, governor=controller)
        state = json.loads(controller._path().read_text())["runs"][state["run"]]
        assert w.current(state)["decision"] == "awaiting_human_approval"
        assert not state["decisions"]
        assert w.binding(state, w.current(state)["packet"]) == binding
        write([shown, human])
        before_capture = controller._path().read_bytes()
        capture = native_approval.capture(state, host="claude", presentation=
                                         local.Harness(controller.workspace, controller.root).read()["presentation"])
        assert controller._path().read_bytes() == before_capture
        value = capture.get("decision")
        source_paths.append(path)
    else:
        if transport == "observed-v3-adjacent":
            from taskplane.tests.test_ordinary_checkpoint_reply import ordinary
            value = ordinary(state)
        else:
            value = observation(state, "owner_reaction" if transport == "observed-v3-reaction" else "owner_text")
        relay = value["relay"]
        relay["question"]["text"] = question
        raw = relay["observation"]["raw"]
        if transport == "observed-v3-reaction":
            raw["message"]["content"]["text"] = question
        else:
            raw["message"]["content"]["text"] = answer
            if raw["before"]:
                raw["before"][0]["content"]["text"] = question
        digest(value)
    return {"transport": transport, "controller": controller, "state": state,
            "value": value, "capture": capture, "question": question,
            "answer": None if transport == "observed-v3-reaction" else answer,
            "sources": _source_bytes(source_paths)}


@pytest.fixture(params=CONSENT_TRANSPORTS)
def consent_transport(tmp_path, monkeypatch, request):
    def build(question="Approve this Product checkpoint?", answer="Approved"):
        return consent_transport_case(tmp_path, monkeypatch, request.param, question, answer)
    return build


@pytest.fixture(params=TEXT_TRANSPORTS)
def consent_text_transport(tmp_path, monkeypatch, request):
    def build(question="Approve this Product checkpoint?", answer="Approved"):
        return consent_transport_case(tmp_path, monkeypatch, request.param, question, answer)
    return build


def assert_consent_refused(case):
    controller, state, value = (case[key] for key in ("controller", "state", "value"))
    original = deepcopy(value)
    before = controller._path().read_bytes()
    authority = consent_authority(controller, state["run"])
    if value is None:
        # Native proof can itself refuse a foreign target; do not fabricate an
        # envelope merely to get past the real capture's earlier refusal.
        assert case["transport"] == "native-capture"
        assert case["capture"]["status"] in {"conflict", "needs_clarification"}
    else:
        with pytest.raises(w.Refusal):
            decide(controller, state, value)
    assert controller._path().read_bytes() == before
    assert consent_authority(controller, state["run"]) == authority
    assert value == original
    assert _source_bytes(case["sources"]) == case["sources"]


def assert_consent_approved(case):
    controller, state, value = (case[key] for key in ("controller", "state", "value"))
    assert value is not None, case["capture"]
    original = deepcopy(value)
    with consent_calls() as calls:
        result = decide(controller, state, value)
    assert calls, "Controller dispatch bypassed the common complete-question validator"
    assert all(call["question"] == case["question"] and call["answer"] == case["answer"]
               and call["phase"] == "product" and call["binding"] == value["binding"]
               and call["reaction"] == (case["transport"] == "observed-v3-reaction") for call in calls)
    recorded = result["decisions"][value["event_id"]]
    assert recorded["choice"] == "approved"
    assert recorded["binding"] == value["binding"]
    assert recorded["human"] is True and recorded["automatic"] is False
    assert w.current(result)["phase"] == "product"
    assert w.current(result)["decision"] == "approved"
    assert w.current(result)["packet"] == w.current(state)["packet"]
    assert result.get("approval_policy") == state.get("approval_policy")
    assert recorded["provenance"]["excerpt"] == case["answer"]
    if case["transport"].startswith("observed-v3"):
        assert recorded["provenance"]["relay"] == value["relay"]
    else:
        assert recorded["provenance"]["presentation"]["text"] == case["question"]
        if case["transport"] == "observed-v2":
            assert recorded["provenance"]["relay"] == value["relay"]
        if case["transport"] == "native-capture":
            assert recorded["provenance"]["native_proof"] == value["native_proof"]
    assert value == original
    assert _source_bytes(case["sources"]) == case["sources"]
    return result


def _target_answer(phase_target, checkpoint_target):
    phase = {"correct": "Product approved.", "omitted": "", "wrong": "Design approved.",
             "multiple": "Product approved. Design approved."}[phase_target]
    checkpoint = {"correct": "Approve {checkpoint}.", "omitted": "",
                  "wrong": "Approve " + FOREIGN_CHECKPOINT + ".",
                  "multiple": "Approve {checkpoint}. Approve " + FOREIGN_CHECKPOINT + "."}[checkpoint_target]
    return (phase + " " + checkpoint).strip() or "Approved"


@pytest.mark.parametrize("phase_target", TARGETS)
@pytest.mark.parametrize("checkpoint_target", TARGETS)
def test_sec01_transport_answer_target_matrix(consent_text_transport, phase_target, checkpoint_target):
    case = consent_text_transport(answer=_target_answer(phase_target, checkpoint_target))
    if phase_target in {"correct", "omitted"} and checkpoint_target in {"correct", "omitted"}:
        assert_consent_approved(case)
    else:
        assert_consent_refused(case)


@pytest.mark.parametrize("phase_target", TARGETS)
@pytest.mark.parametrize("checkpoint_target", TARGETS)
def test_sec01_transport_question_target_matrix(consent_transport, phase_target, checkpoint_target):
    phase = {"correct": "Product ", "omitted": "", "wrong": "Design ",
             "multiple": "Product and Design "}[phase_target]
    checkpoint = {"correct": " {checkpoint}", "omitted": "", "wrong": " " + FOREIGN_CHECKPOINT,
                  "multiple": " {checkpoint} and " + FOREIGN_CHECKPOINT}[checkpoint_target]
    case = consent_transport(f"Approve this {phase}checkpoint{checkpoint}?")
    if phase_target in {"correct", "omitted"} and checkpoint_target in {"correct", "omitted"}:
        assert_consent_approved(case)
    else:
        assert_consent_refused(case)


@pytest.mark.parametrize("bad", ["run", "visit", "revision", "scope_digest", "manifest_digest", "foreign-conversation"])
def test_sec01_transport_stale_or_foreign_binding_is_atomic(consent_transport, bad):
    case = consent_transport()
    value = case["value"]
    assert value is not None, case["capture"]
    if bad == "foreign-conversation":
        if case["transport"].startswith("observed-v3"):
            value["relay"]["receiver_thread"] = "foreign-conversation"
        else:
            value["source"]["conversation"] = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    else:
        value["binding"][bad] = value["binding"][bad] + 1 if bad == "revision" else "e" * 32
        if case["transport"].startswith("observed-v3"):
            value["relay"]["binding"] = deepcopy(value["binding"])
            value["relay"]["question"]["binding"] = deepcopy(value["binding"])
    if case["transport"].startswith("observed-v3"):
        digest(value)
    assert_consent_refused(case)


def test_sec01_transport_proof_precedes_question_validation(consent_transport):
    case = consent_transport()
    value = case["value"]
    assert value is not None, case["capture"]
    if case["transport"] == "observed-v1":
        value["source"]["actor"] = "assistant"
    elif case["transport"] == "observed-v2":
        value["relay"]["human"]["sha256"] = "0" * 64
    elif case["transport"] == "native-capture":
        value["native_proof"]["human"]["sha256"] = "0" * 64
    else:
        value["relay"]["owner"]["verified"] = False
        digest(value)
    with consent_calls() as calls:
        assert_consent_refused(case)
    assert calls == [], "Invalid transport proof reached consent semantics"


@pytest.mark.parametrize("question", [
    pytest.param("Only after CI passes. Approve this Product checkpoint?", id="condition-before"),
    pytest.param("Approve this Product checkpoint only after CI passes?", id="condition-inside"),
    pytest.param("Approve this Product checkpoint? Only after CI passes.", id="condition-after"),
    pytest.param("Once I confirm.\nApprove this Product checkpoint?", id="multiline"),
    pytest.param('"Approve this Product checkpoint?"', id="quoted"),
    pytest.param("For example: Approve this Product checkpoint?", id="example"),
    pytest.param("Do not approve it. Approve this Product checkpoint?", id="negation"),
    pytest.param("Approve this Product checkpoint? Reject this Product.", id="mixed-dissent"),
    pytest.param("Approve this Product checkpoint? Also publish the package.", id="extra-prose"),
])
def test_sec02_transport_validates_complete_original_question(consent_transport, question):
    case = consent_transport(question, "yes")
    assert case["value"] is not None, case["capture"]
    with consent_calls() as calls:
        assert_consent_refused(case)
    assert calls, "Transport proof must be followed by complete original-question validation"
    assert all(call["question"] == case["question"] and call["answer"] == case["answer"] for call in calls)


@pytest.mark.parametrize("answer", [
    pytest.param("If CI passes, Approved", id="condition-before"),
    pytest.param("Approve only after CI passes", id="condition-inside"),
    pytest.param("Approved. Only after CI passes.", id="condition-after"),
    pytest.param("Approved.\nWait until I confirm.", id="multiline"),
    pytest.param('"Approved"', id="quoted"),
    pytest.param("Example: Approved", id="example"),
    pytest.param("Approved. Do not proceed.", id="negation"),
    pytest.param("Approved. Reject this checkpoint.", id="mixed-dissent"),
    pytest.param("Approved. Unknown instruction.", id="extra-prose"),
])
def test_sec02_transport_keeps_complete_answer_qualification(consent_text_transport, answer):
    case = consent_text_transport(answer=answer)
    with consent_calls() as calls:
        assert_consent_refused(case)
    assert all(call["question"] == case["question"] and call["answer"] == case["answer"] for call in calls)


def test_sec02_v1_missing_original_question_requires_fresh_consent(tmp_path, monkeypatch):
    case = consent_transport_case(tmp_path, monkeypatch, "observed-v1", "Approve this Product checkpoint?")
    del case["value"]["presentation"]["text"]
    assert_consent_refused(case)


@pytest.mark.parametrize("transport", ["observed-v2", "native-capture"])
def test_sec02_caller_text_cannot_replace_pinned_original_question(tmp_path, monkeypatch, transport):
    case = consent_transport_case(tmp_path, monkeypatch, transport,
                                  "Approve this Product checkpoint? Only after CI passes.", "yes")
    assert case["value"] is not None, case["capture"]
    case["value"]["presentation"]["text"] = "Approve this Product checkpoint?"
    assert_consent_refused(case)


def test_sec01_exact_original_request_replay_is_idempotent(consent_transport):
    case = consent_transport()
    accepted = assert_consent_approved(case)
    controller, original_state, value = (case[key] for key in ("controller", "state", "value"))
    before = controller._path().read_bytes()
    authority = consent_authority(controller, original_state["run"])
    # Retry the SAME request and expected revision, not a new operation at the
    # post-approval revision. The latter's duplicate refusal is tested above.
    replay = decide(controller, original_state, deepcopy(value))
    assert replay == accepted
    assert replay["decisions"] == accepted["decisions"]
    assert controller._path().read_bytes() == before
    assert consent_authority(controller, original_state["run"]) == authority
    assert _source_bytes(case["sources"]) == case["sources"]


def test_sec01_consumed_event_cannot_approve_a_later_checkpoint(consent_transport):
    case = consent_transport()
    accepted = assert_consent_approved(case)
    controller = case["controller"]
    advanced = controller.apply("advance", accepted["run"], expected_revision=accepted["revision"], phase="design")
    case["state"] = submit(controller, advanced)
    assert_consent_refused(case)


def test_sec01_conflicting_replay_is_refused_without_authority_mutation(consent_transport):
    case = consent_transport()
    assert_consent_approved(case)
    case["value"]["choice"] = "rejected"
    assert_consent_refused(case)


def test_sec01_current_phase_approval_can_continue_to_next_phase(consent_text_transport):
    case = consent_text_transport(answer="Approve Product and proceed to Design")
    assert_consent_approved(case)


@pytest.mark.parametrize("answer", [
    "Approve Product and proceed to Build",
    "Approve Product and proceed to Product",
    "Approve Product and approve Design",
    "Approve Design and proceed to Plan",
    "Approve Product and proceed to Design if CI passes",
    "Approve Product and proceed to Design. Only after I confirm.",
])
def test_sec01_continuation_keeps_phase_and_condition_boundaries(consent_text_transport, answer):
    assert_consent_refused(consent_text_transport(answer=answer))


@pytest.mark.parametrize("field", ["reference", "recorded_at", "question"])
def test_sec03_changed_delegated_receipt_cannot_replay(tmp_path, monkeypatch, field):
    case = consent_transport_case(tmp_path, monkeypatch, "observed-v3-direct",
                                  "Approve this Product checkpoint?")
    assert_consent_approved(case)
    relay = case["value"]["relay"]
    if field == "question":
        relay["question"]["text"] = "Approve this Product output?"
    elif field == "recorded_at":
        relay[field] = datetime.now(timezone.utc).isoformat()
    else:
        relay[field] += "-changed"
    digest(case["value"])
    assert_consent_refused(case)


@pytest.mark.parametrize("metadata", [
    pytest.param("[Snapshot]({workspace}/.taskplane/snapshot-a1b2.html)", id="canonical"),
    pytest.param("[Snapshot](/foreign/.taskplane/snapshot-a1b2.html)", id="foreign-workspace"),
    pytest.param("[Snapshot]({workspace}/snapshot-a1b2.html)", id="wrong-directory"),
    pytest.param("[Snapshot]({workspace}/.taskplane/../snapshot-a1b2.html)", id="traversal"),
    pytest.param("[Snapshot]({workspace}/.taskplane/dashboard.html)", id="mutable-dashboard"),
    pytest.param("[Snapshot]({workspace}/.TASKPLANE/snapshot-a1b2.html)", id="path-case-changed"),
    pytest.param("[Snapshot](https://example.test/snapshot-a1b2.html)", id="remote-link"),
    pytest.param("[Snapshot]({workspace}/.taskplane/snapshot-a1b2.html) Publish it.", id="extra-prose"),
    pytest.param("[Snapshot]({workspace}/.taskplane/snapshot-a1b2.html)\nApprove Design.", id="foreign-phase"),
    pytest.param("[Snapshot]({workspace}/.taskplane/snapshot-a1b2.html)\n[Snapshot]({workspace}/.taskplane/snapshot-a1b2.html)", id="duplicate-link"),
    pytest.param("[Snapshot]({workspace}/.taskplane/snapshot-a1b2.html)\n[Snapshot]({workspace}/.taskplane/snapshot-c3d4.html)", id="extra-link"),
])
def test_sec01_snapshot_metadata_never_erases_extra_original_text(tmp_path, metadata):
    """Grammar boundary only; transport proof is exercised by the matrix above."""
    from taskplane.workflow_approval import validate_checkpoint_consent
    binding = {"workspace": str(tmp_path), "checkpoint": "a" * 32}
    question = "Approve this checkpoint?\n" + metadata.format(**binding)
    if metadata == "[Snapshot]({workspace}/.taskplane/snapshot-a1b2.html)":
        assert validate_checkpoint_consent(question, "Approved", phase="product", binding=binding) == "approved"
    else:
        with pytest.raises(w.Refusal, match="Unsupported or conditional checkpoint question"):
            validate_checkpoint_consent(question, "Approved", phase="product", binding=binding)


@pytest.mark.parametrize("extra", [
    pytest.param("[Snapshot]({snapshot})", id="duplicate-original-link"),
    pytest.param("[Snapshot](/foreign/.taskplane/snapshot-a1b2.html)", id="foreign-path"),
    pytest.param("[Snapshot]({workspace}/.taskplane/dashboard.html)", id="mutable-path"),
    pytest.param("[Snapshot]({workspace}/.taskplane/snapshot-a1b2.html)", id="extra-link"),
    pytest.param("[Snapshot]({snapshot}) Publish it.", id="extra-link-prose"),
    pytest.param("Approve Design.", id="conflicting-phase"),
])
def test_sec01_v2_original_snapshot_metadata_cannot_disappear(tmp_path, monkeypatch, extra):
    case = consent_transport_case(tmp_path, monkeypatch, "observed-v2",
                                  "Approve this Product checkpoint?\n" + extra)
    assert_consent_refused(case)
