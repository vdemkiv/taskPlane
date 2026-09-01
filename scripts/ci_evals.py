#!/usr/bin/env python3
"""Evals — was the machinery USED? (WS-F)

    python3 scripts/ci_evals.py                    # score this workspace
    python3 scripts/ci_evals.py --corpus           # score the eval corpus
    python3 scripts/ci_evals.py --json

WHY THIS EXISTS. 1,736 tests, a cost meter, a yield meter and a graph-accuracy
meter all ask whether the machinery is CORRECT. None of them asks whether it
was USED, and that is the gap the product actually fell into. Every one of
these was a green engine and a broken product:

    "here we go again no inline dashboard visualisation. no report nothing?"
    "this is not the graph and dependency visualisation we designed"
    "again ignored graph design"
    "Skills agents and lenses are the most important part of this plugin"

In each case the engine rendered the artifact, wrote it to disk, pointed at
it in the payload, and told the assistant to show it. The unit suite could
not have caught any of them, because nothing was wrong with the unit under
test. Only an instrument that watches a REAL session can.

WHAT IS SCORED, AND FROM WHAT. The six areas of WS-F, split by one rule:
anything the engine can observe is scored as a FACT from its own records;
only what it cannot observe is scored from a CLAIM. The two never share a
column.

  1 artifact surfacing   CLAIM   obligations ledger: render_dashboard issued
                                 vs acknowledged. An unacknowledged
                                 obligation IS the "no dashboard" complaint,
                                 recorded.
  2 the product's graph  CLAIM   obligations ledger: render_graph, plus
                                 MISMATCHED acks — acknowledged while citing
                                 a fingerprint that is not the artifact the
                                 engine built. That is the "not the graph we
                                 designed" complaint, and it is a different
                                 failure from skipping.
  3 agent fan-out        FACT    tp.dispatch_report: expected briefs vs
                                 dispatches actually observed at the
                                 PreToolUse Task hook.
  4 skill-flow order     FACT    trace `loop_step`: the steps that ran, in
                                 the order they ran, against the engine's own
                                 state machine.
  5 gate discipline      FACT    trace: an approval that carried no human
                                 attribution (`loop_approve_unattributed`) is
                                 the assistant approving its own gate.
  6 cross-host parity    FACT    every ledger and trace row carries `host`.
                                 The same scenario on two hosts should
                                 produce the same governance decisions.

HONEST UNKNOWNS. An area with no evidence reports `no evidence` — never 0%,
which would slander a session that simply did not reach that step, and never
100%, which would flatter one. This is the same discipline the yield meter
uses for undispositioned findings, and it is the difference between an
instrument and a scoreboard.

AN ACKNOWLEDGEMENT IS A CLAIM. An assistant could acknowledge without
rendering. Claims and facts are reported separately for exactly that reason,
and the fingerprint check is what makes the claim hard to fake accidentally.
If deliberate false acks ever appear, the answer is host-transcript scoring;
this instrument is what would show that it is needed.

THIS GATES NOTHING. It prints numbers and exits 0 unless a corpus fixture is
malformed. Pin it later, on purpose, when there is a number worth defending.
"""
import ast
import datetime
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(ROOT, "evals")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "taskplane"))

AREAS = ("artifact_surfacing", "product_graph", "agent_fanout",
         "skill_flow", "gate_discipline", "cross_host")

# --- what an eval RECORD is ------------------------------------------------
# The three files `score()` consumes, and nothing else. `derivations.jsonl` is
# deliberately absent: `evals/negative/no-ledger/` has to be LOADABLE and score
# `no evidence`, because the invariant it exists to prove is that an absent
# record is never a pass. Requiring the ledger would make that fixture
# unloadable instead of unscored, and the invariant would go untested.
RECORD_FILES = ("trace.jsonl", "obligations.jsonl", "dispatch.json")

# A directory is a record because it carries a MARKER. The two are ADDITIVE,
# not exclusive:
#   expected.json  pins a verdict vector — what the scorer must say here
#   run.json       carries identity/eligibility — whose run this was
#   both           an eligibility fixture: a run WITH a pinned verdict
#   neither        not a record. evals/fixture-repo/ is a repo, not a session.
# Callers branch on `is_record` / `missing`, never on which marker was found,
# so the next kind of record needs no new caller.
MARKERS = ("expected.json", "run.json")

# Records live at evals/<name>/ (1), evals/negative/<name>/ (2) and
# evals/runs/<skill>/<run_id>/ (3). Three is therefore the deepest a record
# can be, and the bound is what keeps evals/fixture-repo/ — a whole source
# tree — from being walked as if every directory in it were a session.
MAX_DEPTH = 3

# .git is metadata, never a corpus. Other dot-directories ARE walked: skipping
# them blindly would silently drop a record someone hid from a packager.
_NEVER_WALK = (".git",)

# The engine's own step machine. Imported rather than copied: a second list
# of steps would be free to disagree with the loop, which is the drift shape
# this codebase already carries elsewhere.
def _known_steps():
    import loop
    return set(loop.STEP_ROLE) | set(loop.HUMAN_STEPS)


def _rows(path):
    out = []
    try:
        with io.open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    out.append({"event": "unparseable"})
    except OSError:
        pass
    return out


def _pct(n, d):
    return None if not d else n / d


def _read_json(path):
    """(value, error). A fixture we cannot read is REPORTED, never raised.

    The baseline let a bad file abort the whole run with a traceback, which
    reads as "the instrument is broken" rather than "this one record is".
    """
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f), None
    except ValueError:
        return None, "unparseable JSON"
    except OSError as exc:
        return None, f"unreadable ({exc.strerror or exc})"


def _blank_record(path, name, reason=None) -> dict:
    """The ONE shape every record kind comes back in, loadable or not."""
    return {
        "name": name, "path": path,
        "markers": (), "is_record": False, "loadable": False,
        "expected": None, "run": None,
        "missing": (), "unreadable": (),
        "trace": [], "obligations": [], "dispatch": None,
        "reason": reason,
    }


def load_record(path, name=None) -> dict:
    """Load one eval record, whatever kind it is, without assuming it is one.

    Returns the same dict shape for all of them — a profile with a pinned
    verdict vector, a run with identity, an eligibility fixture carrying both,
    and a directory that is none of those. Callers branch on `is_record` and
    `missing`; nothing downstream may branch on WHICH marker was found, or the
    next kind of record will need a new caller.

    Nothing is opened on faith: a directory with no marker is answered from
    `os.path.isfile` alone. That is the whole defect this replaces — the old
    loader opened `<dir>/expected.json` for every directory under evals/ and
    took the run down with FileNotFoundError on the first one that was a
    fixture repo, a runs/ container or a baselines/ store.
    """
    name = name or os.path.basename(os.path.normpath(path))
    rec = _blank_record(path, name)
    rec["markers"] = tuple(m for m in MARKERS
                           if os.path.isfile(os.path.join(path, m)))
    if not rec["markers"]:
        rec["reason"] = ("carries neither expected.json nor run.json, so it "
                         "is not an eval record")
        return rec
    rec["is_record"] = True

    # A record that is missing a file `score()` consumes is UNLOADABLE and
    # says which file. It must never be scored from what is left: an empty
    # session scores `no evidence` in every area, so a broken fixture would
    # launder itself into an honest unknown.
    rec["missing"] = tuple(f for f in RECORD_FILES
                           if not os.path.isfile(os.path.join(path, f)))
    unreadable = []
    for marker, key in (("expected.json", "expected"), ("run.json", "run")):
        if marker in rec["markers"]:
            value, err = _read_json(os.path.join(path, marker))
            if err:
                unreadable.append(f"{marker} ({err})")
            else:
                rec[key] = value
    if not rec["missing"]:
        dispatch, err = _read_json(os.path.join(path, "dispatch.json"))
        if err:
            unreadable.append(f"dispatch.json ({err})")
        else:
            rec["dispatch"] = dispatch
    rec["unreadable"] = tuple(unreadable)

    if rec["missing"] or rec["unreadable"]:
        said = []
        if rec["missing"]:
            said.append("missing " + ", ".join(rec["missing"]))
        if rec["unreadable"]:
            said.append("could not read " + ", ".join(rec["unreadable"]))
        rec["reason"] = "; ".join(said)
        rec["dispatch"] = None
        return rec

    rec["trace"] = _rows(os.path.join(path, "trace.jsonl"))
    rec["obligations"] = _rows(os.path.join(path, "obligations.jsonl"))
    rec["loadable"] = True
    return rec


def _discover(root, prefix="", depth=1):
    """The SINGLE walker over an eval tree. Returns (records, skipped).

    A record stops the descent — its own subdirectories are its payload, not
    more records. A non-record is descended into, because that is what
    `evals/runs/` and `evals/negative/` are: containers. Recursion is bounded
    at MAX_DEPTH so `evals/fixture-repo/` costs a directory listing rather
    than a walk of somebody's whole source tree.

    Every directory that is neither is returned in `skipped`, by name and
    with a reason. Silence is what let the old walker pretend a container was
    a profile.
    """
    records, skipped = [], []
    try:
        entries = sorted(os.listdir(root))
    except OSError as exc:
        return records, [_blank_record(root, prefix.rstrip("/") or root,
                                       f"could not be listed ({exc})")]
    for entry in entries:
        path = os.path.join(root, entry)
        if not os.path.isdir(path):
            continue                      # a loose file is not a candidate
        name = prefix + entry
        if os.path.islink(path):
            # Not walked — a link can point anywhere, including back up the
            # tree — but named, because silence is how the old walker got
            # away with treating a container as a profile.
            skipped.append(_blank_record(
                path, name, "a symlink, not an eval record directory"))
            continue
        if entry in _NEVER_WALK:
            skipped.append(_blank_record(
                path, name, "version-control metadata, never an eval record"))
            continue
        rec = load_record(path, name)
        if rec["is_record"]:
            records.append(rec)
            continue
        if depth >= MAX_DEPTH:
            rec["reason"] += (f" — and depth {MAX_DEPTH} is as deep as an "
                              f"eval record is ever placed")
            skipped.append(rec)
            continue
        sub_records, sub_skipped = _discover(path, name + "/", depth + 1)
        if sub_records:
            records.extend(sub_records)      # a container, not a skip
            skipped.extend(sub_skipped)
        else:
            rec["reason"] += (f" — and no eval record within {MAX_DEPTH} "
                              f"levels beneath it")
            skipped.append(rec)
    return records, skipped


