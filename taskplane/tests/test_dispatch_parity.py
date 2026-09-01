"""Current dispatch contracts across library, Codex, and workflow rails.

These checks protect required fields, settings propagation, host-safe model
resolution, and semantic equality between Task and workflow transport. They
deliberately do not replay whole historical payload snapshots.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lens  # noqa: E402
import taskplane_lite as tp  # noqa: E402
import tp as cli  # noqa: E402
from taskplane.settings import load_settings  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BRIEFS = os.path.join(HERE, "fixtures", "briefs")
WORKSPACE = os.path.join(BRIEFS, "workspace")
DETECTORS = os.path.join(HERE, "fixtures", "detectors")
# The checkout-bound gate deliberately imports the engine from the
# orchestrator checkout while collecting this test from the task checkout.
# Scrub the root that actually authored role_instructions, not the root that
# happens to contain this test file.
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(tp.__file__)))
SHARED_CONTEXT_PATHS = {
    "diff.patch": ".em-review/context/diff.patch",
}

# every env var that may vary tier->model resolution or the dispatch path —
# cleared for determinism (the goldens' documented env scrub)
SCRUB_VARS = ("CODEX_HOME", "CODEX_THREAD_ID", "TASKPLANE_MODEL_CHEAP",
              "TASKPLANE_MODEL_STANDARD", "TASKPLANE_MODEL_DEEP",
              "TASKPLANE_REASONING_CHEAP", "TASKPLANE_REASONING_STANDARD",
              "TASKPLANE_REASONING_DEEP",
              "TASKPLANE_WORKFLOWS", "CLAUDE_CODE_WORKFLOWS")


def _scrub_plugin_root(value, root=PLUGIN_ROOT):
    """Replace the plugin root with <PLUGIN>, in EITHER separator shape.

    Dispatch briefs now emit '/'-shaped role-instruction paths on every host
    (they are cross-host artifacts compared byte for byte), while
    `PLUGIN_ROOT` is host-shaped. On Windows the two no longer matched, so
    the scrub silently did nothing and the golden compare failed on a real
    absolute path. Only the ROOT is substituted — unrelated text is never
    rewritten.
    """
    if isinstance(value, str):
        base = root.rstrip("/\\")
        for cand in (base, base.replace("\\", "/")):
            if value.startswith(cand):
                suffix = value[len(cand):].lstrip("/\\").replace("\\", "/")
                return "<PLUGIN>/" + suffix if suffix else "<PLUGIN>"
        return (value.replace(base, "<PLUGIN>")
                .replace(base.replace("\\", "/"), "<PLUGIN>"))
    if isinstance(value, list):
        return [_scrub_plugin_root(item, root) for item in value]
    if isinstance(value, dict):
        return {key: _scrub_plugin_root(item, root)
                for key, item in value.items()}
    return value


def tree_files(root):
    out = []
    for dirpath, dirs, names in os.walk(root):
        dirs.sort()
        for n in sorted(names):
            out.append(os.path.relpath(os.path.join(dirpath, n),
                                       root).replace(os.sep, "/"))
    return sorted(out)


def fixture_files():
    return tree_files(WORKSPACE)


@pytest.fixture()
def scrubbed_env(monkeypatch):
    for v in SCRUB_VARS:
        monkeypatch.delenv(v, raising=False)


# ------------------------------------------------------- 1. live contract


class TestDispatchContract:
    def test_path_scrub_handles_windows_separators_before_json_encoding(self):
        root = r"C:\repo\taskPlane"
        payload = {"role_instructions":
                   root + r"\agents\tp-executor.md"}
        scrubbed = _scrub_plugin_root(payload, root)
        assert scrubbed["role_instructions"] == \
            "<PLUGIN>/agents/tp-executor.md"
        assert root not in json.dumps(scrubbed)

    def test_review_dispatch_is_complete_and_never_dispatches_na_lenses(
            self, scrubbed_env):
        payload = lens.dispatch_briefs(
            lens.route(fixture_files(), stage="review",
                       workspace=WORKSPACE), base="HEAD",
            context_paths=SHARED_CONTEXT_PATHS)
        assert len(payload["routing_decision"]) == len(lens.load_catalog()["lenses"])
        dispatched = {b["id"] for b in payload["deep"]} | set(
            (payload["sweep"] or {}).get("ids") or [])
        na = {k for k, v in payload["routing_decision"].items()
              if v["verdict"] == "n/a"}
        assert na, "a real diff should mark some lenses n/a"
        assert not (na & dispatched), "an n/a lens must not be dispatched"
        for k in na:
            assert payload["routing_decision"][k]["negative_evidence"], \
                f"{k} is n/a with no evidence — that is a silent skip"

    def test_live_dispatch_carries_the_lens_brief_contract_fields(
            self, monkeypatch):
        payload = lens.dispatch_briefs(
            lens.route(fixture_files(), breadth="all"), base="HEAD",
            context_paths=SHARED_CONTEXT_PATHS)
        assert payload["base"] == "HEAD"
        assert payload["deep"] and payload["sweep"]
        for b in payload["deep"]:
            assert b["task_name"] == tp.dispatch_task_name(
                "lens", "tp-lens", b["id"])
            assert b["role_marker"] == "taskplane-role:tp-lens"
            assert b["role_instructions"].endswith("agents/tp-lens.md")
            assert b["reasoning_effort"] in tp.REASONING_EFFORTS and \
                b["settings_digest"] == payload["settings_digest"]
            assert b["task_slot"] == f"lens-{b['id']}"
            assert b["contract"]["read_only"] is True
            assert b["contract"]["task_slot"] == b["task_slot"]
            assert b["output"] == f".em-review/lens-{b['id']}/findings.json"
            assert f"export TASKPLANE_TASK={b['task_slot']}" in b["prompt"]
        assert payload["sweep"]["model_tier"] == "cheap"
        assert payload["sweep"]["reasoning_effort"] == "high" and \
            payload["sweep"]["settings_digest"] == payload["settings_digest"]
        assert payload["sweep"]["task_name"] == tp.dispatch_task_name(
            "lens", "tp-lens", "sweep")

        custom = load_settings(overlay={"limits": {
            "budgets": {"lens_deep_max_actions": 41,
                        "lens_sweep_max_actions": 19},
            "timeouts": {"lens_wait_seconds": 901,
                         "lens_minimum_wait_seconds": 101},
        }})
        loads = []

        def settings_snapshot(**_kwargs):
            loads.append(custom.digest)
            return custom

        monkeypatch.setattr(tp, "_canonical_operational_settings",
                            settings_snapshot)
        projected = lens.dispatch_briefs(
            lens.route(fixture_files(), breadth="all"), base="HEAD",
            context_paths=SHARED_CONTEXT_PATHS)
        assert loads == [custom.digest]
        assert {brief["contract"]["max_actions"]
                for brief in projected["deep"]} == {41}
        assert projected["sweep"]["contract"]["max_actions"] == 19
        assert projected["sweep"]["wait_policy"]["timeout_seconds"] == 901
        assert projected["sweep"]["wait_policy"][
            "minimum_timeout_seconds"] == 101


# ------------------------------------------------- 2. codex-env CLI parity


def _fixture_repo(tmp_path) -> str:
    """A throwaway git repo whose diff IS the frozen fixture: one empty-ish
    base commit, then the checked-in workspace tree copied in untracked —
    `route_git_diff` sees exactly the frozen changed-files list."""
    ws = os.path.join(str(tmp_path), "ws")
    os.makedirs(ws)
    with open(os.path.join(ws, ".gitkeep"), "w", encoding="utf-8") as f:
        f.write("")
    for a in (["init", "-q"], ["config", "user.email", "e@e"],
              ["config", "user.name", "t"], ["add", "-A"],
              ["commit", "-qm", "base"]):
        subprocess.run(["git", *a], cwd=ws, capture_output=True, check=True)
    # .gitkeep stays tracked and UNCHANGED — invisible to both the diff and
    # the untracked scan, so the routed set is exactly the fixture tree.
    for rel in tree_files(WORKSPACE):
        dst = os.path.join(ws, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(os.path.join(WORKSPACE, rel), dst)
    return ws


def _dispatch(ws, *extra) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = cli.main(["lens", "dispatch", "--workspace", ws, *extra])
    return rc, out.getvalue()


class TestCodexEnvParity:
    def test_codex_cli_uses_current_settings_and_host_safe_models(
            self, tmp_path, monkeypatch):
        for v in SCRUB_VARS:
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv("CODEX_HOME", "/x")
        monkeypatch.setenv("TASKPLANE_WORKFLOWS", "1")  # codex still wins
        ws = _fixture_repo(tmp_path)
        rc, out = _dispatch(ws)
        assert rc == 0
        payload = json.loads(out)
        assert payload["settings_digest"] == load_settings().digest
        assert all(brief["model"] is None for brief in payload["deep"])
        assert payload["routing_decision"]

    def test_codex_cli_breadth_all_keeps_sweep_host_safe(
            self, tmp_path, monkeypatch):
        for v in SCRUB_VARS:
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv("CODEX_HOME", "/x")
        ws = _fixture_repo(tmp_path)
        rc, out = _dispatch(ws, "--all")
        assert rc == 0
        payload = json.loads(out)
        assert payload["sweep"]["model"] is None
        assert payload["sweep"]["settings_digest"] == \
            payload["settings_digest"]

    def test_codex_stdout_never_carries_workflow_keys(self, tmp_path,
                                                      monkeypatch):
        for v in SCRUB_VARS:
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv("CODEX_HOME", "/x")
        monkeypatch.setenv("TASKPLANE_WORKFLOWS", "1")
        ws = _fixture_repo(tmp_path)
        _, out = _dispatch(ws)
        payload = json.loads(out)
        for key in ("dispatch_path", "workflow", "reason"):
            assert key not in payload

# ------------------------- 3. detector-fixture completeness (CI-level pin)


class TestDetectorFixtureCompleteness:
    """t1's per-detector fixture discipline, re-asserted thinly so THIS CI
    leg is self-contained: every catalog lens id must keep a non-empty
    positive AND negative fixture tree (the deep behavior tests live in
    test_lens_signals_fixtures.py, which CI runs as its own named step)."""

    def test_every_catalog_lens_has_positive_and_negative_fixtures(self):
        missing = []
        for l in lens.load_catalog()["lenses"]:
            for kind in ("positive", "negative"):
                d = os.path.join(DETECTORS, l["id"], kind)
                if not os.path.isdir(d) or not tree_files(d):
                    missing.append(f"{l['id']}/{kind}")
        assert missing == [], \
            f"missing/empty detector fixture dirs: {missing}"


# ------------------------------------- 4. workflow-args vs Task-path parity


def _task_transport_workers(payload):
    """Decode the workers a Task consumer would actually dispatch."""
    briefs = list(payload.get("deep") or [])
    if payload.get("sweep") is not None:
        briefs.append(payload["sweep"])
    return [{
        "identity": brief["task_name"],
        "agent": brief["agent"],
        "role_marker": brief["role_marker"],
        "model": brief["model"],
        "model_tier": brief["model_tier"],
        "reasoning_effort": brief["reasoning_effort"],
        "lens_ids": list(brief.get("ids") or [brief["id"]]),
        "prompt": brief["prompt"],
        "result_path": brief["output"],
        "task_slot": brief["task_slot"],
        "lease": brief["contract"],
    } for brief in briefs]


def _review_wave_workers(args):
    """Decode only the canonical input available to review-wave.js."""
    return [{
        "identity": slot["task_name"],
        "agent": slot["agent"],
        "role_marker": slot["role_marker"],
        "model": slot["model"],
        "model_tier": slot["model_tier"],
        "reasoning_effort": slot["reasoning_effort"],
        "lens_ids": list(slot["lens_ids"]),
        "prompt": slot["prompt"],
        "result_path": slot["result_path"],
        "task_slot": slot["lease"]["task_slot"],
        "lease": slot["lease"],
    } for slot in args["slots"]]


class TestWorkflowArgsParity:
    def test_transports_dispatch_the_same_routed_workers(self, tmp_path,
                                                         scrubbed_env):
        """Both transports preserve dispatch semantics, not JSON layout."""
        ws = _fixture_repo(tmp_path)
        rc_t, out_t = _dispatch(ws, "--emit", "task")
        rc_w, out_w = _dispatch(ws, "--emit", "workflow")
        assert rc_t == 0 and rc_w == 0
        task_payload = json.loads(out_t)
        wf_payload = json.loads(out_w)
        assert wf_payload["dispatch_path"] == "workflow"
        args = wf_payload["workflow"]["args"]
        assert (_review_wave_workers(args) ==
                _task_transport_workers(task_payload))
        assert args["settings_digest"] == load_settings().digest
        assert set(args) == {"settings_digest", "slots"}

    def test_breadth_all_preserves_the_sweep_worker(self, tmp_path,
                                                    scrubbed_env):
        ws = _fixture_repo(tmp_path)
        rc_t, out_t = _dispatch(ws, "--all", "--emit", "task")
        rc_w, out_w = _dispatch(ws, "--all", "--emit", "workflow")
        assert rc_t == 0 and rc_w == 0
        task_payload = json.loads(out_t)
        args = json.loads(out_w)["workflow"]["args"]
        task_workers = _task_transport_workers(task_payload)
        workflow_workers = _review_wave_workers(args)
        assert workflow_workers == task_workers
        assert any(worker["task_slot"] == "lens-sweep"
                   for worker in workflow_workers)
