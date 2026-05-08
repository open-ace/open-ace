# Issue #190 Fix Guide: Token累计效应导致成本虚高、ROI显示负值

## 根本原因

通过查询 PostgreSQL 数据库和分析 JSONL 原始数据，确认问题存在。

**关键发现**: Qwen 和 Claude 的 API 结构完全不同：

| API | input_tokens 含义 | 问题 |
|-----|------------------|------|
| **Qwen** `promptTokenCount` | 包含历史 | 直接累加导致膨胀 |
| **Claude** `input_tokens` | 仅新增 tokens | tokens_used 包含了 cache_read |

---

## Fix 1 (P0): Qwen Token累计效应

### 文件: `scripts/fetch_qwen.py`

#### 修改 extract_tokens_from_entry() (第183-207行)

```python
def extract_tokens_from_entry(entry: dict) -> dict:
    """Extract token counts from a Qwen log entry."""
    result = {
        "prompt_tokens": 0,
        "candidates_tokens": 0,
        "thoughts_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
        "actual_input_tokens": 0,  # NEW: 新增的input tokens(扣除缓存)
        "model": None,
        "is_assistant_message": False,
    }

    if entry.get("type") == "assistant":
        result["model"] = entry.get("model")
        result["is_assistant_message"] = True

    usage = entry.get("usageMetadata", {})
    if isinstance(usage, dict):
        result["prompt_tokens"] = usage.get("promptTokenCount", 0)
        result["candidates_tokens"] = usage.get("candidatesTokenCount", 0)
        result["thoughts_tokens"] = usage.get("thoughtsTokenCount", 0)
        result["cached_tokens"] = usage.get("cachedContentTokenCount", 0)
        result["total_tokens"] = usage.get("totalTokenCount", 0)

        # FIX: 计算实际新增的input tokens
        # promptTokenCount 包含历史，cachedContentTokenCount 是缓存的历史tokens
        # actual_input = prompt - cached = 本次新增的tokens
        if result["cached_tokens"] > 0:
            result["actual_input_tokens"] = max(0, result["prompt_tokens"] - result["cached_tokens"])
        else:
            # 无缓存信息时，使用 total - output 估算
            result["actual_input_tokens"] = max(0, result["total_tokens"] - result["candidates_tokens"] - result["thoughts_tokens"])

    return result
```

#### 修改 process_jsonl_file() 累加逻辑 (第471-487行)

```python
                if tokens["total_tokens"] == 0:
                    if tokens["is_assistant_message"]:
                        daily[date_key]["request_count"] += 1
                    continue

                # FIX: 使用 actual_input_tokens 替代 prompt_tokens
                daily[date_key]["prompt_tokens"] += tokens["actual_input_tokens"]
                daily[date_key]["candidates_tokens"] += tokens["candidates_tokens"]
                daily[date_key]["thoughts_tokens"] += tokens["thoughts_tokens"]
                daily[date_key]["cached_tokens"] += tokens["cached_tokens"]
                daily[date_key]["total_tokens"] += tokens["actual_input_tokens"] + tokens["candidates_tokens"] + tokens["thoughts_tokens"]

                if tokens["is_assistant_message"]:
                    daily[date_key]["request_count"] += 1
```

#### 验证方法

```bash
# 验证修复后的数据
cat ~/.qwen/projects/*/chats/*.jsonl | python3 -c "
import json, sys
for line in sys.stdin:
    entry = json.loads(line.strip())
    if entry.get('type') == 'assistant' and 'usageMetadata' in entry:
        usage = entry['usageMetadata']
        prompt = usage.get('promptTokenCount', 0)
        cached = usage.get('cachedContentTokenCount', 0)
        actual = prompt - cached
        print(f'prompt={prompt}, cached={cached}, actual_input={actual}')
"
```

---

## Fix 2 (P0): Claude tokens_used 计算错误

### 文件: `scripts/fetch_claude.py`

#### 修改 save_usage 调用 (第725-730行)

```python
    saved = 0
    for date, stats in aggregated.items():
        if start_date <= date <= today:
            # FIX: tokens_used 不包含 cache_read (缓存读取价格便宜90%)
            total = (
                stats["input_tokens"]
                + stats["output_tokens"]
            )

            if db.save_usage(
                date=date,
                tool_name="claude",
                host_name=hostname,
                tokens_used=total,
                input_tokens=stats["input_tokens"],
                output_tokens=stats["output_tokens"],
                cache_tokens=stats["cache_read_tokens"] + stats["cache_creation_tokens"],
                request_count=stats["request_count"],
                models_used=sorted(stats["models_used"]),
            ):
                saved += 1
```

