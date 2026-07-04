"""SQLite regression tests for LIKE wildcard escaping (issue #15 from #241).

The fix adds an explicit ``ESCAPE '\\'`` clause to every escape_like()-fed
LIKE. This matters because the SQL standard has NO default escape character:
PostgreSQL happens to default to backslash, but SQLite does not — so on SQLite
an escape_like()'d value without ESCAPE still leaks ``%`` / ``_`` wildcards (or,
because the injected backslashes become literal characters, silently breaks the
search). These tests run real LIKE queries against an in-memory SQLite database
to prove the ``ESCAPE`` clause is load-bearing on the platform that was broken.
"""

from __future__ import annotations

import sqlite3

from app.repositories.database import escape_like


def _connect_with_rows() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (title TEXT)")
    rows = [
        ("100% done",),  # literal percent
        ("a_b",),  # literal underscore
        ("axb",),  # would match a_b if '_' were still a wildcard
        ("normal",),  # no special chars
        ("back\\slash",),  # literal backslash
        ("50%_off",),  # both special chars
    ]
    conn.executemany("INSERT INTO t (title) VALUES (?)", rows)
    conn.commit()
    return conn


def _search(conn: sqlite3.Connection, term: str, *, with_escape: bool) -> set[str]:
    """Run a substring LIKE search exactly as the fixed code does.

    ``with_escape=True`` mirrors the post-fix query (ESCAPE clause present);
    ``with_escape=False`` mirrors the pre-fix query (no ESCAPE clause).
    """
    pattern = f"%{escape_like(term)}%"
    clause = "LIKE ? ESCAPE '\\'" if with_escape else "LIKE ?"
    rows = conn.execute(f"SELECT title FROM t WHERE title {clause}", (pattern,)).fetchall()
    return {r[0] for r in rows}


class TestLikeEscapeOnSqlite:
    def test_percent_search_matches_only_literal_percent(self):
        conn = _connect_with_rows()
        # search='%' must match only rows literally containing '%', not all rows
        assert _search(conn, "%", with_escape=True) == {"100% done", "50%_off"}

    def test_underscore_search_matches_only_literal_underscore(self):
        conn = _connect_with_rows()
        # search='_' must match only rows literally containing '_', and must NOT
        # match 'axb' (which it would if '_' were still a single-char wildcard)
        assert _search(conn, "_", with_escape=True) == {"a_b", "50%_off"}
        assert "axb" not in _search(conn, "_", with_escape=True)

    def test_compound_term_with_percent(self):
        conn = _connect_with_rows()
        assert _search(conn, "100%", with_escape=True) == {"100% done"}

    def test_compound_term_with_underscore(self):
        conn = _connect_with_rows()
        assert _search(conn, "a_b", with_escape=True) == {"a_b"}
        # 'axb' must not leak in via wildcard
        assert "axb" not in _search(conn, "a_b", with_escape=True)

    def test_backslash_in_input_matches_literally(self):
        conn = _connect_with_rows()
        assert _search(conn, "back\\slash", with_escape=True) == {"back\\slash"}

    def test_escape_clause_is_load_bearing_on_sqlite(self):
        """Without ESCAPE, SQLite treats the escape_like backslashes as literal
        characters and the search returns the WRONG rows — proving the ESCAPE
        clause is what makes escape_like() effective on SQLite."""
        conn = _connect_with_rows()
        with_escape = _search(conn, "%", with_escape=True)
        without_escape = _search(conn, "%", with_escape=False)
        # The two must differ; with_escape is correct, without_escape is broken.
        assert with_escape != without_escape
        assert with_escape == {"100% done", "50%_off"}
        # Without ESCAPE the stray backslash makes '%' match the backslash row
        # instead of the literal-percent rows — definitively wrong.
        assert "100% done" not in without_escape

    def test_lower_pattern_mirrors_list_sessions(self):
        """list_sessions uses LOWER(col) LIKE ? ESCAPE '\\'. Mirror it end-to-end."""
        conn = _connect_with_rows()
        safe = escape_like("100%".lower())
        pattern = f"%{safe}%"
        rows = conn.execute(
            "SELECT title FROM t WHERE LOWER(title) LIKE ? ESCAPE '\\'", (pattern,)
        ).fetchall()
        assert {r[0] for r in rows} == {"100% done"}
