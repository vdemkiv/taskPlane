import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

import pytest

import lens
import loop
import review
import review_evidence
import review_progression
import storage as runtime_storage
import taskplane_lite as tp
import tp as tp_cli
import build_c


R0006_DIRECTIVE = (
    "Review the shared server-side components and API functionality with "
    "architecture, security, QA, and code quality in mind."
)


def test_define_impact_accepts_scanned_graph_file_counts():
    graph = {
        "modules": {
            "taskplane": {"files": 2, "kind": "module"},
            "taskplane/tests": {"files": 1, "kind": "module"},
        },
        "files": {
            "taskplane/build_c.py": {},
            "taskplane/tests/test_loop.py": {},
        },
    }

    impact = build_c._define_impact(
        graph,
        ["taskplane/build_c.py", "taskplane/tests/test_loop.py", "unknown.py"],
    )

    assert impact == {
        "touched": ["taskplane", "taskplane/tests"],
        "impacted": {},
        "total_impacted": 2,
        "unknown": ["unknown.py"],
    }


def test_event_wait_policy_is_long_lived_and_wake_driven():
    policy = loop.event_wait_policy("review-sweep", 5)
    assert policy == {
        "schema": "taskplane.wait-policy/v1",
        "outstanding_set": "review-sweep",
        "outstanding_count": 5,
        "mode": "event",
        "timeout_seconds": 1800,
        "minimum_timeout_seconds": 300,
        "reissue_after": ["completion", "attention"],
        "scheduled_polling": False,
    }


def test_native_dispatch_guidance_forbids_repeat_polling():
    root = Path(__file__).resolve().parents[2]
    text = (root / "skills/tp-go/references/codex-native-dispatch.md").read_text()
    assert "one event-driven wait per outstanding set" in text
    assert "at least 1800 seconds" in text
    assert "Reissue only after" in text
    assert "repeat while agents" not in text


def test_automatic_review_is_one_four_or_five_lens_sweep():
    for stage in ("review", "build"):
        routing = lens.route(
            ["taskplane/review.py", "taskplane/lens.py"], stage=stage,
            requirement_text=R0006_DIRECTIVE,
        )
        selected = [row for row in routing["lenses"]
                    if row["tier"] == "sweep"]
        assert 4 <= len(selected) <= 5
        assert "architecture" in {row["id"] for row in selected}
        assert {row["tier"] for row in routing["lenses"]} == {"sweep", "n/a"}
        assert all(row["mode"] == "subagent" for row in selected)
        assert all(row["mode"] == "none" for row in routing["lenses"]
                   if row["tier"] == "n/a")
        assert not [row for row in routing["lenses"]
                    if row["tier"] == "deep"]

    wave = review_progression.initial_wave(routing)
    assert wave["deep"] == []
    assert 4 <= len(wave["sweep"]) <= 5
    assert wave["sweep_count"] == len(wave["sweep"])
    assert {row["lens"] for row in wave["sweep"]} == {
        row["id"] for row in selected}
    assert len({row["slot"] for row in wave["sweep"]}) == len(
        wave["sweep"])
    assert wave["dispatch_set"]["concurrent"] is True
    assert wave["dispatch_set"]["member_count"] == len(wave["sweep"])
    assert wave["wait_policy"]["outstanding_set"] == \
        wave["dispatch_set"]["id"]
    assert wave["wait_policy"]["timeout_seconds"] >= 1800
    assert wave["wait_policy"]["scheduled_polling"] is False


