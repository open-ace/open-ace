#!/usr/bin/env python3
"""
Test script to verify stdin fix for launchd/systemd environments.

Tests:
1. Normal stdin (terminal)
2. Closed stdin (simulating launchd without StandardInPath)
3. Invalid stdin fd
"""

import json
import os
import subprocess
import sys
import threading
import time
import uuid

# Import the fix function
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# We'll test by simulating the fix inline


def test_with_closed_stdin() -> dict:
    """Test subprocess stdin behavior when parent stdin is closed."""
    print("\n" + "=" * 60)
    print("Testing: Parent stdin closed (simulating launchd)")
    print("=" * 60)

    # Find qwen
    import shutil

    qwen_path = shutil.which("qwen")
    if not qwen_path:
        print("ERROR: qwen not found")
        return {"success": False, "error": "qwen not found"}

    print(f"qwen path: {qwen_path}")

    results = {
        "test_name": "closed_stdin",
        "stdout_lines": [],
        "stderr_lines": [],
        "sdk_init_sent": False,
        "sdk_init_response": False,
        "timeout": False,
        "broken_pipe": False,
        "success": False,
    }

    sdk_init_received = threading.Event()
    stop_readers = threading.Event()

    def read_stdout(stream):
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
                    print(f"[STDOUT] {text_stripped[:100]}...")
                    try:
                        parsed = json.loads(text_stripped)
                        if parsed.get("type") == "control_response":
                            results["sdk_init_response"] = True
                            sdk_init_received.set()
                            print("[STDOUT] SDK init response received!")
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            print(f"[STDOUT ERROR] {e}")

    def read_stderr(stream):
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

    # Start subprocess with stdin closed in parent
    # This simulates launchd without StandardInPath
    env = dict(os.environ)
    env.pop("TERM", None)  # Simulate launchd environment
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    cmd = [
        qwen_path,
        "--auth-type",
        "openai",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--channel=SDK",
    ]

    print(f"Command: {' '.join(cmd)}")

    # Close stdin before starting subprocess (simulating launchd)
    old_stdin = sys.stdin
    try:
        sys.stdin.close()
        print("Parent stdin closed (simulating launchd)")

        # Now try to start subprocess - this should work with stdin=subprocess.PIPE
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=os.path.expanduser("~/workspace"),
            env=env,
            start_new_session=True,
        )
        print(f"Process started with PID: {process.pid}")

        # Start readers
        stdout_thread = threading.Thread(target=read_stdout, args=(process.stdout,), daemon=True)
        stderr_thread = threading.Thread(target=read_stderr, args=(process.stderr,), daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        time.sleep(1)

        # Send SDK init
        init_request_id = str(uuid.uuid4())
        init_msg = {
            "type": "control_request",
            "request_id": init_request_id,
            "request": {"subtype": "initialize"},
        }

        print("\nSending SDK init...")
        try:
            payload = json.dumps(init_msg) + "\n"
            process.stdin.write(payload.encode("utf-8"))
            process.stdin.flush()
            results["sdk_init_sent"] = True
            print("SDK init sent successfully")
        except BrokenPipeError as e:
            print(f"BROKEN PIPE ERROR: {e}")
            results["broken_pipe"] = True
            results["error"] = str(e)
            stop_readers.set()
            process.terminate()
            process.wait(timeout=5)
            return results
        except Exception as e:
            print(f"ERROR sending: {e}")
            results["error"] = str(e)
            stop_readers.set()
            process.terminate()
            process.wait(timeout=5)
            return results

        # Wait for response
        print("Waiting for SDK init response (timeout=15s)...")
        if sdk_init_received.wait(timeout=15.0):
            print("SUCCESS: SDK init response received!")
        else:
            print("TIMEOUT: No response after 15s")
            results["timeout"] = True

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

        results["success"] = (
            results["sdk_init_response"] and not results["timeout"] and not results["broken_pipe"]
        )

    finally:
        # Restore stdin
        sys.stdin = old_stdin
        if sys.stdin is None or sys.stdin.closed:
            sys.stdin = open("/dev/null")

    print("\nResults:")
    print(f"  SDK init sent: {results['sdk_init_sent']}")
    print(f"  Broken pipe: {results['broken_pipe']}")
    print(f"  SDK init response: {results['sdk_init_response']}")
    print(f"  Timeout: {results['timeout']}")
    print(f"  Success: {results['success']}")

    return results


def test_with_fixed_stdin() -> dict:
    """Test subprocess stdin behavior after applying stdin fix."""
    print("\n" + "=" * 60)
    print("Testing: After applying stdin fix")
    print("=" * 60)

    # Find qwen
    import shutil

    qwen_path = shutil.which("qwen")
    if not qwen_path:
        print("ERROR: qwen not found")
        return {"success": False, "error": "qwen not found"}

    print(f"qwen path: {qwen_path}")

    results = {
        "test_name": "fixed_stdin",
        "stdout_lines": [],
        "stderr_lines": [],
        "sdk_init_sent": False,
        "sdk_init_response": False,
        "timeout": False,
        "broken_pipe": False,
        "success": False,
    }

    sdk_init_received = threading.Event()
    stop_readers = threading.Event()

    def read_stdout(stream):
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
                    print(f"[STDOUT] {text_stripped[:100]}...")
                    try:
                        parsed = json.loads(text_stripped)
                        if parsed.get("type") == "control_response":
                            results["sdk_init_response"] = True
                            sdk_init_received.set()
                            print("[STDOUT] SDK init response received!")
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            print(f"[STDOUT ERROR] {e}")

    def read_stderr(stream):
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

    # Apply stdin fix first (like agent.py does)
    if sys.stdin is None or sys.stdin.closed:
        sys.stdin = open("/dev/null")
        print("Applied stdin fix: reopened to /dev/null")
    else:
        try:
            fd = sys.stdin.fileno()
            if fd < 0:
                sys.stdin = open("/dev/null")
                print(f"Applied stdin fix: invalid fd ({fd}), reopened to /dev/null")
        except (ValueError, OSError):
            sys.stdin = open("/dev/null")
            print("Applied stdin fix: no valid fileno, reopened to /dev/null")

    env = dict(os.environ)
    env.pop("TERM", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    cmd = [
        qwen_path,
        "--auth-type",
        "openai",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--channel=SDK",
    ]

    print(f"Command: {' '.join(cmd)}")

    try:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=os.path.expanduser("~/workspace"),
            env=env,
            start_new_session=True,
        )
        print(f"Process started with PID: {process.pid}")

        stdout_thread = threading.Thread(target=read_stdout, args=(process.stdout,), daemon=True)
        stderr_thread = threading.Thread(target=read_stderr, args=(process.stderr,), daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        time.sleep(1)

        init_request_id = str(uuid.uuid4())
        init_msg = {
            "type": "control_request",
            "request_id": init_request_id,
            "request": {"subtype": "initialize"},
        }

        print("\nSending SDK init...")
        try:
            payload = json.dumps(init_msg) + "\n"
            process.stdin.write(payload.encode("utf-8"))
            process.stdin.flush()
            results["sdk_init_sent"] = True
            print("SDK init sent successfully")
        except BrokenPipeError as e:
            print(f"BROKEN PIPE ERROR: {e}")
            results["broken_pipe"] = True
            results["error"] = str(e)
            stop_readers.set()
            process.terminate()
            process.wait(timeout=5)
            return results
        except Exception as e:
            print(f"ERROR sending: {e}")
            results["error"] = str(e)
            stop_readers.set()
            process.terminate()
            process.wait(timeout=5)
            return results

        print("Waiting for SDK init response (timeout=15s)...")
        if sdk_init_received.wait(timeout=15.0):
            print("SUCCESS: SDK init response received!")
        else:
            print("TIMEOUT: No response after 15s")
            results["timeout"] = True

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

        results["success"] = (
            results["sdk_init_response"] and not results["timeout"] and not results["broken_pipe"]
        )

    except Exception as e:
        print(f"ERROR: {e}")
        results["error"] = str(e)

    print("\nResults:")
    print(f"  SDK init sent: {results['sdk_init_sent']}")
    print(f"  Broken pipe: {results['broken_pipe']}")
    print(f"  SDK init response: {results['sdk_init_response']}")
    print(f"  Timeout: {results['timeout']}")
    print(f"  Success: {results['success']}")

    return results


def main():
    print("=" * 70)
    print("Testing stdin fix for launchd/systemd environments")
    print("=" * 70)

    # Test 1: With closed stdin (problematic scenario)
    result1 = test_with_closed_stdin()

    # Test 2: With stdin fix applied
    result2 = test_with_fixed_stdin()

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    print(f"\n{'Test':<25} {'SDK Init':<12} {'Broken Pipe':<12} {'Success':<10}")
    print("-" * 60)
    print(
        f"{result1['test_name']:<25} {str(result1['sdk_init_response']):<12} {str(result1['broken_pipe']):<12} {str(result1['success']):<10}"
    )
    print(
        f"{result2['test_name']:<25} {str(result2['sdk_init_response']):<12} {str(result2['broken_pipe']):<12} {str(result2['success']):<10}"
    )

    # Diagnosis
    print("\n" + "=" * 70)
    print("DIAGNOSIS")
    print("=" * 70)

    if result1["broken_pipe"] and result2["success"]:
        print("✅ stdin fix is EFFECTIVE!")
        print("   - Without fix: Broken pipe error")
        print("   - With fix: SDK init successful")
    elif result1["success"] and result2["success"]:
        print("⚠️  Both tests passed - stdin closure may not affect subprocess")
        print("   This suggests the issue might be in actual launchd environment")
    elif not result1["success"] and not result2["success"]:
        print("❌ Both tests failed - qwen CLI may not be properly configured")
    else:
        print("⚠️  Mixed results - need further investigation")


if __name__ == "__main__":
    main()
