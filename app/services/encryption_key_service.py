"""
Open ACE - Encryption Key Management Service

提供加密密钥管理的核心业务逻辑，包括：
- 密钥格式验证
- 密钥生成
- 密钥轮换
- 数据一致性验证
- 环境变量更新
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.repositories.database import Database
from app.utils.encryption_key_registry import EncryptionKeyRegistry, KeyStatus, get_registry

logger = logging.getLogger(__name__)


class EncryptionKeyService:
    """加密密钥管理服务"""

    def __init__(self, db: Database | None = None):
        """初始化服务

        Args:
            db: 数据库实例，如果为 None 则创建新实例
        """
        self.db = db or Database()
        self.registry = get_registry()

    def validate_key_format(self, key: str) -> dict:
        """验证密钥格式是否符合 Fernet 标准

        Args:
            key: 密钥字符串

        Returns:
            验证结果字典，包含 valid, fingerprint, error 字段
        """
        try:
            # 验证 base64 URL-safe 编码
            try:
                decoded = base64.urlsafe_b64decode(key)
            except Exception as e:
                return {"valid": False, "fingerprint": None, "error": f"无效的 base64 编码: {str(e)}"}

            # 验证长度
            if len(decoded) != 32:
                return {
                    "valid": False,
                    "fingerprint": None,
                    "error": f"密钥长度必须为 32 字节，当前为 {len(decoded)} 字节",
                }

            # 验证能否创建 Fernet 对象
            try:
                Fernet(key.encode() if isinstance(key, str) else key)
            except Exception as e:
                return {"valid": False, "fingerprint": None, "error": f"无法创建 Fernet 对象: {str(e)}"}

            # 计算指纹
            fingerprint = self._compute_fingerprint(key)

            return {"valid": True, "fingerprint": fingerprint, "error": None}

        except Exception as e:
            return {"valid": False, "fingerprint": None, "error": f"验证失败: {str(e)}"}

    def generate_new_key(self) -> str:
        """生成新的 Fernet 兼容密钥

        Returns:
            32 字节的 base64 URL-safe 编码密钥
        """
        # 使用 Fernet.generate_key() 生成标准密钥
        key = Fernet.generate_key()
        return key.decode()

    def rotate_key(
        self,
        confirmation: str,
        expected_version: int | None = None,
        operator: str = "system",
        ip_address: str = "unknown",
    ) -> dict:
        """执行密钥轮换（含重试机制）

        Args:
            confirmation: 确认文本（必须为 "ROTATE"）
            expected_version: 预期的配置版本（乐观锁）
            operator: 操作者
            ip_address: 操作者 IP

        Returns:
            轮换结果
        """
        # 验证确认文本
        if confirmation != "ROTATE":
            return {
                "success": False,
                "error": "invalid_confirmation",
                "message": "确认文本必须为 'ROTATE'",
            }

        # 尝试获取分布式锁
        lock_acquired = self._acquire_rotation_lock()
        if not lock_acquired:
            return {
                "success": False,
                "error": "rotation_in_progress",
                "message": "已有轮换操作正在执行，请稍后再试",
            }

        try:
            # 验证版本
            if expected_version is not None:
                current_version = self.registry.get_config_version()
                if current_version != expected_version:
                    return {
                        "success": False,
                        "error": "config_version_conflict",
                        "message": "配置版本已变更，请刷新后重试",
                        "current_version": current_version,
                    }

            # 生成新密钥
            new_key = self.generate_new_key()

            # 验证新密钥格式
            validation = self.validate_key_format(new_key)
            if not validation["valid"]:
                return {
                    "success": False,
                    "error": "invalid_key_format",
                    "message": f"生成的密钥格式无效: {validation['error']}",
                }

            # 两阶段提交
            # Phase 1: 更新数据库
            result = self._update_database(new_key, operator, ip_address)

            if not result["success"]:
                return result

            # Phase 2: 更新环境变量（含重试）
            env_result = self._update_environment_variable_with_retry(new_key)

            if not env_result["success"]:
                # 回滚数据库
                self._rollback_database(result["old_key_id"])
                return {
                    "success": False,
                    "error": "env_update_failed",
                    "message": env_result.get("message", "环境变量更新失败"),
                    "manual_instructions": env_result.get("manual_instructions"),
                }

            # Phase 3: 验证热加载
            self.registry.reload()

            # 验证新密钥可用
            test_encrypted = self.registry.encrypt("test")
            test_decrypted = self.registry.decrypt(test_encrypted)

            if test_decrypted is None or test_decrypted[0] != "test":
                # 回滚
                self._rollback_database(result["old_key_id"])
                return {
                    "success": False,
                    "error": "validation_failed",
                    "message": "密钥验证失败",
                }

            return {
                "success": True,
                "new_key_id": result["new_key_id"],
                "previous_key_id": result["old_key_id"],
                "previous_key_status": "deprecated",
                "rotated_at": result["rotated_at"],
                "new_config_version": result["new_config_version"],
            }

        except Exception as e:
            logger.error(f"Key rotation failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": "unexpected_error",
                "message": f"轮换失败: {str(e)}",
            }
        finally:
            # 释放锁
            self._release_rotation_lock()

    def get_encryption_keys(self) -> dict:
        """获取所有加密密钥元数据

        Returns:
            密钥列表和状态信息
        """
        # 从数据库获取元数据
        keys_data = self.db.fetch_all(
            """
            SELECT key_id, key_fingerprint, status, created_at, rotated_at,
                   config_version, last_used_at
            FROM encryption_keys
            ORDER BY key_id
            """
        )

        keys = []
        for row in keys_data:
            keys.append(
                {
                    "key_id": row["key_id"],
                    "fingerprint": row["key_fingerprint"],
                    "status": row["status"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "rotated_at": row["rotated_at"].isoformat() if row["rotated_at"] else None,
                    "config_version": row["config_version"],
                    "last_used_at": row["last_used_at"].isoformat() if row["last_used_at"] else None,
                }
            )

        # 获取配置信息
        config_version = self.registry.get_config_version()
        primary_key_id = self.registry.get_primary_key_id()

        # 检查一致性
        consistency_status = self._check_consistency()

        # 检查是否有轮换进行中
        rotation_in_progress = self._is_rotation_in_progress()

        return {
            "success": True,
            "keys": keys,
            "config_version": config_version,
            "primary_key_id": primary_key_id,
            "rotation_in_progress": rotation_in_progress,
            "consistency_status": consistency_status,
        }

    def sync_keys_from_env_to_db(self, dry_run: bool = False) -> dict:
        """从环境变量同步密钥元数据到数据库

        Args:
            dry_run: 是否为预览模式

        Returns:
            同步结果统计
        """
        keys_json = os.environ.get("OPENACE_ENCRYPTION_KEYS")
        keys = []

        if keys_json:
            try:
                config = json.loads(keys_json)
                keys_list = config.get("keys", [])
                primary_key_id = config.get("primary_key_id")

                for key_info in keys_list:
                    key_id = key_info.get("id")
                    key_value = key_info.get("value")
                    status_str = key_info.get("status", "deprecated")

                    if key_id is not None and key_value:
                        fingerprint = self._compute_fingerprint(key_value)
                        is_primary = key_id == primary_key_id
                        keys.append(
                            {
                                "key_id": key_id,
                                "fingerprint": fingerprint,
                                "status": "active" if is_primary else status_str,
                            }
                        )
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"Failed to parse OPENACE_ENCRYPTION_KEYS: {e}")
        else:
            # 单密钥格式
            key_value = os.environ.get("OPENACE_ENCRYPTION_KEY")
            if key_value:
                fingerprint = self._compute_fingerprint(key_value)
                keys.append({"key_id": 0, "fingerprint": fingerprint, "status": "active"})

        if not keys:
            logger.warning("No encryption keys found in environment variables")
            return {"inserted": 0, "skipped": 0, "errors": 0}

        # 查询现有记录
        existing = self.db.fetch_all("SELECT key_fingerprint FROM encryption_keys")
        existing_fingerprints = {row["key_fingerprint"] for row in existing}

        inserted = 0
        skipped = 0
        errors = 0

        for key in keys:
            fingerprint = key["fingerprint"]
            status = key["status"]

            if fingerprint in existing_fingerprints:
                logger.info(f"Key with fingerprint {fingerprint} already exists, skipping")
                skipped += 1
                continue

            try:
                if not dry_run:
                    self.db.execute(
                        """
                        INSERT INTO encryption_keys (key_fingerprint, status, config_version, created_at)
                        VALUES (?, ?, 1, datetime('now'))
                        """,
                        (fingerprint, status),
                    )
                    logger.info(f"Inserted key with fingerprint {fingerprint}")

                inserted += 1
            except Exception as e:
                logger.error(f"Failed to insert key: {e}")
                errors += 1

        return {"inserted": inserted, "skipped": skipped, "errors": errors}

    def validate_encryption_keys_consistency(self) -> dict:
        """验证环境变量与数据库的一致性

        Returns:
            一致性检查结果
        """
        # 从环境变量获取密钥指纹
        env_fingerprints = self._get_env_key_fingerprints()

        # 从数据库获取密钥指纹
        db_keys = self.db.fetch_all("SELECT key_id, key_fingerprint, status FROM encryption_keys")
        db_fingerprints = {row["key_fingerprint"] for row in db_keys}

        # 检查一致性
        env_only = env_fingerprints - db_fingerprints
        db_only = db_fingerprints - env_fingerprints

        if not env_only and not db_only:
            return {
                "consistent": True,
                "message": "环境变量与数据库一致",
                "env_only": [],
                "db_only": [],
            }

        return {
            "consistent": False,
            "message": "环境变量与数据库不一致",
            "env_only": list(env_only),
            "db_only": list(db_only),
        }

    def generate_env_config(self) -> dict:
        """生成新的环境变量配置（供外部系统使用）

        Returns:
            环境变量配置
        """
        # 获取当前密钥配置
        keys_json = os.environ.get("OPENACE_ENCRYPTION_KEYS")

        if keys_json:
            try:
                config = json.loads(keys_json)
            except json.JSONDecodeError:
                config = {}
        else:
            # 单密钥格式，转换为多密钥格式
            key_value = os.environ.get("OPENACE_ENCRYPTION_KEY")
            if key_value:
                config = {
                    "keys": [{"id": 0, "value": key_value, "status": "active"}],
                    "primary_key_id": 0,
                }
            else:
                config = {}

        env_var_value = json.dumps(config, indent=2)

        return {
            "success": True,
            "env_var_name": "OPENACE_ENCRYPTION_KEYS",
            "env_var_value": env_var_value,
            "instructions": "请将以上配置更新到环境变量中并重启服务或触发热加载",
            "config_file_example": f"OPENACE_ENCRYPTION_KEYS='{env_var_value}'",
        }

    def get_audit_log(self, limit: int = 50, offset: int = 0, action: str | None = None) -> dict:
        """查询密钥操作审计日志

        Args:
            limit: 返回条数
            offset: 偏移量
            action: 操作类型过滤

        Returns:
            审计日志列表
        """
        # 这里需要从审计日志表查询
        # 暂时返回空列表，后续可以集成到现有的审计日志系统
        # TODO: 集成到 app/modules/governance/audit_logger.py

        return {"success": True, "logs": [], "total": 0}

    # ==================== 私有方法 ====================

    def _compute_fingerprint(self, key_value: str) -> str:
        """计算密钥指纹

        Args:
            key_value: 密钥值

        Returns:
            指纹字符串（sha256:前16字符）
        """
        derived_key = hashlib.sha256(key_value.encode()).digest()
        fingerprint = hashlib.sha256(derived_key).hexdigest()[:16]
        return f"sha256:{fingerprint}"

    def _get_env_key_fingerprints(self) -> set:
        """从环境变量获取密钥指纹集合

        Returns:
            密钥指纹集合
        """
        fingerprints = set()

        keys_json = os.environ.get("OPENACE_ENCRYPTION_KEYS")

        if keys_json:
            try:
                config = json.loads(keys_json)
                keys_list = config.get("keys", [])

                for key_info in keys_list:
                    key_value = key_info.get("value")
                    if key_value:
                        fingerprint = self._compute_fingerprint(key_value)
                        fingerprints.add(fingerprint)
            except (json.JSONDecodeError, KeyError):
                pass
        else:
            key_value = os.environ.get("OPENACE_ENCRYPTION_KEY")
            if key_value:
                fingerprint = self._compute_fingerprint(key_value)
                fingerprints.add(fingerprint)

        return fingerprints

    def _acquire_rotation_lock(self) -> bool:
        """获取轮换锁

        Returns:
            是否成功获取锁
        """
        # 优先尝试 Redis 锁
        redis_lock = self._acquire_redis_lock()
        if redis_lock is not None:
            return redis_lock

        # Redis 不可用，尝试数据库锁
        return self._acquire_db_lock()

    def _acquire_redis_lock(self) -> bool | None:
        """尝试获取 Redis 锁

        Returns:
            True: 成功获取
            False: 锁被占用
            None: Redis 不可用
        """
        try:
            import redis

            redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
            client = redis.from_url(redis_url)

            # 尝试设置锁
            result = client.set("encryption:rotation_lock", "locked", nx=True, ex=300)

            if result:
                logger.info("Acquired Redis rotation lock")
                return True
            else:
                logger.info("Redis rotation lock already held")
                return False

        except Exception as e:
            logger.warning(f"Redis lock failed: {e}, falling back to database lock")
            return None

    def _acquire_db_lock(self) -> bool:
        """获取数据库锁

        Returns:
            是否成功获取锁
        """
        try:
            # 使用 PostgreSQL advisory lock
            result = self.db.fetch_one("SELECT pg_try_advisory_lock(12345)")

            if result and result.get("pg_try_advisory_lock"):
                logger.info("Acquired database rotation lock")
                return True
            else:
                logger.info("Database rotation lock already held")
                return False

        except Exception as e:
            logger.error(f"Database lock failed: {e}")
            return False

    def _release_rotation_lock(self):
        """释放轮换锁"""
        # 尝试释放 Redis 锁
        try:
            import redis

            redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
            client = redis.from_url(redis_url)
            client.delete("encryption:rotation_lock")
            logger.info("Released Redis rotation lock")
            return
        except Exception:
            pass

        # 尝试释放数据库锁
        try:
            self.db.execute("SELECT pg_advisory_unlock(12345)")
            logger.info("Released database rotation lock")
        except Exception as e:
            logger.warning(f"Failed to release database lock: {e}")

    def _is_rotation_in_progress(self) -> bool:
        """检查是否有轮换操作正在进行

        Returns:
            是否有轮换进行中
        """
        # 检查 Redis 锁
        try:
            import redis

            redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
            client = redis.from_url(redis_url)
            return client.exists("encryption:rotation_lock")
        except Exception:
            pass

        # 检查数据库锁
        try:
            # 如果能获取锁，说明没有轮换进行
            result = self.db.fetch_one("SELECT pg_try_advisory_lock(12345)")
            if result and result.get("pg_try_advisory_lock"):
                # 立即释放
                self.db.execute("SELECT pg_advisory_unlock(12345)")
                return False
            else:
                return True
        except Exception:
            return False

    def _update_database(self, new_key: str, operator: str, ip_address: str) -> dict:
        """更新数据库元数据

        Args:
            new_key: 新密钥
            operator: 操作者
            ip_address: IP 地址

        Returns:
            更新结果
        """
        try:
            # 获取当前配置版本
            current_version = self.registry.get_config_version()

            # 计算新密钥指纹
            new_fingerprint = self._compute_fingerprint(new_key)

            # 获取旧密钥 ID
            old_primary_id = self.registry.get_primary_key_id()

            # 开始事务
            # 1. 插入新密钥
            self.db.execute(
                """
                INSERT INTO encryption_keys (key_fingerprint, status, config_version, created_at)
                VALUES (?, 'active', ?, datetime('now'))
                """,
                (new_fingerprint, current_version + 1),
            )

            # 2. 获取新密钥 ID
            new_key_row = self.db.fetch_one(
                "SELECT key_id, created_at FROM encryption_keys WHERE key_fingerprint = ?",
                (new_fingerprint,),
            )

            if not new_key_row:
                raise RuntimeError("Failed to retrieve new key ID")

            new_key_id = new_key_row["key_id"]
            rotated_at = new_key_row["created_at"]

            # 3. 更新旧密钥状态
            self.db.execute(
                """
                UPDATE encryption_keys
                SET status = 'deprecated', rotated_at = datetime('now')
                WHERE key_id = ?
                """,
                (old_primary_id,),
            )

            # 4. 记录审计日志（TODO: 集成到审计日志系统）

            return {
                "success": True,
                "new_key_id": new_key_id,
                "old_key_id": old_primary_id,
                "rotated_at": rotated_at.isoformat() if rotated_at else None,
                "new_config_version": current_version + 1,
            }

        except Exception as e:
            logger.error(f"Database update failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _update_environment_variable_with_retry(self, new_key: str, max_retries: int = 3) -> dict:
        """更新环境变量（含重试机制）

        Args:
            new_key: 新密钥
            max_retries: 最大重试次数

        Returns:
            更新结果
        """
        for attempt in range(max_retries):
            try:
                result = self._update_environment_variable(new_key)

                if result["success"]:
                    return result

                # 检查是否为可恢复错误
                error = result.get("error", "")
                if "network" in error.lower() or "permission" in error.lower():
                    if attempt < max_retries - 1:
                        logger.warning(f"Env update failed (attempt {attempt + 1}), retrying in 5s")
                        time.sleep(5)
                        continue

                return result

            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Env update exception (attempt {attempt + 1}): {e}, retrying")
                    time.sleep(5)
                else:
                    return {
                        "success": False,
                        "error": str(e),
                        "message": f"环境变量更新失败（重试 {max_retries} 次后）",
                        "manual_instructions": "请使用 /api/encryption-keys/generate-env-config 获取配置并手动更新",
                    }

        return result

    def _update_environment_variable(self, new_key: str) -> dict:
        """更新环境变量

        Args:
            new_key: 新密钥

        Returns:
            更新结果
        """
        # 方案选择：根据环境选择不同的实现

        # 检查是否为 Kubernetes 环境
        if os.environ.get("KUBERNETES_SERVICE_HOST"):
            return self._update_kubernetes_secret(new_key)

        # 传统部署环境
        return self._update_config_file(new_key)

    def _update_kubernetes_secret(self, new_key: str) -> dict:
        """更新 Kubernetes Secret

        Args:
            new_key: 新密钥

        Returns:
            更新结果
        """
        try:
            # 检查是否安装 kubernetes 库
            try:
                from kubernetes import client, config
            except ImportError:
                return {
                    "success": False,
                    "error": "kubernetes_library_not_installed",
                    "message": "未安装 kubernetes Python 库",
                    "manual_instructions": "请手动更新 Kubernetes Secret",
                }

            # 加载配置
            try:
                config.load_incluster_config()
            except Exception:
                # 如果集群内配置失败，尝试本地配置
                try:
                    config.load_kube_config()
                except Exception as e:
                    return {
                        "success": False,
                        "error": "kubernetes_config_failed",
                        "message": f"Kubernetes 配置加载失败: {str(e)}",
                        "manual_instructions": "请手动更新 Kubernetes Secret",
                    }

            # 构建新的密钥配置
            keys_json = os.environ.get("OPENACE_ENCRYPTION_KEYS", "{}")
            try:
                config_dict = json.loads(keys_json)
            except json.JSONDecodeError:
                config_dict = {"keys": [], "primary_key_id": 0}

            # 添加新密钥
            new_key_id = max([k.get("id", 0) for k in config_dict.get("keys", [])], default=0) + 1
            config_dict["keys"].append({"id": new_key_id, "value": new_key, "status": "active"})

            # 更新旧密钥状态
            old_primary = config_dict.get("primary_key_id")
            for key in config_dict["keys"]:
                if key.get("id") == old_primary:
                    key["status"] = "deprecated"

            # 更新主密钥 ID
            config_dict["primary_key_id"] = new_key_id

            new_config_json = json.dumps(config_dict)

            # 更新 Secret
            v1 = client.CoreV1Api()
            secret_name = os.environ.get("OPENACE_SECRET_NAME", "openace-encryption-keys")
            namespace = os.environ.get("NAMESPACE", "default")

            v1.patch_namespaced_secret(
                name=secret_name,
                namespace=namespace,
                body={
                    "data": {
                        "OPENACE_ENCRYPTION_KEYS": base64.b64encode(new_config_json.encode()).decode()
                    }
                },
            )

            logger.info(f"Updated Kubernetes Secret {secret_name} in namespace {namespace}")

            return {"success": True, "message": "Kubernetes Secret 更新成功"}

        except Exception as e:
            logger.error(f"Kubernetes Secret update failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": f"Kubernetes Secret 更新失败: {str(e)}",
                "manual_instructions": "请手动更新 Kubernetes Secret",
            }

    def _update_config_file(self, new_key: str) -> dict:
        """更新配置文件

        Args:
            new_key: 新密钥

        Returns:
            更新结果
        """
        try:
            # 构建新的密钥配置
            keys_json = os.environ.get("OPENACE_ENCRYPTION_KEYS", "{}")
            try:
                config_dict = json.loads(keys_json)
            except json.JSONDecodeError:
                config_dict = {"keys": [], "primary_key_id": 0}

            # 添加新密钥
            new_key_id = max([k.get("id", 0) for k in config_dict.get("keys", [])], default=0) + 1
            config_dict["keys"].append({"id": new_key_id, "value": new_key, "status": "active"})

            # 更新旧密钥状态
            old_primary = config_dict.get("primary_key_id")
            for key in config_dict["keys"]:
                if key.get("id") == old_primary:
                    key["status"] = "deprecated"

            # 更新主密钥 ID
            config_dict["primary_key_id"] = new_key_id

            new_config_json = json.dumps(config_dict)

            # 写入配置文件
            config_file = os.environ.get("OPENACE_CONFIG_FILE", "/etc/openace/config.env")

            with open(config_file, "a") as f:
                f.write(f"\nOPENACE_ENCRYPTION_KEYS='{new_config_json}'\n")

            logger.info(f"Updated config file {config_file}")

            return {"success": True, "message": "配置文件更新成功"}

        except Exception as e:
            logger.error(f"Config file update failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": f"配置文件更新失败: {str(e)}",
                "manual_instructions": "请手动更新配置文件",
            }

    def _rollback_database(self, old_key_id: int):
        """回滚数据库更改

        Args:
            old_key_id: 旧密钥 ID
        """
        try:
            # 删除新插入的密钥
            self.db.execute("DELETE FROM encryption_keys WHERE status = 'active' AND key_id != ?", (old_key_id,))

            # 恢复旧密钥状态
            self.db.execute(
                """
                UPDATE encryption_keys
                SET status = 'active', rotated_at = NULL
                WHERE key_id = ?
                """,
                (old_key_id,),
            )

            logger.info(f"Rolled back database changes, restored key {old_key_id} to active")

        except Exception as e:
            logger.error(f"Database rollback failed: {e}", exc_info=True)

    def _check_consistency(self) -> str:
        """检查一致性状态

        Returns:
            一致性状态字符串
        """
        result = self.validate_encryption_keys_consistency()
        return "consistent" if result["consistent"] else "inconsistent"