#!/usr/bin/env python3
"""Patch qwen-code-webui SPA bundle so in-app navigation keeps URL params.

Bug: three in-app navigation points rebuild the URL with a bare
``new URLSearchParams`` (no argument), dropping every existing query param
(workspaceType=remote, machineId, encodedProjectName, token, openace_url).
When a remote session is restored from the in-WebUI conversation-history
list, the file-changes panel loses the remote context (the SPA reads
``workspaceType``/``machineId`` straight from the URL), falls back to the
local git/status endpoint and fails with HTTP 403 ("workingDirectory is not
a known project").

Patched navigation points:
1. Clicking a conversation in the history list (``sessionId`` nav) — the
   only params kept were ``sessionId``, so restoring a remote session lost
   workspaceType/machineId/encodedProjectName. Also drops ``view`` so the
   session view actually renders (``view=history`` would keep showing the
   list).
2. Both "view conversation history" buttons (``view=history`` nav) — the
   only param kept was ``view``, so even reaching the history list already
   dropped the remote context.
3. Project-selector click on a project — ``S(`/projects${path}`)`` replaced
   the whole URL, dropping workspaceType/machineId/token. The ChatPage then
   read ``workspaceType`` as missing -> local mode -> webui git/status 403.
   Fix: append ``window.location.search`` so the remote context survives.

Fix: seed ``URLSearchParams`` from ``window.location.search`` so the
existing params are preserved.

It also bumps the bundle URL in ``static/index.html`` (``?v=navparams-...``)
so browsers that already cached the old bundle under the same hashed
filename fetch the patched one.

This patches the SPA bundle at
``/usr/lib/node_modules/qwen-code-webui/dist/static/assets/index-*.js``.
It is pinned to qwen-code-webui@0.2.40 (see Dockerfile); if the upstream
bundle changes, this script exits non-zero so the build fails loudly instead
of silently shipping an unpatched bundle.
"""

import glob
import sys

BUNDLE_GLOB = "/usr/lib/node_modules/qwen-code-webui/dist/static/assets/index-*.js"
INDEX_HTML = "/usr/lib/node_modules/qwen-code-webui/dist/static/index.html"

# Bump when the nav-param fix changes so browsers re-fetch the bundle even
# though the hashed filename stays identical across rebuilds.
CACHE_BUST = "v=navparams-20260807d"

# Exact text from qwen-code-webui@0.2.40 (minified SPA bundle).
OLD_SESSION_NAV = (
    "let l=e=>{let n=new URLSearchParams;n.set(`sessionId`,e),t({search:n.toString()})}"
)
NEW_SESSION_NAV = (
    "let l=e=>{let n=new URLSearchParams(window.location.search);"
    "n.set(`sessionId`,e),n.delete(`view`),t({search:n.toString()})}"
)

OLD_HISTORY_NAV = "let e=new URLSearchParams;e.set(`view`,`history`),t({search:e.toString()})"
NEW_HISTORY_NAV = (
    "let e=new URLSearchParams(window.location.search);"
    "e.set(`view`,`history`),t({search:e.toString()})"
)

# Project selector: clicking a project navigates to /projects/<path> and
# replaces the whole URL, dropping workspaceType/machineId/token.
OLD_PROJECT_NAV = (
    "let E=(0,M.useCallback)(e=>{localStorage.setItem(Do,e);"
    "let t=e.startsWith(`/`)?e:`/${e}`;S(`/projects${t}`)},[S])"
)
NEW_PROJECT_NAV = (
    "let E=(0,M.useCallback)(e=>{localStorage.setItem(Do,e);"
    "let t=e.startsWith(`/`)?e:`/${e}`;S(`/projects${t}${window.location.search}`)},[S])"
)

# Project selector v2: the open-ace integrated-mode picker loads its list from
# /api/projects, whose remote entries now carry machine_id. The first patch
# only preserved existing query params, but the default workspace tab URL has
# no workspaceType/machineId at all — so clicking a remote project still
# degraded ChatPage to local mode (silent chat failure + file-changes 403).
# v2 looks the clicked path up in the raw project list (state var `r`) and,
# when the project has a machine_id, appends workspaceType=remote&machineId
# so ChatPage enters remote mode and starts a remote session.
OLD_PROJECT_NAV_V2 = NEW_PROJECT_NAV
NEW_PROJECT_NAV_V2 = (
    "let E=(0,M.useCallback)(e=>{localStorage.setItem(Do,e);"
    "let t=e.startsWith(`/`)?e:`/${e}`;"
    "let n=r.find(s=>s.path===e),u=window.location.search;"
    "if(n&&n.machine_id){let q=u.includes(`?`)?`&`:`?`;"
    "u=`${u}${q}workspaceType=remote&machineId=${encodeURIComponent(n.machine_id)}`}"
    "S(`/projects${t}${u}`)},[S,r])"
)


