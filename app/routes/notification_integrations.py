"""Admin APIs for centralized notification and collaboration settings."""

import logging

from flask import Blueprint, g, jsonify, request

from app.auth.decorators import admin_required
from app.modules.governance.audit_logger import AuditAction, AuditLogger
from app.repositories.notification_settings_repository import get_notification_settings_repository
from app.utils.outbound_url_guard import OutboundUrlBlockedError, safe_request

logger = logging.getLogger(__name__)
notification_integrations_bp = Blueprint("notification_integrations", __name__)
audit_logger = AuditLogger()


@notification_integrations_bp.before_request
@admin_required
def require_admin():
    pass


def _user_id():
    return g.user.get("id") if getattr(g, "user", None) else None


@notification_integrations_bp.get("/management/notification-channels/status")
def channel_status():
    repo = get_notification_settings_repository()
    from app.repositories.smtp_config_repository import get_smtp_config_repository

    smtp = get_smtp_config_repository().get_config()
    webhook = repo.get("webhook")
    dingtalk = repo.get("dingtalk")
    feishu = repo.get("feishu")
    return jsonify(
        {
            "success": True,
            "data": {
                "email": {
                    "status": "configured" if smtp else "needs_configuration",
                    "verified": bool(smtp and smtp.get("is_verified")),
                },
                "webhook": {
                    "status": (
                        "disabled"
                        if webhook and not webhook.get("enabled")
                        else ("enabled" if webhook else "needs_configuration")
                    )
                },
                "dingtalk_bot": {
                    "status": "available",
                    "fallback_secret_configured": bool(
                        dingtalk and dingtalk.get("fallback_webhook_secret_configured")
                    ),
                },
                "feishu_bot": {"status": "no_configuration_required"},
                "feishu_app": {"status": "configured" if feishu else "needs_configuration"},
                "dingtalk_app": {
                    "status": (
                        "configured"
                        if dingtalk
                        and dingtalk.get("app_key")
                        and dingtalk.get("app_secret_configured")
                        else "needs_configuration"
                    )
                },
            },
        }
    )


@notification_integrations_bp.route("/management/webhook-config", methods=["GET", "PUT", "DELETE"])
def webhook_config():
    repo = get_notification_settings_repository()
    if request.method == "GET":
        return jsonify({"success": True, "data": repo.get("webhook")})
    if request.method == "DELETE":
        deleted = repo.delete("webhook")
        audit_logger.log_action(
            action=AuditAction.WEBHOOK_CONFIG_DELETE,
            user_id=_user_id(),
            resource_type="webhook_config",
            details={"deleted": deleted},
        )
        return jsonify({"success": deleted})
    data = request.get_json(silent=True) or {}
    allowed = {"webhook_secret", "allow_private_webhook_urls", "enabled"}
    values = {k: data[k] for k in allowed if k in data}
    saved = repo.save("webhook", values, _user_id())
    audit_logger.log_action(
        action=AuditAction.WEBHOOK_CONFIG_SAVE,
        user_id=_user_id(),
        resource_type="webhook_config",
        details={
            "secret_updated": "webhook_secret" in values,
            "enabled": values.get("enabled"),
            "allow_private_webhook_urls": values.get("allow_private_webhook_urls"),
        },
    )
    return jsonify({"success": True, "data": saved})


@notification_integrations_bp.route("/management/dingtalk-config", methods=["GET", "PUT", "DELETE"])
def dingtalk_config():
    repo = get_notification_settings_repository()
    if request.method == "GET":
        return jsonify({"success": True, "data": repo.get("dingtalk")})
    if request.method == "DELETE":
        deleted = repo.delete("dingtalk")
        audit_logger.log_action(
            action=AuditAction.DINGTALK_CONFIG_DELETE,
            user_id=_user_id(),
            resource_type="dingtalk_config",
            details={"deleted": deleted},
        )
        return jsonify({"success": deleted})
    data = request.get_json(silent=True) or {}
    allowed = {
        "app_key",
        "app_secret",
        "fallback_webhook_secret",
        "sync_enabled",
        "target_tenant_id",
        "interval_minutes",
        "root_dept_id",
        "max_runtime_seconds",
        "auto_recovery",
    }
    values = {k: data[k] for k in allowed if k in data}
    saved = repo.save("dingtalk", values, _user_id())
    audit_logger.log_action(
        action=AuditAction.DINGTALK_CONFIG_SAVE,
        user_id=_user_id(),
        resource_type="dingtalk_config",
        details={
            "app_key_updated": "app_key" in values,
            "app_secret_updated": "app_secret" in values,
            "fallback_secret_updated": "fallback_webhook_secret" in values,
            "sync_enabled": values.get("sync_enabled"),
            "target_tenant_id": values.get("target_tenant_id"),
        },
    )
    return jsonify({"success": True, "data": saved})


