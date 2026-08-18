import copy

import lens
import lens_signals
import review_progression as progression


FLOORS = {"architecture", "code-quality", "security", "qa"}


def _review_route(files, content=None):
    return lens.route(
        files,
        stage="review",
        use_signals=True,
        workspace=".",
        content_by_file=content or {},
    )


def test_review_floors_are_always_deep_for_code_docs_and_empty_mapping():
    fixtures = [
        (["src/widget.py"], {"src/widget.py": "def widget(): pass"}),
        (["docs/api.md"], {"docs/api.md": "# API\nAuthentication contract"}),
        (["src/widget.py", "docs/runbook.md"], {}),
    ]
    for files, content in fixtures:
        routed = _review_route(files, content)
        by_id = {row["id"]: row for row in routed["lenses"]}
        assert {lid for lid in FLOORS if by_id[lid]["tier"] == "deep"} == FLOORS
        assert all(by_id[lid]["floor"].startswith("mandatory review floor:") for lid in FLOORS)


def test_initial_wave_has_deep_slots_and_at_most_one_bounded_sweep():
    routed = _review_route(
        ["docs/api.md"], {"docs/api.md": "API token security migration guide"}
    )
    plan = progression.initial_wave(routed, sweep_limit=8)
    assert FLOORS <= {slot["lens"] for slot in plan["deep"]}
    assert plan["sweep"] is None or plan["sweep"]["slot"] == "lens-sweep"
    assert len(plan["sweep"]["lenses"] if plan["sweep"] else []) <= 8
    assert len({slot["slot"] for slot in plan["deep"]}) == len(plan["deep"])


def test_production_dispatch_consumes_the_bounded_progressive_wave():
    routed = _review_route(
        ["docs/api.md"], {"docs/api.md": "API token security migration guide"}
    )
    dispatch = lens.dispatch_briefs(routed)
    expected = routed["context"]["review_progression"]["sweep_lenses"]
    assert len(dispatch["deep"]) >= 4
    assert dispatch["sweep"] is None or dispatch["sweep"]["ids"] == expected
    assert len(expected) <= progression.DEFAULT_SWEEP_LIMIT


def test_high_major_promotions_are_attributable_idempotent_and_charter_bound():
    concerns = [
        {
            "id": "c-1",
            "severity": "high",
            "lens": "security",
            "evidence_ref": "diff:auth.py:12",
            "rationale": "authorization check may be bypassed",
            "trigger": "authz",
        },
        copy.deepcopy({
            "id": "c-1",
            "severity": "high",
            "lens": "security",
            "evidence_ref": "diff:auth.py:12",
            "rationale": "authorization check may be bypassed",
            "trigger": "authz",
        }),
        {
            "id": "c-2",
            "severity": "major",
            "lens": "security",
            "evidence_ref": "diff:query.py:4",
            "rationale": "query lacks an index",
            "trigger": "database indexing",
        },
        {"id": "c-3", "severity": "high", "lens": "qa", "rationale": "missing test", "trigger": "coverage"},
        {"id": "c-4", "severity": "low", "lens": "qa", "evidence_ref": "diff:test.py:1", "rationale": "minor", "trigger": "test"},
    ]
    result = progression.resolve_sweep_concerns(concerns)
    assert [row["lens"] for row in result["promotions"]] == ["security"]
    assert result["promotions"][0]["slot"] == "lens-security"
    rejected = {row["concern_id"]: row["reason"] for row in result["rejections"]}
    assert rejected["c-1"] == "duplicate"
    assert rejected["c-2"] == "out-of-charter"
    assert rejected["c-3"] == "missing-evidence"
    assert rejected["c-4"] == "below-promotion-threshold"


def test_promotion_replay_against_prior_fingerprints_is_idempotent():
    concern = {
        "id": "risk-1",
        "severity": "HIGH",
        "lens": "qa",
        "evidence_ref": "diff:test.py:8",
        "rationale": "regression path lacks a test",
        "trigger": "coverage",
    }
    first = progression.resolve_sweep_concerns([concern])
    fingerprint = first["promotions"][0]["fingerprint"]
    replay = progression.resolve_sweep_concerns(
        [concern], already_promoted=[fingerprint]
    )
    assert replay["promotions"] == []
    assert replay["rejections"][0]["reason"] == "duplicate"


def test_distinct_concerns_for_one_lens_create_one_deep_slot():
    base = {
        "severity": "major",
        "lens": "sre",
        "trigger": "recovery",
    }
    result = progression.resolve_sweep_concerns([
        {**base, "id": "one", "evidence_ref": "diff:a.py:1", "rationale": "recovery can stall"},
        {**base, "id": "two", "evidence_ref": "diff:b.py:2", "rationale": "recovery can loop"},
    ])
    assert [row["slot"] for row in result["promotions"]] == ["lens-sre"]
    assert result["rejections"][0]["reason"] == "already-covered"


def test_document_evidence_widens_only_the_smallest_relevant_set():
    cases = [
        ("docs/api.md", "OAuth API endpoint contract", {"integrability", "security", "tech-writer"}),
        ("docs/runbook.md", "Rollback alert incident recovery", {"sre", "tech-writer"}),
        ("CHANGELOG.md", "Fixed a typo", {"tech-writer"}),
    ]
    for path, text, expected in cases:
        signals = progression.document_lens_signals([path], {path: text})
        assert expected <= set(signals)
        assert set(signals) < progression.catalog_lens_ids()
    uncertain = progression.document_lens_signals(["docs/guide.md"], {"docs/guide.md": "\x00"})
    assert set(uncertain) == {"tech-writer"}
