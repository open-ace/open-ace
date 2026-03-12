## 修复完成 ✅

问题已完全修复。Usage Heatmap 现在根据时间范围智能选择显示模式：

### 修复内容

#### 1. 新增数据库函数和 API
- 新增 `get_daily_hourly_usage()` 函数，返回按**日期**和小时聚合的数据
- 新增 `/api/analysis/daily-hourly-usage` API 端点

#### 2. 智能显示模式
根据时间范围自动切换显示模式：

| 时间范围 | 显示模式 | Y 轴标签 |
|---------|---------|---------|
| ≤ 14 天 | 按日期显示 | 具体日期（如 3/5, 3/6...） |
| > 14 天 | 按星期聚合 | 星期几（Mon-Sun） |

#### 3. 标题动态更新
- ≤ 14 天：`Usage Heatmap (N Days)` / `用量热力图（N 天）`
- > 14 天：`Usage Heatmap (N Days - By Weekday)` / `用量热力图（N 天 - 按星期聚合）`

### 修改的文件

| 文件 | 修改内容 |
|------|----------|
| `scripts/shared/db.py` | 新增 `get_daily_hourly_usage()` 函数 |
| `web.py` | 新增 `/api/analysis/daily-hourly-usage` API 端点 |
| `templates/index.html` | 修改 `loadHourlyUsage()` 和 `renderHeatmapChart()` 支持两种显示模式 |

### 修复后效果

**8 天时间范围（按日期显示）：**
- Y 轴显示具体日期：3/5, 3/6, 3/7, 3/8, 3/9, 3/10
- 标题：`Usage Heatmap (8 Days)`

![修复后效果 - 8天](screenshots/screenshot_20260311_202928_02_heatmap.png)

**30 天时间范围（按星期聚合）：**
- Y 轴显示星期几：Mon, Tue, Wed, Thu, Fri, Sat, Sun
- 标题：`Usage Heatmap (30 Days - By Weekday)`

### 技术说明

- 当时间范围 ≤14 天时，系统调用新的 `daily-hourly-usage` API，返回每天的逐小时数据
- 当时间范围 >14 天时，系统调用原有的 `hourly-usage` API，返回按 weekday 聚合的数据
- 这种设计保证了可视化效果：日期太多时按 weekday 聚合更有意义，短时间范围内看每天详情更有价值

---

**Fixed by commit:** `4c22f24`
