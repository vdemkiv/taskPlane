"""t3+t4 (R-0004) — stage waves: the workflow FILES (t3) and the tp.py
stage EMITTER + Codex parity (t4).

t3: workflows/execute-wave.js, evaluate-wave.js, fix-wave.js on the
review-wave.js pattern.

t4 additions (contract:wave-workflow):
  * stage emitter — `loop wave --emit` / `loop next --emit` wrap a stage
    dispatch payload as ONE ready-to-run stage workflow invocation; the
    Task path stays BYTE-IDENTICAL to today's stdout (the mandatory
    fallback and the only Codex path), proven against frozen goldens
    captured through fixtures/briefs/stage_fixture.py (regenerated only
    via fixtures/briefs/regen.py);
  * kill-switch matrix — every documented TASKPLANE_WORKFLOWS spelling ×
    Codex markers × CLAUDE_CODE_WORKFLOWS, asserted against
    tp.workflow_available itself (single detector: the emitter may not
    re-parse the env);
  * resume — a static killed-mid-stage journal fixture replays against the
    workflow files' deterministic label rule (completed agents cached,
    incomplete re-run) and the engine-side idempotence that makes resume
    safe (double-submit of the same outcome is a no-op) is pinned against
    loop.submit through a throwaway workspace;
  * adversarial gate walk — test_every_gate_reachable_without_workflows
    drives init→pm→design→plan→execute→evaluate→em→signoff with
    TASKPLANE_WORKFLOWS=0 and reaches every gate via dispatch;
  * the workflow-agnostic module scan extends to audit.py (the emitter
    lives in tp.py ONLY).

Static pins (CI has no JS runtime — every check is a source scan, the
test_review_wave.py style):
  * each file ships, `export const meta` is a PURE literal (no calls, no
    interpolation), and the meta name matches the stage names pinned in
    contract:wave-workflow (parsed programmatically, not hand-copied);
  * deterministic: no Date.now / new Date / Math.random / dynamic
    import() / require() / process. in any of the three files;
  * ZERO gate verbs (gate/approve/signoff/resolve) anywhere in each file —
    human gates stay at conversation level by construction; the workflow
    is transport, the orchestrator gates OUTSIDE the run;
  * agent() outputs use each brief's canonical versioned output contract;
    evaluate carries the evaluator schema/resume identity and returns only
    transport receipts, while execute/fix retain their strict receipt pins;
  * parallel dispatch shape: one agent() thunk per brief fanned out via
    parallel() (tasks in one wave are independent by plan construction);
  * args consumption: execute/evaluate consume args.briefs, fix consumes
    args.verdicts (the evaluator's repro notes ride in those briefs);
    prompts are passed to agent() VERBATIM (no template interpolation);
  * no workflow translates canonical result artifacts or advances gates.
"""
import contextlib
import importlib.util
import inspect
import io
import hashlib
import json
import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tp as cli  # noqa: E402
import loop  # noqa: E402
import taskplane_lite as tp_lite  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
WF_DIR = os.path.join(ROOT, "workflows")
BRIEFS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "fixtures", "briefs")

# the shared frozen journey + scrub rules — the SAME module regen.py uses,
# so capture and replay can never drift apart
_sf_spec = importlib.util.spec_from_file_location(
    "stage_fixture", os.path.join(BRIEFS, "stage_fixture.py"))
stage_fixture = importlib.util.module_from_spec(_sf_spec)
_sf_spec.loader.exec_module(stage_fixture)
STAGES = ("execute-wave", "evaluate-wave", "fix-wave")
RECEIPT_STAGES = ("execute-wave", "fix-wave")
GATE_VERBS = ("gate", "approve", "signoff", "resolve")


def _path(stage: str) -> str:
    return os.path.join(WF_DIR, f"{stage}.js")


def _js(stage: str) -> str:
    with open(_path(stage), encoding="utf-8") as f:
        return f.read()


def _meta_block(src: str, stage: str) -> str:
    m = re.search(r"const\s+meta\s*=\s*\{(.*?)\n\};", src, re.S)
    assert m, f"workflows/{stage}.js must declare `const meta = {{...}};`"
    return m.group(1)


def _schema_block(src: str, name: str, stage: str) -> str:
    m = re.search(rf"const\s+{name}\s*=\s*\{{(.*?)\n\}};", src, re.S)
    assert m, f"workflows/{stage}.js must declare `const {name} = {{...}};`"
    return m.group(1)


def _contracts() -> list:
    """The SHIPPED design contract list — from the frozen snapshot
    fixtures/briefs/shipped_contracts.json, read programmatically (never
    hand-copied into a test).

    Contract-turnover rule: design/contract.json always describes the
    NEXT design cycle — it is REPLACED wholesale at every new design
    gate — so it stops carrying the Phase 2 field-list pins the moment a
    new phase's design lands. Schema pins for the SHIPPED surface must
    therefore derive from a stable source: the snapshot was captured
    verbatim from the Phase 2 contract that shipped these workflows
    (`git show 6a3d581:design/contract.json`, the `contracts` table) and
    changes ONLY when a shipped contract deliberately changes shape."""
    with open(os.path.join(BRIEFS, "shipped_contracts.json"), encoding="utf-8") as f:
        contracts = json.load(f)["contracts"]
    if any(c["id"] == "contract:wave-workflow" for c in contracts):
        return contracts
    raise AssertionError(
        "shipped_contracts.json must carry contract:wave-workflow")


def _spec(cid: str) -> dict:
    return next(c for c in _contracts() if c["id"] == cid)


def _receipt_fields() -> list:
    """receipts[{task, outcome, note}] — parsed from the contract text."""
    m = re.search(r"receipts\[\{([^}]+)\}\]",
                  _spec("contract:wave-workflow")["description"])
    assert m, "contract:wave-workflow must pin the receipt field list"
    fields = [x.strip() for x in m.group(1).split(",")]
    assert fields
    return fields


def _findings_fields() -> list:
    """findings[{severity, class, ...}] — from contract:findings-v2."""
    m = re.search(r"findings\[\{([^}]+)\}\]",
                  _spec("contract:findings-v2")["description"])
    assert m, "contract:findings-v2 must pin the findings field list"
    fields = [x.strip() for x in m.group(1).split(",")]
    assert fields
    return fields


def _stage_names_from_contract() -> list:
    """`name is execute-wave|evaluate-wave|fix-wave` — parsed, not typed."""
    m = re.search(r"name\s+is\s+([a-z|-]+)",
                  _spec("contract:wave-workflow")["description"])
    assert m, "contract:wave-workflow must pin the stage workflow names"
    names = m.group(1).split("|")
    assert len(names) == 3
    return names


# ------------------------------------------------------------ files + meta


class TestWorkflowFiles:
    def test_all_three_ship_in_plugin_workflows_dir(self):
        for stage in STAGES:
            assert os.path.isfile(_path(stage)), f"{stage}.js missing"

    def test_meta_is_a_pure_literal_in_each(self):
        for stage in STAGES:
            meta = _meta_block(_js(stage), stage)
            # A pure literal has no calls, no interpolation, no identifiers
            # doing work — the runtime reads it without executing the body.
            assert "(" not in meta and ")" not in meta, stage
            assert "${" not in meta and "`" not in meta, stage
            assert f"'{stage}'" in meta, stage
            assert re.search(r"description:\s*'[^']+'", meta), stage
            assert re.search(r"phases:\s*\[", meta), stage

    def test_meta_names_match_contract_wave_workflow(self):
        """The three stage names come from contract:wave-workflow — the
        emitter (t4) selects workflow{name} from the same list, so a rename
        on either side fails here."""
        for name in _stage_names_from_contract():
            meta = _meta_block(_js(name), name)
            assert re.search(rf"name:\s*'{re.escape(name)}'", meta)


# ------------------------------------------------------------- determinism


class TestDeterminism:
    def test_no_clock_no_random_no_dynamic_import_no_process(self):
        for stage in STAGES:
            src = _js(stage)
            assert "Date.now" not in src and "new Date" not in src, stage
            assert "Math.random" not in src, stage
            assert "import(" not in src and "require(" not in src, stage
            assert "process." not in src, stage

    def test_zero_gate_verbs_in_every_stage_file(self):
        """R-0004: no generated stage run contains an approval step — the
        workflow is transport; humans decide at conversation level AFTER
        the run returns. Substring scan, case-insensitive, comments
        included: gate verbs must not appear in any spelling."""
        for stage in STAGES:
            low = _js(stage).lower()
            for verb in GATE_VERBS:
                assert verb not in low, \
                    f"{stage}.js contains gate verb {verb!r}"


# ----------------------------------------------------------- schema pins


