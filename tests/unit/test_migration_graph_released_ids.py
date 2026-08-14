"""The Alembic graph must keep resolving every revision id that has shipped.

``20260731_003_add_proxy_token_terminated_fields`` shipped on main (commit
05bcca72) and was then renumbered to ``20260731_004_...`` so that
``20260731_003_add_teams_sync_source_indexes`` could take the 003 slot. Alembic
keys history off the revision id, so the rename deleted a node that live
databases were already stamped with: every deployment upgraded in that window
now fails ``upgrade head`` with ``Can't locate revision identified by
'20260731_003_add_proxy_token_terminated_fields'`` -- and stays stuck for every
later migration too.

The recovery is a no-op bridge that re-homes the orphaned id between 002 and
the teams-index migration. These tests pin its shape so nobody "cleans up" the
empty migration later.

Lineage: Issue #1704 (migration authoring rules).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.regression,
    pytest.mark.issue(1704),
]

VERSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations" / "versions"

ORPHANED_ID = "20260731_003_add_proxy_token_terminated_fields"
TEAMS_INDEX_ID = "20260731_003_add_teams_sync_source_indexes"
PROXY_TOKEN_ID = "20260731_004_add_proxy_token_terminated_fields"
CI_REPAIR_ID = "20260731_002_add_ci_repair_transient_retries"


def _module_constants(path: Path) -> dict[str, object]:
    """Read module-level literal assignments without importing the module."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            first = node.targets[0]
            if not isinstance(first, ast.Name):
                continue
            name = first.id
        else:
            continue
        if isinstance(node.value, ast.Constant):
            out[name] = node.value.value
    return out


@pytest.fixture(scope="module")
def graph() -> dict[str, dict]:
    """Map revision id -> {down_revision, path} for every committed migration."""
    revisions: dict[str, dict] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        if path.name.startswith("__"):
            continue
        consts = _module_constants(path)
        rev = consts.get("revision")
        if isinstance(rev, str):
            revisions[rev] = {"down_revision": consts.get("down_revision"), "path": path}
    return revisions


class TestOrphanedRevisionIsReachable:
    def test_orphaned_id_exists_in_the_graph(self, graph):
        """Without this node, every database stamped with it is wedged."""
        assert ORPHANED_ID in graph, (
            f"{ORPHANED_ID} shipped on main and must remain a resolvable revision. "
            "See migrations/versions/20260731_003_bridge_renamed_proxy_token_revision.py"
        )

    def test_bridge_is_parented_to_the_revision_it_originally_followed(self, graph):
        """A stuck database sits at 002's successor; the bridge must be exactly that."""
        assert graph[ORPHANED_ID]["down_revision"] == CI_REPAIR_ID

    @staticmethod
    def _effective_statements(node: ast.FunctionDef) -> list[ast.stmt]:
        """Body statements that do something, i.e. everything but a docstring/pass.

        Note what is NOT excluded: a bare ``op.add_column(...)`` parses to
        ``ast.Expr(value=ast.Call(...))``. Filtering out every ``ast.Expr``
        would drop exactly the DDL this check exists to catch, so only
        ``ast.Expr`` wrapping a *constant* (the docstring) is ignored.
        """
        out = []
        for stmt in node.body:
            if isinstance(stmt, ast.Pass):
                continue
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
                continue
            out.append(stmt)
        return out

    def test_bridge_applies_no_ddl(self, graph):
        """The DDL lives in 004. Re-applying it here would double-add columns."""
        source = graph[ORPHANED_ID]["path"].read_text(encoding="utf-8")
        tree = ast.parse(source)
        funcs = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in ("upgrade", "downgrade")
        }
        assert set(funcs) == {"upgrade", "downgrade"}
        for name, node in funcs.items():
            statements = self._effective_statements(node)
            assert not statements, (
                f"{name}() must stay a no-op, found {len(statements)} statement(s): "
                f"{[ast.dump(s) for s in statements]}"
            )

    def test_the_no_ddl_check_would_actually_catch_ddl(self):
        """Guard the guard: the previous version of this check was vacuous.

        It filtered out every ``ast.Expr``, which is precisely how a bare
        ``op.add_column(...)`` call parses -- so a bridge full of DDL passed.
        """
        planted = ast.parse(
            "def upgrade():\n"
            '    """doc"""\n'
            '    op.add_column("t", sa.Column("c"))\n'
            '    op.execute("DROP TABLE users")\n'
        ).body[0]
        assert isinstance(planted, ast.FunctionDef)

        assert len(self._effective_statements(planted)) == 2

        empty = ast.parse('def upgrade():\n    """doc only"""\n').body[0]
        assert isinstance(empty, ast.FunctionDef)
        assert self._effective_statements(empty) == []

        just_pass = ast.parse("def upgrade():\n    pass\n").body[0]
        assert isinstance(just_pass, ast.FunctionDef)
        assert self._effective_statements(just_pass) == []

    def test_teams_index_migration_follows_the_bridge(self, graph):
        """Ordering matters: a stuck database still needs the teams index."""
        assert graph[TEAMS_INDEX_ID]["down_revision"] == ORPHANED_ID

    def test_proxy_token_ddl_still_follows_the_teams_index(self, graph):
        assert graph[PROXY_TOKEN_ID]["down_revision"] == TEAMS_INDEX_ID

    def test_path_from_orphan_reaches_a_single_head(self, graph):
        """Walk the chain forward and confirm it terminates in exactly one head."""
        children: dict[object, list[str]] = {}
        for rev, meta in graph.items():
            children.setdefault(meta["down_revision"], []).append(rev)

        current = ORPHANED_ID
        seen = {current}
        while True:
            nxt = children.get(current, [])
            assert len(nxt) <= 1, f"{current} forks into {nxt}"
            if not nxt:
                break
            current = nxt[0]
            assert current not in seen, f"cycle at {current}"
            seen.add(current)

        heads = [rev for rev in graph if rev not in children]
        assert heads == [current], f"expected the walk to end at the single head, got {heads}"


class TestProxyTokenMigrationStaysIdempotent:
    def test_add_column_calls_are_guarded(self, graph):
        """A recovered database already has these columns from the old id.

        The bridge only works because 004 checks the inspector before adding;
        drop that guard and every recovered database fails on duplicate column.
        """
        source = graph[PROXY_TOKEN_ID]["path"].read_text(encoding="utf-8")
        tree = ast.parse(source)

        upgrade = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
        )

        add_columns = [
            node
            for node in ast.walk(upgrade)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_column"
        ]
        assert add_columns, "expected add_column calls in upgrade()"

        guarded = [
            node
            for node in ast.walk(upgrade)
            if isinstance(node, ast.If)
            and any(
                isinstance(c, ast.Call)
                and isinstance(c.func, ast.Attribute)
                and c.func.attr == "add_column"
                for c in ast.walk(node)
            )
        ]
        assert len(guarded) >= len(add_columns), (
            "every op.add_column in 20260731_004 must sit behind an existence check "
            "-- databases recovered from the orphaned revision id already have these columns"
        )
