#!/usr/bin/env python3
"""
Compare Open ACE Claude/ZCode usage with z.ai usage totals.

This script does not mutate the database. It reads:
  - Open ACE PostgreSQL ``daily_usage`` for Claude
  - Raw ZCode ``turn_usage`` from ~/.zcode/cli/db/db.sqlite
  - Raw Claude JSONL logs from ~/.claude/projects

It then prints:
  - Date-range totals by tool
  - Provider-style source totals
  - The implied Claude cache multiplier needed to match a known z.ai total
  - Optional hourly comparisons for a known z.ai hourly bar
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from shared import config  # noqa: E402


def _load_fetch_claude_module():
    spec = importlib.util.spec_from_file_location(
        "fetch_claude_compare", SCRIPT_DIR / "fetch_claude.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


@dataclass
class Totals:
    tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    @property
    def cache_tokens(self) -> int:
        return self.cache_read_tokens + self.cache_creation_tokens


def _fmt_int(value: int) -> str:
    return f"{value:,}"


def _fmt_float(value: float) -> str:
    return f"{value:.4f}"


def _connect_postgres():
    db_url = config.get_database_url()
    if not db_url.startswith("postgresql"):
        raise RuntimeError(f"Expected PostgreSQL database, got: {db_url}")
    import psycopg2

    return psycopg2.connect(db_url)


def load_claude_daily_usage(start_date: str, end_date: str) -> Totals:
    conn = _connect_postgres()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COALESCE(SUM(tokens_used), 0),
                    COALESCE(SUM(cache_tokens), 0)
                FROM daily_usage
                WHERE tool_name = 'claude'
                  AND date BETWEEN %s AND %s
                """,
                (start_date, end_date),
            )
            total_tokens, cache_tokens = cur.fetchone()
    finally:
        conn.close()

    return Totals(
        tokens=int((total_tokens or 0) - (cache_tokens or 0)),
        cache_read_tokens=int(cache_tokens or 0),
        cache_creation_tokens=0,
    )


def _parse_iso_to_local_hour(ts_str: str) -> str | None:
    if not ts_str:
        return None
    try:
        if ts_str.endswith("Z"):
            if "." in ts_str:
                base, rest = ts_str.rsplit(".", 1)
                ms = rest.rstrip("Z")
                ms = ms[:3].ljust(3, "0")
                dt = datetime.strptime(f"{base}.{ms}Z", "%Y-%m-%dT%H:%M:%S.%fZ")
            else:
                dt = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ")
            dt = dt.replace(tzinfo=timezone.utc).astimezone()
        else:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%Y-%m-%d %H:00")
    except Exception:
        return None


def load_claude_source_usage(
    start_date: str, end_date: str, hour_filter: str | None = None
) -> Totals:
    fetch_claude = _load_fetch_claude_module()
    project_dir = fetch_claude.find_claude_project_dir()
    if not project_dir:
        raise RuntimeError("Cannot find Claude project directory")

    projects: list[Path]
    direct_files = list(project_dir.glob("*.jsonl"))
    if direct_files:
        projects = [project_dir]
    else:
        projects = [d for d in project_dir.iterdir() if d.is_dir() and list(d.glob("*.jsonl"))]

    totals = Totals()
    for proj in projects:
        for jsonl_file in proj.glob("*.jsonl"):
            daily, messages = fetch_claude.process_jsonl_file(jsonl_file)

            if hour_filter is None:
                for date_key, stats in daily.items():
                    if not (start_date <= date_key <= end_date):
                        continue
                    totals.tokens += int(stats["input_tokens"] or 0)
                    totals.tokens += int(stats["output_tokens"] or 0)
                    totals.cache_read_tokens += int(stats["cache_read_tokens"] or 0)
                    totals.cache_creation_tokens += int(stats["cache_creation_tokens"] or 0)
                continue

            for msg in messages:
                ts = msg.get("timestamp")
                local_hour = _parse_iso_to_local_hour(ts) if ts else None
                if local_hour != hour_filter:
                    continue
                local_date = msg.get("date") or (local_hour[:10] if local_hour else None)
                if not local_date or not (start_date <= local_date <= end_date):
                    continue
                totals.tokens += int(msg.get("input_tokens", 0) or 0)
                totals.tokens += int(msg.get("output_tokens", 0) or 0)
                totals.cache_read_tokens += int(msg.get("cache_read_tokens", 0) or 0)
                totals.cache_creation_tokens += int(msg.get("cache_creation_tokens", 0) or 0)

    return totals


