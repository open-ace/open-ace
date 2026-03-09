---
name: playwright
description: 使用 Playwright 对当前问题或结果进行截图验证，生成截图报告并打开查看
---

# Playwright 截图 Skill

此 skill 用于对当前处理的问题或结果进行截图验证，生成可视化报告。

## 工作流程

### 1. 确定截图目标

根据当前对话上下文，确定需要截图的内容：
- 如果问题已解决：截图展示修复后的效果
- 如果问题未解决：截图展示当前问题状态
- 根据问题类型确定需要截图的页面/组件

### 2. 执行截图脚本

运行截图脚本：
```bash
python3 /path/to/skill/scripts/screenshot.py --url <URL> --output <OUTPUT_DIR> --targets <TARGETS>
```

参数说明：
- `--url`: 要截图的页面 URL（默认：http://localhost:5001/）
- `--output`: 截图输出目录（默认：项目 screenshots/ 目录）
- `--targets`: 截图目标，逗号分隔（如：full,datepicker,heatmap,metrics）

### 3. 生成报告

- **单张截图**：直接打开截图文件
- **多张截图**：生成 HTML 报告并打开

HTML 报告格式：
```html
<!DOCTYPE html>
<html>
<head>
    <title>截图报告 - <问题标题></title>
    <style>
        body { font-family: system-ui; max-width: 1200px; margin: 0 auto; padding: 20px; }
        .screenshot { margin: 20px 0; border: 1px solid #ddd; border-radius: 8px; overflow: hidden; }
        .screenshot h3 { margin: 0; padding: 10px; background: #f5f5f5; }
        .screenshot img { max-width: 100%; display: block; }
    </style>
</head>
<body>
    <h1>截图报告</h1>
    <p>生成时间：<timestamp></p>
    <p>问题：<issue_title></p>
    
    <div class="screenshot">
        <h3>截图 1 标题</h3>
        <img src="screenshot1.png">
    </div>
    ...
</body>
</html>
```

### 4. 打开结果

使用系统默认程序打开：
- macOS: `open <file>`
- Linux: `xdg-open <file>`
- Windows: `start <file>`

## 截图目标类型

| 目标 | 说明 |
|------|------|
| `full` | 完整页面截图 |
| `dashboard` | 仪表盘区域 |
| `analysis` | 分析页面 |
| `datepicker` | 日期选择器 |
| `heatmap` | 热力图 |
| `metrics` | 关键指标卡片 |
| `tokens` | Token 统计卡片 |
| `custom:<selector>` | 自定义 CSS 选择器 |

## 示例用法

### 场景 1：验证 Analysis 页面修复
```
用户：验证 Analysis 页面的修复
调用：screenshot.py --targets full,heatmap,datepicker,metrics
输出：生成 4 张截图 → 创建 HTML 报告 → 打开报告
```

### 场景 2：验证单个组件
```
用户：检查热力图是否正常显示
调用：screenshot.py --targets heatmap
输出：生成 1 张截图 → 直接打开截图
```

## 注意事项

1. 确保目标服务已启动（如 localhost:5001）
2. 截图需要认证时，脚本会自动使用已配置的 session token
3. 截图默认保存在项目的 `screenshots/` 目录
4. HTML 报告文件名为 `screenshot_report_<timestamp>.html`

## 配置

在 `scripts/config.py` 中可配置：
- `SESSION_TOKEN`: 认证 token
- `DEFAULT_URL`: 默认截图 URL
- `VIEWPORT_SIZE`: 浏览器视口大小
- `TIMEOUT`: 页面加载超时时间