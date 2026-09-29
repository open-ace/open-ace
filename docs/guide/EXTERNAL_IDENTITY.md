# Optional External Identity: Capability Probe and Token Exchange — 可选外部身份：能力探测与令牌交换

[English](#english) | [中文](#中文)

---

## English

Set `OPENACE_EXTERNAL_IDENTITY_POLICY_FILE` to an absolute, private regular JSON
file (mode `0600`) to enable `POST /api/integrations/external/capabilities`.
The default is disabled (404). No menus, background tasks, model requests, login
sessions or workspaces are created by the probe itself.

Additionally set `OPENACE_EXTERNAL_TOKEN_ENABLED=1` (same policy file) to enable
`POST /api/integrations/external/token`, which issues a short-lived, scoped
proxy token an external server can use to call ACE's existing governed LLM
proxy on behalf of the mapped local user. The default is disabled (404),
independently of the probe. This is the governed-invocation adapter the probe's
`reason: governance_adapter_not_implemented` describes as not yet implemented
until an operator opts in here.

## Policy file

The policy has `issuers` and `mappings`. Each issuer entry is:

- `id` (required) — issuer identifier, identifier characters only.
- `secret_file` (required) — absolute path to a private regular file (mode
  `0600`) containing at least 32 random bytes encoded as printable text.
- `audience` (required) — identifier characters only. Scopes every signature
  to one deployment: an issuer secret reused across staging and production
  must declare distinct audiences, and a signature accepted by one deployment
  then never verifies in the other.
- `allowed_providers` (optional) — non-empty list of provider identifiers this
  issuer's tokens may target. Required (non-empty, alongside `allowed_models`)
  for `/token` to grant this issuer anything; absent or empty, the exchange
  fails closed for that issuer regardless of what the mapping allows.
- `allowed_models` (optional) — non-empty list of model names (1-191 chars
  each) this issuer's tokens may request. Same fail-closed rule as above.
- `max_ttl_seconds` (optional, default 900) — this issuer's ceiling on token
  lifetime, `1..3600` (the deployment-wide hard ceiling). An exchange may
  request less; never more.

`mappings` entries are `issuer`, `external_org`, `external_user`,
`external_login`, `tenant_id`, `user_id`, unchanged by the token exchange. The
entire policy and every issuer's secret are reloaded per request — on both
endpoints. Mappings are exact and unique; no email matching, automatic account
creation, role elevation, wildcard mapping or caller-supplied local
tenant/user is allowed. Use existing active local accounts with completed
password setup and active tenants. Deleted, inactive or reassigned accounts
fail closed on every request, not only at token issuance.

## Signing (shared by both endpoints)

The external application's trusted server signs its authenticated principal in
a JSON body (fields below; no others — duplicate JSON fields are rejected). It
sets `X-ACE-Issuer`, `X-ACE-Time` (Unix seconds), `X-ACE-Nonce` (48 lowercase
hexadecimal characters from a cryptographic random source) and `X-ACE-Signature`.
Browser cookies, bearer auth, Origin and query parameters are rejected on both
routes. Reverse proxies must preserve the signed endpoint path and body. Use
TLS between hosts; a dedicated loopback HTTP deployment is possible.

Signature: HMAC-SHA256, lowercase hexadecimal, over concatenated UTF-8 frames
`<byte length>:<value>`, in this order:

1. `openace-external-v1`
2. issuer ID
3. timestamp header
4. nonce header
5. `POST`
6. the signed endpoint's exact path — `/api/integrations/external/capabilities`
   or `/api/integrations/external/token`; a signature for one never verifies
   against the other
7. SHA256 of the exact request body bytes, lowercase hexadecimal
8. the issuer's configured `audience`

(The `openace-external-v1` label was redefined to include frame 8 while this
protocol was pre-release; nothing upstream shipped the seven-frame form.)

Clock skew is limited to 30 seconds. Authenticated nonces are consumed atomically
in a shared database table with `(issuer, nonce)` as its primary key and retained
for 120 seconds. Replay is rejected across workers and across endpoints (the
same nonce cannot authenticate a `/capabilities` call and then a `/token`
call); database failure is denial. Only expired nonce rows are cleaned. No
keys, bodies or signatures are persisted in this table. The host should
rate-limit both endpoints independently.

An unrecognized issuer name still runs the full keyed comparison, against an
internal decoy secret, before being denied — the failure path never reveals
which issuer names exist in the policy via a timing difference.

## `POST /api/integrations/external/capabilities`

Body: `organization`, `user`, `login` (all strings).

A successful response includes `protocol`, `issuer`, `request_nonce`, mapped
`identity`, `capabilities`, `reason`, and `exchange` — a dry run of what
`/token` would grant this issuer right now, without consuming anything beyond
the one nonce this call itself uses: `available` (bool — true only when the
issuer's policy grants at least one provider and one model, *and*
`OPENACE_EXTERNAL_TOKEN_ENABLED=1`), `providers` (sorted list) and
`max_ttl_seconds`.

This endpoint always reports `diagnosis_llm: false`, `agent_workspace: false`
and `reason: governance_adapter_not_implemented`. It does not itself issue a
login or model token — `/token` does, once enabled. Clients must verify the
expected issuer, nonce, protocol, required fields, TLS peer and allowed
destination; do not follow redirects or accept HTML login pages as success.

## `POST /api/integrations/external/token`

Body: `organization`, `user`, `login`, `provider` (all strings), plus an
optional `ttl_seconds` (integer). `provider` must be one of the issuer's
`allowed_providers`; an issuer with no `allowed_providers` or no
`allowed_models` configured grants nothing, whatever the identity mapping
says. An omitted `ttl_seconds` defaults to `min(900, issuer's max_ttl_seconds)`
so a sub-900 ceiling never forces every caller to pass the field explicitly;
an out-of-range value (outside `1..max_ttl_seconds`) is denied the same way
an invalid signature is — `{"error_code": "not_accessible"}`, 403.

A successful response includes `protocol`, `issuer`, `request_nonce`, `token`
(a proxy token — see below), `session_id`, `expires_at` (UTC, with offset,
computed from the instant of minting — not a floor to the nearest minute) and
`proxy_path` (currently always `/api/remote/llm-proxy`; there is no
`base_url`/host-derived field, since the `Host` header is not part of the
signature and can be attacker-influenced behind a misconfigured reverse
proxy). The response carries `Cache-Control: no-store`.

Every exchange for the same `(issuer, user)` reuses one underlying ACE
session (`session_id` is stable across repeated exchanges) instead of
accumulating a new row per call. That session is also the per-identity kill
switch: if an operator stops it, further exchanges for that identity deny
with `{"error_code": "session_stopped"}`, 403, until it is resumed. (This is
distinct from the proxy's own pre-existing circuit breaker, which returns
`{"error": {"message": "Session has been stopped", "type": "session_stopped"}}`,
HTTP 410, if a session is stopped *after* a token was minted and a proxy call
is then made with it — the exchange-time check above is the earlier, explicit
denial; the proxy-time one is the fallback if the session is stopped
mid-lease.)

A failed audit write denies the exchange (`{"error_code": "unavailable"}`,
503) and revokes only the just-minted token by its `jti` — it does not stop
the shared session or disturb the identity's other in-flight tokens.

### Using the token

The token is a normal ACE proxy token (`session_type: "external"`) for the
existing governed LLM proxy at the `proxy_path` above — the same request
shape as any other proxy client (`Authorization: Bearer <token>`). Three
behaviors apply only to `session_type: "external"` requests:

- **Deny on redact.** Every exchanged token carries `redact_policy: "deny"`.
  Where an ordinary proxy caller's redact verdict is logged and the original
  body is still forwarded, an external caller instead gets
  `{"error": {"message": "...", "type": "content_redaction_denied"}}`, 403 —
  raw text that requires redaction is never sent upstream. The content-filter
  scan for external callers also covers every text-bearing field in the
  request (all message roles including tool results, Responses-API `input`
  and `instructions`, legacy `prompt`, Anthropic `tool_use`/`tool_result`
  blocks), not only `role: "user"` as for ordinary chat callers.
- **Model allow-list.** The token carries the issuer's `allowed_models`. A
  request naming a model outside that list, or naming no model at all, gets
  `{"error": {"message": "Model not allowed for this caller", "type": "model_not_allowed"}}`,
  403.
- **Liveness recheck.** Unlike other session types, an `external` token
  rechecks the mapped user's and tenant's active status on every proxy call,
  not only at issuance — a deactivation takes effect on the next call, not at
  token expiry.

Standard proxy governance (quota, key resolution/failover, audit, streaming
usage accounting) applies unchanged; nothing here bypasses or duplicates it.

## Testing

The module tests use real HMAC and SQLite (cross-worker and cross-endpoint
replay, identity mismatch, account/tenant revocation, the timing-safe
unknown-issuer path, a shared Python/Go signature vector). The route tests
cover both endpoints: signed accept / replay / unknown issuer / disabled for
`/capabilities`; exchange mint, provider/ttl policy denial, the session kill
switch, jti-scoped audit-failure revocation, and an end-to-end exchange →
proxy call → metered usage → quota-visible test for `/token`. The replay
store has a real-PostgreSQL integration test.

---

## 中文

将 `OPENACE_EXTERNAL_IDENTITY_POLICY_FILE` 指向一个绝对路径的私有普通 JSON
文件（权限 `0600`）即可启用 `POST /api/integrations/external/capabilities`。
默认禁用（404）。探测本身不创建任何菜单、后台任务、模型请求、登录会话或工作区。

另外设置 `OPENACE_EXTERNAL_TOKEN_ENABLED=1`（同一份策略文件）可启用
`POST /api/integrations/external/token`：它签发一个短时效、限定范围的代理
token，外部服务器可用它代表映射到的本地用户调用 ACE 既有的受治理 LLM 代理。
默认同样禁用（404），且与探测相互独立。探测响应中 `reason:
governance_adapter_not_implemented` 所描述的"尚未实现"的受治理调用适配器，
正是由运维在此选择启用。

## 策略文件

策略包含 `issuers` 与 `mappings`。每个 issuer 条目为：

- `id`（必填）—— issuer 标识符，仅允许标识符字符。
- `secret_file`（必填）—— 一个私有普通文件（权限 `0600`）的绝对路径，内容为
  至少 32 个随机字节的可打印文本编码。
- `audience`（必填）—— 仅标识符字符。将每个签名限定到单一部署：在 staging
  与生产间复用同一 issuer secret 时必须声明不同的 audience，于是一个部署接受
  的签名在另一个部署中永远无法通过验证。
- `allowed_providers`（可选）—— 非空列表，声明该 issuer 的 token 可指向哪些
  provider 标识符。要让 `/token` 向该 issuer 授予任何内容则必填（非空，且需
  与 `allowed_models` 同时配置）；缺省或为空时，无论映射允许什么，该 issuer
  的交换一律 fail closed。
- `allowed_models`（可选）—— 非空列表，声明该 issuer 的 token 可请求的模型名
  （每项 1-191 字符）。fail-closed 规则同上。
- `max_ttl_seconds`（可选，默认 900）—— 该 issuer 的 token 生命期上限，取值
  `1..3600`（部署级硬上限）。交换可请求更短，不能更长。

`mappings` 条目为 `issuer`、`external_org`、`external_user`、`external_login`、
`tenant_id`、`user_id`，token 交换不会改动它们。整份策略与每个 issuer 的
secret 都按请求重新加载——两个端点皆然。映射为精确且唯一；不允许邮箱匹配、
自动建号、角色提升、通配映射或由调用方提供本地 tenant/user。请使用已完成
密码设置且处于活跃状态的既有本地账户与活跃租户。已删除、停用或被改派的账户
在每次请求时都 fail closed，而不只在签发 token 时。

## 签名（两个端点共用）

外部应用的可信服务器在一个 JSON body 中对其已认证主体签名（字段如下；不允许
其他字段——重复的 JSON 字段会被拒绝）。它设置 `X-ACE-Issuer`、`X-ACE-Time`
（Unix 秒）、`X-ACE-Nonce`（48 个小写十六进制字符，来自密码学随机源）与
`X-ACE-Signature`。两条路由都拒绝浏览器 Cookie、Bearer 认证、Origin 与查询
参数。反向代理必须原样保留被签名的端点路径与 body。主机间请使用 TLS；部署
一个专用的 loopback HTTP 也是可行的。

签名算法：HMAC-SHA256，小写十六进制，对以 UTF-8 帧 `<字节长度>:<值>` 拼接的
内容计算，顺序如下：

1. `openace-external-v1`
2. issuer ID
3. 时间戳头
4. nonce 头
5. `POST`
6. 被签名端点的精确路径 —— `/api/integrations/external/capabilities` 或
   `/api/integrations/external/token`；为一个端点生成的签名对另一个永不成立
7. 精确请求 body 字节的 SHA256，小写十六进制
8. 该 issuer 配置的 `audience`

（`openace-external-v1` 标签在本协议 pre-release 期间被重新定义为包含第 8 帧；
上游从未发布过七帧版本。）

时钟偏移限制在 30 秒内。已认证的 nonce 在一张共享数据库表中被原子消费，主键
为 `(issuer, nonce)`，保留 120 秒。重放在跨 worker 与跨端点两个维度上都被
拒绝（同一 nonce 不能先通过 `/capabilities` 认证、再通过 `/token` 认证）；
数据库失败即拒绝。只有过期的 nonce 行会被清理。该表不持久化任何 key、body
或签名。宿主应对两个端点分别独立限速。

未识别的 issuer 名也会先对内部诱饵 secret 跑完整的带密钥比较，然后才拒绝——
失败路径绝不通过时序差异泄露策略中存在哪些 issuer 名。

## `POST /api/integrations/external/capabilities`

Body：`organization`、`user`、`login`（均为字符串）。

成功响应包含 `protocol`、`issuer`、`request_nonce`、映射到的 `identity`、
`capabilities`、`reason` 与 `exchange`——它是对 `/token` 当前会授予该 issuer
什么的演练（dry run），除本次调用自身消耗的一个 nonce 外不消耗任何东西：
`available`（布尔——仅当 issuer 策略授予至少一个 provider 与一个模型，*且*
`OPENACE_EXTERNAL_TOKEN_ENABLED=1` 时为 true）、`providers`（排序后的列表）与
`max_ttl_seconds`。

该端点始终报告 `diagnosis_llm: false`、`agent_workspace: false` 与 `reason:
governance_adapter_not_implemented`。它本身不签发登录或模型 token——`/token`
在启用后会做。客户端必须校验预期的 issuer、nonce、协议、必需字段、TLS 对端
与允许的目的地；不要跟随重定向，也不要把 HTML 登录页当作成功。

## `POST /api/integrations/external/token`

Body：`organization`、`user`、`login`、`provider`（均为字符串），外加可选的
`ttl_seconds`（整数）。`provider` 必须是该 issuer `allowed_providers` 之一；
未配置 `allowed_providers` 或 `allowed_models` 的 issuer 无论身份映射怎么写
都授予不了任何内容。省略 `ttl_seconds` 时默认取
`min(900, issuer 的 max_ttl_seconds)`，这样低于 900 的上限不会强迫每个调用方
显式传该字段；超出范围（`1..max_ttl_seconds` 之外）的取值与无效签名同样对待
被拒——`{"error_code": "not_accessible"}`，403。

成功响应包含 `protocol`、`issuer`、`request_nonce`、`token`（一个代理
token——见下）、`session_id`、`expires_at`（UTC 带偏移，自签发时刻计算——不
是向下取整到分钟的值）与 `proxy_path`（当前恒为 `/api/remote/llm-proxy`；
没有 `base_url`/host 派生字段，因为 `Host` 头不属于签名内容，且在配置不当的
反向代理之后可能受攻击者影响）。响应携带 `Cache-Control: no-store`。

对同一 `(issuer, user)` 的每次交换都复用同一个底层 ACE 会话（`session_id`
在多次交换间稳定），而不是每次调用累积一个新行。该会话同时也是按身份的
kill switch：运维停止它之后，该身份的后续交换以
`{"error_code": "session_stopped"}` 403 拒绝，直至恢复。（这与代理自身既有的
熔断不同：token 签发之后会话才被停止、再用该 token 发起代理调用时，会得到
`{"error": {"message": "Session has been stopped", "type": "session_stopped"}}`、
HTTP 410——交换时的检查是更早的显式拒绝；代理时的检查是会话在租期内被停止时
的兜底。）

审计写入失败会拒绝该次交换（`{"error_code": "unavailable"}`，503），并仅按
`jti` 撤销刚签发的那个 token——不会停止共享会话，也不影响该身份其他在途
token。

### 使用令牌

该 token 是面向上述 `proxy_path` 既有受治理 LLM 代理的普通 ACE 代理 token
（`session_type: "external"`）——请求形态与其他代理客户端一致
（`Authorization: Bearer <token>`）。仅有三个行为只作用于
`session_type: "external"` 的请求：

- **redact 即拒绝。** 每个交换得到的 token 都携带 `redact_policy: "deny"`。
  普通代理调用方的 redact 判定只记录日志、原始 body 仍会被转发；外部调用方
  则会得到 `{"error": {"message": "...", "type": "content_redaction_denied"}}`，
  403——需要脱敏的原始文本永远不会被发往上游。针对外部调用方的内容过滤扫描
  还覆盖请求中所有承载文本的字段（含 tool result 在内的全部 message role、
  Responses API 的 `input` 与 `instructions`、legacy `prompt`、Anthropic 的
  `tool_use`/`tool_result` 块），而不像普通聊天调用方那样只扫 `role: "user"`。
- **模型白名单。** token 携带 issuer 的 `allowed_models`。请求指定了白名单
  之外的模型、或未指定模型，得到
  `{"error": {"message": "Model not allowed for this caller", "type": "model_not_allowed"}}`，
  403。
- **活跃性复查。** 与其他会话类型不同，`external` token 在每次代理调用时都
  复查映射用户与租户的活跃状态，而不只在签发时——停用在下一次调用即生效，
  无需等 token 过期。

标准代理治理（配额、key 解析/故障转移、审计、流式用量记账）原样适用；本
机制没有任何绕过或重复它的地方。

## 测试

模块测试使用真实 HMAC 与 SQLite（跨 worker 与跨端点重放、身份不匹配、账户/
租户撤销、时序安全的未知 issuer 路径、一个 Python/Go 共享签名向量）。路由
测试覆盖两个端点：`/capabilities` 的签名接受 / 重放 / 未知 issuer / 未启用；
`/token` 的交换签发、provider/ttl 策略拒绝、会话 kill switch、按 jti 范围的
审计失败撤销，以及一条 端到端 交换 → 代理调用 → 计量用量 → 配额可见 的测试。
重放存储另有真实 PostgreSQL 集成测试。
