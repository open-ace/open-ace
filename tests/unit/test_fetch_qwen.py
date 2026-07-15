"""Unit coverage for Qwen fetch token accounting."""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from fetch_qwen import extract_tokens_from_entry, process_jsonl_file  # noqa: E402


def _write_jsonl(path: Path, entries: list[dict]) -> Path:
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def test_extract_tokens_keeps_provider_total_and_separates_cache():
    entry = {
        "type": "assistant",
        "model": "qwen-max",
        "usageMetadata": {
            "promptTokenCount": 1000,
            "candidatesTokenCount": 50,
            "thoughtsTokenCount": 20,
            "cachedContentTokenCount": 800,
            "totalTokenCount": 1050,
        },
    }

    tokens = extract_tokens_from_entry(entry)

    assert tokens["actual_input_tokens"] == 200
    assert tokens["cached_tokens"] == 800
    assert tokens["candidates_tokens"] == 50
    assert tokens["thoughts_tokens"] == 20
    assert tokens["total_tokens"] == 1050


def test_process_jsonl_file_counts_cache_in_total_without_double_counting_thoughts(tmp_path):
    jsonl = _write_jsonl(
        tmp_path / "sess-qwen.jsonl",
        [
            {
                "type": "assistant",
                "timestamp": "2026-07-15T07:00:00Z",
                "model": "qwen-max",
                "sessionId": "sess-qwen",
                "uuid": "assistant-1",
                "message": {
                    "message_id": "assistant-1",
                    "parts": [{"text": "hello"}],
                },
                "usageMetadata": {
                    "promptTokenCount": 1000,
                    "candidatesTokenCount": 50,
                    "thoughtsTokenCount": 20,
                    "cachedContentTokenCount": 800,
                    "totalTokenCount": 1050,
                },
            }
        ],
    )

    daily, messages = process_jsonl_file(jsonl, "localhost", "rhuang")

    assert daily["2026-07-15"]["prompt_tokens"] == 200
    assert daily["2026-07-15"]["candidates_tokens"] == 50
    assert daily["2026-07-15"]["thoughts_tokens"] == 20
    assert daily["2026-07-15"]["cached_tokens"] == 800
    assert daily["2026-07-15"]["total_tokens"] == 1050
    assert daily["2026-07-15"]["request_count"] == 1

    assert len(messages) == 1
    assert messages[0]["tokens_used"] == 1050
    assert messages[0]["input_tokens"] == 200
    assert messages[0]["output_tokens"] == 50
