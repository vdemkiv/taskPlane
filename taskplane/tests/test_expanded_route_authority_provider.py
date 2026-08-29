"""Protected, content-addressed expanded-route authority provider proofs."""

from __future__ import annotations

import base64
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time

import pytest

from taskplane import expanded_route_authority_provider as provider
from taskplane import taskplane_lite as tp
from taskplane import terminal_truth


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "taskplane" / "expanded_route_authority_provider.py"
NOW = 1_700_000_000
_RSA_N = int(
    "b154d86cc0e29a09faebc95408e7655c18ee251455dec44a41cc63e102373ab0"
    "7f5034d795548ecf9eee74d77c5c1a7fa7ea64afddd92e0ec70efab1fe6bddf6"
    "6b103221c9ab211eab3edb8ad3091075014674493e3a6b49e23432807724856dd"
    "736550efa8b4db1f1d62dfddfab193ed3fcb290bfc4829f5be99f0e71962acd3"
    "6a7462b4e9f80bd632fc711de7c08e6574d196087e03e3021d49e0c61dfd408a"
    "619873aca469ff5111a9b0d26a4aaab00fc8c95db16f1027b4dbf86322b219ea"
    "a875a2781defd26a9dc0ac0ff1d46e97523a0f568b22f10aab3f22149d6262aa"
    "69882c84119e1b776b81a31d7649629408edd8ecf4d5691ec71d1761ba903371"
    "702738426942d8814bbe177688d6fb45ca4d32d72aedbe5f2f5ea3baf89705b3"
    "000d01330e3ace2d7a996504132f1ad23799a3d998a593eb60286b2734058364"
    "a1c0e46cc8eb1c3a94e89f31a8c954c314e6fe5f5ab0a5292fd7b605062ae60"
    "dea3f0c754e7c8f1fc40d04267563405068c1de76a3b0a9e69d73cec1449c98f",
    16,
)
_RSA_D = int(
    "4d6ae8b785f807deee8c6ccf42b9deeef7b5543bce075cb3bc89225bb3ef6fb0"
    "694c975d3d04f6fa1e7d25468434e39eb2acaa7b7b039b4f7949095a96e3f9b"
    "9e060e83a0704ae8768a49f0d3af7dc96f05115687a81dfa01860c8617c6255d"
    "c2fd639093a8981887bb79149a211dd0a285b4c8bd424d310067cf66344649658"
    "1411b0192d10869068128e3ab7627832338fe5d1d65028663406d25f3e858cd89"
    "fc74a59f8c01f8a4a86ce854dc71af9600b61c6f4a8cddc52ff8ceb65d09db29"
    "edbad767b0b12cea6ad5e25a0029f3b82c40aab66ed5d56b00610368a14ea0fd"
    "62b1bea75fc7b6bc57c36fe421d22ffe7788a4857bf833166906e80fd1f947cdf"
    "4dd5638000c128352fc0ff30a36cad072d63b0c478c7f04ca37e3da24c662ef6f"
    "a202a4c828585e694b3410b69fa04835fabbab71a617085a195e936da3168540d"
    "35995d2e80f220b802664f52e48b7030558524987f8c7dc871eabe8b84ad0441e"
    "2eaeb3ca7ecd71a2a11ce15dedc6f64e3aab457ae42c78c0de917b9d4bd",
    16,
)
_RSA_E = 65_537


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _mgf1(seed: bytes, length: int) -> bytes:
    out = b""
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        counter += 1
    return out[:length]


def _pss_sign(payload: dict[str, object], salt: bytes = b"s" * 32) -> str:
    message_hash = hashlib.sha256(_canonical(payload)).digest()
    em_bits = _RSA_N.bit_length() - 1
    em_len = (em_bits + 7) // 8
    digest = hashlib.sha256(b"\0" * 8 + message_hash + salt).digest()
    data = b"\0" * (em_len - len(salt) - 34) + b"\1" + salt
    mask = _mgf1(digest, em_len - 33)
    masked = bytearray(left ^ right for left, right in zip(data, mask))
    masked[0] &= 0xFF >> (8 * em_len - em_bits)
    encoded = bytes(masked) + digest + b"\xbc"
    signature = pow(int.from_bytes(encoded, "big"), _RSA_D, _RSA_N)
    size = (_RSA_N.bit_length() + 7) // 8
    return base64.b64encode(signature.to_bytes(size, "big")).decode("ascii")


