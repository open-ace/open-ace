# Model Gateway (LiteLLM-compatible) — POC

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