class TestSchemaPins:
    def test_execute_and_fix_pin_the_receipt_contract(self):
        """RECEIPT_SCHEMA field list is derived from contract:wave-workflow
        (receipts[{task, outcome, note}]), not hand-copied — drift in
        either direction fails here."""
        fields = _receipt_fields()
        for stage in RECEIPT_STAGES:
            src = _js(stage)
            schema = _schema_block(src, "RECEIPT_SCHEMA", stage)
            for field in fields:
                assert re.search(rf"\b{re.escape(field)}\b", schema), \
                    f"{stage}.js RECEIPT_SCHEMA missing field {field!r}"
            # every contract field is REQUIRED, not merely present
            m = re.search(r"required:\s*\[([^\]]*)\]", schema)
            assert m, f"{stage}.js RECEIPT_SCHEMA must pin required fields"
            required = re.findall(r"'([^']+)'", m.group(1))
            assert sorted(required) == sorted(fields), stage
            assert "output_contract.output_schema || RECEIPT_SCHEMA" in src, stage

    def test_evaluate_consumes_the_canonical_evaluator_contract(self):
        src = _js("evaluate-wave")
        for token in ("output_contract.output_schema", "resume_identity",
                      "max_attempts", "taskplane.evaluator-output/v1"):
            assert token in src
        assert "typeof output_schema !== 'object'" in src

    def test_every_stage_returns_transport_receipts_only(self):
        for stage in STAGES:
            src = _js(stage)
            assert re.search(r"return\s+\{\s*receipts:", src), stage


# ------------------------------------------- dispatch shape + args


class TestDispatchShape:
    def test_parallel_fanout_of_agent_thunks(self):
        """One agent() per brief, wrapped as thunks and fanned out with a
        single parallel() barrier — tasks in one wave are independent by
        plan construction."""
        for stage in STAGES:
            src = _js(stage)
            assert "parallel(" in src, stage
            assert "agent(" in src, stage
            assert re.search(r"\.map\(\(\w+\)\s*=>\s*\(\)\s*=>",
                             src), f"{stage}.js must map briefs to thunks"
            assert "await parallel(" in src, stage

    def test_execute_and_evaluate_consume_args_briefs(self):
        for stage in ("execute-wave", "evaluate-wave"):
            src = _js(stage)
            assert "args.briefs" in src, stage
            assert "agent(b.prompt" in src, \
                f"{stage}.js must pass the brief prompt to agent() verbatim"

    def test_fix_consumes_args_verdicts(self):
        src = _js("fix-wave")
        assert "args.verdicts" in src
        assert "agent(v.prompt" in src, \
            "fix-wave.js must pass the verdict brief prompt verbatim"

    def test_prompts_are_verbatim_no_interpolation(self):
        """The harness governs, the workflow transports: the Task-path
        prompt text (per-brief `export TASKPLANE_TASK=<slot>` and the
        claim/submit/CLEAR protocol) must ride through agent() untouched,
        so no template literal may rewrite it anywhere in the file."""
        for stage in STAGES:
            src = _js(stage)
            assert "`" not in src and "${" not in src, stage

    def test_per_brief_labels_and_phases(self):
        for stage, prefix in (("execute-wave", "task"),
                              ("evaluate-wave", "eval"),
                              ("fix-wave", "fix")):
            src = _js(stage)
            assert f"'{prefix}:' + " in src, \
                f"{stage}.js must label each agent per brief"
            assert re.search(r"phase\('[^']+'\)", src), stage

    def test_return_keys_per_stage(self):
        assert "receipts:" in _js("execute-wave")
        assert "receipts:" in _js("fix-wave")
        assert "receipts:" in _js("evaluate-wave")


# =====================================================================
# t4 — the tp.py stage emitter + Codex parity
# =====================================================================


def _clean_env(monkeypatch):
    for v in stage_fixture.SCRUB_VARS:
        monkeypatch.delenv(v, raising=False)


_REAL_CLI_TIME = cli._time


class _FrozenCliClock:
    """Keep sequential rail captures at one host-observation instant."""

    time = staticmethod(_REAL_CLI_TIME.time)
    gmtime = staticmethod(_REAL_CLI_TIME.gmtime)

    @staticmethod
    def strftime(fmt, *args):
        if fmt == "%Y-%m-%dT%H:%M:%SZ":
            return "2026-01-01T00:00:00Z"
        return _REAL_CLI_TIME.strftime(fmt, *args)


def _freeze_cli_clock(monkeypatch):
    monkeypatch.setattr(cli, "_time", _FrozenCliClock)


def _golden_bytes(name: str) -> str:
    """The golden's JSON body EXACTLY as stored (comment header stripped,
    bytes kept) — pins the normalization itself, not just the value."""
    with open(os.path.join(BRIEFS, name), encoding="utf-8") as f:
        raw = f.read()
    return "".join(l for l in raw.splitlines(keepends=True)
                   if not l.startswith("#"))


def _trace_events(ws, event):
    p = os.path.join(tp_lite.tp_dir(ws), "trace.jsonl")
    if not os.path.isfile(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(l) for l in f
                if l.strip() and json.loads(l).get("event") == event]


@pytest.fixture(scope="module")
def rails():
    """ONE frozen journey (stage_fixture.py), every rail captured per
    stage: bare Task-path stdout, explicit --emit task, the Codex-env
    capture (CODEX_HOME + TASKPLANE_WORKFLOWS=1 — Codex must still win),
    and the --emit workflow capture (opted in). Module-scoped: the journey
    is real git+loop work; each stage's engine payload is produced once,
    then replayed through the real CLI emitter on the other rails. This
    keeps the parity boundary honest: rendering a payload must not mint
    three extra ReviewKernel runs, dispatch intents, or signed leases merely
    so the test can compare transport choices. The captures are immutable
    strings.

    Env is managed by hand (not monkeypatch) because the fixture outlives
    any single test's autouse TASKPLANE_HOME patch."""
    saved = {v: os.environ.get(v) for v in
             stage_fixture.SCRUB_VARS + ("TASKPLANE_HOME",)}
    saved_cli_time = cli._time
    for v in stage_fixture.SCRUB_VARS:
        os.environ.pop(v, None)
    os.environ["TASKPLANE_HOME"] = tempfile.mkdtemp(prefix="tp-stage-store-")
    cli._time = _FrozenCliClock
    try:
        ws = stage_fixture.build_repo(tempfile.mkdtemp(prefix="tp-stage-ws-"))
        caps = {}

        def grab(stage):
            bare = stage_fixture.capture_stage(ws, stage)
            frozen = json.loads(bare)
            producer_name = "wave" if stage == "execute" else "next_action"
            producer = getattr(loop, producer_name)

            def replay(*_args, **_kwargs):
                return json.loads(json.dumps(frozen))

            setattr(loop, producer_name, replay)
            try:
                task = stage_fixture.capture_stage(
                    ws, stage, "--emit", "task")
                os.environ["CODEX_HOME"] = "/x"
                os.environ["TASKPLANE_WORKFLOWS"] = "1"
                codex = stage_fixture.capture_stage(ws, stage)
                os.environ.pop("CODEX_HOME")
                wf = stage_fixture.capture_stage(
                    ws, stage)   # opt-in, no codex
                os.environ.pop("TASKPLANE_WORKFLOWS")
            finally:
                setattr(loop, producer_name, producer)
                os.environ.pop("CODEX_HOME", None)
                os.environ.pop("TASKPLANE_WORKFLOWS", None)
            caps[stage] = {"bare": bare, "task": task, "codex": codex,
                           "wf": wf}

        stage_fixture.start_loop(ws)
        grab("execute")
        stage_fixture.build_task(ws, "t1", "alpha")
        stage_fixture.build_task(ws, "t2", "beta")
        grab("evaluate")
        stage_fixture.to_fix_step(ws)
        grab("fix")
        yield {"ws": ws, "caps": caps,
               # resolved WHILE the journey's TASKPLANE_HOME is in effect
               "store": stage_fixture.store_root(ws)}
    finally:
        cli._time = saved_cli_time
        for v, val in saved.items():
            if val is None:
                os.environ.pop(v, None)
            else:
                os.environ[v] = val


# --------------------------------------- task-path byte identity (goldens)


