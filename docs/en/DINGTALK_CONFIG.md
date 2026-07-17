# DingTalk Integration Configuration

This guide explains how to configure DingTalk integration so Open ACE can resolve DingTalk user names and group names from imported OpenClaw session logs.

## Overview

Open ACE can integrate with DingTalk to:

- Display real DingTalk user names instead of raw `userId` values
- Display DingTalk group names instead of raw `chatId` metadata when available

Current scope:

- OpenClaw message import path
- DingTalk user name resolution
- DingTalk group name resolution when session metadata contains a DingTalk `chatId`

## Prerequisites

1. A DingTalk developer account
2. An internal enterprise application with API access
3. An AppKey and AppSecret for that application

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
    "app_secret": "your_app_secret_here"
  }
}
```

### 3. Test configuration

```bash
# Test user name lookup
python3 scripts/shared/dingtalk_user_cache.py test manager123 <app_key> <app_secret>

# Test group name lookup
python3 scripts/shared/dingtalk_group_cache.py test chatabcd1234 <app_key> <app_secret>
```

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

To disable DingTalk integration, remove the `dingtalk` section from your config file.
