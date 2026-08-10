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
    submitted_at are zeroed, git shas / graph fingerprints under
    scanned_head/content_fingerprint/snapshot/fingerprint/baseline become
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
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TASKPLANE = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
PLUGIN_ROOT = os.path.dirname(TASKPLANE)
if TASKPLANE not in sys.path:
    sys.path.insert(0, TASKPLANE)

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
             "fingerprint", "baseline")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


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
            out = {}
            for k, v in obj.items():
                if k in _ZERO_KEYS and isinstance(v, (int, float)):
                    out[k] = 0
                elif k in _SHA_KEYS and isinstance(v, str) and v:
                    out[k] = "<SHA>"
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
