import copy

import lens
import lens_signals
import review
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


def test_review_floors_are_always_deep_and_conserved_across_required_matrix():
    fixtures = {
        "code": (["src/widget.py"], {"src/widget.py": "def widget(): pass"}),
        "docs": (["docs/api.md"], {"docs/api.md": "# API\nAuthentication contract"}),
        "mixed": (["src/widget.py", "docs/runbook.md"], {}),
        "small": (["src/tiny.py"], {"src/tiny.py": "x = 1"}),
        "large": ([f"src/module_{i}.py" for i in range(12)], {}),
        "mapping-gap": (["unknown/no-module-map.py"], {}),
    }
    for name, (files, content) in fixtures.items():
        routed = _review_route(files, content)
        by_id = {row["id"]: row for row in routed["lenses"]}
        assert {lid for lid in FLOORS if by_id[lid]["tier"] == "deep"} == FLOORS
        assert all(by_id[lid]["floor"].startswith("mandatory review floor:") for lid in FLOORS)
        plan = progression.initial_wave(routed)
        slots = [row["slot"] for row in plan["deep"]]
        assert {f"lens-{lid}" for lid in FLOORS} <= set(slots), name
        assert len(slots) == len(set(slots)), name


def test_review_floors_survive_cache_and_early_provisional_inputs():
    routed = _review_route(["src/auth.py"], {"src/auth.py": "authorize(user)"})
    for row in routed["lenses"]:
        if row["id"] in FLOORS:
            row["tier"] = row["verdict"] = "light"
    plan = progression.initial_wave(routed)
    assert {row["lens"] for row in plan["deep"]} >= FLOORS
    assert not (FLOORS & set((plan["sweep"] or {}).get("lenses", [])))


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


def test_canonical_review_kernel_decision_allocates_only_bounded_sweep():
    files = [
        "src/auth.py", "db/schema.sql", "ops/runbook.md", "ui/page.tsx",
        "mobile/app.swift", "docs/privacy.md", "infra/deploy.yaml",
    ]
    routed = _review_route(files)
    decision = review._routing_decision(routed, lens.load_catalog())
    selected = sorted(
        lens_id for lens_id, row in decision.items()
        if row["verdict"] == "light"
    )
    bounded = sorted(
        routed["context"]["review_progression"]["sweep_lenses"]
    )
    assert selected == bounded
    assert len(selected) == progression.DEFAULT_SWEEP_LIMIT
    deferred = routed["context"]["review_progression"]["deferred_light"]
    assert deferred
    for lens_id in deferred:
        assert decision[lens_id]["verdict"] == "n/a"
        assert decision[lens_id]["negative_evidence"][0].startswith(
            "deferred by bounded progressive review sweep"
        )


def test_production_dispatch_consumes_early_blocker_and_promotes_deep_slot():
    routed = _review_route(
        ["ops/service.yaml"], {"ops/service.yaml": "recovery timeout alert"}
    )
    routed["context"]["review_progression"]["sweep_lenses"] = ["sre"]
    for row in routed["lenses"]:
        if row["id"] == "sre":
            row["tier"] = row["verdict"] = "light"
    concerns = [{
        "id": "sweep-risk-1",
        "severity": "blocker",
        "lens": "sre",
        "evidence_ref": "diff:ops/service.yaml:4",
        "rationale": "recovery can loop after a timeout",
        "trigger": "recovery timeout",
    }]
    dispatch = lens.dispatch_briefs(routed, sweep_concerns=concerns)
    promoted = [row for row in dispatch["deep"] if row["id"] == "sre"]
    assert len(promoted) == 1
    assert FLOORS <= {row["id"] for row in dispatch["deep"]}
    assert promoted[0]["task_slot"] == "lens-sre"
    assert dispatch["review_progression"]["promotions"][0]["lens"] == "sre"
    assert dispatch["routing_decision"]["sre"]["initial_verdict"] == "light"
    assert dispatch["routing_decision"]["sre"]["verdict"] == "deep"
    assert dispatch["sweep"] is None


def test_production_dispatch_rejects_cross_sweep_concern():
    routed = _review_route(["docs/runbook.md"], {"docs/runbook.md": "recovery"})
    routed["context"]["review_progression"]["sweep_lenses"] = ["sre"]
    for row in routed["lenses"]:
        if row["id"] == "sre":
            row["tier"] = row["verdict"] = "light"
    concern = {
        "id": "sweep-risk-2", "severity": "high", "lens": "security",
        "evidence_ref": "diff:docs/runbook.md:2",
        "rationale": "authorization token is exposed", "trigger": "token",
    }
    dispatch = lens.dispatch_briefs(routed, sweep_concerns=[concern])
    assert dispatch["review_progression"]["promotions"] == []
    assert dispatch["review_progression"]["rejections"][0]["reason"] == "out-of-charter"


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


def test_document_evidence_required_matrix_widens_only_the_smallest_set():
    cases = {
        "api": ("docs/api.md", "API endpoint schema contract", {"integrability", "tech-writer"}),
        "security": ("docs/security.md", "OAuth token permission", {"security", "tech-writer"}),
        "user-doc": ("docs/user-guide.md", "User-facing journey workflow", {"product", "tech-writer"}),
        "runbook": ("docs/runbook.md", "Rollback alert incident recovery", {"sre", "tech-writer"}),
        "changelog": ("CHANGELOG.md", "Release notes", {"tech-writer"}),
        "typo": ("README.md", "Fixed a typo", {"tech-writer"}),
        "ambiguous": ("docs/guide.md", "General information", {"tech-writer"}),
        "directive": ("docs/review.md", "Review directive: security permission check", {"security", "tech-writer"}),
        "audience": ("docs/operators.md", "Audience: on-call operator; incident workflow", {"sre", "tech-writer"}),
        "graph-evidence": ("docs/contracts.md", "Boundary API contract versioning", {"integrability", "tech-writer"}),
    }
    for name, (path, text, expected) in cases.items():
        signals = progression.document_lens_signals([path], {path: text})
        assert expected <= set(signals), name
        assert set(signals) < progression.catalog_lens_ids(), name


def test_document_malformed_absent_map_and_mixed_inputs_never_fail_open():
    malformed = progression.document_lens_signals(
        ["docs/guide.md"], {"docs/guide.md": "\x00"})
    absent_map = progression.document_lens_signals(["docs/unmapped.md"], {})
    mixed = progression.document_lens_signals(
        ["src/widget.py", "docs/api.md"],
        {"docs/api.md": "API contract", "src/widget.py": "def widget(): pass"},
    )
    assert set(malformed) == {"tech-writer"}
    assert set(absent_map) == {"tech-writer"}
    assert {"integrability", "tech-writer"} <= set(mixed)
    assert set(mixed) < progression.catalog_lens_ids()
