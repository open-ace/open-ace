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

# Add shared_dir to path first (so config can be imported)
if shared_dir not in sys.path:
    sys.path.insert(0, shared_dir)

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
    host = request.args.get('host')
    tool = request.args.get('tool')

    # Get summary filtered by host if specified
    summary = db.get_summary_by_tool(host_name=host) if host else db.get_summary_by_tool()

    # Get all hosts for dropdown
    all_hosts = db.get_all_hosts()
    if host and host not in all_hosts:
        all_hosts.insert(0, host)

    # Get all tools for dropdown
    all_tools = db.get_all_tools()

    today = utils.get_today()
    response = make_response(render_template(
        'index.html',
        summary=summary,
        today=today,
        hosts=all_hosts,
        tools=all_tools,
        selected_host=host,
        selected_tool=tool
    ))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route('/api/summary')
def api_summary():
    """Get summary statistics for all tools."""
    host = request.args.get('host')
    summary = db.get_summary_by_tool(host_name=host) if host else db.get_summary_by_tool()
    return jsonify(summary)


@app.route('/api/today')
def api_today():
    """Get today's usage for all tools."""
    today = utils.get_today()
    host = request.args.get('host')
    tool = request.args.get('tool')
    entries = db.get_usage_by_date(today, tool_name=tool, host_name=host)
    return jsonify(entries)


@app.route('/api/tool/<tool_name>/<int:days>')
def api_tool_usage(tool_name, days):
    """Get usage for a specific tool over N days."""
    host = request.args.get('host')
    entries = db.get_usage_by_tool(tool_name, days, host_name=host)
    return jsonify(entries)


@app.route('/api/date/<date_str>')
def api_date_usage(date_str):
    """Get usage for a specific date."""
    host = request.args.get('host')
    tool = request.args.get('tool')
    entries = db.get_usage_by_date(date_str, tool_name=tool, host_name=host)
    return jsonify(entries)


@app.route('/api/range')
def api_range_usage():
    """Get usage for a date range."""
    start_date = request.args.get('start', utils.get_days_ago(7))
    end_date = request.args.get('end', utils.get_today())
    tool = request.args.get('tool')
    host = request.args.get('host')

    entries = db.get_daily_range(start_date, end_date, tool, host_name=host)
    return jsonify(entries)


@app.route('/api/tools')
def api_tools():
    """Get list of all tools."""
    tools = db.get_all_tools()
    return jsonify(tools)


@app.route('/api/hosts')
def api_hosts():
    """Get list of all hosts (excluding default 'localhost')."""
    hosts = db.get_all_hosts()
    return jsonify(hosts)


@app.route('/api/senders')
def api_senders():
    """Get list of unique senders for a specific date."""
    date = request.args.get('date', utils.get_today())
    tool = request.args.get('tool')
    host = request.args.get('host')
    senders = db.get_unique_senders(date, tool_name=tool, host_name=host)
    return jsonify(senders)


@app.route('/api/upload/usage', methods=['POST'])
def api_upload_usage():
    """Accept usage data upload from remote machine.

    Expected JSON payload:
    {
        "host_name": "machine-name",
        "data": [
            {
                "date": "2024-01-15",
                "tool_name": "claude",
                "tokens_used": 1000,
                "input_tokens": 800,
                "output_tokens": 200,
                "cache_tokens": 0,
                "request_count": 5,
                "models_used": ["claude-3-opus"]
            }
        ]
    }
    """
    auth_key = request.headers.get('X-Auth-Key')
    if not auth_key:
        return jsonify({'error': 'Missing X-Auth-Key header'}), 401

    # Validate auth key (from config)
    config = utils.load_config()
    server_config = config.get('server', {})
    expected_key = server_config.get('upload_auth_key', '')

    if not expected_key:
        return jsonify({'error': 'Server upload not configured'}), 500

    if auth_key != expected_key:
        return jsonify({'error': 'Invalid authentication key'}), 403

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Missing JSON body'}), 400

    host_name = data.get('host_name', 'localhost')
    usage_data = data.get('data', [])

    if not isinstance(usage_data, list):
        return jsonify({'error': 'data must be a list'}), 400

    if len(usage_data) == 0:
        return jsonify({'error': 'No usage data provided'}), 400

    inserted = 0
    updated = 0

    for entry in usage_data:
        required_fields = ['date', 'tool_name', 'tokens_used']
        if not all(field in entry for field in required_fields):
            continue

        result = db.save_usage(
            date=entry['date'],
            tool_name=entry['tool_name'],
            host_name=host_name,
            tokens_used=entry.get('tokens_used', 0),
            input_tokens=entry.get('input_tokens', 0),
            output_tokens=entry.get('output_tokens', 0),
            cache_tokens=entry.get('cache_tokens', 0),
            request_count=entry.get('request_count', 0),
            models_used=entry.get('models_used')
        )

        if result:
            inserted += 1

    return jsonify({
        'success': True,
        'host_name': host_name,
        'records_processed': len(usage_data),
        'records_saved': inserted
    })


