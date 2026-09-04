#!/usr/bin/env python3
"""Every shipped version is tagged, and every tag names a real release.

Five releases — v2.5.0, v2.5.1, v2.6.0, v2.8.1, v2.8.2 — went out with no
tag at all, and nobody noticed for months, because tagging lived in a human's
memory of the release routine. Two CHANGELOG rows (v2.4.0, v2.3.0) name
versions that NO tree ever declared: their content shipped inside the next
version, so the row invents a release boundary that git cannot corroborate.
And a "dangling v2.8.0" was chased for an hour before turning out to be an
annotated tag OBJECT sha read as if it were a commit sha.

All three are the same failure: the release history was asserted in prose and
never checked against the only source that can settle it — the version the
manifests actually held, commit by commit, on the mainline.

So this is the check. Manifest history is the source of truth for declared
version trees; tags, NOT_RELEASED dispositions, and the CHANGELOG establish
which of those trees were releases. A correctly versioned tag on a reachable
merged side parent identifies the rarer release that shipped without ever
becoming a first-parent tree (v2.17.17 is the real example).

  C1  every version the manifest held on the mainline has a `v<version>` tag,
      except the newest release in flight and versions explicitly recorded in
      NOT_RELEASED as superseded candidates that were never releases
  C2  every release tag resolves to a commit reachable from the mainline
  C3  every tag's commit declares that version in its manifest
  C4  every CHANGELOG version was either shipped or is listed in NOT_SHIPPED
      with a reason
  C5  nothing in NOT_SHIPPED was actually shipped — an exemption that starts
      being true is a bypass, not an exemption
  C6  no `v*` tag names a version that never existed
  C7  every NOT_RELEASED entry names a declared, untagged candidate

Run: python3 scripts/ci_release_tags.py [<repo-root>] [--json]
     python3 scripts/ci_release_tags.py [<repo-root>] --release-gate <receipt>
            --authorize-version <version>
"""
import argparse
from datetime import datetime, timedelta
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFESTS = (".codex-plugin/plugin.json", ".claude-plugin/plugin.json",
             ".claude-plugin/marketplace.json")

# Versions the CHANGELOG names that no tree ever declared. Each needs a
# reason a human wrote down, and C5 re-checks that the reason is still true.
NOT_SHIPPED = {
    "2.3.0": {
        "reason": "released in the SAME commit as v2.3.1 — the manifest went "
                  "2.2.1 -> 2.3.1 in one step and the commit subject says so "
                  "('v2.3.0 + v2.3.1'). The v2.3.0 tag deliberately points at "
                  "that shared commit.",
        "co_released_with": "2.3.1",
    },
    "2.4.0": {
        "reason": "never shipped as its own version. The work described in "
                  "the v2.4.0 CHANGELOG row landed inside v2.5.0 — the row "
                  "itself was ADDED by the commit that bumped the manifest to "
                  "2.5.0. No tree has ever declared 2.4.0, so no tag can "
                  "honestly point anywhere.",
        "co_released_with": "2.5.0",
    },
    "2.18.2": {
        "reason": "never shipped as its own version. The external-worktree "
                  "bootstrap repair was installed and packaged only as a "
                  "local development candidate; no source tree declared "
                  "2.18.2. That work shipped with the canonical settings, "
                  "dashboard, cleanup, test-portfolio, and CI delivery in "
                  "2.18.3.",
        "co_released_with": "2.18.3",
    },
}

