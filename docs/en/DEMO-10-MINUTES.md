# 10-Minute Demo: From Zero to a Remote AI Coding Agent

This walkthrough takes Open ACE from an empty checkout to a live, browser-driven AI coding session running on a **remote** machine — the scenario the project's own success signal targets ("new users can run Open ACE locally in under 10 minutes").

It doubles as a guided tour of the [security model](SECURITY.md): at each step you can see one layer of the defense-in-depth design working — default-credential rotation, one-time machine registration, encrypted API keys, and short-lived proxy tokens.

> **Prerequisites**
> - Docker and Docker Compose
> - A second machine (or a second VM/container) reachable over the network to act as the "remote agent" host
> - An LLM API key (OpenAI or Anthropic) to drive a coding session
>
> **No second machine?** You can still run the whole demo on one host: point the agent's `server_url` at `http://host.docker.internal:5000` and run the agent in a second terminal. The security model is identical.

---

## Timeline

| Time | Step | Security layer exercised |
|------|------|--------------------------|
| 0:00 | [Start the server](#1-start-the-server-000–020) | Production-safe defaults, `SECRET_KEY` |
| 0:20 | [Log in and rotate the default password](#2-log-in-and-rotate-the-default-password-020–030) | `must_change_password`, bcrypt, login lockout |
| 0:30 | [Store an API key](#3-store-an-api-key-030–140) | Fernet encryption at rest, key hashing |
| 1:40 | [Register a remote machine](#4-register-a-remote-machine-140–330) | One-time 256-bit registration token |
| 3:30 | [Install the remote agent](#5-install-the-remote-agent-330–600) | Agent config, `skip_ssl_verify` |
| 6:00 | [Start a coding session](#6-start-a-coding-session-600–800) | Proxy token, LLM proxy flow |
| 8:00 | [Open a browser terminal](#7-open-a-browser-terminal-800–900) | Session ownership, machine access control |
| 9:00 | [Review the audit trail](#8-review-the-audit-trail-900–1000) | Audit logs, quota, compliance |

---

## 1. Start the server (0:00–0:20)

```bash
git clone https://github.com/open-ace/open-ace.git
cd open-ace
docker compose up -d --build
```

Open <http://localhost:5000>. The seed script has created a single `admin/admin123` account.

> 🔒 **Security note.** The container expects `SECRET_KEY` in production. For the demo, `docker-compose.yml` supplies a placeholder; in a real deployment you would set a strong, unique value (and a separate `OPENACE_ENCRYPTION_KEY`). The seed user has `must_change_password = True`, so the default credential cannot be reused — step 2 addresses that.

## 2. Log in and rotate the default password (0:20–0:30)

1. Sign in with `admin` / `admin123`.
2. The UI prompts for a new password — set one. (If it doesn't, go to **Manage → Users → admin → Change password**.)

> 🔒 **Security note.** Passwords are hashed with **bcrypt at 12 rounds**. After 5 failed logins the account is locked for 15 minutes (configurable in `security_settings`). Because the seed forces a password change, the well-known `admin123` stops being valid within seconds of first login.

## 3. Store an API key (0:30–1:40)

1. Go to **Manage → API Keys → Add Key**.
2. Choose a provider (OpenAI or Anthropic), paste your key, and save.

> 🔒 **Security note.** The key is encrypted with **Fernet (AES-128-CBC + HMAC-SHA256)** before it touches the database. The encryption key is derived via SHA-256 from `OPENACE_ENCRYPTION_KEY` (or `SECRET_KEY`) and is never persisted. A SHA-256 hash of the key is also stored so duplicates can be detected without decrypting. From this point on, the plaintext key exists only in server memory during a proxied request.

## 4. Register a remote machine (1:40–3:30)

1. In **Manage → Remote Machines**, click **Register Machine**.
2. The server returns a **registration token** — copy it. It is shown **only once**.

```bash
# From the admin UI you get back a token like:
#   a1b2c3...   (256-bit, hex)
```

> 🔒 **Security note.** The token is 256 bits of randomness (`secrets.token_hex(32)`). Only its **SHA-256 hash** is stored in the database, so a database leak does not reveal usable tokens. It is **one-time use** with a **1-hour TTL**: the moment the agent registers, the token is atomically marked consumed, and any replay attempt is rejected. Generating tokens is restricted to system admins.

## 5. Install the remote agent (3:30–6:00)

On the *remote* machine (Linux/macOS), run the installer, passing the server URL and the one-time token:

```bash
curl -fsSL http://<server-host>:5000/api/remote/agent/install.sh | bash -s -- \
  --server http://<server-host>:5000 \
  --token <registration-token> \
  --name dev-machine
```

The installer drops a config at `~/.open-ace-agent/config.json`, installs a CLI tool (Qwen Code by default), and starts the agent daemon, which opens a WebSocket back to the server.

> 🔒 **Security note.** For the demo `skip_ssl_verify` defaults to `true` so self-signed local certificates work. **For any real deployment**, set `OPENACE_SKIP_SSL_VERIFY=false` (or `skip_ssl_verify: false`) once you have a valid certificate, or the agent will trust any presented cert. The agent authenticates every subsequent request with the long-lived **agent token** it received at registration — never with your API key.

## 6. Start a coding session (6:00–8:00)

1. Back in the browser, open **Work → New Session**.
2. Select the remote machine (`dev-machine`), pick the CLI tool and model, and start the session.
3. Type a prompt — the AI CLI runs on the remote machine and streams output back to your browser.

> 🔒 **Security note.** The server did **not** send the real API key to the remote machine. Instead it issued a **short-lived proxy token** — an HMAC-SHA256-signed payload (15-minute validity for workspace sessions) carrying your `user_id`, `session_id`, `tenant_id`, and provider. When the CLI calls a model:
>
> 1. It posts to `/api/remote/llm-proxy` with `Authorization: Bearer <proxy_token>`.
> 2. The server verifies the signature (constant-time) and expiry, and confirms the session is still active.
> 3. The server **decrypts the real key in memory**, swaps it into the Authorization header, and forwards the request to the LLM provider.
> 4. Token usage is parsed from the response and billed to your quota.
>
> **The API key is never written to disk on the remote machine.** When building the CLI's `settings.json`, the server strips `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, base URLs, and any custom `envKey` fields, and injects credentials only as ephemeral environment variables.

## 7. Open a browser terminal (8:00–9:00)

From the session view, open the **Terminal** tab. You now have an interactive shell on the remote machine, streamed over a WebSocket PTY.

> 🔒 **Security note.** Session access is ownership-checked: only you, a system admin, or a machine admin on that machine can view or stop your session. The `machine_assignments` table governs which users may use which machine; machine admins can delegate but cannot escalate beyond their own machine.

## 8. Review the audit trail (9:00–10:00)

1. Open **Manage → Audit Logs** to see the registration, session start, and proxied model calls.
2. Check **Manage → Usage / Quota** to confirm token consumption is attributed to your session and tenant.

> 🔒 **Security note.** Every remote operation is recorded via the `AuditLogger`. Local and remote sessions share the `quota_usage` table and are billed uniformly, and a scheduler enforces per-tenant limits every 60 seconds. If you enabled the content filter, PII and sensitive-content matches also surface here for compliance review.

---

## What you have proven

In under 10 minutes you stood up a self-hosted control plane that:

- rotated its default credential on first login,
- stored an API key with authenticated encryption,
- registered a remote machine with a single-use token,
- ran an AI coding session on that machine **without ever sending the real key to it**, and
- left a complete, quota-aware audit trail.

## Next steps

- **Lock it down for production** using the [hardening checklist](SECURITY.md#9-production-hardening-checklist).
- **Read the full security model**: [SECURITY.md](SECURITY.md).
- **Go deeper on remote architecture**: [Remote Workspace](REMOTE-WORKSPACE.md) and [Remote Agent](REMOTE-AGENT.md).
- **Deploy behind TLS**: [Deployment](DEPLOYMENT.md) and [NGINX](NGINX.md).