def _key() -> dict[str, object]:
    material = {
        "algorithm": "rsa-pss-sha256",
        "modulus": format(_RSA_N, "x"),
        "exponent": _RSA_E,
    }
    return {**material, "key_fingerprint": hashlib.sha256(
        _canonical(material)).hexdigest(), "approver_identity": "human:operator"}


def _install(tmp_path: Path) -> dict[str, object]:
    return provider.install_provider(
        source_path=str(SOURCE),
        repository_source_path="taskplane/expanded_route_authority_provider.py",
        repository_commit="a" * 40,
        authority_root=str(tmp_path / "expanded-route-authority"),
        approver_keys=[_key()],
    )


def _request(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "workspace": "b" * 64,
        "stage": "evaluate",
        "target": "LR-03@candidate",
        "context_fingerprint": "c" * 64,
        "exact_ordered_lens_ids": ["security", "privacy-compliance"],
        "estimated_cost": 2_400,
        "policy_version": "focused-routing/v1",
        "catalog_version": "catalog/v1",
        "action_id": "expanded-LR-03-1",
    }
    values.update(overrides)
    return {"schema": provider.REQUEST_SCHEMA, **values}


def _approval(
    installation: dict[str, object], request: dict[str, object], *,
    now: int = NOW, **overrides: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": provider.APPROVAL_PAYLOAD_SCHEMA,
        "repository_source_path": installation["repository_source_path"],
        "repository_commit": installation["repository_commit"],
        "source_sha256": installation["source_sha256"],
        "package_sha256": installation["package_sha256"],
        "provider_protocol_version": provider.PROTOCOL_VERSION,
        **{field: request[field] for field in (
            "workspace", "stage", "target", "context_fingerprint",
            "exact_ordered_lens_ids", "estimated_cost", "policy_version",
            "catalog_version", "action_id",
        )},
        "approved_at": now,
        "expiry": now + 300,
        "approver_identity": "human:operator",
        "approver_key_fingerprint": _key()["key_fingerprint"],
    }
    payload.update(overrides)
    return {
        "schema": provider.APPROVAL_RECEIPT_SCHEMA,
        "payload": payload,
        "signature": _pss_sign(payload),
    }


def _run_package(
    installation: dict[str, object], request: dict[str, object],
    approval: dict[str, object], *, cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(installation["package_path"]), "authorize",
         "--locator", str(installation["locator_path"])],
        cwd=cwd,
        input=json.dumps({"request": request, "approval": approval}),
        text=True,
        capture_output=True,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ""},
        check=False,
    )


def _client(
    tmp_path: Path, installation: dict[str, object],
) -> terminal_truth.ExpandedRouteProviderClient:
    coordinator = terminal_truth.TerminalCoordinator(
        tmp_path / "terminal-orchestrator-authority")
    return coordinator.expanded_route_provider_client(
        str(installation["locator_path"]))


def _fabricated_receipt(
    installation: dict[str, object], request: dict[str, object], *,
    protocol: str = provider.PROTOCOL_VERSION,
) -> dict[str, object]:
    action: dict[str, object] = {
        "schema": provider.ACTION_SCHEMA,
        "key_id": "1" * 64,
        "repository_source_path": installation["repository_source_path"],
        "repository_commit": installation["repository_commit"],
        "source_sha256": installation["source_sha256"],
        "package_sha256": installation["package_sha256"],
        "provider_protocol_version": protocol,
        **{field: request[field] for field in (
            "workspace", "stage", "target", "context_fingerprint",
            "exact_ordered_lens_ids", "estimated_cost", "policy_version",
            "catalog_version", "action_id",
        )},
        "issued_at": NOW,
        "expiry": NOW + 300,
        "approver_identity": "human:operator",
        "approver_key_fingerprint": _key()["key_fingerprint"],
        "approval_receipt_digest": "5" * 64,
        "seal": "6" * 64,
    }
    return {
        "schema": provider.CONSUMPTION_SCHEMA,
        "provider_protocol_version": protocol,
        "locator_fingerprint": "7" * 64,
        "action": action,
        "action_fingerprint": hashlib.sha256(_canonical(action)).hexdigest(),
        "approval_receipt_digest": "5" * 64,
        "consumed_at": NOW + 1,
        "recovered": False,
        "seal": "8" * 64,
    }


