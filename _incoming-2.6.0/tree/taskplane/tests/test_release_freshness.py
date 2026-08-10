"""Release-freshness gate (month-retro routine, v2.5.1).

The tp-help incident: the feature tour silently fell TWO releases behind
because no mechanical check tied skills/ to the release process. These
tests make freshness part of DoD, not memory:

  1. tp-help must reference the CURRENT major.minor — a version bump fails
     this test until the tour is touched (deliberate friction: the moment
     you bump, you must at least re-read the tour).
  2. Every user-facing long flag on tp.py's argparse surfaces must be
     mentioned somewhere in README/docs/skills — a new flag without docs
     fails CI.
  3. Every docs/*.md path referenced by any SKILL.md must exist — moved
     docs can't leave dead pointers in skills.

Phase 3 WS-D (R-0010) extends the gate:

  4. D4 — tp-go (the delivery driver) must ALSO name the current
     major.minor, same rule as the tp-help tour.
  5. D1 — per-skill required-mentions table: each governed skill must name
     the v2.5 surfaces its behavior actually uses; plus a stale-phrase
     denylist and a lens-count pin (any digit-quantified catalog claim in
     the governed skills must say 26).
  6. D2 — facade/driver routing determinism: tp-go's frontmatter describes
     the internal delivery driver behind the taskplane facade and does NOT
     carry the facade's 'implement' trigger; the facade keeps it.
  7. D3 — the submit/gate/human-checkpoint invariants are single-sourced in
     skills/taskplane/references/harness-rules.md: keyword coverage proves
     no invariant was dropped in extraction, both skills point at it, and a
     restatement detector keeps the canonical sentences from re-forking
     into skills.
  8. D3 durability — every `references/*.md` a SKILL.md points at must
     RESOLVE on disk AND be a member of the Codex package's file set. The
     canonical harness statement first landed at repo-root references/,
     which scripts/package_openai.py does not ship: on that distribution
     both skills pointed at a file that was not there, right after they
     stopped restating the invariants inline. Relocating the file fixed
     that instance; this check is what keeps it fixed, by importing the
     packager and asserting membership rather than trusting convention.
  9. D5 — the flag-mention corpus is no longer hand-written. `tp help --md`
     walks tp.py's LIVE argparse tree into docs/cli-reference.md, so every
     flag is documented BY CONSTRUCTION and _LEGACY_UNDOCUMENTED is empty.
     The generator refuses to emit when a subcommand or long flag carries
     no help text, which makes the new-flag ratchet STRICTER than the
     exemption list it replaces: a new flag without help prose cannot be
     generated at all, and a new flag whose reference was not regenerated
     fails the committed-reference drift check (and the matching CI leg).
"""
import argparse
import contextlib
import glob
import io
import json
import os
import re
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _current_minor():
    v = json.load(open(os.path.join(ROOT, ".codex-plugin", "plugin.json")))["version"]
    return ".".join(v.split(".")[:2])


class TestTpHelpFreshness(unittest.TestCase):
    def test_tp_help_names_the_current_minor_version(self):
        minor = _current_minor()
        text = _read("skills/tp-help/SKILL.md")
        self.assertIn(f"v{minor}", text,
                      f"skills/tp-help/SKILL.md does not mention v{minor} — "
                      "the tour is stale for this release; update it (this "
                      "failing test IS the release routine, not an accident)")


class TestTpGoFreshness(unittest.TestCase):
    """D4 (R-0010): tp-go drives the whole delivery loop — it falls stale
    exactly like the tour did, so it gets the same mechanical pin."""

    def test_tp_go_names_the_current_minor_version(self):
        minor = _current_minor()
        text = _read("skills/tp-go/SKILL.md")
        self.assertIn(f"v{minor}", text,
                      f"skills/tp-go/SKILL.md does not mention v{minor} — "
                      "the delivery driver is stale for this release; "
                      "truth it up (this failing test IS the release "
                      "routine, not an accident)")


