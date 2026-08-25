"""v2.3.1 CLI highs — real `tp.py` invocations for the exact journeys that
broke: max-actions 0, graph --json purity, loop-init refusal surfacing, and
tp status on a corrupt contract."""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TP = os.path.join(ROOT, "taskplane", "tp.py")


def _run(args, ws, env=None):
    e = dict(os.environ)
    e["TASKPLANE_HOME"] = env or ws + "-store"
    return subprocess.run([sys.executable, TP, *args], cwd=ws,
                          capture_output=True, text=True, env=e, encoding="utf-8", errors="replace")


def _git_ws(tmp_path):
    ws = str(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=ws, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=ws, check=True)
    (tmp_path / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=ws, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=ws, check=True)
    return ws


# ---- #19 --max-actions 0 is a ZERO ceiling, not the default ----

def test_max_actions_zero_is_a_zero_ceiling(tmp_path):
    ws = _git_ws(tmp_path)
    _run(["new", "zero budget", "--workspace", ws, "--max-actions", "0",
          "--tools", "Read"], ws)
    st = _run(["status", "--workspace", ws], ws)
    data = json.loads(st.stdout)
    assert data["max_actions"] == 0, data


# ---- #20 graph impact --json emits PURE json ----

def test_graph_impact_json_is_pure(tmp_path):
    ws = _git_ws(tmp_path)
    r = _run(["graph", "--workspace", ws, "impact", "--files", "f.txt",
              "--json"], ws)
    # must parse as a single JSON document with no prose prefix
    json.loads(r.stdout)


# ---- #21 loop init over an in-flight loop surfaces the refusal ----

def test_loop_init_over_inflight_surfaces_refusal(tmp_path):
    ws = _git_ws(tmp_path)
    _run(["loop", "init", "first goal", "--workspace", ws], ws)
    r = _run(["loop", "init", "second goal", "--workspace", ws], ws)
    assert r.returncode != 0, (r.stdout, r.stderr)
    assert "error" in (r.stdout + r.stderr).lower()
    assert "initialized" not in r.stdout or '"error"' in r.stdout


# ---- #22 tp status on a corrupt contract fails closed, not "ungoverned" ----

def test_status_on_corrupt_contract_fails_closed(tmp_path):
    ws = _git_ws(tmp_path)
    _run(["new", "govern", "--workspace", ws, "--tools", "Read"], ws)
    # corrupt the active contract file
    import glob
    cand = glob.glob(os.path.join(ws, ".taskplane", "active_contract.json"))
    assert cand, "no active contract written"
    with open(cand[0], "w", encoding="utf-8") as f:
        f.write("BAD{{{ not json")
    r = _run(["status", "--workspace", ws], ws)
    assert r.returncode != 0, (r.stdout, r.stderr)
    assert "CORRUPT" in r.stdout, r.stdout
    assert "no active contract" not in r.stdout.lower()


# ---- Phase 3 EM em-fix: `clear` releases THIS process's contract slot ----
# The guard used to hardcode the LEGACY .taskplane/active_contract.json, so a
# slotted agent (TASKPLANE_TASK exported — every dispatched wave agent) got
# "taskplane: no active contract to clear." and exit 0 while its
# .taskplane/active/<slot>.json stayed on disk. That leak outlives the agent:
# load_active() governs a slot-less process by the MOST RESTRICTIVE UNION of
# every active slot, so each leaked slot tightens what every later agent may
# do. The kernel's clear() was already slot-aware; only the CLI guard was not.

def _run_slot(args, ws, slot=None):
    """`_run` with explicit control of TASKPLANE_TASK (None = no slot)."""
    e = dict(os.environ)
    e["TASKPLANE_HOME"] = ws + "-store"
    if slot is None:
        e.pop("TASKPLANE_TASK", None)
    else:
        e["TASKPLANE_TASK"] = slot
    return subprocess.run([sys.executable, TP, *args], cwd=ws,
                          capture_output=True, text=True, env=e, encoding="utf-8", errors="replace")


def test_clear_releases_the_exported_task_slot(tmp_path):
    ws = _git_ws(tmp_path)
    slot = "lens-light3"
    _run_slot(["new", "sweep review", "--workspace", ws, "--read-only",
               "--write-allow", ".em-review/**"], ws, slot)
    active = os.path.join(ws, ".taskplane", "active")
    slot_file = os.path.join(active, slot + ".json")
    assert os.path.exists(slot_file), os.listdir(active)

    r = _run_slot(["clear", "--workspace", ws], ws, slot)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert not os.path.exists(slot_file), (r.stdout, os.listdir(active))
    assert not os.path.exists(os.path.join(active, slot + ".snapshot"))
    assert "cleared" in r.stdout, r.stdout          # reports the release
    assert slot in r.stdout, r.stdout               # names WHICH slot
    # the release is real for everyone after: no slot survives to tighten
    # the most-restrictive union a slot-less process is governed by
    assert not [f for f in os.listdir(active) if f.endswith(".json")]


def test_clear_with_a_sibling_slot_active_releases_only_its_own(tmp_path):
    ws = _git_ws(tmp_path)
    for slot in ("tA", "tB"):
        _run_slot(["new", f"task {slot}", "--workspace", ws,
                   "--scope", "src/**"], ws, slot)
    active = os.path.join(ws, ".taskplane", "active")
    r = _run_slot(["clear", "--workspace", ws], ws, "tA")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert not os.path.exists(os.path.join(active, "tA.json"))
    assert os.path.exists(os.path.join(active, "tB.json")), r.stdout
    assert "remains governed by 1 other contract" in r.stdout, r.stdout
    assert "workspace is ungoverned" not in r.stdout, r.stdout


def test_clear_with_no_slot_and_no_contract_still_reports_and_exits_zero(
        tmp_path):
    ws = _git_ws(tmp_path)
    r = _run_slot(["clear", "--workspace", ws], ws)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "no active contract" in r.stdout, r.stdout


def test_clear_still_releases_the_legacy_single_contract(tmp_path):
    ws = _git_ws(tmp_path)
    _run_slot(["new", "legacy task", "--workspace", ws,
               "--scope", "src/**"], ws)
    legacy = os.path.join(ws, ".taskplane", "active_contract.json")
    assert os.path.exists(legacy)
    r = _run_slot(["clear", "--workspace", ws], ws)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert not os.path.exists(legacy), r.stdout
    assert "cleared" in r.stdout, r.stdout
