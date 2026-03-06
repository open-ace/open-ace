# Project Summary

## Overall Goal
Build and maintain an AI token usage tracking and analysis system that collects data from multiple AI tools (OpenClaw, Claude, Qwen) across multiple machines, with a web-based dashboard for visualization and analysis.

## Key Knowledge

### Architecture
- **Central Server**: 192.168.31.181:5001 (Flask web application)
- **Remote Machine**: 192.168.31.159 (hostname: ai-lab, user: openclaw)
- **Data Flow**: Remote machine collects OpenClaw logs → uploads to central server via HTTP API → stored in SQLite database
- **Database**: `~/.ai-token-analyzer/usage.db` with tables: `daily_usage`, `daily_messages`

### Technology Stack
- **Backend**: Python 3.9+, Flask, SQLite
- **Frontend**: HTML templates, Chart.js, Bootstrap
- **Data Collection**: Custom scripts (`fetch_openclaw_messages.py`, `fetch_claude.py`, `fetch_qwen.py`)
- **Remote Sync**: HTTP POST to `/api/upload/batch` with auth key

### Important Files
| File | Purpose |
|------|---------|
| `web.py` | Flask web server with API endpoints |
| `templates/index.html` | Dashboard and Messages UI |
| `scripts/fetch_openclaw_messages.py` | OpenClaw message extraction |
| `scripts/shared/db.py` | Database operations |
| `scripts/shared/feishu_user_cache.py` | Feishu user info caching |
| `scripts/shared/utils.py` | Utility functions |
| `scripts/shared/email_notifier.py` | Email notification (renamed from email.py) |
| `~/.ai-token-analyzer/config.json` | Local configuration |
| `remote_config.json` | Remote machine configuration template |

### User Preferences
- Messages page should display clean user content without metadata
- Sender names should be shown when available (Slack/Feishu)
- Message source badges: Slack (purple), Feishu (cyan), OpenClaw (blue)
- Timestamps should be removed from message content display
- Feishu user IDs (`ou_xxxxx`) should be resolved to real names via API

### Build/Run Commands
```bash
# Start web server
python3 web.py

# Fetch messages locally
python3 scripts/fetch_openclaw_messages.py --days 7

# Fetch messages on remote machine
ssh openclaw@192.168.31.159 "cd /opt/ai-token-analyzer && python3 scripts/fetch_openclaw_messages.py --days 7"

# Push to GitHub (with V2Box auto-connect)
python3 ~/.qwen/scripts/github-push-auto.py
```

## Recent Actions

### [DONE] Fixed Messages Page Host List
- **Problem**: Host dropdown was empty on Messages page
- **Root Cause**: JavaScript only initialized Dashboard host filter, not Messages host filter
- **Fix**: Added `host-filter` initialization in `templates/index.html`
- **Result**: Both Dashboard and Messages pages now show all hosts

### [DONE] Fixed Remote Data Collection
- **Problem**: Remote machine (192.168.31.159) uploads failing with HTTP 500 errors
- **Root Cause**: JSON parsing errors in uploaded data, outdated scripts on remote machine
- **Fix**:
  - Improved error handling in `web.py` upload endpoint
  - Updated scripts on remote machine via root SSH access
  - Scripts synced: `fetch_openclaw_messages.py`, `db.py`, `config.py`, `feishu_user_cache.py`
- **Result**: Remote machine now successfully uploads data every 30 seconds

### [DONE] Enhanced Message Extraction
- **Slack Messages**: Extract sender name from "Slack message in #channel from Name: content" format
- **Slack DM**: Extract sender name from "Slack DM from Name: content" format
- **Feishu Messages**: Detect via `conversation_label` field or `ou_` sender_id prefix
- **Content Cleaning**: Remove metadata blocks, timestamps, mention tags
- **Result**: Messages display clean content with sender info and source badges

### [DONE] Added Feishu User Name Lookup
- **Feature**: Resolve Feishu user IDs to real names via Feishu API
- **Implementation**: `feishu_user_cache.py` module with local caching (1 hour TTL)
- **Configuration**: Requires `feishu_app_id` and `feishu_app_secret` in config.json
- **Permissions Required**: `contact:contact:user:readonly`
- **Documentation**: Created `FEISHU_USER_CONFIG.md` with setup instructions

### [DONE] Fixed Python Module Naming Conflict
- **Problem**: `scripts/shared/email.py` conflicted with Python's built-in `email` module
- **Fix**: Renamed to `scripts/shared/email_notifier.py`
- **Updated**: `__init__.py`, `cli.py`, and remote machine files
- **Result**: No more import errors

### [DONE] Configured Feishu App Credentials
- **App ID**: `cli_a92be94ec4395cc2`
- **App Secret**: `6pvXz79b6gqadmEGKWIuVdTEjkf1DkSf`
- **Status**: ✅ Working - User names now resolved (e.g., `ou_3e479c7f81f8674741d778e8f838f8ed` → `韩成凤`)
- **Updated**: Local and remote config.json files

### [DONE] Updated Web Upload API
- **Added**: `sender_id`, `sender_name`, `message_source` field support in `/api/upload/batch` endpoint
- **Result**: Remote uploads now include complete sender information

### [DONE] Fixed Remote Script Import Error
- **Problem**: `utils` module not imported in `fetch_openclaw_messages.py`
- **Fix**: Added `import utils` to script imports
- **Result**: Remote script runs successfully

