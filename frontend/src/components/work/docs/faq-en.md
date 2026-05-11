# FAQ

This document collects common issues and solutions users may encounter when using Open ACE.

---

## Table of Contents

**1. Installation & Deployment**
- Docker startup fails: Database connection timeout
- Port conflict: Port 5000 is in use
- SECRET_KEY not set: Production startup fails
- Config file not found

**2. Login & Authentication**
- Login failed: Invalid username or password
- Session expired: Auto logout
- Account disabled: Cannot login
- Permission denied: Cannot access admin features
- Password change failed: Password too short

**3. Workspace & Project Management**
- Project creation failed: Insufficient path permissions
- Project path does not exist or cannot be accessed
- Project already exists: Duplicate creation
- Multi-user mode Workspace startup fails
- Workspace instance limit reached

**4. Sessions & AI Interaction**
- Quota exceeded: Workspace disabled
- Remote machine offline: Cannot create remote session
- Request timeout or network error
- Session not found

**5. System Settings**
- Language switching
- Theme switching (Dark/Light)
- Settings lost after page refresh

---

## 1. Installation & Deployment

### Docker startup fails: Database connection timeout

**Symptom:** When starting Docker container, logs show: `ERROR: PostgreSQL not ready after 60s. Exiting.`

**Possible causes:**
1. PostgreSQL container has not completed initialization
2. Database connection parameters are incorrectly configured
3. Docker network issues prevent inter-container communication

**Solutions:**
1. Check PostgreSQL container status: `docker compose ps`
2. View PostgreSQL logs: `docker compose logs postgres`
3. If PostgreSQL is initializing, wait for completion and restart: `docker compose restart open-ace-web`
4. Verify database connection parameters

**Prevention:** Ensure depends_on and healthcheck are configured in docker-compose.yml

---

### Port conflict: Port 5000 is in use

**Symptom:** Startup error: `Error: Address already in use (0.0.0.0:5000)`

**Possible causes:**
1. Another service is using port 5000
2. Previous Open ACE process did not fully stop

**Solutions:**
1. Check port usage: `lsof -i :5000` or `netstat -tlnp | grep 5000`
2. Stop the process using the port: `kill -9 <PID>` or `docker compose down`
3. Start with different port: `PORT=8080 docker compose up -d`

---

### SECRET_KEY not set: Production startup fails

**Symptom:** Container startup fails, logs show: `RuntimeError: SECRET_KEY environment variable must be set in production!`

**Possible causes:**
1. SECRET_KEY environment variable not configured in production
2. Using default development key

**Solutions:**
1. Set SECRET_KEY environment variable: `echo "SECRET_KEY=$(openssl rand -hex 32)" > .env`
2. Configure environment variable in docker-compose.yml
3. Restart container

---

### Config file not found

**Symptom:** Workspace feature unavailable after startup, logs show: `Config file not found: ~/.open-ace/config.json`

**Solutions:**
1. Create config directory and file: `mkdir -p ~/.open-ace` and copy example config
2. Edit config file to modify host_name and other parameters
3. Restart service

---

## 2. Login & Authentication

### Login failed: Invalid username or password

**Symptom:** Login page shows "Invalid username or password"

**Solutions:**
1. First login uses default admin account: username `admin`, password `admin123`
2. If default password doesn't work, contact admin to reset password
3. Check if user exists

---

### Session expired: Auto logout

**Symptom:** Page automatically redirects to login after some time

**Possible causes:**
1. Session validity expired (default 24 hours)
2. Browser cookies cleared
3. Service restart caused session invalidation

**Solutions:** Re-login to restore access

---

### Account disabled: Cannot login

**Symptom:** Login fails with "Account is disabled"

**Solutions:** Contact admin to re-enable account:
```bash
docker compose exec postgres psql -U ace -d ace -c "UPDATE users SET is_active=true WHERE username='xxx';"
```

---

### Permission denied: Cannot access admin features

**Symptom:** Admin pages show "Admin access required"

