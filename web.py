#!/usr/bin/env python3
"""
AI Token Usage - Flask Web Application

A web interface for visualizing AI token usage data from OpenClaw, Claude, and Qwen.
"""

import os
import sys
import importlib.util
import json
import secrets
import subprocess
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request, send_from_directory, make_response, redirect, session


def get_git_commit():
    """Get the current git commit hash and date for version display.
    
    Returns:
        str: Format "commit_hash (MM-DD HH:MM:SS)" or "unknown" if git info unavailable.
    """
    try:
        # Get commit hash
        hash_result = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        # Get commit date in MM-DD HH:MM:SS format
        date_result = subprocess.run(
            ['git', 'log', '-1', '--format=%cd', '--date=format:%m-%d %H:%M:%S'],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        if hash_result.returncode == 0 and date_result.returncode == 0:
            commit_hash = hash_result.stdout.strip()
            commit_date = date_result.stdout.strip()
            return f"{commit_hash} ({commit_date})"
    except Exception:
        pass
    return 'unknown'

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

# Load config module
config_path = os.path.join(shared_dir, 'config.py')
spec_config = importlib.util.spec_from_file_location('config', config_path)
config_module = importlib.util.module_from_spec(spec_config)
spec_config.loader.exec_module(config_module)

app = Flask(__name__, static_folder='static', template_folder='templates')


@app.route('/')
def index():
    """Render the main dashboard page."""
    # Check authentication - check both Authorization header and cookie
    auth_header = request.headers.get('Authorization')
    token = None

    if auth_header:
        token = auth_header.replace('Bearer ', '')

    # Also check cookie for session token
    if not token and 'session_token' in request.cookies:
        token = request.cookies.get('session_token')

    # Check if user is authenticated via session cookie or header
    is_authenticated = False
    user_role = 'user'

    if token:
        session_data = db.get_session_by_token(token)
        if session_data:
            is_authenticated = True
            user_role = session_data.get('role', 'user')

    # If not authenticated, show login page
    if not is_authenticated:
        return redirect('/login')

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

    # Get user info for display
    user_info = None
    if token:
        session_data = db.get_session_by_token(token)
        if session_data:
            user_info = {
                'id': session_data['id'],
                'username': session_data['username'],
                'email': session_data.get('email'),
                'role': session_data['role']
            }

    response = make_response(render_template(
        'index.html',
        summary=summary,
        today=today,
        hosts=all_hosts,
        tools=all_tools,
        selected_host=host,
        selected_tool=tool,
        user_info=user_info,
        is_authenticated=is_authenticated,
        is_admin=user_role == 'admin',
        git_commit=get_git_commit()
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
    """Get today's usage for all tools, merged by tool_name."""
    today = utils.get_today()
    host = request.args.get('host')
    tool = request.args.get('tool')
    entries = db.get_usage_by_date(today, tool_name=tool, host_name=host)

    # Merge entries by tool_name (combine all hosts)
    merged = {}
    for entry in entries:
        tool_name = entry.get('tool_name', 'unknown')
        if tool_name not in merged:
            merged[tool_name] = {
                'date': entry.get('date'),
                'tool_name': tool_name,
                'tokens_used': 0,
                'input_tokens': 0,
                'output_tokens': 0,
                'cache_tokens': 0,
                'request_count': 0,
                'models_used': [],
                'hosts': []
            }
        merged[tool_name]['tokens_used'] += entry.get('tokens_used', 0)
        merged[tool_name]['input_tokens'] += entry.get('input_tokens', 0)
        merged[tool_name]['output_tokens'] += entry.get('output_tokens', 0)
        merged[tool_name]['cache_tokens'] += entry.get('cache_tokens', 0)
        merged[tool_name]['request_count'] += entry.get('request_count', 0)
        if entry.get('models_used'):
            for m in entry.get('models_used', []):
                if m not in merged[tool_name]['models_used']:
                    merged[tool_name]['models_used'].append(m)
        host_name = entry.get('host_name', 'unknown')
        if host_name not in merged[tool_name]['hosts']:
            merged[tool_name]['hosts'].append(host_name)

    # Convert to list and clean up
    result = []
    for tool_name, data in merged.items():
        if not data['models_used']:
            data['models_used'] = None
        result.append(data)

    return jsonify(result)


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


# =============================================================================
# Analysis APIs - 深度分析 API 端点
# =============================================================================

@app.route('/api/analysis/key-metrics')
def api_analysis_key_metrics():
    """Get key metrics for dashboard."""
    start_date = request.args.get('start', utils.get_days_ago(7))
    end_date = request.args.get('end', utils.get_today())
    tool = request.args.get('tool')
    host = request.args.get('host')

    metrics = db.get_key_metrics(start_date, end_date=end_date, tool_name=tool, host_name=host)
    return jsonify(metrics)


@app.route('/api/analysis/hourly-usage')
def api_analysis_hourly_usage():
    """Get hourly usage statistics from daily_messages table."""
    start_date = request.args.get('start', utils.get_days_ago(7))
    end_date = request.args.get('end', utils.get_today())
    tool = request.args.get('tool')
    host = request.args.get('host')

    data = db.get_hourly_usage_from_messages(start_date, end_date, tool_name=tool, host_name=host)
    return jsonify(data)


@app.route('/api/analysis/daily-hourly-usage')
def api_analysis_daily_hourly_usage():
    """Get hourly usage statistics grouped by date (for heatmap with daily view)."""
    start_date = request.args.get('start', utils.get_days_ago(7))
    end_date = request.args.get('end', utils.get_today())
    tool = request.args.get('tool')
    host = request.args.get('host')

    data = db.get_daily_hourly_usage(start_date, end_date, tool_name=tool, host_name=host)
    return jsonify(data)


@app.route('/api/analysis/peak-usage')
def api_analysis_peak_usage():
    """Get peak usage periods."""
    start_date = request.args.get('start', utils.get_days_ago(7))
    end_date = request.args.get('end', utils.get_today())
    tool = request.args.get('tool')
    host = request.args.get('host')
    limit = request.args.get('limit', 10, type=int)
    
    data = db.get_peak_usage_periods(start_date, end_date, tool_name=tool, host_name=host, limit=limit)
    return jsonify(data)


@app.route('/api/analysis/user-ranking')
def api_analysis_user_ranking():
    """Get user activity ranking."""
    start_date = request.args.get('start', utils.get_days_ago(7))
    end_date = request.args.get('end', utils.get_today())
    tool = request.args.get('tool')
    host = request.args.get('host')
    limit = request.args.get('limit', 10, type=int)
    
    data = db.get_user_activity_ranking(start_date, end_date, limit=limit, tool_name=tool, host_name=host)
    return jsonify(data)


@app.route('/api/analysis/conversation-stats')
def api_analysis_conversation_stats():
    """Get conversation statistics."""
    start_date = request.args.get('start', utils.get_days_ago(7))
    end_date = request.args.get('end', utils.get_today())
    tool = request.args.get('tool')
    host = request.args.get('host')
    
    data = db.get_conversation_statistics(start_date, end_date, tool_name=tool, host_name=host)
    return jsonify(data)


@app.route('/api/analysis/user-segmentation')
def api_analysis_user_segmentation():
    """Get user segmentation by activity level."""
    start_date = request.args.get('start', utils.get_days_ago(30))
    end_date = request.args.get('end', utils.get_today())
    tool = request.args.get('tool')
    host = request.args.get('host')

    data = db.get_user_segmentation(start_date, end_date, tool_name=tool, host_name=host)
    return jsonify(data)


@app.route('/api/analysis/tool-comparison')
def api_analysis_tool_comparison():
    """Get comparison metrics for different tools."""
    start_date = request.args.get('start', utils.get_days_ago(30))
    end_date = request.args.get('end', utils.get_today())
    host = request.args.get('host')
    
    data = db.get_tool_comparison_metrics(start_date, end_date, host_name=host)
    return jsonify(data)


@app.route('/api/analysis/anomaly-detection')
def api_analysis_anomaly_detection():
    """Detect usage anomalies using statistical methods."""
    start_date = request.args.get('start', utils.get_days_ago(30))
    end_date = request.args.get('end', utils.get_today())
    tool = request.args.get('tool')
    host = request.args.get('host')
    threshold = request.args.get('threshold', 3.0, type=float)
    
    data = db.detect_usage_anomalies(start_date, end_date, tool_name=tool, host_name=host, threshold_std=threshold)
    return jsonify(data)


@app.route('/api/analysis/recommendations')
def api_analysis_recommendations():
    """Generate recommendations based on usage patterns."""
    start_date = request.args.get('start', utils.get_days_ago(7))
    end_date = request.args.get('end', utils.get_today())
    tool = request.args.get('tool')
    host = request.args.get('host')
    
    recommendations = []
    
    # Get tool comparison data
    tool_data = db.get_tool_comparison_metrics(start_date, end_date, host_name=host)
    
    # Get anomaly data
    anomalies = db.detect_usage_anomalies(start_date, end_date, tool_name=tool, host_name=host)
    
    # Get user segmentation
    user_seg = db.get_user_segmentation(utils.get_today(), tool_name=tool, host_name=host)
    
    # Generate recommendations based on data
    
    # 1. Check for high usage variance
    if len(anomalies) > 0:
        high_severity = [a for a in anomalies if a.get('severity') == 'high']
        if high_severity:
            recommendations.append({
                'id': 'rec_anomaly',
                'type': 'stability',
                'priority': 'high',
                'title': '检测到用量异常波动',
                'title_en': 'Usage Anomaly Detected',
                'description': f'发现 {len(high_severity)} 次严重用量波动，建议检查是否有异常使用模式',
                'description_en': f'Detected {len(high_severity)} severe usage fluctuations. Recommend checking for abnormal usage patterns.',
                'expected_benefit': '提高系统稳定性',
                'expected_benefit_en': 'Improve system stability',
                'implementation_difficulty': 'medium',
                'data': high_severity[:3]
            })
    
    # 2. Check user distribution
    if user_seg.get('high', 0) > user_seg.get('medium', 0) * 2:
        recommendations.append({
            'id': 'rec_user_distribution',
            'type': 'user_management',
            'priority': 'medium',
            'title': '用户分布不均衡',
            'title_en': 'Unbalanced User Distribution',
            'description': '少数高频用户占用大部分资源，建议优化配额管理',
            'description_en': 'Few high-frequency users consume most resources. Recommend optimizing quota management.',
            'expected_benefit': '更公平的资源分配',
            'expected_benefit_en': 'More fair resource distribution',
            'implementation_difficulty': 'easy',
            'data': user_seg
        })
    
    # 3. Tool usage optimization
    if tool_data:
        max_tokens_tool = max(tool_data, key=lambda x: x.get('total_tokens', 0))
        avg_tokens = sum(t.get('total_tokens', 0) for t in tool_data) / len(tool_data) if tool_data else 0
        
        if max_tokens_tool.get('total_tokens', 0) > avg_tokens * 3:
            recommendations.append({
                'id': 'rec_tool_balance',
                'type': 'cost_optimization',
                'priority': 'medium',
                'title': f'{max_tokens_tool["tool_name"]} 用量过高',
                'title_en': f'{max_tokens_tool["tool_name"]} High Usage',
                'description': f'{max_tokens_tool["tool_name"]} 用量远超其他工具，建议评估是否有更经济的替代方案',
                'description_en': f'{max_tokens_tool["tool_name"]} usage far exceeds other tools. Consider evaluating more cost-effective alternatives.',
                'expected_benefit': '降低成本',
                'expected_benefit_en': 'Reduce costs',
                'implementation_difficulty': 'medium',
                'data': max_tokens_tool
            })
    
    # 4. Cache usage recommendation
    for tool in tool_data:
        if tool.get('output_tokens', 0) > 0:
            cache_ratio = tool.get('input_tokens', 0) / max(tool.get('output_tokens', 1), 1)
            if cache_ratio < 2:  # Low cache ratio
                recommendations.append({
                    'id': f'rec_cache_{tool["tool_name"]}',
                    'type': 'performance',
                    'priority': 'low',
                    'title': f'{tool["tool_name"]} 缓存利用率低',
                    'title_en': f'{tool["tool_name"]} Low Cache Utilization',
                    'description': f'{tool["tool_name"]} 的输入/输出比例较低，建议增加缓存使用以提高性能',
                    'description_en': f'{tool["tool_name"]} has low input/output ratio. Recommend increasing cache usage for better performance.',
                    'expected_benefit': '提升响应速度，降低成本',
                    'expected_benefit_en': 'Improve response speed and reduce costs',
                    'implementation_difficulty': 'easy',
                    'data': {'tool': tool['tool_name'], 'cache_ratio': round(cache_ratio, 2)}
                })
    
    # Sort by priority
    priority_order = {'high': 0, 'medium': 1, 'low': 2}
    recommendations.sort(key=lambda x: priority_order.get(x['priority'], 3))

    return jsonify(recommendations)


# =============================================================================
# Session History APIs - 会话历史 API 端点
# =============================================================================

@app.route('/api/conversation-history')
def api_conversation_history():
    """Get session history with pagination and sorting.

    Query parameters:
        start: Start date (default: 7 days ago)
        end: End date (default: today)
        tool: Tool name filter (optional)
        host: Host name filter (optional)
        page: Page number (default: 1)
        limit: Results per page (default: 20)
        sort_by: Sort field (session_id, user, model, start_time, end_time,
                 user_messages, ai_messages, avg_latency)
        sort_order: Sort order (asc or desc, default: desc)
    """
    start_date = request.args.get('start', utils.get_days_ago(7))
    end_date = request.args.get('end', utils.get_today())
    tool = request.args.get('tool')
    host = request.args.get('host')
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    sort_by = request.args.get('sort_by', 'start_time')
    sort_order = request.args.get('sort_order', 'desc')

    data = db.get_conversation_history(
        start_date=start_date,
        end_date=end_date,
        tool_name=tool,
        host_name=host,
        page=page,
        limit=limit,
        sort_by=sort_by,
        sort_order=sort_order
    )
    return jsonify(data)


@app.route('/api/conversation-timeline/<path:session_id>')
def api_conversation_timeline(session_id):
    """Get timeline data for a specific conversation.

    Args:
        session_id: The session identifier (URL-encoded)

    Returns:
        JSON with timeline and latency_curve data for chart rendering
    """
    data = db.get_conversation_timeline(session_id)
    return jsonify(data)


@app.route('/api/conversation-details/<path:session_id>')
def api_conversation_details(session_id):
    """Get complete conversation details for a specific conversation.

    Args:
        session_id: The session identifier (URL-encoded)

    Returns:
        JSON with conversation info and list of messages with full content
    """
    data = db.get_conversation_details(session_id)
    return jsonify(data)


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


def _fetch_local_data():
    """Fetch data from local machine."""
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

    return results


def _fetch_single_host(host_info, config):
    """Fetch data from a single remote machine via SSH.

    Args:
        host_info: Dict with host configuration (name, host, user, base_dir)
        config: Full config dict for server settings

    Returns:
        Dict with host results
    """
    import subprocess

    host_name = host_info.get('name', 'unknown')
    host = host_info.get('host')
    user = host_info.get('user', 'openclaw')
    base_dir = host_info.get('base_dir', '/home/openclaw/ai-token-analyzer')

    if not host:
        return None

    host_results = {
        'host': host,
        'name': host_name,
        'fetch': {'success': False},
        'upload': {'success': False}
    }

    # Execute fetch on remote machine (timeout reduced to 30s)
    try:
        fetch_cmd = f"ssh -o ConnectTimeout=10 -o BatchMode=yes {user}@{host} 'cd {base_dir} && python3 scripts/fetch_openclaw.py --days 7'"
        result = subprocess.run(
            fetch_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30
        )
        host_results['fetch'] = {
            'success': result.returncode == 0,
            'stdout': result.stdout[-500:] if result.stdout else '',
            'stderr': result.stderr[-500:] if result.stderr else ''
        }
    except subprocess.TimeoutExpired:
        host_results['fetch'] = {'success': False, 'error': 'Timeout (30s)'}
    except Exception as e:
        host_results['fetch'] = {'success': False, 'error': str(e)}

    # Execute upload on remote machine (timeout reduced to 30s)
    server_config = config.get('server', {})
    default_server_url = f"http://localhost:{config_module.WEB_PORT}"
    server_url = server_config.get('server_url', default_server_url)
    auth_key = server_config.get('upload_auth_key', '')

    if auth_key:
        try:
            upload_cmd = f"ssh -o ConnectTimeout=10 -o BatchMode=yes {user}@{host} 'cd {base_dir} && python3 scripts/upload_to_server.py --server {server_url} --auth-key {auth_key} --hostname {host_name} --days 7'"
            result = subprocess.run(
                upload_cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30
            )
            host_results['upload'] = {
                'success': result.returncode == 0,
                'stdout': result.stdout[-500:] if result.stdout else '',
                'stderr': result.stderr[-500:] if result.stderr else ''
            }
        except subprocess.TimeoutExpired:
            host_results['upload'] = {'success': False, 'error': 'Timeout (30s)'}
        except Exception as e:
            host_results['upload'] = {'success': False, 'error': str(e)}

    return host_name, host_results


def _fetch_remote_data():
    """Fetch data from remote machines via SSH in parallel.

    Uses ThreadPoolExecutor to fetch from multiple hosts concurrently,
    reducing total wait time when some hosts are unreachable.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    config = utils.load_config()
    remote_config = config.get('remote', {})

    if not remote_config.get('enabled', False):
        return {'error': 'Remote fetch not enabled'}

    results = {}
    hosts = remote_config.get('hosts', [])

    if not hosts:
        return results

    # Fetch from all hosts in parallel
    with ThreadPoolExecutor(max_workers=len(hosts)) as executor:
        # Submit all tasks
        future_to_host = {
            executor.submit(_fetch_single_host, host_info, config): host_info.get('name', 'unknown')
            for host_info in hosts
        }

        # Collect results as they complete
        for future in as_completed(future_to_host):
            host_name = future_to_host[future]
            try:
                result = future.result()
                if result:
                    results[result[0]] = result[1]
            except Exception as e:
                results[host_name] = {
                    'host': host_name,
                    'name': host_name,
                    'fetch': {'success': False, 'error': str(e)},
                    'upload': {'success': False, 'error': str(e)}
                }

    return results


@app.route('/api/fetch')
def api_fetch():
    """Trigger data fetch for all tools.

    Query parameters:
        include_remote: If 'true', also fetch data from remote machines in background

    Note: Local data is returned immediately. Remote fetch runs in background
    and does not block the response.
    """
    include_remote = request.args.get('include_remote', 'false').lower() == 'true'

    if not include_remote:
        # Only fetch local data (fast path)
        return jsonify({
            'local': _fetch_local_data(),
            'remote': {}
        })

    # Fetch local data first (fast, should complete in 1-2 seconds)
    local_result = _fetch_local_data()

    # Start remote fetch in background thread (non-blocking)
    from threading import Thread
    remote_thread = Thread(target=_fetch_remote_data)
    remote_thread.daemon = True
    remote_thread.start()

    # Return local data immediately, remote data will be fetched in background
    return jsonify({
        'local': local_result,
        'remote': {'status': 'fetching_in_background'}
    })


@app.route('/api/fetch/remote')
def api_fetch_remote():
    """Trigger data fetch from remote machines only."""
    results = _fetch_remote_data()
    return jsonify(results)


@app.route('/api/data-status')
def api_data_status():
    """Get data status for all hosts (last update time, record counts)."""
    config = utils.load_config()
    remote_config = config.get('remote', {})
    local_host_name = config.get('host_name', 'localhost')

    status = {
        'hosts': [],
        'last_updated': None
    }

    # Get all hosts from database
    all_hosts = db.get_all_hosts_with_status()

    # Create a map of host_name -> host_data for quick lookup
    host_data_map = {h.get('host_name', 'unknown'): h for h in all_hosts}

    # Get configured remote hosts
    remote_host_names = set()
    if remote_config.get('enabled', False):
        hosts = remote_config.get('hosts', [])
        for host_info in hosts:
            remote_host_names.add(host_info.get('name', 'unknown'))

    # Track which hosts have been added
    added_hosts = set()

    # Add local host first
    local_host_data = host_data_map.get(local_host_name) or host_data_map.get('localhost')
    if local_host_data:
        status['hosts'].append({
            'name': local_host_name,
            'host_name': local_host_name,
            'is_remote': False,
            'is_local': True,
            'last_updated': local_host_data.get('last_updated'),
            'usage_records': local_host_data.get('usage_records', 0),
            'message_records': local_host_data.get('message_records', 0)
        })
        added_hosts.add(local_host_name)
        added_hosts.add('localhost')
    else:
        # Local host not in database yet
        status['hosts'].append({
            'name': local_host_name,
            'host_name': local_host_name,
            'is_remote': False,
            'is_local': True,
            'last_updated': None,
            'usage_records': 0,
            'message_records': 0
        })
        added_hosts.add(local_host_name)

    # Add configured remote hosts
    for host_name in remote_host_names:
        if host_name in added_hosts:
            continue
        host_data = host_data_map.get(host_name)
        if host_data:
            status['hosts'].append({
                'name': host_name,
                'host_name': host_name,
                'is_remote': True,
                'is_local': False,
                'last_updated': host_data.get('last_updated'),
                'usage_records': host_data.get('usage_records', 0),
                'message_records': host_data.get('message_records', 0)
            })
        else:
            # Remote host not in database yet
            status['hosts'].append({
                'name': host_name,
                'host_name': host_name,
                'is_remote': True,
                'is_local': False,
                'last_updated': None,
                'usage_records': 0,
                'message_records': 0
            })
        added_hosts.add(host_name)

    # Add any remaining hosts from database that haven't been added yet
    # (e.g., hosts that are no longer in config but still have data)
    for host_name, host_data in host_data_map.items():
        if host_name not in added_hosts:
            status['hosts'].append({
                'name': host_name,
                'host_name': host_name,
                'is_remote': False,  # Unknown, assume local
                'is_local': False,
                'last_updated': host_data.get('last_updated'),
                'usage_records': host_data.get('usage_records', 0),
                'message_records': host_data.get('message_records', 0)
            })

    # Find the most recent update time
    all_times = [h.get('last_updated') for h in status['hosts'] if h.get('last_updated')]
    if all_times:
        status['last_updated'] = max(all_times)

    return jsonify(status)


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


@app.route('/workspace')
def workspace():
    """Render the workspace page with Claude Code Web UI."""
    # Check authentication - check both Authorization header and cookie
    auth_header = request.headers.get('Authorization')
    token = None

    if auth_header:
        token = auth_header.replace('Bearer ', '')

    # Also check cookie for session token
    if not token and 'session_token' in request.cookies:
        token = request.cookies.get('session_token')

    # Check if user is authenticated via session cookie or header
    is_authenticated = False
    user_role = 'user'

    if token:
        session_data = db.get_session_by_token(token)
        if session_data:
            is_authenticated = True
            user_role = session_data.get('role', 'user')

    # If not authenticated, show login page
    if not is_authenticated:
        return redirect('/login')

    # Workspace is only for non-admin users
    if user_role == 'admin':
        return redirect('/')

    # Get user info for display
    user_info = None
    if token:
        session_data = db.get_session_by_token(token)
        if session_data:
            user_info = {
                'id': session_data['id'],
                'username': session_data['username'],
                'email': session_data.get('email'),
                'role': session_data['role']
            }

    response = make_response(render_template(
        'index.html',
        summary=[],
        today=utils.get_today(),
        hosts=[],
        tools=[],
        selected_host=None,
        selected_tool=None,
        user_info=user_info,
        is_authenticated=is_authenticated,
        is_admin=user_role == 'admin'
    ))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route('/login')
def login_page():
    """Render login page."""
    response = make_response(render_template('login.html'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route('/logout')
def logout_page():
    """Handle logout and redirect to login."""
    # Clear session by deleting token
    auth_header = request.headers.get('Authorization')
    if auth_header:
        token = auth_header.replace('Bearer ', '')
        db.delete_session(token)

    response = make_response(render_template('logout_success.html'))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response


# ==========================================
# Authentication & Admin API Routes
# ==========================================

@app.route('/api/auth/login', methods=['POST'])
def api_login():
    """User login endpoint."""
    import hashlib
    import secrets
    from datetime import timedelta

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Missing JSON body'}), 400

    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({'error': 'Username and password are required'}), 400

    # Verify password
    user = db.verify_password(username, password)

    if not user:
        return jsonify({'error': 'Invalid username or password'}), 401

    if user.get('is_active') != 1:
        return jsonify({'error': 'Account is not active'}), 403

    # Create session token
    session_token = secrets.token_urlsafe(32)

    # Set session expiry (7 days)
    expires_at = datetime.now() + timedelta(days=7)

    # Create session
    session_created = db.create_session(
        user_id=user['id'],
        session_token=session_token,
        expires_at=expires_at
    )

    if not session_created:
        return jsonify({'error': 'Failed to create session'}), 500

    # Return user info without sensitive data
    user_info = {
        'id': user['id'],
        'username': user['username'],
        'email': user.get('email'),
        'role': user['role'],
        'quota_tokens': user.get('quota_tokens', 0),
        'quota_requests': user.get('quota_requests', 0)
    }

    # Create response with cookie
    response = jsonify({
        'success': True,
        'user': user_info,
        'session_token': session_token
    })

    # Set cookie for automatic authentication on page reload
    response.set_cookie(
        'session_token',
        session_token,
        max_age=7 * 24 * 60 * 60,  # 7 days
        httponly=True,
        secure=False,  # Set to True in production with HTTPS
        samesite='Lax'
    )

    return response


@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    """User logout endpoint."""
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return jsonify({'error': 'Missing Authorization header'}), 400

    # Extract token from "Bearer <token>"
    token = auth_header.replace('Bearer ', '')

    # Delete session
    db.delete_session(token)

    return jsonify({'success': True})


@app.route('/api/auth/profile', methods=['GET'])
def api_profile():
    """Get current user profile."""
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return jsonify({'error': 'Missing Authorization header'}), 401

    # Extract token from "Bearer <token>"
    token = auth_header.replace('Bearer ', '')

    # Get session
    session = db.get_session_by_token(token)
    if not session:
        return jsonify({'error': 'Invalid or expired session'}), 401

    # Return user info without sensitive data
    user_info = {
        'id': session['id'],
        'username': session['username'],
        'email': session.get('email'),
        'role': session['role'],
        'quota_tokens': session.get('quota_tokens', 0),
        'quota_requests': session.get('quota_requests', 0)
    }

    return jsonify({'success': True, 'user': user_info})


# ==========================================
# Admin API Routes
# ==========================================

@app.route('/api/admin/users', methods=['GET'])
def api_admin_get_users():
    """Get all users (admin only)."""
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return jsonify({'error': 'Missing Authorization header'}), 401

    token = auth_header.replace('Bearer ', '')
    session = db.get_session_by_token(token)

    if not session:
        return jsonify({'error': 'Invalid or expired session'}), 401

    if session.get('role') != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    users = db.get_all_users()
    return jsonify({'success': True, 'users': users})


@app.route('/api/admin/users', methods=['POST'])
def api_admin_create_user():
    """Create a new user (admin only)."""
    import hashlib
    import secrets

    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return jsonify({'error': 'Missing Authorization header'}), 401

    token = auth_header.replace('Bearer ', '')
    session = db.get_session_by_token(token)

    if not session:
        return jsonify({'error': 'Invalid or expired session'}), 401

    if session.get('role') != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Missing JSON body'}), 400

    username = data.get('username')
    password = data.get('password')
    email = data.get('email')
    role = data.get('role', 'user')
    quota_tokens = data.get('quota_tokens', 1000000)
    quota_requests = data.get('quota_requests', 1000)
    is_active = data.get('is_active', 1)
    linux_account = data.get('linux_account')

    if not username or not password:
        return jsonify({'error': 'Username and password are required'}), 400

    # Hash password
    password_hash = hashlib.sha256(password.encode()).hexdigest()

    result = db.create_user_with_is_active(
        username=username,
        password_hash=password_hash,
        email=email,
        role=role,
        quota_tokens=quota_tokens,
        quota_requests=quota_requests,
        is_active=is_active,
        linux_account=linux_account
    )

    if result:
        return jsonify({'success': True, 'message': 'User created successfully'})
    else:
        return jsonify({'error': 'Failed to create user (may already exist)'}), 400


@app.route('/api/admin/users/<int:user_id>', methods=['PUT'])
def api_admin_update_user(user_id):
    """Update user information (admin only)."""
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return jsonify({'error': 'Missing Authorization header'}), 401

    token = auth_header.replace('Bearer ', '')
    session = db.get_session_by_token(token)

    if not session:
        return jsonify({'error': 'Invalid or expired session'}), 401

    if session.get('role') != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Missing JSON body'}), 400

    # Check if user exists
    user = db.get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    # Update allowed fields
    updates = {}
    if 'email' in data:
        updates['email'] = data['email']
    if 'role' in data and data['role'] in ['admin', 'user']:
        updates['role'] = data['role']
    if 'quota_tokens' in data:
        updates['quota_tokens'] = data['quota_tokens']
    if 'quota_requests' in data:
        updates['quota_requests'] = data['quota_requests']
    if 'is_active' in data:
        updates['is_active'] = 1 if data['is_active'] else 0
    if 'linux_account' in data:
        updates['linux_account'] = data['linux_account']

    if updates:
        db.update_user(user_id, **updates)
        return jsonify({'success': True, 'message': 'User updated successfully'})
    else:
        return jsonify({'error': 'No valid fields to update'}), 400


@app.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
def api_admin_delete_user(user_id):
    """Delete a user (admin only)."""
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return jsonify({'error': 'Missing Authorization header'}), 401

    token = auth_header.replace('Bearer ', '')
    session = db.get_session_by_token(token)

    if not session:
        return jsonify({'error': 'Invalid or expired session'}), 401

    if session.get('role') != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    # Check if user exists
    user = db.get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    db.delete_user(user_id)
    return jsonify({'success': True, 'message': 'User deleted successfully'})


@app.route('/api/admin/users/<int:user_id>/password', methods=['PUT'])
def api_admin_reset_password(user_id):
    """Reset user password (admin only)."""
    import hashlib
    import secrets

    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return jsonify({'error': 'Missing Authorization header'}), 401

    token = auth_header.replace('Bearer ', '')
    session = db.get_session_by_token(token)

    if not session:
        return jsonify({'error': 'Invalid or expired session'}), 401

    if session.get('role') != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    # Check if user exists
    user = db.get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Missing JSON body'}), 400

    password = data.get('password')

    # If no password provided, generate a random one
    if not password:
        password = secrets.token_urlsafe(12)

    # Hash password
    password_hash = hashlib.sha256(password.encode()).hexdigest()

    # Update password
    db.update_user_password(user_id, password_hash)

    return jsonify({
        'success': True,
        'message': 'Password reset successfully',
        'new_password': password  # Return the new password so admin can share with user
    })


@app.route('/api/admin/users/<int:user_id>/quota', methods=['PUT'])
def api_admin_set_quota(user_id):
    """Set user quota (admin only)."""
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return jsonify({'error': 'Missing Authorization header'}), 401

    token = auth_header.replace('Bearer ', '')
    session = db.get_session_by_token(token)

    if not session:
        return jsonify({'error': 'Invalid or expired session'}), 401

    if session.get('role') != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    # Check if user exists
    user = db.get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Missing JSON body'}), 400

    quota_tokens = data.get('quota_tokens')
    quota_requests = data.get('quota_requests')

    updates = {}
    if quota_tokens is not None:
        updates['quota_tokens'] = quota_tokens
    if quota_requests is not None:
        updates['quota_requests'] = quota_requests

    if updates:
        db.update_user(user_id, **updates)
        return jsonify({'success': True, 'message': 'Quota updated successfully'})
    else:
        return jsonify({'error': 'No quota values provided'}), 400


@app.route('/api/admin/quota/usage', methods=['GET'])
def api_admin_quota_usage():
    """Get quota usage statistics (admin only)."""
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return jsonify({'error': 'Missing Authorization header'}), 401

    token = auth_header.replace('Bearer ', '')
    session = db.get_session_by_token(token)

    if not session:
        return jsonify({'error': 'Invalid or expired session'}), 401

    if session.get('role') != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    start_date = request.args.get('start')
    end_date = request.args.get('end')

    if not start_date or not end_date:
        # Default to last 7 days
        from datetime import timedelta
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')

    # Get total usage across all users
    total_tokens = 0
    total_requests = 0
    active_users = 0

    users = db.get_all_users()
    for user in users:
        usage = db.get_total_quota_usage(
            user['id'],
            start_date,
            end_date
        )
        total_tokens += usage['total_tokens']
        total_requests += usage['total_requests']
        if usage['total_tokens'] > 0 or usage['total_requests'] > 0:
            active_users += 1

    return jsonify({
        'success': True,
        'total_tokens': total_tokens,
        'total_requests': total_requests,
        'active_users': active_users,
        'start_date': start_date,
        'end_date': end_date
    })


@app.route('/api/report/my-usage', methods=['GET'])
def api_report_my_usage():
    """Get current user's usage statistics."""
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return jsonify({'error': 'Missing Authorization header'}), 401

    token = auth_header.replace('Bearer ', '')
    session = db.get_session_by_token(token)

    if not session:
        return jsonify({'error': 'Invalid or expired session'}), 401

    user_id = session.get('user_id')  # Use 'user_id' instead of 'id' from joined tables

    if not user_id:
        return jsonify({'error': 'Invalid session: no user_id'}), 401

    start_date = request.args.get('start')
    end_date = request.args.get('end')

    if not start_date or not end_date:
        # Default to last 30 days
        from datetime import timedelta
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')

    # Get user's quota info
    user = db.get_user_by_id(user_id)

    if not user:
        return jsonify({'error': 'User not found'}), 404

    # Get usage summary
    usage_summary = db.get_total_quota_usage(user_id, start_date, end_date)
    usage_by_tool = db.get_quota_usage_by_tool(user_id, start_date, end_date)

    return jsonify({
        'success': True,
        'user': {
            'id': user['id'],
            'username': user['username'],
            'quota_tokens': user['quota_tokens'],
            'quota_requests': user['quota_requests']
        },
        'usage': {
            'start_date': start_date,
            'end_date': end_date,
            'total_tokens': usage_summary['total_tokens'],
            'total_requests': usage_summary['total_requests']
        },
        'usage_by_tool': usage_by_tool
    })


@app.route('/static/claude-code-webui/<path:filename>')
def serve_claude_code_webui(filename):
    """Serve static files from claude-code-webui."""
    directory = os.path.join(script_dir, 'static', 'claude-code-webui')
    return send_from_directory(directory, filename)


if __name__ == '__main__':
    # Initialize database (including auth tables)
    db.init_database()

    # Run the Flask app using configuration from config module
    app.run(host=config_module.WEB_HOST, port=config_module.WEB_PORT, debug=False)
