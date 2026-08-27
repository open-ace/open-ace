# LLM Proxy Transient-DNS-Failure Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the workspace LLM proxy return a retryable 5xx (not a permanent 403) when an upstream host's DNS resolution fails transiently, so autonomous workflows retry a brief DNS blip instead of hard-failing.

**Architecture:** Carry a `transient` flag through the two validation result dataclasses; set it only on DNS-resolution-failure branches; the proxy handler maps `transient` → HTTP 503 with a message the orchestrator's existing transient-retry classifier recognizes. Genuine policy violations keep returning 403. No outbound request is sent on a resolution failure either way, so security posture is unchanged.

**Tech Stack:** Python, Flask, pytest. Spec: `docs/superpowers/specs/2026-08-26-llm-proxy-dns-timeout-transient-design.md`. Issue: #3116.

---

## File Structure

- `app/utils/outbound_url_guard.py` — add `transient` to `OutboundUrlValidationResult`; set it on the resolution-failure branches of `validate_public_http_url`.
- `app/utils/llm_proxy_url_validator.py` — add `transient` to `LlmProxyValidationResult`; propagate it from the standard-SSRF path in `validate_llm_proxy_url`.
- `app/modules/workspace/llm_proxy_handler.py` — in `_determine_target_url`, return 503 for a transient validation failure, 403 otherwise.
- `tests/unit/test_outbound_url_guard.py` — extend (Task 1).
- `tests/unit/test_llm_proxy_url_validator.py` — extend (Task 2).
- `tests/unit/test_llm_proxy_dns_transient.py` — new handler + contract tests (Task 3).

All new tests carry `pytest.mark.issue(3116)` and `pytest.mark.regression`.

---

## Task 1: `transient` flag on the outbound guard

**Files:**
- Modify: `app/utils/outbound_url_guard.py` (dataclass `OutboundUrlValidationResult` ~line 169; `validate_public_http_url` ~lines 244-251)
- Test: `tests/unit/test_outbound_url_guard.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_outbound_url_guard.py`:

```python
import socket as _socket


def _raising_resolver(host, port, type=_socket.SOCK_STREAM):
    raise OSError("temporary failure in name resolution")


def _empty_resolver(host, port, type=_socket.SOCK_STREAM):
    return []


def test_dns_resolution_timeout_is_transient():
    result = validate_public_http_url(
        "https://coding.example.com/v1/messages",
        resolver=_raising_resolver,
    )
    assert not result.allowed
    assert result.transient is True


def test_empty_resolution_is_transient():
    result = validate_public_http_url(
        "https://coding.example.com/v1/messages",
        resolver=_empty_resolver,
    )
    assert not result.allowed
    assert result.transient is True


def test_private_address_block_is_not_transient():
    result = validate_public_http_url(
        "https://sso.example.com/token",
        resolver=_resolver("10.1.2.3"),
    )
    assert not result.allowed
    assert result.transient is False


def test_bad_scheme_block_is_not_transient():
    result = validate_public_http_url("ftp://example.com/file")
    assert not result.allowed
    assert result.transient is False


def test_allowed_url_is_not_transient():
    result = validate_public_http_url(
        "https://login.example.com/oauth/token",
        resolver=_resolver("93.184.216.34"),
    )
    assert result.allowed
    assert result.transient is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_outbound_url_guard.py -k "transient" -v`
Expected: FAIL — `AttributeError: 'OutboundUrlValidationResult' object has no attribute 'transient'`.

- [ ] **Step 3: Add the field**

In `app/utils/outbound_url_guard.py`, extend the dataclass (currently ends with `resolved_addresses`):

