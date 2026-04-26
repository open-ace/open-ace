#!/usr/bin/env python3
"""
Test script to diagnose qwen CLI stdin/stdout behavior in launchd-like environment.

Issue: SDK initialization times out when agent runs under launchd on Mac.
"""

import json
import os
import subprocess
import sys
import threading
import time
import uuid

# Test configurations
TEST_ENVIRONMENTS = [
    {
        "name": "Normal terminal (TERM set)",
        "env_mods": {},
    },
    {
        "name": "launchd-like (TERM unset)",
        "env_mods": {"TERM": None},  # Remove TERM
    },
    {
        "name": "launchd-like with TERM=dumb",
        "env_mods": {"TERM": "dumb"},
    },
    {
        "name": "launchd-like with PYTHONUNBUFFERED=1",
        "env_mods": {"TERM": None, "PYTHONUNBUFFERED": "1"},
    },
]


def test_qwen_cli(env_name: str, env_mods: dict) -> dict:
    """Test qwen CLI subprocess in a specific environment configuration."""
    print(f"\n{'='*60}")
    print(f"Testing: {env_name}")
    print(f"{'='*60}")

    # Build environment
    env = dict(os.environ)
    for key, value in env_mods.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value

    # Also set PYTHONIOENCODING for consistent output
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    # Find qwen executable
    qwen_path = os.path.expanduser("~/.npm-global/bin/qwen")
    if not os.path.exists(qwen_path):
        qwen_path = "/usr/local/bin/qwen"
    if not os.path.exists(qwen_path):
        # Try which
        import shutil
        qwen_path = shutil.which("qwen")
    
    if not qwen_path:
        print(f"ERROR: qwen CLI not found")
        return {"success": False, "error": "qwen not found"}

    print(f"qwen path: {qwen_path}")
    print(f"Environment modifications: {env_mods}")
    print(f"TERM value: {env.get('TERM', 'NOT SET')}")

    # Build command
    cmd = [
        qwen_path,
        "--auth-type", "openai",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--channel=SDK",
    ]

    print(f"Command: {' '.join(cmd)}")

    # Results tracking
    results = {
        "env_name": env_name,
        "env_mods": env_mods,
        "term_value": env.get("TERM", "NOT SET"),
        "stdout_lines": [],
        "stderr_lines": [],
        "sdk_init_sent": False,
        "sdk_init_response": False,
        "timeout": False,
        "process_exit_code": None,
        "start_time": time.time(),
    }

    stdout_ready = threading.Event()
    sdk_init_received = threading.Event()
    stop_readers = threading.Event()

    def read_stdout(stream):
        """Read stdout lines."""
        try:
            while not stop_readers.is_set():
                line = stream.readline()
                if not line:
                    break
                if isinstance(line, bytes):
                    text = line.decode("utf-8", errors="replace")
                else:
                    text = line
                text_stripped = text.strip()
                if text_stripped:
                    results["stdout_lines"].append(text_stripped)
                    print(f"[STDOUT] {text_stripped[:100]}")
                    
                    # Check for SDK init response
                    try:
                        parsed = json.loads(text_stripped)
                        if parsed.get("type") == "control_response":
                            results["sdk_init_response"] = True
                            sdk_init_received.set()
                            print("[STDOUT] SDK init response received!")
                    except json.JSONDecodeError:
                        pass
                    
                    stdout_ready.set()
        except Exception as e:
            print(f"[STDOUT ERROR] {e}")
        finally:
            stdout_ready.set()

    def read_stderr(stream):
        """Read stderr lines."""
        try:
            while not stop_readers.is_set():
                line = stream.readline()
                if not line:
                    break
                if isinstance(line, bytes):
                    text = line.decode("utf-8", errors="replace")
                else:
                    text = line
                text_stripped = text.strip()
                if text_stripped:
                    results["stderr_lines"].append(text_stripped)
                    print(f"[STDERR] {text_stripped[:100]}")
        except Exception as e:
            print(f"[STDERR ERROR] {e}")

    # Start subprocess
    try:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=os.path.expanduser("~/workspace"),
            env=env,
            start_new_session=True,  # Same as executor.py
        )
        print(f"Process started with PID: {process.pid}")
    except Exception as e:
        print(f"ERROR starting process: {e}")
        results["error"] = str(e)
        return results

    # Start reader threads
    stdout_thread = threading.Thread(target=read_stdout, args=(process.stdout,), daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, args=(process.stderr,), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    # Wait briefly for any initial output (should be none in SDK mode)
    time.sleep(1)

    # Send SDK init message
    init_request_id = str(uuid.uuid4())
    init_msg = {
        "type": "control_request",
        "request_id": init_request_id,
        "request": {
            "subtype": "initialize",
        },
    }
    
    print(f"\nSending SDK init (request_id={init_request_id[:8]}...)")
    try:
        payload = json.dumps(init_msg) + "\n"
        process.stdin.write(payload.encode("utf-8"))
        process.stdin.flush()
        results["sdk_init_sent"] = True
        print("SDK init sent successfully")
    except Exception as e:
        print(f"ERROR sending SDK init: {e}")
        results["error"] = str(e)
        stop_readers.set()
        process.terminate()
        process.wait(timeout=5)
        return results

    # Wait for SDK init response (timeout 15s, same as executor.py)
    print("Waiting for SDK init response (timeout=15s)...")
    if sdk_init_received.wait(timeout=15.0):
        print("SUCCESS: SDK init response received!")
        results["timeout"] = False
    else:
        print("TIMEOUT: No SDK init response after 15s")
        results["timeout"] = True

    # Check process status
    process.poll()
    if process.returncode is not None:
        print(f"Process exited with code: {process.returncode}")
        results["process_exit_code"] = process.returncode
    else:
        print(f"Process still running (PID: {process.pid})")

    # Cleanup
    stop_readers.set()
    try:
        process.stdin.close()
    except:
        pass
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)

    stdout_thread.join(timeout=2)
    stderr_thread.join(timeout=2)

    results["end_time"] = time.time()
    results["duration"] = results["end_time"] - results["start_time"]
    results["success"] = results["sdk_init_response"] and not results["timeout"]

    print(f"\nResults for {env_name}:")
    print(f"  SDK init sent: {results['sdk_init_sent']}")
    print(f"  SDK init response: {results['sdk_init_response']}")
    print(f"  Timeout: {results['timeout']}")
    print(f"  Process exit code: {results['process_exit_code']}")
    print(f"  Duration: {results['duration']:.2f}s")
    print(f"  Success: {results['success']}")

    return results


