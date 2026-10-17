"""History retention and retirement never create checkpoint acceptance."""
from copy import deepcopy
import json
import pytest
from taskplane import workflow as w, workflow_host as h, workflow_retention as retention
from taskplane.tests.test_workflow_evidence import prepare


def controller(tmp_path):
    state, _, _ = prepare(tmp_path)
    c = h.Controller(tmp_path, 'root', h.installed_adapter('codex'))
    s = c.start({'scope': state['scope'], 'request_reference': 'actual-fixture-request'})
    return c, s


def test_retire_preserves_unaccepted_output_and_rejects_stale_revision(tmp_path):
    c, s = controller(tmp_path)
    note = json.dumps({'request_reference': 'fixture-user-retire', 'reason': 'obsolete run'})
    with pytest.raises(w.Refusal, match='revision'):
        c.apply('retire', s['run'], expected_revision=99, native_reference=note)
    with pytest.raises(w.Refusal, match='reference'):
        c.apply('retire', s['run'], expected_revision=0, native_reference='{}')
    retired = c.apply('retire', s['run'], expected_revision=0, native_reference=note)
    assert retired['decisions'] == s['decisions'] == {}
    assert not retired['finished'] and retired['visits'] == s['visits']
    assert c.report(s['run'])['status'] == 'retired'
    assert c.report()['status'] == 'no_workflow'
    with pytest.raises(w.Refusal, match='retired'):
        c.guard({'tool_name': 'exec_command', 'tool_input': {'cmd': 'touch app.py'}}, s['run'])


def test_retention_roundtrip_tamper_and_failed_index_write(tmp_path, monkeypatch):
    c, first = controller(tmp_path)
    monkeypatch.setattr(retention, 'WATERMARK', 1)
    second = c.start({'scope': first['scope'], 'request_reference': 'new-user-request',
                      'replace_run': first['run'], 'expected_revision': 0})
    db = c._read(c._path())
    assert list(db['runs']) == [second['run']]
    assert first['run'] in db['archives']
    old = c.report(first['run'])
    assert old['archived'] and old['superseded_by'] == second['run']
    assert old['decisions'] == first['decisions'] and not old['finished']
    assert c.start({'scope': first['scope'], 'request_reference': 'new-user-request',
                    'replace_run': first['run'], 'expected_revision': 0})['run'] == second['run']
    ref = db['archives'][first['run']]
    from taskplane.context import Store
    Store(tmp_path).path(ref['sha256']).write_text('{}')
    with pytest.raises(w.Refusal, match='digest'):
        c.report(first['run'])
    # Archive corruption never promotes history to active grants.
    assert c.report()['run'] == second['run']


