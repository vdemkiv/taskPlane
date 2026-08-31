"""Focused M1-C evidence for reproducible, least-privilege CI tooling."""

from __future__ import annotations

from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
LOCK = ROOT / "requirements-dev.lock"
CONTRIBUTING = ROOT / "CONTRIBUTING.md"

READ_ONLY_PR_JOBS = (
    "ci-plan",
    "tests",
    "python-quality",
    "zero-token-corpus",
    "wave3-contracts",
    "tests-portability",
    "validate-plugin",
    "docs-truth",
    "codex-parity",
    "codex-host",
    "dashboard-browser",
)
TEST_JOBS = (
    "tests",
    "wave3-contracts",
    "tests-portability",
    "codex-parity",
    "codex-host",
    "release-tags",
    "dashboard-browser",
)
TEST_TREE = {
    "pytest": "9.1.1",
    "colorama": "0.4.6",
    "exceptiongroup": "1.3.0",
    "iniconfig": "2.3.0",
    "packaging": "26.3",
    "pluggy": "1.6.0",
    "pygments": "2.21.0",
    "tomli": "2.2.1",
    "typing-extensions": "4.16.0",
}


def _job(source: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [a-z][a-z0-9-]*:\n|\Z)",
        source,
    )
    return match.group(1) if match else ""


def _profile(source: str, name: str) -> str:
    prefix = f"# {name}: "
    return "\n".join(
        line.removeprefix(prefix)
        for line in source.splitlines()
        if line.startswith(prefix)
    )


def _pins(profile: str) -> tuple[dict[str, str], dict[str, list[str]], list[str]]:
    versions: dict[str, str] = {}
    hashes: dict[str, list[str]] = {}
    invalid: list[str] = []
    current = ""
    for raw in profile.splitlines():
        line = raw.strip()
        requirement = re.fullmatch(
            r'([A-Za-z0-9_-]+)==([A-Za-z0-9_.+-]+)'
            r'(?:\s*;\s*[^\\]+)?\s*\\?',
            line,
        )
        if requirement:
            current = requirement.group(1).lower()
            versions[current] = requirement.group(2)
            hashes[current] = []
        elif re.fullmatch(r"--hash=sha256:[0-9a-f]{64}\s*\\?", line) and current:
            hashes[current].append(line.removesuffix("\\").strip())
        else:
            invalid.append(line)
    return versions, hashes, invalid


def _violations(ci: str, lock: str, contributing: str) -> list[str]:
    problems: list[str] = []
    for name in READ_ONLY_PR_JOBS:
        job = _job(ci, name)
        if not job or "persist-credentials: false" not in job:
            problems.append(f"{name} retains unused checkout credentials")

    install_fragments = (
        "Install hash-locked sealed test dependency tree",
        "requirements-dev.lock",
        "--require-hashes --no-deps",
    )
    for name in TEST_JOBS:
        job = _job(ci, name)
        for fragment in install_fragments:
            if fragment not in job:
                problems.append(f"{name} misses sealed install fragment {fragment}")
    if re.search(r"pip install\s+[\"']?pytest(?:==|[\"'])", ci):
        problems.append("CI still contains a direct moving pytest install")

    test_profile = _profile(lock, "test-lock")
    versions, hashes, invalid = _pins(test_profile)
    if versions != TEST_TREE:
        problems.append("pytest direct/transitive tree differs from reviewed pins")
    if invalid or any(not hashes.get(name) for name in TEST_TREE):
        problems.append("pytest tree is not completely SHA-256 locked")

    asset_profile = _profile(lock, "asset-lock")
    asset_versions, asset_hashes, asset_invalid = _pins(asset_profile)
    if asset_versions != {"pillow": "12.2.0"}:
        problems.append("Pillow asset tool is not exactly pinned")
    if asset_invalid or not asset_hashes.get("pillow"):
        problems.append("Pillow asset tool has no reviewed SHA-256 artifact")
    build_profile = _profile(lock, "asset-build-lock")
    build_versions, build_hashes, build_invalid = _pins(build_profile)
    if build_versions != {"setuptools": "80.9.0", "wheel": "0.45.1"}:
        problems.append("Pillow build prerequisites are not exactly pinned")
    if build_invalid or any(
            not build_hashes.get(name) for name in build_versions):
        problems.append("Pillow build prerequisites are not SHA-256 locked")
    if "Pillow" in test_profile:
        problems.append("Pillow leaked into the ordinary CI test profile")
    required_docs = (
        "# asset-build-lock: ",
        "# asset-lock: ",
        "python3 scripts/render_readme_gif.py",
        "git diff --exit-code -- docs/assets/taskplane-cowork-flow.gif",
    )
    for fragment in required_docs:
        if fragment not in contributing:
            problems.append(f"asset regeneration docs miss {fragment}")
    if ("--require-hashes --no-deps --only-binary=:all: "
            "-r .requirements-asset-build.lock") not in contributing:
        problems.append(
            "Pillow build prerequisites do not select sealed binary artifacts")
    if "--no-binary=Pillow" not in contributing:
        problems.append(
            "Pillow install can prefer an artifact outside the source hash")
    if "--no-build-isolation" not in contributing:
        problems.append(
            "Pillow install can resolve unpinned isolated build dependencies")
    if ("--require-hashes --no-deps --no-binary=Pillow "
            "--no-build-isolation -r .requirements-asset.lock") \
            not in contributing:
        problems.append("Pillow source install is not one sealed command")
    runtime = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "taskplane").glob("*.py"))
    )
    if re.search(r"(?m)^\s*(?:from\s+PIL\s+import|import\s+PIL\b)", runtime):
        problems.append("Pillow leaked into the stdlib-only runtime")
    renderer = (ROOT / "scripts" / "render_readme_gif.py").read_text(
        encoding="utf-8"
    )
    for fragment in (
        'parser.add_argument("--output"',
        "Image.Quantize.MEDIANCUT",
        "Image.Dither.NONE",
    ):
        if fragment not in renderer:
            problems.append(f"asset renderer misses deterministic control {fragment}")
    return problems


