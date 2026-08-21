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
INDEX_HTML = "/usr/lib/node_modules/qwen-code-webui/dist/static/index.html"

# Cache-bust version for the bundle URL (increment when patch changes)
CACHE_BUST = "v=local-perm-20260818"

# Exact minified fragments for processStreamLine function (verified against qwen-code-webui@0.2.40).
# Original: handles claude_json, permission_request, error, aborted, heartbeat
# Note: Uses M.useCallback, n (parsed JSON), u (callback function) in actual bundle
OLD_PROCESS = (
    "processStreamLine:(0,M.useCallback)((e,t)=>{try{let n=JSON.parse(e);"
    "if(n.type===`claude_json`&&n.data){let e=n.data;u(e,t),t.onPermissionOrphanCleanup&&t.onPermissionOrphanCleanup()}"
    "else if(n.type===`permission_request`){if(t.onPermissionRequest){if(!n.permissionId||!n.toolName){console.warn(`Invalid permission_request: missing permissionId or toolName`);return}"
    "t.onPermissionRequest({permissionId:n.permissionId,toolName:n.toolName,toolInput:n.toolInput||{},suggestions:n.suggestions||[],autoApproveMs:n.autoApproveMs,confirmationType:n.confirmationType,questions:n.questions})}}"
    "else if(n.type===`error`){let e={type:`error`,subtype:`stream_error`,message:n.error||`Unknown error`,timestamp:Date.now()};t.addMessage(e),t.onStreamError?.(n.error||`Unknown error`)}"
    "else if(n.type===`aborted`){let e={type:`system`,subtype:`abort`,message:`Operation was aborted by user`,timestamp:Date.now()};t.addMessage(e),t.setCurrentAssistantMessage(null)}"
    "else n.type===`heartbeat`&&console.debug(`[Keepalive] Heartbeat received at`,new Date().toISOString())}"
    "catch(e){console.error(`Failed to parse stream line:`,e)}},[u])"
)

# New: adds control_request handling before the catch block
# control_request format: {"type":"control_request","request_id":"...","request":{"subtype":"can_use_tool",...}}
# Uses onPermissionRequest (not onPermissionError) which exists in the actual bundle
NEW_PROCESS = (
    "processStreamLine:(0,M.useCallback)((e,t)=>{try{let n=JSON.parse(e);"
    "if(n.type===`claude_json`&&n.data){let e=n.data;u(e,t),t.onPermissionOrphanCleanup&&t.onPermissionOrphanCleanup()}"
    "else if(n.type===`permission_request`){if(t.onPermissionRequest){if(!n.permissionId||!n.toolName){console.warn(`Invalid permission_request: missing permissionId or toolName`);return}"
    "t.onPermissionRequest({permissionId:n.permissionId,toolName:n.toolName,toolInput:n.toolInput||{},suggestions:n.suggestions||[],autoApproveMs:n.autoApproveMs,confirmationType:n.confirmationType,questions:n.questions})}}"
    "else if(n.type===`control_request`&&n.request&&t.onPermissionRequest){"
    "let e=n.request,r=n.request_id||``;"
    "if(e.subtype===`can_use_tool`&&e.tool_name){"
    "t.onPermissionRequest({permissionId:r,toolName:e.tool_name,toolInput:e.tool_input||{},suggestions:e.permission_suggestions||[],autoApproveMs:e.auto_approve_ms,confirmationType:e.confirmation_type,questions:e.questions||[]})"
    "}"
    "}"
    "else if(n.type===`error`){let e={type:`error`,subtype:`stream_error`,message:n.error||`Unknown error`,timestamp:Date.now()};t.addMessage(e),t.onStreamError?.(n.error||`Unknown error`)}"
    "else if(n.type===`aborted`){let e={type:`system`,subtype:`abort`,message:`Operation was aborted by user`,timestamp:Date.now()};t.addMessage(e),t.setCurrentAssistantMessage(null)}"
    "else n.type===`heartbeat`&&console.debug(`[Keepalive] Heartbeat received at`,new Date().toISOString())}"
    "catch(e){console.error(`Failed to parse stream line:`,e)}},[u])"
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
        if "else if(n.type===`control_request`" in data:
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

    # Cache-bust the bundle URL in index.html
    index_ok = True
    try:
        with open(INDEX_HTML, encoding="utf-8") as f:
            html = f.read()
        basename = BUNDLE.rsplit("/", 1)[-1]
        script_src = f'src="/assets/{basename}"'
        busted_src = f'src="/assets/{basename}?{CACHE_BUST}"'
        if busted_src in html:
            print("[patch-qwen-webui-local-permission] index.html cache-bust already applied")
        elif script_src in html:
            html = html.replace(script_src, busted_src)
            with open(INDEX_HTML, "w", encoding="utf-8") as f:
                f.write(html)
            print("[patch-qwen-webui-local-permission] index.html cache-bust applied")
        else:
            # Check for older cache-bust version
            old_bust_pattern = f'src="/assets/{basename}?v=local-perm-'
            idx = html.find(old_bust_pattern)
            if idx >= 0:
                end = html.find('"', idx)
                html = html[:idx] + busted_src + html[end:]
                with open(INDEX_HTML, "w", encoding="utf-8") as f:
                    f.write(html)
                print("[patch-qwen-webui-local-permission] index.html cache-bust bumped")
            else:
                print(
                    f"[patch-qwen-webui-local-permission] script src {script_src!r} not found in index.html",
                    file=sys.stderr,
                )
                index_ok = False
    except OSError as exc:
        print(
            f"[patch-qwen-webui-local-permission] cannot patch index.html: {exc}", file=sys.stderr
        )
        index_ok = False

    return 0 if index_ok else 1


if __name__ == "__main__":
    sys.exit(main())