def test_archive_failure_keeps_original_index(tmp_path, monkeypatch):
    c, s = controller(tmp_path)
    before = c._path().read_bytes()
    monkeypatch.setattr(retention, 'WATERMARK', 1)
    from taskplane.context import Store
    monkeypatch.setattr(Store, 'put', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(w.Refusal, match='disk full'):
        c.start({'scope': s['scope'], 'request_reference': 'next', 'replace_run': s['run'], 'expected_revision': 0})
    assert c._path().read_bytes() == before


def test_active_and_protected_history_never_auto_archive(tmp_path, monkeypatch):
    c, s = controller(tmp_path)
    db = c._read(c._path())
    monkeypatch.setattr(retention, 'WATERMARK', 1)
    assert retention.compact(tmp_path, db) == db
    protected = deepcopy(db); protected['profile'] = 'protected_host'
    protected['active'] = None; protected['runs'][s['run']]['superseded_by'] = 'new'
    assert retention.compact(tmp_path, protected) == protected


# Snapshot tests exercise the real catalogue/scan/publication boundary. All
# stores live beneath pytest's isolated tmp_path, never a user's host store.
from taskplane import snapshot_retention as snapshots
import hashlib
import time
from types import SimpleNamespace


def _snapshot_model(workspace, label):
    digest = hashlib.sha256(label.encode()).hexdigest()
    return {"run": "retention-fixture", "workflow": {"finished": True}, "label": label,
        "snapshot": {"workspace": str(workspace.resolve()), "root": "fixture-root",
                     "run": "retention-fixture", "digest": digest}}


def _publish(workspace, label, **kwargs):
    return snapshots.publish_pair(workspace, _snapshot_model(workspace, label),
                                  "<html>" + label + "</html>", detached=True, **kwargs)


def _identities(paths):
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _assert_collection_safety(result):
    scans = result.get("reference_scans", [])
    if result.get("removed_pairs", 0):
        assert len(scans) == 2 and all(scan["complete"] for scan in scans)
        assert scans[0]["reference_digest"] == scans[1]["reference_digest"]
        assert not result["changed_references"]


def test_snapshot_stable_store_collects_only_owned_pair(tmp_path):
    pair = _publish(tmp_path, "collectible")
    root = tmp_path / ".taskplane"
    unrelated = root / "unrelated.bin"
    unrelated.write_bytes(b"unmanaged")
    result = snapshots.collect(tmp_path, max_pairs=0, now=time.time() + 120)
    if snapshots.supports_collection():
        assert result["status"] == "complete"
        assert result["removed_pairs"] == 1 and not pair.exists()
    else:
        assert result["removed_pairs"] == 0 and pair.exists()
    assert unrelated.read_bytes() == b"unmanaged"
    _assert_collection_safety(result)


def test_snapshot_exact_entry_cap_and_overflow(tmp_path):
    root = tmp_path / ".taskplane"
    root.mkdir()
    for index in range(3):
        (root / f"entry-{index}.bin").touch()
    first = snapshots._scan(root, references=True, max_entries=3)
    assert first["entries"] == 3 and first["complete"]
    (root / "overflow.bin").touch()
    second = snapshots._scan(root, references=True, max_entries=3)
    assert not second["complete"] and second["entries"] == 3
    assert "entry_limit" in second["errors"]


def test_snapshot_depth_cap_and_exhaustion(tmp_path):
    pair = _publish(tmp_path, "depth-protected-by-incomplete-scan")
    root = tmp_path / ".taskplane"
    nested = root
    for _ in range(32):
        nested = nested / "d"
        nested.mkdir()
    (nested / "reference.txt").write_text("ordinary reference")
    exact = snapshots._scan(root, references=True)
    assert exact["max_depth_seen"] == 32 and "depth_limit" not in exact["errors"]
    (nested / "too-deep").mkdir()
    result = snapshots.collect(tmp_path, max_pairs=0, now=time.time() + 120)
    assert result["removed_pairs"] == 0 and pair.exists()
    assert any("depth_limit" in scan["errors"] for scan in result["reference_scans"])
    _assert_collection_safety(result)


def test_snapshot_time_budget_counts_completed_read(tmp_path, monkeypatch):
    pair = _publish(tmp_path, "timed")
    root = tmp_path / ".taskplane"
    (root / "evidence.txt").write_text("durable text")
    tick = [0.0]
    original_read = snapshots._read
    def delayed_read(*args, **kwargs):
        raw = original_read(*args, **kwargs)
        if kwargs.get("read_observer"):
            tick[0] += 1.0
        return raw
    monkeypatch.setattr(snapshots, "time", SimpleNamespace(monotonic=lambda: tick[0], time=time.time))
    monkeypatch.setattr(snapshots, "_read", delayed_read)
    result = snapshots.collect(tmp_path, max_pairs=0, now=time.time() + 120)
    assert result["removed_pairs"] == 0 and pair.exists()
    assert all(not scan["complete"] and scan["reference_bytes_read"] > 0
               for scan in result["reference_scans"])
    _assert_collection_safety(result)


def test_snapshot_reference_byte_cap_and_exhaustion(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "MAX_REFERENCE_BYTES", 1024)
    root = tmp_path / ".taskplane"
    root.mkdir()
    payload = root / "references.txt"
    payload.write_bytes(b"x" * snapshots.MAX_REFERENCE_BYTES)
    exact = snapshots._scan(root, references=True)
    assert exact["reference_bytes_read"] == snapshots.MAX_REFERENCE_BYTES
    assert "reference_byte_limit" not in exact["errors"]
    assert exact["complete"]
    # Publication under target is allowed despite incomplete reference capacity.
    pair = _publish(tmp_path, "byte-limited")
    with payload.open("ab") as stream:
        stream.write(b"x")
    result = snapshots.collect(tmp_path, max_pairs=0, now=time.time() + 120)
    assert result["removed_pairs"] == 0 and pair.exists()
    assert all(scan["reference_bytes_read"] <= snapshots.MAX_REFERENCE_BYTES
               for scan in result["reference_scans"])
    assert any("reference_byte_limit" in scan["errors"] for scan in result["reference_scans"])
    _assert_collection_safety(result)


def test_snapshot_changing_complete_scans_delete_nothing(tmp_path, monkeypatch):
    pair = _publish(tmp_path, "changing")
    root = tmp_path / ".taskplane"
    reference = root / "evidence.txt"
    reference.write_text("before")
    original_scan = snapshots._scan
    calls = []
    def changing_scan(*args, **kwargs):
        result = original_scan(*args, **kwargs)
        calls.append(result)
        if len(calls) == 1:
            reference.write_text("after " + pair.stem)
        return result
    monkeypatch.setattr(snapshots, "_scan", changing_scan)
    result = snapshots.collect(tmp_path, max_pairs=0, now=time.time() + 120)
    assert len(calls) == 2 and all(scan["complete"] for scan in calls)
    assert result["changed_references"] and result["removed_pairs"] == 0 and pair.exists()
    _assert_collection_safety(result)


@pytest.mark.parametrize("case", ["pins", "leases", "selected-live", "required-durable"])
def test_snapshot_protected_identities(tmp_path, case):
    if case == "selected-live":
        model = _snapshot_model(tmp_path, "selected")
        snapshots.publish_pair(tmp_path, model, "selected", select=True)
        live_model = _snapshot_model(tmp_path, "live")
        live_model["run"] = live_model["snapshot"]["run"] = "live-run"
        live_model["workflow"] = {"finished": False}
        snapshots.publish_pair(tmp_path, live_model, "live", detached=True)
    else:
        pair = _publish(tmp_path, case)
        if case == "pins":
            snapshots.pin(tmp_path, pair, "accepted checkpoint")
        elif case == "required-durable":
            (tmp_path / ".taskplane" / "checkpoint.json").write_text(json.dumps({"evidence": str(pair)}))
    root = tmp_path / ".taskplane"
    paths = [p for p in root.iterdir() if p.suffix in {".html", ".json"} and snapshots.PAIR.fullmatch(p.stem)]
    before = _identities(paths)
    result = snapshots.collect(tmp_path, max_pairs=0,
        now=time.time() if case == "leases" else time.time() + 120)
    after = _identities(paths)
    assert before == after and result["removed_pairs"] == 0
    assert result["status"] == ("complete" if snapshots.supports_collection() else "skipped")
    if case == "required-durable" and snapshots.supports_collection():
        catalog = json.loads((root / snapshots.CATALOG).read_text())
        assert "durable-reference" in catalog["pairs"][pair.stem]["pins"]
    _assert_collection_safety(result)


def test_snapshot_partial_scan_preserves_required_and_refuses_pressure(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "MAX_PAIRS", 2)
    root = tmp_path / ".taskplane"
    required = _publish(tmp_path, "required-first", pin_reason="checkpoint")
    # Every one of these optional pairs is still leased and cannot be reclaimed.
    for index in range(snapshots.MAX_PAIRS):
        _publish(tmp_path, f"optional-{index}")
    (root / "malformed-reference.json").write_text("{")
    protected = [required, required.with_suffix(".json")]
    before = _identities(protected)
    paths = [p for p in root.iterdir() if p.suffix in {".html", ".json"} and snapshots.PAIR.fullmatch(p.stem)]
    existing = _identities(paths)
    with pytest.raises(snapshots.SnapshotCapacityError) as caught:
        _publish(tmp_path, "refused")
    admission = caught.value.admission
    assert not admission["capacity_proved"] and not admission["optional_admitted"]
    assert admission["projected_pairs"] == snapshots.MAX_PAIRS + 1
    assert existing == _identities(paths)
    assert len(list(root.glob("snapshot-*.html"))) == snapshots.MAX_PAIRS + 1
    assert all(not scan["complete"] for scan in admission["collection"]["reference_scans"])
    _assert_collection_safety(admission["collection"])
    extra_required = _publish(tmp_path, "required-after-pressure", pin_reason="durable checkpoint")
    assert extra_required.exists() and before == _identities(protected)
    inventory = snapshots.inventory(tmp_path)
    assert inventory["snapshots"]["pinned_bytes"] > 0
    assert "required evidence may grow" in inventory["snapshots"]["capacity_scope"]


def test_snapshot_complete_pressure_reclaims_before_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "LEASE_SECONDS", 0)
    monkeypatch.setattr(snapshots, "MAX_PAIRS", 2)
    for index in range(snapshots.MAX_PAIRS):
        _publish(tmp_path, f"old-{index}")
    first = tmp_path / ".taskplane" / ("snapshot-" + hashlib.sha256(b"retention-fixture").hexdigest()[:16]
        + "-" + hashlib.sha256(b"old-0").hexdigest() + ".html")
    original_write = snapshots._write
    observed = []
    def checked_write(fd, name, raw, **kwargs):
        if kwargs.get("exclusive") and hashlib.sha256(b"next").hexdigest() in name:
            observed.append(first.exists())
        return original_write(fd, name, raw, **kwargs)
    monkeypatch.setattr(snapshots, "_write", checked_write)
    if not snapshots.supports_collection():
        with pytest.raises(snapshots.SnapshotCapacityError):
            _publish(tmp_path, "next")
        assert first.exists() and observed == []
        return
    result = _publish(tmp_path, "next")
    assert result.exists() and observed == [False, False]
    assert len(list((tmp_path / ".taskplane").glob("snapshot-*.html"))) == snapshots.MAX_PAIRS


