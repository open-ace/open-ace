#!/usr/bin/env python3
"""
Open ACE - Per-session-average root-cause diagnostic (data-gathering gate).

Investigates why "管理模式 → Token 趋势 → 会话统计" per-session averages
(`avg_messages_per_session`, `avg_tokens_per_session`) may show ~0.

Background: ``avg = numerator / total_sessions``. The legacy
``total_sessions = unique_days * unique_tools`` is a SMALL denominator, so a
near-zero average cannot be caused by a small denominator alone. This script
samples the real values and runs the pure classifier
(`app.utils.session_diagnostics.classify_session_avg_rootcause`) to decide
whether switching the denominator to the real distinct conversation count is
the correct fix (scenario "a") -- or whether the real cause is elsewhere
(numerator ~0 / "b", real >> approx / "c", or messages lack session ids / "d").

Run from the repo root in an environment that can reach the database
(same env as the Flask app):

    python scripts/diagnose_session_avg.py [--start 2026-05-18] [--end 2026-06-17] [--host HOST]

This script is read-only; it does not modify any data.
"""

import argparse
import os
import sys
from pathlib import Path

# Make the repo root importable so `app.*` resolves when run as a script.
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from app.repositories.daily_stats_repo import DailyStatsRepository  # noqa: E402
from app.repositories.database import is_postgresql  # noqa: E402
from app.repositories.message_repo import MessageRepository  # noqa: E402
from app.utils.helpers import get_days_ago, get_today  # noqa: E402
from app.utils.session_diagnostics import classify_session_avg_rootcause  # noqa: E402
from app.utils.tool_names import normalize_tool_name  # noqa: E402


def _pct(num: float, denom: float) -> str:
    if not denom:
        return "n/a"
    return f"{num / denom * 100:.1f}%"


def gather_sample(start_date: str, end_date: str, host_name) -> dict:
    """Gather all values needed by the classifier (read-only)."""
    daily_repo = DailyStatsRepository()
    msg_repo = MessageRepository()
    db = msg_repo.db

    host_cond = " AND host_name = ?" if host_name else ""
    host_params: tuple = (host_name,) if host_name else ()

    aggregates = daily_repo.get_batch_aggregates(start_date, end_date, host_name)

    # distinct conversations + session-scoped sums (method B, the fix's source).
    summary = msg_repo.get_conversation_stats_summary(
        start_date=start_date, end_date=end_date, host_name=host_name
    )
    distinct = int(summary.get("total_conversations", 0) or 0)

    # segments (count_conversations: 5-column grouping) -- auxiliary only.
    try:
        segments = msg_repo.count_conversations(
            start_date=start_date, end_date=end_date, host_name=host_name
        )
    except Exception as exc:  # noqa: BLE001
        segments = -1
        print(f"[warn] count_conversations failed: {exc}", file=sys.stderr)

    # Raw (un-normalized) tool_name variant count vs normalized count -> (a).
    rows = db.fetch_all(
        f"SELECT DISTINCT tool_name FROM daily_stats WHERE date >= ? AND date <= ?{host_cond}",
        (start_date, end_date, *host_params),
    )
    raw_tools = {r["tool_name"] for r in rows if r.get("tool_name")}
    norm_tools = {normalize_tool_name(t) for t in raw_tools}

    # agent_session_id fill rate + no-session-id message fraction -> (d) / perf.
    fill = db.fetch_one(
        f"""
        SELECT
            COUNT(*) AS total,
            COUNT(agent_session_id) AS with_agent_session,
            SUM(CASE WHEN COALESCE(conversation_id, feishu_conversation_id,
                                    agent_session_id) IS NOT NULL THEN 1 ELSE 0 END) AS with_any_session
        FROM daily_messages
        WHERE date >= ? AND date <= ?{host_cond}
        """,
        (start_date, end_date, *host_params),
    )

    sample = {
        "total_tokens": aggregates.get("total_tokens", 0),
        "total_messages": aggregates.get("total_messages", 0),
        "unique_days": aggregates.get("unique_days", 0),
        "unique_tools": aggregates.get("unique_tools", 0),
        "distinct": distinct,
    }

    diagnostics = {
        "segments": segments,
        "session_messages": summary.get("total_messages", 0),
        "session_tokens": summary.get("total_tokens", 0),
        "raw_tool_variants": len(raw_tools),
        "normalized_tool_variants": len(norm_tools),
        "total_rows": fill.get("total", 0) if fill else 0,
        "agent_session_fill_pct": _pct(
            (fill.get("with_agent_session", 0) if fill else 0),
            (fill.get("total", 0) if fill else 0) or 1,
        ),
        "no_session_id_pct": _pct(
            (
                (fill.get("total", 0) if fill else 0)
                - (fill.get("with_any_session", 0) if fill else 0)
            ),
            (fill.get("total", 0) if fill else 0) or 1,
        ),
        "backend": "postgresql" if is_postgresql() else "sqlite",
    }
    return sample, diagnostics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=get_days_ago(30), help="start date YYYY-MM-DD")
    parser.add_argument("--end", default=get_today(), help="end date YYYY-MM-DD")
    parser.add_argument("--host", default=None, help="host_name filter")
    args = parser.parse_args()

    print(f"Range: {args.start} .. {args.end}  host={args.host or '(all)'}")
    sample, diag = gather_sample(args.start, args.end, args.host)

    print("\n== Sample (classifier input) ==")
    for k, v in sample.items():
        print(f"  {k}: {v}")

    print("\n== Auxiliary diagnostics ==")
    for k, v in diag.items():
        print(f"  {k}: {v}")

    verdict = classify_session_avg_rootcause(sample)
    print("\n== Verdict ==")
    print(f"  class:    {verdict['class']}")
    print(f"  proceed:  {verdict['proceed']}")
    print(f"  reason:   {verdict['reason']}")

    if not verdict["proceed"]:
        print("\nDo NOT switch the denominator yet -- see reason above.")
        return 1
    print("\nFix direction confirmed (scenario a): switch to real distinct count.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
