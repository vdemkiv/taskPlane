"""Focused adversarial proofs for H1-A terminal authority and evidence CAS."""

from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
import subprocess
import sys

from taskplane import terminal_truth
from taskplane import delivery_ports
from taskplane import loop
from taskplane.delivery_ports import DeliveryPortError, SandboxEvidenceStore


RUN_ID = "h1-terminal-run"
OPERATION_ID = "finalize-h1"
SOURCE_SHA = "a" * 40
ZERO_FP = "0" * 64


def _terminal_input():
    wiring = {"fingerprint": "7" * 64}
    identity = {
        "full_source_sha": SOURCE_SHA,
        "terminal_status": terminal_truth.TERMINAL_STATUS,
        "requirement_id": "R-0002",
        "design_fingerprint": "1" * 64,
        "plan_fingerprint": "2" * 64,
        "graph_fingerprint": "3" * 64,
        "native_usage_fingerprint": "4" * 64,
        "candidate_wiring_fingerprint": wiring["fingerprint"],
        "full_suite_fingerprint": "5" * 64,
        "predecessor_fingerprint": ZERO_FP,
    }
    surfaces = {
        surface_id: terminal_truth.prepare_terminal_surface(
            surface_id,
            identity,
            {"surface": surface_id, "redacted": True},
        )
        for surface_id in terminal_truth.SURFACE_IDS
    }
    return identity, surfaces, wiring


def _accept_test_wiring(monkeypatch):
    monkeypatch.setattr(
        terminal_truth.wiring_closure,
        "validate_candidate_checkout_receipt",
        lambda value, **_expected: dict(value),
    )


def test_h03_supported_terminal_composition_root(tmp_path, monkeypatch):
    _accept_test_wiring(monkeypatch)
    identity, surfaces, wiring = _terminal_input()

    receipt = terminal_truth.finalize_terminal_delivery(
        tmp_path / "authority",
        exports_root=tmp_path / "exports",
        run_id=RUN_ID,
        operation_id=OPERATION_ID,
        identity=identity,
        surfaces=surfaces,
        candidate_wiring_receipt=wiring,
        observed_head_sha=SOURCE_SHA,
        checkout_clean=True,
    )

    assert terminal_truth.assert_terminal_authority(
        receipt, expected_sha=SOURCE_SHA, expected_requirement_id="R-0002"
    )["status"] == "complete"
    assert (tmp_path / "authority" / "head.json").is_file()
    assert (tmp_path / "exports" / f"{SOURCE_SHA}.json").is_file()


def test_h03_retro_finalizes_and_propagates_live_terminal_authority(monkeypatch):
    terminal_delivery = {
        "authority_root": "/authority",
        "exports_root": "/exports",
        "run_id": RUN_ID,
        "operation_id": OPERATION_ID,
        "identity": {},
        "surfaces": {},
        "candidate_wiring_receipt": {},
        "observed_head_sha": SOURCE_SHA,
        "checkout_clean": True,
    }
    final = {"step": "done", "terminal_delivery": terminal_delivery}
    live_receipt = object()
    events = []
    monkeypatch.setattr(
        loop.retro_engine, "run", lambda *_args, **_kwargs: {"goal": "done"}
    )
    monkeypatch.setattr(loop, "load", lambda _ws: final)
    monkeypatch.setattr(
        loop,
        "_stage_loop_transition",
        lambda *_args, **_kwargs: events.append("transition") or {"status": "done"},
    )

    def finalize(**kwargs):
        events.append("finalize")
        assert kwargs == terminal_delivery
        return live_receipt

    monkeypatch.setattr(terminal_truth, "finalize_terminal_delivery", finalize)

    completed = loop.retro("/repo")

    assert events == ["transition", "finalize"]
    assert completed["terminal_authority"] is live_receipt

    monkeypatch.setattr(
        terminal_truth,
        "finalize_terminal_delivery",
        lambda **_kwargs: (_ for _ in ()).throw(
            terminal_truth.TerminalTruthError("partial", "terminal evidence missing")
        ),
    )

    refused = loop.retro("/repo")

    assert refused["step"] == "retro"
    assert "terminal delivery failed closed" in refused["error"]
    assert "terminal_authority" not in refused