class TestSkillsTruthedToCurrentReality(unittest.TestCase):
    """D1 (R-0010): each governed skill must name the shipped v2.5 surfaces
    its behavior actually uses. Every string below is verified against
    tp.py's argparse / the engine modules — none is aspirational."""

    REQUIRED = {
        "skills/tp-go/SKILL.md": (
            "--emit",                  # stage/review dispatch surface
            "loop wave", "loop next",  # the emitting subcommands
            "workflow_available",      # the single host detector
            "TASKPLANE_WORKFLOWS",     # kill-switch / opt-in
            'stage="build"',           # evaluate routes at the build stage
            "TASKPLANE_AUDIT_EVERY",   # audit cadence at the em step
            "router regression",       # audit auto-filing
            "docs/routing-and-flows.md",
            # cross-skill pointer: the canonical statement lives in the
            # taskplane skill's own reference dir so it ships with skills/
            "../taskplane/references/harness-rules.md",
        ),
        "skills/tp-status/SKILL.md": (
            # the component SURFACES (review-graph count, coverage-map
            # attribution, `tp graph html`) — explicitly NOT the Graph tab,
            # whose _graph_panel never reads g["components"]
            "component",
            "coverage",                # coverage map v2
            "deep", "light", "n/a", "evidence",
        ),
        "skills/tp-product/SKILL.md": (
            "--depends", "--contract",  # requirement surfaces (unchanged)
            "req debt", "follow-up", "resolved",  # debt burn-down flow
        ),
        "skills/tp-tag/SKILL.md": (
            ".taskplane-kb/", "knowledge/", "state", "artifacts",
            "meta.json",               # the current store layout
            "../taskplane/references/harness-rules.md",
        ),
        "skills/tp-northstar/SKILL.md": (
            "26",                      # the current catalog count
        ),
        "skills/taskplane/SKILL.md": (
            "references/harness-rules.md",
        ),
    }

    def test_each_skill_mentions_its_current_surfaces(self):
        for rel, needles in self.REQUIRED.items():
            # markdown hard-wraps multiword phrases; normalize whitespace
            text = " ".join(_read(rel).split())
            missing = [n for n in needles if n not in text]
            self.assertEqual(missing, [],
                             f"{rel} is stale for v2.5 — missing required "
                             f"mention(s): {missing}")

    # stale-phrase denylist: instructions that would weaken the harness may
    # never (re)appear in the governed skills — same family as the docs leg
    STALE = ("disable the hook", "disable hooks", "skip the gate",
             "bypass the contract", "turn off the screen")

    def test_no_governed_skill_instructs_weakening(self):
        for rel in self.REQUIRED:
            low = _read(rel).lower()
            for phrase in self.STALE:
                self.assertNotIn(phrase, low,
                                 f"{rel} instructs weakening: {phrase!r}")

    def test_any_quantified_catalog_claim_says_26(self):
        # count-rot pin: a governed skill quantifying the lens catalog with
        # a digit must say 26 (the generated catalog's actual size)
        for rel in self.REQUIRED:
            for num in re.findall(r"\b(\d+)(?:-|\s+)(?:review\s+)?lens",
                                  _read(rel)):
                self.assertEqual(num, "26",
                                 f"{rel} claims a {num}-lens catalog; the "
                                 "shipped catalog has 26")


def _description_line(rel):
    for ln in _read(rel).splitlines():
        if ln.startswith("description:"):
            return ln
    raise AssertionError(f"{rel}: no frontmatter description line")


class TestFacadeDriverRoutingDeterminism(unittest.TestCase):
    """D2 (R-0010): 'implement X' must route to the taskplane facade
    deterministically — the trigger lives ONLY there; tp-go describes the
    internal driver and keeps only the explicit loop-driving phrases."""

    def test_tp_go_described_as_the_internal_driver(self):
        desc = _description_line("skills/tp-go/SKILL.md")
        low = desc.lower()
        self.assertIn("internal delivery driver", low)
        self.assertIn("facade", low)
        self.assertNotIn("implement", low,
                         "tp-go's description must not carry the facade's "
                         "'implement' trigger — that phrasing routes to the "
                         "taskplane facade")

    def test_facade_keeps_the_implement_and_build_triggers(self):
        low = _description_line("skills/taskplane/SKILL.md").lower()
        self.assertIn("implement", low)
        self.assertIn("build", low)

    def test_tp_go_keeps_the_explicit_loop_driving_triggers(self):
        desc = _description_line("skills/tp-go/SKILL.md")
        for trigger in ("start governed work", "run the loop",
                        "run tasks in parallel", "dispatch the wave",
                        "run the retro", "log tech debt"):
            self.assertIn(trigger, desc,
                          f"tp-go dropped the real loop-driving trigger "
                          f"{trigger!r}")

    def test_no_skill_description_carries_xml(self):
        # CI's plugin-validation rule, pinned as a test too
        for p in glob.glob(os.path.join(ROOT, "skills", "*", "SKILL.md")):
            desc = _description_line(os.path.relpath(p, ROOT))
            self.assertNotIn("<", desc, f"{p}: XML in description")
            self.assertNotIn(">", desc, f"{p}: XML in description")


