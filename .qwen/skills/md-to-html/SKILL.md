---
name: md-to-html
description: 将 Markdown 文档转换为美观的 HTML 格式并在浏览器中打开
---

# Markdown 转 HTML Skill

此 skill 用于将 Markdown 文档转换为带有专业样式的 HTML 格式，并在浏览器中打开查看。

## 使用场景

- 需要将项目文档以更美观的方式展示
- 需要生成可分享的 HTML 格式文档
- 需要参考现有 HTML 模板格式转换文档

## 工作流程

### 1. 确认源文件和目标文件

- **源文件**: 用户指定的 Markdown 文件路径
- **目标文件**: 默认为源文件同名但扩展名为 `.html`，或用户指定路径
- **参考模板**: 可选，用于参考样式的现有 HTML 文件

### 2. 读取参考模板（如有）

如果用户指定了参考模板，读取该模板提取：
- CSS 样式（`<style>` 标签内容）
- 页面结构布局
- 颜色变量和主题配置

### 3. 转换 Markdown 为 HTML

将 Markdown 内容转换为 HTML，包括：

| Markdown 元素 | HTML 转换 |
|--------------|----------|
| 标题 `#` | `<h1>` - `<h6>` |
| 粗体 `**text**` | `<strong>text</strong>` |
| 斜体 `*text*` | `<em>text</em>` |
| 代码 `` `code` `` | `<code>code</code>` |
| 代码块 ` ``` ` | `<pre><code>...</code></pre>` |
| 引用 `>` | `<blockquote>...</blockquote>` |
| 列表 `-` / `1.` | `<ul>` / `<ol>` |
| 表格 | `<table>` |
| 链接 `[text](url)` | `<a href="url">text</a>` |
| 分隔线 `---` | `<hr>` |

### 4. 应用默认样式

如果没有参考模板，使用以下默认样式：

```css
:root {
    --primary-color: #667eea;
    --secondary-color: #764ba2;
    --bg-color: #f8f9fa;
    --card-bg: #ffffff;
    --text-color: #333;
    --text-muted: #6c757d;
    --border-color: #dee2e6;
    --code-bg: #f4f4f4;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    line-height: 1.8;
    color: var(--text-color);
    background: linear-gradient(135deg, var(--primary-color) 0%, var(--secondary-color) 100%);
    min-height: 100vh;
    margin: 0;
    padding: 20px;
}

.container {
    max-width: 1200px;
    margin: 0 auto;
    background: var(--card-bg);
    border-radius: 16px;
    box-shadow: 0 20px 60px rgba(0,0,0,0.3);
    overflow: hidden;
}

.header {
    background: linear-gradient(135deg, var(--primary-color) 0%, var(--secondary-color) 100%);
    color: white;
    padding: 40px;
    text-align: center;
}

.content {
    padding: 40px;
}

h1 {
    color: var(--primary-color);
    border-bottom: 3px solid var(--primary-color);
    padding-bottom: 10px;
    margin-top: 40px;
}

h2 {
    color: var(--secondary-color);
    border-left: 4px solid var(--secondary-color);
    padding-left: 15px;
    margin-top: 35px;
}

table {
    width: 100%;
    border-collapse: collapse;
    margin: 20px 0;
    font-size: 14px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    border-radius: 8px;
    overflow: hidden;
}

th {
    background: linear-gradient(135deg, var(--primary-color) 0%, var(--secondary-color) 100%);
    color: white;
    padding: 15px;
    text-align: left;
}

td {
    padding: 12px 15px;
    border-bottom: 1px solid var(--border-color);
}

tr:nth-child(even) {
    background-color: #f8f9fa;
}

tr:hover {
    background-color: #e9ecef;
}

pre {
    background: #2d2d2d;
    color: #f8f8f2;
    padding: 20px;
    border-radius: 8px;
    overflow-x: auto;
    margin: 20px 0;
}

code {
    background: var(--code-bg);
    padding: 2px 6px;
    border-radius: 4px;
    font-family: 'SF Mono', Monaco, Consolas, monospace;
}

pre code {
    background: none;
    padding: 0;
    color: inherit;
}

blockquote {
    border-left: 4px solid var(--primary-color);
    margin: 20px 0;
    padding: 15px 20px;
    background: #f0f4ff;
    border-radius: 0 8px 8px 0;
}