class TestStageTaskPathByteIdentity:
    """R-0004's core claim, PROVEN: `--emit task` and the bare default are
    byte-identical, and both match the frozen goldens captured through the
    documented regen path."""

    def test_bare_default_equals_emit_task_byte_for_byte(self, rails):
        for stage in stage_fixture.STAGES:
            c = rails["caps"][stage]
            assert c["bare"] == c["task"], stage

    def test_task_path_stdout_matches_stage_goldens(self, rails):
        for stage in stage_fixture.STAGES:
            got = stage_fixture.scrubbed_bytes(
                rails["caps"][stage]["bare"], rails["ws"],
                store=rails["store"])
            assert got == _golden_bytes(stage_fixture.GOLDENS[stage]), \
                f"{stage}: Task-path stdout drifted from its golden — " \
                "regenerate ONLY for a deliberate shape change via " \
                "python3 taskplane/tests/fixtures/briefs/regen.py"

    def test_task_stdout_never_carries_workflow_keys(self, rails):
        for stage in stage_fixture.STAGES:
            payload = json.loads(rails["caps"][stage]["bare"])
            for key in ("dispatch_path", "workflow", "reason"):
                assert key not in payload, (stage, key)

    def test_next_action_preserves_stable_delivery_contract(self, rails):
        """Production next_action exposes how and where complete evidence
        was delivered without embedding host/sequence-dependent receipts."""
        for stage in ("evaluate", "fix"):
            delivery = json.loads(
                rails["caps"][stage]["bare"])["dashboard"]["delivery"]
            assert delivery["mode"] in {"inline", "complete-markdown"}
            for artifact in ("json", "markdown"):
                receipt = delivery["artifacts"][artifact]
                assert receipt["status"] == "available"
                assert receipt["path"]
                assert "bytes" not in receipt
                assert "sha256" not in receipt
            assert "semantic_bytes" not in delivery
            assert "semantic_sha256" not in delivery

    def test_codex_env_gets_task_bytes_even_when_opted_in(self, rails):
        """CODEX_HOME + TASKPLANE_WORKFLOWS=1: Codex always wins — stdout
        is byte-identical to the bare Task path (execute/evaluate/fix all
        resolve tier 'standard' → model null on every host, so there is
        not even the review-wave sweep's cheap-tier host delta here)."""
        for stage in stage_fixture.STAGES:
            c = rails["caps"][stage]
            codex = json.loads(c["codex"])
            bare = json.loads(c["bare"])
            # Capability and enforcement receipts are host observations and
            # deliberately differ. Remove only that routing metadata for the
            # canonical cross-host artifact comparison; retain it in both
            # real payloads for dispatch audit and strict enforcement.
            def canonical(payload):
                payload = json.loads(json.dumps(payload))

                def strip(value):
                    if isinstance(value, dict):
                        return {k: strip(v) for k, v in value.items()
                                if k not in ("dispatch_route",
                                             "dispatch_blocked",
                                             "enforcement")}
                    if isinstance(value, list):
                        return [strip(v) for v in value]
                    return value
                return strip(payload)

            assert canonical(codex) == canonical(bare), stage
            def contains_key(value, key):
                if isinstance(value, dict):
                    return key in value or any(
                        contains_key(child, key) for child in value.values())
                if isinstance(value, list):
                    return any(contains_key(child, key) for child in value)
                return False

            assert contains_key(codex, "dispatch_route") == \
                contains_key(bare, "dispatch_route"), stage
            assert "dispatch_path" not in codex, stage

    def test_chosen_path_is_traced_on_both_rails(self, rails):
        evs = _trace_events(rails["ws"], "stage_dispatch_path")
        assert evs, "stage_dispatch_path must be traced"
        for e in evs:
            assert e["stage"] in stage_fixture.STAGES
            assert e["path"] in ("task", "workflow")
            assert e.get("reason")
        by_stage = {s: [e for e in evs if e["stage"] == s]
                    for s in stage_fixture.STAGES}
        for stage, sevs in by_stage.items():
            # bare, --emit task, codex → task; the opt-in capture → workflow
            assert [e["path"] for e in sevs] == \
                ["task", "task", "task", "workflow"], stage
            assert "codex" in sevs[2]["reason"].lower(), stage

    def test_goldens_are_deterministic_artifacts(self):
        for stage in stage_fixture.STAGES:
            name = stage_fixture.GOLDENS[stage]
            body = _golden_bytes(name)
            payload = json.loads(body)
            assert body == stage_fixture.normalize(payload), \
                f"{name}: keys not sorted / normalization drifted"
            stage_fixture.assert_deterministic(body, name)

    def test_c1_activation_block_never_reaches_the_task_path(self):
        """C1 is workflow-rail ONLY. The Task path prints the loop's own
        payload — no composed agent prompt, hence no activation line —
        so the frozen stage goldens carry neither form. This is why the
        one sanctioned regeneration produced a ZERO-byte diff: the
        mandatory byte-identical fallback (and the only Codex path) is
        untouched by the Windows line. A golden that ever grows an
        activation line means the emitter leaked into the Task path."""
        for stage in stage_fixture.STAGES:
            body = _golden_bytes(stage_fixture.GOLDENS[stage])
            payload_bytes = body[body.index("{"):]      # drop the banner
            for form in ("export TASKPLANE_TASK", "set TASKPLANE_TASK"):
                assert form not in payload_bytes, (stage, form)

    def test_golden_headers_document_the_regen_command(self):
        for stage in stage_fixture.STAGES:
            with open(os.path.join(BRIEFS, stage_fixture.GOLDENS[stage]),
                      encoding="utf-8") as f:
                head = f.read(1200)
            assert head.startswith("#")
            assert "python3 taskplane/tests/fixtures/briefs/regen.py" in head


# ------------------------------------------------ workflow-path emission


class TestStageEmitterWorkflowPath:
    def test_execute_wave_compiles_to_one_run_covering_the_wave(self, rails):
        """At most ONE workflow run per stage between human gates: a single
        workflow{name, args} object whose briefs cover EVERY wave entry."""
        out = rails["caps"]["execute"]["wf"]
        payload = json.loads(out)
        assert payload["dispatch_path"] == "workflow"
        assert payload["reason"]
        assert out.count('"workflow":') == 1          # exactly one run
        wf = payload["workflow"]
        assert isinstance(wf, dict) and not isinstance(wf, list)
        assert wf["name"] == "execute-wave"
        briefs = wf["args"]["briefs"]
        assert [b["id"] for b in briefs] == \
            [e["task"]["id"] for e in payload["wave"]]
        assert len(briefs) == len(payload["wave"]) == 2

    def test_workflow_names_come_from_contract_wave_workflow(self, rails):
        """The emitted names and the shipped files both pin to the SAME
        contract list — a rename on either side fails here."""
        names = _stage_names_from_contract()
        got = [json.loads(rails["caps"][s]["wf"])["workflow"]["name"]
               for s in stage_fixture.STAGES]
        assert got == names

    def test_evaluate_and_fix_args_keys_match_the_workflow_files(self, rails):
        ev = json.loads(rails["caps"]["evaluate"]["wf"])["workflow"]
        fx = json.loads(rails["caps"]["fix"]["wf"])["workflow"]
        assert list(ev["args"].keys()) == ["briefs"]    # args.briefs
        assert list(fx["args"].keys()) == ["verdicts"]  # args.verdicts
        assert ev["args"]["briefs"][0]["id"] == "t1"
        assert fx["args"]["verdicts"][0]["id"] == "t1"
        assert ev["args"]["briefs"][0]["worktree"].endswith(".tp-work/t1")

    def test_agent_prompts_are_task_path_bytes_verbatim(self, rails):
        """contract:wave-workflow — prompts are VERBATIM on both rails:
        each brief prompt is the per-task slot export + the Task-path
        payload's own instruction (the claim/submit-not-advance protocol)
        + the Task-path entry serialized EXACTLY as the Task path prints
        it. Nothing is rewritten."""
        # execute: per-brief entry = the wave entry
        wf_payload = json.loads(rails["caps"]["execute"]["wf"])
        task_payload = json.loads(rails["caps"]["execute"]["task"])
        assert wf_payload["wave"] == task_payload["wave"]
        for brief, entry in zip(wf_payload["workflow"]["args"]["briefs"],
                                task_payload["wave"]):
            tid = entry["task"]["id"]
            assert brief["prompt"].startswith(
                f"export TASKPLANE_TASK={tid}\n\n")
            assert task_payload["instruction"] in brief["prompt"]
            assert json.dumps(entry, indent=2) in brief["prompt"]
            # submit-not-advance, verbatim from the Task-path instruction
            assert "loop submit" in brief["prompt"]
            assert "The orchestrator alone runs the matching `loop gate`" \
                in brief["prompt"]
        # evaluate/fix: the entry is the WHOLE Task-path payload — the
        # prompt embeds the exact bytes the Task path printed on stdout
        for stage in ("evaluate", "fix"):
            task_out = rails["caps"][stage]["task"]
            task_payload = json.loads(task_out)
            wf = json.loads(rails["caps"][stage]["wf"])["workflow"]
            key = "verdicts" if stage == "fix" else "briefs"
            brief = wf["args"][key][0]
            assert brief["prompt"].startswith(
                "export TASKPLANE_TASK=t1\n\n")
            assert task_payload["instruction"] in brief["prompt"]
            assert json.dumps(task_payload, indent=2) in brief["prompt"], \
                f"{stage}: prompt must embed the Task-path stdout verbatim"
            assert "loop submit" in brief["prompt"]

    # ---------------------------------------------- C1 (R-0009)

    def _all_briefs(self, rails):
        """(stage, brief) for EVERY brief the three stage runs emit."""
        for stage in stage_fixture.STAGES:
            wf = json.loads(rails["caps"][stage]["wf"])["workflow"]
            key = "verdicts" if stage == "fix" else "briefs"
            for brief in wf["args"][key]:
                yield stage, brief

    def test_every_prompt_carries_both_activation_lines(self, rails):
        """C1 (R-0009): `export TASKPLANE_TASK=<slot>` is POSIX-only, so a
        cmd.exe agent silently never activates its slot and lands in the
        slot-less union screen. Every emitted stage prompt therefore also
        carries the LABELLED cmd form `set TASKPLANE_TASK=<slot>` for the
        SAME slot — the hooks.json commandWindows precedent. The POSIX
        line stays FIRST and unchanged in wording (the Windows line is an
        ADDITION, never a replacement), and both name the same slot: a
        drifting pair would activate the wrong contract on one host."""
        seen = 0
        for stage, brief in self._all_briefs(rails):
            slot, prompt = brief["id"], brief["prompt"]
            assert prompt.startswith(f"export TASKPLANE_TASK={slot}\n"), \
                f"{stage}: the POSIX line must stay first and unchanged"
            assert f"\nset TASKPLANE_TASK={slot}\n" in prompt, \
                f"{stage}: no cmd-form activation line for slot {slot}"
            label = prompt.split(f"\nset TASKPLANE_TASK={slot}\n")[0]
            assert "Windows" in label and "cmd.exe" in label, \
                f"{stage}: the cmd form must be LABELLED for its host"
            # exactly one activation line per form, and the SAME slot in
            # both — no second slot may reach either line
            found = re.findall(r"^(export|set) TASKPLANE_TASK=(\S+)$",
                               prompt, re.M)
            assert [f[0] for f in found] == ["export", "set"], \
                f"{stage}: expected exactly one POSIX then one cmd line"
            assert {f[1] for f in found} == {slot}, (stage, found)
            seen += 1
        assert seen == 4, "2 execute briefs + evaluate + fix"

    def test_activation_block_is_ascii_for_cmd_consoles(self, rails):
        """The activation block is read (and retyped) in a cmd.exe console
        whose default code page is not UTF-8 — it stays pure ASCII. Only
        the block is pinned; the Task-path bytes that follow are VERBATIM
        and not this test's to constrain."""
        for stage, brief in self._all_briefs(rails):
            block = brief["prompt"].split(
                f"\nset TASKPLANE_TASK={brief['id']}\n")[0]
            block.encode("ascii")     # raises → non-ASCII in the block

    def test_stage_agent_prompt_only_prepends_the_activation_block(self):
        """C1 is emitter-side and additive: everything after the activation
        block is still the Task-path instruction + the entry serialized
        exactly as the Task path prints it."""
        entry = {"task": {"id": "t7"}}
        prompt = cli._stage_agent_prompt("t7", "INSTRUCTION", entry)
        tail = "INSTRUCTION\n\n" + json.dumps(entry, indent=2)
        assert prompt.endswith(tail)
        head = prompt[:-len(tail)]
        assert head.startswith("export TASKPLANE_TASK=t7\n")
        assert "set TASKPLANE_TASK=t7\n" in head
        assert "TASKPLANE_TASK" not in tail

    def test_emitted_run_contains_no_gate_step(self, rails):
        """R-0004: no generated stage run contains an approval/gate step.
        The run STRUCTURE (workflow name, args keys, brief ids/worktrees)
        is scanned for gate verbs; the agent prompts are excluded because
        they carry the Task-path protocol text VERBATIM (which necessarily
        SAYS 'only the orchestrator gates') — that text is pinned verbatim
        above, and gates stay at conversation level by construction."""
        for stage in stage_fixture.STAGES:
            wf = json.loads(json.dumps(
                json.loads(rails["caps"][stage]["wf"])["workflow"]))
            for briefs in wf["args"].values():
                for b in briefs:
                    b.pop("prompt")
            low = json.dumps(wf).lower()
            for verb in GATE_VERBS:
                assert verb not in low, (stage, verb)