def test_snapshot_byte_pressure_and_unsupported_collection(tmp_path, monkeypatch):
    pair = _publish(tmp_path, "existing", pin_reason="required")
    before = _identities([pair, pair.with_suffix(".json")])
    monkeypatch.setattr(snapshots, "MAX_BYTES", 1)
    monkeypatch.setattr(snapshots, "supports_collection", lambda: False)
    with pytest.raises(snapshots.SnapshotCapacityError) as caught:
        _publish(tmp_path, "oversized")
    assert not caught.value.admission["capacity_proved"]
    assert "no-follow" in caught.value.admission["collection"]["reason"]
    assert before == _identities([pair, pair.with_suffix(".json")])
    assert _publish(tmp_path, "required-on-unsupported-host", pin_reason="checkpoint").exists()


def test_snapshot_failed_durable_write_never_selects(tmp_path, monkeypatch):
    root = tmp_path / ".taskplane"
    original_fsync = snapshots.os.fsync
    def fail_fsync(fd):
        raise OSError("directory durability unavailable")
    monkeypatch.setattr(snapshots.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="durability unavailable"):
        snapshots.publish_pair(tmp_path, _snapshot_model(tmp_path, "failed"), "failed", select=True)
    assert not (root / "dashboard.selection.json").exists()
    assert not (root / "dashboard.html").exists()
    monkeypatch.setattr(snapshots.os, "fsync", original_fsync)