def test_worker_monkeypatch_cannot_replace_provider_source_clock_or_rsa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = _install(tmp_path)
    request = _request(action_id="prod-worker-probe")
    current = int(time.time())
    approval = _approval(installation, request, now=current)

    assert not hasattr(tp, "issue_expanded_lens_route_action")
    assert not hasattr(tp, "verify_expanded_lens_route_action")
    assert not hasattr(tp, "consume_expanded_lens_route_action")
    monkeypatch.setattr(provider.time, "time", lambda: 0)
    monkeypatch.setattr(provider, "_rsa_pss_sha256_valid", lambda *args: False)
    monkeypatch.setattr(tp._time, "time", lambda: 0)

    result = _run_package(installation, request, approval)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["action"]["action_id"] == "prod-worker-probe"
    assert receipt["consumed_at"] >= current

    broadened = dict(request, clock=current, locator=installation["locator_path"])
    refused = _run_package(installation, broadened, approval)
    assert refused.returncode != 0
    assert "request" in refused.stderr.lower()


def test_missing_altered_relocated_or_symlinked_locator_and_custody_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def check(mutator) -> None:
        case = tmp_path / f"case-{len(list(tmp_path.iterdir()))}"
        installation = _install(case)
        request = _request(action_id=f"locator-{case.name}")
        approval = _approval(installation, request)
        locator = Path(str(installation["locator_path"]))
        mutator(installation, locator)
        with pytest.raises(provider.ProviderError):
            provider._authorize_for_test(
                str(locator), request, approval, now=NOW)

    check(lambda _install, locator: locator.unlink())

    def alter(_install, locator: Path) -> None:
        locator.write_text(locator.read_text() + " ", encoding="utf-8")
    check(alter)

    def symlink(_install, locator: Path) -> None:
        target = locator.with_suffix(".real")
        locator.rename(target)
        locator.symlink_to(target)
    check(symlink)

    def custody_mode(installation, _locator: Path) -> None:
        os.chmod(str(installation["issuer_key_path"]), 0o644)
    check(custody_mode)

    def custody_missing(installation, _locator: Path) -> None:
        Path(str(installation["issuer_key_path"])).unlink()
    check(custody_missing)

    def custody_altered(installation, _locator: Path) -> None:
        issuer = Path(str(installation["issuer_key_path"]))
        issuer.write_bytes(b"x" * 32)
        os.chmod(issuer, 0o600)
    check(custody_altered)

    def custody_symlink(installation, _locator: Path) -> None:
        issuer = Path(str(installation["issuer_key_path"]))
        target = issuer.with_name("issuer-real.key")
        issuer.rename(target)
        issuer.symlink_to(target)
    check(custody_symlink)

    relocated = tmp_path / "relocated-case"
    installation = _install(tmp_path / "original-case")
    shutil.copytree(str(installation["authority_root"]), relocated)
    old_locator = Path(str(installation["locator_path"]))
    relocated_locator = relocated / old_locator.relative_to(
        str(installation["authority_root"]))
    with pytest.raises(provider.ProviderError, match="relocat|root"):
        provider._authorize_for_test(
            str(relocated_locator), _request(action_id="relocated"),
            _approval(installation, _request(action_id="relocated")), now=NOW)

    ownership = _install(tmp_path / "ownership-case")
    monkeypatch.setattr(provider, "_current_uid", lambda: os.geteuid() + 1)
    with pytest.raises(provider.ProviderError, match="owner"):
        provider._validate_installation(
            str(ownership["locator_path"]),
            execution_path=str(ownership["package_path"]),
        )


