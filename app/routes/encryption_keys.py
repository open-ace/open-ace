"""
Open ACE - Encryption Key Management Routes

API 端点：
- GET  /api/encryption-keys         - 获取所有密钥元数据
- POST /api/encryption-keys/validate - 验证密钥格式
- POST /api/encryption-keys/rotate   - 执行密钥轮换
- POST /api/encryption-keys/generate-env-config - 生成环境变量配置
- GET  /api/encryption-keys/audit-log - 查询审计日志
"""

from __future__ import annotations

import logging

from flask import Blueprint, g, jsonify, request

from app.auth.decorators import admin_required, platform_admin_required
from app.repositories.database import Database
from app.services.encryption_key_service import EncryptionKeyService

logger = logging.getLogger(__name__)

encryption_keys_bp = Blueprint("encryption_keys", __name__)


def get_encryption_key_service() -> EncryptionKeyService:
    """获取加密密钥服务实例"""
    db = Database()
    return EncryptionKeyService(db=db)


@encryption_keys_bp.route("/api/encryption-keys", methods=["GET"])
@platform_admin_required
def get_encryption_keys():
    """获取所有加密密钥元数据

    权限: platform_admin only
    """
    try:
        service = get_encryption_key_service()
        result = service.get_encryption_keys()

        return jsonify(result)

    except Exception as e:
        logger.error(f"Failed to get encryption keys: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@encryption_keys_bp.route("/api/encryption-keys/validate", methods=["POST"])
@platform_admin_required
def validate_key():
    """验证密钥格式

    权限: platform_admin only

    请求体:
        {
            "key": "密钥字符串（可选，用于导入场景）"
        }
    """
    try:
        data = request.get_json() or {}
        key = data.get("key")

        service = get_encryption_key_service()

        # 如果没有提供密钥，生成并验证新密钥
        if not key:
            new_key = service.generate_new_key()
            validation = service.validate_key_format(new_key)
            return jsonify(
                {
                    "success": True,
                    "valid": validation["valid"],
                    "fingerprint": validation["fingerprint"],
                    "error": validation["error"],
                    "generated_key": new_key,  # 仅在生成模式下返回
                }
            )

        # 验证提供的密钥
        validation = service.validate_key_format(key)

        return jsonify(
            {
                "success": True,
                "valid": validation["valid"],
                "fingerprint": validation["fingerprint"],
                "error": validation["error"],
            }
        )

    except Exception as e:
        logger.error(f"Key validation failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@encryption_keys_bp.route("/api/encryption-keys/rotate", methods=["POST"])
@platform_admin_required
def rotate_key():
    """执行密钥轮换

    权限: platform_admin only

    请求体:
        {
            "confirmation": "ROTATE",
            "expected_version": 12345678  // 可选，用于乐观锁
        }
    """
    try:
        data = request.get_json() or {}
        confirmation = data.get("confirmation", "")
        expected_version = data.get("expected_version")

        # 获取操作者信息
        operator = g.user.get("email", g.user.get("username", "unknown"))
        ip_address = request.remote_addr

        service = get_encryption_key_service()
        result = service.rotate_key(
            confirmation=confirmation,
            expected_version=expected_version,
            operator=operator,
            ip_address=ip_address,
        )

        if result["success"]:
            return jsonify(result)
        else:
            status_code = 409 if result.get("error") in ["rotation_in_progress", "config_version_conflict"] else 400
            return jsonify(result), status_code

    except Exception as e:
        logger.error(f"Key rotation failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@encryption_keys_bp.route("/api/encryption-keys/generate-env-config", methods=["POST"])
@platform_admin_required
def generate_env_config():
    """生成新的环境变量配置（供外部系统使用）

    权限: platform_admin only
    """
    try:
        service = get_encryption_key_service()
        result = service.generate_env_config()

        return jsonify(result)

    except Exception as e:
        logger.error(f"Failed to generate env config: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@encryption_keys_bp.route("/api/encryption-keys/audit-log", methods=["GET"])
@platform_admin_required
def get_audit_log():
    """查询密钥操作审计日志

    权限: platform_admin only

    查询参数:
        - limit: 返回条数（默认 50）
        - offset: 偏移量（分页）
        - action: 操作类型过滤（rotate, re-encrypt）
    """
    try:
        limit = request.args.get("limit", 50, type=int)
        offset = request.args.get("offset", 0, type=int)
        action = request.args.get("action")

        service = get_encryption_key_service()
        result = service.get_audit_log(limit=limit, offset=offset, action=action)

        return jsonify(result)

    except Exception as e:
        logger.error(f"Failed to get audit log: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@encryption_keys_bp.route("/api/encryption-keys/sync-status", methods=["GET"])
@platform_admin_required
def get_sync_status():
    """获取多副本同步状态

    权限: platform_admin only
    """
    try:
        from app.utils.encryption_key_registry import get_registry

        registry = get_registry()

        # 获取本地配置版本
        local_version = registry.get_config_version()

        # 尝试获取其他副本的版本信息
        # 简化方案：从配置的其他副本地址查询
        replica_endpoints = os.environ.get("OPENACE_REPLICA_ENDPOINTS", "").split(",")
        replica_endpoints = [ep.strip() for ep in replica_endpoints if ep.strip()]

        remote_versions = {}

        if replica_endpoints:
            # 查询其他副本
            import requests

            for endpoint in replica_endpoints:
                try:
                    response = requests.get(f"http://{endpoint}/api/health", timeout=5)
                    if response.status_code == 200:
                        data = response.json()
                        remote_versions[endpoint] = data.get("encryption_key_config_version")
                except Exception as e:
                    logger.warning(f"Failed to query replica {endpoint}: {e}")
                    remote_versions[endpoint] = None

        # 判断同步状态
        if remote_versions:
            versions = [local_version] + list(remote_versions.values())
            all_match = all(v == local_version for v in versions if v is not None)
            sync_status = "synchronized" if all_match else "diverged"
        else:
            sync_status = "unknown"

        return jsonify(
            {
                "success": True,
                "local_version": local_version,
                "remote_versions": remote_versions,
                "sync_status": sync_status,
            }
        )

    except Exception as e:
        logger.error(f"Failed to get sync status: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@encryption_keys_bp.route("/api/encryption-keys/re-encrypt/pre-check", methods=["POST"])
@platform_admin_required
def re_encrypt_pre_check():
    """re-encrypt 前预检查存量密文格式

    权限: platform_admin only
    """
    try:
        from app.utils.encryption_key_registry import get_registry

        registry = get_registry()
        db = Database()

        # 扫描所有加密字段
        # 1. SSO providers
        sso_secrets = db.fetch_all("SELECT name, config FROM sso_providers WHERE config IS NOT NULL")

        # 2. API keys
        api_keys = db.fetch_all("SELECT id, encrypted_key FROM api_keys WHERE encrypted_key IS NOT NULL")

        # 统计密文格式
        ciphertext_stats = {
            "total": 0,
            "with_key_id_prefix": 0,
            "legacy_format": 0,
        }

        failed_items = []

        # 检查 SSO secrets
        for row in sso_secrets:
            try:
                config = json.loads(row["config"])
                encrypted = config.get("client_secret_encrypted", "")

                if encrypted:
                    ciphertext_stats["total"] += 1

                    if encrypted.startswith("v1k"):
                        ciphertext_stats["with_key_id_prefix"] += 1
                    else:
                        ciphertext_stats["legacy_format"] += 1

                    # 尝试解密
                    result = registry.decrypt(encrypted)
                    if result is None:
                        failed_items.append(
                            {
                                "type": "sso_provider",
                                "name": row["name"],
                                "error": "Decryption failed",
                            }
                        )

            except Exception as e:
                failed_items.append({"type": "sso_provider", "name": row["name"], "error": str(e)})

        # 检查 API keys
        for row in api_keys:
            try:
                encrypted = row["encrypted_key"]

                if encrypted:
                    ciphertext_stats["total"] += 1

                    if encrypted.startswith("v1k"):
                        ciphertext_stats["with_key_id_prefix"] += 1
                    else:
                        ciphertext_stats["legacy_format"] += 1

                    # 尝试解密
                    result = registry.decrypt(encrypted)
                    if result is None:
                        failed_items.append(
                            {
                                "type": "api_key",
                                "id": row["id"],
                                "error": "Decryption failed",
                            }
                        )

            except Exception as e:
                failed_items.append({"type": "api_key", "id": row["id"], "error": str(e)})

        all_decryptable = len(failed_items) == 0

        # 生成建议
        recommendations = []
        if all_decryptable:
            recommendations.append("所有密文可以正常解密，可以安全执行 re-encrypt")
            recommendations.append("re-encrypt 后所有密文将使用新密钥并带 key_id 前缀")
        else:
            recommendations.append(f"发现 {len(failed_items)} 个无法解密的密文，请先处理")
            recommendations.append("建议检查这些密文是否使用了已丢失的密钥")

        return jsonify(
            {
                "success": True,
                "ciphertext_stats": ciphertext_stats,
                "decryption_test": {
                    "all_decryptable": all_decryptable,
                    "failed_count": len(failed_items),
                    "failed_items": failed_items[:10],  # 只返回前 10 个
                },
                "recommendations": recommendations,
            }
        )

    except Exception as e:
        logger.error(f"Pre-check failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@encryption_keys_bp.route("/api/encryption-keys/re-encrypt", methods=["POST"])
@platform_admin_required
def re_encrypt():
    """重新加密所有存量密文

    权限: platform_admin only

    请求体:
        {
            "confirmation": "RE-ENCRYPT",
            "batch_size": 100  // 可选，批次大小
        }
    """
    try:
        data = request.get_json() or {}
        confirmation = data.get("confirmation", "")
        batch_size = data.get("batch_size", 100)

        # 验证确认文本
        if confirmation != "RE-ENCRYPT":
            return jsonify(
                {
                    "success": False,
                    "error": "invalid_confirmation",
                    "message": "确认文本必须为 'RE-ENCRYPT'",
                }
            ), 400

        from app.utils.encryption_key_registry import get_registry

        registry = get_registry()
        db = Database()

        stats = {
            "sso_providers": 0,
            "api_keys": 0,
            "smtp_passwords": 0,
        }

        failed = []

        # 重新加密 SSO secrets
        sso_secrets = db.fetch_all("SELECT name, config FROM sso_providers WHERE config IS NOT NULL")

        for row in sso_secrets:
            try:
                config = json.loads(row["config"])
                encrypted = config.get("client_secret_encrypted", "")

                if encrypted:
                    # 解密
                    result = registry.decrypt(encrypted)
                    if result:
                        plaintext, old_key_id = result

                        # 重新加密
                        new_encrypted = registry.encrypt(plaintext)

                        # 更新
                        config["client_secret_encrypted"] = new_encrypted
                        db.execute(
                            "UPDATE sso_providers SET config = ? WHERE name = ?",
                            (json.dumps(config), row["name"]),
                        )

                        stats["sso_providers"] += 1
                    else:
                        failed.append(
                            {
                                "type": "sso_provider",
                                "name": row["name"],
                                "error": "Decryption failed",
                            }
                        )

            except Exception as e:
                failed.append({"type": "sso_provider", "name": row["name"], "error": str(e)})

        # 重新加密 API keys
        api_keys = db.fetch_all("SELECT id, encrypted_key FROM api_keys WHERE encrypted_key IS NOT NULL")

        for row in api_keys:
            try:
                encrypted = row["encrypted_key"]

                # 解密
                result = registry.decrypt(encrypted)
                if result:
                    plaintext, old_key_id = result

                    # 重新加密
                    new_encrypted = registry.encrypt(plaintext)

                    # 更新
                    db.execute(
                        "UPDATE api_keys SET encrypted_key = ? WHERE id = ?",
                        (new_encrypted, row["id"]),
                    )

                    stats["api_keys"] += 1
                else:
                    failed.append(
                        {
                            "type": "api_key",
                            "id": row["id"],
                            "error": "Decryption failed",
                        }
                    )

            except Exception as e:
                failed.append({"type": "api_key", "id": row["id"], "error": str(e)})

        # TODO: 重新加密其他加密字段（SMTP passwords 等）

        return jsonify(
            {
                "success": True,
                "re_encrypted": stats,
                "failed": failed[:50],  # 只返回前 50 个
                "retry_endpoint": "/api/encryption-keys/re-encrypt/retry",
            }
        )

    except Exception as e:
        logger.error(f"Re-encrypt failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# 导入需要的模块
import json
import os