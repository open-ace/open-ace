"""
Shared Codex JSONL parser.

Used by both:
  - remote-agent/session_sync.py (agent-side, lightweight)
  - scripts/fetch_codex.py (server-side, full DB import)

Both modules parse the same JSONL format from ~/.codex/sessions/.
This module consolidates the duplicated extraction logic.
"""

from typing import Any


def extract_codex_text(payload: dict[str, Any]) -> str:
    """Extract plain text from a Codex response_item payload."""
    ptype = payload.get("type", "")
    if ptype == "message":
        content = payload.get("content", [])
        if isinstance(content, list):
            texts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") in (
                    "input_text",
                    "output_text",
                ):
                    texts.append(block.get("text", ""))
            return "\n".join(texts)
    elif ptype == "function_call":
        name = payload.get("name", "")
        args = payload.get("arguments", "")
        return f"[{name}] {args}" if name else ""
    elif ptype in ("function_call_output", "custom_tool_call_output"):
        output = payload.get("output", "")
        if isinstance(output, str) and len(output) > 2000:
            return output[:2000] + "...[truncated]"
        return output if isinstance(output, str) else str(output)
    elif ptype == "custom_tool_call":
        name = payload.get("name", "")
        return f"[{name}]" if name else ""
    elif ptype == "reasoning":
        summary = payload.get("summary", [])
        if isinstance(summary, list):
            texts = []
            for item in summary:
                if isinstance(item, dict) and item.get("type") == "summary_text":
                    texts.append(item.get("text", ""))
            if texts:
                return "\n".join(texts)
    return ""


def extract_codex_content_blocks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract structured content blocks from a Codex response_item payload."""
    import json

    ptype = payload.get("type", "")
    blocks: list[dict[str, Any]] = []

    if ptype == "message":
        content = payload.get("content", [])
        if isinstance(content, list):
            texts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") in (
                    "input_text",
                    "output_text",
                ):
                    texts.append(block.get("text", ""))
            if texts:
                block_dict: dict[str, Any] = {"type": "text", "text": "\n".join(texts)}
                phase = payload.get("phase")
                if payload.get("role") == "assistant" and phase:
                    block_dict["metadata"] = {"phase": phase}
                blocks.append(block_dict)
    elif ptype in ("function_call",):
        call_id = payload.get("call_id", "")
        name = payload.get("name", "")
        args_str = payload.get("arguments", "{}")
        try:
            arguments = json.loads(args_str) if isinstance(args_str, str) else args_str
        except (json.JSONDecodeError, TypeError):
            arguments = {"raw": args_str}
        blocks.append(
            {
                "type": "tool_use",
                "id": call_id,
                "name": name,
                "input": arguments,
            }
        )
    elif ptype in ("function_call_output", "custom_tool_call_output"):
        call_id = payload.get("call_id", "")
        output = payload.get("output", "")
        blocks.append(
            {
                "type": "tool_result",
                "tool_use_id": call_id,
                "content": (
                    output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
                ),
            }
        )
    elif ptype == "custom_tool_call":
        call_id = payload.get("call_id", "")
        name = payload.get("name", "")
        patch_input = payload.get("input", "")
        status = payload.get("status", "")
        blocks.append(
            {
                "type": "tool_use",
                "id": call_id,
                "name": name,
                "input": {"patch": patch_input},
                "status": status,
            }
        )
    elif ptype == "reasoning":
        summary = payload.get("summary", [])
        if isinstance(summary, list):
            texts = []
            for item in summary:
                if isinstance(item, dict) and item.get("type") == "summary_text":
                    texts.append(item.get("text", ""))
            if texts:
                blocks.append({"type": "reasoning", "summary": "\n".join(texts)})
    return blocks