def test_locator_cannot_relocate_protected_objects_inside_authority_root(
    tmp_path: Path,
) -> None:
    installation = _install(tmp_path)
    locator_path = Path(str(installation["locator_path"]))
    locator = json.loads(locator_path.read_text(encoding="utf-8"))
    root = Path(str(installation["authority_root"]))
    relocated_issuer = root / "custody" / "relocated-issuer.key"
    shutil.copyfile(str(installation["issuer_key_path"]), relocated_issuer)
    os.chmod(relocated_issuer, 0o600)
    locator["issuer_key_path"] = str(relocated_issuer)
    forged_bytes = _canonical(locator)
    forged_locator = root / "locators" / (
        hashlib.sha256(forged_bytes).hexdigest() + ".json")
    forged_locator.write_bytes(forged_bytes)
    os.chmod(forged_locator, 0o600)

    with pytest.raises(provider.ProviderError, match="relocat|canonical|custody"):
        provider._validate_installation(
            str(forged_locator), execution_path=str(installation["package_path"]))


@pytest.mark.parametrize(
    "field",
    [
        "repository_source_path", "repository_commit", "source_sha256",
        "package_sha256", "provider_protocol_version", "workspace", "stage",
        "target", "context_fingerprint", "exact_ordered_lens_ids",
        "estimated_cost", "policy_version", "catalog_version", "action_id",
        "approver_identity", "approver_key_fingerprint",
    ],
)
def test_exact_source_workspace_route_cost_policy_action_expiry_approver_and_receipt_binding(
    tmp_path: Path, field: str,
) -> None:
    installation = _install(tmp_path / field)
    request = _request(action_id=f"binding-{field[:20]}")
    replacement: object = "mutated"
    if field == "estimated_cost":
        replacement = 2_401
    elif field == "exact_ordered_lens_ids":
        replacement = ["privacy-compliance", "security"]
    elif field in {"source_sha256", "package_sha256", "context_fingerprint",
                   "approver_key_fingerprint"}:
        replacement = "d" * 64
    approval = _approval(installation, request, **{field: replacement})

    with pytest.raises(provider.ProviderError):
        provider._authorize_for_test(
            str(installation["locator_path"]), request, approval, now=NOW)

    valid = _approval(installation, request)
    tampered = copy.deepcopy(valid)
    tampered["payload"]["estimated_cost"] = 9_999
    with pytest.raises(provider.ProviderError, match="RSA|signature"):
        provider._authorize_for_test(
            str(installation["locator_path"]), request, tampered, now=NOW)


def test_expiry_skew_and_exact_rsa_pss_parameters_fail_closed(
    tmp_path: Path,
) -> None:
    installation = _install(tmp_path)
    cases = (
        {"approved_at": NOW + 31},
        {"expiry": NOW},
        {"expiry": NOW + 3_601},
    )
    for index, changes in enumerate(cases):
        request = _request(action_id=f"time-{index}")
        approval = _approval(installation, request, **changes)
        with pytest.raises(provider.ProviderError, match="time|expiry|skew"):
            provider._authorize_for_test(
                str(installation["locator_path"]), request, approval, now=NOW)

    request = _request(action_id="short-rsa")
    approval = _approval(installation, request)
    approval["signature"] = base64.b64encode(b"x" * 383).decode("ascii")
    with pytest.raises(provider.ProviderError, match="RSA|signature"):
        provider._authorize_for_test(
            str(installation["locator_path"]), request, approval, now=NOW)


def test_atomic_one_use_consume_allows_one_concurrent_success(
    tmp_path: Path,
) -> None:
    installation = _install(tmp_path)
    request = _request(action_id="concurrent-once")
    current = int(time.time())
    approval = _approval(installation, request, now=current)

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(
            lambda _index: _run_package(installation, request, approval),
            range(5),
        ))
    assert sum(result.returncode == 0 for result in results) == 1
    assert sum("replay" in result.stderr.lower() for result in results) == 4


def test_restart_recovers_issuer_receipts_and_consumed_actions(
    tmp_path: Path,
) -> None:
    installation = _install(tmp_path)
    request = _request(action_id="restart-recovery")
    approval = _approval(installation, request)
    with pytest.raises(provider.ProviderError, match="fault"):
        provider._authorize_for_test(
            str(installation["locator_path"]), request, approval, now=NOW,
            fault_at="after-consumption-receipt",
        )

    recovered = provider._authorize_for_test(
        str(installation["locator_path"]), request, approval, now=NOW + 1)
    assert recovered["recovered"] is True
    with pytest.raises(provider.ProviderError, match="replay"):
        provider._authorize_for_test(
            str(installation["locator_path"]), request, approval, now=NOW + 2)

    before = _request(action_id="interrupted-before-receipt")
    before_approval = _approval(installation, before)
    with pytest.raises(provider.ProviderError, match="fault"):
        provider._authorize_for_test(
            str(installation["locator_path"]), before, before_approval,
            now=NOW, fault_at="before-consumption-receipt",
        )
    completed = provider._authorize_for_test(
        str(installation["locator_path"]), before, before_approval, now=NOW + 1)
    assert completed["action"]["action_id"] == "interrupted-before-receipt"