**Solutions:**
1. Check current user role
2. Contact admin to change user role to admin

---

### Password change failed: Password too short

**Symptom:** Password change shows "New password must be at least 8 characters"

**Solutions:**
1. Ensure new password is at least 8 characters
2. Ensure new password differs from current password

---

## 3. Workspace & Project Management

### Project creation failed: Insufficient path permissions

**Symptom:** Creating project shows "Permission denied to create directory"

**Solutions:**
1. Check if user's system_account has permission: `sudo chown -R <user>:<group> /path`
2. Grant permission or use different path
3. Multi-user mode default path: `/workspace/<username>/`

---

### Project path does not exist or cannot be accessed

**Symptom:** Opening project shows "Directory does not exist"

**Solutions:**
1. Confirm path exists and is a directory
2. If path doesn't exist, recreate project

---

### Project already exists: Duplicate creation

**Symptom:** Creating project shows "Project already exists"

**Solutions:** Use different path for new project, or delete existing project and recreate

---

### Multi-user mode Workspace startup fails

**Symptom:** Entering workspace shows "Failed to get user workspace URL"

**Possible causes:**
1. qwen-code-webui not installed or path misconfigured
2. User's system_account system user doesn't exist
3. sudo configuration issues

**Solutions:**
1. Check if qwen-code-webui is available: `which qwen-code-webui`
2. Check if user's system_account exists: `id <account>`
3. Check sudoers configuration
4. View startup logs: `tail -f /tmp/open-ace-*.log`

---

### Workspace instance limit reached

**Symptom:** Creating new session shows "Maximum instances (20) reached"

**Solutions:**
1. Wait for idle instances to auto-clean (default 30 min timeout)
2. Admin can modify config to increase limit (max_instances)

---

## 4. Sessions & AI Interaction

### Quota exceeded: Workspace disabled

**Symptom:** Workspace shows quota exceeded warning, AI features unavailable

**Possible causes:**
1. Daily/monthly token usage exceeded quota limit
2. Daily/monthly request count exceeded quota limit

**Solutions:**
1. View Usage Overview on Dashboard page
2. Wait for quota reset (daily quota resets daily, monthly quota resets monthly)
3. Contact admin to adjust quota

---

### Remote machine offline: Cannot create remote session

**Symptom:** Creating remote session shows "Failed to create remote session"

**Possible causes:**
1. Remote Agent not running or network unreachable
2. Agent registration expired
3. User not assigned to machine

**Solutions:**
1. Check remote machine status
2. Confirm Agent service running: `systemctl status open-ace-agent`
3. Re-register Agent
4. Confirm user is assigned to machine

---

### Request timeout or network error

**Symptom:** API request fails with "Request timed out" or "Network error"

**Possible causes:**
1. Unstable network connection
2. Server slow response or high load
3. Request timeout (default 30 seconds)

**Solutions:**
1. Check network connection status
2. Refresh page to retry (frontend auto-retries 3 times)
3. Check service status

---

### Session not found

**Symptom:** Opening session details shows "Session not found"

**Possible causes:**
1. Session deleted or expired
2. Incorrect session ID
3. User lacks access permission

**Solutions:**
1. Confirm session ID is correct
2. Find valid sessions in session list
3. For remote sessions, confirm remote machine is online

---

## 5. System Settings

### Language switching

**Solutions:** Select language on login page or settings page. Supported languages:
- English
- Chinese (Simplified)
- Japanese
- Korean

---

### Theme switching (Dark/Light)

**Solutions:** Find theme toggle button at top of interface or in settings, select Light / Dark mode

---

### Settings lost after page refresh

**Possible cause:** Browser disabled local storage

**Solutions:** Ensure browser allows localStorage, reconfigure preferences

---

## More Help

If above solutions don't resolve your issue:
1. Check GitHub Issues for related problems: https://github.com/open-ace/open-ace/issues
2. Submit new Issue with problem description, reproduction steps, environment info, and relevant logs
