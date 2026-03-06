#!/usr/bin/env python3
"""
AI Token Usage - CLI Tool

A unified command-line interface for querying token usage data.
"""

import argparse
import sys
import os
from typing import Optional
from collections import defaultdict

# Add shared directory to path
script_dir = os.path.dirname(os.path.abspath(__file__))
shared_dir = os.path.join(script_dir, 'scripts')

if shared_dir not in sys.path:
    sys.path.insert(0, shared_dir)

from shared import db, utils, email_notifier
from shared.config import CONFIG_DIR, CONFIG_PATH


def cmd_today(tool: Optional[str] = None, host: Optional[str] = None) -> None:
    """Show usage for today."""
    today = utils.get_today()
    entries = db.get_usage_by_date(today, tool, host)

    if not entries:
        print(f"No usage data found for {today}")
        if tool:
            print(f"Tool: {tool}")
        if host:
            print(f"Host: {host}")
        return

    print(f"Usage for {today}")
    print("=" * 50)

    for entry in entries:
        tool_name = entry['tool_name']
        tokens = entry['tokens_used']
        input_tok = entry.get('input_tokens', 0)
        output_tok = entry.get('output_tokens', 0)
        cache_tok = entry.get('cache_tokens', 0)
        request_count = entry.get('request_count', 0)

        print(f"\n[{tool_name.upper()}]")
        print(f"  Total:  {utils.format_tokens(tokens)} ({tokens:,})")
        if input_tok > 0 or output_tok > 0:
            print(f"  Input:  {utils.format_tokens(input_tok)} ({input_tok:,})")
            print(f"  Output: {utils.format_tokens(output_tok)} ({output_tok:,})")
        if cache_tok > 0:
            print(f"  Cache:  {utils.format_tokens(cache_tok)} ({cache_tok:,})")
        if request_count > 0:
            print(f"  Requests: {request_count:,}")
        if entry.get('models_used'):
            print(f"  Models: {', '.join(entry['models_used'])}")


def cmd_query(date: str, tool: Optional[str] = None, host: Optional[str] = None) -> None:
    """Query usage for a specific date."""
    parsed_date = utils.parse_date(date)
    if not parsed_date:
        print(f"Invalid date format: {date}. Use YYYY-MM-DD")
        return

    entries = db.get_usage_by_date(parsed_date, tool, host)

    if not entries:
        print(f"No usage data found for {parsed_date}")
        if host:
            print(f"Host: {host}")
        return

    print(f"Usage for {parsed_date}")
    print("=" * 50)

    for entry in entries:
        tool_name = entry['tool_name']
        tokens = entry['tokens_used']
        request_count = entry.get('request_count', 0)

        print(f"\n[{tool_name.upper()}]")
        print(f"  Tokens: {utils.format_tokens(tokens)} ({tokens:,})")
        if request_count > 0:
            print(f"  Requests: {request_count:,}")
        if entry.get('models_used'):
            print(f"  Models: {', '.join(entry['models_used'])}")


def cmd_top(
    tool: Optional[str] = None,
    days: int = 7,
    host: Optional[str] = None
) -> None:
    """Show top usage for the last N days."""
    entries = []
    if tool:
        entries = db.get_usage_by_tool(tool, days, host_name=host)
    else:
        all_tools = db.get_all_tools()
        for t in all_tools:
            entries.extend(db.get_usage_by_tool(t, days, host_name=host))

    if not entries:
        print("No usage data found")
        return

    # Aggregate by tool
    tool_totals = defaultdict(int)
    tool_requests = defaultdict(int)
    for entry in entries:
        tool_totals[entry['tool_name']] += entry['tokens_used']
        tool_requests[entry['tool_name']] += entry.get('request_count', 0)

    print(f"Usage for the last {days} days")
    print("=" * 50)

    # Sort by total tokens
    sorted_tools = sorted(tool_totals.items(), key=lambda x: x[1], reverse=True)

    for tool_name, total in sorted_tools:
        print(f"{tool_name.upper()}: {utils.format_tokens(total)} ({total:,})")
        req_count = tool_requests[tool_name]
        if req_count > 0:
            print(f"  Requests: {req_count:,}")


def cmd_report() -> None:
    """Generate and send email report."""
    import datetime as dt

    config = utils.load_config()

    # Get today's date, or yesterday if running before a certain hour (e.g., 8 AM)
    # This ensures cron jobs running in the early morning still get the previous day's data
    current_hour = dt.datetime.now().hour
    reference_date = utils.get_today()
    if current_hour < 8:  # Before 8 AM, use yesterday's data
        reference_date = utils.get_days_ago(1)

    # Get summary (still show all-time summary)
    summary = db.get_summary_by_tool()

    # Get only today's (or yesterday's) daily data
    daily_data = []
    all_tools = db.get_all_tools()
    for tool in all_tools:
        daily_data.extend(db.get_usage_by_date(reference_date, tool))

    # Format email body
    body = email_notifier.format_report_email(summary, daily_data, report_date=reference_date)

    # Check email config
    email_config = config.get('email', {})
    if not email_config:
        print("Error: Email configuration not found")
        print("Please create config.json with email settings")
        return

    to_email = email_config.get('to_email')
    if not to_email:
        print("Error: to_email not configured")
        return

    # Test connection first
    if not email_notifier.test_email_config(email_config):
        print("Email server connection failed. Check your configuration.")
        return

    # Send email (HTML format)
    subject_date = reference_date
    success = email_notifier.send_email(
        subject=f"AI Token Usage Report - {subject_date}",
        body=body,
        smtp_config=email_config,
        to_email=to_email,
        is_html=True
    )

    if success:
        print(f"Report sent to {to_email}")
    else:
        print("Failed to send report")


