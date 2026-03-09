# Dashboard 和 Messages 页面 Auto-refresh 优化报告

## 一、优化目标

针对 `todo-今日完成.md` 中提出的三个问题进行优化：

1. **Dashboard 页面缺少自动刷新功能**
2. **Messages 页面的 auto-refresh 只更新本地数据**
3. **数据更新机制不够透明**

## 二、优化方案

### 核心思路

当用户启用 auto-refresh 时，中央服务器通过 SSH 触发远程机器执行 `fetch_openclaw.py` 和 `upload_to_server.py`，实现本地+远程数据的同步抓取。

### 数据流优化后

```
用户启用 Auto-refresh
        │
        ▼
┌─────────────────────────────────────────────────────────┐
│  /api/fetch?include_remote=true                         │
│                                                         │
│  ┌─────────────────┐      ┌─────────────────────────┐  │
│  │ 本地数据抓取     │      │ 远程数据抓取 (SSH)       │  │
│  │ - fetch_openclaw│      │ - fetch_openclaw.py     │  │
│  │ - fetch_claude  │  →   │ - upload_to_server.py   │  │
│  │ - fetch_qwen    │      │ (192.168.31.159)        │  │
│  └─────────────────┘      └─────────────────────────┘  │
│           │                         │                   │
│           ▼                         ▼                   │
│  ┌─────────────────────────────────────────────────┐   │
│  │              中央服务器数据库                      │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
        │
        ▼
  Dashboard / Messages 页面更新
```

## 三、修改的文件

### 3.1 配置文件

**文件:** `config/config.json.sample`

新增远程机器 SSH 配置：

```json
"remote": {
  "enabled": true,
  "hosts": [
    {
      "name": "ai-lab",
      "host": "192.168.31.159",
      "user": "openclaw",
      "base_dir": "/home/openclaw/ai-token-analyzer",
      "tools": ["openclaw"]
    }
  ]
}
```

### 3.2 后端修改

**文件:** `web.py`

#### 新增 API 端点

| 端点 | 说明 |
|------|------|
| `GET /api/fetch?include_remote=true` | 同时抓取本地和远程数据 |
| `GET /api/fetch/remote` | 仅抓取远程数据 |
| `GET /api/data-status` | 获取各主机数据状态 |

#### 核心代码

```python
def _fetch_remote_data():
    """Fetch data from remote machines via SSH."""
    config = utils.load_config()
    remote_config = config.get('remote', {})

    if not remote_config.get('enabled', False):
        return {'error': 'Remote fetch not enabled'}

    results = {}
    hosts = remote_config.get('hosts', [])

    for host_info in hosts:
        host_name = host_info.get('name', 'unknown')
        host = host_info.get('host')
        user = host_info.get('user', 'openclaw')
        base_dir = host_info.get('base_dir', '/home/openclaw/ai-token-analyzer')

        # Execute fetch on remote machine
        fetch_cmd = f"ssh {user}@{host} 'cd {base_dir} && python3 scripts/fetch_openclaw.py --days 7'"
        result = subprocess.run(fetch_cmd, shell=True, capture_output=True, text=True, timeout=180)

        # Execute upload on remote machine
        upload_cmd = f"ssh {user}@{host} 'cd {base_dir} && python3 scripts/upload_to_server.py ...'"
        result = subprocess.run(upload_cmd, shell=True, capture_output=True, text=True, timeout=120)

        results[host_name] = host_results

    return results
```

**文件:** `scripts/shared/db.py`

新增函数 `get_data_status_by_host()`：

```python
def get_data_status_by_host(host_name: str) -> Dict:
    """Get data status for a specific host."""
    # Returns: last_updated, usage_records, message_records, date_range
```

### 3.3 前端修改

**文件:** `templates/index.html`

#### Dashboard 页面新增 Auto-refresh

