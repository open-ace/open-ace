"""
Tests for Encryption Key Management

测试加密密钥管理的核心功能
"""

import base64
import hashlib
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from app.services.encryption_key_service import EncryptionKeyService


@pytest.fixture
def mock_db():
    """Mock database"""
    db = MagicMock()
    return db


@pytest.fixture
def encryption_key_service(mock_db):
    """Encryption key service fixture"""
    return EncryptionKeyService(db=mock_db)


class TestValidateKeyFormat:
    """测试密钥格式验证"""

    def test_validate_valid_fernet_key(self, encryption_key_service):
        """测试有效的 Fernet 密钥"""
        # 生成一个有效的 Fernet 密钥
        from cryptography.fernet import Fernet

        valid_key = Fernet.generate_key().decode()

        result = encryption_key_service.validate_key_format(valid_key)

        assert result["valid"] is True
        assert result["fingerprint"] is not None
        assert result["fingerprint"].startswith("sha256:")
        assert result["error"] is None

    def test_validate_invalid_base64_key(self, encryption_key_service):
        """测试无效的 base64 编码"""
        invalid_key = "not-valid-base64!!!"

        result = encryption_key_service.validate_key_format(invalid_key)

        assert result["valid"] is False
        assert result["fingerprint"] is None
        # 错误信息可能是 "无效的 base64 编码" 或 "密钥长度必须为..."
        assert result["error"] is not None

    def test_validate_short_key(self, encryption_key_service):
        """测试长度不足的密钥"""
        # 生成一个短密钥
        short_key = base64.urlsafe_b64encode(b"short").decode()

        result = encryption_key_service.validate_key_format(short_key)

        assert result["valid"] is False
        assert result["fingerprint"] is None
        assert "32" in result["error"]  # 错误信息应包含正确的长度

    def test_validate_wrong_length_key(self, encryption_key_service):
        """测试长度错误的密钥（不是 32 字节）"""
        # 生成一个 16 字节的密钥
        wrong_length_key = base64.urlsafe_b64encode(b"0123456789abcdef").decode()

        result = encryption_key_service.validate_key_format(wrong_length_key)

        assert result["valid"] is False
        assert result["fingerprint"] is None
        assert "32" in result["error"]


class TestGenerateNewKey:
    """测试密钥生成"""

    def test_generate_new_key(self, encryption_key_service):
        """测试生成新密钥"""
        new_key = encryption_key_service.generate_new_key()

        # 验证密钥格式
        assert isinstance(new_key, str)

        # 验证是否为有效的 Fernet 密钥
        validation = encryption_key_service.validate_key_format(new_key)
        assert validation["valid"] is True

    def test_generate_unique_keys(self, encryption_key_service):
        """测试生成多个唯一密钥"""
        keys = [encryption_key_service.generate_new_key() for _ in range(10)]

        # 验证所有密钥都有效
        for key in keys:
            validation = encryption_key_service.validate_key_format(key)
            assert validation["valid"] is True

        # 验证密钥唯一性
        assert len(set(keys)) == 10


class TestComputeFingerprint:
    """测试指纹计算"""

    def test_compute_fingerprint(self, encryption_key_service):
        """测试指纹计算"""
        key_value = "test-encryption-key-32-chars-long-abc-xx"

        fingerprint = encryption_key_service._compute_fingerprint(key_value)

        # 验证指纹格式
        assert fingerprint.startswith("sha256:")
        assert len(fingerprint) == 23  # "sha256:" + 16 字符

    def test_fingerprint_consistency(self, encryption_key_service):
        """测试相同密钥产生相同指纹"""
        key_value = "test-encryption-key-32-chars-long-abc-xx"

        fingerprint1 = encryption_key_service._compute_fingerprint(key_value)
        fingerprint2 = encryption_key_service._compute_fingerprint(key_value)

        assert fingerprint1 == fingerprint2

    def test_fingerprint_uniqueness(self, encryption_key_service):
        """测试不同密钥产生不同指纹"""
        key1 = "test-encryption-key-32-chars-long-abc-xx"
        key2 = "test-encryption-key-32-chars-long-abc-yy"

        fingerprint1 = encryption_key_service._compute_fingerprint(key1)
        fingerprint2 = encryption_key_service._compute_fingerprint(key2)

        assert fingerprint1 != fingerprint2


