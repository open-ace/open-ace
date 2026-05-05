#!/usr/bin/env python3
"""
Test script to verify qwen CLI enters stream-json mode correctly.

Tests:
1. SDK initialization
2. Sending user message in stream-json format
3. Receiving result/response in stream-json format
"""

import contextlib
import json
import os
import subprocess
import threading
import time
import uuid

# Test configurations
TEST_ENVIRONMENTS = [
    {
        "name": "Normal terminal (TERM=xterm)",
        "env_mods": {},
    },
    {
        "name": "launchd-like (TERM unset)",
        "env_mods": {"TERM": None},
    },
]


def test_stream_json_mode(env_name: str, env_mods: dict) -> dict:
    """Test if qwen CLI correctly enters and operates in stream-json mode."""
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

    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    # Find qwen executable
    import shutil

    qwen_path = shutil.which("qwen")

    if not qwen_path:
        print("ERROR: qwen CLI not found")
        return {"success": False, "error": "qwen not found"}

    print(f"qwen path: {qwen_path}")
    print(f"TERM value: {env.get('TERM', 'NOT SET')}")

    # Build command - use stream-json format
    cmd = [
        qwen_path,
        "--auth-type",
        "openai",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--channel=SDK",
        "--approval-mode",
        "yolo",  # Avoid permission prompts
    ]

    print(f"Command: {' '.join(cmd)}")

    results = {
        "env_name": env_name,
        "term_value": env.get("TERM", "NOT SET"),
        "stdout_lines": [],
        "stderr_lines": [],
        "sdk_init_sent": False,
        "sdk_init_response": False,
        "user_msg_sent": False,
        "user_msg_response": False,
        "json_output_count": 0,
        "non_json_output": [],
        "timeout": False,
        "success": False,
    }

    sdk_init_received = threading.Event()
    user_response_received = threading.Event()
    stop_readers = threading.Event()

    def read_stdout(stream):
        """Read stdout lines and parse JSON."""
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
                    print(f"[STDOUT] {text_stripped[:150]}...")

                    try:
                        parsed = json.loads(text_stripped)
                        results["json_output_count"] += 1
                        msg_type = parsed.get("type")

                        if msg_type == "control_response":
                            resp = parsed.get("response", {})
                            if resp.get("subtype") == "success":
                                results["sdk_init_response"] = True
                                sdk_init_received.set()
                                print("[STDOUT] SDK init SUCCESS!")

                        elif msg_type == "result":
                            results["user_msg_response"] = True
                            user_response_received.set()
                            print("[STDOUT] User message RESULT received!")

                        elif msg_type == "assistant":
                            # Stream message from assistant
                            print("[STDOUT] Assistant message streaming...")

                    except json.JSONDecodeError:
                        results["non_json_output"].append(text_stripped)
                        print(f"[STDOUT] NON-JSON OUTPUT: {text_stripped[:100]}")

        except Exception as e:
            print(f"[STDOUT ERROR] {e}")

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
            start_new_session=True,
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

    # Wait 1s to check if any initial non-JSON output (indicates interactive mode)
    time.sleep(1)

    # Check for non-JSON output before sending anything
    if results["non_json_output"]:
        print("\nWARNING: Non-JSON output detected before SDK init!")
        print("This suggests CLI entered interactive mode instead of stream-json mode")
        for line in results["non_json_output"][:5]:
            print(f"  {line[:80]}")

    # Step 1: Send SDK init
    init_request_id = str(uuid.uuid4())
    init_msg = {
        "type": "control_request",
        "request_id": init_request_id,
        "request": {"subtype": "initialize"},
    }

    print("\n[STEP 1] Sending SDK init...")
    try:
        payload = json.dumps(init_msg) + "\n"
        process.stdin.write(payload.encode("utf-8"))
        process.stdin.flush()
        results["sdk_init_sent"] = True
    except Exception as e:
        print(f"ERROR sending SDK init: {e}")
        results["error"] = str(e)
        stop_readers.set()
        process.terminate()
        process.wait(timeout=5)
        return results

    # Wait for SDK init response
    print("Waiting for SDK init response (timeout=10s)...")
    if sdk_init_received.wait(timeout=10.0):
        print("SDK init response received!")
    else:
        print("SDK init TIMEOUT!")
        results["timeout"] = True
        stop_readers.set()
        process.terminate()
        process.wait(timeout=5)
        results["success"] = False
        return results

    # Step 2: Send user message
    session_id = str(uuid.uuid4())
    user_msg = {
        "type": "user",
        "session_id": session_id,
        "message": {
            "role": "user",
            "content": "What is 2 + 2? Answer briefly.",
        },
        "parent_tool_use_id": None,
    }

    print("\n[STEP 2] Sending user message...")
    try:
        payload = json.dumps(user_msg) + "\n"
        process.stdin.write(payload.encode("utf-8"))
        process.stdin.flush()
        results["user_msg_sent"] = True
        print("User message sent!")
    except Exception as e:
        print(f"ERROR sending user message: {e}")
        results["error"] = str(e)
        stop_readers.set()
        process.terminate()
        process.wait(timeout=5)
        return results

    # Wait for user message response (assistant or result)
    print("Waiting for user message response (timeout=30s)...")
    if user_response_received.wait(timeout=30.0):
        print("User message response received!")
    else:
        print("User message response TIMEOUT!")
        results["timeout"] = True

    # Cleanup
    stop_readers.set()
    with contextlib.suppress(BaseException):
        process.stdin.close()
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)

    stdout_thread.join(timeout=2)
    stderr_thread.join(timeout=2)

    # Determine success
    results["success"] = (
        results["sdk_init_sent"]
        and results["sdk_init_response"]
        and results["user_msg_sent"]
        and results["user_msg_response"]
        and len(results["non_json_output"]) == 0  # No non-JSON output
    )

    print(f"\n{'='*60}")
    print(f"Results for: {env_name}")
    print(f"{'='*60}")
    print(f"  SDK init sent:     {results['sdk_init_sent']}")
    print(f"  SDK init response: {results['sdk_init_response']}")
    print(f"  User msg sent:     {results['user_msg_sent']}")
    print(f"  User msg response: {results['user_msg_response']}")
    print(f"  JSON output count: {results['json_output_count']}")
    print(f"  Non-JSON output:   {len(results['non_json_output'])} lines")
    if results["non_json_output"]:
        print("    (Non-JSON lines indicate interactive mode)")
    print(f"  Timeout:           {results['timeout']}")
    print(f"  SUCCESS:           {results['success']}")

    return results


