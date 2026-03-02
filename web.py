#!/usr/bin/env python3
"""
AI Token Usage - Flask Web Application

A web interface for visualizing AI token usage data from OpenClaw, Claude, and Qwen.
"""

import os
import sys
import importlib.util
import json
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request, send_from_directory, make_response

# Dynamically load shared modules
script_dir = os.path.dirname(os.path.abspath(__file__))
shared_dir = os.path.join(script_dir, 'scripts', 'shared')

# Load db module
db_path = os.path.join(shared_dir, 'db.py')
spec_db = importlib.util.spec_from_file_location('db', db_path)
db = importlib.util.module_from_spec(spec_db)
spec_db.loader.exec_module(db)

# Load utils module
utils_path = os.path.join(shared_dir, 'utils.py')
spec_utils = importlib.util.spec_from_file_location('utils', utils_path)
utils = importlib.util.module_from_spec(spec_utils)
spec_utils.loader.exec_module(utils)

app = Flask(__name__, static_folder='static', template_folder='templates')


@app.route('/')
def index():
    """Render the main dashboard page."""
    summary = db.get_summary_by_tool()
    today = utils.get_today()
    response = make_response(render_template('index.html', summary=summary, today=today))
    # Add headers to prevent caching
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route('/api/summary')
def api_summary():
    """Get summary statistics for all tools."""
    summary = db.get_summary_by_tool()
    return jsonify(summary)


@app.route('/api/today')
def api_today():
    """Get today's usage for all tools."""
    today = utils.get_today()
    entries = db.get_usage_by_date(today)
    return jsonify(entries)


@app.route('/api/tool/<tool_name>/<int:days>')
def api_tool_usage(tool_name, days):
    """Get usage for a specific tool over N days."""
    entries = db.get_usage_by_tool(tool_name, days)
    return jsonify(entries)


@app.route('/api/date/<date_str>')
def api_date_usage(date_str):
    """Get usage for a specific date."""
    entries = db.get_usage_by_date(date_str)
    return jsonify(entries)


@app.route('/api/range')
def api_range_usage():
    """Get usage for a date range."""
    start_date = request.args.get('start', utils.get_days_ago(7))
    end_date = request.args.get('end', utils.get_today())
    tool = request.args.get('tool')

    entries = db.get_daily_range(start_date, end_date, tool)
    return jsonify(entries)


@app.route('/api/tools')
def api_tools():
    """Get list of all tools."""
    tools = db.get_all_tools()
    return jsonify(tools)


@app.route('/api/fetch')
def api_fetch():
    """Trigger data fetch for all tools."""
    import subprocess

    results = {}

    # Fetch OpenClaw data (including messages)
    try:
        result = subprocess.run(
            ['python3', 'scripts/fetch_openclaw_messages.py', '--days', '7'],
            capture_output=True,
            text=True,
            timeout=120
        )
        results['openclaw'] = {
            'success': result.returncode == 0,
            'stdout': result.stdout,
            'stderr': result.stderr
        }
    except Exception as e:
        results['openclaw'] = {
            'success': False,
            'error': str(e)
        }

    # Fetch Claude data
    try:
        result = subprocess.run(
            ['python3', 'scripts/fetch_claude.py', '--days', '7'],
            capture_output=True,
            text=True,
            timeout=120
        )
        results['claude'] = {
            'success': result.returncode == 0,
            'stdout': result.stdout,
            'stderr': result.stderr
        }
    except Exception as e:
        results['claude'] = {
            'success': False,
            'error': str(e)
        }

    # Fetch Qwen data
    try:
        result = subprocess.run(
            ['python3', 'scripts/fetch_qwen.py', '--days', '7'],
            capture_output=True,
            text=True,
            timeout=120
        )
        results['qwen'] = {
            'success': result.returncode == 0,
            'stdout': result.stdout,
            'stderr': result.stderr
        }
    except Exception as e:
        results['qwen'] = {
            'success': False,
            'error': str(e)
        }

    return jsonify(results)


@app.route('/api/messages')
def api_messages():
    """Get messages with filters for date, tool, and role."""
    # Query parameters
    date = request.args.get('date', utils.get_today())
    tool = request.args.get('tool')
    roles_param = request.args.get('roles')  # comma-separated list
    search = request.args.get('search')
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 50, type=int)

    # Parse roles from comma-separated string
    roles = roles_param.split(',') if roles_param else None

    # Get messages from database
    result = db.get_messages_by_date(
        date=date,
        tool_name=tool,
        roles=roles,
        search=search,
        page=page,
        limit=limit
    )

    return jsonify(result)


if __name__ == '__main__':
    # Initialize database
    db.init_database()

    # Run the Flask app
    app.run(host='0.0.0.0', port=5001, debug=True)