hr {
    border: none;
    height: 2px;
    background: linear-gradient(90deg, var(--primary-color), var(--secondary-color));
    margin: 40px 0;
}

/* 状态徽章 */
.badge {
    display: inline-block;
    padding: 4px 8px;
    border-radius: 4px;
    font-size: 12px;
    font-weight: 600;
}

.badge-success { background-color: #d4edda; color: #155724; }
.badge-warning { background-color: #fff3cd; color: #856404; }
.badge-danger { background-color: #f8d7da; color: #721c24; }
.badge-info { background-color: #d1ecf1; color: #0c5460; }
.badge-new { background-color: #cce5ff; color: #004085; }

/* 布局图 */
.layout-diagram {
    background: #f8f9fa;
    border: 2px solid var(--border-color);
    border-radius: 8px;
    padding: 20px;
    margin: 20px 0;
    font-family: 'SF Mono', Monaco, Consolas, monospace;
    font-size: 13px;
    white-space: pre;
    overflow-x: auto;
}

/* 区块卡片 */
.section-card {
    background: #f8f9fa;
    border-radius: 8px;
    padding: 20px;
    margin: 20px 0;
    border-left: 4px solid var(--primary-color);
}

/* 目录 */
.toc {
    background: #f8f9fa;
    padding: 20px 30px;
    border-radius: 8px;
    margin-bottom: 30px;
}

.toc ul {
    list-style: none;
    padding-left: 0;
}

/* 页脚 */
.footer {
    text-align: center;
    padding: 20px;
    color: var(--text-muted);
    font-size: 14px;
    border-top: 1px solid var(--border-color);
}
```

### 5. 生成完整 HTML 文件

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{文档标题}</title>
    <style>
        {CSS 样式}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{主标题}</h1>
            <p>{副标题}</p>
        </div>
        <div class="content">
            {转换后的 HTML 内容}
        </div>
        <div class="footer">
            <p>文档维护: {团队名称}<br>最后更新: {日期}</p>
        </div>
    </div>
</body>
</html>
```

### 6. 写入文件并打开

1. 将 HTML 内容写入目标文件
2. 使用系统命令打开文件：
   - macOS: `open <file>`
   - Linux: `xdg-open <file>`
   - Windows: `start <file>`

## 使用示例

### 示例 1：基本转换
```
用户：将 docs/README.md 转成 HTML 并打开
操作：
1. 读取 docs/README.md
2. 转换为 HTML
3. 写入 docs/README.html
4. 打开 docs/README.html
```

### 示例 2：参考模板转换
```
用户：参考 docs/PROJECT_ANALYSIS.html 格式将 docs/UI_UX_OPTIMIZATION_PROPOSAL.md 转成 HTML
操作：
1. 读取 docs/PROJECT_ANALYSIS.html 提取样式
2. 读取 docs/UI_UX_OPTIMIZATION_PROPOSAL.md
3. 使用提取的样式转换内容
4. 写入 docs/UI_UX_OPTIMIZATION_PROPOSAL.html
5. 打开文件
```

### 示例 3：指定输出路径
```
用户：将 docs/report.md 转成 HTML 保存到 /tmp/report.html 并打开
操作：
1. 读取 docs/report.md
2. 转换为 HTML
3. 写入 /tmp/report.html
4. 打开 /tmp/report.html
```

## 特殊处理

### 代码块语法高亮

为代码块添加语言标识的 CSS 类：
```html
<pre><code class="language-python">...</code></pre>
<pre><code class="language-typescript">...</code></pre>
```

### 表格增强

为表格添加响应式包装：
```html
<div class="table-responsive">
    <table>...</table>
</div>
```

### 锚点链接

自动为标题生成 ID 以支持页内跳转：
```html
<h1 id="section-1">一、方案概述</h1>
<h2 id="section-1-1">1.1 设计理念</h2>
```

### 徽章转换

识别特定格式的文本转换为徽章：
- `✅ 已实现` → `<span class="badge badge-success">✅ 已实现</span>`
- `⚠️ 待处理` → `<span class="badge badge-warning">⚠️ 待处理</span>`
- `❌ 未实现` → `<span class="badge badge-danger">❌ 未实现</span>`
- `🆕 新增` → `<span class="badge badge-new">🆕 新增</span>`

## 注意事项

1. 确保 Markdown 文件存在且可读
2. 目标目录需要有写入权限
3. 如果目标文件已存在，会直接覆盖
4. 打开文件需要图形界面环境