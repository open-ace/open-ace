---
name: debug-session-visibility
description: Debug why sessions (codex/claude/qwen/openclaw) are not showing in the Work Mode session list
source: auto-skill
extracted_at: '2026-06-03T11:03:28.516Z'
---

# Debug Session Visibility

When a user reports sessions missing from the left-panel session list in Work Mode,
follow this pipeline-checking approach to isolate the root cause.

## Data Pipeline Overview

There are **two independent import paths** for sessions. Check both:

### Path A: Local fetch scripts (DataFetchScheduler)
```
Source JSONL files → fetch_<tool>.py import → agent_sessions DB table → API → Frontend
```

### Path B: Remote terminal session_sync
```
Remote agent session_sync.py → WebSocket → remote.py handler → agent_sessions DB table → API → Frontend
```

A session can be missing at any stage. Check each stage in order.

## Step 1: Check if source files exist

Verify the raw session data exists on disk for the expected date:

```bash
# Codex
ls -la ~/.codex/sessions/YYYY/MM/DD/

# Claude
ls -la ~/.claude/projects/*/sessions/

# Qwen
ls -la ~/.qwen/sessions/
```

If no files exist for today → the tool hasn't created sessions yet (user-side issue).

## Step 2: Check if sessions are in the database

Query `agent_sessions` directly to see if the sessions were imported:

```python
from scripts.shared.db import get_connection
conn = get_connection()
cursor = conn.cursor()
# Check by tool_name and recent date
cursor.execute("SELECT session_id, title, tool_name, user_id, created_at, updated_at FROM agent_sessions WHERE tool_name = 'codex' ORDER BY updated_at DESC LIMIT 10")
for r in cursor.fetchall(): print(dict(r))
conn.close()
```

If the session ID from the file is **NOT FOUND** → the fetch script hasn't run.
If it IS found → skip to Step 4.

## Step 3: Check if the data fetch ran recently

The `DataFetchScheduler` runs `fetch_<tool>.py` periodically (default 5 min).

1. Check if open-ace service is running: `lsof -i :5001` (port may vary by config)
2. Check scheduler status via API: `GET /api/fetch/status`
3. **Read the output carefully** — the scheduler may be running but the script
   may be silently failing. Look for `"success": false` in the last_result.
4. If service is down → restart it (`python3 web.py`)
5. Manually trigger a fetch if needed: `POST /api/fetch/data`
   Or run the script directly:
   ```bash
   python3 scripts/fetch_codex.py --days 1 --multi-user --recent --config <config_path>
   ```

### Known failure: Codex glob pattern

`find_all_codex_session_dirs()` uses `codex_sessions.glob("*/*/*/*.jsonl")` to detect
if a user has codex data. The Codex directory structure is `sessions/YYYY/MM/DD/file.jsonl`
(4 levels deep). If this glob is wrong (e.g. `*/*/*.jsonl` for 3 levels), the scheduler
reports `"No codex session directories found for any user."` every time — **even though
files exist**. Verify by testing the glob directly:

```python
from pathlib import Path
sessions = Path.home() / ".codex" / "sessions"
print(any(sessions.glob("*/*/*/*.jsonl")))  # Should be True if files exist
```

## Step 4: Check user_id association (most common silent failure)

The session list API filters by `user_id = g.user["id"]`. If `user_id` is NULL in
`agent_sessions`, the session is invisible to ALL logged-in users.

```python
# Check for NULL user_id sessions
cursor.execute("SELECT session_id, host_name, user_id FROM agent_sessions WHERE tool_name = 'codex' AND user_id IS NULL")
```

### Why user_id ends up NULL

The `fetch_codex.py` script resolves `user_id` from `sender_name` (format: `{user}-{hostname}-{tool}`)
by matching against `users.system_account` or `users.username`. If the system_account
doesn't match, user_id stays NULL.

Common causes:
- `system_account` not set on the user record
- Hostname in sender_name contains hyphens confusing the rsplit parsing
- Session was created on a different machine/container

### Remote session_sync path (Path B)

Sessions from remote terminals (Claude Code, Codex, Qwen running on remote machines)
are synced via `remote-agent/session_sync.py` → WebSocket `session_sync` message →
`app/routes/remote.py` handler. This handler creates `agent_sessions` records.

Key details about this path:
- `host_name` is set to `machine_id[:8]` (first 8 chars of the remote machine UUID),
  NOT the actual hostname. This produces hex strings like `f28b865e`, `0092acb3`.
- The `workspace_type` is set to `"terminal"` (not `"remote"`)
- Old machines that were re-registered produce orphaned host_name values no longer
  in `remote_machines` table

If `user_id` is NULL on these sessions, the `session_sync` handler failed to resolve
the user. The handler tries: (1) terminal session's user_id, (2) machine_assignments
table lookup by machine_id.

### Fix: Update NULL user_id sessions

```sql
UPDATE agent_sessions SET user_id = <correct_user_id> WHERE user_id IS NULL AND tool_name = 'codex'
```

Or more precisely, match by host_name patterns that belong to the user.

## Step 5: Check frontend filtering

The frontend `SessionList.tsx` groups by date (Today/Yesterday/This Week/Earlier)
based on `updated_at`. Verify the `updated_at` timestamp is correct and in the
expected timezone.

The API also supports `tool_name` and `search` filters — check the network request
in browser DevTools to confirm no unintended filters are applied.

## Quick Diagnosis Checklist

- [ ] Source JSONL files exist for the date?
- [ ] Session records exist in `agent_sessions` table?
- [ ] `user_id` on the session matches the logged-in user?
- [ ] open-ace service is running (scheduler active)?
- [ ] Scheduler fetch scripts succeed (check `/api/fetch/status` for `"success": false`)?
- [ ] Frontend API call returns the expected sessions?
- [ ] For remote sessions: check `host_name` matches `machine_id[:8]` pattern?
