"""
Open ACE - Fetch Routes

API routes for data fetching operations.
"""

import json
import logging
import os
import re
import subprocess
import sys
import threading
from datetime import datetime
from typing import Any

from flask import Blueprint, jsonify

from app.auth.decorators import admin_required, auth_required
from app.repositories.database import DB_PATH
from app.services.message_service import MessageService
from app.services.usage_service import UsageService
from app.utils.helpers import get_today

fetch_bp = Blueprint("fetch", __name__)
usage_service = UsageService()
message_service = MessageService()
logger = logging.getLogger(__name__)

# Global state for fetch status
_fetch_status: dict[str, Any] = {
    "is_running": False,
    "last_run": None,
    "last_result": None,
    "error": None,
}
_fetch_lock = threading.Lock()


def _parse_fetch_result(output: str) -> dict[str, Any]:
    """Parse FETCH_RESULT line from fetch script output.

    Issue #2733: Extract structured coverage data so the scheduler
    can detect degraded fetch results.

    Args:
        output: The stdout from the fetch script

    Returns:
        dict with 'status' and 'coverage' keys, or empty dict if not found
    """
    match = re.search(r"FETCH_RESULT:\s*(\{.*?\})\s*$", output, re.MULTILINE)
    if match:
        try:
            result: dict[str, Any] = json.loads(match.group(1))
            return result
        except json.JSONDecodeError:
            pass
    return {}