def main():
    """Run all tests."""
    print("=" * 70)
    print("Testing qwen CLI stream-json mode functionality")
    print("=" * 70)

    all_results = []

    for config in TEST_ENVIRONMENTS:
        result = test_stream_json_mode(config["name"], config["env_mods"])
        all_results.append(result)
        time.sleep(2)

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    print(
        f"\n{'Environment':<35} {'SDK Init':<12} {'User Msg':<12} {'Non-JSON':<10} {'Success':<10}"
    )
    print("-" * 70)
    for r in all_results:
        non_json = len(r.get("non_json_output", []))
        print(
            f"{r['env_name']:<35} {str(r['sdk_init_response']):<12} {str(r['user_msg_response']):<12} {non_json:<10} {str(r['success']):<10}"
        )

    # Diagnosis
    print("\n" + "=" * 70)
    print("DIAGNOSIS")
    print("=" * 70)

    for r in all_results:
        if r["non_json_output"]:
            print(f"\n{r['env_name']}: CLI produced non-JSON output!")
            print("This indicates CLI entered INTERACTIVE MODE instead of stream-json mode")
            print("Non-JSON output samples:")
            for line in r["non_json_output"][:3]:
                print(f"  '{line[:60]}...'")
        elif r["success"]:
            print(f"\n{r['env_name']}: CLI correctly entered STREAM-JSON mode ✅")
        else:
            print(f"\n{r['env_name']}: Test failed - timeout or other issue")


if __name__ == "__main__":
    main()
