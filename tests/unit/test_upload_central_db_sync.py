"""Guard against drift between the upload-to-central bundle's minimal db.py and
the canonical ``scripts/shared/db.py`` (eval §6.2 P2-2).

``scripts/upload-to-central/shared/db.py`` was reduced from a ~4000-line copy of
``scripts/shared/db.py`` to just the connection helper its only consumer
(``upload_to_server.py``) uses. Those helpers are copied verbatim from the
canonical module and must stay behaviorally identical. This test enforces that
"keep in sync" convention so the copy cannot silently drift again (the exact
problem the reduction fixed -- the old copy's ``_get_db_url`` had fallen behind
the canonical sudo/gssencmode fix).

The files are parsed with ``ast`` rather than imported: the bundle's
``shared/__init__.py`` eagerly imports modules not vendored in the bundle, so
importing the module in-place fails (a separate, pre-existing issue).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CANONICAL = _REPO_ROOT / "scripts" / "shared" / "db.py"
_BUNDLE = _REPO_ROOT / "scripts" / "upload-to-central" / "shared" / "db.py"

# The connection helper the bundle copy keeps; must match canonical verbatim.
_SHARED_FUNCTIONS = ["_get_db_url", "ensure_db_dir", "get_connection"]


def _function_source(path: Path, name: str) -> str | None:
    """Return the normalized (ast.unparse) source of a top-level function."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    return None


@pytest.mark.parametrize("func_name", _SHARED_FUNCTIONS)
def test_bundle_db_helper_matches_canonical(func_name: str) -> None:
    """Each kept helper in the bundle copy is AST-identical to canonical."""
    canonical = _function_source(_CANONICAL, func_name)
    bundle = _function_source(_BUNDLE, func_name)

    assert canonical is not None, f"{func_name} missing from {_CANONICAL}"
    assert bundle is not None, f"{func_name} missing from {_BUNDLE}"
    assert bundle == canonical, (
        f"{func_name} in {_BUNDLE.relative_to(_REPO_ROOT)} has drifted from "
        f"{_CANONICAL.relative_to(_REPO_ROOT)}. Re-copy it verbatim, or update "
        f"both together."
    )


def test_bundle_db_stays_minimal() -> None:
    """The bundle copy must not regrow into a full duplicate of canonical.

    upload_to_server.py needs only the connection helper; keeping the file small
    is what prevents the ~3900-line duplication from returning.
    """
    bundle_defs = {
        node.name
        for node in ast.parse(_BUNDLE.read_text(encoding="utf-8")).body
        if isinstance(node, ast.FunctionDef)
    }
    assert bundle_defs == set(_SHARED_FUNCTIONS), (
        "The upload-to-central db.py should define only the connection helper "
        f"({sorted(_SHARED_FUNCTIONS)}); found {sorted(bundle_defs)}. If a new "
        "helper is genuinely needed by upload_to_server.py, copy it verbatim "
        "from scripts/shared/db.py and update this test."
    )