### [DONE] Cleaned and Reorganized Remote Deployment
- **Problem**: Remote machine files were disorganized with mixed ownership (root/openclaw)
- **Solution**:
  - Removed old `/opt/ai-token-analyzer/` directory
  - Deployed to `/home/openclaw/ai-token-analyzer/` (openclaw user's home)
  - All files now owned by openclaw:openclaw
  - Removed unnecessary scripts:
    - `email_notifier.py` - Email sent from central server only
    - `fetch_claude.py`, `fetch_qwen.py` - Central server use only
    - `check_*.py`, `test_*.py` - Debug/test scripts
    - `deploy_remote.py`, `fetch_remote.py`, `upload_to_server.py` - Deployment tools
    - Web service scripts (`start_web.sh`, etc.)
  - Kept only essential scripts: `fetch_openclaw.py`, `create_db.py`, `init_db.py`, `setup.py`
  - Updated `__init__.py` to remove `email_notifier` reference
- **Documentation**: Created `REMOTE_DEPLOY.md` with deployment guide
- **Scripts**: Created `scripts/clean_deploy_remote.sh` for automated redeployment
- **Result**: Clean, organized deployment with consistent ownership

### [DONE] Renamed fetch_openclaw_messages.py to fetch_openclaw.py
- **Reason**: Consistent naming convention, merged functionality
- **Updated References**:
  - `web.py` - API endpoint
  - `scripts/fetch_remote.py` - Remote execution
  - `scripts/fetch_all_tools.py` - Tool runner
  - `scripts/clean_deploy_remote.sh` - Deployment script
  - `scripts/sync_remote.sh` - Sync script
  - `contrib/fetch-openclaw.service` - systemd service
  - `REMOTE_DEPLOY.md` - Documentation
- **Fixed**: Added missing `import utils` to script
- **Result**: Script renamed and all references updated

### [DONE] Cleaned Up Central Server Scripts
- **Removed Unnecessary Scripts**:
  - `check_*.py` - Debug/check utilities
  - `test_*.py` - Test scripts
  - `db_info.py`, `fix_timestamps.py` - Debug tools
  - `deploy_remote.py`, `fetch_remote.py`, `upload_to_server.py` - Deployment tools
  - `fetch_all_tools.py` - Replaced by direct calls
- **Kept Essential Scripts**:
  - `fetch_openclaw.py` - OpenClaw data collection (messages + tokens)
  - `fetch_claude.py` - Claude data collection
  - `fetch_qwen.py` - Qwen data collection
  - `create_db.py`, `init_db.py`, `setup.py` - Database utilities
- **Result**: Cleaner codebase with only necessary scripts

### [DONE] Created Unified Management Script
- **New Script**: `scripts/manage.py`
- **Features**:
  - Local deployment (central server): setup, install, start, stop, status
  - Remote deployment (ai-lab): deploy, sync, status
  - Replaces multiple shell scripts
- **Removed Scripts**:
  - `sync_remote.sh` - Replaced by `manage.py remote sync`
  - `clean_deploy_remote.sh` - Replaced by `manage.py remote deploy`
  - `start_web.sh` - Replaced by `manage.py local start`
  - `stop_web.sh` - Replaced by `manage.py local stop`
  - `install_web_service.sh` - Replaced by `manage.py local install`
- **Result**: Single unified script for all deployment and management tasks

### [DONE] Separated Development and Deployment Directories
- **Development Directory**: `/Users/rhuang/workspace/ai-token-analyzer/` - Source code and development
- **Deployment Directory**: `~/ai-token-analyzer/` - Actual runtime deployment
- **Deploy Command**: `python3 scripts/manage.py local deploy`
- **Result**: Clean separation between development and production environments

## Current Plan

1. **[DONE]** Fix Messages page host filter initialization
2. **[DONE]** Fix remote machine data collection
3. **[DONE]** Implement message content cleaning
4. **[DONE]** Add message source detection and badges
5. **[DONE]** Create Feishu user name lookup feature
6. **[DONE]** Configure Feishu app credentials for user name resolution
7. **[DONE]** Fix Python email module naming conflict
8. **[DONE]** Update remote machine scripts and configuration
9. **[DONE]** Clean and reorganize remote deployment
10. **[DONE]** Rename fetch_openclaw_messages.py to fetch_openclaw.py
11. **[DONE]** Clean up central server scripts
12. **[DONE]** Create unified management script (manage.py)
13. **[DONE]** Remove unused service files
14. **[DONE]** Reorganize config files
15. **[DONE]** Separate development and deployment directories
16. **[TODO]** Monitor remote machine uploads for any new issues
17. **[TODO]** Consider adding similar user lookup for Slack users

## Open Issues

1. **Script Synchronization**: Remote machine scripts need to be kept in sync with local development (use `python3 scripts/manage.py remote sync`)

## Testing Checklist

- [x] Dashboard shows all hosts (RichdeMacBook-Pro.local, ai-lab)
- [x] Messages page shows all hosts in filter dropdown
- [x] Slack messages display sender name + [SLACK] badge
- [x] Feishu messages display sender name + [FEISHU] badge
- [x] Message content is cleaned (no metadata, timestamps)
- [x] Feishu user names resolved (韩成凤)
- [x] Remote machine uploads working (HTTP 200 responses)
- [x] No Python import errors
- [x] Remote deployment cleaned and organized
- [x] All remote files owned by openclaw:openclaw
- [x] fetch_openclaw_messages.py renamed to fetch_openclaw.py
- [x] Central server scripts cleaned up
- [x] Unified management script created (manage.py)
- [x] Unused service files removed
- [x] Config files reorganized
- [x] Development and deployment directories separated

## Feishu User Cache

**Cached Users:**
- `ou_3e479c7f81f8674741d778e8f838f8ed` → `韩成凤`

**Cache Location:** `~/.ai-token-analyzer/feishu_users.json`

**Cache TTL:** 1 hour (3600 seconds)

---

## Summary Metadata
**Update time**: 2026-03-06T17:00:00+08:00
**Last updated by**: AI Assistant
