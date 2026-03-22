# Feishu Integration Configuration

> **ACE** = **AI Computing Explorer**

This guide explains how to configure Feishu (Lark) integration to display real user names and group names instead of IDs.

## Overview

Open ACE can integrate with Feishu to:

- Display real user names instead of `ou_xxxxx` IDs
- Display group names instead of `oc_xxxxx` identifiers

## Prerequisites

1. A Feishu developer account
2. Admin access to create a custom app

## Setup

### 1. Create a Feishu App

1. Visit [Feishu Open Platform](https://open.feishu.cn/app)
2. Click "Create App" → "Enterprise Custom App"
3. Fill in app name (e.g., "Open ACE")
4. Save the **App ID** and **App Secret**

### 2. Configure Permissions

In the app settings:

1. Go to "Permissions"
2. Request the following permissions:

| Permission | Description |
|------------|-------------|
| `contact:contact:user:readonly` | Read user information |
| `chat:chat:readonly` | Read chat information |

3. Submit for approval if required
4. Publish the app

### 3. Configure Open ACE

Edit `~/.open-ace/config.json`:

```json
{
  "feishu": {
    "app_id": "cli_xxxxxxxxxxxxxxxx",
    "app_secret": "your_app_secret_here"
  }
}
```

### 4. Test Configuration

```bash
# Test user info query
python3 scripts/shared/feishu_user_cache.py test ou_xxxxx <app_id> <app_secret>

# Test group info query
python3 scripts/shared/feishu_group_cache.py test chat_xxxxx <app_id> <app_secret>
```

## Cache Management

User and group information is cached to avoid frequent API calls.

| Command | Description |
|---------|-------------|
| `python3 scripts/shared/feishu_user_cache.py list` | List cached users |
| `python3 scripts/shared/feishu_user_cache.py clear` | Clear user cache |
| `python3 scripts/shared/feishu_group_cache.py list` | List cached groups |
| `python3 scripts/shared/feishu_group_cache.py clear` | Clear group cache |

**Cache location**: `~/.open-ace/feishu_users.json` and `~/.open-ace/feishu_groups.json`

**Cache TTL**: 1 hour (3600 seconds)

## Troubleshooting

### User names not showing

1. Check if the app has `contact:contact:user:readonly` permission
2. Ensure the app is published
3. Verify App ID and App Secret are correct
4. Check if the user is an external contact (not in organization)

### Group names not showing

1. Check if the app has `chat:chat:readonly` permission
2. Note: OpenClaw uses internal `oc_` prefixed IDs, not Feishu `chat_` IDs
3. For OpenClaw integration, group names may require additional configuration

### API returns 403

- Verify App ID and App Secret
- Ensure the app is published
- Check if permissions are approved

## Disabling Integration

To disable Feishu integration, remove the `feishu` section from your config file.

## References

- [Feishu Open Platform Documentation](https://open.feishu.cn/document)
- [User Info API](https://open.feishu.cn/document/ukTMukTMukTM/uYjNwUjL2YDM14iN2ATN)
- [Chat Info API](https://open.feishu.cn/document/ukTMukTMukTM/uEjNwUjLxYDM14SM2ATN)