#### 验证方法

```bash
# 验证 Claude JSONL 原始数据
cat ~/.claude/projects/*/*.jsonl | python3 -c "
import json, sys
for line in sys.stdin:
    entry = json.loads(line.strip())
    if entry.get('type') == 'assistant' and 'message' in entry:
        usage = entry['message'].get('usage', {})
        inp = usage.get('input_tokens', 0)
        out = usage.get('output_tokens', 0)
        cache_read = usage.get('cache_read_input_tokens', 0)
        print(f'input={inp}, output={out}, cache_read={cache_read}, tokens_used={inp+out}')
"
```

---

## Fix 3 (P1): 添加缺失模型定价

### 文件: `app/modules/analytics/roi_calculator.py`

#### 更新 MODEL_PRICING (第104-121行)

```python
    MODEL_PRICING: dict[str, ModelPricing] = {
        # Claude models
        "claude-3-opus": ModelPricing(input_price=0.015, output_price=0.075),
        "claude-3-sonnet": ModelPricing(input_price=0.003, output_price=0.015),
        "claude-3-haiku": ModelPricing(input_price=0.00025, output_price=0.00125),
        "claude-3-5-sonnet": ModelPricing(input_price=0.003, output_price=0.015),
        "claude-3-5-haiku": ModelPricing(input_price=0.001, output_price=0.005),

        # Qwen models
        "qwen-max": ModelPricing(input_price=0.02, output_price=0.06),
        "qwen-plus": ModelPricing(input_price=0.004, output_price=0.012),
        "qwen-turbo": ModelPricing(input_price=0.002, output_price=0.006),
        "qwen3-coder-next": ModelPricing(input_price=0.002, output_price=0.006),
        "qwen3.5-plus": ModelPricing(input_price=0.004, output_price=0.012),
        "qwen3.6-plus": ModelPricing(input_price=0.004, output_price=0.012),
        "qwen3-coder-plus": ModelPricing(input_price=0.002, output_price=0.006),

        # GLM models
        "glm-4": ModelPricing(input_price=0.001, output_price=0.001),
        "glm-4-plus": ModelPricing(input_price=0.005, output_price=0.005),
        "glm-4-flash": ModelPricing(input_price=0.0001, output_price=0.0001),
        "glm-4.7": ModelPricing(input_price=0.001, output_price=0.001),
        "glm-5": ModelPricing(input_price=0.002, output_price=0.002),
        "glm-5.1": ModelPricing(input_price=0.003, output_price=0.003),

        # Mimo models (智谱)
        "mimo-v2-pro": ModelPricing(input_price=0.002, output_price=0.002),
        "mimo-v2.5-pro": ModelPricing(input_price=0.003, output_price=0.003),

        # Other Chinese models
        "MiniMax-M2.5": ModelPricing(input_price=0.002, output_price=0.002),
        "kimi-k2.5": ModelPricing(input_price=0.008, output_price=0.008),
        "coder-model": ModelPricing(input_price=0.002, output_price=0.002),

        # GPT models
        "gpt-4": ModelPricing(input_price=0.03, output_price=0.06),
        "gpt-4-turbo": ModelPricing(input_price=0.01, output_price=0.03),
        "gpt-4o": ModelPricing(input_price=0.005, output_price=0.015),
        "gpt-4o-mini": ModelPricing(input_price=0.00015, output_price=0.0006),
        "gpt-3.5-turbo": ModelPricing(input_price=0.0005, output_price=0.0015),

        # Gemini models
        "gemini-pro": ModelPricing(input_price=0.00025, output_price=0.0005),
        "gemini-1.5-pro": ModelPricing(input_price=0.0035, output_price=0.0105),
        "gemini-1.5-flash": ModelPricing(input_price=0.000075, output_price=0.0003),

        # DeepSeek models
        "deepseek-chat": ModelPricing(input_price=0.00014, output_price=0.00028),
        "deepseek-coder": ModelPricing(input_price=0.00014, output_price=0.00028),
    }
```

---

## Fix 4 (P1): 前端ROI异常值处理

