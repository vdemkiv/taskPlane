"""F7/F8 adversarial fixtures; these do not claim live provider/browser coverage."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import time

import pytest

from taskplane import dashboard, flow_dashboard, flow_telemetry as telemetry, flow_usage, native_session_meter as meter
from taskplane import primitives, snapshot_retention as snapshots, workflow as w
from taskplane import workflow_retention, workspace_binding

requires_collection = pytest.mark.skipif(
    not snapshots.supports_collection(), reason="Snapshot deletion requires descriptor-relative no-follow operations")


def counts(value):
    return dict(input_tokens=value, cached_input_tokens=0, uncached_input_tokens=value,
                output_tokens=0, reasoning_tokens=0, total_tokens=value)


def measured(value):
    return {"tokens": counts(value), "native_tokens": counts(100 + value), "token_coverage": {},
            "sessions": [{"session": "root", "role": "orchestrator", "agent": "root", "status": "measured",
                          "native_usage": counts(100 + value), "usage": counts(value),
                          "measured_at": "2026-09-01T00:00:10+00:00"}]}


def state(workspace):
    return w.new_state(str(workspace.resolve()), "root", "old", {"criteria": ["A"], "paths": {p: [] for p in w.PHASES}})


def model(workspace, value=1, run="old", *, active=False):
    result = {"run": run, "tokens": counts(value), "workflow": {"finished": not active},
              "usage_measurement": {"attempted_at": "2026-09-01T00:01:00Z", "measured_at": "2026-09-01T00:00:10Z"},
              "snapshot": {"workspace": str(workspace.resolve()), "root": "root", "run": run,
                           "schema": "taskplane.dashboard-snapshot/v1", "revision": value,
                           "captured_at": "2026-09-01T00:01:00Z", "generated_at": "2026-09-01T00:01:00Z"}}
    result["snapshot"]["digest"] = primitives.content_fingerprint(result)
    return result


def publish(workspace, value=1, **kwargs):
    view = model(workspace, value, kwargs.pop("run", "old"), active=kwargs.pop("active", False))
    return snapshots.publish_pair(workspace, view, f"<html>{value}</html>", **kwargs)


def catalog(workspace):
    return json.loads((workspace / ".taskplane" / snapshots.CATALOG).read_text())


@pytest.mark.parametrize("exclusive", [False, True])
def test_snapshot_write_success_cleans_temporary_name(tmp_path, exclusive):
    target = tmp_path / "output.json"
    if not exclusive:
        target.write_bytes(b"old")
    with snapshots._directory(tmp_path.resolve()) as fd:
        snapshots._write(fd, target.name, b"new", exclusive=exclusive)
    assert target.read_bytes() == b"new"
    assert sorted(p.name for p in tmp_path.iterdir()) == [target.name]


def test_snapshot_exclusive_collision_preserves_output_and_cleans_temporary(tmp_path):
    target = tmp_path / "output.json"
    target.write_bytes(b"original")
    with snapshots._directory(tmp_path.resolve()) as fd:
        with pytest.raises(FileExistsError):
            snapshots._write(fd, target.name, b"replacement", exclusive=True)
    assert target.read_bytes() == b"original"
    assert sorted(p.name for p in tmp_path.iterdir()) == [target.name]


def test_snapshot_replace_failure_preserves_error_and_cleans_temporary(tmp_path, monkeypatch):
    target = tmp_path / "output.json"
    target.write_bytes(b"original")

    def fail_replace(*args, **kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr(snapshots.os, "replace", fail_replace)
    with snapshots._directory(tmp_path.resolve()) as fd:
        with pytest.raises(OSError, match="replace failed"):
            snapshots._write(fd, target.name, b"replacement")
    assert target.read_bytes() == b"original"
    assert sorted(p.name for p in tmp_path.iterdir()) == [target.name]


def test_semantic_reuse_keeps_original_bytes_and_sample_time(tmp_path):
    first = model(tmp_path)
    a = snapshots.publish_pair(tmp_path, first, "first bytes", detached=True)
    original = a.read_bytes(), a.with_suffix(".json").read_bytes()
    retry = model(tmp_path)
    retry["snapshot"].update(captured_at="2026-09-01T00:03:00Z", generated_at="2026-09-01T00:03:00Z", digest="b" * 64)
    retry["usage_measurement"]["attempted_at"] = "2026-09-01T00:03:00Z"
    b = snapshots.publish_pair(tmp_path, retry, "later rendered attempt", detached=True)
    assert a == b and (b.read_bytes(), b.with_suffix(".json").read_bytes()) == original
    assert retry["snapshot"]["generated_at"] == first["snapshot"]["generated_at"]
    changed = model(tmp_path)
    changed["usage_measurement"]["measured_at"] = "2026-09-01T00:02:00Z"
    assert snapshots.content_key(changed) != snapshots.content_key(first)


def test_inventory_observation_does_not_recursively_defeat_dedup(tmp_path):
    first = model(tmp_path)
    first["workflow"]["storage"] = {"bytes": 5, "store": {"bytes": 20, "scanned_at": "first"}}
    other = deepcopy(first)
    other["workflow"]["storage"]["store"] = {"bytes": 300, "scanned_at": "second"}
    assert snapshots.content_key(first) == snapshots.content_key(other)
    other["workflow"]["storage"]["bytes"] = 6
    assert snapshots.content_key(first) != snapshots.content_key(other)


@requires_collection
def test_hundreds_of_transients_obey_store_wide_pair_and_byte_bounds(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "LEASE_SECONDS", 0)
    monkeypatch.setattr(snapshots, "MAX_PAIRS", 8)
    monkeypatch.setattr(snapshots, "MAX_BYTES", 9000)
    for index in range(100):
        publish(tmp_path, index, run=f"run-{index % 3}", detached=True)
    pairs = catalog(tmp_path)["pairs"]
    assert len(pairs) <= 8
    assert sum(f["bytes"] for pair in pairs.values() for f in pair["files"].values()) <= 9000
    assert len(list((tmp_path / ".taskplane").glob("snapshot-*.html"))) == len(pairs)


@requires_collection
def test_pinned_selected_legacy_and_leased_pairs_survive(tmp_path):
    pinned = publish(tmp_path, 1, detached=True)
    snapshots.pin(tmp_path, pinned, "presentation:checkpoint")
    selected = publish(tmp_path, 2, select=True)
    selection = json.loads(selected.with_suffix(".selection.json").read_text())
    leased = publish(tmp_path, 3, detached=True)
    legacy = tmp_path / ".taskplane" / ("snapshot-" + "a" * 16 + "-" + "b" * 64)
    legacy.with_suffix(".html").write_text("historical")
    legacy.with_suffix(".json").write_text("{}")
    result = snapshots.collect(tmp_path, max_pairs=0, max_bytes=0)
    assert result["status"] == "complete"
    assert pinned.exists() and Path(selection["snapshot"]).exists() and leased.exists()
    snapshots.collect(tmp_path, max_pairs=0, max_bytes=0, now=time.time() + 1000)
    assert pinned.exists() and Path(selection["snapshot"]).exists() and not leased.exists()
    assert legacy.with_suffix(".html").read_text() == "historical"


@requires_collection
def test_context_reference_becomes_a_permanent_pin(tmp_path):
    target = publish(tmp_path, detached=True)
    context = tmp_path / ".taskplane" / "context-v1" / "objects"
    context.mkdir(parents=True)
    ref = context / "evidence.json"
    ref.write_text(json.dumps({"artifact": str(target)}))
    assert snapshots.collect(tmp_path, max_pairs=0, max_bytes=0, now=time.time() + 1000)["status"] == "complete"
    ref.unlink()
    snapshots.collect(tmp_path, max_pairs=0, max_bytes=0, now=time.time() + 1000)
    assert target.exists() and "durable-reference" in catalog(tmp_path)["pairs"][target.stem]["pins"]


def test_missing_reference_and_corrupt_reference_skip_collection(tmp_path):
    target = publish(tmp_path, detached=True)
    ref = tmp_path / ".taskplane" / "report.json"
    ref.write_text(json.dumps({"snapshot": "snapshot-" + "c" * 16 + "-" + "d" * 64 + ".html"}))
    result = snapshots.collect(tmp_path, max_pairs=0, max_bytes=0, now=time.time() + 1000)
    assert result["status"] == "skipped" and target.exists()
    ref.write_text("{broken")
    result = snapshots.collect(tmp_path, max_pairs=0, max_bytes=0, now=time.time() + 1000)
    assert result["status"] == "skipped" and target.exists()


def test_corrupt_catalog_and_failed_catalog_commit_never_delete(tmp_path, monkeypatch):
    target = publish(tmp_path, detached=True)
    original = (tmp_path / ".taskplane" / snapshots.CATALOG).read_bytes()
    (tmp_path / ".taskplane" / snapshots.CATALOG).write_text("{}")
    assert snapshots.collect(tmp_path, max_pairs=0, now=time.time() + 1000)["status"] == "skipped"
    assert target.exists()
    (tmp_path / ".taskplane" / snapshots.CATALOG).write_bytes(original)
    monkeypatch.setattr(snapshots, "_save", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    assert snapshots.collect(tmp_path, max_pairs=0, now=time.time() + 1000)["status"] == "skipped"
    assert target.exists()


@requires_collection
def test_partial_deletion_intent_recovers_only_owned_garbage(tmp_path, monkeypatch):
    target = publish(tmp_path, detached=True)
    original = snapshots.os.unlink
    def fail_second(name, *args, **kwargs):
        if name == target.with_suffix(".json").name:
            raise OSError("simulated crash between pair members")
        return original(name, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(snapshots.os, "unlink", fail_second)
        snapshots.collect(tmp_path, max_pairs=0, max_bytes=0, now=time.time() + 1000)
    assert catalog(tmp_path)["pairs"][target.stem]["deleting"]
    assert not target.exists() and target.with_suffix(".json").exists()
    snapshots.collect(tmp_path, max_pairs=0, max_bytes=0, now=time.time() + 1000)
    assert not target.with_suffix(".json").exists() and target.stem not in catalog(tmp_path)["pairs"]


def test_linked_pair_and_linked_directory_are_never_followed(tmp_path):
    target = publish(tmp_path, detached=True)
    external = tmp_path / "outside.html"
    external.write_text("private bytes")
    target.unlink()
    target.symlink_to(external)
    result = snapshots.collect(tmp_path, max_pairs=0, max_bytes=0, now=time.time() + 1000)
    assert result["status"] == "skipped" and external.read_text() == "private bytes"
    assert snapshots.inventory(tmp_path)["status"] == "partial"
    with pytest.raises(ValueError, match="verify"):
        snapshots.pin(tmp_path, target, "decision")


def test_unknown_inventory_and_reference_fingerprint_change_are_conservative(tmp_path, monkeypatch):
    target = publish(tmp_path, detached=True)
    scan = snapshots._scan
    calls = 0
    def changed(*args, **kwargs):
        nonlocal calls
        value = scan(*args, **kwargs)
        calls += 1
        value["fingerprint"] = str(calls)
        return value
    monkeypatch.setattr(snapshots, "_scan", changed)
    assert snapshots.collect(tmp_path, max_pairs=0, now=time.time() + 1000)["status"] == "skipped"
    assert target.exists()


def test_detached_same_run_never_replaces_selection(tmp_path):
    selected = publish(tmp_path, 1, select=True)
    before = selected.read_bytes(), selected.with_suffix(".selection.json").read_bytes()
    detached = publish(tmp_path, 2, detached=True, pin_reason="finish:original")
    assert detached != selected and detached.exists()
    assert (selected.read_bytes(), selected.with_suffix(".selection.json").read_bytes()) == before


def test_selection_order_and_latest_detached_active_view(tmp_path):
    selected = publish(tmp_path, 3, select=True)
    def older(prior, incoming):
        return prior["revision"] > incoming["revision"]
    result = publish(tmp_path, 2, older=older)
    assert result != selected and selected.read_text() == "<html>3</html>"
    first = publish(tmp_path, 1, run="active", active=True, detached=True)
    last = publish(tmp_path, 2, run="active", active=True, detached=True)
    snapshots.collect(tmp_path, max_pairs=0, max_bytes=0, now=time.time() + 1000)
    assert first.exists() is (not snapshots.supports_collection())
    assert last.exists()


def test_legacy_publication_reuses_and_pins_but_cannot_collect(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "supports_collection", lambda: False)
    first = publish(tmp_path, 1, detached=True)
    original = first.read_bytes(), first.with_suffix(".json").read_bytes()
    assert publish(tmp_path, 1, detached=True) == first
    assert snapshots.pin(tmp_path, first, "presentation")["status"] == "pinned"
    selected = publish(tmp_path, 2, select=True)
    assert selected.name == "dashboard.html" and selected.read_text() == "<html>2</html>"
    assert publish(tmp_path, 3, run="another").name != "dashboard.html"
    assert selected.read_text() == "<html>2</html>"
    result = snapshots.collect(tmp_path, max_pairs=0, max_bytes=0, now=time.time() + 1000)
    assert result["status"] == "skipped" and result["removed_pairs"] == 0
    assert "no-follow" in result["reason"]
    assert (first.read_bytes(), first.with_suffix(".json").read_bytes()) == original
    assert snapshots.inventory(tmp_path)["complete"]


@pytest.mark.parametrize("linked", ["store", "pair", "lock"])
def test_legacy_publication_rejects_links(tmp_path, monkeypatch, linked):
    monkeypatch.setattr(snapshots, "supports_collection", lambda: False)
    target = publish(tmp_path, detached=True)
    store = tmp_path / ".taskplane"
    original = {p.name: p.read_bytes() for p in store.iterdir()}
    if linked == "store":
        outside = tmp_path / "outside"
        store.rename(outside)
        store.symlink_to(outside, target_is_directory=True)
    else:
        target = target if linked == "pair" else store / (snapshots.CATALOG + ".lock")
        outside = tmp_path / "outside"
        target.rename(outside)
        target.symlink_to(outside)
    with pytest.raises(ValueError, match="ordinary"):
        publish(tmp_path, detached=True)
    actual = outside if linked == "store" else store
    assert {p.name: p.read_bytes() for p in actual.iterdir()} == original


def test_inventory_is_read_only_bounded_and_counts_nested_legacy_and_hardlinks(tmp_path):
    assert snapshots.inventory(tmp_path)["bytes"] == 0
    assert not (tmp_path / ".taskplane").exists()
    store = tmp_path / ".taskplane"
    (store / "nested" / ".taskplane").mkdir(parents=True)
    nested = store / "nested" / ".taskplane" / "evidence.txt"
    nested.write_bytes(b"abc")
    snapshots.os.link(nested, store / "same-bytes.txt")
    (store / "journal.jsonl").write_text('{}\n')
    inventory = snapshots.inventory(tmp_path)
    assert inventory["bytes"] == 9 and inventory["files"] == 3 and inventory["complete"]
    assert inventory["categories"]["journals"]["bytes"] == 3
    limited = snapshots.inventory(tmp_path, max_entries=1)
    assert not limited["complete"] and limited["lower_bound"] and limited["bytes"] <= 9


def test_controller_capacity_is_separate_from_whole_store(tmp_path):
    root = tmp_path / ".taskplane"
    root.mkdir()
    (root / "evidence.txt").write_bytes(b"x" * 100)
    db = {"runs": {}, "archives": {}}
    result = workflow_retention.capacity(db, 1024, tmp_path)
    assert result["limit_bytes"] == 1024 and result["bytes"] < 100
    assert result["store"]["bytes"] == 100


@pytest.mark.parametrize("relative", [".taskplane", ".taskplane/nested", "project/.taskplane/deep"])
def test_reserved_root_refuses_without_creating_state(tmp_path, relative):
    selected = tmp_path / relative
    selected.mkdir(parents=True)
    with pytest.raises(w.Refusal, match="containing project"):
        workspace_binding.validate_workspace_root(selected)
    assert not (selected / ".taskplane").exists()


def test_resolved_reserved_alias_refuses_but_similarly_named_project_survives(tmp_path):
    nested = tmp_path / ".taskplane" / "legacy"
    nested.mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(nested, target_is_directory=True)
    with pytest.raises(w.Refusal, match="reserved"):
        workspace_binding.validate_workspace_root(alias)
    valid = tmp_path / ".taskplane-cache"
    valid.mkdir()
    assert workspace_binding.validate_workspace_root(valid) == valid
    assert workspace_binding.resolve_workspace(tmp_path, event={"cwd": str(nested)}) == tmp_path


@pytest.mark.parametrize("host", ["codex", "claude"])
def test_explicit_shared_root_counter_closes_old_usage_exactly(monkeypatch, host):
    run = {"run": "old", "session": "root", "host": host, "at": "2026-09-01T00:00:00Z",
           "usage": counts(100), "usage_status": "observed"}
    closing = {"session": "root", "host": host, "at": "2026-09-01T00:01:00Z",
               "usage": counts(180), "usage_status": "observed"}
    unavailable = [{"session": "root", "role": "orchestrator", "agent": "root", "usage": None,
                    "native_usage": None, "status": "unavailable"}]
    calls = []
    def codex_read(_run, cutoff, diagnostics):
        calls.append(cutoff)
        return deepcopy(unavailable), 0
    def claude_read(_run, _events, cutoff):
        calls.append(cutoff)
        return deepcopy(unavailable), 0
    monkeypatch.setattr(flow_usage, "_codex_sessions", codex_read)
    monkeypatch.setattr(flow_usage.claude, "sessions", claude_read)
    result = flow_usage.reconcile(run, [], closing=closing)
    assert result["tokens"]["total_tokens"] == 80
    assert result["sessions"][0]["measured_at"] == closing["at"]
    assert calls == [datetime.fromisoformat(closing["at"].replace("Z", "+00:00")).timestamp()]
    saved = {"kind": "usage", "run": "old", "measurement": measured(20)}
    closing["usage_status"] = "partial"
    assert flow_usage.reconcile(run, [saved], closing=closing)["tokens"] is None
    closing.update(usage_status="observed", usage=counts(90))
    assert flow_usage.reconcile(run, [saved], closing=closing)["tokens"] is None
    closing["session"] = "foreign"
    with pytest.raises(ValueError, match="root"):
        flow_usage.reconcile(run, [], closing=closing)


def test_finish_original_sample_frozen_retry_and_follow_up_conserve(tmp_path):
    before = state(tmp_path)
    initial = telemetry._point(before, measured(0), "2026-09-01T00:00:00Z", None)
    after = deepcopy(before)
    after.update(finished=True, revision=1)
    closure = telemetry.commit_boundary(tmp_path, before, after, "finish", measured(10), "2026-09-01T00:00:10Z")
    assert telemetry.commit_boundary(tmp_path, before, after, "finish", measured(99),
                                     "2026-09-01T00:03:00Z") == closure
    view = telemetry.frozen_model({**model(tmp_path), "workflow": after}, closure, [initial])
    assert view["tokens"]["total_tokens"] == 10 and view["final_observation"]["label"] == "At finish"
    view["snapshot"]["digest"] = primitives.content_fingerprint(view)
    frozen = snapshots.publish_pair(tmp_path, view, "At finish", detached=True, pin_reason="finish:" + closure["id"])
    original = frozen.read_bytes(), frozen.with_suffix(".json").read_bytes()
    endpoint = telemetry._point(after, measured(25), "2026-09-01T00:00:25Z", 1)
    live = flow_usage.phase_accounting([initial, closure["point"]], endpoint, after, measured(25))
    assert live["non_phase"]["follow_up"]["tokens"]["total_tokens"] == 15
    assert live["accounting"]["run"]["total_tokens"] == sum(
        live["accounting"][key].get("total_tokens", 0) for key in ("phase", "non_phase", "unresolved"))
    publish(tmp_path, 25)
    assert (frozen.read_bytes(), frozen.with_suffix(".json").read_bytes()) == original
    assert telemetry.read_boundaries(tmp_path, "old", "root") == [closure]


def test_refused_transition_never_creates_closure(tmp_path):
    before = state(tmp_path)
    with pytest.raises(ValueError, match="committed"):
        telemetry.commit_boundary(tmp_path, before, before, "finish", measured(10), "2026-09-01T00:00:10Z")
    assert not (tmp_path / ".taskplane").exists()


def test_completed_run_can_close_at_next_start_without_new_phase_revision(tmp_path):
    complete = state(tmp_path)
    complete.update(finished=True, revision=8)
    next_state = {**state(tmp_path), "run": "next", "started_at": "2026-09-01T01:00:00Z"}
    closed = telemetry.commit_boundary(tmp_path, complete, complete, "interval_closed", measured(80),
                                       "2026-09-01T01:00:00Z", cutoff=next_state["started_at"],
                                       closing_counter={"usage": counts(180)}, next_state=next_state)
    assert closed["revision"] == closed["previous_revision"] == 8
    assert closed["next_run"] == "next" and closed["label"] == "Interval closed"


def test_missing_child_and_missing_sample_are_unknown_not_zero(tmp_path):
    before = state(tmp_path)
    after = deepcopy(before)
    after.update(finished=True, revision=1)
    missing = {"sessions": [], "tokens": None, "token_coverage": {"discovery_errors": 1}}
    closure = telemetry.commit_boundary(tmp_path, before, after, "finish", missing, "2026-09-01T00:00:10Z")
    final = telemetry.frozen_model(model(tmp_path), closure)
    assert final["usage_measurement"]["status"] == "unavailable" and final["usage_measurement"]["measured_at"] is None
    partial = measured(10)
    partial["sessions"].append({"session": "child", "role": "lens", "status": "unavailable", "usage": None})
    point = telemetry._point(before, measured(0), "2026-09-01T00:00:00Z", None)
    result = flow_usage.phase_accounting([point], telemetry._point(before, partial, "2026-09-01T00:00:10Z", 0), before, partial)
    assert result["status"] == "partial" and result["accounting"]["reconciled"]
    assert any("incomplete session coverage" in gap for gap in result["gaps"])


@pytest.mark.parametrize("transition", ["finish", "replacement", "interval_closed"])
@pytest.mark.parametrize("coverage", ["unavailable", "partial"])
def test_frozen_snapshot_metadata_survives_later_fresh_reports_and_recovery(tmp_path, transition, coverage):
    cutoff = "2026-09-01T00:00:10Z"
    oldest, newest = "2026-09-01T00:00:04Z", "2026-09-01T00:00:07Z"
    sample = {"sessions": [], "tokens": None, "token_coverage": {"discovery_errors": 1}}
    if coverage == "partial":
        sample = measured(10)
        sample["sessions"][0]["measured_at"] = oldest
        sample["sessions"].extend([
            {"session": "child", "agent": "child", "role": "lens", "status": "measured",
             "usage": counts(5), "native_usage": counts(105), "measured_at": newest},
            {"session": "missing", "agent": "missing", "role": "lens", "status": "unavailable",
             "usage": None, "native_usage": None},
        ])
        sample.update(tokens=counts(15), native_tokens=counts(215),
                      token_coverage={"measured_sessions": 2, "unmeasured_sessions": 1})
    before = state(tmp_path)
    after = deepcopy(before)
    after.update(finished=True, revision=1)
    kwargs = {}
    if transition == "replacement":
        after.update(finished=False, superseded_by="next")
    elif transition == "interval_closed":
        before = deepcopy(after)
        kwargs = {"next_state": {**state(tmp_path), "run": "next", "started_at": cutoff}}
    closure = telemetry.commit_boundary(tmp_path, before, after, transition, sample, cutoff,
                                        cutoff=cutoff, **kwargs)
    closure_path = tmp_path / ".taskplane" / ("usage-closure-" + closure["id"] + ".json")
    original_closure = closure_path.read_bytes()
    expected_clock = {"status": coverage, "measured_at": None, "attempted_at": cutoff,
                      "oldest_at": oldest if coverage == "partial" else None,
                      "newest_at": newest if coverage == "partial" else None}
    original_artifact = None
    original_pair = None
    for later_at in ("2026-09-01T00:05:00Z", "2026-09-01T00:06:00Z"):
        later = {**model(tmp_path), **measured(99), "status": "finished", "phase": "retro",
                 "tasks": [], "reviews": [], "artifacts": {}}
        later["sessions"][0]["measured_at"] = later_at
        later["usage_measurement"] = {"status": "fresh", "measured_at": later_at, "attempted_at": later_at,
                                      "oldest_at": later_at, "newest_at": later_at}
        later["snapshot"].update(measurement_status="fresh", measurement_at=later_at,
                                 measurement_attempted_at=later_at, measurement_oldest_at=later_at,
                                 measurement_newest_at=later_at)
        incoming = deepcopy(later)
        # A retry may see healthy counters; recovery must still use the durable first sample.
        assert telemetry.commit_boundary(tmp_path, before, after, transition, later, later_at,
                                         cutoff=cutoff, **kwargs) == closure
        recovered, = telemetry.read_boundaries(tmp_path, "old", "root")
        frozen = telemetry.frozen_model(later, recovered)
        assert frozen["usage_measurement"] == expected_clock
        assert frozen["tokens"] == sample["tokens"]
        for snapshot_key, clock_key in (("measurement_status", "status"), ("measurement_at", "measured_at"),
                                        ("measurement_attempted_at", "attempted_at"),
                                        ("measurement_oldest_at", "oldest_at"), ("measurement_newest_at", "newest_at")):
            assert frozen["snapshot"][snapshot_key] == expected_clock[clock_key]
        frozen["snapshot"]["digest"] = primitives.content_fingerprint(frozen)
        html = flow_dashboard.render(str(tmp_path), frozen)
        for label, value in (("Counter coverage", coverage), ("Counter measurement", "Unknown"),
                             ("Oldest included measurement", expected_clock["oldest_at"] or "Unknown"),
                             ("Newest included measurement", expected_clock["newest_at"] or "Unknown"),
                             ("Last read attempt", cutoff)):
            assert label + ': </span><code>' + value + '</code>' in html
        assert "Coverage: " + coverage in html
        assert later_at not in html
        artifact = snapshots.publish_pair(tmp_path, frozen, html, detached=True,
                                          pin_reason="closure/" + closure["id"])
        pair = artifact.read_bytes(), artifact.with_suffix(".json").read_bytes()
        if original_artifact is None:
            original_artifact, original_pair = artifact, pair
        else:
            assert artifact == original_artifact and pair == original_pair
        assert closure_path.read_bytes() == original_closure
        assert later == incoming


def test_exact_cutoff_native_cumulative_and_owned_replay_differ(tmp_path):
    path = tmp_path / "native.jsonl"
    rows = [{"type": "session_meta", "timestamp": "2026-09-01T00:00:00Z",
             "payload": {"id": "child", "timestamp": "2026-09-01T00:00:00Z", "thread_source": "agent_created_thread"}}]
    for at, value in [(5, 10), (10, 30)]:
        rows.append({"type": "token_usage_record", "timestamp": f"2026-09-01T00:00:{at:02d}Z", "ordinal": at,
                     "payload": {"thread_id": "child", "thread_token_usage": counts(value)}})
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    cutoff = datetime.fromisoformat("2026-09-01T00:00:10+00:00").timestamp()
    cumulative = meter.read_logical_snapshot([path], "child", at_or_before=cutoff)
    interval = meter.read_owned_interval([path], "child", start=cutoff - 10, end=cutoff)
    assert cumulative["usage"]["total_tokens"] == 30
    assert interval["usage"]["total_tokens"] == 10
    assert cumulative["measured_at"] == "2026-09-01T00:00:10Z"


def test_rendered_telemetry_names_storage_and_escapes_observations():
    html = dashboard.telemetry_sections({"final_observation": {"label": "At finish", "observed_at": "time", "cutoff": "cutoff"},
        "post_completion_observations": [{"at": "later", "kind": "result", "note": "<script>bad</script>"}],
        "workflow": {"storage": {"store": {"bytes": 10, "files": 2, "complete": False,
            "snapshots": {"status": "catalogued", "pinned_bytes": 2, "transient_bytes": 3,
                          "protected_bytes": 4, "legacy_bytes": 5}}}}})
    assert "At finish" in html and "Post-completion observations" in html
    assert "Whole-store storage" in html and "Partial inventory; observed lower bound" in html
    assert "controller JSON capacity" in html and "Preserved legacy snapshots" in html
    assert "<script>" not in html and "&lt;script&gt;" in html


def frozen_snapshot_index(workspace, transition="finish"):
    before = state(workspace)
    after = deepcopy(before)
    after.update(finished=True, revision=1)
    kwargs = {}
    if transition == "replacement":
        after.update(finished=False, superseded_by="next")
    elif transition == "interval_closed":
        before = deepcopy(after)
        kwargs = {"cutoff": "2026-09-01T01:00:00Z",
                  "next_state": {**state(workspace), "run": "next", "started_at": "2026-09-01T01:00:00Z"}}
    closure = telemetry.commit_boundary(workspace, before, after, transition, measured(10),
                                        "2026-09-01T00:00:10Z", **kwargs)
    frozen = telemetry.frozen_model(model(workspace), closure)
    frozen["snapshot"]["digest"] = primitives.content_fingerprint(frozen)
    artifact = snapshots.publish_pair(workspace, frozen, "<html>Original frozen observation</html>",
                                      detached=True, pin_reason="closure/" + closure["id"])
    current = model(workspace, 25)
    current["final_snapshots"] = [{"kind": "final_snapshot", "run": "old", "session": "root",
        "artifact": str(artifact), "closure_id": closure["id"],
        "label": "<script>journal label</script>", "cutoff": "<script>journal cutoff</script>"}]
    return current, artifact, closure


@pytest.mark.parametrize("transition", ["finish", "replacement", "interval_closed"])
def test_frozen_snapshot_links_bind_original_artifact_and_survive_collection(tmp_path, transition):
    workspace = tmp_path / 'workspace with spaces & "quotes"'
    workspace.mkdir()
    current, artifact, closure = frozen_snapshot_index(workspace, transition)
    before = artifact.read_bytes(), artifact.with_suffix(".json").read_bytes()
    catalog_before = (workspace / ".taskplane" / snapshots.CATALOG).read_bytes()
    html = dashboard.telemetry_sections(current)
    assert 'href="' + artifact.as_uri() + '"' in html
    assert closure["label"] in html and closure["cutoff"] in html
    assert "journal label" not in html and "journal cutoff" not in html
    assert (workspace / ".taskplane" / snapshots.CATALOG).read_bytes() == catalog_before
    publish(workspace, 99)
    snapshots.collect(workspace, max_pairs=0, max_bytes=0, now=time.time() + 1000)
    assert artifact.as_uri() in dashboard.telemetry_sections(current)
    assert (artifact.read_bytes(), artifact.with_suffix(".json").read_bytes()) == before


@pytest.mark.parametrize("invalid", ["javascript", "remote", "foreign_workspace", "traversal",
    "relative", "missing", "symlink", "html_changed", "json_changed", "row_run", "row_root",
    "row_closure", "context_run", "context_root", "context_workspace", "context_snapshot_run",
    "context_schema", "catalog_changed"])
def test_frozen_snapshot_links_reject_unverified_or_unsafe_targets(tmp_path, invalid):
    current, artifact, _ = frozen_snapshot_index(tmp_path)
    row = current["final_snapshots"][0]
    if invalid == "javascript":
        row["artifact"] = 'javascript:alert("bad")'
    elif invalid == "remote":
        row["artifact"] = "https://example.invalid/" + artifact.name
    elif invalid == "foreign_workspace":
        row["artifact"] = str(tmp_path.parent / artifact.name)
    elif invalid == "traversal":
        row["artifact"] = str(artifact.parent / ".." / ".taskplane" / artifact.name)
    elif invalid == "relative":
        row["artifact"] = artifact.name
    elif invalid == "missing":
        artifact.unlink()
    elif invalid == "symlink":
        saved = tmp_path / "saved.html"
        artifact.rename(saved)
        artifact.symlink_to(saved)
    elif invalid == "html_changed":
        artifact.write_text("changed")
    elif invalid == "json_changed":
        artifact.with_suffix(".json").write_text("{}")
    elif invalid == "row_run":
        row["run"] = "foreign"
    elif invalid == "row_root":
        row["session"] = "foreign"
    elif invalid == "row_closure":
        row["closure_id"] = "foreign"
    elif invalid == "context_run":
        current["run"] = "foreign"
    elif invalid == "context_root":
        current["snapshot"]["root"] = "foreign"
    elif invalid == "context_workspace":
        current["snapshot"]["workspace"] = str(tmp_path.parent)
    elif invalid == "context_snapshot_run":
        current["snapshot"]["run"] = "foreign"
    elif invalid == "context_schema":
        current["snapshot"]["schema"] = "foreign"
    elif invalid == "catalog_changed":
        (tmp_path / ".taskplane" / snapshots.CATALOG).write_text("{}")
    html = dashboard.telemetry_sections(current)
    assert "href=" not in html
    assert "Snapshot artifacts unavailable or unverified" in html


def test_frozen_snapshot_index_is_bounded_and_deduplicated(tmp_path):
    current, artifact, _ = frozen_snapshot_index(tmp_path)
    row = current["final_snapshots"][0]
    current["final_snapshots"] = [row] * 40
    assert dashboard.telemetry_sections(current).count(artifact.as_uri()) == 1
    current["final_snapshots"] = [row] + [{}] * 32
    assert artifact.as_uri() not in dashboard.telemetry_sections(current)


def test_frozen_snapshot_cutoff_is_html_escaped(tmp_path):
    current, artifact, _ = frozen_snapshot_index(tmp_path)
    frozen = json.loads(artifact.with_suffix(".json").read_text())
    frozen["final_observation"]["cutoff"] = '<script>bad</script> & "quoted"'
    frozen["snapshot"].pop("digest")
    frozen["snapshot"]["digest"] = primitives.content_fingerprint(frozen)
    escaped = snapshots.publish_pair(tmp_path, frozen, "<html>Escaping fixture</html>",
                                     detached=True, pin_reason="fixture")
    current["final_snapshots"][0]["artifact"] = str(escaped)
    html = dashboard.telemetry_sections(current)
    assert "<script>" not in html and "&lt;script&gt;bad&lt;/script&gt; &amp;" in html
