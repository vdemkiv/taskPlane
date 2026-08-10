"""Review-discipline classification (v2.3.1): only regressions and new
high-in-diff defects block; pre-existing debt and taste are surfaced only."""
from taskplane import loop


def test_regression_always_blocks():
    f = {"class": "regression", "severity": "low", "file": "x.py"}
    assert loop.finding_blocks(f) is True


def test_explicit_observation_never_blocks_even_if_high():
    f = {"class": "observation", "severity": "high", "file": "x.py"}
    assert loop.finding_blocks(f) is False


def test_preexisting_high_does_not_block_the_change():
    f = {"class": "pre-existing", "severity": "high", "file": "x.py"}
    assert loop.finding_blocks(f) is False


def test_unclassified_high_in_diff_blocks():
    f = {"severity": "high", "file": "taskplane/loop.py"}
    assert loop.finding_blocks(f, changed_files=["taskplane/loop.py"]) is True


def test_unclassified_high_outside_diff_does_not_block():
    f = {"severity": "high", "file": "taskplane/other.py"}
    assert loop.finding_blocks(f, changed_files=["taskplane/loop.py"]) is False


def test_unclassified_high_cannot_be_hidden_by_omitting_class():
    # no class, no diff context → cannot prove it's old → blocks (fail closed)
    f = {"severity": "high", "file": "x.py"}
    assert loop.finding_blocks(f, changed_files=None) is True


def test_unclassified_med_never_blocks():
    f = {"severity": "med", "file": "x.py"}
    assert loop.finding_blocks(f, changed_files=None) is False


def test_normalize_class_unknown_is_unclassified_not_regression():
    assert loop.normalize_finding_class("weird") == "unclassified"
    assert loop.normalize_finding_class("taste") == "observation"
    assert loop.normalize_finding_class("debt") == "pre-existing"


def test_classify_findings_splits_the_v230_shape():
    # 1 regression, 1 pre-existing high, 2 observations → 1 blocker only
    findings = [
        {"class": "regression", "severity": "high", "file": "a"},
        {"class": "pre-existing", "severity": "high", "file": "b"},
        {"class": "observation", "severity": "med", "file": "c"},
        {"class": "observation", "severity": "low", "file": "d"},
    ]
    out = loop.classify_findings(findings)
    assert len(out["blockers"]) == 1
    assert len(out["regressions"]) == 1
    assert len(out["pre_existing"]) == 1
    assert len(out["observations"]) == 2
