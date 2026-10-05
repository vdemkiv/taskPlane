"""Fixture contracts only: these tests cannot certify Claude updatedInput support."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import base64
import json
import shlex
import threading

import pytest

from taskplane import claude_worker_invocation as inv
from taskplane import primitives, workflow as w


RUNTIME = "a" * 64
SHA = "b" * 64


@pytest.fixture
def clocks(monkeypatch):
    clock = {"utc_ns": 1_800_000_000_000_000_000, "monotonic_ns": 10_000_000_000,
             "boot_id": "fixture-boot"}
    monkeypatch.setattr(inv, "_clock", lambda: dict(clock))
    monkeypatch.setattr(inv, "runtime_digest", lambda: RUNTIME)
    return clock


def words(action="worker", *, workspace="/project with spaces", run="run", task="task", grant="grant"):
    python, script = inv._runtime(None, None)
    if action == "worker":
        return [python, script, "flow", "worker", "--operation", "claim", "--run", run,
                "--grant", grant, "--workspace", workspace]
    return [python, script, "flow", "context", "--workspace", workspace, "--run", run,
            *(["--task", task] if task is not None else [])]


def binding(command=None, *, actor="worker-one", call="call-one", kind=None):
    command = command or inv.parse_command(shlex.join(words()))
    root = kind == "root-context"
    return {"kind": kind or ("worker-claim" if command.action == "worker" else "worker-context"),
            "workspace": command.workspace, "root": "root", "actor": "root" if root else actor,
            "call_id": call, "run": command.run, "visit": "visit", "revision": 3,
            "binding_sha256": SHA, "workspace_contract_sha256": SHA, "runtime_sha256": RUNTIME,
            "task_id": command.task_id if root else command.task_id or "task",
            "active": True, "automatic": True,
            "grant_id": None if root else command.grant_id or "grant",
            "attempt": None if root else 1, "task_generation": None if root else 0,
            "launch_call_id": None if root else "launch-call",
            "launch_evidence_sha256": None if root else SHA,
            "task_sha256": None if root else SHA, "inputs_sha256": None if root else SHA,
            "claimed": None if root else command.action == "context"}


def issue(*, action="worker", argv=None, who=None, tool_input=None):
    command = inv.parse_command(shlex.join(argv or words(action)))
    who = binding(command) if who is None else who
    original = {"command": command.command, "description": "Read the task", "timeout": 10000}
    original.update(tool_input or {})
    record, rewritten = inv.issue_record(command, original, who)
    return command, who, record, original, rewritten


def consume(record, command, who, *, persist=None, dispatch=None, reference=None):
    return inv.consume_record(record, command, reference or record["reference"], who,
                              persist=persist or (lambda value: None), dispatch=dispatch or (lambda: "done"))


@pytest.mark.parametrize("selector", [[], ["--consume", SHA], ["--read-required", SHA],
                                     ["--drain", SHA], ["--read", SHA],
                                     ["--read", SHA, "--page", "7", "--section", "some section"]])
def test_supported_context_grammar_reorders_flags(selector):
    canonical = words("context") + selector
    prefix, options = canonical[:4], canonical[4:]
    unordered = prefix + [x for pair in reversed(list(zip(options[::2], options[1::2]))) for x in pair]
    result = inv.parse_command(shlex.join(unordered))
    assert list(result.argv) == canonical
    assert result.action == "context" and result.task_id == "task"
    assert inv.parse_command(result.canonical).argv == result.argv


def test_claim_reorders_and_quotes_only_literals():
    argv = words(workspace="/project's worktree")
    command = shlex.join(argv[:4] + argv[-2:] + argv[4:-2])
    result = inv.parse_command(command)
    assert result.argv == tuple(argv)
    assert result.canonical == shlex.join(argv)
    assert result.command == command


@pytest.mark.parametrize("suffix", ["; id", " && id", " | cat", " > /tmp/out", " < /tmp/in",
                                   " &", "\ntrue", "\rtrue", "\0", "\tid", " # hidden",
                                   " --invocation-ref v1.cm9vdA." + "c" * 64])
def test_shell_syntax_and_user_supplied_reference_refuse(suffix):
    with pytest.raises(w.Refusal):
        inv.parse_command(shlex.join(words()) + suffix)


@pytest.mark.parametrize("replacement", ["$(id)", "`id`", "$HOME", "${HOME}", "~", "*", "[a-z]",
                                        "a;id", "a|cat", "a\\ b", "a\nb", "a\x7fb"])
def test_metacharacters_refuse_even_inside_quotes(replacement):
    with pytest.raises(w.Refusal):
        inv.parse_command(shlex.join(words(workspace="/" + replacement)))


@pytest.mark.parametrize("mutate", [
    lambda a: ["env", *a], lambda a: ["X=value", *a], lambda a: ["bash", "-c", shlex.join(a)],
    lambda a: [a[0], "-I", *a[1:]], lambda a: ["python3", *a[1:]],
    lambda a: ["/fake/python3", *a[1:]], lambda a: [a[0], "/fake/tp.py", *a[2:]],
    lambda a: [*a, "--run", "run"], lambda a: [*a, "--work", "/elsewhere"],
    lambda a: [*a, "--workspace=/elsewhere"], lambda a: [*a, "unknown"],
    lambda a: [*a[:5], "prepare", *a[6:]], lambda a: a[:-2],
    lambda a: a[:-1] + ["relative"], lambda a: a[:-1] + ["/project/../project"],
    lambda a: a[:-1] + ["//project"], lambda a: a[:-1] + ["/project/"],
])
def test_command_identity_and_option_confusion_refuse(mutate):
    with pytest.raises(w.Refusal):
        inv.parse_command(shlex.join(mutate(words())))


@pytest.mark.parametrize("options", [["--read", SHA, "--consume", SHA], ["--page", "0"],
                                    ["--section", "x"], ["--drain", "B" * 64],
                                    ["--read", SHA, "--page", "-1"], ["--read", SHA, "--page", "01"],
                                    ["--read", SHA, "--page", "1e2"],
                                    ["--read", SHA, "--page", "9999999999"], ["--grant", "grant"],
                                    ["--task", "other"], ["--read", SHA, "--section", "x" * 257]])
def test_context_selectors_refuse_ambiguous_or_invalid_arguments(options):
    with pytest.raises(w.Refusal):
        inv.parse_command(shlex.join(words("context") + options))


def test_bad_quoting_and_oversize_refuse():
    with pytest.raises(w.Refusal):
        inv.parse_command(shlex.join(words()) + " '")
    with pytest.raises(w.Refusal):
        inv.parse_command("x" * (inv.MAX_COMMAND_BYTES + 1))


def test_unsupported_platform_refuses(monkeypatch):
    monkeypatch.setattr(inv.sys, "platform", "win32")
    with pytest.raises(w.Refusal, match="unavailable"):
        inv.parse_command("python tp.py flow context")


def test_reference_decoding_is_bounded_canonical_utf8_and_never_identity():
    for root in ("root", "session-α", "r" * 200):
        reference = inv._reference(root)
        assert inv.decode_reference(reference) == root
        assert len(reference.rsplit(".", 1)[1]) == 64
        assert inv._reference(root) != reference


@pytest.mark.parametrize("value", [None, 10, "", "v2.cm9vdA." + "0" * 64,
                                  "v1.cm9vdA==." + "0" * 64, "v1.cm9vdB." + "0" * 64,
                                  "v1._w." + "0" * 64, "v1.AA." + "0" * 64,
                                  "v1.." + "0" * 64, "v1.cm9vdA." + "A" * 64,
                                  "v1.cm9vdA." + "0" * 63, "v1.cm9vdA." + "0" * 65,
                                  "v1.cm9vdA." + "0" * 64 + ".extra",
                                  "v1." + "a" * 268 + "." + "0" * 64,
                                  "v1." + base64.urlsafe_b64encode(b"a" * 201).decode().rstrip("=") + "." + "0" * 64])
def test_bad_reference_never_reaches_controller_selection(value):
    with pytest.raises(w.Refusal):
        inv.decode_reference(value)


def test_issue_roundtrip_preserves_input_and_binds_both_digests(clocks):
    command, who, record, original, rewritten = issue(tool_input={"run_in_background": False})
    cli, reference = inv.parse_cli(shlex.split(rewritten["command"])[1:])
    assert cli.argv == command.argv and reference == record["reference"]
    assert cli.workspace == who["workspace"]
    assert {k: v for k, v in rewritten.items() if k != "command"} == {k: v for k, v in original.items() if k != "command"}
    assert record["original_input_sha256"] == primitives.content_fingerprint(original)
    assert record["rewritten_input_sha256"] == primitives.content_fingerprint(rewritten)
    assert record["original_input_sha256"] != record["rewritten_input_sha256"]
    assert record["expires"]["utc_ns"] - record["issued"]["utc_ns"] == inv.TTL_NS
    assert record["state"] == "pending"
    assert "description" not in json.dumps(record)
    assert inv.post_input_matches(record, original) and inv.post_input_matches(record, rewritten)
    assert not inv.post_input_matches(record, {**rewritten, "command": rewritten["command"] + " "})
    assert not inv.post_input_matches(record, {**rewritten, "description": "Changed by later hook"})
    who["actor"] = "changed"
    assert record["binding"]["actor"] == "worker-one"


@pytest.mark.parametrize("mutate", [lambda a: a[:-2], lambda a: a + ["--page", "0"],
                                   lambda a: a + a[-2:], lambda a: ["/other/tp.py", *a[1:]],
                                   lambda a: a[:-2] + ["--invocation-ref=" + a[-1]],
                                   lambda a: a[:3] + a[-4:-2] + a[3:-4] + a[-2:]])
def test_cli_requires_exact_final_reference_and_canonical_argv(clocks, mutate):
    _, _, _, _, rewritten = issue()
    argv = shlex.split(rewritten["command"])[1:]
    with pytest.raises(w.Refusal):
        inv.parse_cli(mutate(argv))


def test_cli_does_not_trust_inherited_identity(clocks, monkeypatch):
    command, who, record, _, rewritten = issue()
    for key in ("CLAUDE_SESSION_ID", "CODEX_THREAD_ID", "TASKPLANE_ROOT", "TASKPLANE_WORKER_ID"):
        monkeypatch.setenv(key, "foreign-parent")
    parsed, reference = inv.parse_cli(shlex.split(rewritten["command"])[1:])
    inv.validate_pending(record, parsed, reference, who)
    assert inv.decode_reference(reference) == "root"
    with pytest.raises(w.Refusal):
        inv.parse_cli(list(command.argv)[1:])


@pytest.mark.parametrize("change", [{"actor": "foreign"}, {"root": "foreign"}, {"call_id": "other"},
                                   {"workspace": "/other"}, {"run": "other"}, {"visit": "other"},
                                   {"revision": 4}, {"grant_id": "other"}, {"attempt": 2},
                                   {"task_id": "other"}, {"task_generation": 1},
                                   {"launch_call_id": "other"}, {"launch_evidence_sha256": "c" * 64},
                                   {"runtime_sha256": "c" * 64}, {"workspace_contract_sha256": "c" * 64},
                                   {"task_sha256": "c" * 64}, {"inputs_sha256": "c" * 64},
                                   {"binding_sha256": "c" * 64}, {"claimed": True},
                                   {"active": False}, {"automatic": False}])
def test_every_current_identity_dimension_is_revalidated(clocks, change):
    command, who, record, _, _ = issue()
    called = []
    with pytest.raises(w.Refusal):
        consume(record, command, who | change, dispatch=lambda: called.append(True))
    assert not called and record["state"] == "pending"


@pytest.mark.parametrize("change", [{"actor": "root"}, {"actor": ""}, {"call_id": ""}, {"attempt": True},
                                   {"attempt": 0}, {"task_generation": -1}, {"revision": True},
                                   {"runtime_sha256": None}, {"launch_evidence_sha256": ""},
                                   {"task_id": None}, {"claimed": "yes"}, {"automatic": False},
                                   {"active": False}, {"unexpected": "value"}])
def test_issuance_requires_complete_guarded_automatic_binding(clocks, change):
    command = inv.parse_command(shlex.join(words()))
    with pytest.raises(w.Refusal):
        inv.issue_record(command, {"command": command.command}, binding(command) | change)


def test_context_requires_claimed_exact_worker_task_and_root_is_separate(clocks):
    command = inv.parse_command(shlex.join(words("context")))
    who = binding(command)
    for wrong in (who | {"claimed": False}, who | {"task_id": "foreign"}):
        with pytest.raises(w.Refusal):
            inv.issue_record(command, {"command": command.command}, wrong)
    missing = inv.parse_command(shlex.join(words("context", task=None)))
    with pytest.raises(w.Refusal):
        inv.issue_record(missing, {"command": missing.command}, who)
    root = binding(missing, kind="root-context")
    record, _ = inv.issue_record(missing, {"command": missing.command}, root)
    assert record["binding"]["grant_id"] is None
    for wrong in (root | {"actor": "child"}, root | {"grant_id": "grant"}, who):
        with pytest.raises(w.Refusal):
            consume(record, missing, wrong)
    selected = binding(command, kind="root-context")
    inv.issue_record(command, {"command": command.command}, selected)


def test_grant_only_selects_already_observed_actor(clocks):
    command, who, record, _, _ = issue()
    foreign = inv.parse_command(shlex.join(words(grant="foreign-grant")))
    with pytest.raises(w.Refusal):
        inv.issue_record(foreign, {"command": foreign.command}, who)
    with pytest.raises(w.Refusal):
        consume(record, foreign, who)


@pytest.mark.parametrize("data", [{"run_in_background": True}, {"run_in_background": "false"},
                                 {"timeout": float("nan")}, {"description": "x" * (inv.MAX_INPUT_BYTES + 1)},
                                 {"command": "different"}])
def test_unsafe_bash_input_refuses(clocks, data):
    with pytest.raises(w.Refusal):
        issue(tool_input=data)


def test_duplicate_pre_hook_is_idempotent_only_while_identical_and_pending(clocks):
    command, who, record, original, rewritten = issue()
    repeat, repeated_input = inv.issue_record(command, original, who, existing=record)
    assert repeat == record and repeated_input == rewritten and repeat is not record
    with pytest.raises(w.Refusal):
        inv.issue_record(command, original | {"description": "changed"}, who, existing=record)
    reordered = inv.parse_command(shlex.join(words()[:4] + words()[-2:] + words()[4:-2]))
    with pytest.raises(w.Refusal):
        inv.issue_record(reordered, {"command": reordered.command}, who, existing=record)
    consume(record, command, who)
    with pytest.raises(w.Refusal):
        inv.issue_record(command, original, who, existing=record)


@pytest.mark.parametrize("axis, delta", [("utc_ns", inv.TTL_NS), ("monotonic_ns", inv.TTL_NS),
                                       ("utc_ns", -1), ("monotonic_ns", -1)])
def test_either_clock_expiry_or_regression_burns_validity(clocks, axis, delta):
    command, who, record, original, _ = issue()
    clocks[axis] += delta
    with pytest.raises(w.Refusal, match="expired"):
        consume(record, command, who)
    with pytest.raises(w.Refusal):
        inv.issue_record(command, original, who, existing=record)


def test_reboot_runtime_drift_and_unavailable_clock_refuse(clocks, monkeypatch):
    command, who, record, _, _ = issue()
    clocks["boot_id"] = "new-boot"
    with pytest.raises(w.Refusal):
        consume(record, command, who)
    clocks["boot_id"] = "fixture-boot"
    monkeypatch.setattr(inv, "runtime_digest", lambda: "c" * 64)
    with pytest.raises(w.Refusal, match="runtime"):
        consume(record, command, who)
    monkeypatch.setattr(inv, "runtime_digest", lambda: RUNTIME)
    monkeypatch.setattr(inv, "_clock", lambda: {**clocks, "utc_ns": False})
    with pytest.raises(w.Refusal):
        consume(record, command, who)


def test_persistence_precedes_dispatch_and_replay_always_refuses(clocks):
    command, who, record, original, rewritten = issue()
    trace = []
    def persist(value):
        inv.validate_record(value)
        assert value["state"] == "consumed" and value["consumed"] == clocks
        trace.append("saved")
    def dispatch():
        assert trace == ["saved"]
        trace.append("dispatched")
        return {"claimed": True}
    assert consume(record, command, who, persist=persist, dispatch=dispatch) == {"claimed": True}
    assert trace == ["saved", "dispatched"]
    with pytest.raises(w.Refusal, match="consumed"):
        consume(record, command, who)
    clocks["utc_ns"] += inv.TTL_NS * 2
    assert inv.post_input_matches(record, original) and inv.post_input_matches(record, rewritten)
    assert record["state"] == "consumed"  # A late post never recreates authority.


@pytest.mark.parametrize("error", [OSError("disk full"), primitives.StateError("store", "lock/write failure"),
                                  RuntimeError("unexpected persistence failure")])
def test_write_failure_does_not_dispatch_or_restore_in_memory_authority(clocks, error):
    command, who, record, _, _ = issue()
    called = []
    def fail(value):
        raise error
    with pytest.raises((w.Refusal, RuntimeError)):
        consume(record, command, who, persist=fail, dispatch=lambda: called.append(True))
    assert not called and record["state"] == "consumed"
    with pytest.raises(w.Refusal):
        consume(record, command, who)


def test_dispatch_failure_or_crash_burns_saved_reference(clocks, tmp_path):
    command, who, record, _, _ = issue()
    store = tmp_path / "control.json"
    def crash():
        raise RuntimeError("crash after consumption")
    with pytest.raises(RuntimeError):
        consume(record, command, who, persist=lambda r: primitives.atomic_json(store, r), dispatch=crash)
    persisted = json.loads(store.read_text())
    assert persisted["state"] == "consumed"
    with pytest.raises(w.Refusal):
        consume(persisted, command, who)


def test_concurrent_consumers_under_controller_lock_admit_exactly_one(clocks, tmp_path):
    command, who, record, _, _ = issue()
    store = tmp_path / "control.json"
    primitives.atomic_json(store, record)
    start = threading.Barrier(2)
    dispatched = []
    def attempt():
        start.wait()
        with primitives.file_lock(str(store)):
            current = json.loads(store.read_text())
            try:
                return consume(current, command, who,
                               persist=lambda r: primitives.atomic_json(store, r),
                               dispatch=lambda: dispatched.append(who["actor"]) or "success")
            except w.Refusal:
                return "refused"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(results) == ["refused", "success"]
    assert dispatched == ["worker-one"]


def test_two_actors_same_context_command_reverse_consumption_stay_separate(clocks):
    command = inv.parse_command(shlex.join(words("context")))
    one = binding(command, actor="one", call="call-one")
    two = binding(command, actor="two", call="call-two") | {"grant_id": "grant-two", "launch_call_id": "launch-two"}
    r1, _ = inv.issue_record(command, {"command": command.command}, one)
    r2, _ = inv.issue_record(command, {"command": command.command}, two)
    assert r1["reference"] != r2["reference"]
    with pytest.raises(w.Refusal):
        consume(r1, command, two)
    with pytest.raises(w.Refusal):
        consume(r1, command, one, reference=r2["reference"])
    assert consume(r2, command, two) == "done"
    assert consume(r1, command, one) == "done"


@pytest.mark.parametrize("mutate", [lambda r: r.update(extra="bad"), lambda r: r.pop("binding"),
                                   lambda r: r.update(argv="bad"), lambda r: r.update(state="unknown"),
                                   lambda r: r["binding"].update(actor="foreign"),
                                   lambda r: r["issued"].update(utc_ns=True),
                                   lambda r: r["expires"].update(utc_ns=r["expires"]["utc_ns"] + 1),
                                   lambda r: r.update(consumed={"utc_ns": 1}),
                                   lambda r: r.update(reference=inv._reference("foreign")),
                                   lambda r: r.update(argv_sha256="0" * 64)])
def test_corrupt_record_refuses_even_if_unkeyed_checksum_recomputed(clocks, mutate):
    _, _, original, _, _ = issue()
    record = deepcopy(original)
    mutate(record)
    with pytest.raises(w.Refusal):
        inv.validate_record(record)
    # A checksum detects damage but conveys no authority. Semantically invalid
    # records must also fail validation even if a local writer can recompute it.
    record = primitives.manifest_record(record)
    if record.get("binding", {}).get("actor") == "foreign":
        # Structurally valid actor changes instead fail against current state.
        command, who, _, _, _ = issue()
        with pytest.raises(w.Refusal):
            consume(record, command, who)
    else:
        with pytest.raises(w.Refusal):
            inv.validate_record(record)


def test_runtime_launcher_drift_refuses_even_if_other_binding_matches(clocks, monkeypatch):
    command, who, record, _, _ = issue()
    monkeypatch.setattr(inv.sys, "executable", "/different/python")
    with pytest.raises(w.Refusal, match="installed Python"):
        consume(record, command, who)


def test_expiry_during_durable_save_burns_without_dispatch(clocks):
    command, who, record, _, _ = issue()
    calls = []
    def persist(value):
        calls.append("saved")
        clocks["monotonic_ns"] += inv.TTL_NS
    with pytest.raises(w.Refusal, match="expired"):
        consume(record, command, who, persist=persist, dispatch=lambda: calls.append("dispatched"))
    assert calls == ["saved"] and record["state"] == "consumed"


@pytest.mark.parametrize("field", ["state", "binding", "issued", "expires", "argv", "reference"])
@pytest.mark.parametrize("value", [None, False, [], {}, 42])
def test_malformed_record_values_refuse_without_type_errors(clocks, field, value):
    _, _, record, _, _ = issue()
    record[field] = value
    record = primitives.manifest_record(record)
    with pytest.raises(w.Refusal):
        inv.validate_record(record)


def test_command_dataclass_is_not_an_authority_constructor(clocks):
    parsed = inv.parse_command(shlex.join(words()))
    forged = inv.InvocationCommand(parsed.command, parsed.argv, parsed.action, "/other", parsed.run,
                                   parsed.task_id, parsed.grant_id)
    with pytest.raises(w.Refusal):
        inv.issue_record(forged, {"command": forged.command}, binding(forged))


def test_post_input_cannot_restore_missing_evicted_or_consumed_record(clocks):
    command, who, record, original, _ = issue()
    consume(record, command, who)
    assert inv.post_input_matches(record, original)
    assert record["state"] == "consumed"
    for absent in (None, {}):
        with pytest.raises(w.Refusal):
            inv.validate_pending(absent, command, record["reference"], who)


def test_real_os_boot_adapter_is_stable_or_refuses_when_os_denies_access():
    if inv.sys.platform not in {"linux", "darwin"}:
        pytest.skip("No boot adapter for this platform")
    try:
        boot = inv._boot_identity()
    except OSError:
        # A sandbox can deny kern.boottime (EPERM). That is not a live success:
        # validate the production refusal, without a fabricated boot fallback.
        with pytest.raises(w.Refusal, match="unavailable"):
            inv._clock()
        return
    assert boot == inv._boot_identity()
    assert inv._valid_clock(inv._clock())


def test_os_boot_adapter_failure_is_closed(monkeypatch):
    def fail():
        raise OSError("Unavailable")
    monkeypatch.setattr(inv, "_boot_identity", fail)
    with pytest.raises(w.Refusal, match="unavailable"):
        inv._clock()
