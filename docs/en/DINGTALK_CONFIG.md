# DingTalk Integration Configuration

This guide explains how to configure DingTalk integration for:

- imported-session user and group name resolution
- local org sync of DingTalk departments and users into Open ACE teams and users
- alert delivery to DingTalk custom robot webhooks

## Overview

Open ACE can use DingTalk APIs to:

- Display real DingTalk user names instead of raw `userId` values
- Display DingTalk group names instead of raw `chatId` metadata when available
- Sync DingTalk departments into Open ACE collaboration teams
- Sync DingTalk users into local users + team memberships
- Send alert center notifications to DingTalk group robots

Current scope:

- OpenClaw message import path
- DingTalk user name resolution
- DingTalk group name resolution when session metadata contains a DingTalk `chatId`
- Manual and optionally scheduled DingTalk organization sync
- DingTalk custom robot webhook notification payloads

## Prerequisites

1. A DingTalk developer account
2. An internal enterprise application with API access
3. An AppKey and AppSecret for that application
4. Optional: a DingTalk custom robot webhook URL for alert delivery

## Setup

### 1. Create a DingTalk app

1. Visit the DingTalk Open Platform
2. Create an internal enterprise application
3. Save the **AppKey** and **AppSecret**

### 2. Configure Open ACE

Edit `~/.open-ace/config.json`:

```json
{
  "dingtalk": {
    "app_key": "dingxxxxxxxxxxxxxx",
    "app_secret": "your_app_secret_here",
    "org_sync_enabled": true,
    "org_sync_tenant_id": 1,
    "org_sync_interval_minutes": 60,
    "org_sync_root_dept_id": "1"
  }
}
```

### 3. Test name-resolution configuration

```bash
# Test user name lookup
python3 scripts/shared/dingtalk_user_cache.py test manager123 <app_key> <app_secret>

# Test group name lookup
python3 scripts/shared/dingtalk_group_cache.py test chatabcd1234 <app_key> <app_secret>
```

### 4. Sync the organization structure

After saving the config:

1. Restart Open ACE if you changed `config.json`
2. Call `POST /api/admin/dingtalk/sync` as an administrator, optionally with `{"tenant_id": 1}`

When `dingtalk.org_sync_enabled=true`, the background data-fetch scheduler also performs periodic sync based on `dingtalk.org_sync_interval_minutes`.

Current sync behavior:

- departments are mirrored into collaboration `teams`
- users are provisioned into local `users`
- DingTalk identities are linked through `sso_identities`
- team memberships are reconciled for DingTalk-managed teams

Current non-goals:

- disabling or deleting local users when they disappear from DingTalk
- removing DingTalk-managed teams automatically when departments disappear
- DingTalk SSO login flow
- inbound DingTalk chatbot commands

### 5. Configure DingTalk robot alerts

In `Manage -> Quota Alerts -> Notification Preferences`, set the webhook URL to a DingTalk custom robot URL such as:

```text
https://oapi.dingtalk.com/robot/send?access_token=xxxxxxxx
```

Open ACE sends DingTalk-compatible `text` payloads for alert notifications. If the DingTalk robot requires signing, set `alerts.dingtalk_webhook_secret` in `config.json`. As a per-webhook fallback, `openace_dingtalk_secret=<secret>` can be added to the saved URL; Open ACE strips that parameter before sending and adds DingTalk's `timestamp` / `sign` parameters.

## Cache management

| Command | Description |
|---------|-------------|
| `python3 scripts/shared/dingtalk_user_cache.py list` | List cached users |
| `python3 scripts/shared/dingtalk_user_cache.py clear` | Clear user cache |
| `python3 scripts/shared/dingtalk_group_cache.py list` | List cached groups |
| `python3 scripts/shared/dingtalk_group_cache.py clear` | Clear group cache |

Cache files:

- `~/.open-ace/dingtalk_users.json`
- `~/.open-ace/dingtalk_groups.json`

## Troubleshooting

### User names are not resolving

- Check that the DingTalk app credentials are correct
- Verify the app can call user-detail APIs
- Confirm the imported session metadata contains a DingTalk `sender_id`

### Group names are not resolving

- Confirm the imported session metadata contains a DingTalk `chatId`
- Check that the app can call group-info APIs
- Verify the cached label includes a `chat...` identifier

## Disabling integration

To disable DingTalk integration, remove the `dingtalk` section from your config file. To keep name resolution but stop scheduled org sync, set `dingtalk.org_sync_enabled=false`.
