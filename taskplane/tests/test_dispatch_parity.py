"""t6 (R-0002) — CI Codex-parity leg: frozen briefs replayed against goldens.

The Task-dispatch payload (contract:lens-brief) is the SINGLE handoff both
review paths consume — the workflow path as `workflow.args`, the Codex path
as stdout. These tests freeze it:

  * golden replay — `lens.dispatch_briefs(lens.route(<frozen files>))` must
    byte-match the checked-in goldens (routed AND breadth=all), so ANY change
    to the dispatch-payload shape fails CI even while the workflow-path tests
    stay green. A deliberate shape change regenerates the goldens via
    `python3 taskplane/tests/fixtures/briefs/regen.py` (documented in the
    goldens' comment headers — that friction is the point);
  * codex-env parity — with CODEX_HOME set, `tp lens dispatch` stdout for the
    fixture equals the same golden-derived bytes (the only host-legitimate
    delta is tier->model resolution: on codex the cheap tier resolves to null
    instead of "haiku" so another host's model id is never dispatched), and
    NEVER carries workflow keys;
  * detector-fixture completeness — re-asserted here so the CI leg is
    self-contained: every catalog lens id has a non-empty positive AND
    negative detector fixture dir (t1's discipline, pinned at CI level);
  * workflow-args parity — the `--emit workflow` payload's `args` field
    JSON-equals the Task-path payload for the same fixture (fixture-level,
    no JS runtime needed).

BYTE NORMALIZATION (mirrors fixtures/briefs/regen.py): goldens are stored
with sorted keys, indent=2, default ensure_ascii, trailing newline, after an
env scrub (CODEX_HOME/CODEX_THREAD_ID/TASKPLANE_MODEL_*/
TASKPLANE_REASONING_*/TASKPLANE_WORKFLOWS/CLAUDE_CODE_WORKFLOWS unset).
Payloads are compared as
`json.dumps(obj, indent=2, sort_keys=True)` bytes — key ORDER is the only
thing normalization forgives; every key, value, prompt byte and brief count
is pinned. The emitted absolute plugin root in `role_instructions` is replaced
with `<PLUGIN>`; the fixture routing input is workspace-relative and the
payload carries no timestamps, so the goldens are machine-portable.
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

HERE = os.path.dirname(os.path.abspath(__file__))
BRIEFS = os.path.join(HERE, "fixtures", "briefs")
WORKSPACE = os.path.join(BRIEFS, "workspace")
DETECTORS = os.path.join(HERE, "fixtures", "detectors")
PLUGIN_ROOT = os.path.dirname(os.path.dirname(HERE))

# every env var that may vary tier->model resolution or the dispatch path —
# cleared for determinism (the goldens' documented env scrub)
SCRUB_VARS = ("CODEX_HOME", "CODEX_THREAD_ID", "TASKPLANE_MODEL_CHEAP",
              "TASKPLANE_MODEL_STANDARD", "TASKPLANE_MODEL_DEEP",
              "TASKPLANE_REASONING_CHEAP", "TASKPLANE_REASONING_STANDARD",
              "TASKPLANE_REASONING_DEEP",
              "TASKPLANE_WORKFLOWS", "CLAUDE_CODE_WORKFLOWS")


def _scrub_plugin_root(value, root=PLUGIN_ROOT):
    if isinstance(value, str):
        base = root.rstrip("/\\")
        if value.startswith(base):
            suffix = value[len(base):].lstrip("/\\").replace("\\", "/")
            return "<PLUGIN>/" + suffix if suffix else "<PLUGIN>"
        return value.replace(base, "<PLUGIN>")
    if isinstance(value, list):
        return [_scrub_plugin_root(item, root) for item in value]
    if isinstance(value, dict):
        return {key: _scrub_plugin_root(item, root)
                for key, item in value.items()}
    return value


def normalize(payload) -> str:
    """THE byte normalization (same dumps args as regen.py's goldens)."""
    return json.dumps(_scrub_plugin_root(payload), indent=2,
                      sort_keys=True) + "\n"


def load_golden(name: str):
    """Goldens carry a '#' comment header (regen command + scrub rules)
    above the JSON body — strip it, parse the rest."""
    with open(os.path.join(BRIEFS, name), encoding="utf-8") as f:
        raw = f.read()
    body = "".join(l for l in raw.splitlines(keepends=True)
                   if not l.startswith("#"))
    return json.loads(body)


def golden_bytes(name: str) -> str:
    """The golden's JSON body EXACTLY as stored (header stripped, bytes
    kept) — pins the normalization itself, not just the parsed value."""
    with open(os.path.join(BRIEFS, name), encoding="utf-8") as f:
        raw = f.read()
    return "".join(l for l in raw.splitlines(keepends=True)
                   if not l.startswith("#"))


def tree_files(root):
    out = []
    for dirpath, dirs, names in os.walk(root):
        dirs.sort()
        for n in sorted(names):
            out.append(os.path.relpath(os.path.join(dirpath, n),
                                       root).replace(os.sep, "/"))
    return sorted(out)


def frozen_files():
    with open(os.path.join(BRIEFS, "changed_files.json"), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture()
def scrubbed_env(monkeypatch):
    for v in SCRUB_VARS:
        monkeypatch.delenv(v, raising=False)


# ------------------------------------------------------- 1. golden replay


class TestGoldenReplay:
    def test_path_scrub_handles_windows_separators_before_json_encoding(self):
        root = r"C:\repo\taskPlane"
        payload = {"role_instructions":
                   root + r"\agents\tp-executor.md"}
        scrubbed = _scrub_plugin_root(payload, root)
        assert scrubbed["role_instructions"] == \
            "<PLUGIN>/agents/tp-executor.md"
        assert root not in json.dumps(scrubbed)

    def test_fixture_tree_matches_frozen_changed_files(self):
        """The checked-in tree IS the routing input — drift between the two
        would make the goldens replay a different change than the one the
        fixture documents."""
        assert tree_files(WORKSPACE) == frozen_files()

    def test_routed_dispatch_payload_matches_golden(self, scrubbed_env):
        payload = lens.dispatch_briefs(lens.route(frozen_files()),
                                       base="HEAD", max_actions=30)
        assert normalize(payload) == golden_bytes(
            "golden_dispatch_routed.json")

    def test_breadth_all_dispatch_payload_matches_golden(self, scrubbed_env):
        """breadth=all is the em-step shape: deep briefs + the batched sweep
        brief (cheap tier) — the sweep half of the contract is pinned too."""
        payload = lens.dispatch_briefs(
            lens.route(frozen_files(), breadth="all"),
            base="HEAD", max_actions=30)
        assert normalize(payload) == golden_bytes("golden_dispatch_all.json")

    def test_goldens_are_deterministic_artifacts(self):
        """No absolute paths, no timestamps, keys sorted — the scrub rules
        the goldens' headers document, machine-checked."""
        for name in ("golden_dispatch_routed.json",
                     "golden_dispatch_all.json"):
            body = golden_bytes(name)
            payload = json.loads(body)
            assert body == normalize(payload), f"{name}: keys not sorted"
            assert "/tmp/" not in body and "/home/" not in body, \
                f"{name}: absolute path leaked"
            for k in ('"timestamp"', '"time"', '"date"'):
                assert k not in body, f"{name}: nondeterministic field {k}"

    def test_golden_headers_document_the_regen_command(self):
        for name in ("golden_dispatch_routed.json",
                     "golden_dispatch_all.json"):
            with open(os.path.join(BRIEFS, name), encoding="utf-8") as f:
                head = f.read(1000)
            assert head.startswith("#")
            assert "python3 taskplane/tests/fixtures/briefs/regen.py" in head

    def test_golden_carries_the_lens_brief_contract_fields(self):
        """Spot-pin the contract:lens-brief surface so a reviewer can see
        WHAT the byte-compare protects."""
        payload = load_golden("golden_dispatch_all.json")
        assert payload["base"] == "HEAD"
        assert payload["deep"] and payload["sweep"]
        for b in payload["deep"]:
            assert b["task_name"] == tp.dispatch_task_name(
                "lens", "tp-lens", b["id"])
            assert b["role_marker"] == "taskplane-role:tp-lens"
            assert b["role_instructions"].endswith("agents/tp-lens.md")
            assert b["reasoning_effort"] in tp.REASONING_EFFORTS
            assert b["task_slot"] == f"lens-{b['id']}"
            assert b["contract"]["read_only"] is True
            assert b["contract"]["task_slot"] == b["task_slot"]
            assert b["output"] == f".em-review/lens-{b['id']}/findings.json"
            assert f"export TASKPLANE_TASK={b['task_slot']}" in b["prompt"]
        assert payload["sweep"]["model_tier"] == "cheap"
        assert payload["sweep"]["reasoning_effort"] == "low"
        assert payload["sweep"]["task_name"] == tp.dispatch_task_name(
            "lens", "tp-lens", "sweep")


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


def _host_expected(golden_name: str) -> dict:
    """Golden-derived expectation for the CURRENT host env: identical payload
    except each brief's `model` re-resolves through tp.model_for_tier (the
    documented host seam — on codex the cheap tier must resolve to null so
    another host's model id is never dispatched). Nothing else may differ."""
    payload = load_golden(golden_name)
    for b in payload.get("deep") or []:
        b["model"] = tp.model_for_tier(b["model_tier"])
    if payload.get("sweep"):
        payload["sweep"]["model"] = tp.model_for_tier(
            payload["sweep"]["model_tier"])
    return payload


class TestCodexEnvParity:
    def test_codex_cli_stdout_equals_golden_derived_bytes(self, tmp_path,
                                                          monkeypatch):
        for v in SCRUB_VARS:
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv("CODEX_HOME", "/x")
        monkeypatch.setenv("TASKPLANE_WORKFLOWS", "1")  # codex still wins
        ws = _fixture_repo(tmp_path)
        rc, out = _dispatch(ws)
        assert rc == 0
        assert normalize(json.loads(out)) == normalize(
            _host_expected("golden_dispatch_routed.json"))

    def test_codex_cli_breadth_all_equals_golden_derived_bytes(
            self, tmp_path, monkeypatch):
        """The sweep brief exercises the one legitimate host delta: cheap
        tier resolves to null under codex, 'haiku' in the golden."""
        for v in SCRUB_VARS:
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv("CODEX_HOME", "/x")
        ws = _fixture_repo(tmp_path)
        rc, out = _dispatch(ws, "--all")
        assert rc == 0
        expected = _host_expected("golden_dispatch_all.json")
        assert expected["sweep"]["model"] is None  # the codex model rule
        assert normalize(json.loads(out)) == normalize(expected)

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

    def test_bare_host_cli_equals_scrubbed_golden_bytes(self, tmp_path,
                                                        scrubbed_env):
        """On the capture host (bare env) the CLI stdout normalizes to the
        golden EXACTLY — model fields included ('haiku' sweep)."""
        ws = _fixture_repo(tmp_path)
        rc, out = _dispatch(ws, "--all")
        assert rc == 0
        assert normalize(json.loads(out)) == golden_bytes(
            "golden_dispatch_all.json")


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


class TestWorkflowArgsParity:
    def test_workflow_args_json_equal_task_path_payload(self, tmp_path,
                                                        scrubbed_env):
        """contract:lens-brief's core claim, fixture-verified with no JS
        runtime: the workflow consumes the IDENTICAL payload the Task path
        prints — `workflow.args` == Task-path stdout == golden."""
        ws = _fixture_repo(tmp_path)
        rc_t, out_t = _dispatch(ws, "--emit", "task")
        rc_w, out_w = _dispatch(ws, "--emit", "workflow")
        assert rc_t == 0 and rc_w == 0
        task_payload = json.loads(out_t)
        wf_payload = json.loads(out_w)
        assert wf_payload["dispatch_path"] == "workflow"
        args = wf_payload["workflow"]["args"]
        assert args == task_payload  # JSON-equal, whole payload
        assert normalize(args) == golden_bytes("golden_dispatch_routed.json")

    def test_workflow_args_breadth_all_parity_including_sweep(self, tmp_path,
                                                              scrubbed_env):
        ws = _fixture_repo(tmp_path)
        _, out_t = _dispatch(ws, "--all", "--emit", "task")
        _, out_w = _dispatch(ws, "--all", "--emit", "workflow")
        task_payload = json.loads(out_t)
        args = json.loads(out_w)["workflow"]["args"]
        assert args == task_payload
        assert args["sweep"] == task_payload["sweep"]
        assert normalize(args) == golden_bytes("golden_dispatch_all.json")
