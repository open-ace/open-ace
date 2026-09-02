"""Guardrails for the test taxonomy introduced by Issue #2429."""

from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]
LEGACY_ISSUES_ROOT = TESTS_ROOT / "issues"
LEGACY_INVENTORY = LEGACY_ISSUES_ROOT / "legacy-directories.txt"
RETIRED_PR_GATE_LIST = LEGACY_ISSUES_ROOT / "pr-gate-directories.txt"
LEGACY_TOP_LEVEL_DIRECTORIES = TESTS_ROOT / "legacy-top-level-directories.txt"
LEGACY_TOP_LEVEL_FILES = TESTS_ROOT / "legacy-top-level-files.txt"
CANONICAL_DIRECTORIES = {"e2e", "integration", "issues", "performance", "support", "unit"}


def test_legacy_issue_quarantine_tree_stays_retired():
    """#2429 final exodus deleted the tests/issues quarantine with its final
    e2e wave. New regressions belong to a canonical execution-layer directory
    with issue/regression markers; the tree must not come back."""
    assert not LEGACY_ISSUES_ROOT.exists(), (
        "tests/issues/ was retired by the #2429 final exodus. Put the test in "
        "tests/unit, tests/integration, tests/e2e, or tests/performance and add "
        "issue/regression markers instead of recreating the quarantine."
    )


def test_legacy_issue_inventory_stays_retired():
    """The legacy-directories inventory died with the tree; a stray inventory
    file would imply an untracked tests/issues revival."""
    assert not LEGACY_INVENTORY.exists(), (
        "tests/issues/legacy-directories.txt was retired together with the "
        "tests/issues tree; do not recreate it."
    )


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
    """Top-level directories describe runtime layers, not feature domains.

    #3185 emptied the last grandfather domain (tests/autonomous) and retired
    its inventory; the guard now reads the live tree only.
    """
    source_directories = {
        path.name
        for path in TESTS_ROOT.iterdir()
        if path.is_dir() and any(child.suffix == ".py" for child in path.rglob("*.py"))
    }
    unexpected = source_directories - CANONICAL_DIRECTORIES
    assert not unexpected, (
        "New top-level test directories are prohibited; choose an execution layer. "
        f"Unexpected: {sorted(unexpected)}"
    )


def test_no_new_test_module_is_added_at_the_test_root():
    """tests/ holds ONLY conftest.py and __init__.py as Python modules.

    #3185 retired the grandfather-file inventory with the last root module.
    The exact-set pin covers every collectible shape (test_*.py, *_test.py,
    e2e_*.py) AND non-collectible .py litter (api_test_*.py class).
    """
    current = {path.name for path in TESTS_ROOT.glob("*.py")}
    assert current == {"conftest.py", "__init__.py"}, (
        "New tests/*.py modules are prohibited; choose unit, integration, e2e, or "
        f"performance. Found at tests root: {sorted(current)}"
    )


def test_grandfather_inventories_stay_retired():
    """The legacy-top-level inventories died with the last grandfathered
    item; a stray file would imply an untracked root revival."""
    assert (
        not LEGACY_TOP_LEVEL_DIRECTORIES.exists()
    ), "tests/legacy-top-level-directories.txt was retired by #3185; do not recreate."
    assert (
        not LEGACY_TOP_LEVEL_FILES.exists()
    ), "tests/legacy-top-level-files.txt was retired by #3185; do not recreate."


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
        if path.is_dir() and path.name in {"regression", "security"}
    )
    assert (
        not prohibited
    ), f"regression/security must be markers in a runtime directory, not directories: {prohibited}"
