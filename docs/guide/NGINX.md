# Nginx Reverse Proxy Configuration Guide — Nginx 反向代理配置指南

[English](#english) | [中文](#中文)

---

## English

## Background

In multi-user mode, Open ACE runs a dedicated qwen-code-webui process for each user on a separate port, such as 3100-3200. When deploying with HTTPS and an nginx reverse proxy, the setup must handle three common issues:

1. **Mixed content blocking**: an HTTPS page cannot load an HTTP iframe.
2. **Path rewriting**: frontend assets use absolute paths such as `/assets/index.js`, so they need the `/webui/{port}/` prefix.
3. **API routing**: JavaScript calls both webui APIs and Open ACE APIs, and each group must be proxied to the correct backend.

> **Note**: Issue 2, React Router basename handling, is built into qwen-code-webui v0.2.29+. The webui automatically reads `window.__WEBUI_BASENAME__` as the router basename, so nginx no longer needs to inject basename changes into JavaScript files with `sub_filter`.

## qwen-code-webui version requirements

| Feature | Minimum version | Notes |
|---------|-----------------|-------|
| React Router basename support | v0.2.29 | reads `window.__WEBUI_BASENAME__` as the router basename automatically |
| Language-sync postMessage listener | unreleased | listens for `openace-language-change` messages for live language switching |

> **Language sync** (Issue #1425): when the language is switched in the Open ACE management UI, a postMessage with `openace-language-change` is sent to the iframe. If qwen-code-webui does not implement the listener yet, the language switch only takes effect after opening a new tab or reloading the iframe.

## Overall Architecture

```text
Browser (HTTPS)
    |
    v
nginx (443) --- /webui/{port}/* ---> http://127.0.0.1:{port} (qwen-code-webui)
    |
    +--- /* -----------------------> http://127.0.0.1:19888   (Open ACE main service)
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

    # Internal SSE event ingest (deny externally). The scheduler
    # forwards events to the web process over loopback (#3046); behind this
    # proxy every external request arrives as 127.0.0.1, so the route is
    # guarded only by its shared secret — deny it here as well:
    location /api/autonomous/internal/ {
        return 403;
    }

    # Main Open ACE application.
    location / {
        client_max_body_size 50m;      # Agent session sync payloads can exceed the 1MB default.
        proxy_pass         http://127.0.0.1:19888;
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
- Open ACE APIs, such as `/api/remote/sessions/...` and `/api/workspace/...`, must be proxied to the main backend on port 19888.

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
        proxy_pass http://127.0.0.1:19888;
        # ...
    }
}
```

In this case, Flask does not need `ProxyFix` or URL conversion logic. The iframe can use direct `http://ip:port` addresses.

---

## 中文

## 背景

Open-ACE 在多用户模式下，每个用户拥有独立的 qwen-code-webui 进程，运行在不同端口（如 3100-3200）。当使用 HTTPS + nginx 反向代理部署时，需要解决以下问题：

1. **混合内容阻止**：HTTPS 页面无法加载 HTTP iframe
2. **路径重写**：前端资源引用的是绝对路径（如 `/assets/index.js`），需要添加 `/webui/{port}/` 前缀
3. **API 路径代理**：JS 中同时包含 webui API 和 Open-ACE API，需要分别代理到不同后端

> **注意**：问题 2（React Router basename）已由 qwen-code-webui v0.2.29+ 内置支持解决，webui 会自动读取 `window.__WEBUI_BASENAME__` 作为 Router 的 basename，无需 nginx 侧对 JS 文件做 `sub_filter` 注入。

## qwen-code-webui 版本要求

| 功能 | 最低版本 | 说明 |
|------|----------|------|
| React Router basename 支持 | v0.2.29 | 自动读取 `window.__WEBUI_BASENAME__` 作为 Router basename |
| 语言同步 postMessage 监听 | 待发布 | 监听 `openace-language-change` 消息，实现实时语言切换 |

> **语言同步说明**（Issue #1425）：Open ACE 管理界面切换语言时，会通过 postMessage 发送 `openace-language-change` 消息到 iframe。若 qwen-code-webui 未实现该消息监听，语言切换仅在新建标签页或 iframe 重载后生效。

## 整体架构

```
浏览器 (HTTPS)
    │
    ▼
nginx (443) ─── /webui/{port}/* ──→ http://127.0.0.1:{port} (qwen-code-webui)
    │
    └── /* ──────────────────────→ http://127.0.0.1:19888   (Open-ACE 主服务)
```

用户通过 `https://your-domain/webui/3100/` 访问自己的 webui 实例。

## 完整配置

将 `your-domain.com` 替换为你的实际域名，端口范围根据 `config.json` 中的 `port_range_start` / `port_range_end` 调整。

```nginx
# HTTP -> HTTPS 重定向
server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$host$request_uri;
}

# HTTPS 主配置
server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate     /path/to/ssl/fullchain.pem;
    ssl_certificate_key /path/to/ssl/privkey.pem;

    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 10m;

    # ── JS 文件：直接代理（无需 sub_filter，basename 由 webui 内置支持） ──
    # qwen-code-webui v0.2.29+ 会自动读取 window.__WEBUI_BASENAME__ 作为 Router basename
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

    # ── CSS 文件：修正 Content-Type ──
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

    # ── HTML 页面和 API 请求 ──
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

        # 注入 __WEBUI_BASENAME__ + fetch/EventSource 拦截脚本
        # 拦截脚本将 webui API 调用（/api/chat 等）加上 /webui/{port} 前缀，
        # 而 Open-ACE API（/api/remote/ 等）保持不变，直接走主后端
        sub_filter '<script type="module"' '<script>window.__WEBUI_BASENAME__="/webui/$webui_port";(function(){var p="/webui/$webui_port";var s=["/api/remote/","/api/workspace/","/api/quota/"];var of=window.fetch;window.fetch=function(u,o){if(typeof u==="string"&&u.startsWith("/api/")&&!s.some(function(x){return u.startsWith(x)})){u=p+u}return of.call(this,u,o)};var oe=window.EventSource;window.EventSource=function(u,o){if(typeof u==="string"&&u.startsWith("/api/")&&!s.some(function(x){return u.startsWith(x)})){u=p+u}return new oe(u,o)}})();</script><script type="module"';
        # 重写 HTML 中的资源路径
        sub_filter 'href="/' 'href="/webui/$webui_port/';
        sub_filter 'src="/' 'src="/webui/$webui_port/';
        sub_filter_types text/html;
        sub_filter_once off;
    }

    # ── 内部事件接收端点（拒绝外部访问） ──
    # 调度器进程通过环回地址向 web 进程转发 SSE 事件（#3046）。反代拓扑下
    # 所有外部请求的 remote_addr 都是 127.0.0.1，环回校验失效，该路径仅由
    # 共享密钥保护——建议在反代层直接拒绝：
    location /api/autonomous/internal/ {
        return 403;
    }

    # ── Open-ACE 主应用 ──
    location / {
        client_max_body_size 50m;      # Agent 会话同步数据可能超过默认的 1MB 限制
        proxy_pass         http://127.0.0.1:19888;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection "";
        proxy_cache_bypass $http_upgrade;
        proxy_read_timeout 3600s;      # 远程终端 relay 等长连接 WebSocket 需要 1 小时超时
        proxy_connect_timeout 75s;
        proxy_send_timeout 75s;
    }
}
```

## 配置详解

### 1. 三个 location 块的作用

nginx 按 **最长匹配优先** 处理 location。三个 location 从上到下匹配精度递减：

| location | 匹配内容 | 作用 |
|---|---|---|
| `~ \.js$` | `/webui/{port}/assets/index-xxx.js` | JS 文件直接代理（basename 由 webui 内置支持） |
| `~ \.css$` | `/webui/{port}/assets/index-xxx.css` | CSS 文件 Content-Type 修正 |
| `~ ^/webui/...` | `/webui/{port}/`、`/webui/{port}/api/chat` 等 | HTML 页面 + API 请求 + fetch 拦截器注入 |

### 2. 端口范围正则

```
(310[0-9]|31[1-9][0-9]|3200)
```

匹配 3100-3200 端口范围。如果你的 `port_range_start` / `port_range_end` 配置不同，需要相应调整。

### 3. sub_filter 工作原理

`sub_filter` 是 nginx 的响应内容替换模块，对代理返回的内容做字符串替换：

```
sub_filter '原始字符串' '替换后的字符串';
```

关键注意事项：
- **基于 Content-Type 过滤**：`sub_filter_types` 指定哪些 Content-Type 的响应会被处理
- **不能与 `proxy_hide_header` 配合**：`proxy_hide_header Content-Type` 会移除类型信息，导致 sub_filter 无法判断是否处理
- **不处理压缩内容**：如果上游返回 gzip 压缩，sub_filter 不会工作（确保上游不发压缩或使用 `proxy_set_header Accept-Encoding ""`）

### 4. 核心问题与解决方案

#### 问题 A：混合内容阻止

**现象**：iframe 一直转圈，浏览器控制台报 mixed content 错误。

**原因**：Flask 应用不知道自己在 HTTPS 后面，`request.scheme` 是 `http`，返回的 iframe URL 是 `http://ip:port`。

**解决**：三处配合

1. Flask 应用添加 `ProxyFix` 中间件（`app/__init__.py`）：
```python
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
```

2. nginx 传递正确的代理头：
```nginx
proxy_set_header X-Forwarded-Proto $scheme;
```

3. Flask 在 HTTPS 时返回相对路径 URL（`app/routes/workspace.py`）：
```python
if manager.config.multi_user_mode and flask_request.scheme == "https":
    port_match = re.search(r":(\d+)$", url)
    if port_match:
        url = f"/webui/{port_match.group(1)}/"
```

#### 问题 B：React Router 路由不匹配

**现象**：iframe 加载空白，浏览器控制台报 `No routes matched location "/webui/{port}/..."`。

**原因**：qwen-code-webui 的 `BrowserRouter` 没有配置 `basename`，路由定义在 `/`、`/projects/*` 等根路径上，无法匹配 `/webui/{port}/` 前缀。

**解决**：qwen-code-webui v0.2.29+ 内置支持 `window.__WEBUI_BASENAME__`

webui 在创建 Router 时自动读取 `window.__WEBUI_BASENAME__` 作为 `basename` 属性。nginx 只需在 HTML 中注入该全局变量（见下方），无需对 JS 文件做任何修改。

在 HTML location 块中注入全局变量：
```nginx
sub_filter '<script type="module"'
    '<script>window.__WEBUI_BASENAME__="/webui/$webui_port";</script><script type="module"';
```
效果：在模块脚本加载前设置 `window.__WEBUI_BASENAME__`，webui 的 Router 自动读取并设置 `basename="/webui/{port}"`。

> **注意**：使用 v0.2.29+ 版本的 webui 时，JS location 块中的 `sub_filter` **不再需要**。旧版本需要通过 JS `sub_filter` 匹配 minified 变量名来注入 basename，但每次 webui 重新打包变量名都会变化，维护成本高。

#### 问题 C：API 路径代理

**现象**：前端 API 请求 404 或返回 Not Found。

**原因**：JS 中同时包含两类 API 调用，路径都以 `/api/` 开头：
- webui API：`/api/chat`、`/api/version` 等 → 需要代理到 webui 端口
- Open-ACE API：`/api/remote/sessions/...`、`/api/workspace/...` → 需要代理到主后端（19888 端口）

使用 `sub_filter '`/api/'` 会**无差别重写**所有 API 路径，导致 Open-ACE API 调用被错误代理到 webui 端口而返回 404。

**解决**：在 HTML 中注入 fetch/EventSource 拦截器，按路径前缀区分两类 API：
```nginx
# 注入拦截脚本：仅对 webui API（/api/chat 等）添加 /webui/{port} 前缀
# Open-ACE API（/api/remote/、/api/workspace/、/api/quota/）保持原路径
sub_filter '<script type="module"' '<script>window.__WEBUI_BASENAME__="/webui/$webui_port";(function(){var p="/webui/$webui_port";var s=["/api/remote/","/api/workspace/","/api/quota/"];var of=window.fetch;window.fetch=function(u,o){if(typeof u==="string"&&u.startsWith("/api/")&&!s.some(function(x){return u.startsWith(x)})){u=p+u}return of.call(this,u,o)};var oe=window.EventSource;window.EventSource=function(u,o){if(typeof u==="string"&&u.startsWith("/api/")&&!s.some(function(x){return u.startsWith(x)})){u=p+u}return new oe(u,o)}})();</script><script type="module"';
# 重写 HTML 中的静态资源路径
sub_filter 'href="/' 'href="/webui/$webui_port/';
sub_filter 'src="/' 'src="/webui/$webui_port/';
```

> **注意**：不要在 JS location 块中使用 `sub_filter '`/api/'` 重写 API 路径，因为它无法区分 webui API 和 Open-ACE API。

#### 问题 D：查询参数丢失

**现象**：URL 中的 token 参数没有被传递到后端。

**原因**：当 `proxy_pass` 使用变量时，nginx 不会自动附加查询参数。

**解决**：手动附加 `$is_args$args`：
```nginx
proxy_pass http://127.0.0.1:$webui_port$webui_path$is_args$args;
```
- `$is_args`：如果请求有查询参数则为 `?`，否则为空
- `$args`：查询参数字符串

#### 问题 E：Agent HTTP 413 — 请求体过大

**现象**：远程 agent 状态一直离线，或 agent 日志中持续出现 `/api/remote/agent/message` 的 `HTTP 413` 错误。

**原因**：nginx 的 `client_max_body_size` 默认值为 **1MB**。远程 agent 通过 `POST /api/remote/agent/message` 发送会话同步数据（包含完整的 Claude/Qwen 对话历史和 content blocks），较长的会话（如 2.9MB、1370 条消息）会超过此限制。由于 agent 的所有流量（轮询、心跳、会话同步）都走同一个端点，413 错误会阻塞 agent 的**全部**通信。

**解决**：在 `location /` 块中添加 `client_max_body_size 50m;`：

```nginx
location / {
    client_max_body_size 50m;
    # ... 其他代理设置
}
```

然后重载 nginx：`nginx -s reload`。

> **注意**：这是 nginx 特有的限制。直连 Flask/gevent 没有请求体大小限制。

#### 问题 F：远程终端 5 分钟后断连

**现象**：远程终端 relay 连接（用于私有网络机器）初始正常，但空闲约 5 分钟后断开。浏览器显示 "Connection closed. Reconnecting..."。

**原因**：nginx 的 `proxy_read_timeout` 默认为 **60s**（常见配置设为 300s）。终端空闲时，WebSocket relay 无数据流，nginx 在超时后关闭连接，导致 relay bridge 断裂。

**解决**：两部分 — 提高超时时间和添加 keepalive ping：

1. 在 `location /` 块中提高 `proxy_read_timeout`：
```nginx
location / {
    proxy_read_timeout 3600s;  # 1 小时，适用于远程终端等长连接
    # ... 其他代理设置
}
```

2. Open-ACE v1.0+ 在 WebSocket bridge 中内置了 keepalive ping（30 秒间隔），防止空闲超时。无需应用层改动。

> **注意**：直连环境（无 nginx）不会出现此问题。keepalive ping 对其他中间件（CDN、负载均衡器）的空闲超时同样有效。

## 部署步骤

```bash
# 1. 复制配置文件
cp open-ace.conf /etc/nginx/conf.d/open-ace.conf

# 2. 测试配置语法
nginx -t

# 3. 重载 nginx
nginx -s reload

# 4. 验证
# 检查 HTML 中是否注入了 __WEBUI_BASENAME__ 和 fetch 拦截器
curl -sk "https://your-domain/webui/3100/" | grep __WEBUI_BASENAME__

# 检查 JS 中是否包含 __WEBUI_BASENAME__ 引用（qwen-code-webui v0.2.29+）
curl -sk "https://your-domain/webui/3100/assets/index-xxx.js" | grep '__WEBUI_BASENAME__'

# 确认 JS 中没有被重写 API 路径（不应出现 /webui/3100/api/）
curl -sk "https://your-domain/webui/3100/assets/index-xxx.js" | grep '/webui/3100/api/' | wc -l

# 检查 API 可达性（需要有效 token）
curl -sk "https://your-domain/webui/3100/api/version?token=YOUR_TOKEN"
```

## 常见问题排查

### iframe 空白

1. 打开浏览器开发者工具 → Console
2. 检查是否有 `No routes matched location` 错误 → basename 未注入
3. 检查是否有 `mixed content` 错误 → HTTPS 配置问题
4. 检查 Network 面板，确认 JS/CSS 返回 200

### JS 文件 sub_filter 不生效

使用 qwen-code-webui v0.2.29+ 时，JS location 块中不再需要 `sub_filter`。如果仍需使用旧版 webui，需手动匹配 minified 变量名：

1. 检查上游返回的 Content-Type：
   ```bash
   curl -sI http://127.0.0.1:3100/assets/index-xxx.js | grep Content-Type
   ```
2. 确保 `sub_filter_types` 包含上游实际返回的 Content-Type
3. 不要使用 `proxy_hide_header Content-Type`，这会阻止 sub_filter 工作

### 远程会话 "Not Found" 错误

**现象**：创建远程工作区时返回 "Failed to create remote session: Not Found"。

**原因**：如果 nginx 配置中使用了 `sub_filter '`/api/'` 全局重写 API 路径，Open-ACE API 调用（如 `/api/remote/sessions/123`）也会被加上 `/webui/3100` 前缀，被错误代理到 webui 端口。

**解决**：使用 fetch/EventSource 拦截器代替 sub_filter 全局重写（见"问题 C"）。

### 端口范围不匹配

如果你的 `config.json` 中 `port_range_start` / `port_range_end` 不是 3100-3200，需要更新 nginx 中的正则表达式。

## 仅 HTTP 部署

如果不需要 HTTPS，可以简化配置：

```nginx
server {
    listen 80;
    server_name your-domain.com;

    # WebUI 代理（同上，无需 SSL 配置）
    location ~ ^/webui/(310[0-9]|31[1-9][0-9]|3200)(/.*)?$ {
        # ... 同 HTTPS 配置中的 location 块
    }

    # 主应用
    location / {
        proxy_pass http://127.0.0.1:19888;
        # ...
    }
}
```

此时 Flask 不需要 ProxyFix，也不需要 URL 转换逻辑。iframe 可以直接使用 `http://ip:port` 地址。
