#!/usr/bin/env python3
"""
Upload daemon - continuously fetches data and uploads to central server.
Integrates fetch_openclaw.py and upload_to_server.py with retry mechanism.
"""
import subprocess
import sys
import time
import os
from datetime import datetime

# Configuration
SERVER_URL = "http://192.168.31.208:5001"
AUTH_KEY = "deploy-remote-machine-key-2026"
HOSTNAME = "ai-lab"
CHECK_INTERVAL = 60  # seconds between checks
FETCH_INTERVAL = 1800  # seconds between data fetches (30 minutes)
UPLOAD_INTERVAL = 300  # seconds between uploads (5 minutes)
MAX_RETRIES = 5
BASE_RETRY_DELAY = 10  # seconds

def log(message):
    """Log message with timestamp."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")
    sys.stdout.flush()

def fetch_data():
    """Fetch data from OpenClaw."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    # If running from scripts directory, go up one level
    if os.path.basename(base_dir) == 'scripts':
        base_dir = os.path.dirname(base_dir)
    fetch_script = os.path.join(base_dir, "scripts", "fetch_openclaw.py")

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
    base_dir = os.path.dirname(os.path.abspath(__file__))
    # If running from scripts directory, go up one level
    if os.path.basename(base_dir) == 'scripts':
        base_dir = os.path.dirname(base_dir)
    upload_script = os.path.join(base_dir, "scripts", "upload_to_server.py")

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

def fetch_with_backoff():
    """Fetch data with retry."""
    for attempt in range(1, 3):  # Fewer retries for fetch
        success, message = fetch_data()
        if success:
            return True
        else:
            log(f"Fetch failed: {message}")
            if attempt < 2:
                log("Waiting 30 seconds before retry...")
                time.sleep(30)
    return False

def upload_with_backoff():
    """Upload with exponential backoff retry."""
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
    log("Starting AI Token Analyzer Upload Daemon")
    log("=" * 60)
    log(f"Server: {SERVER_URL}")
    log(f"Hostname: {HOSTNAME}")
    log(f"Fetch interval: {FETCH_INTERVAL}s ({FETCH_INTERVAL // 60} minutes)")
    log(f"Upload interval: {UPLOAD_INTERVAL}s ({UPLOAD_INTERVAL // 60} minutes)")
    log(f"Check interval: {CHECK_INTERVAL}s")

    last_fetch_time = 0
    last_upload_time = 0

    # Initial fetch and upload
    log("Performing initial data fetch and upload...")
    fetch_with_backoff()
    upload_with_backoff()
    last_fetch_time = time.time()
    last_upload_time = time.time()

    while True:
        current_time = time.time()

        # Check if it's time to fetch data
        if current_time - last_fetch_time >= FETCH_INTERVAL:
            log("Starting scheduled data fetch...")
            if fetch_with_backoff():
                last_fetch_time = current_time
                log(f"Next fetch in {FETCH_INTERVAL // 60} minutes")
            else:
                log("Fetch failed, will retry on next check")

        # Check if it's time to upload
        if current_time - last_upload_time >= UPLOAD_INTERVAL:
            log("Starting scheduled upload...")
            if upload_with_backoff():
                last_upload_time = current_time
                log(f"Next upload in {UPLOAD_INTERVAL // 60} minutes")
            else:
                log("Upload failed, will retry on next check")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Daemon stopped by user")
        sys.exit(0)
