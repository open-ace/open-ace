#!/usr/bin/env python3
"""Patch qwen-code-webui dist JS to fix local mode permission confirmation dialog.

Problem: In local mode (non-remote), the CLI sends `control_request` messages
for permission requests, but the frontend's `processStreamLine` function does
not handle this message type. It only handles `claude_json`, `permission_request`,
`error`, `aborted`, and `heartbeat`.

When the CLI needs user permission for a tool (e.g., write_file), it outputs:
  {"type": "control_request", "request_id": "...", "request": {"subtype": "can_use_tool", ...}}

The frontend ignores this message, so the permission confirmation dialog never
appears, and the operation appears to hang.

Fix: Add a branch to handle `control_request` messages in processStreamLine,
converting them to the format expected by `onPermissionRequest` callback.

This patches the minified bundle at
``/usr/lib/node_modules/qwen-code-webui/dist/static/assets/index-DO2hmkKX.js``.
It is pinned to qwen-code-webui@0.2.40 (see Dockerfile); if the upstream
bundle changes, this script exits non-zero so the build fails loudly instead
of silently shipping an unpatched bundle.
"""

import sys

BUNDLE = "/usr/lib/node_modules/qwen-code-webui/dist/static/assets/index-DO2hmkKX.js"

# The pattern in processStreamLine where we need to add control_request handling.
# Current code ends with: else n.type===`heartbeat`&&console.debug(`[Keepalive]...
OLD_PATTERN = "else n.type===`heartbeat`&&console.debug(`[Keepalive] Heartbeat received at`,new Date().toISOString())"
# New code adds control_request handling before heartbeat
NEW_PATTERN = (
    "else if(n.type===`control_request`){"
    "let e=n.request;"
    "if(e?.subtype===`can_use_tool`&&t.onPermissionRequest){"
    "t.onPermissionRequest({"
    "permissionId:n.request_id,"
    "toolName:e.tool_name,"
    "toolInput:e.input||{},"
    "suggestions:e.permission_suggestions||[]"
    "})"
    "}"
    "}"
    "else n.type===`heartbeat`&&console.debug(`[Keepalive] Heartbeat received at`,new Date().toISOString())"
)


def main() -> int:
    try:
        with open(BUNDLE, encoding="utf-8") as f:
            data = f.read()
    except OSError as exc:
        print(f"[patch-qwen-webui-local-permission] cannot read bundle: {exc}", file=sys.stderr)
        return 1

    if OLD_PATTERN not in data:
        print(
            "[patch-qwen-webui-local-permission] pattern not found — already patched or version drift",
            file=sys.stderr,
        )
        # Already patched counts as success; drift fails.
        if NEW_PATTERN[:50] in data:
            print("[patch-qwen-webui-local-permission] bundle already patched, skipping", file=sys.stderr)
            return 0
        return 1

    if data.count(OLD_PATTERN) != 1:
        print(
            "[patch-qwen-webui-local-permission] pattern not unique — aborting to avoid corrupting the bundle",
            file=sys.stderr,
        )
        return 1

    data = data.replace(OLD_PATTERN, NEW_PATTERN)

    try:
        with open(BUNDLE, "w", encoding="utf-8") as f:
            f.write(data)
    except OSError as exc:
        print(f"[patch-qwen-webui-local-permission] cannot write bundle: {exc}", file=sys.stderr)
        return 1

    print("[patch-qwen-webui-local-permission] patched processStreamLine with control_request handling OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())