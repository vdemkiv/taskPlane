"""The derivation ledger — the instrument for "did the model skip steps or
reinvent the wheel", and the proof that measuring it never denies anything.

Two rubric items need a fact the harness never recorded:

  R7a  did the run RE-DERIVE expensive work (the diff, the blast radius, the
       graph scan) instead of deriving it once and sharing it?
  R10  did it invent a CLI surface — a subcommand that does not exist?

`.taskplane/derivations.jsonl` answers both, because until now an ALLOWED
command left no trace of what ran: only refusals reached `trace.jsonl`
(`hook_deny`). A ledger of what was allowed is new evidence, and evidence
collected at the enforcement boundary is exactly where an instrument can
turn into a wall by accident.

So this file is two halves, and the second half is the important one:

  1. the instrument does its job — one screened `tp review start` records
     BOTH derivations it performs, an argument never reaches a row, the
     pre-flight probe proves the row hit the disk, and `repeats()` is the
     one place the R7a arithmetic lives;

  2. the instrument CANNOT DENY. Every screen payload is compared byte for
     byte against the SAME scenario run through the baseline `tp.py` read
     out of git — with the ledger present, absent, unwritable, blocked by a
     directory, and with the writer/classifier/input-key each raising on
     every call.

Two defects this file exists to keep dead, both from the previous attempt:

  * an obligation id (`o-a590b2f59e`) has verb shape, so a depth-2 verb
    extractor that accepted "any lowercase identifier" as a subcommand
    recorded `tp ack o-a590b2f59e` — an ARGUMENT in the ledger;
  * the test that was supposed to catch that could not fail: all of its
    rows had arguments disqualified by SHAPE (paths, flags, shas), so the
    leaking shape was never exercised. The table below carries arguments
    that are ordinary lowercase words — `retro`, `status`, `pass` — which
    are real taskplane verbs somewhere else in the tree.
"""
import ast
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))
import derivation as dv           # noqa: E402
import obligations as ob          # noqa: E402
import target as tgt              # noqa: E402
import taskplane_lite as tpl      # noqa: E402
import tp as cli                  # noqa: E402


def _ws() -> str:
    return tempfile.mkdtemp(prefix="tp-derivation-")


def _governed(ws, *, task_id="task_fixed", **kw) -> dict:
    """A contract with a PINNED task_id — block payloads quote it, and a
    random id would make the differential comparison meaningless."""
    c = tpl.build_contract(kw.pop("task", "t: derivation"),
                           scope=kw.pop("scope", ["src/**"]), **kw)
    c["task_id"] = task_id
    tpl.activate(ws, c, snapshot=None)
    return c


def _pinned(ws, *, head="h" * 40, base=None, files=()):
    rec = {"ok": True, "root": ws, "origin": None, "head": head,
           "changed_files": list(files)}
    if base is not None:
        rec["base"] = base
    tgt.save(ws, rec)
    return rec


def _payload(mod, ws, tool_input, *, tool_name="Bash", extra=None) -> str:
    """Run the REAL hook body in-process and return exactly what it wrote to
    stdout — empty string means ABSTAIN (no decision emitted)."""
    event = {"cwd": ws, "tool_name": tool_name, "tool_input": tool_input}
    event.update(extra or {})
    out = io.StringIO()
    old_in, old_out = sys.stdin, sys.stdout
    sys.stdin = io.StringIO(json.dumps(event))
    sys.stdout = out
    try:
        mod._screen(None)
    finally:
        sys.stdin, sys.stdout = old_in, old_out
    return out.getvalue()


class TestStructuralMetrics(unittest.TestCase):
    def test_repeated_bytes_belong_only_to_the_second_derivation(self):
        rows = [
            {"event": "command", "verb": "tp review start",
             "emitted_bytes": 12},
            {"event": "derived", "key": "diff", "input_key": "b..h",
             "derived_bytes": 100},
            {"event": "derived", "key": "diff", "input_key": "b..h",
             "derived_bytes": 100},
        ]
        got = dv.metrics(rows=rows)
        self.assertEqual(got["cli_count"], 1)
        self.assertEqual(got["emitted_bytes"], 12)
        self.assertEqual(got["repeated_derivation_count"], 1)
        self.assertEqual(got["repeated_derivation_bytes"], 100)

    def test_missing_byte_instrumentation_is_named_not_invented(self):
        got = dv.metrics(rows=[
            {"event": "derived", "key": "impact", "input_key": "h"}])
        self.assertFalse(got["derivation_bytes_observed"])
        self.assertEqual(got["repeated_derivation_bytes"], 0)


# --------------------------------------------------------------------------
# 1. THE VERB, AND NOTHING BUT THE VERB
# --------------------------------------------------------------------------

