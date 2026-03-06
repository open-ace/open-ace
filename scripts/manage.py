#!/usr/bin/env python3
"""
AI Token Analyzer - Deploy and Manage Script

Unified script for:
- Local deployment (central server)
- Remote machine deployment (ai-lab)
- Service management (start/stop/status)
- Configuration setup
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Project directories
BASE_DIR = Path(__file__).parent.parent  # Development directory
DEV_DIR = BASE_DIR  # Development directory (/Users/rhuang/workspace/ai-token-analyzer)
DEPLOY_DIR = Path.home() / "ai-token-analyzer"  # Deployment directory (~/ai-token-analyzer)
SCRIPTS_DIR = DEPLOY_DIR / "scripts"
CONFIG_DIR = DEPLOY_DIR / "config"
LOG_DIR = DEPLOY_DIR / "logs"

# Remote machine configuration
REMOTE_USER = "openclaw"
REMOTE_HOST = "192.168.31.159"
REMOTE_DIR = "/home/openclaw/ai-token-analyzer"


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


def run_command(cmd: str, capture: bool = False, check: bool = True) -> Optional[subprocess.CompletedProcess]:
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
# Local Deployment (Central Server)
# ============================================================================

def setup_local_config():
    """Setup local configuration."""
    print_header("Setting up Local Configuration")
    
    config_path = Path.home() / ".ai-token-analyzer"
    config_path.mkdir(parents=True, exist_ok=True)
    
    config_file = config_path / "config.json"
    sample_file = CONFIG_DIR / "config.json.sample"
    
    if not config_file.exists():
        if sample_file.exists():
            import shutil
            shutil.copy(sample_file, config_file)
            print_success(f"Created config file: {config_file}")
            print(f"  Please edit {config_file} with your settings")
        else:
            # Create default config
            config = {
                "host_name": "localhost",
                "server": {
                    "upload_auth_key": "your-auth-key-here",
                    "server_url": "http://localhost:5001"
                },
                "tools": {
                    "openclaw": {"enabled": True, "hostname": "localhost"},
                    "claude": {"enabled": True, "hostname": "localhost"},
                    "qwen": {"enabled": True, "hostname": "localhost"}
                }
            }
            with open(config_file, 'w') as f:
                json.dump(config, f, indent=2)
            print_success(f"Created default config: {config_file}")
    else:
        print(f"Config file already exists: {config_file}")


def deploy_local():
    """Deploy to local ~/ai-token-analyzer directory."""
    print_header("Deploying to Local Directory")
    print(f"Target: {DEPLOY_DIR}")
    
    # Step 1: Create directory
    print("\n1. Creating deployment directory...")
    DEPLOY_DIR.mkdir(parents=True, exist_ok=True)
    (DEPLOY_DIR / "logs").mkdir(parents=True, exist_ok=True)
    (DEPLOY_DIR / "config").mkdir(parents=True, exist_ok=True)
    print_success("Deployment directory created")
    
    # Step 2: Sync files (exclude dev-only files)
    print("\n2. Syncing files...")
    exclude_patterns = [
        ".git", ".qwen", "__pycache__", "*.pyc", ".DS_Store"
    ]
    
    rsync_cmd = f"rsync -avz"
    for pattern in exclude_patterns:
        rsync_cmd += f" --exclude='{pattern}'"
    rsync_cmd += f" {DEV_DIR}/ {DEPLOY_DIR}/"
    
    run_command(rsync_cmd)
    print_success("Files synced")
    
    # Step 3: Setup config
    print("\n3. Setting up configuration...")
    config_path = Path.home() / ".ai-token-analyzer"
    config_path.mkdir(parents=True, exist_ok=True)
    
    config_file = config_path / "config.json"
    sample_file = DEPLOY_DIR / "config" / "config.json.sample"
    
    if not config_file.exists() and sample_file.exists():
        import shutil
        shutil.copy(sample_file, config_file)
        print_success(f"Created config file: {config_file}")
    else:
        print("Config already exists")
    
    # Step 4: Set permissions
    print("\n4. Setting executable permissions...")
    run_command(f"chmod +x {DEPLOY_DIR}/scripts/*.py")
    print_success("Permissions set")
    
    # Step 5: Test deployment
    print("\n5. Testing deployment...")
    os.chdir(DEPLOY_DIR)
    result = run_command(f"cd {DEPLOY_DIR} && python3 scripts/fetch_openclaw.py --days 1 2>&1 | tail -5", capture=True)
    if result:
        print(f"Test output:\n{result.stdout}")
        print_success("Local deployment successful!")
    else:
        print_error("Deployment test failed")
    
    print("\n" + "=" * 60)
    print("Local deployment completed!")
    print(f"Deployment directory: {DEPLOY_DIR}")
    print(f"To run: cd {DEPLOY_DIR} && python3 web.py")
    print("=" * 60)


def install_local_service():
    """Install local systemd/launchd service."""
    print_header("Installing Local Service")
    
    system = os.uname().sysname
    service_file = DEPLOY_DIR / "contrib" / "fetch-openclaw.service"
    
    if system == "Linux":
        # systemd service
        if service_file.exists():
            run_command(f"sudo cp {service_file} /etc/systemd/system/")
            run_command("sudo systemctl daemon-reload")
            run_command("sudo systemctl enable fetch-openclaw.service")
            print_success("Installed systemd service")
        else:
            print_error("Service file not found")
    
    elif system == "Darwin":  # macOS
        plist_file = DEPLOY_DIR / "scripts" / "com.ai-token-analyzer.web.plist"
        launch_dir = Path.home() / "Library" / "LaunchAgents"
        
        if plist_file.exists():
            launch_dir.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy(plist_file, launch_dir / plist_file.name)
            run_command(f"launchctl load {launch_dir / plist_file.name}")
            print_success("Installed launchd agent")
        else:
            print_error("Plist file not found")


def start_local_service():
    """Start local web server."""
    print_header("Starting Local Web Server")
    
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "web_server.log"
    
    os.chdir(DEPLOY_DIR)
    
    # Check if already running
    result = run_command("lsof -i :5001", capture=True, check=False)
    if result and result.returncode == 0:
        print("Web server is already running on port 5001")
        return
    
    # Start web server
    print(f"Starting web server... (log: {log_file})")
    with open(log_file, 'a') as log:
        subprocess.Popen(
            [sys.executable, str(DEPLOY_DIR / "web.py")],
            stdout=log,
            stderr=log,
            cwd=DEPLOY_DIR
        )
    
    import time
    time.sleep(2)
    
    # Verify it started
    result = run_command("lsof -i :5001", capture=True, check=False)
    if result and result.returncode == 0:
        print_success("Web server started on http://localhost:5001")
    else:
        print_error("Failed to start web server")


def stop_local_service():
    """Stop local web server."""
    print_header("Stopping Local Web Server")
    
    # Find and kill process on port 5001
    if os.uname().sysname == "Darwin":  # macOS
        run_command("lsof -ti :5001 | xargs kill -9", check=False)
    else:  # Linux
        run_command("fuser -k 5001/tcp", check=False)
    
    print_success("Web server stopped")


def status_local_service():
    """Check local web server status."""
    print_header("Local Service Status")
    
    result = run_command("lsof -i :5001", capture=True, check=False)
    if result and result.returncode == 0:
        print("Web server is RUNNING on http://localhost:5001")
        print(f"\nProcess info:\n{result.stdout}")
    else:
        print("Web server is NOT RUNNING")


# ============================================================================
# Remote Deployment (ai-lab)
# ============================================================================

def deploy_remote():
    """Deploy to remote machine."""
    print_header("Deploying to Remote Machine")
    print(f"Target: {REMOTE_USER}@{REMOTE_HOST}:{REMOTE_DIR}")
    
    # Step 1: Create directory
    print("\n1. Creating remote directory...")
    run_command(f"ssh {REMOTE_USER}@{REMOTE_HOST} 'mkdir -p {REMOTE_DIR}'")
    print_success("Remote directory created")
    
    # Step 2: Sync files
    print("\n2. Syncing files...")
    exclude_patterns = [
        ".git", ".qwen", "logs/*", "__pycache__", "*.pyc", ".DS_Store",
        "scripts/shared/email_notifier.py"
    ]
    
    rsync_cmd = f"rsync -avz"
    for pattern in exclude_patterns:
        rsync_cmd += f" --exclude='{pattern}'"
    rsync_cmd += f" ./ {REMOTE_USER}@{REMOTE_HOST}:{REMOTE_DIR}/"
    
    run_command(rsync_cmd)
    print_success("Files synced")
    
    # Step 3: Clean unnecessary scripts
    print("\n3. Cleaning unnecessary scripts...")
    clean_script = f"""