def test_define_projection_reuses_one_quick_selector_and_one_event_wait(
        tmp_path):
    workspace = str(tmp_path)
    (tmp_path / "exports").mkdir()
    (tmp_path / "design").mkdir()
    (tmp_path / "exports" / "r0012-program-ledger.json").write_text(
        json.dumps({
            "schema": "taskplane.r0012-program-ledger/v1",
            "program_authority": {
                "schema": build_c.PROGRAM_LEDGER_SCHEMA,
                "consolidated_approval": {
                    "approved": True, "actor": "user",
                    "authority_receipt": "decision:0045"},
                "r0009": {"accepted": True,
                           "evidence_digest": "baseline:green"},
                "r0010": {"status": "active"},
                "r0011": {"exact_sha_green": False,
                           "signed_off_by": None},
            },
        }), encoding="utf-8")
    (tmp_path / "design" / "contract.json").write_text(json.dumps({
        "graph": {"proposed_modules": ["taskplane/loop.py"]},
    }), encoding="utf-8")
    calls = []

    def start_review(*args, **kwargs):
        calls.append(kwargs)
        kwargs["router"]()
        dispatch_set = {
            "schema": "taskplane.dispatch-set/v1",
            "id": "automatic-review-sweep", "concurrent": True,
            "member_count": 4,
        }
        wait_policy = {
            "schema": "taskplane.wait-policy/v1",
            "outstanding_set": dispatch_set["id"],
            "outstanding_count": 4, "mode": "event",
            "timeout_seconds": 1800, "scheduled_polling": False,
            "reissue_after": ["completion", "attention"],
        }
        slots = [
            {"slot_id": f"sweep.{lens_id}", "lens_ids": [lens_id],
             "dispatch_set": dispatch_set, "wait_policy": wait_policy}
            for lens_id in ("architecture", "security", "qa",
                            "code-quality")
        ]
        return {
            "schema": "taskplane.review-start-manifest/v2",
            "status": "ready", "stage": "define", "run_id": "define-1",
            "routing_mode": "selective", "routing_counts": {
                "sweep": 4, "n/a": 22},
            "review_depth_policy": {
                "depth": "quick-only", "deep_slots_allowed": False,
                "deep_slots": [], "promotion_attempts": 0,
                "quick_slots": [row["slot_id"] for row in slots],
            },
            "slots": slots,
        }

    def selector(*args, **kwargs):
        return {"lenses": [], "context": {}}

    def bind_actions(_ws, manifest, *, task_id):
        assert task_id == "define"
        slots = []
        for row in manifest["slots"]:
            task_slot = "review-" + row["slot_id"].replace(".", "-")
            slots.append({**row, "contract_bootstrap": {
                "activation_order": "orchestrator_before_subagent_start",
                "environment": {"TASKPLANE_TASK": task_slot},
                "task_slot": task_slot,
            }})
        return {**manifest, "slots": slots, "collection": {
            "schema": "taskplane.review-collection-bridge/v1",
            "function": "loop.collect_review_bridge",
            "run_id": manifest["run_id"],
            "release_incomplete_producers": True,
        }, "wait_invocation": {
            "schema": "taskplane.event-wait-invocation/v1",
            "operation": "wait_for_events",
            "outstanding_members": [row["slot_id"]
                                    for row in manifest["slots"]],
            "timeout_seconds": 1800, "scheduled": False,
            "reissue": False, "wake": None,
        }}

    projected = build_c.project_define(
        workspace,
        {"goal": "approved program", "design_fingerprint": "design-1",
         "design_approved_by": "user"},
        start_review=start_review, selector=selector,
        bind_actions=bind_actions,
        graph={"meta": {"content_fingerprint": "graph-1"},
               "modules": {}, "edges": []}, revision="abc123")

    assert len(calls) == 1
    assert calls[0]["stage"] == "define"
    assert calls[0]["requirement"]["review_policy"] == {
        "depth": "quick-only"}
    assert projected["dispatch_set"]["concurrent"] is True
    assert projected["dispatch_set"]["member_count"] == 4
    assert projected["dispatch_set"]["id"] == "automatic-review-sweep"
    assert projected["selector_invocations"] == 1
    assert projected["serial_fallback"] is False
    assert projected["selected_lenses"][0] == "architecture"
    assert projected["wait_invocation"]["scheduled"] is False
    assert "routing" not in projected


