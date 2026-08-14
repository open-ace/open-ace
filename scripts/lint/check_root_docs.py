#!/usr/bin/env python3
"""
Block ad-hoc process documents from landing in the repository root.

Between 2026-08-04 and 2026-08-12 the autonomous pipeline committed 24 such
files (2,777 lines) straight to the root -- CI_FIX_ROUND2.md,
CI_FIX_FINAL_VERIFICATION.md, IMPLEMENTATION_SUMMARY_2327.md, FINAL_STATUS.md
and friends. They referenced only each other, nothing else referenced them, and
they were the first thing a visitor saw on the repo landing page.

One of them, .issue_2189_implementation_summary.md, was a hidden dotfile that
`ls *.md` never showed -- so the patterns below and in .gitignore deliberately
cover the dotfile spelling too.

Process notes are useful; the root is just the wrong place for them. Anything
that is a fix write-up, status snapshot, progress log or implementation summary
belongs under docs/dev-notes/ (see docs/dev-notes/README.md).

The .gitignore carries matching patterns so these files never get staged by
accident. This hook is the backstop for the cases .gitignore cannot catch:
`git add -f`, a rename into the root, or a pattern nobody thought of.

Usage:
    python scripts/lint/check_root_docs.py [FILE ...]

    With no arguments, checks every tracked root-level *.md file.
    pre-commit passes the staged files.

Exit codes:
    0: root is clean
    1: disallowed process document(s) in the repository root
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

# Root-level Markdown that is legitimately part of the project's public face.
ALLOWED = {
    "README.md",
    "README_EN.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "ROADMAP.md",
    "SECURITY.md",
    "GOVERNANCE.md",
    "MAINTAINERS.md",
    "AUTHORS.md",
    "LICENSE.md",
    "CLAUDE.md",
    "AGENTS.md",
    "GEMINI.md",
    "QWEN.md",
}

# Naming shapes that mark a file as a process artifact rather than project doc.
PROCESS_PATTERNS = [
    re.compile(r"^CI_.*\.md$", re.I),
    re.compile(r".*_SUMMARY\.md$", re.I),
    re.compile(r".*_FIX(ES)?\.md$", re.I),
    re.compile(r"^FIX_.*\.md$", re.I),
    re.compile(r".*_FIX_.*\.md$", re.I),
    re.compile(r"^FINAL_.*\.md$", re.I),
    re.compile(r".*_STATUS\.md$", re.I),
    re.compile(r"^STATUS_.*\.md$", re.I),
    re.compile(r"^PROGRESS.*\.md$", re.I),
    re.compile(r"^IMPLEMENTATION_.*\.md$", re.I),
    re.compile(r"^ISSUE_.*\.md$", re.I),
    re.compile(r"^\d+[-_].*\.md$", re.I),  # 2437-plan.md
    re.compile(r"^HANDOVER.*\.md$", re.I),
    re.compile(r"^MERGE_STAGE.*\.md$", re.I),
    re.compile(r"^PYTHON_.*(FIX|COMPAT).*\.md$", re.I),
    re.compile(r"^P\d_.*\.md$", re.I),  # P0_FIXES_SUMMARY.md
    re.compile(r".*_(NOTE|NOTES|PLAN|ANALYSIS|VERIFICATION|REPORT)\.md$", re.I),
]

HINT = """
过程文档不要放在仓库根目录 —— 请改放 docs/dev-notes/ 。

    git mv {name} docs/dev-notes/{name}

根目录只保留面向使用者的文档（README / CHANGELOG / CONTRIBUTING / ROADMAP 等）。
若这确实是一份长期的项目级文档，把文件名加进 scripts/lint/check_root_docs.py 的 ALLOWED。
详见 docs/dev-notes/README.md。
"""


def is_process_doc(name: str) -> bool:
    if name in ALLOWED:
        return False
    return any(p.match(name) for p in PROCESS_PATTERNS)


def tracked_root_markdown() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "--", "*.md", ":(exclude)*/*"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [line for line in out.splitlines() if line and "/" not in line]


def main(argv: list[str]) -> int:
    candidates = argv[1:] or tracked_root_markdown()

    offenders = []
    for path in candidates:
        norm = path.replace("\\", "/")
        # Only the repository root is policed; docs/dev-notes/ is the sanctioned home.
        if "/" in norm:
            continue
        if not norm.lower().endswith(".md"):
            continue
        if is_process_doc(os.path.basename(norm)):
            offenders.append(norm)

    if not offenders:
        return 0

    print("仓库根目录出现过程文档（%d 个）:" % len(offenders))
    for name in offenders:
        print("  - %s" % name)
    print(HINT.format(name=offenders[0]))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
