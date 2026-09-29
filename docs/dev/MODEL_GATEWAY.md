# Model Gateway (LiteLLM-compatible POC) — 模型网关（LiteLLM 兼容，POC）

[English](#english) | [中文](#中文)

---

## English

Open ACE can optionally route LLM proxy traffic through a **LiteLLM-compatible model
gateway** while preserving Open ACE quota checks, usage recording, attribution, and
direct-provider behavior. This makes Open ACE a vendor-neutral control plane on top
of a centralized model gateway.

This feature is a **fully-pluggable, flag-toggled module** (see "Removal" below). It
mirrors the `run_timeline` pluggable shape: a config flag, a Null/real planner pair,
and a single integration seam in the LLM proxy handler.

## How it works

Every proxied LLM request (`/api/workspace/llm-proxy` and `/api/remote/llm-proxy`)
passes through a single seam in `handle_llm_proxy_request`
(`app/modules/workspace/llm_proxy_handler.py`):

1. Token is validated and **scope-checked** (unchanged).
2. **Open ACE quota is checked** — *before any forwarding*, in both modes.
3. The seam consults the gateway planner:
   - **Disabled** (default) → the existing **direct-provider** path runs unchanged.
   - **Enabled + configured** → the request is forwarded once to the gateway with
     gateway credentials + attribution headers; the gateway owns upstream keys and
     failover. **Usage is recorded** from the gateway response via the same shared
     tail as direct mode.
   - **Enabled but misconfigured** → a clear **503** is returned; Open ACE **never
     silently falls back** to direct mode.

### Attribution forwarded (R3)

Two injection points are used (defense in depth):

- **HTTP headers**: `X-OpenACE-User-Id`, `X-OpenACE-Tenant-Id`, `X-OpenACE-Session-Id`,
  `X-OpenACE-Tool`, `X-OpenACE-Model`, `X-OpenACE-Run-Id`, `X-OpenACE-Provider` —
  sourced **only** from the validated proxy token. They never carry secrets and are
  never echoed to the client.
- **Request body `metadata`** (LiteLLM spec): a non-destructive
  `{openace_user_id, openace_tenant_id, openace_session_id, openace_tool,
  openace_run_id, openace_provider_hint, openace_model}` object plus the `user` field,
  so LiteLLM records them in its spend/logs DB. When streaming, Open ACE also sets
  `stream_options.include_usage=true` so a usage chunk is returned.

### Responses API

A `/responses` request is converted to `/chat/completions` (the same conversion the
direct path uses for non-OpenAI upstreams) and the chat-completions response is
re-wrapped into a Responses-API SSE stream — identical to direct mode.

## Configuration

There are two layers: a **toggle** and **credentials**.

### 1. Toggle (`model_gateway.enabled`)

In `~/.open-ace/config.json`:

```json
{
  "model_gateway": { "enabled": true }
}
```

Or via environment override (handy for CI/headless): `OPENACE_MODEL_GATEWAY_MODE=gateway`.

Default is **disabled** (`direct` mode), so existing deployments are unaffected.

### 2. Credentials (admin API / UI)

Store the gateway base URL + gateway API key via the admin UI
(**Manage → Settings → Model Gateway**, `/manage/settings/model-gateway`) or the
admin REST API:

```bash
curl -X PUT http://localhost:5000/api/management/model-gateway-config \
  -H "Authorization: Bearer <admin-session>" \
  -H "Content-Type: application/json" \
  -d '{
        "base_url": "http://litellm-host:4000/v1",
        "api_key": "sk-litellm-virtual-key",
        "model_prefix_mode": false,
        "model_prefix": null
      }'
```

Endpoints (admin-only): `GET/PUT/DELETE /api/management/model-gateway-config` and
`POST /api/management/model-gateway-config/test`.

The gateway API key is encrypted at rest (Fernet, same key derivation as API-key
encryption); `GET` returns only a masked value.

Environment overrides (skip the DB entirely): `OPENACE_MODEL_GATEWAY_BASE_URL`,
`OPENACE_MODEL_GATEWAY_API_KEY`, `OPENACE_MODEL_GATEWAY_MODEL_PREFIX_MODE`,
`OPENACE_MODEL_GATEWAY_MODEL_PREFIX`.

### Model prefix

LiteLLM usually expects `provider/model`. Open ACE is provider-agnostic by default
(passthrough — configure model aliases on the LiteLLM side). Enable **Model Prefix
Mode** to prefix the requested model (e.g. `gpt-4` → `openai/gpt-4`) based on the
token's provider, or supply an explicit prefix.

## Configuring LiteLLM

Minimal LiteLLM `config.yaml`:

```yaml
model_list:
  - model_name: gpt-4
    litellm_params:
      model: openai/gpt-4
      api_key: os.environ/OPENAI_API_KEY
  - model_name: glm-5
    litellm_params:
      model: openai/glm-5
      api_key: os.environ/UPSTREAM_KEY

general_settings:
  master_key: sk-litellm-master
  # Optional: record the Open ACE metadata in LiteLLM's spend DB
  # disable_turn_off_message_logging: False
```

Create a virtual key for Open ACE, then set that key + the LiteLLM base URL
(`http://<host>:4000/v1`) in Open ACE's gateway config. LiteLLM will see the
`X-OpenACE-*` headers and the body `metadata`, letting you correlate LiteLLM spend
with Open ACE sessions/runs.

## Accepted POC limitations

- **Provider attribution**: usage is recorded against the **token-claimed** provider
  (e.g. `openai`), not the real upstream behind LiteLLM. **Model** attribution is
  taken from the response `model` field and is accurate. (Future: derive the real
  provider from a LiteLLM response header hint.)
- **Gateway errors** are sanitized: the gateway key is redacted from any upstream
  error before it is logged or returned; responses are truncated to 500 chars.
- Gateway mode makes a **single attempt** (the gateway owns upstream failover); the
  direct-mode per-key HA loop does not apply.

## Removal checklist

This feature is self-contained. To remove it:

1. `git rm app/modules/workspace/model_gateway/`
2. Delete the model-gateway seam + import in `app/modules/workspace/llm_proxy_handler.py`
   (the `if not _gateway.is_noop:` block and the `_forward_via_gateway` /
   `_gateway_error_response` helpers; the Phase-0 `_finalize_upstream_response` /
   `_emit_responses_sse` extraction can be kept or inlined).
3. Remove `is_model_gateway_enabled` from `app/utils/config.py`.
4. Unregister the blueprint in `app/__init__.py:register_blueprints` and delete
   `app/routes/model_gateway.py`.
5. Delete the admin page `frontend/src/components/features/management/ModelGatewayConfig.tsx`,
   its API client `frontend/src/api/modelGateway.ts`, the route/nav/i18n entries.
6. Drop the `model_gateway_config` table migration
   (`migrations/versions/20260627_001_add_model_gateway_config.py`).
7. Delete this file.

Run-timeline provenance is **not** on the LLM-proxy path, so it is unaffected by
gateway mode regardless.

---

## 中文

Open ACE 可以选择将 LLM 代理流量**路由**经由一个**兼容 LiteLLM 的模型网关**，同时保留 Open ACE 的配额检查、用量记录、归因（attribution）以及直连提供方行为。这使得 Open ACE 成为构建在集中式模型网关之上的厂商中立控制平面。

该功能是一个**完全可插拔、由开关切换的模块**（见下文“移除清单”）。它沿用了 `run_timeline` 的可插拔形态：一个配置开关、一对 Null/真实 planner，以及 LLM 代理处理器中的单一集成接缝。

## 工作原理

每个被代理的 LLM 请求（`/api/workspace/llm-proxy` 与 `/api/remote/llm-proxy`）都会经过 `handle_llm_proxy_request`（`app/modules/workspace/llm_proxy_handler.py`）中的单一接缝：

1. 验证令牌并做**作用域检查**（保持不变）。
2. **检查 Open ACE 配额**——在两种模式下都*先于任何转发*执行。
3. 该接缝会咨询网关 planner：
   - **禁用**（默认）→ 照旧运行现有的**直连提供方**路径。
   - **启用且已配置** → 请求携带网关凭据 + 归因头转发到网关一次；上游密钥与故障转移由网关负责。**用量记录**取自网关响应，经由与直连模式相同的共享尾部逻辑完成。
   - **启用但配置有误** → 返回明确的 **503**；Open ACE **绝不静默回退**到直连模式。

### 转发的归因信息（R3）

采用两个注入点（纵深防御）：

- **HTTP 请求头**：`X-OpenACE-User-Id`、`X-OpenACE-Tenant-Id`、`X-OpenACE-Session-Id`、`X-OpenACE-Tool`、`X-OpenACE-Model`、`X-OpenACE-Run-Id`、`X-OpenACE-Provider`——**仅**取自经过验证的代理令牌。它们绝不携带机密信息，也绝不回显给客户端。
- **请求体 `metadata`**（LiteLLM 规范）：一个非破坏性的 `{openace_user_id, openace_tenant_id, openace_session_id, openace_tool, openace_run_id, openace_provider_hint, openace_model}` 对象，外加 `user` 字段，使 LiteLLM 将其记录到自己的 spend/logs 数据库中。流式请求时，Open ACE 还会设置 `stream_options.include_usage=true`，以便返回一个 usage 数据块。

### Responses API

发往 `/responses` 的请求会被转换为 `/chat/completions`（与直连路径处理非 OpenAI 上游时使用的转换相同），chat-completions 响应随后被重新封装为 Responses-API 的 SSE 流——与直连模式完全一致。

## 配置

配置分为两层：一个**开关**与**凭据**。

### 1. 开关（`model_gateway.enabled`）

在 `~/.open-ace/config.json` 中：

```json
{
  "model_gateway": { "enabled": true }
}
```

也可以通过环境变量覆盖（便于 CI/无头环境）：`OPENACE_MODEL_GATEWAY_MODE=gateway`。

默认为**禁用**（`direct` 模式），因此现有部署不受影响。

### 2. 凭据（管理端 API / UI）

通过管理端 UI（**Manage → Settings → Model Gateway**，`/manage/settings/model-gateway`）或管理端 REST API，保存网关 base URL + 网关 API 密钥：

```bash
curl -X PUT http://localhost:5000/api/management/model-gateway-config \
  -H "Authorization: Bearer <admin-session>" \
  -H "Content-Type: application/json" \
  -d '{
        "base_url": "http://litellm-host:4000/v1",
        "api_key": "sk-litellm-virtual-key",
        "model_prefix_mode": false,
        "model_prefix": null
      }'
```

端点（仅限管理员）：`GET/PUT/DELETE /api/management/model-gateway-config` 与 `POST /api/management/model-gateway-config/test`。

网关 API 密钥静态加密存储（Fernet，与 API 密钥加密采用相同的密钥推导）；`GET` 只返回脱敏值。

环境变量覆盖（完全绕过数据库）：`OPENACE_MODEL_GATEWAY_BASE_URL`、`OPENACE_MODEL_GATEWAY_API_KEY`、`OPENACE_MODEL_GATEWAY_MODEL_PREFIX_MODE`、`OPENACE_MODEL_GATEWAY_MODEL_PREFIX`。

### 模型前缀

LiteLLM 通常期望 `provider/model` 形式。Open ACE 默认与提供方无关（透传——在 LiteLLM 一侧配置模型别名）。启用**模型前缀模式（Model Prefix Mode）**可根据令牌的提供方为请求的模型添加前缀（例如 `gpt-4` → `openai/gpt-4`），也可以显式指定前缀。

## 配置 LiteLLM

最小化的 LiteLLM `config.yaml`：

```yaml
model_list:
  - model_name: gpt-4
    litellm_params:
      model: openai/gpt-4
      api_key: os.environ/OPENAI_API_KEY
  - model_name: glm-5
    litellm_params:
      model: openai/glm-5
      api_key: os.environ/UPSTREAM_KEY

general_settings:
  master_key: sk-litellm-master
  # Optional: record the Open ACE metadata in LiteLLM's spend DB
  # disable_turn_off_message_logging: False
```

为 Open ACE 创建一个虚拟密钥，然后将该密钥 + LiteLLM base URL（`http://<host>:4000/v1`）设置到 Open ACE 的网关配置中。LiteLLM 会看到 `X-OpenACE-*` 请求头与请求体 `metadata`，从而可以将 LiteLLM 的花费与 Open ACE 的会话/运行关联起来。

## 已接受的 POC 局限

- **提供方归因**：用量记录到**令牌所声明**的提供方（例如 `openai`），而非 LiteLLM 背后真实的上游。**模型**归因取自响应的 `model` 字段，是准确的。（未来：从 LiteLLM 响应头提示推导真实提供方。）
- **网关错误**会经过净化处理：任何上游错误在被记录或返回之前都会抹除网关密钥；响应被截断为 500 字符。
- 网关模式只做**单次尝试**（上游故障转移由网关负责）；直连模式的逐密钥 HA 循环不适用。

## 移除清单

该功能是自包含的。要移除它：

1. `git rm app/modules/workspace/model_gateway/`
2. 删除 `app/modules/workspace/llm_proxy_handler.py` 中的 model-gateway 接缝与导入（`if not _gateway.is_noop:` 代码块以及 `_forward_via_gateway` / `_gateway_error_response` 辅助函数；Phase-0 抽取出的 `_finalize_upstream_response` / `_emit_responses_sse` 可以保留或内联）。
3. 从 `app/utils/config.py` 移除 `is_model_gateway_enabled`。
4. 在 `app/__init__.py:register_blueprints` 注销蓝图，并删除 `app/routes/model_gateway.py`。
5. 删除管理页面 `frontend/src/components/features/management/ModelGatewayConfig.tsx`、其 API 客户端 `frontend/src/api/modelGateway.ts`，以及路由/导航/i18n 条目。
6. 删除 `model_gateway_config` 表的迁移（`migrations/versions/20260627_001_add_model_gateway_config.py`）。
7. 删除本文件。

Run-timeline 溯源**不在** LLM 代理路径上，因此无论如何都不受网关模式影响。