cd {REMOTE_DIR}/scripts
rm -f check_*.py db_info.py test_*.py fix_timestamps.py
rm -f deploy_remote.py fetch_remote.py upload_to_server.py
rm -f fetch_all_tools.py fetch_claude.py fetch_qwen.py
rm -f install_web_service.sh start_web.sh stop_web.sh
rm -f com.ai-token-analyzer.web.plist
rm -f sync_remote.sh clean_deploy_remote.sh
rm -f ../scripts/shared/email_notifier.py
"""
    run_command(f"ssh {REMOTE_USER}@{REMOTE_HOST} '{clean_script}'")
    print_success("Unnecessary scripts removed")
    
    # Step 4: Update __init__.py
    print("\n4. Updating __init__.py...")
    run_command(f"ssh {REMOTE_USER}@{REMOTE_HOST} \"cat > {REMOTE_DIR}/scripts/shared/__init__.py << 'EOF'\nfrom . import db, utils, config\n\n__all__ = ['db', 'utils', 'config']\nEOF\"")
    print_success("__init__.py updated")
    
    # Step 5: Fix ownership
    print("\n5. Fixing file ownership...")
    run_command(f"ssh root@{REMOTE_HOST} 'chown -R {REMOTE_USER}:{REMOTE_USER} {REMOTE_DIR}'")
    print_success("Ownership fixed")
    
    # Step 6: Set permissions
    print("\n6. Setting executable permissions...")
    run_command(f"ssh {REMOTE_USER}@{REMOTE_HOST} 'chmod +x {REMOTE_DIR}/scripts/fetch_openclaw.py'")
    print_success("Permissions set")
    
    # Step 7: Setup config
    print("\n7. Setting up configuration...")
    config_content = f"""{{
  "host_name": "ai-lab",
  "server": {{
    "upload_auth_key": "deploy-remote-machine-key-2026",
    "server_url": "http://192.168.31.181:5001"
  }},
  "tools": {{
    "openclaw": {{
      "enabled": true,
      "token_env": "4a1783fec45ae0dd5e67d0560fe63415cbbd1daff1bb2cd1",
      "gateway_url": "http://127.0.0.1:18789",
      "hostname": "ai-lab"
    }}
  }},
  "feishu": {{
    "app_id": "cli_a92be94ec4395cc2",
    "app_secret": "6pvXz79b6gqadmEGKWIuVdTEjkf1DkSf"
  }}
}}"""
    run_command(f"ssh {REMOTE_USER}@{REMOTE_HOST} \"cat > ~{REMOTE_USER}/.ai-token-analyzer/config.json << 'EOF'\n{config_content}\nEOF\"")
    print_success("Configuration setup")
    
    # Step 8: Test deployment
    print("\n8. Testing deployment...")
    result = run_command(
        f"ssh {REMOTE_USER}@{REMOTE_HOST} 'cd {REMOTE_DIR} && python3 scripts/fetch_openclaw.py --days 1 2>&1 | tail -5'",
        capture=True
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
    """Quick sync to remote machine (without full cleanup)."""
    print_header("Syncing Files to Remote Machine")
    
    # Sync shared modules
    print("Syncing shared modules...")
    run_command(f"rsync -avz scripts/shared/__init__.py scripts/shared/db.py scripts/shared/config.py scripts/shared/utils.py scripts/shared/feishu_user_cache.py {REMOTE_USER}@{REMOTE_HOST}:{REMOTE_DIR}/scripts/shared/")
    
    # Sync main script
    print("Syncing fetch_openclaw.py...")
    run_command(f"rsync -avz scripts/fetch_openclaw.py {REMOTE_USER}@{REMOTE_HOST}:{REMOTE_DIR}/scripts/")
    
    print_success("Sync completed!")


def status_remote():
    """Check remote machine status."""
    print_header("Remote Machine Status")
    
    print("Checking remote deployment...")
    run_command(f"ssh {REMOTE_USER}@{REMOTE_HOST} 'ls -la {REMOTE_DIR}/scripts/'")
    
    print("\nTesting data collection...")
    run_command(f"ssh {REMOTE_USER}@{REMOTE_HOST} 'cd {REMOTE_DIR} && python3 scripts/fetch_openclaw.py --days 1 2>&1 | tail -5'")


# ============================================================================
# Main Entry Point
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="AI Token Analyzer - Deploy and Manage",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s local deploy    - Deploy to ~/ai-token-analyzer/
  %(prog)s local setup     - Setup local configuration
  %(prog)s local install   - Install local service
  %(prog)s local start     - Start local web server
  %(prog)s local stop      - Stop local web server
  %(prog)s local status    - Check local service status
  %(prog)s remote deploy   - Full deployment to remote machine
  %(prog)s remote sync     - Quick sync to remote machine
  %(prog)s remote status   - Check remote machine status
        """
    )
    
    parser.add_argument(
        'target',
        choices=['local', 'remote'],
        help='Target: local (central server) or remote (ai-lab)'
    )
    
    parser.add_argument(
        'action',
        choices=['setup', 'install', 'start', 'stop', 'status', 'deploy', 'sync'],
        help='Action to perform'
    )
    
    args = parser.parse_args()
    
    if args.target == 'local':
        if args.action == 'setup':
            setup_local_config()
        elif args.action == 'install':
            install_local_service()
        elif args.action == 'start':
            start_local_service()
        elif args.action == 'stop':
            stop_local_service()
        elif args.action == 'status':
            status_local_service()
        elif args.action == 'deploy':
            deploy_local()
    elif args.target == 'remote':
        if args.action == 'deploy':
            deploy_remote()
        elif args.action == 'sync':
            sync_remote()
        elif args.action == 'status':
            status_remote()


if __name__ == '__main__':
    main()