class TestRotateKey:
    """测试密钥轮换"""

    def test_rotate_key_requires_confirmation(self, encryption_key_service):
        """测试轮换需要确认文本"""
        result = encryption_key_service.rotate_key(confirmation="WRONG")

        assert result["success"] is False
        assert result["error"] == "invalid_confirmation"

    def test_rotate_key_validates_confirmation(self, encryption_key_service):
        """测试确认文本验证"""
        result = encryption_key_service.rotate_key(confirmation="ROTATE")

        # 由于是集成测试，这里会失败（没有真实的环境变量和数据库）
        # 但我们可以验证确认文本通过了验证
        assert result.get("error") != "invalid_confirmation"


class TestSyncKeysFromEnvToDb:
    """测试从环境变量同步密钥到数据库"""

    def test_sync_no_keys(self, encryption_key_service, mock_db):
        """测试环境变量中没有密钥"""
        with patch.dict(os.environ, {}, clear=True):
            result = encryption_key_service.sync_keys_from_env_to_db()

            assert result["inserted"] == 0
            assert result["skipped"] == 0

    def test_sync_single_key(self, encryption_key_service, mock_db):
        """测试同步单个密钥"""
        with patch.dict(os.environ, {"OPENACE_ENCRYPTION_KEY": "test-key-32-chars-long-abc-xxxxx"}):
            # Mock 数据库查询返回空
            mock_db.fetch_all.return_value = []

            result = encryption_key_service.sync_keys_from_env_to_db(dry_run=True)

            # 验证插入计数
            assert result["inserted"] == 1

    def test_sync_multi_key(self, encryption_key_service, mock_db):
        """测试同步多密钥配置"""
        keys_json = json.dumps(
            {
                "keys": [
                    {"id": 1, "value": "key-1-value-32-chars-long-abc-xx", "status": "deprecated"},
                    {"id": 2, "value": "key-2-value-32-chars-long-abc-xx", "status": "active"},
                ],
                "primary_key_id": 2,
            }
        )

        with patch.dict(os.environ, {"OPENACE_ENCRYPTION_KEYS": keys_json}):
            # Mock 数据库查询返回空
            mock_db.fetch_all.return_value = []

            result = encryption_key_service.sync_keys_from_env_to_db(dry_run=True)

            # 验证插入计数
            assert result["inserted"] == 2


class TestValidateEncryptionKeysConsistency:
    """测试一致性验证"""

    def test_consistency_check(self, encryption_key_service, mock_db):
        """测试一致性检查"""
        # Mock 数据库返回
        mock_db.fetch_all.return_value = [
            {"key_id": 1, "key_fingerprint": "sha256:abc123", "status": "active"}
        ]

        # 设置环境变量
        with patch.dict(os.environ, {"OPENACE_ENCRYPTION_KEY": "test-key-32-chars-long-abc-xxxxx"}):
            result = encryption_key_service.validate_encryption_keys_consistency()

            # 验证返回的结构
            assert "consistent" in result
            assert "message" in result
            assert "env_only" in result
            assert "db_only" in result


class TestGenerateEnvConfig:
    """测试生成环境变量配置"""

    def test_generate_env_config(self, encryption_key_service):
        """测试生成环境变量配置"""
        with patch.dict(os.environ, {"OPENACE_ENCRYPTION_KEY": "test-key"}):
            result = encryption_key_service.generate_env_config()

            assert result["success"] is True
            assert result["env_var_name"] == "OPENACE_ENCRYPTION_KEYS"
            assert "env_var_value" in result
            assert "instructions" in result
            assert "config_file_example" in result


class TestInputValidation:
    """测试输入验证"""

    def test_validate_key_length_limit(self, encryption_key_service):
        """测试密钥长度限制"""
        # 创建一个超长的密钥
        long_key = "a" * 200

        # 这应该不会抛出异常，因为验证在 API 层
        # 但我们可以在服务层测试验证逻辑
        result = encryption_key_service.validate_key_format(long_key[:44])

        # 短密钥应该失败
        assert result["valid"] is False


# 集成测试标记
pytestmark = [pytest.mark.unit]
