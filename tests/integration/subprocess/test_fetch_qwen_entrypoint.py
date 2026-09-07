"""Exercise the deployed Qwen script without pytest's import-path setup."""

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.issue(3357)]

ROOT = Path(__file__).resolve().parents[3]


def test_direct_script_persists_user_text_without_system_envelopes(tmp_path):
    home_dir = tmp_path / "home"
    project = home_dir / ".qwen" / "projects" / "fixture-project"
    project.mkdir(parents=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    envelope = "<system-reminder>The current date is: 2026-09-07</system-reminder>"
    entries = [
        {
            "type": "user",
            "uuid": message_id,
            "parentUuid": None,
            "sessionId": "qwen-entrypoint-fixture",
            "timestamp": timestamp,
            "message": {"role": "user", "parts": [{"text": content}]},
        }
        for message_id, content in [
            ("mixed", envelope + "\nKeep this real user request."),
            ("system-only", envelope),
            ("plain", "Another real user request."),
        ]
    ]
    (project / "session.jsonl").write_text(
        "\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8"
    )
    database = tmp_path / "usage.db"
    # Deliberately do not inherit PYTHONPATH, DB_*, config, or real-user HOME.
    env = {
        "HOME": str(home_dir),
        "USERPROFILE": str(home_dir),
        "PATH": os.defpath,
        "DATABASE_URL": f"sqlite:///{database}",
    }
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "fetch_qwen.py"),
            "--project",
            str(project),
            "--hostname",
            "entrypoint-test",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT message_id, role, content FROM daily_messages ORDER BY message_id"
        ).fetchall()
    assert rows == [
        ("mixed", "user", "Keep this real user request."),
        ("plain", "user", "Another real user request."),
    ]
