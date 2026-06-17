# Nginx Reverse Proxy Configuration Guide

## Background

In multi-user mode, Open ACE runs a dedicated qwen-code-webui process for each user on a separate port, such as 3100-3200. When deploying with HTTPS and an nginx reverse proxy, the setup must handle three common issues:

1. **Mixed content blocking**: an HTTPS page cannot load an HTTP iframe.
2. **Path rewriting**: frontend assets use absolute paths such as `/assets/index.js`, so they need the `/webui/{port}/` prefix.
3. **API routing**: JavaScript calls both webui APIs and Open ACE APIs, and each group must be proxied to the correct backend.

> **Note**: Issue 2, React Router basename handling, is built into qwen-code-webui v0.2.29+. The webui automatically reads `window.__WEBUI_BASENAME__` as the router basename, so nginx no longer needs to inject basename changes into JavaScript files with `sub_filter`.

## Overall Architecture

```text
Browser (HTTPS)
    |
    v
nginx (443) --- /webui/{port}/* ---> http://127.0.0.1:{port} (qwen-code-webui)
    |
    +--- /* -----------------------> http://127.0.0.1:5000   (Open ACE Flask)
```

Users access their own webui instance through `https://your-domain/webui/3100/`.

## Complete Configuration

Replace `your-domain.com` with your actual domain. Adjust the port range according to `port_range_start` and `port_range_end` in `config.json`.

```nginx
# Redirect HTTP to HTTPS
server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$host$request_uri;
}

# Main HTTPS server
server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate     /path/to/ssl/fullchain.pem;
    ssl_certificate_key /path/to/ssl/privkey.pem;

    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 10m;

    # JS files: proxy directly. No sub_filter is needed because webui supports basename.
    # qwen-code-webui v0.2.29+ reads window.__WEBUI_BASENAME__ as the router basename.
    location ~ ^/webui/(310[0-9]|31[1-9][0-9]|3200)/(.+\.js)$ {
        set $webui_port $1;
        set $webui_file $2;

        proxy_pass http://127.0.0.1:$webui_port/$webui_file$is_args$args;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # CSS files: fix Content-Type when needed.
    location ~ ^/webui/(310[0-9]|31[1-9][0-9]|3200)/(.+\.css)$ {
        set $webui_port $1;
        set $webui_file $2;

        add_header Content-Type "text/css; charset=utf-8" always;

        proxy_pass http://127.0.0.1:$webui_port/$webui_file$is_args$args;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # HTML pages and API requests.
    location ~ ^/webui/(310[0-9]|31[1-9][0-9]|3200)(/.*)?$ {
        set $webui_port $1;
        set $webui_path $2;

        if ($webui_path = "") {
            set $webui_path "/";
        }

        proxy_pass http://127.0.0.1:$webui_port$webui_path$is_args$args;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "";
        proxy_cache_bypass $http_upgrade;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
        proxy_send_timeout 75s;

        # Inject __WEBUI_BASENAME__ and fetch/EventSource interceptors.
        # The interceptor prefixes webui API calls, such as /api/chat, with /webui/{port}.
        # Open ACE APIs, such as /api/remote/, stay unchanged and go to the main backend.
        sub_filter '<script type="module"' '<script>window.__WEBUI_BASENAME__="/webui/$webui_port";(function(){var p="/webui/$webui_port";var s=["/api/remote/","/api/workspace/","/api/quota/"];var of=window.fetch;window.fetch=function(u,o){if(typeof u==="string"&&u.startsWith("/api/")&&!s.some(function(x){return u.startsWith(x)})){u=p+u}return of.call(this,u,o)};var oe=window.EventSource;window.EventSource=function(u,o){if(typeof u==="string"&&u.startsWith("/api/")&&!s.some(function(x){return u.startsWith(x)})){u=p+u}return new oe(u,o)}})();</script><script type="module"';
        # Rewrite static asset paths in HTML.
        sub_filter 'href="/' 'href="/webui/$webui_port/';
        sub_filter 'src="/' 'src="/webui/$webui_port/';
        sub_filter_types text/html;
        sub_filter_once off;
    }

    # Main Open ACE application.
    location / {
        client_max_body_size 50m;      # Agent session sync payloads can exceed the 1MB default.
        proxy_pass         http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection "";
        proxy_cache_bypass $http_upgrade;
        proxy_read_timeout 3600s;      # Long-lived WebSocket relay connections, such as terminal and VS Code.
        proxy_connect_timeout 75s;
        proxy_send_timeout 75s;
    }
}
```

## Configuration Details

### 1. Purpose Of The Three Location Blocks

nginx processes regex locations according to its location matching rules. These three blocks become progressively broader:

| location | Matches | Purpose |
|---|---|---|
| `~ \.js$` | `/webui/{port}/assets/index-xxx.js` | Proxies JavaScript files directly; basename is handled by webui |
| `~ \.css$` | `/webui/{port}/assets/index-xxx.css` | Fixes CSS Content-Type |
| `~ ^/webui/...` | `/webui/{port}/`, `/webui/{port}/api/chat`, and similar paths | Handles HTML pages, API requests, and fetch interceptor injection |