@app.route('/api/upload/messages', methods=['POST'])
def api_upload_messages():
    """Accept messages data upload from remote machine.

    Expected JSON payload:
    {
        "host_name": "machine-name",
        "data": [
            {
                "date": "2024-01-15",
                "tool_name": "claude",
                "message_id": "msg-123",
                "role": "user",
                "content": "Hello",
                "tokens_used": 10,
                "input_tokens": 8,
                "output_tokens": 2,
                "model": "claude-3-opus",
                "timestamp": "2024-01-15T10:00:00Z",
                "parent_id": null
            }
        ]
    }
    """
    auth_key = request.headers.get('X-Auth-Key')
    if not auth_key:
        return jsonify({'error': 'Missing X-Auth-Key header'}), 401

    # Validate auth key (from config)
    config = utils.load_config()
    server_config = config.get('server', {})
    expected_key = server_config.get('upload_auth_key', '')

    if not expected_key:
        return jsonify({'error': 'Server upload not configured'}), 500

    if auth_key != expected_key:
        return jsonify({'error': 'Invalid authentication key'}), 403

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Missing JSON body'}), 400

    host_name = data.get('host_name', 'localhost')
    messages_data = data.get('data', [])

    if not isinstance(messages_data, list):
        return jsonify({'error': 'data must be a list'}), 400

    if len(messages_data) == 0:
        return jsonify({'success': True, 'message': 'No messages to upload'}), 200

    inserted = 0

    for entry in messages_data:
        required_fields = ['date', 'tool_name', 'message_id', 'role', 'content']
        if not all(field in entry for field in required_fields):
            continue

        result = db.save_message(
            date=entry['date'],
            tool_name=entry['tool_name'],
            message_id=entry['message_id'],
            host_name=host_name,
            role=entry['role'],
            content=entry['content'],
            full_entry=entry.get('full_entry'),
            tokens_used=entry.get('tokens_used', 0),
            input_tokens=entry.get('input_tokens', 0),
            output_tokens=entry.get('output_tokens', 0),
            model=entry.get('model'),
            timestamp=entry.get('timestamp'),
            parent_id=entry.get('parent_id'),
            sender_id=entry.get('sender_id'),
            sender_name=entry.get('sender_name'),
            message_source=entry.get('message_source'),
            conversation_label=entry.get('conversation_label'),
            group_subject=entry.get('group_subject'),
            is_group_chat=entry.get('is_group_chat')
        )

        if result:
            inserted += 1

    return jsonify({
        'success': True,
        'host_name': host_name,
        'records_processed': len(messages_data),
        'records_saved': inserted
    })