# ---------------------------------------------------- kill-switch matrix


class TestStageKillSwitchMatrix:
    """The emitter's path choice is pinned to tp.workflow_available's truth
    table in BOTH directions: each case asserts the expected availability
    on the detector itself AND that the stage stdout carries workflow keys
    exactly when the detector says available — reuse, no drift."""

    @pytest.fixture()
    def wave_ws(self, tmp_path, monkeypatch):
        _clean_env(monkeypatch)
        ws = stage_fixture.build_repo(str(tmp_path))
        stage_fixture.start_loop(ws)
        return ws

    def _paths_match_detector(self, ws, expected_available):
        got = cli.workflow_available(ws)
        assert got["available"] is expected_available
        out = stage_fixture.capture_stage(ws, "execute")
        payload = json.loads(out)
        if expected_available:
            assert payload["dispatch_path"] == "workflow"
            assert payload["workflow"]["name"] == "execute-wave"
        else:
            for key in ("dispatch_path", "workflow", "reason"):
                assert key not in payload

    def test_falsey_spellings_always_force_the_task_path(self, wave_ws,
                                                         monkeypatch):
        for val in ("0", "false", "no", "off", "FALSE", " Off "):
            _clean_env(monkeypatch)
            monkeypatch.setenv("TASKPLANE_WORKFLOWS", val)
            monkeypatch.setenv("CLAUDE_CODE_WORKFLOWS", "1")  # loses
            self._paths_match_detector(wave_ws, False)

    def test_truthy_spellings_opt_in_to_the_workflow_path(self, wave_ws,
                                                          monkeypatch):
        for val in ("1", "true", "yes", "on", "TRUE"):
            _clean_env(monkeypatch)
            monkeypatch.setenv("TASKPLANE_WORKFLOWS", val)
            self._paths_match_detector(wave_ws, True)

    def test_codex_markers_always_win_over_opt_in(self, wave_ws,
                                                  monkeypatch):
        for marker in ("CODEX_HOME", "CODEX_THREAD_ID"):
            _clean_env(monkeypatch)
            monkeypatch.setenv(marker, "/x")
            monkeypatch.setenv("TASKPLANE_WORKFLOWS", "1")
            self._paths_match_detector(wave_ws, False)

    def test_claude_marker_enables_falsey_marker_does_not(self, wave_ws,
                                                          monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv("CLAUDE_CODE_WORKFLOWS", "1")
        self._paths_match_detector(wave_ws, True)
        for val in ("0", "false", "no", "off"):
            _clean_env(monkeypatch)
            monkeypatch.setenv("CLAUDE_CODE_WORKFLOWS", val)
            self._paths_match_detector(wave_ws, False)

    def test_conservative_default_is_the_task_path(self, wave_ws,
                                                   monkeypatch):
        _clean_env(monkeypatch)
        self._paths_match_detector(wave_ws, False)

    def test_single_detector_the_emitter_never_parses_the_env(self):
        """contract R-0004: the stage emitter calls tp.workflow_available
        DIRECTLY — no second env parse anywhere in the emitter."""
        emitter_src = "".join(inspect.getsource(fn) for fn in (
            cli._emit_stage, cli._stage_wave_run, cli._stage_agent_prompt))
        assert "workflow_available(" in inspect.getsource(cli._emit_stage)
        for needle in ("environ", "getenv", "TASKPLANE_WORKFLOWS",
                       "CLAUDE_CODE_WORKFLOWS", "CODEX"):
            assert needle not in emitter_src, needle


# ------------------------------------------------------------- resume


def test_resume_fixture():
    """Killed-mid-stage journal replay (STATIC fixture): completed agents'
    cached results are reused — never re-dispatched — and incomplete ones
    re-run. Journal keying is valid ONLY because each stage workflow labels
    its agents with the deterministic literal rule '<prefix>:' + brief id;
    the fixture is bound to that rule programmatically."""
    with open(os.path.join(BRIEFS, "stage_journal_killed.json"), encoding="utf-8") as f:
        fx = json.load(f)
    stage = fx["workflow"]
    assert stage in STAGES
    src = _js(stage)
    m = re.search(r"label:\s*'([a-z]+):' \+ ", src)
    assert m, f"{stage}.js must label agents with a literal prefix rule"
    prefix = m.group(1)
    labels = [f"{prefix}:{b['id']}" for b in fx["briefs"]]
    completed = {e["label"]: e["cached"]
                 for e in fx["journal"]["completed"]}
    assert set(completed) <= set(labels), "journal keys must be brief labels"
    # THE resume contract: cached results reused, incomplete re-run.
    rerun = [l for l in labels if l not in completed]
    assert rerun == fx["journal"]["killed_during"]
    assert not set(rerun) & set(completed)
    # the cached result honors the schema the workflow pins (receipt shape)
    for cached in completed.values():
        assert sorted(cached.keys()) == sorted(_receipt_fields())


def test_resume_relies_on_pinned_script_determinism():
    """The journal replay above is the HOST runtime's; the script-side
    property that makes it possible is determinism (no clock, no random,
    no dynamic loading — pinned by TestDeterminism) plus the literal label
    rule per stage. Re-assert the binding for all three stages."""
    for stage, prefix in (("execute-wave", "task"),
                          ("evaluate-wave", "eval"),
                          ("fix-wave", "fix")):
        src = _js(stage)
        assert f"label: '{prefix}:' + " in src, stage
        assert "Date.now" not in src and "Math.random" not in src, stage


def test_double_submit_same_outcome_is_a_noop(tmp_path, monkeypatch):
    """Engine-side idempotence (the other half of resume safety): a
    resumed agent that already submitted re-submits the SAME outcome and
    the engine returns the ORIGINAL submission unchanged — byte-equal,
    including fingerprint and submitted_at — pinned against loop.py's
    REAL submit through a throwaway workspace."""
    _clean_env(monkeypatch)
    ws = stage_fixture.build_repo(str(tmp_path))
    stage_fixture.start_loop(ws)
    aws = os.path.join(ws, ".tp-work", "t1")
    stage_fixture._git(ws, "worktree", "add", "-q", aws, "-b", "tp/t1")
    assert loop.claim(ws, "t1", aws)["claimed"] == "t1"
    with open(os.path.join(aws, "src", "alpha", "m.py"), "w", encoding="utf-8") as f:
        f.write("x = 2\n")
    stage_fixture._git(aws, "add", "-A")
    stage_fixture._git(aws, "commit", "-qm", "t1")
    first = loop.submit(ws, "pass", task_id="t1")
    second = loop.submit(ws, "pass", task_id="t1")
    assert first["submitted"] and second["submitted"]
    assert second["submission"] == first["submission"]   # the no-op
    stored = next(t for t in loop.load(ws)["tasks"] if t["id"] == "t1")
    assert stored["_submission"] == first["submission"]
    # and neither submission advanced state — workers never transition
    assert first["transitioned"] is False
    assert second["transitioned"] is False
    assert loop.load(ws)["step"] == "execute"


# ---------------------------- every gate reachable WITHOUT workflows


def _walk_repo(tmp: str) -> str:
    import subprocess
    ws = os.path.join(tmp, "walk")
    os.makedirs(os.path.join(ws, "src", "core"))
    with open(os.path.join(ws, "src", "core", "a.py"), "w", encoding="utf-8") as f:
        f.write("VALUE = 1\n")
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "init"]):
        subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                        *args], cwd=ws, check=True, capture_output=True)
    return ws