def main():
    """Run all test configurations."""
    print("=" * 70)
    print("Testing qwen CLI stdin/stdout behavior in different environments")
    print("=" * 70)

    all_results = []

    for config in TEST_ENVIRONMENTS:
        result = test_qwen_cli(config["name"], config["env_mods"])
        all_results.append(result)
        time.sleep(2)  # Brief pause between tests

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"\n{'Environment':<40} {'SDK Response':<15} {'Timeout':<10} {'Success':<10}")
    print("-" * 75)
    for r in all_results:
        print(f"{r['env_name']:<40} {str(r['sdk_init_response']):<15} {str(r['timeout']):<10} {str(r['success']):<10}")

    # Determine root cause
    print("\n" + "=" * 70)
    print("DIAGNOSIS")
    print("=" * 70)

    success_count = sum(1 for r in all_results if r["success"])
    if success_count == len(all_results):
        print("All tests passed - problem may not be reproducible in this test environment")
    elif success_count == 0:
        print("All tests failed - qwen CLI may not be properly installed or configured")
    else:
        # Find which config works vs doesn't
        working = [r for r in all_results if r["success"]]
        failing = [r for r in all_results if not r["success"]]
        
        print(f"Working configurations: {len(working)}")
        for r in working:
            print(f"  - {r['env_name']} (TERM={r['term_value']})")
        
        print(f"Failing configurations: {len(failing)}")
        for r in failing:
            print(f"  - {r['env_name']} (TERM={r['term_value']})")
        
        # Compare to find root cause
        if len(working) > 0 and len(failing) > 0:
            working_term = working[0]["term_value"]
            failing_term = failing[0]["term_value"]
            print(f"\nPotential root cause: TERM environment variable")
            print(f"  Working TERM: {working_term}")
            print(f"  Failing TERM: {failing_term}")


if __name__ == "__main__":
    main()