### 2. Port Range Regex

```text
(310[0-9]|31[1-9][0-9]|3200)
```

This matches ports 3100-3200. If your `port_range_start` or `port_range_end` differs, update the regex accordingly.

### 3. How sub_filter Works

`sub_filter` is nginx's response body replacement module. It performs string replacements on upstream responses:

```nginx
sub_filter 'original string' 'replacement string';
```

Important details:

- **Content-Type based filtering**: `sub_filter_types` controls which response Content-Types are processed.
- **Do not combine with `proxy_hide_header Content-Type`**: hiding Content-Type prevents `sub_filter` from deciding whether to process the response.
- **Compressed content is not processed**: if upstream returns gzip-compressed content, `sub_filter` will not work. Make sure upstream does not compress responses, or use `proxy_set_header Accept-Encoding ""`.

### 4. Core Problems And Fixes

#### Problem A: Mixed Content Blocking

**Symptom**: The iframe keeps spinning, and the browser console reports a mixed content error.

**Cause**: The Flask app does not know it is behind HTTPS. `request.scheme` is `http`, so the iframe URL returned by the app is `http://ip:port`.

**Fix**: Use these three pieces together:

1. Add the `ProxyFix` middleware to the Flask app in `app/__init__.py`:

```python
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
```

2. Pass the correct proxy header in nginx:

```nginx
proxy_set_header X-Forwarded-Proto $scheme;
```

3. Return relative URLs when Flask receives an HTTPS request in `app/routes/workspace.py`:

```python
if manager.config.multi_user_mode and flask_request.scheme == "https":
    port_match = re.search(r":(\d+)$", url)
    if port_match:
        url = f"/webui/{port_match.group(1)}/"
```

#### Problem B: React Router Route Mismatch

**Symptom**: The iframe loads a blank page, and the browser console reports `No routes matched location "/webui/{port}/..."`.

**Cause**: qwen-code-webui's `BrowserRouter` does not have a `basename`. Its routes are defined at root paths such as `/` and `/projects/*`, so they cannot match the `/webui/{port}/` prefix.

**Fix**: qwen-code-webui v0.2.29+ supports `window.__WEBUI_BASENAME__`.

The webui reads `window.__WEBUI_BASENAME__` as the `basename` property when creating the router. nginx only needs to inject the global variable into the HTML page; it does not need to modify JavaScript files.

Inject the global variable in the HTML location block:

```nginx
sub_filter '<script type="module"'
    '<script>window.__WEBUI_BASENAME__="/webui/$webui_port";</script><script type="module"';
```

This sets `window.__WEBUI_BASENAME__` before module scripts load, allowing the webui router to use `basename="/webui/{port}"`.

> **Note**: With qwen-code-webui v0.2.29+, the JavaScript location block no longer needs `sub_filter`. Older webui versions required matching minified JavaScript variable names, which changed on every rebuild and was difficult to maintain.

#### Problem C: API Path Routing

**Symptom**: Frontend API requests return 404 or `Not Found`.

**Cause**: JavaScript includes two categories of API calls, and both start with `/api/`:

- webui APIs, such as `/api/chat` and `/api/version`, must be proxied to the webui port.
- Open ACE APIs, such as `/api/remote/sessions/...` and `/api/workspace/...`, must be proxied to the main backend on port 5000.

Using `sub_filter '`/api/'` rewrites every API path indiscriminately, which sends Open ACE API calls to the webui port and causes 404 responses.

**Fix**: Inject a fetch/EventSource interceptor into HTML and route calls by path prefix:

```nginx
# Prefix only webui API calls, such as /api/chat, with /webui/{port}.
# Keep Open ACE APIs, such as /api/remote/, /api/workspace/, and /api/quota/, unchanged.
sub_filter '<script type="module"' '<script>window.__WEBUI_BASENAME__="/webui/$webui_port";(function(){var p="/webui/$webui_port";var s=["/api/remote/","/api/workspace/","/api/quota/"];var of=window.fetch;window.fetch=function(u,o){if(typeof u==="string"&&u.startsWith("/api/")&&!s.some(function(x){return u.startsWith(x)})){u=p+u}return of.call(this,u,o)};var oe=window.EventSource;window.EventSource=function(u,o){if(typeof u==="string"&&u.startsWith("/api/")&&!s.some(function(x){return u.startsWith(x)})){u=p+u}return new oe(u,o)}})();</script><script type="module"';
# Rewrite static asset paths in HTML.
sub_filter 'href="/' 'href="/webui/$webui_port/';
sub_filter 'src="/' 'src="/webui/$webui_port/';
```

> **Note**: Do not rewrite API paths with `sub_filter '`/api/'` in the JavaScript location block. It cannot distinguish webui APIs from Open ACE APIs.

#### Problem D: Query Parameters Are Lost

**Symptom**: Token parameters in the URL are not passed to the backend.

**Cause**: When `proxy_pass` uses variables, nginx does not automatically append query parameters.

**Fix**: Append `$is_args$args` manually:

```nginx
proxy_pass http://127.0.0.1:$webui_port$webui_path$is_args$args;
```