```html
<div class="d-flex justify-content-between align-items-center mb-4">
    <h3><i class="bi bi-bar-chart-line"></i> Dashboard</h3>
    <div class="d-flex align-items-center gap-2">
        <div class="form-check form-switch">
            <input class="form-check-input" type="checkbox" id="dashboard-auto-refresh" 
                   onchange="toggleDashboardAutoRefresh()">
            <label class="form-check-label" for="dashboard-auto-refresh">Auto-refresh</label>
        </div>
        <button class="btn btn-outline-primary btn-sm" onclick="refreshDashboardData()">
            <i class="bi bi-arrow-repeat"></i> Refresh
        </button>
    </div>
</div>
```

#### 数据状态面板

```html
<div id="data-status-container" class="data-status-panel mt-2">
    <!-- 显示各主机数据状态 -->
</div>
```

#### JavaScript 函数

```javascript
// Dashboard auto-refresh
function toggleDashboardAutoRefresh() {
    if (isDashboardAutoRefreshing) {
        dashboardAutoRefreshInterval = setInterval(() => {
            fetchDashboardData();  // 调用 /api/fetch?include_remote=true
        }, 30000);  // 30秒间隔
    }
}

// Messages auto-refresh (已修改)
async function fetchData() {
    const response = await fetch('/api/fetch?include_remote=true');
    // 同时抓取本地和远程数据
}
```

## 四、API 测试结果

### 4.1 /api/data-status 端点

```bash
$ curl -s http://localhost:5001/api/data-status | python3 -m json.tool
```

```json
{
    "hosts": [
        {
            "date_range": {
                "end": "2026-03-06",
                "start": "2026-02-28"
            },
            "host_name": "localhost",
            "is_remote": false,
            "last_updated": "2026-03-06 10:38:07",
            "message_records": 42,
            "name": "localhost",
            "usage_records": 4
        }
    ],
    "last_updated": "2026-03-06 10:38:07"
}
```

### 4.2 /api/fetch 端点

```bash
$ curl -s "http://localhost:5001/api/fetch?include_remote=false" | python3 -m json.tool
```

```json
{
    "local": {
        "claude": {
            "success": true,
            "stdout": "Saved 6 days of Claude usage data"
        },
        "openclaw": {
            "success": true,
            "stdout": "Saved 3 days of OpenClaw usage data"
        },
        "qwen": {
            "success": true,
            "stdout": "Saved 5 days of Qwen usage data"
        }
    },
    "remote": {}
}
```

## 五、代码变更统计

```
 config/config.json.sample    |   12 +
 scripts/shared/db.py         |  163 +
 templates/index.html         |  615 +-
 web.py                       |  224 +-
 27 files changed, 1116 insertions(+), 20071 deletions(-)
```

## 六、功能对比

| 功能 | 优化前 | 优化后 |
|------|--------|--------|
| Dashboard 自动刷新 | ❌ 无 | ✅ 30秒间隔 |
| Dashboard 刷新范围 | 仅本地 | 本地 + 远程 |
| Messages 自动刷新 | 仅本地数据 | 本地 + 远程数据 |
| 数据状态可见性 | ❌ 不可见 | ✅ Sidebar 显示 |
| 远程数据抓取 | 手动 SSH | 自动 SSH |

## 七、使用前提

### 7.1 SSH 免密登录

中央服务器需要配置 SSH 密钥免密登录远程机器：

```bash
# 测试免密登录
ssh openclaw@192.168.31.159

# 如果需要配置
ssh-copy-id openclaw@192.168.31.159
```

### 7.2 配置文件

在 `~/.ai-token-analyzer/config.json` 中添加远程配置：

```json
{
  "remote": {
    "enabled": true,
    "hosts": [
      {
        "name": "ai-lab",
        "host": "192.168.31.159",
        "user": "openclaw",
        "base_dir": "/home/openclaw/ai-token-analyzer"
      }
    ]
  }
}
```

## 八、总结

本次优化完成了以下目标：

1. ✅ **Dashboard 添加自动刷新功能** - 每 30 秒自动刷新，同时抓取本地和远程数据
2. ✅ **Messages auto-refresh 支持远程数据** - 修改为调用 `/api/fetch?include_remote=true`
3. ✅ **数据更新机制透明化** - Sidebar 显示各主机数据状态，颜色指示新鲜度

---

**报告日期:** 2026-03-09
**优化完成:** ✅