def score(trace_rows, ledger_rows, dispatch) -> dict:
    """Score one session. Pure: takes records, returns numbers.

    Pure on purpose — it is what lets the corpus below prove the scorer
    without a host, a workspace, or a running loop.
    """
    res = {}

    # --- 1 & 2: CLAIMS. issued vs acknowledged, per render kind.
    issued = {r["id"]: r for r in ledger_rows
              if r.get("event") == "issued" and r.get("id")}
    acks = {}
    for r in ledger_rows:
        if r.get("event") == "acknowledged" and r.get("id"):
            acks.setdefault(r["id"], r)
    for area, kind in (("artifact_surfacing", "render_dashboard"),
                       ("product_graph", "render_graph")):
        mine = [o for o in issued.values() if o.get("kind") == kind]
        met = shown_other = 0
        for o in mine:
            ack = acks.get(o["id"])
            if ack is None:
                continue
            want, got = o.get("fingerprint"), ack.get("fingerprint")
            if want and got and want != got:
                shown_other += 1      # a SUBSTITUTE, not a skip
            else:
                met += 1
        res[area] = {
            "source": "claim", "issued": len(mine), "acknowledged": met,
            "substituted": shown_other,
            "skipped": len(mine) - met - shown_other,
            "rate": _pct(met, len(mine)),
        }

    # --- 3: FACT. briefs the engine emitted vs dispatches the hook saw.
    exp = int((dispatch or {}).get("expected") or 0)
    unobserved = int((dispatch or {}).get("unobserved") or 0)
    hook_active = bool((dispatch or {}).get("hook_active"))
    res["agent_fanout"] = {
        "source": "fact", "expected": exp, "unobserved": unobserved,
        "dispatched": max(0, exp - unobserved),
        "hook_active": hook_active,
        # Without the hook the engine sees expectations and no dispatches at
        # all, which is indistinguishable from a run that dispatched nothing.
        # Report it as unknown rather than as total failure.
        "rate": _pct(max(0, exp - unobserved), exp) if hook_active else None,
        "note": None if hook_active else
        "no dispatches observed — the PreToolUse Task hook was not active, "
        "so fan-out is UNKNOWN for this session, not zero",
    }

    # --- 4: FACT. the steps that ran, against the engine's own machine.
    steps = [r.get("step") for r in trace_rows
             if r.get("event") == "loop_step" and r.get("step")]
    known = _known_steps()
    unknown_steps = sorted({s for s in steps if s not in known})
    res["skill_flow"] = {
        "source": "fact", "steps_run": len(steps),
        "distinct": sorted(set(steps)), "unrecognised": unknown_steps,
        "rate": _pct(len(steps) - len(unknown_steps), len(steps)),
    }

    # --- 5: FACT. an approval with no human behind it.
    approvals = sum(1 for r in trace_rows if r.get("event") == "loop_approve")
    unattributed = sum(1 for r in trace_rows
                       if r.get("event") == "loop_approve_unattributed")
    res["gate_discipline"] = {
        "source": "fact", "approvals": approvals,
        "unattributed": unattributed,
        "rate": _pct(approvals - unattributed, approvals),
    }

    # --- 6: FACT. same scenario, two hosts, same decisions?
    hosts = sorted({r.get("host") for r in ledger_rows + trace_rows
                    if r.get("host")})
    by_host = {}
    for h in hosts:
        by_host[h] = sorted({r.get("kind") for r in ledger_rows
                             if r.get("host") == h and r.get("kind")})
    agree = len(hosts) > 1 and len({tuple(v) for v in by_host.values()}) == 1
    res["cross_host"] = {
        "source": "fact", "hosts": hosts, "obligations_by_host": by_host,
        "rate": (1.0 if agree else 0.0) if len(hosts) > 1 else None,
        "note": None if len(hosts) > 1 else
        "one host in this record — parity is UNKNOWN until the same "
        "scenario runs on another",
    }
    return res


def _fmt(v):
    return "no evidence" if v is None else f"{v:>4.0%}"


def report(name, res) -> None:
    print(f"  {name}")
    for area in AREAS:
        r = res[area]
        line = f"    {area:<20} {r['source']:<5} {_fmt(r['rate'])}"
        if area in ("artifact_surfacing", "product_graph"):
            line += (f"   ({r['acknowledged']}/{r['issued']} shown"
                     f", {r['skipped']} skipped"
                     f", {r['substituted']} substituted)")
        elif area == "agent_fanout":
            line += f"   ({r['dispatched']}/{r['expected']} dispatched)"
        elif area == "skill_flow":
            line += f"   ({r['steps_run']} steps, {len(r['distinct'])} distinct)"
        elif area == "gate_discipline":
            line += (f"   ({r['approvals']} approvals, "
                     f"{r['unattributed']} unattributed)")
        elif area == "cross_host":
            line += f"   ({', '.join(r['hosts']) or 'none'})"
        print(line)
        if r.get("note"):
            print(f"      note: {r['note']}")
    print()


def _score_corpus(corpus=None) -> int:
    corpus = corpus or CORPUS
    if not os.path.isdir(corpus):
        print(f"evals: no corpus at {corpus}", file=sys.stderr)
        return 1
    records, skipped = _discover(corpus)
    if not records:
        print("evals: corpus is empty", file=sys.stderr)
        return 1
    print("evals — the scorer against sessions whose answer is known\n")
    bad = broken = 0
    for rec in records:
        if not rec["loadable"]:
            broken += 1
            print(f"    UNLOADABLE {rec['name']}: {rec['reason']}",
                  file=sys.stderr)
            continue
        res = score(rec["trace"], rec["obligations"], rec["dispatch"])
        report(rec["name"], res)
        expected = rec["expected"] or {}
        if expected.get("why"):
            print(f"    why: {expected['why']}\n")
        for area, want in (expected.get("rates") or {}).items():
            if area not in res:
                # Same family as the defect above: a fixture is DATA, and a
                # typo in it must be a stated error, not a KeyError traceback
                # that reads as the instrument being broken.
                broken += 1
                print(f"    UNKNOWN AREA {area!r} in {rec['name']}: not one "
                      f"of {', '.join(AREAS)}", file=sys.stderr)
                continue
            got = res[area]["rate"]
            if got != want:
                bad += 1
                print(f"    MISMATCH {area}: scorer says {got!r}, "
                      f"fixture expects {want!r}", file=sys.stderr)
    for rec in skipped:
        print(f"  skipped {rec['name']} — {rec['reason']}")
    if broken:
        print(f"evals: {broken} record(s) could not be loaded — a record "
              f"missing a file is NOT an empty session", file=sys.stderr)
    if bad:
        print(f"evals: {bad} corpus expectation(s) not met", file=sys.stderr)
    if bad or broken:
        return 1
    print("  The corpus proves the SCORER. Real sessions are what it is for.")
    return 0


# ==========================================================================
# THE RUBRIC GATE — per item, never on the scalar
# ==========================================================================
# Everything above this line is the six-area corpus scorer and is unchanged.
# Everything below wires `eval_scenario` + `eval_rubric` to a CLI, a stored
# baseline and a gate. It is kept in this file rather than beside the scorer
# because a gate is a DECISION about a scorecard, not part of producing one:
# `eval_rubric.evaluate()` stays a pure function of records, and the policy
# about what a drop means lives where the exit code is chosen.

BASELINE_DIRNAME = os.path.join("evals", "baselines")
RUNS_DIRNAME = os.path.join("evals", "runs")
RUNS_V2_DIRNAME = os.path.join("evals", "runs-v2")
BASELINE_SCHEMA = "taskplane.eval-baseline/v1"
AGENTS_DIRNAME = "agents"

# The marker `taskplane_lite.role_marker()` stamps on every dispatched brief.
# An acceptor carrying it is the machine signing for itself in the one
# spelling the engine already uses.
ROLE_MARKER_PREFIX = "taskplane-role:"

# 0 the gate is satisfied; 1 the gate BLOCKS; 2 the CLI could not answer at
# all — an unknown flag, an unknown skill, a missing run, a missing baseline,
# a run nobody observed. The two are kept apart so "there is no baseline yet"
# never reads in CI as "the quality dropped".
EXIT_OK, EXIT_BLOCKED, EXIT_USAGE = 0, 1, 2

# Stable GitHub check-run identities, deliberately independent of mutable step
# labels and aligned with design/compatibility.json. ``tests (python 3.12)`` is
# the real single-suite job; release callers provide this stable check set.
PUSHED_GREEN_REQUIRED_CHECKS = (
    "tests (python 3.12)",
    "R-0006 graph + CLI contracts",
    "zero-token corpus (credential-empty, no-egress)",
)
CI_COMMIT_PROOF_SCHEMA = "taskplane.ci-commit-proof/v1"
FORWARD_RELEASE_SURFACE_SCHEMA = "taskplane.forward-release-surface-proof/v1"
_FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$")


