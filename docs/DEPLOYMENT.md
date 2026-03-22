# Deployment Guide

> **ACE** = **AI Computing Explorer**

This guide covers deploying Open ACE in various scenarios.

## Quick Start

### Local Deployment

```bash
# Install dependencies
pip install -r requirements.txt

# Initialize configuration
python3 cli.py config init

# Start web server
python3 web.py

# Visit http://localhost:5001
```

## Configuration

### Configuration File

Configuration is stored in `~/.open-ace/config.json`:

```json
{
  "host_name": "my-machine",
  "tools": {
    "claude": {
      "enabled": true,
      "log_path": "~/.claude/projects"
    },
    "qwen": {
      "enabled": true,
      "log_path": "~/.qwen/projects"
    },
    "openclaw": {
      "enabled": true,
      "log_path": "~/.openclaw/agents"
    }
  },
  "email": {
    "smtp_host": "smtp.example.com",
    "smtp_port": 587,
    "sender": "noreply@example.com"
  }
}
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `OPENCLAW_TOKEN` | OpenClaw API token |
| `SMTP_PASSWORD` | Email SMTP password |

## Deployment Scenarios

### 1. Single Machine (Recommended for Personal Use)

All components run on one machine:

```bash
# Start web server
python3 web.py

# Set up cron for data collection
crontab -e
```

Add to crontab:
```bash
# Collect data daily at 00:30
30 0 * * * cd /path/to/open-ace && python3 scripts/fetch_claude.py && python3 scripts/fetch_qwen.py >> logs/cron.log 2>&1
```

### 2. Central Server + Remote Collectors

For distributed environments:

#### Central Server

```bash
# Deploy
python3 scripts/manage.py local deploy

# Start web service
python3 scripts/manage.py local start
```

#### Remote Machine

```bash
# Deploy to remote
python3 scripts/manage.py remote deploy

# Or manually configure
scp -r open-ace user@remote:/path/to/
ssh user@remote "cd /path/to/open-ace && python3 scripts/fetch_openclaw.py"
```

## System Services

### Linux (systemd)

Create service file `/etc/systemd/system/open-ace.service`:

```ini
[Unit]
Description=Open ACE Web Server
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/open-ace
ExecStart=/usr/bin/python3 web.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable open-ace
sudo systemctl start open-ace
```

### macOS (launchd)

Create `~/Library/LaunchAgents/com.open-ace.web.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.open-ace.web</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/path/to/open-ace/web.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
```

Load the service:

```bash
launchctl load ~/Library/LaunchAgents/com.open-ace.web.plist
```

## Data Collection

### Manual Collection

```bash
# Collect from all tools
python3 scripts/fetch_claude.py
python3 scripts/fetch_qwen.py
python3 scripts/fetch_openclaw.py

# Collect for specific days
python3 scripts/fetch_claude.py --days 7
```

### Scheduled Collection

Using cron:

```bash
# Edit crontab
crontab -e

# Add scheduled tasks
30 0 * * * cd /path/to/open-ace && python3 scripts/fetch_claude.py >> logs/cron.log 2>&1
35 0 * * * cd /path/to/open-ace && python3 scripts/fetch_qwen.py >> logs/cron.log 2>&1
40 0 * * * cd /path/to/open-ace && python3 scripts/fetch_openclaw.py >> logs/cron.log 2>&1
```

## Management Commands

```bash
# Using manage.py
python3 scripts/manage.py local start     # Start local service
python3 scripts/manage.py local stop      # Stop local service
python3 scripts/manage.py local status    # Check status
python3 scripts/manage.py remote deploy   # Deploy to remote
python3 scripts/manage.py remote sync     # Sync files to remote
```

## Upgrading

```bash
# Backup data
cp ~/.open-ace/usage.db ~/.open-ace/usage.db.backup

# Pull latest code
git pull

# Run migrations if needed
python3 scripts/migrations/migrate_concepts.py

# Restart service
python3 scripts/manage.py local stop
python3 scripts/manage.py local start
```

## Troubleshooting

### Port Already in Use

```bash
# Find process using port 5001
lsof -i :5001

# Kill process
kill -9 <PID>
```

### Database Locked

```bash
# Check for running processes
ps aux | grep python

# Stop all services before maintenance
```

### Permission Issues

```bash
# Fix permissions
chmod -R 755 ~/.open-ace/
```

## Security Considerations

1. **Authentication**: Enable user authentication in production
2. **HTTPS**: Use reverse proxy (nginx/Apache) with SSL
3. **Firewall**: Restrict access to port 5001
4. **Secrets**: Use environment variables for sensitive data