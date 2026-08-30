"""Exact-candidate integration proof for the complete R-0002 AC7 surface.

The medium join consumes the exception-aware high gate.  It therefore keeps
the two accepted high-severity exceptions explicit and never upgrades their
affected rows to independently-green evidence.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module", autouse=True)
def retained_r0002_candidate(tmp_path_factory):
    global ROOT
    original = ROOT
    original_repository_root = remediation_trace._REPOSITORY_ROOT
    original_priced_debt_spec = remediation_trace._PRICED_DEBT_SPEC
    workspace = tmp_path_factory.mktemp("m2-r0002") / "repository"
    subprocess.run(["git", "clone", "--quiet", "--no-hardlinks",
                    str(original), str(workspace)], check=True)
    subprocess.run(["git", "checkout", "-q", "86c7f74"], cwd=workspace,
                   check=True)
    ROOT = workspace
    remediation_trace._REPOSITORY_ROOT = workspace
    remediation_trace._PRICED_DEBT_SPEC = workspace / "specs" / "spec.md"
    try:
        yield
    finally:
        ROOT = original
        remediation_trace._REPOSITORY_ROOT = original_repository_root
        remediation_trace._PRICED_DEBT_SPEC = original_priced_debt_spec
sys.path.insert(0, str(ROOT / "taskplane"))

from taskplane import dashboard, remediation_trace, text_runtime  # noqa: E402
from taskplane import taskplane_lite as tp  # noqa: E402
from taskplane.delivery_ports import TrustedGitInspector  # noqa: E402
from taskplane.host_native import HostSurfaceSnapshot  # noqa: E402


LEAF_COMMITS = {
    "M2-A": "1b298defe833f26bf943394f1198993dbd8379bb",
    "M2-B": "647334358ffabc88afde5f37a96b4e3d849dc8d8",
    "M2-C": "f0ae6d653b2b57e6cb0104dc776d96064b268ddc",
    "M2-D": "5347c268465bfa5c170558fd0907dea609e30e22",
    "M2-E": "3721032909baef8bc25853f464193abd64f593f5",
    "MX-DOCS-ARCH": "365876a3278c28f7555550d9c246926041e6931b",
}
AC7_FINDINGS = {
    "M-01": "M2-A", "M-05": "M2-A", "M-08": "M2-A",
    "M-09": "M2-A", "M-10": "M2-B", "M-11": "M2-B",
    "M-12": "M2-C", "M-13": "M2-C", "M-16": "M2-D",
    "M-20": "M2-B", "M-21": "M2-C", "M-22": "MX-DOCS-ARCH",
    "M-23": "M2-C", "M-25": "M2-E",
    "L-03": "M2-A", "L-05": "M2-C", "L-07": "M2-C",
    "L-08": "M2-C", "L-09": "M2-C",
}
H1_EXCEPTION_IDS = {
    "H-03", "H-04", "H-05", "H-06", "H-07", "H-08", "H-14",
    "H-15", "H-19", "H-22", "H-26", "H-30", "H-34",
}
H3_EXCEPTION_IDS = {"H-23", "H-25"}
EXCEPTION_PATHS = {
    "H1-I-selector-receipt-authority": (
        "design/backlog/r0002-h1i-selector-receipt-authority.md",
        "f705432f38a95f663bdaa3678ed42e2e1ed7c7e2bc5e03d5b439cb20dcdfe890",
    ),
    "H3-C-retention-gaps": (
        "design/backlog/r0002-h3c-retention-exceptions.md",
        "bd40d659569919abe09b797cf7df66c81f7e1e73ad425efc48dc417f52d550a5",
    ),
}

EXACT_CANDIDATE_INPUTS = (
    ".codex-plugin/plugin.json",
    ".em-review/remediation/high-gate/disposition.md",
    ".em-review/remediation/high-gate/results.json",
    "PRIVACY.md",
    "README.md",
    "design/backlog/r0002-h1i-selector-receipt-authority.md",
    "design/backlog/r0002-h3c-retention-exceptions.md",
    "design/contract.json",
    "docs/cli-reference.md",
    "docs/loop-design.md",
    "docs/onboarding.md",
    "plan/tasks.json",
    "skills/tp-help/SKILL.md",
    "specs/spec.md",
    "taskplane/dashboard.py",
    "taskplane/locales/ar.json",
    "taskplane/locales/en.json",
    "taskplane/remediation_trace.py",
    "taskplane/taskplane_lite.py",
    "taskplane/text_runtime.py",
    "taskplane/tests/test_em_m2_concurrency.py",
    "taskplane/tests/test_em_m2_dashboard.py",
    "taskplane/tests/test_em_m2_debt.py",
    "taskplane/tests/test_em_m2_docs.py",
    "taskplane/tests/test_em_m2_integration.py",
    "taskplane/tests/test_em_m2_privacy.py",
    "taskplane/tests/test_em_mx_loop_docs.py",
    "taskplane/tests/test_review_routing.py",
    "taskplane/tp.py",
)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/usr/bin/git", *args], cwd=ROOT, text=True, encoding="utf-8",
        errors="replace", capture_output=True, check=False,
    )


def _ancestry_errors(candidate_sha: str, leaves=LEAF_COMMITS) -> list[str]:
    errors: list[str] = []
    for task, revision in leaves.items():
        result = _git("merge-base", "--is-ancestor", revision, candidate_sha)
        if result.returncode != 0:
            errors.append(f"{task} leaf {revision} is not candidate ancestry")
    return errors


def _candidate_errors(candidate_sha: str, status: str) -> list[str]:
    errors = _ancestry_errors(candidate_sha)
    if status:
        errors.append("AC7 candidate is not clean")
    if not re.fullmatch(r"[0-9a-f]{40,64}", candidate_sha):
        errors.append("AC7 candidate identity is invalid")
    return errors


def _finding_errors(design: dict) -> list[str]:
    rows = design.get("finding_map") or []
    mapped = {
        row.get("id"): row for row in rows
        if isinstance(row, dict) and row.get("id") in AC7_FINDINGS
    }
    errors: list[str] = []
    if set(mapped) != set(AC7_FINDINGS):
        errors.append("AC7 finding inventory is incomplete")
    for finding_id, task in AC7_FINDINGS.items():
        row = mapped.get(finding_id) or {}
        if row.get("task") != task:
            errors.append(f"{finding_id} is not owned by {task}")
        evidence = str(row.get("evidence") or "")
        if "taskplane/tests/test_em_" not in evidence or "::test_" not in evidence:
            errors.append(f"{finding_id} has no focused evidence selector")
    return errors


def _dashboard_snapshot(candidate_sha: str) -> HostSurfaceSnapshot:
    statuses = ("complete", "pending", "degraded", "failed")
    values = {
        name: {
            "status": statuses[index % len(statuses)],
            "provenance": f"git:{candidate_sha}:{name}",
            "summary": f"AC7 {name} evidence",
            "items": [],
        }
        for index, name in enumerate(dashboard.HOST_DASHBOARD_COMPONENTS)
    }
    return HostSurfaceSnapshot.create(
        workflow_id="R-0002", run_id="M2-I", target="taskplane",
        revision=candidate_sha, sequence=2, stage="medium-integration",
        state="awaiting_approval", values=values,
        evidence=(f"git:{candidate_sha}",),
        safe_actions=("Retry evidence", "Inspect diagnostics"),
    )


def _dashboard_errors(
        markup: str, localized: str, card: str, full_text: str) -> list[str]:
    errors: list[str] = []
    required = (
        'data-state="success" role="status"',
        'data-state="pending" role="status"',
        'data-state="warning" role="status"',
        'data-state="failure" role="alert"',
        "Component state: failed",
        "Recovery action: Retry evidence",
        "--tp-success", "--tp-pending", "--tp-warning", "--tp-failure",
    )
    for fragment in required:
        if fragment not in markup:
            errors.append(f"dashboard/text edge is missing: {fragment}")
    for fragment in (
        'lang="ar-EG" dir="auto"',
        'aria-label="لوحة سير عمل Taskplane"',
        "حالة المكوّن: فشل",
        "إجراء الاسترداد: Retry evidence",
    ):
        if fragment not in localized:
            errors.append(f"localized dashboard edge is missing: {fragment}")
    if f'<span class="sr">{full_text}</span>' not in card:
        errors.append("grapheme truncation lost full accessible text")
    if ("👨‍👩‍👧‍👦…" not in card or "👨…" in card or
            "👨‍…" in card):
        errors.append("visible truncation split a grapheme cluster")
    if dashboard._msg("n_findings", locale="ar-EG", n=2) != "2 نتيجتان":
        errors.append("locale plural routing lost the Arabic two category")
    return errors


def _privacy_errors(mode: dict, notice: str) -> list[str]:
    errors: list[str] = []
    expected = ("external", True, "shared-config-unconfirmed")
    if (mode.get("store"), mode.get("private"), mode.get("source")) != expected:
        errors.append("repository configuration silently selected shared storage")
    if "tp share set shared" not in str(mode.get("notice") or ""):
        errors.append("explicit shared-storage authority is not presented")
    lowered = notice.lower()
    obsolete = (
        "collects nothing", "shares data with no one",
        "no network requests initiated",
    )
    if any(claim in lowered for claim in obsolete):
        errors.append("privacy notice retains a categorical false denial")
    required = (
        "repository urls", "actor or approval", "24-hour retention",
        "remote repository", "pull request", "local `git`", "credentials",
        "request and connection metadata", "repository host's procedures",
    )
    for fragment in required:
        if fragment not in lowered:
            errors.append(f"privacy disclosure is incomplete: {fragment}")
    return errors


def _docs_errors(blobs: dict[str, bytes], generated: str) -> list[str]:
    errors: list[str] = []
    text = {path: raw.decode("utf-8") for path, raw in blobs.items()}
    if generated != text["docs/cli-reference.md"]:
        errors.append("generated CLI reference drifted from checked-in truth")
    required_generated = (
        "`outcome` (required; choices: `pass`, `fail`, `unavailable`)",
        "`decision` (required; choices: `approve`, `changes`)",
    )
    for fragment in required_generated:
        if fragment not in generated:
            errors.append(f"generated CLI semantics are missing: {fragment}")
    readme = text["README.md"]
    for fragment in (
        "one consolidated pre-implementation authorization",
        "final human sign-off", "CPython 3.10 or newer",
        "[docs/cli-reference.md](docs/cli-reference.md)",
    ):
        if fragment not in readme:
            errors.append(f"primary journey is incomplete: {fragment}")
    try:
        market = json.loads(text[".codex-plugin/plugin.json"])["interface"][
            "longDescription"]
    except (KeyError, TypeError, ValueError):
        market = ""
    for fragment in (
        "complete 26-lens disposition", "evidence-selected deep reviewers",
        "at most one light sweep", "light or n/a rationale disclosed",
    ):
        if fragment not in market:
            errors.append(f"marketplace review truth is incomplete: {fragment}")
    onboarding = text["docs/onboarding.md"]
    if "four context docs" not in onboarding or "three context docs" in onboarding:
        errors.append("onboarding context model is inconsistent")
    for name in ("current-state.md", "product.md", "tech-stack.md", "workflow.md"):
        if name not in onboarding:
            errors.append(f"onboarding omits {name}")
    help_text = text["skills/tp-help/SKILL.md"]
    if "What's new in v2.16" in help_text or \
            ".codex-plugin/plugin.json" not in help_text or \
            "CHANGELOG.md" not in help_text:
        errors.append("built-in help is not version-neutral/release-derived")
    return errors


def _call_path(call: ast.Call) -> str:
    current: ast.expr = call.func
    parts: list[str] = []
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _concurrency_errors(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ["publication concurrency proof is not valid Python"]
    method = next(
        (member for node in tree.body if isinstance(node, ast.ClassDef)
         and node.name == "TestSelectiveReviewKernel"
         for member in node.body if isinstance(member, ast.FunctionDef)
         and member.name ==
         "test_concurrent_collect_loser_never_publishes_authoritative_views"),
        None,
    )
    if method is None:
        return ["publication concurrency proof is unavailable"]
    calls = [_call_path(node) for node in ast.walk(method)
             if isinstance(node, ast.Call)]
    errors: list[str] = []
    if any(path == "sleep" or path.endswith(".sleep") for path in calls):
        errors.append("publication proof is sleep-ordered")
    for required in (
        "winner_publish_entered.wait", "loser_lock_attempted.wait",
        "winner_publish_release.wait", "real_file_lock",
        "real_acquire_reservation", "real_release_reservation",
    ):
        if required not in calls:
            errors.append(f"publication proof misses event/lock edge: {required}")
    if calls.count("threading.Event") < 8:
        errors.append("publication proof has incomplete event observations")
    return errors


def _run_concurrency_case() -> list[str]:
    from taskplane.tests.test_review_routing import (  # noqa: PLC0415
        TestSelectiveReviewKernel,
    )

    case = TestSelectiveReviewKernel(methodName=
        "test_concurrent_collect_loser_never_publishes_authoritative_views")
    result = unittest.TestResult()
    case.run(result)
    return [detail for _test, detail in [*result.failures, *result.errors]] + [
        f"unexpected skipped concurrency proof: {result.skipped}"
        for _ in [0] if result.skipped
    ]


def _debt_errors(trace: dict) -> list[str]:
    try:
        verified = remediation_trace.verify_priced_debt_trace(trace)
    except remediation_trace.RemediationTraceError as exc:
        return [str(exc)]
    errors: list[str] = []
    if verified.get("required_debt_ids") != ["D-1301", "D-1302", "D-1303"]:
        errors.append("priced debt inventory is incomplete")
    if verified.get("record_count") != 3:
        errors.append("priced debt row count is not fixed")
    for record in verified.get("records", []):
        if not str(record.get("owner") or "").startswith("owner:"):
            errors.append("priced debt owner is not governed")
        if set(record.get("reentry_trigger") or {}) != {
                "signal", "threshold", "action"}:
            errors.append("priced debt re-entry trigger is incomplete")
        if record["now_cost"]["unit"] != record["later_cost"]["unit"]:
            errors.append("priced debt costs are not comparable")
    return errors


def _mx_record(document: str) -> dict:
    section = document.split(
        "### Decision record: `D-LOOP-ENGINE-OWNERSHIP/v1`", 1)[1]
    match = re.search(r"```json\n(?P<record>.*?)\n```", section, re.DOTALL)
    if match is None:
        return {}
    return json.loads(match.group("record"))


def _mx_errors(document: str) -> list[str]:
    errors: list[str] = []
    for fragment in (
        "tp.py loop submit [pass|fail|unavailable]",
        "Only an EVALUATE worker may submit it",
        "`unavailable` cannot turn such a failure into progress",
        "Accepted decision `D-LOOP-STAGE-MIGRATION`",
        "There is no dual-authority window",
        "A. Retained-source, receipt-verified one-way conversion (selected)",
        "B. Bidirectional dual-write with reverse migration",
        "C. In-place singleton schema upgrade",
        "Both conditions are required",
    ):
        if fragment not in document:
            errors.append(f"shared MX decision evidence is missing: {fragment}")
    try:
        record = _mx_record(document)
    except (IndexError, KeyError, TypeError, ValueError):
        record = {}
    if record.get("schema") != "taskplane.decision/v1" or \
            record.get("status") != "ACTIVE" or \
            record.get("owner") != "taskplane-loop-engine":
        errors.append("loop ownership decision is not active authority")
    if record.get("selected_alternative") != "A-host-orchestrator-lifecycle":
        errors.append("loop ownership selected alternative drifted")
    if record.get("authority_owners") != {
        "governed_state_transitions_gates_and_audit": "taskplane-loop-engine",
        "native_worker_dispatch_start_stop_and_wait": "host-orchestrator",
    }:
        errors.append("loop/host authority ownership drifted")
    alternatives = record.get("alternatives") or []
    if len(alternatives) < 3 or sum(
            row.get("disposition") == "SELECTED" for row in alternatives) != 1:
        errors.append("loop ownership alternatives are incomplete")
    return errors


def _exception_errors(results: dict, documents: dict[str, bytes]) -> list[str]:
    errors: list[str] = []
    if results.get("strict_ac5_status") != "not-satisfied" or \
            results.get("disposition") != \
            "proceed-under-explicit-human-exceptions":
        errors.append("medium join relabelled the exception-aware high gate")
    counts = results.get("counts") or {}
    if counts.get("accepted_exception") != 15 or \
            counts.get("independently_green") != 19:
        errors.append("high-gate exception counts drifted")
    records = {row.get("id"): row for row in
               results.get("exception_records") or []}
    for exception_id, (path, expected_sha) in EXCEPTION_PATHS.items():
        row = records.get(exception_id) or {}
        raw = documents.get(path)
        if raw is None or _sha256(raw) != expected_sha or \
                row.get("sha256") != expected_sha or \
                row.get("independently_green") is not False:
            errors.append(f"{exception_id} authority is unavailable or relabelled")
    expected = {
        **{finding: "H1-I-selector-receipt-authority"
           for finding in H1_EXCEPTION_IDS},
        **{finding: "H3-C-retention-gaps" for finding in H3_EXCEPTION_IDS},
    }
    rows = {row.get("finding_id"): row for row in results.get("results") or []}
    for finding_id, exception_id in expected.items():
        row = rows.get(finding_id) or {}
        if row.get("status") != "accepted-exception" or \
                row.get("independent") is not False or \
                row.get("exception_id") != exception_id:
            errors.append(f"{finding_id} was relabelled independently green")
    return errors


def test_ac7_user_facing_truth_closes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inspector = TrustedGitInspector()
    before = inspector.snapshot(ROOT, evidence_paths=EXACT_CANDIDATE_INPUTS)
    candidate_sha = before.head_sha
    status = _git("status", "--porcelain=v1", "--untracked-files=all").stdout.strip()
    assert _candidate_errors(candidate_sha, status) == []

    blobs: dict[str, bytes] = {}
    assert set(before.evidence_sha256) == set(EXACT_CANDIDATE_INPUTS)
    for relative, expected in before.evidence_sha256.items():
        raw = (ROOT / relative).read_bytes()
        assert _sha256(raw) == expected
        retained = subprocess.run(
            [before.git_executable, "show", f"{candidate_sha}:{relative}"],
            cwd=ROOT, capture_output=True, check=True,
        ).stdout
        assert raw == retained
        blobs[relative] = raw

    design = json.loads(blobs["design/contract.json"])
    assert _finding_errors(design) == []

    projection = dashboard.native_dashboard_projection(
        _dashboard_snapshot(candidate_sha), host="codex")
    markup = dashboard.render_native_dashboard_surface(projection, locale="en")
    localized = dashboard.render_native_dashboard_surface(
        projection, locale="ar-EG")
    family = "👨‍👩‍👧‍👦"
    full_text = "x" * 259 + family + " after"
    card = dashboard._compact_card({
        "severity": "med", "domain": "i18n", "title": "cluster",
        "scenario": full_text, "fix": "e\u0301" * 221,
    })
    assert _dashboard_errors(markup, localized, card, full_text) == []

    workspace = tmp_path / "repo-controlled-workspace"
    workspace.mkdir()
    subprocess.run(["/usr/bin/git", "init", "-q"], cwd=workspace, check=True)
    shared = workspace / ".taskplane-kb"
    shared.mkdir()
    (shared / "config.json").write_text(json.dumps({
        "plan": "team", "store": "repo", "private": False,
        "sharing_confirmed": True,
    }), encoding="utf-8")
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "private-home"))
    monkeypatch.delenv("TASKPLANE_STORE", raising=False)
    private_mode = tp.get_mode(str(workspace))
    assert _privacy_errors(private_mode, blobs["PRIVACY.md"].decode()) == []
    assert Path(tp.store_root(str(workspace))).is_relative_to(
        tmp_path / "private-home")
    shared_mode = tp.set_mode(str(workspace), private=False)
    assert (shared_mode["store"], shared_mode["private"],
            shared_mode["source"]) == ("repo", False, "shared-config")
    assert Path(tp.store_root(str(workspace))) == shared

    completed = subprocess.run(
        [sys.executable, "taskplane/tp.py", "help", "--md"], cwd=ROOT,
        env={**dict(os.environ), "PYTHONDONTWRITEBYTECODE": "1"},
        text=True, encoding="utf-8", errors="replace", capture_output=True,
        check=True,
    )
    assert _docs_errors(blobs, completed.stdout) == []

    concurrency_source = blobs[
        "taskplane/tests/test_review_routing.py"].decode("utf-8")
    assert _concurrency_errors(concurrency_source) == []
    assert _run_concurrency_case() == []

    authority = remediation_trace.priced_debt_authority()
    debt_trace = remediation_trace.build_priced_debt_trace(
        records=list(reversed(authority["records"])))
    assert _debt_errors(debt_trace) == []
    assert debt_trace["authority"]["content_sha256"] == \
        _sha256(blobs["specs/spec.md"])

    assert _mx_errors(blobs["docs/loop-design.md"].decode()) == []
    high_results = json.loads(
        blobs[".em-review/remediation/high-gate/results.json"])
    assert _exception_errors(high_results, blobs) == []

    unchanged = inspector.assert_unchanged(before)
    assert unchanged.head_sha == candidate_sha
    assert unchanged.tree_sha == before.tree_sha


def test_ac7_candidate_leaf_or_cleanliness_mutations_fail_closed() -> None:
    candidate_sha = _git("rev-parse", "HEAD").stdout.strip()
    assert _candidate_errors(candidate_sha, "?? unreviewed.txt") == [
        "AC7 candidate is not clean"]
    severed = {**LEAF_COMMITS, "M2-E": "0" * 40}
    errors = _ancestry_errors(candidate_sha, severed)
    assert any("M2-E leaf" in error for error in errors)


def test_ac7_dashboard_text_and_privacy_mutations_fail_closed() -> None:
    candidate_sha = _git("rev-parse", "HEAD").stdout.strip()
    projection = dashboard.native_dashboard_projection(
        _dashboard_snapshot(candidate_sha), host="codex")
    markup = dashboard.render_native_dashboard_surface(projection, locale="en")
    localized = dashboard.render_native_dashboard_surface(
        projection, locale="ar-EG")
    family = "👨‍👩‍👧‍👦"
    full_text = "x" * 259 + family + " after"
    card = dashboard._compact_card({
        "severity": "med", "domain": "i18n", "title": "cluster",
        "scenario": full_text, "fix": "safe",
    })
    misleading = markup.replace(
        'data-state="failure" role="alert"',
        'data-state="success" role="status"')
    assert any("failure" in error for error in
               _dashboard_errors(misleading, localized, card, full_text))

    private = {
        "store": "repo", "private": False, "source": "shared-config",
        "notice": "sharing inherited",
    }
    notice = (ROOT / "PRIVACY.md").read_text(encoding="utf-8") + \
        "\nTaskplane shares data with no one."
    errors = _privacy_errors(private, notice)
    assert any("silently selected" in error for error in errors)
    assert any("categorical false denial" in error for error in errors)


def test_ac7_docs_concurrency_and_debt_mutations_fail_closed() -> None:
    paths = (
        ".codex-plugin/plugin.json", "README.md", "docs/cli-reference.md",
        "docs/onboarding.md", "skills/tp-help/SKILL.md",
    )
    blobs = {path: (ROOT / path).read_bytes() for path in paths}
    generated = blobs["docs/cli-reference.md"].decode().replace(
        "`pass`, `fail`, `unavailable`", "`pass`, `fail`", 1)
    assert any("generated CLI" in error for error in
               _docs_errors(blobs, generated))

    concurrency = (ROOT / "taskplane/tests/test_review_routing.py").read_text(
        encoding="utf-8")
    sleep_ordered = concurrency.replace(
        "self.assertTrue(loser_lock_attempted.wait(5))",
        "time.sleep(5)", 1)
    errors = _concurrency_errors(sleep_ordered)
    assert "publication proof is sleep-ordered" in errors
    assert any("loser_lock_attempted.wait" in error for error in errors)

    authority = remediation_trace.priced_debt_authority()
    trace = remediation_trace.build_priced_debt_trace(
        records=authority["records"])
    trace["records"][0]["owner"] = "owner:caller-reminted"
    assert _debt_errors(trace)


def test_ac7_mx_and_high_exception_mutations_fail_closed() -> None:
    loop_design = (ROOT / "docs/loop-design.md").read_text(encoding="utf-8")
    severed = loop_design.replace(
        '"native_worker_dispatch_start_stop_and_wait": "host-orchestrator"',
        '"native_worker_dispatch_start_stop_and_wait": "taskplane-loop-engine"',
        1,
    )
    assert any("authority ownership drifted" in error
               for error in _mx_errors(severed))

    results = json.loads((
        ROOT / ".em-review/remediation/high-gate/results.json"
    ).read_text(encoding="utf-8"))
    relabelled = copy.deepcopy(results)
    row = next(item for item in relabelled["results"]
               if item["finding_id"] == "H-23")
    row.update({"status": "independently-green", "independent": True})
    documents = {
        path: (ROOT / path).read_bytes()
        for path, _expected in EXCEPTION_PATHS.values()
    }
    errors = _exception_errors(relabelled, documents)
    assert "H-23 was relabelled independently green" in errors