```python
@dataclass(frozen=True)
class OutboundUrlValidationResult:
    """Result for an outbound URL security validation."""

    allowed: bool
    error: str | None = None
    # The public IP addresses that were verified for this URL. Populated only
    # when ``allowed`` is True. Used by :func:`safe_request` to pre-validate
    # before the request and by :class:`_PinnedIPAdapter` to re-check at
    # connect time.
    resolved_addresses: tuple[IPAddress, ...] = ()
    # True when the failure is a *resolution* failure (DNS timeout / empty
    # answer) rather than a policy determination. A transient failure sends no
    # request but is safe to retry; callers may surface it as a retryable 5xx
    # instead of a permanent 4xx. #3116.
    transient: bool = False
```

- [ ] **Step 4: Set `transient` on the resolution-failure branches**

In `validate_public_http_url`, the two resolution branches become:

```python
    try:
        addresses = _resolve_addresses(ascii_host, parsed.port, resolver)
    except OSError as exc:
        return OutboundUrlValidationResult(
            False, f"Host could not be resolved: {exc}", transient=True
        )
    except ValueError as exc:
        return OutboundUrlValidationResult(False, str(exc))

    if not addresses:
        return OutboundUrlValidationResult(
            False, "Host did not resolve to an IP address", transient=True
        )
```

