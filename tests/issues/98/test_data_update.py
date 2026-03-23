#!/usr/bin/env python3
"""
Test for Issue #98: 刷新后数据没有变化

测试内容：
1. 获取当前消息列表
2. 添加新消息到数据库
3. 点击刷新按钮
4. 检查新消息是否显示
"""

import asyncio
import time
import os
import json
import psycopg2
from datetime import datetime
from playwright.async_api import async_playwright

BASE_URL = "http://localhost:5001"


def get_db_connection():
    """Get database connection."""
    config_path = os.path.expanduser("~/.open-ace/config.json")
    with open(config_path) as f:
        config = json.load(f)
    
    db_config = config.get('database', {})
    db_url = db_config.get('url')
    
    if db_url and db_url.startswith('postgresql'):
        conn = psycopg2.connect(db_url)
        return conn
    else:
        raise ValueError("PostgreSQL database not configured")


def add_test_message():
    """Add a test message to the database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Generate unique test message
    timestamp = datetime.now().isoformat()
    test_content = f"TEST MESSAGE - {timestamp}"
    import uuid
    message_id = str(uuid.uuid4())
    
    cursor.execute('''
        INSERT INTO daily_messages (
            date, tool_name, host_name, sender_name, sender_id,
            role, content, tokens_used, input_tokens, output_tokens,
            timestamp, model, message_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    ''', (
        datetime.now().strftime('%Y-%m-%d'),
        'test_tool',
        'test_host',
        'test_sender',
        'test_sender_id',
        'user',
        test_content,
        100,
        50,
        50,
        timestamp,
        'test-model',
        message_id
    ))
    
    msg_id = cursor.fetchone()[0]
    conn.commit()
    conn.close()
    
    print(f"  Added test message with ID: {msg_id}, content: {test_content[:50]}...")
    return test_content


def delete_test_messages():
    """Delete test messages from the database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM daily_messages WHERE tool_name = 'test_tool'")
    deleted = cursor.rowcount
    
    conn.commit()
    conn.close()
    
    print(f"  Deleted {deleted} test messages")
    return deleted


async def test_data_update():
    """Test that refresh updates the data."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1400, "height": 900},
            locale="zh-CN"
        )
        page = await context.new_page()

        try:
            # Clean up any existing test messages
            print("\n[Setup] Cleaning up test messages...")
            delete_test_messages()

            # Step 1: Navigate to login page
            print("\n[Step 1] Navigating to login page...")
            await page.goto(f"{BASE_URL}/login", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(1000)

            # Step 2: Login
            print("[Step 2] Logging in...")
            await page.fill('#username', "admin")
            await page.fill('#password', "admin123")
            await page.click('button[type="submit"]')
            await page.wait_for_url(lambda url: "/login" not in url, timeout=10000)
            print("✓ Login successful")

            # Step 3: Navigate to Messages page
            print("\n[Step 3] Navigating to Messages page...")
            await page.goto(f"{BASE_URL}/manage/messages", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)
            print("✓ Messages page loaded")

            # Step 4: Get current message count
            print("\n[Step 4] Getting current message count...")
            
            messages_before = await page.locator('.message-item').count()
            print(f"  Messages visible before: {messages_before}")

            # Step 5: Add a test message
            print("\n[Step 5] Adding test message to database...")
            test_content = add_test_message()

            # Step 6: Click refresh button
            print("\n[Step 6] Clicking refresh button...")
            refresh_btn = page.locator('button:has-text("刷新"), button:has-text("Refresh")')
            await refresh_btn.first.click()
            await page.wait_for_timeout(3000)
            print("✓ Refresh button clicked")

            # Step 7: Check if new message appears
            print("\n[Step 7] Checking for new message...")
            
            # Check if test message appears (search for partial content)
            test_msg_locator = page.locator(f'.message-item:has-text("TEST MESSAGE")')
            test_msg_count = await test_msg_locator.count()
            
            if test_msg_count > 0:
                print(f"  ✓ Test message found in UI!")
            else:
                print(f"  ✗ Test message NOT found in UI")
                
                # Check current messages
                messages_after = await page.locator('.message-item').count()
                print(f"  Messages visible after: {messages_after}")
                
                # Check API directly
                print("\n  Checking API directly...")
                api_response = await page.evaluate('''async () => {
                    const response = await fetch('/api/messages?limit=100&role=user,assistant,system');
                    const data = await response.json();
                    return data;
                }''')
                
                if api_response and 'messages' in api_response:
                    print(f"  API returned {len(api_response['messages'])} messages")
                    
                    # Check if test message is in API response
                    test_msg_in_api = any(
                        msg.get('content', '').startswith('TEST MESSAGE')
                        for msg in api_response['messages']
                    )
                    print(f"  Test message in API response: {test_msg_in_api}")

            # Take screenshot
            await page.screenshot(path="screenshots/issues/98/05_data_update_test.png", full_page=True)
            print("  Screenshot saved: screenshots/issues/98/05_data_update_test.png")

            # Summary
            print("\n" + "=" * 50)
            print("Test Summary:")
            print(f"  - Test message added: {test_content[:50]}...")
            print(f"  - Test message found in UI: {test_msg_count > 0}")
            print("=" * 50)

        except Exception as e:
            print(f"\n✗ Error: {e}")
            import traceback
            traceback.print_exc()
            await page.screenshot(path="screenshots/issues/98/error_data_update.png")
            print("  Error screenshot saved: screenshots/issues/98/error_data_update.png")
        finally:
            # Clean up test messages
            print("\n[Cleanup] Removing test messages...")
            delete_test_messages()
            
            await browser.close()


if __name__ == "__main__":
    import os
    os.makedirs("screenshots/issues/98", exist_ok=True)
    asyncio.run(test_data_update())