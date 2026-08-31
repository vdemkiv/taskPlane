"""The scenario manifest: what a governed skill's flow must SHOW, as data.

WHAT A SCENARIO IS. `evals/scenarios/<skill>.json` declares, for one named
skill, the rubric rows a recorded run has to satisfy — each row a
human-readable CLAIM, the RECORD that decides it, and a CONSTRAINT over that
record. The scorer lane reads these and never learns a skill's name: every
constraint is evaluated generically, so adding a skill is adding a JSON file,
not adding a branch.

WHY THE FINGERPRINT IS A FLOW EXTRACT AND NOT A FILE HASH. A recorded run
goes stale when the skill it graded changes, and `inputs_fingerprint` is what
detects that. Hashing the skill's bytes detects a typo just as loudly as it
detects the deletion of `$TP graph impact` — and a gate that fires on every
typo gets waived by routine, which is the exact failure this layer exists to
prevent. So the fingerprint is taken over an EXTRACT: the taskplane surfaces
the file names, the flags it mandates, the polarity of each mention
(require / forbid), and the gate terms it uses. Prose is not in it.

Both directions are proved below, on the REAL skill files, mutated in a
temp copy:

  * TestProseDoesNotMoveTheFingerprint — an appended paragraph, a reworded
    sentence and a re-wrapped paragraph must leave it BYTE-IDENTICAL.
  * TestFlowChangesMoveTheFingerprint — deleting the `graph impact` mandate,
    deleting the `tp dod` gate, deleting the `tp ack` obligation mechanism,
    and FLIPPING "Do NOT pass `--all`" into "Pass `--all`" must each move it.

The flip is the one a set-of-tokens extract would miss, and it is why the
extract carries polarity per sentence rather than a bag of surfaces.

Every assertion here was observed FAILING before it was kept.
"""
import copy
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import derivation                                              # noqa: E402
import eval_scenario as es                                     # noqa: E402


def _load(skill):
    return es.load(os.path.join(es.scenario_dir(REPO), skill + ".json"))