def test_define_projection_refuses_deep_or_selector_reentry_shapes(tmp_path):
    manifest = {
        "status": "ready", "stage": "define", "run_id": "define-1",
        "routing_mode": "selective",
        "slots": [
            {"slot_id": "deep.architecture", "lens_ids": ["architecture"]},
            {"slot_id": "sweep.security", "lens_ids": ["security"]},
            {"slot_id": "sweep.qa", "lens_ids": ["qa"]},
            {"slot_id": "sweep.code-quality", "lens_ids": ["code-quality"]},
        ],
    }
    with pytest.raises(build_c.DefineProjectionError, match="quick sweep"):
        build_c.validate_define_projection(manifest)


@pytest.mark.parametrize(
    ("selector_calls", "concurrent", "message"),
    [(2, True, "exactly one selector invocation"),
     (1, False, "concurrent router-produced dispatch set")],
)
def test_define_projection_refuses_reentered_or_serial_router_evidence(
        tmp_path, selector_calls, concurrent, message):
    workspace = str(tmp_path)
    (tmp_path / "exports").mkdir()
    (tmp_path / "design").mkdir()
    (tmp_path / "exports" / "r0012-program-ledger.json").write_text(
        json.dumps({
            "schema": "taskplane.r0012-program-ledger/v1",
            "program_authority": {
                "schema": build_c.PROGRAM_LEDGER_SCHEMA,
                "consolidated_approval": {
                    "approved": True, "actor": "user",
                    "authority_receipt": "decision:0045"},
                "r0009": {"accepted": True,
                           "evidence_digest": "baseline:green"},
                "r0010": {"status": "active"},
                "r0011": {"exact_sha_green": False,
                           "signed_off_by": None},
            },
        }), encoding="utf-8")
    (tmp_path / "design" / "contract.json").write_text(json.dumps({
        "graph": {"proposed_modules": ["taskplane/loop.py"]},
    }), encoding="utf-8")
    dispatch_set = {
        "schema": "taskplane.dispatch-set/v1",
        "id": "automatic-review-sweep", "concurrent": concurrent,
        "member_count": 4,
    }
    wait_policy = {
        "schema": "taskplane.wait-policy/v1",
        "outstanding_set": dispatch_set["id"],
        "outstanding_count": 4, "mode": "event",
        "timeout_seconds": 1800, "scheduled_polling": False,
        "reissue_after": ["completion", "attention"],
    }
    slots = [
        {"slot_id": f"sweep.{lens_id}", "lens_ids": [lens_id],
         "dispatch_set": dispatch_set, "wait_policy": wait_policy}
        for lens_id in ("architecture", "security", "qa", "code-quality")
    ]

    def start_review(*args, **kwargs):
        router = kwargs.get("router")
        if router:
            for _ in range(selector_calls):
                router()
        return {
            "status": "ready", "stage": "define", "run_id": "define-1",
            "routing_mode": "selective",
            "routing_counts": {"sweep": 4, "n/a": 22},
            "review_depth_policy": {
                "depth": "quick-only", "deep_slots_allowed": False,
                "deep_slots": [], "promotion_attempts": 0,
                "quick_slots": [row["slot_id"] for row in slots],
            },
            "slots": slots,
        }

    def bind_actions(_ws, manifest, *, task_id):
        return {**manifest, "wait_invocation": {
            "operation": "wait_for_events",
            "outstanding_members": [row["slot_id"]
                                    for row in manifest["slots"]],
            "timeout_seconds": 1800, "scheduled": False,
            "reissue": False,
        }}

    with pytest.raises(build_c.DefineProjectionError, match=message):
        build_c.project_define(
            workspace,
            {"goal": "approved program", "design_fingerprint": "design-1",
             "design_approved_by": "user"},
            start_review=start_review,
            selector=lambda *args, **kwargs: {
                "lenses": [], "context": {}},
            bind_actions=bind_actions,
            graph={"modules": {}, "edges": []}, revision="abc123")