### 文件: `frontend/src/components/features/analysis/ROIAnalysis.tsx`

#### 修改 ROI 显示 (第357-363行)

```tsx
          <div className="col-md-3">
            <StatCard
              label={t('roiPercentage', language)}
              value={
                roiMetrics.roi_percentage < -100
                  ? 'N/A'
                  : `${roiMetrics.roi_percentage.toFixed(1)}%`
              }
              icon={<i className="bi bi-graph-up-arrow fs-4" />}
              variant={roiMetrics.roi_percentage >= 0 ? 'success' : 'danger'}
            />
            {roiMetrics.roi_percentage < -100 && (
              <div className="text-danger small mt-1">
                <i className="bi bi-exclamation-triangle me-1" />
                {t('roiDataAnomaly', language) || 'Data anomaly detected'}
              </div>
            )}
          </div>
```

#### 添加异常数据警告组件 (在 StatCard 之后)

```tsx
      {/* Data Anomaly Warning */}
      {roiMetrics && roiMetrics.roi_percentage < -100 && (
        <div className="alert alert-warning mb-4" role="alert">
          <i className="bi bi-exclamation-triangle me-2" />
          <strong>{t('dataAnomalyDetected', language) || 'Data Anomaly Detected'}:</strong>
          {' '}
          {t('tokenAccumulationWarning', language) ||
            'Token counts may be inflated due to cumulative counting. Cost and ROI calculations may be inaccurate.'}
        </div>
      )}
```

### 文件: `frontend/src/i18n/index.ts`

#### 添加翻译

```typescript
// en
roiDataAnomaly: 'Data anomaly detected',
dataAnomalyDetected: 'Data Anomaly Detected',
tokenAccumulationWarning: 'Token counts may be inflated due to cumulative counting. Cost and ROI calculations may be inaccurate.',

// zh
roiDataAnomaly: '数据异常',
dataAnomalyDetected: '检测到数据异常',
tokenAccumulationWarning: 'Token 计数可能存在累计膨胀，成本和 ROI 计算可能不准确。',
```

---

## Fix 5 (P2): 解析 model JSON 字符串

### 文件: `app/modules/analytics/roi_calculator.py`

#### 修改 model 解析 (第243行)

```python
        for model_row in model_rows:
            model_raw = model_row.get("model") or "default"

            # FIX: 解析 JSON 字符串格式的 model
            if isinstance(model_raw, str):
                try:
                    import json
                    model_parsed = json.loads(model_raw)
                    if isinstance(model_parsed, list):
                        model = model_parsed[0] if model_parsed else "default"
                    elif isinstance(model_parsed, str):
                        model = model_parsed
                    else:
                        model = str(model_parsed)
                except (json.JSONDecodeError, TypeError):
                    model = model_raw
            else:
                model = str(model_raw)

            input_tokens = model_row.get("input_tokens") or 0
            output_tokens = model_row.get("output_tokens") or 0
```

---

## 历史数据修复

修复脚本后，需要重新计算历史数据：

```bash
# 1. 重新获取 Qwen 数据
cd /Users/rhuang/workspace/open-ace
python scripts/fetch_qwen.py --days 90

# 2. 重新获取 Claude 数据
python scripts/fetch_claude.py --days 90

# 3. 刷新 summary 表
curl -X POST http://localhost:5001/api/summary/refresh
```

---

## 文件修改清单

| 文件 | 修改类型 | 优先级 |
|------|---------|--------|
| `scripts/fetch_qwen.py` | Token计算修正 (actual_input = prompt - cached) | P0 |
| `scripts/fetch_claude.py` | tokens_used 不含 cache_read | P0 |
| `app/modules/analytics/roi_calculator.py` | 模型定价 + model解析 | P1/P2 |
| `frontend/src/components/features/analysis/ROIAnalysis.tsx` | 异常值显示 | P1 |
| `frontend/src/i18n/index.ts` | 添加翻译 | P1 |

## 问题汇总（修复5个）

| # | 优先级 | 问题 | 状态 |
|---|--------|------|------|
| 1 | P0 | Qwen Token累计效应 | 待修复 |
| 2 | P0 | Claude tokens_used 包含 cache_read | 待修复 |
| 3 | P1 | ROI负值显示 | 待修复 |
| 4 | P1 | 缺失模型定价 | 待修复 |
| 5 | P2 | model是JSON字符串 | 待修复 |