def _mirror(root, source_files):
    """Copy the real source files into `root`, preserving relative paths."""
    for rel in source_files:
        dst = os.path.join(root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(os.path.join(REPO, rel.replace("/", os.sep)), dst)


def _patch(root, rel, fn):
    p = os.path.join(root, rel.replace("/", os.sep))
    with open(p, encoding="utf-8") as f:
        before = f.read()
    after = fn(before)
    with open(p, "w", encoding="utf-8") as f:
        f.write(after)
    return before, after


class _MutationCase(unittest.TestCase):
    """A temp mirror of tp-engineering's real source files, plus its baseline
    fingerprint. Mutations are applied to the COPY — this lane never edits
    `skills/` or `agents/`."""

    SKILL = "tp-engineering"
    MAIN = "skills/tp-engineering/SKILL.md"

    def setUp(self):
        self.scenario = _load(self.SKILL)
        self.sources = list(self.scenario["source_files"])
        self.root = tempfile.mkdtemp(prefix="tp-scenario-")
        self.addCleanup(shutil.rmtree, self.root, True)
        _mirror(self.root, self.sources)
        self.baseline = es.fingerprint(self.root, self.sources)

    def mutate(self, fn, rel=None):
        before, after = _patch(self.root, rel or self.MAIN, fn)
        self.assertNotEqual(before, after,
                            "the mutation changed nothing — the fixture text "
                            "it targets is gone, so the case proves nothing")
        return es.fingerprint(self.root, self.sources)


# --------------------------------------------------------------- direction A

class TestProseDoesNotMoveTheFingerprint(_MutationCase):
    """Re-wording, adding prose, and re-wrapping must leave it byte-identical.

    This is the half that keeps the staleness gate credible. A gate that
    fires on a typo is a gate people learn to waive.
    """

    def test_an_appended_prose_paragraph_leaves_the_fingerprint_identical(self):
        """New prose that mandates no surface, no flag and no gate term is
        not a flow change, however much of it there is."""
        got = self.mutate(lambda t: t + (
            "\n\nA reviewer reading this paragraph learns something about "
            "tone and nothing whatsoever about which commands the flow "
            "must run or which gates it must pass.\n"))
        self.assertEqual(self.baseline, got)

    def test_rewording_a_sentence_around_a_mandate_leaves_it_identical(self):
        """The sentence changes; the surface it mandates does not."""
        got = self.mutate(lambda t: t.replace(
            "Graph quality is assessed before routing.",
            "Routing begins only after graph quality is assessed."))
        self.assertEqual(self.baseline, got)

    def test_fixing_a_typo_leaves_it_identical(self):
        """The single most common edit a skill file gets, and the one a file
        hash would report as a stale corpus."""
        got = self.mutate(lambda t: t.replace("blast radius", "blast-radius"))
        self.assertEqual(self.baseline, got)

    def test_a_byte_hash_would_have_moved_where_this_did_not(self):
        """The contrast is the whole design claim. If hashing the bytes gave
        the same answer as the extract, the extract would be dead weight."""
        digest = lambda: hashlib.sha256(b"".join(  # noqa: E731
            open(os.path.join(self.root, r.replace("/", os.sep)), "rb").read()
            for r in sorted(self.sources))).hexdigest()
        before = digest()
        got = self.mutate(lambda t: t.replace("blast radius", "blast-radius"))
        self.assertEqual(self.baseline, got)
        self.assertNotEqual(before, digest())

    def test_rewrapping_a_paragraph_leaves_it_identical(self):
        """Line breaks are typography. A re-flowed paragraph — including one
        carrying inline surfaces AND a prohibition — is the same flow."""
        anchor = "**Every lens consumes a scoped view of the same context.**"

        def rewrap(text):
            i = text.index(anchor)
            j = text.index("\n\n", i)
            para = " ".join(text[i:j].split())
            return text[:i] + para + text[j:]

        self.assertIn(anchor, open(
            os.path.join(self.root, self.MAIN), encoding="utf-8").read())
        self.assertEqual(self.baseline, self.mutate(rewrap))


# --------------------------------------------------------------- direction B

class TestFlowChangesMoveTheFingerprint(_MutationCase):
    """Deleting a mandated surface, a gate, or a flag — or flipping a
    prohibition into a permission — must move it."""

    def test_deleting_the_graph_impact_mandate_moves_the_fingerprint(self):
        """`$TP graph impact` is the blast radius the review is not allowed
        to skip. A run recorded against a skill that no longer mandates it
        was graded against a different flow."""
        got = self.mutate(lambda t: re.sub(
            r"`[^`\n]*graph impact[^`\n]*`", "the blast radius", t))
        self.assertNotEqual(self.baseline, got)

    def test_deleting_the_dod_gate_moves_the_fingerprint(self):
        """`tp dod` is the DoD gate. Removing it removes a control point,
        and every recorded run that scored it is now unanchored."""
        got = self.mutate(lambda t: re.sub(
            r"`[^`\n]*\bdod\b[^`\n]*`", "done", t))
        self.assertNotEqual(self.baseline, got)

    def test_deleting_the_ack_obligation_mechanism_moves_the_fingerprint(self):
        """`tp ack` is how a rendered artifact obligation becomes durable.
        Dropping the surface turns a mechanism back into an instruction."""
        got = self.mutate(lambda t: re.sub(
            r"`tp ack[^`]*`", "the acknowledgement", t))
        self.assertNotEqual(self.baseline, got)

    def test_flipping_a_prohibition_into_a_permission_moves_it(self):
        """The mutation a bag-of-surfaces extract cannot see: the same token,
        the opposite mandate. `--all` forces every lens to run AND switches
        the applicability engine off — permitting it is a flow change."""
        got = self.mutate(lambda t: t.replace(
            "Do NOT pass `--all`.", "Pass `--all`."))
        self.assertNotEqual(self.baseline, got)


class TestTheFingerprintIsStableAndSelfDescribing(unittest.TestCase):

    def test_the_fingerprint_is_deterministic_across_calls(self):
        """No time, no path-order, no dict-iteration in the digest."""
        s = _load("tp-engineering")
        a = es.fingerprint(REPO, s["source_files"])
        b = es.fingerprint(REPO, list(reversed(s["source_files"])))
        self.assertEqual(a, b)
        self.assertRegex(a, r"^[0-9a-f]{64}$")

    def test_a_missing_source_file_is_recorded_not_raised(self):
        """A deleted skill file is the loudest flow change there is; it must
        move the fingerprint rather than take the instrument down."""
        root = tempfile.mkdtemp(prefix="tp-scenario-")
        self.addCleanup(shutil.rmtree, root, True)
        rel = "skills/tp-engineering/SKILL.md"
        _mirror(root, [rel])
        full = es.fingerprint(root, [rel])
        os.remove(os.path.join(root, rel.replace("/", os.sep)))
        self.assertNotEqual(full, es.fingerprint(root, [rel]))

    def test_the_extract_names_the_surfaces_and_never_the_prose(self):
        """The extract is the auditable part: it must be readable, and a
        sentence of prose must not appear in it."""
        text = ("Lead every review with impact — it is NOT optional: "
                "`$TP graph impact --files x`.\n\n"
                "Do NOT pass `--all`.\n")
        got = es.flow_extract(text)
        self.assertIn("require surface tp graph impact", got)
        self.assertIn("forbid flag --all", got)
        self.assertFalse([g for g in got if "optional" in g])


class TestBareSubcommandSpansCountAsMandates(unittest.TestCase):
    """`review start` and `lens dispatch` are written without the `$TP`
    prefix throughout the skills. A one-word span is NOT absorbed — `status`
    and `new` are ordinary English — so the rule is: a bare span counts only
    when its first token is a known verb AND its second is a known
    subcommand of that verb."""

    def test_a_bare_verb_plus_subcommand_span_is_a_surface(self):
        self.assertIn("require surface tp review start",
                      es.flow_extract("`review start` writes the context."))

    def test_a_bare_single_word_span_is_not_a_surface(self):
        got = es.flow_extract("The `status` of the work is unchanged.")
        self.assertFalse([g for g in got if g.startswith("require surface")])

    def test_an_invented_surface_is_not_recorded_as_a_verb(self):
        """derivation's UNKNOWN rule: a token that is not a known subcommand
        is never written down, so `tp ?` must not enter the extract — two
        different invented surfaces would collapse into one item and the
        deletion of either would be invisible."""
        got = es.flow_extract("Run `$TP invent thing` to make it up.")
        self.assertFalse([g for g in got if derivation.UNKNOWN in g])


# ------------------------------------------------------------ the manifests

class TestEveryEvaluatedSkillHasAScenario(unittest.TestCase):

    def test_all_governed_and_advisory_skills_carry_a_manifest(self):
        found = set(es.discover(REPO))
        self.assertEqual(set(es.EVALUATED_SKILLS), found)

    def test_every_scenario_on_disk_validates(self):
        for skill in es.EVALUATED_SKILLS:
            with self.subTest(skill=skill):
                s = _load(skill)
                self.assertEqual((), es.validate(s, root=REPO))

    def test_every_scenario_covers_every_universal_step(self):
        """A skill whose flow genuinely lacks a universal step says so, with
        a reason. Silence is the failure mode: an omitted step reads as a
        pass for a control point nobody checked."""
        for skill in es.EVALUATED_SKILLS:
            with self.subTest(skill=skill):
                s = _load(skill)
                tags = set()
                for step in s["steps"]:
                    tags.update(step.get("universal") or ())
                self.assertEqual(set(es.UNIVERSAL), set(es.UNIVERSAL) & tags)

    def test_a_step_declared_inapplicable_always_carries_a_reason(self):
        for skill in es.EVALUATED_SKILLS:
            s = _load(skill)
            for step in s["steps"]:
                if step.get("applicable") is False:
                    with self.subTest(skill=skill, step=step["id"]):
                        self.assertTrue((step.get("reason") or "").strip())


class TestTheReferenceScenarioIsTheWorkedExample(unittest.TestCase):
    """tp-engineering's eight steps are the user's own worked review. Each
    one names the record that decides it — the point of the whole layer is
    that a claim without a record behind it is not scoreable."""

    EIGHT = {
        "R1": "context",        # the target pin, head == the PR head
        "R2": "trace",          # graph_impact.scanned_head == reviewed head
        "R3": "context",        # findings.json written before any brief
        "R4": "trace",          # review_context_written precedes dispatch
        "R5": "trace",          # lens_route with evidence, breadth != all
        "R6": "dispatch",       # one findings file per dispatched brief
        "R7": "derivations",    # repeats() == 0, and every brief cites it
        "R8": "trace",          # contract / DoR before write, DoD before close
    }

    def setUp(self):
        self.s = _load("tp-engineering")
        self.steps = {x["id"]: x for x in self.s["steps"]}

    def test_the_eight_reference_steps_are_present(self):
        self.assertEqual(set(self.EIGHT), {i for i in self.steps
                                           if i.startswith("R")})

    def test_each_reference_step_names_the_record_that_decides_it(self):
        for sid, record in self.EIGHT.items():
            with self.subTest(step=sid):
                self.assertEqual(record, self.steps[sid]["record"])

    def test_every_reference_step_carries_a_human_readable_claim(self):
        for sid in self.EIGHT:
            with self.subTest(step=sid):
                self.assertGreater(len(self.steps[sid]["claim"].split()), 4)

    def test_the_shared_context_step_forbids_re_derivation(self):
        """R7 is the complaint this whole ledger was built for — the diff
        derived once and SHARED, not re-derived per lens."""
        of = self.steps["R7"]["of"]
        repeats = [c for c in of if c["check"] == "repeats"]
        self.assertEqual(1, len(repeats))
        self.assertEqual(0, repeats[0]["max"])
        self.assertEqual(["key", "input_key"], repeats[0]["distinct_by"])

    def test_the_routing_step_requires_the_selective_kernel(self):
        selective = [c for c in es.constraints(self.steps["R5"])
                     if c["check"] == "field_equals"
                     and c.get("field") == "routing_mode"
                     and c.get("value") == "selective"]
        self.assertEqual(1, len(selective))

    def test_the_routing_step_still_requires_a_recorded_decision(self):
        """Refusing `--all` is only half of it: a review that routed nothing
        at all would satisfy the prohibition trivially."""
        fields = {c.get("field") for c in es.constraints(self.steps["R5"])
                  if c["check"] == "field_equals"}
        self.assertEqual({"routing_mode", "routing_complete",
                          "dispositions_complete"}, fields)

    def test_the_declared_surfaces_are_real_taskplane_surfaces(self):
        for surface in self.s["declared_surfaces"]:
            with self.subTest(surface=surface):
                self.assertEqual(surface, derivation.verb(surface))
                self.assertNotIn(derivation.UNKNOWN, surface)

    def test_the_expected_derivations_are_ledger_keys(self):
        for key in self.s["expects_derivations"]:
            self.assertIn(key, derivation.KEYS)


# ------------------------------------------------------------- the guards

class TestTheLoaderRefusesAMalformedScenario(unittest.TestCase):
    """Every guard, fired. A validator nobody has seen reject anything is a
    validator that accepts everything."""

    def setUp(self):
        self.good = _load("tp-engineering")

    def bad(self, mutate, needle):
        s = copy.deepcopy(self.good)
        mutate(s)
        errors = es.validate(s, root=REPO)
        self.assertTrue(errors, f"expected an error mentioning {needle!r}")
        self.assertTrue([e for e in errors if needle in e],
                        f"{needle!r} not in {errors!r}")

    def test_a_wrong_schema_string_is_refused(self):
        self.bad(lambda s: s.update(schema="taskplane.eval-scenario/v2"),
                 "schema")

    def test_a_missing_required_key_is_refused(self):
        self.bad(lambda s: s.pop("declared_surfaces"), "declared_surfaces")

    def test_an_unknown_top_level_key_is_refused(self):
        """Strict, on purpose: a typo'd key is silently ignored by a lenient
        loader, and the constraint it was meant to carry never runs."""
        self.bad(lambda s: s.update(expects_derivation=["diff"]),
                 "expects_derivation")

    def test_a_skill_name_that_does_not_match_the_file_is_refused(self):
        self.bad(lambda s: s.update(skill="tp-product"), "skill")

    def test_a_source_file_that_does_not_exist_is_refused(self):
        self.bad(lambda s: s["source_files"].append("skills/ghost/SKILL.md"),
                 "skills/ghost/SKILL.md")

    def test_an_unknown_derivation_key_is_refused(self):
        self.bad(lambda s: s["expects_derivations"].append("vibes"), "vibes")

    def test_an_invented_declared_surface_is_refused(self):
        self.bad(lambda s: s["declared_surfaces"].append("tp invent thing"),
                 "tp invent thing")

    def test_a_declared_surface_the_skill_never_mandates_is_refused(self):
        """The manifest may not claim a surface its own source files do not
        name — that is how a scenario drifts into grading a flow the skill
        never had."""
        self.bad(lambda s: s["declared_surfaces"].append("tp share push"),
                 "tp share push")

    def test_an_unknown_record_name_is_refused(self):
        self.bad(lambda s: s["steps"][0].update(record="vibes"), "vibes")

    def test_an_unknown_check_name_is_refused(self):
        self.bad(lambda s: s["steps"][0].update(check="vibes"), "vibes")

    def test_an_unknown_anchor_name_is_refused(self):
        """`before: "first_write"` is only evaluable because every anchor is
        a named selector in ONE table. An unknown name would be scored as
        satisfied-by-absence."""
        s = copy.deepcopy(self.good)
        for step in s["steps"]:
            if step.get("before"):
                step["before"] = "the_vibes"
                break
        else:
            self.fail("no step carries a `before` anchor")
        self.assertTrue([e for e in es.validate(s, root=REPO)
                         if "the_vibes" in e])

    def test_an_unknown_selector_operator_is_refused(self):
        self.bad(lambda s: s["steps"][0].update(
            select={"event": {"vibes": ["x"]}}), "vibes")

    def test_a_duplicate_step_id_is_refused(self):
        self.bad(lambda s: s["steps"].append(dict(s["steps"][0])), "duplicate")

    def test_an_unknown_step_key_is_refused(self):
        self.bad(lambda s: s["steps"][0].update(befor="first_write"), "befor")

    def test_an_unknown_universal_tag_is_refused(self):
        self.bad(lambda s: s["steps"][0].update(universal=["vibes"]), "vibes")

    def test_a_missing_universal_tag_is_refused(self):
        """The coverage rule, enforced by the loader and not only by a test:
        a manifest that never mentions the DoD gate is incomplete, not
        lenient."""
        def drop(s):
            for step in s["steps"]:
                step.pop("universal", None)
        self.bad(drop, "dod")

    def test_an_inapplicable_step_without_a_reason_is_refused(self):
        self.bad(lambda s: s["steps"][0].update(applicable=False, reason=" "),
                 "reason")

    def test_a_step_with_no_claim_is_refused(self):
        self.bad(lambda s: s["steps"][0].update(claim=""), "claim")

    def test_an_all_check_with_no_constraints_is_refused(self):
        self.bad(lambda s: s["steps"][0].update(check="all", of=[]), "of")

    def test_a_stale_fingerprint_is_reported_by_name(self):
        s = copy.deepcopy(self.good)
        s["inputs_fingerprint"] = "0" * 64
        self.assertIn("stale", (es.stale(s, REPO) or "").lower())
        self.assertTrue([e for e in es.validate(s, root=REPO)
                         if "fingerprint" in e])


class TestTheSchemaStaysDeclarativeData(unittest.TestCase):
    """The scorer must evaluate every scenario generically. Anything that
    would need per-skill code is a defect in this schema."""

    def test_no_scenario_carries_an_executable_or_a_skill_name_hook(self):
        for skill in es.GOVERNED_SKILLS:
            blob = json.dumps(_load(skill))
            with self.subTest(skill=skill):
                for smell in ("lambda", "eval(", "import ", "__", "python"):
                    self.assertNotIn(smell, blob)

    def test_every_constraint_in_every_scenario_uses_the_vocabulary(self):
        for skill in es.GOVERNED_SKILLS:
            for step in _load(skill)["steps"]:
                for c in es.constraints(step):
                    with self.subTest(skill=skill, step=step["id"]):
                        self.assertIn(c["check"], es.CHECKS)
                        self.assertIn(c["record"], es.RECORDS)

    def test_every_anchor_resolves_to_a_record_and_a_selector(self):
        for name, anchor in es.ANCHORS.items():
            with self.subTest(anchor=name):
                self.assertIn(anchor["record"], es.RECORDS)
                self.assertIsInstance(anchor["select"], dict)

    def test_every_synthetic_event_states_where_it_comes_from(self):
        """Some events these scenarios select on do not exist in the engine's
        trace vocabulary yet — the recorder lane must synthesize them. Each
        one names its source here so the coordination is written down rather
        than assumed."""
        for event, source in es.SYNTHETIC_EVENTS.items():
            with self.subTest(event=event):
                self.assertTrue(source.strip())
                self.assertNotIn(event, es.ENGINE_EVENTS)

    def test_every_engine_event_a_scenario_selects_on_is_real_or_declared(self):
        """No scenario may select on an event that neither the engine emits
        nor the recorder is asked to synthesize — such a row scores
        `no evidence` forever and looks like a shy session."""
        known = set(es.ENGINE_EVENTS) | set(es.SYNTHETIC_EVENTS)
        for skill in es.GOVERNED_SKILLS:
            for event in es.selected_events(_load(skill)):
                with self.subTest(skill=skill, event=event):
                    self.assertIn(event, known)


class TestTheCorpusWalkerIsUnharmed(unittest.TestCase):
    """`evals/scenarios/` carries neither expected.json nor run.json, so
    Wave 1's discriminator must classify it as not-a-record and skip it. If
    it ever needs a marker, the marker goes in `ci_evals.MARKERS` — this test
    is what would say so."""

    def test_the_scenario_directory_is_skipped_not_scored(self):
        import ci_evals
        records, skipped = ci_evals._discover(os.path.join(REPO, "evals"))
        self.assertFalse([r for r in records if "scenarios" in r["name"]])
        mine = [s for s in skipped if s["name"].startswith("scenarios")]
        self.assertTrue(mine, "scenarios/ was neither scored nor named")
        self.assertFalse(any(m["is_record"] for m in mine))

    def test_the_scenario_files_are_loose_json_not_record_directories(self):
        for name, path in es.discover(REPO).items():
            with self.subTest(skill=name):
                self.assertTrue(os.path.isfile(path))
                self.assertTrue(path.endswith(".json"))


if __name__ == "__main__":
    unittest.main()