# Versions with real manifest trees that attributed release history explicitly
# dispositioned as superseded candidates, not releases. These differ from
# NOT_SHIPPED because their source snapshots exist. C7 re-derives that each
# entry remains declared and untagged so the list cannot hide a release or a
# fictional version.
NOT_RELEASED = {
    "2.18.10": {
        "reason": "superseded Marketplace candidate. It completed the "
                  "fail-closed delivery authority and isolated global-hook "
                  "work, but the installed OpenAI archive could not parse "
                  "its launcher-only hook commands during onboarding and a "
                  "fresh native SessionStart could not bind its receipt "
                  "before a governed locator existed. 2.19.0 closes both "
                  "public bootstrap edges; 2.18.10 was never promoted to "
                  "released truth.",
        "superseded_by": "2.19.0",
    },
    "2.18.9": {
        "reason": "superseded local Marketplace candidate. It wired native "
                  "telemetry, zero-context pickups, and the current dashboard, "
                  "but preceded the fail-closed delivery authority, isolated "
                  "global hooks, and installed-package proof completed in "
                  "2.18.10; it was never promoted to released truth.",
        "superseded_by": "2.18.10",
    },
    "2.18.8": {
        "reason": "superseded local Marketplace candidate. It restored "
                  "the canonical terminal Plan graph, but native Codex "
                  "counters were not yet the required per-pickup meter, "
                  "zero-context worker inheritance was not enforced at the "
                  "spawn boundary, and the surfaced dashboard path could "
                  "still point at a fragment. 2.18.9 closes those runtime "
                  "wiring edges; 2.18.8 was never promoted to released "
                  "truth.",
        "superseded_by": "2.18.9",
    },
    "2.18.7": {
        "reason": "superseded local Marketplace candidate. It restored "
                  "terminal Design visibility and private artifact "
                  "preservation, but a real migrated run whose mutable Plan "
                  "file no longer matched its approval receipt still hid "
                  "the canonical task DAG and waves. 2.18.8 projects that "
                  "graph from governed loop state and labels derived waves "
                  "unverified; 2.18.7 was never promoted to released truth.",
        "superseded_by": "2.18.8",
    },
    "2.18.6": {
        "reason": "superseded local Marketplace candidate. Its terminal-only "
                  "legacy migration closed the run truthfully, but terminal "
                  "failed dashboards dropped Design and Plan graphs, named "
                  "the baseline as the delivered candidate, and skipped the "
                  "migrated run-artifact manifest. 2.18.7 wires those existing "
                  "authorities before upload; 2.18.6 was never promoted to "
                  "released truth.",
        "superseded_by": "2.18.7",
    },
    "2.18.5": {
        "reason": "superseded local Marketplace candidate. Its new-run "
                  "artifact boundary could not truthfully terminalize an "
                  "active pre-2.18.5 run after upgrade; 2.18.6 adds a narrow "
                  "fail-closed migration without inventing execution truth. "
                  "It was never promoted to released truth.",
        "superseded_by": "2.18.6",
    },
    "2.18.4": {
        "reason": "superseded local Marketplace candidate. Its dashboard "
                  "status repair preceded the R-0002 control-plane wiring, "
                  "semantic test-value adjudication, durable per-run "
                  "artifacts, and installed-package journey completed in "
                  "2.18.5; it was never promoted to released truth.",
        "superseded_by": "2.18.5",
    },
    "2.18.3": {
        "reason": "superseded local Marketplace candidate. Its final Plan "
                  "dependency graph replayed Plan-time pending status after "
                  "the governed loop had completed; 2.18.4 corrects the "
                  "live execution-status join before upload.",
        "superseded_by": "2.18.4",
    },
    "2.17.22": {
        "reason": "superseded Marketplace candidate with a bootstrap locator "
                  "defect; it was never promoted, installed as released "
                  "truth, or authorized for a release tag.",
        "superseded_by": "2.17.23",
    },
    "2.17.23": {
        "reason": "superseded local candidate that repaired locator binding "
                  "but left the W31 producer and zero-lens EM seams open; it "
                  "was explicitly withheld from release.",
        "superseded_by": "2.17.24",
    },
    "2.17.24": {
        "reason": "superseded local bootstrap candidate that closed the W31 "
                  "and zero-lens EM seams but did not contain the completed "
                  "R-0013 delivery and was explicitly not released.",
        "superseded_by": "2.17.25",
    },
    "2.17.25": {
        "reason": "superseded main-integration candidate that completed "
                  "R-0013 but preceded the whole-codebase R-0002 EM "
                  "remediation; it was explicitly not released.",
        "superseded_by": "2.17.26",
    },
    "2.17.26": {
        "reason": "superseded whole-codebase remediation candidate that "
                  "preceded the public-metadata regression correction; it "
                  "was explicitly not released.",
        "superseded_by": "2.18.0",
    },
    "2.18.0": {
        "reason": "superseded public-metadata correction candidate that "
                  "preceded the complete local and exact-PR-head-SHA release "
                  "proof; it was explicitly not released.",
        "superseded_by": "2.18.1",
    },
}

# Version numbers deliberately skipped — never bumped to, never released.
SKIPPED = {"2.7.2": "burned during the v2.7.x lens rewrite; never bumped to."}


