"""GitHubOps ISO-8601 date parsing (Z-suffix normalization).

GitHub API timestamps end in ``Z``; ``datetime.fromisoformat`` only parses
that suffix from Python 3.11, while this repo supports 3.10. The merge-phase
freshness defer depends on this parser — a silent parse failure there would
disable the defer exactly on 3.10 deployments.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.modules.workspace.autonomous.github_ops import parse_github_iso_datetime


def test_parse_z_suffix_yields_aware_utc():
    parsed = parse_github_iso_datetime("2026-08-18T17:43:12Z")
    assert parsed == datetime(2026, 8, 18, 17, 43, 12, tzinfo=timezone.utc)


def test_parse_explicit_offset():
    parsed = parse_github_iso_datetime("2026-08-18T17:43:12+08:00")
    assert parsed is not None
    assert parsed.utcoffset().total_seconds() == 8 * 3600


def test_parse_garbage_and_empty_returns_none():
    assert parse_github_iso_datetime("") is None
    assert parse_github_iso_datetime("   ") is None
    assert parse_github_iso_datetime("not-a-date") is None
    assert parse_github_iso_datetime(None) is None
