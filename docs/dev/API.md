# API Reference — API 参考

[English](#english) | [中文](#中文)

---

## English

This reference documents all 426 HTTP endpoints exposed by the Open ACE backend,
grouped by path family. Paths are the full externally visible routes, with blueprint
URL prefixes already folded in. Route parameters use Flask syntax
(`<int:user_id>`, `<path:filename>`, ...).

### Base URL and authentication

**Base URL.** The server listens on port `19888` by default, so the API root is
`http://<host>:19888/api`. All business routes live under the `/api` prefix;
health probes and SPA routes live at the application root.

**Session token (primary authentication).** Most endpoints authenticate the
current user via the session token, provided either as:

- the `session_token` cookie (set by `POST /api/auth/login`), or
- the `Authorization: Bearer <token>` header.

**API keys and service tokens.** A few route groups use dedicated credentials
instead of (or on top of) the session token:

- `POST /api/upload/*` requires the `X-Upload-Auth` header carrying the
  `UPLOAD_AUTH_KEY` value; the endpoints return `503` when the key is not
  configured and `401` when it does not match.
- Remote agents authenticate with machine registration/agent tokens
  (`/api/remote/agent/*`, `/api/remote/usage-report`); see
  [../guide/REMOTE_AGENT.md](../guide/REMOTE_AGENT.md).
- `GET /api/quota/webui-check` accepts the webui token used by the webui
  backend middleware.
- The `/api/api-keys` family manages *provider* API keys stored encrypted in
  the database and consumed by the LLM proxy endpoints — they are not Open ACE
  login credentials.

**Roles.** Elevated endpoints require admin-level roles (`admin`,
`platform_admin`, `tenant_admin`). The authoritative per-endpoint listing is
[API_PERMISSION_MATRIX.md](API_PERMISSION_MATRIX.md); the role model is
described in [PERMISSION_MODEL.md](PERMISSION_MODEL.md).

**Auth column legend.** The `Auth` column reproduces the route's permission
decorators verbatim from the source; `-` means no permission decorator:

| Decorator | Meaning |
|-----------|---------|
| `public_endpoint` | Explicitly public; no authentication required. |
| `require_upload_auth` | Upload collector endpoints guarded by the `X-Auth-Key` header instead of a session. |
| `-` | No permission decorator declared; the handler may still resolve the session or use dedicated credentials (upload key, agent token). |
| `auth_required` | Valid session required. |
| `admin_required` | Admin role required (legacy platform-level admin). |
| `platform_admin_required` | Platform admin role required. |
| `api_key_admin_required` | Tenant/platform admin authorized for API-key management. |
| `any_admin_required` | Any admin role (platform, tenant or legacy admin). |
| `tenant_member_required` | Tenant member required. |
| `same_tenant_user_required` | Target user must belong to the caller's tenant (stacked with `admin_required`). |
| `same_tenant_or_platform_admin` | Same-tenant admin or platform admin. |
| `machine_access_required` | System admin or user assigned to the machine. |
| `machine_admin_required` | System admin or machine admin. |

### Global conventions

- **Content type.** Requests and responses are JSON (`application/json`)
  except for file upload/download, script downloads, SSE/WebSocket streams and
  the XML/HTML SSO metadata/ACS endpoints.
- **Errors.** Failures return a JSON body of the form `{"error": "<message>"}`
  with the usual status codes: `400` bad input, `401` unauthenticated,
  `403` insufficient role, `404` not found, `500` internal error.
- **Pagination.** List endpoints paginate with `limit`/`offset` query
  parameters (typical defaults 50–100). `GET
  /api/workspace/sessions/<session_id>/messages` instead uses composite-key
  keyset pagination.
- **Realtime.** SSE and WebSocket endpoints are flagged inline in the
  Description column (e.g. the alert stream, autonomous workflow event stream,
  remote session stream, and the terminal/VS Code/agent WebSocket routes).
- **Row granularity.** One row per registered route; a few multi-method aliases share a row, so the row count can differ from a method-level count.
- **Paths.** Paths are written as registered. Most have no trailing slash; a few catch-all proxy routes keep a trailing-slash base form (Flask's strict-slashes behavior).

### Endpoint families

| Family | Prefix | Endpoints |
|--------|--------|-----------|
| Remote machines and sessions | `/api/remote` | 56 |
| Workspace | `/api/workspace` | 46 |
| Autonomous development | `/api/autonomous` | 25 |
| Single sign-on | `/api/sso` | 20 |
| Integration and notification management | `/api/management` | 18 |
| User and organization administration | `/api/admin` | 18 |
| Tenants | `/api/tenants` | 19 |
| Compliance | `/api/compliance` | 17 |
| Tool accounts | `/api/tool-accounts` | 14 |
| Analysis | `/api/analysis` | 16 |
| Alerts | `/api/alerts` | 14 |
| Projects | `/api/projects` | 12 |
| Quota | `/api/quota` | 9 |
| Mapping rules | `/api/mapping-rules` | 8 |
| Local file system | `/api/fs` | 8 |
| Encryption keys | `/api/api/encryption-keys` | 8 |
| Return on investment | `/api/roi` | 7 |
| Request statistics | `/api/request` | 6 |
| Authentication and account | `/api/auth` | 6 |
| External identity integration | `/api/integrations/external` | 2 |
| Other endpoints | — | 87 |
| Operational endpoints | — | 10 |
| **Total** | | **426** |

### Remote machines and sessions (`/api/remote`)

Register and manage remote machines, drive remote AI sessions, web terminals and VS Code (code-server) instances, and speak the remote-agent protocol (registration, messaging, LLM proxy, usage reporting).

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/remote/agent/files/<path:filename>` | - | Serve agent source files for download during installation. |
| GET | `/api/remote/agent/install.ps1` | - | Serve the agent installation PowerShell script (Windows). |
| GET | `/api/remote/agent/install.sh` | - | Serve the agent installation shell script (Linux/macOS). |
| POST | `/api/remote/agent/message` | - | HTTP fallback for agent communication when WebSocket is not available. |
| POST | `/api/remote/agent/register` | - | Register a remote agent using a registration token. |
| GET | `/api/remote/agent/uninstall.ps1` | - | Serve the agent uninstallation PowerShell script (Windows). |
| GET | `/api/remote/agent/uninstall.sh` | - | Serve the agent uninstallation shell script (Linux/macOS). |
| GET | `/api/remote/agent/ws` | - | Deprecated WebSocket channel for agent communication (WebSocket); use HTTP polling via /api/remote/agent/message instead. |
| GET | `/api/remote/heartbeat-status` | admin_required | Get heartbeat monitor status for diagnostics. |
| POST/HEAD | `/api/remote/llm-proxy` | - | Transparent LLM API proxy for remote workspaces (empty path); forwards provider requests using stored encrypted API keys. |
| GET/POST/PUT/DELETE/HEAD | `/api/remote/llm-proxy/<path:path>` | - | Transparent LLM API proxy — catch-all path form. |
| GET | `/api/remote/machines` | - | List machines with tenant isolation. |
| DELETE | `/api/remote/machines/<machine_id>` | admin_required | Deregister a remote machine. Admin only. |
| GET | `/api/remote/machines/<machine_id>` | machine_access_required | Get details and status of a specific machine. |
| POST | `/api/remote/machines/<machine_id>/assign` | machine_admin_required | Assign a user to a machine. System admin or machine admin. |
| DELETE | `/api/remote/machines/<machine_id>/assign/<int:user_id>` | machine_admin_required | Revoke a user's access to a machine. System admin or machine admin. |
| GET | `/api/remote/machines/<machine_id>/browse` | machine_access_required | Browse the file system on a remote machine. |
| GET | `/api/remote/machines/<machine_id>/commands` | machine_access_required | Get operational commands for a specific machine. |
| POST | `/api/remote/machines/<machine_id>/create-directory` | - | Create a directory on a remote machine. |
| GET | `/api/remote/machines/<machine_id>/git/diff` | - | Get git diff for a specific file on a remote machine. |
| GET | `/api/remote/machines/<machine_id>/git/file` | - | Read a file from a remote machine. |
| GET | `/api/remote/machines/<machine_id>/git/status` | - | Get git status on a remote machine. |
| GET | `/api/remote/machines/<machine_id>/sessions` | machine_admin_required | Get list of active sessions on a machine. System admin or machine admin. |
| POST | `/api/remote/machines/<machine_id>/token/revoke` | admin_required | Revoke all agent tokens for a machine. System admin only. |
| POST | `/api/remote/machines/<machine_id>/token/rotate` | admin_required | Rotate the agent token for a machine. System admin only. |
| GET | `/api/remote/machines/<machine_id>/users` | machine_admin_required | Get list of users assigned to a machine. System admin or machine admin. |
| GET | `/api/remote/machines/available` | - | Get machines available to the current user. |
| POST | `/api/remote/machines/register` | admin_required | Generate a registration token for a new machine. |
| POST | `/api/remote/sessions` | machine_access_required | Create a new remote session on a selected machine. |
| GET | `/api/remote/sessions/<session_id>` | - | Get remote session status and output. |
| POST | `/api/remote/sessions/<session_id>/abort` | - | Abort the current in-progress request without stopping the session. |
| GET | `/api/remote/sessions/<session_id>/approvals` | - | Return the durable approval records for a remote session. |
| POST | `/api/remote/sessions/<session_id>/chat` | - | Send a message to a remote session. |
| GET | `/api/remote/sessions/<session_id>/events` | - | Return the persisted run timeline for a remote session. |
| POST | `/api/remote/sessions/<session_id>/interaction` | - | Send an interaction response from the frontend to the remote agent. |
| PUT | `/api/remote/sessions/<session_id>/model` | - | Switch the model of an active remote session. |
| POST | `/api/remote/sessions/<session_id>/pause` | - | Pause a remote session. |
| POST | `/api/remote/sessions/<session_id>/permission` | - | Send a permission response (approve/deny) from the frontend to the remote agent. |
| POST | `/api/remote/sessions/<session_id>/resume` | - | Resume a paused remote session. |
| POST | `/api/remote/sessions/<session_id>/stop` | - | Stop a remote session. |
| GET | `/api/remote/sessions/<session_id>/stream` | - | SSE: real-time stream of remote session output, formatted as claude_json. |
| POST | `/api/remote/terminal/<terminal_id>/attach` | - | Attach to existing terminal session (after browser refresh). |
| GET | `/api/remote/terminal/<terminal_id>/status` | - | Get terminal status. |
| GET | `/api/remote/terminal/<terminal_id>/ws` | - | Fallback for non-WebSocket requests to the terminal WebSocket endpoint (WebSocket). |
| POST | `/api/remote/terminal/cli/start` | machine_access_required | Start an SSH/local CLI-backed terminal session. |
| POST | `/api/remote/terminal/start` | machine_access_required | Start a web terminal on a remote machine. |
| POST | `/api/remote/terminal/stop` | machine_access_required | Stop a web terminal on a remote machine. |
| POST | `/api/remote/token_info` | - | Get token version information for an agent. |
| POST | `/api/remote/usage-report` | - | Receive an authenticated, bound, idempotent Agent usage report. |
| POST | `/api/remote/vscode/<vscode_id>/attach` | - | Re-attach to an existing code-server instance. |
| GET | `/api/remote/vscode/<vscode_id>/status` | - | Get the status of a code-server instance. |
| GET | `/api/remote/vscode/<vscode_id>/ws` | - | Fallback for non-WebSocket requests to the VS Code WebSocket endpoint (WebSocket). |
| GET/POST/PUT/DELETE/PATCH/OPTIONS/HEAD | `/api/remote/vscode/<vscode_id>/proxy/<path:path>` | - | HTTP reverse proxy to the remote code-server. |
| GET/POST/PUT/DELETE/PATCH/OPTIONS/HEAD | `/api/remote/vscode/<vscode_id>/proxy/` | - | Proxy base path (`path` empty). |
| POST | `/api/remote/vscode/start` | machine_access_required | Start a code-server instance on a remote machine. |
| POST | `/api/remote/vscode/stop` | machine_access_required | Stop a code-server instance on a remote machine. |

### Workspace (`/api/workspace`)

The built-in multi-user AI workspace: agent sessions, prompt templates, knowledge base, shares and annotations, teams, webui instances and the local LLM proxy.

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/workspace/annotations` | - | Get annotations for a session. |
| POST | `/api/workspace/annotations` | - | Create an annotation. |
| GET | `/api/workspace/config` | - | Get workspace configuration. |
| GET | `/api/workspace/instances` | - | List all active webui instances (admin only). |
| POST | `/api/workspace/instances/<int:user_id>/stop` | - | Stop a specific user's webui instance (admin only). |
| POST | `/api/workspace/instances/stop-all` | - | Stop all webui instances (admin only). |
| GET | `/api/workspace/isolation-capabilities` | auth_required | Get the workspace isolation capability contract. |
| GET | `/api/workspace/knowledge` | - | List knowledge base entries. |
| POST | `/api/workspace/knowledge` | - | Create a knowledge base entry. |
| GET | `/api/workspace/knowledge/<entry_id>` | - | Fetch one knowledge base entry (ownership-checked). |
| POST/HEAD | `/api/workspace/llm-proxy` | - | Transparent LLM proxy for local multi-user qwen-code-webui sessions (empty path). |
| GET/POST/PUT/DELETE/HEAD | `/api/workspace/llm-proxy/<path:path>` | - | Local LLM proxy — catch-all path form. |
| GET | `/api/workspace/prompts` | - | List prompt templates. |
| POST | `/api/workspace/prompts` | - | Create a new prompt template. |
| DELETE | `/api/workspace/prompts/<int:template_id>` | - | Delete a prompt template. |
| GET | `/api/workspace/prompts/<int:template_id>` | - | Get a prompt template. |
| PUT | `/api/workspace/prompts/<int:template_id>` | - | Update a prompt template. |
| POST | `/api/workspace/prompts/<int:template_id>/copy` | - | Record a prompt copy action (increments use count). |
| POST | `/api/workspace/prompts/<int:template_id>/render` | - | Render a prompt template with variables. |
| GET | `/api/workspace/prompts/categories` | - | Get prompt categories with counts. |
| GET | `/api/workspace/prompts/featured` | - | Get featured prompt templates. |
| GET | `/api/workspace/remote-projects` | - | Get user's remote workspace projects list. |
| GET | `/api/workspace/session-models` | - | Return integrated-mode qwen-code model options for the current scope context. |
| GET | `/api/workspace/sessions` | - | List agent sessions. |
| POST | `/api/workspace/sessions` | - | Create a new agent session. |
| DELETE | `/api/workspace/sessions/<session_id>` | - | Delete a session. |
| GET | `/api/workspace/sessions/<session_id>` | - | Get a session by ID. |
| POST | `/api/workspace/sessions/<session_id>/complete` | - | Mark a session as completed. |
| GET | `/api/workspace/sessions/<session_id>/messages` | - | Page through a session's messages with composite-key keyset pagination. |
| POST | `/api/workspace/sessions/<session_id>/rename` | - | Rename a session. |
| POST | `/api/workspace/sessions/<session_id>/restore` | - | Restore a historical session from agent_sessions to workspace. |
| GET | `/api/workspace/sessions/stats` | - | Get session statistics. |
| GET | `/api/workspace/shares` | - | List sessions shared with user. |
| POST | `/api/workspace/shares` | - | Share a session. |
| DELETE | `/api/workspace/shares/<share_id>` | - | Revoke a share. |
| GET | `/api/workspace/status` | - | Get workspace status including today's token and request usage for current user. |
| GET | `/api/workspace/sync/events` | - | Get sync events. |
| GET | `/api/workspace/sync/stats` | - | Get sync statistics. |
| GET | `/api/workspace/teams` | - | List user's teams. |
| POST | `/api/workspace/teams` | - | Create a new team. |
| GET | `/api/workspace/terminal-models` | - | Get all available models for terminal mode across all CLI tools. |
| GET | `/api/workspace/tools` | - | List available AI tools. |
| GET | `/api/workspace/tools/<tool_name>` | - | Get tool information. |
| GET | `/api/workspace/tools/<tool_name>/models` | - | Get available models for a tool. |
| GET | `/api/workspace/tools/health` | - | Check health of all tools. |
| GET | `/api/workspace/user-url` | - | Get the user-specific webui URL with authentication token. |

### Autonomous development (`/api/autonomous`)

Create and supervise autonomous development workflows: milestones, forks, retries, pause/resume, human verification overrides and a live event stream.

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| DELETE | `/api/autonomous/batches/<batch_id>` | auth_required | Delete an entire batch of workflows. |
| POST | `/api/autonomous/internal/events/ingest` | public_endpoint | Cross-process SSE ingest: scheduler process → this web process. |
| GET | `/api/autonomous/models` | auth_required | Get available models for a given tool and workspace type. |
| GET | `/api/autonomous/tools` | auth_required | Get the list of available agent tools. |
| GET | `/api/autonomous/workflows` | auth_required | List autonomous development workflows. |
| POST | `/api/autonomous/workflows` | auth_required | Create a new autonomous development workflow. |
| DELETE | `/api/autonomous/workflows/<workflow_id>` | auth_required | Cancel and delete a workflow. |
| GET | `/api/autonomous/workflows/<workflow_id>` | auth_required | Get a workflow by ID. |
| POST | `/api/autonomous/workflows/<workflow_id>/done` | auth_required | Mark workflow as complete, triggering merge phase. |
| GET | `/api/autonomous/workflows/<workflow_id>/events/stream` | auth_required | SSE stream for real-time workflow events. |
| POST | `/api/autonomous/workflows/<workflow_id>/extend-planning-timeout` | auth_required | Extend the planning phase timeout for a timed-out workflow. |
| GET | `/api/autonomous/workflows/<workflow_id>/forks` | auth_required | List all child workflows forked from this one. |
| POST | `/api/autonomous/workflows/<workflow_id>/milestones/<milestone_id>/cancel` | auth_required | Cancel a milestone and all subsequent milestones with user feedback. |
| GET | `/api/autonomous/workflows/<workflow_id>/milestones/<milestone_id>/diff` | auth_required | Get the code diff for a milestone's commits. |
| POST | `/api/autonomous/workflows/<workflow_id>/milestones/<milestone_id>/fork` | auth_required | Fork from a milestone, creating a new independent workflow. |
| GET | `/api/autonomous/workflows/<workflow_id>/milestones/<milestone_id>/session` | auth_required | Get the agent session associated with a milestone. |
| POST | `/api/autonomous/workflows/<workflow_id>/pause` | auth_required | Pause a running workflow. |
| GET | `/api/autonomous/workflows/<workflow_id>/pr-diff` | auth_required | Get the cumulative PR diff (head vs base) for a workflow. |
| GET | `/api/autonomous/workflows/<workflow_id>/pr-stats` | auth_required | Get lightweight cumulative PR stats for a workflow. |
| POST | `/api/autonomous/workflows/<workflow_id>/resume` | auth_required | Resume a paused workflow. |
| POST | `/api/autonomous/workflows/<workflow_id>/resume-with-feedback` | auth_required | Resume a waiting workflow with updated user feedback. |
| POST | `/api/autonomous/workflows/<workflow_id>/retry` | auth_required | Retry a failed workflow from its current phase. |
| POST | `/api/autonomous/workflows/<workflow_id>/stop` | auth_required | Gracefully stop a workflow. |
| GET | `/api/autonomous/workflows/<workflow_id>/timeline` | auth_required | Get all milestones for a workflow (timeline). |
| POST | `/api/autonomous/workflows/<workflow_id>/verification_override` | auth_required | Human override: confirm an acceptance-paused workflow (#2335 S6, #2658). |

### Single sign-on (`/api/sso`)

SSO provider registry (OAuth2/OIDC/SAML), login flows, SAML metadata/ACS/SLO endpoints, session info and identity linking.

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| POST | `/api/sso/acs/<provider_name>` | public_endpoint | Handle SAML HTTP-POST Assertion Consumer Service callbacks. |
| GET | `/api/sso/callback/<provider_name>` | public_endpoint | Handle SSO callback. |
| GET | `/api/sso/identities/<int:user_id>` | auth_required | Get SSO identities for a user. |
| DELETE | `/api/sso/identities/<int:user_id>/<provider_name>` | auth_required | Unlink an SSO identity from a user. |
| GET | `/api/sso/login/<provider_name>` | public_endpoint | Start SSO login flow. |
| GET | `/api/sso/providers` | public_endpoint | List available SSO providers. |
| POST | `/api/sso/providers` | admin_required | Register a new SSO provider (admin only). |
| DELETE | `/api/sso/providers/<provider_name>` | admin_required | Disable an SSO provider (DELETE method, deprecated - use PATCH /disable instead). |
| GET | `/api/sso/providers/<provider_name>` | admin_required | Get detailed information about a specific SSO provider. |
| PUT | `/api/sso/providers/<provider_name>` | admin_required | Update an existing SSO provider configuration. |
| PATCH | `/api/sso/providers/<provider_name>/disable` | admin_required | Disable an SSO provider (PATCH method, recommended). |
| PATCH | `/api/sso/providers/<provider_name>/enable` | admin_required | Enable an SSO provider. |
| GET | `/api/sso/providers/<provider_name>/metadata` | public_endpoint | Return SAML Service Provider metadata for an enabled SAML provider. |
| POST | `/api/sso/providers/<provider_name>/reset` | admin_required | Reset a predefined provider to its default configuration. |
| POST | `/api/sso/providers/<provider_name>/test` | admin_required | Test SSO provider connection (basic validation). |
| GET | `/api/sso/providers/export` | admin_required | Export SSO provider configurations. |
| DELETE | `/api/sso/session` | public_endpoint | Logout from SSO session. |
| GET | `/api/sso/session` | - | Get current SSO session info. |
| GET | `/api/sso/slo-redirect/<provider_name>` | public_endpoint | Handle SAML HTTP-Redirect Single Logout Service. |
| POST | `/api/sso/slo/<provider_name>` | public_endpoint | Handle SAML HTTP-POST Single Logout Service. |

### Integration and notification management (`/api/management`)

Administrative configuration for outbound integrations: Feishu, DingTalk, SMTP, generic webhook, model gateway and notification channel status.

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET/PUT/DELETE | `/api/management/dingtalk-config` | - | Get, update, or delete the DingTalk integration configuration. |
| POST | `/api/management/dingtalk-config/test` | - | Test DingTalk credentials and organisation-sync permissions. |
| DELETE | `/api/management/feishu-config` | - | Delete Feishu configuration. |
| GET | `/api/management/feishu-config` | - | Get Feishu configuration. |
| PUT | `/api/management/feishu-config` | - | Update Feishu configuration. |
| POST | `/api/management/feishu-config/test` | - | Test Feishu connection and persist verification status. |
| DELETE | `/api/management/model-gateway-config` | - | Delete the model gateway configuration. |
| GET | `/api/management/model-gateway-config` | - | Get the model gateway configuration (API key masked) with enabled status. |
| PUT | `/api/management/model-gateway-config` | - | Save (replace) the model gateway configuration. |
| POST | `/api/management/model-gateway-config/test` | - | Test the gateway connection with supplied (or stored) credentials. |
| GET | `/api/management/notification-channels/status` | - | Get the aggregated configuration status of all notification channels. |
| DELETE | `/api/management/smtp-config` | - | Delete SMTP configuration. |
| GET | `/api/management/smtp-config` | - | Get SMTP configuration. |
| PUT | `/api/management/smtp-config` | - | Update SMTP configuration. |
| POST | `/api/management/smtp-config/send-test` | - | Send a test email notification. |
| GET | `/api/management/smtp-config/statistics` | - | Get email sending statistics. |
| POST | `/api/management/smtp-config/test` | - | Test SMTP connection. |
| GET/PUT/DELETE | `/api/management/webhook-config` | - | Get, update, or delete the generic webhook configuration (secret, private-URL policy, enabled flag). |

### User and organization administration (`/api/admin`)

Platform administration: user lifecycle and quotas, Feishu/DingTalk organization sync and tenant quota tooling.

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| POST | `/api/admin/dingtalk/sync` | admin_required | Manually trigger a DingTalk organization sync. |
| GET | `/api/admin/dingtalk/sync/lock-state` | admin_required | Inspect the DingTalk org-sync advisory lock (holder pid + hold time). |
| POST | `/api/admin/dingtalk/sync/release-lock` | admin_required | Forcefully release a stuck DingTalk org-sync advisory lock. |
| POST | `/api/admin/feishu/sync` | admin_required | Manually trigger a Feishu organization sync. |
| GET | `/api/admin/feishu/sync/lock-state` | admin_required | Inspect the Feishu org-sync advisory lock (holder pid + hold time). |
| POST | `/api/admin/feishu/sync/release-lock` | admin_required | Forcefully release a stuck Feishu org-sync advisory lock. |
| POST | `/api/admin/quota/health-check` | admin_required | Check tenant quota health status. |
| GET | `/api/admin/quota/stats` | admin_required | Get quota allocation statistics for reference. |
| GET | `/api/admin/quota/usage` | admin_required | Get quota usage for all users the caller may see. |
| POST | `/api/admin/quota/validate-allocation` | admin_required | Validate if a quota allocation would exceed tenant limits. |
| GET | `/api/admin/users` | admin_required | Get all users, optionally filtered by tenant. |
| POST | `/api/admin/users` | admin_required | Create a new user. |
| DELETE | `/api/admin/users/<int:user_id>` | admin_required,same_tenant_user_required | Delete a user. |
| PUT | `/api/admin/users/<int:user_id>` | admin_required,same_tenant_user_required | Update a user. |
| PUT | `/api/admin/users/<int:user_id>/password` | admin_required,same_tenant_user_required | Update a user's password. |
| PUT | `/api/admin/users/<int:user_id>/quota` | admin_required,same_tenant_user_required | Update a user's quota. |
| POST | `/api/admin/users/<int:user_id>/reset-password` | admin_required,same_tenant_user_required | Reset user password and generate a temporary password. |
| POST | `/api/admin/users/<int:user_id>/restore` | admin_required,same_tenant_user_required | Restore a soft-deleted user. |

### Tenants (`/api/tenants`)

Multi-tenant lifecycle and settings: create/update/suspend tenants, quotas, billing periods, usage, stats and tenant sensitive keywords.

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/tenants` | platform_admin_required | List all tenants (platform admin only). |
| POST | `/api/tenants` | platform_admin_required | Create a new tenant (platform admin only). Optionally create an admin user. |
| DELETE | `/api/tenants/<int:tenant_id>` | platform_admin_required | Delete a tenant (platform admin only). |
| GET | `/api/tenants/<int:tenant_id>` | platform_admin_required | Get tenant by ID (platform admin only). |
| PUT | `/api/tenants/<int:tenant_id>` | platform_admin_required | Update tenant (platform admin only). |
| POST | `/api/tenants/<int:tenant_id>/activate` | platform_admin_required | Activate a suspended tenant (platform admin only). |
| POST | `/api/tenants/<int:tenant_id>/check-quota` | same_tenant_or_platform_admin | Check if tenant has quota available (same tenant or platform admin). |
| PUT | `/api/tenants/<int:tenant_id>/quota` | platform_admin_required | Update tenant quota (platform admin only). |
| POST | `/api/tenants/<int:tenant_id>/reset-period` | platform_admin_required | Reset billing period for a tenant (platform admin only). |
| GET | `/api/tenants/<int:tenant_id>/sensitive-keywords` | same_tenant_or_platform_admin | Get tenant sensitive keywords with pagination. |
| POST | `/api/tenants/<int:tenant_id>/sensitive-keywords` | same_tenant_or_platform_admin | Create a tenant sensitive keyword. |
| PUT | `/api/tenants/<int:tenant_id>/sensitive-keywords/<int:keyword_id>` | same_tenant_or_platform_admin | Update one tenant sensitive keyword. |
| DELETE | `/api/tenants/<int:tenant_id>/sensitive-keywords/<int:keyword_id>` | same_tenant_or_platform_admin | Delete one tenant sensitive keyword. |
| PUT | `/api/tenants/<int:tenant_id>/settings` | same_tenant_or_platform_admin | Update tenant settings (same tenant or platform admin). |
| GET | `/api/tenants/<int:tenant_id>/stats` | same_tenant_or_platform_admin | Get tenant statistics (same tenant or platform admin). |
| POST | `/api/tenants/<int:tenant_id>/suspend` | platform_admin_required | Suspend a tenant (platform admin only). |
| GET | `/api/tenants/<int:tenant_id>/usage` | same_tenant_or_platform_admin | Get tenant usage history (same tenant or platform admin). |
| GET | `/api/tenants/plans` | auth_required | Get quota configurations for all plans (authenticated users). |
| GET | `/api/tenants/slug/<slug>` | platform_admin_required | Get tenant by slug (platform admin only). |

### Compliance (`/api/compliance`)

Compliance reporting, audit analytics (patterns, anomalies, security score) and data-retention rules, status and cleanup.

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/compliance/audit/anomalies` | admin_required | Detect audit anomalies (admin only). |
| POST | `/api/compliance/audit/anomalies/status` | admin_required | Update anomaly status (admin only). |
| GET | `/api/compliance/audit/patterns` | admin_required | Analyze audit patterns (admin only). |
| GET | `/api/compliance/audit/security-score` | admin_required | Get security score (admin only). |
| GET | `/api/compliance/audit/thresholds` | admin_required | Get audit anomaly detection thresholds (admin only). |
| PUT | `/api/compliance/audit/thresholds` | admin_required | Update audit anomaly detection thresholds (admin only). |
| GET | `/api/compliance/audit/user/<int:user_id>/profile` | admin_required,same_tenant_user_required | Get user behavior profile (admin only). |
| GET | `/api/compliance/reports` | admin_required | List available report types. |
| POST | `/api/compliance/reports` | admin_required | Generate a compliance report (admin only). |
| GET | `/api/compliance/reports/<report_id>` | admin_required | Get a saved report (admin only). |
| GET | `/api/compliance/reports/saved` | admin_required | List saved reports the caller may see. |
| POST | `/api/compliance/retention/cleanup` | admin_required | Run data retention cleanup (admin only). |
| GET | `/api/compliance/retention/history` | admin_required | Get retention cleanup history (admin only). |
| GET | `/api/compliance/retention/rules` | admin_required | Get data retention rules (admin only). |
| PUT | `/api/compliance/retention/rules` | admin_required | Set a data retention rule (admin only). |
| GET | `/api/compliance/retention/status` | admin_required | Get data retention compliance status (admin only). |
| GET | `/api/compliance/retention/storage` | admin_required | Estimate storage usage (admin only). |

### Tool accounts (`/api/tool-accounts`)

Mappings between tool sender accounts and platform users, including pending, conflicting, stale and unmapped states.

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/tool-accounts` | - | Get all tool account mappings. |
| POST | `/api/tool-accounts` | - | Create a new tool account mapping. |
| DELETE | `/api/tool-accounts/<int:id>` | - | Delete a tool account mapping. |
| PUT | `/api/tool-accounts/<int:id>` | - | Update a tool account mapping. |
| POST | `/api/tool-accounts/<int:id>/resolve-conflict` | - | Resolve a conflict mapping. |
| POST | `/api/tool-accounts/<int:id>/touch-activity` | - | Update last_activity_at timestamp. |
| POST | `/api/tool-accounts/<int:id>/verify` | - | Verify a single tool account mapping. |
| GET | `/api/tool-accounts/conflicts` | - | Get all unresolved conflict mappings. |
| GET | `/api/tool-accounts/pending` | - | Get all pending (predeclared) mappings. |
| GET | `/api/tool-accounts/stale` | - | Get all stale mappings. |
| GET | `/api/tool-accounts/status-summary` | - | Get summary counts by mapping status. |
| GET | `/api/tool-accounts/unmapped` | - | Get sender_names that are not mapped to any user. |
| GET | `/api/tool-accounts/user/<int:user_id>` | - | Get tool accounts for a specific user. |
| POST | `/api/tool-accounts/user/<int:user_id>/batch` | - | Batch create tool account mappings for a user. |

### Analysis (`/api/analysis`)

Dashboard analytics over collected usage: key metrics, hourly/peak patterns, rankings, segmentation, anomalies and recommendations.

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/analytics/forecast` | any_admin_required | Get usage forecast. |
| GET | `/api/analysis/forecast` | any_admin_required | Alias route for the usage forecast. |
| GET | `/api/analysis/anomaly-detection` | - | Get anomaly detection results. |
| GET | `/api/analysis/anomaly-trend` | - | Get anomaly trend over time. |
| GET | `/api/analysis/batch` | - | Get all analysis data in a single request for better performance. |
| GET | `/api/analysis/conversation-stats` | - | Get conversation statistics. |
| GET | `/api/analysis/daily-hourly-usage` | - | Get daily and hourly usage patterns. |
| GET | `/api/analysis/data-range` | - | Get the global data range (min and max dates) for the "All" quick-range. |
| GET | `/api/analysis/hourly-usage` | - | Get hourly usage breakdown. |
| GET | `/api/analysis/key-metrics` | - | Get key metrics for the dashboard. |
| GET | `/api/analysis/peak-usage` | - | Get peak usage periods. |
| GET | `/api/analysis/recommendations` | - | Get usage optimization recommendations. |
| GET | `/api/analysis/tool-comparison` | - | Get tool comparison data. |
| GET | `/api/analysis/user-ranking` | - | Get user ranking by token usage. |
| GET | `/api/analysis/user-role-distribution` | - | Get user role distribution data. |
| GET | `/api/analysis/user-segmentation` | - | Get user segmentation data. |

### Alerts (`/api/alerts`)

The user-facing alert center: listing, read state, preferences, tenant alerts, failure queue and the SSE stream.

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/alerts` | - | Get alerts with filters. |
| DELETE | `/api/alerts/<alert_id>` | - | Delete an alert and sync to quota_alerts. |
| POST | `/api/alerts/<alert_id>/read` | - | Mark an alert as read and sync to quota_alerts. |
| GET | `/api/alerts/consistency-check` | - | Check consistency between quota_alerts and alerts tables. |
| GET | `/api/alerts/failure-queue` | - | Get alert creation failure queue status. |
| POST | `/api/alerts/failure-queue/retry` | - | Manually trigger processing of the failure queue. |
| GET | `/api/alerts/preferences` | - | Get notification preferences for current user. |
| PUT | `/api/alerts/preferences` | - | Update notification preferences for current user. |
| POST | `/api/alerts/read-all` | - | Mark all alerts as read. |
| GET | `/api/alerts/stream` | - | Server-Sent Events stream for real-time alerts. |
| POST | `/api/alerts/sync-cleanup` | - | Trigger synchronized cleanup of old alerts. |
| GET | `/api/alerts/tenant` | tenant_member_required | Get tenant-scoped alerts. |
| POST | `/api/alerts/test` | - | Create a test alert (for testing purposes). |
| GET | `/api/alerts/unread-count` | - | Get count of unread alerts. |

### Projects (`/api/projects`)

Shared projects: CRUD, daily statistics and collaborator management.

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/projects` | - | Get projects accessible by current user. |
| POST | `/api/projects` | - | Create a new project. |
| DELETE | `/api/projects/<int:project_id>` | - | Delete a project (soft delete). |
| GET | `/api/projects/<int:project_id>` | - | Get project details. |
| PUT | `/api/projects/<int:project_id>` | - | Update project information. |
| GET | `/api/projects/<int:project_id>/daily` | - | Get daily statistics for a project. |
| POST | `/api/projects/<int:project_id>/fix-permissions` | - | Fix shared project directory permissions. |
| GET | `/api/projects/<int:project_id>/users` | - | Get users collaborating on a project. |
| POST | `/api/projects/<int:project_id>/users` | - | Add a user to a shared project. |
| PUT | `/api/projects/<int:project_id>/users` | - | Batch update visible users for a shared project. |
| DELETE | `/api/projects/<int:project_id>/users/<int:target_user_id>` | - | Remove a user from a shared project. |
| GET | `/api/projects/stats` | - | Get statistics for all projects (admin only). |

### Quota (`/api/quota`)

Per-user and admin quota views and checks, quota alerts and the webui-token quota check.

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/quota/alerts` | admin_required | Get quota alerts. |
| POST | `/api/quota/alerts/<int:alert_id>/acknowledge` | admin_required | Acknowledge a quota alert. |
| GET | `/api/quota/check` | auth_required | Check if the current user has quota available. |
| POST | `/api/quota/check` | auth_required | Check if user has quota available. |
| POST | `/api/quota/check-all` | - | Manually trigger quota check for all users. |
| GET | `/api/quota/status` | auth_required | Get detailed quota status for the current user. |
| GET | `/api/quota/status/all` | admin_required | Get quota status for all users (admin only). |
| GET | `/api/quota/usage/me` | auth_required | Get detailed usage data for the current user. |
| GET | `/api/quota/webui-check` | public_endpoint | Check quota using webui token (called by webui backend middleware). |

### Mapping rules (`/api/mapping-rules`)

The rule engine that auto-maps tool accounts to users: rule CRUD, match testing and per-user default generation.

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/mapping-rules` | admin_required | Get all mapping rules. |
| POST | `/api/mapping-rules` | admin_required | Create a new mapping rule. |
| DELETE | `/api/mapping-rules/<int:id>` | admin_required | Delete a mapping rule. |
| PUT | `/api/mapping-rules/<int:id>` | admin_required | Update a mapping rule. |
| POST | `/api/mapping-rules/auto-map` | admin_required | Run auto-mapping for all unmapped accounts. |
| POST | `/api/mapping-rules/test-match` | admin_required | Test if a tool_account matches any rules. |
| GET | `/api/mapping-rules/user/<int:user_id>` | admin_required | Get mapping rules for a specific user. |
| POST | `/api/mapping-rules/user/<int:user_id>/generate-default` | admin_required | Generate default mapping rules for a user. |

### Local file system (`/api/fs`)

Bounded file access inside the user's home subtree: browse, search, upload, download and delete.

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/fs/browse` | - | Browse a directory and list subdirectories (and optionally files). |
| POST | `/api/fs/check-path` | - | Check if a path is valid and can be used for a project. |
| POST | `/api/fs/create-directory` | - | Create a directory on the local file system. |
| POST | `/api/fs/delete-file` | - | Delete a file from the user's home subtree. |
| GET | `/api/fs/download` | - | Stream a file from the user's home subtree as an attachment. |
| GET | `/api/fs/home` | - | Get user's home directory. |
| GET | `/api/fs/search` | - | Recursively search directory/file names under a home-subtree path. |
| POST | `/api/fs/upload` | - | Upload a file into the user's home subtree (personal files page). |

### Encryption keys (`/api/api/encryption-keys`)

Database encryption key management: rotation, validation, re-encryption, multi-replica sync status and the audit log.

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/api/encryption-keys` | platform_admin_required | Get metadata for all encryption keys. |
| GET | `/api/api/encryption-keys/audit-log` | platform_admin_required | Query the key-operation audit log. |
| POST | `/api/api/encryption-keys/generate-env-config` | platform_admin_required | Generate environment-variable configuration for external systems. |
| POST | `/api/api/encryption-keys/re-encrypt` | platform_admin_required | Re-encrypt all existing ciphertext with the current key. |
| POST | `/api/api/encryption-keys/re-encrypt/pre-check` | platform_admin_required | Pre-check existing ciphertext formats before a re-encryption run. |
| POST | `/api/api/encryption-keys/rotate` | platform_admin_required | Rotate the encryption key. |
| GET | `/api/api/encryption-keys/sync-status` | platform_admin_required | Get multi-replica key synchronization status. |
| POST | `/api/api/encryption-keys/validate` | platform_admin_required | Validate key format. |

### Return on investment (`/api/roi`)

ROI views built on configurable planning assumptions: totals, trends, per-tool/per-user breakdowns and cost detail.

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/roi` | - | Get ROI metrics for a period. |
| GET | `/api/roi/by-tool` | - | Get ROI breakdown by tool. |
| GET | `/api/roi/by-user` | - | Get ROI breakdown by user. |
| GET | `/api/roi/cost-breakdown` | - | Get detailed cost breakdown. |
| GET | `/api/roi/daily-costs` | - | Get daily cost data for charting. |
| GET | `/api/roi/summary` | - | Get ROI summary statistics. |
| GET | `/api/roi/trend` | - | Get ROI trend over months. |

### Request statistics (`/api/request`)

AI request counts (assistant responses): today, trend, per-tool, per-user and monthly aggregates.

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/request/by-tool` | - | Get request trend data aggregated by date and tool for charts. |
| GET | `/api/request/by-user` | - | Get request statistics grouped by user (sender_name) for today. |
| GET | `/api/request/monthly` | - | Get monthly request statistics grouped by user. |
| GET | `/api/request/today` | - | Get today's request statistics with total and by-tool breakdown. |
| GET | `/api/request/trend` | - | Get request trend data aggregated by date for charts. |
| GET | `/api/request/user/<user_name>/trend` | - | Get request trend data for a specific user. |

### Authentication and account (`/api/auth`)

Session login/logout, password change and current-user profile.

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| POST | `/api/auth/change-password` | auth_required | Change password endpoint. |
| GET | `/api/auth/check` | - | Check if user is authenticated and extend session if needed. |
| POST | `/api/auth/login` | - | Login endpoint. |
| POST | `/api/auth/logout` | public_endpoint | Logout endpoint. |
| GET | `/api/auth/me` | auth_required | Get current user info (alias for /auth/profile). |
| GET | `/api/auth/profile` | auth_required | Get current user profile. |

### External identity integration (`/api/integrations/external`)

Default-off bridge for trusted external identity issuers. Both endpoints authenticate each request by its HMAC-SHA256 signature (`X-ACE-Issuer`/`X-ACE-Time`/`X-ACE-Nonce`/`X-ACE-Signature` headers, database-backed replay protection) — no cookie, bearer token, browser origin or query identity is ever accepted, hence the `-` Auth column. The policy file (`OPENACE_EXTERNAL_IDENTITY_POLICY_FILE`) gates the whole family: without it both endpoints return `404`.

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| POST | `/api/integrations/external/capabilities` | - | Read-only probe: verifies the signed request and reports the mapped issuer identity plus whether the token exchange is available (providers and models configured and `OPENACE_EXTERNAL_TOKEN_ENABLED=1`); consumes nothing. |
| POST | `/api/integrations/external/token` | - | Exchange a signed request for a short-lived scoped proxy token for the mapped user (`openace-external-v1`); idempotent per external identity, audited, returns the token, expiry and the fixed LLM proxy path. Returns `404` unless the policy file and `OPENACE_EXTERNAL_TOKEN_ENABLED=1` are set. |

### Other endpoints

Smaller families without a dedicated section: analytics and insights, audit trails, content governance and policy, data fetch/upload pipelines, project categories, permission tasks, settings and schedulers, unmapped accounts, usage dashboards, API keys, user avatars and branding.

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/ai-agent/settings` | admin_required | Get AI agent settings (token masked). |
| PUT | `/api/ai-agent/settings` | admin_required | Update AI agent settings. |
| POST | `/api/ai-agent/settings/validate-github-token` | admin_required | Validate a GitHub PAT by calling the GitHub API. |
| GET | `/api/analytics/efficiency` | any_admin_required | Get efficiency metrics. |
| GET | `/api/analytics/export` | any_admin_required | Export analytics data. |
| GET | `/api/analytics/report` | any_admin_required | Generate a comprehensive usage report. |
| GET | `/api/api-keys` | api_key_admin_required | List all encrypted API keys (without revealing actual keys). Admin only. |
| POST | `/api/api-keys` | api_key_admin_required | Store a new encrypted API key. Admin only. |
| DELETE | `/api/api-keys/<int:key_id>` | api_key_admin_required | Delete an API key by ID. Admin only. |
| PUT | `/api/api-keys/<int:key_id>` | api_key_admin_required | Update an API key by ID. Admin only. |
| GET | `/api/audit-actions` | admin_required | Get all audit action types with categories. |
| GET | `/api/audit-logs` | admin_required | Get audit logs with filters (alias for /audit/logs). |
| GET | `/api/audit/logs` | admin_required | Get audit logs with filters. |
| GET | `/api/audit/logs/export` | admin_required | Export audit logs. |
| GET | `/api/audit/user/<int:user_id>/activity` | admin_required,same_tenant_user_required | Get activity summary for a user. |
| POST | `/api/content/check` | auth_required | Check content for sensitive information. |
| POST | `/api/content/filter/keywords` | platform_admin_required | Add a custom sensitive keyword. |
| POST | `/api/content/filter/patterns` | platform_admin_required | Add a custom content filter pattern. |
| GET | `/api/content/filter/stats` | admin_required | Get content filter statistics. |
| GET | `/api/conversation-details/<path:session_id>` | - | Get details of a conversation. |
| GET | `/api/conversation-history` | - | Get conversation history. |
| GET | `/api/conversation-timeline/<path:session_id>` | - | Get timeline of messages for a conversation. |
| GET | `/api/data-status` | auth_required | Get data status information. |
| GET | `/api/date/<date_str>` | - | Get usage for a specific date. |
| GET | `/api/feature-flags` | auth_required | Get current state of all feature flags. |
| GET | `/api/fetch` | admin_required | Fetch data from local sources. |
| POST | `/api/fetch/data` | auth_required | Trigger data collection from all sources. |
| GET | `/api/fetch/remote` | admin_required | Fetch data from remote sources. |
| GET | `/api/fetch/status` | auth_required | Get data fetch status. |
| GET | `/api/filter-rules` | admin_required | Get content filter rules with pagination and filtering. |
| POST | `/api/filter-rules` | platform_admin_required | Create a new content filter rule (idempotent). |
| DELETE | `/api/filter-rules/<int:rule_id>` | platform_admin_required | Delete a content filter rule. |
| PUT | `/api/filter-rules/<int:rule_id>` | platform_admin_required | Update a content filter rule. |
| POST | `/api/frontend-errors` | public_endpoint | Receive frontend error reports (public endpoint, no auth required). |
| GET | `/api/governance/audit-logs` | admin_required | Get audit logs with filters (full path alias for /audit/logs). |
| GET | `/api/hosts` | - | Get list of all hosts from pre-aggregated summary table and remote machines. |
| DELETE | `/api/insights/<int:report_id>` | auth_required | Delete an insights report. |
| POST | `/api/insights/generate` | auth_required | Generate or retrieve a cached insights report. |
| GET | `/api/insights/history` | auth_required | Get user's insights report history. |
| GET | `/api/mapping-stats` | admin_required | Get mapping statistics. |
| GET | `/api/messages` | - | Get messages with pagination and filters. |
| GET | `/api/messages/count` | - | Get count of messages with filters. |
| POST | `/api/migrate-quota-alerts` | - | Migrate quota_alerts to alerts table. |
| GET | `/api/migration-progress` | - | Get progress of quota_alerts to alerts migration. |
| GET | `/api/optimization/cost-trend` | - | Get cost trend for optimization analysis. |
| GET | `/api/optimization/efficiency` | - | Get efficiency analysis report. |
| GET | `/api/optimization/suggestions` | - | Get cost optimization suggestions. |
| GET | `/api/password-policy` | auth_required | Get password policy settings. |
| DELETE | `/api/permission-tasks/<task_id>` | - | Cancel a permission task. |
| GET | `/api/permission-tasks/<task_id>` | - | Get permission task status. |
| GET | `/api/policy/decisions` | admin_required | List policy decisions, filtered by session_id (and optionally request_id). |
| GET | `/api/policy/rules` | admin_required | List current (latest version) policy rules. |
| POST | `/api/policy/rules` | admin_required | Create a new policy rule (first version of a rule_key). |
| PATCH | `/api/policy/rules/<int:rule_id>/enabled` | admin_required | Toggle enabled on the current version of a rule. |
| PUT | `/api/policy/rules/<rule_key>` | admin_required | Versioned edit: supersede the current version and insert a new one. |
| GET | `/api/project-categories` | - | List all project categories. |
| POST | `/api/project-categories` | - | Create a new category (admin only). |
| DELETE | `/api/project-categories/<int:category_id>` | - | Delete a category (admin only). |
| PUT | `/api/project-categories/<int:category_id>` | - | Update a category (admin only). |
| GET | `/api/public/branding` | - | Get branding configuration for login page. |
| GET | `/api/range` | - | Get usage for a date range. |
| GET | `/api/report/my-usage` | auth_required | Get current user's usage report. |
| GET | `/api/schedulers` | - | Get status of all background schedulers. |
| GET | `/api/schedulers/data-fetch` | - | Get data fetch scheduler status. |
| GET | `/api/schedulers/quota-enforcement` | - | Get quota enforcement scheduler status. |
| GET | `/api/security-settings` | admin_required | Get security settings. |
| PUT | `/api/security-settings` | platform_admin_required | Update security settings. |
| GET | `/api/security-settings/upload-auth-status` | admin_required | Get upload authentication status. |
| GET | `/api/senders` | - | Get list of all senders (cached for 5 minutes). |
| GET | `/api/settings` | - | Get all system settings. |
| PUT | `/api/settings` | - | Update system settings. |
| GET | `/api/settings/sso-enabled` | - | Get SSO enabled status. |
| GET | `/api/summary` | - | Get summary statistics for all tools from pre-aggregated summary table. |
| POST | `/api/summary/refresh` | - | Refresh summary data from daily_messages table. |
| GET | `/api/today` | - | Get today's usage for all tools, merged by tool_name. |
| GET | `/api/tool-types` | - | Get available tool types. |
| GET | `/api/tool/<tool_name>/<int:days>` | - | Get usage for a specific tool over N days. |
| GET | `/api/tools` | - | Get list of all tools. |
| GET | `/api/trend` | - | Get usage trend data aggregated by date for charts. |
| GET | `/api/unmapped-accounts` | admin_required | Get list of unmapped tool accounts. |
| POST | `/api/unmapped-accounts/<sender_name>/map` | admin_required | Manually map an unmapped account to a user. |
| GET | `/api/unmapped-accounts/<sender_name>/suggest-mapping` | admin_required | Get suggested mapping for an unmapped account. |
| POST | `/api/upload/batch` | require_upload_auth | Upload batch data (usage and messages). |
| POST | `/api/upload/messages` | require_upload_auth | Upload message data. |
| POST | `/api/upload/usage` | require_upload_auth | Upload usage data. |
| DELETE | `/api/user/avatar` | auth_required | Delete user avatar. |
| POST | `/api/user/avatar` | auth_required | Upload user avatar. |

### Operational endpoints

Application-level health and readiness probes, Prometheus metrics, security baseline status, plus the React SPA page and static routes.

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/` | public_endpoint | Serve the React SPA for the main page. |
| GET | `/<path:path>` | public_endpoint | Serve React SPA for all other routes. |
| GET | `/health` | - | Health check endpoint for Docker and load balancers. |
| GET | `/livez` | - | Liveness probe for Kubernetes. |
| GET | `/login` | public_endpoint | Serve the React SPA for the login page. |
| GET | `/logout` | public_endpoint | Logout and serve React SPA. |
| GET | `/metrics` | - | Prometheus metrics (fallback endpoint when prometheus_flask_exporter is not available). |
| GET | `/readyz` | - | Readiness check endpoint for Kubernetes and load balancers. |
| GET | `/security-status` | - | Security baseline status endpoint for monitoring and health checks. |
| GET | `/static/claude-code-webui/<path:filename>` | public_endpoint | Serve static files. |

### Related documentation

- [API_PERMISSION_MATRIX.md](API_PERMISSION_MATRIX.md) — generated permission
  matrix for every elevated endpoint (regenerate after changing decorators).
- [../guide/REMOTE_WORKSPACE.md](../guide/REMOTE_WORKSPACE.md) — remote
  machines, sessions, terminals and VS Code workflows.
- [../guide/REMOTE_AGENT.md](../guide/REMOTE_AGENT.md) — agent installation
  and the agent protocol.
- [AUTONOMOUS_DEVELOPMENT.md](AUTONOMOUS_DEVELOPMENT.md) — autonomous
  workflow lifecycle and milestone model.
- [MODEL_GATEWAY.md](MODEL_GATEWAY.md) — model gateway configuration and the
  LLM proxy chain.
- [TOKEN_ACCOUNTING.md](TOKEN_ACCOUNTING.md) — token and request
  accounting semantics behind the usage/quota endpoints.
- [PERMISSION_MODEL.md](PERMISSION_MODEL.md) — role model and tenant-admin scope.
- [../security/API_EXCEPTIONS.md](../security/API_EXCEPTIONS.md) — routes
  deliberately exempted from authentication.

---

## 中文

本参考覆盖 Open ACE 后端暴露的全部 426 个 HTTP 端点，按路径族分组。
路径为折算 blueprint URL 前缀后的完整对外路由；路由参数使用 Flask 语法
（`<int:user_id>`、`<path:filename>` 等）。

### 基础 URL 与认证

**基础 URL。** 服务默认监听 `19888` 端口，API 根路径为
`http://<host>:19888/api`。所有业务路由都挂在 `/api` 前缀下；健康探针与
SPA 路由位于应用根路径。

**会话令牌（主要认证方式）。** 大多数端点通过会话令牌识别当前用户，
可通过以下任一方式提供：

- `session_token` Cookie（由 `POST /api/auth/login` 下发）；或
- `Authorization: Bearer <token>` 请求头。

**API Key 与服务令牌。** 少数路由组使用专用凭据替代（或叠加）会话令牌：

- `POST /api/upload/*` 要求 `X-Upload-Auth` 请求头携带 `UPLOAD_AUTH_KEY`
  的值；未配置密钥时返回 `503`，不匹配时返回 `401`。
- 远程 Agent 使用机器注册/Agent 令牌认证
  （`/api/remote/agent/*`、`/api/remote/usage-report`），见
  [../guide/REMOTE_AGENT.md](../guide/REMOTE_AGENT.md)。
- `GET /api/quota/webui-check` 接受 webui 后端中间件使用的 webui 令牌。
- `/api/api-keys` 族管理的是*加密存储在数据库中、供 LLM 代理使用的提供商
  API Key*——它们不是 Open ACE 的登录凭据。

**角色。** 提权端点要求管理员级角色（`admin`、`platform_admin`、
`tenant_admin`）。逐端点的权威清单见
[API_PERMISSION_MATRIX.md](API_PERMISSION_MATRIX.md)；角色模型见
[PERMISSION_MODEL.md](PERMISSION_MODEL.md)。

**Auth 列图例。** `Auth` 列按源码原样给出路由的权限装饰器；`-` 表示未声明
权限装饰器：

| Decorator | Meaning |
|-----------|---------|
| `public_endpoint` | 显式公共路由，无需认证。 |
| `require_upload_auth` | 上传采集端点，以 `X-Auth-Key` 头而非会话防护。 |
| `-` | 未声明权限装饰器；处理函数内部仍可能解析会话或使用专用凭据（上传密钥、Agent 令牌）。 |
| `auth_required` | 需要有效会话。 |
| `admin_required` | 需要 admin 角色（旧版平台级管理员）。 |
| `platform_admin_required` | 需要平台管理员角色。 |
| `api_key_admin_required` | 有权限管理 API Key 的租户/平台管理员。 |
| `any_admin_required` | 任意管理员角色（平台、租户或旧版管理员）。 |
| `tenant_member_required` | 需要租户成员身份。 |
| `same_tenant_user_required` | 目标用户必须属于调用者所在租户（与 `admin_required` 叠加）。 |
| `same_tenant_or_platform_admin` | 同租户管理员或平台管理员。 |
| `machine_access_required` | 系统管理员或被分配到该机器的用户。 |
| `machine_admin_required` | 系统管理员或机器管理员。 |

### 全局约定

- **内容类型。** 请求与响应均为 JSON（`application/json`），文件上传/下载、
  脚本下载、SSE/WebSocket 流以及 XML/HTML 的 SSO 元数据/ACS 端点除外。
- **错误。** 失败时返回形如 `{"error": "<message>"}` 的 JSON 体，配合常规
  状态码：`400` 输入错误、`401` 未认证、`403` 角色不足、`404` 不存在、
  `500` 内部错误。
- **分页。** 列表端点使用 `limit`/`offset` 查询参数分页（默认值多为
  50–100）。`GET /api/workspace/sessions/<session_id>/messages` 例外，使用
  复合键 keyset 分页。
- **实时通信。** SSE 与 WebSocket 端点在 Description 列内联标注（如告警流、
  自主工作流事件流、远程会话流，以及终端/VS Code/Agent 的 WebSocket 路由）。
- **行粒度。** 每个注册路由一行；个别多方法别名路由共用一行，行数与方法级计数可能不同。
- **路径。** 路径按注册原样书写。绝大多数不带尾部斜杠；个别 catch-all 代理路由保留尾部斜杠基路径（Flask strict-slashes 行为）。

### 端点族

| Family | Prefix | Endpoints |
|--------|--------|-----------|
| 远程机器与会话 | `/api/remote` | 56 |
| 工作区 | `/api/workspace` | 46 |
| 自主开发 | `/api/autonomous` | 25 |
| 单点登录 | `/api/sso` | 20 |
| 集成与通知管理 | `/api/management` | 18 |
| 用户与组织管理 | `/api/admin` | 18 |
| 租户 | `/api/tenants` | 19 |
| 合规 | `/api/compliance` | 17 |
| 工具账号 | `/api/tool-accounts` | 14 |
| 分析 | `/api/analysis` | 16 |
| 告警 | `/api/alerts` | 14 |
| 项目 | `/api/projects` | 12 |
| 配额 | `/api/quota` | 9 |
| 映射规则 | `/api/mapping-rules` | 8 |
| 本地文件系统 | `/api/fs` | 8 |
| 加密密钥 | `/api/api/encryption-keys` | 8 |
| 投入产出 | `/api/roi` | 7 |
| 请求统计 | `/api/request` | 6 |
| 认证与账号 | `/api/auth` | 6 |
| 外部身份集成 | `/api/integrations/external` | 2 |
| 其他端点 | — | 87 |
| 运维端点 | — | 10 |
| **合计** | | **426** |

### 远程机器与会话（`/api/remote`）

注册并管理远程机器，驱动远程 AI 会话、Web 终端与 VS Code（code-server）实例，并承载远程 Agent 协议（注册、消息、LLM 代理、用量上报）。

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/remote/agent/files/<path:filename>` | - | 提供 Agent 源码文件下载（安装期间使用）。 |
| GET | `/api/remote/agent/install.ps1` | - | 提供 Agent 安装 PowerShell 脚本（Windows）。 |
| GET | `/api/remote/agent/install.sh` | - | 提供 Agent 安装 Shell 脚本（Linux/macOS）。 |
| POST | `/api/remote/agent/message` | - | WebSocket 不可用时 Agent 通信的 HTTP 回退通道。 |
| POST | `/api/remote/agent/register` | - | 使用注册令牌注册远程 Agent。 |
| GET | `/api/remote/agent/uninstall.ps1` | - | 提供 Agent 卸载 PowerShell 脚本（Windows）。 |
| GET | `/api/remote/agent/uninstall.sh` | - | 提供 Agent 卸载 Shell 脚本（Linux/macOS）。 |
| GET | `/api/remote/agent/ws` | - | 已废弃的 Agent WebSocket 通道（WebSocket），请改用 /api/remote/agent/message 轮询。 |
| GET | `/api/remote/heartbeat-status` | admin_required | 获取心跳监控状态（用于诊断）。 |
| POST/HEAD | `/api/remote/llm-proxy` | - | 面向远程工作区的透明 LLM 代理（空路径），使用已存储的加密 API Key 转发模型请求。 |
| GET/POST/PUT/DELETE/HEAD | `/api/remote/llm-proxy/<path:path>` | - | 远程 LLM 代理——catch-all 路径形态。 |
| GET | `/api/remote/machines` | - | 列出远程机器（租户隔离）。 |
| DELETE | `/api/remote/machines/<machine_id>` | admin_required | 注销远程机器，仅管理员。 |
| GET | `/api/remote/machines/<machine_id>` | machine_access_required | 获取指定机器的详情与状态。 |
| POST | `/api/remote/machines/<machine_id>/assign` | machine_admin_required | 将用户分配到机器（系统管理员或机器管理员）。 |
| DELETE | `/api/remote/machines/<machine_id>/assign/<int:user_id>` | machine_admin_required | 撤销用户对机器的访问（系统管理员或机器管理员）。 |
| GET | `/api/remote/machines/<machine_id>/browse` | machine_access_required | 浏览远程机器文件系统。 |
| GET | `/api/remote/machines/<machine_id>/commands` | machine_access_required | 获取指定机器的运维命令。 |
| POST | `/api/remote/machines/<machine_id>/create-directory` | - | 在远程机器上创建目录。 |
| GET | `/api/remote/machines/<machine_id>/git/diff` | - | 获取远程机器上指定文件的 git 差异。 |
| GET | `/api/remote/machines/<machine_id>/git/file` | - | 读取远程机器上的文件。 |
| GET | `/api/remote/machines/<machine_id>/git/status` | - | 获取远程机器的 git 状态。 |
| GET | `/api/remote/machines/<machine_id>/sessions` | machine_admin_required | 获取机器上的活跃会话（系统管理员或机器管理员）。 |
| POST | `/api/remote/machines/<machine_id>/token/revoke` | admin_required | 吊销机器的全部 Agent 令牌，仅系统管理员。 |
| POST | `/api/remote/machines/<machine_id>/token/rotate` | admin_required | 轮换机器的 Agent 令牌，仅系统管理员。 |
| GET | `/api/remote/machines/<machine_id>/users` | machine_admin_required | 获取分配到机器的用户列表（系统管理员或机器管理员）。 |
| GET | `/api/remote/machines/available` | - | 获取当前用户可用的机器。 |
| POST | `/api/remote/machines/register` | admin_required | 为新机器生成注册令牌。 |
| POST | `/api/remote/sessions` | machine_access_required | 在指定机器上创建远程会话。 |
| GET | `/api/remote/sessions/<session_id>` | - | 获取远程会话状态与输出。 |
| POST | `/api/remote/sessions/<session_id>/abort` | - | 中止当前进行中的请求，但不停止会话。 |
| GET | `/api/remote/sessions/<session_id>/approvals` | - | 返回远程会话的持久化审批记录。 |
| POST | `/api/remote/sessions/<session_id>/chat` | - | 向远程会话发送消息。 |
| GET | `/api/remote/sessions/<session_id>/events` | - | 返回远程会话持久化的运行时间线。 |
| POST | `/api/remote/sessions/<session_id>/interaction` | - | 前端向远程 Agent 发送交互响应。 |
| PUT | `/api/remote/sessions/<session_id>/model` | - | 切换活跃远程会话的模型。 |
| POST | `/api/remote/sessions/<session_id>/pause` | - | 暂停远程会话。 |
| POST | `/api/remote/sessions/<session_id>/permission` | - | 前端向远程 Agent 发送权限响应（批准/拒绝）。 |
| POST | `/api/remote/sessions/<session_id>/resume` | - | 恢复已暂停的远程会话。 |
| POST | `/api/remote/sessions/<session_id>/stop` | - | 停止远程会话。 |
| GET | `/api/remote/sessions/<session_id>/stream` | - | 远程会话输出的实时 SSE 流（claude_json 格式）。 |
| POST | `/api/remote/terminal/<terminal_id>/attach` | - | 重新接入已有终端会话（浏览器刷新后）。 |
| GET | `/api/remote/terminal/<terminal_id>/status` | - | 获取终端状态。 |
| GET | `/api/remote/terminal/<terminal_id>/ws` | - | 终端 WebSocket 端点的非 WebSocket 请求回退（WebSocket）。 |
| POST | `/api/remote/terminal/cli/start` | machine_access_required | 启动基于 SSH/本地 CLI 的终端会话。 |
| POST | `/api/remote/terminal/start` | machine_access_required | 在远程机器上启动 Web 终端。 |
| POST | `/api/remote/terminal/stop` | machine_access_required | 停止远程机器上的 Web 终端。 |
| POST | `/api/remote/token_info` | - | 获取 Agent 的令牌版本信息。 |
| POST | `/api/remote/usage-report` | - | 接收经过认证、绑定且幂等的 Agent 用量上报。 |
| POST | `/api/remote/vscode/<vscode_id>/attach` | - | 重新接入已有的 code-server 实例。 |
| GET | `/api/remote/vscode/<vscode_id>/status` | - | 获取 code-server 实例状态。 |
| GET | `/api/remote/vscode/<vscode_id>/ws` | - | VS Code WebSocket 端点的非 WebSocket 请求回退（WebSocket）。 |
| GET/POST/PUT/DELETE/PATCH/OPTIONS/HEAD | `/api/remote/vscode/<vscode_id>/proxy/<path:path>` | - | 远程 code-server 的 HTTP 反向代理。 |
| GET/POST/PUT/DELETE/PATCH/OPTIONS/HEAD | `/api/remote/vscode/<vscode_id>/proxy/` | - | 代理基路径（`path` 为空）。 |
| POST | `/api/remote/vscode/start` | machine_access_required | 在远程机器上启动 code-server 实例。 |
| POST | `/api/remote/vscode/stop` | machine_access_required | 停止远程机器上的 code-server 实例。 |

### 工作区（`/api/workspace`）

内置多用户 AI 工作区：Agent 会话、提示词模板、知识库、共享与批注、团队、webui 实例及本地 LLM 代理。

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/workspace/annotations` | - | 获取会话的批注。 |
| POST | `/api/workspace/annotations` | - | 创建批注。 |
| GET | `/api/workspace/config` | - | 获取工作区配置。 |
| GET | `/api/workspace/instances` | - | 列出全部活跃 webui 实例（仅管理员）。 |
| POST | `/api/workspace/instances/<int:user_id>/stop` | - | 停止指定用户的 webui 实例（仅管理员）。 |
| POST | `/api/workspace/instances/stop-all` | - | 停止全部 webui 实例（仅管理员）。 |
| GET | `/api/workspace/isolation-capabilities` | auth_required | 获取工作区隔离能力契约。 |
| GET | `/api/workspace/knowledge` | - | 列出知识库条目。 |
| POST | `/api/workspace/knowledge` | - | 创建知识库条目。 |
| GET | `/api/workspace/knowledge/<entry_id>` | - | 获取单条知识库条目（所有权校验）。 |
| POST/HEAD | `/api/workspace/llm-proxy` | - | 面向本地多用户 qwen-code-webui 会话的透明 LLM 代理（空路径）。 |
| GET/POST/PUT/DELETE/HEAD | `/api/workspace/llm-proxy/<path:path>` | - | 本地 LLM 代理——catch-all 路径形态。 |
| GET | `/api/workspace/prompts` | - | 列出提示词模板。 |
| POST | `/api/workspace/prompts` | - | 创建提示词模板。 |
| DELETE | `/api/workspace/prompts/<int:template_id>` | - | 删除提示词模板。 |
| GET | `/api/workspace/prompts/<int:template_id>` | - | 获取提示词模板。 |
| PUT | `/api/workspace/prompts/<int:template_id>` | - | 更新提示词模板。 |
| POST | `/api/workspace/prompts/<int:template_id>/copy` | - | 记录提示词复制动作（使用计数加一）。 |
| POST | `/api/workspace/prompts/<int:template_id>/render` | - | 以变量渲染提示词模板。 |
| GET | `/api/workspace/prompts/categories` | - | 获取提示词分类及数量。 |
| GET | `/api/workspace/prompts/featured` | - | 获取精选提示词模板。 |
| GET | `/api/workspace/remote-projects` | - | 获取用户的远程工作区项目列表。 |
| GET | `/api/workspace/session-models` | - | 返回当前 scope 上下文的集成模式 qwen-code 模型选项。 |
| GET | `/api/workspace/sessions` | - | 列出 Agent 会话。 |
| POST | `/api/workspace/sessions` | - | 创建 Agent 会话。 |
| DELETE | `/api/workspace/sessions/<session_id>` | - | 删除会话。 |
| GET | `/api/workspace/sessions/<session_id>` | - | 按 ID 获取会话。 |
| POST | `/api/workspace/sessions/<session_id>/complete` | - | 将会话标记为已完成。 |
| GET | `/api/workspace/sessions/<session_id>/messages` | - | 以复合键 keyset 分页拉取会话消息。 |
| POST | `/api/workspace/sessions/<session_id>/rename` | - | 重命名会话。 |
| POST | `/api/workspace/sessions/<session_id>/restore` | - | 将历史会话从 agent_sessions 恢复到工作区。 |
| GET | `/api/workspace/sessions/stats` | - | 获取会话统计。 |
| GET | `/api/workspace/shares` | - | 列出与用户共享的会话。 |
| POST | `/api/workspace/shares` | - | 共享会话。 |
| DELETE | `/api/workspace/shares/<share_id>` | - | 撤销共享。 |
| GET | `/api/workspace/status` | - | 获取工作区状态，含当前用户今日 Token 与请求用量。 |
| GET | `/api/workspace/sync/events` | - | 获取同步事件。 |
| GET | `/api/workspace/sync/stats` | - | 获取同步统计。 |
| GET | `/api/workspace/teams` | - | 列出用户团队。 |
| POST | `/api/workspace/teams` | - | 创建团队。 |
| GET | `/api/workspace/terminal-models` | - | 获取终端模式下全部 CLI 工具的可用模型。 |
| GET | `/api/workspace/tools` | - | 列出可用 AI 工具。 |
| GET | `/api/workspace/tools/<tool_name>` | - | 获取工具信息。 |
| GET | `/api/workspace/tools/<tool_name>/models` | - | 获取工具可用模型。 |
| GET | `/api/workspace/tools/health` | - | 检查全部工具健康状态。 |
| GET | `/api/workspace/user-url` | - | 获取带认证令牌的用户专属 webui URL。 |

### 自主开发（`/api/autonomous`）

创建并监督自主开发工作流：里程碑、派生、重试、暂停/恢复、验收人工确认及实时事件流。

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| DELETE | `/api/autonomous/batches/<batch_id>` | auth_required | 删除整批工作流。 |
| POST | `/api/autonomous/internal/events/ingest` | public_endpoint | 跨进程 SSE 事件接入：调度器进程推送到本 Web 进程。 |
| GET | `/api/autonomous/models` | auth_required | 获取指定工具与工作区类型的可用模型。 |
| GET | `/api/autonomous/tools` | auth_required | 获取可用 Agent 工具列表。 |
| GET | `/api/autonomous/workflows` | auth_required | 列出自主开发工作流。 |
| POST | `/api/autonomous/workflows` | auth_required | 创建新的自主开发工作流。 |
| DELETE | `/api/autonomous/workflows/<workflow_id>` | auth_required | 取消并删除工作流。 |
| GET | `/api/autonomous/workflows/<workflow_id>` | auth_required | 按 ID 获取工作流。 |
| POST | `/api/autonomous/workflows/<workflow_id>/done` | auth_required | 标记工作流完成并触发合并阶段。 |
| GET | `/api/autonomous/workflows/<workflow_id>/events/stream` | auth_required | 工作流实时事件的 SSE 事件流。 |
| POST | `/api/autonomous/workflows/<workflow_id>/extend-planning-timeout` | auth_required | 为已超时的工作流延长规划阶段超时时间。 |
| GET | `/api/autonomous/workflows/<workflow_id>/forks` | auth_required | 列出从该工作流派生的所有子工作流。 |
| POST | `/api/autonomous/workflows/<workflow_id>/milestones/<milestone_id>/cancel` | auth_required | 附带用户反馈取消某里程碑及其后所有里程碑。 |
| GET | `/api/autonomous/workflows/<workflow_id>/milestones/<milestone_id>/diff` | auth_required | 获取里程碑提交的代码差异。 |
| POST | `/api/autonomous/workflows/<workflow_id>/milestones/<milestone_id>/fork` | auth_required | 从里程碑派生，创建新的独立工作流。 |
| GET | `/api/autonomous/workflows/<workflow_id>/milestones/<milestone_id>/session` | auth_required | 获取里程碑关联的 Agent 会话。 |
| POST | `/api/autonomous/workflows/<workflow_id>/pause` | auth_required | 暂停运行中的工作流。 |
| GET | `/api/autonomous/workflows/<workflow_id>/pr-diff` | auth_required | 获取工作流 PR 的累计差异（head 对 base）。 |
| GET | `/api/autonomous/workflows/<workflow_id>/pr-stats` | auth_required | 获取工作流 PR 的轻量累计统计。 |
| POST | `/api/autonomous/workflows/<workflow_id>/resume` | auth_required | 恢复已暂停的工作流。 |
| POST | `/api/autonomous/workflows/<workflow_id>/resume-with-feedback` | auth_required | 以更新后的用户反馈恢复等待中的工作流。 |
| POST | `/api/autonomous/workflows/<workflow_id>/retry` | auth_required | 从当前阶段重试失败的工作流。 |
| POST | `/api/autonomous/workflows/<workflow_id>/stop` | auth_required | 优雅停止工作流。 |
| GET | `/api/autonomous/workflows/<workflow_id>/timeline` | auth_required | 获取工作流的全部里程碑（时间线）。 |
| POST | `/api/autonomous/workflows/<workflow_id>/verification_override` | auth_required | 人工介入：确认处于验收暂停状态的工作流。 |

### 单点登录（`/api/sso`）

SSO 提供商注册（OAuth2/OIDC/SAML）、登录流程、SAML 元数据/ACS/SLO 端点、会话信息与身份绑定。

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| POST | `/api/sso/acs/<provider_name>` | public_endpoint | 处理 SAML HTTP-POST ACS 回调。 |
| GET | `/api/sso/callback/<provider_name>` | public_endpoint | 处理 SSO 回调。 |
| GET | `/api/sso/identities/<int:user_id>` | auth_required | 获取用户的 SSO 身份。 |
| DELETE | `/api/sso/identities/<int:user_id>/<provider_name>` | auth_required | 解绑用户的 SSO 身份。 |
| GET | `/api/sso/login/<provider_name>` | public_endpoint | 发起 SSO 登录流程。 |
| GET | `/api/sso/providers` | public_endpoint | 列出可用的 SSO 提供商。 |
| POST | `/api/sso/providers` | admin_required | 注册新的 SSO 提供商（仅管理员）。 |
| DELETE | `/api/sso/providers/<provider_name>` | admin_required | 停用 SSO 提供商（DELETE 方式已废弃，请改用 PATCH /disable）。 |
| GET | `/api/sso/providers/<provider_name>` | admin_required | 获取指定 SSO 提供商详情。 |
| PUT | `/api/sso/providers/<provider_name>` | admin_required | 更新已有 SSO 提供商配置。 |
| PATCH | `/api/sso/providers/<provider_name>/disable` | admin_required | 停用 SSO 提供商（PATCH 方式，推荐）。 |
| PATCH | `/api/sso/providers/<provider_name>/enable` | admin_required | 启用 SSO 提供商。 |
| GET | `/api/sso/providers/<provider_name>/metadata` | public_endpoint | 返回已启用 SAML 提供商的 SP 元数据。 |
| POST | `/api/sso/providers/<provider_name>/reset` | admin_required | 将预定义提供商重置为默认配置。 |
| POST | `/api/sso/providers/<provider_name>/test` | admin_required | 测试 SSO 提供商连接（基础校验）。 |
| GET | `/api/sso/providers/export` | admin_required | 导出 SSO 提供商配置。 |
| DELETE | `/api/sso/session` | public_endpoint | 登出 SSO 会话。 |
| GET | `/api/sso/session` | - | 获取当前 SSO 会话信息。 |
| GET | `/api/sso/slo-redirect/<provider_name>` | public_endpoint | 处理 SAML HTTP-Redirect 单点登出。 |
| POST | `/api/sso/slo/<provider_name>` | public_endpoint | 处理 SAML HTTP-POST 单点登出。 |

### 集成与通知管理（`/api/management`）

出站集成的管理配置：飞书、钉钉、SMTP、通用 Webhook、模型网关及通知渠道状态。

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET/PUT/DELETE | `/api/management/dingtalk-config` | - | 获取、更新或删除钉钉集成配置。 |
| POST | `/api/management/dingtalk-config/test` | - | 测试钉钉凭据及组织同步权限。 |
| DELETE | `/api/management/feishu-config` | - | 删除飞书配置。 |
| GET | `/api/management/feishu-config` | - | 获取飞书配置。 |
| PUT | `/api/management/feishu-config` | - | 更新飞书配置。 |
| POST | `/api/management/feishu-config/test` | - | 测试飞书连接并保存校验状态。 |
| DELETE | `/api/management/model-gateway-config` | - | 删除模型网关配置。 |
| GET | `/api/management/model-gateway-config` | - | 获取模型网关配置（API Key 已脱敏）及启用状态。 |
| PUT | `/api/management/model-gateway-config` | - | 保存（整体替换）模型网关配置。 |
| POST | `/api/management/model-gateway-config/test` | - | 使用给定（或已存储）凭据测试网关连接。 |
| GET | `/api/management/notification-channels/status` | - | 获取全部通知渠道的聚合配置状态。 |
| DELETE | `/api/management/smtp-config` | - | 删除 SMTP 配置。 |
| GET | `/api/management/smtp-config` | - | 获取 SMTP 配置。 |
| PUT | `/api/management/smtp-config` | - | 更新 SMTP 配置。 |
| POST | `/api/management/smtp-config/send-test` | - | 发送测试邮件通知。 |
| GET | `/api/management/smtp-config/statistics` | - | 获取邮件发送统计。 |
| POST | `/api/management/smtp-config/test` | - | 测试 SMTP 连接。 |
| GET/PUT/DELETE | `/api/management/webhook-config` | - | 获取、更新或删除通用 Webhook 配置（密钥、私网地址策略、启用开关）。 |

### 用户与组织管理（`/api/admin`）

平台管理：用户生命周期与配额、飞书/钉钉组织同步及租户配额工具。

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| POST | `/api/admin/dingtalk/sync` | admin_required | 手动触发钉钉组织架构同步。 |
| GET | `/api/admin/dingtalk/sync/lock-state` | admin_required | 查看钉钉组织同步咨询锁状态（持有者 PID 与持锁时长）。 |
| POST | `/api/admin/dingtalk/sync/release-lock` | admin_required | 强制释放卡死的钉钉组织同步咨询锁。 |
| POST | `/api/admin/feishu/sync` | admin_required | 手动触发飞书组织架构同步。 |
| GET | `/api/admin/feishu/sync/lock-state` | admin_required | 查看飞书组织同步咨询锁状态（持有者 PID 与持锁时长）。 |
| POST | `/api/admin/feishu/sync/release-lock` | admin_required | 强制释放卡死的飞书组织同步咨询锁。 |
| POST | `/api/admin/quota/health-check` | admin_required | 检查租户配额健康状态。 |
| GET | `/api/admin/quota/stats` | admin_required | 获取配额分配统计（供参考）。 |
| GET | `/api/admin/quota/usage` | admin_required | 获取调用者可见范围内所有用户的配额使用情况。 |
| POST | `/api/admin/quota/validate-allocation` | admin_required | 校验某个配额分配方案是否会超出租户上限。 |
| GET | `/api/admin/users` | admin_required | 获取所有用户，可按租户过滤。 |
| POST | `/api/admin/users` | admin_required | 创建新用户。 |
| DELETE | `/api/admin/users/<int:user_id>` | admin_required,same_tenant_user_required | 删除用户。 |
| PUT | `/api/admin/users/<int:user_id>` | admin_required,same_tenant_user_required | 更新用户信息。 |
| PUT | `/api/admin/users/<int:user_id>/password` | admin_required,same_tenant_user_required | 更新用户密码。 |
| PUT | `/api/admin/users/<int:user_id>/quota` | admin_required,same_tenant_user_required | 更新用户配额。 |
| POST | `/api/admin/users/<int:user_id>/reset-password` | admin_required,same_tenant_user_required | 重置用户密码并生成临时密码。 |
| POST | `/api/admin/users/<int:user_id>/restore` | admin_required,same_tenant_user_required | 恢复软删除的用户。 |

### 租户（`/api/tenants`）

多租户生命周期与设置：创建/更新/停用租户、配额、计费周期、用量、统计及租户敏感词。

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/tenants` | platform_admin_required | 列出全部租户（仅平台管理员）。 |
| POST | `/api/tenants` | platform_admin_required | 创建租户（仅平台管理员），可选同时创建管理员用户。 |
| DELETE | `/api/tenants/<int:tenant_id>` | platform_admin_required | 删除租户（仅平台管理员）。 |
| GET | `/api/tenants/<int:tenant_id>` | platform_admin_required | 按 ID 获取租户（仅平台管理员）。 |
| PUT | `/api/tenants/<int:tenant_id>` | platform_admin_required | 更新租户（仅平台管理员）。 |
| POST | `/api/tenants/<int:tenant_id>/activate` | platform_admin_required | 激活已暂停的租户（仅平台管理员）。 |
| POST | `/api/tenants/<int:tenant_id>/check-quota` | same_tenant_or_platform_admin | 检查租户是否有可用配额（同租户或平台管理员）。 |
| PUT | `/api/tenants/<int:tenant_id>/quota` | platform_admin_required | 更新租户配额（仅平台管理员）。 |
| POST | `/api/tenants/<int:tenant_id>/reset-period` | platform_admin_required | 重置租户计费周期（仅平台管理员）。 |
| GET | `/api/tenants/<int:tenant_id>/sensitive-keywords` | same_tenant_or_platform_admin | 分页获取租户敏感词。 |
| POST | `/api/tenants/<int:tenant_id>/sensitive-keywords` | same_tenant_or_platform_admin | 新增租户敏感词。 |
| PUT | `/api/tenants/<int:tenant_id>/sensitive-keywords/<int:keyword_id>` | same_tenant_or_platform_admin | 更新一条租户敏感词。 |
| DELETE | `/api/tenants/<int:tenant_id>/sensitive-keywords/<int:keyword_id>` | same_tenant_or_platform_admin | 删除一条租户敏感词。 |
| PUT | `/api/tenants/<int:tenant_id>/settings` | same_tenant_or_platform_admin | 更新租户设置（同租户或平台管理员）。 |
| GET | `/api/tenants/<int:tenant_id>/stats` | same_tenant_or_platform_admin | 获取租户统计（同租户或平台管理员）。 |
| POST | `/api/tenants/<int:tenant_id>/suspend` | platform_admin_required | 暂停租户（仅平台管理员）。 |
| GET | `/api/tenants/<int:tenant_id>/usage` | same_tenant_or_platform_admin | 获取租户用量历史（同租户或平台管理员）。 |
| GET | `/api/tenants/plans` | auth_required | 获取全部套餐的配额配置（需登录）。 |
| GET | `/api/tenants/slug/<slug>` | platform_admin_required | 按 slug 获取租户（仅平台管理员）。 |

### 合规（`/api/compliance`）

合规报告、审计分析（模式、异常、安全评分）以及数据保留规则、状态与清理。

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/compliance/audit/anomalies` | admin_required | 检测审计异常（仅管理员）。 |
| POST | `/api/compliance/audit/anomalies/status` | admin_required | 更新异常状态（仅管理员）。 |
| GET | `/api/compliance/audit/patterns` | admin_required | 分析审计模式（仅管理员）。 |
| GET | `/api/compliance/audit/security-score` | admin_required | 获取安全评分（仅管理员）。 |
| GET | `/api/compliance/audit/thresholds` | admin_required | 获取审计异常检测阈值（仅管理员）。 |
| PUT | `/api/compliance/audit/thresholds` | admin_required | 更新审计异常检测阈值（仅管理员）。 |
| GET | `/api/compliance/audit/user/<int:user_id>/profile` | admin_required,same_tenant_user_required | 获取用户行为画像（仅管理员）。 |
| GET | `/api/compliance/reports` | admin_required | 列出可用的报告类型。 |
| POST | `/api/compliance/reports` | admin_required | 生成合规报告（仅管理员）。 |
| GET | `/api/compliance/reports/<report_id>` | admin_required | 获取已保存的报告（仅管理员）。 |
| GET | `/api/compliance/reports/saved` | admin_required | 列出调用者可见的已保存报告。 |
| POST | `/api/compliance/retention/cleanup` | admin_required | 执行数据保留清理（仅管理员）。 |
| GET | `/api/compliance/retention/history` | admin_required | 获取保留清理历史（仅管理员）。 |
| GET | `/api/compliance/retention/rules` | admin_required | 获取数据保留规则（仅管理员）。 |
| PUT | `/api/compliance/retention/rules` | admin_required | 设置数据保留规则（仅管理员）。 |
| GET | `/api/compliance/retention/status` | admin_required | 获取数据保留合规状态（仅管理员）。 |
| GET | `/api/compliance/retention/storage` | admin_required | 估算存储占用（仅管理员）。 |

### 工具账号（`/api/tool-accounts`）

工具发送账号与平台用户之间的映射，含待定、冲突、过期与未映射状态。

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/tool-accounts` | - | 获取全部工具账号映射。 |
| POST | `/api/tool-accounts` | - | 创建工具账号映射。 |
| DELETE | `/api/tool-accounts/<int:id>` | - | 删除工具账号映射。 |
| PUT | `/api/tool-accounts/<int:id>` | - | 更新工具账号映射。 |
| POST | `/api/tool-accounts/<int:id>/resolve-conflict` | - | 解决冲突映射。 |
| POST | `/api/tool-accounts/<int:id>/touch-activity` | - | 更新 last_activity_at 时间戳。 |
| POST | `/api/tool-accounts/<int:id>/verify` | - | 校验单条工具账号映射。 |
| GET | `/api/tool-accounts/conflicts` | - | 获取全部未解决冲突映射。 |
| GET | `/api/tool-accounts/pending` | - | 获取全部待定（预声明）映射。 |
| GET | `/api/tool-accounts/stale` | - | 获取全部过期映射。 |
| GET | `/api/tool-accounts/status-summary` | - | 获取按映射状态汇总的数量。 |
| GET | `/api/tool-accounts/unmapped` | - | 获取尚未映射到任何用户的 sender_name。 |
| GET | `/api/tool-accounts/user/<int:user_id>` | - | 获取指定用户的工具账号。 |
| POST | `/api/tool-accounts/user/<int:user_id>/batch` | - | 为用户批量创建工具账号映射。 |

### 分析（`/api/analysis`）

基于已采集用量的看板分析：关键指标、小时/高峰模式、排行、分段、异常与建议。

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/analytics/forecast` | any_admin_required | 获取用量预测。 |
| GET | `/api/analysis/forecast` | any_admin_required | 用量预测的别名路由。 |
| GET | `/api/analysis/anomaly-detection` | - | 获取异常检测结果。 |
| GET | `/api/analysis/anomaly-trend` | - | 获取异常随时间的变化趋势。 |
| GET | `/api/analysis/batch` | - | 一次请求返回全部分析数据以提升性能。 |
| GET | `/api/analysis/conversation-stats` | - | 获取会话统计数据。 |
| GET | `/api/analysis/daily-hourly-usage` | - | 获取按天与按小时的用量分布模式。 |
| GET | `/api/analysis/data-range` | - | 获取全局数据日期范围（最早与最晚日期），用于“全部”快捷区间。 |
| GET | `/api/analysis/hourly-usage` | - | 获取按小时的用量明细。 |
| GET | `/api/analysis/key-metrics` | - | 获取仪表盘关键指标。 |
| GET | `/api/analysis/peak-usage` | - | 获取用量高峰时段。 |
| GET | `/api/analysis/recommendations` | - | 获取用量优化建议。 |
| GET | `/api/analysis/tool-comparison` | - | 获取工具对比数据。 |
| GET | `/api/analysis/user-ranking` | - | 获取按 Token 用量的用户排行。 |
| GET | `/api/analysis/user-role-distribution` | - | 获取用户角色分布数据。 |
| GET | `/api/analysis/user-segmentation` | - | 获取用户分段数据。 |

### 告警（`/api/alerts`）

面向用户的告警中心：列表、已读状态、偏好、租户告警、失败队列及 SSE 流。

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/alerts` | - | 按条件筛选获取告警列表。 |
| DELETE | `/api/alerts/<alert_id>` | - | 删除告警并同步 quota_alerts 表。 |
| POST | `/api/alerts/<alert_id>/read` | - | 将告警标记为已读并同步 quota_alerts 表。 |
| GET | `/api/alerts/consistency-check` | - | 检查 quota_alerts 与 alerts 两张表的一致性。 |
| GET | `/api/alerts/failure-queue` | - | 获取告警创建失败队列状态。 |
| POST | `/api/alerts/failure-queue/retry` | - | 手动触发失败队列的处理。 |
| GET | `/api/alerts/preferences` | - | 获取当前用户的通知偏好。 |
| PUT | `/api/alerts/preferences` | - | 更新当前用户的通知偏好。 |
| POST | `/api/alerts/read-all` | - | 将全部告警标记为已读。 |
| GET | `/api/alerts/stream` | - | 实时告警的 Server-Sent Events 流（SSE）。 |
| POST | `/api/alerts/sync-cleanup` | - | 触发旧告警的同步清理。 |
| GET | `/api/alerts/tenant` | tenant_member_required | 获取租户范围内的告警。 |
| POST | `/api/alerts/test` | - | 创建测试告警（用于测试）。 |
| GET | `/api/alerts/unread-count` | - | 获取未读告警数量。 |

### 项目（`/api/projects`）

共享项目：增删改查、每日统计与协作者管理。

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/projects` | - | 获取当前用户可访问的项目。 |
| POST | `/api/projects` | - | 创建项目。 |
| DELETE | `/api/projects/<int:project_id>` | - | 删除项目（软删除）。 |
| GET | `/api/projects/<int:project_id>` | - | 获取项目详情。 |
| PUT | `/api/projects/<int:project_id>` | - | 更新项目信息。 |
| GET | `/api/projects/<int:project_id>/daily` | - | 获取项目的每日统计。 |
| POST | `/api/projects/<int:project_id>/fix-permissions` | - | 修复共享项目目录权限。 |
| GET | `/api/projects/<int:project_id>/users` | - | 获取项目协作用户。 |
| POST | `/api/projects/<int:project_id>/users` | - | 将用户加入共享项目。 |
| PUT | `/api/projects/<int:project_id>/users` | - | 批量更新共享项目的可见用户。 |
| DELETE | `/api/projects/<int:project_id>/users/<int:target_user_id>` | - | 将用户移出共享项目。 |
| GET | `/api/projects/stats` | - | 获取全部项目统计（仅管理员）。 |

### 配额（`/api/quota`）

用户与管理员视角的配额查询与检查、配额告警及 webui 令牌配额检查。

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/quota/alerts` | admin_required | 获取配额告警。 |
| POST | `/api/quota/alerts/<int:alert_id>/acknowledge` | admin_required | 确认（已读）配额告警。 |
| GET | `/api/quota/check` | auth_required | 检查当前用户是否有可用配额。 |
| POST | `/api/quota/check` | auth_required | 检查用户是否有可用配额。 |
| POST | `/api/quota/check-all` | - | 手动触发全体用户配额检查。 |
| GET | `/api/quota/status` | auth_required | 获取当前用户的详细配额状态。 |
| GET | `/api/quota/status/all` | admin_required | 获取全部用户的配额状态（仅管理员）。 |
| GET | `/api/quota/usage/me` | auth_required | 获取当前用户的详细用量数据。 |
| GET | `/api/quota/webui-check` | public_endpoint | 使用 webui 令牌检查配额（由 webui 后端中间件调用）。 |

### 映射规则（`/api/mapping-rules`）

将工具账号自动映射到用户的规则引擎：规则增删改查、匹配测试及按用户生成默认规则。

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/mapping-rules` | admin_required | 获取全部映射规则。 |
| POST | `/api/mapping-rules` | admin_required | 创建映射规则。 |
| DELETE | `/api/mapping-rules/<int:id>` | admin_required | 删除映射规则。 |
| PUT | `/api/mapping-rules/<int:id>` | admin_required | 更新映射规则。 |
| POST | `/api/mapping-rules/auto-map` | admin_required | 对全部未映射账号执行自动映射。 |
| POST | `/api/mapping-rules/test-match` | admin_required | 测试 tool_account 是否命中任一规则。 |
| GET | `/api/mapping-rules/user/<int:user_id>` | admin_required | 获取指定用户的映射规则。 |
| POST | `/api/mapping-rules/user/<int:user_id>/generate-default` | admin_required | 为用户生成默认映射规则。 |

### 本地文件系统（`/api/fs`）

用户主目录子树内的受限文件访问：浏览、搜索、上传、下载与删除。

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/fs/browse` | - | 浏览目录并列出子目录（可选包含文件）。 |
| POST | `/api/fs/check-path` | - | 校验路径是否合法、可用于项目。 |
| POST | `/api/fs/create-directory` | - | 在本地文件系统创建目录。 |
| POST | `/api/fs/delete-file` | - | 删除用户主目录子树内的文件。 |
| GET | `/api/fs/download` | - | 以附件形式下载用户主目录子树内的文件。 |
| GET | `/api/fs/home` | - | 获取用户主目录。 |
| GET | `/api/fs/search` | - | 在主目录子树内递归搜索目录/文件名。 |
| POST | `/api/fs/upload` | - | 上传文件到用户主目录子树（个人文件页）。 |

### 加密密钥（`/api/api/encryption-keys`）

数据库加密密钥管理：轮换、校验、重加密、多副本同步状态与审计日志。

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/api/encryption-keys` | platform_admin_required | 获取所有加密密钥的元数据。 |
| GET | `/api/api/encryption-keys/audit-log` | platform_admin_required | 查询密钥操作审计日志。 |
| POST | `/api/api/encryption-keys/generate-env-config` | platform_admin_required | 生成供外部系统使用的环境变量配置。 |
| POST | `/api/api/encryption-keys/re-encrypt` | platform_admin_required | 使用当前密钥重新加密全部存量密文。 |
| POST | `/api/api/encryption-keys/re-encrypt/pre-check` | platform_admin_required | re-encrypt 执行前预检查存量密文格式。 |
| POST | `/api/api/encryption-keys/rotate` | platform_admin_required | 执行密钥轮换。 |
| GET | `/api/api/encryption-keys/sync-status` | platform_admin_required | 获取多副本密钥同步状态。 |
| POST | `/api/api/encryption-keys/validate` | platform_admin_required | 校验密钥格式。 |

### 投入产出（`/api/roi`）

基于可配置规划假设的投入产出视图：总量、趋势、按工具/按用户分解及成本明细。

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/roi` | - | 获取某时间段的 ROI 指标。 |
| GET | `/api/roi/by-tool` | - | 获取按工具的 ROI 分解。 |
| GET | `/api/roi/by-user` | - | 获取按用户的 ROI 分解。 |
| GET | `/api/roi/cost-breakdown` | - | 获取详细成本分解。 |
| GET | `/api/roi/daily-costs` | - | 获取用于图表的每日成本数据。 |
| GET | `/api/roi/summary` | - | 获取 ROI 汇总统计。 |
| GET | `/api/roi/trend` | - | 获取按月的 ROI 趋势。 |

### 请求统计（`/api/request`）

AI 请求数（assistant 响应）统计：今日、趋势、按工具、按用户与按月聚合。

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/request/by-tool` | - | 获取按日期与工具聚合的请求趋势（用于图表）。 |
| GET | `/api/request/by-user` | - | 获取今日按用户（sender_name）分组的请求统计。 |
| GET | `/api/request/monthly` | - | 获取按用户分组的月度请求统计。 |
| GET | `/api/request/today` | - | 获取今日请求统计（总量与按工具分解）。 |
| GET | `/api/request/trend` | - | 获取按日期聚合的请求趋势（用于图表）。 |
| GET | `/api/request/user/<user_name>/trend` | - | 获取指定用户的请求趋势。 |

### 认证与账号（`/api/auth`）

会话登录/登出、密码修改与当前用户资料。

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| POST | `/api/auth/change-password` | auth_required | 修改当前用户密码。 |
| GET | `/api/auth/check` | - | 检查登录态，必要时延长会话。 |
| POST | `/api/auth/login` | - | 用户登录。 |
| POST | `/api/auth/logout` | public_endpoint | 用户登出。 |
| GET | `/api/auth/me` | auth_required | 获取当前用户信息（/auth/profile 的别名）。 |
| GET | `/api/auth/profile` | auth_required | 获取当前用户资料。 |

### 外部身份集成（`/api/integrations/external`）

默认关闭的可信外部身份发行方接入桥。两个端点都按每请求的 HMAC-SHA256 签名认证（`X-ACE-Issuer`/`X-ACE-Time`/`X-ACE-Nonce`/`X-ACE-Signature` 请求头，数据库防重放）——不接受任何 Cookie、Bearer 令牌、浏览器 Origin 或查询参数身份，因此 Auth 列为 `-`。策略文件（`OPENACE_EXTERNAL_IDENTITY_POLICY_FILE`）门控整个族：未配置时两个端点均返回 `404`。

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| POST | `/api/integrations/external/capabilities` | - | 只读探测：验证签名请求，返回映射的发行方身份，以及令牌交换是否可用（已配置 providers 与 models 且 `OPENACE_EXTERNAL_TOKEN_ENABLED=1`）；不消耗任何状态。 |
| POST | `/api/integrations/external/token` | - | 将签名请求交换为映射用户的短期限定范围代理令牌（`openace-external-v1`）；按外部身份幂等、写入审计，返回令牌、过期时间与固定的 LLM 代理路径。未配置策略文件或 `OPENACE_EXTERNAL_TOKEN_ENABLED=1` 时返回 `404`。 |

### 其他端点

未单列的小族：分析与洞察、审计、内容治理与策略、数据拉取/上传管道、项目分类、权限任务、设置与调度器、未映射账号、用量看板、API Key、用户头像与品牌配置。

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/ai-agent/settings` | admin_required | 获取 AI Agent 设置（令牌已脱敏）。 |
| PUT | `/api/ai-agent/settings` | admin_required | 更新 AI Agent 设置。 |
| POST | `/api/ai-agent/settings/validate-github-token` | admin_required | 调用 GitHub API 校验 GitHub 个人访问令牌。 |
| GET | `/api/analytics/efficiency` | any_admin_required | 获取效率指标。 |
| GET | `/api/analytics/export` | any_admin_required | 导出分析数据。 |
| GET | `/api/analytics/report` | any_admin_required | 生成综合用量报告。 |
| GET | `/api/api-keys` | api_key_admin_required | 列出全部加密存储的 API Key（不回显明文），仅管理员。 |
| POST | `/api/api-keys` | api_key_admin_required | 新增加密存储的 API Key，仅管理员。 |
| DELETE | `/api/api-keys/<int:key_id>` | api_key_admin_required | 按 ID 删除 API Key，仅管理员。 |
| PUT | `/api/api-keys/<int:key_id>` | api_key_admin_required | 按 ID 更新 API Key，仅管理员。 |
| GET | `/api/audit-actions` | admin_required | 获取所有审计动作类型及其分类。 |
| GET | `/api/audit-logs` | admin_required | 按条件筛选获取审计日志（/audit/logs 的别名）。 |
| GET | `/api/audit/logs` | admin_required | 按条件筛选获取审计日志。 |
| GET | `/api/audit/logs/export` | admin_required | 导出审计日志。 |
| GET | `/api/audit/user/<int:user_id>/activity` | admin_required,same_tenant_user_required | 获取用户活动摘要。 |
| POST | `/api/content/check` | auth_required | 检查内容是否包含敏感信息。 |
| POST | `/api/content/filter/keywords` | platform_admin_required | 新增自定义敏感词。 |
| POST | `/api/content/filter/patterns` | platform_admin_required | 新增自定义内容过滤模式。 |
| GET | `/api/content/filter/stats` | admin_required | 获取内容过滤统计。 |
| GET | `/api/conversation-details/<path:session_id>` | - | 获取会话详情。 |
| GET | `/api/conversation-history` | - | 获取会话历史。 |
| GET | `/api/conversation-timeline/<path:session_id>` | - | 获取会话的消息时间线。 |
| GET | `/api/data-status` | auth_required | 获取数据状态信息。 |
| GET | `/api/date/<date_str>` | - | 获取指定日期的用量。 |
| GET | `/api/feature-flags` | auth_required | 获取所有特性开关的当前状态。 |
| GET | `/api/fetch` | admin_required | 从本地数据源拉取数据。 |
| POST | `/api/fetch/data` | auth_required | 触发从所有数据源采集数据。 |
| GET | `/api/fetch/remote` | admin_required | 从远程数据源拉取数据。 |
| GET | `/api/fetch/status` | auth_required | 获取数据拉取状态。 |
| GET | `/api/filter-rules` | admin_required | 分页且带过滤地获取内容过滤规则。 |
| POST | `/api/filter-rules` | platform_admin_required | 创建内容过滤规则（幂等）。 |
| DELETE | `/api/filter-rules/<int:rule_id>` | platform_admin_required | 删除内容过滤规则。 |
| PUT | `/api/filter-rules/<int:rule_id>` | platform_admin_required | 更新内容过滤规则。 |
| POST | `/api/frontend-errors` | public_endpoint | 接收前端错误上报（公共端点，无需认证）。 |
| GET | `/api/governance/audit-logs` | admin_required | 按条件筛选获取审计日志（/audit/logs 的完整路径别名）。 |
| GET | `/api/hosts` | - | 从预聚合汇总表与远程机器获取全部主机列表。 |
| DELETE | `/api/insights/<int:report_id>` | auth_required | 删除洞察报告。 |
| POST | `/api/insights/generate` | auth_required | 生成或获取缓存的洞察报告。 |
| GET | `/api/insights/history` | auth_required | 获取用户的洞察报告历史。 |
| GET | `/api/mapping-stats` | admin_required | 获取映射统计。 |
| GET | `/api/messages` | - | 分页且带过滤地获取消息。 |
| GET | `/api/messages/count` | - | 按条件统计消息数量。 |
| POST | `/api/migrate-quota-alerts` | - | 将 quota_alerts 迁移到 alerts 表。 |
| GET | `/api/migration-progress` | - | 获取 quota_alerts 到 alerts 的迁移进度。 |
| GET | `/api/optimization/cost-trend` | - | 获取用于优化分析的成本趋势。 |
| GET | `/api/optimization/efficiency` | - | 获取效率分析报告。 |
| GET | `/api/optimization/suggestions` | - | 获取成本优化建议。 |
| GET | `/api/password-policy` | auth_required | 获取密码策略设置。 |
| DELETE | `/api/permission-tasks/<task_id>` | - | 取消权限任务。 |
| GET | `/api/permission-tasks/<task_id>` | - | 获取权限任务状态。 |
| GET | `/api/policy/decisions` | admin_required | 按 session_id（及可选 request_id）过滤列出策略决策。 |
| GET | `/api/policy/rules` | admin_required | 列出当前（最新版本）的策略规则。 |
| POST | `/api/policy/rules` | admin_required | 创建策略规则（某 rule_key 的首个版本）。 |
| PATCH | `/api/policy/rules/<int:rule_id>/enabled` | admin_required | 切换规则当前版本的启用状态。 |
| PUT | `/api/policy/rules/<rule_key>` | admin_required | 版本化编辑：取代当前版本并插入新版本。 |
| GET | `/api/project-categories` | - | 列出全部项目分类。 |
| POST | `/api/project-categories` | - | 创建项目分类（仅管理员）。 |
| DELETE | `/api/project-categories/<int:category_id>` | - | 删除项目分类（仅管理员）。 |
| PUT | `/api/project-categories/<int:category_id>` | - | 更新项目分类（仅管理员）。 |
| GET | `/api/public/branding` | - | 获取登录页品牌配置。 |
| GET | `/api/range` | - | 获取日期区间用量。 |
| GET | `/api/report/my-usage` | auth_required | 获取当前用户的用量报告。 |
| GET | `/api/schedulers` | - | 获取所有后台调度器状态。 |
| GET | `/api/schedulers/data-fetch` | - | 获取数据拉取调度器状态。 |
| GET | `/api/schedulers/quota-enforcement` | - | 获取配额执行调度器状态。 |
| GET | `/api/security-settings` | admin_required | 获取安全设置。 |
| PUT | `/api/security-settings` | platform_admin_required | 更新安全设置。 |
| GET | `/api/security-settings/upload-auth-status` | admin_required | 获取上传认证状态。 |
| GET | `/api/senders` | - | 获取全部发送者列表（缓存 5 分钟）。 |
| GET | `/api/settings` | - | 获取全部系统设置。 |
| PUT | `/api/settings` | - | 更新系统设置。 |
| GET | `/api/settings/sso-enabled` | - | 获取 SSO 启用状态。 |
| GET | `/api/summary` | - | 从预聚合汇总表获取全工具汇总统计。 |
| POST | `/api/summary/refresh` | - | 从 daily_messages 表刷新汇总数据。 |
| GET | `/api/today` | - | 获取今日全工具用量（按 tool_name 合并）。 |
| GET | `/api/tool-types` | - | 获取可用工具类型。 |
| GET | `/api/tool/<tool_name>/<int:days>` | - | 获取指定工具近 N 天用量。 |
| GET | `/api/tools` | - | 获取全部工具列表。 |
| GET | `/api/trend` | - | 获取按日期聚合的用量趋势（用于图表）。 |
| GET | `/api/unmapped-accounts` | admin_required | 获取未映射工具账号列表。 |
| POST | `/api/unmapped-accounts/<sender_name>/map` | admin_required | 手动将未映射账号映射到用户。 |
| GET | `/api/unmapped-accounts/<sender_name>/suggest-mapping` | admin_required | 获取未映射账号的建议映射。 |
| POST | `/api/upload/batch` | require_upload_auth | 批量上传数据（用量与消息）。 |
| POST | `/api/upload/messages` | require_upload_auth | 上传消息数据。 |
| POST | `/api/upload/usage` | require_upload_auth | 上传用量数据。 |
| DELETE | `/api/user/avatar` | auth_required | 删除用户头像。 |
| POST | `/api/user/avatar` | auth_required | 上传用户头像。 |

### 运维端点

应用级健康与就绪探针、Prometheus 指标、安全基线状态，以及 React SPA 页面与静态路由。

| Method | Path | Auth | Description |
|--------|-------|------|-------------|
| GET | `/` | public_endpoint | 渲染主页面 React SPA。 |
| GET | `/<path:path>` | public_endpoint | 为其余所有前端路由渲染 React SPA。 |
| GET | `/health` | - | 面向 Docker 与负载均衡器的健康检查。 |
| GET | `/livez` | - | Kubernetes 存活探针。 |
| GET | `/login` | public_endpoint | 渲染登录页 React SPA。 |
| GET | `/logout` | public_endpoint | 登出并渲染 React SPA。 |
| GET | `/metrics` | - | Prometheus 指标（prometheus_flask_exporter 不可用时的回退端点）。 |
| GET | `/readyz` | - | 面向 Kubernetes 与负载均衡器的就绪检查。 |
| GET | `/security-status` | - | 用于监控与健康检查的安全基线状态。 |
| GET | `/static/claude-code-webui/<path:filename>` | public_endpoint | 提供 claude-code-webui 静态文件。 |

### 相关文档

- [API_PERMISSION_MATRIX.md](API_PERMISSION_MATRIX.md)——全部提权端点的自动
  生成权限矩阵（改动装饰器后需重新生成）。
- [../guide/REMOTE_WORKSPACE.md](../guide/REMOTE_WORKSPACE.md)——远程机器、
  会话、终端与 VS Code 工作流。
- [../guide/REMOTE_AGENT.md](../guide/REMOTE_AGENT.md)——Agent 安装与 Agent
  协议。
- [AUTONOMOUS_DEVELOPMENT.md](AUTONOMOUS_DEVELOPMENT.md)——自主工作流生命
  周期与里程碑模型。
- [MODEL_GATEWAY.md](MODEL_GATEWAY.md)——模型网关配置与 LLM 代理链路。
- [TOKEN_ACCOUNTING.md](TOKEN_ACCOUNTING.md)——用量/配额端点背后
  的 Token 与请求计量口径。
- [PERMISSION_MODEL.md](PERMISSION_MODEL.md)——角色
  模型与租户管理员范围。
- [../security/API_EXCEPTIONS.md](../security/API_EXCEPTIONS.md)——刻意豁免
  认证的路由。
