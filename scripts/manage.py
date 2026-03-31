#!/usr/bin/env python3
"""
Open ACE - AI Computing Explorer - Manage Script

Unified script for:
- Configuration setup (init)
- Local deployment and service management (default mode)
- Remote machine deployment (--remote mode)

Usage:
  python3 scripts/manage.py init       # Initialize configuration
  python3 scripts/manage.py deploy     # Deploy to ~/open-ace/
  python3 scripts/manage.py start      # Start local web server
  python3 scripts/manage.py stop       # Stop local web server
  python3 scripts/manage.py status     # Check service status
  python3 scripts/manage.py --remote deploy  # Deploy to remote machine
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Project directories
DEV_DIR = Path(__file__).parent.parent  # Development directory
DEPLOY_DIR = Path.home() / "open-ace"  # Deployment directory
CONFIG_DIR = Path.home() / ".open-ace"  # Configuration directory
LOG_DIR = DEPLOY_DIR / "logs"

# Import shared config for web server settings
sys.path.insert(0, str(DEV_DIR / "scripts" / "shared"))
try:
    import config

    WEB_PORT = config.WEB_PORT
    WEB_HOST = config.WEB_HOST
except ImportError:
    WEB_PORT = int(os.environ.get("AI_TOKEN_WEB_PORT", "5000"))
    WEB_HOST = os.environ.get("AI_TOKEN_WEB_HOST", "0.0.0.0")


def print_header(text: str):
    """Print a formatted header."""
    print("\n" + "=" * 60)
    print(f"  {text}")
    print("=" * 60 + "\n")


def print_success(text: str):
    """Print success message."""
    print(f"✓ {text}")


def print_error(text: str):
    """Print error message."""
    print(f"✗ {text}")


def run_command(
    cmd: str, capture: bool = False, check: bool = True
) -> Optional[subprocess.CompletedProcess]:
    """Run a shell command."""
    try:
        if capture:
            return subprocess.run(cmd, shell=True, check=check, capture_output=True, text=True)
        else:
            return subprocess.run(cmd, shell=True, check=check)
    except subprocess.CalledProcessError as e:
        print_error(f"Command failed: {e}")
        return None


# ============================================================================
# Configuration Setup (from setup.py)
# ============================================================================


def get_remote_config() -> dict:
    """Get remote configuration from config file."""
    config_file = CONFIG_DIR / "config.json"
    if config_file.exists():
        try:
            with open(config_file) as f:
                cfg = json.load(f)

            remote_cfg = cfg.get("remote", {})
            if remote_cfg.get("enabled") and remote_cfg.get("hosts"):
                host_info = remote_cfg["hosts"][0]
                return {
                    "host": host_info.get("host", ""),
                    "user": host_info.get("user", "openclaw"),
                    "dir": host_info.get("base_dir", "/home/openclaw/open-ace"),
                }
        except (json.JSONDecodeError, IOError) as e:
            print_error(f"Failed to load config: {e}")

    return {"host": "", "user": "openclaw", "dir": "/home/openclaw/open-ace"}


def init_config():
    """Initialize configuration directory and create config file."""
    print_header("Initializing Configuration")

    # Create config directory
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    print_success(f"Config directory: {CONFIG_DIR}")

    # Create deployment directory
    DEPLOY_DIR.mkdir(parents=True, exist_ok=True)
    (DEPLOY_DIR / "logs").mkdir(parents=True, exist_ok=True)
    print_success(f"Deploy directory: {DEPLOY_DIR}")

    # Copy sample config
    config_file = CONFIG_DIR / "config.json"
    sample_file = DEV_DIR / "config" / "config.json.sample"

    if not config_file.exists():
        if sample_file.exists():
            shutil.copy(sample_file, config_file)
            print_success(f"Created config file: {config_file}")
            print(f"\n  Please edit {config_file} with your settings")
        else:
            # Create default config
            default_config = {
                "host_name": "localhost",
                "database": {"type": "postgresql", "path": str(CONFIG_DIR / "ace.db"), "url": None},
                "server": {
                    "upload_auth_key": "your-auth-key-here",
                    "server_url": f"http://localhost:{WEB_PORT}",
                    "web_port": WEB_PORT,
                    "web_host": WEB_HOST,
                },
                "tools": {
                    "openclaw": {"enabled": True, "hostname": "localhost"},
                    "claude": {"enabled": True, "hostname": "localhost"},
                    "qwen": {"enabled": True, "hostname": "localhost"},
                },
            }
            with open(config_file, "w") as f:
                json.dump(default_config, f, indent=2)
            print_success(f"Created default config: {config_file}")
    else:
        print(f"Config file already exists: {config_file}")

    print("\n" + "=" * 60)
    print("Configuration initialized!")
    print(f"Config file: {config_file}")
    print("=" * 60)


def show_config():
    """Show current configuration."""
    print_header("Configuration Info")

    print(f"Config directory: {CONFIG_DIR}")
    print(f"Config file: {CONFIG_DIR / 'config.json'}")
    print(f"Deploy directory: {DEPLOY_DIR}")
    print(f"Log directory: {LOG_DIR}")

    config_file = CONFIG_DIR / "config.json"
    if config_file.exists():
        print(f"\nCurrent configuration:")
        with open(config_file) as f:
            print(f.read())
    else:
        print("\nConfig file not found. Run 'init' to create it.")


# ============================================================================
# Local Deployment
# ============================================================================


def deploy_local():
    """Deploy to local ~/open-ace directory."""
    print_header("Deploying to Local Directory")
    print(f"Source: {DEV_DIR}")
    print(f"Target: {DEPLOY_DIR}")

    # Step 1: Create directories
    print("\n1. Creating directories...")
    DEPLOY_DIR.mkdir(parents=True, exist_ok=True)
    (DEPLOY_DIR / "logs").mkdir(parents=True, exist_ok=True)
    (DEPLOY_DIR / "config").mkdir(parents=True, exist_ok=True)
    print_success("Directories created")

    # Step 2: Sync files
    print("\n2. Syncing files...")
    exclude_patterns = [
        ".git",
        ".qwen",
        "__pycache__",
        "*.pyc",
        ".DS_Store",
        "logs/*",
        ".pytest_cache",
        "*.egg-info",
    ]

    rsync_cmd = "rsync -avz"
    for pattern in exclude_patterns:
        rsync_cmd += f" --exclude='{pattern}'"
    rsync_cmd += f" {DEV_DIR}/ {DEPLOY_DIR}/"

    run_command(rsync_cmd)
    print_success("Files synced")

    # Step 3: Set permissions
    print("\n3. Setting executable permissions...")
    run_command(f"chmod +x {DEPLOY_DIR}/scripts/*.py 2>/dev/null || true")
    print_success("Permissions set")

    # Step 4: Verify deployment
    print("\n4. Verifying deployment...")
    if (DEPLOY_DIR / "web.py").exists():
        print_success("Deployment successful!")
    else:
        print_error("Deployment failed - web.py not found")
        return

    print("\n" + "=" * 60)
    print("Local deployment completed!")
    print(f"Deploy directory: {DEPLOY_DIR}")
    print(f"To start: python3 scripts/manage.py start")
    print("=" * 60)


def install_service():
    """Install local systemd/launchd service."""
    print_header("Installing Local Service")

    system = os.uname().sysname

    if system == "Darwin":  # macOS
        plist_file = DEV_DIR / "scripts" / "com.open-ace.web.plist"
        launch_dir = Path.home() / "Library" / "LaunchAgents"

        if plist_file.exists():
            launch_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy(plist_file, launch_dir / plist_file.name)
            run_command(f"launchctl load {launch_dir / plist_file.name} 2>/dev/null || true")
            print_success("Installed launchd agent")
            print(f"Plist: {launch_dir / plist_file.name}")
        else:
            print_error("Plist file not found")
    else:  # Linux
        service_file = DEV_DIR / "contrib" / "fetch-openclaw.service"
        if service_file.exists():
            run_command(f"sudo cp {service_file} /etc/systemd/system/")
            run_command("sudo systemctl daemon-reload")
            run_command("sudo systemctl enable fetch-openclaw.service")
            print_success("Installed systemd service")
        else:
            print_error("Service file not found")


def start_service(dev_mode: bool = False):
    """Start local web server."""
    print_header("Starting Web Server")

    # Determine working directory
    work_dir = DEV_DIR if dev_mode else DEPLOY_DIR
    mode_str = "development" if dev_mode else "deployment"
    print(f"Mode: {mode_str}")
    print(f"Directory: {work_dir}")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "web_server.log"

    # Check if already running
    result = run_command(f"lsof -i :{WEB_PORT}", capture=True, check=False)
    if result and result.returncode == 0:
        print(f"Web server is already running on port {WEB_PORT}")
        print(f"Process info:\n{result.stdout}")
        return

    # Start web server
    print(f"Starting web server on port {WEB_PORT}...")
    print(f"Log file: {log_file}")

    os.chdir(work_dir)

    # Set environment variables
    env = os.environ.copy()
    if dev_mode:
        env["FLASK_DEBUG"] = "true"
        print("Debug mode: ON")

    with open(log_file, "a") as log:
        subprocess.Popen(
            [sys.executable, str(work_dir / "web.py")],
            stdout=log,
            stderr=log,
            cwd=work_dir,
            env=env,
        )

    import time

    time.sleep(2)

    # Verify it started
    result = run_command(f"lsof -i :{WEB_PORT}", capture=True, check=False)
    if result and result.returncode == 0:
        print_success(f"Web server started on http://localhost:{WEB_PORT}")
    else:
        print_error("Failed to start web server")
        print(f"Check log: {log_file}")


def stop_service():
    """Stop local web server."""
    print_header("Stopping Web Server")

    # Find and kill process on web port
    result = run_command(f"lsof -ti :{WEB_PORT}", capture=True, check=False)
    if result and result.stdout.strip():
        pids = result.stdout.strip().split("\n")
        for pid in pids:
            run_command(f"kill -9 {pid}", check=False)
        print_success(f"Web server stopped (killed PIDs: {', '.join(pids)})")
    else:
        print("Web server is not running")


def status_service():
    """Check local web server status."""
    print_header("Service Status")

    result = run_command(f"lsof -i :{WEB_PORT}", capture=True, check=False)
    if result and result.returncode == 0:
        print(f"Web server is RUNNING on http://localhost:{WEB_PORT}")
        print(f"\nProcess info:\n{result.stdout}")

        # Show process start time
        pid = result.stdout.split("\n")[1].split()[1] if "\n" in result.stdout else None
        if pid:
            time_result = run_command(f"ps -p {pid} -o lstart=", capture=True, check=False)
            if time_result and time_result.stdout.strip():
                print(f"Started at: {time_result.stdout.strip()}")
    else:
        print("Web server is NOT RUNNING")

    # Show config info
    print(f"\nConfig: {CONFIG_DIR / 'config.json'}")
    print(f"Deploy: {DEPLOY_DIR}")
    print(f"Logs: {LOG_DIR}")


# ============================================================================
# Remote Deployment
# ============================================================================


def deploy_remote():
    """Deploy to remote machine."""
    remote_cfg = get_remote_config()
    host = remote_cfg["host"]
    user = remote_cfg["user"]
    remote_dir = remote_cfg["dir"]

    if not host:
        print_error("Remote host not configured")
        print("Set REMOTE_HOST environment variable or configure in ~/.open-ace/config.json")
        return

    print_header("Deploying to Remote Machine")
    print(f"Target: {user}@{host}:{remote_dir}")

    # Step 1: Create directory
    print("\n1. Creating remote directory...")
    run_command(f"ssh {user}@{host} 'mkdir -p {remote_dir}'")
    print_success("Remote directory created")

    # Step 2: Sync files
    print("\n2. Syncing files...")
    exclude_patterns = [
        ".git",
        ".qwen",
        "logs/*",
        "__pycache__",
        "*.pyc",
        ".DS_Store",
        "scripts/shared/email_notifier.py",
    ]

    rsync_cmd = "rsync -avz"
    for pattern in exclude_patterns:
        rsync_cmd += f" --exclude='{pattern}'"
    rsync_cmd += f" ./ {user}@{host}:{remote_dir}/"

    run_command(rsync_cmd)
    print_success("Files synced")

    # Step 3: Clean unnecessary scripts
    print("\n3. Cleaning unnecessary scripts...")
    clean_script = f"""