def _walk_design_contract(ws, req):
    """A minimal COMPLETE Design Contract (the test_design_workflow.py
    recipe) so the design gate + human approval are reachable."""
    import depgraph
    import design_contract as dc
    fp = (depgraph.load(ws).get("meta") or {}).get("content_fingerprint")
    contract = {
        "schema": "taskplane.design/v1",
        "requirement": req["id"],
        "title": "Governed walk design",
        "summary": "Walk every gate on the dispatch rail.",
        "current_state": {"summary": "Serial loop over one module.",
                          "sources": ["src/core/a.py"]},
        "alternatives": [
            {"id": "a", "name": "State", "description": "In-loop design.",
             "tradeoffs": {"gains": ["one rail"], "costs": ["state grows"],
                           "revisit_when": "lifecycle splits"}},
            {"id": "b", "name": "Sidecar", "description": "Outside design.",
             "tradeoffs": {"gains": ["small loop"], "costs": ["drift"],
                           "revisit_when": "compat dominates"}}],
        "selected_approach": "a",
        "decision": "Optional design phase with explicit approval.",
        "modules": {"existing": ["taskplane"], "new": ["skills/tp-design"]},
        "contracts": [{"relation": "provides",
                       "id": "contract:design-artifact",
                       "description": "Approved design evidence"}],
        "graph": {
            "baseline_fingerprint": fp,
            "proposed_modules": ["taskplane", "skills/tp-design"],
            "proposed_edges": [
                {"from": "skills/tp-design", "to": "taskplane",
                 "kind": "runtime", "reason": "skill drives the loop"},
                {"from": "taskplane", "to": "contract:design-artifact",
                 "kind": "provides", "reason": "engine emits evidence"}],
            "depth_policy": {"local_depth": 3,
                             "boundary_mode": "contract-only",
                             "contract_depth": 1, "requirement_depth": 1},
            "dor": [{"check": "baseline graph is current", "evidence": fp}],
            "dod": [{"check": "realized graph matches proposal",
                     "evidence": "final engineering review"}]},
        "acceptance_map": [
            {"criterion": c, "design_element": "design approval gate",
             "validation": "state-machine regression test",
             "tests": [
                 "taskplane/tests/test_stage_waves.py::"
                 "test_every_gate_reachable_without_workflows",
             ]}
            for c in req["acceptance"]],
        "risks": [{"risk": "state regression", "mitigation": "opt-in",
                   "owner": "engineering"}],
        "failure_modes": [{"mode": "evidence changes after approval",
                           "detection": "fingerprint mismatch",
                           "recovery": "re-approve"}],
        "observability": {"signals": ["design gate trace"],
                          "alerts": ["stale design rejection"]},
        "rollout": {"strategy": "opt-in flag", "rollback": "init without"},
        "visualization": {"required": False, "kind": "none", "path": None,
                          "reason": "doc + graph edge suffice"},
        "lens_evidence": [{"lens": "solution-design", "verdict": "pass",
                           "blockers": 0,
                           "evidence": "alternatives + boundaries checked",
                           "produced_by": "tp-lens solution-design run",
                           "independent": True}],
        "open_questions": [],
    }
    os.makedirs(os.path.join(ws, "design"), exist_ok=True)
    with open(os.path.join(ws, "design", "design.md"), "w", encoding="utf-8") as f:
        f.write("# Governed walk\n\nOptional design phase.\n")
    contract["lens_evidence"][0]["content_fingerprint"] = \
        dc.design_content_fingerprint(ws, contract)
    with open(os.path.join(ws, "design", "contract.json"), "w", encoding="utf-8") as f:
        json.dump(contract, f, indent=2)
    return contract


