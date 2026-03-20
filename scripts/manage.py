#!/usr/bin/env python3
"""
Open ACE - Deploy and Manage Script

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
DEV_DIR = BASE_DIR  # Development directory (/Users/rhuang/workspace/open-ace)
DEPLOY_DIR = Path.home() / "open-ace"  # Deployment directory (~/open-ace)
SCRIPTS_DIR = DEPLOY_DIR / "scripts"
CONFIG_DIR = DEPLOY_DIR / "config"
LOG_DIR = DEPLOY_DIR / "logs"

# Remote machine configuration - loaded from config file or environment
# Set REMOTE_HOST environment variable or configure in ~/.open-ace/config.json
REMOTE_USER = os.environ.get("REMOTE_USER", "openclaw")
REMOTE_HOST = os.environ.get("REMOTE_HOST", "")

# Import shared config for web server settings
sys.path.insert(0, str(DEV_DIR / "scripts" / "shared"))
try:
    import config
    WEB_PORT = config.WEB_PORT
    WEB_HOST = config.WEB_HOST
except ImportError:
    # Fallback defaults if config module not available
    WEB_PORT = int(os.environ.get('AI_TOKEN_WEB_PORT', '5001'))
    WEB_HOST = os.environ.get('AI_TOKEN_WEB_HOST', '0.0.0.0')
REMOTE_DIR = os.environ.get("REMOTE_DIR", "/home/openclaw/open-ace")


def get_remote_config() -> dict:
    """Get remote configuration from config file.
    
    Returns dict with host, user, dir, and other settings.
    """
    global REMOTE_HOST, REMOTE_USER, REMOTE_DIR
    
    # If REMOTE_HOST is set via environment, use it
    if REMOTE_HOST:
        return {
            "host": REMOTE_HOST,
            "user": REMOTE_USER,
            "dir": REMOTE_DIR
        }
    
    # Otherwise, try to load from config file
    config_file = Path.home() / ".open-ace" / "config.json"
    if config_file.exists():
        try:
            with open(config_file) as f:
                config = json.load(f)
            
            remote_config = config.get("remote", {})
            if remote_config.get("enabled") and remote_config.get("hosts"):
                host_info = remote_config["hosts"][0]
                return {
                    "host": host_info.get("host", ""),
                    "user": host_info.get("user", "openclaw"),
                    "dir": host_info.get("base_dir", "/home/openclaw/open-ace")
                }
        except (json.JSONDecodeError, IOError) as e:
            print_error(f"Failed to load config: {e}")
    
    return {"host": "", "user": REMOTE_USER, "dir": REMOTE_DIR}


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
    
    config_path = Path.home() / ".open-ace"
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
            # Create default config using WEB_PORT from environment or config
            default_config = {
                "host_name": "localhost",
                "server": {
                    "upload_auth_key": "your-auth-key-here",
                    "server_url": f"http://localhost:{WEB_PORT}"
                },
                "tools": {
                    "openclaw": {"enabled": True, "hostname": "localhost"},
                    "claude": {"enabled": True, "hostname": "localhost"},
                    "qwen": {"enabled": True, "hostname": "localhost"}
                }
            }
            with open(config_file, 'w') as f:
                json.dump(default_config, f, indent=2)
            print_success(f"Created default config: {config_file}")
    else:
        print(f"Config file already exists: {config_file}")


def deploy_local():
    """Deploy to local ~/open-ace directory."""
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
    config_path = Path.home() / ".open-ace"
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
        plist_file = DEPLOY_DIR / "scripts" / "com.open-ace.web.plist"
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
    result = run_command(f"lsof -i :{WEB_PORT}", capture=True, check=False)
    if result and result.returncode == 0:
        print(f"Web server is already running on port {WEB_PORT}")
        return

    # Start web server
    print(f"Starting web server on port {WEB_PORT}... (log: {log_file})")
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
    result = run_command(f"lsof -i :{WEB_PORT}", capture=True, check=False)
    if result and result.returncode == 0:
        print_success(f"Web server started on http://localhost:{WEB_PORT}")
    else:
        print_error("Failed to start web server")


def stop_local_service():
    """Stop local web server."""
    print_header("Stopping Local Web Server")

    # Find and kill process on web port
    if os.uname().sysname == "Darwin":  # macOS
        run_command(f"lsof -ti :{WEB_PORT} | xargs kill -9", check=False)
    else:  # Linux
        run_command(f"fuser -k {WEB_PORT}/tcp", check=False)

    print_success("Web server stopped")


def status_local_service():
    """Check local web server status."""
    print_header("Local Service Status")

    result = run_command(f"lsof -i :{WEB_PORT}", capture=True, check=False)
    if result and result.returncode == 0:
        print(f"Web server is RUNNING on http://localhost:{WEB_PORT}")
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
rm -f com.open-ace.web.plist
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
    
    # Get remote config to determine host info
    remote_cfg = get_remote_config()
    if not remote_cfg["host"]:
        print_error("Remote host not configured. Set REMOTE_HOST environment variable or configure in ~/.open-ace/config.json")
        return
    
    # Generate config template - user should edit with actual credentials
    config_content = f"""{{
  "host_name": "ai-lab",
  "server": {{
    "upload_auth_key": "<UPLOAD_AUTH_KEY>",
    "server_url": "http://<SERVER_IP>:{WEB_PORT}"
  }},
  "tools": {{
    "openclaw": {{
      "enabled": true,
      "token_env": "<OPENCLAW_TOKEN>",
      "gateway_url": "http://localhost:18789",
      "hostname": "ai-lab"
    }}
  }},
  "feishu": {{
    "app_id": "cli_xxxxxxxxxxxxxxxx",
    "app_secret": "your_feishu_app_secret_here"
  }}
}}"""
    print("\n  ⚠️  IMPORTANT: Please edit the config file with your actual credentials!")
    print(f"  Config location: ~{REMOTE_USER}/.open-ace/config.json")
    print("  Required fields to update:")
    print("    - server.upload_auth_key")
    print("    - server.server_url")
    print("    - tools.openclaw.token_env")
    print("    - feishu.app_id")
    print("    - feishu.app_secret")
    
    run_command(f"ssh {REMOTE_USER}@{REMOTE_HOST} \"mkdir -p ~{REMOTE_USER}/.open-ace && cat > ~{REMOTE_USER}/.open-ace/config.json << 'EOF'\n{config_content}\nEOF\"")
    print_success("Configuration template created")
    
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
    run_command(f"rsync -avz scripts/shared/__init__.py scripts/shared/db.py scripts/shared/config.py scripts/shared/utils.py scripts/shared/feishu_user_cache.py scripts/shared/email_notifier.py {REMOTE_USER}@{REMOTE_HOST}:{REMOTE_DIR}/scripts/shared/")
    
    # Sync main scripts
    print("Syncing main scripts...")
    run_command(f"rsync -avz scripts/fetch_openclaw.py scripts/upload_to_server.py {REMOTE_USER}@{REMOTE_HOST}:{REMOTE_DIR}/scripts/")
    
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
        description="Open ACE - Deploy and Manage",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s local deploy    - Deploy to ~/open-ace/
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
