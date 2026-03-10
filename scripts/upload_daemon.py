#!/usr/bin/env python3
"""
Upload daemon - continuously uploads data with retry and exponential backoff.
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
UPLOAD_INTERVAL = 300  # seconds between successful uploads (5 minutes)
MAX_RETRIES = 5
BASE_RETRY_DELAY = 10  # seconds

def log(message):
    """Log message with timestamp."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")
    sys.stdout.flush()

def upload_data():
    """Upload data to server."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    upload_script = os.path.join(script_dir, "scripts/upload_to_server.py")
    
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
        return False, f"Exception: {e}"

def upload_with_backoff():
    """Upload with exponential backoff retry."""
    for attempt in range(1, MAX_RETRIES + 1):
        log(f"Upload attempt {attempt}/{MAX_RETRIES}...")
        
        success, message = upload_data()
        
        if success:
            log(f"Upload successful!")
            return True
        else:
            log(f"Upload failed: {message}")
            
            if attempt < MAX_RETRIES:
                delay = BASE_RETRY_DELAY * (2 ** (attempt - 1))  # Exponential backoff
                log(f"Waiting {delay} seconds before retry...")
                time.sleep(delay)
    
    log("All retry attempts exhausted.")
    return False

def main():
    """Main daemon loop."""
    log("Starting upload daemon...")
    log(f"Server: {SERVER_URL}")
    log(f"Hostname: {HOSTNAME}")
    log(f"Check interval: {CHECK_INTERVAL}s")
    log(f"Upload interval: {UPLOAD_INTERVAL}s")
    
    last_upload_time = 0
    
    while True:
        current_time = time.time()
        
        # Check if it's time to upload
        if current_time - last_upload_time >= UPLOAD_INTERVAL:
            log("Starting scheduled upload...")
            
            if upload_with_backoff():
                last_upload_time = current_time
                log(f"Next upload in {UPLOAD_INTERVAL} seconds")
            else:
                log("Upload failed, will retry on next check interval")
        
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Daemon stopped by user")
        sys.exit(0)
