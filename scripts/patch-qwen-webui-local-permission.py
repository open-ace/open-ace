#!/usr/bin/env python3
"""Patch qwen-code-webui dist JS to fix local mode permission confirmation dialog.

Problem: In local mode (non-remote), the CLI sends `control_request` messages
for permission requests in stream-json mode, but the frontend's `processStreamLine`
function does not handle this message type. It only handles `claude_json`, `error`,
and `aborted` types.

When the CLI needs user permission for a tool (e.g., write_file), it outputs:
  {"type": "control_request", "request_id": "...", "request": {"subtype": "can_use_tool", ...}}

The frontend ignores this message, so the permission confirmation dialog never
appears, and the tool executes without user confirmation.

Fix: Add a branch to handle `control_request` messages in processStreamLine,
calling `t.onPermissionError` callback to show the permission confirmation dialog.

This patches the minified bundle at
``/usr/lib/node_modules/qwen-code-webui/dist/static/assets/index-DO2hmkKX.js``.
It is pinned to qwen-code-webui@0.2.40 (see Dockerfile); if the upstream
bundle changes, this script exits non-zero so the build fails loudly instead
of silently shipping an unpatched bundle.
"""

import sys

BUNDLE = "/usr/lib/node_modules/qwen-code-webui/dist/static/assets/index-DO2hmkKX.js"

# Exact minified fragments for processStreamLine function (verified against qwen-code-webui@0.2.40).
# Original: only handles claude_json, error, aborted
OLD_PROCESS = (
    "processStreamLine:(0,_.useCallback)((e,t)=>{try{let r=JSON.parse(e);"
    "if(r.type===`claude_json`&&r.data){let e=r.data;n(e,t)}"
    "else if(r.type===`error`){let e={type:`error`,subtype:`stream_error`,message:r.error||`Unknown error`,timestamp:Date.now()};t.addMessage(e)}"
    "else if(r.type===`aborted`){let e={type:`system`,subtype:`abort`,message:`Operation was aborted by user`,timestamp:Date.now()};t.addMessage(e),t.setCurrentAssistantMessage(null)}}"
    "catch(e){console.error(`Failed to parse stream line:`,e)}},[n])"
)

# New: adds control_request handling before the catch block
NEW_PROCESS = (
    "processStreamLine:(0,_.useCallback)((e,t)=>{try{let r=JSON.parse(e);"
    "if(r.type===`claude_json`&&r.data){let e=r.data;n(e,t)}"
    "else if(r.type===`error`){let e={type:`error`,subtype:`stream_error`,message:r.error||`Unknown error`,timestamp:Date.now()};t.addMessage(e)}"
    "else if(r.type===`aborted`){let e={type:`system`,subtype:`abort`,message:`Operation was aborted by user`,timestamp:Date.now()};t.addMessage(e),t.setCurrentAssistantMessage(null)}"
    "else if(r.type===`control_request`&&r.request&&t.onPermissionError){"
    "let e=r.request,n=r.request_id||``;"
    "if(e.subtype===`can_use_tool`&&e.tool_name){"
    "t.onPermissionError(e.tool_name,e.permission_suggestions?.map(s=>s.rule)||[],e.tool_use_id||``,n)"
    "}"
    "}"
    "}"
    "catch(e){console.error(`Failed to parse stream line:`,e)}},[n])"
)


def main() -> int:
    try:
        with open(BUNDLE, encoding="utf-8") as f:
            data = f.read()
    except OSError as exc:
        print(f"[patch-qwen-webui-local-permission] cannot read bundle: {exc}", file=sys.stderr)
        return 1

    if OLD_PROCESS not in data:
        print(
            "[patch-qwen-webui-local-permission] PROCESS pattern not found — already patched or version drift",
            file=sys.stderr,
        )
        # Already patched (NEW_PROCESS present) counts as success; drift fails.
        if "else if(r.type===`control_request`" in data:
            print(
                "[patch-qwen-webui-local-permission] bundle already patched, skipping",
                file=sys.stderr,
            )
            return 0
        return 1

    if data.count(OLD_PROCESS) != 1:
        print(
            "[patch-qwen-webui-local-permission] pattern not unique — aborting to avoid corrupting the bundle",
            file=sys.stderr,
        )
        return 1

    data = data.replace(OLD_PROCESS, NEW_PROCESS)

    try:
        with open(BUNDLE, "w", encoding="utf-8") as f:
            f.write(data)
    except OSError as exc:
        print(f"[patch-qwen-webui-local-permission] cannot write bundle: {exc}", file=sys.stderr)
        return 1

    print(
        "[patch-qwen-webui-local-permission] patched processStreamLine with control_request handling OK"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
