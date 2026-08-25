"""Shared frozen stage-wave fixture journey (t4, R-0004 stage parity).

The three governed stage dispatch payloads — the EXECUTE wave (`loop wave`),
the EVALUATE dispatch and the FIX dispatch (`loop next`) — are captured from
ONE deterministic loop journey in a throwaway git workspace and frozen as
goldens next to this module (golden_stage_execute.json /
golden_stage_evaluate.json / golden_stage_fix.json). Both the regen script
(regen.py — the ONLY documented regen path) and the parity tests
(test_stage_waves.py) drive the journey through THIS module so the capture
and the replay can never drift apart.

SCRUB RULES (the R-0002 discipline extended to loop payloads — every rule
is documented and applied identically at capture and replay):
  * env scrub — captured with CODEX_HOME, CODEX_THREAD_ID,
    TASKPLANE_MODEL_CHEAP/STANDARD/DEEP,
    TASKPLANE_REASONING_CHEAP/STANDARD/DEEP, TASKPLANE_WORKFLOWS,
    CLAUDE_CODE_WORKFLOWS and TASKPLANE_TASK unset (SCRUB_VARS);
  * path scrub — the throwaway workspace, the external store root, and the
    plugin checkout root are replaced with the stable tokens <WS>, <STORE>
    and <PLUGIN> (loop payloads carry task worktree paths, the artifacts
    cache path, and role_instructions);
  * no timestamps — unix-time values under the keys updated_at/scanned_at/
    submitted_at are zeroed, enforcement observed_at values become <TIME>,
    immutable artifact-reference digests/byte sizes are normalized, git shas
    / graph fingerprints / enforcement evidence ids under scanned_head/
    content_fingerprint/snapshot/fingerprint/baseline/evidence_id become
    <SHA>, and calendar dates (YYYY-MM-DD, e.g. the KB decision date) become
    <DATE> wherever they appear in strings;
  * stable ids — task ids (t1/t2), the KB decision id (0001) and the goal
    text are fixed by the journey itself;
  * byte normalization — json.dumps(payload, indent=2, sort_keys=True),
    default ensure_ascii, trailing newline (identical to the R-0002
    goldens).
"""
import contextlib
import io
import json
import os
import re
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TASKPLANE = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if TASKPLANE not in sys.path:
    sys.path.insert(0, TASKPLANE)
import taskplane_lite as _ENGINE  # noqa: E402

# A checkout-bound suite can collect fixtures from one checkout while its
# orchestrator-provided engine is loaded from another.  Stage payload paths
# are authored by that loaded engine, so bind the strict scrub to its exact
# plugin root rather than wildcarding either checkout path.
PLUGIN_ROOT = os.path.dirname(
    os.path.dirname(os.path.realpath(_ENGINE.__file__)))

# every env var that may vary the dispatch path, tier->model resolution, or
# the contract slot — cleared for determinism (the goldens' env scrub)
SCRUB_VARS = ("CODEX_HOME", "CODEX_THREAD_ID", "TASKPLANE_MODEL_CHEAP",
              "TASKPLANE_MODEL_STANDARD", "TASKPLANE_MODEL_DEEP",
              "TASKPLANE_REASONING_CHEAP", "TASKPLANE_REASONING_STANDARD",
              "TASKPLANE_REASONING_DEEP",
              "TASKPLANE_WORKFLOWS", "CLAUDE_CODE_WORKFLOWS",
              "TASKPLANE_TASK")

STAGES = ("execute", "evaluate", "fix")
GOLDENS = {stage: f"golden_stage_{stage}.json" for stage in STAGES}

# journey constants — part of the frozen fixture (stable ids)
GOAL = "stage wave fixture"
TASKS = [
    {"id": "t1", "scope": ["src/alpha/**"], "tests": "true",
     "criteria": ["alpha updated"]},
    {"id": "t2", "scope": ["src/beta/**"], "tests": "true",
     "criteria": ["beta updated"]},
]