# (command, expected verb, tokens that must NEVER appear in a ledger row).
# Rows 2-4 are the ones the previous table lacked: the argument is an
# ordinary lowercase word that is a REAL taskplane verb elsewhere in the
# tree, so neither a shape filter nor a "does it look like a subcommand"
# filter can disqualify it. Only knowing that `ack`/`claim` take no
# subcommand keeps it out.
ARGUMENT_TABLE = (
    ("python3 /x/taskplane/tp.py ack o-a590b2f59e", "tp ack",
     ("o-a590b2f59e",)),
    ("python3 /x/taskplane/tp.py ack retro", "tp ack", ("retro",)),
    ("python3 /x/taskplane/tp.py ack status", "tp ack", ("status",)),
    ("tp loop submit pass --task t01-record-loader", "tp loop submit",
     ("pass", "t01-record-loader")),
    ("tp loop claim --task t02-derivation-ledger", "tp loop claim",
     ("t02-derivation-ledger",)),
    ("tp graph impact --files taskplane/tp.py", "tp graph impact",
     ("taskplane/tp.py",)),
    ("tp kb retrieve --tags graph,scan", "tp kb retrieve", ("graph,scan",)),
    ("tp req new --score 4 'scan the graph'", "tp req new",
     ("scan the graph",)),
    ("git show 1f4b2c9d0a7e", "git show", ("1f4b2c9d0a7e",)),
    ("git diff main..HEAD", "git diff", ("main..HEAD",)),
    ("git commit -m 'tp dod'", "git commit", ("tp dod",)),
    ("cd /repo && python3 tp.py ack retro", "tp ack", ("/repo", "retro")),
    ("git -C /repo diff main..HEAD", "git diff", ("/repo", "main..HEAD")),
)


