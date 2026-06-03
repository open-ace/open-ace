# How Open ACE Runs AI CLIs On Remote Machines Without Shipping API Keys

This is a draft technical article for engineering audiences.

## Short Summary

Open ACE is a self-hosted AI workspace and governance platform for teams adopting AI coding tools such as Claude Code, Qwen Code, Codex, and OpenClaw. One of its core design goals is letting AI CLIs run on remote machines while keeping real LLM API keys encrypted on the Open ACE server.

The key mechanism is Remote Agent plus API Key proxy:

- The AI CLI runs near the code, terminal, and development environment.
- The real API key stays on the Open ACE server.
- The remote agent receives a scoped, short-lived proxy token.
- Model calls flow through Open ACE, where quota, audit, and usage tracking can happen.

## The Team Adoption Problem

AI coding tools are easy to try individually. Team adoption is harder.

Once developers start using AI CLIs across laptops, development servers, staging machines, and GPU machines, administrators need answers to questions like:

- Which users can use which machines?
- Which tools and models are allowed?
- Where do API keys live?
- How do we rotate or revoke keys?
- Can we enforce quota before a tool burns through a monthly budget?
- Can we audit sessions after the fact?
- Can developers use remote environments without repeatedly handling SSH credentials and long-lived API keys?

Putting API keys directly on every remote machine is operationally convenient but creates risk. It spreads secrets across machines, makes rotation harder, and weakens central visibility.

Open ACE takes a different approach.

## Architecture

At a high level:

```text
Browser UI
   |
   | HTTP / WebSocket
   v
Open ACE Server
   |
   | command queue / polling
   v
Remote Agent on target machine
   |
   | subprocess
   v
AI CLI: Claude Code / Qwen Code / Codex / OpenClaw
```

For model calls:

```text
AI CLI
   |
   | request with short-lived proxy token
   v
Remote Agent
   |
   | /api/remote/llm-proxy
   v
Open ACE Server
   |
   | resolve encrypted API key, enforce quota, record usage
   v
LLM Provider
```

The important detail: the remote agent does not need the real provider API key. It needs a proxy token that Open ACE can validate, scope, expire, and audit.

## Request Flow

1. An administrator stores an LLM API key in Open ACE.
2. The key is encrypted on the server side.
3. A remote machine is registered with Open ACE through Remote Agent.
4. A user starts a remote AI session from the browser.
5. Open ACE authorizes the user, machine, tool, provider, and session.
6. Open ACE issues a scoped proxy token to the remote session.
7. The AI CLI runs on the remote machine.
8. When the CLI needs to call a model, the request goes through the Open ACE LLM proxy.
9. Open ACE validates the proxy token, checks quota, resolves the real API key, forwards the request, and records usage.

This lets the CLI operate in the right execution environment while governance stays centralized.

## Why This Matters

### 1. Secret Containment

Real API keys stay on the Open ACE server. Remote machines receive proxy tokens instead of long-lived provider keys.

This reduces the blast radius of a remote machine compromise and makes key rotation simpler.

### 2. Centralized Quota

Because model calls pass through Open ACE, quota enforcement does not depend on each CLI implementing the same budget logic.

Administrators can reason about usage by user, tenant, session, provider, model, or machine.

### 3. Auditability

Open ACE can record session metadata, token usage, tool names, and governance events. That is difficult to reconstruct if each developer runs unmanaged AI tools on separate machines.

### 4. Remote Execution

The AI CLI can run where the code and runtime environment live:

- development servers
- staging boxes
- GPU machines
- machines with private network access

This is especially useful when the browser is the control plane, but the actual work must happen near the target environment.

## Supported CLI Direction

Open ACE has adapters for tools such as:

- Claude Code
- Qwen Code
- Codex
- OpenClaw

Each adapter can handle tool-specific startup behavior, environment variables, permission modes, and session history patterns.

## Browser Development Loop

Remote work is not only about model calls. Developers also need to interact with the machine.

Open ACE's remote workspace direction includes:

- browser terminal through WebSocket PTY
- terminal reconnect and screen recovery
- remote directory browsing
- code-server/VSCode proxy support
- session history synchronization

The goal is to make remote AI sessions usable for actual development work, not just background automation.

## Tradeoffs

This design is not free.

### More Moving Parts

There is a server, a remote agent, CLI adapters, proxy tokens, and model provider forwarding. That is more complex than exporting `OPENAI_API_KEY` on a machine.

### Proxy Availability Matters

If the Open ACE server is unavailable, remote sessions cannot call models through the proxy.

### Provider Compatibility Requires Care

Different tools and providers expect different environment variables, request shapes, streaming behavior, and history formats. Adapters must be maintained.

These tradeoffs are acceptable when the team needs centralized governance, but they may be unnecessary for a single developer.

## Who Should Care

Open ACE is most useful when:

- multiple people use AI coding tools
- API key handling matters
- remote machines are part of the development workflow
- administrators need cost, quota, audit, or compliance visibility
- a team wants self-hosted infrastructure instead of unmanaged tool sprawl

It is probably overkill if one developer is only experimenting locally.

## Try It

```bash
git clone https://github.com/open-ace/open-ace.git
cd open-ace
docker compose up -d --build
```

Then open:

```text
http://localhost:5000
```

Links:

- Repository: https://github.com/open-ace/open-ace
- Website: https://www.open-ace.com
- Remote Workspace docs: https://github.com/open-ace/open-ace/blob/main/docs/en/REMOTE-WORKSPACE.md
- Remote Agent docs: https://github.com/open-ace/open-ace/blob/main/docs/en/REMOTE-AGENT.md
- Release: https://github.com/open-ace/open-ace/releases/tag/v1.0.0

We are looking for feedback from teams already using AI coding tools. The most useful feedback is concrete:

- Which AI coding tools are you using?
- Where do API keys live today?
- Do you run AI tools on remote machines?
- What governance data would actually help your team?
- What would prevent you from adopting a system like this?
