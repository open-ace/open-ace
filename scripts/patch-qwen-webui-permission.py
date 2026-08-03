#!/usr/bin/env python3
"""Patch qwen-code-webui dist JS to fix "Allow All" permission in remote sessions.

Problem: the frontend's ``Ut`` handler (onAllowAll — the "Allow All" button for
run_shell_command) lacks the WebSocket/remote-session branch. Remote-session
permission requests carry only ``requestId`` (permissionId is undefined), so
``Ut`` short-circuits on ``!z.permissionId`` and never sends a response. The
pending permission then hangs and times out, which the user perceives as
"denied".

Fix: mirror the branch used by ``Vt`` (onAllow) / ``Wt`` (onDeny): when a
WebSocket context (``A``) is active, send the response through
``L.sendPermissionResponse(requestId, 'allow', ...)`` before falling back to
the HTTP ``ks()`` path used by the local (non-remote) mode.

This patches the minified bundle at
``/usr/lib/node_modules/qwen-code-webui/dist/static/assets/index-DO2hmkKX.js``.
It is pinned to qwen-code-webui@0.2.40 (see Dockerfile); if the upstream
bundle changes, this script exits non-zero so the build fails loudly instead
of silently shipping an unpatched bundle.
"""

import sys

BUNDLE = "/usr/lib/node_modules/qwen-code-webui/dist/static/assets/index-DO2hmkKX.js"

# Exact minified fragments (verified against qwen-code-webui@0.2.40).
OLD_BODY = (
    "Ut=(0,M.useCallback)(async()=>{if(z){if(vt(),H(),!z.permissionId){B();return}"
)
NEW_BODY = (
    "Ut=(0,M.useCallback)(async()=>{if(z){if(vt(),H(),A){"
    "L.sendPermissionResponse(z.requestId||``,`allow`,void 0,z.toolName);B();return}"
    "if(!z.permissionId){B();return}"
)

OLD_DEPS = "B()}},[z,ct,dt,B,vt,H,U]),Wt="
NEW_DEPS = "B()}},[z,ct,dt,B,vt,H,A,L,U]),Wt="


def main() -> int:
    try:
        with open(BUNDLE, encoding="utf-8") as f:
            data = f.read()
    except OSError as exc:
        print(f"[patch-qwen-webui] cannot read bundle: {exc}", file=sys.stderr)
        return 1

    if OLD_BODY not in data:
        print("[patch-qwen-webui] BODY pattern not found — already patched or version drift", file=sys.stderr)
        # Already patched (NEW_BODY present) counts as success; drift fails.
        if NEW_BODY in data:
            print("[patch-qwen-webui] bundle already patched, skipping", file=sys.stderr)
            return 0
        return 1

    if data.count(OLD_BODY) != 1 or data.count(OLD_DEPS) != 1:
        print("[patch-qwen-webui] pattern not unique — aborting to avoid corrupting the bundle", file=sys.stderr)
        return 1

    data = data.replace(OLD_BODY, NEW_BODY)
    data = data.replace(OLD_DEPS, NEW_DEPS)

    try:
        with open(BUNDLE, "w", encoding="utf-8") as f:
            f.write(data)
    except OSError as exc:
        print(f"[patch-qwen-webui] cannot write bundle: {exc}", file=sys.stderr)
        return 1

    print("[patch-qwen-webui] patched Ut (onAllowAll) with remote WebSocket branch OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
