# Issue #79 — Optimize config.json structure (remove redundant fields)

## Goal

Remove fields from `config.json` (and every generator/reader of them) that the
**current** code no longer consumes, so the config schema has a single source of
truth and stops advertising settings that silently do nothing. Scope was verified
field-by-field against the live code, not against the issue text or docs. Issue
points #2 (`token_secret` generation) and #3 (`webui_path` npm pollution) were
already fixed in commit `3431627`.

## Redundant field-sets (each verified to have ZERO readers in current code)

1. **`tools.<tool>.hostname`** (issue #79's named target). All fetchers read the
   top-level `config.get("host_name", "localhost")`
   (`fetch_qwen.py:1304`, `fetch_claude.py:1199`, `fetch_zcode.py`, `fetch_codex.py`,
   `fetch_openclaw.py`). The only code touching `tools.*.hostname` is a dead
   placeholder-cleanup loop in two copies of `load_config()`
   (`scripts/shared/utils.py:82-88`, `scripts/upload-to-central/shared/utils.py:81-87`)
   that mutates the dict in memory and is never saved or read.
2. **`auth` section** (`auth.auth_type`, `auth.env.OPENAI_API_KEY`,
   `auth.env.OPENAI_BASE_URL`). Dead by design: `WorkspaceConfig` was refactored to
   drop the `auth_type`/`auth_env` fields and `WebUIManager._load_config()` explicitly
   ignores the `auth` section (regression-guarded by
   `tests/unit/test_webui_manager.py::test_no_auth_type_field` /
   `test_load_config_from_file_without_auth`). `OPENAI_API_KEY` at runtime comes from
   the API-key proxy/store and `os.environ`, never from config `auth.env`. No
   `get_config_value("auth", …)` / `config.get("auth")` reader exists.
3. **`cron` section** (`cron.enabled`, `cron.run_time`). The daily fetch is driven by
   `app/services/data_fetch_scheduler.py`, which reads the `data_fetch` section
   (`get_data_fetch_interval` / `is_data_fetch_enabled`), not `cron`. No
   `get_config_value("cron", …)` / `config.get("cron")` reader exists. `cli.py:238`
   and the docker generators write it, but nothing consumes it.

Generators are already drifting/inconsistent (`cli.py` omits `hostname` and `auth`;
`manage.py` omits `cron`/`auth`), which corroborates all three as cruft.

## Naming standardization (issue point #4): host_name vs hostname

The only real divergence is the legacy `scripts/upload-to-central/` uploader, which
reads a flat top-level `config.get("hostname", …)`
(`upload_to_server.py:396,459`; `deploy.sh:395,424`) and generates a flat
`"hostname"` (`deploy.sh:123-131`). Standardize on `host_name` **backward-compatibly**:
prefer `host_name`, fall back to the old `hostname`, so existing flat configs keep
working; update `deploy.sh` to emit `host_name`.

## Changes

### Remove `tools.*.hostname`
1. `config/config.json.sample` — drop the three `"hostname"` lines; fix the orphaned
   trailing comma on whichever key becomes last in each tool entry
   (openclaw→`gateway_url`, claude→`enabled`, qwen→`enabled`).
2. `scripts/manage.py:143-145` — drop `"hostname": "localhost"` from the three tools.
3. `docker-entrypoint.sh:889-899` — drop the three `"hostname": "$HOST_NAME"` lines +
   comma fixes.
4. `scripts/install-central/docker-method/install.sh:3229-3243` — same.
5. `scripts/install-central/docker-method/quick-install-mac.sh:200-215` — same.
6. `scripts/shared/utils.py` — remove the dead tools-hostname cleanup loop in
   `load_config()` (keep the top-level `host_name` placeholder cleanup).
7. `scripts/upload-to-central/shared/utils.py` — remove the same dead loop.

### Remove dead `auth` section
8. `config/config.json.sample` — drop the `auth` block (lines 75-81).
9. `docker-entrypoint.sh:909-915` — drop the generated `auth` block.

### Remove dead `cron` section
10. `config/config.json.sample` — drop the `cron` block (lines 56-59).
11. `docker-entrypoint.sh:901-904` — drop the generated `cron` block.
12. `cli.py:238` — drop the `"cron"` entry from the default-config dict.
13. `scripts/install-central/docker-method/install.sh:3245-3248` — drop generated `cron`.
14. `config/CONFIG_GUIDE.md` — remove the now-inaccurate `定时任务` (cron) table
    (lines 118-122).

### Naming standardization (#4)
15. `scripts/upload-to-central/upload_to_server.py:396,459` — read
    `config.get("host_name") or config.get("hostname", os.uname().nodename)`.
16. `scripts/upload-to-central/deploy.sh` — emit `host_name` and read
    `config.get('host_name') or config.get('hostname', …)`.

### Docs
17. `config/CONFIG_GUIDE.md` — add a one-line note that top-level `host_name` is the
    single machine-identity key (tools inherit it); remove the cron table (item 14).

## Explicitly NOT touched (with rationale)
- Top-level `secret_key` — **kept**, it IS read (`events_ingest.py:74`,
  `scripts/shared/config.py:277`) as an events-ingest signing fallback.
- `insights` section — **kept**, read by `insights_service.py:171`.
- `data_fetch` / `quota_enforcement` — read by code but absent from the sample; that
  is a *missing* (not redundant) key, out of scope for this cleanup.

## Backward compatibility
Removing keys is safe for existing user configs: `json.load` keeps any stale
`tools.*.hostname` / `auth` / `cron` keys in the dict and every reader either uses
`.get(...)` with a default or never looks at them — no `KeyError`, no migration.

## Verification
- `python -m json.tool config/config.json.sample` — valid JSON.
- `bash -n docker-entrypoint.sh scripts/install-central/docker-method/install.sh scripts/install-central/docker-method/quick-install-mac.sh scripts/upload-to-central/deploy.sh` — parse OK; spot-check each generated `config.json` heredoc renders valid JSON (no dangling comma).
- `python -m ruff check` on every edited `.py`.
- `python -m pytest -q tests/unit/test_webui_manager.py tests/unit/test_webui_manager_42.py tests/unit/test_insights.py tests/unit/test_insights_service.py tests/unit/test_fetch_openclaw_dingtalk.py` (sample-validity + config-load guards).
- Post-change grep: `grep -rn '"hostname"' config/ scripts/manage.py cli.py docker-entrypoint.sh scripts/install-central/docker-method/` returns nothing in tool blocks; `grep -rn '"auth"\|"cron"' config/config.json.sample docker-entrypoint.sh cli.py` returns nothing.
