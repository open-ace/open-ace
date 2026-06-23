from __future__ import annotations

import re

# Shared artifact-cleaning helpers used by both the autonomous runner and the
# orchestrator. Workflow timelines / comments should show publishable output,
# not the agent's process chatter or leaked tool payloads.

_AGENT_INTRO_PATTERNS = [
    re.compile(r"^我来[^\n]{0,30}[。！]"),
    re.compile(r"^让我[^\n]{0,30}[。！]"),
    re.compile(r"^首先[让我]*[^\n]{0,30}[。！]"),
    re.compile(r"^现在[我来让]*[^\n]{0,30}[。！]"),
    re.compile(r"^好的[，,][^\n]{0,30}[。！]"),
    re.compile(r"^方案[已完]*[^\n]{0,20}[。，！]"),
    re.compile(r"^探索完成[^\n]*"),
    re.compile(r"^分析完成[^\n]*"),
]

_AGENT_CLOSING_PATTERNS = [
    re.compile(r"下一步是否需要"),
    re.compile(r"是否需要开始"),
    re.compile(r"按照[^\n]*流程"),
    re.compile(r"按照[^\n]*工作流"),
    re.compile(r"建议[：:]\s*$"),
]

_PROCESS_MARKERS = (
    "the user wants me",
    "user wants me to",
    "let me",
    "wait, let me",
    "wait,",
    "hmm,",
    "actually,",
    "i need to",
    "i should",
    "i'm in plan mode",
    "exitplanmode",
    "working dir",
    "working directory",
    "conversation start",
    "让我",
    "我来",
    "现在让我",
    "待办事项",
    "待办事项列表",
    "检查一下",
    "更新一下",
)

_STRUCTURED_LINE_RE = re.compile(r"(?m)^(#{1,6}\s+|\s*(?:\d+\.\s+|[-*]\s+)).+")
_JSON_BLOB_LINE_RE = re.compile(
    r'(?im)^\s*\{.*"(description|prompt|tool|command|subagent_type|file_path)".*\}\s*$'
)
_PROCESS_LINE_RE = re.compile(
    r"(?im)^\s*(The user wants me to|user wants me to|Let me\b|I need to:).*$"
)
_REPEATED_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$")


def clean_agent_text(text: str) -> str:
    """Strip generic intro/closing narration while preserving structured content."""
    if not text:
        return text

    match = re.search(r"^#{1,6}\s", text, re.MULTILINE)
    if match:
        text = text[match.start() :]

    intro_scan_limit = 5
    lines = text.split("\n")
    intro_end = 0
    non_heading_count = 0
    seen_intro = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        is_heading = bool(re.match(r"^#{1,6}\s", stripped))
        if is_heading:
            if seen_intro:
                break
            continue
        non_heading_count += 1
        if non_heading_count > intro_scan_limit:
            break
        is_intro = any(p.search(stripped) for p in _AGENT_INTRO_PATTERNS)
        if is_intro:
            intro_end = i + 1
            seen_intro = True
        else:
            break
    if intro_end > 0:
        lines = lines[intro_end:]
        text = "\n".join(lines)

    lines = text.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and any(p.search(stripped) for p in _AGENT_CLOSING_PATTERNS):
            while i > 0 and not lines[i - 1].strip():
                i -= 1
            text = "\n".join(lines[:i])
            break

    return text.strip()


def _slice_from_structured_start(text: str) -> str:
    """Drop a noisy preamble when the agent later emits a structured artifact."""
    if not text:
        return ""
    head = re.sub(r"\s+", " ", text[:800]).lower()
    if not any(marker in head for marker in _PROCESS_MARKERS):
        return text

    match = _STRUCTURED_LINE_RE.search(text)
    if match and match.start() > 0:
        return text[match.start() :]
    return text


def _is_process_paragraph(paragraph: str) -> bool:
    stripped = paragraph.strip()
    if not stripped:
        return False
    if _STRUCTURED_LINE_RE.match(stripped):
        return False

    normalized = re.sub(r"\s+", " ", stripped).lower()
    if "exitplanmode" in normalized:
        return True
    if normalized.startswith(
        (
            "the user wants me",
            "user wants me to",
            "let me",
            "wait,",
            "hmm,",
            "actually,",
            "i need to",
            "i'm in plan mode",
            "让我",
            "我来",
            "现在让我",
            "好的，",
            "好的,",
        )
    ):
        return True

    marker_hits = sum(marker in normalized for marker in _PROCESS_MARKERS)
    return marker_hits >= 2


def _dedupe_adjacent_lines(text: str) -> str:
    lines = text.splitlines()
    deduped: list[str] = []
    last_norm = ""
    for line in lines:
        norm = re.sub(r"\s+", " ", line).strip()
        if norm and norm == last_norm:
            continue
        deduped.append(line)
        if norm:
            last_norm = norm
    return "\n".join(deduped)


def _truncate_at_repeated_heading(text: str) -> str:
    lines = text.splitlines()
    seen: dict[str, int] = {}
    out: list[str] = []
    emitted_chars = 0
    for line in lines:
        stripped = line.strip()
        match = _REPEATED_HEADING_RE.match(stripped)
        if match:
            heading = re.sub(r"\s+", " ", match.group(1)).strip().lower()
            if heading and heading in seen and emitted_chars >= 200:
                break
            if heading:
                seen[heading] = seen.get(heading, 0) + 1
        out.append(line)
        emitted_chars += len(line) + 1
    return "\n".join(out)


def sanitize_artifact_text(text: str) -> str:
    """Best-effort cleanup for workflow artifacts and session detail output."""
    if not text:
        return ""

    cleaned = _slice_from_structured_start(text)
    cleaned = clean_agent_text(cleaned)
    cleaned = _PROCESS_LINE_RE.sub("", cleaned)
    cleaned = _JSON_BLOB_LINE_RE.sub("", cleaned)
    cleaned = _truncate_at_repeated_heading(cleaned)
    cleaned = _dedupe_adjacent_lines(cleaned)

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", cleaned) if p.strip()]
    deduped: list[str] = []
    seen_norms: set[str] = set()
    for paragraph in paragraphs:
        if _is_process_paragraph(paragraph):
            continue
        norm = re.sub(r"\s+", " ", paragraph).strip().lower()
        if norm in seen_norms:
            continue
        deduped.append(paragraph)
        seen_norms.add(norm)

    return "\n\n".join(deduped).strip()


def score_artifact_text(text: str) -> int:
    """Score a cleaned candidate so callers can choose the best publishable view."""
    if not text:
        return -1
    score = len(text.strip())
    has_structure = bool(_STRUCTURED_LINE_RE.search(text))
    if has_structure:
        score += 120
    if "TL;DR:" in text:
        score += 40
    if not has_structure:
        paragraph_count = len([p for p in re.split(r"\n\s*\n", text) if p.strip()])
        score -= 80 * max(paragraph_count - 1, 0)
    head = re.sub(r"\s+", " ", text[:240]).lower()
    score -= 200 * sum(marker in head for marker in _PROCESS_MARKERS)
    return score


def pick_best_artifact_text(*texts: str) -> str:
    """Clean multiple candidates and return the best publishable artifact."""
    best_text = ""
    best_score = -1
    seen: set[str] = set()
    for text in texts:
        cleaned = sanitize_artifact_text(text or "")
        if not cleaned:
            continue
        norm = re.sub(r"\s+", " ", cleaned).strip().lower()
        if norm in seen:
            continue
        seen.add(norm)
        score = score_artifact_text(cleaned)
        if score > best_score:
            best_text = cleaned
            best_score = score
    return best_text
