#!/usr/bin/env python3
"""
Upload daemon - continuously fetches data and uploads to central server.
Fetches data before each upload to ensure real-time synchronization.
"""
import subprocess
import sys
import time
import os
from datetime import datetime

# Configuration - can be overridden via environment variables
DEFAULT_PORT = os.environ.get('AI_TOKEN_WEB_PORT', '5001')
SERVER_URL = os.environ.get('AI_TOKEN_SERVER_URL', f"http://192.168.31.208:{DEFAULT_PORT}")
AUTH_KEY = os.environ.get('AI_TOKEN_UPLOAD_AUTH_KEY', 'deploy-remote-machine-key-2026')
HOSTNAME = os.environ.get('AI_TOKEN_HOSTNAME', 'ai-lab')
UPLOAD_INTERVAL = int(os.environ.get('AI_TOKEN_UPLOAD_INTERVAL', '300'))  # seconds between uploads (5 minutes)
MAX_RETRIES = 5
BASE_RETRY_DELAY = 10  # seconds

def log(message):
    """Log message with timestamp."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")
    sys.stdout.flush()

def get_script_path(script_name):
    """Get the full path to a script in the scripts directory."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    # If running from scripts directory, go up one level
    if os.path.basename(base_dir) == 'scripts':
        base_dir = os.path.dirname(base_dir)
    return os.path.join(base_dir, "scripts", script_name)

def fetch_data():
    """Fetch data from OpenClaw."""
    fetch_script = get_script_path("fetch_openclaw.py")

    log("Fetching data from OpenClaw...")
    try:
        result = subprocess.run(
            ["python3", fetch_script, "--days", "1"],
            capture_output=True,
            text=True,
            timeout=300  # 5 minutes timeout for fetch
        )

        if result.returncode == 0:
            log("Data fetch completed successfully")
            return True, result.stdout
        else:
            return False, result.stderr or result.stdout
    except subprocess.TimeoutExpired:
        return False, "Fetch timed out after 300 seconds"
    except Exception as e:
        return False, f"Exception during fetch: {e}"

def upload_data():
    """Upload data to server."""
    upload_script = get_script_path("upload_to_server.py")

    log("Uploading data to server...")
    try:
        result = subprocess.run(
            [
                "python3", upload_script,
                "--server", SERVER_URL,
                "--auth-key", AUTH_KEY,
                "--hostname", HOSTNAME,
                "--days", "1"
            ],
            capture_output=True,
            text=True,
            timeout=120
        )

        if result.returncode == 0 and "Upload successful" in result.stdout:
            return True, result.stdout
        else:
            return False, result.stderr or result.stdout
    except subprocess.TimeoutExpired:
        return False, "Upload timed out after 120 seconds"
    except Exception as e:
        return False, f"Exception during upload: {e}"

def fetch_and_upload():
    """Fetch data and upload to server with retry mechanism."""
    # First, fetch data
    log("Starting data fetch...")
    fetch_success, fetch_message = fetch_data()
    
    if not fetch_success:
        log(f"Fetch failed: {fetch_message}")
        log("Skipping upload, will retry on next cycle")
        return False
    
    # Then, upload with exponential backoff retry
    log("Starting upload...")
    for attempt in range(1, MAX_RETRIES + 1):
        log(f"Upload attempt {attempt}/{MAX_RETRIES}...")
        
        success, message = upload_data()
        
        if success:
            log("Upload successful!")
            return True
        else:
            log(f"Upload failed: {message}")
            
            if attempt < MAX_RETRIES:
                delay = BASE_RETRY_DELAY * (2 ** (attempt - 1))  # Exponential backoff
                log(f"Waiting {delay} seconds before retry...")
                time.sleep(delay)
    
    log("All upload retry attempts exhausted.")
    return False

def main():
    """Main daemon loop."""
    log("=" * 60)
    log("Starting Open ACE Upload Daemon")
    log("=" * 60)
    log(f"Server: {SERVER_URL}")
    log(f"Hostname: {HOSTNAME}")
    log(f"Upload interval: {UPLOAD_INTERVAL}s ({UPLOAD_INTERVAL // 60} minutes)")
    log("Mode: Fetch + Upload on each cycle")

    last_upload_time = 0

    # Initial fetch and upload
    log("Performing initial fetch and upload...")
    fetch_and_upload()
    last_upload_time = time.time()

    while True:
        current_time = time.time()
        elapsed = current_time - last_upload_time
        remaining = UPLOAD_INTERVAL - elapsed
        
        if remaining > 0:
            log(f"Waiting {int(remaining)} seconds until next fetch+upload...")
            time.sleep(min(remaining, 60))  # Check every minute
            continue
        
        # Time to fetch and upload
        log(f"Starting scheduled fetch+upload cycle...")
        if fetch_and_upload():
            last_upload_time = current_time
            log(f"Next cycle in {UPLOAD_INTERVAL // 60} minutes")
        else:
            log("Cycle failed, will retry on next check")
            last_upload_time = current_time  # Still update to avoid rapid retries

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Daemon stopped by user")
        sys.exit(0)
