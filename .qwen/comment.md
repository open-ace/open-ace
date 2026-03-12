## 修复完成 ✅

问题已修复，Usage Heatmap 标题现在会根据用户选择的时间范围动态更新。

### 修复内容

在 `loadAnalysisData` 函数中添加了动态标题更新逻辑：
- 根据选择的开始和结束日期计算天数差
- 支持中英文双语标题动态更新
- 标题格式：`Usage Heatmap ({days} Days)` 或 `用量热力图（{days} 天）`

### 修改的文件

| 文件 | 修改内容 |
|------|----------|
| `templates/index.html` | 在 `loadAnalysisData` 函数中添加标题更新逻辑，根据时间范围动态更新 Usage Heatmap 标题 |

### 修复后效果

选择不同时间范围时，Usage Heatmap 标题会正确显示实际的天数，例如：
- 选择最近 7 天 → `Usage Heatmap (7 Days)`
- 选择最近 30 天 → `Usage Heatmap (30 Days)`
- 选择自定义范围 → 根据实际天数计算

### 截图验证

修复后的截图（选择 8 天时间范围）：

![修复后效果](/screenshots/screenshot_20260311_111458_02_heatmap.png)

---

**Fixed by commit:** `1a3f2ec`
