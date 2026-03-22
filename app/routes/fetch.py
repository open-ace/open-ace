#!/usr/bin/env python3
"""
Open ACE - Fetch Routes

API routes for data fetching operations.
"""

import logging
import os
from datetime import datetime

from flask import Blueprint, jsonify

from app.repositories.database import DB_PATH
from app.services.message_service import MessageService
from app.services.usage_service import UsageService
from app.utils.helpers import get_today

fetch_bp = Blueprint('fetch', __name__)
usage_service = UsageService()
message_service = MessageService()
logger = logging.getLogger(__name__)


@fetch_bp.route('/fetch')
def api_fetch():
    """Fetch data from local sources."""
    # This would integrate with the existing fetch scripts
    return jsonify({
        'success': True,
        'message': 'Fetch endpoint - integrate with existing fetch scripts'
    })


@fetch_bp.route('/fetch/remote')
def api_fetch_remote():
    """Fetch data from remote sources."""
    # This would integrate with the existing remote fetch functionality
    return jsonify({
        'success': True,
        'message': 'Remote fetch endpoint - integrate with existing remote fetch'
    })


@fetch_bp.route('/data-status')
def api_data_status():
    """Get data status information."""
    try:
        # Check database exists
        db_exists = os.path.exists(DB_PATH)

        # Get last update time
        last_update = None
        if db_exists:
            last_update = datetime.fromtimestamp(os.path.getmtime(DB_PATH)).isoformat()

        # Get data counts
        from app.repositories.message_repo import MessageRepository
        from app.repositories.usage_repo import UsageRepository

        usage_repo = UsageRepository()
        message_repo = MessageRepository()

        tools = usage_repo.get_all_tools()
        hosts = usage_repo.get_all_hosts()
        senders = message_repo.get_all_senders()

        # Get date range
        today = get_today()

        return jsonify({
            'status': 'ok',
            'database_exists': db_exists,
            'last_update': last_update,
            'tools_count': len(tools),
            'hosts_count': len(hosts),
            'senders_count': len(senders),
            'tools': tools[:10],  # First 10 tools
            'hosts': hosts[:10],  # First 10 hosts
            'date': today
        })
    except Exception as e:
        logger.exception("Error getting data status")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500