def cmd_config(action: str) -> None:
    """Handle configuration management."""
    import os
    import json

    config_dir = CONFIG_DIR
    config_path = CONFIG_PATH

    if action == 'show':
        config = utils.load_config()
        if not config:
            print("No configuration found.")
            print(f"Create one at: {config_path}")
            return
        print(f"Configuration from: {config_path}")
        print(json.dumps(config, indent=2))

    elif action == 'init':
        # Create config directory if it doesn't exist
        os.makedirs(config_dir, exist_ok=True)

        if os.path.exists(config_path):
            print(f"Configuration already exists at: {config_path}")
            response = input("Overwrite? (y/N): ")
            if response.lower() != 'y':
                print("Cancelled.")
                return

        # Copy sample config
        sample_path = os.path.join(script_dir, "config", "settings.json.sample")
        if os.path.exists(sample_path):
            with open(sample_path, 'r') as src:
                config = json.load(src)
            with open(config_path, 'w') as dst:
                json.dump(config, dst, indent=2)
            print(f"Configuration created at: {config_path}")
            print("Please edit the file with your settings.")
        else:
            # Create default config
            default_config = {
                "email": {
                    "smtp_server": "smtp.gmail.com",
                    "smtp_port": 587,
                    "smtp_username": "",
                    "smtp_password": "",
                    "from_email": "",
                    "to_email": "",
                    "use_tls": True
                },
                "tools": {
                    "openclaw": {"enabled": True},
                    "claude": {"enabled": True},
                    "qwen": {"enabled": True}
                },
                "cron": {
                    "enabled": True,
                    "run_time": "00:30"
                }
            }
            with open(config_path, 'w') as f:
                json.dump(default_config, f, indent=2)
            print(f"Default configuration created at: {config_path}")

    elif action == 'edit':
        config = utils.load_config()
        if not config:
            print("No configuration found. Running 'init' first...")
            cmd_config('init')
            return

        editor = os.environ.get('EDITOR', 'nano')
        print(f"Opening {config_path} with {editor}...")
        os.system(f"{editor} {config_path}")


def cmd_summary(host: Optional[str] = None) -> None:
    """Show a summary of all data."""
    summary = db.get_summary_by_tool(host_name=host)

    if not summary:
        print("No usage data available")
        if host:
            print(f"Host: {host}")
        return

    print("AI Token Usage Summary")
    print("=" * 60)

    for tool, stats in sorted(summary.items(), key=lambda x: x[1]['total_tokens'], reverse=True):
        print(f"\n{tool.upper()}")
        print(f"  Days tracked:   {stats['days_count']}")
        print(f"  Total tokens:   {utils.format_tokens(stats['total_tokens'])} ({stats['total_tokens']:,})")
        print(f"  Average/day:    {utils.format_tokens(int(stats['avg_tokens']))} ({int(stats['avg_tokens']):,})")
        if stats.get('total_requests'):
            print(f"  Total requests: {stats['total_requests']:,}")
            print(f"  Avg requests/day: {int(stats.get('avg_requests', 0))}")
        print(f"  Date range:     {stats['first_date']} to {stats['last_date']}")


def main():
    parser = argparse.ArgumentParser(
        description='AI Token Usage CLI',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # today command
    today_parser = subparsers.add_parser('today', help='Show usage for today')
    today_parser.add_argument('--tool', help='Filter by tool')
    today_parser.add_argument('--host', help='Filter by host')

    # query command
    query_parser = subparsers.add_parser('query', help='Query usage by date')
    query_parser.add_argument('date', help='Date in YYYY-MM-DD format')
    query_parser.add_argument('--tool', help='Filter by tool')
    query_parser.add_argument('--host', help='Filter by host')

    # top command
    top_parser = subparsers.add_parser('top', help='Show top usage')
    top_parser.add_argument('--tool', help='Filter by tool')
    top_parser.add_argument('--days', type=int, default=7, help='Number of days')
    top_parser.add_argument('--host', help='Filter by host')

    # report command
    report_parser = subparsers.add_parser('report', help='Generate report')
    report_parser.add_argument('type', nargs='?', default='email', choices=['email'], help='Report type (default: email)')

    # summary command
    summary_parser = subparsers.add_parser('summary', help='Show summary')
    summary_parser.add_argument('--host', help='Filter by host')

    # config command
    config_parser = subparsers.add_parser('config', help='Configuration management')
    config_parser.add_argument('action', choices=['show', 'edit', 'init'], help='Action: show, edit, or init')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Initialize database
    db.init_database()

    if args.command == 'today':
        cmd_today(args.tool, args.host)
    elif args.command == 'query':
        cmd_query(args.date, args.tool, args.host)
    elif args.command == 'top':
        cmd_top(args.tool, args.days, args.host)
    elif args.command == 'report':
        cmd_report()
    elif args.command == 'summary':
        cmd_summary(args.host)
    elif args.command == 'config':
        cmd_config(args.action)


if __name__ == "__main__":
    main()
