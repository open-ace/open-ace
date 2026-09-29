#!/usr/bin/env python3
"""
Docs governance gates for the 2026-09-29 restructure.

Checks (all must pass):
1. Bilingual completeness — every curated doc (docs/README.md plus guide/,
   dev/, contracts/, security/) follows the single-file bilingual template:
   a combined H1 ("# EN title — CN title"), the nav line
   "[English](#english) | [中文](#中文)", and exactly one "## English"
   section followed by one "## 中文" section. dev-notes/ is an English-only
   process archive and is exempt.
2. No language directories — docs/en/ and docs/cn/ must not exist (the
   single-file bilingual layout replaced them; the docs-site sync script
   splits on the anchors above).
3. Index consistency — every curated doc is linked from docs/README.md and
   every docs-relative markdown link in the index resolves to a curated file,
   so nothing lands undiscoverable and no link dangles.
4. Endpoint coverage — every registered Flask route path appears as an
   endpoint-table row in docs/dev/API.md (drift gate; the rewrite audit found
   69% undocumented and three families of ghost endpoints).
5. Env coverage — every KEY in .env.example appears as a table row in
   docs/guide/ENV_REFERENCE.md.

Exit code 1 with one line per violation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = REPO_ROOT / "docs"
CURATED_DIRS = ("guide", "dev", "contracts", "security")
ANCHORS = ("## English", "## 中文")
NAV_LINE = "[English](#english) | [中文](#中文)"


def curated_docs() -> list[Path]:
    files = [DOCS / "README.md"]
    for d in CURATED_DIRS:
        files.extend(sorted((DOCS / d).glob("*.md")))
    return [f for f in files if f.exists()]


def check_bilingual(errors: list[str]) -> None:
    for f in curated_docs():
        text = f.read_text(encoding="utf-8")
        rel = f.relative_to(REPO_ROOT)
        lines = text.splitlines()
        for anchor in ANCHORS:
            n = sum(1 for line in lines if line == anchor)
            if n != 1:
                errors.append(f"{rel}: anchor {anchor!r} appears {n} times (expected exactly 1)")
        en_idx = next((i for i, line in enumerate(lines) if line == "## English"), -1)
        cn_idx = next((i for i, line in enumerate(lines) if line == "## 中文"), -1)
        if en_idx != -1 and cn_idx != -1 and cn_idx < en_idx:
            errors.append(f"{rel}: '## 中文' appears before '## English'")
        if not lines or not lines[0].startswith("# ") or " — " not in lines[0][2:]:
            errors.append(f"{rel}: first line must be a combined H1 '# EN title — CN title'")
        if NAV_LINE not in text:
            errors.append(f"{rel}: missing nav line {NAV_LINE!r}")


def check_no_language_dirs(errors: list[str]) -> None:
    for d in ("en", "cn"):
        if (DOCS / d).exists():
            errors.append(
                f"docs/{d}/ exists — per-language directories were replaced by "
                "single-file bilingual docs (2026-09-29 governance)"
            )


def check_index(errors: list[str]) -> None:
    readme = (DOCS / "README.md").read_text(encoding="utf-8")
    curated_rel = {f.relative_to(DOCS).as_posix() for f in curated_docs() if f.name != "README.md"}
    for rel in sorted(curated_rel):
        # Must appear as an actual markdown link target (optionally with an
        # anchor) — not merely as a substring inside prose or a code block.
        if not re.search(r"\]\(" + re.escape(rel) + r"(#[^)\s]*)?\)", readme):
            errors.append(f"{rel}: not linked from docs/README.md index")
    # Reverse: every docs-relative link in the index must resolve.
    for match in re.finditer(
        r"\]\((guide|dev|contracts|security)/([^)#\s]+\.md)(#[^)\s]*)?\)", readme
    ):
        target = f"{match.group(1)}/{match.group(2)}"
        if target not in curated_rel:
            errors.append(f"docs/README.md links to {target} which is not a curated doc")


def extract_endpoint_paths() -> set[str]:
    """Registered Flask route paths, prefixes resolved from app/__init__.py."""
    init_src = (REPO_ROOT / "app" / "__init__.py").read_text(encoding="utf-8")
    prefix_map = dict(re.findall(r'register_blueprint\((\w+),\s*url_prefix="([^"]+)"\)', init_src))
    route_re = re.compile(r"@(\w+)\.(?:route|get|post|put|delete|patch)\(\s*[\"']([^\"']*)[\"']")
    paths: set[str] = set()
    route_files = list((REPO_ROOT / "app" / "routes").rglob("*.py")) + [
        REPO_ROOT / "app" / "__init__.py"
    ]
    for f in sorted(route_files):
        if f.name.startswith("_"):
            continue
        src = f.read_text(encoding="utf-8")
        file_prefix_match = re.search(
            r"Blueprint\(\s*[\"']\w+[\"']\s*,\s*__name__\s*,\s*url_prefix\s*=\s*[\"']([^\"']+)[\"']",
            src,
        )
        file_prefix = file_prefix_match.group(1) if file_prefix_match else ""
        for var, path in route_re.findall(src):
            prefix = prefix_map.get(var, file_prefix)
            full = prefix + (path if path.startswith("/") or path == "" else f"/{path}")
            paths.add(full)
    return paths


def check_endpoint_coverage(errors: list[str], endpoint_paths: set[str]) -> None:
    api_doc = DOCS / "dev" / "API.md"
    if not api_doc.exists():
        errors.append("docs/dev/API.md: missing")
        return
    text = api_doc.read_text(encoding="utf-8")
    # Match the path as an endpoint-table cell (| METHOD | `path` |) so a bare
    # path that is only a prefix of a longer documented path cannot pass.
    missing = sorted(
        p
        for p in endpoint_paths
        if not re.search(r"\| [A-Z/]+ \| `" + re.escape(p) + r"` \|", text)
    )
    for p in missing[:20]:
        errors.append(f"endpoint not documented in docs/dev/API.md: {p}")
    if missing:
        errors.append(
            f"docs/dev/API.md: {len(missing)} of {len(endpoint_paths)} registered endpoint paths missing"
        )


def check_env_coverage(errors: list[str]) -> None:
    env_example = REPO_ROOT / ".env.example"
    env_doc = DOCS / "guide" / "ENV_REFERENCE.md"
    if not env_example.exists() or not env_doc.exists():
        return
    keys = [
        line.split("=")[0]
        for line in env_example.read_text(encoding="utf-8").splitlines()
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", line)
    ]
    text = env_doc.read_text(encoding="utf-8")
    missing = [k for k in keys if not re.search(r"\|\s*`" + re.escape(k) + r"`", text)]
    if missing:
        errors.append(
            "docs/guide/ENV_REFERENCE.md: missing env vars: " + ", ".join(sorted(missing))
        )


def main() -> int:
    errors: list[str] = []
    check_no_language_dirs(errors)
    check_bilingual(errors)
    check_index(errors)
    endpoint_paths = extract_endpoint_paths()
    check_endpoint_coverage(errors, endpoint_paths)
    check_env_coverage(errors)
    for e in errors:
        print(f"ERROR: {e}")
    if errors:
        print(
            f"\n{len(errors)} docs-governance violation(s). "
            "See docs/README.md for the layout rules."
        )
        return 1
    print(
        "docs governance: OK "
        f"({len(curated_docs())} curated bilingual docs, "
        f"{len(endpoint_paths)} endpoint paths covered)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