def test_directives_pin_membership_without_promoting_depth():
    routing = lens.route(
        ["taskplane/review.py"], stage="review",
        requirement_text=R0006_DIRECTIVE,
    )
    pinned = review._directive_lens_ids(
        [{"text": R0006_DIRECTIVE}], lens.load_catalog())
    routed = lens.automatic_sweep_route(routing, pinned_lenses=pinned)
    selected = [row for row in routed["lenses"] if row["tier"] == "sweep"]
    assert 4 <= len(selected) <= 5
    assert {row["tier"] for row in routed["lenses"]} == {"sweep", "n/a"}
    assert "architecture" in {row["id"] for row in selected}
    assert all(row["mode"] == "subagent" for row in selected)


def test_live_review_edges_use_selector_wait_and_sweep_collector():
    root = Path(__file__).resolve().parents[1]
    review_source = (root / "review.py").read_text()
    loop_source = (root / "loop.py").read_text()
    lens_source = (root / "lens.py").read_text()
    assert "lensmod.automatic_sweep_route(" in review_source
    assert "event_wait_policy(" in loop_source
    assert 'row["verdict"] in {"sweep", "light"}' in review_source
    assert '"dispatch_set"' in lens_source


def test_em_instruction_consumes_exact_concurrent_sweep_slots_once():
    instruction = loop._instruction(
        "em", {"tasks": [{"id": "signoff"}], "current_task": 0}, ".")
    assert "every emitted `review_kernel.slots` entry concurrently" in \
        instruction
    assert "exact brief, lease, contract_bootstrap, and result_path" in \
        instruction
    assert "exactly `review_kernel.wait_invocation` once" in instruction
    assert "Refuse selector re-entry, serial fallback" in instruction
    assert "deep/light/full/26-lens dispatch" in instruction
    assert "Run exact tier=deep slots" not in instruction
    assert "tier=light sweep" not in instruction

    source = Path(loop.__file__).read_text(encoding="utf-8")
    em_text = source[source.index('"em": "Run tp-engineering'):]
    em_text = em_text[:em_text.index("\n    }[step]")]
    assert '"`review_kernel.slots` entry concurrently' in em_text
    assert '"once. Refuse selector re-entry, serial fallback' in em_text