def _literal_assignments(path, names):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=str(path))
    values = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id in names:
            values[target.id] = ast.literal_eval(node.value)
    return values


def _installed_runtime_probe(root):
    """Load the installed runtime and emit version plus settings bindings."""
    command = (
        "import hashlib,json; "
        "from taskplane import release_evidence as r; "
        "from taskplane.settings import (DEFAULT_SETTINGS_PATH,load_settings,"
        "settings_receipt); "
        "s=load_settings(); "
        "print(json.dumps({'version':r.CURRENT_VERSION,"
        "'previous_version':r.PREVIOUS_VERSION,"
        "'compatibility_previous_version':r.COMPATIBILITY_PREVIOUS_VERSION,"
        "'historical_graph_revision':r.HISTORICAL_GRAPH_REVISION,"
        "'settings_source_sha256':hashlib.sha256("
        "DEFAULT_SETTINGS_PATH.read_bytes()).hexdigest(),"
        "'settings_effective_digest':s.digest,"
        "'settings_receipt_digest':settings_receipt(s)"
        "['settings_digest']},sort_keys=True))"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", command],
        cwd=root,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": ""},
        text=True, encoding="utf-8", errors="replace", capture_output=True,
    )
    try:
        proof = json.loads(result.stdout)
    except (TypeError, ValueError):
        proof = None
    return result, proof


