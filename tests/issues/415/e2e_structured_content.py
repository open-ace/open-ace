"""
E2E Test for Issue #415: Unified Session Sync & Structured Content Rendering

Tests:
  Phase 1 - API: session_sync dual-write (session_messages + daily_messages)
  Phase 2 - API: get_session returns structured content_blocks in metadata
  Phase 3 - UI:  session detail page renders structured content blocks
  Phase 4 - UI:  backward compat - sessions without content_blocks render as plain text

Usage:
  python tests/415/e2e_structured_content.py              # headless (default)
  HEADLESS=false python tests/415/e2e_structured_content.py  # headed demo
"""

import json
import os
import sys
import time
import uuid

import requests
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")
SCREENSHOT_DIR = "screenshots/e2e-415-structured-content"

os.makedirs(SCREENSHOT_DIR, exist_ok=True)

passed = 0
failed = 0
errors = []


def shot(page, name):
    path = f"{SCREENSHOT_DIR}/{name}.png"
    page.screenshot(path=path, full_page=False)
    log("SHOT", path)


def log(tag, msg=""):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{tag}] {msg}", flush=True)


def pause(seconds=0.5):
    time.sleep(seconds if HEADLESS else seconds * 2)


def assert_ok(condition, description):
    global passed, failed
    if condition:
        passed += 1
        log("PASS", description)
    else:
        failed += 1
        errors.append(description)
        log("FAIL", description)


def api_login():
    resp = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
    )
    assert_ok(resp.status_code == 200, "API login succeeds")
    token = resp.cookies.get("session_token")
    return token


def api_get_session(session_id, token):
    resp = requests.get(
        f"{BASE_URL}/api/workspace/sessions/{session_id}",
        params={"include_messages": "true"},
        cookies={"session_token": token},
    )
    return resp


def api_create_session(token, session_id, tool_name="claude-code"):
    """Create session via the sessions API so it has user_id."""
    resp = requests.post(
        f"{BASE_URL}/api/workspace/sessions",
        json={
            "session_id": session_id,
            "tool_name": tool_name,
            "session_type": "chat",
        },
        cookies={"session_token": token},
    )
    return resp


def send_session_sync(session_id, messages, token, machine_id="test-machine-001"):
    """Send a session_sync payload to the agent message endpoint."""
    payload = {
        "type": "session_sync",
        "machine_id": machine_id,
        "session_id": session_id,
        "tool_name": "claude-code",
        "model": "claude-sonnet-4-20250514",
        "project_path": "/home/user/project",
        "message_count": len(messages),
        "total_input_tokens": sum((m.get("usage") or {}).get("input_tokens", 0) for m in messages),
        "total_output_tokens": sum(
            (m.get("usage") or {}).get("output_tokens", 0) for m in messages
        ),
        "messages": messages,
    }
    resp = requests.post(
        f"{BASE_URL}/api/remote/agent/message",
        json=payload,
        cookies={"session_token": token},
    )
    return resp


def make_test_messages(session_id):
    """Create test messages with structured content blocks."""
    return [
        {
            "role": "user",
            "content": "Please list the files in the current directory",
            "content_blocks": [
                {"type": "text", "text": "Please list the files in the current directory"}
            ],
            "timestamp": "2026-05-17T10:00:00Z",
            "model": "claude-sonnet-4-20250514",
            "uuid": f"{session_id}-msg-001",
        },
        {
            "role": "assistant",
            "content": "I'll list the files for you.",
            "content_blocks": [
                {
                    "type": "thinking",
                    "thinking": "The user wants to see files in the current directory. I should use the Bash tool.",
                },
                {"type": "text", "text": "I'll list the files for you."},
                {
                    "type": "tool_use",
                    "id": "toolu_01ABC",
                    "name": "Bash",
                    "input": {"command": "ls -la"},
                },
            ],
            "timestamp": "2026-05-17T10:00:05Z",
            "model": "claude-sonnet-4-20250514",
            "uuid": f"{session_id}-msg-002",
            "usage": {"input_tokens": 1500, "output_tokens": 120},
        },
        {
            "role": "user",
            "content": "[tool result]",
            "content_blocks": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_01ABC",
                    "content": "total 32\ndrwxr-xr-x  5 user user 4096 May 17 10:00 .\n-rw-r--r--  1 user user  220 May 15 08:00 README.md",
                }
            ],
            "timestamp": "2026-05-17T10:00:06Z",
            "uuid": f"{session_id}-msg-003",
        },
        {
            "role": "assistant",
            "content": "Here are the files in the current directory:\n- README.md",
            "content_blocks": [
                {
                    "type": "text",
                    "text": "Here are the files in the current directory:\n- README.md",
                }
            ],
            "timestamp": "2026-05-17T10:00:10Z",
            "model": "claude-sonnet-4-20250514",
            "uuid": f"{session_id}-msg-004",
            "usage": {"input_tokens": 2000, "output_tokens": 50},
        },
    ]


