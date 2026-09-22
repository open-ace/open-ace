# Optional external identity: capability probe and token exchange

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
`{"error": {"type": "session_stopped"}}`, HTTP 410, if a session is stopped
*after* a token was minted and a proxy call is then made with it — the
exchange-time check above is the earlier, explicit denial; the proxy-time
one is the fallback if the session is stopped mid-lease.)

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