class TestTheRowCarriesTheVerbNeverTheArgument(unittest.TestCase):
    """A ledger row names WHAT RAN. Command text and arguments are the two
    things it must never carry: they are unbounded, they contain paths and
    prose, and the previous design leaked an obligation id straight into the
    row because the id had verb shape."""

    def test_no_argument_token_survives_into_the_verb(self):
        for command, expected, args in ARGUMENT_TABLE:
            with self.subTest(command=command):
                got = dv.verb(command)
                self.assertEqual(got, expected)
                for arg in args:
                    self.assertNotIn(arg, got)

    def test_a_verb_shaped_argument_is_not_absorbed(self):
        """THE regression. `tp ack o-a590b2f59e` recorded the obligation id
        because a depth-2 walk accepted any lowercase identifier as a
        subcommand. `ack` takes no subcommand, so the walk must stop."""
        self.assertEqual(dv.verb("python3 tp.py ack o-a590b2f59e"), "tp ack")
        self.assertEqual(dv.verb("python3 tp.py ack retro"), "tp ack")

    def test_an_invented_subcommand_is_marked_not_quoted(self):
        """R10 needs to SEE an invented surface, but printing the invented
        token would be printing an argument when the model merely mistyped
        an id. The marker records the fact without the text."""
        self.assertEqual(dv.verb("python3 tp.py blastradius --all"), "tp ?")
        self.assertEqual(dv.verb("python3 tp.py graph blastradius"),
                         "tp graph ?")
        self.assertEqual(dv.verb("git frobnicate x"), "git ?")

    def test_a_chained_line_names_the_command_that_did_the_work(self):
        """`cd repo && tp graph impact` is how an agent actually writes it.
        Reading only the first word of the line records `cd` and loses the
        derivation — an R7a undercount that looks like compliance."""
        self.assertEqual(dv.verb("cd /repo && python3 tp.py graph impact"),
                         "tp graph impact")
        self.assertEqual(dv.classify("cd /repo && python3 tp.py graph impact"),
                         ("impact",))
        self.assertEqual(dv.verb("python3 tp.py graph impact | jq ."),
                         "tp graph impact")

    def test_a_real_first_command_is_never_hidden_by_a_later_one(self):
        """Only NAVIGATION prefixes are skipped. Skipping any first segment
        would let `rm -rf x && tp status` be recorded as a `tp status`."""
        self.assertEqual(dv.verb("rm -rf /tmp/x && python3 tp.py status"),
                         "rm")
        self.assertEqual(dv.verb("echo 'tp dod' | sh"), "echo")

    def test_a_git_global_flag_does_not_hide_the_subcommand(self):
        """`git -C /repo diff` read its own repo path as the subcommand, so
        a diff derived that way was recorded as `git ?` and never counted."""
        self.assertEqual(dv.verb("git -C /repo diff main..HEAD"), "git diff")
        self.assertEqual(dv.classify("git -C /repo diff main..HEAD"),
                         ("diff",))
        self.assertEqual(dv.verb("git -c user.name=t commit -m x"),
                         "git commit")

    def test_a_bare_program_records_only_its_name(self):
        self.assertEqual(dv.verb("pytest -q taskplane/tests"), "pytest")
        self.assertEqual(dv.verb("rg -n 'def classify' taskplane"), "rg")
        self.assertEqual(dv.verb(""), "")

    def test_every_verb_the_tables_name_is_a_real_cli_command(self):
        """The instrument for "did it invent a CLI surface" must not itself
        contain an invented one. Checked against the LIVE argparse tree."""
        r = subprocess.run([sys.executable, os.path.join(ROOT, "taskplane",
                                                         "tp.py"),
                            "help", "--md"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, r.stderr)
        real = set(re.findall(r"^## `tp\.py ([^`]+)`", r.stdout, re.M))
        declared = set()
        for top, children in dv.TP_COMMANDS.items():
            declared.add(top)
            declared.update(f"{top} {c}" for c in children)
        for v in dv.DERIVED_BY_VERB:
            if v.startswith("tp "):
                declared.add(v[3:])
        self.assertTrue(declared)
        self.assertEqual(sorted(declared - real), [])


# --------------------------------------------------------------------------
# 2. CLASSIFICATION IS A TABLE, AND ONE COMMAND CAN DERIVE MORE THAN ONE THING
# --------------------------------------------------------------------------

class TestClassifyReturnsEveryDerivationOfOneCommand(unittest.TestCase):
    """`tp review start` exists BECAUSE it derives several expensive things
    in one call — it pins the target (the diff) and computes the blast
    radius (the impact). With one key per command the whole reference
    scenario is unsatisfiable: the model would look like it never derived
    the impact, and its own later `graph impact` would score as first use
    rather than as a repeat."""

    def test_review_start_derives_both_the_impact_and_the_diff(self):
        self.assertEqual(dv.classify("python3 tp.py review start --base main"),
                         ("impact", "diff"))

    def test_classify_always_returns_a_tuple(self):
        for command in ("python3 tp.py graph impact", "git diff main..HEAD",
                        "echo hi", ""):
            with self.subTest(command=command):
                self.assertIsInstance(dv.classify(command), tuple)

    def test_the_table_covers_the_expensive_derivations(self):
        self.assertEqual(dv.classify("python3 tp.py graph scan"),
                         ("graph_scan",))
        self.assertEqual(dv.classify("python3 tp.py graph impact"),
                         ("impact",))
        self.assertEqual(dv.classify("git diff main..HEAD"), ("diff",))
        self.assertEqual(dv.classify("python3 tp.py findings"), ("findings",))

    def test_an_ordinary_command_derives_nothing(self):
        self.assertEqual(dv.classify("echo hi"), ())
        self.assertEqual(dv.classify("pytest -q"), ())

    def test_every_key_the_table_emits_is_a_declared_key(self):
        for keys in dv.DERIVED_BY_VERB.values():
            for k in keys:
                self.assertIn(k, dv.KEYS)


class TestTheInputKeyIdentifiesTheWorkNotTheCall(unittest.TestCase):
    """Two calls that derive the SAME thing must collide on (key,
    input_key) — that collision is the whole R7a signal."""

    def test_the_diff_key_names_both_ends_of_the_diff(self):
        ws = _ws()
        _pinned(ws, head="h" * 40, base="b" * 40)
        self.assertEqual(dv.input_key(ws, "diff"), "b" * 40 + ".." + "h" * 40)
        self.assertEqual(dv.input_key(ws, "findings"),
                         dv.input_key(ws, "diff"))

    def test_an_empty_base_is_legitimate_and_still_keys(self):
        """A pinned checkout with no base ref is normal (`tp target pin` with
        no --base). The key must stay well-formed, not fall back to
        something that collides with a different head."""
        ws = _ws()
        _pinned(ws, head="c" * 40)
        self.assertEqual(dv.input_key(ws, "diff"), ".." + "c" * 40)

    def test_the_impact_key_binds_the_head_and_the_changed_set(self):
        ws = _ws()
        _pinned(ws, head="h" * 40, files=["b.py", "a.py"])
        first = dv.input_key(ws, "impact")
        self.assertTrue(first.startswith("h" * 40 + "|"))
        self.assertEqual(len(first.split("|")[1]), 16)
        # order of the changed set must not change the key ...
        _pinned(ws, head="h" * 40, files=["a.py", "b.py"])
        self.assertEqual(dv.input_key(ws, "impact"), first)
        self.assertEqual(dv.input_key(ws, "graph_scan"), first)
        # ... but its CONTENT must.
        _pinned(ws, head="h" * 40, files=["a.py", "c.py"])
        self.assertNotEqual(dv.input_key(ws, "impact"), first)

    def test_an_unpinned_workspace_still_yields_a_key(self):
        self.assertIsInstance(dv.input_key(_ws(), "diff"), str)


# --------------------------------------------------------------------------
# 3. THE LEDGER, THROUGH THE REAL HOOK
# --------------------------------------------------------------------------

class TestTheLedgerRecordsWhatWasAllowed(unittest.TestCase):
    """Until now an allowed command left NO trace of what ran; only refusals
    did (`hook_deny`). A rubric that asks what the model actually invoked
    had nothing to read."""

    def test_an_approved_command_leaves_a_command_row(self):
        ws = _ws()
        _governed(ws)
        _payload(cli, ws, {"command": "python3 tp.py graph impact"})
        rows = dv.read(ws)
        cmd = [r for r in rows if r["event"] == "command"]
        self.assertEqual(len(cmd), 1)
        self.assertEqual(cmd[0]["verb"], "tp graph impact")
        self.assertEqual(cmd[0]["decision"], "approve")
        self.assertIn("host", cmd[0])
        self.assertIsInstance(cmd[0]["ts"], float)

    def test_one_screened_review_start_records_both_derivations(self):
        ws = _ws()
        _governed(ws)
        _pinned(ws, head="h" * 40, base="b" * 40, files=["a.py"])
        _payload(cli, ws, {"command": "python3 tp.py review start --base main"})
        derived = [r for r in dv.read(ws) if r["event"] == "derived"]
        self.assertEqual([r["key"] for r in derived], ["impact", "diff"])
        self.assertEqual(derived[1]["input_key"], "b" * 40 + ".." + "h" * 40)

    def test_the_ledger_never_holds_the_command_text(self):
        ws = _ws()
        _governed(ws)
        _payload(cli, ws, {"command": "python3 tp.py kb retrieve --tags retro"})
        with open(dv.ledger_path(ws), encoding="utf-8") as f:
            body = f.read()
        self.assertIn("tp kb retrieve", body)
        self.assertNotIn("--tags", body)
        self.assertNotIn("retro", body)

    def test_a_denied_command_writes_no_row(self):
        """The deny path already records (`hook_deny` in trace.jsonl).
        Recording it twice would double-count every refusal in R7a."""
        ws = _ws()
        _governed(ws)
        p = _payload(cli, ws, {"command": "git push origin main"})
        self.assertEqual(json.loads(p)["decision"], "block")
        self.assertEqual(dv.read(ws), [])

    def test_an_ungoverned_workspace_records_nothing(self):
        ws = _ws()
        self.assertEqual(_payload(cli, ws, {"command": "echo hi"}), "")
        self.assertEqual(dv.read(ws), [])


class TestTheReleaseVerbsAreRecordedToo(unittest.TestCase):
    """`_screen` ABSTAINS for the release verbs — `status`, `contracts`,
    `version`, `ack` — and it does so LONG before the approve path where the
    ledger was written. So those commands left no row at all: R10 (did the
    run invent a CLI surface) and every efficiency reading were blind to
    them, and a run that polled `tp status` twenty times was indistinguishable
    from one that never called it.

    The abstain is unchanged. Only the recording is new — and because this
    is the enforcement hook, "unchanged" is proved byte for byte by the
    differential table above, which now carries the abstain path twice."""

    def test_an_abstained_release_verb_leaves_a_command_row(self):
        ws = _ws()
        _governed(ws)
        self.assertEqual(_payload(cli, ws,
                                  {"command": "python3 tp.py status"}), "",
                         "the release verb must still ABSTAIN")
        rows = [r for r in dv.read(ws) if r["event"] == "command"]
        self.assertEqual([r["verb"] for r in rows], ["tp status"])
        self.assertEqual(rows[0]["decision"], "abstain")
        self.assertIn("host", rows[0])
        self.assertIsInstance(rows[0]["ts"], float)

    def test_every_release_verb_reaches_the_ledger(self):
        for command, verb in (("python3 tp.py status", "tp status"),
                              ("python3 tp.py contracts", "tp contracts"),
                              ("python3 tp.py version", "tp version"),
                              ("python3 tp.py ack o-a590b2f59e", "tp ack")):
            with self.subTest(command=command):
                ws = _ws()
                _governed(ws)
                self.assertEqual(_payload(cli, ws, {"command": command}), "")
                self.assertEqual([r["verb"] for r in dv.read(ws)
                                  if r["event"] == "command"], [verb])

    def test_the_release_row_carries_the_verb_never_the_argument(self):
        """`tp ack o-a590b2f59e` is the command that leaked an obligation id
        into a row in the previous design. The new call site inherits the
        same walker, and this proves it rather than assuming it."""
        ws = _ws()
        _governed(ws)
        _payload(cli, ws, {"command": "python3 tp.py ack o-a590b2f59e"})
        with open(dv.ledger_path(ws), encoding="utf-8") as f:
            body = f.read()
        self.assertIn("tp ack", body)
        self.assertNotIn("o-a590b2f59e", body)

    def test_repeated_polling_is_now_visible(self):
        """The reading this unblocks: `tp status` called three times is three
        rows, not silence."""
        ws = _ws()
        _governed(ws)
        for _ in range(3):
            _payload(cli, ws, {"command": "python3 tp.py status"})
        self.assertEqual(len([r for r in dv.read(ws)
                              if r.get("verb") == "tp status"]), 3)

    def test_an_ungoverned_release_verb_still_records_nothing(self):
        """The recorder sits INSIDE the release branch, which is only
        reachable under a contract. An ungoverned workspace abstains earlier
        and must stay untouched."""
        ws = _ws()
        self.assertEqual(_payload(cli, ws,
                                  {"command": "python3 tp.py status"}), "")
        self.assertEqual(dv.read(ws), [])

    def test_a_release_verb_derives_nothing(self):
        """`status`/`ack` derive none of the four expensive things, so the
        new call site must not add phantom `derived` rows — they would score
        as R7a repeats against a run that derived nothing twice."""
        ws = _ws()
        _governed(ws)
        _payload(cli, ws, {"command": "python3 tp.py status"})
        self.assertEqual([r for r in dv.read(ws)
                          if r["event"] == "derived"], [])
        self.assertEqual(dv.repeats(ws), 0)


class TestRepeatsIsTheOnlyPlaceTheR7aArithmeticLives(unittest.TestCase):
    def test_deriving_the_same_thing_twice_is_one_repeat(self):
        ws = _ws()
        _pinned(ws, head="h" * 40, files=["a.py"])
        dv.record(ws, "python3 tp.py graph impact", "approve")
        dv.record(ws, "python3 tp.py graph impact", "approve")
        self.assertEqual(dv.repeats(ws), 1)

    def test_deriving_two_different_things_is_no_repeat(self):
        ws = _ws()
        _pinned(ws, head="h" * 40, base="b" * 40, files=["a.py"])
        dv.record(ws, "python3 tp.py graph impact", "approve")
        dv.record(ws, "git diff main..HEAD", "approve")
        self.assertEqual(dv.repeats(ws), 0)

    def test_a_different_head_is_not_a_repeat(self):
        ws = _ws()
        _pinned(ws, head="h" * 40, files=["a.py"])
        dv.record(ws, "python3 tp.py graph impact", "approve")
        _pinned(ws, head="k" * 40, files=["a.py"])
        dv.record(ws, "python3 tp.py graph impact", "approve")
        self.assertEqual(dv.repeats(ws), 0)

    def test_an_empty_ledger_is_zero_not_an_error(self):
        self.assertEqual(dv.repeats(_ws()), 0)


class TestTheProbeProvesTheInstrumentReachedDisk(unittest.TestCase):
    """A pre-flight that returns a healthy-looking id over an EMPTY ledger is
    the exact failure it exists to catch — the recorder would then measure a
    run whose instrument never wrote anything and report zero repeats as
    compliance."""

    def test_the_probe_id_is_actually_in_the_ledger(self):
        ws = _ws()
        _pinned(ws, head="h" * 40, files=["a.py"])
        pid = dv.probe(ws)
        self.assertTrue(pid)
        self.assertIn(pid, [r.get("id") for r in dv.read(ws)])

    def test_the_probe_returns_none_when_the_row_never_reached_disk(self):
        ws = _ws()
        os.makedirs(dv.ledger_path(ws))       # a directory where the file goes
        self.assertIsNone(dv.probe(ws))

    def test_the_probe_classifies_through_the_same_table(self):
        ws = _ws()
        _pinned(ws, head="h" * 40, files=["a.py"])
        dv.probe(ws)
        row = [r for r in dv.read(ws) if r.get("probe")][0]
        self.assertEqual(row["key"], "impact")
        self.assertEqual(row["input_key"], dv.input_key(ws, "impact"))
        self.assertEqual(dv.classify(dv.PROBE_COMMAND), ("impact",))

    def test_the_probe_row_is_excluded_from_repeats(self):
        """Without the exclusion a FULLY COMPLIANT run scores exactly one
        repeat and fails the item the probe exists to protect: the probe's
        `graph impact` and the model's own carry the same (key, input_key)."""
        ws = _ws()
        _pinned(ws, head="h" * 40, files=["a.py"])
        dv.probe(ws)
        dv.record(ws, "python3 tp.py graph impact", "approve")
        self.assertEqual(dv.repeats(ws), 0)
        # and the model's second one is still caught
        dv.record(ws, "python3 tp.py graph impact", "approve")
        self.assertEqual(dv.repeats(ws), 1)


# --------------------------------------------------------------------------
# 4. RECORDING ONLY — THE INSTRUMENT CANNOT DENY
# --------------------------------------------------------------------------

def _corrupt_contract(ws):
    os.makedirs(tpl.tp_dir(ws), exist_ok=True)
    with open(os.path.join(tpl.tp_dir(ws), "active_contract.json"), "w",
              encoding="utf-8") as f:
        f.write("{not json")


def _exhausted(ws):
    c = _governed(ws, max_actions=1)
    with open(os.path.join(tpl.tp_dir(ws), "meter.json"), "w",
              encoding="utf-8") as f:
        json.dump({c["task_id"]: {"actions": 1, "denies": 0}}, f)


def _corrupt_meter(ws):
    _governed(ws)
    with open(os.path.join(tpl.tp_dir(ws), "meter.json"), "w",
              encoding="utf-8") as f:
        f.write("{torn")


def _owes_an_artifact(ws):
    _governed(ws)
    oid = ob.issue(ws, "render_dashboard",
                   detail="the mission-control dashboard", step="execute",
                   key="dashboard.html", binding=True)
    assert oid, "the obligation scenario must really owe something"


def _read_only(ws):
    _governed(ws, read_only=True, write_allow=[".em-review/**"])


# (name, setup, tool_input, event extras) — every distinct payload the
# screener can emit, captured from the baseline and required to stay
# byte-identical.
SCENARIOS = (
    ("ungoverned-abstain", lambda ws: None, {"command": "echo hi"}, {}),
    ("contract-corrupt-block", _corrupt_contract, {"command": "echo hi"}, {}),
    ("release-command-abstain", _governed,
     {"command": "python3 tp.py status"}, {}),
    # `ack` is the release verb that carries an ARGUMENT, and the argument
    # is an obligation id — the exact token the previous ledger design
    # leaked. It earns its own row in this table because the abstain path is
    # now a recording call site: the differential has to cover a release
    # verb whose command text must never reach the disk.
    ("release-ack-abstain", _governed,
     {"command": "python3 tp.py ack o-a590b2f59e"}, {}),
    ("claude-approve", _governed, {"command": "echo hi"}, {}),
    ("codex-silent-approve", _governed, {"command": "echo hi"},
     {"turn_id": "t-1"}),
    ("scope-deny-block", _governed,
     {"file_path": "docs/x.md", "content": "x"}, {"tool_name": "Write"}),
    ("denied-command-block", _governed, {"command": "git push origin main"},
     {}),
    ("budget-exhausted-block", _exhausted, {"command": "echo hi"}, {}),
    ("meter-corrupt-block", _corrupt_meter, {"command": "echo hi"}, {}),
    ("obligation-block", _owes_an_artifact,
     {"command": "python3 tp.py dod"}, {}),
    ("target-unbound-block", _read_only,
     {"command": "python3 tp.py loop submit pass"}, {}),
)


def _baseline_module():
    """The shipped `tp.py` as of HEAD, imported from OUTSIDE the repo.

    Comparing the new screener against a re-read of the same file would
    prove nothing; this is the payload table as it was before the
    instrument existed."""
    src = subprocess.run(["git", "show", "HEAD:taskplane/tp.py"], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    if src.returncode != 0:
        raise unittest.SkipTest("git show HEAD:taskplane/tp.py failed")
    scratch = tempfile.mkdtemp(prefix="tp-baseline-")
    path = os.path.join(scratch, "tp_baseline.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(src.stdout)
    spec = importlib.util.spec_from_file_location("tp_baseline", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tp_baseline"] = mod
    before = list(sys.path)
    try:
        spec.loader.exec_module(mod)
    finally:
        # the module inserts its own (scratch) dir at sys.path[0] on import;
        # leaving it there would let a later import resolve out of a temp dir
        sys.path[:] = before
    return mod


class TestRecordingCannotChangeOneDecision(unittest.TestCase):
    """The rule that outranks the feature. This touches the enforcement
    hook: if the instrument can change one screen decision it is a defect,
    not a feature. Proven DIFFERENTIALLY — same scenario, baseline `tp.py`
    from git vs. the instrumented one, byte for byte."""

    @classmethod
    def setUpClass(cls):
        cls.baseline = _baseline_module()

    def _run_table(self, *, prepare=None):
        """Every scenario through both screeners; returns the two payload
        lists. Each side gets its OWN fresh workspace — screening mutates
        the meter — built by the same setup function."""
        base_out, live_out = [], []
        for name, setup, tool_input, extra in SCENARIOS:
            for mod, sink in ((self.baseline, base_out), (cli, live_out)):
                ws = _ws()
                setup(ws)
                if mod is cli and prepare:
                    prepare(ws)
                e = dict(extra)
                tool_name = e.pop("tool_name", "Bash")
                ti = dict(tool_input)
                if "file_path" in ti:
                    ti["file_path"] = os.path.join(ws, ti["file_path"])
                sink.append((name, _payload(mod, ws, ti, tool_name=tool_name,
                                            extra=e)))
        return base_out, live_out

    def _assert_identical(self, prepare=None, why=""):
        base_out, live_out = self._run_table(prepare=prepare)
        for (n1, p1), (n2, p2) in zip(base_out, live_out):
            with self.subTest(scenario=n1, condition=why):
                self.assertEqual(n1, n2)
                self.assertEqual(p1, p2)
        return base_out

    def test_the_payload_table_is_byte_identical_to_the_baseline(self):
        table = self._assert_identical(why="ledger writable")
        # the table must actually exercise the payloads it claims to
        blocks = [p for _n, p in table if p and
                  json.loads(p).get("decision") == "block"]
        self.assertEqual(len(set(blocks)), 7)   # 7 DISTINCT block payloads
        self.assertEqual(len([p for _n, p in table if not p.strip()]), 4)
        self.assertEqual(len([p for _n, p in table if p.strip() and
                              json.loads(p).get("decision") == "approve"]), 1)

    def test_identical_with_the_ledger_deleted(self):
        def prepare(ws):
            try:
                os.remove(dv.ledger_path(ws))
            except OSError:
                pass
        self._assert_identical(prepare, why="ledger deleted")

    def test_identical_with_a_directory_where_the_ledger_belongs(self):
        """`chmod 500` proves nothing here: CI and the container run as
        ROOT, which writes through the permission bits — the test would go
        green while testing nothing. A directory in the file's place fails
        for every user."""
        self._assert_identical(lambda ws: os.makedirs(dv.ledger_path(ws),
                                                      exist_ok=True),
                               why="directory blocks the ledger")

    def test_identical_with_an_unwritable_runtime_dir(self):
        """A ledger failure must not disable the pre-existing action meter.

        The old fixture chmod'ed all of ``.taskplane`` and failed in
        ``meter.json`` before derivation recording ran.  Redirect only the
        ledger to a parent that structurally cannot accept a child: a regular
        file.  Opening its child is a real ENOTDIR write failure for every
        user (including root), while the meter keeps its normal writable
        runtime directory.
        """
        real_path = dv.ledger_path

        def isolated_ledger_path(ws):
            return os.path.join(ws, "blocked-ledger-parent", dv.LEDGER_NAME)

        def prepare(ws):
            parent = os.path.dirname(isolated_ledger_path(ws))
            with open(parent, "w", encoding="utf-8") as f:
                f.write("not a directory")
            with self.assertRaises(OSError):
                with open(isolated_ledger_path(ws), "a", encoding="utf-8"):
                    pass

        with mock.patch.object(dv, "ledger_path", isolated_ledger_path):
            self._assert_identical(
                prepare, why="derivation ledger parent rejects writes")
        self.assertIs(dv.ledger_path, real_path)

    def test_identical_when_the_instrument_raises_on_every_call(self):
        """Belt and suspenders: the module swallows its own failures AND the
        call site is guarded. Injecting a raise into each seam proves the
        call site, which is the layer that stands if the module is ever
        replaced."""
        def boom(*a, **k):
            raise RuntimeError("instrument exploded")
        for name in ("record", "classify", "input_key", "_append", "verb"):
            with self.subTest(raising=name):
                real = getattr(dv, name)
                self.addCleanup(setattr, dv, name, real)
                setattr(dv, name, boom)
                try:
                    self._assert_identical(why=f"{name} raises")
                finally:
                    setattr(dv, name, real)

    def test_the_approve_payload_is_emitted_before_the_instrument_runs(self):
        """Ordering is the difference between an instrument and a wall: if
        the payload were written after the ledger, a slow or hanging write
        would delay — or lose — a decision the agent is waiting on."""
        seen = {}
        real = dv.record

        def spy(ws, command, decision, **kw):
            # setdefault, not assignment: the FIRST touch of the instrument
            # is the one that has to come after the payload. Overwriting
            # would let an early call hide behind a later one.
            seen.setdefault("stdout_at_call_time", sys.stdout.getvalue())
            return real(ws, command, decision, **kw)

        dv.record = spy
        self.addCleanup(setattr, dv, "record", real)
        ws = _ws()
        _governed(ws)
        out = _payload(cli, ws, {"command": "echo hi"})
        self.assertEqual(json.loads(out)["decision"], "approve")
        self.assertEqual(seen["stdout_at_call_time"], out)

    def test_every_payload_is_complete_before_the_instrument_is_touched(self):
        """The approve test above pins ONE call site. With a second recorder
        on the abstain path the property has to hold for the whole table: at
        the first touch of the instrument, stdout already holds the COMPLETE
        payload for that scenario — nothing is printed afterwards, so no
        write can delay, alter or lose a decision.

        For an abstain the complete payload is the EMPTY one, which is the
        case that would break if the recorder were hoisted above the branch
        that decides to abstain: it would then run for scenarios that go on
        to print a block."""
        touched = []
        real = dv.record

        def spy(ws, command, decision, **kw):
            touched.append(sys.stdout.getvalue())
            return real(ws, command, decision, **kw)

        for name, setup, tool_input, extra in SCENARIOS:
            with self.subTest(scenario=name):
                touched.clear()
                dv.record = spy
                try:
                    ws = _ws()
                    setup(ws)
                    e = dict(extra)
                    tool_name = e.pop("tool_name", "Bash")
                    ti = dict(tool_input)
                    if "file_path" in ti:
                        ti["file_path"] = os.path.join(ws, ti["file_path"])
                    out = _payload(cli, ws, ti, tool_name=tool_name, extra=e)
                finally:
                    dv.record = real
                for at_call_time in touched:
                    self.assertEqual(at_call_time, out)

    def test_the_release_path_really_reaches_the_instrument(self):
        """Guards the test above from passing vacuously: a call site that is
        never reached satisfies "the payload came first" trivially."""
        seen = []
        real = dv.record
        dv.record = lambda *a, **k: seen.append(a[1]) or real(*a, **k)
        self.addCleanup(setattr, dv, "record", real)
        ws = _ws()
        _governed(ws)
        self.assertEqual(_payload(cli, ws,
                                  {"command": "python3 tp.py status"}), "")
        self.assertEqual(seen, ["python3 tp.py status"])

    def test_the_writer_never_waits_on_a_lock(self):
        """`obligations._append` takes a 10s file_lock; on a blocked
        `.taskplane` that stalls the hook for the full timeout, which IS an
        instrument changing behaviour. This writer is an unlocked O_APPEND
        of one small line and must fail immediately."""
        ws = _ws()
        os.makedirs(dv.ledger_path(ws))
        t0 = time.time()
        dv.record(ws, "python3 tp.py graph impact", "approve")
        self.assertLess(time.time() - t0, 2.0)
        with open(os.path.join(ROOT, "taskplane", "derivation.py"),
                  encoding="utf-8") as f:
            tree = ast.parse(f.read())
        called = {n.func.attr if isinstance(n.func, ast.Attribute)
                  else getattr(n.func, "id", "")
                  for n in ast.walk(tree) if isinstance(n, ast.Call)}
        self.assertNotIn("file_lock", called,
                         "the ledger writer must not take the 10s file_lock")


class TestTheWriterNeverRaises(unittest.TestCase):
    """The module's own guard, tested directly — the call-site guard in
    tp.py would otherwise mask its removal."""

    def test_record_survives_a_directory_in_the_ledgers_place(self):
        ws = _ws()
        os.makedirs(dv.ledger_path(ws))
        self.assertEqual(dv.record(ws, "python3 tp.py graph impact",
                                   "approve"), [])

    def test_the_writer_reports_failure_instead_of_raising(self):
        """Pins the WRITER's own guard. `record` catches too, so without
        this the writer's try/except could be deleted with the suite still
        green — and the next caller of `_append` would inherit a raise."""
        ws = _ws()
        os.makedirs(dv.ledger_path(ws))
        self.assertIs(dv._append(ws, [{"event": "command", "verb": "tp dod"}]),
                      False)

    def test_record_swallows_an_exploding_internal(self):
        """Pins `record`'s guard — the FIRST of the two layers. tp.py's
        call-site guard would otherwise mask its removal, and this module is
        also called from the recorder, outside the hook."""
        def boom(*a, **k):
            raise RuntimeError("classifier exploded")
        real = dv.input_key
        dv.input_key = boom
        self.addCleanup(setattr, dv, "input_key", real)
        self.assertEqual(dv.record(_ws(), "python3 tp.py graph impact",
                                   "approve"), [])

    def test_record_survives_an_unwritable_runtime_dir(self):
        ws = _ws()
        d = tpl.tp_dir(ws)
        os.makedirs(d, exist_ok=True)
        os.chmod(d, 0o500)
        self.addCleanup(os.chmod, d, 0o700)
        dv.record(ws, "echo hi", "approve")     # must not raise

    def test_read_survives_a_torn_ledger(self):
        ws = _ws()
        dv.record(ws, "python3 tp.py graph impact", "approve")
        with open(dv.ledger_path(ws), "a", encoding="utf-8") as f:
            f.write("{not json\n")
        self.assertEqual(len(dv.read(ws)), 2)
        self.assertEqual(dv.repeats(ws), 0)

    def test_the_runtime_dir_still_ignores_itself(self):
        """A first write that CREATES .taskplane must not make the ledger
        committable by `git add -A`."""
        ws = _ws()
        dv.record(ws, "python3 tp.py graph impact", "approve")
        with open(os.path.join(tpl.tp_dir(ws), ".gitignore"),
                  encoding="utf-8") as f:
            self.assertIn("*", f.read().splitlines())


if __name__ == "__main__":
    unittest.main()