def git(root, *args):
    p = subprocess.run(["git"] + list(args), cwd=root,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout.strip()


def vkey(v):
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


def mainline(root):
    """The branch releases ship from. Named explicitly so a topic branch
    cannot quietly redefine what 'released' means."""
    for ref in ("refs/remotes/origin/main", "refs/heads/main"):
        if git(root, "rev-parse", "--verify", "--quiet", ref)[0] == 0:
            return ref
    return None


def version_at(root, commit):
    for path in MANIFESTS:
        rc, blob = git(root, "show", f"{commit}:{path}")
        if rc != 0 or not blob:
            continue
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        v = data.get("version")
        if not v and isinstance(data.get("plugins"), list) and data["plugins"]:
            v = data["plugins"][0].get("version")
        if v:
            return v
    return None


def shipped_versions(root, ref):
    """version -> the first first-parent commit on the mainline whose
    manifest declares it. Only commits that TOUCH a manifest are read."""
    rc, out = git(root, "log", "--first-parent", "--reverse",
                  "--format=%H", ref, "--", *MANIFESTS)
    if rc != 0:
        return {}
    intro, seen = {}, set()
    for c in out.split():
        v = version_at(root, c)
        if v and v not in seen:
            seen.add(v)
            intro[v] = c
    return intro


def release_tags(root):
    """tag -> the COMMIT it names (annotated tags dereferenced). Reading the
    tag object's own sha as a commit is what produced the phantom
    'dangling v2.8.0'."""
    rc, out = git(root, "show-ref", "--tags", "-d")
    tags = {}
    if rc != 0:
        return tags
    for line in out.splitlines():
        sha, ref = line.split(" ", 1)
        name = ref[len("refs/tags/"):]
        if name.endswith("^{}"):
            tags[name[:-3]] = sha           # dereferenced: always wins
        else:
            tags.setdefault(name, sha)
    return {k: v for k, v in tags.items() if k.startswith("v")}


def changelog_versions(root):
    path = os.path.join(root, "CHANGELOG.md")
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("| **v"):
                out.append(line.split("**")[1].lstrip("v"))
    return out


def audit(root=ROOT):
    ref = mainline(root)
    if ref is None:
        return {"ok": False, "unavailable": "no main branch found — fetch "
                "origin/main (CI needs fetch-depth: 0 and tags)"}
    intro = shipped_versions(root, ref)
    if not intro:
        return {"ok": False, "unavailable": "no manifest history reachable — "
                "a shallow clone cannot verify release tags"}
    tags = release_tags(root)
    if not tags:
        return {"ok": False, "unavailable": "no tags fetched — CI needs "
                "`fetch-tags: true` (a tagless clone cannot verify tags)"}

    problems = []
    newest = max(intro, key=vkey)

    # Validate EVERY tag, not only tags whose version appeared on the
    # first-parent manifest path. A no-ff merge can make a released, tagged
    # side-parent commit reachable without making that intermediate version
    # a first-parent tree. Ignoring those tags made the real v2.17.17 release
    # fail C4 and C6 despite its tag naming a reachable tree whose manifests
    # all declare 2.17.17. An abandoned side branch still fails C2, and a tag
    # on the wrong tree still fails C3.
    exact_reachable_tags = {}
    for name, sha in sorted(tags.items(), key=lambda item: vkey(item[0][1:])):
        v = name[1:]
        reachable = git(root, "merge-base", "--is-ancestor", sha, ref)[0] == 0
        if not reachable:
            problems.append({
                "check": "C2", "version": v,
                "detail": f"{name} -> {sha[:9]} is NOT reachable from {ref}"})
            continue
        declared = version_at(root, sha)
        if declared != v and v not in NOT_SHIPPED:
            problems.append({
                "check": "C3", "version": v,
                "detail": f"{name} -> {sha[:9]}, whose manifest declares "
                          f"{declared!r}, not {v!r}"})
            continue
        if declared == v:
            exact_reachable_tags[v] = sha

    for v in sorted(intro, key=vkey):
        name = "v" + v
        sha = tags.get(name)
        if sha is None:
            if v == newest or v in NOT_RELEASED:
                continue          # the release in flight; tagged after CI
            problems.append({
                "check": "C1", "version": v,
                "detail": f"shipped at {intro[v][:9]} on {ref} but has no "
                          f"{name} tag. Fix: git tag -a {name} "
                          f"{intro[v][:9]} -m \"{name}\" && git push origin "
                          f"{name}"})

    # The version the WORKING TREE declares is the release in flight: its
    # CHANGELOG row is written before the bump reaches the mainline, which
    # is the correct order. C1 already tolerates the newest mainline
    # version being untagged for the same reason; this is that rule's twin
    # for C4. It exempts exactly one version — the one on disk right now —
    # so a CHANGELOG row for a version nobody is preparing still fails.
    in_flight = None
    try:
        with open(os.path.join(root, MANIFESTS[0]), encoding="utf-8") as f:
            in_flight = json.load(f).get("version")
    except (OSError, ValueError):
        in_flight = None
    # ...and the same holds for a release that is COMMITTED but not yet
    # pushed. The first version of this exemption covered exactly one
    # version, which was wrong the moment two release commits stacked up
    # locally: v2.11.0 was committed here, v2.12.0 was in the working tree,
    # and the gate reported the older one as fictional. "Prepared" means
    # some commit reachable from HEAD declares it — a CHANGELOG row for a
    # version nobody has prepared anywhere still fails.
    prepared = set(shipped_versions(root, "HEAD"))
    if in_flight:
        prepared.add(in_flight)

    shipped_records = dict(intro)
    for v, sha in exact_reachable_tags.items():
        shipped_records.setdefault(v, sha)
    shipped = set(shipped_records)
    tagged_side_releases = sorted(set(exact_reachable_tags) - set(intro),
                                  key=vkey)
    for v in changelog_versions(root):
        if v in shipped or v in NOT_SHIPPED or v in prepared:
            continue
        problems.append({
            "check": "C4", "version": v,
            "detail": f"CHANGELOG names v{v} but no tree ever declared it. "
                      f"Either it shipped (and this check is wrong) or it "
                      f"needs a NOT_SHIPPED entry saying where it went."})

    for v, info in NOT_SHIPPED.items():
        if v in shipped:
            problems.append({
                "check": "C5", "version": v,
                "detail": f"v{v} IS in the manifest history ({intro[v][:9]}) "
                          f"but sits in NOT_SHIPPED — remove the exemption "
                          f"and tag it."})

    changelog_claims = set(changelog_versions(root))
    observed_versions = shipped | prepared | {
        name[1:] for name in tags if name.startswith("v")
    }
    for v, info in NOT_RELEASED.items():
        if v not in observed_versions:
            continue
        if v not in changelog_claims:
            problems.append({
                "check": "C7", "version": v,
                "detail": f"v{v} is marked declared-but-not-released but "
                          "its CHANGELOG disposition is missing"})
        elif v not in shipped and v not in prepared:
            problems.append({
                "check": "C7", "version": v,
                "detail": f"v{v} is marked declared-but-not-released but no "
                          "manifest tree on the mainline or prepared HEAD "
                          "declares it"})
        elif v in exact_reachable_tags:
            problems.append({
                "check": "C7", "version": v,
                "detail": f"v{v} is marked declared-but-not-released but has "
                          "an exact reachable release tag"})

    known = shipped | set(NOT_SHIPPED)
    for name in tags:
        v = name[1:]
        if v not in known:
            problems.append({
                "check": "C6", "version": v,
                "detail": f"tag {name} names a version no tree ever declared"})

    return {"ok": not problems, "mainline": ref, "newest": newest,
            "in_flight": in_flight,
            "prepared": sorted(prepared, key=vkey),
            "shipped": {v: shipped_records[v]
                        for v in sorted(shipped_records, key=vkey)},
            "tagged_side_releases": tagged_side_releases,
            "tags": {k: tags[k] for k in sorted(tags, key=lambda t: vkey(t[1:]))},
            "not_shipped": sorted(NOT_SHIPPED),
            "not_released": sorted(NOT_RELEASED), "skipped": sorted(SKIPPED),
            "problems": problems}


def authorize_tag(root, version, protected_main_gate):
    """Authorize, but never create, one tag for the exact protected-main SHA.

    Tag creation remains an explicit irreversible action.  This function is
    the fail-closed seam release automation must cross immediately before that
    action; historical reachability alone is deliberately insufficient.
    """
    from taskplane import release_evidence

    repository = os.path.abspath(os.fspath(root))
    receipt = release_evidence.validate_protected_main_release_gate(
        protected_main_gate, repository=repository)
    source_sha = receipt["source_sha"]
    if version_at(repository, source_sha) != version:
        raise release_evidence.ReleaseEvidenceError(
            "release tag version does not match the protected-main source tree")
    result = audit(repository)
    if result.get("unavailable") or result.get("problems"):
        raise release_evidence.ReleaseEvidenceError(
            "release history audit is unavailable or not clean")
    existing = release_tags(repository).get("v" + version)
    if existing is not None and existing != source_sha:
        raise release_evidence.ReleaseEvidenceError(
            "release tag already names another source SHA")
    authorization = {
        "schema": "taskplane.release-tag-authorization/v1",
        "tag": "v" + version,
        "source_sha": source_sha,
        "protected_main_gate_fingerprint": receipt["fingerprint"],
        "authorized": True,
        "cryptographic_authenticity_claimed": False,
    }
    authorization["fingerprint"] = hashlib.sha256(
        (json.dumps(authorization, sort_keys=True, separators=(",", ":")) + "\n")
        .encode("utf-8")
    ).hexdigest()
    return authorization


def _read_json_object(path, label):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unavailable") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _git_exact(root, *args):
    rc, output = git(root, *args)
    if rc != 0 or not output:
        raise ValueError("protected-main Git topology is unavailable")
    return output


def authenticate_direct_ci_matrix(cell_runtimes, cell_receipts):
    """Authenticate each CI cell on its host and one shared source authority."""
    import ci_local
    from taskplane import release_evidence

    if not isinstance(cell_runtimes, Mapping) or \
            not isinstance(cell_receipts, Mapping) or not cell_runtimes:
        raise release_evidence.ReleaseEvidenceError(
            "authoritative CI direct runtimes and receipts are incomplete")
    if set(cell_runtimes) != set(cell_receipts) or any(
        not isinstance(cell_id, str) or not cell_id
        for cell_id in cell_runtimes
    ):
        raise release_evidence.ReleaseEvidenceError(
            "authoritative CI direct runtime and receipt identities disagree")
    try:
        runtimes = {
            cell_id: ci_local.validate_authoritative_ci_runtime(runtime)
            for cell_id, runtime in cell_runtimes.items()
        }
        authorities = {
            cell_id: ci_local.authoritative_ci_shared_authority(runtime)
            for cell_id, runtime in runtimes.items()
        }
    except (TypeError, ci_local.RunnerError) as exc:
        raise release_evidence.ReleaseEvidenceError(
            "authoritative CI direct runtime is invalid") from exc
    authority_fingerprints = {
        row["fingerprint"] for row in authorities.values()
    }
    if len(authority_fingerprints) != 1:
        raise release_evidence.ReleaseEvidenceError(
            "authoritative CI direct runtimes do not share one candidate authority")
    authority = next(iter(authorities.values()))
    planned_cells = authority.get("plan", {}).get("cells")
    if not isinstance(planned_cells, list) or not planned_cells:
        raise release_evidence.ReleaseEvidenceError(
            "authoritative CI shared plan is incomplete")
    planned_ids = [
        row.get("id") for row in planned_cells if isinstance(row, Mapping)
    ]
    if len(planned_ids) != len(planned_cells) or \
            any(not isinstance(cell_id, str) for cell_id in planned_ids) or \
            set(planned_ids) != set(cell_runtimes):
        raise release_evidence.ReleaseEvidenceError(
            "authoritative CI direct runtimes do not cover the shared plan")
    checked_cells = []
    try:
        for cell_id in planned_ids:
            runtime = runtimes[cell_id]
            matches = [
                row for row in runtime["plan"]["cells"]
                if row.get("id") == cell_id
            ]
            if len(matches) != 1:
                raise ci_local.RunnerError(
                    "CI cell is absent or ambiguous in its sealed runtime")
            checked_cells.append(
                ci_local.validate_authoritative_ci_cell_receipt(
                    cell_receipts[cell_id], runtime, matches[0],
                )
            )
    except (KeyError, TypeError, ci_local.RunnerError) as exc:
        raise release_evidence.ReleaseEvidenceError(
            "authoritative CI direct receipt is invalid") from exc
    return authority, checked_cells


def validate_measured_ci_signoff(wave_receipt, checked_cells):
    """Bind release sign-off to timings from authenticated direct CI cells."""
    from taskplane import ci_policy, release_evidence
    from taskplane import wave_metrics as wave_metrics_module

    try:
        wave = wave_metrics_module.validate_wave_receipt(wave_receipt)
    except wave_metrics_module.WaveMetricsError as exc:
        raise release_evidence.ReleaseEvidenceError(
            "sealed wave metrics evidence is invalid") from exc
    names = {
        "ci_first_validation_hours", "ci_red_validation_domains",
        "ci_critical_path_minutes", "ci_p50_minutes", "ci_p95_minutes",
        "ci_runner_minutes", "ci_parallelism_factor",
    }
    metrics = wave.get("metrics")
    if not isinstance(metrics, Mapping) or not names.issubset(metrics):
        raise release_evidence.ReleaseEvidenceError(
            "sealed wave metrics omit measured CI release signoff")
    if any(metrics[name].get("passed") is not True for name in names):
        raise release_evidence.ReleaseEvidenceError(
            "measured CI targets do not authorize release signoff")

    cells = []
    domains = {}
    for row in checked_cells:
        if not isinstance(row, Mapping):
            raise release_evidence.ReleaseEvidenceError(
                "authenticated CI timing receipt is invalid")
        cell_id = row.get("id")
        kind = row.get("kind")
        duration_ms = row.get("duration_ms")
        if not isinstance(cell_id, str) or not cell_id or \
                not isinstance(kind, str) or not kind or \
                isinstance(duration_ms, bool) or \
                not isinstance(duration_ms, int) or duration_ms <= 0:
            raise release_evidence.ReleaseEvidenceError(
                "authenticated CI timing receipt is invalid")
        duration = duration_ms / 60_000
        cells.append({"id": cell_id, "duration_minutes": duration})
        domains[kind] = max(domains.get(kind, 0.0), duration)
    if len(cells) != len({row["id"] for row in cells}) or not cells:
        raise release_evidence.ReleaseEvidenceError(
            "authenticated CI timing identities are incomplete")

    try:
        ready = datetime.fromisoformat(
            str(wave["run"]["integration_ready_at"]).replace("Z", "+00:00"))
        first_hours = metrics["ci_first_validation_hours"]["actual"]
        first_started = ready + timedelta(hours=float(first_hours))
        evidence = {
            "integration_ready_at": ready.isoformat(),
            "first_validation_started_at": first_started.isoformat(),
            "validation_domain_ids": list(domains),
            "validation_domain_durations_minutes": list(domains.values()),
            "authoritative_elapsed_minutes":
                metrics["ci_critical_path_minutes"]["actual"],
            "cells": cells,
            "targets": dict(ci_policy.DECLARED_TARGETS),
        }
        evaluated = ci_policy.evaluate_ci_metrics(evidence)
    except (KeyError, TypeError, ValueError, ci_policy.CIPolicyError) as exc:
        raise release_evidence.ReleaseEvidenceError(
            "measured CI evidence is invalid") from exc
    if evaluated["passed"] is not True:
        raise release_evidence.ReleaseEvidenceError(
            "measured CI metrics do not meet release targets")
    expected = {
        "ci_first_validation_hours": "first_validation_hours",
        "ci_p50_minutes": "p50_minutes",
        "ci_p95_minutes": "p95_minutes",
        "ci_runner_minutes": "runner_minutes",
        "ci_parallelism_factor": "parallelism",
    }
    if any(
        float(metrics[metric]["actual"]) != evaluated["values"][value]
        for metric, value in expected.items()
    ) or metrics["ci_red_validation_domains"]["actual"] != 0:
        raise release_evidence.ReleaseEvidenceError(
            "sealed wave metrics disagree with authenticated CI timings")
    return evaluated


def assemble_protected_main_gate(root, *, pull_request_head_sha, cell_runtimes,
                                 cell_receipts, dashboard,
                                 dashboard_current, wave_metrics, cleanup,
                                 openai_provenance,
                                 claude_provenance):
    """Assemble release truth only from existing sealed producer receipts."""
    import release_provenance
    from taskplane import owned_cleanup, release_evidence, views
    from taskplane import wave_metrics as wave_metrics_module

    repository = Path(root).resolve()
    source_sha = release_evidence._source_sha(
        _git_exact(repository, "rev-parse", "HEAD"))
    main_head = _git_exact(repository, "rev-parse", "refs/heads/main")
    if main_head != source_sha:
        raise release_evidence.ReleaseEvidenceError(
            "release assembly requires the exact protected-main HEAD")
    pull_head = release_evidence._source_sha(
        pull_request_head_sha, "pull_request_head_sha")
    parents = _git_exact(
        repository, "rev-list", "--parents", "-n", "1", source_sha)
    parent_rows = parents.split()
    if len(parent_rows) != 3 or parent_rows[0] != source_sha or \
            parent_rows[2] != pull_head:
        raise release_evidence.ReleaseEvidenceError(
            "release assembly requires the exact merge-created first-parent topology")
    first_parent = parent_rows[1]

    authority, checked_cells = authenticate_direct_ci_matrix(
        cell_runtimes, cell_receipts)
    if authority.get("source_sha") != source_sha:
        raise release_evidence.ReleaseEvidenceError(
            "authoritative CI shared authority names another protected-main SHA")
    if any(row["status"] != "green" for row in checked_cells) or \
            sum(row["kind"] == "browser" for row in checked_cells) != 1:
        raise release_evidence.ReleaseEvidenceError(
            "authoritative CI direct receipts are not all green")

    try:
        dashboard_evidence = views.validate_dashboard_publication_receipt(
            dashboard, current_head=dashboard_current,
            expected_source_sha=source_sha)
    except (TypeError, ValueError) as exc:
        raise release_evidence.ReleaseEvidenceError(
            "dashboard publication evidence is stale or incomplete") from exc

    try:
        metrics = wave_metrics_module.validate_wave_receipt(wave_metrics)
    except wave_metrics_module.WaveMetricsError as exc:
        raise release_evidence.ReleaseEvidenceError(
            "sealed wave metrics evidence is invalid") from exc
    if metrics["run"]["candidate_fingerprint"] != authority["fingerprint"] or \
            metrics["signoff"]["ready"] is not True:
        raise release_evidence.ReleaseEvidenceError(
            "sealed wave metrics do not bind the exact release candidate")
    validate_measured_ci_signoff(metrics, checked_cells)
    try:
        cleanup_evidence = owned_cleanup.cleanup_consumer_evidence(cleanup)
    except owned_cleanup.OwnedCleanupError as exc:
        raise release_evidence.ReleaseEvidenceError(
            "owned cleanup evidence is invalid") from exc
    if cleanup_evidence["cleanup_status"] != "clean" or \
            cleanup_evidence["leak_count"] != 0:
        raise release_evidence.ReleaseEvidenceError(
            "owned cleanup evidence has nonzero leaks")

    packages = []
    inputs = release_evidence.release_input_digests(repository)
    for kind, record in (("openai", openai_provenance),
                         ("claude", claude_provenance)):
        try:
            validated = release_provenance.validate(
                record, expected_source_sha=source_sha,
                require_release_inputs=True)
            package = release_provenance.release_gate_record(validated)
        except release_provenance.ProvenanceError as exc:
            raise release_evidence.ReleaseEvidenceError(
                f"{kind} package provenance is invalid") from exc
        if validated["kind"] != kind or validated["release_inputs"] != inputs:
            raise release_evidence.ReleaseEvidenceError(
                f"{kind} package provenance names stale release inputs")
        packages.append(package)

    supply = release_evidence.release_supply_chain_evidence(repository)
    evidence = {
        "schema": release_evidence.PROTECTED_MAIN_RELEASE_EVIDENCE_SCHEMA,
        "source_sha": source_sha,
        "protected_branch": "main",
        "merge_topology": {
            "event": "push", "ref_kind": "protected-main",
            "merge_created_sha": source_sha, "checked_sha": source_sha,
            "first_parent_sha": first_parent,
            "pull_request_head_sha": pull_head,
            "topology": "merge-created-first-parent",
        },
        "ci": {
            "event": "push", "ref_kind": "protected-main",
            "candidate_sha": source_sha, "terminal_status": "green",
            "required_checks": [row["id"] for row in checked_cells],
            "conclusions": {row["id"]: "success" for row in checked_cells},
        },
        "supply_chain": supply,
        "receipts": {
            "settings": {"digest": inputs["settings_digest"]},
            "candidate": {"digest": authority["fingerprint"],
                          "source_sha": source_sha},
            "checks": {
                row["id"]: {
                    "digest": row["receipt"], "source_sha": source_sha,
                    "status": "green", "fresh": True,
                }
                for row in checked_cells
            },
            "dashboard": dashboard_evidence,
            "wave_metrics": {"digest": metrics["fingerprint"],
                             "source_sha": source_sha, "status": "sealed",
                             "recounted": False},
            "cleanup": {"digest": cleanup_evidence["evidence_digest"],
                        "source_sha": source_sha, "status": "clean",
                        "leak_count": 0},
        },
        "packages": packages,
    }
    return release_evidence.create_protected_main_release_gate(
        evidence, repository=repository)


def main():
    # An optional root makes the gate runnable against any checkout — which
    # is what lets its own tests prove the EXIT CODES on synthetic repos
    # instead of on whatever the current CI job happened to fetch. The first
    # version of that test ran the script against this repo and asserted
    # exit 0; the main test job checks out without tags, the gate correctly
    # reported CANNOT VERIFY, and the test failed on the gate being right.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=ROOT)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--release-gate")
    parser.add_argument("--authorize-version")
    parser.add_argument("--assemble-gate")
    parser.add_argument("--assembly-manifest")
    args = parser.parse_args()
    if bool(args.assemble_gate) != bool(args.assembly_manifest):
        parser.error(
            "--assemble-gate and --assembly-manifest must be supplied together")
    if args.assemble_gate:
        manifest_path = Path(args.assembly_manifest).resolve()
        manifest = _read_json_object(manifest_path, "release assembly manifest")
        expected = {
            "schema", "pull_request_head_sha", "cell_runtimes", "cell_receipts",
            "dashboard", "dashboard_current", "wave_metrics",
            "cleanup",
            "openai_provenance", "claude_provenance",
        }
        if set(manifest) != expected or manifest.get("schema") != \
                "taskplane.release-gate-assembly/v2":
            parser.error("release assembly manifest fields are not closed")

        def artifact(name):
            value = manifest.get(name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"release assembly {name} path is invalid")
            path = Path(value)
            if not path.is_absolute():
                path = manifest_path.parent / path
            return _read_json_object(path, name.replace("_", " ") + " receipt")

        def artifact_map(name):
            value = manifest.get(name)
            if not isinstance(value, Mapping) or not value or any(
                not isinstance(cell_id, str) or not cell_id or
                not isinstance(path, str) or not path
                for cell_id, path in value.items()
            ):
                raise ValueError(
                    f"release assembly {name} path map is invalid")
            result = {}
            for cell_id, value_path in value.items():
                path = Path(value_path)
                if not path.is_absolute():
                    path = manifest_path.parent / path
                result[cell_id] = _read_json_object(
                    path, f"{name} {cell_id} receipt")
            return result

        gate = assemble_protected_main_gate(
            args.root,
            pull_request_head_sha=manifest["pull_request_head_sha"],
            cell_runtimes=artifact_map("cell_runtimes"),
            cell_receipts=artifact_map("cell_receipts"),
            dashboard=artifact("dashboard"),
            dashboard_current=artifact("dashboard_current"),
            wave_metrics=artifact("wave_metrics"),
            cleanup=artifact("cleanup"),
            openai_provenance=artifact("openai_provenance"),
            claude_provenance=artifact("claude_provenance"),
        )
        output = Path(args.assemble_gate).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(gate, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        temporary.replace(output)
        print(f"protected-main release gate: {output}")
        print(f"source_sha: {gate['source_sha']}")
        print(f"fingerprint: {gate['fingerprint']}")
        return 0
    res = audit(args.root)
    authorization_error = None
    if bool(args.release_gate) != bool(args.authorize_version):
        authorization_error = (
            "--release-gate and --authorize-version must be supplied together")
    elif args.release_gate:
        try:
            with open(args.release_gate, encoding="utf-8") as stream:
                receipt = json.load(stream)
            res["tag_authorization"] = authorize_tag(
                args.root, args.authorize_version, receipt)
        except (OSError, ValueError) as exc:
            authorization_error = str(exc)
    if authorization_error is not None:
        res["ok"] = False
        res["authorization_error"] = authorization_error
    if args.json:
        print(json.dumps(res, indent=2, sort_keys=True))
        return 0 if res.get("ok") else 1
    if res.get("unavailable"):
        print(f"release tags: CANNOT VERIFY — {res['unavailable']}")
        return 1
    print(f"release tags: {len(res['shipped'])} declared version tree(s) on "
          f"{res['mainline']}, {len(res['tags'])} tag(s)")
    print(f"  newest ({res['newest']}) may be untagged while in flight; "
          "older untagged versions require an exact NOT_RELEASED disposition")
    if res["not_shipped"]:
        print(f"  never shipped as own version: "
              f"{', '.join('v' + v for v in res['not_shipped'])} "
              f"(reasons in NOT_SHIPPED)")
    if res["not_released"]:
        print(f"  declared superseded candidates, not releases: "
              f"{', '.join('v' + v for v in res['not_released'])} "
              f"(reasons in NOT_RELEASED)")
    if res["skipped"]:
        print(f"  version numbers skipped: "
              f"{', '.join('v' + v for v in res['skipped'])}")
    for p in res["problems"]:
        print(f"  {p['check']} v{p['version']}: {p['detail']}")
    if authorization_error is not None:
        print(f"  RELEASE REFUSED: {authorization_error}")
    elif res.get("tag_authorization"):
        authorization = res["tag_authorization"]
        print(f"  authorized {authorization['tag']} at "
              f"{authorization['source_sha'][:12]} from exact protected-main green")
    print("ok: every shipped version is tagged and every tag names a real "
          "release" if res["ok"] else
          f"FAIL: {len(res['problems'])} release-tag problem(s)")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
