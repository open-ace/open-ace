"""Guardrails for the test taxonomy introduced by Issue #2429."""

from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]
LEGACY_ISSUES_ROOT = TESTS_ROOT / "issues"
LEGACY_INVENTORY = LEGACY_ISSUES_ROOT / "legacy-directories.txt"
RETIRED_PR_GATE_LIST = LEGACY_ISSUES_ROOT / "pr-gate-directories.txt"
LEGACY_TOP_LEVEL_DIRECTORIES = TESTS_ROOT / "legacy-top-level-directories.txt"
LEGACY_TOP_LEVEL_FILES = TESTS_ROOT / "legacy-top-level-files.txt"
CANONICAL_DIRECTORIES = {"e2e", "integration", "issues", "performance", "support", "unit"}


def _legacy_inventory():
    return {
        line.strip()
        for line in LEGACY_INVENTORY.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }


def _text_inventory(path):
    return {
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }


def test_no_new_issue_number_directory_is_added_to_legacy_quarantine():
    """New regressions belong to a canonical execution-layer directory."""
    inventory = _legacy_inventory()
    current = {
        path.name for path in LEGACY_ISSUES_ROOT.iterdir() if path.is_dir() and path.name.isdigit()
    }

    unexpected = current - inventory
    assert not unexpected, (
        "New tests/issues/<number> directories are prohibited. Put the test in "
        "tests/unit, tests/integration, tests/e2e, or "
        f"tests/performance and add issue/regression markers. Unexpected: {sorted(unexpected)}"
    )


def test_legacy_issue_inventory_contains_only_issue_numbers():
    invalid = sorted(entry for entry in _legacy_inventory() if not entry.isdigit())
    assert not invalid, f"Invalid entries in legacy issue inventory: {invalid}"


def test_legacy_pr_gate_promotion_list_stays_retired():
    """The pr-gate promotion list and its legacy-pr suite were retired when the
    last two promoted directories (2335, 2431) migrated to canonical layers
    (#2429 batch 2). Promoted coverage is the required python-core/python-min
    lanes now; the list must not come back."""
    assert not RETIRED_PR_GATE_LIST.exists(), (
        "tests/issues/pr-gate-directories.txt was retired with the legacy-pr "
        "suite; promote regressions by moving them to a canonical layer instead"
    )


def test_no_numbered_test_directory_exists_at_the_test_root():
    numbered = sorted(
        path.name for path in TESTS_ROOT.iterdir() if path.is_dir() and path.name.isdigit()
    )
    assert not numbered, f"Numbered test directories must not live at tests/: {numbered}"


def test_no_new_test_domain_directory_is_added_at_the_test_root():
    """Top-level directories describe runtime layers, not feature domains."""
    legacy = _text_inventory(LEGACY_TOP_LEVEL_DIRECTORIES)
    source_directories = {
        path.name
        for path in TESTS_ROOT.iterdir()
        if path.is_dir() and any(child.suffix == ".py" for child in path.rglob("*.py"))
    }
    unexpected = source_directories - CANONICAL_DIRECTORIES - legacy
    assert not unexpected, (
        "New top-level test directories are prohibited; choose an execution layer. "
        f"Unexpected: {sorted(unexpected)}"
    )


def test_no_new_test_module_is_added_at_the_test_root():
    legacy = _text_inventory(LEGACY_TOP_LEVEL_FILES)
    current = {path.name for path in TESTS_ROOT.glob("test_*.py")}
    unexpected = current - legacy
    assert not unexpected, (
        "New tests/*.py modules are prohibited; choose unit, integration, e2e, or "
        f"performance. Unexpected: {sorted(unexpected)}"
    )


def test_regression_is_metadata_not_a_top_level_directory():
    assert not (TESTS_ROOT / "regression").exists(), (
        "Regression is a test purpose, not an execution environment; use the "
        "regression marker in the canonical test layer instead"
    )


def test_security_is_metadata_not_a_top_level_directory():
    assert not (TESTS_ROOT / "security").exists(), (
        "Security is a test property, not an execution environment; use the "
        "security marker in the canonical test layer instead"
    )


def test_purpose_markers_are_not_runtime_directories():
    prohibited = sorted(
        str(path.relative_to(TESTS_ROOT))
        for path in TESTS_ROOT.rglob("*")
        if path.is_dir()
        and path.name in {"regression", "security"}
        and LEGACY_ISSUES_ROOT not in path.parents
    )
    assert not prohibited, (
        "regression/security must be markers in a runtime directory, not directories: "
        f"{prohibited}"
    )
