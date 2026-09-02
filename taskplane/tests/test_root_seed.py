from __future__ import annotations

import copy
import json

import pytest

from taskplane import root_seed, tp
from taskplane.settings import load_settings


def _context(settings=None):
    return {
        "run_id": "run-38",
        "wave_id": "W1",
        "candidate_sha": "a" * 40,
        "settings": settings or load_settings(),
        "delivery_mode": "iteration",
        "design": {"path": "design/contract.json", "fingerprint": "b" * 64},
        "plan": {"path": "plan/tasks.json", "fingerprint": "c" * 64},
        "prepared_at": "2026-09-02T04:00:00Z",
        "operation_id": "prepare-root-run-38-w1",
    }


def _inputs():
    return {
        "pickups": [
            {"id": "P12", "write_scopes": ["taskplane/z.py", "taskplane/a.py"],
             "disjointness_receipt_fingerprint": "d" * 64},
            {"id": "P11", "write_scopes": ["taskplane/b.py"],
             "disjointness_receipt_fingerprint": "e" * 64},
        ],
        "wave_budgets": {"max_actions": 60, "target_tokens": 12_000_000,
                         "max_tokens": 17_000_000},
        "outstanding_human_gates": [
            {"id": "final-signoff", "owner": "human:operator"}],
        "predecessor_terminal_projection": {
            "path": "runs/prior/terminal-projection.json",
            "fingerprint": "f" * 64,
        },
    }


def test_root_seed_is_exact_deterministic_reference_only_and_bound_to_host_start_and_wave(
        tmp_path, monkeypatch, capsys):
    """Prove P10's producer binding only; actual consumers belong to P13."""
    context = _context()
    inputs = _inputs()
    first = root_seed.build_root_seed(context, inputs)

    reordered = copy.deepcopy(inputs)
    reordered["pickups"].reverse()
    reordered["pickups"][1]["write_scopes"].reverse()
    assert root_seed.build_root_seed(context, reordered) == first
    assert set(first) == root_seed.ROOT_SEED_FIELDS
    assert first["schema"] == "taskplane.root-seed/v1"
    assert first["run_id"] == "run-38"
    assert first["wave_id"] == "W1"
    assert first["settings_fingerprint"] == context["settings"].digest
    assert first["budgets"]["seed_budget_tokens"] == 40_000
    assert first["budgets"]["root_budget_tokens"] == 40_000_000
    assert [row["id"] for row in first["pickups"]] == ["P11", "P12"]
    assert first["pickups"][1]["write_scopes"] == [
        "taskplane/a.py", "taskplane/z.py"]
    assert len(first["seed_fingerprint"]) == 64
    assert len(json.dumps(first, sort_keys=True).encode("utf-8")) < 64 * 1024

    root = tmp_path / "export"
    receipt = root_seed.prepare_root_seed(
        root, "waves/W1/root-seed.json", context, inputs)
    stored = json.loads((root / "waves/W1/root-seed.json").read_text(
        encoding="utf-8"))
    assert stored == first
    assert receipt["seed_fingerprint"] == first["seed_fingerprint"]
    assert receipt["binding"] == root_seed.seed_binding(first)
    assert set(receipt) == {
        "schema", "status", "seed_ref", "seed_fingerprint", "binding",
        "prepared_at", "operation_id",
    }
    assert len(json.dumps(receipt, sort_keys=True).encode("utf-8")) < 4096

    binding = root_seed.seed_binding(first)
    assert binding == {
        "candidate_sha": "a" * 40,
        "run_id": "run-38",
        "settings_fingerprint": context["settings"].digest,
        "seed_fingerprint": first["seed_fingerprint"],
        "wave_id": "W1",
    }
    root_seed.verify_seed_binding(
        first, binding, surface="future host root start")
    root_seed.verify_seed_binding(
        first, dict(binding), surface="future wave seal")
    mismatched = dict(binding, wave_id="W2")
    with pytest.raises(root_seed.RootSeedError, match="future wave seal binding"):
        root_seed.verify_seed_binding(
            first, mismatched, surface="future wave seal")

    cli_context = {key: value for key, value in context.items()
                   if key != "settings"}
    request_path = tmp_path / "root-seed-request.json"
    request_path.write_text(json.dumps({
        "context": cli_context, "inputs": inputs,
    }), encoding="utf-8")
    cli_workspace = tmp_path / "cli-workspace"
    cli_workspace.mkdir()
    assert tp.main([
        "root-seed", "--request", str(request_path),
        "--output", "waves/W1/root-seed.json",
        "--workspace", str(cli_workspace),
    ]) == 0
    cli_receipt = json.loads(capsys.readouterr().out)
    assert cli_receipt["seed_fingerprint"] == first["seed_fingerprint"]
    assert cli_receipt["binding"] == binding
    assert json.loads((cli_workspace / "waves/W1/root-seed.json").read_text(
        encoding="utf-8")) == first

    original_prepare = root_seed.prepare_root_seed

    def stale_prepare(*args, **kwargs):
        stale = original_prepare(*args, **kwargs)
        stale["binding"]["wave_id"] = "W2"
        return stale

    monkeypatch.setattr(root_seed, "prepare_root_seed", stale_prepare)
    stale_workspace = tmp_path / "stale-workspace"
    stale_workspace.mkdir()
    assert tp.main([
        "root-seed", "--request", str(request_path),
        "--output", "waves/W1/root-seed.json",
        "--workspace", str(stale_workspace),
    ]) == 1
    refusal = json.loads(capsys.readouterr().out)
    assert refusal["status"] == "refused"
    assert "prepare receipt binding" in refusal["error"]
    monkeypatch.setattr(root_seed, "prepare_root_seed", original_prepare)

    oversized_request = tmp_path / "oversized-root-seed-request.json"
    oversized_request.write_text(" " * (root_seed.MAX_SEED_BYTES + 1),
                                  encoding="utf-8")
    assert tp.main([
        "root-seed", "--request", str(oversized_request),
        "--output", "waves/W1/oversized.json",
        "--workspace", str(cli_workspace),
    ]) == 1
    refusal = json.loads(capsys.readouterr().out)
    assert refusal["status"] == "refused"
    assert "exceeds the 65536-byte bound" in refusal["error"]
    assert not (cli_workspace / "waves/W1/oversized.json").exists()


