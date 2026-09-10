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

















def test_canonical_review_kernel_decision_allocates_only_bounded_sweep():
    files = [
        "src/auth.py", "db/schema.sql", "ops/runbook.md", "ui/page.tsx",
        "mobile/app.swift", "docs/privacy.md", "infra/deploy.yaml",
    ]
    routed = _review_route(files)
    decision = review._routing_decision(routed, lens.load_catalog())
    selected = sorted(
        lens_id for lens_id, row in decision.items()
        if row["verdict"] == "sweep"
    )
    bounded = sorted(
        routed["context"]["review_progression"]["sweep_lenses"]
    )
    assert selected == bounded
    assert 4 <= len(selected) <= 5
    deferred = routed["context"]["review_progression"]["deferred_light"]
    for lens_id in deferred:
        assert decision[lens_id]["verdict"] == "n/a"
        assert decision[lens_id]["negative_evidence"][0].startswith(
            "deferred by bounded progressive review sweep"
        )








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


def test_missing_mapping_alone_stays_single_deep_and_only_evidenced_ambiguity_widens():
    missing_mapping = _review_route(["docs/unmapped.md"], {})
    assert missing_mapping["context"]["review_risk"] == {
        "class": "documentation-only",
        "reason": "documentation evidence selected tech-writer",
        "required_deep_lenses": ["tech-writer"],
    }

    uncertain_documents = {
        "corrupt": "\x00broken document payload",
        "ambiguous": "TBD: impact is ambiguous and ownership is unknown.",
    }
    for name, content in uncertain_documents.items():
        routed = _review_route([f"docs/{name}.md"], {f"docs/{name}.md": content})
        risk = routed["context"]["review_risk"]
        assert risk["class"] == "substantive-risky", name
        assert set(risk["required_deep_lenses"]) == FLOORS, name
        assert name in risk["reason"], name
        assert "document evidence" in risk["reason"], name
