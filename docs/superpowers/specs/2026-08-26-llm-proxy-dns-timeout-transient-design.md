# Design: LLM proxy — treat transient upstream DNS-resolution failure as retryable, not a 403 policy violation

- **Issue:** #3116
- **Date:** 2026-08-26
- **Origin:** autonomous-workflow monitoring (class-2), workflow `23db89b3` / issue #3083

## Problem

The workspace LLM proxy validates the upstream target URL for SSRF on every
call. For a **public, non-allowlisted** host the path is:

```
llm_proxy_handler._handle...                      (app/modules/workspace/llm_proxy_handler.py:224)
  -> validate_llm_proxy_url                        (app/utils/llm_proxy_url_validator.py:360)
    -> validate_public_http_url                    (app/utils/outbound_url_guard.py:244)
      -> _resolve_addresses(host, 443, getaddrinfo)  (live DNS lookup)
```

When DNS resolution **times out transiently**, `getaddrinfo` raises `OSError`;
`validate_public_http_url` returns
`OutboundUrlValidationResult(allowed=False, error="Host could not be resolved: ...")`.
The handler runs that through `sanitize_error_message`, whose fall-through
collapses it to the generic **`"Blocked outbound URL: security policy
violation"`** and returns **HTTP 403**.

The autonomous orchestrator's transient-retry classifier
(`_TRANSIENT_API_ERROR_RE`, `orchestrator.py:2094`) intentionally treats **all
4xx except 429 as permanent** ("400/401/403/404/422 are permanent client errors
and must NOT trigger retry") and matches `5\d{2}`, `service unavailable`,
`bad gateway`, etc. as transient. So a **403 is never retried**: a ~1-minute DNS
blip on a known upstream endpoint **hard-fails the entire workflow** with a
misleading, security-sounding error.

### Evidence (prod, ai-lab, 2026-08-26)

- Test-phase agent calls to `https://coding.dashscope.aliyuncs.com/.../messages`
  (glm-5) returned **403** after a fixed **~20.0 s** stall (getaddrinfo timeout)
  at 14:48–14:49 local; dozens of calls to the same upstream returned 200 in the
  minutes before and after.
- Only **5** such blocked warnings in 3 days, all inside that single minute — an
  isolated transient.
- Downstream symptom: workflow reported *"Tests were not actually run — agent
  could not execute any test framework"* (the agent got 403 on its first call
  and ran nothing).

## Root cause

A transient **DNS-resolution failure** is conflated with a permanent **policy
violation** and surfaced as a non-retryable **403**. The allowlist path already
tolerates DNS failure ("Allow on DNS failure, will be caught by safe_request",
`llm_proxy_url_validator.py:350`); the public-host path fail-closes hard and,
worse, mislabels the reason.

## Chosen approach — Distinguish "transient" at the proxy boundary

Carry a `transient` flag through the validation results and let the handler map
it to a **5xx** (retryable) response instead of the generic 403.

**Security posture is unchanged.** On a resolution failure **no outbound request
is sent** in any case — the request is still blocked. The only thing that
changes is the HTTP status/message returned to the *caller* (the agent), which
is what decides retry-vs-hard-fail in the orchestrator. **403 remains** for
genuine policy determinations (non-public IP, blocked host, bad scheme, bad
port, credentials-in-URL, DNS rebinding).

### Components & changes

1. **`app/utils/outbound_url_guard.py`**
   - Add `transient: bool = False` to the frozen dataclass
     `OutboundUrlValidationResult` (default keeps every existing construction
     valid).
   - In `validate_public_http_url`, set `transient=True` on the two
     **resolution-failure** branches only:
     - `except OSError` (getaddrinfo failure/timeout) — line ~245.
     - "Host did not resolve to an IP address" (empty result) — line ~250.
   - All **policy** branches (blocked host, non-public address, port, scheme,
     credentials, invalid host) keep `transient=False` (the default).

