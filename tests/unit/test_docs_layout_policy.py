"""Guardrails for the docs tree reorganization (2026-09, PR #3383)."""

from pathlib import Path

DOCS_ROOT = Path(__file__).resolve().parents[2] / "docs"
RETIRED_SUPERPOWERS_ROOT = DOCS_ROOT / "superpowers"


def test_superpowers_plans_tree_stays_retired():
    """docs/superpowers/ was retired by the 2026-09 docs reorganization: its
    57 skill-generated plans/specs had no readers and duplicated
    docs/dev-notes/. In-flight pipeline PRs (#3381/#3382) reintroduced the
    tree once already; this guard turns any recurrence red at PR time.

    Curated process write-ups belong in docs/dev-notes/ (see its README);
    throwaway scratch plans stay untracked and gitignored.
    """
    assert not RETIRED_SUPERPOWERS_ROOT.exists(), (
        "docs/superpowers/ is retired. Move the write-up to docs/dev-notes/ "
        "with an <issue>-<slug>.md name, or keep scratch plans out of the "
        "commit entirely (the path is gitignored)."
    )
