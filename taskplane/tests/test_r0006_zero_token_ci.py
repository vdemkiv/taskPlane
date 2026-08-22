"""R-0006 V1: the frozen corpus is deterministic, credential-empty CI."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SCRIPT = ROOT / "scripts" / "ci_evals.py"
CORPUS_COMMAND = "python3 -B scripts/ci_evals.py --corpus"


def _job(source: str, job: str, next_job: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(job)}:\n(?P<body>.*?)(?=^  {re.escape(next_job)}:)",
        source,
    )
    assert match, f"workflow job {job!r} is missing"
    return match.group("body")


def test_push_and_pull_request_run_a_dedicated_zero_token_corpus_gate():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "push:\n    branches: [main]" in source
    assert "pull_request:\n    branches: [main]" in source

    job = _job(source, "zero-token-corpus", "wave3-contracts")
    assert "name: zero-token corpus (credential-empty, no-egress)" in job
    assert "env -i" in job
    assert CORPUS_COMMAND in job
    assert "secrets." not in job
    assert "OPENAI" not in job
    assert "ANTHROPIC" not in job


def test_no_egress_guard_loads_before_intentional_socket_and_dns_probes():
    source = WORKFLOW.read_text(encoding="utf-8")
    job = _job(source, "zero-token-corpus", "wave3-contracts")

    guard = job.index("sitecustomize.py")
    socket_probe = job.index("socket.socket")
    dns_probe = job.index("socket.getaddrinfo")
    corpus = job.index(CORPUS_COMMAND)
    assert guard < socket_probe < corpus
    assert guard < dns_probe < corpus
    assert "sitecustomize.ATTEMPTS" in job
    assert 'PATH="$PATH"' in job
    assert 'HOME="$' in job
    assert "LANG=C.UTF-8" in job
    assert "LC_ALL=C.UTF-8" in job
    assert "PYTHONPATH=" in job


def test_corpus_output_is_byte_compared_and_corrupt_expected_must_fail():
    source = WORKFLOW.read_text(encoding="utf-8")
    job = _job(source, "zero-token-corpus", "wave3-contracts")

    assert job.count(CORPUS_COMMAND) >= 3
    assert re.search(r"\bcmp\b", job)
    assert "expected.json" in job
    assert "corrupt" in job.lower()
    assert re.search(r"if .*ci_evals\.py --corpus", job)


def test_valid_corpus_is_already_deterministic_in_a_clean_process():
    first = subprocess.run(
        [sys.executable, str(SCRIPT), "--corpus"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    second = subprocess.run(
        [sys.executable, str(SCRIPT), "--corpus"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout

    assert first == second


def test_embedded_guard_is_preloaded_under_exact_env_and_blocks_every_probe(
    tmp_path,
):
    source = WORKFLOW.read_text(encoding="utf-8")
    job = _job(source, "zero-token-corpus", "wave3-contracts")
    match = re.search(
        r"(?ms)cat >\"\$guard_dir/sitecustomize\.py\" <<'PY'\n"
        r"(?P<guard>.*?)^          PY$",
        job,
    )
    assert match, "sitecustomize heredoc is not extractable"
    guard_dir = tmp_path / "guard"
    guard_dir.mkdir()
    (guard_dir / "sitecustomize.py").write_text(
        textwrap.dedent(match.group("guard")), encoding="utf-8"
    )
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    clean_env = {
        "PATH": os.environ["PATH"],
        "HOME": str(isolated_home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONPATH": str(guard_dir),
    }
    probe = r"""
import os
import sitecustomize
import socket
import sys

assert "sitecustomize" in sys.modules
expected_env = {"PATH", "HOME", "LANG", "LC_ALL", "PYTHONPATH"}
extras = set(os.environ) - expected_env
# macOS injects this locale key even across env -i.  The production job is
# Ubuntu and retains the exact-set assertion; this local behavioral replay
# permits only that host-owned key and no credential/proxy/model variables.
assert not extras or (sys.platform == "darwin" and
                      extras == {"__CF_USER_TEXT_ENCODING"})
assert expected_env <= set(os.environ)
probes = (
    ("socket.socket", lambda: socket.socket()),
    ("socket.connect", lambda: socket.socket.connect(None, ("example.invalid", 443))),
    ("socket.connect_ex", lambda: socket.socket.connect_ex(None, ("example.invalid", 443))),
    ("socket.create_connection", lambda: socket.create_connection(("example.invalid", 443))),
    ("socket.getaddrinfo", lambda: socket.getaddrinfo("example.invalid", 443)),
)
for label, call in probes:
    try:
        call()
    except sitecustomize.NoEgressError:
        pass
    else:
        raise AssertionError("probe escaped: " + label)
assert sitecustomize.ATTEMPTS == [label for label, _ in probes]
"""
    blocked = subprocess.run(
        [sys.executable, "-B", "-c", probe], env=clean_env,
        text=True, encoding="utf-8", capture_output=True,
    )
    assert blocked.returncode == 0, blocked.stderr

    first = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--corpus"],
        cwd=ROOT, env=clean_env, capture_output=True,
    )
    second = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--corpus"],
        cwd=ROOT, env=clean_env, capture_output=True,
    )
    assert first.returncode == second.returncode == 0
    assert first.stdout + first.stderr == second.stdout + second.stderr


def test_pushed_sha_proof_waits_for_all_required_wave3_checks():
    source = WORKFLOW.read_text(encoding="utf-8")
    job = _job(source, "pushed-sha-proof", "tests-portability")

    assert "needs: [tests, zero-token-corpus, wave3-contracts]" in job
    assert "github.event_name == 'push'" in job
    assert "--prove-pushed-sha" in job
    assert "--checked-sha" in job
    assert "--check-receipts" in job
    assert "${{ github.sha }}" in job
