"""Signed identity verification for external applications.

Explicit mappings never create users, match emails, or confer administrator roles.
The capability probe reports what the deployment allows; the token exchange
issues a short-lived scoped proxy token for the mapped user. The signature is
scoped to the exact method, audience and body bytes.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import stat

_UNKNOWN_ISSUER_SECRET = secrets.token_bytes(32)
import time

PATH = "/api/integrations/external/capabilities"
TOKEN_PATH = "/api/integrations/external/token"
# Token TTL bounds: an exchange may request less, never more.
DEFAULT_TTL_SECONDS = 900
MAX_TTL_SECONDS = 3600
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")


class DelegationDenied(Exception):
    """Signed-request verification failed; callers must not reveal which check."""

    pass


def private_file(path, limit):
    if not isinstance(path, str) or not os.path.isabs(path):
        raise ValueError("An absolute private policy path is required")
    with open(path, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("Private regular file required")
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Policy file too large")
    return raw


def strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique)


def load_policy(path):
    policy = strict_json(private_file(path, 262144))
    if not isinstance(policy, dict) or set(policy) != {"issuers", "mappings"}:
        raise ValueError("Invalid delegation policy")
    if not isinstance(policy["issuers"], list) or not isinstance(policy["mappings"], list):
        raise ValueError("Invalid delegation policy lists")
    issuers, mappings, audiences, exchanges = {}, {}, {}, {}
    for issuer in policy["issuers"]:
        # The deployment audience is mandatory: it scopes every signature to
        # one deployment so an issuer secret reused across staging and
        # production can never cross-verify. Exchange settings are optional
        # at load time; the exchange itself fails closed when they are absent.
        required = {"id", "secret_file", "audience"}
        optional = {"allowed_providers", "allowed_models", "max_ttl_seconds"}
        if (
            not isinstance(issuer, dict)
            or not required.issubset(issuer)
            or set(issuer) - required - optional
        ):
            raise ValueError("Invalid issuer")
        name = issuer["id"]
        if not isinstance(name, str) or not IDENTIFIER.fullmatch(name) or name in issuers:
            raise ValueError("Invalid issuer identifier")
        audience = issuer["audience"]
        if not isinstance(audience, str) or not IDENTIFIER.fullmatch(audience):
            raise ValueError("Invalid issuer audience")
        key = private_file(issuer["secret_file"], 4096).strip()
        if len(key) < 32:
            raise ValueError("Delegation secret too short")
        providers = issuer.get("allowed_providers")
        models = issuer.get("allowed_models")
        max_ttl = issuer.get("max_ttl_seconds", DEFAULT_TTL_SECONDS)
        if providers is not None and (
            not isinstance(providers, list)
            or not providers
            or any(not isinstance(p, str) or not IDENTIFIER.fullmatch(p) for p in providers)
        ):
            raise ValueError("Invalid issuer providers")
        if models is not None and (
            not isinstance(models, list)
            or not models
            or any(not isinstance(m, str) or not 1 <= len(m) <= 191 for m in models)
        ):
            raise ValueError("Invalid issuer models")
        if (
            not isinstance(max_ttl, int)
            or isinstance(max_ttl, bool)
            or not 1 <= max_ttl <= MAX_TTL_SECONDS
        ):
            raise ValueError("Invalid issuer TTL")
        issuers[name] = key
        audiences[name] = audience
        exchanges[name] = {
            "providers": set(providers) if providers is not None else set(),
            "models": list(models) if models is not None else [],
            "max_ttl_seconds": max_ttl,
        }
    fields = {"issuer", "external_org", "external_user", "external_login", "tenant_id", "user_id"}
    for row in policy["mappings"]:
        if not isinstance(row, dict) or set(row) != fields:
            raise ValueError("Invalid mapping")
        if any(
            not isinstance(row[field], str)
            or not 1 <= len(row[field]) <= 191
            or any(ord(c) < 32 for c in row[field])
            for field in ("issuer", "external_org", "external_user", "external_login")
        ):
            raise ValueError("Invalid external identity")
        if row["issuer"] not in issuers or any(
            not isinstance(row[field], int) or isinstance(row[field], bool) or row[field] <= 0
            for field in ("tenant_id", "user_id")
        ):
            raise ValueError("Invalid local identity")
        key = tuple(
            row[field] for field in ("issuer", "external_org", "external_user", "external_login")
        )
        if key in mappings:
            raise ValueError("Ambiguous identity mapping")
        mappings[key] = (row["tenant_id"], row["user_id"])
    return issuers, mappings, audiences, exchanges


def signature(secret, issuer, timestamp, nonce, body, *, method="POST", path=PATH, audience):
    # The audience scopes a signature to one deployment: an issuer secret
    # reused across staging and production must declare distinct audiences so
    # a signature accepted by one can never verify in the other.
    parts = [
        "openace-external-v1",
        issuer,
        timestamp,
        nonce,
        method,
        path,
        hashlib.sha256(body).hexdigest(),
        audience,
    ]
    framed = b"".join(str(len(part.encode())).encode() + b":" + part.encode() for part in parts)
    return hmac.new(secret, framed, hashlib.sha256).hexdigest()


class ReplayStore:
    """Cross-worker atomic nonce consumption. Database errors fail closed."""

    def __init__(self, connection, placeholder="?"):
        if placeholder not in ("?", "%s"):
            raise ValueError("Unsupported database")
        self.connection, self.placeholder = connection, placeholder

    def consume(self, issuer, nonce, now):
        conn = self.connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM external_identity_nonces WHERE expires_at < ?".replace(
                    "?", self.placeholder
                ),
                (now,),
            )
            cursor.execute(
                "INSERT INTO external_identity_nonces (issuer, nonce, expires_at) VALUES (?, ?, ?)".replace(
                    "?", self.placeholder
                ),
                (issuer, nonce, now + 120),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise DelegationDenied() from None
        finally:
            conn.close()


def verify_signed_request(
    policy,
    headers,
    body,
    replay,
    account_lookup,
    tenant_lookup,
    *,
    now=None,
    path=PATH,
    expected_fields=None,
    optional_fields=None,
):
    now = int(time.time()) if now is None else now
    try:
        issuers, mappings, audiences, _exchange = policy
        issuer, stamp, nonce, supplied = (
            headers.get(name, "")
            for name in ("X-ACE-Issuer", "X-ACE-Time", "X-ACE-Nonce", "X-ACE-Signature")
        )
        # L1 hardening: format and window checks first, then always run the
        # keyed comparison — including for unknown issuers, against a dummy
        # secret — so the failure path does not reveal which issuer names
        # exist in the operator policy.
        if (
            not re.fullmatch(r"[0-9]{1,12}", stamp)
            or abs(now - int(stamp)) > 30
            or not re.fullmatch(r"[0-9a-f]{48}", nonce)
            or not re.fullmatch(r"[0-9a-f]{64}", supplied)
            or len(body) > 4096
        ):
            raise DelegationDenied()
        secret = issuers.get(issuer, _UNKNOWN_ISSUER_SECRET)
        if not hmac.compare_digest(
            signature(
                secret,
                issuer,
                stamp,
                nonce,
                body,
                path=path,
                audience=audiences[issuer],
            ),
            supplied,
        ):
            raise DelegationDenied()
        if issuer not in issuers:
            raise DelegationDenied()
        request = strict_json(body)
        fields = expected_fields or {"organization", "user", "login"}
        if not isinstance(fields, set) or not {"organization", "user", "login"}.issubset(fields):
            raise DelegationDenied()
        if not isinstance(request, dict):
            raise DelegationDenied()
        keys = set(request)
        optional = optional_fields or set()
        # Every required field present, and nothing outside the declared
        # optional extras — the token exchange carries exactly one optional.
        if not fields <= keys or not keys <= fields | optional:
            raise DelegationDenied()
        identity = tuple(request[field] for field in ("organization", "user", "login"))
        if any(not isinstance(value, str) or not 1 <= len(value) <= 191 for value in identity):
            raise DelegationDenied()
        tenant_id, user_id = mappings[(issuer, *identity)]
        replay.consume(issuer, nonce, now)
        account, tenant = account_lookup(user_id), tenant_lookup(tenant_id)
        if (
            not account
            or account.get("id") != user_id
            or not account.get("is_active")
            or account.get("tenant_id") != tenant_id
            or account.get("deleted_at")
            or account.get("must_change_password")
            or not tenant
            or tenant.get("id") != tenant_id
            or tenant.get("status") != "active"
        ):
            raise DelegationDenied()
        return {
            "protocol": "openace-external-v1",
            "issuer": issuer,
            "request_nonce": nonce,
            "identity": {"tenant_id": tenant_id, "user_id": user_id},
            "body": request,
        }
    except (KeyError, ValueError, TypeError):
        raise DelegationDenied() from None