def main() -> int:
    matches = glob.glob(BUNDLE_GLOB)
    if len(matches) != 1:
        print(
            f"[patch-qwen-webui-navparams] expected exactly one SPA bundle, found {len(matches)}",
            file=sys.stderr,
        )
        return 1
    bundle = matches[0]

    try:
        with open(bundle, encoding="utf-8") as f:
            data = f.read()
    except OSError as exc:
        print(f"[patch-qwen-webui-navparams] cannot read bundle: {exc}", file=sys.stderr)
        return 1

    # sessionId nav must appear exactly once; history nav appears twice
    # (two identical "view conversation history" buttons).
    if OLD_SESSION_NAV in data:
        if data.count(OLD_SESSION_NAV) != 1:
            print(
                "[patch-qwen-webui-navparams] sessionId nav pattern not unique — "
                "aborting to avoid corrupting the bundle",
                file=sys.stderr,
            )
            return 1
        data = data.replace(OLD_SESSION_NAV, NEW_SESSION_NAV)
    elif NEW_SESSION_NAV not in data:
        print(
            "[patch-qwen-webui-navparams] sessionId nav OLD pattern not found "
            "and NEW not present — version drift?",
            file=sys.stderr,
        )
        return 1
    else:
        print(
            "[patch-qwen-webui-navparams] sessionId nav already patched, skipping", file=sys.stderr
        )

    history_count = data.count(OLD_HISTORY_NAV)
    if history_count > 0:
        data = data.replace(OLD_HISTORY_NAV, NEW_HISTORY_NAV)
    elif NEW_HISTORY_NAV not in data:
        print(
            "[patch-qwen-webui-navparams] history nav OLD pattern not found "
            "and NEW not present — version drift?",
            file=sys.stderr,
        )
        return 1
    else:
        print("[patch-qwen-webui-navparams] history nav already patched, skipping", file=sys.stderr)

    if OLD_PROJECT_NAV in data:
        if data.count(OLD_PROJECT_NAV) != 1:
            print(
                "[patch-qwen-webui-navparams] project-selector nav pattern not unique — "
                "aborting to avoid corrupting the bundle",
                file=sys.stderr,
            )
            return 1
        data = data.replace(OLD_PROJECT_NAV, NEW_PROJECT_NAV)
    elif NEW_PROJECT_NAV not in data and NEW_PROJECT_NAV_V2 not in data:
        print(
            "[patch-qwen-webui-navparams] project-selector nav OLD pattern not found "
            "and NEW/V2 not present — version drift?",
            file=sys.stderr,
        )
        return 1
    else:
        print(
            "[patch-qwen-webui-navparams] project-selector nav already patched, skipping",
            file=sys.stderr,
        )

    # Project-selector v2 upgrade: keep-search (v1) -> machine-id aware (v2).
    # v2 makes clicking a remote project append workspaceType=remote&machineId
    # so ChatPage starts a remote session instead of degrading to local mode.
    if OLD_PROJECT_NAV_V2 in data:
        if data.count(OLD_PROJECT_NAV_V2) != 1:
            print(
                "[patch-qwen-webui-navparams] project-selector v2 OLD pattern not unique — "
                "aborting to avoid corrupting the bundle",
                file=sys.stderr,
            )
            return 1
        data = data.replace(OLD_PROJECT_NAV_V2, NEW_PROJECT_NAV_V2)
        print(
            "[patch-qwen-webui-navparams] project-selector nav upgraded to machine-id aware (v2)",
            file=sys.stderr,
        )
    elif NEW_PROJECT_NAV_V2 not in data:
        print(
            "[patch-qwen-webui-navparams] project-selector v2 OLD pattern not found "
            "and v2 not present — version drift?",
            file=sys.stderr,
        )
        return 1
    else:
        print(
            "[patch-qwen-webui-navparams] project-selector nav already at v2, skipping",
            file=sys.stderr,
        )

    try:
        with open(bundle, "w", encoding="utf-8") as f:
            f.write(data)
    except OSError as exc:
        print(f"[patch-qwen-webui-navparams] cannot write bundle: {exc}", file=sys.stderr)
        return 1

    # Cache-bust the bundle URL so browsers drop the stale cached copy.
    # Handle both the pristine script src and an older ?v=navparams- bust.
    index_ok = True
    try:
        with open(INDEX_HTML, encoding="utf-8") as f:
            html = f.read()
        basename = bundle.rsplit("/", 1)[-1]
        script_src = f'src="/assets/{basename}"'
        busted_src = f'src="/assets/{basename}?{CACHE_BUST}"'
        if busted_src in html:
            print("[patch-qwen-webui-navparams] index.html cache-bust already applied, skipping")
        elif script_src in html:
            html = html.replace(script_src, busted_src)
            with open(INDEX_HTML, "w", encoding="utf-8") as f:
                f.write(html)
            print("[patch-qwen-webui-navparams] index.html cache-bust applied")
        else:
            # Older bust (different ?v=navparams- version) — swap it for the
            # current one so browsers fetch the freshly patched bundle.
            old_busted = f'src="/assets/{basename}?v=navparams-'
            idx = html.find(old_busted)
            if idx >= 0:
                end = html.find('"', idx)
                html = html[:idx] + busted_src + html[end:]
                with open(INDEX_HTML, "w", encoding="utf-8") as f:
                    f.write(html)
                print("[patch-qwen-webui-navparams] index.html cache-bust bumped")
            else:
                print(
                    f"[patch-qwen-webui-navparams] script src {script_src!r} not found in index.html",
                    file=sys.stderr,
                )
                index_ok = False
    except OSError as exc:
        print(f"[patch-qwen-webui-navparams] cannot patch index.html: {exc}", file=sys.stderr)
        index_ok = False

    print(
        f"[patch-qwen-webui-navparams] patched {bundle}: sessionId nav keeps URL params, "
        f"{history_count} history nav(s) keep URL params, project-selector nav keeps URL params"
    )
    return 0 if index_ok else 1


if __name__ == "__main__":
    sys.exit(main())