def test_clock_injection_is_available_only_to_trusted_provider_tests(
    tmp_path: Path,
) -> None:
    installation = _install(tmp_path)
    request = _request(action_id="clock-boundary")
    approval = _approval(installation, request)

    assert "clock" not in provider.AuthorityProvider.__init__.__annotations__
    with pytest.raises(TypeError):
        provider.AuthorityProvider(
            str(installation["locator_path"]), clock=lambda: NOW)
    receipt = provider._authorize_for_test(
        str(installation["locator_path"]), request, approval, now=NOW)
    assert receipt["consumed_at"] == NOW


def test_clean_content_addressed_package_import_and_protocol_binding(
    tmp_path: Path,
) -> None:
    installation = _install(tmp_path)
    package = Path(str(installation["package_path"]))
    assert package.parent.name == installation["source_sha256"]
    assert hashlib.sha256(package.read_bytes()).hexdigest() == \
        installation["package_sha256"]
    assert stat.S_IMODE(package.stat().st_mode) == 0o600

    empty = tmp_path / "empty-cwd"
    empty.mkdir()
    result = subprocess.run(
        [sys.executable, str(package), "self-check", "--locator",
         str(installation["locator_path"])],
        cwd=empty, text=True, capture_output=True,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ""},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["protocol_version"] == provider.PROTOCOL_VERSION
    assert report["package_sha256"] == installation["package_sha256"]

    altered = bytearray(package.read_bytes())
    altered[-1:] = b" "
    package.write_bytes(bytes(altered))
    os.chmod(package, 0o600)
    refused = subprocess.run(
        [sys.executable, str(package), "self-check", "--locator",
         str(installation["locator_path"])],
        cwd=empty, text=True, capture_output=True,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ""},
        check=False,
    )
    assert refused.returncode != 0


def test_orchestrator_client_launches_exact_package_and_returns_live_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = _install(tmp_path / "provider")
    client = _client(tmp_path, installation)
    request = _request(action_id="client-honest")
    current = int(time.time())
    approval = _approval(installation, request, now=current)
    observed: dict[str, object] = {}
    real_run = terminal_truth.ExpandedRouteProviderClient._run_provider

    def spy(
        argv, *, cwd: Path, environment: dict[str, str], payload: bytes,
    ) -> subprocess.CompletedProcess[bytes]:
        observed.update({
            "argv": tuple(argv), "cwd": cwd,
            "environment": dict(environment), "payload": payload,
        })
        return real_run(
            argv, cwd=cwd, environment=environment, payload=payload)

    monkeypatch.setattr(
        terminal_truth.ExpandedRouteProviderClient,
        "_run_provider", staticmethod(spy))
    receipt = client.authorize(request, approval)

    assert isinstance(receipt, terminal_truth.ExpandedRouteProviderReceipt)
    client.assert_authenticated(receipt, request)
    assert receipt["action"]["action_id"] == "client-honest"
    assert observed["argv"] == (
        str(Path(sys.executable).resolve()), "-I",
        str(installation["package_path"]), "authorize", "--locator",
        str(installation["locator_path"]),
    )
    assert observed["cwd"] == Path(str(installation["authority_root"]))
    assert set(observed["environment"]) == {
        "PATH", "LANG", "LC_ALL", "PYTHONDONTWRITEBYTECODE",
        "PYTHONHASHSEED", "PYTHONNOUSERSITE",
    }
    assert b'"request"' in observed["payload"]


def test_orchestrator_client_rejects_worker_fabricated_hmac_seals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = _install(tmp_path / "provider")
    client = _client(tmp_path, installation)
    request = _request(action_id="client-forgery")
    approval = _approval(installation, request)
    fabricated = _fabricated_receipt(installation, request)

    def forged(*_args, **_kwargs) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            [], 0, stdout=_canonical(fabricated) + b"\n", stderr=b"")

    monkeypatch.setattr(
        terminal_truth.ExpandedRouteProviderClient,
        "_run_provider", staticmethod(forged))
    with pytest.raises(
        terminal_truth.TerminalTruthError,
        match="seal|authentic",
    ) as failure:
        client.authorize(request, approval)
    assert failure.value.code == "provider-authentication"


