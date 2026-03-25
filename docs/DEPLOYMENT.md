# Deployment Guide

> **ACE** = **AI Computing Explorer**

This guide covers deploying Open ACE in various scenarios.

## Table of Contents

- [Quick Start](#quick-start)
- [Docker Deployment](#docker-deployment)
- [Configuration](#configuration)
- [Deployment Scenarios](#deployment-scenarios)
- [System Services](#system-services)
- [Data Collection](#data-collection)
- [Upgrading](#upgrading)
- [Troubleshooting](#troubleshooting)
- [Security Considerations](#security-considerations)

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

## Docker Deployment

### Prerequisites

- Docker and Docker Compose installed
- Open ACE Docker image (`open-ace:latest`)
- PostgreSQL image (`postgres:15-alpine`)

### Initial Deployment

```bash
# 1. Export Docker images (on development machine)
./scripts/export-image.sh --compress

# 2. Copy to server
scp dist/open-ace-images.tar.gz user@server:~
scp scripts/deploy.sh user@server:~

# 3. Run deployment script
chmod +x deploy.sh
sudo ./deploy.sh

# 4. Follow interactive prompts
```

### Deployment Configuration

The deployment script will prompt for:

| Setting | Description | Default |
|---------|-------------|---------|
| Run User | User to run the application | `open-ace` |
| Deploy Directory | Installation directory | `/home/open-ace/open-ace` |
| Web Port | Web server port | `5001` |
| Host Name | Server hostname | Auto-detected |
| Database User | PostgreSQL username | `open-ace` |
| Database Name | PostgreSQL database name | `ace` |
| OpenClaw | Enable OpenClaw tool | `yes` |
| Claude | Enable Claude tool | `yes` |
| Qwen | Enable Qwen tool | `yes` |
| Workspace | Enable Workspace | `no` |

**Note**: Workspace runs in a separate container. When enabled, Open ACE will connect to the Workspace service at the specified URL. Make sure the Workspace container is running and the port is accessible.

**URL Configuration**: If you enter `localhost` in the Workspace or OpenClaw URL, the deployment script automatically converts it to the server's IP address. This is because:
- The URL is used by the frontend (browser), not the container
- Browsers cannot resolve `localhost` to the server's address
- Example: `http://localhost:3000` → `http://192.168.1.100:3000`

### Default Credentials

After deployment, use these credentials to login:

```
Username: admin
Password: admin123
```

**Important**: Change the default password immediately after first login!

### Directory Structure

```
/home/open-ace/open-ace/
├── config/                  # Configuration files
│   └── config.json          # Main configuration
├── docker-compose.yml       # Docker Compose configuration
└── .env                     # Environment variables (sensitive!)
```

**Note**: Data is stored in the PostgreSQL container's volume (`postgres-data`), not in the host filesystem.

### Management Commands

```bash
cd /home/open-ace/open-ace

# View status
docker compose ps

# View logs
docker compose logs -f

# View open-ace logs only
docker compose logs -f open-ace

# Restart services
docker compose restart

# Restart open-ace only
docker compose restart open-ace

# Stop services
docker compose down

# Start services
docker compose up -d
```

### Updating Open ACE Image

When a new version of Open ACE is released, you only need to update the Docker image:

#### Method 1: Simple Restart (Recommended)

```bash
cd /home/open-ace/open-ace

# 1. Load new image
gunzip -c open-ace-images.tar.gz | docker load

# 2. Restart open-ace container
docker compose up -d open-ace

# 3. Verify startup
docker compose logs -f open-ace
```

#### Method 2: Complete Rebuild

```bash
cd /home/open-ace/open-ace

# 1. Load new image
gunzip -c open-ace-images.tar.gz | docker load

# 2. Stop and remove old container
docker compose stop open-ace
docker compose rm -f open-ace

# 3. Start new container
docker compose up -d open-ace

# 4. Verify startup
docker compose logs -f open-ace
```

#### Method 3: Using Version Tags

```bash
# 1. Load specific version
docker load -i open-ace-v1.2.0.tar

# 2. Update docker-compose.yml
sed -i 's|image: open-ace:latest|image: open-ace:v1.2.0|' docker-compose.yml

# 3. Recreate container
docker compose up -d open-ace
```

**Note**: 
- Data is stored in `./data` directory and PostgreSQL volume - it will NOT be lost
- Configuration in `./config` is preserved
- PostgreSQL container continues running - only open-ace container is updated

### Database Migrations

If the new version includes database schema changes:

```bash
cd /home/open-ace/open-ace

# Run migrations
docker compose run --rm open-ace alembic upgrade head

# Restart application
docker compose restart open-ace
```

### Uninstallation

```bash
cd /home/open-ace/open-ace

# Interactive uninstall (keeps data)
./uninstall.sh

# Complete uninstall (removes everything)
./uninstall.sh --purge
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

# Run database migrations if needed
alembic upgrade head

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