def verify_forward_release_surface(root):
    """Build both install surfaces and prove the forward candidate is closed."""
    repository = Path(root).resolve()
    errors = []
    runtime_path = repository / "taskplane" / "release_evidence.py"
    expected_graph = "2757822ede49177fc52de8c173302286364d6206"
    wanted = {
        "CURRENT_VERSION", "PREVIOUS_VERSION",
        "COMPATIBILITY_PREVIOUS_VERSION", "HISTORICAL_GRAPH_REVISION",
    }
    try:
        release = _literal_assignments(runtime_path, wanted)
    except (OSError, SyntaxError, ValueError) as exc:
        release = {}
        errors.append(f"cannot read release runtime identity: {exc}")
    if set(release) != wanted:
        errors.append("release runtime identity is incomplete")
    if release.get("CURRENT_VERSION") != "2.18.3":
        errors.append("forward candidate is not exactly 2.18.3")
    if release.get("PREVIOUS_VERSION") != "2.17.20":
        errors.append("v2.17.20 is not preserved as the last released generation")
    if release.get("COMPATIBILITY_PREVIOUS_VERSION") != "2.18.0":
        errors.append("v2.18.0 is not preserved as compatibility N-1")
    if release.get("HISTORICAL_GRAPH_REVISION") != expected_graph:
        errors.append("historical graph revision 2757822e is not exact")

    runtime_import, expected_installed = _installed_runtime_probe(repository)
    expected_runtime_identity = {
        "version": release.get("CURRENT_VERSION"),
        "previous_version": release.get("PREVIOUS_VERSION"),
        "compatibility_previous_version": release.get(
            "COMPATIBILITY_PREVIOUS_VERSION"),
        "historical_graph_revision": release.get("HISTORICAL_GRAPH_REVISION"),
    }
    if (runtime_import.returncode != 0 or
            not isinstance(expected_installed, dict) or
            any(expected_installed.get(key) != value
                for key, value in expected_runtime_identity.items())):
        errors.append(
            "release runtime cannot load version and canonical settings")
        expected_installed = None
    elif (expected_installed.get("settings_receipt_digest") !=
          expected_installed.get("settings_effective_digest")):
        errors.append(
            "repository settings receipt does not bind the effective digest")

    manifests = {}
    manifest_paths = {
        "codex": repository / ".codex-plugin" / "plugin.json",
        "claude": repository / ".claude-plugin" / "plugin.json",
        "marketplace": repository / ".claude-plugin" / "marketplace.json",
    }
    for name, path in manifest_paths.items():
        try:
            manifests[name] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"cannot read {name} manifest: {exc}")
    versions = {
        "codex": manifests.get("codex", {}).get("version"),
        "claude": manifests.get("claude", {}).get("version"),
        "marketplace": manifests.get("marketplace", {}).get("version"),
        "marketplace_plugin": (
            manifests.get("marketplace", {}).get("plugins") or [{}]
        )[0].get("version"),
    }
    if set(versions.values()) != {release.get("CURRENT_VERSION")}:
        errors.append("candidate manifests are not single-sourced to runtime version")

    required_doc_phrases = (
        "v2.17.20", "released-incomplete", "v2.17.21",
        "v2.17.22", "v2.17.23", "v2.17.24", "superseded",
        "v2.17.25", "v2.17.26", "v2.18.0", "v2.18.1", "v2.18.2",
        "v2.18.3",
        "not released",
        "2757822e", "inherited limitation", "no history rewrite",
        "no re-release", "no verifier weakening",
    )
    for relative in ("README.md", "CHANGELOG.md"):
        try:
            prose = " ".join(
                (repository / relative).read_text(encoding="utf-8")
                .replace(">", "").split()
            )
        except OSError as exc:
            errors.append(f"cannot read {relative}: {exc}")
            continue
        missing = [phrase for phrase in required_doc_phrases if phrase not in prose]
        if missing:
            errors.append(f"{relative} misses forward-history truth: {', '.join(missing)}")

    required_tests = (
        "taskplane/tests/test_r0001_repository_default_branch.py",
        "taskplane/tests/test_r0001_release_green.py",
        "taskplane/tests/test_r0001_compatibility.py",
    )
    for relative in required_tests:
        if not (repository / relative).is_file():
            errors.append(f"required forward-release test file is missing: {relative}")
    try:
        workflow = (repository / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8")
    except OSError as exc:
        workflow = ""
        errors.append(f"cannot read CI workflow: {exc}")
    if "python scripts/ci_evals.py --verify-release-surface --json" not in workflow:
        errors.append("CI does not execute the forward-release surface proof")
    python_312 = re.search(
        r"\n  tests:\n(?P<body>.*?)(?=\n  [a-zA-Z0-9_-]+:\n)",
        workflow, re.DOTALL,
    )
    if python_312 is None or not all(
        marker in python_312.group("body")
        for marker in (
            'python-version: "3.12"',
            "Execute the frozen authoritative pytest suite",
            '--ci-cell "$cell"',
        )
    ):
        errors.append(
            "the real Python 3.12 tests job does not execute the complete "
            "settings-derived test surface"
        )

    archives = {}
    surface_members = (
        "taskplane/release_evidence.py",
        "taskplane/operational-settings.json",
        "taskplane/settings_inventory.json",
        "taskplane/test_portfolio.json",
        "lenses/references/prompt-injection-defense.md",
    )
    with tempfile.TemporaryDirectory(prefix="taskplane-release-surface-") as tmp:
        for name, script in (
            ("openai", "package_openai.py"),
            ("claude", "package_claude.py"),
        ):
            try:
                path = repository / "scripts" / script
                spec = importlib.util.spec_from_file_location(
                    f"_forward_surface_{name}", path)
                if spec is None or spec.loader is None:
                    raise RuntimeError(f"cannot load {script}")
                packager = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(packager)
                files = (
                    packager.package_files(packager.load_manifest())
                    if name == "openai" else packager.package_files()
                )
                archive_path = Path(tmp) / f"{name}.zip"
                packager.write_zip(files, archive_path)
                if name == "openai":
                    packager.validate_archive(archive_path)
                else:
                    packager.validate_archive(
                        archive_path, release.get("CURRENT_VERSION"))
                with zipfile.ZipFile(archive_path) as archive:
                    member_digests = {}
                    for relative in surface_members:
                        source = (repository / relative).read_bytes()
                        member = archive.read("taskplane/" + relative)
                        if member != source:
                            errors.append(
                                f"{name} archive has stale bytes for {relative}")
                        member_digests[relative] = hashlib.sha256(member).hexdigest()
                    extract_root = Path(tmp) / f"{name}-installed"
                    archive.extractall(extract_root)
                    installed_import, installed_proof = \
                        _installed_runtime_probe(extract_root / "taskplane")
                    if (installed_import.returncode != 0 or
                            installed_proof != expected_installed or
                            not isinstance(installed_proof, dict) or
                            installed_proof.get("settings_receipt_digest") !=
                            installed_proof.get("settings_effective_digest")):
                        errors.append(
                            f"{name} installed runtime does not load exact "
                            "settings source/effective digests")
                    archives[name] = {
                        "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
                        "member_count": len(archive.namelist()),
                        "surface_member_digests": member_digests,
                        "settings": installed_proof,
                    }
            except Exception as exc:
                errors.append(f"{name} release surface failed: {exc}")

    proof = {
        "schema": FORWARD_RELEASE_SURFACE_SCHEMA,
        "status": "release-surface-green" if not errors else "refused",
        "version": release.get("CURRENT_VERSION"),
        "previous_version": release.get("COMPATIBILITY_PREVIOUS_VERSION"),
        "last_released_version": release.get("PREVIOUS_VERSION"),
        "historical_graph_revision": release.get("HISTORICAL_GRAPH_REVISION"),
        "manifest_versions": versions,
        "archives": archives,
        "released": False,
        "cryptographic_authenticity_claimed": False,
        "errors": errors,
    }
    encoded = json.dumps(proof, sort_keys=True, separators=(",", ":"))
    proof["fingerprint"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return proof


def _report_forward_release_surface(proof, as_json):
    if as_json:
        print(json.dumps(proof, indent=2, sort_keys=True))
        return
    print(f"forward release surface: {proof['status']}")
    print(f"version: {proof['version']} (not released)")
    print(f"archives: {', '.join(sorted(proof['archives'])) or 'none'}")
    for error in proof["errors"]:
        print(f"error: {error}")


def classify_ci_commit_proof(*, fetch_receipt, head_sha, remote_sha,
                             checked_sha, ahead_count, behind_count,
                             receipts):
    """Classify immutable CI evidence without performing I/O.

    ``pushed_green`` is intentionally a narrow conjunction.  Every other
    shape remains local evidence or a refusal; no caller can rename an ahead,
    behind, stale, fetchless, or receipt-mismatched result after the fact.
    """
    errors = []
    malformed = False
    fetch = dict(fetch_receipt) if isinstance(fetch_receipt, dict) else {}

    if fetch.get("performed") is not True:
        errors.append("explicit fetch was not performed")
        malformed = True
    elif fetch.get("ok") is not True:
        errors.append("explicit fetch failed")
        malformed = True
    if fetch.get("remote") != "origin":
        errors.append("fetch receipt does not name remote origin")
        malformed = True
    if fetch.get("ref") != "refs/remotes/origin/main":
        errors.append("fetch receipt does not name refs/remotes/origin/main")
        malformed = True

    sha_values = (("HEAD", head_sha), ("origin/main", remote_sha),
                  ("checked_sha", checked_sha))
    for label, value in sha_values:
        if not isinstance(value, str) or not _FULL_SHA.fullmatch(value):
            errors.append(f"{label} is not a full commit SHA")
            malformed = True
    if head_sha != checked_sha:
        errors.append("HEAD does not equal checked_sha")
    if remote_sha != checked_sha:
        errors.append("origin/main does not equal checked_sha")

    for label, count in (("ahead", ahead_count), ("behind", behind_count)):
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            errors.append(f"{label} count is unavailable or malformed")
            malformed = True
        elif count:
            errors.append(f"{label} count is {count}, expected 0")

    by_name = {}
    if not isinstance(receipts, list):
        errors.append("check receipts must be a JSON list")
        malformed = True
        receipt_rows = []
    else:
        receipt_rows = receipts
    required = set(PUSHED_GREEN_REQUIRED_CHECKS)
    for index, row in enumerate(receipt_rows):
        if not isinstance(row, dict):
            errors.append(f"check receipt {index} is malformed")
            malformed = True
            continue
        name = row.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"check receipt {index} has no valid name")
            malformed = True
            continue
        if name not in required:
            errors.append(f"unknown required check receipt: {name}")
            malformed = True
            continue
        if name in by_name:
            errors.append(f"duplicate required check receipt: {name}")
            malformed = True
            continue
        by_name[name] = {
            "name": name,
            "sha": row.get("sha"),
            "conclusion": row.get("conclusion"),
        }

    ordered_receipts = []
    for name in PUSHED_GREEN_REQUIRED_CHECKS:
        row = by_name.get(name)
        if row is None:
            errors.append(f"missing required check: {name}")
            malformed = True
            continue
        ordered_receipts.append(row)
        if row["sha"] != checked_sha:
            errors.append(f"required check receipt SHA mismatch: {name}")
        if row["conclusion"] != "success":
            errors.append(f"required check is not successful: {name}")

    status = "pushed_green" if not errors else (
        "refused" if malformed else "local_green"
    )
    return {
        "schema": CI_COMMIT_PROOF_SCHEMA,
        "status": status,
        "fetch_receipt": fetch,
        "head_sha": head_sha,
        "remote_sha": remote_sha,
        "checked_sha": checked_sha,
        "ahead_count": ahead_count,
        "behind_count": behind_count,
        "required_check_names": list(PUSHED_GREEN_REQUIRED_CHECKS),
        "required_checks": ordered_receipts,
        "errors": errors,
    }


def _git_fact(root, *args):
    result = subprocess.run(
        ["git", "-C", root, *args], text=True, encoding="utf-8",
        errors="replace", capture_output=True,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def prove_pushed_sha(root, checked_sha, receipts_path):
    """Fetch origin/main and bind repository topology plus check receipts."""
    remote_ref = "refs/remotes/origin/main"
    code, _out, err = _git_fact(
        root, "fetch", "--no-tags", "origin",
        "+refs/heads/main:refs/remotes/origin/main",
    )
    fetch_receipt = {
        "performed": True,
        "ok": code == 0,
        "remote": "origin",
        "ref": remote_ref,
        "returncode": code,
    }
    head_sha = remote_sha = None
    ahead_count = behind_count = None
    head_code, head_out, _ = _git_fact(root, "rev-parse", "--verify",
                                       "HEAD^{commit}")
    if head_code == 0:
        head_sha = head_out
    # A cached tracking ref is never freshness evidence.  Resolve and compare
    # it only after the fetch in this operation succeeded.
    if code == 0:
        remote_code, remote_out, _ = _git_fact(
            root, "rev-parse", "--verify", remote_ref + "^{commit}")
        if remote_code == 0:
            remote_sha = remote_out
        if head_sha and remote_sha:
            ahead_code, ahead_out, _ = _git_fact(
                root, "rev-list", "--count", remote_ref + "..HEAD")
            behind_code, behind_out, _ = _git_fact(
                root, "rev-list", "--count", "HEAD.." + remote_ref)
            if ahead_code == 0 and ahead_out.isdigit():
                ahead_count = int(ahead_out)
            if behind_code == 0 and behind_out.isdigit():
                behind_count = int(behind_out)

    try:
        with io.open(receipts_path, encoding="utf-8") as handle:
            receipts = json.load(handle)
    except (OSError, ValueError):
        receipts = None

    return classify_ci_commit_proof(
        fetch_receipt=fetch_receipt,
        head_sha=head_sha,
        remote_sha=remote_sha,
        checked_sha=checked_sha,
        ahead_count=ahead_count,
        behind_count=behind_count,
        receipts=receipts,
    )


def _report_ci_commit_proof(proof, as_json=False):
    if as_json:
        print(json.dumps(proof, indent=2, sort_keys=True))
        return
    print(f"ci commit proof: {proof['status']}")
    print(f"  HEAD:        {proof['head_sha']}")
    print(f"  origin/main: {proof['remote_sha']}")
    print(f"  checked SHA: {proof['checked_sha']}")
    print(f"  ahead/behind: {proof['ahead_count']}/{proof['behind_count']}")
    for error in proof["errors"]:
        print(f"  - {error}")

# --- the transition table -------------------------------------------------
# The gate is per rubric ITEM. A scalar bar is rejected outright, and
# `evals/negative/no-ledger/` is why in one fixture: it pins `score: 1.0`
# beside `instrument: broken`. `eval_rubric.score` is pass over pass plus
# fail, so a row falling to `no_evidence` leaves the denominator SMALLER and
# the average HIGHER — a bar of "not worse than last time" is passed by an
# instrument going blind, and a row improving while another regresses leaves
# the number exactly where it was.
REGRESSION = "REGRESSION"          # pass -> fail
EVIDENCE_LOST = "EVIDENCE LOST"    # pass -> no_evidence
RETIRED = "STEP RETIRED"           # pass -> n/a
DROPPED = "STEP DROPPED"           # pass -> gone from the manifest
HELD = "held"
IMPROVED = "IMPROVED"
NEW = "NEW"
CHANGED = "changed"

# Only a drop FROM a pass blocks: a row that never passed cannot be lowered.
#
# The first two are the specified pair. The last two are an EXTENSION and are
# marked as such, because `inputs_fingerprint` digests the SKILL's source
# files and not the scenario manifest: editing `evals/scenarios/<skill>.json`
# to declare a failing row `applicable: false`, or deleting the row outright,
# moves no fingerprint and fires no staleness. Without these two rows the
# cheapest way past this gate would be to delete the rubric item, which is
# the same laundering the gate exists to stop, done one file over.
BLOCKING_TRANSITIONS = {
    ("pass", "fail"): REGRESSION,
    ("pass", "no_evidence"): EVIDENCE_LOST,
    ("pass", "n/a"): RETIRED,
    ("pass", None): DROPPED,
}

# --- what bounds a waiver -------------------------------------------------
# Because the acceptor is NOT authenticated (below), the only honest control
# left is to make an unauthenticated waiver cost something to KEEP. Two bounds,
# both required, on two different axes:
#
#   inputs_fingerprint  RELEVANCE. The flow the waiver was written about. When
#                       the skill moves, the drop that reappears under it is a
#                       DIFFERENT drop and deserves a fresh human sentence.
#                       This is the axis that matters: it is tied to the thing
#                       that changed rather than to the calendar, and it is
#                       computable from the baseline the reader is holding.
#   expires             WALL CLOCK. The second axis, and the one that forces a
#                       re-reading even when nothing moved. `2999-01-01` would
#                       satisfy a required-field check and bound nothing, so
#                       the horizon below caps how far out it may be set.
#
# Neither is grandfathered. There is no waiver in the tree yet, which makes
# strict free today and impossible later.
# `YYYY-MM-DD`, UTC, expiring at the END of the named day. One spelling, so a
# waiver log stays greppable and a date is never ambiguous by timezone.
WAIVER_DATE_FORMAT = "%Y-%m-%d"

# How far out an expiry may be set. A quarter is long enough that renewing is
# not busywork and short enough that the sentence is re-read by someone who
# still remembers why it was written.
WAIVER_MAX_HORIZON_DAYS = 90

# Reported by an ORDINARY green gate run this many days before the bound. An
# expiry whose first appearance is a broken build teaches people to renew
# without reading, which is the failure this whole section exists to avoid.
WAIVER_WARN_DAYS = 14

# The shortest fingerprint prefix a waiver may name. A 64-character hex copied
# by hand is a field people get wrong; 12 hex characters still name one flow.
FINGERPRINT_BOUND_MIN_CHARS = 12

# What a waiver is worth against the run in front of it.
IN_FORCE = "in force"
EXPIRING = "EXPIRING"
EXPIRED = "EXPIRED"
OUT_OF_SCOPE = "OUT OF SCOPE"

# Stated here, printed at every applied waiver, and asserted by the suite —
# so it cannot be a comment someone scrolls past.
ACCEPTOR_IS_NOT_AUTHENTICATED = (
    "the acceptor string is NOT AUTHENTICATED. In this product the committer "
    "is routinely the model, and it satisfies this check by typing a human's "
    "name. What the check buys is ATTRIBUTION: every lowering is a named, "
    "reasoned line in the diff of an append-only log. What it does not buy "
    "is authorisation, consent, or evidence that the named person ever saw "
    "it. Read a waiver as a claim to be checked, never as a signature.")


def _es():
    """`eval_scenario`, imported late — the same convention `_known_steps`
    uses, so this script still runs its corpus leg without the engine on the
    path."""
    import eval_scenario
    return eval_scenario


def _er():
    import eval_rubric
    return eval_rubric


# --- where things live ----------------------------------------------------

def baseline_path(root, skill) -> str:
    return os.path.join(root, BASELINE_DIRNAME, f"{skill}.json")


def waiver_path(root, skill) -> str:
    """Append-only JSONL, one waiver per line. A row is ADDED, never edited
    over the top of the last one, so `git log -p` on this file is the whole
    history of every bar that was ever lowered."""
    return os.path.join(root, BASELINE_DIRNAME, f"{skill}.waivers.jsonl")


def runs_dir(root, skill) -> str:
    return os.path.join(root, RUNS_DIRNAME, skill)


def runs_v2_dir(root, skill) -> str:
    return os.path.join(root, RUNS_V2_DIRNAME, skill)


def _posix(path) -> str:
    return str(path).replace(os.sep, "/")


# --- the scenario ---------------------------------------------------------

def load_scenario(root, skill):
    """(scenario, error). An unknown skill NAMES what it looked for.

    Guessing — nearest match, or falling through to some other mode — is the
    same family as the argv defect this CLI was carrying: the run keeps going
    and reports on something the caller did not ask about.
    """
    es = _es()
    found = es.discover(root)
    path = found.get(skill)
    if not path:
        known = ", ".join(sorted(found)) or "none"
        return None, (f"unknown skill {skill!r}: no manifest at "
                      f"{_posix(os.path.join(es.scenario_dir(root), skill))}"
                      f".json — the skills with a scenario are {known}")
    try:
        return es.load(path), None
    except (OSError, ValueError) as exc:
        return None, f"{_posix(path)}: {exc}"


# --- the run record -------------------------------------------------------

def _run_sort_key(entry):
    run = entry["run"] or {}
    def _num(key):
        value = run.get(key)
        return float(value) if isinstance(value, (int, float)) else 0.0
    return (_num("frozen_at"), _num("recorded_at"), entry["run_id"])


def find_runs(root, skill) -> list:
    """Every recorded run for one skill, oldest first.

    A directory under `evals/runs/<skill>/` counts only when it carries a
    `run.json` that does not claim to be some OTHER skill's run. Without that
    second check a misfiled record would be graded against a rubric it was
    never recorded for, and every row would read as a real failure.
    """
    out = []
    for base in (runs_dir(root, skill), runs_v2_dir(root, skill)):
        try:
            names = sorted(os.listdir(base))
        except OSError:
            continue
        for name in names:
            path = os.path.join(base, name)
            if not os.path.isdir(path) or os.path.islink(path):
                continue
            run, err = _read_json(os.path.join(path, "run.json"))
            if err or not isinstance(run, dict):
                continue
            if run.get("skill") not in (None, skill):
                continue
            out.append({"path": path, "run_id": run.get("run_id") or name,
                        "run": run})
    out.sort(key=_run_sort_key)
    return out


def pick_run(root, skill, run_dir=None):
    """(entry, error) for the run to grade — the newest, or one named.

    A skill with no recorded run is an ERROR, never an empty record: an empty
    record scores `no_evidence` in every row, so falling through would print
    a full card of honest unknowns for a run that never happened.
    """
    if run_dir:
        path = os.path.abspath(run_dir)
        run, err = _read_json(os.path.join(path, "run.json"))
        if err or not isinstance(run, dict):
            return None, (f"{_posix(path)} carries no readable run.json, so "
                          f"it is not a recorded run "
                          f"({err or 'not an object'})")
        return {"path": path, "run_id": run.get("run_id")
                or os.path.basename(path), "run": run}, None
    runs = find_runs(root, skill)
    if not runs:
        return None, (f"no recorded run for {skill!r}: nothing under "
                      f"{_posix(runs_dir(root, skill))}/ or "
                      f"{_posix(runs_v2_dir(root, skill))}/ carries a run.json. "
                      f"Record one with scripts/eval_record.py before scoring "
                      f"or gating.")
    return runs[-1], None


def run_identity(entry, root=None) -> dict:
    """The provenance a baseline carries.

    Two things are deliberately absent. No wall clock: setting the same
    baseline from the same run twice must produce a byte-identical file, or
    `git diff evals/baselines/` stops being where a lowering shows up. And no
    absolute path: a baseline is COMMITTED, and one checkout's `/home/…`
    prefix is noise in every other checkout's diff.
    """
    run = entry["run"] or {}
    path = entry["path"]
    if root:
        rel = os.path.relpath(path, root)
        if not rel.startswith(os.pardir):
            path = rel
    return {
        "run_id": entry["run_id"],
        "path": _posix(path),
        "mode": run.get("mode"),
        "host": run.get("host"),
        "hook_active": bool(run.get("hook_active")),
        "recorded_at": run.get("recorded_at"),
        "target_head": run.get("target_head"),
        "effective_tokens": run.get("effective_tokens"),
    }


def eligibility_problem(entry) -> "str | None":
    """None when this run may set or satisfy a bar; the RECORD's own reason
    when it may not.

    Read out of `run.json` rather than re-derived from `mode` and
    `hook_active`: a second implementation of that judgement would be free to
    disagree with the record it is reading, and the recorder is the thing
    that was actually there.
    """
    run = entry["run"] or {}
    if run.get("baseline_eligible"):
        return None
    said = run.get("baseline_reason") or "no reason recorded"
    return (f"run {entry['run_id']} is not baseline-eligible "
            f"(mode: {run.get('mode')}, hook_active: "
            f"{bool(run.get('hook_active'))}) — {said}")


# --- the baseline ---------------------------------------------------------

def source_digests(root, source_files) -> dict:
    """{relative path: digest of THAT file's flow extract}.

    Stored beside the whole-input fingerprint so a STALE verdict can name the
    file whose flow moved instead of printing two hexes and leaving the
    reader to diff a skill by hand.
    """
    es = _es()
    return {str(rel): es.fingerprint(root, [rel])
            for rel in sorted(str(f) for f in (source_files or ()))}


def make_baseline(root, scenario, card, entry) -> dict:
    """The stored bar: a VECTOR, its inputs, and whose run it came from.

    The scalar is stored under `score_for_humans` and under no shorter name.
    A key called `score` sitting in a file called a baseline is an invitation
    to compare it, and comparing it is the one thing this design refuses.
    """
    sources = scenario.get("source_files") or ()
    return {
        "schema": BASELINE_SCHEMA,
        "skill": scenario.get("skill"),
        "verdicts": dict(card["verdicts"]),
        "inputs_fingerprint": (entry["run"] or {}).get("inputs_fingerprint"),
        "source_files": source_digests(root, sources),
        "run": run_identity(entry, root),
        "instrument": card.get("instrument"),
        "score_for_humans": card.get("score"),
    }


def read_baseline(root, skill):
    """(baseline, error). A baseline that cannot be read is an ERROR.

    Never an empty vector: an empty vector carries no `pass`, so every drop
    becomes a non-transition and the gate goes green on a corrupt file.
    """
    path = baseline_path(root, skill)
    if not os.path.isfile(path):
        return None, (f"no baseline for {skill!r} at {_posix(path)} — set one "
                      f"from an observed run with "
                      f"`ci_evals.py --set-baseline {skill}`")
    value, err = _read_json(path)
    if err:
        return None, f"{_posix(path)}: {err}"
    if not isinstance(value, dict) or not isinstance(value.get("verdicts"),
                                                     dict):
        return None, (f"{_posix(path)}: a baseline is an object carrying a "
                      f"`verdicts` vector")
    return value, None


def write_baseline(root, skill, baseline) -> str:
    path = baseline_path(root, skill)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(baseline, f, indent=2, sort_keys=True)
        f.write("\n")
    return path


# --- staleness ------------------------------------------------------------

def _moved_inputs(root, baseline, sources) -> str:
    """", changed inputs: a, b" — named from the per-file digests."""
    was = baseline.get("source_files")
    if not isinstance(was, dict):
        return ""
    now = source_digests(root, sources)
    changed = sorted(set(was) ^ set(now))
    changed += sorted(k for k in set(was) & set(now) if was[k] != now[k])
    if not changed:
        return ""
    return ". Changed inputs: " + ", ".join(sorted(set(changed)))


def stale_reasons(root, scenario, baseline, entry) -> list:
    """Every way this comparison is graded against a skill that has moved.

    A baseline graded against a skill that has since changed is not a
    baseline, it is a fossil — and the failure mode it closes is the silent
    one: "nobody re-records" passes a per-item gate perfectly, because the
    vector does not move at all.

    Three comparisons, because there are three things that can drift apart:
      A  the baseline and the run were graded against different skills
      B  the RUN predates a change to the skill (nobody re-recorded)
      C  the MANIFEST no longer describes its own source files
    """
    es = _es()
    out = []
    sources = scenario.get("source_files") or ()
    now = es.fingerprint(root, sources)
    base_fp = baseline.get("inputs_fingerprint")
    run_fp = (entry["run"] or {}).get("inputs_fingerprint")
    moved = _moved_inputs(root, baseline, sources)

    if run_fp is None:
        out.append("STALE: this run records no inputs_fingerprint, so there "
                   "is nothing to tell a fresh run from a fossil")
    elif base_fp != run_fp:
        out.append(f"STALE: the baseline was graded at inputs {base_fp} and "
                   f"this run at {run_fp} — they are not the same skill"
                   + moved)
    elif run_fp != now:
        out.append(f"STALE: this run was recorded at inputs {run_fp} and the "
                   f"skill's flow now digests to {now} — the skill changed "
                   f"and nobody re-recorded" + moved)
    manifest = es.stale(scenario, root)
    if manifest:
        out.append(manifest + " — the manifest no longer describes its own "
                              "source files")
    return out


# --- waivers --------------------------------------------------------------

def agent_names(root) -> tuple:
    """Every agent this repo ships, by file name.

    Read off `agents/` rather than hardcoded, so an agent added tomorrow is
    covered the day it lands instead of the day somebody remembers.
    """
    out = []
    try:
        names = os.listdir(os.path.join(root, AGENTS_DIRNAME))
    except OSError:
        return ()
    for name in sorted(names):
        if name.endswith(".md"):
            out.append(name[:-3])
    return tuple(out)


def machine_identities(root) -> tuple:
    """The names the machine already answers to: every agent, plus every
    governed skill (`tp-engineering` is both a skill and an agent, and either
    spelling in an acceptor field is the machine signing for itself)."""
    try:
        skills = tuple(_es().GOVERNED_SKILLS)
    except (ImportError, AttributeError):   # pragma: no cover - engine absent
        skills = ()
    return tuple(sorted(set(agent_names(root)) | set(skills)))


def acceptor_problem(acceptor, root) -> "str | None":
    """None when the acceptor may stand; a reason when it may not.

    THIS IS NOT AUTHENTICATION — see `ACCEPTOR_IS_NOT_AUTHENTICATED`. It
    rejects the two identities the machine already answers to and stops
    there, deliberately: a longer blocklist would read as a stronger control
    than it is, and the honest control here is that the lowering is visible,
    named and attributed in the diff.
    """
    said = str(acceptor or "").strip()
    if not said:
        return "a waiver needs an `acceptor` — an unattributed lowering is " \
               "a bar moved by nobody"
    if said.lower().startswith(ROLE_MARKER_PREFIX):
        return (f"acceptor {said!r} carries the {ROLE_MARKER_PREFIX} marker, "
                f"which is the engine's own role identity — a role cannot "
                f"accept a lowering of the bar it is measured against")
    if said.lower() in machine_identities(root):
        return (f"acceptor {said!r} is an agent this repo ships, so it is the "
                f"machine signing for itself; name the person who accepted "
                f"this")
    return None


def _today():
    return datetime.datetime.now(datetime.timezone.utc).date()


def parse_expiry(value):
    """(date, error). One spelling of a date, refused rather than guessed.

    `dateutil`-style leniency would accept "when #412 lands" as some date or
    other and print a bound nobody wrote. A field whose value the reader
    cannot predict is not a bound.
    """
    said = str(value or "").strip()
    if not said:
        return None, ("carries no `expires` — a waiver with no wall-clock "
                      "bound is one nobody is ever prompted to re-read")
    try:
        return datetime.datetime.strptime(
            said, WAIVER_DATE_FORMAT).date(), None
    except ValueError:
        return None, (f"`expires` is {said!r}, which is not a "
                      f"YYYY-MM-DD date — a bound nobody can compute is not "
                      f"a bound")


def _fingerprint_bound(value):
    """(prefix, error) for the flow a waiver was written about."""
    said = str(value or "").strip().lower()
    if not said:
        return None, ("carries no `inputs_fingerprint` — an unbounded waiver "
                      "covers that step's drops forever, including the "
                      "regression nobody has written yet. Name the flow this "
                      "was argued about (the baseline's inputs_fingerprint)")
    if len(said) < FINGERPRINT_BOUND_MIN_CHARS or any(
            c not in "0123456789abcdef" for c in said):
        return None, (f"`inputs_fingerprint` is {said!r}, which is not at "
                      f"least {FINGERPRINT_BOUND_MIN_CHARS} hex characters of "
                      f"an inputs fingerprint")
    return said, None


def bound_problem(row, today=None):
    """None when this row declares both bounds in a form the gate can check;
    a reason when it does not.

    Refused at READ time, beside the missing-reason and missing-acceptor
    checks, because an unbounded waiver is the same defect they are: a row
    that cannot be evaluated must never be quietly skipped.
    """
    _, bad = _fingerprint_bound(row.get("inputs_fingerprint"))
    if bad:
        return bad
    when, bad = parse_expiry(row.get("expires"))
    if bad:
        return bad
    horizon = (today or _today()) + datetime.timedelta(
        days=WAIVER_MAX_HORIZON_DAYS)
    if when > horizon:
        return (f"`expires` is {when.isoformat()}, more than "
                f"{WAIVER_MAX_HORIZON_DAYS} days out — that is a waiver that "
                f"never asks to be re-read, wearing a date")
    return None


def waiver_status(row, run_fp, today=None):
    """What this waiver is worth against the run in front of it.

    {state, covers, blocking, why, days_left}. Pure — a row, a fingerprint
    and a date in; a verdict out — so the states are a table rather than a
    description of branching somewhere else.

    SCOPE IS CHECKED BEFORE THE CLOCK. A waiver about a flow that has since
    moved has nothing left for anyone to re-read, so it retires: reported,
    never blocking. A waiver that still speaks about THIS flow and has run out
    of time is the opposite — somebody has to read it again, and it blocks
    until they do.
    """
    today = today or _today()
    want, bad = _fingerprint_bound(row.get("inputs_fingerprint"))
    when, when_bad = parse_expiry(row.get("expires"))
    if bad or when_bad:      # pragma: no cover - read_waivers refuses these
        return {"state": OUT_OF_SCOPE, "covers": False, "blocking": False,
                "why": bad or when_bad, "days_left": None,
                "expires": row.get("expires")}
    have = str(run_fp or "").strip().lower()
    if not have or not have.startswith(want):
        return {
            "state": OUT_OF_SCOPE, "covers": False, "blocking": False,
            "days_left": None, "expires": when.isoformat(),
            "why": (f"written about inputs {want[:16]}, and this run is at "
                    f"{have[:16] or 'no fingerprint at all'} — the flow it "
                    f"was argued about has moved, so it covers nothing here"),
        }
    days_left = (when - today).days
    if days_left < 0:
        return {
            "state": EXPIRED, "covers": False, "blocking": True,
            "days_left": days_left, "expires": when.isoformat(),
            "why": (f"expired on {when.isoformat()} and still speaks about "
                    f"this flow — re-read the reason and append a renewal, "
                    f"or fix the drop"),
        }
    if days_left <= WAIVER_WARN_DAYS:
        return {
            "state": EXPIRING, "covers": True, "blocking": False,
            "days_left": days_left, "expires": when.isoformat(),
            "why": (f"expires on {when.isoformat()}, in {days_left} day(s) — "
                    f"read it again before it stops covering anything"),
        }
    return {"state": IN_FORCE, "covers": True, "blocking": False,
            "days_left": days_left, "expires": when.isoformat(),
            "why": f"in force until {when.isoformat()}"}


def _same_coverage(a, b) -> bool:
    """Two rows that narrow to the same transition — step and both ends."""
    return all(a.get(k) == b.get(k) for k in ("step", "from", "to"))


def waiver_notices(waivers, run_fp, today=None) -> list:
    """Annotate every row with its status and report the ones that are not
    plainly in force — including on a GREEN run.

    Two jobs in one pass. The status is what `apply_waivers` reads, and the
    notices are what an ordinary gate prints, so a waiver approaching its
    bound is surfaced by the tool rather than by a broken build.

    SUPERSESSION. The log is append-only: an expired row cannot be deleted,
    only answered. A later row covering the same transition and still in force
    IS that answer, so the expired one stops blocking — and is still printed,
    because the history of every bar that was ever lowered is the point.
    """
    for row in waivers:
        row["_status"] = waiver_status(row, run_fp, today)
    out = []
    for row in waivers:
        st = row["_status"]
        if st["state"] == EXPIRED:
            renewal = next((r for r in waivers
                            if r.get("_line", 0) > row.get("_line", 0)
                            and _same_coverage(r, row)
                            and _covers(r)), None)
            if renewal:
                st["blocking"] = False
                st["superseded_by"] = renewal.get("_line")
                st["why"] += (f"; superseded by the renewal on line "
                              f"{renewal.get('_line')}")
        if st["state"] == IN_FORCE:
            continue
        out.append({"line": row.get("_line"), "step": row.get("step"),
                    "state": st["state"], "why": st["why"],
                    "expires": st.get("expires"),
                    "days_left": st.get("days_left"),
                    "acceptor": row.get("acceptor"),
                    "reason": row.get("reason"),
                    "blocking": st["blocking"]})
    return out


def read_waivers(root, skill):
    """(waivers, problems). Every row is checked; a bad row is NAMED.

    A malformed, unattributed or UNBOUNDED waiver blocks rather than being
    skipped: the row someone could not write correctly may be exactly the row
    that was meant to cover the drop being gated, and silently ignoring it
    turns a broken control into a green one.
    """
    path = waiver_path(root, skill)
    rows, problems = [], []
    if not os.path.isfile(path):
        return rows, problems
    try:
        with io.open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as exc:
        return rows, [f"waiver log {_posix(path)} is unreadable ({exc})"]
    for n, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            problems.append(f"waiver line {n} is not JSON, so nothing on it "
                            f"waives anything")
            continue
        if not isinstance(row, dict):
            problems.append(f"waiver line {n} is not an object")
            continue
        if not str(row.get("step") or "").strip():
            problems.append(f"waiver line {n} names no `step` — a blanket "
                            f"waiver is not a waiver")
            continue
        if not str(row.get("reason") or "").strip():
            problems.append(f"waiver line {n} (step {row.get('step')!r}) "
                            f"carries no `reason`")
            continue
        bad = acceptor_problem(row.get("acceptor"), root)
        if bad:
            problems.append(f"waiver line {n} (step {row.get('step')!r}): "
                            f"{bad}")
            continue
        bad = bound_problem(row)
        if bad:
            problems.append(f"waiver line {n} (step {row.get('step')!r}) "
                            f"{bad}")
            continue
        row["_line"] = n
        rows.append(row)
    return rows, problems


def _narrows_to(row, transition) -> bool:
    """`from`/`to` are optional and NARROW the waiver. A waiver written for an
    evidence gap must not silently absorb a later real failure of the same
    row, which is how one accepted exception becomes a permanent hole."""
    return (row.get("step") == transition["step"]
            and row.get("from") in (None, transition["was"])
            and row.get("to") in (None, transition["now"]))


def _covers(row) -> bool:
    """Whether this row may lower a bar — FAIL CLOSED.

    The bounds are evaluated by `waiver_notices()` against the run being
    gated. A row that never went through it waives nothing, so a caller that
    forgot the step gets a loud gate full of unwaived drops rather than the
    silent forever-waiver this whole section exists to remove.
    """
    return bool((row.get("_status") or {}).get("covers"))


def match_waiver(transition, waivers) -> "dict | None":
    """The waiver covering ONE transition, or None.

    A row still WITHIN its bounds wins, and the last such row wins over an
    earlier one — a renewal is appended below the sentence it renews. When
    nothing is in force the last row that merely names the transition is
    returned anyway, so the gate can say which waiver stopped applying
    instead of letting the drop surface as if it were new.
    """
    rows = [r for r in waivers if _narrows_to(r, transition)]
    live = [r for r in rows if _covers(r)]
    if live:
        return live[-1]
    return rows[-1] if rows else None


# --- the comparison -------------------------------------------------------

def compare(baseline_verdicts, verdicts, claims=None) -> list:
    """One transition per rubric item, in manifest order then baseline order.

    Pure: two vectors in, transitions out. The blocking decision is a lookup
    in `BLOCKING_TRANSITIONS`, so the table above IS the specification rather
    than a description of some branching elsewhere.
    """
    claims = claims or {}
    order = list(verdicts) + [s for s in baseline_verdicts
                              if s not in verdicts]
    out = []
    for step in order:
        was = baseline_verdicts.get(step)
        now = verdicts.get(step)
        kind = BLOCKING_TRANSITIONS.get((was, now))
        blocking = kind is not None
        if kind is None:
            if was is None:
                kind = NEW
            elif was == now:
                kind = HELD
            elif now == "pass":
                kind = IMPROVED
            else:
                kind = CHANGED
        out.append({"step": step, "was": was, "now": now, "kind": kind,
                    "blocking": blocking, "waiver": None,
                    "claim": claims.get(step)})
    return out


def apply_waivers(transitions, waivers) -> list:
    """A recorded waiver, still within its bounds, is the ONLY thing that lets
    a drop pass. It does not hide the line: the transition keeps its kind and
    gains an acceptor.

    A row that names this transition but has run out of bounds is attached as
    `waiver_lapsed` and the drop stays BLOCKING. Dropping it silently would
    leave the reader with a regression that looks new and a waiver, one file
    over, that looks like it is still doing something.
    """
    for t in transitions:
        if not t["blocking"]:
            continue
        row = match_waiver(t, waivers)
        if not row:
            continue
        if _covers(row):
            t["waiver"] = row
            t["blocking"] = False
        else:
            t["waiver_lapsed"] = row
    return transitions


# --- rendering ------------------------------------------------------------

def _claims(scenario) -> dict:
    return {s.get("id"): s.get("claim")
            for s in (scenario.get("steps") or ()) if isinstance(s, dict)}


def _run_line(entry) -> str:
    run = entry["run"] or {}
    return (f"    run {entry['run_id']}  (mode {run.get('mode')}, hook "
            f"{'active' if run.get('hook_active') else 'INACTIVE'}, "
            f"baseline-eligible "
            f"{'yes' if run.get('baseline_eligible') else 'NO'})")


def render_card(scenario, card, entry) -> None:
    """The per-step table. Every rubric row is printed, verdict and claim,
    including the ones that passed — a card that showed only failures would
    make a row that VANISHED look like a row that was fine."""
    print(f"  {card.get('skill')} — {scenario.get('title') or ''}")
    print(_run_line(entry))
    for step in card["steps"]:
        print(f"    {str(step['id']):<6} {str(step['verdict']):<12} "
              f"{step.get('claim') or ''}")
        if step.get("reason"):
            print(f"    {'':<6} {'':<12} └─ {step['reason']}")
    counts = card["counts"]
    print("    counts   " + "  ".join(f"{v} {counts[v]}"
                                      for v in sorted(counts)))
    print("    universal   " + "  ".join(
        f"{k}={v}" for k, v in sorted((card.get("universal") or {}).items())))
    print("    records   " + "  ".join(
        f"{k}={v}" for k, v in sorted((card.get("records") or {}).items())))
    print(f"    instrument   {card.get('instrument')}"
          + (f" — {card['instrument_reason']}"
             if card.get("instrument_reason") else ""))
    print(f"    score {card.get('score')} — for humans only. The gate is per "
          f"item, never on this number: one row improving while another "
          f"regresses leaves it flat, and a row falling to no_evidence "
          f"RAISES it.")
    print()


def _score_skill(root, skill, run_dir=None, want_json=False) -> int:
    scenario, err = load_scenario(root, skill)
    if err:
        print(f"evals: {err}", file=sys.stderr)
        return EXIT_USAGE
    entry, err = pick_run(root, skill, run_dir)
    if err:
        print(f"evals: {err}", file=sys.stderr)
        return EXIT_USAGE
    card = _er().evaluate(scenario, _er().read_record(entry["path"]))
    card["run"] = run_identity(entry, root)
    if want_json:
        print(json.dumps(card, indent=2, default=str))
        return EXIT_OK
    render_card(scenario, card, entry)
    for problem in _es().validate(scenario, root):
        print(f"    manifest problem: {problem}", file=sys.stderr)
    return EXIT_OK


def _worst(codes) -> int:
    """The exit code for a fan-out over several skills.

    A real BLOCK outranks a "cannot answer": if one skill regressed and
    another simply has no baseline yet, the run must exit 1, not 2. Plain
    `max()` would relabel the regression as a usage error and hide it behind
    a setup message.
    """
    codes = list(codes) or [EXIT_OK]
    return EXIT_BLOCKED if EXIT_BLOCKED in codes else max(codes)


def _set_baseline(root, skill, run_dir=None, want_json=False) -> int:
    scenario, err = load_scenario(root, skill)
    if err:
        print(f"evals: {err}", file=sys.stderr)
        return EXIT_USAGE
    entry, err = pick_run(root, skill, run_dir)
    if err:
        print(f"evals: {err}", file=sys.stderr)
        return EXIT_USAGE
    bad = eligibility_problem(entry)
    if bad:
        print(f"evals: refusing to set a baseline — {bad}", file=sys.stderr)
        print("evals: an unobserved run can never set a bar; nothing was "
              "written", file=sys.stderr)
        return EXIT_USAGE
    card = _er().evaluate(scenario, _er().read_record(entry["path"]))
    baseline = make_baseline(root, scenario, card, entry)
    path = write_baseline(root, skill, baseline)
    if want_json:
        print(json.dumps(baseline, indent=2, sort_keys=True, default=str))
        return EXIT_OK
    print(f"  baseline for {skill} written to {_posix(path)}")
    print(f"    from run {entry['run_id']}")
    for sid, verdict in card["verdicts"].items():
        print(f"    {str(sid):<6} {verdict}")
    return EXIT_OK


def _gate_skill(root, skill, run_dir=None, want_json=False) -> int:
    scenario, err = load_scenario(root, skill)
    if err:
        print(f"evals: {err}", file=sys.stderr)
        return EXIT_USAGE
    entry, err = pick_run(root, skill, run_dir)
    if err:
        print(f"evals: {err}", file=sys.stderr)
        return EXIT_USAGE
    # An in-session run "may never set OR SATISFY a baseline" — the recorder's
    # own words. Grading one anyway manufactures regressions out of a fan-out
    # nobody observed, and a gate that cries wolf is answered with waivers.
    bad = eligibility_problem(entry)
    if bad:
        print(f"evals: cannot gate — {bad}", file=sys.stderr)
        return EXIT_USAGE
    baseline, err = read_baseline(root, skill)
    if err:
        print(f"evals: {err}", file=sys.stderr)
        return EXIT_USAGE

    card = _er().evaluate(scenario, _er().read_record(entry["path"]))
    waivers, waiver_problems = read_waivers(root, skill)
    # The bounds are read against the run being GATED: at this point the run,
    # the baseline and the skill on disk all agree on a fingerprint, or
    # `stale_reasons()` below has already blocked the whole comparison.
    notices = waiver_notices(waivers,
                             (entry["run"] or {}).get("inputs_fingerprint"))
    transitions = apply_waivers(
        compare(baseline["verdicts"], card["verdicts"], _claims(scenario)),
        waivers)
    stale = stale_reasons(root, scenario, baseline, entry)
    # ONE source of truth for the exit code, computed before either renderer.
    # Deriving it twice — once for `--json` and once for the table — is how
    # two spellings of the same gate come to disagree.
    blocking = [t for t in transitions if t["blocking"]]
    lapsed = [n for n in notices if n["blocking"]]
    blocked = (bool(blocking) or bool(stale) or bool(waiver_problems)
               or bool(lapsed))

    if want_json:
        print(json.dumps({
            "skill": skill, "blocked": blocked,
            "run": run_identity(entry, root),
            "baseline_run": baseline.get("run"),
            "transitions": transitions, "stale": stale,
            "waiver_problems": waiver_problems,
            "waiver_notices": notices,
            "acceptor_disclaimer": ACCEPTOR_IS_NOT_AUTHENTICATED,
        }, indent=2, default=str))
        return EXIT_BLOCKED if blocked else EXIT_OK

    print(f"  gate — {skill}, per rubric item")
    print(_run_line(entry))
    print(f"    baseline {_posix(baseline_path(root, skill))}  from run "
          f"{(baseline.get('run') or {}).get('run_id')}")
    waived = 0
    for t in transitions:
        line = (f"    {str(t['step']):<6} {str(t['was']):<11} -> "
                f"{str(t['now']):<11} {t['kind']}")
        if t["claim"]:
            line += f"   {t['claim']}"
        print(line)
        if t["waiver"]:
            waived += 1
            st = t["waiver"].get("_status") or {}
            print(f"    {'':<6} WAIVED by {t['waiver'].get('acceptor')!r} — "
                  f"{t['waiver'].get('reason')} "
                  f"[waiver line {t['waiver'].get('_line')}, "
                  f"{st.get('why', 'bounds unknown')}]")
        if t.get("waiver_lapsed"):
            row = t["waiver_lapsed"]
            st = row.get("_status") or {}
            print(f"    {'':<6} {st.get('state', 'UNEVALUATED')} WAIVER on "
                  f"line {row.get('_line')} by {row.get('acceptor')!r} — "
                  f"{st.get('why', 'its bounds were never evaluated')}. "
                  f"This drop is NOT waived: a drop that "
                  f"reappears outside its waiver's bounds is a different "
                  f"drop, and it needs a fresh human sentence.")
    for notice in notices:
        print(f"    WAIVER {notice['state']} — line {notice['line']} "
              f"(step {notice['step']!r}) by {notice['acceptor']!r}: "
              f"{notice['why']}")
        print(f"    {'':<6} it says: {notice['reason']}")
    for reason in stale:
        print(f"    {reason}", file=sys.stderr)
    for problem in waiver_problems:
        print(f"    BAD WAIVER: {problem}", file=sys.stderr)
    if waived:
        print(f"    note: {waived} lowering(s) passed on a recorded waiver. "
              f"{ACCEPTOR_IS_NOT_AUTHENTICATED}")

    for t in blocking:
        print(f"evals: {t['kind']} at rubric item {t['step']!r} "
              f"({t['was']} -> {t['now']}) — {t['claim'] or 'no claim'}",
              file=sys.stderr)
    for notice in lapsed:
        print(f"evals: waiver line {notice['line']} (step {notice['step']!r}, "
              f"accepted by {notice['acceptor']!r}) {notice['state']} — "
              f"{notice['why']}", file=sys.stderr)
    if blocked:
        print(f"evals: gate BLOCKED for {skill} — {len(blocking)} unwaived "
              f"drop(s), {len(stale)} staleness finding(s), "
              f"{len(waiver_problems)} bad waiver row(s), "
              f"{len(lapsed)} expired waiver(s)", file=sys.stderr)
        return EXIT_BLOCKED
    print("    gate OK — every item that passed still passes")
    print()
    return EXIT_OK


def _every_skill(root) -> list:
    return sorted(_es().discover(root))


def _parser():
    """Strict. An unrecognised flag EXITS 2 instead of falling through.

    This is the defect the rest of the file rests on: `main()` hand-parsed
    argv and silently ignored what it did not know, so
    `--totally-invented-flag` scored the workspace and exited 0 — and every
    gate added here would have been one typo away from not running while
    reporting success.
    """
    import argparse
    p = argparse.ArgumentParser(
        prog="ci_evals.py",
        description="evals — was the machinery USED, and is it still?")
    p.add_argument("--corpus", action="store_true",
                   help="score the frozen six-area corpus")
    p.add_argument("--skill", metavar="NAME",
                   help="score one governed skill's newest recorded run")
    p.add_argument("--all-skills", action="store_true",
                   help="every skill with a scenario manifest")
    p.add_argument("--set-baseline", metavar="NAME",
                   help="write evals/baselines/NAME.json from a run record")
    p.add_argument("--gate", action="store_true",
                   help="block on any rubric item that dropped from a pass")
    p.add_argument("--prove-pushed-sha", action="store_true",
                   help="fetch and prove exact pushed-SHA CI evidence")
    p.add_argument("--verify-release-surface", action="store_true",
                   help="prove 2.18.3 manifests and both install archives")
    p.add_argument("--checked-sha", metavar="SHA",
                   help="full commit SHA whose required checks were observed")
    p.add_argument("--check-receipts", metavar="FILE",
                   help="JSON required-check receipts for --prove-pushed-sha")
    p.add_argument("--run", metavar="DIR",
                   help="grade this run record instead of the newest")
    p.add_argument("--root", metavar="DIR", default=ROOT,
                   help="repository root (default: this checkout)")
    p.add_argument("--json", action="store_true", help="machine-readable")
    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(argv)
    root = os.path.abspath(args.root)
    picked = [name for name, on in
              (("--corpus", args.corpus), ("--skill", bool(args.skill)),
               ("--all-skills", args.all_skills),
               ("--set-baseline", bool(args.set_baseline)),
               ("--gate", args.gate),
               ("--prove-pushed-sha", args.prove_pushed_sha),
               ("--verify-release-surface", args.verify_release_surface)) if on]
    if "--corpus" in picked and len(picked) > 1:
        print(f"evals: --corpus scores the frozen corpus and nothing else; "
              f"it cannot be combined with {', '.join(picked[1:])}",
              file=sys.stderr)
        return EXIT_USAGE
    # `--run` names ONE record. Fanning it out over every skill would grade
    # each rubric against a run recorded for a different one, and every row
    # would read as a real failure.
    if args.run and not (args.skill or args.set_baseline):
        print("evals: --run names one recorded run, so it needs --skill or "
              "--set-baseline to say which rubric to grade it against",
              file=sys.stderr)
        return EXIT_USAGE
    if args.prove_pushed_sha:
        if len(picked) != 1:
            print("evals: --prove-pushed-sha cannot be combined with another "
                  "scoring mode", file=sys.stderr)
            return EXIT_USAGE
        if not args.checked_sha or not args.check_receipts:
            print("evals: --prove-pushed-sha requires --checked-sha and "
                  "--check-receipts", file=sys.stderr)
            return EXIT_USAGE
        proof = prove_pushed_sha(root, args.checked_sha,
                                 args.check_receipts)
        _report_ci_commit_proof(proof, args.json)
        return EXIT_OK if proof["status"] == "pushed_green" else EXIT_BLOCKED
    if args.verify_release_surface:
        if len(picked) != 1:
            print("evals: --verify-release-surface cannot be combined with "
                  "another scoring mode", file=sys.stderr)
            return EXIT_USAGE
        proof = verify_forward_release_surface(root)
        _report_forward_release_surface(proof, args.json)
        return EXIT_OK if proof["status"] == "release-surface-green" else EXIT_BLOCKED
    if args.checked_sha or args.check_receipts:
        print("evals: --checked-sha and --check-receipts require "
              "--prove-pushed-sha", file=sys.stderr)
        return EXIT_USAGE
    if args.corpus:
        return _score_corpus(os.path.join(root, "evals"))
    if args.set_baseline:
        return _set_baseline(root, args.set_baseline, args.run, args.json)
    if args.gate:
        skills = [args.skill] if args.skill else _every_skill(root)
        return _worst(_gate_skill(root, s, args.run, args.json)
                      for s in skills)
    if args.skill:
        return _score_skill(root, args.skill, args.run, args.json)
    if args.all_skills:
        return _worst(_score_skill(root, s, None, args.json)
                      for s in _every_skill(root))

    import taskplane_lite as tp
    import obligations
    ws = os.path.abspath(os.environ.get("TASKPLANE_WORKSPACE") or ".")
    trace_rows = []
    for p in tp.trace_paths(ws):
        trace_rows += _rows(p)
    res = score(trace_rows, obligations.read(ws), tp.dispatch_report(ws))
    if args.json:
        print(json.dumps(res, indent=2, default=str))
        return 0
    print("evals — was the machinery USED?\n")
    report(os.path.basename(ws) or ws, res)
    print("  Claims and facts are separate columns on purpose: an "
          "acknowledgement\n  says the artifact was shown, it does not "
          "prove it. This gates nothing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