2. **`app/utils/llm_proxy_url_validator.py`**
   - Add `transient: bool = False` to `LlmProxyValidationResult`.
   - In `validate_llm_proxy_url`, when the standard-SSRF path returns
     `not allowed`, propagate `transient=result.transient` (line ~361).
   - The allowlist branch and the parse/host-normalization guards remain
     `transient=False`.

3. **`app/modules/workspace/llm_proxy_handler.py`** (call-site ~224)
   - When `validate_llm_proxy_url` returns `not allowed`:
     - If `result.transient`: return **HTTP 503** with
       `type="upstream_unavailable"` and a message that contains both the
       numeric and phrase cues the retry classifier recognizes, e.g.
       `"Upstream host DNS resolution failed (transient). Service Unavailable — retry later."`
       Log at WARNING as an upstream/transient event; audit with a
       `dns_resolution_failed` reason (distinct from `ssrf_violation`).
     - Else: unchanged — sanitized **403** `ssrf_blocked`.
   - The DNS-rebinding branch (line ~194) is a genuine policy block and stays
     **403** (`"DNS resolution changed"`).

### Why 503 + this message

The orchestrator matches on the agent's surfaced **response text**, not the raw
HTTP code. The agent renders proxy failures as `"API Error: <status>-... <body>"`
(observed: `"API Error: 403-***-**** Blocked outbound URL: ..."`). A **503** body
gives the classifier two independent matches — `api\s*error:?\s*5\d{2}` (status)
and `service\s+unavailable` (phrase in the body) — so retry fires even if the
agent's formatting of one cue changes.

## Blast radius

- `OutboundUrlValidationResult` / `LlmProxyValidationResult` each gain one
  optional field defaulting to `False`; `assert_public_http_url`, `safe_request`,
  and all existing callers are unaffected (they only read `allowed` / `error` /
  `resolved_addresses`).
- Handler adds one status branch. Every genuine-policy-violation 403 is
  byte-for-byte unchanged.

## Alternatives rejected

- **B — Let the request proceed on public-host DNS failure (mirror the allowlist
  path).** Public hosts have no pinned IPs, so the handler's non-pinned branch
  (`http_requests.request`, line ~1676) performs **no** SSRF check → a real
  security hole; and it does not help when DNS is genuinely down (the request
  fails anyway).
- **C — Retry DNS inside the validator.** A ~1-minute outage outlasts any
  in-request retry budget, adds request latency, and duplicates the
  orchestrator's existing, well-tested retry loop.

## Test strategy (TDD, `tests/unit/`, marked `issue(3116)` + `regression`)

1. `validate_public_http_url`:
   - resolver raising `OSError` → `allowed False`, **`transient True`**.
   - resolver returning a **private** IP → `allowed False`, **`transient False`**
     (policy).
   - resolver returning **empty** → `allowed False`, **`transient True`**.
   - a blocked scheme/port → `allowed False`, `transient False`.
2. `validate_llm_proxy_url` propagates `transient` from the standard-SSRF path;
   allowlist and parse-guard failures stay `transient False`.
3. Handler:
   - transient validation failure → **503**, body message present.
   - genuine policy violation → **403** (unchanged).
4. **Linchpin contract test:** the exact 503 message string the handler emits is
   matched by `orchestrator._TRANSIENT_API_ERROR_RE` — proving the cross-module
   coupling that makes retry actually fire.

## Deployment & rollout

- Pure logic change, no migration, no schema. CI gates via `tests/unit/`
  (`python-core`).
- Deploy: hot-patch `app/utils/outbound_url_guard.py`,
  `app/utils/llm_proxy_url_validator.py`,
  `app/modules/workspace/llm_proxy_handler.py` to prod; the proxy runs in
  `open-ace.service` (web app) — restart it (not the scheduler) so the new
  validation takes effect.
- Then reset workflow `23db89b3` (#3083) so it re-runs; it will now retry the
  transient DNS failure instead of hard-failing.