@app.route('/api/upload/batch', methods=['POST'])
def api_upload_batch():
    """Accept batch upload of both usage and messages data.

    Expected JSON payload:
    {
        "host_name": "machine-name",
        "auth_key": "optional-in-body-if-not-in-header",
        "usage": [...],
        "messages": [...]
    }
    """
    import werkzeug
    
    # Support auth key in header or body
    auth_key = request.headers.get('X-Auth-Key')
    
    # Try to parse JSON with better error handling
    try:
        data = request.get_json(force=True, silent=False)
    except werkzeug.exceptions.BadRequest as e:
        # JSON parsing failed - return detailed error
        return jsonify({
            'error': 'Invalid JSON in request body',
            'details': str(e),
            'content_length': request.content_length,
            'host_name': request.headers.get('X-Forwarded-For', request.remote_addr)
        }), 400
    
    if data and not auth_key:
        auth_key = data.get('auth_key')

    if not auth_key:
        return jsonify({'error': 'Missing X-Auth-Key header or auth_key in body'}), 401

    # Validate auth key (from config)
    config = utils.load_config()
    server_config = config.get('server', {})
    expected_key = server_config.get('upload_auth_key', '')

    if not expected_key:
        return jsonify({'error': 'Server upload not configured'}), 500

    if auth_key != expected_key:
        return jsonify({'error': 'Invalid authentication key'}), 403

    if not data:
        return jsonify({'error': 'Missing JSON body'}), 400

    host_name = data.get('host_name', 'localhost')
    usage_data = data.get('usage', [])
    messages_data = data.get('messages', [])

    usage_saved = 0
    messages_saved = 0

    # Process usage data
    if isinstance(usage_data, list):
        for entry in usage_data:
            required_fields = ['date', 'tool_name', 'tokens_used']
            if not all(field in entry for field in required_fields):
                continue
            if db.save_usage(
                date=entry['date'],
                tool_name=entry['tool_name'],
                host_name=host_name,
                tokens_used=entry.get('tokens_used', 0),
                input_tokens=entry.get('input_tokens', 0),
                output_tokens=entry.get('output_tokens', 0),
                cache_tokens=entry.get('cache_tokens', 0),
                request_count=entry.get('request_count', 0),
                models_used=entry.get('models_used')
            ):
                usage_saved += 1

    # Process messages data
    if isinstance(messages_data, list):
        for entry in messages_data:
            required_fields = ['date', 'tool_name', 'message_id', 'role', 'content']
            if not all(field in entry for field in required_fields):
                continue
            if db.save_message(
                date=entry['date'],
                tool_name=entry['tool_name'],
                message_id=entry['message_id'],
                host_name=host_name,
                role=entry['role'],
                content=entry['content'],
                full_entry=entry.get('full_entry'),
                tokens_used=entry.get('tokens_used', 0),
                input_tokens=entry.get('input_tokens', 0),
                output_tokens=entry.get('output_tokens', 0),
                model=entry.get('model'),
                timestamp=entry.get('timestamp'),
                parent_id=entry.get('parent_id'),
                sender_id=entry.get('sender_id'),
                sender_name=entry.get('sender_name'),
                message_source=entry.get('message_source'),
                conversation_label=entry.get('conversation_label'),
                group_subject=entry.get('group_subject'),
                is_group_chat=entry.get('is_group_chat')
            ):
                messages_saved += 1

    return jsonify({
        'success': True,
        'host_name': host_name,
        'usage_records_saved': usage_saved,
        'messages_records_saved': messages_saved
    })


@app.route('/api/fetch')
def api_fetch():
    """Trigger data fetch for all tools."""
    import subprocess

    results = {}

    # Fetch OpenClaw data (including messages)
    try:
        result = subprocess.run(
            ['python3', 'scripts/fetch_openclaw.py', '--days', '7'],
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
    """Get messages with filters for date, tool, role, host, and sender."""
    # Query parameters
    date = request.args.get('date', utils.get_today())
    tool = request.args.get('tool')
    host = request.args.get('host')
    sender = request.args.get('sender')
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
        host_name=host,
        sender=sender,
        roles=roles,
        search=search,
        page=page,
        limit=limit
    )

    # Format timestamps to CST for consistent display
    for msg in result.get('messages', []):
        if msg.get('timestamp'):
            msg['timestamp_cst'] = db.format_timestamp_to_cst(msg['timestamp'])

    return jsonify(result)


if __name__ == '__main__':
    # Initialize database
    db.init_database()

    # Run the Flask app
    app.run(host='0.0.0.0', port=5001, debug=True)