cd {remote_dir}/scripts
rm -f check_*.py db_info.py test_*.py fix_timestamps.py
rm -f deploy_remote.py fetch_remote.py upload_to_server.py
rm -f fetch_all_tools.py fetch_claude.py fetch_qwen.py
rm -f install_web_service.sh start_web.sh stop_web.sh
rm -f com.open-ace.web.plist
rm -f sync_remote.sh clean_deploy_remote.sh
rm -f ../scripts/shared/email_notifier.py
"""
    run_command(f"ssh {user}@{host} '{clean_script}'")
    print_success("Unnecessary scripts removed")

    # Step 4: Update __init__.py
    print("\n4. Updating __init__.py...")
    run_command(
        f"ssh {user}@{host} \"cat > {remote_dir}/scripts/shared/__init__.py << 'EOF'\nfrom . import db, utils, config\n\n__all__ = ['db', 'utils', 'config']\nEOF\""
    )
    print_success("__init__.py updated")

    # Step 5: Fix ownership
    print("\n5. Fixing file ownership...")
    run_command(f"ssh root@{host} 'chown -R {user}:{user} {remote_dir}' 2>/dev/null || true")
    print_success("Ownership fixed")

    # Step 6: Set permissions
    print("\n6. Setting executable permissions...")
    run_command(f"ssh {user}@{host} 'chmod +x {remote_dir}/scripts/*.py'")
    print_success("Permissions set")

    # Step 7: Test deployment
    print("\n7. Testing deployment...")
    result = run_command(
        f"ssh {user}@{host} 'cd {remote_dir} && python3 scripts/fetch_openclaw.py --days 1 2>&1 | tail -5'",
        capture=True,
    )
    if result:
        print(f"Test output:\n{result.stdout}")
        print_success("Deployment successful!")
    else:
        print_error("Deployment test failed")

    print("\n" + "=" * 60)
    print("Remote deployment completed!")
    print("=" * 60)


def sync_remote():
    """Quick sync to remote machine."""
    remote_cfg = get_remote_config()
    host = remote_cfg["host"]
    user = remote_cfg["user"]
    remote_dir = remote_cfg["dir"]

    if not host:
        print_error("Remote host not configured")
        return

    print_header("Syncing Files to Remote Machine")

    # Sync shared modules
    print("Syncing shared modules...")
    run_command(
        f"rsync -avz scripts/shared/__init__.py scripts/shared/db.py scripts/shared/config.py scripts/shared/utils.py scripts/shared/feishu_user_cache.py {user}@{host}:{remote_dir}/scripts/shared/"
    )

    # Sync main scripts
    print("Syncing main scripts...")
    run_command(
        f"rsync -avz scripts/fetch_openclaw.py scripts/upload_to_server.py {user}@{host}:{remote_dir}/scripts/"
    )

    print_success("Sync completed!")


def status_remote():
    """Check remote machine status."""
    remote_cfg = get_remote_config()
    host = remote_cfg["host"]
    user = remote_cfg["user"]
    remote_dir = remote_cfg["dir"]

    if not host:
        print_error("Remote host not configured")
        return

    print_header("Remote Machine Status")
    print(f"Host: {user}@{host}")
    print(f"Directory: {remote_dir}")

    print("\nChecking remote deployment...")
    run_command(f"ssh {user}@{host} 'ls -la {remote_dir}/scripts/'")

    print("\nTesting data collection...")
    run_command(
        f"ssh {user}@{host} 'cd {remote_dir} && python3 scripts/fetch_openclaw.py --days 1 2>&1 | tail -5'"
    )


# ============================================================================
# Main Entry Point
# ============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Open ACE - Manage Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scripts/manage.py init       # Initialize configuration
  python3 scripts/manage.py deploy     # Deploy to ~/open-ace/
  python3 scripts/manage.py start      # Start web server (deployment dir)
  python3 scripts/manage.py --dev start    # Start web server (dev dir)
  python3 scripts/manage.py stop       # Stop web server
  python3 scripts/manage.py restart    # Restart web server
  python3 scripts/manage.py status     # Check service status
  python3 scripts/manage.py --remote deploy  # Deploy to remote machine
        """,
    )

    parser.add_argument(
        "action",
        choices=["init", "deploy", "install", "start", "stop", "restart", "status", "sync", "show"],
        help="Action to perform",
    )

    parser.add_argument(
        "--remote", action="store_true", help="Operate on remote machine instead of local"
    )

    parser.add_argument(
        "--dev",
        action="store_true",
        help="Run in development directory instead of deployment directory",
    )

    args = parser.parse_args()

    # Remote operations
    if args.remote:
        if args.action == "deploy":
            deploy_remote()
        elif args.action == "sync":
            sync_remote()
        elif args.action == "status":
            status_remote()
        else:
            print_error(f"Action '{args.action}' not supported for remote mode")
            print("Supported remote actions: deploy, sync, status")
            return 1
        return 0

    # Local operations (default)
    if args.action == "init":
        init_config()
    elif args.action == "show":
        show_config()
    elif args.action == "deploy":
        deploy_local()
    elif args.action == "install":
        install_service()
    elif args.action == "start":
        start_service(dev_mode=args.dev)
    elif args.action == "stop":
        stop_service()
    elif args.action == "restart":
        stop_service()
        start_service(dev_mode=args.dev)
    elif args.action == "status":
        status_service()
    elif args.action == "sync":
        print_error("sync is only available in remote mode (--remote)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
