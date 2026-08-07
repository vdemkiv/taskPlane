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
                          capture_output=True, text=True, env=e)


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
    with open(cand[0], "w") as f:
        f.write("BAD{{{ not json")
    r = _run(["status", "--workspace", ws], ws)
    assert r.returncode != 0, (r.stdout, r.stderr)
    assert "CORRUPT" in r.stdout, r.stdout
    assert "no active contract" not in r.stdout.lower()