def _author_kernel_results(ws):
    import review
    import review_evidence
    kernel = review._load_state(ws)
    store = review_evidence.ArtifactStore(ws)
    for index, slot in enumerate(kernel["slots"]):
        lease = store.read(slot["lease"])
        brief = store.read(slot["brief"])
        row = {**lease, "schema": "taskplane.lens-slot-output/v2",
               "authored_by": "lens-slot", "findings": [],
               "lens_results": [{"lens": lens_id, "verdict": "pass",
                                  "blockers": 0,
                                  "checked_evidence": [{
                                      "file": "taskplane/review.py",
                                      "line": 1,
                                      "claim": "review kernel contract checked",
                                  }]}
                                for lens_id in lease["lens_ids"]]}
        if brief.get("language_references"):
            row["references_applied"] = list(brief["language_references"])
        producer = brief["producer_contract"]
        content = json.dumps(row, sort_keys=True, separators=(",", ":"))
        # One observed child may own only one immutable lease. Evaluate and
        # final engineering review create distinct runs in this same test
        # workspace, so bind the synthetic hook identity to the run as the
        # real dispatcher does.
        event = {"session_id": f"stage-lens-session-{kernel['run_id']}",
                 "agent_id":
                     f"stage-lens-child-{kernel['run_id'][:8]}-{index}",
                 "tool_name": "Write",
                 "tool_input": {"file_path": slot["result_path"],
                                "content": content}}
        contract = {"task": producer["task"], "task_id": "stage-lens",
                    "read_only": True,
                    "write_allow": producer["write_allow"]}
        review.register_slot_producer(
            ws, event=event, contract=contract,
            task_slot=producer["task_slot"])
        review.record_slot_write_observation(
            ws, event=event, contract=contract,
            task_slot=producer["task_slot"])
        path = os.path.join(ws, slot["result_path"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


def _walk_pass_eval(ws):
    import depgraph
    import review
    state = loop.load(ws)
    task = state["tasks"][state["current_task"]]
    act_ws = task.get("workspace") or ws
    # The evaluator must disposition the exact ReviewKernel route that was
    # dispatched. Re-deriving from the live diff can legitimately see an
    # empty tree after execute evidence is committed while the kernel still
    # carries its bounded automatic sweep.
    kernel = review._load_state(act_ws)
    routed_lenses = sorted({
        lens_id
        for slot in (kernel.get("slots") or [])
        for lens_id in (slot.get("lens_ids") or [])
    })
    criteria = loop._criteria_for(ws, state, task)
    os.makedirs(os.path.join(act_ws, ".eval"), exist_ok=True)
    graph_dod = loop._task_graph_dod(ws, state, task)
    impact = graph_dod.get("impact") or {}
    direct = sorted({e.get("module")
                     for e in (impact.get("impacted") or {}).get(1, [])
                     if e.get("module")
                     and not str(e.get("module")).startswith("req:")})
    contracts = [c.get("id") if isinstance(c, dict) else c
                 for c in (task.get("contracts") or [])]
    with open(os.path.join(act_ws, ".eval", "verdict.json"), "w", encoding="utf-8") as f:
        json.dump({"schema": "taskplane.evaluator-output/v1",
                   "task": task["id"],
                   "requirement": task.get("req") or
                                  state.get("requirement_id") or "",
                   "verdict": "pass",
                   "criteria": [{"criterion": c, "status": "met",
                                 "evidence": "verified by test"}
                                for c in criteria],
                   "lenses": [{"lens": lens_id, "verdict": "pass",
                               "blockers": 0}
                              for lens_id in routed_lenses],
                   "graph": {"dispositions": [
                       {"node": n, "status": "tested",
                        "evidence": "covered by declared task tests"}
                       for n in direct],
                       "requirements_checked": [],
                       "contracts_checked": contracts},
                   "failures": []}, f)
    _author_kernel_results(act_ws)
    submitted = loop.submit(ws, "pass")
    assert submitted.get("submitted"), submitted
    gated = loop.gate(ws, "pass")
    assert gated.get("step") == "em", gated
    return gated


def _walk_collect_observed_evaluate(ws, act_ws, state, task, *,
                                    step="evaluate"):
    """Model the external host adapter for this compatibility-only walk."""
    from producer_observation import (
        consume_matching_observation,
        record_codex_subagent_stop,
    )
    material = loop.producer_output_identity(
        act_ws, state, task, step,
        active_contract=tp_lite.load_active(act_ws) or {})
    if step == "em":
        return consume_matching_observation(**material)
    import evaluation_output
    binding = loop.review_kernel_binding(state, "evaluate", task)
    kernel_ws = str(binding.get("workspace") or act_ws)
    run_id = binding["run_id"]
    event = {"hook_event_name": "SubagentStop",
             "session_id": "stage-wave-compat-session",
             "turn_id": f"evaluate-{run_id}",
             "agent_id": f"evaluate-{run_id}",
             "agent_type": material["producer_dispatch"]["task_name"],
             "task_name": material["producer_dispatch"]["task_name"]}
    claim = hashlib.sha256(tp_lite.hook_event_identity(
        act_ws, "subagent-stop", event).encode("utf-8")).hexdigest()
    record_codex_subagent_stop(
        event=event, hook_claim_id=claim, **material)
    observation = consume_matching_observation(**material)
    loop.collect_review_bridge(
        kernel_ws, publish=False, run_id=run_id,
        evaluator_result=evaluation_output.validate_evaluator_value(
            json.loads(material["output_bytes"])),
        producer_observation_fingerprint=observation["fingerprint"],
    )
    return observation


def _walk_pass_em(ws, state):
    import depgraph
    import lens
    import review
    import review_evidence
    # realize the designed edges so the as-built graph carries the designed
    # modules (the reviewer's evidence must match reality)
    depgraph.record_edge(ws, "skills/tp-design", "taskplane", kind="runtime")
    depgraph.record_edge(ws, "taskplane", "contract:design-artifact",
                         kind="provides")
    # Author one result per immutable lease, then let the production collect
    # path validate provenance and create the single canonical revision.
    kernel = review._load_state(ws)
    store = review_evidence.ArtifactStore(ws)
    _author_kernel_results(ws)
    if kernel.get("status") == "complete" and kernel.get("slots") == []:
        collected = kernel
    else:
        collected = review.collect_review(ws, publish=False)
    assert collected["status"] == "complete"
    changed = [f for f in loop._diff_files(
        ws, state.get("baseline") or "HEAD")
        if not f.startswith(lens.LOOP_OWNED)]
    impact = depgraph.impact(ws, changed)
    findings_path = os.path.join(ws, ".em-review", "findings.json")
    with open(findings_path, encoding="utf-8") as f:
        findings = json.load(f)
    meta = {**findings["meta"], "impact": impact, "tests": ["true"],
            "gate": {"verdict": "recommend-pass"},
            "design": {
                "fingerprint": state["design_fingerprint"],
                "verdict": "conformant",
                "modules_checked": ["taskplane", "skills/tp-design"],
                "edges_checked": [
                    "skills/tp-design->taskplane:runtime",
                    "taskplane->contract:design-artifact:provides"],
                "contracts_checked": ["contract:design-artifact"],
                "edge_evidence": [
                    {"edge": "taskplane->contract:design-artifact:provides",
                     "evidence": "design approval emits the artifact; "
                                 "regression test passes",
                     "declared_by": "reviewer — hand-recorded edge"}],
                "drift": []}}
    with open(findings_path, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "findings": findings["findings"]}, f)
    from producer_observation import record_codex_subagent_stop
    material = loop.producer_output_identity(
        ws, loop.load(ws), loop._current_task(loop.load(ws)), "em",
        active_contract=tp_lite.load_active(ws) or {})
    event = {"hook_event_name": "SubagentStop",
             "session_id": "stage-wave-compat-session",
             "turn_id": "em-turn", "agent_id": "em-agent",
             "agent_type": material["producer_dispatch"]["task_name"],
             "task_name": material["producer_dispatch"]["task_name"]}
    claim = hashlib.sha256(tp_lite.hook_event_identity(
        ws, "subagent-stop", event).encode("utf-8")).hexdigest()
    record_codex_subagent_stop(
        event=event, hook_claim_id=claim, **material)
    submitted = loop.submit(ws, "pass")
    assert submitted.get("submitted"), submitted
    return loop.gate(ws, "pass")


def test_every_gate_reachable_without_workflows(tmp_path, monkeypatch):
    """R-0004 adversarial walk: with the org kill-switch DOWN
    (TASKPLANE_WORKFLOWS=0) the FULL journey — init→pm→design→plan→
    execute→evaluate→em→signoff→retro — reaches every gate via the dispatch
    rail alone, and no CLI dispatch surface ever prints a workflow key.
    Zero workflow coupling on the mandatory path."""
    import depgraph
    import requirements as reqs
    _clean_env(monkeypatch)
    monkeypatch.setenv("TASKPLANE_WORKFLOWS", "0")
    # A real host supplies this receipt out-of-band.  This transport test
    # installs an equally strict recorded-host adapter; the production
    # no-receipt selector remains fail-closed and is exercised separately.
    monkeypatch.setattr(
        loop, "_collect_zero_lens_evaluate_before_guidance",
        _walk_collect_observed_evaluate)
    assert cli.workflow_available(".")["available"] is False
    ws = _walk_repo(str(tmp_path))
    req = reqs.record_requirement(
        ws, "governed walk", functional=["walk every gate"],
        nfr={"reliability": "every governed gate remains reachable",
             "security": "workflow disablement never weakens a gate",
             "architecture": "dispatch remains independent of workflows"},
        acceptance=["every gate is reachable via dispatch",
                    "no step depends on a workflow runtime"],
        contracts=[{"relation": "provides",
                    "id": "contract:design-artifact"}],
        context_files=["src/core/**"])
    depgraph.scan(ws)

    def nxt(expect_step):
        rc, out = stage_fixture.cli("loop", "--workspace", ws, "next")
        assert rc == 0, out
        payload = json.loads(out)
        action = payload.get("current_action", payload)
        assert action.get("step") == expect_step, payload
        for key in ("dispatch_path", "workflow"):
            assert key not in payload, (expect_step, key)
            assert key not in action, (expect_step, key)
        return action

    loop.init(ws, "governed walk", requirement_id=req["id"], design=True)
    nxt("pm")
    assert loop.gate(ws, "pass").get("step") != "pm"          # pm gate
    assert loop.load(ws)["step"] == "design"
    nxt("design")
    _walk_design_contract(ws, req)
    assert loop.gate(ws, "pass")["step"] == "design_approval"  # design gate
    assert nxt("design_approval")["paused"]                    # human gate
    assert loop.approve(ws, by="human — walk")["step"] == "plan"
    nxt("plan")
    state = loop.load(ws)
    tasks = [{"id": "t1", "scope": ["src/core/**"], "tests": "true",
              "req": req["id"], "criteria": list(req["acceptance"]),
              "contracts": ["contract:design-artifact"],
              "new_modules": ["skills/tp-design", "taskplane"],
              "design_edges": [
                  "skills/tp-design->taskplane:runtime",
                  "taskplane->contract:design-artifact:provides"],
              "impact_policy": {"local_depth": 3,
                                "boundary_mode": "contract-only",
                                "contract_depth": 1,
                                "requirement_depth": 1}}]
    os.makedirs(os.path.join(ws, "plan"), exist_ok=True)
    with open(os.path.join(ws, "plan", "tasks.json"), "w", encoding="utf-8") as f:
        json.dump({"requirement": req["id"], "delivery_mode": "build",
                   "automatic_lenses": [],
                   "plan_authority": "human:walk", "tasks": tasks},
                  f, indent=2)
    with open(os.path.join(ws, "plan", "plan.md"), "w", encoding="utf-8") as f:
        f.write("# Plan\n\nOne task realizes the approved design.\n")
    assert loop.gate(ws, "pass")["step"] == "plan_approval"    # plan gate
    assert nxt("plan_approval")["paused"]                      # human gate
    assert loop.approve(ws, by="human — walk")["step"] == "execute"
    # commit the earlier steps' authored artifacts (design/plan) so the
    # execute contract's scope diff starts clean — the engine's own
    # documented recovery for artifacts authored by earlier loop steps
    stage_fixture._git(ws, "add", "-A")
    stage_fixture._git(ws, "commit", "-qm", "design + plan artifacts")
    nxt("execute")                                     # stage dispatch
    assert loop.submit(ws, "pass")["submitted"]
    assert loop.gate(ws, "pass")["step"] == "evaluate"         # execute gate
    evaluate_action = nxt("evaluate")                  # stage dispatch
    evaluate_state = loop.load(ws)
    evaluate_binding = loop.review_kernel_binding(
        evaluate_state, "evaluate", loop._current_task(evaluate_state))
    assert evaluate_binding["run_id"] == \
        evaluate_action["review_kernel"]["run_id"]
    assert _walk_pass_eval(ws)["step"] == "em"                 # evaluate gate
    nxt("em")
    assert _walk_pass_em(ws, loop.load(ws))["step"] == "signoff"  # em gate
    assert nxt("signoff")["paused"]                            # human gate
    assert loop.approve(ws, by="human — walk")["step"] == "retro"
    assert loop.retro(ws)["graph_true_up"]["content_fingerprint"]
    assert loop.load(ws)["step"] == "done"
    # every stage_dispatch_path choice on this walk was the task rail,
    # forced by the kill-switch
    evs = _trace_events(ws, "stage_dispatch_path")
    assert evs, "the stage dispatches must be traced"
    for e in evs:
        assert e["path"] == "task"
        assert "TASKPLANE_WORKFLOWS=0" in e["reason"]


# --------------------------- workflow-agnostic modules (extended pin)


class TestWorkflowAgnosticModulesExtended:
    def test_loop_lens_and_audit_have_zero_workflow_coupling(self):
        """The R-0002 pin (loop.py/lens.py workflow-agnostic) EXTENDS to
        audit.py: the stage emitter lives in tp.py ONLY, so no gate can
        ever be reachable only via workflows. Canonical product identities
        such as ``workflow_id`` and stage dispatch-set names are allowed;
        coupling means importing, selecting, or launching the optional JS
        workflow transport."""
        transport_markers = (
            "workflows/", "TASKPLANE_WORKFLOWS",
            "CLAUDE_CODE_WORKFLOWS", "workflow_available(",
        )
        for mod in ("loop.py", "lens.py", "audit.py"):
            with open(os.path.join(ROOT, "taskplane", mod), encoding="utf-8") as f:
                src = f.read()
            for marker in transport_markers:
                assert marker not in src, (mod, marker)


# =====================================================================
# A6 (R-0007) — malformed wave entries degrade to the Task path
# E5 (R-0011) — un-slottable ids refuse emission at compose time
# =====================================================================


def _loop_cli(ws, *argv):
    """Run `tp loop ...` in-process capturing BOTH streams."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(["loop", "--workspace", ws, *argv])
    return rc, out.getvalue(), err.getvalue()


def _malformed_wave_payload():
    """A wave payload whose SECOND entry lost its task.id — the shape the
    A6 negative fixture pins (pre-A6 this KeyError'd the emitter)."""
    return {"step": "execute", "parallel": True,
            "instruction": "Dispatch ONE governed subagent per wave entry.",
            "wave": [
                {"task": {"id": "t1"}, "worktree": "w1"},
                {"task": {"scope": ["src/**"]}, "worktree": "w2"},
            ]}


class TestMalformedWaveEntryFailOpen:
    """A6: a malformed wave entry (missing task/id) must NEVER crash the
    emitter — it degrades to the Task path (the mandatory fallback can
    always print what the loop printed) with a traced reason."""

    @pytest.fixture()
    def ws(self, tmp_path, monkeypatch):
        _clean_env(monkeypatch)
        _freeze_cli_clock(monkeypatch)
        return stage_fixture.build_repo(str(tmp_path))

    def _run_with_payload(self, ws, payload, monkeypatch, *extra):
        monkeypatch.setattr(loop, "wave", lambda _ws: payload)
        return _loop_cli(ws, "wave", *extra)

    def test_missing_task_id_degrades_to_task_path_stdout(self, ws,
                                                          monkeypatch):
        payload = _malformed_wave_payload()
        rc, out, err = self._run_with_payload(ws, payload, monkeypatch)
        assert rc == 0                                   # no crash, no error
        assert out == json.dumps(payload, indent=2) + "\n"  # Task-path bytes
        for key in ("dispatch_path", "workflow"):
            assert key not in json.loads(out)
        evs = _trace_events(ws, "stage_dispatch_path")
        assert evs and evs[-1]["path"] == "task"
        assert "malformed wave entry" in evs[-1]["reason"]
        assert "fail-open" in evs[-1]["reason"]

    def test_degrade_wins_even_under_explicit_emit_workflow(self, ws,
                                                            monkeypatch):
        """--emit workflow cannot force a brief that cannot be composed:
        the malformed entry aborts workflow wrapping BEFORE the emit
        override is consulted — Task-path stdout, traced reason, exit 0."""
        monkeypatch.setenv("TASKPLANE_WORKFLOWS", "1")
        payload = _malformed_wave_payload()
        rc, out, err = self._run_with_payload(ws, payload, monkeypatch,
                                              "--emit", "workflow")
        assert rc == 0
        assert out == json.dumps(payload, indent=2) + "\n"
        evs = _trace_events(ws, "stage_dispatch_path")
        assert evs[-1]["path"] == "task"
        assert "malformed wave entry" in evs[-1]["reason"]

    def test_entry_not_a_dict_and_task_not_a_dict_both_degrade(self, ws,
                                                               monkeypatch):
        for bad in ("just-a-string", {"task": "t1"}, {"task": None}):
            payload = {"step": "execute", "parallel": True,
                       "instruction": "x",
                       "wave": [{"task": {"id": "t1"}}, bad]}
            rc, out, _ = self._run_with_payload(ws, payload, monkeypatch)
            assert rc == 0, bad
            assert json.loads(out) == payload, bad
        evs = _trace_events(ws, "stage_dispatch_path")
        assert all("malformed wave entry" in e["reason"] for e in evs[-3:])

    def test_malformed_single_task_step_degrades_traced(self, ws,
                                                        monkeypatch):
        """The same validation covers the single-task step shapes: an
        evaluate payload whose task lost its id degrades to the Task
        path with the traced reason, never a silent skip or a crash."""
        payload = {"step": "evaluate", "instruction": "x",
                   "task": {"workspace": "w1"}}
        monkeypatch.setattr(loop, "next_action",
                            lambda _ws, rid=None: payload)
        _clean_env(monkeypatch)
        rc, out, _ = _loop_cli(ws, "next")
        assert rc == 0
        assert json.loads(out) == payload
        evs = _trace_events(ws, "stage_dispatch_path")
        assert evs and evs[-1]["path"] == "task"
        assert evs[-1]["stage"] == "evaluate"
        assert "malformed wave entry" in evs[-1]["reason"]

    def test_well_formed_wave_unchanged_on_the_workflow_rail(self, rails):
        """The A6 guard adds validation only: the frozen journey's
        well-formed emission is byte-covered by the existing golden and
        workflow-path pins (re-asserted here against the capture)."""
        payload = json.loads(rails["caps"]["execute"]["wf"])
        assert payload["dispatch_path"] == "workflow"
        assert len(payload["workflow"]["args"]["briefs"]) == 2


class TestSlotCharsetRefusalAtEmission:
    """E5: an id the emitter would embed into `export TASKPLANE_TASK=<id>`
    must already BE a valid slot (taskplane_lite._TASK_SLOT_RE — the ONE
    enforced charset). Invalid → refuse the WORKFLOW emission fail-closed,
    traced, BEFORE any prompt line is composed. Never sanitize.

    Phase 3 EM review (deep3 finding #2): the refusal is scoped to the
    workflow RAIL. The Task path composes no shell line, is the MANDATORY
    fallback and the only rail on Codex, so it degrades instead of being
    denied — see TestSlotCharsetNeverDeniesTheTaskPath below."""

    @pytest.fixture()
    def ws(self, tmp_path, monkeypatch):
        _clean_env(monkeypatch)
        _freeze_cli_clock(monkeypatch)
        return stage_fixture.build_repo(str(tmp_path))

    def _wave_with_id(self, tid):
        return {"step": "execute", "parallel": True, "instruction": "x",
                "wave": [{"task": {"id": tid}, "worktree": "w"}]}

    def test_shell_metacharacter_id_refuses_on_the_workflow_rail(
            self, ws, monkeypatch):
        monkeypatch.setenv("TASKPLANE_WORKFLOWS", "1")   # workflow-capable
        monkeypatch.setattr(loop, "wave",
                            lambda _ws: self._wave_with_id("t1;rm"))
        rc, out, err = _loop_cli(ws, "wave")             # default auto
        assert rc != 0
        assert out == ""                       # nothing composed, no payload
        # no COMPOSED export line anywhere (the reason may cite the
        # template form, but never an activation line carrying the id)
        assert "export TASKPLANE_TASK=t1;rm" not in out + err
        assert "t1;rm" in err                  # reason names the id...
        assert tp_lite._TASK_SLOT_RE.pattern in err   # ...and the charset
        evs = _trace_events(ws, "stage_dispatch_path")
        assert evs and evs[-1]["path"] == "refused"
        assert "t1;rm" in evs[-1]["reason"]

    def test_invalid_id_refuses_on_explicit_emit_workflow_too(self, ws,
                                                              monkeypatch):
        monkeypatch.setenv("TASKPLANE_WORKFLOWS", "1")
        monkeypatch.setattr(loop, "wave",
                            lambda _ws: self._wave_with_id("$(evil)"))
        rc, out, err = _loop_cli(ws, "wave", "--emit", "workflow")
        assert rc != 0
        assert out == ""
        assert "$(evil)" in err
        evs = _trace_events(ws, "stage_dispatch_path")
        assert evs[-1]["path"] == "refused"

    def test_single_task_step_ids_are_validated_too(self, ws, monkeypatch):
        monkeypatch.setenv("TASKPLANE_WORKFLOWS", "1")
        payload = {"step": "fix", "instruction": "x",
                   "task": {"id": "t 1", "workspace": "w"}}
        monkeypatch.setattr(loop, "next_action",
                            lambda _ws, rid=None: payload)
        rc, out, err = _loop_cli(ws, "next")
        assert rc != 0 and out == ""
        assert "t 1" in err
        evs = _trace_events(ws, "stage_dispatch_path")
        assert evs[-1]["path"] == "refused" and evs[-1]["stage"] == "fix"

    def test_valid_ids_emit_byte_identically(self, rails):
        """All-valid payloads are untouched by the E5 guard — the frozen
        journey's Task-path bytes still match the goldens (re-asserted
        against the capture; the full golden compare lives above)."""
        for stage in stage_fixture.STAGES:
            got = stage_fixture.scrubbed_bytes(
                rails["caps"][stage]["bare"], rails["ws"],
                store=rails["store"])
            assert got == _golden_bytes(stage_fixture.GOLDENS[stage]), stage

    def test_charset_single_source_is_the_enforced_slot_regex(self):
        """The emitter validates against taskplane_lite._TASK_SLOT_RE
        itself — no second regex that could drift from the screener's."""
        src = inspect.getsource(cli._valid_slot_id)
        assert "_TASK_SLOT_RE" in src
        assert cli._valid_slot_id("t1") and cli._valid_slot_id("fix.a-2_b")
        for bad in ("t1;rm", "$(x)", "a b", "", ".hidden", None, 7):
            assert not cli._valid_slot_id(bad), bad


class TestSlotCharsetNeverDeniesTheTaskPath:
    """Phase 3 EM review, deep3 finding #2 (HIGH regression): E5's charset
    refusal fired in `_emit_stage` BEFORE the rail was chosen and regardless
    of --emit, so a task id like `feat/login` made `loop wave` / `loop next`
    exit 1 with EMPTY stdout on every host — including a definitively
    workflow-less one, where the Task path is the only rail there is. The
    same docstring calls that path "the MANDATORY fallback and the only
    Codex path", and C3's own refusal text points refused users at it.

    The Task path interpolates the id into nothing (it prints the engine's
    own payload verbatim), so a slot-charset problem can never be a reason
    to deny it."""

    BAD = "feat/login"
    GOOD = "feat-login"

    @pytest.fixture()
    def ws(self, tmp_path, monkeypatch):
        _clean_env(monkeypatch)
        _freeze_cli_clock(monkeypatch)
        return stage_fixture.build_repo(str(tmp_path))

    def _payload(self, tid):
        return {"step": "evaluate", "instruction": "Evaluate the task.",
                "task": {"id": tid, "workspace": "w"}}

    def _run_next(self, ws, monkeypatch, tid, *argv):
        monkeypatch.setattr(loop, "next_action",
                            lambda _ws, rid=None: self._payload(tid))
        return _loop_cli(ws, "next", *argv)

    def test_task_path_emits_normally_for_a_bad_id_on_a_codex_host(
            self, ws, monkeypatch):
        monkeypatch.setenv("CODEX_HOME", "/codex")   # definitively no runtime
        rc, out, err = self._run_next(ws, monkeypatch, self.BAD)
        assert rc == 0, err
        assert json.loads(out)["task"]["id"] == self.BAD
        assert "dispatch_path" not in json.loads(out)
        evs = _trace_events(ws, "stage_dispatch_path")
        assert evs[-1]["path"] == "task"      # degraded, NOT refused
        assert self.BAD in evs[-1]["reason"]  # still named in the trace

    def test_task_path_bytes_differ_only_by_the_id(self, ws, monkeypatch):
        """The mandatory-fallback invariant: a bad id changes the id and
        nothing else about what the Task path prints."""
        for emit in ([], ["--emit", "task"]):
            _, bad_out, _ = self._run_next(ws, monkeypatch, self.BAD, *emit)
            _, good_out, _ = self._run_next(ws, monkeypatch, self.GOOD, *emit)
            assert bad_out and good_out
            assert bad_out.replace(self.BAD, self.GOOD) == good_out, emit

    def test_explicit_emit_task_is_never_refused_even_where_workflows_run(
            self, ws, monkeypatch):
        monkeypatch.setenv("TASKPLANE_WORKFLOWS", "1")
        rc, out, err = self._run_next(ws, monkeypatch, self.BAD,
                                      "--emit", "task")
        assert rc == 0, err
        assert json.loads(out)["task"]["id"] == self.BAD
        assert _trace_events(ws, "stage_dispatch_path")[-1]["path"] == "task"

    def test_workflow_rail_still_refuses_the_same_id(self, ws, monkeypatch):
        """The guard itself is intact where it belongs: the workflow rail
        composes the export line, so it still refuses fail-closed."""
        monkeypatch.setenv("TASKPLANE_WORKFLOWS", "1")
        rc, out, err = self._run_next(ws, monkeypatch, self.BAD,
                                      "--emit", "workflow")
        assert rc != 0 and out == ""
        assert self.BAD in err
        assert _trace_events(ws,
                             "stage_dispatch_path")[-1]["path"] == "refused"


class TestPlanGateRefusesUnslottableTaskIds:
    """Phase 3 EM review, deep3 finding #2, the EARLY half: nothing
    validated task ids against the slot charset before the human plan
    gate, so a plan carrying `feat/login` cleared approval and only broke
    at execute/evaluate/fix — where the remedy (renaming ids in
    plan/tasks.json) costs a re-plan and a re-approval. The check now runs
    at BOTH plan transitions, via taskplane_lite.plan_ordering_refusal."""

    def test_plan_task_id_errors_names_every_offender(self):
        errs = tp_lite.plan_task_id_errors([
            {"id": "t1"}, {"id": "feat/login"}, {"id": "a b"},
            {"id": "." + "x"}, {"id": None}, {"id": "x" * 65},
            "not-a-dict",
        ])
        assert len(errs) == 5
        for bad in ("feat/login", "a b", ".x", "None", "x" * 65):
            assert any(bad in e for e in errs), bad
        assert tp_lite._TASK_SLOT_RE.pattern in errs[0]
        assert tp_lite.plan_task_id_errors([{"id": "t1"},
                                            {"id": "fix.a-2_b"}]) == []

    def test_gate_refuses_a_plan_with_a_bad_id(self, tmp_path):
        ws = str(tmp_path / "ws")
        os.makedirs(ws)
        refusal = tp_lite.plan_ordering_refusal(
            ws, [{"id": "feat/login"}, {"id": "t2"}], "gate")
        assert refusal is not None
        assert refusal["step"] == "plan"
        assert "feat/login" in refusal["error"]
        assert refusal["task_ids"] and refusal["ordering"] == []
        evs = _trace_events(ws, "loop_gate_blocked")
        assert evs[-1]["reason"] == "task_id"

    def test_approve_refuses_the_same_plan(self, tmp_path):
        ws = str(tmp_path / "ws")
        os.makedirs(ws)
        refusal = tp_lite.plan_ordering_refusal(
            ws, [{"id": "feat/login"}], "approve", by="human")
        assert refusal is not None and refusal["step"] == "plan_approval"
        assert "feat/login" in refusal["error"]
        evs = _trace_events(ws, "loop_approve_blocked")
        assert evs[-1]["reason"] == "task_id" and evs[-1]["by"] == "human"

    def test_good_ids_still_approve(self, tmp_path):
        ws = str(tmp_path / "ws")
        os.makedirs(ws)
        for where in ("gate", "approve"):
            assert tp_lite.plan_ordering_refusal(
                ws, [{"id": "t1"}, {"id": "fix.a-2_b", "deps": ["t1"]}],
                where) is None, where