class TestHarnessRulesSingleSourced(unittest.TestCase):
    """D3 (R-0010): ONE canonical statement of the submit/gate/human-
    checkpoint invariants in CANONICAL below; the skills carry one-line
    summaries plus a pointer, never a second full statement."""

    # The canonical file lives inside the taskplane SKILL's own reference
    # dir (the repo convention: tp-go/references/parallel.md, tp-engineering/
    # references/security.md) because scripts/package_openai.py ships
    # skills/ wholesale and nothing from a repo-root references/.
    CANONICAL = "skills/taskplane/references/harness-rules.md"

    # keyword coverage: extraction may not drop an invariant
    KEYWORDS = (
        "submit", "stop",                  # workers submit and stop
        "orchestrator", "loop gate",       # only the orchestrator gates
        "engine", "evidence",              # the engine judges evidence
        "fingerprint",
        "human", "self-approv",            # human gates never self-approved
        "sign-off", "plan approval",
        "contract", "clear",               # contracts never self-cleared
        "weaken", "scope",
    )

    def test_reference_covers_every_invariant_keyword(self):
        low = _read(self.CANONICAL).lower()
        missing = [k for k in self.KEYWORDS if k not in low]
        self.assertEqual(missing, [],
                         f"{self.CANONICAL} dropped invariant "
                         f"keyword(s) in extraction: {missing}")

    # the exact pointer each skill must carry, relative to its OWN dir —
    # so a re-relocation cannot leave a skill pointing somewhere else
    POINTERS = {
        "skills/taskplane/SKILL.md": "references/harness-rules.md",
        "skills/tp-go/SKILL.md": "../taskplane/references/harness-rules.md",
        "skills/tp-tag/SKILL.md": "../taskplane/references/harness-rules.md",
    }

    def test_both_skills_point_at_the_single_source(self):
        for rel, pointer in self.POINTERS.items():
            self.assertIn(pointer, _read(rel),
                          f"{rel} must point at the canonical harness rules "
                          f"as `{pointer}`")

    # restatement detector: these canonical sentences live ONLY in the
    # reference; reappearing in either skill means the statement re-forked
    RESTATEMENTS = (
        "recomputes the submission fingerprint",
        "missing, mismatched, or stale submission",
        "bind the exact verdict/findings/report bytes",
    )

    def test_canonical_sentences_do_not_refork_into_the_skills(self):
        # whitespace-normalized both ways: a hard-wrapped canonical sentence
        # still counts, and a hard-wrapped re-fork still gets caught
        ref = " ".join(_read(self.CANONICAL).split())
        for phrase in self.RESTATEMENTS:
            self.assertIn(phrase, ref,
                          f"canonical sentence missing from the reference: "
                          f"{phrase!r}")
        for rel in ("skills/taskplane/SKILL.md", "skills/tp-go/SKILL.md"):
            body = " ".join(_read(rel).split())
            for phrase in self.RESTATEMENTS:
                self.assertNotIn(phrase, body,
                                 f"{rel} re-forked the canonical harness "
                                 f"statement: {phrase!r} — summarize in one "
                                 f"line and point at {self.CANONICAL} "
                                 "instead")


