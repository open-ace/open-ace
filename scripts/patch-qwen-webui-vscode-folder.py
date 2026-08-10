#!/usr/bin/env python3
"""Patch qwen-code-webui dist JS to fix VS Code folder parameter for remote workspaces.

Problem: when opening VS Code for a remote workspace (e.g., C:/workspace), the
webui SPA constructs the iframe URL with ``folder=${encodeURIComponent(n)}``
using the raw project path without a leading ``/`` prefix. code-server's
workbench.js parses the folder parameter and only enters the ``vscodeRemote``
branch when the path starts with ``/``. Without it, the path is parsed as a
URI where ``C:`` becomes the scheme → "Unable to resolve resource C:/workspace".

Fix: normalize the folder parameter before encoding:
1. Add a leading ``/`` prefix
2. Convert backslashes to forward slashes
3. Remove duplicate leading slashes (``//C:/`` → ``/C:/``)

This transforms ``C:/workspace`` → ``/C:/workspace``, which triggers the
vscodeRemote branch and resolves correctly.

This patches the minified bundle at
``/usr/lib/node_modules/qwen-code-webui/dist/static/assets/index-*.js``.
It is pinned to qwen-code-webui@0.2.40 (see Dockerfile); if the upstream
bundle changes, this script exits non-zero so the build fails loudly.
"""

import glob
import re
import sys

BUNDLE_GLOB = "/usr/lib/node_modules/qwen-code-webui/dist/static/assets/index-*.js"
INDEX_HTML = "/usr/lib/node_modules/qwen-code-webui/dist/static/index.html"

# Cache-bust version for the bundle URL
CACHE_BUST = "v=vscodefolder-20260810"

# Pattern: folder=${encodeURIComponent(n)} where n is the raw project path
# We need to wrap n with a normalization function that adds "/" prefix
# In the minified bundle, look for the VS Code iframe URL construction
OLD_FOLDER_ENCODE = "folder=${encodeURIComponent(n)}"
NEW_FOLDER_ENCODE = 'folder=${encodeURIComponent("/"+n.replace(/\\\\/g,"/").replace(/^\\/+/,""))}'

# Alternative pattern seen in some versions: folder parameter in template literal
OLD_FOLDER_TEMPLATE = "folder=${encodeURIComponent(e)"
NEW_FOLDER_TEMPLATE = 'folder=${encodeURIComponent("/"+e.replace(/\\\\/g,"/").replace(/^\\/+/,""))}'


def main() -> int:
    matches = glob.glob(BUNDLE_GLOB)
    if len(matches) != 1:
        print(
            f"[patch-qwen-webui-vscode-folder] expected exactly one SPA bundle, found {len(matches)}",
            file=sys.stderr,
        )
        return 1
    bundle = matches[0]

    try:
        with open(bundle, encoding="utf-8") as f:
            data = f.read()
    except OSError as exc:
        print(f"[patch-qwen-webui-vscode-folder] cannot read bundle: {exc}", file=sys.stderr)
        return 1

    patched = False

    # Try pattern 1: folder=${encodeURIComponent(n)}
    if OLD_FOLDER_ENCODE in data:
        if data.count(OLD_FOLDER_ENCODE) != 1:
            print(
                "[patch-qwen-webui-vscode-folder] folder pattern not unique — aborting",
                file=sys.stderr,
            )
            return 1
        data = data.replace(OLD_FOLDER_ENCODE, NEW_FOLDER_ENCODE)
        patched = True
        print("[patch-qwen-webui-vscode-folder] patched folder=${encodeURIComponent(n)}", file=sys.stderr)

    # Try pattern 2: folder=${encodeURIComponent(e)
    if OLD_FOLDER_TEMPLATE in data:
        if data.count(OLD_FOLDER_TEMPLATE) != 1:
            print(
                "[patch-qwen-webui-vscode-folder] folder template pattern not unique — aborting",
                file=sys.stderr,
            )
            return 1
        data = data.replace(OLD_FOLDER_TEMPLATE, NEW_FOLDER_TEMPLATE)
        patched = True
        print("[patch-qwen-webui-vscode-folder] patched folder=${encodeURIComponent(e)}", file=sys.stderr)

    if not patched:
        # Check if already patched
        if NEW_FOLDER_ENCODE in data or NEW_FOLDER_TEMPLATE in data:
            print("[patch-qwen-webui-vscode-folder] bundle already patched, skipping", file=sys.stderr)
            return 0
        print(
            "[patch-qwen-webui-vscode-folder] no folder pattern found — version drift?",
            file=sys.stderr,
        )
        return 1

    try:
        with open(bundle, "w", encoding="utf-8") as f:
            f.write(data)
    except OSError as exc:
        print(f"[patch-qwen-webui-vscode-folder] cannot write bundle: {exc}", file=sys.stderr)
        return 1

    # Cache-bust the bundle URL in index.html
    try:
        with open(INDEX_HTML, encoding="utf-8") as f:
            html = f.read()
        basename = bundle.rsplit("/", 1)[-1]
        script_src = f'src="/assets/{basename}"'
        busted_src = f'src="/assets/{basename}?{CACHE_BUST}"'
        if busted_src in html:
            print("[patch-qwen-webui-vscode-folder] index.html cache-bust already applied", file=sys.stderr)
        elif script_src in html:
            html = html.replace(script_src, busted_src)
            with open(INDEX_HTML, "w", encoding="utf-8") as f:
                f.write(html)
            print("[patch-qwen-webui-vscode-folder] index.html cache-bust applied", file=sys.stderr)
        else:
            # Check for older cache-bust version
            old_bust_pattern = f'src="/assets/{basename}?v=vscodefolder-'
            idx = html.find(old_bust_pattern)
            if idx >= 0:
                end = html.find('"', idx)
                html = html[:idx] + busted_src + html[end:]
                with open(INDEX_HTML, "w", encoding="utf-8") as f:
                    f.write(html)
                print("[patch-qwen-webui-vscode-folder] index.html cache-bust bumped", file=sys.stderr)
            else:
                print(
                    f"[patch-qwen-webui-vscode-folder] script src not found in index.html",
                    file=sys.stderr,
                )
    except OSError as exc:
        print(f"[patch-qwen-webui-vscode-folder] cannot patch index.html: {exc}", file=sys.stderr)
        return 1

    print("[patch-qwen-webui-vscode-folder] VS Code folder normalization patched OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())