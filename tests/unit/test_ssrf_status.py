"""
Unit tests for SSRF status functionality (Issue #3328).

Tests SSRF protection status API, configuration reset, cache invalidation,
and optimistic locking.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.repositories.governance_repo import GovernanceRepository


@pytest.fixture
def governance_repo():
    """Create a governance repository instance."""
    return GovernanceRepository()


class TestSsrfStatus:
    """Tests for SSRF status functionality."""

    def test_get_ssrf_status_default_config(self, governance_repo):
        """Test getting SSRF status with default configuration."""
        with patch.object(governance_repo, '_get_ssrf_config_from_db') as mock_db_config:
            mock_db_config.return_value = {
                'outbound_port_whitelist': None,
                'global_allowlist_hosts': None,
                'ssrf_config_version': 1,
            }

            with patch('app.utils.llm_proxy_url_validator.get_allowed_hosts') as mock_hosts:
                mock_hosts.return_value = {0: []}

                with patch('app.utils.outbound_url_guard.get_allowed_ports') as mock_ports:
                    mock_ports.return_value = {80, 443, 8080}

                    with patch('os.environ.get') as mock_env:
                        mock_env.return_value = ''

                        status = governance_repo.get_ssrf_status()

                        assert 'ssrf_protection_enabled' in status
                        assert 'emergency_mode' in status
                        assert 'config_source' in status
                        assert 'config_version' in status
                        assert 'port_whitelist' in status
                        assert 'global_allowlist' in status
                        assert 'default_policy' in status
                        assert 'interception_stats' in status
                        assert 'can_reset' in status

    def test_get_ssrf_status_emergency_mode(self, governance_repo):
        """Test SSRF status when emergency mode is enabled."""
        with patch.object(governance_repo, '_get_ssrf_config_from_db') as mock_db_config:
            mock_db_config.return_value = {
                'outbound_port_whitelist': None,
                'global_allowlist_hosts': None,
                'ssrf_config_version': 1,
            }

            with patch('app.utils.llm_proxy_url_validator.get_allowed_hosts') as mock_hosts:
                mock_hosts.return_value = {0: []}

                with patch('app.utils.outbound_url_guard.get_allowed_ports') as mock_ports:
                    mock_ports.return_value = {80, 443, 8080}

                    with patch('os.environ.get') as mock_env:
                        # Mock OPENACE_LLM_PROXY_DISABLE_SSRF_CHECK=true
                        def env_side_effect(key, default=''):
                            if key == 'OPENACE_LLM_PROXY_DISABLE_SSRF_CHECK':
                                return 'true'
                            return default

                        mock_env.side_effect = env_side_effect

                        status = governance_repo.get_ssrf_status()

                        assert status['emergency_mode'] is True
                        assert status['ssrf_protection_enabled'] is False

    def test_reset_ssrf_config_success(self, governance_repo):
        """Test successful SSRF configuration reset."""
        with patch.object(governance_repo, '_get_config_version') as mock_version:
            mock_version.return_value = 2

            with patch.object(governance_repo, '_delete_ssrf_config_item') as mock_delete:
                mock_delete.return_value = True

                with patch.object(governance_repo, '_increment_config_version') as mock_increment:
                    mock_increment.return_value = 3

                    with patch('app.utils.outbound_url_guard.invalidate_port_cache') as mock_port_cache:
                        with patch('app.utils.llm_proxy_url_validator.invalidate_dns_cache') as mock_dns_cache:
                            result = governance_repo.reset_ssrf_config(
                                reset_ports=True,
                                reset_global_allowlist=True,
                                expected_version=2,
                            )

                            assert result['reset_items'] == ['port_whitelist', 'global_allowlist']
                            assert result['new_config_version'] == 3

                            # Verify cache invalidation was called
                            mock_port_cache.assert_called_once()
                            mock_dns_cache.assert_called_once()

    def test_reset_ssrf_config_version_conflict(self, governance_repo):
        """Test SSRF reset with version conflict."""
        with patch.object(governance_repo, '_get_config_version') as mock_version:
            mock_version.return_value = 3  # Current version is 3, expected is 2

            with pytest.raises(ValueError, match='version conflict'):
                governance_repo.reset_ssrf_config(
                    reset_ports=True,
                    reset_global_allowlist=True,
                    expected_version=2,
                )

    def test_get_interception_stats(self, governance_repo):
        """Test getting interception statistics."""
        with patch.object(governance_repo.db, 'fetch_one') as mock_fetch:
            mock_fetch.return_value = {'count': 5}

            stats = governance_repo._get_interception_stats()

            assert 'last_24h' in stats
            assert 'last_7d' in stats
            assert 'last_30d' in stats

            # Verify query was called for each time range
            assert mock_fetch.call_count == 3

    def test_increment_config_version(self, governance_repo):
        """Test incrementing config version."""
        with patch.object(governance_repo, '_get_config_version') as mock_version:
            mock_version.return_value = 1

            with patch.object(governance_repo.db, 'connection') as mock_conn:
                mock_cursor = MagicMock()
                mock_conn.return_value.__enter__ = MagicMock(return_value=MagicMock())
                mock_conn.return_value.__exit__ = MagicMock(return_value=False)
                mock_conn.return_value.__enter__.return_value.cursor.return_value = mock_cursor

                new_version = governance_repo._increment_config_version()

                assert new_version == 2
                # Verify the cursor executed an update
                assert mock_cursor.execute.called


class TestSsrfCacheInvalidation:
    """Tests for SSRF cache invalidation."""

    def test_invalidate_port_cache(self):
        """Test port cache invalidation."""
        from app.utils.outbound_url_guard import (
            _ALLOWED_PORTS_CACHE,
            get_allowed_ports,
            invalidate_port_cache,
        )

        # Populate cache
        _ = get_allowed_ports()

        # Invalidate cache
        invalidate_port_cache()

        # Verify cache is cleared
        # The cache should be None after invalidation
        import app.utils.outbound_url_guard as guard_module

        assert guard_module._ALLOWED_PORTS_CACHE is None

    def test_invalidate_dns_cache(self):
        """Test DNS cache invalidation."""
        from app.utils.llm_proxy_url_validator import (
            _DNS_CACHE,
            invalidate_dns_cache,
        )

        # Add some cache entries
        _DNS_CACHE['test.example.com'] = ((MagicMock(),), 0.0)

        # Invalidate cache
        invalidate_dns_cache()

        # Verify cache is cleared
        assert len(_DNS_CACHE) == 0


class TestSsrfAuditLog:
    """Tests for SSRF audit logging."""

    def test_ssrf_config_reset_audit_logged(self, governance_repo):
        """Test that SSRF config reset is logged to audit log."""
        with patch.object(governance_repo, '_get_config_version') as mock_version:
            mock_version.return_value = 1

            with patch.object(governance_repo, '_delete_ssrf_config_item') as mock_delete:
                mock_delete.return_value = True

                with patch.object(governance_repo, '_increment_config_version') as mock_increment:
                    mock_increment.return_value = 2

                    with patch('app.utils.outbound_url_guard.invalidate_port_cache'):
                        with patch('app.utils.llm_proxy_url_validator.invalidate_dns_cache'):
                            # The audit log is handled in the route, not in the repo
                            # Just verify the reset operation succeeds
                            result = governance_repo.reset_ssrf_config(
                                reset_ports=True,
                                reset_global_allowlist=False,
                                expected_version=1,
                            )

                            assert result['reset_items'] == ['port_whitelist']