def load_zcode_source_usage(
    start_date: str,
    end_date: str,
    *,
    include_subagents: bool,
    hour_filter: str | None = None,
) -> Totals:
    db_path = Path.home() / ".zcode" / "cli" / "db" / "db.sqlite"
    if not db_path.is_file():
        raise RuntimeError(f"Cannot find ZCode DB: {db_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        where = [
            "date(datetime(t.started_at/1000,'unixepoch','localtime')) BETWEEN ? AND ?",
        ]
        params: list[Any] = [start_date, end_date]
        if not include_subagents:
            where.append("s.task_type = 'interactive'")
            where.append("s.time_archived IS NULL")
        if hour_filter is not None:
            where.append(
                "strftime('%Y-%m-%d %H:00', datetime(t.started_at/1000,'unixepoch','localtime')) = ?"
            )
            params.append(hour_filter)

        sql = f"""
            SELECT
                COALESCE(SUM(t.computed_total_tokens), 0),
                COALESCE(SUM(t.cache_read_input_tokens), 0),
                COALESCE(SUM(t.cache_creation_input_tokens), 0)
            FROM turn_usage t
            JOIN session s ON s.id = t.session_id
            WHERE {" AND ".join(where)}
        """
        row = conn.execute(sql, tuple(params)).fetchone()
    finally:
        conn.close()

    return Totals(
        tokens=int(row[0] or 0),
        cache_read_tokens=int(row[1] or 0),
        cache_creation_tokens=int(row[2] or 0),
    )


def implied_cache_multiplier(target_total: int, base_total: int, cache_total: int) -> float | None:
    if cache_total == 0:
        return None
    return (target_total - base_total) / cache_total


def print_totals(label: str, totals: Totals) -> None:
    print(label)
    print(f"  Tokens:         {_fmt_int(totals.tokens)}")
    print(f"  Cache read:     {_fmt_int(totals.cache_read_tokens)}")
    print(f"  Cache creation: {_fmt_int(totals.cache_creation_tokens)}")
    print(f"  Cache total:    {_fmt_int(totals.cache_tokens)}")
    print(f"  Tokens+cache:   {_fmt_int(totals.tokens + totals.cache_tokens)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2026-07-09", help="Start date YYYY-MM-DD")
    parser.add_argument("--end-date", default="2026-07-15", help="End date YYYY-MM-DD")
    parser.add_argument(
        "--zai-total",
        type=int,
        default=170051875,
        help="Known z.ai total tokens for the range",
    )
    parser.add_argument(
        "--zai-hour",
        default="2026-07-15 15:00",
        help="Known local-hour bucket from z.ai chart",
    )
    parser.add_argument(
        "--zai-hour-total",
        type=int,
        default=28618223,
        help="Known z.ai token total for --zai-hour",
    )
    args = parser.parse_args()

    print(f"Range: {args.start_date} -> {args.end_date}")
    print(f"z.ai total: {_fmt_int(args.zai_total)}")
    print()

    claude_db = load_claude_daily_usage(args.start_date, args.end_date)
    claude_src = load_claude_source_usage(args.start_date, args.end_date)
    zcode_openace_scope = load_zcode_source_usage(
        args.start_date, args.end_date, include_subagents=False
    )
    zcode_all_scope = load_zcode_source_usage(
        args.start_date, args.end_date, include_subagents=True
    )

    print_totals("Claude (Open ACE daily_usage)", claude_db)
    print()
    print_totals("Claude (raw source logs)", claude_src)
    print()
    print_totals("ZCode (interactive, active sessions only)", zcode_openace_scope)
    print()
    print_totals("ZCode (all source sessions)", zcode_all_scope)
    print()

    for label, zcode in [
        ("Open ACE ZCode scope", zcode_openace_scope),
        ("All-source ZCode scope", zcode_all_scope),
    ]:
        base_total = zcode.tokens + claude_src.tokens
        cache_total = claude_src.cache_tokens
        multiplier = implied_cache_multiplier(args.zai_total, base_total, cache_total)
        print(label)
        print(f"  Base total without Claude cache: {_fmt_int(base_total)}")
        print(f"  Claude cache to reconcile:       {_fmt_int(cache_total)}")
        if multiplier is None:
            print("  Implied Claude cache multiplier: n/a")
        else:
            print(f"  Implied Claude cache multiplier: {_fmt_float(multiplier)}")
            reconciled = base_total + int(round(cache_total * multiplier))
            print(f"  Reconciled total at multiplier:  {_fmt_int(reconciled)}")
        print()

    if args.zai_hour and args.zai_hour_total:
        print(f"Hourly check: {args.zai_hour} -> z.ai {_fmt_int(args.zai_hour_total)}")
        claude_hour = load_claude_source_usage(
            args.zai_hour[:10], args.zai_hour[:10], hour_filter=args.zai_hour
        )
        zcode_hour = load_zcode_source_usage(
            args.zai_hour[:10],
            args.zai_hour[:10],
            include_subagents=True,
            hour_filter=args.zai_hour,
        )
        print_totals("Claude (raw source hour)", claude_hour)
        print()
        print_totals("ZCode (raw source hour)", zcode_hour)
        print()
        hour_base = claude_hour.tokens + zcode_hour.tokens
        hour_multiplier = implied_cache_multiplier(
            args.zai_hour_total, hour_base, claude_hour.cache_tokens
        )
        print(f"  Hour base total without Claude cache: {_fmt_int(hour_base)}")
        print(f"  Hour Claude cache to reconcile:       {_fmt_int(claude_hour.cache_tokens)}")
        if hour_multiplier is None:
            print("  Hour implied Claude cache multiplier: n/a")
        else:
            print(f"  Hour implied Claude cache multiplier: {_fmt_float(hour_multiplier)}")
            reconciled = hour_base + int(round(claude_hour.cache_tokens * hour_multiplier))
            print(f"  Hour reconciled total:               {_fmt_int(reconciled)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
