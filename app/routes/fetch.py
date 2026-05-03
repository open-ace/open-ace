#!/usr/bin/env python3
"""
Open ACE - Fetch Routes

API routes for data fetching operations.
"""

import logging
import os
import subprocess
import threading
from datetime import datetime

from flask import Blueprint, jsonify

from app.repositories.database import DB_PATH
from app.services.message_service import MessageService
from app.services.usage_service import UsageService
from app.utils.helpers import get_today

fetch_bp = Blueprint("fetch", __name__)
usage_service = UsageService()
message_service = MessageService()
logger = logging.getLogger(__name__)

# Global state for fetch status
_fetch_status = {"is_running": False, "last_run": None, "last_result": None, "error": None}
_fetch_lock = threading.Lock()


def run_fetch_scripts():
    """Run data fetch scripts in background."""
    global _fetch_status

    with _fetch_lock:
        if _fetch_status["is_running"]:
            return
        _fetch_status["is_running"] = True
        _fetch_status["error"] = None

    try:
        # Get project root directory
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        results = {}

        # Run fetch_qwen.py with sudo and --multi-user to scan all users' qwen directories
        # sudo is needed to read other users' .qwen directories
        # Pass --config with current user's config path so root uses correct database
        qwen_script = os.path.join(project_root, "scripts", "fetch_qwen.py")
        if os.path.exists(qwen_script):
            try:
                # Get config file path from current user's home directory
                config_path = os.path.expanduser("~/.open-ace/config.json")

                result = subprocess.run(
                    [
                        "sudo",
                        "-n",
                        "/usr/bin/python3",
                        qwen_script,
                        "--days",
                        "1",
                        "--multi-user",
                        "--config",
                        config_path,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300,  # 5 minutes timeout
                    cwd=project_root,
                )
                results["qwen"] = {
                    "success": result.returncode == 0,
                    "output": result.stdout[-1000:] if result.stdout else "",
                    "error": result.stderr[-500:] if result.stderr else None,
                }
            except subprocess.TimeoutExpired:
                results["qwen"] = {"success": False, "error": "Timeout after 5 minutes"}
            except Exception:
                results["qwen"] = {"success": False, "error": "Internal server error"}

        # Run fetch_claude.py with sudo and --multi-user to scan all users' Claude directories
        # sudo is needed to read other users' .claude directories
        # Pass --config with current user's config path so root uses correct database
        claude_script = os.path.join(project_root, "scripts", "fetch_claude.py")
        if os.path.exists(claude_script):
            try:
                result = subprocess.run(
                    [
                        "sudo",
                        "-n",
                        "/usr/bin/python3",
                        claude_script,
                        "--days",
                        "1",
                        "--multi-user",
                        "--config",
                        config_path,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    cwd=project_root,
                )
                results["claude"] = {
                    "success": result.returncode == 0,
                    "output": result.stdout[-1000:] if result.stdout else "",
                    "error": result.stderr[-500:] if result.stderr else None,
                }
            except subprocess.TimeoutExpired:
                results["claude"] = {"success": False, "error": "Timeout after 5 minutes"}
            except Exception:
                results["claude"] = {"success": False, "error": "Internal server error"}

        # Run fetch_openclaw.py with sudo and --multi-user to scan all users' OpenClaw directories
        # sudo is needed to read other users' .openclaw directories
        # Pass --config with current user's config path so root uses correct database
        openclaw_script = os.path.join(project_root, "scripts", "fetch_openclaw.py")
        if os.path.exists(openclaw_script):
            try:
                result = subprocess.run(
                    [
                        "sudo",
                        "-n",
                        "/usr/bin/python3",
                        openclaw_script,
                        "--days",
                        "1",
                        "--mode",
                        "messages",
                        "--multi-user",
                        "--config",
                        config_path,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    cwd=project_root,
                )
                results["openclaw"] = {
                    "success": result.returncode == 0,
                    "output": result.stdout[-1000:] if result.stdout else "",
                    "error": result.stderr[-500:] if result.stderr else None,
                }
            except subprocess.TimeoutExpired:
                results["openclaw"] = {"success": False, "error": "Timeout after 5 minutes"}
            except Exception:
                results["openclaw"] = {"success": False, "error": "Internal server error"}

        with _fetch_lock:
            _fetch_status["last_run"] = datetime.now().isoformat()
            _fetch_status["last_result"] = results
            _fetch_status["is_running"] = False

        logger.info(f"Data fetch completed: {results}")

    except Exception as e:
        logger.exception("Error running fetch scripts")
        with _fetch_lock:
            _fetch_status["error"] = str(e)
            _fetch_status["is_running"] = False


@fetch_bp.route("/fetch/data", methods=["POST"])
def api_fetch_data():
    """Trigger data collection from all sources."""
    global _fetch_status

    with _fetch_lock:
        if _fetch_status["is_running"]:
            return jsonify(
                {
                    "success": False,
                    "message": "Data fetch is already running",
                    "status": _fetch_status,
                }
            )

    # Start fetch in background thread
    thread = threading.Thread(target=run_fetch_scripts)
    thread.daemon = True
    thread.start()

    return jsonify(
        {
            "success": True,
            "message": "Data fetch started in background",
            "status": {"is_running": True, "last_run": _fetch_status["last_run"]},
        }
    )


@fetch_bp.route("/fetch/status")
def api_fetch_status():
    """Get data fetch status."""
    from app.services.data_fetch_scheduler import scheduler

    with _fetch_lock:
        fetch_status = _fetch_status.copy()

    # Add scheduler status
    scheduler_status = scheduler.get_status()

    return jsonify({"success": True, "status": fetch_status, "scheduler": scheduler_status})


@fetch_bp.route("/fetch")
def api_fetch():
    """Fetch data from local sources."""
    # This would integrate with the existing fetch scripts
    return jsonify(
        {"success": True, "message": "Fetch endpoint - integrate with existing fetch scripts"}
    )


@fetch_bp.route("/fetch/remote")
def api_fetch_remote():
    """Fetch data from remote sources."""
    # This would integrate with the existing remote fetch functionality
    return jsonify(
        {"success": True, "message": "Remote fetch endpoint - integrate with existing remote fetch"}
    )


@fetch_bp.route("/data-status")
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

        return jsonify(
            {
                "status": "ok",
                "database_exists": db_exists,
                "last_update": last_update,
                "tools_count": len(tools),
                "hosts_count": len(hosts),
                "senders_count": len(senders),
                "tools": tools[:10],  # First 10 tools
                "hosts": hosts[:10],  # First 10 hosts
                "date": today,
            }
        )
    except Exception:
        logger.exception("Error getting data status")
        return jsonify({"status": "error", "error": "Internal server error"}), 500