def test_prepare_existing_seed_accepts_only_exact_valid_idempotent_bytes(
        tmp_path):
    seed_ref = "waves/W1/root-seed.json"
    context = _context()
    inputs = _inputs()
    first = root_seed.prepare_root_seed(tmp_path, seed_ref, context, inputs)
    target = tmp_path / seed_ref
    exact = target.read_bytes()

    assert root_seed.prepare_root_seed(
        tmp_path, seed_ref, context, inputs) == first
    assert target.read_bytes() == exact

    valid_seed = root_seed.build_root_seed(context, inputs)
    target.write_text(json.dumps(valid_seed, indent=2), encoding="utf-8")
    with pytest.raises(root_seed.RootSeedError, match="other data"):
        root_seed.prepare_root_seed(tmp_path, seed_ref, context, inputs)

    conflicting_context = _context()
    conflicting_context["wave_id"] = "W2"
    conflicting_context["operation_id"] = "prepare-root-run-38-w2"
    conflicting = root_seed.build_root_seed(conflicting_context, inputs)
    target.write_text(json.dumps(
        conflicting, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    with pytest.raises(root_seed.RootSeedError, match="other data"):
        root_seed.prepare_root_seed(tmp_path, seed_ref, context, inputs)


@pytest.mark.parametrize("kind, error", [
    ("invalid-utf8", "unreadable"),
    ("invalid-json", "unreadable"),
    ("oversized", "65536-byte bound"),
])
def test_persisted_seed_boundaries_refuse_malformed_or_oversized_bytes(
        tmp_path, kind, error):
    body = {
        "invalid-utf8": b"\xff",
        "invalid-json": b"{",
        "oversized": b" " * (root_seed.MAX_SEED_BYTES + 1),
    }[kind]
    seed_ref = "waves/W1/root-seed.json"
    target = tmp_path / seed_ref
    target.parent.mkdir(parents=True)
    target.write_bytes(body)

    with pytest.raises(root_seed.RootSeedError, match=error):
        root_seed.load_root_seed(tmp_path, seed_ref)
    with pytest.raises(root_seed.RootSeedError, match=error):
        root_seed.prepare_root_seed(
            tmp_path, seed_ref, _context(), _inputs())


def test_prepare_existing_seed_refuses_contract_invalid_equal_python_value(
        tmp_path):
    seed_ref = "waves/W1/root-seed.json"
    invalid = root_seed.build_root_seed(_context(), _inputs())
    invalid["version"] = True
    target = tmp_path / seed_ref
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(invalid), encoding="utf-8")

    with pytest.raises(root_seed.RootSeedError, match="version must be 1"):
        root_seed.load_root_seed(tmp_path, seed_ref)
    with pytest.raises(root_seed.RootSeedError, match="version must be 1"):
        root_seed.prepare_root_seed(
            tmp_path, seed_ref, _context(), _inputs())


@pytest.mark.parametrize(
    "shape", ["final-symlink", "symlinked-parent", "directory"])
def test_persisted_seed_reference_refuses_symlinks_and_nonregular_targets(
        tmp_path, shape):
    """Both public consumers refuse aliases and non-file seed identities."""
    seed_ref = "waves/W1/root-seed.json"
    canonical = root_seed._canonical(
        root_seed.build_root_seed(_context(), _inputs()))
    actual_parent = tmp_path / "actual"
    actual_parent.mkdir()
    actual = actual_parent / "root-seed.json"
    actual.write_bytes(canonical)
    target = tmp_path / seed_ref

    if shape == "final-symlink":
        target.parent.mkdir(parents=True)
        try:
            target.symlink_to(actual)
        except (NotImplementedError, OSError) as exc:
            pytest.skip(f"symlinks unavailable: {exc}")
    elif shape == "symlinked-parent":
        target.parent.parent.mkdir()
        try:
            target.parent.symlink_to(actual_parent, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            pytest.skip(f"symlinks unavailable: {exc}")
    else:
        target.mkdir(parents=True)

    for operation in (
        lambda: root_seed.load_root_seed(tmp_path, seed_ref),
        lambda: root_seed.prepare_root_seed(
            tmp_path, seed_ref, _context(), _inputs()),
    ):
        with pytest.raises(
                root_seed.RootSeedError, match="symlink|regular file"):
            operation()


def test_persisted_seed_reference_accepts_one_regular_file_identity(tmp_path):
    seed_ref = "waves/W1/root-seed.json"

    receipt = root_seed.prepare_root_seed(
        tmp_path, seed_ref, _context(), _inputs())
    loaded = root_seed.load_root_seed(tmp_path, seed_ref)

    assert receipt["seed_ref"] == seed_ref
    assert receipt["seed_fingerprint"] == loaded["seed_fingerprint"]
    assert (tmp_path / seed_ref).is_file()
    assert not (tmp_path / seed_ref).is_symlink()


def test_prepare_does_not_clobber_a_target_created_during_publish(
        tmp_path, monkeypatch):
    seed_ref = "waves/W1/root-seed.json"
    target = tmp_path / seed_ref
    competing_context = _context()
    competing_context["wave_id"] = "W2"
    competing_context["operation_id"] = "prepare-root-run-38-w2"
    competing = root_seed._canonical(
        root_seed.build_root_seed(competing_context, _inputs()))
    publish = root_seed.os.link

    def publish_after_competitor(source, destination):
        target.write_bytes(competing)
        return publish(source, destination)

    monkeypatch.setattr(root_seed.os, "link", publish_after_competitor)
    with pytest.raises(root_seed.RootSeedError, match="other data"):
        root_seed.prepare_root_seed(
            tmp_path, seed_ref, _context(), _inputs())
    assert target.read_bytes() == competing


def test_root_seed_consumer_boundary_rejects_rehashed_malformed_seed():
    valid = root_seed.build_root_seed(_context(), _inputs())

    malformed_seeds = []
    for mutation in (
        lambda seed: seed.update(schema="attacker.seed/v99"),
        lambda seed: seed.update(version=2),
        lambda seed: seed.update(candidate_sha="not-a-git-object"),
        lambda seed: seed.update(settings_fingerprint="invalid"),
        lambda seed: seed["approved_design"].update(
            prompt="retained secret"),
        lambda seed: seed["pickups"][0].update(write_scopes=[]),
        lambda seed: seed["budgets"].update(max_tokens=1),
        lambda seed: seed["outstanding_human_gates"][0].update(
            transcript="retained secret"),
    ):
        malformed = copy.deepcopy(valid)
        mutation(malformed)
        material = {key: value for key, value in malformed.items()
                    if key != "seed_fingerprint"}
        malformed["seed_fingerprint"] = root_seed._digest(material)
        malformed_seeds.append(malformed)

    for malformed in malformed_seeds:
        with pytest.raises(root_seed.RootSeedError):
            root_seed.seed_binding(malformed)


@pytest.mark.parametrize("field", [
    "prompt", "transcript", "conversation", "worker_output", "raw_test_log",
])
def test_root_seed_rejects_unknown_transcript_prompt_worker_output_log_and_native_path_fields(
        tmp_path, field):
    context = _context()
    inputs = _inputs()
    inputs[field] = "must not be retained"
    with pytest.raises(root_seed.RootSeedError, match="unknown seed input"):
        root_seed.build_root_seed(context, inputs)

    native_paths = [
        "/Users/operator/private/design.json",
        "C:\\Users\\operator\\design.json",
        "../outside/plan.json",
        "\\\\server\\share\\plan.json",
        "design/bad?.json",
        "design/bad*.json",
        "design/trailing./contract.json",
        "design/NUL.json",
        "design/com1/contract.json",
        "design/control\u0001.json",
    ]
    for native_path in native_paths:
        bad = _context()
        bad["design"]["path"] = native_path
        with pytest.raises(root_seed.RootSeedError, match="portable relative"):
            root_seed.build_root_seed(bad, _inputs())

    with pytest.raises(root_seed.RootSeedError, match="portable relative"):
        root_seed.prepare_root_seed(
            tmp_path, "/tmp/root-seed.json", _context(), _inputs())

    for prepared_at in (
        "Z", "2026-09-02", "2026-09-02T04:00:00",
        "2026-09-02T04:00:00+00:00", "2026-02-30T04:00:00Z",
    ):
        bad = _context()
        bad["prepared_at"] = prepared_at
        with pytest.raises(root_seed.RootSeedError, match="canonical UTC"):
            root_seed.build_root_seed(bad, _inputs())
