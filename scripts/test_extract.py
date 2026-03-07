#!/usr/bin/env python3
"""Test script for extract_user_message_metadata function."""

import json
import re
from typing import Optional, Dict


def extract_user_message_metadata(text: str) -> Optional[dict]:
    """Extract sender info and clean content from user message.

    OpenClaw user messages often contain metadata like:
    - Conversation info (untrusted metadata)
    - Sender (untrusted metadata)
    - [message_id: ...]
    - Channel info from slack/feishu
    - System: [...] Slack message in #channel from User: content
    - System: [...] Feishu[default] message in group XXX: ACTUAL_CONTENT

    This function extracts the actual user content and sender information.
    """
    if not text:
        return None

    sender_id = None
    sender_name = None
    cleaned_content = text
    message_source = "openclaw"  # Default source

    # ========== Step 1: Detect message source ==========
    if 'conversation_label' in text or 'Feishu' in text:
        message_source = "feishu"
    elif 'Slack' in text:
        message_source = "slack"

    # ========== Step 2: Handle Feishu System message format ==========
    # Pattern: "System: [...] Feishu[default] message in group XXX: ACTUAL_CONTENT"
    # or: "System: [...] Feishu message from User: ACTUAL_CONTENT"
    feishu_system_match = re.search(
        r'System:\s*\[[^\]]+\]\s*Feishu\[[^\]]*\]\s*(?:message\s+in\s+group\s+\w+|message\s+from\s+\w+):\s*(.+?)(?:\n\nConversation info|$)',
        text,
        re.DOTALL
    )
    if feishu_system_match:
        # Extract the actual content after the colon
        actual_content = feishu_system_match.group(1).strip()
        # Remove any leading sender_id: pattern if present
        prefix_match = re.match(r'^(ou_[a-f0-9]+):\s*(.+)$', actual_content, re.DOTALL)
        if prefix_match:
            sender_id = prefix_match.group(1)
            actual_content = prefix_match.group(2).strip()
        cleaned_content = actual_content
        # Extract sender name from Sender metadata block
        sender_name_match = re.search(r'"label":\s*"([^"]+)"', text)
        if sender_name_match:
            sender_name = sender_name_match.group(1)
        return {
            "sender_id": sender_id,
            "sender_name": sender_name,
            "cleaned_content": cleaned_content,
            "message_source": "feishu"
        }

    # ========== Step 3: Handle Slack System message format ==========
    # Pattern: "System: [...] Slack message in #channel from Name: ACTUAL_CONTENT"
    slack_match = re.search(
        r'Slack\s+(?:message\s+in\s+\S+\s+from|DM\s+from)\s+([^:]+):\s*(.+?)(?:\n\nConversation info|$)',
        text,
        re.DOTALL
    )
    if slack_match:
        extracted_name = slack_match.group(1).strip()
        extracted_content = slack_match.group(2).strip()
        # Remove user mention tags like <@U0AE9GW0KLJ>
        extracted_content = re.sub(r'<@[A-Z0-9]+>', '', extracted_content).strip()
        return {
            "sender_id": None,
            "sender_name": extracted_name,
            "cleaned_content": extracted_content,
            "message_source": "slack"
        }

    # ========== Step 4: Handle simple sender_id: content format ==========
    # Pattern: "ou_xxxxx: content" or "on_xxxxx: content" or "oc_xxxxx: content" or "Uxxxx: content"
    simple_match = re.match(r'^(ou_[a-f0-9]+|on_[a-f0-9]+|oc_[a-f0-9]+|U[A-Z0-9]+):\s*(.+)$', text.strip(), re.DOTALL)
    if simple_match:
        sender_id = simple_match.group(1)
        actual_content = simple_match.group(2).strip()
        message_source = "openclaw"
        if sender_id.startswith('ou_') or sender_id.startswith('on_') or sender_id.startswith('oc_'):
            message_source = "feishu"
        return {
            "sender_id": sender_id,
            "sender_name": None,  # Will be resolved later from cache
            "cleaned_content": actual_content,
            "message_source": message_source
        }

    # ========== Step 5: Fallback - try to extract content from metadata blocks ==========
    # Remove ```json``` code blocks
    content = re.sub(r'```json\s*\n?\s*```', '', text)
    content = re.sub(r'```\s*\n?\s*```', '', content)
    
    # Remove "Replied message" blocks
    content = re.sub(r'Replied message \(untrusted, for context\):\s*```json\s*\n?"[^"]*"\s*```', '', content, flags=re.DOTALL)
    
    # Remove standalone JSON string lines like "body": "..."
    content = re.sub(r'^\s*"body":\s*"[^"]*"\s*$', '', content, flags=re.MULTILINE)

    # Remove metadata lines
    lines = content.split('\n')
    cleaned_lines = []
    skip_until_empty = False

    for line in lines:
        stripped = line.strip()

        # Skip metadata patterns
        if stripped.startswith('Conversation info'):
            skip_until_empty = True
            continue
        if stripped.startswith('Sender (untrusted'):
            continue
        if stripped.startswith('```json') or stripped.startswith('```'):
            continue
        if stripped.startswith('[message_id:'):
            continue
        if stripped.startswith('[Thread history'):
            continue
        if stripped.startswith('[Slack') or stripped.startswith('[Feishu'):
            continue
        if stripped.startswith('[media attached:'):
            continue
        if stripped.startswith('System:'):
            continue
        if stripped.startswith('{') or stripped.startswith('}'):
            continue
        # Skip JSON key-value lines
        if re.match(r'^"[^"]+"\s*:\s*("[^"]*"|\d+|true|false|null)\s*,?\s*$', stripped):
            continue
        if re.match(r'^"(message_id|sender_id|reply_to_id|conversation_label|sender|timestamp|group_subject|is_group_chat|was_mentioned|has_reply_context|label|id|name)"\s*:', stripped):
            continue
        if stripped.endswith('"]') or stripped.endswith('"}'):
            continue
        if stripped.startswith('Replied message'):
            continue
        if stripped == '':
            if skip_until_empty:
                skip_until_empty = False
                continue
            continue

        # Remove timestamp prefix
        stripped = re.sub(r'^\[[A-Za-z]{3}\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}.*?\]\s*', '', stripped)

        # Remove "Sender: " prefix
        sender_prefix_match = re.match(r'^[\u4e00-\u9fa5a-zA-Z\s]+:\s*(.+)$', stripped)
        if sender_prefix_match and len(stripped) < 100:
            stripped = sender_prefix_match.group(1)

        if stripped:
            cleaned_lines.append(stripped)

    if cleaned_lines:
        cleaned_content = '\n'.join(cleaned_lines).strip()

    # Try to extract sender_id from JSON metadata
    try:
        json_blocks = re.findall(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
        conversation_label = None
        group_subject = None
        is_group_chat = None
        for full_block in json_blocks:
            try:
                data = json.loads(full_block)
                if isinstance(data, dict):
                    if "sender_id" in data:
                        sender_id = data.get("sender_id")
                    if "sender" in data and not sender_id:
                        sender_id = data.get("sender")
                    if "label" in data and data.get("label") != sender_id:
                        sender_name = data.get("label")
                    if "name" in data and data.get("name") != sender_id:
                        sender_name = data.get("name")
                    # Extract conversation metadata
                    if "conversation_label" in data and not conversation_label:
                        conversation_label = data.get("conversation_label")
                    if "group_subject" in data and not group_subject:
                        group_subject = data.get("group_subject")
                    if "is_group_chat" in data:
                        is_group_chat = data.get("is_group_chat")
            except json.JSONDecodeError:
                continue
    except Exception:
        pass

    return {
        "sender_id": sender_id,
        "sender_name": sender_name,
        "cleaned_content": cleaned_content,
        "message_source": message_source,
        "conversation_label": conversation_label,
        "group_subject": group_subject,
        "is_group_chat": is_group_chat
    }


# Test cases
test_cases = [
    # Feishu message with System format
    """System: [2026-03-04 10:10:07 GMT+8] Feishu[default] message in group xxx: ou_3e479c7f81f8674741d778e8f838f8ed: 你能做什么

Conversation info (untrusted metadata):
```json
{
  "message_id": "xxx",
  "sender_id": "ou_3e479c7f81f8674741d778e8f838f8ed",
  "conversation_label": "group_xxx",
  "group_subject": "test_group"
}
```

Sender (untrusted metadata):
```json
{
  "label": "韩成凤",
  "name": "韩成凤"
}
```""",
    
    # Simple ou_xxxxx: content format
    """ou_0f6e393a5fa05fd2dfa6e8b00a768a8d: 你能做什么""",
    
    # Slack message with System format
    """System: [2026-03-04 10:10:07 GMT+8] Slack message in #all-openclaw from Richard Huang: <@U0AE9GW0KLJ> 刚才发你的看到了吗

Conversation info (untrusted metadata):
```json
{
  "message_id": "1772590207.640109",
  "sender_id": "U0A4PB8LF9P",
  "conversation_label": "#all-openclaw"
}
```""",
    
    # Message with metadata blocks
    """```json
```
```json
```
Replied message (untrusted, for context):
```json
"body": "[Interactive Card]"
```
HPCinsights 是使用 grafana 进行数据展示的（表格也可以通过 web 前端制定），你认为这个表格需要展示哪些指标？""",
]

print("Testing extract_user_message_metadata function\n")
print("=" * 60)

for i, test in enumerate(test_cases, 1):
    print(f"\n\nTest Case {i}:")
    print("-" * 40)
    result = extract_user_message_metadata(test)
    if result:
        print(f"sender_id: {result.get('sender_id')}")
        print(f"sender_name: {result.get('sender_name')}")
        print(f"message_source: {result.get('message_source')}")
        print(f"cleaned_content: {result.get('cleaned_content', '')[:100]}...")
    else:
        print("Result: None")