_ZERO_KEYS = ("updated_at", "scanned_at", "submitted_at")
_SHA_KEYS = ("scanned_head", "content_fingerprint", "snapshot",
             "fingerprint", "baseline", "run_id", "evidence_id",
             "revision", "scanned_revision", "target_commit")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_HEX64_RE = re.compile(r"\b[0-9a-f]{64}\b")
_REVIEW_SLOT_RE = re.compile(r"\breview-[0-9a-f]{20}\b")


def _scrub_review_bootstrap(value: dict) -> dict:
    """Normalize one signed, path-bound ReviewKernel bootstrap projection.

    The action remains structurally frozen, including every semantic binding,
    while the local signing key, signature, issuance time, lease-derived slot,
    and opaque transport encodings are portable tokens.
    """
    out = json.loads(json.dumps(value))
    action = out.get("action") or {}
    action["action_id"] = "<ACTION>"
    action["issued_at"] = 0
    action["expires_at"] = 0
    action["key_id"] = "<SHA>"
    action["signature"] = "<SHA>"
    action["worker_identity"] = re.sub(
        r"_[0-9a-f]{8}$", "_<LEASE>",
        str(action.get("worker_identity") or ""))
    producer = action.get("producer_contract") or {}
    producer["task"] = _HEX64_RE.sub("<SHA>", str(producer.get("task") or ""))
    producer["task_slot"] = _REVIEW_SLOT_RE.sub(
        "review-<SLOT>", str(producer.get("task_slot") or ""))
    producer["write_allow"] = [
        _HEX64_RE.sub("<SHA>", str(path))
        for path in producer.get("write_allow") or []
    ]
    action["result_path"] = _HEX64_RE.sub(
        "<SHA>", str(action.get("result_path") or ""))

    argv = list(out.get("command_argv") or [])
    host_python = str(argv[0]) if argv else ""
    if argv:
        argv[0] = "<PYTHON>"
    for flag, token in (("--task-slot", "review-<SLOT>"),
                        ("--signed-action", "<SIGNED_ACTION>"),
                        ("--expected-identity", "<EXPECTED_IDENTITY>")):
        if flag in argv and argv.index(flag) + 1 < len(argv):
            argv[argv.index(flag) + 1] = token
    out["command_argv"] = argv
    environment = out.get("environment") or {}
    if "TASKPLANE_TASK" in environment:
        environment["TASKPLANE_TASK"] = "review-<SLOT>"
    expected = out.get("expected") or {}
    expected["action_id"] = "<ACTION>"
    expected["worker_identity"] = re.sub(
        r"_[0-9a-f]{8}$", "_<LEASE>",
        str(expected.get("worker_identity") or ""))
    command = str(out.get("host_command") or "")
    # Production authors host_command with shlex.join(command_argv). Replace
    # that exact first argv before the later recursive path scrub turns the
    # plugin root into <PLUGIN>. Looking for <PLUGIN> here is too early and
    # leaks the generator's Python executable into the frozen golden.
    quoted_python = shlex.quote(host_python) if host_python else ""
    if quoted_python and (command == quoted_python or
                          command.startswith(quoted_python + " ")):
        command = "<PYTHON>" + command[len(quoted_python):]
    command = _REVIEW_SLOT_RE.sub("review-<SLOT>", command)
    command = re.sub(r"--signed-action\s+\S+",
                     "--signed-action <SIGNED_ACTION>", command)
    command = re.sub(r"--expected-identity\s+\S+",
                     "--expected-identity <EXPECTED_IDENTITY>", command)
    out["host_command"] = command
    out["task_slot"] = "review-<SLOT>"
    return out


def _git(ws, *args):
    subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                    *args], cwd=ws, check=True, capture_output=True)