(Leave every other `OutboundUrlValidationResult(False, ...)` in the function untouched — they default `transient=False`.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_outbound_url_guard.py -v`
Expected: PASS (all, including pre-existing).

- [ ] **Step 6: Commit**

```bash
git add app/utils/outbound_url_guard.py tests/unit/test_outbound_url_guard.py
git commit -m "fix(#3116): flag DNS-resolution failures as transient in outbound guard"
```

---

## Task 2: Propagate `transient` through the LLM-proxy validator

**Files:**
- Modify: `app/utils/llm_proxy_url_validator.py` (dataclass `LlmProxyValidationResult` ~line 39; `validate_llm_proxy_url` return at ~line 361)
- Test: `tests/unit/test_llm_proxy_url_validator.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_llm_proxy_url_validator.py` (match the file's existing imports; add these if missing):

```python
import socket as _socket

from app.utils.llm_proxy_url_validator import validate_llm_proxy_url


def _raising_resolver(host, port, type=_socket.SOCK_STREAM):
    raise OSError("temporary failure in name resolution")


def _public_resolver(host, port, type=_socket.SOCK_STREAM):
    return [(_socket.AF_INET, _socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def _private_resolver(host, port, type=_socket.SOCK_STREAM):
    return [(_socket.AF_INET, _socket.SOCK_STREAM, 6, "", ("10.1.2.3", port))]


def test_validate_llm_proxy_url_marks_dns_failure_transient():
    result = validate_llm_proxy_url(
        "https://coding.example.com/v1/messages",
        tenant_id=1,
        provider="anthropic",
        resolver=_raising_resolver,
    )
    assert not result.allowed
    assert result.transient is True


def test_validate_llm_proxy_url_private_block_not_transient():
    result = validate_llm_proxy_url(
        "https://coding.example.com/v1/messages",
        tenant_id=1,
        provider="anthropic",
        resolver=_private_resolver,
    )
    assert not result.allowed
    assert result.transient is False


def test_validate_llm_proxy_url_allowed_not_transient():
    result = validate_llm_proxy_url(
        "https://coding.example.com/v1/messages",
        tenant_id=1,
        provider="anthropic",
        resolver=_public_resolver,
    )
    assert result.allowed
    assert result.transient is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_llm_proxy_url_validator.py -k transient -v`
Expected: FAIL — `AttributeError: 'LlmProxyValidationResult' object has no attribute 'transient'`.

- [ ] **Step 3: Add the field**

In `app/utils/llm_proxy_url_validator.py`:

```python
@dataclass(frozen=True)
class LlmProxyValidationResult:
    """Result for LLM proxy URL validation."""

    allowed: bool
    error: str | None = None
    resolved_ips: tuple[IPAddress, ...] = ()
    is_allowlist_match: bool = False
    # Mirrors OutboundUrlValidationResult.transient: True for a DNS-resolution
    # failure (retryable) vs a policy block (permanent). #3116.
    transient: bool = False
```

- [ ] **Step 4: Propagate from the standard-SSRF path**

In `validate_llm_proxy_url`, the standard-SSRF failure return (currently `return LlmProxyValidationResult(False, result.error)`) becomes:

```python
    # Standard SSRF validation
    result = validate_public_http_url(url, resolver=resolver)
    if not result.allowed:
        return LlmProxyValidationResult(False, result.error, transient=result.transient)

    return LlmProxyValidationResult(True, resolved_ips=result.resolved_addresses)
```

(The allowlist branch and the parse/host-normalization guards stay `transient=False` by default — a malformed URL is a permanent client error, not a transient one.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_llm_proxy_url_validator.py -v`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add app/utils/llm_proxy_url_validator.py tests/unit/test_llm_proxy_url_validator.py
git commit -m "fix(#3116): propagate transient DNS-failure flag through proxy validator"
```

---

## Task 3: Handler returns 503 for transient failures + retry-contract test

**Files:**
- Modify: `app/modules/workspace/llm_proxy_handler.py` (`_determine_target_url`, the `if not result.allowed:` block ~lines 225-251)
- Test: `tests/unit/test_llm_proxy_dns_transient.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_llm_proxy_dns_transient.py`:

```python
"""LLM proxy returns a retryable 5xx for transient DNS-resolution failures
instead of a permanent 403, so autonomous workflows retry a brief DNS blip
rather than hard-failing. Issue #3116.
"""

from unittest.mock import patch

import pytest
from flask import Flask

from app.modules.workspace.llm_proxy_handler import _determine_target_url
from app.utils.llm_proxy_url_validator import LlmProxyValidationResult

pytestmark = [pytest.mark.issue(3116), pytest.mark.regression]

_APP = Flask(__name__)


def _call_determine(validation_result):
    with patch(
        "app.utils.llm_proxy_url_validator.validate_llm_proxy_url",
        return_value=validation_result,
    ):
        with _APP.test_request_context("/api/remote/llm-proxy/v1/messages"):
            return _determine_target_url(
                provider="anthropic",
                base_url="https://coding.example.com",
                path="v1/messages",
                tenant_id=1,
            )


def test_transient_validation_failure_returns_503():
    out = _call_determine(
        LlmProxyValidationResult(False, "Host could not be resolved: timeout", transient=True)
    )
    assert isinstance(out, tuple)
    _response, status = out
    assert status == 503


def test_policy_violation_still_returns_403():
    out = _call_determine(
        LlmProxyValidationResult(False, "Blocked non-public address: 10.1.2.3", transient=False)
    )
    assert isinstance(out, tuple)
    _response, status = out
    assert status == 403


def test_transient_503_message_matches_orchestrator_retry_classifier():
    """Linchpin: the 503 body the handler emits must be recognized as transient
    by the orchestrator's retry regex, otherwise the workflow won't retry."""
    from app.modules.workspace.autonomous.orchestrator import _TRANSIENT_API_ERROR_RE

    out = _call_determine(
        LlmProxyValidationResult(False, "Host could not be resolved: timeout", transient=True)
    )
    response, status = out
    assert status == 503
    message = response.get_json()["error"]["message"]
    assert _TRANSIENT_API_ERROR_RE.search(message), (
        f"503 message not recognized as transient by orchestrator: {message!r}"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_llm_proxy_dns_transient.py -v`
Expected: FAIL — `test_transient_validation_failure_returns_503` and the contract test get `403` (current behavior).

- [ ] **Step 3: Add the transient branch in the handler**

In `app/modules/workspace/llm_proxy_handler.py`, replace the `if not result.allowed:` block (the one that logs "LLM proxy URL blocked", records `ssrf_blocked`, audits, and returns 403) with a branch that splits transient from policy:

```python
        # Standard SSRF validation
        result = validate_llm_proxy_url(target_url, tenant_id, provider)
        if not result.allowed:
            if result.transient:
                # A transient DNS-resolution failure (timeout / empty answer) is
                # NOT a policy violation — no request is sent either way, but it
                # is safe to retry. Surface a retryable 5xx whose body the
                # orchestrator's transient-error classifier recognizes, instead
                # of a permanent 403 that hard-fails the workflow. #3116.
                logger.warning(
                    "LLM proxy upstream DNS resolution failed for tenant %s provider %s: %s",
                    tenant_id,
                    provider,
                    result.error,
                )
                audit_blocked_url(
                    tenant_id=tenant_id,
                    provider=provider,
                    url=target_url,
                    reason="dns_resolution_failed",
                )
                return (
                    jsonify(
                        {
                            "error": {
                                "message": (
                                    "Upstream host DNS resolution failed (transient). "
                                    "Service Unavailable — retry later."
                                ),
                                "type": "upstream_unavailable",
                            }
                        }
                    ),
                    503,
                )
            sanitized_error = sanitize_error_message(result.error or "Invalid URL")
            logger.warning(
                "LLM proxy URL blocked for tenant %s provider %s: %s",
                tenant_id,
                provider,
                sanitized_error,
            )
            record_ssrf_blocked(tenant_id, provider, "ssrf_violation")
            audit_blocked_url(
                tenant_id=tenant_id,
                provider=provider,
                url=target_url,
                reason=result.error or "ssrf_violation",
            )
            return (
                jsonify(
                    {
                        "error": {
                            "message": sanitized_error,
                            "type": "ssrf_blocked",
                            "blocked_host_hash": hash_host_for_audit(target_url),
                        }
                    }
                ),
                403,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_llm_proxy_dns_transient.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full affected suites**

Run: `python -m pytest tests/unit/test_outbound_url_guard.py tests/unit/test_llm_proxy_url_validator.py tests/unit/test_llm_proxy_dns_transient.py tests/unit/test_llm_proxy_handler_audit_username.py -v`
Expected: PASS (all, no regressions).

- [ ] **Step 6: Commit**

```bash
git add app/modules/workspace/llm_proxy_handler.py tests/unit/test_llm_proxy_dns_transient.py
git commit -m "fix(#3116): return retryable 503 for transient upstream DNS failure in LLM proxy"
```

---

## Task 4: Mutation checks (verify each guard clause bites)

- [ ] **Step 1: Mutation — revert the OSError `transient=True`**

Temporarily change Task 1 Step 4's `except OSError` return back to `transient` default (drop `transient=True`).
Run: `python -m pytest tests/unit/test_outbound_url_guard.py::test_dns_resolution_timeout_is_transient tests/unit/test_llm_proxy_dns_transient.py -v`
Expected: FAIL (the transient/503/contract tests fail). Restore the change; re-run → PASS.

- [ ] **Step 2: Mutation — make the handler always 403**

Temporarily force the handler's `if result.transient:` to `if False:`.
Run: `python -m pytest tests/unit/test_llm_proxy_dns_transient.py -v`
Expected: FAIL (`test_transient_validation_failure_returns_503` + contract test). Restore; re-run → PASS.

(No commit — mutation checks are verification only. Confirm the tree is clean and matches Task 3's committed state afterward.)

---

## Self-Review notes

- **Spec coverage:** dataclass fields (Tasks 1-2), resolution-failure branches (Task 1), propagation (Task 2), handler 503-vs-403 (Task 3), linchpin contract test (Task 3 Step 1), mutation checks (Task 4). All spec test-strategy items are covered.
- **Type consistency:** field name `transient: bool` identical across both dataclasses and the handler read `result.transient`.
- **No security change:** every non-resolution `OutboundUrlValidationResult(False, ...)` keeps `transient=False`; the DNS-rebinding branch in the handler is untouched (still 403).
