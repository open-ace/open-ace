"""Issue acceptance-snapshot parsing for the acceptance_verification phase (#2335).

Pure module: markdown-section extraction + canonical hashing. No git/db deps.
Convention sections: ## Scope, ## 验收标准 / ## Acceptance Criteria, ## 不在 Scope /
## Non-Scope. When required sections are absent, source="missing" and the verifier
performs LLM extraction (re-persisted with source="llm", confidence="low").
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

# Matches a markdown heading and captures its title.
_SECTION_RE = re.compile(r"(?m)^(?:#{1,6})\s+(.*?)\s*$")
_CLOSURE_CONSTRAINT_RE = re.compile(
    r"禁止阶段性关闭|do not close (?:until|before)|no (?:premature|staged) close",
    re.IGNORECASE,
)
# A path/glob token inside a list item: a backticked token, or a bare token that
# looks like a path (contains '/', or a dot extension, or glob chars).
_TOKEN_PATH_RE = re.compile(r"`?([A-Za-z0-9_./?*{}\[\]\-]+)`?")


@dataclass
class AcceptanceSnapshot:
    """Parsed acceptance criteria for an issue.

    Persisted + hashed for the acceptance_verification phase; see the module
    docstring for the section convention.
    """

    required_paths: list[str] = field(default_factory=list)
    checklist: list[str] = field(default_factory=list)
    non_scope: list[str] = field(default_factory=list)
    closure_constraints: bool = False
    source: str = "missing"  # "convention" | "missing" | "llm"
    confidence: str = "low"  # "high" (convention) | "low" (missing/llm)

    def to_canonical(self) -> dict:
        # The hash captures the acceptance *content* (paths/checklist/non-scope/
        # constraints), not source/confidence — so an issue edit changes the hash
        # and forces re-verification, while a same-content LLM re-extraction is stable.
        return {
            "required_paths": sorted(self.required_paths),
            "checklist": sorted(self.checklist),
            "non_scope": sorted(self.non_scope),
            "closure_constraints": self.closure_constraints,
        }


def _split_sections(body: str) -> dict[str, str]:
    """Return {lowercased_heading: body-until-next-heading}."""
    matches = list(_SECTION_RE.finditer(body))
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        title = m.group(1).strip().lower()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections[title] = body[start:end]
    return sections


def _extract_paths(text: str) -> list[str]:
    paths: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith(("-", "*")):
            continue
        item = line.lstrip("-* ").strip()
        # Take the first path-shaped token on the line (backticked preferred).
        for tok in _TOKEN_PATH_RE.findall(item):
            if "/" in tok or tok.startswith(".") or "*" in tok:
                paths.append(tok.strip("`"))
                break
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _extract_checklist(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        m = re.match(r"\s*[-*]\s+\[[ xX]\]\s+(.*)$", line)
        if m:
            out.append(m.group(1).strip())
    return out


def _extract_plain_list(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(("-", "*")):
            out.append(line.lstrip("-* ").strip())
    return out


_SCOPE_TITLES = {"scope"}
_CRITERIA_KEYWORDS = ("验收标准", "acceptance criteria", "acceptance", "验收")
_NONSCOPE_KEYWORDS = ("不在 scope", "non-scope", "non scope", "out of scope")


def parse_acceptance_snapshot(body: str) -> AcceptanceSnapshot:
    sections = _split_sections(body or "")
    snap = AcceptanceSnapshot()

    scope_text = next((sections[t] for t in sections if t in _SCOPE_TITLES), None)
    criteria_text = next(
        (sections[t] for t in sections if any(k in t for k in _CRITERIA_KEYWORDS)), None
    )
    nonscope_text = next(
        (sections[t] for t in sections if any(k in t for k in _NONSCOPE_KEYWORDS)), None
    )

    if scope_text is not None or criteria_text is not None:
        snap.source = "convention"
        snap.confidence = "high"

    if scope_text is not None:
        snap.required_paths = _extract_paths(scope_text)
    if criteria_text is not None:
        snap.checklist = _extract_checklist(criteria_text)
    if nonscope_text is not None:
        snap.non_scope = _extract_plain_list(nonscope_text)

    snap.closure_constraints = bool(_CLOSURE_CONSTRAINT_RE.search(body or ""))
    return snap


def hash_snapshot(snap: AcceptanceSnapshot) -> str:
    canonical = json.dumps(snap.to_canonical(), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