def build_repo(tmp: str) -> str:
    """The frozen fixture workspace: two disjoint one-file modules and the
    two-task plan, committed as the baseline."""
    ws = os.path.join(tmp, "ws")
    os.makedirs(os.path.join(ws, "plan"))
    for d in ("src/alpha", "src/beta"):
        os.makedirs(os.path.join(ws, d))
        with open(os.path.join(ws, d, "m.py"), "w") as f:
            f.write("x = 1\n")
    with open(os.path.join(ws, "plan", "tasks.json"), "w") as f:
        json.dump({"tasks": TASKS}, f, indent=2)
    _git(ws, "init", "-q")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "base")
    return ws


def cli(*argv) -> "tuple[int, str]":
    """Run the tp CLI in-process, capturing stdout — the byte surface the
    goldens pin."""
    import tp as _cli
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = _cli.main(list(argv))
    return rc, out.getvalue()


def start_loop(ws: str) -> None:
    """init → plan gate → human plan approval → EXECUTE (parallel)."""
    import loop
    loop.init(ws, GOAL, spec_path="s", checkpoints=["plan"], parallel=True)
    loop.next_action(ws)
    loop.gate(ws, "pass")
    loop.approve(ws)


def build_task(ws: str, tid: str, module: str) -> None:
    """One wave worker's task-rail journey: worktree → claim → edit →
    commit → submit → orchestrator gate."""
    import loop
    aws = os.path.join(ws, ".tp-work", tid)
    _git(ws, "worktree", "add", "-q", aws, "-b", f"tp/{tid}")
    claimed = loop.claim(ws, tid, aws)
    assert claimed.get("claimed") == tid, claimed
    with open(os.path.join(aws, "src", module, "m.py"), "w") as f:
        f.write("x = 2\n")
    _git(aws, "add", "-A")
    _git(aws, "commit", "-qm", tid)
    assert loop.submit(ws, "pass", task_id=tid).get("submitted")
    assert loop.gate(ws, "pass", task_id=tid).get("built")


def to_fix_step(ws: str) -> None:
    """Fail the current evaluation → the loop enters FIX for that task."""
    import loop
    assert loop.submit(ws, "fail", note="repro: alpha regression").get(
        "submitted")
    out = loop.gate(ws, "fail")
    assert out.get("step") == "fix", out


def capture_stage(ws: str, stage: str, *extra) -> str:
    """The stage's Task-path stdout via the REAL CLI surface."""
    sub = "wave" if stage == "execute" else "next"
    rc, out = cli("loop", "--workspace", ws, sub, *extra)
    assert rc == 0, out
    return out


def journey(ws: str) -> "dict[str, str]":
    """Drive the frozen journey and return each stage's bare Task-path
    stdout: execute (the two-task wave), evaluate (t1 built → evaluated),
    fix (t1's evaluation failed)."""
    captures = {}
    start_loop(ws)
    captures["execute"] = capture_stage(ws, "execute")
    build_task(ws, "t1", "alpha")
    build_task(ws, "t2", "beta")
    captures["evaluate"] = capture_stage(ws, "evaluate")
    to_fix_step(ws)
    captures["fix"] = capture_stage(ws, "fix")
    return captures


def store_root(ws: str) -> str:
    """The external store root for the capture — resolve it WHILE the
    capture's TASKPLANE_HOME is in effect (env-dependent)."""
    import taskplane_lite as tp
    return tp.external_store_root(ws)


def scrub_tokens(ws: str, store: "str | None" = None) -> list:
    """(real, token) substitutions for the path scrub — longest first so a
    nested path can never leak its parent."""
    subs = []
    store = store or store_root(ws)
    for real, token in ((os.path.join(ws, ""), "<WS>/"), (ws, "<WS>"),
                        (os.path.join(store, ""), "<STORE>/"),
                        (store, "<STORE>"),
                        (os.path.join(PLUGIN_ROOT, ""), "<PLUGIN>/"),
                        (PLUGIN_ROOT, "<PLUGIN>")):
        subs.append((real, token))
        # '/'-shaped variant: payload paths are normalized before emission
        # (cross-host artifacts), while these roots are host-shaped.
        if "\\" in real:
            subs.append((real.replace("\\", "/"), token))
        real_r = os.path.realpath(real.rstrip(os.sep))
        if real_r != real.rstrip(os.sep):
            subs.append((real_r + ("/" if real.endswith(os.sep) else ""),
                         token))
    return sorted(set(subs), key=lambda s: -len(s[0]))


