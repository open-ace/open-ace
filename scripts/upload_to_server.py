#!/usr/bin/env python3
"""
AI Token Usage - Upload to Server

Uploads token usage data to central server.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Add shared modules
script_dir = os.path.dirname(os.path.abspath(__file__))
shared_dir = os.path.join(script_dir, 'shared')
if shared_dir not in sys.path:
    sys.path.insert(0, shared_dir)

from db import get_connection, get_usage_by_date, get_messages_by_date
import utils


def upload_data(server_url: str, auth_key: str, hostname: str, days: int = 1, incremental: bool = True):
    """Upload usage data to central server."""
    import requests
    
    print(f"Uploading {days} days of data to {server_url}")
    print(f"Hostname: {hostname}")
    
    # Get marker file for incremental uploads
    marker_file = Path.home() / ".open-ace" / "upload_marker.json"
    last_upload = None
    
    if incremental and marker_file.exists():
        try:
            with open(marker_file) as f:
                marker = json.load(f)
            last_upload = marker.get(hostname, {}).get('last_upload')
        except:
            pass
    
    # Prepare data
    upload_data = {
        'host_name': hostname,
        'usage': [],
        'messages': []
    }
    
    # Get usage data for each day
    for i in range(days):
        date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
        usage_list = get_usage_by_date(date)
        if usage_list and isinstance(usage_list, list):
            for item in usage_list:
                upload_data['usage'].append({
                    'date': date,
                    'tool_name': item.get('tool_name'),
                    'tokens_used': item.get('tokens_used', 0),
                    'input_tokens': item.get('input_tokens', 0),
                    'output_tokens': item.get('output_tokens', 0),
                    'cache_tokens': item.get('cache_tokens', 0),
                    'request_count': item.get('request_count', 0)
                })
    
    # Get messages
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get messages from last upload time (for incremental) or all messages for specified days
    date_conditions = []
    params = []
    for i in range(days):
        date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
        date_conditions.append("date = ?")
        params.append(date)
    
    date_filter = " OR ".join(date_conditions)
    query = f"SELECT * FROM daily_messages WHERE ({date_filter}) AND host_name = ?"
    params.append(hostname)
    
    cursor.execute(query, params)
    columns = [desc[0] for desc in cursor.description]
    
    for row in cursor.fetchall():
        message = dict(zip(columns, row))
        upload_data['messages'].append({
            'date': message.get('date'),
            'tool_name': message.get('tool_name'),
            'message_id': message.get('message_id'),
            'parent_id': message.get('parent_id'),
            'role': message.get('role'),
            'content': message.get('content'),
            'tokens_used': message.get('tokens_used', 0),
            'input_tokens': message.get('input_tokens', 0),
            'output_tokens': message.get('output_tokens', 0),
            'model': message.get('model'),
            'timestamp': message.get('timestamp'),
            'sender_id': message.get('sender_id'),
            'sender_name': message.get('sender_name'),
            'message_source': message.get('message_source'),
            'conversation_label': message.get('conversation_label'),
            'group_subject': message.get('group_subject'),
            'is_group_chat': message.get('is_group_chat')
        })
    
    conn.close()
    
    print(f"Uploading {len(upload_data['usage'])} usage records and {len(upload_data['messages'])} messages")
    
    # Upload to server
    upload_url = f"{server_url.rstrip('/')}/api/upload/batch"
    headers = {
        'X-Auth-Key': auth_key,
        'Content-Type': 'application/json'
    }
    
    try:
        response = requests.post(upload_url, json=upload_data, headers=headers, timeout=60)
        response.raise_for_status()
        
        result = response.json()
        print(f"Upload successful!")
        print(f"  Usage records saved: {result.get('usage_records_saved', 0)}")
        print(f"  Messages saved: {result.get('messages_records_saved', 0)}")
        
        # Update marker file
        if incremental:
            marker_file.parent.mkdir(parents=True, exist_ok=True)
            marker = {}
            if marker_file.exists():
                try:
                    with open(marker_file) as f:
                        marker = json.load(f)
                except:
                    pass
            
            marker[hostname] = {
                'last_upload': datetime.now().isoformat(),
                'timestamp': datetime.now().isoformat()
            }
            
            with open(marker_file, 'w') as f:
                json.dump(marker, f, indent=2)
        
        return True
        
    except requests.exceptions.RequestException as e:
        print(f"Upload failed: {e}")
        return False


def run_daemon(server_url: str, auth_key: str, hostname: str, interval: int = 30, days: int = 1):
    """Run as daemon, uploading data at specified interval."""
    print(f"Starting upload daemon (interval: {interval}s)")
    
    while True:
        try:
            upload_data(server_url, auth_key, hostname, days=days, incremental=True)
        except Exception as e:
            print(f"Error: {e}")
        
        time.sleep(interval)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Upload token usage data to central server')
    parser.add_argument('--server', required=True, help='Server URL')
    parser.add_argument('--auth-key', required=True, help='Authentication key')
    parser.add_argument('--hostname', default=os.uname().nodename, help='Hostname')
    parser.add_argument('--days', type=int, default=1, help='Number of days to upload')
    parser.add_argument('--daemon', action='store_true', help='Run as daemon')
    parser.add_argument('--interval', type=int, default=30, help='Upload interval in seconds (daemon mode)')
    parser.add_argument('--incremental', action='store_true', default=True, help='Only upload new data')
    
    args = parser.parse_args()
    
    if args.daemon:
        run_daemon(args.server, args.auth_key, args.hostname, args.interval, args.days)
    else:
        upload_data(args.server, args.auth_key, args.hostname, args.days, args.incremental)