def test_orchestrator_client_rejects_receipt_from_other_provider_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted = _install(tmp_path / "trusted")
    foreign = _install(tmp_path / "foreign")
    client = _client(tmp_path, trusted)
    request = _request(action_id="client-cross-provider")
    foreign_receipt = provider._authorize_for_test(
        str(foreign["locator_path"]), request,
        _approval(foreign, request), now=NOW)

    def crossed(*_args, **_kwargs) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            [], 0, stdout=_canonical(foreign_receipt) + b"\n", stderr=b"")

    monkeypatch.setattr(
        terminal_truth.ExpandedRouteProviderClient,
        "_run_provider", staticmethod(crossed))
    with pytest.raises(terminal_truth.TerminalTruthError) as failure:
        client.authorize(request, _approval(trusted, request))
    assert failure.value.code == "provider-authentication"


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("timeout", "provider-timeout"),
        ("nonzero", "provider-process"),
        ("malformed", "provider-output"),
        ("protocol", "provider-protocol"),
        ("oversized", "provider-transport"),
    ],
)
def test_orchestrator_client_fails_closed_on_process_and_transport_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    case: str, expected_code: str,
) -> None:
    installation = _install(tmp_path / case)
    client = _client(tmp_path, installation)
    request = _request(action_id=f"client-{case}")
    approval = _approval(installation, request)

    def failed(*_args, **_kwargs) -> subprocess.CompletedProcess[bytes]:
        if case == "timeout":
            raise subprocess.TimeoutExpired(["provider"], 10)
        if case == "nonzero":
            return subprocess.CompletedProcess(
                [], 2, stdout=b"", stderr=b'{"error":"closed"}\n')
        if case == "malformed":
            return subprocess.CompletedProcess(
                [], 0, stdout=b"not-json\n", stderr=b"")
        if case == "protocol":
            mismatched = _fabricated_receipt(
                installation, request, protocol="provider/v0")
            return subprocess.CompletedProcess(
                [], 0, stdout=_canonical(mismatched) + b"\n", stderr=b"")
        return subprocess.CompletedProcess(
            [], 0,
            stdout=b"x" * (
                terminal_truth._EXPANDED_ROUTE_PROVIDER_MAX_OUTPUT_BYTES + 1),
            stderr=b"",
        )

    monkeypatch.setattr(
        terminal_truth.ExpandedRouteProviderClient,
        "_run_provider", staticmethod(failed))
    with pytest.raises(terminal_truth.TerminalTruthError) as failure:
        client.authorize(request, approval)
    assert failure.value.code == expected_code


def test_orchestrator_client_fails_closed_if_protected_package_changes(
    tmp_path: Path,
) -> None:
    installation = _install(tmp_path / "provider")
    client = _client(tmp_path, installation)
    package = Path(str(installation["package_path"]))
    package.write_bytes(package.read_bytes() + b" ")
    os.chmod(package, 0o600)

    with pytest.raises(terminal_truth.TerminalTruthError) as failure:
        client.authorize(
            _request(action_id="client-provenance-change"),
            _approval(
                installation,
                _request(action_id="client-provenance-change"),
            ),
        )
    assert failure.value.code == "provider-provenance"


def test_copied_or_reconstructed_provider_mapping_has_no_live_authority(
    tmp_path: Path,
) -> None:
    installation = _install(tmp_path / "provider")
    client = _client(tmp_path, installation)
    request = _request(action_id="client-live-only")
    current = int(time.time())
    receipt = client.authorize(
        request, _approval(installation, request, now=current))

    client.assert_authenticated(receipt, request)
    with pytest.raises(terminal_truth.TerminalTruthError):
        client.assert_authenticated(dict(receipt), request)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        terminal_truth.ExpandedRouteProviderReceipt(
            dict(receipt), client=client,
            request_fingerprint=hashlib.sha256(_canonical(request)).hexdigest(),
            token=object(),
        )