def _dingtalk_oapi_request(token: str, url: str, json_payload: dict) -> dict:
    """Call a DingTalk oapi endpoint via *safe_request* and return the payload.

    Raises ``ValueError`` with a human-readable message when the API returns a
    non-zero *errcode*.  The access token is passed as a query parameter per
    DingTalk's oapi convention.
    """
    response = safe_request(
        "POST",
        url,
        params={"access_token": token},
        json=json_payload,
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    errcode = payload.get("errcode", 0)
    if errcode == 0:
        return payload
    errmsg = payload.get("errmsg") or payload.get("message") or "unknown error"
    raise ValueError(f"DingTalk API error (errcode={errcode}): {errmsg}")


@notification_integrations_bp.post("/management/dingtalk-config/test")
def test_dingtalk_config():
    """Test DingTalk credentials **and** organisation-sync permissions.

    The test performs the following checks sequentially:

    1. **access_token** – exchange AppKey/AppSecret for an access token.
    2. **department_list** – list child departments of the root department
       (verifies the *通讯录部门读取* permission).
    3. **user_list** – list one user from the root department (verifies the
       *成员信息读取* permission).

    The response keeps the legacy ``success`` / ``message`` fields for backward
    compatibility and adds a ``checks`` dict with per-check details.
    """
    data = request.get_json(silent=True) or {}
    saved = get_notification_settings_repository().get("dingtalk", include_secrets=True) or {}
    app_key = data.get("app_key") or saved.get("app_key")
    app_secret = data.get("app_secret") or saved.get("app_secret")
    if not app_key or not app_secret:
        return jsonify({"success": False, "message": "AppKey and AppSecret are required"}), 400

    checks: dict[str, dict[str, str]] = {}

    # ---- 1. access_token check ----
    try:
        response = safe_request(
            "POST",
            "https://api.dingtalk.com/v1.0/oauth2/accessToken",
            json={"appKey": app_key, "appSecret": app_secret},
            timeout=5,
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("accessToken") or payload.get("access_token")
        if not token:
            checks["access_token"] = {"status": "failed", "message": "No access token returned"}
            return jsonify({
                "success": False,
                "message": "DingTalk did not return an access token",
                "checks": checks,
            })
        checks["access_token"] = {"status": "passed", "message": "Access token obtained"}
    except OutboundUrlBlockedError as e:
        logger.error("DingTalk connection test blocked by SSRF protection: %s", e)
        checks["access_token"] = {
            "status": "failed",
            "message": f"Request blocked by security policy: {e}",
        }
        return jsonify({
            "success": False,
            "message": f"Request blocked by security policy: {e}",
            "checks": checks,
        }), 403
    except Exception:
        logger.warning("DingTalk connection test failed", exc_info=True)
        checks["access_token"] = {"status": "failed", "message": "Failed to obtain access token"}
        return jsonify({
            "success": False,
            "message": "DingTalk connection test failed",
            "checks": checks,
        }), 502

    # ---- 2. department_list check ----
    try:
        _dingtalk_oapi_request(
            token,
            "https://oapi.dingtalk.com/topapi/v2/department/listsub",
            {"dept_id": 1},
        )
        checks["department_list"] = {"status": "passed", "message": "Department list accessible"}
    except ValueError as exc:
        checks["department_list"] = {
            "status": "failed",
            "message": f"Department read permission missing ({exc})",
        }
    except Exception:
        logger.warning("DingTalk department list check failed", exc_info=True)
        checks["department_list"] = {
            "status": "failed",
            "message": "Failed to query department list",
        }

    # ---- 3. user_list check ----
    try:
        _dingtalk_oapi_request(
            token,
            "https://oapi.dingtalk.com/topapi/v2/user/list",
            {"dept_id": 1, "cursor": 0, "size": 1},
        )
        checks["user_list"] = {"status": "passed", "message": "User list accessible"}
    except ValueError as exc:
        checks["user_list"] = {
            "status": "failed",
            "message": f"Member read permission missing ({exc})",
        }
    except Exception:
        logger.warning("DingTalk user list check failed", exc_info=True)
        checks["user_list"] = {
            "status": "failed",
            "message": "Failed to query user list",
        }

    # ---- Overall result ----
    all_passed = all(c["status"] == "passed" for c in checks.values())
    failed_checks = [name for name, c in checks.items() if c["status"] != "passed"]

    if all_passed:
        message = "DingTalk connection test successful"
    else:
        message = (
            "DingTalk connection test: some checks failed – "
            + ", ".join(failed_checks)
        )

    return jsonify({
        "success": all_passed,
        "message": message,
        "checks": checks,
    })