class TestUserSurfaceDocumented(unittest.TestCase):
    # user-facing surfaces whose flags must be documented; internal/dev
    # plumbing flags can be listed in _EXEMPT with a reason
    _EXEMPT = {
        "--workspace",      # universal plumbing, documented via CLI --help
        "--json",           # output-format toggle on many commands
        "--note", "--task", "--req", "--by",  # loop protocol plumbing
        "--force",
    }

    # RATCHET, BURNED DOWN (D5 / R-0010, debt D-0005). This list once held
    # 34 flags that predated the gate with zero doc mentions. It is now
    # EMPTY and asserted empty below: docs/cli-reference.md is generated
    # from tp.py's own argparse tree by `tp help --md`, so a flag is
    # documented by construction and an exemption is never the answer
    # again. If a flag turns up missing, fix the GENERATOR (or write the
    # flag's help text) and regenerate — do not re-open this list.
    _LEGACY_UNDOCUMENTED = set()

    def test_the_legacy_exemption_list_is_empty(self):
        self.assertEqual(
            self._LEGACY_UNDOCUMENTED, set(),
            "the legacy flag exemption list was re-opened — D5 replaced it "
            "with a GENERATED reference (`tp help --md > "
            "docs/cli-reference.md`); document the flag by writing its "
            "argparse help text and regenerating, never by exempting it")

    def test_every_user_flag_is_mentioned_in_docs(self):
        src = _read("taskplane/tp.py")
        flags = set(re.findall(r'add_argument\(\s*"(--[a-z][a-z0-9-]+)"', src))
        flags -= self._EXEMPT | self._LEGACY_UNDOCUMENTED
        corpus = _read("README.md")
        for p in glob.glob(os.path.join(ROOT, "docs", "*.md")):
            corpus += open(p, encoding="utf-8").read()
        for p in glob.glob(os.path.join(ROOT, "skills", "*", "SKILL.md")):
            corpus += open(p, encoding="utf-8").read()
        missing = sorted(f for f in flags if f not in corpus)
        self.assertEqual(missing, [],
                         "user-facing flags with ZERO mention in README/docs/"
                         f"skills: {missing} — document or exempt with reason")


