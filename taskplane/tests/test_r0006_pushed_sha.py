"""R-0006 P1: pushed-green means one fetched, exact, checked commit."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci_evals.py"


def _load_ci_evals():
    spec = importlib.util.spec_from_file_location("r0006_ci_evals", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EVALS = _load_ci_evals()
SHA = "a" * 40
OTHER_SHA = "b" * 40
EXPECTED_REQUIRED_CHECKS = (
    "tests (python 3.12)",
    "R-0006 graph + CLI contracts",
    "zero-token corpus (credential-empty, no-egress)",
)


def _receipts(sha: str = SHA, conclusion: str = "success") -> list[dict]:
    return [
        {"name": name, "sha": sha, "conclusion": conclusion}
        for name in EXPECTED_REQUIRED_CHECKS
    ]


def _proof(**changes):
    facts = {
        "fetch_receipt": {
            "performed": True,
            "remote": "origin",
            "ref": "refs/remotes/origin/main",
            "ok": True,
        },
        "head_sha": SHA,
        "remote_sha": SHA,
        "checked_sha": SHA,
        "ahead_count": 0,
        "behind_count": 0,
        "receipts": _receipts(),
    }
    facts.update(changes)
    return EVALS.classify_ci_commit_proof(**facts)


def test_exact_fetched_sha_and_required_receipts_is_pushed_green():
    assert EVALS.PUSHED_GREEN_REQUIRED_CHECKS == EXPECTED_REQUIRED_CHECKS
    proof = _proof()

    assert proof["schema"] == "taskplane.ci-commit-proof/v1"
    assert proof["status"] == "pushed_green"
    assert proof["errors"] == []
    assert proof["head_sha"] == proof["remote_sha"] == proof["checked_sha"]
    assert proof["ahead_count"] == proof["behind_count"] == 0
    assert [row["name"] for row in proof["required_checks"]] == list(
        EXPECTED_REQUIRED_CHECKS
    )


@pytest.mark.parametrize(
    ("changes", "error_fragment"),
    [
        ({"ahead_count": 1}, "ahead"),
        ({"behind_count": 1}, "behind"),
        ({"head_sha": OTHER_SHA}, "HEAD"),
        ({"remote_sha": OTHER_SHA}, "origin/main"),
        ({"fetch_receipt": {"performed": False}}, "explicit fetch"),
        ({"receipts": _receipts(OTHER_SHA)}, "receipt SHA"),
        ({"receipts": _receipts(conclusion="failure")}, "not successful"),
        ({"receipts": _receipts()[:-1]}, "missing required check"),
        ({"checked_sha": OTHER_SHA}, "checked_sha"),
        ({"ahead_count": 1, "behind_count": 1}, "ahead"),
        ({"receipts": _receipts() + [_receipts()[0]]}, "duplicate"),
        ({"receipts": _receipts() + [{
            "name": "untrusted check", "sha": SHA, "conclusion": "success",
        }]}, "unknown"),
        ({"receipts": "not a receipt list"}, "JSON list"),
    ],
)
def test_ahead_behind_stale_local_only_and_receipt_drift_never_push_green(
    changes, error_fragment
):
    proof = _proof(**changes)

    assert proof["status"] in {"local_green", "refused"}
    assert proof["status"] != "pushed_green"
    assert any(error_fragment in row for row in proof["errors"]), proof


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        text=True,
        encoding="utf-8",
        capture_output=True,
    ).stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True,
        text=True, encoding="utf-8", capture_output=True,
    )
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "taskplane-test")
    _git(repo, "config", "user.email", "taskplane@example.invalid")
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-qm", "initial")
    _git(repo, "branch", "-M", "main")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "-u", "origin", "main")
    return repo, _git(repo, "rev-parse", "HEAD")


def test_public_cli_fetches_then_emits_exact_sha_proof(tmp_path):
    repo, sha = _repository(tmp_path)
    receipts = tmp_path / "receipts.json"
    receipts.write_text(
        json.dumps(_receipts(sha), sort_keys=True), encoding="utf-8"
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--prove-pushed-sha",
            "--checked-sha",
            sha,
            "--check-receipts",
            str(receipts),
            "--root",
            str(repo),
            "--json",
        ],
        text=True,
        encoding="utf-8",
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    proof = json.loads(result.stdout)
    assert proof["status"] == "pushed_green"
    assert proof["fetch_receipt"]["performed"] is True
    assert proof["fetch_receipt"]["ok"] is True
    assert proof["fetch_receipt"]["ref"] == "refs/remotes/origin/main"


def test_public_cli_refuses_to_relabel_a_local_commit_as_pushed(tmp_path):
    repo, pushed_sha = _repository(tmp_path)
    (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "local only")
    local_sha = _git(repo, "rev-parse", "HEAD")
    receipts = tmp_path / "receipts.json"
    receipts.write_text(
        json.dumps(_receipts(local_sha), sort_keys=True), encoding="utf-8"
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--prove-pushed-sha",
            "--checked-sha",
            local_sha,
            "--check-receipts",
            str(receipts),
            "--root",
            str(repo),
            "--json",
        ],
        text=True,
        encoding="utf-8",
        capture_output=True,
    )

    assert result.returncode == 1
    proof = json.loads(result.stdout)
    assert proof["status"] == "local_green"
    assert proof["remote_sha"] == pushed_sha
    assert proof["ahead_count"] == 1
    assert proof["behind_count"] == 0


def test_public_cli_refreshes_a_stale_equal_tracking_ref_before_classifying(
    tmp_path,
):
    repo, stale_sha = _repository(tmp_path)
    remote = tmp_path / "remote.git"
    publisher = tmp_path / "publisher"
    subprocess.run(
        ["git", "clone", "-q", str(remote), str(publisher)], check=True,
        text=True, encoding="utf-8", capture_output=True,
    )
    _git(publisher, "config", "user.name", "taskplane-test")
    _git(publisher, "config", "user.email", "taskplane@example.invalid")
    _git(publisher, "checkout", "-q", "-B", "main", "origin/main")
    (publisher / "tracked.txt").write_text("remote advanced\n", encoding="utf-8")
    _git(publisher, "commit", "-qam", "remote advanced")
    _git(publisher, "push", "-q", "origin", "main")
    remote_sha = _git(publisher, "rev-parse", "HEAD")
    assert _git(repo, "rev-parse", "refs/remotes/origin/main") == stale_sha

    receipts = tmp_path / "receipts.json"
    receipts.write_text(json.dumps(_receipts(stale_sha)), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--prove-pushed-sha",
         "--checked-sha", stale_sha, "--check-receipts", str(receipts),
         "--root", str(repo), "--json"],
        text=True, encoding="utf-8", capture_output=True,
    )

    assert result.returncode == 1
    proof = json.loads(result.stdout)
    assert proof["status"] == "local_green"
    assert proof["remote_sha"] == remote_sha
    assert proof["behind_count"] == 1
    assert any("origin/main" in row for row in proof["errors"])


def test_public_cli_does_not_trust_a_cached_ref_when_fetch_fails(tmp_path):
    repo, sha = _repository(tmp_path)
    remote = tmp_path / "remote.git"
    unavailable = tmp_path / "remote-unavailable.git"
    remote.rename(unavailable)
    assert _git(repo, "rev-parse", "refs/remotes/origin/main") == sha

    receipts = tmp_path / "receipts.json"
    receipts.write_text(json.dumps(_receipts(sha)), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--prove-pushed-sha",
         "--checked-sha", sha, "--check-receipts", str(receipts),
         "--root", str(repo), "--json"],
        text=True, encoding="utf-8", capture_output=True,
    )

    assert result.returncode == 1
    proof = json.loads(result.stdout)
    assert proof["status"] == "refused"
    assert proof["fetch_receipt"]["ok"] is False
    assert proof["remote_sha"] is None
    assert any("fetch failed" in row for row in proof["errors"])
