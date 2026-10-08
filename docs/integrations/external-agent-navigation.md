# Optional external Agent navigation

This opt-in integration lets a trusted application discover the native workspace
isolation posture, list a mapped user's own local Qwen sessions, and issue a
short-lived entry to the native workbench. It does not replace login, start a
parallel Agent runner, expose model credentials, copy business data, or implement
OS isolation. The client chooses the deployment complexity.

## Requirements and configuration

This review patch requires the native `workspace_isolation_contract` service and
an accepted deployment whose local workbench entry points (webui,
filesystem_api, session_history) are enforced. Remote terminal and VS Code are
native remote-machine features; this handoff does not grant machine assignments,
paths or remote session tokens. The older pinned baseline without this service
refuses the request. A partial/provisional local-workbench posture is also
refused. API fakes are not Linux acceptance evidence.

Set `OPENACE_EXTERNAL_AGENT_ENABLED=1`, reuse the separate
`OPENACE_EXTERNAL_IDENTITY_POLICY_FILE`, and point
`OPENACE_EXTERNAL_AGENT_POLICY_FILE` to a private regular JSON file (0600):

```json
{
  "required_isolation": "os_user",
  "accepted_revisions": [],
  "acceptance_ref": "",
  "subjects": [
    {"issuer": "client", "organization": "1", "user": "7", "login": "alice"}
  ]
}
```

Fill the revision allowlist and reference with the actual deployment acceptance
record before enabling. Empty values deliberately deny access. The native
`workspace.required_isolation_level` must explicitly pin the same level; a
derived default is insufficient because the native UI launches independently of
the external request. Both `os_user` and `sandboxed` require a matching, accepted
runtime. `os_user` is not a strong sandbox or an egress/resource isolation claim.

Every external identity maps to an existing active user and tenant. Navigation
rejects many-to-one identity aliases and, for `os_user`, missing system accounts.
Sandboxed deployments use native per-instance identity without requiring a host
OS account. Ordinary login
and text inference mapping semantics are unchanged.

## Protocol

`POST /api/integrations/external/agent` uses the existing `openace-external-v1`
signature framing, including this exact path in the signature audience. No
cookie, Authorization, Origin, or query identity is accepted on this endpoint.
Body fields are `organization/user/login/operation/parameters`.

Operations:

- `capabilities`, parameters `{}`: returns `openace-agent-v1`, mapped identity,
  opaque workspace reference, `governance: openace_native`, and the unmodified
  native isolation snapshot. Initializing the native manager can run its normal
  bookkeeping, but this operation never calls its user-launch method.
- `sessions`: requires `required_isolation` and `accepted_revisions`; returns at
  most 50 own local Qwen chat sessions with valid projects, active or completed.
  No message body, tool permission, project path or credential is returned.
- `launch`: additionally takes a random 48-lowercase-hex `request_id`; prepares
  native navigation, not an empty fake CLI history or a running process.
- `open`: additionally takes a listed `session_id`; revalidates exact owner and
  tenant even when the user has an administrator role.

Responses use the existing signed-request-correlated envelope. The `result`
contains `capabilities`, `sessions`, `ticket`, `expires_at`; ticket is empty and
expiry zero for read operations. The caller must compare identity, workspace,
version and posture with its own accepted policy, not just trust `available`.

The client builds `/api/integrations/external/agent/handoff?ticket=...` on its
configured browser-visible ACE origin. Tickets are 60-second, one-use navigation
receipts stored by hash with SQL atomic consumption. An identical launch request
does not extend a ticket. Expiry/consumption requires an explicit new request.

Handoff requires a native login session and exact tenant/user equality. A ticket
alone cannot log anyone in. Wrong-account use does not consume the receipt;
authenticated consumption does, even if a subsequent policy check refuses.
The handler rechecks live identity/mapping/grants/signing-key binding, the hard
isolation floor, runtime version and workspace reference. Session resume also
rechecks owner and delegates history resolution to native code. Only the native
session ID/project-name/tool-name fields are URL-encoded into a fixed workbench
route; arbitrary redirect/tool permission parameters are not copied.

Native SSO/login, remote-machine grants, tool approvals, model selection,
runtime cancellation and history remain in the native workbench. External entry
revocation is not a promise to terminate an independently authenticated native
session. Global deprovisioning must revoke native account/session access and
stop native runs.

The WebUI proxy now derives its tenant from the active user rather than assuming
tenant 1. When external Agent navigation is enabled, proxy setup failures refuse
launch. Native pool selection remains native pool selection: this feature does
not claim exact external text-credential binding or unified atomic quotas.

## Compatibility and tests

Default-off routes and separate policy/table avoid adding mandatory UI or setup
to ordinary installations. No product-specific roles, jobs, menus or branding are
added. Tenant routing is a general correctness fix and can be reviewed separately.

`test_external_agent_flask.py` checks real Flask/HMAC/SQLite contracts, concurrent
one-use receipts, cross-user/tenant access, mapping/key/policy drift, expiry,
signature replay and safe native resume construction. Runtime and login/account
lookup are explicit fakes. `test_webui_manager.py` checks actual-tenant proxy
wiring, missing-tenant refusal and optional fail-closed launch; these are not
production SSO, PostgreSQL or Linux process/filesystem acceptance tests.