- `$is_args`: `?` when the request has query parameters; empty otherwise.
- `$args`: the query parameter string.

#### Problem E: Agent HTTP 413 - Request Entity Too Large

**Symptom**: Remote agent status stays offline, or agent logs show repeated `HTTP 413` errors from `/api/remote/agent/message`.

**Cause**: nginx's `client_max_body_size` defaults to **1MB**. The remote agent sends session sync data, including full Claude/Qwen conversation history and content blocks, through `POST /api/remote/agent/message`. Long sessions, such as 2.9MB with 1370 messages, exceed this limit. Because all agent traffic, including poll, heartbeat, and session sync, goes through the same endpoint, 413 errors block **all** agent communication, not just session sync.

**Fix**: Add `client_max_body_size 50m;` to the `location /` block:

```nginx
location / {
    client_max_body_size 50m;
    # ... other proxy settings
}
```

Then reload nginx:

```bash
nginx -s reload
```

> **Note**: This is nginx-specific. Direct Flask/gevent connections have no body size limit.

#### Problem F: Remote Terminal Disconnects After 5 Minutes

**Symptom**: Remote terminal relay connections for machines on private networks work initially but disconnect after exactly five minutes of idle time. The browser shows "Connection closed. Reconnecting...".

**Cause**: nginx's `proxy_read_timeout` defaults to **60s** and is commonly set to 300s in sample configs. When the terminal is idle, no data flows through the WebSocket relay, so nginx closes the connection after the timeout.

**Fix**: Raise the timeout and rely on keepalive pings:

1. Raise `proxy_read_timeout` in the `location /` block:

```nginx
location / {
    proxy_read_timeout 3600s;  # 1 hour for long-lived WebSocket relay
    # ... other proxy settings
}
```

2. Open ACE v1.0+ includes built-in keepalive pings at a 30-second interval in the WebSocket bridge to prevent idle timeouts. No application-level changes are needed.

> **Note**: Direct connections without nginx do not have this issue. The keepalive ping also helps with other middleboxes, such as CDNs and load balancers, that enforce similar idle timeouts.

## Deployment Steps

```bash
# 1. Copy the configuration file.
cp open-ace.conf /etc/nginx/conf.d/open-ace.conf

# 2. Test the configuration syntax.
nginx -t

# 3. Reload nginx.
nginx -s reload

# 4. Verify.
# Check that __WEBUI_BASENAME__ and the fetch interceptor are injected into HTML.
curl -sk "https://your-domain/webui/3100/" | grep __WEBUI_BASENAME__

# Check that JavaScript references __WEBUI_BASENAME__ in qwen-code-webui v0.2.29+.
curl -sk "https://your-domain/webui/3100/assets/index-xxx.js" | grep '__WEBUI_BASENAME__'

# Confirm that API paths were not rewritten inside JavaScript.
# This should not print /webui/3100/api/.
curl -sk "https://your-domain/webui/3100/assets/index-xxx.js" | grep '/webui/3100/api/' | wc -l

# Check API reachability. Requires a valid token.
curl -sk "https://your-domain/webui/3100/api/version?token=YOUR_TOKEN"
```

## Troubleshooting

### Blank iframe

1. Open the browser developer tools and go to Console.
2. Check for `No routes matched location`; if present, basename was not injected.
3. Check for `mixed content`; if present, HTTPS proxy configuration is incomplete.
4. Check the Network panel and confirm that JavaScript and CSS requests return 200.

### JavaScript sub_filter does not take effect

With qwen-code-webui v0.2.29+, the JavaScript location block no longer needs `sub_filter`. If you still need to support an older webui version, you must match the minified variable names manually:

1. Check the upstream Content-Type:

   ```bash
   curl -sI http://127.0.0.1:3100/assets/index-xxx.js | grep Content-Type
   ```

2. Make sure `sub_filter_types` includes the upstream Content-Type.
3. Do not use `proxy_hide_header Content-Type`; it prevents `sub_filter` from working.

### Remote session returns "Not Found"

**Symptom**: Creating a remote workspace returns "Failed to create remote session: Not Found".

**Cause**: If nginx uses a global `sub_filter '`/api/'` rewrite, Open ACE API calls such as `/api/remote/sessions/123` also receive the `/webui/3100` prefix and are incorrectly proxied to the webui port.

**Fix**: Use the fetch/EventSource interceptor instead of a global API `sub_filter` rewrite. See Problem C.

### Port range mismatch

If `port_range_start` and `port_range_end` in `config.json` are not 3100-3200, update the nginx regex accordingly.

## HTTP-Only Deployment

If HTTPS is not required, the configuration can be simplified:

```nginx
server {
    listen 80;
    server_name your-domain.com;

    # WebUI proxy. Same as the HTTPS location block, without SSL settings.
    location ~ ^/webui/(310[0-9]|31[1-9][0-9]|3200)(/.*)?$ {
        # ... same as the HTTPS location block
    }

    # Main application.
    location / {
        proxy_pass http://127.0.0.1:5000;
        # ...
    }
}
```

In this case, Flask does not need `ProxyFix` or URL conversion logic. The iframe can use direct `http://ip:port` addresses.