class TestGeneratedCliReference(unittest.TestCase):
    """D5 (R-0010): docs/cli-reference.md is GENERATED, never written.

    `tp help --md` walks tp.py's live argparse tree, so the reference
    cannot describe a flag the CLI does not have and cannot omit one it
    does. That is what let _LEGACY_UNDOCUMENTED (34 flags) go to empty:
    the freshness corpus above already globs docs/*.md, so every flag
    gains its mention mechanically.

    The generator is deliberately STRICTER than the list it replaces —
    it REFUSES (nonzero, reason on stderr) rather than emit a reference
    with a help-less subcommand or flag, or a degenerate one. A new flag
    therefore needs real help prose to be generated at all, and needs the
    reference regenerated to survive the drift check below (and the
    matching CI leg, which runs exactly this comparison).
    """

    REFERENCE = "docs/cli-reference.md"
    REGEN = "python3 taskplane/tp.py help --md > docs/cli-reference.md"
    TPPY = os.path.join(ROOT, "taskplane", "tp.py")

    def _generate(self):
        run = subprocess.run([sys.executable, self.TPPY, "help", "--md"],
                             capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(run.returncode, 0,
                         f"`tp help --md` refused: {run.stderr}")
        self.assertTrue(run.stdout.strip(),
                        "`tp help --md` printed nothing")
        return run.stdout

    def _cli(self):
        import tp as cli
        return cli

    # --- the generator itself ---------------------------------------
    def test_generation_is_deterministic(self):
        # no timestamps, no absolute paths, no set-iteration order: two
        # runs of the generator must be byte-identical, or the CI drift
        # leg would flap instead of catching drift
        first, second = self._generate(), self._generate()
        self.assertEqual(first, second,
                         "`tp help --md` is not deterministic — two runs "
                         "differ, so the CI drift leg cannot be trusted")
        self.assertNotIn(ROOT, first,
                         "the generated reference leaks an absolute path")

    def test_the_committed_reference_is_not_stale(self):
        # this IS the CI drift leg, as a test: regenerate and compare
        self.assertEqual(
            _read(self.REFERENCE), self._generate(),
            f"{self.REFERENCE} is stale — regenerate it with `{self.REGEN}` "
            "and commit the result (a flag changed without the reference "
            "being regenerated)")

    def test_ci_regenerates_and_diffs_the_reference(self):
        # the drift leg itself: CI must run the SAME regen command this
        # test uses and fail on any difference, or a stale reference could
        # merge (the test above only guards the pytest legs)
        ci = _read(".github/workflows/ci.yml")
        self.assertIn(self.REGEN, ci,
                      "CI does not regenerate the CLI reference — the "
                      f"drift leg must run `{self.REGEN}`")
        self.assertIn(f"git diff --exit-code -- {self.REFERENCE}", ci,
                      "CI regenerates the CLI reference but never diffs "
                      "it — a stale committed reference would pass")

    def test_the_reference_names_its_own_regen_command(self):
        # provenance banner, same convention as docs/lens-catalog.md
        text = _read(self.REFERENCE)
        head = text[:1200]
        self.assertIn("GENERATED", head,
                      f"{self.REFERENCE} has no provenance banner")
        self.assertIn(self.REGEN, head,
                      f"{self.REFERENCE} does not name its exact regen "
                      f"command (`{self.REGEN}`)")

    def test_every_long_flag_and_subcommand_is_in_the_reference(self):
        text = _read(self.REFERENCE)
        src = _read("taskplane/tp.py")
        flags = sorted(set(re.findall(
            r'add_argument\(\s*"(--[a-z][a-z0-9-]+)"', src)))
        self.assertTrue(flags, "no flags found in tp.py — regex rotted")
        missing = [f for f in flags if f"`{f}`" not in text]
        self.assertEqual(missing, [],
                         f"the generated reference omits flags: {missing} — "
                         "fix the GENERATOR, never the exemption list")
        for cmd in ("tp.py loop submit", "tp.py graph impact",
                    "tp.py req debt", "tp.py help"):
            self.assertIn(f"`{cmd}`", text,
                          f"the generated reference omits `{cmd}` — nested "
                          "subparsers are part of the walk")

    def test_the_reference_carries_the_help_text_not_just_the_flag(self):
        text = _read(self.REFERENCE)
        # a spot-check that the HELP column is real prose from argparse,
        # not an empty cell: these strings live only in tp.py's help=
        for phrase in ("cooperative $ ceiling",
                       "hook-enforced action ceiling",
                       "the worker's worktree"):
            self.assertIn(phrase, text,
                          f"the reference dropped argparse help text: "
                          f"{phrase!r}")

    # --- the refusals (the stricter ratchet) ------------------------
    def _parser_with(self, flag_help):
        p = argparse.ArgumentParser(prog="fake")
        sub = p.add_subparsers(dest="cmd", required=True)
        one = sub.add_parser("one", help="a documented subcommand")
        one.add_argument("--good", help="documented")
        one.add_argument("--bare", help=flag_help)
        return p

    def test_generator_refuses_a_flag_with_empty_help(self):
        cli = self._cli()
        for bad in (None, "", "   "):
            with self.assertRaises(cli.CliReferenceError) as ctx:
                cli.cli_reference_markdown(self._parser_with(bad))
            self.assertIn("--bare", str(ctx.exception),
                          "the refusal must NAME the offending flag")

    def test_generator_refuses_a_subcommand_with_empty_help(self):
        cli = self._cli()
        p = argparse.ArgumentParser(prog="fake")
        sub = p.add_subparsers(dest="cmd", required=True)
        sub.add_parser("mystery").add_argument("--x", help="documented")
        with self.assertRaises(cli.CliReferenceError) as ctx:
            cli.cli_reference_markdown(p)
        self.assertIn("mystery", str(ctx.exception))

    def test_generator_refuses_a_degenerate_reference(self):
        cli = self._cli()
        with self.assertRaises(cli.CliReferenceError) as ctx:
            cli.cli_reference_markdown(argparse.ArgumentParser(prog="fake"))
        self.assertIn("degenerate", str(ctx.exception).lower())

    def test_generator_accepts_a_fully_documented_parser(self):
        cli = self._cli()
        md = cli.cli_reference_markdown(self._parser_with("documented too"))
        self.assertIn("`--bare`", md)
        self.assertIn("documented too", md)

    def test_the_cli_refuses_nonzero_with_a_reason_on_stderr(self):
        # the CLI boundary, not just the pure function: exit code 1 and a
        # named reason, never a traceback
        cli = self._cli()
        ns = argparse.Namespace(md=True, cmd="help",
                                root_parser=self._parser_with(None))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = cli.cmd_help(ns)
        self.assertEqual(rc, 1, "a refused generation must exit nonzero")
        self.assertIn("--bare", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())


class TestSkillDocPointersExist(unittest.TestCase):
    def test_docs_paths_referenced_by_skills_exist(self):
        dead = []
        for p in glob.glob(os.path.join(ROOT, "skills", "*", "SKILL.md")):
            text = open(p, encoding="utf-8").read()
            for rel in set(re.findall(r"`(docs/[a-z0-9-]+\.md)`", text)):
                if not os.path.isfile(os.path.join(ROOT, rel)):
                    dead.append(f"{os.path.relpath(p, ROOT)} -> {rel}")
        self.assertEqual(dead, [], f"skills reference missing docs: {dead}")


class TestSkillReferencePointersArePackaged(unittest.TestCase):
    """D3 durability (R-0010): a skill that points at a `references/*.md`
    instead of restating its content has only moved the content — it has
    not shipped it. This pins BOTH halves: the pointer resolves on disk,
    and the target is a member of the Codex package's file set, computed by
    executing scripts/package_openai.py rather than by re-describing what
    it packages. The regression this replaces: the canonical harness rules
    were extracted to a repo-root references/, which the packager never
    walks (it ships assets/skills/hooks/agents/discipline/taskplane/lenses
    plus 7 named docs), so on Codex both skills pointed at nothing right
    after they stopped restating the invariants inline."""

    # `references/x.md` (own skill) or `../<skill>/references/x.md` (cross-
    # skill, the same form tp-go already uses for ../tp-product/SKILL.md)
    POINTER = re.compile(r"`((?:\.\./[a-z0-9-]+/)?references/[a-z0-9-]+\.md)`")

    def _pointers(self):
        """[(skill_name, skill_rel_md, pointer_as_written, repo_rel_target)]"""
        out = []
        for p in sorted(glob.glob(os.path.join(ROOT, "skills", "*",
                                               "SKILL.md"))):
            skill_dir = os.path.dirname(p)
            skill = os.path.basename(skill_dir)
            text = open(p, encoding="utf-8").read()
            for ptr in sorted(set(self.POINTER.findall(text))):
                target = os.path.normpath(os.path.join(skill_dir, ptr))
                out.append((skill, os.path.relpath(p, ROOT), ptr,
                            os.path.relpath(target, ROOT)))
        return out

    def _packager(self):
        import importlib.util
        path = os.path.join(ROOT, "scripts", "package_openai.py")
        self.assertTrue(os.path.isfile(path),
                        "scripts/package_openai.py is missing — this gate "
                        "cannot verify packagability without it")
        spec = importlib.util.spec_from_file_location(
            "_pkg_openai_for_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_reference_pointers_resolve_on_disk(self):
        dead = [f"{md} -> {ptr} (no {target})"
                for _skill, md, ptr, target in self._pointers()
                if not os.path.isfile(os.path.join(ROOT, target))]
        self.assertEqual(dead, [],
                         f"skills point at missing references: {dead}")

    def test_every_referenced_file_is_inside_the_codex_package(self):
        mod = self._packager()
        packaged = {os.path.relpath(str(f), str(mod.ROOT)).replace(os.sep, "/")
                    for f in mod.package_files(mod.load_manifest())}
        # a reference is only reachable on Codex if BOTH the pointing skill
        # and the target ship there; skills excluded from that distribution
        # take their pointers with them, so they cannot strand a reader
        excluded = set(getattr(mod, "OPENAI_EXCLUDED_SKILLS", ()))
        unshipped = [f"{md} -> {ptr} (target {target} not packaged)"
                     for skill, md, ptr, target in self._pointers()
                     if skill not in excluded
                     and target.replace(os.sep, "/") not in packaged]
        self.assertEqual(unshipped, [],
                         "SKILL.md points at a reference that the Codex "
                         "package does not ship, so that distribution gets a "
                         f"dead pointer: {unshipped} — put the file under "
                         "skills/<skill>/references/ (which package_openai.py "
                         "walks wholesale), do not add a new top-level dir")

    def test_the_canonical_harness_rules_are_packaged(self):
        # the specific file the D3 extraction depends on, named outright so
        # a failure reads as the invariant it protects, not as a path list
        mod = self._packager()
        packaged = {os.path.relpath(str(f), str(mod.ROOT)).replace(os.sep, "/")
                    for f in mod.package_files(mod.load_manifest())}
        self.assertIn(TestHarnessRulesSingleSourced.CANONICAL, packaged,
                      "the ONE canonical statement of the harness invariants "
                      "is not in the Codex package — the skills that stopped "
                      "restating it would ship pointing at nothing")


if __name__ == "__main__":
    unittest.main()