def test_producer_activation_dispatches_independent_sweep_set_and_collects_all(
        tmp_path, monkeypatch):
    workspace = str(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    outer_run_id = "outer-delivery-run"
    identity = runtime_storage.resolve_repository_identity(workspace)
    layout = runtime_storage.resolve_layout(
        identity, home=str(tmp_path / ".runtime-home"),
        run_id=outer_run_id)
    runtime_storage.write_workspace_locator(
        workspace, identity=identity, layout=layout,
        run_id=outer_run_id)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/service.py").write_text(
        "def changed():\n    return 2\n", encoding="utf-8")
    production_calls = []
    production_slot_plan = review._slot_plan
    wait_calls = []
    production_wait_invocation = loop.event_wait_invocation

    def observed_slot_plan(*args, **kwargs):
        production_calls.append(True)
        return production_slot_plan(*args, **kwargs)

    def observed_wait_invocation(*args, **kwargs):
        wait_calls.append((args, kwargs))
        return production_wait_invocation(*args, **kwargs)

    monkeypatch.setattr(review, "_slot_plan", observed_slot_plan)
    monkeypatch.setattr(loop, "event_wait_invocation",
                        observed_wait_invocation)
    opened = review.start_review(
        workspace,
        target={"fingerprint": "target-1", "head": "abc123"},
        graph={"meta": {"scanned_head": "abc123",
                        "content_fingerprint": "graph-1"},
               "modules": {"src": {"files": ["src/service.py"]}},
               "edges": []},
        impact={"touched": ["src"], "impacted": {},
                "total_impacted": 1, "unknown": []},
        diff={"files": ["src/service.py"],
              "changed_symbols": ["changed"],
              "patch_artifact": {"fingerprint": "diff-1"}},
        runnability={"summary": "available"},
        requirement={"id": "R-0007", "text": R0006_DIRECTIVE},
        acceptance=["works"], contracts=["contract:review.collection"],
    )
    assert opened["status"] == "ready"
    assert production_calls == [True]
    assert 4 <= len(opened["slots"]) <= 5
    assert all(slot["slot_id"].startswith("sweep.")
               for slot in opened["slots"])
    assert all(len(slot["lens_ids"]) == 1 for slot in opened["slots"])
    assert "architecture" in {
        slot["lens_ids"][0] for slot in opened["slots"]}

    state = review._load_state(workspace, opened["run_id"])
    store = review_evidence.ArtifactStore(workspace)
    assert len(state["slots"]) == len(opened["slots"])
    briefs = [store.read(slot["brief"]) for slot in state["slots"]]
    leases = [store.read(slot["lease"]) for slot in state["slots"]]
    set_ids = {brief["dispatch_set"]["id"] for brief in briefs}
    wait_policies = {
        json.dumps(brief["wait_policy"], sort_keys=True) for brief in briefs}
    assert len(set_ids) == len(wait_policies) == 1
    assert {brief["wait_policy"]["outstanding_set"] for brief in briefs} == \
        set_ids
    assert all(brief["dispatch_set"]["concurrent"] for brief in briefs)
    assert all(brief["dispatch_set"]["member_count"] == len(briefs)
               for brief in briefs)
    assert all(brief["wait_policy"]["outstanding_count"] == len(briefs)
               for brief in briefs)
    assert all(brief["wait_policy"]["timeout_seconds"] >= 1800
               for brief in briefs)
    assert len({slot["brief"]["fingerprint"] for slot in state["slots"]}) == \
        len(briefs)
    assert len({lease["lease_fingerprint"] for lease in leases}) == len(leases)
    assert len({brief["producer_contract"]["task_slot"] for brief in briefs}) == \
        len(briefs)
    assert len({brief["role"]["task_name"] for brief in briefs}) == len(briefs)
    assert len({slot["result_path"] for slot in state["slots"]}) == len(briefs)

    instruction = loop._instruction(
        "evaluate", {"tasks": [{"id": "bootstrap-sweep"}],
                     "current_task": 0}, workspace)
    assert "one governed read-only subagent per subagent-mode lens" in instruction
    assert "Pass each slot's contract_bootstrap unchanged" in instruction
    worker_lenses = {
        row["id"] for row in state["routing"]["lenses"]
        if row.get("mode") == "subagent"}
    assert len(worker_lenses) == 5
    bound = loop._bind_stateless_review_contract_actions(
        workspace, opened, task_id="bootstrap-sweep", now=int(time.time()))
    assert len(wait_calls) == 1
    wait_invocation = bound["wait_invocation"]
    assert wait_invocation == {
        "schema": "taskplane.event-wait-invocation/v1",
        "operation": "wait_for_events",
        "outstanding_set": next(iter(set_ids)),
        "outstanding_members": [slot["slot_id"] for slot in state["slots"]],
        "timeout_seconds": 1800,
        "scheduled": False,
        "reissue": False,
        "wake": None,
    }
    shared_policy = briefs[0]["wait_policy"]
    for wake in ("completion", "attention"):
        reissued = production_wait_invocation(
            shared_policy, wait_invocation["outstanding_members"], wake=wake)
        assert reissued["reissue"] is True
        assert reissued["wake"] == wake
    for forbidden_wake in ("timeout", "scheduled", "unscheduled"):
        with pytest.raises(ValueError, match="completion or attention"):
            production_wait_invocation(
                shared_policy, wait_invocation["outstanding_members"],
                wake=forbidden_wake)
    worker_slots = [
        slot for slot in bound["slots"]
        if slot["lens_ids"][0] in worker_lenses]
    assert len(worker_slots) == len(bound["slots"]) == 5
    bootstraps = [slot["contract_bootstrap"] for slot in worker_slots]
    assert len({row["action"]["action_id"] for row in bootstraps}) == 5
    assert {row["command"] for row in bootstraps} == {
        "review activate-contract"}
    assert all(row["activation_order"] ==
               "orchestrator_before_subagent_start" for row in bootstraps)
    assert all(row["environment"] == {
        "TASKPLANE_TASK": row["task_slot"]} for row in bootstraps)
    assert all("TASKPLANE_TASK=" not in row["host_command"]
               for row in bootstraps)
    cli = Path(__file__).resolve().parents[1] / "tp.py"

    substituted_interpreter = list(bootstraps[0]["command_argv"])
    substituted_interpreter[0] = "/usr/bin/python3"
    if os.path.realpath(substituted_interpreter[0]) == os.path.realpath(
            sys.executable):
        substituted_interpreter[0] = "/bin/false"
    with pytest.raises(tp.StateError, match="command shape is invalid"):
        tp_cli._visible_review_bootstrap(
            shlex.join(substituted_interpreter), workspace)

    native_host_cwd = tmp_path / "native-host-cwd"
    native_host_cwd.mkdir()
    locator = runtime_storage.load_workspace_locator(workspace)
    assert locator["run_id"] == outer_run_id
    assert opened["run_id"] != outer_run_id
    parsed_native = tp_cli._visible_review_bootstrap(
        bootstraps[0]["host_command"], str(native_host_cwd))
    assert parsed_native["workspace"] == os.path.realpath(workspace)

    mutated_argv = list(bootstraps[0]["command_argv"])
    action_index = mutated_argv.index("--signed-action") + 1
    mutated_argv[action_index] = mutated_argv[action_index][:-1] + (
        "A" if mutated_argv[action_index][-1] != "A" else "B")
    denied_mutation = subprocess.run(
        [sys.executable, str(cli), "screen"], cwd=workspace,
        input=json.dumps({
            "cwd": workspace, "turn_id": "host-pretool-mutated",
            "tool_name": "Bash",
            "tool_input": {"command": shlex.join(mutated_argv)},
        }), text=True, capture_output=True, check=False)
    assert denied_mutation.returncode == 0, denied_mutation.stderr
    assert json.loads(denied_mutation.stdout)["decision"] == "block"
    assert tp.list_task_slots(workspace) == []

    for bootstrap in bootstraps:
        expected = bootstrap["expected"]
        environment = os.environ.copy()
        for name in ("TASKPLANE_TASK", "TASKPLANE_REVIEW_CONTRACT_ACTION",
                     "TASKPLANE_REVIEW_CONTRACT_EXPECTED"):
            environment.pop(name, None)
        screened = subprocess.run(
            [sys.executable, str(cli), "screen"], cwd=workspace,
            env=environment, input=json.dumps({
                "cwd": workspace,
                "turn_id": "host-pretool-" + expected["action_id"],
                "tool_name": "Bash",
                "tool_input": {"command": bootstrap["host_command"]},
            }), text=True, capture_output=True, check=False)
        assert screened.returncode == 0, screened.stderr
        assert screened.stdout == ""
        assert bootstrap["task_slot"] in tp.list_task_slots(workspace)
        activated = subprocess.run(
            bootstrap["command_argv"], cwd=workspace, env=environment,
            text=True, capture_output=True, check=False)
        assert activated.returncode == 0, activated.stderr
        receipt = json.loads(activated.stdout)
        assert receipt["status"] == "active"
        assert receipt["task_slot"] == bootstrap["task_slot"]
        assert receipt["action_id"] == expected["action_id"]
    assert set(tp.list_task_slots(workspace)) == {
        row["task_slot"] for row in bootstraps}
    active_union = tp.load_active(workspace)
    assert len(active_union["_union"]) == 5
    legacy_screened = subprocess.run(
        [sys.executable, str(cli), "screen"], cwd=workspace,
        env=environment, input=json.dumps({
            "cwd": workspace, "turn_id": "host-pretool-legacy",
            "tool_name": "Bash",
            "tool_input": {
                "command": f"{sys.executable} -m taskplane_lite"},
        }), text=True, capture_output=True, check=False)
    assert legacy_screened.returncode == 0, legacy_screened.stderr
    legacy_denial = json.loads(legacy_screened.stdout)
    assert legacy_denial["decision"] == "block"
    assert "legacy direct taskplane_lite activation" in \
        legacy_denial["reason"]
    assert "most-restrictive union of 5 active contracts" in \
        legacy_denial["reason"]

    for index, (slot, lease, brief) in enumerate(
            zip(state["slots"], leases, briefs, strict=True)):
        assert lease["lens_ids"] == slot["lens_ids"]
        lens_id = lease["lens_ids"][0]
        result = {
            **lease, "schema": "taskplane.lens-slot-output/v2",
            "authored_by": "lens-slot", "findings": [],
            "lens_results": [{
                "lens": lens_id, "verdict": "pass", "blockers": 0,
                "checked_evidence": [{
                    "file": "src/service.py", "line": 1,
                    "claim": f"{lens_id} reviewed changed behavior"}],
            }],
        }
        if brief.get("language_references"):
            result["references_applied"] = brief["language_references"]
        content = json.dumps(result, sort_keys=True, separators=(",", ":"))
        event = {
            "turn_id": f"sweep-child-turn-{index}",
            "tool_use_id": f"sweep-write-{index}",
            "agent_id": f"sweep-child-{index}", "tool_name": "Write",
            "tool_input": {"file_path": slot["result_path"],
                           "content": content},
        }
        producer_slot = brief["producer_contract"]["task_slot"]
        screen_environment = os.environ.copy()
        screen_environment.update(
            {"TASKPLANE_TASK": producer_slot,
             "TASKPLANE_HOOK_PATH": "native"})
        started = subprocess.run(
            [sys.executable, str(cli), "subagent-start"], cwd=workspace,
            env=screen_environment, input=json.dumps({
                "cwd": workspace,
                "turn_id": event["turn_id"],
                "agent_id": event["agent_id"],
                "agent_type": "default",
            }), text=True, capture_output=True, check=False)
        assert started.returncode == 0, started.stderr
        screened = subprocess.run(
            [sys.executable, str(cli), "screen"], cwd=workspace,
            env=screen_environment, input=json.dumps(event), text=True,
            capture_output=True, check=False)
        assert screened.returncode == 0, screened.stderr
        assert '"decision": "block"' not in screened.stdout
        path = os.path.join(workspace, slot["result_path"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        Path(path).write_text(content, encoding="utf-8")

    assert tp.list_task_slots(workspace) == []

    collected = loop.collect_review_bridge(
        workspace, publish=False, run_id=opened["run_id"])
    assert collected["status"] == "complete"
    assert collected["slot_conservation"]["collected"]["count"] == len(briefs)
    assert set(collected["slot_conservation"]["collected"]["slot_ids"]) == {
        slot["slot_id"] for slot in state["slots"]}


def test_review_bridge_releases_missing_result_producer_slots(
        tmp_path, monkeypatch):
    workspace = str(tmp_path)
    slots = []
    for suffix in ("a", "b"):
        task_slot = f"review-{suffix * 20}"
        producer = {
            "task": f"review producer {suffix}",
            "task_slot": task_slot,
            "read_only": True,
            "write_allow": [f".eval/results/{suffix}.json"],
        }
        slots.append({"slot_id": f"sweep.{suffix}",
                      "producer_contract": producer})
        tp.atomic_write_json(
            tp.active_contract_path(workspace, task_slot), producer)
    state = {"run_id": "missing-results", "status": "ready",
             "slots": slots}
    monkeypatch.setattr(review, "_load_state",
                        lambda *_args, **_kwargs: state)
    monkeypatch.setattr(review, "collect_review", lambda *_args, **_kwargs: {
        "status": "incomplete", "repairs": ["sweep.a", "sweep.b"]})

    result = loop.collect_review_bridge(
        workspace, publish=False, run_id="missing-results")

    assert result["status"] == "incomplete"
    assert tp.list_task_slots(workspace) == []