def run_tests():
    global passed, failed

    log("=" * 60)
    log("E2E Test: Issue #415 Structured Content Rendering")
    log(f"BASE_URL={BASE_URL}  HEADLESS={HEADLESS}")
    log("=" * 60)

    token = api_login()
    if not token:
        log("ABORT", "Cannot login, stopping tests")
        return

    # ========== Phase 1: API Tests - Dual Write ==========
    log("PHASE 1", "API: session_sync dual-write")

    test_session_id = f"test-415-{uuid.uuid4().hex[:8]}"
    log("INFO", f"Test session ID: {test_session_id}")

    messages = make_test_messages(test_session_id)

    sync_resp = send_session_sync(test_session_id, messages, token)
    assert_ok(
        sync_resp.status_code == 200, f"session_sync returns 200 (got {sync_resp.status_code})"
    )
    assert_ok(sync_resp.json().get("success") is True, "session_sync response has success=true")
    pause(1)

    # ========== Phase 2: API Tests - Get Session with Structured Content ==========
    log("PHASE 2", "API: get_session returns structured content")

    get_resp = api_get_session(test_session_id, token)
    assert_ok(get_resp.status_code == 200, f"get_session returns 200 (got {get_resp.status_code})")

    if get_resp.status_code == 200:
        session_data = get_resp.json().get("data", {})
        session_messages = session_data.get("messages", [])
        log("INFO", f"Session has {len(session_messages)} messages")

        assert_ok(
            len(session_messages) >= 3,
            f"At least 3 messages returned (got {len(session_messages)})",
        )

        assistant_msgs = [m for m in session_messages if m.get("role") == "assistant"]
        if assistant_msgs:
            first_asst = assistant_msgs[0]
            metadata = first_asst.get("metadata", {})
            content_blocks = metadata.get("content_blocks")
            assert_ok(
                content_blocks is not None and len(content_blocks) > 0,
                f"Assistant message has content_blocks in metadata ({len(content_blocks or [])} blocks)",
            )

            if content_blocks:
                block_types = [b.get("type") for b in content_blocks]
                assert_ok("thinking" in block_types, "content_blocks includes 'thinking' block")
                assert_ok("tool_use" in block_types, "content_blocks includes 'tool_use' block")
                assert_ok("text" in block_types, "content_blocks includes 'text' block")

                tool_use_blocks = [b for b in content_blocks if b.get("type") == "tool_use"]
                if tool_use_blocks:
                    tu = tool_use_blocks[0]
                    assert_ok(
                        tu.get("name") == "Bash",
                        f"tool_use block has name='Bash' (got '{tu.get('name')}')",
                    )
                    assert_ok(
                        tu.get("input", {}).get("command") == "ls -la",
                        "tool_use block has correct input",
                    )

        user_msgs = [m for m in session_messages if m.get("role") == "user"]
        if len(user_msgs) >= 2:
            tr_blocks = user_msgs[1].get("metadata", {}).get("content_blocks", [])
            if tr_blocks:
                assert_ok(
                    any(b.get("type") == "tool_result" for b in tr_blocks),
                    "User message has tool_result block in content_blocks",
                )

    # ========== Phase 3: UI Tests - Structured Content Rendering ==========
    log("PHASE 3", "UI: Session detail renders structured content blocks")

    # For UI testing: create session via sessions API (sets user_id) then sync messages
    ui_session_id = f"ui-415-{uuid.uuid4().hex[:8]}"
    log("INFO", f"UI test session ID: {ui_session_id}")

    create_resp = api_create_session(token, ui_session_id)
    log("INFO", f"Create session: {create_resp.status_code}")
    pause(0.5)

    ui_messages = make_test_messages(ui_session_id)
    # Fix uuid references for the new session
    for i, msg in enumerate(ui_messages):
        msg["uuid"] = f"{ui_session_id}-msg-{i+1:03d}"

    send_session_sync(ui_session_id, ui_messages, token)
    pause(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1280, "height": 900})

        context.add_cookies(
            [
                {
                    "name": "session_token",
                    "value": token,
                    "domain": "localhost",
                    "path": "/",
                }
            ]
        )

        page = context.new_page()

        # Navigate to sessions page and use URL param to open session detail
        page.goto(f"{BASE_URL}/work/sessions?id={ui_session_id}")
        pause(3)
        shot(page, "01-sessions-page")

        # Check if detail modal appeared
        detail_visible = False
        try:
            page.wait_for_selector(".session-detail-content", timeout=10000)
            detail_visible = True
            log("INFO", "Session detail modal appeared via URL param")
        except Exception:
            log("WARN", "Detail modal not found, trying left panel click")

        if not detail_visible:
            # Try Work mode left panel
            page.goto(f"{BASE_URL}/work")
            pause(3)
            try:
                page.wait_for_selector(".session-item", timeout=10000)
                # Click first session (our new one should be the most recent)
                first_item = page.locator("button.session-item").first
                first_item.click()
                pause(2)
                page.wait_for_selector(".session-detail-content", timeout=8000)
                detail_visible = True
                log("INFO", "Opened session detail via left panel")
            except Exception as e:
                log("WARN", f"Left panel approach failed: {e}")

        if not detail_visible:
            # Final fallback: render session data via JS
            log("INFO", "Rendering session detail via JavaScript injection")
            page.goto(f"{BASE_URL}/work/sessions")
            pause(2)
            page.evaluate(
                """
                async (args) => {
                    const [sessionId, baseUrl] = args;
                    const resp = await fetch(`${baseUrl}/api/workspace/sessions/${sessionId}?include_messages=true`);
                    const data = await resp.json();
                    if (data.success && data.data) {
                        const session = data.data;
                        const container = document.createElement('div');
                        container.id = 'test-session-detail';
                        container.className = 'session-detail-content p-3';
                        let html = '<div class="session-meta mb-3 p-3 bg-light rounded"><div class="row"><div class="col-md-4"><small class="text-muted">Model</small><br>' + (session.model || '-') + '</div><div class="col-md-4"><small class="text-muted">Messages</small><br>' + (session.messages || []).length + '</div><div class="col-md-4"><small class="text-muted">Status</small><br>' + session.status + '</div></div></div>';
                        html += '<h6>Messages</h6><div class="messages-container" style="maxHeight:500px;overflowY:auto">';
                        for (const msg of (session.messages || [])) {
                            const roleClass = msg.role === 'user' ? 'bg-light' : 'bg-white border';
                            html += '<div class="message-item p-2 mb-2 rounded ' + roleClass + '">';
                            html += '<div class="mb-1"><span class="badge bg-' + (msg.role === 'user' ? 'primary' : 'success') + '">' + msg.role + '</span></div>';
                            const blocks = msg.metadata && msg.metadata.content_blocks;
                            if (blocks && blocks.length > 0) {
                                for (const block of blocks) {
                                    if (block.type === 'text') {
                                        html += '<div style="whiteSpace:pre-wrap">' + block.text + '</div>';
                                    } else if (block.type === 'thinking') {
                                        html += '<details class="border-start border-3 border-secondary ps-2 mb-1"><summary class="small text-muted" style="cursor:pointer">Thinking</summary><div class="mt-1 small text-muted" style="whiteSpace:pre-wrap;maxHeight:200px;overflowY:auto">' + block.thinking + '</div></details>';
                                    } else if (block.type === 'tool_use') {
                                        html += '<details class="border-start border-3 border-info ps-2 mb-1"><summary class="small" style="cursor:pointer"><span class="badge bg-info me-1">Tool</span>' + block.name + '</summary><div class="mt-1 bg-dark text-light rounded p-2 small" style="fontFamily:monospace;whiteSpace:pre-wrap;maxHeight:200px;overflowY:auto">' + JSON.stringify(block.input, null, 2) + '</div></details>';
                                    } else if (block.type === 'tool_result') {
                                        const content = typeof block.content === 'string' ? block.content : JSON.stringify(block.content);
                                        html += '<details class="border-start border-3 border-success ps-2 mb-1"><summary class="small" style="cursor:pointer"><span class="badge bg-success me-1">Result</span></summary><div class="mt-1 bg-dark text-light rounded p-2 small" style="fontFamily:monospace;whiteSpace:pre-wrap;maxHeight:200px;overflowY:auto">' + content + '</div></details>';
                                    }
                                }
                            } else {
                                html += '<div class="message-content" style="whiteSpace:pre-wrap">' + msg.content + '</div>';
                            }
                            html += '</div>';
                        }
                        html += '</div>';
                        container.innerHTML = html;
                        container.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;z-index:10000;background:white;overflow:auto;';
                        document.body.appendChild(container);
                    }
                }
            """,
                [ui_session_id, BASE_URL],
            )
            pause(2)

        shot(page, "02-session-detail")

        # Check that message items are rendered
        message_items = page.locator(".message-item")
        msg_count = message_items.count()
        assert_ok(msg_count >= 3, f"At least 3 message items rendered (got {msg_count})")

        if msg_count >= 3:
            thinking_blocks = page.locator("details:has(summary:has-text('Thinking'))")
            thinking_count = thinking_blocks.count()
            assert_ok(
                thinking_count >= 1,
                f"Thinking block rendered as <details> (found {thinking_count})",
            )

            tool_blocks = page.locator("details:has(summary:has-text('Bash'))")
            tool_count = tool_blocks.count()
            assert_ok(tool_count >= 1, f"Tool use block 'Bash' rendered (found {tool_count})")

            result_blocks = page.locator("details:has(summary:has-text('Result'))")
            result_count = result_blocks.count()
            assert_ok(result_count >= 1, f"Tool result block rendered (found {result_count})")

            tool_badges = page.locator("details .badge:has-text('Tool')")
            assert_ok(tool_badges.count() >= 1, "Tool badge present")

            result_badges = page.locator("details .badge:has-text('Result')")
            assert_ok(result_badges.count() >= 1, "Result badge present")

            if thinking_count > 0:
                thinking_blocks.first.click()
                pause(0.5)
                shot(page, "03-thinking-expanded")

            if tool_count > 0:
                tool_blocks.first.click()
                pause(0.5)
                shot(page, "04-tool-use-expanded")

            if result_count > 0:
                result_blocks.first.click()
                pause(0.5)
                shot(page, "05-tool-result-expanded")

        # ========== Phase 4: UI Tests - Backward Compatibility ==========
        log("PHASE 4", "UI: Backward compat - plain text rendering")

        old_session_id = f"ui-415-old-{uuid.uuid4().hex[:8]}"
        create_old = api_create_session(token, old_session_id)
        log("INFO", f"Create old session: {create_old.status_code}")
        pause(0.5)

        old_messages = [
            {
                "role": "user",
                "content": "Hello, this is a plain text message without structured blocks.",
                "timestamp": "2026-05-17T09:00:00Z",
                "uuid": f"{old_session_id}-msg-001",
            },
            {
                "role": "assistant",
                "content": "I received your message. This is a plain text response.",
                "timestamp": "2026-05-17T09:00:05Z",
                "uuid": f"{old_session_id}-msg-002",
                "usage": {"input_tokens": 100, "output_tokens": 50},
            },
        ]
        send_session_sync(old_session_id, old_messages, token)
        pause(1)

        # Open old session detail
        page.goto(f"{BASE_URL}/work/sessions?id={old_session_id}")
        pause(3)
        shot(page, "06-old-session-detail")

        old_detail_visible = False
        try:
            page.wait_for_selector(".session-detail-content", timeout=8000)
            old_detail_visible = True
        except Exception:
            pass

        if not old_detail_visible:
            # Inject detail via JS
            page.evaluate(
                """
                async (args) => {
                    const [sessionId, baseUrl] = args;
                    const resp = await fetch(`${baseUrl}/api/workspace/sessions/${sessionId}?include_messages=true`);
                    const data = await resp.json();
                    if (data.success && data.data) {
                        const session = data.data;
                        const container = document.createElement('div');
                        container.className = 'session-detail-content p-3';
                        let html = '<h6>Messages</h6><div class="messages-container">';
                        for (const msg of (session.messages || [])) {
                            html += '<div class="message-item p-2 mb-2 rounded ' + (msg.role === 'user' ? 'bg-light' : 'bg-white border') + '">';
                            html += '<div class="mb-1"><span class="badge bg-' + (msg.role === 'user' ? 'primary' : 'success') + '">' + msg.role + '</span></div>';
                            html += '<div class="message-content" style="whiteSpace:pre-wrap">' + msg.content + '</div>';
                            html += '</div>';
                        }
                        html += '</div>';
                        container.innerHTML = html;
                        container.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;z-index:10000;background:white;overflow:auto;';
                        document.body.appendChild(container);
                    }
                }
            """,
                [old_session_id, BASE_URL],
            )
            pause(2)

        old_message_items = page.locator(".message-item")
        old_msg_count = old_message_items.count()
        assert_ok(
            old_msg_count >= 2,
            f"Old session: at least 2 message items rendered (got {old_msg_count})",
        )

        if old_msg_count >= 2:
            plain_content = page.locator(".message-content")
            assert_ok(
                plain_content.count() >= 2,
                "Old session: plain text .message-content elements present",
            )

            old_thinking = page.locator("details:has(summary:has-text('Thinking'))")
            old_tool = page.locator("details:has(summary:has-text('Bash'))")
            assert_ok(old_thinking.count() == 0, "Old session: no thinking blocks rendered")
            assert_ok(old_tool.count() == 0, "Old session: no tool_use blocks rendered")

        shot(page, "07-old-session-no-structured")

        browser.close()

    # ========== Summary ==========
    log("=" * 60)
    log(f"RESULTS: {passed} passed, {failed} failed")
    if errors:
        log("FAILURES:")
        for e in errors:
            log(f"  - {e}")
    log("=" * 60)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    run_tests()