def scrub(payload, ws: str, store: "str | None" = None):
    """Apply the documented scrub rules to a parsed stage payload."""
    subs = scrub_tokens(ws, store=store)

    def clean(obj):
        if isinstance(obj, dict):
            if obj.get("schema") == \
                    "taskplane.review-contract-bootstrap/v1":
                obj = _scrub_review_bootstrap(obj)
            elif obj.get("schema") == \
                    "taskplane.native-agent-dispatch-intent-telemetry/v1":
                obj = dict(obj)
                obj["intent_id"] = "<INTENT>"
                if isinstance(obj.get("telemetry_path"), str):
                    obj["telemetry_path"] = re.sub(
                        r"intent-[0-9a-f]{32}\.json$", "<INTENT>.json",
                        obj["telemetry_path"])
            if obj.get("schema") == "taskplane.artifact-reference/v1":
                obj = dict(obj)
                obj["fingerprint"] = "<SHA>"
                obj["digest"] = "<SHA>"
                obj["bytes"] = 0
                if isinstance(obj.get("relative_path"), str):
                    obj["relative_path"] = re.sub(
                        r"/[0-9a-f]{64}\.json$", "/<SHA>.json",
                        obj["relative_path"])
            out = {}
            for k, v in obj.items():
                if k in _ZERO_KEYS and isinstance(v, (int, float)):
                    out[k] = 0
                elif k == "observed_at" and isinstance(v, str) and v:
                    out[k] = "<TIME>"
                elif (k in _SHA_KEYS or k.endswith("_fingerprint")) \
                        and isinstance(v, str) and v:
                    out[k] = "<SHA>"
                elif k == "result_path" and isinstance(v, str):
                    out[k] = _HEX64_RE.sub("<SHA>", v)
                else:
                    out[k] = clean(v)
            return out
        if isinstance(obj, list):
            return [clean(x) for x in obj]
        if isinstance(obj, str):
            for real, token in subs:
                obj = obj.replace(real, token)
            return _DATE_RE.sub("<DATE>", obj)
        return obj

    return clean(payload)


def normalize(payload) -> str:
    """THE byte normalization (same dumps args as the R-0002 goldens)."""
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def scrubbed_bytes(stdout: str, ws: str, store: "str | None" = None) -> str:
    """Captured stage stdout → the golden's byte form."""
    return normalize(scrub(json.loads(stdout), ws, store=store))


def assert_deterministic(body: str, name: str) -> None:
    """The scrub rules, machine-checked on the golden bytes."""
    assert "/tmp/" not in body and "/home/" not in body, \
        f"{name}: absolute path leaked"
    assert not _DATE_RE.search(body), f"{name}: calendar date leaked"
    for k in ('"timestamp"', '"time"', '"date": "2'):
        assert k not in body, f"{name}: nondeterministic field {k} leaked"
    for pattern, label in (
            (r'intent-[0-9a-f]{32}', "dispatch intent id"),
            (r'review-[0-9a-f]{20}', "review slot id"),
            (r'review-action-[0-9a-f]{24}', "review action id"),
            (r'"observed_at": "(?!<TIME>)', "enforcement timestamp")):
        assert not re.search(pattern, body), \
            f"{name}: nondeterministic {label} leaked"
    pending = [json.loads(body)]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if value.get("schema") == \
                    "taskplane.review-contract-bootstrap/v1":
                assert (value.get("command_argv") or [None])[0] == \
                    "<PYTHON>", f"{name}: bootstrap argv interpreter leaked"
                assert str(value.get("host_command") or "").startswith(
                    "<PYTHON> "), \
                    f"{name}: bootstrap host interpreter leaked"
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
