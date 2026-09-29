#!/usr/bin/env python3
"""
Docs governance gates for the 2026-09-29 restructure.

Checks (all must pass):
1. Bilingual completeness — every curated doc (docs/README.md plus guide/,
   dev/, contracts/, security/) contains exactly one "## English" and one
   "## 中文" section anchor. dev-notes/ is an English-only process archive
   and is exempt.
2. No language directories — docs/en/ and docs/cn/ must not exist (the
   single-file bilingual layout replaced them; the docs-site sync script
   splits on the anchors above).
3. Index consistency — every curated doc is linked from docs/README.md, so
   new files cannot land undiscoverable (the audit found four such files).
4. Endpoint coverage — every registered Flask route path appears in
   docs/dev/API.md (the audit found 69% of endpoints undocumented and three
   families of ghost endpoints that no longer existed).
5. Env coverage — every KEY in .env.example appears in
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


def curated_docs() -> list[Path]:
    files = [DOCS / "README.md"]
    for d in CURATED_DIRS:
        files.extend(sorted((DOCS / d).glob("*.md")))
    return [f for f in files if f.exists()]


def check_bilingual(errors: list[str]) -> None:
    for f in curated_docs():
        text = f.read_text(encoding="utf-8")
        rel = f.relative_to(REPO_ROOT)
        for anchor in ANCHORS:
            n = text.count(f"\n{anchor}\n") + text.startswith(f"{anchor}\n")
            if n != 1:
                errors.append(f"{rel}: anchor {anchor!r} appears {n} times (expected exactly 1)")


def check_no_language_dirs(errors: list[str]) -> None:
    for d in ("en", "cn"):
        if (DOCS / d).exists():
            errors.append(
                f"docs/{d}/ exists — per-language directories were replaced by "
                "single-file bilingual docs (2026-09-29 governance)"
            )


def check_index(errors: list[str]) -> None:
    readme = (DOCS / "README.md").read_text(encoding="utf-8")
    for f in curated_docs():
        if f.name == "README.md":
            continue
        rel = f.relative_to(DOCS).as_posix()
        if f"({rel})" not in readme:
            errors.append(f"{rel}: not linked from docs/README.md index")


def extract_endpoint_paths() -> set[str]:
    """Registered Flask route paths, prefixes resolved from app/__init__.py."""
    init_src = (REPO_ROOT / "app" / "__init__.py").read_text(encoding="utf-8")
    prefix_map = dict(re.findall(r'register_blueprint\((\w+),\s*url_prefix="([^"]+)"\)', init_src))
    route_re = re.compile(r"@(\w+)\.(?:route|get|post|put|delete|patch)\(\s*[\"']([^\"']*)[\"']")
    paths: set[str] = set()
    for f in sorted((REPO_ROOT / "app" / "routes").glob("*.py")):
        if f.name.startswith("_"):
            continue
        src = f.read_text(encoding="utf-8")
        for var, path in route_re.findall(src):
            prefix = prefix_map.get(
                var,
                (
                    re.search(r"url_prefix\s*=\s*[\"']([^\"']+)[\"']", src).group(1)
                    if re.search(r"url_prefix\s*=\s*[\"']([^\"']+)[\"']", src)
                    else ""
                ),
            )
            full = prefix + (path if path.startswith("/") or path == "" else f"/{path}")
            paths.add(full)
    for var, path in route_re.findall(init_src):
        paths.add(path if path.startswith("/") else f"/{path}")
    return paths


def check_endpoint_coverage(errors: list[str]) -> None:
    api_doc = DOCS / "dev" / "API.md"
    if not api_doc.exists():
        errors.append("docs/dev/API.md: missing")
        return
    text = api_doc.read_text(encoding="utf-8")
    missing = sorted(p for p in extract_endpoint_paths() if p not in text)
    for p in missing[:20]:
        errors.append(f"endpoint not documented in docs/dev/API.md: {p}")
    if missing:
        errors.append(
            f"docs/dev/API.md: {len(missing)} of {len(extract_endpoint_paths())} "
            "registered endpoint paths missing"
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
    missing = [k for k in keys if k not in text]
    if missing:
        errors.append(
            "docs/guide/ENV_REFERENCE.md: missing env vars: " + ", ".join(sorted(missing))
        )


def main() -> int:
    errors: list[str] = []
    check_no_language_dirs(errors)
    check_bilingual(errors)
    check_index(errors)
    check_endpoint_coverage(errors)
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
        f"{len(extract_endpoint_paths())} endpoint paths covered)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
