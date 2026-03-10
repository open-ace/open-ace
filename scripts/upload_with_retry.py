#!/usr/bin/env python3
"""
Upload data to central server with retry mechanism.
"""
import subprocess
import sys
import time
import os

# Configuration
SERVER_URL = "http://192.168.31.208:5001"
AUTH_KEY = "deploy-remote-machine-key-2026"
HOSTNAME = "ai-lab"
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds

def upload_with_retry():
    """Upload data with retry mechanism."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    upload_script = os.path.join(script_dir, "scripts/upload_to_server.py")
    
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"[Attempt {attempt}/{MAX_RETRIES}] Uploading data to {SERVER_URL}...")
        
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
            
            print(result.stdout)
            
            if result.returncode == 0 and "Upload successful" in result.stdout:
                print("[SUCCESS] Data uploaded successfully!")
                return 0
            else:
                print(f"[FAILED] Upload failed: {result.stderr}")
                
        except subprocess.TimeoutExpired:
            print(f"[TIMEOUT] Upload timed out after 120 seconds")
        except Exception as e:
            print(f"[ERROR] Exception occurred: {e}")
        
        if attempt < MAX_RETRIES:
            print(f"[RETRY] Waiting {RETRY_DELAY} seconds before retry...")
            time.sleep(RETRY_DELAY)
    
    print("[FAILED] All retry attempts exhausted. Upload failed.")
    return 1

if __name__ == "__main__":
    sys.exit(upload_with_retry())
