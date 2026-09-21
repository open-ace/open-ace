# Optional external identity and capability probe

Set `OPENACE_EXTERNAL_IDENTITY_POLICY_FILE` to an absolute, private regular JSON
file (mode `0600`) to enable `POST /api/integrations/external/capabilities`.
The default is disabled (404). No menus, background tasks, model requests, login
sessions or workspaces are created. This is a protocol foundation, not a complete
external model gateway.

The policy has `issuers` (`id`, `secret_file`, `audience`) and `mappings`
(`issuer`, `external_org`, `external_user`, `external_login`, `tenant_id`,
`user_id`). Each issuer has a separate private secret file containing at least
32 random bytes encoded as printable text, and a mandatory `audience` string
(identifier characters only). The audience scopes every signature to one
deployment: an issuer secret reused across staging and production must declare
distinct audiences, and a signature accepted by one deployment then never
verifies in the other. The entire policy and secrets are reloaded per probe.
Mappings are exact and unique; no email matching, automatic account creation,
role elevation, wildcard mapping or caller-supplied local tenant/user is allowed.
Use existing active local accounts with completed password setup and active
tenants. Deleted, inactive or reassigned accounts fail closed.

The external application's trusted server signs its authenticated principal in
JSON fields `organization`, `user`, `login` (all strings, no other fields).
It sets `X-ACE-Issuer`, `X-ACE-Time` (Unix seconds), `X-ACE-Nonce` (48 lowercase
hexadecimal characters from a cryptographic random source) and `X-ACE-Signature`.
Browser cookies, bearer auth, Origin, query parameters and duplicate JSON fields
are rejected. Reverse proxies must preserve the signed endpoint path and body.
Use TLS between hosts; a dedicated loopback HTTP deployment is possible.

Signature: HMAC-SHA256, lowercase hexadecimal, over concatenated UTF-8 frames
`<byte length>:<value>`, in this order:

1. `openace-external-v1`
2. issuer ID
3. timestamp header
4. nonce header
5. `POST`
6. `/api/integrations/external/capabilities`
7. SHA256 of the exact request body bytes, lowercase hexadecimal
8. the issuer's configured `audience`

(The `openace-external-v1` label was redefined to include frame 8 while this
endpoint was pre-release; nothing upstream shipped the seven-frame form.)

Clock skew is limited to 30 seconds. Authenticated nonces are consumed atomically
in a shared database table with `(issuer, nonce)` as its primary key and retained
for 120 seconds. Replay is rejected across workers; database failure is denial.
Only expired nonce rows are cleaned. No keys, bodies or signatures are persisted
in this table. The host should rate-limit this endpoint independently.

A successful response includes `protocol`, `issuer`, `request_nonce`, mapped
`identity` and `capabilities`. Clients must verify the expected issuer, nonce,
protocol, required fields, TLS peer and allowed destination; do not follow
redirects or accept HTML login pages as success.

This endpoint always reports `diagnosis_llm: false`, `agent_workspace: false`
and `reason: governance_adapter_not_implemented`. It does not issue a login or
model token. Adapters that would advertise a capability through this endpoint
ship separately, opt in, and carry their own session/model binding, quota,
filtering, audit and cancellation enforcement. Identity success alone must
never enable either capability.

The module tests use real HMAC and SQLite (cross-worker replay, identity
mismatch, account/tenant revocation, a shared Python/Go signature vector), the
route tests cover signed accept / replay / unknown issuer / disabled, and the
replay store has a real-PostgreSQL integration test.