def _sources() -> tuple[str, str, str]:
    return (
        CI.read_text(encoding="utf-8"),
        LOCK.read_text(encoding="utf-8"),
        CONTRIBUTING.read_text(encoding="utf-8"),
    )


def test_m06_readonly_PR_jobs_disable_persist_credentials() -> None:
    """M-06: PR jobs that never fetch cannot retain checkout credentials."""
    ci, lock, contributing = _sources()
    assert not [p for p in _violations(ci, lock, contributing)
                if "credentials" in p]


def test_m07_CI_uses_reviewed_hash_locked_dev_dependencies() -> None:
    """M-07: every pytest CI consumer installs one reviewed lock profile."""
    ci, lock, contributing = _sources()
    assert not [p for p in _violations(ci, lock, contributing)
                if "install fragment" in p or "moving pytest" in p]


def test_m17_pytest_is_version_and_hash_locked() -> None:
    """M-17: pytest itself is an exact pin with artifact integrity evidence."""
    profile = _profile(LOCK.read_text(encoding="utf-8"), "test-lock")
    versions, hashes, invalid = _pins(profile)
    assert invalid == []
    assert versions["pytest"] == "9.1.1"
    assert hashes["pytest"] == [
        "--hash=sha256:37a86b45efb9a47a61a36449063e8e18d0cab3161329fc099eb21783169c4f0c"
    ]


def test_m18_transitive_test_dependency_tree_is_sealed() -> None:
    """M-18: CI cannot resolve an undeclared or moving pytest dependency."""
    ci, lock, contributing = _sources()
    versions, hashes, invalid = _pins(_profile(lock, "test-lock"))
    assert versions == TEST_TREE
    assert invalid == []
    assert set(hashes) == set(TEST_TREE)
    assert all(hashes.values())
    assert ci.count("--require-hashes --no-deps") == len(TEST_JOBS)
    assert _violations(ci, lock, contributing) == []


def test_l06_Pillow_is_pinned_dev_only_and_assets_regenerate_reproducibly() -> None:
    """L-06: asset authoring is exact, documented, and absent from runtime."""
    ci, lock, contributing = _sources()
    assert not [p for p in _violations(ci, lock, contributing)
                if "Pillow" in p or "asset" in p]


@pytest.mark.parametrize(
    ("target", "old", "new", "reason"),
    (
        ("ci", "persist-credentials: false", "persist-credentials: true",
         "credentials"),
        ("ci", "--require-hashes --no-deps", "--require-hashes",
         "install fragment"),
        ("lock", "pytest==9.1.1", "pytest==9.*", "reviewed pins"),
        ("lock", "37a86b45efb9a47a61a36449063e8e18d0cab3161329fc099eb21783169c4f0c",
         "not-a-real-hash", "SHA-256"),
        ("lock", "# asset-lock: Pillow==12.2.0",
         "# test-lock: Pillow==12.2.0", "Pillow"),
        ("docs", "--only-binary=:all:", "--prefer-binary",
         "sealed binary artifacts"),
        ("docs", "--no-binary=Pillow", "--prefer-binary",
         "outside the source hash"),
        ("docs", "--no-build-isolation", "--use-pep517",
         "unpinned isolated build dependencies"),
        ("docs", "python3 scripts/render_readme_gif.py",
         "echo skip-render", "asset regeneration"),
    ),
)
def test_m1c_adversarial_mutations_fail_closed(
    target: str, old: str, new: str, reason: str
) -> None:
    """The focused evidence rejects weakened credentials, pins, and assets."""
    ci, lock, contributing = _sources()
    values = {"ci": ci, "lock": lock, "docs": contributing}
    assert old in values[target]
    values[target] = values[target].replace(old, new, 1)
    assert any(reason in problem for problem in _violations(
        values["ci"], values["lock"], values["docs"]
    ))