def _run_subprocess(cmd, timeout=600, cwd=None):
    """Run subprocess using Popen to avoid gevent monkey-patch issues.

    gevent monkey.patch_all() causes subprocess.run to crash (SIGSEGV)
    when executing sudo commands. Using Popen + communicate() works correctly.
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return type(
            "Result",
            (),
            {
                "returncode": proc.returncode,
                "stdout": stdout.decode("utf-8", errors="replace") if stdout else "",
                "stderr": stderr.decode("utf-8", errors="replace") if stderr else "",
            },
        )()
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise


def run_fetch_scripts():
    """Run data fetch scripts in background.

    Returns:
        dict or None: Per-tool results on success (e.g. {"qwen": {"success": True, ...}, ...}).
                      {"_skipped": True} if a concurrent fetch is already running.
                      None if an unexpected error occurred in the outer handler.
    """
    global _fetch_status

    with _fetch_lock:
        if _fetch_status["is_running"]:
            return {"_skipped": True}
        _fetch_status["is_running"] = True
        _fetch_status["error"] = None

    try:
        # Get project root directory
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        results = {}

        # NOTE: fetch scripts may need elevated privileges to read other users'
        # home directories. By default sudo is disabled for safety.
        # If your deployment requires cross-user data collection, either:
        #   - set FETCH_USE_SUDO=true in the environment AND configure
        #     passwordless sudo for the service user, or
        #   - configure a dedicated service account with read access to
        #     user home directories.
        use_sudo = os.environ.get("FETCH_USE_SUDO", "false").lower() == "true"
        # Use the same Python interpreter as the main process to ensure compatibility
        # with type annotation syntax (e.g., str | None requires Python 3.10+)
        python_path = sys.executable

        def _build_cmd(script_path, args):
            cmd = []
            if use_sudo:
                cmd.extend(["sudo", "-n", python_path])
            else:
                cmd.append(python_path)
            cmd.append(script_path)
            cmd.extend(args)
            return cmd

        # Define config_path once before all per-script blocks to avoid
        # NameError when individual scripts are not present on disk.
        config_path = os.path.expanduser("~/.open-ace/config.json")

        # Run fetch_qwen.py to scan all users' qwen directories
        qwen_script = os.path.join(project_root, "scripts", "fetch_qwen.py")
        if os.path.exists(qwen_script):
            try:
                result = _run_subprocess(
                    _build_cmd(
                        qwen_script,
                        ["--days", "1", "--multi-user", "--recent", "--config", config_path],
                    ),
                    timeout=600,
                    cwd=project_root,
                )
                # Issue #2733: Parse structured coverage data
                fetch_result = _parse_fetch_result(result.stdout) if result.stdout else {}
                qwen_result = {
                    "success": result.returncode == 0,
                    "output": result.stdout[-1000:] if result.stdout else "",
                    "error": result.stderr[-500:] if result.stderr else None,
                }
                # Include coverage data if available
                if fetch_result:
                    qwen_result["coverage"] = fetch_result.get("coverage", {})
                    qwen_result["status"] = fetch_result.get("status", "unknown")
                    # Log degraded/failed states
                    if fetch_result.get("status") in ("degraded", "denied", "failed"):
                        logger.warning(
                            f"Qwen fetch {fetch_result['status']}: "
                            f"users_denied={fetch_result.get('coverage', {}).get('users_denied', [])}"
                        )
                results["qwen"] = qwen_result
            except subprocess.TimeoutExpired:
                results["qwen"] = {"success": False, "error": "Timeout after 10 minutes"}
            except Exception as e:
                logger.error(f"Error running qwen fetch script: {e}")
                results["qwen"] = {"success": False, "error": "Internal server error"}

        # Run fetch_claude.py to scan all users' Claude directories
        claude_script = os.path.join(project_root, "scripts", "fetch_claude.py")
        if os.path.exists(claude_script):
            try:
                result = _run_subprocess(
                    _build_cmd(
                        claude_script,
                        ["--days", "1", "--multi-user", "--recent", "--config", config_path],
                    ),
                    timeout=600,
                    cwd=project_root,
                )
                results["claude"] = {
                    "success": result.returncode == 0,
                    "output": result.stdout[-1000:] if result.stdout else "",
                    "error": result.stderr[-500:] if result.stderr else None,
                }
            except subprocess.TimeoutExpired:
                results["claude"] = {"success": False, "error": "Timeout after 10 minutes"}
            except Exception as e:
                logger.error(f"Error running claude fetch script: {e}")
                results["claude"] = {"success": False, "error": "Internal server error"}

        # Run fetch_openclaw.py to scan all users' OpenClaw directories
        openclaw_script = os.path.join(project_root, "scripts", "fetch_openclaw.py")
        if os.path.exists(openclaw_script):
            try:
                result = _run_subprocess(
                    _build_cmd(
                        openclaw_script,
                        [
                            "--days",
                            "1",
                            "--mode",
                            "both",
                            "--multi-user",
                            "--recent",
                            "--config",
                            config_path,
                        ],
                    ),
                    timeout=600,
                    cwd=project_root,
                )
                results["openclaw"] = {
                    "success": result.returncode == 0,
                    "output": result.stdout[-1000:] if result.stdout else "",
                    "error": result.stderr[-500:] if result.stderr else None,
                }
            except subprocess.TimeoutExpired:
                results["openclaw"] = {"success": False, "error": "Timeout after 10 minutes"}
            except Exception as e:
                logger.error(f"Error running openclaw fetch script: {e}")
                results["openclaw"] = {"success": False, "error": "Internal server error"}

        # Run fetch_codex.py to scan all users' Codex directories
        codex_script = os.path.join(project_root, "scripts", "fetch_codex.py")
        if os.path.exists(codex_script):
            try:
                result = _run_subprocess(
                    _build_cmd(
                        codex_script,
                        ["--days", "1", "--multi-user", "--recent", "--config", config_path],
                    ),
                    timeout=600,
                    cwd=project_root,
                )
                results["codex"] = {
                    "success": result.returncode == 0,
                    "output": result.stdout[-1000:] if result.stdout else "",
                    "error": result.stderr[-500:] if result.stderr else None,
                }
            except subprocess.TimeoutExpired:
                results["codex"] = {"success": False, "error": "Timeout after 10 minutes"}
            except Exception as e:
                logger.error(f"Error running codex fetch script: {e}")
                results["codex"] = {"success": False, "error": "Internal server error"}

        # Run fetch_zcode.py to scan all users' ZCode CLI databases.
        # ZCode stores sessions in ~/.zcode/cli/db/db.sqlite (not JSONL), so this
        # is the only path by which local ZCode sessions reach the session list.
        zcode_script = os.path.join(project_root, "scripts", "fetch_zcode.py")
        if os.path.exists(zcode_script):
            try:
                result = _run_subprocess(
                    _build_cmd(
                        zcode_script,
                        ["--days", "1", "--multi-user", "--recent", "--config", config_path],
                    ),
                    timeout=600,
                    cwd=project_root,
                )
                results["zcode"] = {
                    "success": result.returncode == 0,
                    "output": result.stdout[-1000:] if result.stdout else "",
                    "error": result.stderr[-500:] if result.stderr else None,
                }
            except subprocess.TimeoutExpired:
                results["zcode"] = {"success": False, "error": "Timeout after 10 minutes"}
            except Exception as e:
                logger.error(f"Error running zcode fetch script: {e}")
                results["zcode"] = {"success": False, "error": "Internal server error"}

        with _fetch_lock:
            _fetch_status["last_run"] = datetime.now().isoformat()
            _fetch_status["last_result"] = results
            _fetch_status["is_running"] = False

        all_failed = results and all(not r.get("success", False) for r in results.values())
        if all_failed:
            logger.error(f"All data fetch scripts failed: {results}")
        else:
            logger.info(f"Data fetch finished: {results}")

        return results

    except Exception as e:
        logger.exception("Error running fetch scripts")
        with _fetch_lock:
            _fetch_status["error"] = str(e)
            _fetch_status["is_running"] = False
        return None


@fetch_bp.route("/fetch/data", methods=["POST"])
@auth_required
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
@auth_required
def api_fetch_status():
    """Get data fetch status."""
    from app.services.data_fetch_scheduler import scheduler

    with _fetch_lock:
        fetch_status = _fetch_status.copy()

    # Add scheduler status
    scheduler_status = scheduler.get_status()

    return jsonify({"success": True, "status": fetch_status, "scheduler": scheduler_status})


@fetch_bp.route("/fetch")
@admin_required
def api_fetch():
    """Fetch data from local sources."""
    # This would integrate with the existing fetch scripts
    return jsonify(
        {"success": True, "message": "Fetch endpoint - integrate with existing fetch scripts"}
    )


@fetch_bp.route("/fetch/remote")
@admin_required
def api_fetch_remote():
    """Fetch data from remote sources."""
    # This would integrate with the existing remote fetch functionality
    return jsonify(
        {"success": True, "message": "Remote fetch endpoint - integrate with existing remote fetch"}
    )


@fetch_bp.route("/data-status")
@auth_required
def api_data_status():
    """Get data status information."""
    try:
        from app.repositories.database import Database, is_postgresql

        db = Database()

        # Check database exists - different logic for PostgreSQL vs SQLite
        if is_postgresql():
            # PostgreSQL: test connection availability
            try:
                with db.connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT 1")
                db_exists = True
            except Exception as e:
                logger.warning(f"PostgreSQL connection test failed: {e}")
                db_exists = False
        else:
            # SQLite: check file existence
            db_exists = os.path.exists(DB_PATH)

        # Get last update time
        last_update = None
        if db_exists:
            if is_postgresql():
                # PostgreSQL: query latest timestamp from daily_messages table
                try:
                    result = db.fetch_one('SELECT MAX("timestamp") as latest FROM daily_messages')
                    if result and result.get("latest"):
                        last_update = result["latest"].isoformat()
                except Exception as e:
                    logger.warning(f"Failed to query last_update: {e}")
            else:
                # SQLite: use file modification time
                last_update = datetime.fromtimestamp(os.path.getmtime(DB_PATH)).isoformat()

        # Get data counts
        tools = []
        hosts = []
        senders = []

        if db_exists:
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