def test_snapshot_byte_pressure_keeps_required_publication(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "MAX_BYTES", 1024)
    required = _publish(tmp_path, "byte-pressure-required", pin_reason="checkpoint")
    paths = [required, required.with_suffix(".json")]
    before = _identities(paths)
    # The HTML member fits its file read bound, but together the pair exceeds
    # the optional target. Required durable publication remains distinct.
    document = "x" * snapshots.MAX_BYTES
    model = _snapshot_model(tmp_path, "byte-pressure")
    with pytest.raises(snapshots.SnapshotCapacityError) as caught:
        snapshots.publish_pair(tmp_path, model, document, detached=True)
    admission = caught.value.admission
    assert admission["projected_bytes"] > snapshots.MAX_BYTES
    assert not admission["optional_admitted"] and not admission["capacity_proved"]
    assert before == _identities(paths)
    _assert_collection_safety(admission["collection"])
    published = snapshots.publish_pair(tmp_path, model, document, detached=True, pin_reason="durable-checkpoint")
    assert published.stat().st_size == snapshots.MAX_BYTES
    assert before == _identities(paths)


@pytest.mark.parametrize("reference_name", ["snapshot-catalog.json", "rendering"])
def test_snapshot_nested_catalog_and_rendering_names_are_reference_roots(tmp_path, reference_name):
    pair = _publish(tmp_path, "nested-reference")
    nested = tmp_path / ".taskplane" / "context-v1" / "objects"
    nested.mkdir(parents=True)
    if reference_name == "rendering":
        reference_name = "snapshot-" + "a" * 16 + "-" + "b" * 64 + ".html"
    (nested / reference_name).write_text(json.dumps({"artifact": str(pair)}))
    result = snapshots.collect(tmp_path, max_pairs=0, now=time.time() + 120)
    assert pair.exists() and result["removed_pairs"] == 0
    if snapshots.supports_collection():
        catalog = json.loads((tmp_path / ".taskplane" / snapshots.CATALOG).read_text())
        assert "durable-reference" in catalog["pairs"][pair.stem]["pins"]