def test_h04_restart_reacquires_exact_terminal_authority(tmp_path):
    identity, surfaces, wiring = _terminal_input()
    payload_path = tmp_path / "input.json"
    payload_path.write_text(
        json.dumps({"identity": identity, "surfaces": surfaces, "wiring": wiring}),
        encoding="utf-8",
    )
    authority = tmp_path / "authority"
    exports = tmp_path / "exports"
    script = """
import json, sys
from pathlib import Path
from taskplane import terminal_truth
terminal_truth.wiring_closure.validate_candidate_checkout_receipt = lambda value, **kw: dict(value)
data = json.loads(Path(sys.argv[1]).read_text())
fault = None if sys.argv[4] == '-' else sys.argv[4]
receipt = terminal_truth.finalize_terminal_delivery(
    Path(sys.argv[2]), exports_root=Path(sys.argv[3]), run_id='h1-terminal-run',
    operation_id='finalize-h1', identity=data['identity'], surfaces=data['surfaces'],
    candidate_wiring_receipt=data['wiring'], observed_head_sha='a' * 40,
    checkout_clean=True, commit_fault_at=fault,
)
print(receipt['status'])
"""
    failed = subprocess.run(
        [
            sys.executable, "-c", script, str(payload_path), str(authority),
            str(exports), "before_cas",
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    assert failed.returncode != 0
    assert not (authority / "head.json").exists()

    recovered = subprocess.run(
        [sys.executable, "-c", script, str(payload_path), str(authority), str(exports), "-"],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    assert recovered.returncode == 0, recovered.stderr
    assert recovered.stdout.strip() == "complete"

    (authority / "projections" / "public_report.json").unlink()
    reconciled = subprocess.run(
        [sys.executable, "-c", script, str(payload_path), str(authority), str(exports), "-"],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    assert reconciled.returncode == 0, reconciled.stderr
    assert (authority / "projections" / "public_report.json").is_file()


def test_h05_immutable_publish_is_atomic_and_durable(tmp_path):
    target = tmp_path / "immutable" / "bundle.json"
    payload = b"x" * 131072
    script = """
import os, sys
from pathlib import Path
from taskplane.terminal_truth import TerminalCoordinator
real_write = os.write
def interrupted(fd, data):
    written = real_write(fd, data[: max(1, len(data) // 2)])
    os.kill(os.getpid(), 9)
    return written
os.write = interrupted
TerminalCoordinator._write_immutable(Path(sys.argv[1]), b'x' * 131072)
"""
    killed = subprocess.run(
        [sys.executable, "-c", script, str(target)],
        capture_output=True,
        check=False,
    )
    assert killed.returncode != 0
    assert not target.exists()

    terminal_truth.TerminalCoordinator._write_immutable(target, payload)
    assert target.read_bytes() == payload


class _BeforeCasBarrier:
    def __init__(self, barrier):
        self._barrier = barrier

    def checkpoint(self, seam):
        if seam == "before-head-cas":
            try:
                self._barrier.wait(timeout=1)
            except Exception:
                pass


def _commit_competing_successor(root, operation, barrier, queue):
    store = SandboxEvidenceStore(
        root,
        "repository",
        "run",
        fault_injector=_BeforeCasBarrier(barrier),
    )
    prepared = store.prepare("telemetry", operation, {"operation": operation})
    try:
        queue.put(("committed", json.loads(store.commit(prepared))["fingerprint"]))
    except DeliveryPortError as exc:
        queue.put(("refused", str(exc)))


def test_h06_only_one_successor_wins_per_predecessor(tmp_path, monkeypatch):
    monkeypatch.setattr(delivery_ports, "fcntl", None)
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    queue = context.Queue()
    processes = [
        context.Process(
            target=_commit_competing_successor,
            args=(tmp_path, operation, barrier, queue),
        )
        for operation in ("first", "second")
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    outcomes = [queue.get(timeout=1) for _ in processes]

    assert [status for status, _ in outcomes].count("committed") == 1
    assert [status for status, _ in outcomes].count("refused") == 1
    assert "CAS mismatch" in next(detail for status, detail in outcomes if status == "refused")
    receipts = list(
        (
            tmp_path / ".taskplane-evidence" / "repository" / "run" /
            "telemetry" / "receipts"
        ).glob("*.json")
    )
    assert len(receipts) == 1
